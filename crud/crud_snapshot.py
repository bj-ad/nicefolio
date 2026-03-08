"""
CRUD operations for Snapshot model.
Handles daily portfolio valuation snapshots and historical tracking.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, select, asc
from models import Snapshot, Position, Portfolio, Transaction, MarketData, LotAllocation
from crud.crud_position import get_positions_by_portfolio, calculate_position_market_value
from crud.crud_market_fx import get_latest_price, get_latest_fx_rate
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Dict, Tuple
from utils.logging_config import get_logger

logger = get_logger(__name__)

# Initial NAV price (like an index fund starting at $100)
INITIAL_NAV_PRICE = Decimal('100.00')

# Portfolio types that should NOT have NAV calculated (cash doesn't have "performance")
NAV_EXCLUDED_PORTFOLIO_TYPES = {'cash'}


def calculate_external_cash_flows(
    db: Session,
    portfolio_id: int,
    end_date: Optional[date] = None
) -> Tuple[Decimal, Decimal]:
    """
    Calculate cumulative EXTERNAL cash flows for NAV calculation.
    
    IMPORTANT: Investment portfolios (Securities, Crypto, Commodities) don't hold cash.
    Cash lives in cash portfolios (1, 2, 8). Therefore:
    
    - buy = cash flowing IN from outside to purchase securities
    - sell = cash flowing OUT (securities sold, proceeds leave)
    - transfer_in/transfer_out = external transfers (to/from outside NiceFolio)
    - portfolio_transfer = INTERNAL (between NiceFolio portfolios, NOT counted)
    
    Cash Inflows (deposits_base):
    - deposit: Explicit deposit
    - buy: Cash enters to purchase securities
    - transfer_in: External transfer in
    - opening_balance: Initial investment
    
    Cash Outflows (withdrawals_base):
    - withdrawal: Explicit withdrawal
    - sell: Securities sold, cash leaves
    - transfer_out: External transfer out
    
    NOT counted (internal or performance-related):
    - portfolio_transfer: Internal between portfolios
    - dividend/dividend_reinvest/interest: Income (performance)
    - staking_reward/staking/staking_loss: Staking (performance)
    - fee/withholding_tax: Costs (performance)
    - exchange: Crypto swaps (internal rebalancing)
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        end_date: End date (inclusive)
    
    Returns:
        tuple: (total_external_deposits, total_external_withdrawals)
    """
    from datetime import time as dt_time
    
    query = db.query(Transaction).filter(
        Transaction.portfolio_id == portfolio_id
    )
    
    if end_date:
        end_dt = datetime.combine(end_date, dt_time.max)
        query = query.filter(Transaction.occurred_at <= end_dt)
    
    transactions = query.all()
    
    total_deposits = Decimal('0')
    total_withdrawals = Decimal('0')
    
    # Cash inflows - money entering the portfolio from outside
    deposit_types = ['deposit', 'buy', 'transfer_in', 'opening_balance']
    # Cash outflows - money leaving the portfolio to outside
    withdrawal_types = ['withdrawal', 'sell', 'transfer_out']
    
    for tx in transactions:
        if tx.type in deposit_types and tx.value_base:
            total_deposits += abs(Decimal(str(tx.value_base)))
        elif tx.type in withdrawal_types and tx.value_base:
            total_withdrawals += abs(Decimal(str(tx.value_base)))
    
    return total_deposits, total_withdrawals


def is_nav_applicable(db: Session, portfolio_id: int) -> bool:
    """
    Check if NAV calculation is applicable for this portfolio.
    
    NAV is NOT applicable for:
    - Cash portfolios (value = amount, no "performance")
    - Portfolios with no external cash flows (closed/manual without transactions)
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        
    Returns:
        bool: True if NAV should be calculated
    """
    from utils.portfolios_loader import get_portfolios_loader
    
    try:
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        
        for p in all_portfolios:
            if p['id'] == portfolio_id:
                portfolio_type = p.get('type', '').lower()
                if portfolio_type in NAV_EXCLUDED_PORTFOLIO_TYPES:
                    return False
                
                # Check if portfolio has ANY snapshots with external cash flows
                # (some manually-tracked portfolios have values but no transactions)
                has_flows = db.query(Snapshot).filter(
                    Snapshot.portfolio_id == portfolio_id,
                    (Snapshot.deposits_base > 0) | (Snapshot.withdrawals_base > 0)
                ).first() is not None
                
                if not has_flows:
                    logger.info(
                        f"Portfolio {portfolio_id} excluded from NAV - "
                        f"no external cash flows (manual/closed portfolio)"
                    )
                    return False
                
                return True
        
        # Portfolio not found in config, default to True
        return True
    except Exception as e:
        logger.warning(f"Error checking NAV applicability for portfolio {portfolio_id}: {e}")
        return True  # Default to calculating NAV


def calculate_nav_values(
    db: Session,
    portfolio_id: int,
    snapshot_date: date,
    total_value_base: Decimal,
    deposits_base: Decimal = None,
    withdrawals_base: Decimal = None
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Calculate NAV units and price for a new snapshot.
    
    This implements the "unit price" method for normalized portfolio comparison:
    - NAV price starts at 100.00 on portfolio inception
    - EXTERNAL cash flows (deposits/withdrawals) buy/sell units at current price
    - NAV price reflects true investment performance, independent of portfolio size
    
    NOTE: Cash portfolios return (None, None) - NAV doesn't apply to them.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        snapshot_date: Date of the new snapshot
        total_value_base: Current total portfolio value
        deposits_base: Cumulative deposits up to snapshot_date (from calculate_deposits_and_withdrawals)
        withdrawals_base: Cumulative withdrawals up to snapshot_date (from calculate_deposits_and_withdrawals)
    
    Returns:
        Tuple[Optional[Decimal], Optional[Decimal]]: (nav_units, nav_price) or (None, None) for cash portfolios
    """
    # Skip NAV calculation for cash portfolios
    if not is_nav_applicable(db, portfolio_id):
        logger.debug(f"NAV not applicable for portfolio {portfolio_id} (cash portfolio)")
        return None, None
    
    # Get the previous snapshot to determine prior state
    prev_snapshot = db.query(Snapshot).filter(
        Snapshot.portfolio_id == portfolio_id,
        Snapshot.snapshot_date < snapshot_date
    ).order_by(Snapshot.snapshot_date.desc()).first()
    
    if prev_snapshot is None:
        # First snapshot for this portfolio - initialize NAV
        if total_value_base > 0:
            nav_price = INITIAL_NAV_PRICE
            nav_units = total_value_base / INITIAL_NAV_PRICE
        else:
            nav_price = INITIAL_NAV_PRICE
            nav_units = Decimal('0')
        
        logger.debug(
            f"Initial NAV for portfolio {portfolio_id}: "
            f"units={nav_units:.4f}, price={nav_price:.2f}"
        )
        return nav_units, nav_price
    
    # Get previous NAV values
    prev_units = Decimal(str(prev_snapshot.nav_units or 0))
    prev_price = Decimal(str(prev_snapshot.nav_price or INITIAL_NAV_PRICE))
    
    # If previous snapshot has no NAV data (pre-backfill), use initial values
    if prev_units == 0 and prev_price == INITIAL_NAV_PRICE:
        prev_total_value = Decimal(str(prev_snapshot.total_value_base or 0))
        if prev_total_value > 0:
            prev_units = prev_total_value / INITIAL_NAV_PRICE
            prev_price = INITIAL_NAV_PRICE
    
    # Calculate EXTERNAL cash flows using snapshot cumulative values
    # Note: deposits_base and withdrawals_base are already cumulative and include
    # the correct transaction types (deposit, buy, transfer_in, withdrawal, sell, transfer_out)
    current_deposits = deposits_base if deposits_base is not None else Decimal('0')
    current_withdrawals = withdrawals_base if withdrawals_base is not None else Decimal('0')
    prev_deposits = Decimal(str(prev_snapshot.deposits_base or 0))
    prev_withdrawals = Decimal(str(prev_snapshot.withdrawals_base or 0))
    
    # Calculate daily external cash flow (delta from previous snapshot)
    daily_deposit = current_deposits - prev_deposits
    daily_withdrawal = current_withdrawals - prev_withdrawals
    net_flow = daily_deposit - daily_withdrawal
    
    # Handle cash flows (buying/selling units at previous day's price)
    current_units = prev_units
    if net_flow != 0 and prev_price > 0:
        units_change = net_flow / prev_price
        current_units = prev_units + units_change
        
        logger.debug(
            f"Portfolio {portfolio_id}: External cash flow {net_flow:+.2f} -> "
            f"{units_change:+.4f} units @ {prev_price:.2f}"
        )
    
    # Calculate new price
    if current_units > 0:
        nav_price = total_value_base / current_units
    else:
        # No units (empty portfolio), keep previous price
        nav_price = prev_price
    
    # Ensure price is non-negative
    if nav_price < 0:
        logger.warning(
            f"Portfolio {portfolio_id}: Negative NAV price calculated ({nav_price}), using 0"
        )
        nav_price = Decimal('0')
    
    logger.debug(
        f"NAV for portfolio {portfolio_id} on {snapshot_date}: "
        f"units={current_units:.4f}, price={nav_price:.2f}"
    )
    
    return current_units, nav_price


