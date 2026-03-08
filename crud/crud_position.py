"""
CRUD operations for Position model.
Handles position tracking, cost basis calculation, and P&L reporting.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, select
from models import Position, Transaction, Portfolio, CryptoBalance, CryptoWallet
from decimal import Decimal
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc

logger = get_logger(__name__)


def get_or_create_position(
    db: Session,
    portfolio_id: int,
    symbol: str,
    asset_class: Optional[str] = None,
    symbol_normalized: Optional[str] = None
) -> Position:
    """
    Get existing position or create new one.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        symbol: Asset symbol
        asset_class: Asset class (stocks, crypto, gold_baht, etc.)
        symbol_normalized: Normalized symbol
    
    Returns:
        Position: Existing or newly created position
    """
    position = db.query(Position).filter(
        and_(
            Position.portfolio_id == portfolio_id,
            Position.symbol == symbol
        )
    ).first()
    
    if not position:
        # Get portfolio to access currency_base
        from utils.app_config import get_global_base_currency
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        currency_base = portfolio.currency_base if portfolio else get_global_base_currency()
        
        position = Position(
            portfolio_id=portfolio_id,
            symbol=symbol,
            quantity=Decimal('0'),
            cost_basis_base=Decimal('0'),
            currency_base=currency_base,  # Position.currency_base matches naming convention
            avg_price_base=Decimal('0'),
            cost_basis_native=Decimal('0'),
            asset_class=asset_class,
            symbol_normalized=symbol_normalized or symbol,
            last_updated=now_utc()
        )
        db.add(position)
        db.commit()
        db.refresh(position)
        logger.info(f"Created new position for {symbol} in portfolio {portfolio_id}")
    
    return position


def update_position_from_transaction(
    db: Session,
    transaction: Transaction
) -> Optional[Position]:
    """
    Update position based on a transaction.
    Handles buy, sell, transfer_in, transfer_out, exchange, balance_adjustment, dividend_reinvest,
    staking_reward, interest, dividend.
    
    IMPORTANT: For cash portfolios, dividends increase the CURRENCY position, not the stock symbol.
    Example: AAPL dividend of 3 USD in cash portfolio → increases USD position by 3, not AAPL position.
    
    Args:
        db: Database session
        transaction: Transaction object
    
    Returns:
        Position: Updated position or None if not applicable
    """
    # Only process transactions that affect positions
    relevant_types = ['buy', 'sell', 'transfer_in', 'transfer_out', 'deposit', 'withdrawal', 
                      'exchange', 'balance_adjustment', 'dividend_reinvest', 
                      'staking_reward', 'interest', 'dividend', 'fee', 'withholding_tax']
    if transaction.type not in relevant_types:
        return None
    
    # Skip if no symbol or quantity
    if not transaction.symbol or not transaction.quantity:
        return None
    
    # Get portfolio type to handle dividend correctly (for cash portfolios)
    from utils.portfolios_loader import PortfoliosLoader
    
    portfolios_loader = PortfoliosLoader()
    portfolio_type = portfolios_loader.get_portfolio_type(transaction.portfolio_id)
    
    # Determine which symbol to use for position
    position_symbol = transaction.symbol
    position_asset_class = transaction.asset_class
    position_symbol_normalized = transaction.symbol_normalized
    
    # CRITICAL: For cash portfolios, dividends should update currency position, not stock symbol
    # Example: AAPL dividend of 0.75 USD means 0.75 USD cash received (not 0.75 AAPL shares)
    if portfolio_type == 'cash' and transaction.type == 'dividend':
        position_symbol = transaction.currency_native
        position_asset_class = 'cash'
        position_symbol_normalized = transaction.currency_native
        logger.debug(
            f"Cash portfolio dividend: {transaction.symbol} dividend of "
            f"{transaction.quantity} {transaction.currency_native} → updating {transaction.currency_native} position"
        )
    
    try:
        position = get_or_create_position(
            db=db,
            portfolio_id=transaction.portfolio_id,
            symbol=position_symbol,
            asset_class=position_asset_class,
            symbol_normalized=position_symbol_normalized
        )
        
        qty = Decimal(str(transaction.quantity))
        
        # Handle different transaction types
        # For cash transactions (exchange, balance_adjustment), qty can be positive or negative
        # NOTE: 'dividend' removed from this list because dividends represent cash income, not stock purchases
        # Only 'dividend_reinvest' increases stock positions (when dividend is used to buy more shares)
        # Regular dividends in cash portfolios are handled above (currency position increase)
        if transaction.type in ['buy', 'transfer_in', 'deposit', 'dividend_reinvest', 
                                'staking_reward', 'interest']:
            # Increase position
            old_qty = position.quantity
            old_cost = position.cost_basis_base or Decimal('0')
            
            new_qty = old_qty + abs(qty)
            
            # Calculate fee in base currency for cost basis
            # Per German tax law (§ 20 EStG) and international standards:
            # Cost basis = Purchase price + All acquisition costs (fees, commissions)
            fee_base = Decimal('0')
            if transaction.fee and Decimal(str(transaction.fee)) > 0:
                fee = abs(Decimal(str(transaction.fee)))
                # Convert fee to base currency
                fee_currency = getattr(transaction, 'fee_currency', None)
                tx_currency_base = getattr(transaction, 'currency_base', None)
                if fee_currency and tx_currency_base and fee_currency == tx_currency_base:
                    # Fee is already in base currency
                    fee_base = fee
                elif fee_currency and fee_currency == transaction.currency_native and transaction.exchange_rate_to_base:
                    # Fee is in transaction currency - convert using same exchange rate
                    fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
                elif transaction.exchange_rate_to_base:
                    # Fallback: use transaction exchange rate
                    fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
                else:
                    # No conversion possible - use fee as-is
                    fee_base = fee
            
            # Add blockchain_fee if present (for crypto transactions)
            if hasattr(transaction, 'blockchain_fee') and transaction.blockchain_fee:
                blockchain_fee = abs(Decimal(str(transaction.blockchain_fee)))
                if transaction.exchange_rate_to_base:
                    fee_base += blockchain_fee * Decimal(str(transaction.exchange_rate_to_base))
                else:
                    fee_base += blockchain_fee
            
            # Add cost (use value_base if available, otherwise calculate)
            # IMPORTANT: Include fee_base in cost basis
            # CRITICAL FIX (Dec 21, 2025): For base currency positions, cost = quantity (face value)
            # Base currency (EUR in EUR-based portfolio) has no "cost" - 1 EUR = 1 EUR
            # Using transaction.value_base causes drift due to exchange rate/fee calculations
            portfolio = db.query(Portfolio).filter(Portfolio.id == transaction.portfolio_id).first()
            if portfolio and position_symbol == portfolio.currency_base:
                # Base currency: cost_basis = quantity (1 EUR = 1 EUR)
                added_cost = abs(qty)
                logger.debug(f"Base currency {position_symbol}: cost_basis = quantity = {abs(qty)}")
            elif transaction.value_base:
                added_cost = abs(Decimal(str(transaction.value_base))) + fee_base
            elif transaction.value_native and transaction.exchange_rate_to_base:
                added_cost = abs(Decimal(str(transaction.value_native)) * Decimal(str(transaction.exchange_rate_to_base))) + fee_base
            else:
                # Fallback: use qty * price + fee
                added_cost = abs(qty) * (Decimal(str(transaction.price)) if transaction.price else Decimal('0')) + fee_base
            
            new_cost = old_cost + added_cost
            position.quantity = new_qty
            position.cost_basis_base = new_cost
            position.avg_price_base = new_cost / new_qty if new_qty > 0 else Decimal('0')
            
            if transaction.type == 'dividend_reinvest':
                logger.info(f"Position {position_symbol}: DRIP - qty {old_qty} → {new_qty}, cost {old_cost} → {new_cost}")
            else:
                logger.debug(f"Position {position_symbol}: Buy/In - qty {old_qty} → {new_qty}, cost {old_cost} → {new_cost}")
        
        elif transaction.type in ['sell', 'transfer_out', 'withdrawal', 'staking_loss']:
            # Decrease position (staking_loss = slashing event, treated like forced sale)
            old_qty = position.quantity
            old_cost = position.cost_basis_base or Decimal('0')
            
            qty_reduced = abs(qty)
            new_qty = old_qty - qty_reduced
            
            # For cash portfolios, allow negative balances (overdrafts, timing issues)
            # For asset portfolios, clamp to zero (can't have negative shares/coins)
            if new_qty < 0 and portfolio_type != 'cash':
                logger.warning(f"Position {position_symbol} going negative: {old_qty} - {qty_reduced} = {new_qty} (clamping to 0)")
                new_qty = Decimal('0')
            elif new_qty < 0:
                logger.debug(f"Position {position_symbol} going negative: {old_qty} - {qty_reduced} = {new_qty} (allowed for cash)")
            
            # CRITICAL FIX: Use actual lot allocations if available (more accurate than proportional)
            # Check if this transaction has lot allocations
            from models import LotAllocation, Lot
            allocations = db.query(LotAllocation).filter(
                LotAllocation.transaction_id == transaction.id
            ).all()
            
            if allocations:
                # Use actual cost from lot allocations (FIFO-based)
                # LotAllocation.allocated_cost_basis_base already stores the cost for the allocated quantity
                cost_reduced = Decimal('0')
                for alloc in allocations:
                    cost_reduced += Decimal(str(alloc.allocated_cost_basis_base))
                logger.debug(f"Position {transaction.symbol}: Using lot allocation cost: €{cost_reduced:.2f}")
            else:
                # Fallback: Reduce cost basis proportionally (for portfolios without lot management)
                if old_qty > 0:
                    cost_reduced = (qty_reduced / old_qty) * old_cost
                else:
                    cost_reduced = Decimal('0')
                logger.debug(f"Position {transaction.symbol}: Using proportional cost: €{cost_reduced:.2f}")
                
            new_cost = old_cost - cost_reduced
            position.quantity = new_qty
            position.cost_basis_base = new_cost
            position.avg_price_base = new_cost / new_qty if new_qty > 0 else Decimal('0')
            
            logger.debug(f"Position {position_symbol}: Sell/Out - qty {old_qty} → {new_qty}, cost {old_cost} → {new_cost}")
        
        elif transaction.type in ['exchange', 'balance_adjustment', 'dividend', 'fee', 'withholding_tax']:
            # For currency exchanges, balance adjustments, fees, taxes, and dividends in securities portfolios
            # qty can be positive or negative
            # For currency exchanges and balance adjustments, qty can be positive or negative
            # Just add/subtract the quantity and adjust cost basis accordingly
            old_qty = position.quantity
            old_cost = position.cost_basis_base or Decimal('0')
            
            new_qty = old_qty + qty  # qty already has correct sign
            
            # CRITICAL FIX (Dec 21, 2025): For base currency positions, cost_change = quantity change
            # Base currency (EUR in EUR-based portfolio): 1 EUR always costs 1 EUR (face value)
            # Using transaction.value_base causes drift due to exchange rate calculations
            portfolio = db.query(Portfolio).filter(Portfolio.id == transaction.portfolio_id).first()
            if portfolio and position_symbol == portfolio.currency_base:
                # Base currency: cost_basis change = quantity change (face value)
                cost_change = qty
                logger.debug(f"Base currency {position_symbol}: cost_change = qty = {qty}")
            elif qty < 0 and transaction.type == 'exchange':
                # CRITICAL FIX (Dec 30, 2025): For currency OUTFLOWS (selling foreign currency),
                # use lot allocations to get actual cost basis, not value_base (which is market value at sale)
                # This mirrors the logic used for sell transactions
                from models import LotAllocation
                allocations = db.query(LotAllocation).filter(
                    LotAllocation.transaction_id == transaction.id
                ).all()
                
                if allocations:
                    # Use actual cost from lot allocations (FIFO-based)
                    cost_change = Decimal('0')
                    for alloc in allocations:
                        cost_change -= Decimal(str(alloc.allocated_cost_basis_base))
                    logger.debug(f"Exchange outflow {position_symbol}: Using lot allocation cost: €{-cost_change:.2f}")
                elif old_qty > 0:
                    # Fallback: Reduce cost basis proportionally
                    qty_reduced = abs(qty)
                    cost_change = -((qty_reduced / old_qty) * old_cost)
                    logger.debug(f"Exchange outflow {position_symbol}: Using proportional cost: €{-cost_change:.2f}")
                else:
                    cost_change = Decimal('0')
            elif transaction.value_base:
                cost_change = Decimal(str(transaction.value_base))
            else:
                # For cash, cost basis changes by the same amount as quantity
                cost_change = qty
            
            new_cost = old_cost + cost_change
            position.quantity = new_qty
            position.cost_basis_base = new_cost
            position.avg_price_base = new_cost / new_qty if new_qty != 0 else Decimal('0')
            
            tx_type_desc = 'Dividend' if transaction.type == 'dividend' else 'Exchange/Adjustment'
            logger.debug(f"Position {position_symbol}: {tx_type_desc} - qty {old_qty} → {new_qty}, cost {old_cost} → {new_cost}")
        
        # CRITICAL: For base currency positions, cost_basis_base MUST equal quantity
        # Base currency has no exchange rate - 1 EUR always costs 1 EUR
        # This avoids accumulation issues from historical transactions
        portfolio = db.query(Portfolio).filter(Portfolio.id == transaction.portfolio_id).first()
        if portfolio and position_symbol == portfolio.currency_base:
            position.cost_basis_base = position.quantity
            position.avg_price_base = Decimal('1')
            logger.debug(f"Position {position_symbol}: Base currency - forcing cost_basis = quantity ({position.quantity})")
        
        # Update position metadata
        position.last_updated = now_utc()
        db.commit()
        db.refresh(position)
        
        return position
        
    except Exception as e:
        logger.error(f"Error updating position from transaction {transaction.id}: {e}", exc_info=True)
        db.rollback()
        return None


# NOTE: reconcile_positions_for_portfolio() deleted (obsolete)
# This function used proportional cost reduction which overwrote correct
# lot-based cost basis calculations. Positions are now updated by:
#   1. update_position_from_transaction() after each transaction (real-time)
#   2. recreate_positions_from_transactions() weekly (self-correction)
# See DIVIDEND_REINVESTMENT_BUG_FIX_COMPLETE.md for details.


def reconcile_position_cost_from_lots(
    db: Session,
    portfolio_id: int
) -> Tuple[int, List[str]]:
    """
    Reconcile Position.cost_basis_base to match active Lots.
    
    This ensures Position table reflects true cost basis of current holdings
    using FIFO lot allocation, not proportional reduction.
    
    IMPORTANT: Should run after lot reconciliation to sync Position table
    with lot-based cost calculations.
    
    Background:
    - Position table uses proportional cost reduction on sells
    - Lot table uses FIFO allocation (First In, First Out)
    - For portfolios with many transactions, these diverge significantly
    - Solution: Periodically sync Position cost_basis to match active Lots
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        tuple: (positions_updated, warnings)
    """
    from models import Lot
    from sqlalchemy import func
    
    logger.info(f"Reconciling position cost basis from lots for portfolio {portfolio_id}")
    
    updated = 0
    warnings = []
    
    try:
        # Get portfolio base currency to handle base currency positions specially
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        base_currency = portfolio.currency_base if portfolio else None
        
        # FIRST: Handle base currency position (no lots, cost_basis = quantity)
        if base_currency:
            base_position = db.query(Position).filter(
                Position.portfolio_id == portfolio_id,
                Position.symbol == base_currency
            ).first()
            
            if base_position and base_position.quantity:
                old_cost = base_position.cost_basis_base or Decimal('0')
                new_cost = base_position.quantity  # Base currency: 1 EUR = 1 EUR
                
                if abs(old_cost - new_cost) > Decimal('0.01'):
                    base_position.cost_basis_base = new_cost
                    base_position.avg_price_base = Decimal('1')
                    updated += 1
                    logger.info(
                        f"Reconciled {base_currency} (base currency) position cost: "
                        f"€{old_cost:,.2f} → €{new_cost:,.2f} (forced to equal quantity)"
                    )
        
        # Get cost basis from lots grouped by symbol
        # remaining cost = (remaining_quantity / quantity) * total_cost_basis_base for each lot
        lot_costs = db.query(
            Lot.symbol,
            func.sum((Lot.remaining_quantity / Lot.quantity) * Lot.total_cost_basis_base).label('cost_basis'),
            func.sum(Lot.remaining_quantity).label('total_qty')
        ).filter(
            Lot.portfolio_id == portfolio_id,
            Lot.remaining_quantity > 0,
            Lot.quantity > 0  # Avoid division by zero
        ).group_by(Lot.symbol).all()
        
        for symbol, lot_cost_basis, lot_qty in lot_costs:
            # Skip base currency - already handled above
            if base_currency and symbol == base_currency:
                continue
                
            position = db.query(Position).filter(
                Position.portfolio_id == portfolio_id,
                Position.symbol == symbol
            ).first()
            
            if position:
                old_cost = Decimal(str(position.cost_basis_base))
                new_cost = Decimal(str(lot_cost_basis))
                position_qty = Decimal(str(position.quantity))
                lot_qty_decimal = Decimal(str(lot_qty))
                
                # Check if quantities match
                if abs(position_qty - lot_qty_decimal) > Decimal('0.00000001'):
                    warnings.append(
                        f"{symbol}: Position qty ({position_qty}) != Lot qty ({lot_qty_decimal}), "
                        f"difference: {position_qty - lot_qty_decimal}"
                    )
                
                # Update cost basis if different
                if abs(old_cost - new_cost) > Decimal('0.01'):  # Allow 1 cent tolerance
                    position.cost_basis_base = new_cost
                    position.avg_price_base = (
                        new_cost / position_qty
                        if position_qty > 0 else Decimal('0')
                    )
                    updated += 1
                    
                    diff = new_cost - old_cost
                    logger.info(
                        f"Reconciled {symbol} position cost: "
                        f"${old_cost:,.2f} → ${new_cost:,.2f} "
                        f"(diff: ${diff:+,.2f})"
                    )
        
        db.commit()
        logger.info(
            f"Position cost reconciliation complete for portfolio {portfolio_id}: "
            f"{updated} positions updated, {len(warnings)} warnings"
        )
        
        return updated, warnings
        
    except Exception as e:
        logger.error(f"Error reconciling position costs from lots for portfolio {portfolio_id}: {e}", exc_info=True)
        db.rollback()
        return 0, [f"Error: {str(e)}"]


def reconcile_crypto_positions_from_balances(
    db: Session,
    portfolio_id: int
) -> Tuple[int, List[str]]:
    """
    Reconcile crypto positions with CryptoBalance data.
    This ensures crypto positions match actual wallet balances.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        tuple: (positions_updated, warnings)
    """
    logger.info(f"Reconciling crypto positions from balances for portfolio {portfolio_id}")
    
    positions_updated = 0
    warnings = []
    
    try:
        # Get all crypto wallets for accounts that have transactions in this portfolio
        # Since Account model doesn't have portfolio_id (accounts can serve multiple portfolios),
        # we need to find accounts through their transactions
        account_ids_in_portfolio = db.query(Transaction.account_id).filter(
            Transaction.portfolio_id == portfolio_id
        ).distinct().all()
        
        account_ids = [aid[0] for aid in account_ids_in_portfolio if aid[0] is not None]
        
        if not account_ids:
            logger.info(f"No accounts with transactions found for portfolio {portfolio_id}")
            return 0, []
        
        crypto_wallets = db.query(CryptoWallet).filter(
            CryptoWallet.account_id.in_(account_ids)
        ).all()
        
        if not crypto_wallets:
            logger.info(f"No crypto wallets found for accounts in portfolio {portfolio_id}")
            return 0, []
        
        # Aggregate balances by symbol
        symbol_balances: Dict[str, Decimal] = {}
        
        # 1. Get wallet-based balances (hardware wallets)
        for wallet in crypto_wallets:
            # Get latest balance for each symbol in this wallet
            latest_balances = db.query(
                CryptoBalance.symbol,
                func.max(CryptoBalance.as_of_date).label('max_ts')
            ).filter(
                CryptoBalance.wallet_id == wallet.id
            ).group_by(CryptoBalance.symbol).all()
            
            for symbol, max_ts in latest_balances:
                # Get all balance types for this symbol at this timestamp
                balances = db.query(CryptoBalance).filter(
                    and_(
                        CryptoBalance.wallet_id == wallet.id,
                        CryptoBalance.symbol == symbol,
                        CryptoBalance.as_of_date == max_ts
                    )
                ).all()
                
                # Sum all balance types (liquid + staked + activating + etc)
                total_balance = sum(Decimal(str(b.balance)) for b in balances)
                
                if symbol not in symbol_balances:
                    symbol_balances[symbol] = Decimal('0')
                symbol_balances[symbol] += total_balance
        
        # 2. Get account-based balances (exchange accounts like Binance.th)
        for account_id in account_ids:
            # Get latest balance for each symbol in this account
            latest_balances = db.query(
                CryptoBalance.symbol,
                func.max(CryptoBalance.as_of_date).label('max_ts')
            ).filter(
                CryptoBalance.account_id == account_id
            ).group_by(CryptoBalance.symbol).all()
            
            for symbol, max_ts in latest_balances:
                # Get all balance types for this symbol at this timestamp
                balances = db.query(CryptoBalance).filter(
                    and_(
                        CryptoBalance.account_id == account_id,
                        CryptoBalance.symbol == symbol,
                        CryptoBalance.as_of_date == max_ts
                    )
                ).all()
                
                # Sum all balance types (liquid + staked + etc)
                total_balance = sum(Decimal(str(b.balance)) for b in balances)
                
                if symbol not in symbol_balances:
                    symbol_balances[symbol] = Decimal('0')
                symbol_balances[symbol] += total_balance
        
        # Compare with positions and warn if discrepancy
        for symbol, balance in symbol_balances.items():
            position = db.query(Position).filter(
                and_(
                    Position.portfolio_id == portfolio_id,
                    Position.symbol == symbol
                )
            ).first()
            
            if position:
                position_qty = Decimal(str(position.quantity))
                tolerance = Decimal('0.00000001')  # 1e-8 tolerance
                
                if abs(position_qty - balance) > tolerance:
                    warnings.append(
                        f"{symbol}: Position qty {position_qty} != Balance {balance} "
                        f"(diff: {position_qty - balance})"
                    )
                    logger.warning(f"Crypto position mismatch for {symbol}: {position_qty} vs {balance}")
        
        logger.info(
            f"Crypto position reconciliation complete for portfolio {portfolio_id}: "
            f"{len(symbol_balances)} symbols checked, {len(warnings)} discrepancies"
        )
        
        return positions_updated, warnings
        
    except Exception as e:
        logger.error(f"Error reconciling crypto positions for portfolio {portfolio_id}: {e}", exc_info=True)
        return 0, [f"Error: {str(e)}"]


def get_positions_by_portfolio(
    db: Session,
    portfolio_id: int,
    include_zero: bool = False
) -> List[Position]:
    """
    Get all positions for a portfolio.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        include_zero: Include positions with zero quantity
    
    Returns:
        List[Position]: List of positions
    """
    query = db.query(Position).filter(Position.portfolio_id == portfolio_id)
    
    if not include_zero:
        query = query.filter(Position.quantity > 0)
    
    return query.order_by(Position.symbol).all()


def get_position(
    db: Session,
    portfolio_id: int,
    symbol: str
) -> Optional[Position]:
    """
    Get a specific position.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        symbol: Asset symbol
    
    Returns:
        Position or None
    """
    return db.query(Position).filter(
        and_(
            Position.portfolio_id == portfolio_id,
            Position.symbol == symbol
        )
    ).first()


def calculate_position_market_value(
    position: Position,
    current_price: Decimal
) -> Dict[str, Decimal]:
    """
    Calculate market value and P&L for a position.
    
    Args:
        position: Position object
        current_price: Current market price
    
    Returns:
        dict: Market value, unrealized P&L, and P&L percentage
    """
    quantity = Decimal(str(position.quantity))
    cost_basis = Decimal(str(position.cost_basis_base))
    
    market_value = quantity * current_price
    unrealized_pnl = market_value - cost_basis
    pnl_percentage = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else Decimal('0')
    
    return {
        'market_value': market_value,
        'unrealized_pnl': unrealized_pnl,
        'pnl_percentage': pnl_percentage
    }


def get_positions_summary(
    db: Session,
    portfolio_id: int
) -> Dict:
    """
    Get summary of all positions for a portfolio.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        dict: Summary statistics
    """
    positions = get_positions_by_portfolio(db, portfolio_id, include_zero=False)
    
    total_cost_basis = sum(Decimal(str(p.cost_basis_base)) for p in positions)
    total_quantity_by_asset = {}
    
    for position in positions:
        asset_class = position.asset_class or 'unknown'
        if asset_class not in total_quantity_by_asset:
            total_quantity_by_asset[asset_class] = 0
        total_quantity_by_asset[asset_class] += 1
    
    return {
        'total_positions': len(positions),
        'total_cost_basis': total_cost_basis,
        'positions_by_asset_class': total_quantity_by_asset,
        'symbols': [p.symbol for p in positions]
    }


def recreate_positions_from_transactions(
    db: Session,
    portfolio_id: int,
    commit: bool = True
) -> Dict[str, any]:
    """
    Recreate all positions from transactions for self-correction.
    
    This function rebuilds the positions table from scratch by replaying
    all transactions in chronological order. Useful for:
    - Fixing historical position errors (like missing dividend reinvestments)
    - Daily/weekly self-correction
    - After bulk transaction imports
    - Recovering from position table corruption
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID to recreate positions for
        commit: If True, commit changes; if False, rollback (for testing)
    
    Returns:
        dict: {
            'positions_created': int,        # Number of unique positions
            'transactions_processed': int,   # Number of transactions processed
            'symbols': List[str],            # List of symbols with positions
            'deleted_positions': int         # Number of old positions cleared
        }
    
    Example:
        >>> result = recreate_positions_from_transactions(db, portfolio_id=3)
        >>> print(f"Recreated {result['positions_created']} positions from {result['transactions_processed']} transactions")
    """
    logger.info(f"Starting position recreation for portfolio {portfolio_id}")
    
    # Step 1: Clear existing positions
    deleted = db.query(Position).filter(
        Position.portfolio_id == portfolio_id
    ).delete()
    logger.info(f"Cleared {deleted} existing positions for portfolio {portfolio_id}")
    
    # Step 2: Get all transactions chronologically
    transactions = db.query(Transaction).filter(
        Transaction.portfolio_id == portfolio_id
    ).order_by(Transaction.occurred_at).all()
    
    logger.info(f"Found {len(transactions)} transactions to process for portfolio {portfolio_id}")
    
    # Step 3: Rebuild positions by replaying transactions
    positions_created = set()
    processed_count = 0
    
    for tx in transactions:
        try:
            result = update_position_from_transaction(db, tx)
            if result:
                positions_created.add(result.symbol)
            processed_count += 1
            
            # Log progress every 100 transactions
            if processed_count % 100 == 0:
                logger.debug(f"Processed {processed_count}/{len(transactions)} transactions")
                
        except Exception as e:
            logger.error(
                f"Failed to update position for transaction {tx.id} "
                f"(symbol={tx.symbol}, type={tx.type}, date={tx.occurred_at}): {e}",
                exc_info=True
            )
            # Continue processing other transactions
            continue
    
    # Step 4: Commit or rollback
    if commit:
        db.commit()
        logger.info(
            f"Position recreation complete for portfolio {portfolio_id}: "
            f"{len(positions_created)} positions from {processed_count} transactions"
        )
    else:
        db.rollback()
        logger.info(f"Position recreation rolled back (test mode) for portfolio {portfolio_id}")
    
    return {
        'positions_created': len(positions_created),
        'transactions_processed': processed_count,
        'symbols': sorted(list(positions_created)),
        'deleted_positions': deleted
    }


def verify_positions_vs_lots(
    db: Session,
    portfolio_id: int
) -> Dict[str, any]:
    """
    Verify that positions match lots for securities portfolio.
    
    This diagnostic function compares cost basis between the positions table
    (transaction-based) and lots table (FIFO-based). Any discrepancy indicates
    missing position updates (e.g., dividend reinvestments not processed).
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID to verify
    
    Returns:
        dict: {
            'positions_cost': Decimal,        # Total cost from positions table
            'lots_cost': Decimal,             # Total cost from lots table
            'discrepancy': Decimal,           # Absolute difference
            'is_matched': bool,               # True if difference < €0.01
            'symbols_compared': int,          # Number of symbols checked
            'details': List[dict]            # Per-symbol comparison
        }
    
    Example:
        >>> result = verify_positions_vs_lots(db, portfolio_id=3)
        >>> if not result['is_matched']:
        ...     print(f"DISCREPANCY: €{result['discrepancy']:.2f}")
        ...     print(f"Run recreate_positions_from_transactions() to fix")
    """
    from models import Lot
    
    logger.info(f"Verifying positions vs lots for portfolio {portfolio_id}")
    
    # Get positions with positive quantity
    positions = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.quantity > 0
    ).all()
    
    # Get lots with remaining quantity
    lots = db.query(Lot).filter(
        Lot.portfolio_id == portfolio_id,
        Lot.remaining_quantity > 0
    ).all()
    
    # Calculate position-based cost
    positions_cost = sum(
        Decimal(str(p.cost_basis_base))
        for p in positions
    )
    
    # Calculate lot-based cost (remaining proportion of total cost basis)
    lots_cost = sum(
        (Decimal(str(lot.remaining_quantity)) / Decimal(str(lot.quantity))) * Decimal(str(lot.total_cost_basis_base))
        for lot in lots
        if Decimal(str(lot.quantity)) > 0  # Avoid division by zero
    )
    
    discrepancy = abs(lots_cost - positions_cost)
    is_matched = discrepancy < Decimal('0.01')
    
    # Per-symbol details
    details = []
    position_by_symbol = {p.symbol: p for p in positions}
    
    # Group lots by symbol
    lots_by_symbol = {}
    for lot in lots:
        symbol = lot.symbol
        if symbol not in lots_by_symbol:
            lots_by_symbol[symbol] = []
        lots_by_symbol[symbol].append(lot)
    
    # Compare each symbol
    all_symbols = set(position_by_symbol.keys()) | set(lots_by_symbol.keys())
    
    for symbol in sorted(all_symbols):
        pos = position_by_symbol.get(symbol)
        symbol_lots = lots_by_symbol.get(symbol, [])
        
        pos_qty = Decimal(str(pos.quantity)) if pos else Decimal('0')
        pos_cost = Decimal(str(pos.cost_basis_base)) if pos else Decimal('0')
        
        lot_qty = sum(Decimal(str(lot.remaining_quantity)) for lot in symbol_lots)
        lot_cost = sum(
            (Decimal(str(lot.remaining_quantity)) / Decimal(str(lot.quantity))) * Decimal(str(lot.total_cost_basis_base))
            for lot in symbol_lots
            if Decimal(str(lot.quantity)) > 0  # Avoid division by zero
        )
        
        qty_diff = lot_qty - pos_qty
        cost_diff = lot_cost - pos_cost
        
        if abs(qty_diff) > Decimal('0.0001') or abs(cost_diff) > Decimal('0.01'):
            details.append({
                'symbol': symbol,
                'position_qty': float(pos_qty),
                'lot_qty': float(lot_qty),
                'qty_difference': float(qty_diff),
                'position_cost': float(pos_cost),
                'lot_cost': float(lot_cost),
                'cost_difference': float(cost_diff)
            })
    
    logger.info(
        f"Verification complete: Positions €{positions_cost:,.2f}, "
        f"Lots €{lots_cost:,.2f}, Discrepancy €{discrepancy:.2f}, "
        f"Matched: {is_matched}"
    )
    
    return {
        'positions_cost': float(positions_cost),
        'lots_cost': float(lots_cost),
        'discrepancy': float(discrepancy),
        'is_matched': is_matched,
        'symbols_compared': len(all_symbols),
        'details': details
    }
