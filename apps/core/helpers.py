"""
Shared Helper Functions for NiceFolio
Common utility functions used across multiple pages.
"""

from decimal import Decimal
from datetime import date, timedelta
from collections import defaultdict
from database import SessionLocal
from models import Snapshot, MarketData
from utils.app_config import get_global_base_currency, load_app_config
from utils.portfolios_loader import get_portfolios_loader
from utils.logging_config import get_logger
import scipy.optimize
import math
import pandas as pd

logger = get_logger(__name__)


def calculate_net_invested_capital(snapshot) -> float:
    """
    Calculate net invested capital from a snapshot.
    
    Net invested capital = deposits - withdrawals
    This represents the actual cash flow into/out of the portfolio,
    which is different from cost_basis (total_invested_base).
    
    NAMING CLARIFICATION:
    Despite its name, snapshot.total_invested_base is actually COST BASIS,
    not net invested capital. This function calculates the correct
    "Invested Capital" for display purposes.
    
    Args:
        snapshot: Snapshot object or dict with deposits_base and withdrawals_base
        
    Returns:
        float: Net invested capital (deposits - withdrawals)
    """
    if snapshot is None:
        return 0.0
    
    # Handle dict or object
    if isinstance(snapshot, dict):
        deposits = snapshot.get('deposits_base', 0) or 0
        withdrawals = snapshot.get('withdrawals_base', 0) or 0
    else:
        deposits = getattr(snapshot, 'deposits_base', None)
        withdrawals = getattr(snapshot, 'withdrawals_base', None)
        
        # Handle None values
        if deposits is None:
            deposits = 0
        if withdrawals is None:
            withdrawals = 0
    
    # Convert to float, handling Decimal and None
    try:
        deposits_float = float(deposits) if deposits else 0.0
        withdrawals_float = float(withdrawals) if withdrawals else 0.0
        return deposits_float - withdrawals_float
    except (TypeError, ValueError, AttributeError) as e:
        # Fallback if conversion fails
        logger.warning(f"Error calculating net invested capital: {e}, deposits={deposits}, withdrawals={withdrawals}")
        return 0.0


def calculate_xirr(cashflows, dates):
    """
    Calculate XIRR using scipy.optimize.newton
    cashflows: list of float amounts (negative for investment, positive for return)
    dates: list of date objects
    """
    if not cashflows or not dates or len(cashflows) != len(dates):
        return None

    try:
        # Must have at least one positive and one negative cashflow
        has_pos = False
        has_neg = False
        for cf in cashflows:
            if cf > 0: has_pos = True
            if cf < 0: has_neg = True
            if has_pos and has_neg: break
        
        if not (has_pos and has_neg):
            return None

        # Sort pairs by date
        sorted_data = sorted(zip(dates, cashflows), key=lambda x: x[0])
        dates_sorted, cf_sorted = zip(*sorted_data)
        
        start_date = dates_sorted[0]
        
        def xnpv(rate):
            if rate <= -1.0:
                 # As rate -> -1, (1+rate)^t -> 0. 
                 # If t > 0, term -> inf * sign(cf). 
                 # We return a large number instead of inf to help optimizers
                 return 1e100
            try:
                return sum([cf / ((1 + rate) ** ((d - start_date).days / 365.0)) for d, cf in zip(dates_sorted, cf_sorted)])
            except (OverflowError, ZeroDivisionError):
                return 1e100

        # 1. Try Newton-Raphson with multiple guesses
        guesses = [0.1, 0.5, -0.5, 0.0, 1.0]
        for guess in guesses:
            try:
                return scipy.optimize.newton(xnpv, guess, tol=1e-6, maxiter=50)
            except Exception:
                # Catch RuntimeError (convergence), OverflowError (math), etc.
                continue

        # 2. Try Brent's method (robust bracketing) if Newton fails
        # We search from -0.99 (near total loss) to 1000.0 (100,000% return)
        a, b = -0.99, 1000.0
        try:
            f_a = xnpv(a)
            f_b = xnpv(b)
            if f_a * f_b < 0:
                return scipy.optimize.brentq(xnpv, a, b)
            elif f_a > 0 and f_b > 0:
                 # Curve doesn't cross zero in reasonable range (returns > 100,000%) or invalid Data (Profit without Cost)
                 # Check if we assume 'infinite' return
                 pass
        except Exception:
            pass

        # 3. Log data for debugging
        total_in = sum(c for c in cashflows if c < 0)
        total_out = sum(c for c in cashflows if c > 0)
        logger.warning(
            f"XIRR failed to converge. "
            f"Flows: {len(cashflows)}. "
            f"In: {total_in:.2f}, Out: {total_out:.2f}. "
            f"First: {cashflows[0] if cashflows else 'None'} ({dates[0] if dates else 'None'}). "
            f"Last: {cashflows[-1] if cashflows else 'None'}."
        )
        return None
    except Exception as e:
        logger.warning(f"XIRR calculation failed: {e}")
        return None