def create_snapshot(
    db: Session,
    portfolio_id: int,
    snapshot_date: date,
    total_value_base: Decimal,
    currency_base: str,
    total_invested_base: Decimal,
    realized_pnl_base: Decimal,
    unrealized_pnl_base: Decimal,
    deposits_base: Decimal = Decimal('0'),
    withdrawals_base: Decimal = Decimal('0'),
    nav_units: Optional[Decimal] = None,
    nav_price: Optional[Decimal] = None,
    notes: Optional[str] = None
) -> Snapshot:
    """
    Create a new snapshot record.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        snapshot_date: Date of snapshot
        total_value_base: Total portfolio value in base currency
        currency_base: Base currency code (e.g., 'EUR')
        total_invested_base: COST BASIS (sum of cost_basis from open positions/lots).
                           This is NOT net invested capital (deposits - withdrawals).
                           For display of "Invested Capital", use calculate_net_invested_capital().
        realized_pnl_base: Realized profit/loss
        unrealized_pnl_base: Unrealized profit/loss
        deposits_base: Cumulative deposits (cash flow in)
        withdrawals_base: Cumulative withdrawals (cash flow out)
        nav_units: Number of NAV units (for normalized performance tracking)
        nav_price: NAV unit price (starts at 100.0 on inception)
        notes: Optional notes
    
    Returns:
        Snapshot: Created snapshot
    """
    # Calculate NAV values if not provided
    if nav_units is None or nav_price is None:
        nav_units, nav_price = calculate_nav_values(
            db, portfolio_id, snapshot_date,
            total_value_base, deposits_base, withdrawals_base
        )
    
    # Check if snapshot already exists for this date
    existing = db.query(Snapshot).filter(
        and_(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date == snapshot_date
        )
    ).first()
    
    if existing:
        # Update existing snapshot
        existing.total_value_base = total_value_base
        existing.currency_base = currency_base
        existing.total_invested_base = total_invested_base
        existing.realized_pnl_base = realized_pnl_base
        existing.unrealized_pnl_base = unrealized_pnl_base
        existing.deposits_base = deposits_base
        existing.withdrawals_base = withdrawals_base
        existing.nav_units = nav_units
        existing.nav_price = nav_price
        existing.notes = notes
        db.commit()
        db.refresh(existing)
        logger.info(f"Updated existing snapshot for portfolio {portfolio_id} on {snapshot_date}")
        return existing
    
    # Create new snapshot
    snapshot = Snapshot(
        portfolio_id=portfolio_id,
        snapshot_date=snapshot_date,
        total_value_base=total_value_base,
        currency_base=currency_base,
        total_invested_base=total_invested_base,
        realized_pnl_base=realized_pnl_base,
        unrealized_pnl_base=unrealized_pnl_base,
        deposits_base=deposits_base,
        withdrawals_base=withdrawals_base,
        nav_units=nav_units,
        nav_price=nav_price,
        notes=notes,
        created_at=datetime.now(timezone.utc)
    )
    
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    
    # Handle None nav_price in log message
    nav_display = f"{nav_price:.2f}" if nav_price is not None else "N/A"
    logger.info(
        f"Created snapshot for portfolio {portfolio_id} on {snapshot_date}: "
        f"Value=${total_value_base}, P&L=${unrealized_pnl_base + realized_pnl_base}, "
        f"NAV={nav_display}"
    )
    
    return snapshot


