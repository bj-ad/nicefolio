"""
Portfolio Service - Orchestrates position, lot, and snapshot management.
Follows the established Service → CRUD → Model pattern.
"""
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Portfolio, Transaction
from datetime import timezone
from crud.crud_position import (
    reconcile_crypto_positions_from_balances,
    get_positions_by_portfolio,
    get_positions_summary,
    update_position_from_transaction
)
from crud.crud_snapshot import (
    create_daily_snapshot,
    get_snapshot_history,
    calculate_portfolio_performance,
    cleanup_old_snapshots
)
from crud.crud_lot import (
    create_lot_from_transaction,
    allocate_sale_to_lots,
    reconcile_lots_from_transactions,
    get_lot_summary_by_symbol
)
from typing import Optional, List, Dict, Tuple
from datetime import date, timedelta
from decimal import Decimal
from utils.logging_config import get_logger
from utils.app_config import load_app_config


logger = get_logger(__name__)


# NOTE: reconcile_all_positions() deleted (obsolete)
# Positions are now updated by:
#   1. update_position_from_transaction() after each transaction (real-time)
#   2. recreate_positions_from_transactions() weekly (self-correction)
# The old daily reconciliation used proportional cost reduction which
# overwrote correct lot-based cost basis calculations.
# See DIVIDEND_REINVESTMENT_BUG_FIX_COMPLETE.md for details.