def get_aligned_aggregate_series(db, start_date, portfolio_ids=None):
    """
    Generates a single time-series for 'All Portfolios' by correctly 
    forward-filling missing snapshots before summing.
    Excludes 'cash' type portfolios and closed portfolios.
    If portfolio_ids is provided, uses those (filtering out cash and closed).
    If None, uses all active non-cash portfolios.
    """
    # 0. Get valid portfolio IDs (excluding cash and closed)
    portfolios_loader = get_portfolios_loader()
    all_portfolios = portfolios_loader.get_portfolios()
    
    # Set of all valid IDs (active portfolios only, excluding reserved, closed, cash, placeholder)
    valid_ids = {
        p['id'] for p in all_portfolios 
        if p.get('status') not in ['reserved', 'closed'] and p.get('type') not in ['cash', 'placeholder']
    }
    
    if portfolio_ids:
        # Use intersection of requested and valid
        target_ids = [pid for pid in portfolio_ids if pid in valid_ids]
    else:
        target_ids = list(valid_ids)
        
    if not target_ids:
        return []
    
    # 1. Fetch ALL snapshots for the period for valid portfolios
    # We need strictly ordered data
    query = db.query(Snapshot).filter(
        Snapshot.snapshot_date >= start_date,
        Snapshot.portfolio_id.in_(target_ids)
    ).order_by(Snapshot.snapshot_date)
    all_snapshots = query.all()

    if not all_snapshots:
        return []

    # 2. Group snapshots by Portfolio ID
    # Structure: { portfolio_id: { date: snapshot_obj } }
    portfolio_map = defaultdict(dict)
    all_dates = set()
    
    for s in all_snapshots:
        portfolio_map[s.portfolio_id][s.snapshot_date] = s
        all_dates.add(s.snapshot_date)
    
    # 3. Create a master timeline
    sorted_dates = sorted(list(all_dates))
    
    # 4. Initialize "Last Known State" for every portfolio
    # If a portfolio hasn't started yet, its values are 0.
    portfolio_state = {
        pid: {
            'val': Decimal('0'), 
            'dep': Decimal('0'), 
            'wd': Decimal('0')
        } 
        for pid in target_ids
    }

    aggregate_series = []

    # 5. Walk the timeline day by day
    for d in sorted_dates:
        daily_total_val = Decimal('0')
        daily_total_dep = Decimal('0')
        daily_total_wd = Decimal('0')

        for pid in target_ids:
            # If this portfolio has a snapshot today, update its state
            if d in portfolio_map[pid]:
                snap = portfolio_map[pid][d]
                portfolio_state[pid]['val'] = snap.total_value_base or Decimal('0')
                portfolio_state[pid]['dep'] = snap.deposits_base or Decimal('0')
                portfolio_state[pid]['wd'] = snap.withdrawals_base or Decimal('0')
            
            # ADD THE STATE to the daily total (Forward-Fill logic)
            # Even if the portfolio is missing a snapshot today, we use yesterday's values
            daily_total_val += portfolio_state[pid]['val']
            daily_total_dep += portfolio_state[pid]['dep']
            daily_total_wd += portfolio_state[pid]['wd']

        aggregate_series.append({
            'date': d,
            'total_value': daily_total_val,
            'deposits': daily_total_dep,
            'withdrawals': daily_total_wd
        })

    return aggregate_series


