"""
Data fetching and calculation functions for NiceFolio portfolio dashboard.

This module contains functions to retrieve portfolio data, calculate statistics,
and prepare data for display in the UI.
"""

from datetime import date, timedelta, datetime
from decimal import Decimal
from collections import defaultdict, namedtuple
from typing import Optional, Dict, List
from nicegui import app

from database import SessionLocal
from models import Snapshot, Portfolio, Position, CashPosition, Transaction
from utils.portfolios_loader import get_portfolios_loader
from utils.app_config import get_global_base_currency
from utils.logging_config import get_logger
from crud.crud_market_fx import get_latest_price, get_latest_fx_rate
from service.portfolio_service import calculate_portfolio_return, _calculate_xirr_period
from apps.core.helpers import (
    calculate_max_drawdown, 
    _calculate_twr_between_dates, 
    _calculate_mdd_between_dates,
    _calculate_volatility_between_dates,
    calculate_sharpe_ratio,
    calculate_net_invested_capital
)

logger = get_logger(__name__)


def calculate_period_statistics(db, portfolio_id, date_range='1y'):
    """
    Calculate statistics for a specific period matching the chart date range.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID (None for all portfolios)
        date_range: Date range ('1m', '1y', '3y', '5y', 'all')
    
    Returns:
        dict with period statistics including value change, invested change, pnl change,
        TWR, and MDD (where applicable)
    """
    # Calculate date range
    today = date.today()
    if date_range == 'custom':
        # Use custom dates from storage
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            start_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            today = datetime.strptime(to_str, '%Y-%m-%d').date()
            days_back = (today - start_date).days
        else:
            days_back = 365
            start_date = today - timedelta(days=days_back)
    elif date_range == 'all':
        # CRITICAL: 'all' must use actual portfolio history, NOT fixed 10 years
        # This ensures benchmark comparisons are over the same time period
        start_date = None  # Will be determined by first snapshot below
    else:
        # Fixed periods (3m, 6m, 1y, 3y, 5y)
        range_mapping = {
            '3m': 90,
            '6m': 180,
            '1y': 365,
            '3y': 3*365,
            '5y': 5*365
        }
        days_back = range_mapping.get(date_range, 365)
        start_date = today - timedelta(days=days_back)
    
    # Get portfolio config
    portfolios_loader = get_portfolios_loader()
    all_portfolios_config = {p['id']: p for p in portfolios_loader.get_portfolios()}
    
    try:
        if portfolio_id:
            # Single portfolio
            portfolio_config = all_portfolios_config.get(portfolio_id, {})
            portfolio_type = portfolio_config.get('type', '')
            
            # Get start and end snapshots
            # If requested start date is before available data, use earliest available snapshot
            if start_date:
                start_snap = db.query(Snapshot).filter(
                    Snapshot.portfolio_id == portfolio_id,
                    Snapshot.snapshot_date >= start_date,
                    Snapshot.snapshot_date <= today
                ).order_by(Snapshot.snapshot_date).first()
                
                # If no data after start_date, use the earliest available snapshot
                if not start_snap:
                    start_snap = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == portfolio_id,
                        Snapshot.snapshot_date <= today
                    ).order_by(Snapshot.snapshot_date).first()
            else:
                start_snap = db.query(Snapshot).filter(
                    Snapshot.portfolio_id == portfolio_id,
                    Snapshot.snapshot_date <= today
                ).order_by(Snapshot.snapshot_date).first()
            
            end_snap = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id,
                Snapshot.snapshot_date <= today
            ).order_by(Snapshot.snapshot_date.desc()).first()
            
            if not start_snap or not end_snap:
                return None
            
            # Calculate actual days between snapshots (may be less than requested)
            actual_days_back = (end_snap.snapshot_date - start_snap.snapshot_date).days
            if actual_days_back < 1:
                actual_days_back = 1
            
            # Calculate changes
            start_value = float(start_snap.total_value_base or Decimal('0'))
            end_value = float(end_snap.total_value_base or Decimal('0'))
            start_invested = float((start_snap.deposits_base or Decimal('0')) - (start_snap.withdrawals_base or Decimal('0')))
            end_invested = float((end_snap.deposits_base or Decimal('0')) - (end_snap.withdrawals_base or Decimal('0')))
            start_pnl = float((start_snap.unrealized_pnl_base or Decimal('0')) + (start_snap.realized_pnl_base or Decimal('0')))
            end_pnl = float((end_snap.unrealized_pnl_base or Decimal('0')) + (end_snap.realized_pnl_base or Decimal('0')))
            
            value_change = end_value - start_value
            invested_change = end_invested - start_invested
            pnl_change = end_pnl - start_pnl
            
            # Simple percentage calculations
            value_change_pct = (value_change / start_value * 100) if start_value > 0 else 0
            invested_change_pct = (invested_change / start_invested * 100) if start_invested > 0 else 0
            pnl_change_pct = (pnl_change / start_invested * 100) if start_invested > 0 else 0
            
            # Calculate TWR, XIRR, MDD, Volatility, and Sharpe for investment portfolios only
            # Skip for cash, closed, and aggregate portfolios to save processing power
            twr = None
            twr_annualized = None
            xirr = None
            mdd = None
            volatility = None
            sharpe_ratio = None
            
            portfolio_status = portfolio_config.get('status', 'active')
            
            if portfolio_type not in ['cash', 'all', ''] and portfolio_status != 'closed':
                try:
                    # Calculate period TWR (non-annualized) between specific dates
                    twr = _calculate_twr_between_dates(db, portfolio_id, start_snap.snapshot_date, end_snap.snapshot_date)
                    
                    # Annualize TWR for Sharpe calculation
                    if twr is not None and actual_days_back > 0:
                        twr_factor = 1 + (twr / 100)
                        annualization_factor = 365 / actual_days_back
                        twr_annualized = (pow(twr_factor, annualization_factor) - 1) * 100
                    
                    # XIRR is annualized by definition
                    xirr = float(_calculate_xirr_period(db, portfolio_id, actual_days_back))
                except Exception as e:
                    logger.debug(f"Could not calculate TWR/XIRR: {e}")
                
                # MDD is period-specific (not annualized)
                mdd = _calculate_mdd_between_dates(db, portfolio_id, start_snap.snapshot_date, end_snap.snapshot_date)
                
                # Calculate volatility (annualized)
                volatility = _calculate_volatility_between_dates(db, portfolio_id, start_snap.snapshot_date, end_snap.snapshot_date)
                
                # Calculate Sharpe ratio if we have both TWR and volatility
                if twr_annualized is not None and volatility is not None and volatility > 0:
                    sharpe_ratio = calculate_sharpe_ratio(twr_annualized, volatility, risk_free_rate=2.0)
                    logger.debug(f"Portfolio {portfolio_id} ({actual_days_back}d): TWR={twr_annualized:.1f}%, Vol={volatility:.1f}%, Sharpe={sharpe_ratio:.2f}")
            
            return {
                'value_change': value_change,
                'value_change_pct': value_change_pct,
                'invested_change': invested_change,
                'invested_change_pct': invested_change_pct,
                'pnl_change': pnl_change,
                'pnl_change_pct': pnl_change_pct,
                'twr': twr,
                'twr_annualized': twr_annualized,
                'xirr': xirr,
                'mdd': mdd,
                'volatility': volatility,
                'sharpe_ratio': sharpe_ratio,
                'portfolio_type': portfolio_type
            }
        
        else:
            # All portfolios aggregated
            # NOTE: Use ACTIVE investment portfolios only for period statistics (TWR, XIRR, MDD)
            # Investment portfolios: type NOT 'cash'/'placeholder' AND status NOT 'reserved'/'closed'
            # Closed portfolios are excluded as they represent past investments no longer active
            all_portfolios = portfolios_loader.get_portfolios()
            portfolio_ids = [p['id'] for p in all_portfolios 
                           if p.get('type') not in ['cash', 'placeholder'] and p.get('status') not in ['reserved', 'closed']]
            
            # Sum ALL investment portfolios that exist at each date (start and end)
            # Portfolios starting mid-period contribute to end value and thus the change
            if start_date:
                # Query snapshots for the specific start date
                start_snaps = db.query(Snapshot).filter(
                    Snapshot.portfolio_id.in_(portfolio_ids),
                    Snapshot.snapshot_date == start_date
                ).all()
            else:
                # Get earliest snapshot for each portfolio
                start_snaps = []
                for pid in portfolio_ids:
                    snap = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == pid
                    ).order_by(Snapshot.snapshot_date).first()
                    if snap:
                        start_snaps.append(snap)
            
            # Get snapshots for the specific end date
            end_snaps = db.query(Snapshot).filter(
                Snapshot.portfolio_id.in_(portfolio_ids),
                Snapshot.snapshot_date == today
            ).all()
            
            # If no snapshots on exact end date, get latest for each portfolio
            if not end_snaps:
                end_snaps = []
                for pid in portfolio_ids:
                    snap = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == pid,
                        Snapshot.snapshot_date <= today
                    ).order_by(Snapshot.snapshot_date.desc()).first()
                    if snap:
                        end_snaps.append(snap)
            
            if not end_snaps:
                return None
            
            # Sum ALL portfolios at start date (portfolios not yet started = 0 contribution)
            # Sum ALL portfolios at end date (includes portfolios started mid-period)
            start_value = sum(float(s.total_value_base or Decimal('0')) for s in start_snaps)
            end_value = sum(float(s.total_value_base or Decimal('0')) for s in end_snaps)
            start_invested = sum(float((s.deposits_base or Decimal('0')) - (s.withdrawals_base or Decimal('0'))) for s in start_snaps)
            end_invested = sum(float((s.deposits_base or Decimal('0')) - (s.withdrawals_base or Decimal('0'))) for s in end_snaps)
            start_pnl = sum(float((s.unrealized_pnl_base or Decimal('0')) + (s.realized_pnl_base or Decimal('0'))) for s in start_snaps)
            end_pnl = sum(float((s.unrealized_pnl_base or Decimal('0')) + (s.realized_pnl_base or Decimal('0'))) for s in end_snaps)
            
            value_change = end_value - start_value
            invested_change = end_invested - start_invested
            pnl_change = end_pnl - start_pnl
            
            # Simple percentage calculations
            value_change_pct = (value_change / start_value * 100) if start_value > 0 else 0
            invested_change_pct = (invested_change / start_invested * 100) if start_invested > 0 else 0
            pnl_change_pct = (pnl_change / start_invested * 100) if start_invested > 0 else 0
            
            # Calculate TWR, XIRR, MDD, Volatility, Sharpe for aggregate view
            # Using NAV-based method to match individual portfolio calculations
            twr = None
            twr_annualized = None
            xirr = None
            mdd = None
            volatility = None
            sharpe_ratio = None
            
            try:
                # Get aligned aggregate series for proper TWR calculation
                from apps.core.helpers import get_aligned_aggregate_series, calculate_xirr
                import math
                
                # Get all transactions for cash flow tracking
                all_transactions = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(portfolio_ids)
                ).order_by(Transaction.occurred_at).all()
                
                if not all_transactions:
                    logger.warning("No transactions found for aggregate XIRR - returning early")
                    return {
                        'value_change': value_change,
                        'value_change_pct': value_change_pct,
                        'invested_change': invested_change,
                        'invested_change_pct': invested_change_pct,
                        'pnl_change': pnl_change,
                        'pnl_change_pct': pnl_change_pct,
                        'twr': None,
                        'twr_annualized': None,
                        'xirr': None,
                        'mdd': None,
                        'volatility': None,
                        'sharpe_ratio': None,
                        'portfolio_type': 'all'
                    }
                
                # Get first transaction date for start
                first_transaction_date = all_transactions[0].occurred_at.date()
                logger.debug(f"Found {len(all_transactions)} transactions, first date: {first_transaction_date}")
                
                # Get aligned aggregate series
                agg_series = get_aligned_aggregate_series(db, first_transaction_date, portfolio_ids)
                
                if not agg_series or len(agg_series) < 2:
                    logger.warning(f"Insufficient aggregate series data: {len(agg_series) if agg_series else 0} days")
                    return {
                        'value_change': value_change,
                        'value_change_pct': value_change_pct,
                        'invested_change': invested_change,
                        'invested_change_pct': invested_change_pct,
                        'pnl_change': pnl_change,
                        'pnl_change_pct': pnl_change_pct,
                        'twr': None,
                        'twr_annualized': None,
                        'xirr': None,
                        'mdd': None,
                        'volatility': None,
                        'sharpe_ratio': None,
                        'portfolio_type': 'all'
                    }
                
                logger.debug(f"Got aggregate series with {len(agg_series)} days")
                
                if agg_series and len(agg_series) >= 2:
                    # Calculate daily returns
                    daily_returns = []
                    for i in range(1, len(agg_series)):
                        prev = agg_series[i-1]
                        curr = agg_series[i]
                        
                        v_start = float(prev['total_value'])
                        v_end = float(curr['total_value'])
                        
                        # Net Flow = (Dep_curr - Dep_prev) - (Wd_curr - Wd_prev)
                        daily_net_flow = float(
                            (curr['deposits'] - prev['deposits']) - 
                            (curr['withdrawals'] - prev['withdrawals'])
                        )
                        
                        if v_start > 0:
                            daily_return = ((v_end - daily_net_flow) / v_start) - 1
                            daily_returns.append(daily_return)
                        else:
                            daily_returns.append(0.0)
                    
                    if len(daily_returns) >= 10:
                        # Calculate annualized TWR from daily returns
                        actual_days = (agg_series[-1]['date'] - agg_series[0]['date']).days
                        if actual_days < 1:
                            actual_days = 1
                        
                        cumulative_return = 1.0
                        for r in daily_returns:
                            cumulative_return *= (1 + r)
                        
                        # Annualized TWR (this is what goes in the cache)
                        annualization_factor = 365 / actual_days
                        twr_annualized = (pow(cumulative_return, annualization_factor) - 1) * 100
                        
                        # Also store period TWR (non-annualized) for reference
                        twr = (cumulative_return - 1) * 100
                        
                        # Calculate volatility (annualized)
                        mean_return = sum(daily_returns) / len(daily_returns)
                        variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
                        daily_volatility = math.sqrt(variance)
                        volatility = daily_volatility * math.sqrt(365) * 100
                        
                        # Calculate Sharpe ratio
                        risk_free_rate = 2.0
                        if volatility > 0:
                            sharpe_ratio = (twr_annualized - risk_free_rate) / volatility
                        
                        # Calculate MDD from NAV unit prices (not raw values)
                        unit_prices = [100.0]
                        for r in daily_returns:
                            unit_prices.append(unit_prices[-1] * (1 + r))
                        
                        mdd = 0.0
                        peak = unit_prices[0]
                        for price in unit_prices:
                            if price > peak:
                                peak = price
                            if peak > 0:
                                drawdown = ((price - peak) / peak) * 100
                                if drawdown < mdd:
                                    mdd = drawdown
                        
                        # Calculate aggregate XIRR using NAV-based method (same as individual portfolios)
                        try:
                            NAV_INITIAL_PRICE = 100.0
                            
                            first_day = agg_series[0]
                            
                            # Start: Calculate initial units based on first day's total value
                            start_total_value = float(first_day['total_value'])
                            start_nav_price = NAV_INITIAL_PRICE
                            start_nav_units = start_total_value / start_nav_price if start_nav_price > 0 else 0
                            start_date_xirr = first_day['date']
                            
                            # Calculate NAV price at end by tracking daily returns
                            nav_price = start_nav_price
                            nav_units = start_nav_units
                            
                            for i in range(1, len(agg_series)):
                                prev_day = agg_series[i-1]
                                curr_day = agg_series[i]
                                
                                # Calculate net flow for this day
                                net_flow = float(
                                    (curr_day['deposits'] - prev_day['deposits']) -
                                    (curr_day['withdrawals'] - prev_day['withdrawals'])
                                )
                                
                                # Get values
                                prev_value = float(prev_day['total_value'])
                                curr_value = float(curr_day['total_value'])
                                
                                # Calculate return (adjusting for flows)
                                if prev_value > 0:
                                    daily_return_calc = ((curr_value - net_flow) / prev_value) - 1
                                    nav_price = nav_price * (1 + daily_return_calc)
                                else:
                                    daily_return_calc = 0
                                
                                # Update units if there was a flow
                                if abs(net_flow) > 0.01 and nav_price > 0:
                                    # Positive net_flow = deposit (buy units)
                                    # Negative net_flow = withdrawal (sell units)
                                    units_changed = net_flow / nav_price
                                    nav_units += units_changed
                            
                            # End: Calculate ending value using NAV
                            end_nav_price = nav_price
                            end_nav_units = nav_units
                            
                            # Get actual current value from latest snapshots
                            current_total_value = 0.0
                            for pid in portfolio_ids:
                                latest_snap = db.query(Snapshot).filter(
                                    Snapshot.portfolio_id == pid
                                ).order_by(Snapshot.snapshot_date.desc()).first()
                                if latest_snap:
                                    current_total_value += float(latest_snap.total_value_base or 0)
                            
                            # Adjust end NAV price if current value differs from series end
                            if end_nav_units > 0:
                                end_nav_price = current_total_value / end_nav_units
                            
                            end_date_xirr = today
                            
                            # Build cash flows using NAV method (same as individual portfolios)
                            cashflows = []
                            cashflow_dates = []
                            
                            # Initial investment: start_units × start_nav_price (negative = cash out)
                            cashflows.append(-start_nav_units * start_nav_price)
                            cashflow_dates.append(start_date_xirr)
                            
                            # Add all transactions at their actual cash amounts
                            for txn in all_transactions:
                                txn_date = txn.occurred_at.date()
                                # Ensure dates are comparable (convert to date if needed)
                                try:
                                    if start_date_xirr <= txn_date <= end_date_xirr:
                                        tx_value = abs(float(txn.value_base))
                                        if txn.type in ['buy', 'deposit', 'transfer_in']:
                                            cashflows.append(-tx_value)
                                            cashflow_dates.append(txn_date)
                                        elif txn.type in ['sell', 'withdrawal', 'transfer_out']:
                                            cashflows.append(tx_value)
                                            cashflow_dates.append(txn_date)
                                except TypeError as e:
                                    logger.warning(f"Date comparison error: start_date_xirr={start_date_xirr} (type={type(start_date_xirr)}), txn_date={txn_date} (type={type(txn_date)}), end_date_xirr={end_date_xirr} (type={type(end_date_xirr)}), error={e}")
                                    continue
                            
                            # Ending value: end_units × end_nav_price (positive = cash in)
                            cashflows.append(end_nav_units * end_nav_price)
                            cashflow_dates.append(end_date_xirr)
                            
                            # Calculate XIRR
                            if len(cashflows) >= 2:
                                clean_flows = []
                                clean_dates = []
                                for cf, dt in zip(cashflows, cashflow_dates):
                                    if abs(cf) > 0.01:
                                        clean_flows.append(cf)
                                        clean_dates.append(dt)
                                
                                if len(clean_flows) >= 2:
                                    xirr_result = calculate_xirr(clean_flows, clean_dates)
                                    if xirr_result is not None:
                                        xirr = xirr_result * 100
                        
                        except Exception as e:
                            logger.debug(f"XIRR calculation failed for aggregate: {e}")
                
            except Exception as e:
                logger.debug(f"Could not calculate aggregate TWR/XIRR/MDD: {e}")
            
            return {
                'value_change': value_change,
                'value_change_pct': value_change_pct,
                'invested_change': invested_change,
                'invested_change_pct': invested_change_pct,
                'pnl_change': pnl_change,
                'pnl_change_pct': pnl_change_pct,
                'twr': twr,
                'twr_annualized': twr_annualized,
                'xirr': xirr,
                'mdd': mdd,
                'volatility': volatility,
                'sharpe_ratio': sharpe_ratio,
                'portfolio_type': 'all'
            }
    
    except Exception as e:
        logger.warning(f"Could not calculate period statistics: {e}", exc_info=True)
        return None