def create_all_snapshots(snapshot_date: Optional[date] = None) -> Dict:
    """
    Create daily snapshots for all portfolios.
    This is called by the daily worker job.
    Excludes portfolios with status='closed' or update_method='manual' from config.
    
    Args:
        snapshot_date: Date for snapshot (default: today)
    
    Returns:
        dict: Snapshot creation summary
    """
    if snapshot_date is None:
        snapshot_date = date.today()
    
    logger.info(f"Creating snapshots for all portfolios on {snapshot_date}")
    
    # Load portfolio config to check update_method
    import yaml
    from pathlib import Path
    config_path = Path(__file__).parent.parent / 'config' / 'portfolio_config.yaml'
    with open(config_path, 'r') as f:
        portfolio_configs = yaml.safe_load(f)
    
    manual_portfolio_ids = {
        p['id'] for p in portfolio_configs 
        if p.get('status') == 'closed' or p.get('update_method') == 'manual'
    }
    
    if manual_portfolio_ids:
        logger.info(f"Excluding portfolios {manual_portfolio_ids} from snapshot creation (closed or manual update)")
    
    db = SessionLocal()
    try:
        # Get all portfolios and filter out manual/closed ones
        portfolios = db.query(Portfolio).filter(
            ~Portfolio.id.in_(manual_portfolio_ids) if manual_portfolio_ids else True
        ).all()
        
        results = {
            'portfolios_processed': 0,
            'snapshots_created': 0,
            'snapshots_failed': 0,
            'warnings': []
        }
        
        for portfolio in portfolios:
            try:
                snapshot, warnings = create_daily_snapshot(
                    db, portfolio.id, snapshot_date
                )
                
                if snapshot:
                    results['snapshots_created'] += 1
                    logger.info(
                        f"Created snapshot for {portfolio.name}: "
                        f"Value=${snapshot.total_value_base}"
                    )
                else:
                    results['snapshots_failed'] += 1
                    logger.warning(f"Failed to create snapshot for {portfolio.name}")
                
                results['warnings'].extend([f"Portfolio {portfolio.name}: {w}" for w in warnings])
                results['portfolios_processed'] += 1
                
            except Exception as e:
                logger.error(f"Error creating snapshot for portfolio {portfolio.id}: {e}", exc_info=True)
                results['snapshots_failed'] += 1
                results['warnings'].append(f"Portfolio {portfolio.id} error: {str(e)}")
        
        logger.info(
            f"Snapshot creation complete: "
            f"{results['snapshots_created']} created, "
            f"{results['snapshots_failed']} failed"
        )
        
        return results
        
    except Exception as e:
        logger.error(f"Error in snapshot creation: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def reconcile_all_lots(symbol: Optional[str] = None) -> Dict:
    """
    Reconcile lots from transactions using FIFO.
    This rebuilds lot state from scratch.
    
    Args:
        symbol: Optional symbol to reconcile (None = all)
    
    Returns:
        dict: Reconciliation summary
    """
    logger.info(f"Starting lot reconciliation for symbol: {symbol or 'ALL'}")
    
    db = SessionLocal()
    try:
        lots_created, sales_allocated, warnings = reconcile_lots_from_transactions(
            db, symbol
        )
        
        results = {
            'lots_created': lots_created,
            'sales_allocated': sales_allocated,
            'warnings': warnings
        }
        
        logger.info(
            f"Lot reconciliation complete: "
            f"{lots_created} lots created, {sales_allocated} sales allocated"
        )
        
        return results
        
    except Exception as e:
        logger.error(f"Error in lot reconciliation: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def process_transaction_for_portfolio(
    db: Session,
    transaction: Transaction
) -> Dict:
    """
    Process a transaction for portfolio tracking.
    This handles position updates and lot creation/allocation.
    
    Args:
        db: Database session
        transaction: Transaction to process
    
    Returns:
        dict: Processing results
    """
    results = {
        'position_updated': False,
        'lot_created': False,
        'lot_allocated': False,
        'warnings': []
    }
    
    try:
        # Update position
        position = update_position_from_transaction(db, transaction)
        if position:
            results['position_updated'] = True
        
        # Handle lots
        if transaction.type in ['buy', 'transfer_in', 'deposit']:
            # Create lot
            lot = create_lot_from_transaction(db, transaction)
            if lot:
                results['lot_created'] = True
            else:
                results['warnings'].append("Failed to create lot")
        
        elif transaction.type in ['sell', 'transfer_out', 'withdrawal']:
            # Allocate to lots
            allocations, realized_gain = allocate_sale_to_lots(db, transaction)
            if allocations:
                results['lot_allocated'] = True
                results['realized_gain'] = realized_gain
            else:
                results['warnings'].append("Failed to allocate sale to lots")
        
        return results
        
    except Exception as e:
        logger.error(f"Error processing transaction {transaction.id}: {e}", exc_info=True)
        results['warnings'].append(f"Error: {str(e)}")
        return results


def get_portfolio_report(
    db: Session,
    portfolio_id: int
) -> Dict:
    """
    Generate a comprehensive portfolio report.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        dict: Comprehensive portfolio report
    """
    logger.info(f"Generating portfolio report for portfolio {portfolio_id}")
    
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            return {'error': 'Portfolio not found'}
        
        # Get positions
        positions = get_positions_by_portfolio(db, portfolio_id, include_zero=False)
        positions_summary = get_positions_summary(db, portfolio_id)
        
        # Get latest snapshot
        from crud.crud_snapshot import get_latest_snapshot
        latest_snapshot = get_latest_snapshot(db, portfolio_id)
        
        # Get performance metrics
        performance_30d = calculate_portfolio_performance(db, portfolio_id, days=30)
        performance_90d = calculate_portfolio_performance(db, portfolio_id, days=90)
        
        # Get lot summary
        lot_summary = get_lot_summary_by_symbol(db)
        
        report = {
            'portfolio': {
                'id': portfolio.id,
                'name': portfolio.name,
                'currency_base': portfolio.currency_base,
                'description': portfolio.description
            },
            'positions': {
                'count': len(positions),
                'summary': positions_summary,
                'details': [
                    {
                        'symbol': p.symbol,
                        'quantity': p.quantity,
                        'cost_basis': p.cost_basis_base,
                        'avg_price': p.avg_price_base,
                        'asset_class': p.asset_class
                    }
                    for p in positions
                ]
            },
            'latest_snapshot': {
                'date': latest_snapshot.snapshot_date if latest_snapshot else None,
                'total_value': latest_snapshot.total_value_base if latest_snapshot else None,
                'unrealized_pnl': latest_snapshot.unrealized_pnl_base if latest_snapshot else None,
                'realized_pnl': latest_snapshot.realized_pnl_base if latest_snapshot else None
            } if latest_snapshot else None,
            'performance': {
                '30_days': performance_30d,
                '90_days': performance_90d
            },
            'lots': lot_summary
        }
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating portfolio report: {e}", exc_info=True)
        return {'error': str(e)}


def cleanup_old_data() -> Dict:
    """
    Cleanup old snapshots based on retention policy.
    
    Returns:
        dict: Cleanup summary
    """
    logger.info("Starting data cleanup")
    
    db = SessionLocal()
    try:
        config = load_app_config()
        retention_days = config.get('portfolio', {}).get('snapshot_retention_days', 0)
        
        deleted = cleanup_old_snapshots(db, retention_days)
        
        return {
            'snapshots_deleted': deleted,
            'retention_days': retention_days
        }
        
    except Exception as e:
        logger.error(f"Error in data cleanup: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def get_all_portfolios_summary() -> List[Dict]:
    """
    Get summary of all portfolios.
    
    Returns:
        List[Dict]: Summary for each portfolio
    """
    db = SessionLocal()
    try:
        portfolios = db.query(Portfolio).all()
        
        summaries = []
        for portfolio in portfolios:
            from crud.crud_snapshot import get_latest_snapshot
            
            positions_summary = get_positions_summary(db, portfolio.id)
            latest_snapshot = get_latest_snapshot(db, portfolio.id)
            
            summaries.append({
                'id': portfolio.id,
                'name': portfolio.name,
                'currency_base': portfolio.currency_base,
                'position_count': positions_summary['total_positions'],
                'total_cost_basis': positions_summary['total_cost_basis'],
                'latest_value': latest_snapshot.total_value_base if latest_snapshot else None,
                'latest_snapshot_date': latest_snapshot.snapshot_date if latest_snapshot else None
            })
        
        return summaries
        
    except Exception as e:
        logger.error(f"Error getting portfolio summaries: {e}", exc_info=True)
        return []
    finally:
        db.close()


def calculate_portfolio_return(
    db: Session,
    portfolio_id: int
) -> Dict:
    """
    Calculate total return for a portfolio using lots and positions as source of truth.
    
    This provides a reliable performance metric based on:
    - Lots: Actual cost basis of current holdings
    - LotAllocations: Realized gains from closed positions
    - Positions + Market prices: Current market value
    - Snapshots: Daily portfolio values for TWR calculation
    - Transactions: Cash flows for XIRR calculation
    
    Metrics:
    - Total Return = (Unrealized P&L + Realized P&L) / Total Cost Basis
    - XIRR = Internal rate of return accounting for cash flow timing
    - TWR = Time-weighted return isolating investment performance from cash flows
    
    This function belongs in the Service Layer because:
    - It performs business logic calculations (not CRUD operations)
    - It reads from multiple tables but doesn't modify them
    - It orchestrates data retrieval and computation
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        dict: Performance metrics including:
            - total_return_pct: Total return as percentage
            - xirr_pct: Internal rate of return (annualized, accounts for cash flow timing)
            - twr_pct: Time-weighted return (annualized, isolates performance)
            - years_active: How long the portfolio has been running
            - first_transaction_date: Start date of portfolio
            - total_cost_basis: What was invested
            - current_market_value: What it's worth now
            - unrealized_pnl: Paper gains/losses
            - realized_pnl: Gains/losses from closed positions
            - total_pnl: Unrealized + Realized
            - data_available: Whether enough data exists
    """
    from models import Lot, LotAllocation, Position, Portfolio, Transaction, Snapshot
    from crud.crud_market_fx import get_latest_price, get_latest_fx_rate
    from decimal import Decimal
    from sqlalchemy import func
    from datetime import datetime
    
    try:
        # Get portfolio for base currency
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            return {
                'total_return_pct': Decimal('0'),
                'data_available': False,
                'message': f'Portfolio {portfolio_id} not found'
            }
        
        base_currency = portfolio.currency_base
        
        # Get first transaction date to calculate years active
        first_tx_date = db.query(func.min(Transaction.occurred_at)).filter(
            Transaction.portfolio_id == portfolio_id
        ).scalar()
        
        if not first_tx_date:
            return {
                'total_return_pct': Decimal('0'),
                'xirr_pct': Decimal('0'),
                'twr_pct': Decimal('0'),
                'years_active': Decimal('0'),
                'data_available': False,
                'message': 'No transactions found'
            }
        
        # Calculate years active
        days_active = (datetime.now(timezone.utc).date() - first_tx_date.date()).days
        years_active = Decimal(str(days_active)) / Decimal('365.25')
        
        # 1. Get total cost basis from open lots (what we paid for current holdings)
        open_lots = db.query(Lot).filter(
            Lot.portfolio_id == portfolio_id,
            Lot.remaining_quantity > 0
        ).all()
        
        total_cost_basis = Decimal('0')
        for lot in open_lots:
            # Pro-rate cost basis for remaining quantity
            if lot.quantity and lot.quantity > 0:
                cost_per_unit = Decimal(str(lot.total_cost_basis_base)) / Decimal(str(lot.quantity))
                remaining_cost = cost_per_unit * Decimal(str(lot.remaining_quantity))
                total_cost_basis += remaining_cost
        
        # 2. Get current market value from positions
        positions = db.query(Position).filter(
            Position.portfolio_id == portfolio_id,
            Position.quantity > 0
        ).all()
        
        current_market_value = Decimal('0')
        for pos in positions:
            price_data = get_latest_price(db, pos.symbol)
            if price_data:
                price = Decimal(str(price_data.price))
                price_currency = price_data.currency
                
                # Convert price to base currency if needed
                if price_currency != base_currency:
                    fx_pair = f"{price_currency}/{base_currency}"
                    fx_rate = get_latest_fx_rate(db, fx_pair)
                    if fx_rate:
                        price = price * Decimal(str(fx_rate.rate))
                
                current_market_value += Decimal(str(pos.quantity)) * price
            else:
                # For cash positions or missing prices, use quantity as value
                # (assumes 1:1 if it's the base currency)
                if pos.symbol == base_currency:
                    current_market_value += Decimal(str(pos.quantity))
        
        # 3. Get realized P&L from lot allocations (all time)
        realized_pnl = db.query(
            func.coalesce(func.sum(LotAllocation.realized_gain_base), 0)
        ).join(Lot, LotAllocation.lot_id == Lot.lot_id).filter(
            Lot.portfolio_id == portfolio_id
        ).scalar()
        
        realized_pnl = Decimal(str(realized_pnl or 0))
        
        # 4. Calculate unrealized P&L
        unrealized_pnl = current_market_value - total_cost_basis
        
        # 5. Calculate total return
        total_pnl = unrealized_pnl + realized_pnl
        
        # Total return = Total P&L / (Total Cost Basis + amount from closed positions)
        # For simplicity: Total Return = Total P&L / Total Invested
        # Where Total Invested = current cost basis + realized cost basis
        # But since we only have current cost basis, use: Total P&L / Current Cost Basis
        # This is approximate but useful for comparison
        
        if total_cost_basis > 0:
            total_return_pct = (total_pnl / total_cost_basis) * Decimal('100')
        else:
            total_return_pct = Decimal('0')
        
        # 6. Calculate XIRR (Internal Rate of Return)
        # XIRR accounts for the timing and amount of all cash flows
        logger.debug(f"Portfolio {portfolio_id}: calculating XIRR with current_market_value={current_market_value}")
        xirr_pct = _calculate_xirr(db, portfolio_id, current_market_value)
        
        # 7. Calculate TWR (Time-Weighted Return)
        # TWR isolates investment performance from cash flow timing
        twr_pct = _calculate_twr(db, portfolio_id, years_active)
        
        # 8. Get first snapshot date for benchmark comparisons
        first_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id
        ).order_by(Snapshot.snapshot_date).first()
        first_snapshot_date = first_snapshot.snapshot_date if first_snapshot else first_tx_date.date()
        
        return {
            'total_return_pct': total_return_pct,
            'xirr_pct': xirr_pct,
            'twr_pct': twr_pct,
            'years_active': years_active,
            'first_transaction_date': first_tx_date.date(),
            'first_snapshot_date': first_snapshot_date,
            'total_cost_basis': total_cost_basis,
            'current_market_value': current_market_value,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'total_pnl': total_pnl,
            'position_count': len(positions),
            'lot_count': len(open_lots),
            'base_currency': base_currency,
            'data_available': True
        }
        
    except Exception as e:
        from decimal import Decimal
        logger.error(f"Error calculating portfolio return for {portfolio_id}: {e}", exc_info=True)
        return {
            'total_return_pct': Decimal('0'),
            'xirr_pct': Decimal('0'),
            'twr_pct': Decimal('0'),
            'years_active': Decimal('0'),
            'data_available': False,
            'message': str(e)
        }


def _calculate_xirr(
    db: Session,
    portfolio_id: int,
    current_value: Decimal
) -> Decimal:
    """
    Calculate lifetime XIRR (Internal Rate of Return) using NAV-based method.
    
    This uses the same NAV-based calculation as _calculate_xirr_period but for
    the entire portfolio history. Delegates to _calculate_xirr_period with full days.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        current_value: Current market value (not used, fetched from snapshots)
    
    Returns:
        Decimal: XIRR as annualized percentage (e.g., 15.5 for 15.5%)
    """
    from models import Transaction, Snapshot
    from decimal import Decimal
    from datetime import datetime
    
    try:
        # Get first transaction date to calculate full lifetime
        first_tx = db.query(Transaction).filter(
            Transaction.portfolio_id == portfolio_id
        ).order_by(Transaction.occurred_at).first()
        
        if not first_tx:
            return Decimal('0')
        
        # Calculate days from first transaction to now
        days_active = (datetime.now(timezone.utc).date() - first_tx.occurred_at.date()).days
        if days_active < 1:
            days_active = 1
        
        # Use NAV-based period calculation for full lifetime
        return _calculate_xirr_period(db, portfolio_id, days_active)
        
    except Exception as e:
        logger.warning(f"Could not calculate lifetime XIRR for portfolio {portfolio_id}: {e}")
        return Decimal('0')


def _calculate_twr(
    db: Session,
    portfolio_id: int,
    years_active: Decimal
) -> Decimal:
    """
    Calculate lifetime TWR (Time-Weighted Return) using NAV-based method.
    
    TWR isolates investment performance from cash flow timing by using NAV price growth.
    NAV price already accounts for cash flows (deposits/withdrawals adjust units),
    so TWR is simply: (end_NAV / start_NAV - 1) × 100
    
    This is the industry standard method (mutual funds, ETFs) and is mathematically
    equivalent to geometric linking but simpler and more accurate.
    
    Formula:
    - Period TWR = (End_NAV / Start_NAV - 1) × 100
    - Annualized TWR = ((End_NAV/Start_NAV)^(365/days) - 1) × 100
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        years_active: Years the portfolio has been active
    
    Returns:
        Decimal: TWR as annualized percentage
    """
    from models import Snapshot
    from decimal import Decimal
    
    try:
        # Get snapshots with valid NAV data, ordered by date
        snapshots = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.nav_price.isnot(None),
            Snapshot.nav_price > 0
        ).order_by(Snapshot.snapshot_date).all()
        
        if len(snapshots) < 2:
            logger.debug(f"Not enough NAV snapshots for TWR calculation (portfolio {portfolio_id})")
            return Decimal('0')
        
        # Get first and last NAV prices
        start_nav = float(snapshots[0].nav_price)
        end_nav = float(snapshots[-1].nav_price)
        
        if start_nav <= 0:
            return Decimal('0')
        
        # Calculate cumulative growth factor
        cumulative_growth_factor = end_nav / start_nav
        
        # Annualize the TWR
        if years_active > 0:
            years = float(years_active)
            if cumulative_growth_factor > 0:
                annualized_twr = (pow(cumulative_growth_factor, 1.0 / years) - 1.0) * 100
                return Decimal(str(round(annualized_twr, 2)))
            else:
                return Decimal('0')
        else:
            # For very new portfolios, return total return
            total_twr = (cumulative_growth_factor - 1.0) * 100
            return Decimal(str(round(total_twr, 2)))
            
    except Exception as e:
        logger.warning(f"Could not calculate TWR for portfolio {portfolio_id}: {e}")
        return Decimal('0')