def calculate_max_drawdown(db, portfolio_id, days):
    """
    Calculate Maximum Drawdown (MDD) based on Unit Price (NAV per share).
    
    This correctly isolates market risk from cash flows:
    - Deposits/withdrawals change the number of units, not the unit price
    - Only market movements affect the unit price
    - MDD is calculated on unit price to avoid false signals from cash flows
    
    Uses the same geometric linking approach as TWR:
    Unit Price growth = (V_end - Net_Cash_Flow) / V_start
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID (None for aggregated view)
        days: Number of days to look back
    
    Returns:
        float: MDD as percentage (negative value, e.g., -15.5 for 15.5% drawdown)
    """
    try:
        # Calculate date range
        today = date.today()
        start_date = today - timedelta(days=days)
        
        if portfolio_id:
            # Single portfolio - calculate unit price using same logic as TWR
            snapshots = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id,
                Snapshot.snapshot_date >= start_date,
                Snapshot.snapshot_date <= today
            ).order_by(Snapshot.snapshot_date).all()
            
            if len(snapshots) < 2:
                return 0.0
            
            # Find first snapshot with non-zero value
            start_idx = None
            for i, snap in enumerate(snapshots):
                if float(snap.total_value_base or Decimal('0')) > 0:
                    start_idx = i
                    break
            
            if start_idx is None or start_idx >= len(snapshots) - 1:
                return 0.0
            
            # Calculate unit price series using TWR logic
            # Start with unit price = 100.0
            unit_prices = [100.0]
            
            for i in range(start_idx + 1, len(snapshots)):
                prev_snap = snapshots[i - 1]
                curr_snap = snapshots[i]
                
                v_start = float(prev_snap.total_value_base or Decimal('0'))
                v_end = float(curr_snap.total_value_base or Decimal('0'))
                
                # Get daily cash flow delta from cumulative totals
                prev_deposits = float(prev_snap.deposits_base or Decimal('0'))
                curr_deposits = float(curr_snap.deposits_base or Decimal('0'))
                prev_withdrawals = float(prev_snap.withdrawals_base or Decimal('0'))
                curr_withdrawals = float(curr_snap.withdrawals_base or Decimal('0'))
                
                daily_deposits = curr_deposits - prev_deposits
                daily_withdrawals = curr_withdrawals - prev_withdrawals
                net_flow = daily_deposits - daily_withdrawals
                
                # Calculate daily growth factor (same as TWR)
                if v_start > 0:
                    daily_growth = (v_end - net_flow) / v_start
                    # Apply to unit price
                    new_unit_price = unit_prices[-1] * daily_growth
                    unit_prices.append(new_unit_price)
                elif v_end > 0 and net_flow > 0:
                    # First deposit, maintain unit price
                    unit_prices.append(unit_prices[-1])
                else:
                    # Both zero, no change
                    unit_prices.append(unit_prices[-1])
            
            # Calculate MDD on unit prices
            if len(unit_prices) < 2:
                return 0.0
            
            peak = unit_prices[0]
            max_drawdown = 0.0
            
            for price in unit_prices:
                if price > peak:
                    peak = price
                if peak > 0:
                    drawdown = ((price - peak) / peak) * 100
                    if drawdown < max_drawdown:
                        max_drawdown = drawdown
            
            return max_drawdown
            
        else:
            # --- ALL PORTFOLIOS AGGREGATED ---
            # USE THE NEW ALIGNED SERIES (Forward-Filled)
            agg_series = get_aligned_aggregate_series(db, start_date)
            
            if len(agg_series) < 2:
                return 0.0

            # Find first date with non-zero value
            start_idx = None
            for i, data in enumerate(agg_series):
                if float(data['total_value']) > 0:
                    start_idx = i
                    break
            
            if start_idx is None or start_idx >= len(agg_series) - 1:
                return 0.0

            # Now calculate Unit Price on the aggregated series
            unit_prices = [100.0]
            
            for i in range(start_idx + 1, len(agg_series)):
                prev = agg_series[i-1]
                curr = agg_series[i]
                
                v_start = float(prev['total_value'])
                v_end = float(curr['total_value'])
                
                # Calculate Net Flow from Cumulative Deltas
                # This handles new portfolios correctly (0 -> 100 is a 100 deposit)
                daily_net_flow = float(
                    (curr['deposits'] - prev['deposits']) - 
                    (curr['withdrawals'] - prev['withdrawals'])
                )
                
                if v_start > 0:
                    daily_growth = (v_end - daily_net_flow) / v_start
                    unit_prices.append(unit_prices[-1] * daily_growth)
                elif v_end > 0 and daily_net_flow > 0:
                    # Portfolio system initialized (First deposit)
                    unit_prices.append(unit_prices[-1])
                else:
                    unit_prices.append(unit_prices[-1])
            
            # Calculate MDD
            if len(unit_prices) < 2:
                return 0.0
            
            peak = unit_prices[0]
            max_drawdown = 0.0
            
            for price in unit_prices:
                if price > peak:
                    peak = price
                if peak > 0:
                    drawdown = ((price - peak) / peak) * 100
                    if drawdown < max_drawdown:
                        max_drawdown = drawdown
            
            return max_drawdown
            
    except Exception as e:
        logger.warning(f"Could not calculate MDD for portfolio {portfolio_id}: {e}", exc_info=True)
        return 0.0