def get_portfolio_summary(db, portfolio_id=None):
    """
    Get portfolio summary data.
    
    Args:
        db: Database session
        portfolio_id: Optional portfolio ID. If None, aggregates all portfolios
    
    Returns:
        dict with keys: portfolio, positions, total_value, total_invested, realized_pnl, 
        unrealized_pnl, total_pnl, pnl_percentage, change_7d, change_30d, change_365d, position_count
        OR None if no portfolio found
    """
    # Get portfolio config to determine which table to use
    portfolios_loader = get_portfolios_loader()
    all_portfolios_config = {p['id']: p for p in portfolios_loader.get_portfolios()}
    
    if portfolio_id:
        # Single portfolio view
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        
        if not portfolio:
            return None
        
        # Check portfolio type from config to determine which positions table to query
        portfolio_config = all_portfolios_config.get(portfolio.id, {})
        portfolio_type = portfolio_config.get('type', '')
        update_method = portfolio_config.get('update_method', '')
        
        # For manual cash portfolios (Portfolio 1 & 2), use CashPosition table
        # Must check BOTH type='cash' AND update_method='manual' because:
        # - Portfolio 8 (Broker Cash Pool) is type='cash' but update_method='automatic' (uses Position table)
        # - Closed mutual fund portfolios may be update_method='manual' but type='securities' (uses Position table)
        if portfolio_type == 'cash' and update_method == 'manual':
            # Get cash positions from CashPosition table
            cash_positions = db.query(CashPosition).filter(
                CashPosition.portfolio_id == portfolio.id
            ).all()
            
            # Convert CashPosition to Position-like objects for consistent display
            # Create a Position-like structure that the UI can render
            positions = []
            for cp in cash_positions:
                # Create a dynamic object with Position-like attributes
                pos_obj = type('obj', (object,), {
                    'symbol': cp.label,  # Use label as "symbol"
                    'quantity': cp.amount,
                    'current_price': Decimal('1.0') if cp.currency == portfolio.currency_base else None,
                    'portfolio_id': cp.portfolio_id,
                    'currency': cp.currency,  # Store original currency
                    'is_cash_position': True  # Flag to identify cash positions
                })()
                positions.append(pos_obj)
        else:
            # Get current positions from Position table (stocks, crypto, broker cash, etc.)
            positions = db.query(Position).filter(
                Position.portfolio_id == portfolio.id,
                Position.quantity > 0
            ).all()
        
        # Get previous snapshots for comparison (7, 30, 365 days ago)
        # Use closest available snapshot if exact date doesn't exist (weekends, holidays, etc.)
        today = date.today()
        date_7d = today - timedelta(days=7)
        date_30d = today - timedelta(days=30)
        date_365d = today - timedelta(days=365)
        
        # Get closest available snapshots (not exact dates - handles weekends, holidays)
        snap_7d = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio.id,
            Snapshot.snapshot_date <= date_7d
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        snap_30d = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio.id,
            Snapshot.snapshot_date <= date_30d
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        snap_365d = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio.id,
            Snapshot.snapshot_date <= date_365d
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        previous_7d = snap_7d.total_value_base if snap_7d else None
        previous_30d = snap_30d.total_value_base if snap_30d else None
        previous_365d = snap_365d.total_value_base if snap_365d else None
        
    else:
        # All portfolios aggregated view
        all_portfolios = portfolios_loader.get_portfolios()
        
        # Separate investment portfolios from cash portfolios
        # Investment portfolios: type NOT 'cash' AND status NOT 'reserved'
        # Include closed investment portfolios (e.g., Portfolio 7) for historical data
        investment_portfolio_ids = [
            p['id'] for p in all_portfolios 
            if p.get('type') not in ['cash', 'placeholder'] and p.get('status') != 'reserved'
        ]
        
        # All portfolios (for total value/wealth calculation)
        all_portfolio_ids = [p['id'] for p in all_portfolios if p.get('status') != 'reserved']
        
        # Create a virtual "All Portfolios" object
        PortfolioView = namedtuple('PortfolioView', ['id', 'name', 'currency_base'])
        portfolio = PortfolioView(id=None, name='All Portfolios', currency_base=get_global_base_currency())
        
        # Get positions from all selected portfolios
        positions = db.query(Position).filter(
            Position.portfolio_id.in_(all_portfolio_ids),
            Position.quantity > 0
        ).all()
        
        # Get previous snapshots for all portfolios (for 7, 30, 365 days ago)
        # Use closest available snapshot if exact date doesn't exist (weekends, holidays, etc.)
        today = date.today()
        date_7d = today - timedelta(days=7)
        date_30d = today - timedelta(days=30)
        date_365d = today - timedelta(days=365)
        
        # Helper to find closest snapshot for each portfolio at a target date
        def get_portfolio_values_at_date(target_date, portfolio_ids):
            """Get portfolio values at or before target date (closest available snapshot)"""
            portfolio_values = {}
            for pid in portfolio_ids:
                closest_snap = db.query(Snapshot).filter(
                    Snapshot.portfolio_id == pid,
                    Snapshot.snapshot_date <= target_date
                ).order_by(Snapshot.snapshot_date.desc()).first()
                
                if closest_snap:
                    portfolio_values[pid] = closest_snap.total_value_base or Decimal('0')
            return portfolio_values
        
        # Calculate totals for each comparison date using closest available snapshots
        previous_7d = None
        previous_30d = None
        previous_365d = None
        
        # 7-day: get closest snapshots for all portfolios
        values_7d = get_portfolio_values_at_date(date_7d, all_portfolio_ids)
        if values_7d:
            previous_7d = sum(values_7d.values())
        
        # 30-day: get closest snapshots for all portfolios
        values_30d = get_portfolio_values_at_date(date_30d, all_portfolio_ids)
        if values_30d:
            previous_30d = sum(values_30d.values())
        
        # 365-day: get closest snapshots for all portfolios
        values_365d = get_portfolio_values_at_date(date_365d, all_portfolio_ids)
        if values_365d:
            previous_365d = sum(values_365d.values())
    
    # USE SNAPSHOTS FOR TOTAL VALUE AND PNL (consistent with chart, works for all portfolios)
    # All portfolios have snapshots, so use them as the single source of truth
    today = date.today()
    
    if portfolio_id:
        # Single portfolio - get today's snapshot
        today_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date == today
        ).first()
        
        if not today_snapshot:
            # Fall back to most recent snapshot
            today_snapshot = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id
            ).order_by(Snapshot.snapshot_date.desc()).first()
        
        total_value = today_snapshot.total_value_base if today_snapshot else Decimal('0')
        # CRITICAL: Use net invested capital (deposits - withdrawals), not cost basis
        total_invested = Decimal(str(calculate_net_invested_capital(today_snapshot))) if today_snapshot else Decimal('0')
        realized_pnl = today_snapshot.realized_pnl_base if today_snapshot else Decimal('0')
        unrealized_pnl = today_snapshot.unrealized_pnl_base if today_snapshot else Decimal('0')
    else:
        # All portfolios - get latest snapshot for EACH portfolio (may be different dates)
        # Total value includes ALL portfolios (for net worth calculation)
        all_latest_snapshots = []
        for pid in all_portfolio_ids:
            latest_snap = db.query(Snapshot).filter(
                Snapshot.portfolio_id == pid
            ).order_by(Snapshot.snapshot_date.desc()).first()
            
            if latest_snap:
                all_latest_snapshots.append(latest_snap)
        
        # Total value (Market Value) from all portfolios including cash
        total_value = sum(s.total_value_base or Decimal('0') for s in all_latest_snapshots)
        
        # CRITICAL: Invested Capital and P&L from INVESTMENT portfolios only (exclude cash)
        # Use net invested capital (deposits - withdrawals), not cost basis
        investment_snapshots = [s for s in all_latest_snapshots if s.portfolio_id in investment_portfolio_ids]
        total_invested = sum(Decimal(str(calculate_net_invested_capital(s))) for s in investment_snapshots)
        realized_pnl = sum(s.realized_pnl_base or Decimal('0') for s in investment_snapshots)
        unrealized_pnl = sum(s.unrealized_pnl_base or Decimal('0') for s in investment_snapshots)
    
    # Enrich positions with current prices for display purposes only (not used for total_value)
    for position in positions:
        # Get the portfolio for this position to know its base currency
        portfolio_for_position = db.query(Portfolio).filter(Portfolio.id == position.portfolio_id).first()
        portfolio_base = portfolio_for_position.currency_base
        
        # Handle cash positions differently (they have .is_cash_position attribute)
        if hasattr(position, 'is_cash_position') and position.is_cash_position:
            # Cash position - need to convert currency to base
            cash_currency = position.currency
            cash_amount = position.quantity
            
            if cash_currency == portfolio_base:
                # Same currency - direct value
                position.current_price = Decimal('1.0')
            else:
                # Different currency - need FX conversion
                fx_pair = f"{cash_currency}/{portfolio_base}"
                fx_rate = get_latest_fx_rate(db, fx_pair)
                if fx_rate:
                    position.current_price = Decimal(str(fx_rate.rate))
                else:
                    # No FX rate - use 1:1 as fallback
                    position.current_price = Decimal('1.0')
            continue
        
        # Try to get market price first (for stocks, crypto, etc.)
        price_data = get_latest_price(db, position.symbol)
        if price_data:
            # We have a market price, but it might be in a different currency
            price_currency = price_data.currency  # USD, THB, etc.
            price_value = Decimal(str(price_data.price))
            
            # Check if price currency matches portfolio base currency
            if price_currency == portfolio_base:
                # Direct match - use as is
                position.current_price = price_value
            else:
                # Need to convert price from price_currency to portfolio_base
                fx_pair = f"{price_currency}/{portfolio_base}"
                fx_rate = get_latest_fx_rate(db, fx_pair)
                if fx_rate:
                    # Convert price to portfolio base currency
                    position.current_price = price_value * Decimal(str(fx_rate.rate))
                else:
                    # Can't convert - use price as-is (fallback)
                    position.current_price = price_value
        else:
            # No market price - check if it's the base currency
            if position.symbol == portfolio_base:
                # Base currency: 1:1 value
                position.current_price = Decimal('1.0')
            else:
                # Try FX rate for currency conversion
                fx_pair = f"{position.symbol}/{portfolio_base}"
                fx_rate = get_latest_fx_rate(db, fx_pair)
                if fx_rate:
                    position.current_price = fx_rate.rate
                else:
                    # No price or FX rate - use face value as fallback
                    position.current_price = Decimal('1.0')
    
    # Calculate percentage changes for 7-day, 30-day, and 365-day windows
    # These are HPR (Holding Period Return) calculations - simple percentage change
    hpr_7d = 0.0
    hpr_30d = 0.0
    hpr_365d = 0.0
    
    if previous_7d and previous_7d > 0:
        hpr_7d = ((float(total_value) - float(previous_7d)) / float(previous_7d)) * 100
    if previous_30d and previous_30d > 0:
        hpr_30d = ((float(total_value) - float(previous_30d)) / float(previous_30d)) * 100
    if previous_365d and previous_365d > 0:
        hpr_365d = ((float(total_value) - float(previous_365d)) / float(previous_365d)) * 100
    
    # Calculate total PnL and PnL percentage
    total_pnl = realized_pnl + unrealized_pnl
    pnl_percentage = 0.0
    if total_invested and total_invested > 0:
        pnl_percentage = (float(total_pnl) / float(total_invested)) * 100
    
    # Determine portfolio type for conditional UI rendering
    if portfolio_id:
        portfolio_config = all_portfolios_config.get(portfolio_id, {})
        portfolio_type = portfolio_config.get('type', '')
    else:
        portfolio_type = 'all'  # Special type for "All Portfolios" view
    
    # Note: 7d, 30d, 365d TWR/MDD metrics are no longer calculated here
    # They were never displayed in the UI - only lifetime TWR is shown
    # Period-specific TWR is available via _calculate_twr_between_dates() if needed
    
    
    return {
        'portfolio': portfolio,
        'positions': positions,
        'total_value': total_value,          # Market Value - includes all portfolios
        'total_invested': total_invested,    # Invested Capital - investment portfolios only for 'All Portfolios'
        'realized_pnl': realized_pnl,        # Investment P&L (realized) - investment portfolios only for 'All Portfolios'
        'unrealized_pnl': unrealized_pnl,    # Investment P&L (unrealized) - investment portfolios only for 'All Portfolios'
        'total_pnl': total_pnl,              # Total Investment P&L - investment portfolios only for 'All Portfolios'
        'pnl_percentage': pnl_percentage,    # Investment Return % - based on investment portfolios only
        # HPR (Holding Period Return) - simple percentage change
        'hpr_7d': hpr_7d,
        'hpr_30d': hpr_30d,
        'hpr_365d': hpr_365d,
        'position_count': len(positions),
        'portfolio_type': portfolio_type
    }


