"""
Cache Retrieval Service
Fast retrieval of pre-computed portfolio data.

This service provides fast access to pre-computed data stored by
precomputation_service.py. The dashboard should try cache first,
then fall back to live computation on cache miss.

Following the established architecture pattern:
- Service layer provides retrieval interface
- Returns cached data or None on cache miss
- Caller is responsible for fallback to live computation

See docs/BACKGROUND_PRECOMPUTATION_IMPLEMENTATION.md for details.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, List
import json

from database import SessionLocal
from models import PortfolioSummaryCache, PeriodStatisticsCache, ChartDataCache, Portfolio, PositionCache
from utils.logging_config import get_logger
from utils.portfolios_loader import get_portfolios_loader

logger = get_logger(__name__)

# Default cache freshness window (in hours)
DEFAULT_CACHE_MAX_AGE_HOURS = 28  # 28 hours allows for job schedule variance


def _is_cache_recent(computed_at: Optional[datetime], max_age_hours: int = DEFAULT_CACHE_MAX_AGE_HOURS) -> bool:
    """
    Check if cache is recent enough based on computed_at timestamp.
    
    Uses actual timestamp comparison for accurate 24-hour window check,
    not date-based comparison which can fail at midnight boundaries.
    
    Args:
        computed_at: Timestamp when cache was computed
        max_age_hours: Maximum age in hours (default: 28 hours for schedule variance)
    
    Returns:
        bool: True if cache is recent enough, False otherwise
    """
    if computed_at is None:
        return False
    
    # Convert both to naive datetime for comparison (strip timezone info)
    # This avoids timezone-aware vs naive comparison errors
    now = datetime.now()
    computed_at_naive = computed_at.replace(tzinfo=None) if computed_at.tzinfo else computed_at
    
    cache_age = now - computed_at_naive
    return cache_age < timedelta(hours=max_age_hours)


def get_cached_summary(portfolio_id: Optional[int] = None, snapshot_date: Optional[date] = None) -> Optional[Dict]:
    """
    Get cached portfolio summary.
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
        snapshot_date: Date to retrieve (default: today)
    
    Returns:
        dict with cached summary data, or None on cache miss
        
    Note:
        Cache is considered valid if computed_at is less than 28 hours ago,
        using proper timestamp comparison. This prevents cache misses when
        daily job runs at 21:00 but query happens after midnight.
    """
    if snapshot_date is None:
        snapshot_date = date.today()
    
    db = SessionLocal()
    try:
        # Get the most recent cache entry for this portfolio
        cache = db.query(PortfolioSummaryCache).filter(
            PortfolioSummaryCache.portfolio_id == portfolio_id
        ).order_by(PortfolioSummaryCache.snapshot_date.desc()).first()
        
        if not cache:
            logger.debug(f"Cache miss: no summary found for portfolio {portfolio_id}")
            return None
        
        # Check if cache is recent enough (using computed_at timestamp for accurate 24-hour check)
        if not _is_cache_recent(cache.computed_at):
            cache_age_hours = (datetime.now() - cache.computed_at.replace(tzinfo=None)).total_seconds() / 3600 if cache.computed_at else float('inf')
            logger.debug(
                f"Cache miss: summary for portfolio {portfolio_id} is {cache_age_hours:.1f} hours old "
                f"(computed_at={cache.computed_at})"
            )
            return None
        
        # Get portfolio name and type
        if portfolio_id is None:
            portfolio_name = "All Portfolios"
            portfolio_type = "aggregate"
        else:
            # Try to get from Portfolio table first
            portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            if portfolio:
                portfolio_name = portfolio.name
                portfolio_type = "other"  # Default
            else:
                portfolio_name = "Unknown"
                portfolio_type = "other"
            
            # Get type from portfolios config
            try:
                portfolios_loader = get_portfolios_loader()
                portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), None)
                if portfolio_config:
                    portfolio_type = portfolio_config.get('type', 'other')
                    portfolio_name = portfolio_config.get('name', portfolio_name)  # Prefer config name
            except Exception as e:
                logger.debug(f"Could not load portfolio config: {e}")
        
        return {
            'portfolio_name': portfolio_name,
            'portfolio_type': portfolio_type,
            'total_value': float(cache.total_value),
            'total_invested': float(cache.total_invested),
            'total_pnl': float(cache.total_pnl),
            'realized_pnl': float(cache.realized_pnl or 0),
            'unrealized_pnl': float(cache.unrealized_pnl or 0),
            'overall_return_pct': float(cache.overall_return_pct),
            'pnl_percentage': float(cache.overall_return_pct),  # Alias for compatibility
            'currency_base': cache.currency_base,
            'twr': float(cache.twr) if cache.twr is not None else None,
            'xirr': float(cache.xirr) if cache.xirr is not None else None,
            'mdd': float(cache.mdd) if cache.mdd is not None else None,
            'years_active': float(cache.years_active) if cache.years_active is not None else None,
            'first_snapshot_date': cache.first_snapshot_date,  # Date of first snapshot for correct alpha calculation
            'hpr_7d': float(cache.hpr_7d or 0),
            'hpr_30d': float(cache.hpr_30d or 0),
            'hpr_365d': float(cache.hpr_365d or 0),
            'cached': True,
            'cached_at': cache.computed_at.isoformat() if cache.computed_at else None
        }
        
    except Exception as e:
        logger.warning(f"Error retrieving cached summary: {e}")
        return None
    finally:
        db.close()


def get_cached_period_stats(portfolio_id: Optional[int] = None, period_key: str = '1y') -> Optional[Dict]:
    """
    Get cached period statistics.
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
        period_key: Period key ('3m', '6m', '1y', '3y', '5y', 'all')
    
    Returns:
        dict with cached period stats, or None on cache miss
        
    Note:
        Cache is considered valid if computed_at is less than 28 hours ago.
    """
    db = SessionLocal()
    try:
        cache = db.query(PeriodStatisticsCache).filter(
            PeriodStatisticsCache.portfolio_id == portfolio_id,
            PeriodStatisticsCache.period_key == period_key
        ).order_by(PeriodStatisticsCache.end_date.desc()).first()
        
        if not cache:
            logger.debug(f"Cache miss: {period_key} stats for portfolio {portfolio_id}")
            return None
        
        # Check if cache is recent enough (using computed_at timestamp)
        if not _is_cache_recent(cache.computed_at):
            cache_age_hours = (datetime.now() - cache.computed_at.replace(tzinfo=None)).total_seconds() / 3600 if cache.computed_at else float('inf')
            logger.debug(
                f"Cache miss: {period_key} stats for portfolio {portfolio_id} is {cache_age_hours:.1f} hours old "
                f"(computed_at={cache.computed_at})"
            )
            return None
        
        return {
            'value_change': float(cache.value_change),
            'value_change_pct': float(cache.value_change_pct),
            'invested_change': float(cache.invested_change),
            'invested_change_pct': float(cache.invested_change_pct),
            'pnl_change': float(cache.pnl_change),
            'pnl_change_pct': float(cache.pnl_change_pct),
            'twr': float(cache.twr_return) if cache.twr_return is not None else None,
            'twr_annualized': float(cache.twr_annualized) if cache.twr_annualized is not None else None,
            'xirr': float(cache.xirr) if cache.xirr is not None else None,
            'volatility': float(cache.volatility) if cache.volatility is not None else None,
            'sharpe_ratio': float(cache.sharpe_ratio) if cache.sharpe_ratio is not None else None,
            'mdd': float(cache.max_drawdown) if cache.max_drawdown is not None else None,
            'benchmark_twr': float(cache.benchmark_twr) if cache.benchmark_twr is not None else None,
            'alpha': float(cache.alpha) if cache.alpha is not None else None,
            'benchmark_symbol': cache.benchmark_symbol,
            'start_date': cache.start_date,
            'end_date': cache.end_date,
            'cached': True,
            'cached_at': cache.computed_at.isoformat() if cache.computed_at else None
        }
        
    except Exception as e:
        logger.warning(f"Error retrieving cached period stats: {e}")
        return None
    finally:
        db.close()


def get_cached_chart(portfolio_id: Optional[int] = None, chart_type: str = 'performance', period_key: str = '1y') -> Optional[Dict]:
    """
    Get cached chart data as a dict.
    
    Returns raw dict (not go.Figure) for performance. The caller can convert
    to go.Figure if needed for Plotly rendering.
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
        chart_type: Chart type:
            - 'performance': Wealth trajectory (all portfolios)
            - 'growth': Growth comparison NAV/normalized (All Portfolios + investment portfolios)
            - 'risk': Risk/reward scatter (All Portfolios + investment portfolios)
        period_key: Period key ('3m', '6m', '1y', '3y', '5y', 'all')
    
    Returns:
        dict: Plotly figure data as dict, or None on cache miss
              Can be passed to go.Figure(data) to reconstruct.
    """
    db = SessionLocal()
    try:
        cache = db.query(ChartDataCache).filter(
            ChartDataCache.portfolio_id == portfolio_id,
            ChartDataCache.chart_type == chart_type,
            ChartDataCache.period_key == period_key
        ).first()
        
        if not cache:
            logger.debug(f"Cache miss: {chart_type} chart ({period_key}) for portfolio {portfolio_id}")
            return None
        
        # Check if cache is recent enough (using computed_at timestamp)
        if not _is_cache_recent(cache.computed_at):
            cache_age_hours = (datetime.now() - cache.computed_at.replace(tzinfo=None)).total_seconds() / 3600 if cache.computed_at else float('inf')
            logger.debug(
                f"Cache miss: {chart_type} chart ({period_key}) for portfolio {portfolio_id} is {cache_age_hours:.1f} hours old "
                f"(computed_at={cache.computed_at})"
            )
            return None
        
        # Return raw dict - caller can convert to go.Figure if needed
        chart_data = json.loads(cache.chart_json)
        chart_data['cached'] = True
        chart_data['cached_at'] = cache.computed_at.isoformat() if cache.computed_at else None
        return chart_data
        
    except Exception as e:
        logger.warning(f"Error retrieving cached chart: {e}")
        return None
    finally:
        db.close()


def is_cache_fresh(portfolio_id: Optional[int] = None, max_age_hours: int = 24) -> bool:
    """
    Check if cache is fresh (computed within max_age_hours).
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
        max_age_hours: Maximum cache age in hours
    
    Returns:
        bool: True if cache exists and is fresh
    """
    from datetime import datetime, timedelta
    
    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        cache = db.query(PortfolioSummaryCache).filter(
            PortfolioSummaryCache.portfolio_id == portfolio_id,
            PortfolioSummaryCache.computed_at >= cutoff
        ).first()
        
        return cache is not None
        
    except Exception as e:
        logger.warning(f"Error checking cache freshness: {e}")
        return False
    finally:
        db.close()


def get_cache_stats() -> Dict:
    """
    Get statistics about the cache.
    
    Returns:
        dict: Cache statistics
    """
    db = SessionLocal()
    try:
        from sqlalchemy import func
        
        summary_count = db.query(func.count(PortfolioSummaryCache.id)).scalar() or 0
        period_count = db.query(func.count(PeriodStatisticsCache.id)).scalar() or 0
        chart_count = db.query(func.count(ChartDataCache.id)).scalar() or 0
        
        # Get latest cache time
        latest_summary = db.query(func.max(PortfolioSummaryCache.computed_at)).scalar()
        latest_period = db.query(func.max(PeriodStatisticsCache.computed_at)).scalar()
        latest_chart = db.query(func.max(ChartDataCache.computed_at)).scalar()
        
        # Determine overall latest
        latest_times = [t for t in [latest_summary, latest_period, latest_chart] if t is not None]
        latest_overall = max(latest_times) if latest_times else None
        
        return {
            'summary_entries': summary_count,
            'period_stats_entries': period_count,
            'chart_entries': chart_count,
            'total_entries': summary_count + period_count + chart_count,
            'latest_summary_at': latest_summary.isoformat() if latest_summary else None,
            'latest_period_at': latest_period.isoformat() if latest_period else None,
            'latest_chart_at': latest_chart.isoformat() if latest_chart else None,
            'latest_overall': latest_overall.isoformat() if latest_overall else None
        }
        
    except Exception as e:
        logger.warning(f"Error getting cache stats: {e}")
        return {'error': str(e)}
    finally:
        db.close()

def get_cached_positions(portfolio_id: Optional[int] = None, snapshot_date: Optional[date] = None) -> Optional[List[Dict]]:
    """
    Get cached positions with enriched prices.
    
    Args:
        portfolio_id: Portfolio ID or None for "All Portfolios"
        snapshot_date: Date to retrieve (default: today)
    
    Returns:
        list of dicts with position data, or None on cache miss
        
    Note:
        Cache is considered valid if it's less than 24 hours old,
        not just same calendar date.
    """
    if snapshot_date is None:
        snapshot_date = date.today()
    
    db = SessionLocal()
    try:
        if portfolio_id is None:
            # All Portfolios - get positions from all active portfolios
            portfolios_loader = get_portfolios_loader()
            all_portfolios = portfolios_loader.get_portfolios()
            portfolio_ids = [p['id'] for p in all_portfolios if p.get('status') != 'reserved']
            
            # Get most recent positions (order by date descending)
            cached_positions = db.query(PositionCache).filter(
                PositionCache.portfolio_id.in_(portfolio_ids)
            ).order_by(PositionCache.snapshot_date.desc()).all()
        else:
            # Single portfolio - get most recent positions
            cached_positions = db.query(PositionCache).filter(
                PositionCache.portfolio_id == portfolio_id
            ).order_by(PositionCache.snapshot_date.desc()).all()
        
        if not cached_positions:
            logger.debug(f"Cache miss: no positions found for portfolio {portfolio_id}")
            return None
        
        # Check if cache is recent enough (using computed_at timestamp for accurate check)
        first_position = cached_positions[0]
        if not _is_cache_recent(first_position.computed_at):
            cache_age_hours = (datetime.now() - first_position.computed_at.replace(tzinfo=None)).total_seconds() / 3600 if first_position.computed_at else float('inf')
            logger.debug(
                f"Cache miss: positions for portfolio {portfolio_id} are {cache_age_hours:.1f} hours old "
                f"(computed_at={first_position.computed_at})"
            )
            return None
        
        # Convert to dict format matching Position objects for compatibility
        positions_list = []
        for cp in cached_positions:
            positions_list.append({
                'portfolio_id': cp.portfolio_id,
                'symbol': cp.symbol,
                'quantity': float(cp.quantity),
                'current_price': float(cp.current_price) if cp.current_price else None,
                'value': float(cp.value) if cp.value else None,
                'price_currency': cp.price_currency,
                'price_source': cp.price_source,
                'is_cash_position': cp.is_cash_position,
                'currency': cp.currency,
                'cached': True,
                'cached_at': cp.computed_at.isoformat() if cp.computed_at else None
            })
        
        return positions_list
        
    except Exception as e:
        logger.warning(f"Error retrieving cached positions: {e}")
        return None
    finally:
        db.close()