def _calculate_twr_between_dates(db, portfolio_id, start_date, end_date):
    """
    Calculate TWR between two specific dates using NAV-based method.
    
    TWR (Time-Weighted Return) measures investment performance isolated from cash flows
    by using NAV price growth. NAV price already accounts for cash flows 
    (deposits/withdrawals buy/sell units), so TWR is simply: 
    (end_NAV / start_NAV - 1) × 100
    
    This is the industry standard method used by mutual funds and ETFs. It is
    mathematically equivalent to geometric linking but simpler and more accurate.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    
    Returns:
        float: TWR as percentage (e.g., 5.25 for 5.25%)
    """
    try:
        # Get start and end snapshots
        start_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date >= start_date
        ).order_by(Snapshot.snapshot_date).first()
        
        end_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        if not start_snapshot or not end_snapshot:
            return 0.0
        
        # Get NAV prices
        start_nav = float(start_snapshot.nav_price or 0)
        end_nav = float(end_snapshot.nav_price or 0)
        
        if start_nav <= 0:
            return 0.0
        
        # TWR is simply NAV price growth
        twr = ((end_nav / start_nav) - 1.0) * 100
        
        logger.debug(f"Portfolio {portfolio_id} TWR ({start_snapshot.snapshot_date} to {end_snapshot.snapshot_date}): "
                    f"NAV {start_nav:.2f} → {end_nav:.2f} = {twr:.2f}%")
        return round(twr, 2)
        
    except Exception as e:
        logger.warning(f"Could not calculate TWR between dates for portfolio {portfolio_id}: {e}")
        return 0.0


