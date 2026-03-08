"""
Pre-computation Service
Generates and stores cached portfolio data for fast dashboard retrieval.

This service runs once daily after all sync jobs complete. It pre-computes:
1. Portfolio summaries (total value, invested, P&L, KPI metrics)
2. Period statistics (3M, 6M, 1Y, etc. with TWR, volatility, Sharpe, benchmark alpha)
3. Chart data (Plotly figures as JSON)

Following the established architecture pattern:
- Service layer orchestrates the computation
- Uses existing data functions from apps/core/data.py
- Stores results in cache tables via direct database operations

See docs/BACKGROUND_PRECOMPUTATION_IMPLEMENTATION.md for details.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict
import json

from database import SessionLocal
from models import (
    Portfolio,
    Snapshot,
    PortfolioSummaryCache,
    PeriodStatisticsCache,
    ChartDataCache,
    PositionCache
)
from apps.core.data import get_portfolio_summary, calculate_period_statistics, get_portfolio_kpi_data
from apps.core.charts import (
    create_performance_chart,
    create_single_portfolio_nav_chart,
    create_normalized_performance_chart,
    create_risk_reward_scatter,
    create_position_risk_reward_scatter
)
from utils.portfolios_loader import get_portfolios_loader
from utils.logging_config import get_logger

logger = get_logger(__name__)

# Periods to pre-compute
PERIODS = ['3m', '6m', '1y', '3y', '5y', 'all']

# Chart types available (which ones are used depends on portfolio type)
# - 'performance': All portfolios (wealth trajectory tab)
# - 'growth': "All Portfolios" + investment portfolios (growth comparison tab - NAV/normalized)
# - 'risk': "All Portfolios" + investment portfolios (risk/reward scatter tab)
CHART_TYPES = ['performance', 'growth', 'risk']


def precompute_all_portfolios(force: bool = False) -> Dict:
    """
    Pre-compute all portfolio summaries, statistics, and charts.
    Run this once daily after sync jobs complete.
    
    Args:
        force: If False, skip portfolios that already have today's cache.
               If True, recompute all portfolios regardless of existing cache.
    
    Returns:
        dict: Summary of pre-computation results
    """
    db = SessionLocal()
    try:
        logger.info("=" * 60)
        logger.info(f"Starting pre-computation job (force={force})...")
        
        # OPTIMIZATION: Truncate all cache tables at start if force=True
        # This is simpler and faster than per-portfolio deletion
        # Cache is disposable - dashboard falls back to live computation if needed
        if force:
            logger.info("Clearing all cache tables (force=True)...")
            db.query(PortfolioSummaryCache).delete()
            db.query(PeriodStatisticsCache).delete()
            db.query(ChartDataCache).delete()
            db.query(PositionCache).delete()
            db.commit()
            logger.info("All cache tables cleared")
        
        results = {
            'portfolios_processed': 0,
            'portfolios_skipped': 0,
            'summaries_cached': 0,
            'period_stats_cached': 0,
            'charts_cached': 0,
            'errors': []
        }
        
        # Get all portfolios (active + closed with data) + aggregate (None for "All Portfolios")
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        
        # Filter out only placeholder portfolios (keep active AND closed with historical data)
        # Rationale: Closed portfolios may have historical snapshots and should be viewable
        cacheable_portfolios = [p for p in all_portfolios if p.get('type') != 'placeholder']
        
        # Include None for "All Portfolios" aggregate view
        portfolio_ids = [None] + [p['id'] for p in cacheable_portfolios]
        
        logger.info(f"Pre-computing data for {len(portfolio_ids)} portfolios (including 'All')")
        
        today = date.today()
        
        for portfolio_id in portfolio_ids:
            # Check if cache already exists for today (skip if not forcing)
            if not force:
                existing_cache = db.query(PortfolioSummaryCache).filter(
                    PortfolioSummaryCache.portfolio_id == portfolio_id,
                    PortfolioSummaryCache.snapshot_date == today
                ).first()
                
                if existing_cache:
                    portfolio_label = f"portfolio {portfolio_id}" if portfolio_id else "'All Portfolios'"
                    logger.debug(f"Skipping {portfolio_label} - cache exists for today")
                    results['portfolios_skipped'] += 1
                    continue
            
            try:
                portfolio_results = precompute_portfolio(db, portfolio_id)
                results['portfolios_processed'] += 1
                results['summaries_cached'] += portfolio_results.get('summary_cached', 0)
                results['period_stats_cached'] += portfolio_results.get('period_stats_cached', 0)
                results['charts_cached'] += portfolio_results.get('charts_cached', 0)
            except Exception as e:
                error_msg = f"Error pre-computing portfolio {portfolio_id}: {e}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
                continue
        
        logger.info(
            f"Pre-computation complete: "
            f"{results['portfolios_processed']} processed, "
            f"{results['portfolios_skipped']} skipped, "
            f"{results['summaries_cached']} summaries, "
            f"{results['period_stats_cached']} period stats, "
            f"{results['charts_cached']} charts cached"
        )
        if results['errors']:
            logger.warning(f"Errors encountered: {len(results['errors'])}")
        
        logger.info("=" * 60)
        return results
        
    except Exception as e:
        logger.error(f"Pre-computation job failed: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def precompute_portfolio(db, portfolio_id: Optional[int]) -> Dict:
    """
    Pre-compute data for a single portfolio.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID or None for "All Portfolios"
    
    Returns:
        dict: Results with counts of cached items
    """
    portfolio_label = f"portfolio {portfolio_id}" if portfolio_id else "'All Portfolios'"
    logger.info(f"Pre-computing {portfolio_label}...")
    
    results = {
        'summary_cached': 0,
        'period_stats_cached': 0,
        'charts_cached': 0,
        'positions_cached': 0
    }
    
    # 1. Compute and cache portfolio summary
    try:
        if precompute_summary(db, portfolio_id):
            results['summary_cached'] = 1
    except Exception as e:
        logger.warning(f"Failed to cache summary for {portfolio_label}: {e}")
    
    # 2. Compute and cache positions (with enriched prices)
    try:
        positions_count = precompute_positions(db, portfolio_id)
        results['positions_cached'] = positions_count
    except Exception as e:
        logger.warning(f"Failed to cache positions for {portfolio_label}: {e}")
    
    # 3. Compute and cache period statistics
    for period in PERIODS:
        try:
            if precompute_period_stats(db, portfolio_id, period):
                results['period_stats_cached'] += 1
        except Exception as e:
            logger.warning(f"Failed to cache {period} stats for {portfolio_label}: {e}")
    
    # 4. Compute and cache chart data
    # Determine which chart types this portfolio needs
    chart_types_for_portfolio = get_chart_types_for_portfolio(portfolio_id)
    
    for chart_type in chart_types_for_portfolio:
        for period in PERIODS:
            try:
                if precompute_chart(db, portfolio_id, chart_type, period):
                    results['charts_cached'] += 1
            except Exception as e:
                logger.warning(f"Failed to cache {chart_type} chart ({period}) for {portfolio_label}: {e}")
    
    return results


def precompute_summary(db, portfolio_id: Optional[int]) -> bool:
    """
    Pre-compute portfolio summary and KPI metrics, store in cache.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID or None for "All Portfolios"
    
    Returns:
        bool: True if cached successfully
    """
    today = date.today()
    
    # Get portfolio summary using existing function
    # NOTE: get_portfolio_summary() already handles investment vs. cash portfolio filtering:
    #   - For "All Portfolios" (portfolio_id=None): total_value includes ALL portfolios,
    #     but total_invested and PnL are from INVESTMENT portfolios only (excludes cash)
    #   - For individual portfolios: uses portfolio's own values
    summary = get_portfolio_summary(db, portfolio_id=portfolio_id)
    
    if not summary:
        logger.debug(f"No summary data available for portfolio {portfolio_id}")
        return False
    
    # Determine currency
    if summary.get('portfolio'):
        currency_base = summary['portfolio'].currency_base
    else:
        from utils.app_config import get_global_base_currency
        currency_base = get_global_base_currency()
    
    # Calculate overall return percentage
    total_invested = float(summary.get('total_invested') or 0)
    total_pnl = float(summary.get('total_pnl') or 0)
    overall_return_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    
    # Get KPI data (TWR, XIRR, MDD, years_active) - now includes aggregate
    kpi_data = None
    first_snapshot_date = None
    
    if portfolio_id is not None:
        # Individual portfolio
        kpi_data = get_portfolio_kpi_data(db, portfolio_id=portfolio_id)
        # Get first_snapshot_date for this portfolio
        first_snap = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id
        ).order_by(Snapshot.snapshot_date).first()
        first_snapshot_date = first_snap.snapshot_date if first_snap else None
    else:
        # For "All Portfolios" aggregate, calculate KPI metrics from period stats
        try:
            # Calculate period statistics for 'all' to get TWR, XIRR, MDD, etc.
            from apps.core.data import calculate_period_statistics
            all_stats = calculate_period_statistics(db, None, 'all')
            
            if all_stats:
                # Get first snapshot across all portfolios for years_active
                first_snap = db.query(Snapshot).order_by(Snapshot.snapshot_date).first()
                first_snapshot_date = first_snap.snapshot_date if first_snap else None
                
                if first_snapshot_date:
                    years_active = (today - first_snapshot_date).days / 365.25
                else:
                    years_active = 0
                
                # Use twr_annualized for the cache (this is what cards display)
                twr_value = all_stats.get('twr_annualized')
                
                kpi_data = {
                    'twr': twr_value,
                    'xirr': all_stats.get('xirr'),
                    'mdd': all_stats.get('mdd'),
                    'years_active': years_active
                }
        except Exception as e:
            logger.warning(f"Failed to calculate aggregate KPI data: {e}")
            # Get first snapshot date even if KPI calculation fails
            first_snap = db.query(Snapshot).order_by(Snapshot.snapshot_date).first()
            first_snapshot_date = first_snap.snapshot_date if first_snap else None
    
    # Create cache record
    cache = PortfolioSummaryCache(
        portfolio_id=portfolio_id,
        total_value=Decimal(str(summary.get('total_value') or 0)),
        total_invested=Decimal(str(total_invested)),
        total_pnl=Decimal(str(total_pnl)),
        realized_pnl=Decimal(str(summary.get('realized_pnl') or 0)),
        unrealized_pnl=Decimal(str(summary.get('unrealized_pnl') or 0)),
        overall_return_pct=Decimal(str(overall_return_pct)),
        currency_base=currency_base,
        twr=Decimal(str(kpi_data.get('twr') or 0)) if kpi_data else None,
        xirr=Decimal(str(kpi_data.get('xirr') or 0)) if kpi_data else None,
        mdd=Decimal(str(kpi_data.get('mdd') or 0)) if kpi_data else None,
        years_active=Decimal(str(kpi_data.get('years_active') or 0)) if kpi_data else None,
        first_snapshot_date=first_snapshot_date,
        hpr_7d=Decimal(str(summary.get('hpr_7d') or 0)),
        hpr_30d=Decimal(str(summary.get('hpr_30d') or 0)),
        hpr_365d=Decimal(str(summary.get('hpr_365d') or 0)),
        snapshot_date=today
    )
    
    db.add(cache)
    db.commit()
    
    logger.debug(f"Cached summary for portfolio {portfolio_id}")
    return True


def precompute_positions(db, portfolio_id: Optional[int]) -> int:
    """
    Pre-compute and cache position data with enriched prices.
    
    This eliminates expensive Position + MarketData + FX queries during dashboard load.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID or None for "All Portfolios"
    
    Returns:
        int: Number of positions cached
    """
    # Skip position caching for "All Portfolios" aggregate view
    if portfolio_id is None:
        logger.debug("Skipping position cache for 'All Portfolios' aggregate")
        return 0
    
    today = date.today()
    
    # Get portfolio summary which includes positions with enriched prices
    summary = get_portfolio_summary(db, portfolio_id=portfolio_id)
    
    if not summary or not summary.get('positions'):
        logger.debug(f"No positions available for portfolio {portfolio_id} - old cache cleared")
        return 0
    
    # Get portfolio currency
    if summary.get('portfolio'):
        currency_base = summary['portfolio'].currency_base
    else:
        from utils.app_config import get_global_base_currency
        currency_base = get_global_base_currency()
    
    # Cache each position
    positions_cached = 0
    for position in summary['positions']:
        try:
            # Determine if this is a cash position
            is_cash = hasattr(position, 'is_cash_position') and position.is_cash_position
            
            # Get currency for cash positions
            pos_currency = None
            if is_cash and hasattr(position, 'currency'):
                pos_currency = position.currency
            
            # Calculate value in base currency
            quantity = float(position.quantity or 0)
            current_price = float(position.current_price or 0)
            value = quantity * current_price
            
            # Determine price source (for transparency)
            price_source = 'unknown'
            if is_cash:
                price_source = 'fx_rate' if pos_currency != currency_base else 'manual'
            else:
                # Check if price came from market data or FX rate
                # This is a best-guess based on symbol type
                if position.symbol in ['EUR', 'USD', 'THB', 'GBP', 'JPY']:
                    price_source = 'fx_rate'
                else:
                    price_source = 'market_data'
            
            # Create new cache record (old entries already deleted)
            cache = PositionCache(
                portfolio_id=position.portfolio_id,
                symbol=position.symbol,
                quantity=Decimal(str(quantity)),
                current_price=Decimal(str(current_price)) if current_price else None,
                value=Decimal(str(value)) if value else None,
                price_currency=pos_currency or currency_base,
                price_source=price_source,
                is_cash_position=is_cash,
                currency=pos_currency,
                snapshot_date=today
            )
            db.add(cache)
            
            positions_cached += 1
            
        except Exception as e:
            logger.warning(f"Failed to cache position {position.symbol}: {e}")
            continue
    
    # Commit all position cache records
    try:
        db.commit()
        logger.debug(f"Cached {positions_cached} positions for portfolio {portfolio_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit position cache: {e}")
        return 0
    
    return positions_cached


def precompute_period_stats(db, portfolio_id: Optional[int], period_key: str) -> bool:
    """
    Pre-compute period statistics (including benchmark comparison) and store in cache.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID or None for "All Portfolios"
        period_key: Period key ('3m', '6m', '1y', '3y', '5y', 'all')
    
    Returns:
        bool: True if cached successfully
    """
    today = date.today()
    
    # Calculate period statistics using existing function
    stats = calculate_period_statistics(db, portfolio_id, period_key)
    
    if not stats:
        logger.debug(f"No {period_key} stats available for portfolio {portfolio_id}")
        return False
    
    # Calculate start date based on period
    # CRITICAL: 'all' must use actual portfolio history, NOT fixed 10 years
    if period_key == 'all':
        # Use actual portfolio history for correct benchmark comparison
        if portfolio_id is None:
            # All Portfolios: use earliest snapshot across all portfolios
            earliest_snap = db.query(Snapshot).order_by(Snapshot.snapshot_date).first()
            start_date = earliest_snap.snapshot_date if earliest_snap else today - timedelta(days=365)
        else:
            # Individual portfolio: use first snapshot
            first_snap = db.query(Snapshot).filter(
                Snapshot.portfolio_id == portfolio_id
            ).order_by(Snapshot.snapshot_date).first()
            start_date = first_snap.snapshot_date if first_snap else today - timedelta(days=365)
    else:
        # Fixed periods (3m, 6m, 1y, 3y, 5y)
        range_mapping = {
            '3m': 90,
            '6m': 180,
            '1y': 365,
            '3y': 3*365,
            '5y': 5*365
        }
        days_back = range_mapping.get(period_key, 365)
        start_date = today - timedelta(days=days_back)
    
    # Get benchmark data if portfolio has TWR
    benchmark_twr = None
    alpha = None
    benchmark_symbol = None
    
    twr_value = stats.get('twr_annualized')
    
    if twr_value is not None:
        try:
            if portfolio_id is not None:
                # Individual portfolio - use portfolio-specific benchmark
                from service.benchmark_service import (
                    calculate_alpha, 
                    get_benchmark_for_portfolio_type,
                    get_benchmark_twr_for_portfolio_type
                )
                
                # Get portfolio type from config
                portfolios_loader = get_portfolios_loader()
                portfolio_config = next(
                    (p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), 
                    {}
                )
                portfolio_type = portfolio_config.get('type', 'other')
                
                # Skip benchmark calculation for cash/placeholder portfolios
                if portfolio_type not in ['cash', 'placeholder', '']:
                    # Get benchmark config for symbol
                    benchmark_config = get_benchmark_for_portfolio_type(portfolio_type)
                    if benchmark_config:
                        benchmark_symbol = benchmark_config.get('symbol')
                    
                    # Calculate benchmark TWR for the period
                    benchmark_twr = get_benchmark_twr_for_portfolio_type(portfolio_type, start_date, today)
                    
                    # Calculate alpha (portfolio TWR - benchmark TWR)
                    if benchmark_twr is not None:
                        alpha = round(float(twr_value) - benchmark_twr, 2)
            else:
                # Aggregate portfolio - use composite benchmark
                from service.benchmark_service import calculate_composite_benchmark_twr
                
                benchmark_symbol = 'composite'
                benchmark_twr = calculate_composite_benchmark_twr(start_date, today)
                
                # Calculate alpha (aggregate TWR - composite benchmark TWR)
                if benchmark_twr is not None:
                    alpha = round(float(twr_value) - benchmark_twr, 2)
                    
        except Exception as e:
            logger.warning(f"Failed to calculate benchmark for portfolio {portfolio_id}: {e}")
    
    # Create cache record
    cache = PeriodStatisticsCache(
        portfolio_id=portfolio_id,
        period_key=period_key,
        value_change=Decimal(str(stats.get('value_change') or 0)),
        value_change_pct=Decimal(str(stats.get('value_change_pct') or 0)),
        invested_change=Decimal(str(stats.get('invested_change') or 0)),
        invested_change_pct=Decimal(str(stats.get('invested_change_pct') or 0)),
        pnl_change=Decimal(str(stats.get('pnl_change') or 0)),
        pnl_change_pct=Decimal(str(stats.get('pnl_change_pct') or 0)),
        twr_return=Decimal(str(stats.get('twr') or 0)) if stats.get('twr') is not None else None,
        twr_annualized=Decimal(str(stats.get('twr_annualized') or 0)) if stats.get('twr_annualized') is not None else None,
        xirr=Decimal(str(stats.get('xirr') or 0)) if stats.get('xirr') is not None else None,
        volatility=Decimal(str(stats.get('volatility') or 0)) if stats.get('volatility') is not None else None,
        sharpe_ratio=Decimal(str(stats.get('sharpe_ratio') or 0)) if stats.get('sharpe_ratio') is not None else None,
        max_drawdown=Decimal(str(stats.get('mdd') or 0)) if stats.get('mdd') is not None else None,
        benchmark_twr=Decimal(str(benchmark_twr)) if benchmark_twr is not None else None,
        alpha=Decimal(str(alpha)) if alpha is not None else None,
        benchmark_symbol=benchmark_symbol,
        start_date=start_date,
        end_date=today
    )
    
    db.add(cache)
    db.commit()
    
    logger.debug(f"Cached {period_key} stats for portfolio {portfolio_id}")
    return True


def get_chart_types_for_portfolio(portfolio_id: Optional[int]) -> List[str]:
    """
    Determine which chart types a portfolio needs.
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
    
    Returns:
        list: Chart types needed for this portfolio
    """
    if portfolio_id is None:
        # "All Portfolios" view needs all 3 chart types
        return ['performance', 'growth', 'risk']
    
    # Get portfolio type
    portfolios_loader = get_portfolios_loader()
    portfolio_config = next(
        (p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), 
        {}
    )
    portfolio_type = portfolio_config.get('type', '')
    
    # All individual portfolios need performance chart (wealth trajectory)
    chart_types = ['performance']
    
    # Investment portfolios (not cash/placeholder) also get growth comparison and risk/reward
    if portfolio_type not in ['cash', 'placeholder', '']:
        chart_types.extend(['growth', 'risk'])
    
    return chart_types


def precompute_chart(db, portfolio_id: Optional[int], chart_type: str, period_key: str) -> bool:
    """
    Pre-compute chart data and store in cache.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID or None for "All Portfolios"
        chart_type: Chart type ('performance', 'growth', 'risk')
        period_key: Period key ('3m', '6m', '1y', '3y', '5y', 'all')
    
    Returns:
        bool: True if cached successfully
    """
    try:
        # Generate chart using appropriate function
        # NOTE: create_performance_chart() already handles investment vs. cash portfolio filtering:
        #   - Market Value line: ALL portfolios (including cash)
        #   - Invested Capital line: INVESTMENT portfolios only (excludes cash)
        #   - Investment P&L line: INVESTMENT portfolios only (excludes cash)
        if chart_type == 'performance':
            # Wealth trajectory - all portfolios
            chart = create_performance_chart(db, portfolio_id, None, period_key)
        
        elif chart_type == 'growth':
            # Growth comparison - NAV/normalized charts
            if portfolio_id is None:
                # "All Portfolios" - normalized comparison of active investment portfolios only
                portfolios_loader = get_portfolios_loader()
                investment_ids = [
                    p['id'] for p in portfolios_loader.get_portfolios() 
                    if p.get('type') not in ['cash', 'placeholder'] and p.get('status') == 'active'
                ]
                if not investment_ids:
                    logger.debug("No investment portfolios for normalized chart")
                    return False
                chart = create_normalized_performance_chart(db, investment_ids, period_key)
            else:
                # Individual investment portfolio - NAV chart
                chart = create_single_portfolio_nav_chart(db, portfolio_id, period_key)
        
        elif chart_type == 'risk':
            # Risk/Reward scatter - portfolio or position comparison
            if portfolio_id is None:
                # "All Portfolios" - compare active investment portfolios only
                portfolios_loader = get_portfolios_loader()
                investment_ids = [
                    p['id'] for p in portfolios_loader.get_portfolios() 
                    if p.get('type') not in ['cash', 'placeholder'] and p.get('status') == 'active'
                ]
                if not investment_ids:
                    logger.debug("No investment portfolios for risk scatter")
                    return False
                chart = create_risk_reward_scatter(db, investment_ids, period_key)
            else:
                # Individual investment portfolio - compare positions within portfolio
                chart, _ = create_position_risk_reward_scatter(db, portfolio_id, period_key)
        
        else:
            logger.debug(f"Unknown chart type: {chart_type}")
            return False
        
        if chart is None:
            logger.debug(f"No chart data for portfolio {portfolio_id}, {chart_type}, {period_key}")
            return False
        
        # Convert Plotly figure to JSON
        chart_json = chart.to_json()
        
        # Create cache record
        cache = ChartDataCache(
            portfolio_id=portfolio_id,
            chart_type=chart_type,
            period_key=period_key,
            chart_json=chart_json
        )
        
        db.add(cache)
        db.commit()
        
        logger.debug(f"Cached {chart_type} chart ({period_key}) for portfolio {portfolio_id}")
        return True
        
    except Exception as e:
        logger.warning(f"Failed to cache {chart_type} chart: {e}")
        return False


def clear_all_cache() -> Dict:
    """
    Clear all cached data. Use for manual cache invalidation.
    
    Returns:
        dict: Counts of deleted records
    """
    db = SessionLocal()
    try:
        summary_count = db.query(PortfolioSummaryCache).delete()
        period_count = db.query(PeriodStatisticsCache).delete()
        chart_count = db.query(ChartDataCache).delete()
        db.commit()
        
        logger.info(
            f"Cache cleared: {summary_count} summaries, "
            f"{period_count} period stats, {chart_count} charts"
        )
        
        return {
            'summaries_deleted': summary_count,
            'period_stats_deleted': period_count,
            'charts_deleted': chart_count
        }
    finally:
        db.close()


def cleanup_old_cache(days_old: int = 7) -> Dict:
    """
    Remove cache entries older than specified days.
    
    Args:
        days_old: Delete cache older than this many days
    
    Returns:
        dict: Counts of deleted records
    """
    from datetime import datetime
    
    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(days=days_old)
        
        summary_count = db.query(PortfolioSummaryCache).filter(
            PortfolioSummaryCache.computed_at < cutoff
        ).delete()
        
        period_count = db.query(PeriodStatisticsCache).filter(
            PeriodStatisticsCache.computed_at < cutoff
        ).delete()
        
        chart_count = db.query(ChartDataCache).filter(
            ChartDataCache.computed_at < cutoff
        ).delete()
        
        db.commit()
        
        logger.info(
            f"Old cache cleaned (>{days_old}d): {summary_count} summaries, "
            f"{period_count} period stats, {chart_count} charts"
        )
        
        return {
            'summaries_deleted': summary_count,
            'period_stats_deleted': period_count,
            'charts_deleted': chart_count
        }
    finally:
        db.close()
