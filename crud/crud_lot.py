"""
CRUD operations for Lot model.
Handles lot-based cost basis tracking using FIFO method.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, select
from models import Lot, LotAllocation, Transaction, Position
from decimal import Decimal
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import load_app_config
import uuid

logger = get_logger(__name__)

# Load configuration
app_config = load_app_config()
PORTFOLIO_CONFIG = app_config.get('portfolio', {})
LOT_EXCLUDE_ASSET_CLASSES = set(PORTFOLIO_CONFIG.get('lot_tracking_exclude_asset_classes', []))

# Get base currency from app config (EUR)
# Lot tracking: Create lots for ALL foreign currencies, skip only base currency
if 'base_currency' not in app_config:
    raise KeyError(
        "base_currency not configured in app_config.yaml. "
        "This is critical for lot tracking and tax compliance. "
        "Please add 'base_currency: EUR' (or your currency) to config/app_config.yaml"
    )
BASE_CURRENCY = app_config['base_currency']

# Dust tolerance for lot allocation warnings
# Amounts below this threshold are considered rounding dust and won't trigger warnings
# 1e-6 = 0.000001 (works for most crypto with 8 decimals, stocks with fractional shares)
DUST_TOLERANCE = Decimal('0.000001')


def generate_lot_id(symbol: str, acquired_at: datetime, isin: str = None) -> str:
    """
    Generate a globally unique lot ID.
    
    German tax (§ 20 EStG) requires FIFO per ISIN globally, not per account.
    
    Args:
        symbol: Asset symbol (e.g., AAPL, USD, BTC)
        acquired_at: Acquisition date
        isin: ISIN code for stocks/ETFs (e.g., US0378331005)
    
    Returns:
        str: Unique lot ID
            - Stocks/ETFs: {isin}_{date}_{uuid} (e.g., US0378331005_20250826_abc123)
            - Currencies/Crypto: {symbol}_{date}_{uuid} (e.g., USD_20250826_def456)
    """
    # Use ISIN as identifier for stocks/ETFs, symbol for currencies/crypto
    identifier = isin if isin else symbol
    date_str = acquired_at.strftime('%Y%m%d')
    unique_id = str(uuid.uuid4())[:8]
    return f"{identifier}_{date_str}_{unique_id}"


def create_lot_from_transaction(
    db: Session,
    transaction: Transaction
) -> Optional[Lot]:
    """
    Create a lot from a buy transaction.
    
    German Tax Compliance (§ 20 EStG):
    - For dividend/interest paid in foreign currency (e.g., USD): Creates a foreign currency lot
    - The lot tracks the currency acquisition, not the stock that paid the dividend
    - This is required for proper FX gain/loss calculation when the currency is later sold
    
    Args:
        db: Database session
        transaction: Buy transaction
    
    Returns:
        Lot: Created lot or None if not applicable
    """
    # Create lots for:
    # - buy, transfer_in, deposit, dividend_reinvest: Standard acquisitions
    # - staking_reward, interest, dividend: Income types that create new assets
    # - exchange with positive qty: Acquiring foreign currency (e.g., EUR → USD exchange creates USD lot)
    # - fee with positive qty: Standalone fee refunds (e.g., IBKR "Other Fees" credits)
    #
    # IMPORTANT: Distinguish between:
    # 1. COMMISSIONS (transaction.fee column): Part of trade cost basis, NOT separate lots
    # 2. STANDALONE FEES (transaction.type='fee'): Real cash flows (e.g., IBKR "Other Fees")
    #    - Positive qty = fee refund/credit → Creates lot
    #    - Negative qty = fee payment → Allocates from lot
    if transaction.type in ['buy', 'transfer_in', 'deposit', 'dividend_reinvest', 
                             'staking_reward', 'interest', 'dividend']:
        # Standard acquisition or income
        pass
    elif transaction.type == 'exchange' and transaction.quantity and Decimal(str(transaction.quantity)) > 0:
        # FX exchange: Positive quantity = acquiring currency (creates lot)
        # Example: Exchange EUR → USD, USD side has positive quantity
        logger.debug(
            f"Processing FX exchange (acquisition): {transaction.symbol} "
            f"quantity={transaction.quantity} (positive = acquiring)"
        )
    elif transaction.type == 'withholding_tax' and transaction.quantity and Decimal(str(transaction.quantity)) > 0:
        # Withholding tax refund/cancellation: Positive quantity = acquisition (creates lot)
        # Example: Tax refund returns USD to balance
        logger.debug(
            f"Processing withholding tax refund: {transaction.symbol} "
            f"quantity={transaction.quantity} (positive = refund/cancellation)"
        )
    elif transaction.type == 'fee' and transaction.quantity and Decimal(str(transaction.quantity)) > 0:
        # Standalone fee refund/credit: Positive quantity = acquisition (creates lot)
        # Example: IBKR "Other Fees" credit (+0.34 USD)
        # This is different from trade commissions (which are in transaction.fee column)
        logger.debug(
            f"Processing standalone fee refund: {transaction.symbol} "
            f"quantity={transaction.quantity} (positive = credit from IBKR 'Other Fees')"
        )
    else:
        # Not a lot-creating transaction
        return None
    
    if not transaction.symbol or not transaction.quantity:
        return None
    
    # =========================================================================
    # GERMAN TAX COMPLIANCE: Dividend/Interest in foreign currency
    # =========================================================================
    # For dividend/interest transactions, the symbol is the stock ticker (for DRIP detection)
    # but the LOT must be created for the CURRENCY received (currency_native).
    # 
    # Example: VGK pays $2.77 dividend
    #   - transaction.symbol = 'VGK' (stock that paid dividend)
    #   - transaction.currency_native = 'USD' (currency received)
    #   - LOT should be for USD, not VGK
    #
    # This is required for German tax because:
    # 1. The USD received is a taxable FX acquisition
    # 2. When USD is later sold/exchanged, FIFO applies to USD lots
    # 3. FX gain/loss must be calculated based on EUR cost basis at acquisition
    # =========================================================================
    lot_symbol = transaction.symbol  # Default: use transaction symbol
    
    if transaction.type in ['dividend', 'interest']:
        # For dividend/interest: Create lot for the CURRENCY received, not the stock symbol
        if transaction.currency_native and transaction.currency_native != BASE_CURRENCY:
            lot_symbol = transaction.currency_native
            logger.debug(
                f"Dividend/interest transaction {transaction.id}: Creating lot for currency "
                f"'{lot_symbol}' (not stock symbol '{transaction.symbol}')"
            )
        elif transaction.currency_native == BASE_CURRENCY:
            # Dividend/interest paid in base currency - no lot needed
            logger.debug(
                f"Skipping lot creation for dividend/interest in base currency {BASE_CURRENCY}"
            )
            return None
    
    # Skip lot creation for base currency (EUR) - no cost basis needed for base currency
    if lot_symbol == BASE_CURRENCY:
        logger.debug(
            f"Skipping lot creation for base currency {BASE_CURRENCY}"
        )
        return None
    
    # Skip lot creation for other excluded asset classes (except cash - we need foreign currency lots)
    # Foreign currencies (USD, THB, etc.) NEED lots for German tax compliance
    if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
        # Exception: If asset_class='cash' but it's a foreign currency, CREATE the lot
        if transaction.asset_class == 'cash' and lot_symbol != BASE_CURRENCY:
            logger.debug(
                f"Creating lot for foreign currency {lot_symbol} "
                f"(asset_class='cash' but not base currency)"
            )
            # Continue to lot creation
        else:
            logger.debug(
                f"Skipping lot creation for {lot_symbol} "
                f"(asset_class '{transaction.asset_class}' is excluded from lot tracking)"
            )
            return None
    
    try:
        quantity = abs(Decimal(str(transaction.quantity)))
        
        # Calculate fee in base currency for cost basis
        # Per German tax law (§ 20 EStG) and international standards:
        # Cost basis = Purchase price + All acquisition costs (fees, commissions)
        fee_base = Decimal('0')
        fee_native = Decimal('0')
        if transaction.fee and Decimal(str(transaction.fee)) > 0:
            fee = abs(Decimal(str(transaction.fee)))
            fee_native = fee  # Store native fee
            # Convert fee to base currency
            fee_currency = getattr(transaction, 'fee_currency', None)
            if fee_currency and fee_currency == transaction.currency_base:
                # Fee is already in base currency
                fee_base = fee
            elif fee_currency and fee_currency == transaction.currency_native and transaction.exchange_rate_to_base:
                # Fee is in transaction currency - convert using same exchange rate
                fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
            elif transaction.exchange_rate_to_base:
                # Fallback: use transaction exchange rate (assumes fee in transaction currency)
                fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
            else:
                # No conversion possible - log warning and use fee as-is (best effort)
                logger.warning(
                    f"Transaction {transaction.id}: Cannot convert fee {fee} {fee_currency} to base currency - using as-is"
                )
                fee_base = fee
        
        # Add blockchain_fee if present (for crypto transactions)
        if hasattr(transaction, 'blockchain_fee') and transaction.blockchain_fee:
            blockchain_fee = abs(Decimal(str(transaction.blockchain_fee)))
            fee_native += blockchain_fee
            if transaction.exchange_rate_to_base:
                fee_base += blockchain_fee * Decimal(str(transaction.exchange_rate_to_base))
            else:
                fee_base += blockchain_fee
        
        # Calculate total cost basis in base currency (value + fees)
        if transaction.value_base and quantity > 0:
            gross_value_base = abs(Decimal(str(transaction.value_base)))
        elif transaction.price:
            gross_value_base = quantity * Decimal(str(transaction.price))
            if transaction.exchange_rate_to_base:
                gross_value_base *= Decimal(str(transaction.exchange_rate_to_base))
        else:
            logger.warning(f"Cannot determine price for transaction {transaction.id}")
            return None
        
        total_cost_basis_base = gross_value_base + fee_base
        
        # Calculate total cost basis in native currency
        if transaction.value_native:
            gross_value_native = abs(Decimal(str(transaction.value_native)))
        elif transaction.price and quantity > 0:
            gross_value_native = quantity * Decimal(str(transaction.price))
        else:
            gross_value_native = gross_value_base  # Fallback
        
        total_cost_basis_native = gross_value_native + fee_native
        
        # Get portfolio_id from transaction
        if not transaction.portfolio_id:
            logger.error(f"Transaction {transaction.id} missing portfolio_id - cannot create lot")
            return None
        
        # Get ISIN and conid from transaction (for German tax compliance § 20 EStG)
        # Note: For dividend/interest currency lots, ISIN is not applicable (currencies have no ISIN)
        isin = getattr(transaction, 'isin', None) if lot_symbol == transaction.symbol else None
        conid = getattr(transaction, 'conid', None) if lot_symbol == transaction.symbol else None
        
        # Generate lot ID (ISIN-based for stocks, symbol-based for currencies/crypto)
        lot_id = generate_lot_id(lot_symbol, transaction.occurred_at, isin)
        
        # Get currency from transaction - MUST be set, no fallbacks
        if not hasattr(transaction, 'currency_base') or not transaction.currency_base:
            raise ValueError(
                f"Transaction {transaction.id} missing currency_base. "
                f"Cannot create lot without base currency. Symbol: {lot_symbol}"
            )
        currency_base = transaction.currency_base
        currency_native = transaction.currency_native
        
        # Create lot with new schema
        lot = Lot(
            lot_id=lot_id,
            portfolio_id=transaction.portfolio_id,
            transaction_id=transaction.id,  # NEW: Link to source transaction
            symbol=lot_symbol,  # Use lot_symbol (may differ from transaction.symbol for dividends)
            isin=isin,
            conid=conid,
            acquired_at=transaction.occurred_at,  # Renamed from buy_date
            quantity=quantity,  # Renamed from qty
            remaining_quantity=quantity,  # Renamed from remaining_qty
            total_cost_basis_base=total_cost_basis_base,  # NEW: Total cost including fees
            fee_base=fee_base if fee_base > 0 else None,  # NEW: Explicit fee tracking
            currency_base=currency_base,  # Renamed from base_currency
            total_cost_basis_native=total_cost_basis_native,  # NEW
            fee_native=fee_native if fee_native > 0 else None,  # NEW
            currency_native=currency_native,
            created_at=now_utc(),
            last_updated=now_utc()  # NEW
        )
        
        db.add(lot)
        
        # Update transaction with lot_id
        transaction.lot_id = lot_id
        
        db.commit()
        db.refresh(lot)
        
        # Calculate cost per unit for logging
        cost_per_unit = total_cost_basis_base / quantity if quantity > 0 else Decimal('0')
        
        # Log with lot_symbol (may differ from transaction.symbol for dividends/interest)
        logger.info(
            f"Created lot {lot_id} for portfolio {transaction.portfolio_id}, {lot_symbol}: "
            f"qty={quantity}, total_cost_basis={total_cost_basis_base} {currency_base} "
            f"(cost/unit={cost_per_unit:.4f}, fee={fee_base})"
            + (f" [from {transaction.type} on {transaction.symbol}]" if lot_symbol != transaction.symbol else "")
        )
        
        return lot
        
    except Exception as e:
        logger.error(f"Error creating lot from transaction {transaction.id}: {e}", exc_info=True)
        db.rollback()
        return None


def get_open_lots_fifo(
    db: Session,
    symbol: str,
    portfolio_id: int = None,
    isin: str = None
) -> List[Lot]:
    """
    Get open lots for a symbol in FIFO order (oldest first).
    
    German tax (§ 20 EStG) requires FIFO per ISIN globally across all accounts.
    
    Args:
        db: Database session
        symbol: Asset symbol
        portfolio_id: Portfolio ID (DEPRECATED - kept for backward compatibility)
        isin: ISIN code (for stocks/ETFs) - uses global FIFO
    
    Returns:
        List[Lot]: List of open lots ordered by acquired_at
        
    Examples:
        # Stock: Get all AAPL lots across all accounts
        get_open_lots_fifo(db, 'AAPL', isin='US0378331005')
        
        # Currency: Get all USD lots across all accounts
        get_open_lots_fifo(db, 'USD')  # isin=None for currencies
    """
    if isin:
        # Stocks/ETFs: Query by ISIN (combines all accounts/portfolios)
        query = db.query(Lot).filter(
            and_(
                Lot.isin == isin,
                Lot.remaining_quantity > 0
            )
        ).order_by(Lot.acquired_at.asc())
    else:
        # Currencies/Crypto: Query by symbol with NULL ISIN (combines all accounts/portfolios)
        query = db.query(Lot).filter(
            and_(
                Lot.symbol == symbol,
                Lot.isin.is_(None),
                Lot.remaining_quantity > 0
            )
        ).order_by(Lot.acquired_at.asc())
    
    return query.all()


def allocate_sale_to_lots(
    db: Session,
    transaction: Transaction,
    lots: Optional[List[Lot]] = None
) -> Tuple[List[Dict], Decimal]:
    """
    Allocate a sale/reduction transaction to lots using FIFO.
    Creates LotAllocation records to track realized gains.
    
    Handles:
    - sell, transfer_out, withdrawal: Normal sales/transfers
    - fee: Small reductions (treated like partial sales)
    - exchange with negative qty: Disposing foreign currency (e.g., USD → EUR exchange)
    
    Args:
        db: Database session
        transaction: Sale/reduction transaction
        lots: Optional pre-fetched lots (will fetch if not provided)
    
    Returns:
        tuple: (list of allocations, total realized gain/loss)
    """
    # Handle reductions/disposals:
    # - sell, transfer_out, withdrawal, fee, staking_loss: Standard disposals
    # - exchange with negative qty: Disposing foreign currency (allocates to lots)
    is_disposal = False
    
    if transaction.type in ['sell', 'transfer_out', 'withdrawal', 'fee', 'staking_loss']:
        is_disposal = True
    elif transaction.type == 'exchange' and transaction.quantity and Decimal(str(transaction.quantity)) < 0:
        # FX exchange: Negative quantity = disposing currency (allocates to lots)
        # Example: Exchange EUR → USD, EUR side has negative quantity
        is_disposal = True
        logger.debug(
            f"Processing FX exchange (disposal): {transaction.symbol} "
            f"quantity={transaction.quantity} (negative = disposing)"
        )
    elif transaction.type == 'withholding_tax' and transaction.quantity and Decimal(str(transaction.quantity)) < 0:
        # Withholding tax deduction: Negative quantity = disposal (allocates to lots)
        # Example: Tax withheld on interest reduces USD balance
        is_disposal = True
        logger.debug(
            f"Processing withholding tax deduction: {transaction.symbol} "
            f"quantity={transaction.quantity} (negative = deduction)"
        )
    
    if not is_disposal:
        logger.warning(
            f"Transaction {transaction.id} is not a disposal type "
            f"(type={transaction.type}, quantity={transaction.quantity})"
        )
        return [], Decimal('0')
    
    if not transaction.symbol or not transaction.quantity:
        logger.warning(f"Transaction {transaction.id} missing symbol or quantity")
        return [], Decimal('0')
    
    if not transaction.portfolio_id:
        logger.error(f"Transaction {transaction.id} missing portfolio_id - cannot allocate lots")
        return [], Decimal('0')
    
    # Skip lot allocation for base currency (EUR) - no cost basis needed
    if transaction.symbol == BASE_CURRENCY:
        logger.debug(
            f"Skipping lot allocation for base currency {BASE_CURRENCY}"
        )
        return [], Decimal('0')
    
    # Skip lot allocation for other excluded asset classes (except cash - we need foreign currency lots)
    # Foreign currencies (USD, THB, etc.) NEED lot allocation for German tax compliance
    if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
        # Exception: If asset_class='cash' but it's a foreign currency, ALLOCATE to lots
        if transaction.asset_class == 'cash' and transaction.symbol != BASE_CURRENCY:
            logger.debug(
                f"Allocating sale for foreign currency {transaction.symbol} "
                f"(asset_class='cash' but not base currency)"
            )
            # Continue to lot allocation
        else:
            logger.debug(
                f"Skipping lot allocation for {transaction.symbol} "
                f"(asset_class '{transaction.asset_class}' is excluded from lot tracking)"
            )
            return [], Decimal('0')
    
    qty_to_allocate = abs(Decimal(str(transaction.quantity)))
    
    # Get ISIN from transaction (for global FIFO)
    isin = getattr(transaction, 'isin', None)
    
    # Get open lots if not provided - Uses global FIFO by ISIN
    if lots is None:
        lots = get_open_lots_fifo(db, transaction.symbol, isin=isin)
    
    if not lots:
        logger.warning(
            f"No open lots found for {transaction.symbol} "
            f"(isin={isin or 'NULL'}) across all accounts"
        )
        return [], Decimal('0')
    
    # Get base_currency from transaction or use global base currency
    from utils.app_config import get_global_base_currency
    base_currency = transaction.currency_base if hasattr(transaction, 'currency_base') and transaction.currency_base else get_global_base_currency()
    
    # Calculate fee in base currency (to deduct from sale proceeds)
    # Per German tax law (§ 20 EStG) and international standards:
    # Net proceeds = Sale price - All selling costs (fees, commissions)
    fee_base = Decimal('0')
    if transaction.fee and Decimal(str(transaction.fee)) > 0:
        fee = abs(Decimal(str(transaction.fee)))
        # Convert fee to base currency
        fee_currency = getattr(transaction, 'fee_currency', None)
        if fee_currency and fee_currency == base_currency:
            # Fee is already in base currency
            fee_base = fee
        elif fee_currency and fee_currency == transaction.currency_native and transaction.exchange_rate_to_base:
            # Fee is in transaction currency - convert using same exchange rate
            fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
        elif transaction.exchange_rate_to_base:
            # Fallback: use transaction exchange rate (assumes fee in transaction currency)
            fee_base = fee * Decimal(str(transaction.exchange_rate_to_base))
        else:
            # No conversion possible - log warning and use fee as-is (best effort)
            logger.warning(
                f"Transaction {transaction.id}: Cannot convert fee {fee} {fee_currency} to base currency - using as-is"
            )
            fee_base = fee
    
    # Add blockchain_fee if present (for crypto transactions)
    if hasattr(transaction, 'blockchain_fee') and transaction.blockchain_fee:
        blockchain_fee = abs(Decimal(str(transaction.blockchain_fee)))
        if transaction.exchange_rate_to_base:
            fee_base += blockchain_fee * Decimal(str(transaction.exchange_rate_to_base))
        else:
            fee_base += blockchain_fee
    
    # Calculate gross sale proceeds (before fee deduction)
    if transaction.value_base and qty_to_allocate > 0:
        total_gross_proceeds_base = abs(Decimal(str(transaction.value_base)))
    elif transaction.price:
        total_gross_proceeds_base = qty_to_allocate * Decimal(str(transaction.price))
        if transaction.exchange_rate_to_base:
            total_gross_proceeds_base *= Decimal(str(transaction.exchange_rate_to_base))
    else:
        logger.warning(f"Cannot determine sale price for transaction {transaction.id}")
        total_gross_proceeds_base = Decimal('0')
    
    # Net proceeds = gross - fees
    total_net_proceeds_base = total_gross_proceeds_base - fee_base
    
    allocations = []
    total_realized_gain = Decimal('0')
    remaining_to_allocate = qty_to_allocate
    lot_ids_used = []
    
    try:
        for lot in lots:
            if remaining_to_allocate <= 0:
                break
            
            lot_remaining = Decimal(str(lot.remaining_quantity))
            
            if lot_remaining <= 0:
                continue
            
            # Allocate from this lot
            qty_from_lot = min(remaining_to_allocate, lot_remaining)
            
            # Calculate proportion of this allocation
            allocation_ratio = qty_from_lot / qty_to_allocate if qty_to_allocate > 0 else Decimal('0')
            
            # Calculate cost basis for this portion (from lot's total_cost_basis_base)
            cost_per_unit = Decimal(str(lot.total_cost_basis_base)) / Decimal(str(lot.quantity)) if lot.quantity else Decimal('0')
            allocated_cost_basis_base = qty_from_lot * cost_per_unit
            
            # Calculate sale proceeds for this portion (proportional)
            gross_proceeds_base = total_gross_proceeds_base * allocation_ratio
            sale_fee_base = fee_base * allocation_ratio
            net_proceeds_base = total_net_proceeds_base * allocation_ratio
            
            # Calculate realized gain/loss
            realized_gain_base = net_proceeds_base - allocated_cost_basis_base
            
            # Update lot remaining quantity and last_updated
            lot.remaining_quantity = lot_remaining - qty_from_lot
            lot.last_updated = now_utc()
            
            # Create LotAllocation record with new schema
            lot_allocation = LotAllocation(
                transaction_id=transaction.id,
                lot_id=lot.lot_id,
                allocated_at=now_utc(),  # NEW
                allocated_quantity=qty_from_lot,  # Renamed from qty_allocated
                allocated_cost_basis_base=allocated_cost_basis_base,  # Renamed from cost_basis
                gross_proceeds_base=gross_proceeds_base,  # NEW
                sale_fee_base=sale_fee_base if sale_fee_base > 0 else None,  # NEW
                net_proceeds_base=net_proceeds_base,  # Renamed from sale_proceeds
                realized_gain_base=realized_gain_base,  # Renamed from realized_gain
                currency_base=base_currency,  # Local variable 'base_currency' mapped to column 'currency_base'
                created_at=now_utc(),
                last_updated=now_utc()  # NEW
            )
            db.add(lot_allocation)
            
            # Record allocation for return value
            allocations.append({
                'lot_id': lot.lot_id,
                'allocated_quantity': qty_from_lot,
                'allocated_cost_basis_base': allocated_cost_basis_base,
                'gross_proceeds_base': gross_proceeds_base,
                'sale_fee_base': sale_fee_base,
                'net_proceeds_base': net_proceeds_base,
                'realized_gain_base': realized_gain_base,
                'acquired_at': lot.acquired_at,
                'cost_per_unit': cost_per_unit
            })
            
            lot_ids_used.append(lot.lot_id)
            total_realized_gain += realized_gain_base
            remaining_to_allocate -= qty_from_lot
            
            logger.debug(
                f"Allocated {qty_from_lot} from lot {lot.lot_id}: "
                f"cost={allocated_cost_basis_base}, proceeds={net_proceeds_base}, gain={realized_gain_base}"
            )
        
        # Only warn if remaining amount exceeds dust tolerance (filter out rounding errors)
        if remaining_to_allocate > DUST_TOLERANCE:
            logger.warning(
                f"Could not fully allocate sale of {transaction.symbol} in portfolio {transaction.portfolio_id}: "
                f"{remaining_to_allocate} units remaining unallocated"
            )
        elif remaining_to_allocate > 0:
            logger.debug(
                f"Ignoring dust amount for {transaction.symbol} in portfolio {transaction.portfolio_id}: "
                f"{remaining_to_allocate} units (below dust tolerance of {DUST_TOLERANCE})"
            )
        
        # Update transaction with lot_id reference
        # Store as comma-separated list if multiple lots
        transaction.lot_id = ','.join(lot_ids_used) if lot_ids_used else None
        
        db.commit()
        
        logger.info(
            f"Allocated sale transaction {transaction.id} to {len(allocations)} lots: "
            f"Total realized gain={total_realized_gain} (after deducting fee_base={fee_base})"
        )
        
        return allocations, total_realized_gain
        
    except Exception as e:
        logger.error(f"Error allocating sale to lots: {e}", exc_info=True)
        db.rollback()
        return [], Decimal('0')


def get_lots_for_symbol(
    db: Session,
    symbol: str,
    include_closed: bool = False
) -> List[Lot]:
    """
    Get all lots for a symbol.
    
    Args:
        db: Database session
        symbol: Asset symbol
        include_closed: Include lots with zero remaining quantity
    
    Returns:
        List[Lot]: List of lots
    """
    query = db.query(Lot).filter(Lot.symbol == symbol)
    
    if not include_closed:
        query = query.filter(Lot.remaining_quantity > 0)
    
    return query.order_by(Lot.acquired_at.asc()).all()


def get_lot_by_id(
    db: Session,
    lot_id: str
) -> Optional[Lot]:
    """
    Get a specific lot by ID.
    
    Args:
        db: Database session
        lot_id: Lot ID
    
    Returns:
        Lot or None
    """
    return db.query(Lot).filter(Lot.lot_id == lot_id).first()


def calculate_unrealized_gain_for_lots(
    db: Session,
    symbol: str,
    current_price: Decimal
) -> Dict:
    """
    Calculate unrealized gain/loss for all open lots of a symbol.
    
    Args:
        db: Database session
        symbol: Asset symbol
        current_price: Current market price
    
    Returns:
        dict: Unrealized gain details
    """
    lots = get_open_lots_fifo(db, symbol)
    
    total_qty = Decimal('0')
    total_cost_basis = Decimal('0')
    lot_details = []
    
    for lot in lots:
        remaining = Decimal(str(lot.remaining_quantity))
        # Calculate cost per unit from total_cost_basis_base / quantity
        cost_per_unit = Decimal(str(lot.total_cost_basis_base)) / Decimal(str(lot.quantity)) if lot.quantity else Decimal('0')
        cost_basis = remaining * cost_per_unit
        market_value = remaining * current_price
        unrealized_gain = market_value - cost_basis
        
        total_qty += remaining
        total_cost_basis += cost_basis
        
        lot_details.append({
            'lot_id': lot.lot_id,
            'acquired_at': lot.acquired_at,
            'remaining_quantity': remaining,
            'cost_per_unit': cost_per_unit,
            'cost_basis': cost_basis,
            'market_value': market_value,
            'unrealized_gain': unrealized_gain,
            'gain_percentage': (unrealized_gain / cost_basis * 100) if cost_basis > 0 else Decimal('0')
        })
    
    total_market_value = total_qty * current_price
    total_unrealized_gain = total_market_value - total_cost_basis
    
    return {
        'symbol': symbol,
        'current_price': current_price,
        'total_quantity': total_qty,
        'total_cost_basis': total_cost_basis,
        'total_market_value': total_market_value,
        'total_unrealized_gain': total_unrealized_gain,
        'avg_cost_per_unit': (total_cost_basis / total_qty) if total_qty > 0 else Decimal('0'),
        'lots': lot_details,
        'lot_count': len(lots)
    }


def get_realized_gains_for_period(
    db: Session,
    symbol: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict:
    """
    Calculate realized gains from closed lots for a period.
    This requires tracking sell transactions with lot allocations.
    
    Args:
        db: Database session
        symbol: Optional symbol filter
        start_date: Start date
        end_date: End date
    
    Returns:
        dict: Realized gains summary
    """
    # Query sell transactions with lot_id
    query = db.query(Transaction).filter(
        and_(
            Transaction.type.in_(['sell', 'transfer_out', 'withdrawal']),
            Transaction.lot_id.isnot(None)
        )
    )
    
    if symbol:
        query = query.filter(Transaction.symbol == symbol)
    if start_date:
        query = query.filter(Transaction.occurred_at >= start_date)
    if end_date:
        query = query.filter(Transaction.occurred_at <= end_date)
    
    transactions = query.order_by(Transaction.occurred_at.asc()).all()
    
    total_realized = Decimal('0')
    transaction_details = []
    
    for tx in transactions:
        # Parse lot_ids (may be comma-separated)
        lot_ids = tx.lot_id.split(',') if tx.lot_id else []
        
        # Get lots
        lots_used = []
        for lot_id in lot_ids:
            lot = get_lot_by_id(db, lot_id.strip())
            if lot:
                lots_used.append(lot)
        
        # Re-calculate realized gain for this transaction
        # (This is a reconstruction - ideally we'd query lot_allocations table)
        if tx.value_base and tx.quantity and lots_used:
            qty = abs(Decimal(str(tx.quantity)))
            sale_proceeds = abs(Decimal(str(tx.value_base)))
            
            # Estimate cost basis from lots (using cost per unit)
            # This is approximate if lots have changed since sale
            cost_basis_estimate = Decimal('0')
            for lot in lots_used:
                cost_per_unit = Decimal(str(lot.total_cost_basis_base)) / Decimal(str(lot.quantity)) if lot.quantity else Decimal('0')
                cost_basis_estimate += cost_per_unit * (qty / len(lots_used))
            
            realized_gain = sale_proceeds - cost_basis_estimate
            total_realized += realized_gain
            
            transaction_details.append({
                'transaction_id': tx.id,
                'date': tx.occurred_at,
                'symbol': tx.symbol,
                'quantity': qty,
                'sale_proceeds': sale_proceeds,
                'cost_basis': cost_basis_estimate,
                'realized_gain': realized_gain,
                'lots_used': [lot.lot_id for lot in lots_used]
            })
    
    return {
        'total_realized_gain': total_realized,
        'transaction_count': len(transactions),
        'transactions': transaction_details,
        'period': {
            'start_date': start_date,
            'end_date': end_date
        }
    }


def reconcile_lots_from_transactions(
    db: Session,
    symbol: Optional[str] = None
) -> Tuple[int, int, List[str]]:
    """
    Rebuild all lots from buy transactions and allocate to sells (FIFO).
    This is a full reconciliation that rebuilds lot state from scratch.
    
    Args:
        db: Database session
        symbol: Optional symbol to reconcile (None = all symbols)
    
    Returns:
        tuple: (lots_created, sales_allocated, warnings)
    """
    logger.info(f"Starting lot reconciliation for symbol: {symbol or 'ALL'}")
    
    lots_created = 0
    sales_allocated = 0
    warnings = []
    
    try:
        # Get all buy transactions (including dividend reinvestments and staking rewards)
        # German tax compliance: 
        # - staking_reward creates income lot with cost basis
        # - dividend/interest in foreign currency create currency lots (e.g., USD)
        buy_query = db.query(Transaction).filter(
            Transaction.type.in_(['buy', 'transfer_in', 'deposit', 'dividend_reinvest', 
                                  'staking_reward', 'dividend', 'interest'])
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            buy_query = buy_query.filter(Transaction.symbol == symbol)
        
        buy_transactions = buy_query.all()
        
        # Get all exchange transactions with POSITIVE quantity (acquisitions)
        # FX exchanges with positive quantity create lots (acquiring foreign currency)
        exchange_buy_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'exchange',
                Transaction.quantity > 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            exchange_buy_query = exchange_buy_query.filter(Transaction.symbol == symbol)
        
        exchange_buy_transactions = exchange_buy_query.all()
        
        # Get all withholding_tax transactions with POSITIVE quantity (refunds/cancellations)
        # Tax refunds with positive quantity create lots (returned to balance)
        withholding_tax_refund_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'withholding_tax',
                Transaction.quantity > 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            withholding_tax_refund_query = withholding_tax_refund_query.filter(Transaction.symbol == symbol)
        
        withholding_tax_refund_transactions = withholding_tax_refund_query.all()
        
        # Get all fee transactions with POSITIVE quantity (standalone fee refunds)
        # IMPORTANT: These are IBKR "Other Fees" (type='fee'), NOT trade commissions
        # Standalone fee refunds with positive quantity create lots (credited to balance)
        # Example: IBKR "Other Fees" credit (+0.34 USD)
        fee_refund_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'fee',
                Transaction.quantity > 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            fee_refund_query = fee_refund_query.filter(Transaction.symbol == symbol)
        
        fee_refund_transactions = fee_refund_query.all()
        
        # Combine all acquisition transactions
        all_buy_transactions = (buy_transactions + exchange_buy_transactions + 
                                withholding_tax_refund_transactions + fee_refund_transactions)
        logger.info(
            f"Found {len(buy_transactions)} standard buys + "
            f"{len(exchange_buy_transactions)} FX acquisitions (exchange with +qty) + "
            f"{len(withholding_tax_refund_transactions)} withholding tax refunds + "
            f"{len(fee_refund_transactions)} standalone fee refunds (IBKR 'Other Fees')"
        )
        
        # Delete existing lot allocations FIRST (before deleting lots)
        # This prevents orphaned FK references since lot_allocations depends on lots
        if symbol:
            # Get lot_ids for this symbol
            lot_ids_to_delete = db.query(Lot.lot_id).filter(Lot.symbol == symbol).all()
            lot_ids = [lid[0] for lid in lot_ids_to_delete]
            
            if lot_ids:
                deleted_allocations = db.query(LotAllocation).filter(
                    LotAllocation.lot_id.in_(lot_ids)
                ).delete(synchronize_session=False)
                logger.info(f"Deleted {deleted_allocations} allocations for {len(lot_ids)} {symbol} lots")
        else:
            # Delete ALL allocations
            deleted_allocations = db.query(LotAllocation).delete()
            logger.info(f"Deleted {deleted_allocations} lot allocations")
        
        db.commit()
        
        # Delete existing lots for this symbol
        if symbol:
            deleted_lots = db.query(Lot).filter(Lot.symbol == symbol).delete()
        else:
            deleted_lots = db.query(Lot).delete()
        
        logger.info(f"Deleted {deleted_lots} lots")
        db.commit()
        
        # Create lots from buy transactions (including exchange acquisitions)
        for tx in all_buy_transactions:
            lot = create_lot_from_transaction(db, tx)
            if lot:
                lots_created += 1
        
        # Get all sell transactions
        # NOTE: 'fee' is NOT included here - it's handled separately below with fee_payment_query
        # to properly distinguish positive (refund) vs negative (payment) fee transactions
        sell_query = db.query(Transaction).filter(
            Transaction.type.in_(['sell', 'transfer_out', 'withdrawal'])
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            sell_query = sell_query.filter(Transaction.symbol == symbol)
        
        sell_transactions = sell_query.all()
        
        # Get all exchange transactions with NEGATIVE quantity (disposals)
        # FX exchanges with negative quantity allocate to lots (disposing foreign currency)
        exchange_sell_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'exchange',
                Transaction.quantity < 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            exchange_sell_query = exchange_sell_query.filter(Transaction.symbol == symbol)
        
        exchange_sell_transactions = exchange_sell_query.all()
        
        # Get all withholding_tax transactions with NEGATIVE quantity (deductions)
        # Tax deductions with negative quantity allocate to lots (withheld from balance)
        withholding_tax_deduction_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'withholding_tax',
                Transaction.quantity < 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            withholding_tax_deduction_query = withholding_tax_deduction_query.filter(Transaction.symbol == symbol)
        
        withholding_tax_deduction_transactions = withholding_tax_deduction_query.all()
        
        # Get all fee transactions with NEGATIVE quantity (standalone fee payments)
        # IMPORTANT: These are IBKR "Other Fees" (type='fee'), NOT trade commissions
        # Standalone fee payments with negative quantity allocate to lots (debited from balance)
        # Example: IBKR "Other Fees" charge (-0.34 USD)
        fee_payment_query = db.query(Transaction).filter(
            and_(
                Transaction.type == 'fee',
                Transaction.quantity < 0
            )
        ).order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        
        if symbol:
            fee_payment_query = fee_payment_query.filter(Transaction.symbol == symbol)
        
        fee_payment_transactions = fee_payment_query.all()
        
        # Combine all disposal transactions
        all_sell_transactions = (sell_transactions + exchange_sell_transactions + 
                                 withholding_tax_deduction_transactions + fee_payment_transactions)
        logger.info(
            f"Found {len(sell_transactions)} standard sells + "
            f"{len(exchange_sell_transactions)} FX disposals (exchange with -qty) + "
            f"{len(withholding_tax_deduction_transactions)} withholding tax deductions + "
            f"{len(fee_payment_transactions)} standalone fee payments (IBKR 'Other Fees')"
        )
        
        # Allocate sells to lots (FIFO)
        for tx in all_sell_transactions:
            if not tx.symbol:
                continue
            
            # Skip allocation for base currency (EUR) - no cost basis needed
            if tx.symbol == BASE_CURRENCY:
                logger.debug(
                    f"Skipping allocation for base currency {BASE_CURRENCY}"
                )
                continue
            
            # Skip allocation for other excluded asset classes (except cash - we need foreign currency lots)
            # Foreign currencies (USD, THB, etc.) NEED lot allocation for German tax compliance
            if tx.asset_class and tx.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
                # Exception: If asset_class='cash' but it's a foreign currency, ALLOCATE to lots
                if tx.asset_class == 'cash' and tx.symbol != BASE_CURRENCY:
                    logger.debug(
                        f"Allocating sale for foreign currency {tx.symbol} "
                        f"(asset_class='cash' but not base currency)"
                    )
                    # Continue to lot allocation
                else:
                    logger.debug(
                        f"Skipping allocation for {tx.symbol} sell "
                        f"(asset_class '{tx.asset_class}' is excluded from lot tracking)"
                    )
                    continue
            
            # Global FIFO: Get lots by ISIN (stocks/ETFs) or symbol (currencies/crypto)
            # No portfolio_id filter - lots are combined across all accounts
            isin = getattr(tx, 'isin', None)
            lots = get_open_lots_fifo(db, tx.symbol, isin=isin)
            allocations, realized_gain = allocate_sale_to_lots(db, tx, lots)
            
            if allocations:
                sales_allocated += 1
            else:
                warnings.append(
                    f"Could not allocate sale transaction {tx.id} for {tx.symbol} "
                    f"(isin={isin or 'NULL'})"
                )
        
        logger.info(
            f"Lot reconciliation complete: "
            f"{lots_created} lots created, {sales_allocated} sales allocated, "
            f"{len(warnings)} warnings"
        )
        
        # TODO: Sync Position table cost_basis with lot-based calculations
        # This would ensure Position.cost_basis_base reflects accurate FIFO cost
        # instead of proportional reduction which diverges over time.
        # However, reconcile_lots_from_transactions() doesn't have portfolio_id parameter,
        # so we'd need to either:
        # 1. Add portfolio_id parameter to this function
        # 2. Sync all portfolios (could be expensive)
        # 3. Let positions self-correct during normal transaction processing
        # For now, skip this step during full reconciliation.
        
        return lots_created, sales_allocated, warnings
        
    except Exception as e:
        logger.error(f"Error reconciling lots: {e}", exc_info=True)
        db.rollback()
        return 0, 0, [f"Error: {str(e)}"]


def get_lot_summary_by_symbol(
    db: Session
) -> List[Dict]:
    """
    Get summary of lots grouped by symbol.
    
    Args:
        db: Database session
    
    Returns:
        List[Dict]: Summary by symbol
    """
    # Group by symbol and sum
    results = db.query(
        Lot.symbol,
        func.count(Lot.lot_id).label('lot_count'),
        func.sum(Lot.remaining_quantity).label('total_remaining')
    ).filter(
        Lot.remaining_quantity > 0
    ).group_by(Lot.symbol).all()
    
    summary = []
    for symbol, lot_count, total_remaining in results:
        summary.append({
            'symbol': symbol,
            'open_lot_count': lot_count,
            'total_remaining_quantity': Decimal(str(total_remaining)) if total_remaining else Decimal('0')
        })
    
    return summary