def _calculate_mdd_between_dates(db, portfolio_id, start_date, end_date):
    """
    Calculate Maximum Drawdown between two specific dates (not days back from today).
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    
    Returns:
        float: MDD as percentage (negative value, e.g., -15.5 for 15.5% drawdown)
    """
    try:
        # Get snapshots within the date range
        snapshots = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date >= start_date,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date).all()
        
        if len(snapshots) < 2:
            return 0.0
        
        # Find first snapshot with non-zero value
        start_idx = None
        for i, snap in enumerate(snapshots):
            if float(snap.total_value_base or Decimal('0')) > 0:
                start_idx = i
                break
        
        if start_idx is None or start_idx >= len(snapshots) - 1:
            return 0.0
        
        # Calculate unit price series using TWR logic
        unit_prices = [100.0]
        
        for i in range(start_idx + 1, len(snapshots)):
            prev_snap = snapshots[i - 1]
            curr_snap = snapshots[i]
            
            v_start = float(prev_snap.total_value_base or Decimal('0'))
            v_end = float(curr_snap.total_value_base or Decimal('0'))
            
            # Get daily cash flow delta
            prev_deposits = float(prev_snap.deposits_base or Decimal('0'))
            curr_deposits = float(curr_snap.deposits_base or Decimal('0'))
            prev_withdrawals = float(prev_snap.withdrawals_base or Decimal('0'))
            curr_withdrawals = float(curr_snap.withdrawals_base or Decimal('0'))
            
            daily_deposits = curr_deposits - prev_deposits
            daily_withdrawals = curr_withdrawals - prev_withdrawals
            net_flow = daily_deposits - daily_withdrawals
            
            # Calculate daily growth factor
            if v_start > 0:
                daily_growth = (v_end - net_flow) / v_start
                new_unit_price = unit_prices[-1] * daily_growth
                unit_prices.append(new_unit_price)
            elif v_end > 0 and net_flow > 0:
                unit_prices.append(unit_prices[-1])
            else:
                unit_prices.append(unit_prices[-1])
        
        # Calculate MDD on unit prices
        if len(unit_prices) < 2:
            return 0.0
        
        peak = unit_prices[0]
        max_drawdown = 0.0
        
        for price in unit_prices:
            if price > peak:
                peak = price
            if peak > 0:
                drawdown = ((price - peak) / peak) * 100
                if drawdown < max_drawdown:
                    max_drawdown = drawdown
        
        return max_drawdown
        
    except Exception as e:
        logger.warning(f"Could not calculate MDD between dates for portfolio {portfolio_id}: {e}")
        return 0.0


def _calculate_volatility_between_dates(db, portfolio_id, start_date, end_date):
    """
    Calculate annualized volatility (standard deviation of returns) between two dates.
    
    Uses NAV price changes to calculate daily returns, then annualizes the standard deviation.
    Volatility measures the consistency of returns, independent of cash flows.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    
    Returns:
        float: Annualized volatility as percentage (e.g., 15.5 for 15.5% annualized volatility)
    """
    import math
    
    try:
        # Get snapshots within the date range
        snapshots = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date >= start_date,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date).all()
        
        if len(snapshots) < 2:
            return 0.0
        
        # Find first snapshot with non-zero value
        start_idx = None
        for i, snap in enumerate(snapshots):
            if float(snap.total_value_base or Decimal('0')) > 0:
                start_idx = i
                break
        
        if start_idx is None or start_idx >= len(snapshots) - 1:
            return 0.0
        
        # Calculate daily returns based on NAV price (unit price)
        # This isolates market returns from cash flow effects
        daily_returns = []
        
        for i in range(start_idx + 1, len(snapshots)):
            prev_snap = snapshots[i - 1]
            curr_snap = snapshots[i]
            
            v_start = float(prev_snap.total_value_base or Decimal('0'))
            v_end = float(curr_snap.total_value_base or Decimal('0'))
            
            # Get daily cash flow delta
            prev_deposits = float(prev_snap.deposits_base or Decimal('0'))
            curr_deposits = float(curr_snap.deposits_base or Decimal('0'))
            prev_withdrawals = float(prev_snap.withdrawals_base or Decimal('0'))
            curr_withdrawals = float(curr_snap.withdrawals_base or Decimal('0'))
            
            daily_deposits = curr_deposits - prev_deposits
            daily_withdrawals = curr_withdrawals - prev_withdrawals
            net_flow = daily_deposits - daily_withdrawals
            
            # Calculate daily return (growth rate)
            if v_start > 0:
                daily_return = ((v_end - net_flow) / v_start) - 1.0
                daily_returns.append(daily_return)
        
        if len(daily_returns) < 2:
            return 0.0
        
        # Calculate standard deviation of daily returns
        # Use sample standard deviation (n-1) for better statistical accuracy
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        daily_std = math.sqrt(variance)
        
        # Annualize volatility: daily_std * sqrt(252) for daily data
        # 252 is the standard number of trading days per year
        annualized_volatility = daily_std * math.sqrt(252) * 100
        
        logger.debug(f"Portfolio {portfolio_id} volatility: {annualized_volatility:.2f}% "
                    f"(n={len(daily_returns)}, daily_std={daily_std*100:.4f}%)")
        
        return round(annualized_volatility, 2)
        
    except Exception as e:
        logger.warning(f"Could not calculate volatility between dates for portfolio {portfolio_id}: {e}")
        return 0.0