def calculate_historical_cost_basis(
    db: Session,
    portfolio_id: int,
    as_of_date: date
) -> Decimal:
    """
    Calculate historical cost basis from lots table by reconstructing remaining_qty as of a specific date.
    
    This function:
    1. Gets all lots acquired on or before as_of_date
    2. For each lot, calculates remaining_qty by subtracting allocations that occurred AFTER as_of_date
    3. Sums (historical_remaining_qty * price_base) for all lots
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        as_of_date: Date to calculate historical cost basis for
        
    Returns:
        Decimal: Total cost basis as of the specified date
    """
    from models import Lot, LotAllocation, Transaction
    from datetime import datetime
    
    # Convert date to end-of-day timestamp for comparison
    as_of_timestamp = datetime.combine(as_of_date, datetime.max.time())
    
    logger.debug(f"Calculating historical cost basis for portfolio {portfolio_id} as of {as_of_date}")
    
    # Get all lots acquired on or before as_of_date
    lots = db.query(Lot).filter(
        Lot.portfolio_id == portfolio_id,
        Lot.acquired_at <= as_of_timestamp
    ).all()
    
    total_cost_basis = Decimal('0')
    
    for lot in lots:
        # Current remaining_quantity
        current_remaining = Decimal(str(lot.remaining_quantity))
        
        # Get allocations that happened AFTER as_of_date
        # We need to ADD these back because they reduced remaining_quantity after our target date
        future_allocations = db.query(LotAllocation).join(
            Transaction, LotAllocation.transaction_id == Transaction.id
        ).filter(
            LotAllocation.lot_id == lot.lot_id,
            Transaction.occurred_at > as_of_timestamp
        ).all()
        
        # Calculate historical remaining_quantity
        historical_remaining = current_remaining
        for alloc in future_allocations:
            historical_remaining += Decimal(str(alloc.allocated_quantity))
        
        # Skip lots that were fully sold before as_of_date
        if historical_remaining <= 0:
            continue
        
        # Calculate cost basis for this lot (per-unit price * historical qty)
        original_qty = Decimal(str(lot.quantity))
        if original_qty > 0:
            per_unit_cost = Decimal(str(lot.total_cost_basis_base)) / original_qty
            lot_cost_basis = historical_remaining * per_unit_cost
        else:
            lot_cost_basis = Decimal('0')
        total_cost_basis += lot_cost_basis
        
        if future_allocations:
            logger.debug(
                f"Lot {lot.lot_id}: current_remaining={current_remaining}, "
                f"future_allocations={len(future_allocations)}, "
                f"historical_remaining={historical_remaining}, "
                f"cost_basis={lot_cost_basis}"
            )
    
    logger.info(f"Historical cost basis for portfolio {portfolio_id} as of {as_of_date}: ${total_cost_basis}")
    
    return total_cost_basis