def get_portfolio_kpi_data(db, portfolio_id: int) -> Optional[Dict]:
    """
    Get KPI data for a single portfolio (for Tier 1 cards).
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
    
    Returns:
        dict with KPI metrics or None if portfolio not found
    """
    portfolios_loader = get_portfolios_loader()
    portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), {})
    
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        return None
    
    # Get latest snapshot
    latest_snap = db.query(Snapshot).filter(
        Snapshot.portfolio_id == portfolio_id
    ).order_by(Snapshot.snapshot_date.desc()).first()
    
    if not latest_snap:
        return None
    
    # Get performance metrics
    perf = calculate_portfolio_return(db, portfolio_id)
    
    # Calculate performance metrics only for active investment portfolios (skip cash and closed)
    # This saves processing power on portfolios where these metrics are not meaningful
    portfolio_type = portfolio_config.get('type', '')
    portfolio_status = portfolio_config.get('status', 'active')
    
    if portfolio_type not in ['cash', 'all', ''] and portfolio_status != 'closed':
        mdd = calculate_max_drawdown(db, portfolio_id, 3650)
        twr = float(perf.get('twr_pct', 0)) if perf.get('data_available') else 0
        lifetime_xirr = float(perf.get('xirr_pct', 0)) if perf.get('data_available') else 0
    else:
        mdd = None
        twr = None
        lifetime_xirr = None
    
    return {
        'id': portfolio_id,
        'name': portfolio_config.get('name', portfolio.name),
        'type': portfolio_config.get('type', 'other'),
        'currency': portfolio.currency_base,
        'total_value': float(latest_snap.total_value_base or 0),
        'total_invested': float(latest_snap.total_invested_base or 0),
        'unrealized_pnl': float(latest_snap.unrealized_pnl_base or 0),
        'realized_pnl': float(latest_snap.realized_pnl_base or 0),
        'total_pnl': float((latest_snap.unrealized_pnl_base or 0) + (latest_snap.realized_pnl_base or 0)),
        'twr': twr,
        'xirr': lifetime_xirr,
        'mdd': mdd,
        'years_active': float(perf.get('years_active', 0)) if perf.get('data_available') else 0,
        'first_snapshot_date': perf.get('first_snapshot_date') if perf.get('data_available') else None,
    }