def calculate_sharpe_ratio(annualized_return, annualized_volatility, risk_free_rate=2.0):
    """
    Calculate Sharpe Ratio from annualized return and volatility.
    
    Sharpe Ratio = (Return - Risk-Free Rate) / Volatility
    
    Args:
        annualized_return: Annualized return as percentage (e.g., 12.5 for 12.5%)
        annualized_volatility: Annualized volatility as percentage (e.g., 15.0 for 15%)
        risk_free_rate: Risk-free rate as percentage (default 2.0 for 2%)
    
    Returns:
        float: Sharpe ratio (e.g., 0.75)
    """
    if annualized_volatility == 0:
        return 0.0
    
    sharpe = (annualized_return - risk_free_rate) / annualized_volatility
    
    logger.debug(f"Sharpe: ({annualized_return:.2f} - {risk_free_rate:.2f}) / {annualized_volatility:.2f} = {sharpe:.2f}")
    
    return round(sharpe, 2)


def calculate_portfolio_statistics(db, portfolio_id, start_date, end_date, risk_free_rate=2.0):
    """
    Calculate comprehensive portfolio statistics for a given period.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        start_date: Start date for calculations (will use earliest available if before portfolio start)
        end_date: End date for calculations
        risk_free_rate: Risk-free rate as percentage (default 2.0 for 2%)
    
    Returns:
        dict: {
            'twr_annualized': Annualized TWR as percentage,
            'volatility_annualized': Annualized volatility as percentage,
            'sharpe_ratio': Sharpe ratio
        }
    """
    try:
        # Get actual snapshots to determine real date range
        snapshots = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date >= start_date,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date).all()
        
        if len(snapshots) < 2:
            # Try getting all available data if requested range has insufficient data
            snapshots = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id,
                Snapshot.snapshot_date <= end_date
            ).order_by(Snapshot.snapshot_date).all()
            
            if len(snapshots) < 2:
                return None
        
        # Use actual data date range for annualization
        actual_start_date = snapshots[0].snapshot_date
        actual_end_date = snapshots[-1].snapshot_date
        actual_days = (actual_end_date - actual_start_date).days
        
        if actual_days < 2:
            return None
        
        # Calculate TWR for the actual period
        twr_period = _calculate_twr_between_dates(db, portfolio_id, actual_start_date, actual_end_date)
        if twr_period is None:
            return None
        
        # Annualize TWR using ACTUAL days, not requested days
        twr_factor = 1 + (twr_period / 100)
        annualization_factor = 365 / actual_days
        twr_annualized = (pow(twr_factor, annualization_factor) - 1) * 100
        
        # Calculate annualized volatility (already annualized internally)
        volatility = _calculate_volatility_between_dates(db, portfolio_id, actual_start_date, actual_end_date)
        if volatility is None:
            return None
        
        # Calculate Sharpe ratio
        sharpe = calculate_sharpe_ratio(twr_annualized, volatility, risk_free_rate)
        
        logger.debug(f"Portfolio {portfolio_id} ({actual_days}d): TWR={twr_annualized:.1f}%, Vol={volatility:.1f}%, Sharpe={sharpe:.2f}")
        
        return {
            'twr_annualized': round(twr_annualized, 2),
            'volatility_annualized': round(volatility, 2),
            'sharpe_ratio': sharpe
        }
        
    except Exception as e:
        logger.warning(f"Could not calculate portfolio statistics for portfolio {portfolio_id}: {e}")
        return None


# ============================================================================
# COMPOSITE BENCHMARK CALCULATIONS (Target Allocation with Rebalancing)
# ============================================================================