def _calculate_xirr_period(
    db: Session,
    portfolio_id: int,
    days: int
) -> Decimal:
    """
    Calculate XIRR using NAV-based method.
    
    NAV-based XIRR properly accounts for the timing of cash flows:
    - Starting value = units × start_NAV (your baseline investment)
    - Each transaction is valued at the cash amount (not converted, as cash flows are real)
    - Ending value = units × end_NAV (your current holdings)
    
    This gives accurate money-weighted returns that reflect when you invested.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        days: Number of days to look back
    
    Returns:
        Decimal: XIRR as annualized percentage (e.g., 15.5 for 15.5%)
    """
    from models import Transaction, Snapshot
    from decimal import Decimal
    from datetime import datetime, timedelta
    import numpy as np
    from apps.core.helpers import calculate_xirr
    
    try:
        # Calculate date range
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)
        
        # Get start and end snapshots
        start_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date <= start_date
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        # If no snapshot at requested start date, use the earliest available snapshot
        if not start_snapshot:
            start_snapshot = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id
            ).order_by(Snapshot.snapshot_date).first()
            
            if start_snapshot:
                logger.debug(f"Portfolio {portfolio_id}: No snapshot at {start_date}, "
                           f"using earliest available snapshot from {start_snapshot.snapshot_date}")
            else:
                logger.warning(f"Portfolio {portfolio_id}: No snapshots found at all")
                return Decimal('0')
        
        end_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        if not end_snapshot:
            logger.warning(f"Portfolio {portfolio_id}: No end snapshot found")
            return Decimal('0')
        
        # Get NAV data
        start_nav_price = start_snapshot.nav_price
        end_nav_price = end_snapshot.nav_price
        start_units = start_snapshot.nav_units
        end_units = end_snapshot.nav_units
        
        if (start_nav_price is None or end_nav_price is None or 
            start_units is None or end_units is None or 
            start_nav_price <= 0 or end_nav_price <= 0):
            logger.debug(f"Portfolio {portfolio_id}: NAV data not available - "
                        f"NAV-based XIRR calculation skipped. "
                        f"(Expected for cash portfolios without NAV tracking)")
            return Decimal('0')
        
        start_nav_price = float(start_nav_price)
        end_nav_price = float(end_nav_price)
        start_units = float(start_units)
        end_units = float(end_units)
        
        # Use actual snapshot dates for cash flow calculation
        actual_start_date = start_snapshot.snapshot_date
        actual_end_date = end_snapshot.snapshot_date
        
        # Get transactions within the period (between actual snapshot dates)
        cash_flow_types = ['deposit', 'withdrawal', 'buy', 'sell', 'transfer_in', 'transfer_out']
        transactions = db.query(Transaction).filter(
            Transaction.portfolio_id == portfolio_id,
            Transaction.type.in_(cash_flow_types),
            Transaction.occurred_at >= datetime.combine(actual_start_date, datetime.min.time()).replace(tzinfo=timezone.utc),
            Transaction.occurred_at <= datetime.combine(actual_end_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            Transaction.value_base.isnot(None)
        ).order_by(Transaction.occurred_at).all()
        
        # Build cash flow arrays for XIRR using actual snapshot dates
        dates = [actual_start_date]
        amounts = [-start_units * start_nav_price]  # Initial investment at start NAV
        
        # Add actual transaction cash flows
        for tx in transactions:
            tx_date = tx.occurred_at.date()
            tx_value = abs(float(tx.value_base))
            
            dates.append(tx_date)
            if tx.type in ['deposit', 'buy', 'transfer_in']:
                # Cash out (you pay money)
                amounts.append(-tx_value)
            else:  # withdrawal, sell, transfer_out
                # Cash in (you receive money)
                amounts.append(tx_value)
        
        # Add ending value at ending NAV (use actual snapshot date)
        dates.append(actual_end_date)
        amounts.append(end_units * end_nav_price)
        
        # Need at least 2 cash flows
        if len(dates) < 2:
            return Decimal('0')
        
        # Calculate XIRR
        xirr = calculate_xirr(amounts, dates)
        
        if xirr is not None and not np.isnan(xirr) and not np.isinf(xirr):
            logger.debug(f"Portfolio {portfolio_id} NAV-based XIRR ({days}d): {xirr*100:.2f}% "
                        f"(NAV {start_nav_price:.2f} → {end_nav_price:.2f}, "
                        f"Units {start_units:.2f} → {end_units:.2f})")
            return Decimal(str(round(xirr * 100, 2)))
        else:
            logger.warning(f"Portfolio {portfolio_id} XIRR calculation did not converge. "
                         f"xirr={xirr}, len(dates)={len(dates)}, sum(amounts)={sum(amounts):.2f}, "
                         f"NAV: {start_nav_price:.2f} → {end_nav_price:.2f}, "
                         f"Units: {start_units:.2f} → {end_units:.2f}")
            return Decimal('0')
            
    except Exception as e:
        logger.warning(f"Could not calculate NAV-based XIRR for portfolio {portfolio_id}: {e}", exc_info=True)
        return Decimal('0')