def calculate_historical_positions(
    db: Session,
    portfolio_id: int,
    as_of_date: date
) -> List[Dict]:
    """
    Calculate positions as they were on a specific historical date.
    
    For SECURITIES portfolios: Reconstructs positions from Lot table (lot-based calculation)
    For CASH portfolios: Aggregates signed transaction amounts (transaction-based calculation)
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        as_of_date: Date to calculate positions for
    
    Returns:
        List[Dict]: List of position dictionaries with symbol, quantity, cost_basis
    """
    from datetime import datetime
    from models import Lot, LotAllocation
    
    # End of day timestamp for the target date
    as_of_timestamp = datetime.combine(as_of_date, datetime.max.time())
    
    # Get portfolio to check if it's a cash portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        logger.warning(f"Portfolio {portfolio_id} not found")
        return []
    
    # Load portfolio config to check portfolio type
    from utils.portfolios_loader import PortfoliosLoader
    
    portfolios_loader = PortfoliosLoader()
    portfolio_type = portfolios_loader.get_portfolio_type(portfolio_id)
    
    # ===================================================================
    # SECURITIES, CRYPTO & COMMODITIES PORTFOLIOS: Use lot-based calculation
    # ===================================================================
    if portfolio_type in ('securities', 'crypto', 'commodities'):
        logger.debug(f"{portfolio_type.capitalize()} portfolio detected (ID {portfolio_id}): using lot-based calculation")
        
        # Get all lots acquired on or before as_of_date
        lots = db.query(Lot).filter(
            Lot.portfolio_id == portfolio_id,
            Lot.acquired_at <= as_of_timestamp
        ).all()
        
        positions_map = {}
        
        # Build lookup for asset_class and symbol_normalized from transactions
        # This is more reliable than positions table, which doesn't include sold assets
        symbol_metadata = {}
        buy_transactions = db.query(Transaction).filter(
            Transaction.portfolio_id == portfolio_id,
            Transaction.type.in_(['buy', 'transfer_in'])
        ).all()
        
        for tx in buy_transactions:
            if tx.symbol and tx.symbol not in symbol_metadata:
                symbol_metadata[tx.symbol] = {
                    'asset_class': tx.asset_class or 'stocks',
                    'symbol_normalized': tx.symbol_normalized or tx.symbol
                }
        
        for lot in lots:
            # Current remaining_quantity
            current_remaining = Decimal(str(lot.remaining_quantity))
            
            # Get allocations that happened AFTER as_of_date
            # We need to ADD these back because they reduced remaining_quantity after our target date
            future_allocations = db.query(LotAllocation).join(
                Transaction, LotAllocation.transaction_id == Transaction.id
            ).filter(
                LotAllocation.lot_id == lot.lot_id,
                Transaction.occurred_at > as_of_timestamp
            ).all()
            
            # Calculate historical remaining_quantity
            historical_remaining = current_remaining
            for alloc in future_allocations:
                historical_remaining += Decimal(str(alloc.allocated_quantity))
            
            # Skip lots that were fully sold before as_of_date
            if historical_remaining <= 0:
                continue
            
            # Get asset_class and symbol_normalized from transaction metadata
            metadata = symbol_metadata.get(lot.symbol, {
                'asset_class': 'stocks',
                'symbol_normalized': lot.symbol
            })
            
            # Aggregate by symbol
            if lot.symbol not in positions_map:
                positions_map[lot.symbol] = {
                    'symbol': lot.symbol,
                    'quantity': Decimal('0'),
                    'cost_basis_base': Decimal('0'),
                    'asset_class': metadata['asset_class'],
                    'symbol_normalized': metadata['symbol_normalized']
                }
            
            # Add this lot's contribution to the position (per-unit price * historical qty)
            original_qty = Decimal(str(lot.quantity))
            if original_qty > 0:
                per_unit_cost = Decimal(str(lot.total_cost_basis_base)) / original_qty
                lot_cost_basis = historical_remaining * per_unit_cost
            else:
                lot_cost_basis = Decimal('0')
            positions_map[lot.symbol]['quantity'] += historical_remaining
            positions_map[lot.symbol]['cost_basis_base'] += lot_cost_basis
        
        historical_positions = [
            pos for pos in positions_map.values()
            if pos['quantity'] != Decimal('0')
        ]
        
        logger.debug(
            f"Calculated {len(historical_positions)} historical positions from lots "
            f"for securities portfolio {portfolio_id} as of {as_of_date}"
        )
        
        return historical_positions
    
    # ===================================================================
    # CASH PORTFOLIOS: Hybrid calculation - lots for foreign currency, transactions for base currency
    # ===================================================================
    logger.debug(f"Cash portfolio detected (ID {portfolio_id}, type='{portfolio_type}'): using hybrid calculation")
    
    # STEP 1: Get foreign currency positions from lots (for tax compliance)
    # Foreign currencies need lot tracking for accurate cost basis and PnL
    lots = db.query(Lot).filter(
        Lot.portfolio_id == portfolio_id,
        Lot.acquired_at <= as_of_timestamp
    ).all()
    
    positions_map = {}
    
    # Process lots for foreign currencies
    for lot in lots:
        # Skip base currency lots (shouldn't exist, but safety check)
        if lot.symbol == portfolio.currency_base:
            continue
        
        # Current remaining_quantity
        current_remaining = Decimal(str(lot.remaining_quantity))
        
        # Get allocations that happened AFTER as_of_date
        future_allocations = db.query(LotAllocation).join(
            Transaction, LotAllocation.transaction_id == Transaction.id
        ).filter(
            LotAllocation.lot_id == lot.lot_id,
            Transaction.occurred_at > as_of_timestamp
        ).all()
        
        # Calculate historical remaining_quantity
        historical_remaining = current_remaining
        for alloc in future_allocations:
            historical_remaining += Decimal(str(alloc.allocated_quantity))
        
        # Skip lots that were fully sold before as_of_date
        if historical_remaining <= 0:
            continue
        
        # Aggregate by symbol
        if lot.symbol not in positions_map:
            positions_map[lot.symbol] = {
                'symbol': lot.symbol,
                'quantity': Decimal('0'),
                'cost_basis_base': Decimal('0'),
                'asset_class': 'cash',
                'symbol_normalized': lot.symbol
            }
        
        # Add this lot's contribution (per-unit price * historical qty)
        original_qty = Decimal(str(lot.quantity))
        if original_qty > 0:
            per_unit_cost = Decimal(str(lot.total_cost_basis_base)) / original_qty
            lot_cost_basis = historical_remaining * per_unit_cost
        else:
            lot_cost_basis = Decimal('0')
        
        positions_map[lot.symbol]['quantity'] += historical_remaining
        positions_map[lot.symbol]['cost_basis_base'] += lot_cost_basis
    
    # STEP 2: Get base currency position from transactions (simple summation, no lots)
    # Base currency doesn't need lot tracking - it's face value
    query = db.query(Transaction).filter(
        and_(
            Transaction.portfolio_id == portfolio_id,
            Transaction.occurred_at <= as_of_timestamp,
            Transaction.symbol == portfolio.currency_base
        )
    )
    
    if portfolio_type == 'cash':
        cash_transaction_types = [
            'deposit', 'withdrawal', 'transfer_in', 'transfer_out',
            'exchange', 'interest', 'fee', 'dividend',
            'withholding_tax', 'balance_adjustment'
        ]
        query = query.filter(Transaction.type.in_(cash_transaction_types))
    
    base_currency_txs = query.order_by(Transaction.occurred_at).all()
    
    if base_currency_txs:
        # Sum base currency transactions
        base_currency_amount = sum(
            Decimal(str(tx.value_native)) if tx.value_native else Decimal('0')
            for tx in base_currency_txs
        )
        
        if base_currency_amount != Decimal('0'):
            positions_map[portfolio.currency_base] = {
                'symbol': portfolio.currency_base,
                'quantity': base_currency_amount,
                'cost_basis_base': base_currency_amount,  # Face value for base currency
                'asset_class': 'cash',
                'symbol_normalized': portfolio.currency_base
            }
    
    # Filter out zero positions
    historical_positions = [
        pos for pos in positions_map.values()
        if pos['quantity'] != Decimal('0')
    ]
    
    logger.debug(
        f"Calculated {len(historical_positions)} historical positions from transactions "
        f"for cash portfolio {portfolio_id} as of {as_of_date}"
    )
    
    return historical_positions