def prepare_composite_benchmark_data(db, start_date):
    """
    Fetches and aligns prices for composite benchmark assets.
    
    Uses symbols from app_config.yaml:
    - benchmarks.benchmark_securities.symbol
    - benchmarks.benchmark_crypto.symbol
    - benchmarks.benchmark_commodities.symbol
    
    Args:
        db: Database session
        start_date: Start date for data
        
    Returns:
        pd.DataFrame: Aligned prices with columns for each asset, forward-filled
    """
    try:
        logger.debug(f"Preparing composite benchmark data from {start_date}")
        config = load_app_config()
        benchmarks = config.get('benchmarks', {})
        
        # Get symbols from config
        sec_config = benchmarks.get('benchmark_securities', {})
        crypto_config = benchmarks.get('benchmark_crypto', {})
        comm_config = benchmarks.get('benchmark_commodities', {})
        
        symbols_map = {
            'securities': sec_config.get('symbol'),
            'crypto': crypto_config.get('symbol'),
            'commodities': comm_config.get('symbol')
        }
        
        # Remove None values
        symbols_map = {k: v for k, v in symbols_map.items() if v}
        
        if len(symbols_map) < 2:
            logger.warning("Insufficient benchmark symbols configured for composite benchmark")
            return pd.DataFrame()
        
        # Fetch data for each symbol
        data_frames = []
        for asset_class, symbol in symbols_map.items():
            prices = db.query(MarketData.as_of_date, MarketData.price)\
                       .filter(MarketData.symbol == symbol, MarketData.as_of_date >= start_date)\
                       .order_by(MarketData.as_of_date)\
                       .all()
            
            if not prices:
                logger.warning(f"No price data for {symbol} ({asset_class})")
                continue
            
            # Create DataFrame for this symbol
            df = pd.DataFrame(prices, columns=['date', asset_class])
            df.set_index('date', inplace=True)
            # Convert Decimals to Float for calculation
            df[asset_class] = df[asset_class].astype(float)
            data_frames.append(df)
        
        if not data_frames:
            return pd.DataFrame()
        
        # Merge into one DataFrame (Outer Join to keep all dates)
        market_df = pd.concat(data_frames, axis=1)
        
        # Forward Fill: If Stocks don't trade on Sat/Sun, carry Friday's price forward.
        # This prevents crypto returns from being applied to "NaN" stock values.
        market_df = market_df.ffill()
        
        # Drop rows where any asset is still NaN (e.g. before an asset existed)
        # The benchmark can only start when ALL components exist.
        market_df = market_df.dropna()
        
        logger.debug(f"Composite benchmark data prepared: {len(market_df)} rows, columns: {list(market_df.columns)}")
        return market_df
        
    except Exception as e:
        logger.error(f"Failed to prepare composite benchmark data: {e}")
        return pd.DataFrame()


def normalize_target_allocations(allocations):
    """
    Normalize target allocations to sum to 1.0.
    
    If allocations don't sum to 1.0 (with small tolerance), they are normalized
    proportionally. This handles user input errors gracefully.
    
    Args:
        allocations: dict mapping asset class to target weight
    
    Returns:
        dict: Normalized allocations that sum to 1.0
    """
    if not allocations:
        return {}
    
    total = sum(allocations.values())
    
    # If already close to 1.0, return as-is
    if abs(total - 1.0) < 0.0001:
        return allocations
    
    # If total is zero or negative, return empty (invalid)
    if total <= 0:
        logger.error(f"Invalid target allocations (sum={total}): {allocations}")
        return {}
    
    # Normalize proportionally
    normalized = {
        asset_class: weight / total 
        for asset_class, weight in allocations.items()
    }
    
    logger.warning(
        f"Target allocations normalized from sum={total:.4f} to 1.0: "
        f"Original={allocations}, Normalized={normalized}"
    )
    
    return normalized