def calculate_portfolio_value(
    db: Session,
    portfolio_id: int,
    as_of_date: Optional[date] = None,
    use_historical_calculation: bool = False
) -> Dict:
    """
    Calculate total portfolio value based on positions and current market prices.
    
    TWO-TIER ARCHITECTURE:
    1. DAILY SNAPSHOTS (default): Use positions table (fast, accurate for current state)
    2. HISTORICAL RECONSTRUCTION (use_historical_calculation=True): Use lot-based calculation
       (resource-intensive, accurate for past dates)
    
    Valuation logic:
    1. Base currency position (e.g., USD in USD portfolio): valued at face value (1:1)
    2. Other currency positions: converted using FX rate from fx_rates table
    3. All other assets: valued using market price from market_data table
    
    Args:
        db: Session database session
        portfolio_id: Portfolio ID
        as_of_date: Date to calculate value for (default: today)
        use_historical_calculation: If True, use lot-based calculation (for historical dates)
                                    If False, use positions table (for daily snapshots)
    
    Returns:
        dict: Portfolio valuation details
    """
    if as_of_date is None:
        as_of_date = date.today()
    
    # Convert date to timestamp (end of day) for historical price lookups
    # This ensures historical snapshots use prices from that date, not future prices
    from datetime import datetime
    as_of_timestamp = datetime.combine(as_of_date, datetime.max.time())
    
    is_today = (as_of_date == date.today())
    calculation_method = "positions table" if (is_today and not use_historical_calculation) else "lot-based historical"
    
    logger.info(
        f"Calculating portfolio value for portfolio {portfolio_id} "
        f"as of {as_of_date} (method: {calculation_method}, using prices up to {as_of_timestamp})"
    )
    
    # Get portfolio to check base currency
    from models import Portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    
    if not portfolio:
        logger.error(f"Portfolio {portfolio_id} not found")
        return {
            'total_market_value': Decimal('0'),
            'total_cost_basis': Decimal('0'),
            'unrealized_pnl': Decimal('0'),
            'position_count': 0,
            'positions': [],
            'missing_prices': ['Portfolio not found'],
            'as_of_date': as_of_date
        }
    
    # ARCHITECTURE DECISION:
    # - For TODAY's snapshot: Use positions table (fast, reflects transaction-processed state)
    # - For HISTORICAL dates: Use lot-based calculation (accurate reconstruction)
    if is_today and not use_historical_calculation:
        # DAILY SNAPSHOT PATH: Use positions table
        logger.debug(f"Using positions table for portfolio {portfolio_id} (today's snapshot)")
        from models import Position
        current_positions = db.query(Position).filter(
            Position.portfolio_id == portfolio_id
        ).all()
        
        # Convert positions to same format as historical positions
        positions_list = []
        total_cost_basis = Decimal('0')
        for pos in current_positions:
            # CRITICAL FIX (Dec 30, 2025): Skip near-zero positions (tolerance-based)
            # Positions can end up with tiny residual quantities due to rounding
            # but still have accumulated cost_basis, causing PnL drift
            if abs(pos.quantity) < Decimal('0.01'):
                if pos.cost_basis_base and abs(Decimal(str(pos.cost_basis_base))) > Decimal('0.01'):
                    logger.warning(
                        f"Skipping near-zero position {pos.symbol} with qty={pos.quantity:.6f} "
                        f"but cost_basis_base={pos.cost_basis_base:.2f} - possible data issue"
                    )
                continue  # Skip near-zero positions
            
            # CRITICAL FIX: For base currency positions (e.g., EUR in EUR-based portfolio),
            # cost_basis should equal quantity (face value). The Position table may have
            # incorrect accumulated cost_basis from transaction processing.
            if pos.symbol == portfolio.currency_base:
                # Base currency: cost_basis = quantity (1:1 face value)
                correct_cost_basis = Decimal(str(pos.quantity))
            else:
                correct_cost_basis = Decimal(str(pos.cost_basis_base))
            
            positions_list.append({
                'symbol': pos.symbol,
                'quantity': Decimal(str(pos.quantity)),
                'cost_basis_base': correct_cost_basis,
                'asset_class': pos.asset_class,
                'symbol_normalized': pos.symbol_normalized
            })
            total_cost_basis += correct_cost_basis
    else:
        # HISTORICAL RECONSTRUCTION PATH: Use lot-based calculation
        logger.debug(f"Using lot-based calculation for portfolio {portfolio_id} (historical reconstruction)")
        positions_list = calculate_historical_positions(db, portfolio_id, as_of_date)
        
        # Calculate total cost basis by summing from positions_list
        # NOTE: calculate_historical_positions() returns cost_basis from:
        #   - Lot table for assets with lots (securities, crypto, foreign currency)
        #   - Transaction table for cash portfolios (includes base currency face value)
        # This is more accurate than calculate_historical_cost_basis() which only uses lots
        # and would miss base currency positions (EUR has no lots by design)
        # 
        # CRITICAL UPDATE (Dec 23, 2025): Include base currency in cost_basis
        # For cash portfolios, "Investment" should equal "Value" for base currency.
        # This ensures Total Invested tracks Total Value for cash holdings.
        total_cost_basis = sum(
            Decimal(str(pos['cost_basis_base'])) 
            for pos in positions_list 
        )
    
    total_market_value = Decimal('0')
    position_values = []
    missing_prices = []
    
    # Track base currency separately (not included in invested/PnL calculations)
    base_currency_value = Decimal('0')
    
    for position_data in positions_list:
        # Get position cost basis for reporting (per-position details)
        position_cost_basis = position_data['cost_basis_base']
        quantity = position_data['quantity']
        symbol = position_data['symbol']
        
        # Check if this is the portfolio's base currency (e.g., EUR in EUR portfolio)
        if symbol == portfolio.currency_base:
            # Base currency: valued at face value (1:1)
            # CRITICAL: Base currency positions ARE included in invested calculations (Dec 23, 2025)
            # They represent cash holdings where Cost = Value
            market_value = quantity
            base_currency_value = market_value
            logger.debug(f"Base currency position {symbol}: {quantity} (face value)")
            
            position_values.append({
                'symbol': symbol,
                'quantity': quantity,
                'cost_basis': quantity,  # Face value is the cost basis
                'current_price': Decimal('1.0'),
                'market_value': market_value,
                'unrealized_pnl': Decimal('0'),  # No PnL for base currency
                'pnl_percentage': Decimal('0')
            })
            
            # Add to total market value
            total_market_value += market_value
            continue
        
        # Try to get market price from market_data table
        # Use as_of_timestamp to get historical prices for backfilling
        # CRITICAL: get_latest_price already handles forward-filling from previous trading days
        market_price = get_latest_price(db, symbol, at_ts=as_of_timestamp)
        
        if market_price:
            # Check if currency conversion needed (Nov 16, 2025 fix)
            # When global base currency (EUR) was introduced, old prices (THB) need conversion
            price = Decimal(str(market_price.price))
            
            if market_price.currency != portfolio.currency_base:
                # Market price is in different currency - need FX conversion
                fx_pair = f"{market_price.currency}/{portfolio.currency_base}"
                
                # DEBUG: Log before FX lookup
                logger.info(
                    f"🔍 FX DEBUG [{symbol}]: Need conversion {market_price.currency} → {portfolio.currency_base}, "
                    f"looking for pair: {fx_pair}, at timestamp: {as_of_timestamp}"
                )
                
                fx_rate_record = get_latest_fx_rate(db, fx_pair, at_ts=as_of_timestamp)
                
                if fx_rate_record:
                    fx_rate = Decimal(str(fx_rate_record.rate))
                    price_converted = price * fx_rate
                    
                    # DEBUG: Detailed logging at INFO level
                    logger.info(
                        f"💰 FX CONVERSION [{symbol}]: {market_price.currency} → {portfolio.currency_base}\n"
                        f"   Pair requested: {fx_pair}\n"
                        f"   Pair found: {fx_rate_record.pair if hasattr(fx_rate_record, 'pair') else 'unknown'}\n"
                        f"   Rate: {fx_rate}\n"
                        f"   Price before: {price} {market_price.currency}\n"
                        f"   Price after: {price_converted} {portfolio.currency_base}\n"
                        f"   Formula: {price} × {fx_rate} = {price_converted}"
                    )
                    
                    price = price_converted
                else:
                    # FX rate not available - log warning and fall back to cost basis
                    logger.warning(
                        f"Position {symbol}: Market price in {market_price.currency} but no FX rate found for "
                        f"{fx_pair}. Using cost basis instead."
                    )
                    # Set market_price to None to trigger fallback logic below
                    market_price = None
            
            if market_price:
                # Calculate position market value
                market_value = quantity * price
                unrealized_pnl = market_value - position_cost_basis
                
                logger.info(
                    f"🔢 POSITION VALUE [{symbol}]: qty={quantity}, price={price} {portfolio.currency_base}, "
                    f"market_value={market_value} {portfolio.currency_base}"
                )
                
                position_values.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'cost_basis': position_cost_basis,
                    'current_price': price,
                    'market_value': market_value,
                    'unrealized_pnl': unrealized_pnl,
                    'pnl_percentage': ((unrealized_pnl / position_cost_basis * 100) if position_cost_basis else Decimal('0'))
                })
                
                total_market_value += market_value
        else:
            # No market price - try FX rate for currency conversion
            # Format: "SYMBOL/BASE" (e.g., "EUR/USD" for EUR position in USD portfolio)
            # Use as_of_timestamp to get historical FX rates for backfilling
            fx_rate_record = get_latest_fx_rate(
                db, 
                f"{symbol}/{portfolio.currency_base}",
                at_ts=as_of_timestamp
            )
            
            if fx_rate_record:
                # Foreign currency position - convert using FX rate
                fx_rate = Decimal(str(fx_rate_record.rate))
                market_value = quantity * fx_rate
                logger.debug(f"Currency position {symbol}: {quantity} × {fx_rate} = {market_value} {portfolio.currency_base}")
                
                position_values.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'cost_basis': position_cost_basis,
                    'current_price': fx_rate,
                    'market_value': market_value,
                    'unrealized_pnl': market_value - position_cost_basis,
                    'pnl_percentage': ((market_value - position_cost_basis) / position_cost_basis * 100) if position_cost_basis else Decimal('0')
                })
                
                total_market_value += market_value
            else:
                # No market price or FX rate - use position cost basis as fallback
                # This is expected for minor currency positions or illiquid assets
                logger.info(f"Position {symbol} in portfolio {portfolio_id} (base: {portfolio.currency_base}): No market/FX price available, using cost basis {position_cost_basis}")
                missing_prices.append(symbol)
                
                position_values.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'cost_basis': position_cost_basis,
                    'current_price': None,
                    'market_value': position_cost_basis,
                    'unrealized_pnl': Decimal('0'),
                    'pnl_percentage': Decimal('0')
                })
                
                total_market_value += position_cost_basis
    
    # Calculate unrealized PnL
    # Standard formula: Market Value - Cost Basis
    # Since base currency is now included in both (Value=Cost), it cancels out (P&L = 0)
    # Only foreign currency positions contribute to unrealized PnL
    unrealized_pnl = total_market_value - total_cost_basis
    
    logger.info(
        f"📊 PORTFOLIO VALUE SUMMARY [{portfolio_id}]:\n"
        f"   Total market value: {total_market_value} {portfolio.currency_base}\n"
        f"   Total cost basis: {total_cost_basis} {portfolio.currency_base}\n"
        f"   Unrealized P&L: {unrealized_pnl} {portfolio.currency_base}\n"
        f"   Position count: {len(positions_list)}\n"
        f"   Missing prices: {missing_prices}"
    )
    
    return {
        'total_market_value': total_market_value,
        'total_cost_basis': total_cost_basis,
        'unrealized_pnl': unrealized_pnl,
        'position_count': len(positions_list),
        'positions': position_values,
        'missing_prices': missing_prices,
        'as_of_date': as_of_date
    }


def calculate_realized_pnl(
    db: Session,
    portfolio_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    as_of_date: Optional[date] = None
) -> Decimal:
    """
    Calculate realized P&L from lot allocations.
    
    Uses LotAllocation records which are created when sell transactions
    are allocated to purchase lots via FIFO. This provides accurate
    realized gains/losses without recalculation.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date for calculation (inclusive)
        end_date: End date for calculation (inclusive)
        as_of_date: Calculate realized P&L as of this date (all sells up to this date)
                    If provided, overrides start_date/end_date
    
    Returns:
        Decimal: Total realized P&L (gain/loss)
    """
    # Query lot allocations for sell transactions in this portfolio
    query = db.query(LotAllocation).join(
        Transaction, LotAllocation.transaction_id == Transaction.id
    ).filter(
        Transaction.portfolio_id == portfolio_id
    )
    
    # If as_of_date provided, get all allocations up to that date
    if as_of_date:
        query = query.filter(Transaction.occurred_at <= datetime.combine(as_of_date, datetime.max.time()))
    else:
        # Otherwise use start_date/end_date range
        if start_date:
            query = query.filter(Transaction.occurred_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            query = query.filter(Transaction.occurred_at <= datetime.combine(end_date, datetime.max.time()))
    
    allocations = query.all()
    
    # Sum realized gains from all allocations
    total_realized = Decimal('0')
    for allocation in allocations:
        total_realized += Decimal(str(allocation.realized_gain_base))
    
    logger.info(
        f"Calculated realized P&L for portfolio {portfolio_id}: {total_realized} "
        f"(from {len(allocations)} lot allocations)"
    )
    
    return total_realized


def calculate_deposits_and_withdrawals(
    db: Session,
    portfolio_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Tuple[Decimal, Decimal]:
    """
    Calculate total deposits and withdrawals for a period.
    
    CRITICAL FOR TWR CALCULATION:
    - Deposits include: 'deposit', 'buy', 'transfer_in' (money entering portfolio)
    - Withdrawals include: 'withdrawal', 'sell', 'transfer_out' (money leaving portfolio)
    
    This ensures TWR can correctly isolate investment performance from cash flows.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date (inclusive, from 00:00:00)
        end_date: End date (inclusive, up to 23:59:59.999999)
    
    Returns:
        tuple: (total_deposits, total_withdrawals)
    """
    from datetime import datetime, time
    
    query = db.query(Transaction).filter(
        Transaction.portfolio_id == portfolio_id
    )
    
    if start_date:
        # Start from beginning of day
        start_dt = datetime.combine(start_date, time.min)
        query = query.filter(Transaction.occurred_at >= start_dt)
    if end_date:
        # Include up to end of day (23:59:59.999999)
        end_dt = datetime.combine(end_date, time.max)
        query = query.filter(Transaction.occurred_at <= end_dt)
    
    transactions = query.all()
    
    total_deposits = Decimal('0')
    total_withdrawals = Decimal('0')
    
    # Cash inflows to portfolio (money in)
    deposit_types = ['deposit', 'buy', 'transfer_in']
    # Cash outflows from portfolio (money out)
    withdrawal_types = ['withdrawal', 'sell', 'transfer_out']
    
    for tx in transactions:
        if tx.type in deposit_types and tx.value_base:
            total_deposits += abs(Decimal(str(tx.value_base)))
        elif tx.type in withdrawal_types and tx.value_base:
            total_withdrawals += abs(Decimal(str(tx.value_base)))
    
    return total_deposits, total_withdrawals
    
    return total_deposits, total_withdrawals


def create_daily_snapshot(
    db: Session,
    portfolio_id: int,
    snapshot_date: Optional[date] = None,
    use_historical_calculation: bool = False
) -> Tuple[Optional[Snapshot], List[str]]:
    """
    Create a daily snapshot for a portfolio.
    This is the main function called by the daily job.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        snapshot_date: Date for snapshot (default: today)
        use_historical_calculation: Force lot-based calculation (for rolling window reconstruction)
    
    Returns:
        tuple: (Snapshot object, list of warnings)
    """
    if snapshot_date is None:
        snapshot_date = date.today()
    
    logger.info(f"Creating daily snapshot for portfolio {portfolio_id} on {snapshot_date}")
    
    warnings = []
    
    try:
        # Get portfolio to access base_currency
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} not found")
        
        # Calculate portfolio value
        # For rolling window reconstruction, force historical calculation
        valuation = calculate_portfolio_value(
            db, portfolio_id, snapshot_date, 
            use_historical_calculation=use_historical_calculation
        )
        
        if valuation['missing_prices']:
            warnings.append(f"Missing prices for: {', '.join(valuation['missing_prices'])}")
        
        # Calculate realized P&L as of snapshot_date (cumulative from all sells up to this date)
        realized_pnl = calculate_realized_pnl(db, portfolio_id, as_of_date=snapshot_date)
        
        # Calculate deposits/withdrawals UP TO snapshot_date (not lifetime!)
        deposits, withdrawals = calculate_deposits_and_withdrawals(
            db, portfolio_id, end_date=snapshot_date
        )
        
        # Create snapshot
        # CRITICAL: total_invested_base = cost basis (from valuation), NOT deposits - withdrawals
        snapshot = create_snapshot(
            db=db,
            portfolio_id=portfolio_id,
            snapshot_date=snapshot_date,
            total_value_base=valuation['total_market_value'],
            currency_base=portfolio.currency_base,  # Add currency_base from portfolio
            total_invested_base=valuation['total_cost_basis'],  # Cost basis of open positions
            realized_pnl_base=realized_pnl,
            unrealized_pnl_base=valuation['unrealized_pnl'],
            deposits_base=deposits,  # Cumulative deposits (for net invested calc)
            withdrawals_base=withdrawals,  # Cumulative withdrawals (for net invested calc)
            notes=f"Positions: {valuation['position_count']}, Missing prices: {len(valuation['missing_prices'])}"
        )
        
        logger.info(
            f"Snapshot created for portfolio {portfolio_id}: "
            f"Value={valuation['total_market_value']}, "
            f"Unrealized P&L={valuation['unrealized_pnl']}, "
            f"Positions={valuation['position_count']}"
        )
        
        return snapshot, warnings
        
    except Exception as e:
        logger.error(f"Error creating snapshot for portfolio {portfolio_id}: {e}", exc_info=True)
        return None, [f"Error: {str(e)}"]


def get_snapshot_history(
    db: Session,
    portfolio_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: Optional[int] = None
) -> List[Snapshot]:
    """
    Get snapshot history for a portfolio.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date
        end_date: End date
        limit: Maximum number of snapshots to return
    
    Returns:
        List[Snapshot]: List of snapshots
    """
    query = db.query(Snapshot).filter(
        Snapshot.portfolio_id == portfolio_id
    )
    
    if start_date:
        query = query.filter(Snapshot.snapshot_date >= start_date)
    if end_date:
        query = query.filter(Snapshot.snapshot_date <= end_date)
    
    query = query.order_by(Snapshot.snapshot_date.desc())
    
    if limit:
        query = query.limit(limit)
    
    return query.all()


def get_snapshot_at_date(
    db: Session,
    portfolio_id: int,
    snapshot_date: date
) -> Optional[Snapshot]:
    """
    Get snapshot for a specific date.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        snapshot_date: Date
    
    Returns:
        Snapshot or None
    """
    return db.query(Snapshot).filter(
        and_(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date == snapshot_date
        )
    ).first()


def get_latest_snapshot(
    db: Session,
    portfolio_id: int
) -> Optional[Snapshot]:
    """
    Get the most recent snapshot for a portfolio.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        Snapshot or None
    """
    return db.query(Snapshot).filter(
        Snapshot.portfolio_id == portfolio_id
    ).order_by(Snapshot.snapshot_date.desc()).first()


def calculate_portfolio_performance(
    db: Session,
    portfolio_id: int,
    days: int = 30
) -> Dict:
    """
    Calculate portfolio performance metrics over a period.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        days: Number of days to look back
    
    Returns:
        dict: Performance metrics
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    snapshots = get_snapshot_history(db, portfolio_id, start_date, end_date)
    
    if not snapshots or len(snapshots) < 2:
        return {
            'period_days': days,
            'data_available': False,
            'message': 'Insufficient snapshot data'
        }
    
    # Most recent is first (desc order)
    latest = snapshots[0]
    oldest = snapshots[-1]
    
    latest_value = Decimal(str(latest.total_value_base))
    oldest_value = Decimal(str(oldest.total_value_base))
    
    # Calculate returns
    value_change = latest_value - oldest_value
    percent_change = (value_change / oldest_value * 100) if oldest_value > 0 else Decimal('0')
    
    # Calculate deposits/withdrawals during period
    deposits, withdrawals = calculate_deposits_and_withdrawals(
        db, portfolio_id, oldest.snapshot_date, latest.snapshot_date
    )
    
    net_flow = deposits - withdrawals
    value_change_adjusted = value_change - net_flow
    
    return {
        'period_days': days,
        'start_date': oldest.snapshot_date,
        'end_date': latest.snapshot_date,
        'start_value': oldest_value,
        'end_value': latest_value,
        'value_change': value_change,
        'percent_change': percent_change,
        'deposits': deposits,
        'withdrawals': withdrawals,
        'net_flow': net_flow,
        'value_change_adjusted': value_change_adjusted,
        'snapshot_count': len(snapshots),
        'data_available': True
    }


def cleanup_old_snapshots(
    db: Session,
    days_to_keep: int = 365
) -> int:
    """
    Delete snapshots older than specified days.
    
    Args:
        db: Database session
        days_to_keep: Number of days to retain (0 = keep all)
    
    Returns:
        int: Number of snapshots deleted
    """
    if days_to_keep == 0:
        logger.info("Snapshot retention set to 0, keeping all snapshots")
        return 0
    
    cutoff_date = date.today() - timedelta(days=days_to_keep)
    
    deleted = db.query(Snapshot).filter(
        Snapshot.snapshot_date < cutoff_date
    ).delete()
    
    db.commit()
    
    logger.info(f"Deleted {deleted} snapshots older than {cutoff_date}")
    
    return deleted