def calculate_rebalanced_benchmark(market_df, target_allocations, rebalancing_period='monthly'):
    """
    Generates a synthetic NAV series (Index starting at 100) for a composite
    benchmark with periodic rebalancing to target weights.
    
    Args:
        market_df: DataFrame indexed by Date, containing columns for each asset class
                   (e.g., 'securities', 'crypto', 'commodities').
                   Prices must be forward-filled (no NaNs).
        target_allocations: dict mapping asset class to target weight
                           e.g., {'securities': 0.70, 'crypto': 0.20, 'commodities': 0.10}
                           Will be normalized if they don't sum to 1.0.
        rebalancing_period: str - 'monthly', 'quarterly', or 'yearly'
    
    Returns:
        pd.Series: The daily NAV of the benchmark, indexed by Date.
    """
    try:
        if market_df.empty:
            return pd.Series(dtype=float)
        
        # Normalize allocations to ensure they sum to 1.0
        target_allocations = normalize_target_allocations(target_allocations)
        if not target_allocations:
            logger.error("Invalid target allocations after normalization")
            return pd.Series(dtype=float)
        
        # Validation - ensure all target assets exist in dataframe
        for asset_class in target_allocations.keys():
            if asset_class not in market_df.columns:
                logger.warning(f"Asset class '{asset_class}' not found in market data")
                return pd.Series(dtype=float)
        
        # Calculate daily returns
        asset_returns = market_df.pct_change().fillna(0)
        
        # Initialization
        benchmark_nav = [100.0]  # Start Index
        
        # Current value of each "slice" of the portfolio
        # Day 0: allocate $100 according to target weights
        current_positions = {
            asset_class: 100.0 * weight 
            for asset_class, weight in target_allocations.items()
        }
        
        dates = market_df.index
        
        # Iterate day by day
        prev_date = dates[0]
        for i in range(1, len(dates)):
            current_date = dates[i]
            
            # A. Apply Daily Growth to each slice
            daily_portfolio_value = 0
            for asset_class in target_allocations.keys():
                # If asset went up 1%, the slice value grows by 1%
                r = asset_returns[asset_class].iloc[i]
                current_positions[asset_class] = current_positions[asset_class] * (1 + r)
                daily_portfolio_value += current_positions[asset_class]
            
            # B. Check for Rebalancing Trigger
            should_rebalance = False
            
            if rebalancing_period == 'monthly':
                # Rebalance on the first day of a new month
                should_rebalance = current_date.month != prev_date.month
            elif rebalancing_period == 'quarterly':
                # Rebalance at start of each quarter (Jan, Apr, Jul, Oct)
                curr_quarter = (current_date.month - 1) // 3
                prev_quarter = (prev_date.month - 1) // 3
                should_rebalance = curr_quarter != prev_quarter or current_date.year != prev_date.year
            elif rebalancing_period == 'yearly':
                # Rebalance at start of each year
                should_rebalance = current_date.year != prev_date.year
            
            if should_rebalance:
                # "Sell high, Buy low" -> Reset slices to target % of the NEW total value
                for asset_class, weight in target_allocations.items():
                    current_positions[asset_class] = daily_portfolio_value * weight
            
            # C. Record the Total NAV
            benchmark_nav.append(daily_portfolio_value)
            prev_date = current_date
        
        # Format output
        result = pd.Series(benchmark_nav, index=dates, name="Target_Composite")
        logger.debug(f"Rebalanced benchmark calculated: {len(result)} points, start value: {result.iloc[0]:.2f}, end value: {result.iloc[-1]:.2f}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to calculate rebalanced benchmark: {e}")
        return pd.Series(dtype=float)


def get_composite_benchmark_label():
    """
    Get formatted label for composite benchmark showing target allocations.
    
    Returns:
        str: Label like "Target Composite (70/20/10)"
    """
    try:
        config = load_app_config()
        benchmarks = config.get('benchmarks', {})
        allocations = benchmarks.get('target_allocations', {})
        
        if not allocations:
            return "Target Composite"
        
        # Validate allocations sum to 1.0 (with small tolerance for floating point errors)
        total = sum(allocations.values())
        if abs(total - 1.0) > 0.01:  # Allow 1% tolerance
            logger.warning(
                f"Target allocations sum to {total:.4f}, not 1.0. "
                f"Values will be normalized. Consider updating app_config.yaml to sum to 1.0 exactly."
            )
        
        # Simply return "Composite" as the label
        # The allocations are documented in the "Understanding Your Metrics" section
        return "Composite"
        
    except Exception as e:
        logger.error(f"Failed to generate composite benchmark label: {e}")
        return "Composite"

