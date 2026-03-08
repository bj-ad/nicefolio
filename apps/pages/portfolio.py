"""
Portfolio Dashboard Page - Main portfolio tracking and analysis interface

This page provides comprehensive portfolio monitoring including:
- Total wealth summary and KPI cards (All Portfolios view)
- Performance charts with multiple date ranges
- Portfolio allocation visualizations
- Position tracking
- System diagnostics

This is the default landing page (/) as well as /portfolio.

PERFORMANCE METRICS:
TWR (Time-Weighted Return) calculations use the NAV-based method consistently:
- Formula: TWR = (End_NAV / Start_NAV - 1) × 100
- This is the industry standard (mutual funds, ETFs)
- NAV already accounts for cash flows through unit adjustments
- Simple, accurate, and mathematically equivalent to geometric linking
"""

import textwrap
from typing import Optional
from nicegui import ui, app
from sqlalchemy import func, and_, desc

from database import SessionLocal
from models import (
    Snapshot, 
    Portfolio, 
    Position, 
    FxRate, 
    MarketData, 
    Transaction,
    PortfolioSummaryCache,
    PeriodStatisticsCache
)
from utils.portfolios_loader import get_portfolios_loader
from utils.app_config import get_global_base_currency
from utils.logging_config import get_logger
from apps.core.layout import page_layout, format_currency, format_percentage
from apps.core.data import (
    get_portfolio_summary, 
    calculate_period_statistics,
    get_portfolio_kpi_data
)
from apps.core.charts import (
    create_performance_chart,
    create_allocation_chart,
    create_portfolio_allocation_chart,
    create_portfolio_invested_capital_allocation_chart,
    create_normalized_performance_chart,
    create_risk_reward_scatter,
    create_single_portfolio_nav_chart,
    create_position_risk_reward_scatter
)
from apps.core.helpers import calculate_max_drawdown
from service.portfolio_service import calculate_portfolio_return
from service.cache_service import (
    get_cached_summary,
    get_cached_period_stats,
    get_cached_chart,
    is_cache_fresh,
    get_cached_positions
)

logger = get_logger(__name__)


# ============================================================================
# Portfolio Styling Constants
# ============================================================================

# Color mapping by portfolio type (borders for cards)
PORTFOLIO_TYPE_BORDERS = {
    'securities': 'border-emerald-600',
    'commodities': 'border-amber-500',
    'crypto': 'border-indigo-600',
    'cash': 'border-blue-600',
    'other': 'border-slate-600'
}

# Icon mapping by portfolio type (icon_name, icon_color_class)
PORTFOLIO_TYPE_ICONS = {
    'securities': ('trending_up', 'text-emerald-600'),
    'commodities': ('diamond', 'text-amber-500'),
    'crypto': ('currency_bitcoin', 'text-indigo-600'),
    'other': ('pie_chart', 'text-slate-600')
}


# Helper function to get position value (works with both dict and object)
def get_pos_value(pos, key, default=None):
    """Get value from position, handling both dict (from cache) and object (from live query) formats."""
    if isinstance(pos, dict):
        return pos.get(key, default)
    return getattr(pos, key, default)


def _get_summary_with_cache(db, portfolio_id: Optional[int] = None):
    """
    Get portfolio summary from cache first, fallback to live computation.
    Returns the full summary dict with positions list.
    """
    # Try cache first for summary data
    cached_summary = get_cached_summary(portfolio_id)
    
    if cached_summary:
        logger.debug(f"Using cached summary for portfolio {portfolio_id}")
        
        # Get positions from cache
        cached_positions = get_cached_positions(portfolio_id)
        
        # Get portfolio object
        if portfolio_id is not None:
            # Single portfolio - get from database
            portfolio_obj = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        else:
            # All Portfolios - create virtual portfolio with global base currency
            from collections import namedtuple
            PortfolioView = namedtuple('PortfolioView', ['id', 'name', 'currency_base'])
            portfolio_obj = PortfolioView(id=None, name='All Portfolios', currency_base=get_global_base_currency())
        
        # Convert cache format to summary format
        summary = {
            'portfolio': portfolio_obj,
            'total_value': cached_summary.get('total_value', 0),
            'total_invested': cached_summary.get('total_invested', 0),
            'total_pnl': cached_summary.get('total_pnl', 0),
            'realized_pnl': cached_summary.get('realized_pnl', 0),
            'unrealized_pnl': cached_summary.get('unrealized_pnl', 0),
            'pnl_percentage': cached_summary.get('overall_return_pct', 0),
            'twr': cached_summary.get('twr'),
            'xirr': cached_summary.get('xirr'),
            'mdd': cached_summary.get('mdd'),
            'positions': cached_positions or [],
            'hpr_7d': cached_summary.get('hpr_7d'),
            'hpr_30d': cached_summary.get('hpr_30d'),
            'hpr_365d': cached_summary.get('hpr_365d'),
            'cached': True
        }
        return summary
    
    # Fallback to live computation
    logger.warning(f"Cache miss, computing live summary for portfolio {portfolio_id} (this may be slow)")
    summary = get_portfolio_summary(db, portfolio_id=portfolio_id)
    if summary:
        summary['cached'] = False
    return summary


def _get_kpi_data_with_cache(db, portfolio_id: int):
    """Get KPI data from cache, fallback to computation"""
    # Try cache first
    cached_summary = get_cached_summary(portfolio_id)
    if cached_summary:
        logger.debug(f"Using cached KPI data for portfolio {portfolio_id}")
        
        # Get portfolio type from config (not stored in cache)
        from utils.portfolios_loader import get_portfolios_loader
        portfolios_loader = get_portfolios_loader()
        portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), {})
        portfolio_type = portfolio_config.get('type', 'other')
        
        return {
            'id': portfolio_id,
            'name': cached_summary.get('portfolio_name', 'Unknown'),
            'type': portfolio_type,  # Use type from config, not cache
            'currency': cached_summary.get('currency_base', 'USD'),
            'total_value': cached_summary.get('total_value', 0),
            'total_invested': cached_summary.get('total_invested', 0),
            'total_pnl': cached_summary.get('total_pnl', 0),
            'twr': cached_summary.get('twr'),
            'xirr': cached_summary.get('xirr'),
            'mdd': cached_summary.get('mdd'),
            'first_snapshot_date': cached_summary.get('first_snapshot_date'),
            'cached': True
        }
    
    # Fallback to computation
    logger.debug(f"Cache miss, computing KPI data for portfolio {portfolio_id}")
    kpi = get_portfolio_kpi_data(db, portfolio_id)
    if kpi:
        kpi['cached'] = False
    return kpi


def _get_period_stats_with_cache(db, portfolio_id, date_range):
    """Get period statistics from cache, fallback to computation"""
    # Try cache first
    cached_stats = get_cached_period_stats(portfolio_id, date_range)
    if cached_stats:
        logger.debug(f"Using cached period stats for portfolio {portfolio_id}, period {date_range}")
        return cached_stats
    
    # Fallback to computation
    logger.debug(f"Cache miss, computing period stats for portfolio {portfolio_id}, period {date_range}")
    return calculate_period_statistics(db, portfolio_id, date_range)


def _get_chart_with_cache(db, portfolio_id, chart_type, currency_base, date_range):
    """Get chart from cache, fallback to generation"""
    # Map chart_type to cache keys
    chart_type_map = {
        'performance': 'performance',
        'growth': 'growth',
        'risk': 'risk'
    }
    
    cache_chart_type = chart_type_map.get(chart_type)
    if not cache_chart_type:
        logger.warning(f"Unknown chart type: {chart_type}")
        return None
    
    # Try cache first
    cached_chart = get_cached_chart(portfolio_id, cache_chart_type, date_range)
    if cached_chart:
        logger.debug(f"Using cached {chart_type} chart for portfolio {portfolio_id}, period {date_range}")
        # Convert dict back to go.Figure for ui.plotly()
        import plotly.graph_objects as go
        return go.Figure(cached_chart)
    
    # Fallback to generation
    logger.debug(f"Cache miss, generating {chart_type} chart for portfolio {portfolio_id}, period {date_range}")
    
    if chart_type == 'performance':
        return create_performance_chart(db, portfolio_id, currency_base, date_range)
    elif chart_type == 'growth':
        if portfolio_id is None:
            # All Portfolios: normalized chart (including closed portfolios with historical data)
            portfolios_loader = get_portfolios_loader()
            investment_ids = [p['id'] for p in portfolios_loader.get_portfolios() 
                             if p.get('type') not in ['cash', 'placeholder']]
            return create_normalized_performance_chart(db, investment_ids, date_range)
        else:
            # Individual portfolio: NAV chart
            return create_single_portfolio_nav_chart(db, portfolio_id, date_range)
    elif chart_type == 'risk':
        if portfolio_id is None:
            # All Portfolios: portfolio risk/reward scatter (including closed portfolios)
            portfolios_loader = get_portfolios_loader()
            investment_ids = [p['id'] for p in portfolios_loader.get_portfolios() 
                             if p.get('type') not in ['cash', 'placeholder']]
            return create_risk_reward_scatter(db, investment_ids, date_range)
        else:
            # Individual portfolio: position risk/reward scatter
            return create_position_risk_reward_scatter(db, portfolio_id, date_range)
    
    return None


def _clear_loading_states():
    """Clear all loading state flags to trigger fresh load"""
    loading_keys = [
        'dashboard_loaded', 'wealth_loaded', 'charts_loaded', 
        'allocation_loaded', 'comparison_loaded', 'position_alloc_loaded',
        'positions_loaded', 'diagnostics_loaded'
    ]
    for key in loading_keys:
        if key in app.storage.user:
            del app.storage.user[key]


def _portfolio_dashboard_content():
    """
    Main portfolio dashboard content - Progressive Rendering Pattern
    
    PHASE 1 (INSTANT): Render top wealth card using ONLY cached data (~10ms)
    PHASE 2 (DEFERRED): Load heavy sections asynchronously via ui.timer
    
    This ensures the user sees the main wealth summary immediately while
    charts and tables load progressively in the background.
    """
    
    # Check if we need to scroll to chart after page load
    if app.storage.user.get('scroll_to_chart', False):
        app.storage.user['scroll_to_chart'] = False
        ui.run_javascript('''
            setTimeout(() => {
                const anchor = document.getElementById("performance-chart-anchor");
                if (anchor) {
                    anchor.scrollIntoView({ behavior: "instant", block: "start" });
                    window.scrollBy(0, -80);
                }
            }, 100);
        ''')
    
    # State management for selected portfolio
    # Reset logic is now handled in the page functions (before header rendering)
    # to ensure the dropdown selector syncs properly with the dashboard view
    selected_portfolio_id = app.storage.user.get('selected_portfolio_id', None)  # None = All portfolios
    
    # Get portfolios loader (used throughout this function)
    portfolios_loader = get_portfolios_loader()
    
    # =========================================================================
    # PHASE 1: INSTANT RENDER (Cached Data Only - ~10ms)
    # =========================================================================
    
    # Try to get cached summary first (fast database read)
    cached_summary = get_cached_summary(selected_portfolio_id)
    
    if cached_summary:
        # Render the top wealth card IMMEDIATELY from cache (~10ms)
        _render_wealth_card_from_cache(cached_summary, selected_portfolio_id, portfolios_loader)
    else:
        # Cache miss - show loading state temporarily, then load and render
        wealth_container = ui.column().classes('w-full')
        with wealth_container:
            with ui.card().classes('w-full p-12').style('min-height: 200px; background: linear-gradient(135deg, #1e293b 0%, #334155 100%)'):
                with ui.column().classes('items-center justify-center w-full'):
                    ui.spinner('dots', size='xl').classes('text-white')
                    ui.label('Loading portfolio summary...').classes('text-white text-sm mt-4 opacity-80')
        
        # Load data and re-render
        async def load_wealth_card():
            try:
                db = SessionLocal()
                try:
                    # Use cache-first approach
                    summary = _get_summary_with_cache(db, selected_portfolio_id)
                    if summary:
                        wealth_container.clear()
                        with wealth_container:
                            # Convert summary to cache-compatible format
                            cache_format = {
                                'total_value': summary.get('total_value', 0),
                                'total_invested': summary.get('total_invested', 0),
                                'total_pnl': summary.get('total_pnl', 0),
                                'overall_return_pct': summary.get('pnl_percentage', 0),
                                'currency_base': summary['portfolio'].currency_base if summary.get('portfolio') else 'EUR',
                                'twr': summary.get('twr'),
                                'xirr': summary.get('xirr'),
                                'mdd': summary.get('mdd')
                            }
                            _render_wealth_card_from_cache(cache_format, selected_portfolio_id, portfolios_loader)
                finally:
                    db.close()
            except RuntimeError as e:
                if 'parent slot' in str(e).lower():
                    logger.debug('Wealth card timer cancelled - user navigated away')
                else:
                    logger.error(f"Error loading wealth card: {e}")
            except Exception as e:
                logger.error(f"Error loading wealth card: {e}")
                try:
                    wealth_container.clear()
                    with wealth_container:
                        with ui.card().classes('w-full p-6 bg-red-50'):
                            ui.label(f'Error loading summary: {str(e)}').classes('text-red-600')
                except RuntimeError:
                    pass
        
        # Use active flag to prevent timer from executing on destroyed context
        wealth_active = {'value': True}
        
        async def safe_load_wealth_card():
            if not wealth_active['value']:
                return
            await load_wealth_card()
        
        ui.timer(0, safe_load_wealth_card, once=True)
        ui.context.client.on_disconnect(lambda: wealth_active.__setitem__('value', False))
    
    # =========================================================================
    # PHASE 2: DEFERRED RENDER (Heavy Computation via Async)
    # Create containers for heavy content sections
    # =========================================================================
    
    # Container for KPI cards (below the main wealth card)
    kpi_container = ui.column().classes('w-full')
    with kpi_container:
        # Skeleton loader - shown until data loads
        # Calculate expected number of cards dynamically
        if selected_portfolio_id is None:
            # All Portfolios view: count investment portfolios + 1 for Cash & Equivalents
            investment_count = len([p for p in portfolios_loader.get_portfolios() 
                                   if p.get('status') == 'active' and p.get('type') not in ['cash', 'placeholder']])
            cash_count = 1 if any(p.get('status') == 'active' and p.get('type') == 'cash' 
                                 for p in portfolios_loader.get_portfolios()) else 0
            skeleton_count = investment_count + cash_count
        else:
            # Individual portfolio view: only 1 Performance Metrics card
            skeleton_count = 1
        
        with ui.row().classes('w-full gap-3 flex-wrap'):
            for _ in range(skeleton_count):
                with ui.card().classes('flex-1 min-w-[250px] p-4 bg-slate-50 animate-pulse'):
                    with ui.column().classes('gap-2'):
                        ui.label('').classes('h-4 w-32 bg-slate-200 rounded')
                        ui.label('').classes('h-8 w-40 bg-slate-200 rounded mt-2')
                        ui.label('').classes('h-3 w-28 bg-slate-200 rounded mt-2')
                        with ui.row().classes('gap-3 mt-2'):
                            for _ in range(3):
                                with ui.column().classes('gap-1'):
                                    ui.label('').classes('h-2 w-12 bg-slate-200 rounded')
                                    ui.label('').classes('h-4 w-16 bg-slate-200 rounded')
    
    # Container for charts section
    charts_container = ui.column().classes('w-full mt-4')
    with charts_container:
        # Skeleton loader for charts
        with ui.card().classes('w-full p-6 animate-pulse'):
            ui.label('').classes('h-6 w-48 bg-slate-200 rounded mb-4')
            ui.label('').classes('h-64 w-full bg-slate-100 rounded')
    
    # Container for allocation/comparison charts (All Portfolios view)
    # or position allocation (individual portfolio view)
    allocation_container = ui.column().classes('w-full mt-4')
    with allocation_container:
        # Skeleton loader for allocation
        with ui.card().classes('w-full p-6 animate-pulse'):
            ui.label('').classes('h-6 w-40 bg-slate-200 rounded mb-4')
            ui.label('').classes('h-48 w-full bg-slate-100 rounded')
    
    # Container for positions table
    positions_container = ui.column().classes('w-full mt-4')
    with positions_container:
        # Skeleton loader for positions table
        with ui.card().classes('w-full p-6 animate-pulse'):
            ui.label('').classes('h-6 w-32 bg-slate-200 rounded mb-4')
            for _ in range(5):
                ui.label('').classes('h-4 w-full bg-slate-100 rounded mb-2')
    
    # Container for diagnostics (All Portfolios view only)
    diagnostics_container = ui.column().classes('w-full mt-4') if selected_portfolio_id is None else None
    if diagnostics_container:
        with diagnostics_container:
            # Skeleton loader for diagnostics
            with ui.card().classes('w-full p-6 animate-pulse'):
                ui.label('').classes('h-6 w-48 bg-slate-200 rounded mb-4')
                for _ in range(3):
                    ui.label('').classes('h-4 w-full bg-slate-100 rounded mb-2')
    
    # =========================================================================
    # PHASE 2: Schedule deferred loading
    # Each section loads independently after the page is sent to the browser
    # =========================================================================
    
    async def load_kpi_cards():
        """Load KPI cards (for each investment portfolio or individual portfolio metrics)"""
        try:
            kpi_container.clear()  # Remove skeleton
            with kpi_container:
                db = SessionLocal()
                try:
                    _render_kpi_cards_section(db, selected_portfolio_id, portfolios_loader, cached_summary)
                finally:
                    db.close()
        except RuntimeError as e:
            if 'parent slot' in str(e).lower():
                # User navigated away before timer fired - this is expected on slow VMs
                logger.debug('KPI cards timer cancelled - user navigated away')
            else:
                logger.error(f"Error loading KPI cards: {e}")
        except Exception as e:
            logger.error(f"Error loading KPI cards: {e}")
            try:
                kpi_container.clear()
                with kpi_container:
                    ui.label(f'Error loading KPI cards').classes('text-red-500 text-sm')
            except RuntimeError:
                # Container was also deleted, ignore
                pass
    
    async def load_charts():
        """Load performance charts section (mostly from cache)"""
        try:
            db = SessionLocal()
            try:
                # Use cache-first approach
                summary = _get_summary_with_cache(db, selected_portfolio_id)
                if summary:
                    charts_container.clear()  # Remove skeleton
                    with charts_container:
                        _render_charts_section(db, selected_portfolio_id, summary)
            finally:
                db.close()
        except RuntimeError as e:
            if 'parent slot' in str(e).lower():
                logger.debug('Charts timer cancelled - user navigated away')
            else:
                logger.error(f"Error loading charts: {e}")
        except Exception as e:
            logger.error(f"Error loading charts: {e}")
            try:
                charts_container.clear()
                with charts_container:
                    with ui.card().classes('w-full p-6 bg-red-50'):
                        ui.label(f'Error loading charts: {str(e)}').classes('text-red-600')
            except RuntimeError:
                pass
    
    async def load_allocation():
        """Load allocation charts or position allocation (mostly from cache)"""
        try:
            db = SessionLocal()
            try:
                allocation_container.clear()  # Remove skeleton
                with allocation_container:
                    if selected_portfolio_id is None:
                        # All Portfolios view - show allocation and comparison
                        _render_allocation_charts(db)
                        _render_performance_comparison_table(db)
                    else:
                        # Individual portfolio - show position allocation
                        # Use cache-first approach
                        summary = _get_summary_with_cache(db, selected_portfolio_id)
                        if summary:
                            _render_position_allocation(summary)
            finally:
                db.close()
        except RuntimeError as e:
            if 'parent slot' in str(e).lower():
                logger.debug('Allocation timer cancelled - user navigated away')
            else:
                logger.error(f"Error loading allocation: {e}")
        except Exception as e:
            logger.error(f"Error loading allocation: {e}")
            try:
                allocation_container.clear()
                with allocation_container:
                    with ui.card().classes('w-full p-6 bg-red-50'):
                        ui.label(f'Error loading allocation: {str(e)}').classes('text-red-600')
            except RuntimeError:
                pass
    
    async def load_positions():
        """Load positions table (using cached positions)"""
        try:
            db = SessionLocal()
            try:
                # Use cache-first approach
                summary = _get_summary_with_cache(db, selected_portfolio_id)
                if summary:
                    positions_container.clear()  # Remove skeleton
                    with positions_container:
                        _render_positions_table(summary, selected_portfolio_id)
            finally:
                db.close()
        except RuntimeError as e:
            if 'parent slot' in str(e).lower():
                logger.debug('Positions timer cancelled - user navigated away')
            else:
                logger.error(f"Error loading positions: {e}")
        except Exception as e:
            logger.error(f"Error loading positions: {e}")
            try:
                positions_container.clear()
                with positions_container:
                    with ui.card().classes('w-full p-6 bg-red-50'):
                        ui.label(f'Error loading positions: {str(e)}').classes('text-red-600')
            except RuntimeError:
                pass
    
    async def load_diagnostics():
        """Load diagnostics section (All Portfolios view only)"""
        if diagnostics_container is None:
            return
        try:
            db = SessionLocal()
            try:
                # Use cache-first approach
                summary = _get_summary_with_cache(db, portfolio_id=None)
                if summary:
                    diagnostics_container.clear()  # Remove skeleton
                    with diagnostics_container:
                        _render_diagnostics_section(db, summary)
            finally:
                db.close()
        except RuntimeError as e:
            if 'parent slot' in str(e).lower():
                logger.debug('Diagnostics timer cancelled - user navigated away')
            else:
                logger.error(f"Error loading diagnostics: {e}")
        except Exception as e:
            logger.error(f"Error loading diagnostics: {e}")
            try:
                diagnostics_container.clear()
                with diagnostics_container:
                    with ui.card().classes('w-full p-6 bg-red-50'):
                        ui.label(f'Error loading diagnostics: {str(e)}').classes('text-red-600')
            except RuntimeError:
                pass
    
    # Schedule all deferred loads using ui.timer(0, ...)
    # The once=True ensures they run only once after the initial page render
    # Faster intervals (10ms stagger) since cache is fast
    # Use active flag to prevent timers from executing on destroyed contexts
    page_active = {'value': True}
    
    async def safe_load_kpi_cards():
        if not page_active['value']:
            return
        await load_kpi_cards()
    
    async def safe_load_charts():
        if not page_active['value']:
            return
        await load_charts()
    
    async def safe_load_allocation():
        if not page_active['value']:
            return
        await load_allocation()
    
    async def safe_load_positions():
        if not page_active['value']:
            return
        await load_positions()
    
    async def safe_load_diagnostics():
        if not page_active['value']:
            return
        await load_diagnostics()
    
    ui.timer(0, safe_load_kpi_cards, once=True)
    ui.timer(0.01, safe_load_charts, once=True)  # 10ms stagger
    ui.timer(0.02, safe_load_allocation, once=True)  # 20ms stagger
    ui.timer(0.03, safe_load_positions, once=True)  # 30ms stagger
    ui.timer(0.04, safe_load_diagnostics, once=True)  # 40ms stagger
    
    # Mark page as inactive when client disconnects
    ui.context.client.on_disconnect(lambda: page_active.__setitem__('value', False))


def _render_wealth_card_from_cache(cached_summary: dict, selected_portfolio_id, portfolios_loader):
    """
    Render the main wealth/value hero card using ONLY cached data.
    This is Phase 1 of progressive rendering - must be instant (~10ms).
    
    Args:
        cached_summary: Dict from get_cached_summary() 
        selected_portfolio_id: Portfolio ID or None for "All Portfolios"
        portfolios_loader: Portfolios loader instance
    """
    # Extract values from cache
    total_value = cached_summary.get('total_value', 0)
    total_invested = cached_summary.get('total_invested', 0)
    total_pnl = cached_summary.get('total_pnl', 0)
    overall_return_pct = cached_summary.get('overall_return_pct', 0)
    base_currency = cached_summary.get('currency_base', 'USD')
    
    # Fallback HPR strategy
    # For All Portfolios: use shortest common period (apples-to-apples comparison)
    # For individual portfolio: use longest available period
    hpr_365d = cached_summary.get('hpr_365d', 0)
    hpr_30d = cached_summary.get('hpr_30d', 0)
    hpr_7d = cached_summary.get('hpr_7d', 0)
    
    if selected_portfolio_id is None:
        # All Portfolios: determine shortest common period across ALL investment portfolios
        investment_portfolios = [p for p in portfolios_loader.get_portfolios() 
                               if p.get('status') == 'active' and p.get('type') not in ['cash', 'placeholder']]
        
        # Check which period all portfolios have data for
        all_have_365d = True
        all_have_30d = True
        all_have_7d = True
        
        for p_config in investment_portfolios:
            p_summary = get_cached_summary(p_config['id'])
            if p_summary:
                if p_summary.get('hpr_365d', 0) == 0:
                    all_have_365d = False
                if p_summary.get('hpr_30d', 0) == 0:
                    all_have_30d = False
                if p_summary.get('hpr_7d', 0) == 0:
                    all_have_7d = False
        
        # Use longest common period (apples-to-apples)
        if all_have_365d:
            hpr_display = hpr_365d
            hpr_period_label = '1Y'
        elif all_have_30d:
            hpr_display = hpr_30d
            hpr_period_label = '30D'
        elif all_have_7d:
            hpr_display = hpr_7d
            hpr_period_label = '7D'
        else:
            hpr_display = 0
            hpr_period_label = '1Y'
    else:
        # Individual portfolio: use longest available period
        if hpr_365d != 0:
            hpr_display = hpr_365d
            hpr_period_label = '1Y'
        elif hpr_30d != 0:
            hpr_display = hpr_30d
            hpr_period_label = '30D'
        elif hpr_7d != 0:
            hpr_display = hpr_7d
            hpr_period_label = '7D'
        else:
            hpr_display = 0
            hpr_period_label = '1Y'
    
    # Hero Card Label
    if selected_portfolio_id is None:
        hero_label = 'Total Liquid Assets'
        invested_label = 'Invested Capital'
        pnl_label = 'Investment P&L'
    else:
        hero_label = 'Market Value'
        # Check portfolio type for individual portfolio labels
        portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == selected_portfolio_id), {})
        portfolio_type = portfolio_config.get('type', 'other')
        
        if portfolio_type == 'cash':
            # For cash portfolios: rename main label to 'Balance', hide invested section, show Total P&L for FX
            hero_label = 'Balance'
            invested_label = None  # Don't show
            pnl_label = 'Total P&L'  # Show for FX gains/losses
        else:
            invested_label = 'Invested Capital'
            pnl_label = 'Total P&L'
    
    # Total Liquid Assets / Market Value Hero Card - INSTANT from cache
    with ui.card().classes('w-full p-6 mb-2').style('background: linear-gradient(135deg, #1e293b 0%, #334155 100%)'):
        with ui.row().classes('items-center justify-between w-full flex-wrap gap-4'):
            with ui.column().classes('gap-2'):
                # Header with trend indicator (1Y if available, fallback to shorter period)
                show_trend = False
                if selected_portfolio_id is None:
                    # All Portfolios view - show if we have data
                    show_trend = hpr_display != 0
                else:
                    # Individual portfolio - show HPR for both investment and cash portfolios
                    show_trend = (portfolio_type not in ['placeholder']) and hpr_display != 0
                
                if show_trend:
                    with ui.row().classes('items-center gap-2'):
                        ui.label(hero_label).classes('text-white text-sm font-semibold uppercase tracking-wide opacity-80')
                        # Trend indicator with dynamic period label
                        trend_icon = 'trending_up' if hpr_display >= 0 else 'trending_down'
                        trend_color = 'text-emerald-400' if hpr_display >= 0 else 'text-red-400'
                        with ui.row().classes('items-center gap-0.5'):
                            ui.icon(trend_icon, size='xs').classes(f'{trend_color} opacity-60')
                            ui.label(f'{hpr_display:+.1f}% ({hpr_period_label})').classes(f'{trend_color} text-[10px] opacity-60')
                else:
                    ui.label(hero_label).classes('text-white text-sm font-semibold uppercase tracking-wide opacity-80')
                
                ui.label(f'{base_currency} {total_value:,.0f}').classes('text-white text-4xl sm:text-5xl font-black')
                
                # Show investment portfolios market value under Total Liquid Assets (All Portfolios only)
                if selected_portfolio_id is None:
                    # Calculate investment portfolios total (exclude cash)
                    investment_portfolios = [p for p in portfolios_loader.get_portfolios() 
                                           if p.get('status') == 'active' and p.get('type') not in ['cash', 'placeholder']]
                    investment_total = 0
                    
                    # Determine shortest common period across all portfolios (apples-to-apples)
                    all_have_365d = True
                    all_have_30d = True
                    all_have_7d = True
                    
                    portfolio_hprs = []  # Store HPR for each portfolio
                    
                    for p_config in investment_portfolios:
                        p_summary = get_cached_summary(p_config['id'])
                        if p_summary:
                            p_value = p_summary.get('total_value', 0)
                            investment_total += p_value
                            
                            # Check which periods have data
                            p_hpr_365 = p_summary.get('hpr_365d', 0)
                            p_hpr_30 = p_summary.get('hpr_30d', 0)
                            p_hpr_7 = p_summary.get('hpr_7d', 0)
                            
                            if p_hpr_365 == 0:
                                all_have_365d = False
                            if p_hpr_30 == 0:
                                all_have_30d = False
                            if p_hpr_7 == 0:
                                all_have_7d = False
                            
                            portfolio_hprs.append({
                                'value': p_value,
                                'hpr_365d': p_hpr_365,
                                'hpr_30d': p_hpr_30,
                                'hpr_7d': p_hpr_7
                            })
                    
                    # Use longest common period for fair comparison
                    if all_have_365d:
                        period_key = 'hpr_365d'
                        period_label = '1Y'
                    elif all_have_30d:
                        period_key = 'hpr_30d'
                        period_label = '30D'
                    elif all_have_7d:
                        period_key = 'hpr_7d'
                        period_label = '7D'
                    else:
                        period_key = None
                        period_label = '1Y'
                    
                    # Calculate weighted average HPR using common period
                    weighted_hpr = 0
                    total_weight = 0
                    if period_key:
                        for p_data in portfolio_hprs:
                            p_hpr = p_data[period_key]
                            p_value = p_data['value']
                            if p_hpr != 0 and p_value > 0:
                                weighted_hpr += p_hpr * p_value
                                total_weight += p_value
                    
                    avg_hpr = (weighted_hpr / total_weight) if total_weight > 0 else 0
                    
                    with ui.column().classes('gap-0 mt-1'):
                        ui.separator().classes('my-1 opacity-20 bg-white')
                        # Market Value header with trend indicator
                        if avg_hpr != 0:
                            with ui.row().classes('items-center gap-2 mt-1'):
                                ui.label('Market Value').classes('text-white text-sm opacity-80')
                                trend_icon = 'trending_up' if avg_hpr >= 0 else 'trending_down'
                                trend_color = 'text-emerald-400' if avg_hpr >= 0 else 'text-red-400'
                                with ui.row().classes('items-center gap-0.5'):
                                    ui.icon(trend_icon, size='xs').classes(f'{trend_color} opacity-60')
                                    ui.label(f'{avg_hpr:+.1f}% ({period_label})').classes(f'{trend_color} text-[10px] opacity-60')
                        else:
                            ui.label('Market Value').classes('text-white text-sm opacity-80 mt-1')
                        ui.label(f'{base_currency} {investment_total:,.0f}').classes('text-white text-xl sm:text-2xl font-semibold opacity-100')
                
                with ui.row().classes('gap-4 sm:gap-6 mt-2 flex-wrap'):
                    # Show invested section only for non-cash portfolios
                    if invested_label:
                        with ui.column().classes('gap-0'):
                            ui.label(invested_label).classes('text-white text-xs opacity-60')
                            ui.label(f'{base_currency} {total_invested:,.0f}').classes('text-white text-lg sm:text-xl font-semibold')
                    
                    # Show Total P&L (all portfolios, but no percentage for cash)
                    with ui.column().classes('gap-0'):
                        ui.label(pnl_label).classes('text-white text-xs opacity-60')
                        pnl_color = 'text-emerald-300' if total_pnl >= 0 else 'text-red-300'
                        ui.label(f'{base_currency} {total_pnl:+,.0f}').classes(f'{pnl_color} text-lg sm:text-xl font-semibold')
                    
                    # Show Return % only for non-cash portfolios
                    if selected_portfolio_id is None or portfolio_type != 'cash':
                        with ui.column().classes('gap-0'):
                            ui.label('Return').classes('text-white text-xs opacity-60')
                            return_color = 'text-emerald-300' if overall_return_pct >= 0 else 'text-red-300'
                            ui.label(f'{overall_return_pct:+.1f}%').classes(f'{return_color} text-lg sm:text-xl font-semibold')
            
            ui.icon('account_balance', size='4rem').classes('text-white opacity-30 hidden sm:block')



def _render_kpi_cards_section(db, selected_portfolio_id, portfolios_loader, cached_summary):
    """
    Render KPI cards for investment portfolios (All Portfolios view)
    or detailed metrics for a single portfolio.
    
    This is loaded asynchronously in Phase 2.
    """
    if selected_portfolio_id is None:
        # All Portfolios View: Show KPI cards for each investment portfolio + Cash & Equivalents
        
        investment_portfolios = [
            p for p in portfolios_loader.get_portfolios() 
            if p.get('status') == 'active' and p.get('type') not in ['cash', 'placeholder']
        ]
        
        # Get cash portfolios (1, 2, 8)
        cash_portfolios = [
            p for p in portfolios_loader.get_portfolios() 
            if p.get('status') == 'active' and p.get('type') == 'cash'
        ]
        
        if investment_portfolios or cash_portfolios:
            ui.label('Portfolio Overview').classes('text-slate-700 text-sm font-bold uppercase tracking-wide mt-2 mb-1')
        
        # Determine common period for all KPI cards (apples-to-apples comparison)
        all_have_365d = True
        all_have_30d = True
        all_have_7d = True
        
        for p_config in investment_portfolios:
            p_summary = get_cached_summary(p_config['id'])
            if p_summary:
                if p_summary.get('hpr_365d', 0) == 0:
                    all_have_365d = False
                if p_summary.get('hpr_30d', 0) == 0:
                    all_have_30d = False
                if p_summary.get('hpr_7d', 0) == 0:
                    all_have_7d = False
        
        # Use longest common period
        if all_have_365d:
            common_period = '365d'
            common_label = '1Y'
        elif all_have_30d:
            common_period = '30d'
            common_label = '30D'
        elif all_have_7d:
            common_period = '7d'
            common_label = '7D'
        else:
            common_period = '365d'
            common_label = '1Y'
        
        with ui.row().classes('w-full gap-3 flex-wrap'):
            # Investment portfolio cards (Securities, Commodities, Crypto)
            for p_config in investment_portfolios:
                kpi = _get_kpi_data_with_cache(db, p_config['id'])
                if not kpi:
                    continue
                
                # Use common period for all cards (apples-to-apples)
                p_summary = get_cached_summary(p_config['id'])
                if p_summary:
                    hpr_display = p_summary.get(f'hpr_{common_period}', 0)
                    hpr_label = common_label
                else:
                    hpr_display = 0
                    hpr_label = common_label
                
                p_type = kpi.get('type', 'other')
                border_class = PORTFOLIO_TYPE_BORDERS.get(p_type, 'border-slate-600')
                icon_name, icon_class = PORTFOLIO_TYPE_ICONS.get(p_type, ('pie_chart', 'text-slate-600'))
                
                with ui.card().classes(f'flex-1 min-w-[250px] lg:w-[calc(25%-0.563rem)] lg:flex-none p-4 border-l-4 {border_class}'):
                    with ui.row().classes('items-center justify-between w-full mb-2'):
                        ui.label(kpi['name']).classes('text-slate-800 text-sm font-bold truncate')
                        ui.icon(icon_name, size='sm').classes(icon_class)
                    
                    # Market Value with trend indicator (fallback to shorter period if needed)
                    if hpr_display != 0:
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.label(f"{kpi['currency']} {kpi['total_value']:,.0f}").classes('text-xl font-bold text-slate-900')
                            trend_icon = 'trending_up' if hpr_display >= 0 else 'trending_down'
                            trend_color = 'text-emerald-600' if hpr_display >= 0 else 'text-red-600'
                            with ui.row().classes('items-center gap-0.5'):
                                ui.icon(trend_icon, size='xs').classes(f'{trend_color} opacity-70')
                                ui.label(f'{hpr_display:+.1f}% ({hpr_label})').classes(f'{trend_color} text-[10px] opacity-70')
                    else:
                        ui.label(f"{kpi['currency']} {kpi['total_value']:,.0f}").classes('text-xl font-bold text-slate-900')
                    
                    pnl_color = 'text-emerald-700' if kpi['total_pnl'] >= 0 else 'text-red-700'
                    pnl_pct = (kpi['total_pnl'] / kpi['total_invested'] * 100) if kpi['total_invested'] > 0 else 0
                    ui.label(f"{kpi['currency']} {kpi['total_pnl']:+,.0f} ({pnl_pct:+.1f}%)").classes(f'text-sm font-semibold {pnl_color}')
                    
                    with ui.row().classes('gap-3 mt-2 flex-wrap'):
                        with ui.column().classes('gap-0'):
                            ui.label('TWR').classes('text-slate-500 text-[10px] uppercase')
                            if kpi['twr'] is not None:
                                twr_color = 'text-cyan-700' if kpi['twr'] >= 0 else 'text-red-700'
                                ui.label(f"{kpi['twr']:+.1f}%").classes(f'text-sm font-semibold {twr_color}')
                            else:
                                ui.label('N/A').classes('text-sm font-semibold text-slate-400')
                        
                        with ui.column().classes('gap-0'):
                            ui.label('XIRR').classes('text-slate-500 text-[10px] uppercase')
                            if kpi['xirr'] is not None:
                                xirr_color = 'text-purple-700' if kpi['xirr'] >= 0 else 'text-red-700'
                                ui.label(f"{kpi['xirr']:+.1f}%").classes(f'text-sm font-semibold {xirr_color}')
                            else:
                                ui.label('N/A').classes('text-sm font-semibold text-slate-400')
                        
                        with ui.column().classes('gap-0'):
                            ui.label('MDD').classes('text-slate-500 text-[10px] uppercase')
                            if kpi['mdd'] is not None:
                                mdd_color = 'text-red-600' if kpi['mdd'] < -10 else 'text-orange-600' if kpi['mdd'] < 0 else 'text-slate-600'
                                ui.label(f"{kpi['mdd']:.1f}%").classes(f'text-sm font-semibold {mdd_color}')
                            else:
                                ui.label('N/A').classes('text-sm font-semibold text-slate-400')
            
            # Cash & Equivalents card (combined portfolios 1, 2, 8)
            if cash_portfolios:
                # Calculate combined cash value and weighted HPR
                total_cash_value = 0
                base_currency = cached_summary.get('currency_base', 'EUR')
                
                # Calculate weighted average HPR for all cash portfolios using common period
                weighted_cash_hpr = 0
                total_cash_weight = 0
                
                for p_config in cash_portfolios:
                    cash_kpi = _get_kpi_data_with_cache(db, p_config['id'])
                    if cash_kpi:
                        total_cash_value += cash_kpi['total_value']
                        
                        # Get HPR for this cash portfolio using common period
                        p_summary = get_cached_summary(p_config['id'])
                        if p_summary and cash_kpi['total_value'] > 0:
                            cash_hpr = p_summary.get(f'hpr_{common_period}', 0)
                            if cash_hpr != 0:
                                weighted_cash_hpr += cash_hpr * cash_kpi['total_value']
                                total_cash_weight += cash_kpi['total_value']
                
                avg_cash_hpr = (weighted_cash_hpr / total_cash_weight) if total_cash_weight > 0 else 0
                
                # Cash & Equivalents card with slate color - consistent height with investment cards
                with ui.card().classes('flex-1 min-w-[250px] lg:w-[calc(25%-0.563rem)] lg:flex-none p-4 border-l-4 border-slate-600'):
                    with ui.row().classes('items-center justify-between w-full mb-2'):
                        ui.label('Cash & Equivalents').classes('text-slate-800 text-sm font-bold truncate')
                        ui.icon('euro', size='sm').classes('text-slate-600')
                    
                    # Balance with HPR trend indicator (if available)
                    if avg_cash_hpr != 0:
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.label(f"{base_currency} {total_cash_value:,.0f}").classes('text-xl font-bold text-slate-900')
                            trend_icon = 'trending_up' if avg_cash_hpr >= 0 else 'trending_down'
                            trend_color = 'text-emerald-600' if avg_cash_hpr >= 0 else 'text-red-600'
                            with ui.row().classes('items-center gap-0.5'):
                                ui.icon(trend_icon, size='xs').classes(f'{trend_color} opacity-70')
                                ui.label(f'{avg_cash_hpr:+.1f}% ({common_label})').classes(f'{trend_color} text-[10px] opacity-70')
                    else:
                        ui.label(f"{base_currency} {total_cash_value:,.0f}").classes('text-xl font-bold text-slate-900')
                    
                    # Spacer to match P&L line height from investment cards
                    ui.label('').classes('text-sm font-semibold invisible')
                    
                    # Show breakdown of cash portfolios with aligned values
                    with ui.column().classes('gap-1 mt-2'):
                        for p_config in cash_portfolios:
                            cash_kpi = _get_kpi_data_with_cache(db, p_config['id'])
                            if cash_kpi:
                                with ui.row().classes('w-full items-center gap-2'):
                                    ui.label(f"{p_config['name']}").classes('text-xs text-slate-600 flex-1')
                                    ui.label(base_currency).classes('text-xs font-semibold text-slate-600').style('min-width: 32px; text-align: right')
                                    ui.label(f"{cash_kpi['total_value']:,.0f}").classes('text-xs font-semibold text-slate-700').style('min-width: 50px; text-align: right')
        
        # Metrics explanation (collapsible)
        with ui.expansion('Understanding Your Metrics', icon='help').classes('w-full mt-2'):
            # Generate composite benchmark description dynamically from config
            from utils.app_config import load_app_config
            config = load_app_config()
            benchmarks = config.get('benchmarks', {})
            
            # Get composite benchmark components
            target_allocations = benchmarks.get('target_allocations', {})
            rebalancing_period = benchmarks.get('rebalancing_period', 'monthly')
            
            # Get symbols and labels
            sec_config = benchmarks.get('benchmark_securities', {})
            crypto_config = benchmarks.get('benchmark_crypto', {})
            comm_config = benchmarks.get('benchmark_commodities', {})
            
            sec_symbol = sec_config.get('label', sec_config.get('symbol', 'VHVE'))
            crypto_symbol = crypto_config.get('label', crypto_config.get('symbol', 'BTC'))
            comm_symbol = comm_config.get('label', comm_config.get('symbol', '4GLD'))
            
            # Convert allocations to percentages and sort by weight (highest first)
            allocations_pct = [
                (round(target_allocations.get('securities', 0) * 100), sec_symbol, 'securities'),
                (round(target_allocations.get('crypto', 0) * 100), crypto_symbol, 'crypto'),
                (round(target_allocations.get('commodities', 0) * 100), comm_symbol, 'commodities')
            ]
            
            # Sort by percentage (highest first), then by predefined order (securities, commodities, crypto)
            order_map = {'securities': 0, 'commodities': 1, 'crypto': 2}
            allocations_pct.sort(key=lambda x: (-x[0], order_map.get(x[2], 999)))
            
            # Build allocation string (e.g., "60% VWCE / 20% 4GLD / 20% BTC")
            allocation_str = ' / '.join([f"{pct}% {symbol}" for pct, symbol, _ in allocations_pct if pct > 0])
            
            # Determine rebalancing frequency text
            rebalancing_map = {
                'monthly': ('monthly', 'on the first day of each month'),
                'quarterly': ('quarterly', 'in January, April, July, and October'),
                'yearly': ('annually', 'in January')
            }
            rebal_freq, rebal_timing = rebalancing_map.get(rebalancing_period, ('monthly', 'monthly'))
            
            composite_explanation = f"**Composite Benchmark**: Custom benchmark index with {rebal_freq} rebalancing {rebal_timing}, consisting of {allocation_str}"
            
            ui.markdown(textwrap.dedent(f'''
                **Performance Metrics:**

                - **TWR (Time-Weighted Return)**: Measures your investment skill, excluding the effect of deposits/withdrawals
                - **XIRR (Extended IRR)**: Your actual annualized return, accounting for when you added or withdrew money
                - **MDD (Maximum Drawdown)**: The worst peak-to-trough decline you experienced - shows risk exposure
                - **Alpha**: Portfolio TWR - Benchmark TWR (positive = outperforming)
                - {composite_explanation}
            '''))
    else:
        # Individual Portfolio View: Show detailed metrics card
        from service.benchmark_service import calculate_alpha, get_benchmark_for_portfolio_type
        from datetime import date, timedelta
        
        ui.label('Performance Metrics').classes('text-slate-700 text-sm font-bold uppercase tracking-wide mt-2 mb-1')
        
        kpi = _get_kpi_data_with_cache(db, selected_portfolio_id)
        if kpi:
            p_type = kpi.get('type', 'other')
            border_class = PORTFOLIO_TYPE_BORDERS.get(p_type, 'border-slate-600')
            
            try:
                start_date = kpi.get('first_snapshot_date') or date.today() - timedelta(days=365)
                end_date = date.today()
                alpha = calculate_alpha(kpi['twr'], p_type, start_date, end_date)
                
                benchmark_config = get_benchmark_for_portfolio_type(p_type)
                if benchmark_config:
                    if benchmark_config.get('is_composite'):
                        benchmark_label = f"vs {benchmark_config['label']}"
                    elif benchmark_config.get('is_blend'):
                        benchmark_label = f"vs {benchmark_config['securities'].get('label', 'SEC')}/{benchmark_config['crypto'].get('label', 'CRYPTO')}"
                    else:
                        benchmark_label = f"vs {benchmark_config.get('label', 'Benchmark')}"
                else:
                    benchmark_label = ""
            except Exception:
                alpha = None
                benchmark_label = ""
            
            # Get summary for realized/unrealized P&L
            total_invested = cached_summary.get('total_invested', 0) if cached_summary else kpi.get('total_invested', 0)
            base_currency = cached_summary.get('currency_base', 'USD') if cached_summary else kpi.get('currency', 'USD')
            realized_pnl = cached_summary.get('realized_pnl', 0) if cached_summary else 0
            unrealized_pnl = cached_summary.get('unrealized_pnl', 0) if cached_summary else 0
            
            with ui.card().classes(f'w-full p-4 border-l-4 {border_class}'):
                with ui.row().classes('w-full gap-6 flex-wrap mb-3'):
                    with ui.column().classes('gap-0'):
                        ui.label('TWR').classes('text-slate-500 text-[10px] uppercase')
                        if kpi['twr'] is not None:
                            twr_color = 'text-cyan-700' if kpi['twr'] >= 0 else 'text-red-700'
                            ui.label(f"{kpi['twr']:+.1f}%").classes(f'text-sm font-semibold {twr_color}')
                            if alpha is not None:
                                alpha_color = 'text-emerald-600' if alpha >= 0 else 'text-red-500'
                                alpha_bg = 'bg-emerald-50' if alpha >= 0 else 'bg-red-50'
                                ui.label(f"{alpha:+.1f}% {benchmark_label}").classes(f'text-[9px] {alpha_color} {alpha_bg} px-1 py-0.5 rounded mt-0.5')
                        else:
                            ui.label('N/A').classes('text-sm font-semibold text-slate-400')
                    
                    with ui.column().classes('gap-0'):
                        ui.label('XIRR').classes('text-slate-500 text-[10px] uppercase')
                        if kpi['xirr'] is not None:
                            xirr_color = 'text-purple-700' if kpi['xirr'] >= 0 else 'text-red-700'
                            ui.label(f"{kpi['xirr']:+.1f}%").classes(f'text-sm font-semibold {xirr_color}')
                        else:
                            ui.label('N/A').classes('text-sm font-semibold text-slate-400')
                    
                    with ui.column().classes('gap-0'):
                        ui.label('MDD').classes('text-slate-500 text-[10px] uppercase')
                        if kpi['mdd'] is not None:
                            mdd_color = 'text-red-600' if kpi['mdd'] < -10 else 'text-orange-600' if kpi['mdd'] < 0 else 'text-slate-600'
                            ui.label(f"{kpi['mdd']:.1f}%").classes(f'text-sm font-semibold {mdd_color}')
                        else:
                            ui.label('N/A').classes('text-sm font-semibold text-slate-400')
                
                with ui.row().classes('w-full gap-6 flex-wrap'):
                    with ui.column().classes('gap-0'):
                        ui.label('Realized P&L').classes('text-slate-500 text-[10px] uppercase')
                        realized_pct = (realized_pnl / total_invested * 100) if total_invested > 0 else 0
                        realized_color = 'text-emerald-700' if realized_pnl >= 0 else 'text-red-700'
                        ui.label(f"{base_currency} {realized_pnl:+,.0f} ({realized_pct:+.1f}%)").classes(f'text-sm font-semibold {realized_color}')
                    
                    with ui.column().classes('gap-0'):
                        ui.label('Unrealized P&L').classes('text-slate-500 text-[10px] uppercase')
                        unrealized_pct = (unrealized_pnl / total_invested * 100) if total_invested > 0 else 0
                        unrealized_color = 'text-emerald-700' if unrealized_pnl >= 0 else 'text-red-700'
                        ui.label(f"{base_currency} {unrealized_pnl:+,.0f} ({unrealized_pct:+.1f}%)").classes(f'text-sm font-semibold {unrealized_color}')


@ui.page('/')
def home_page():
    """Home page - Portfolio Dashboard as the default landing page"""
    
    # Clear loading states on fresh page load
    _clear_loading_states()
    
    # Check if we need to reset to 'All Portfolios' (BEFORE rendering header)
    from datetime import datetime, timedelta
    last_activity = app.storage.user.get('last_portfolio_activity')
    current_time = datetime.now()
    
    should_reset = False
    if last_activity is None:
        should_reset = True
        logger.debug("First visit - reset to 'All Portfolios' view")
    else:
        try:
            last_time = datetime.fromisoformat(last_activity)
            time_diff = current_time - last_time
            if time_diff > timedelta(minutes=30):
                should_reset = True
                logger.debug(f"Inactive for {time_diff.total_seconds() / 60:.1f} minutes - reset to 'All Portfolios'")
        except (ValueError, TypeError):
            should_reset = True
    
    if should_reset:
        app.storage.user['selected_portfolio_id'] = None
        app.storage.user['_portfolio_selector_value'] = None
    
    # Update last activity timestamp
    app.storage.user['last_portfolio_activity'] = current_time.isoformat()
    
    # Create refreshable content container
    @ui.refreshable
    def portfolio_content():
        _portfolio_dashboard_content()
    
    # Portfolio selector in header
    @ui.refreshable
    def header_controls():
        # Use synced value from storage to ensure selector matches page state
        selected_portfolio_id = app.storage.user.get('_portfolio_selector_value', None)
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        portfolio_options = {None: 'All Portfolios'}
        for p in all_portfolios:
            if p.get('type') != 'placeholder':
                portfolio_options[p['id']] = p['name']
        
        ui.select(
            options=portfolio_options,
            value=selected_portfolio_id,
            on_change=lambda e: handle_portfolio_change(e.value)
        ).classes('bg-white text-gray-800 min-w-[250px]').props('outlined dense')
    
    def handle_portfolio_change(portfolio_id):
        app.storage.user['selected_portfolio_id'] = portfolio_id
        app.storage.user['_portfolio_selector_value'] = portfolio_id
        _clear_loading_states()
        header_controls.refresh()  # Refresh selector to show new value
        portfolio_content.refresh()  # Partial refresh instead of full reload
    
    with page_layout('/', header_content=header_controls):
        portfolio_content()


@ui.page('/portfolio')
def portfolio_dashboard():
    """Portfolio Dashboard - Comprehensive portfolio tracking and analysis"""
    
    # Clear loading states on fresh page load
    _clear_loading_states()
    
    # Check if we need to reset to 'All Portfolios' (BEFORE rendering header)
    from datetime import datetime, timedelta
    last_activity = app.storage.user.get('last_portfolio_activity')
    current_time = datetime.now()
    
    should_reset = False
    if last_activity is None:
        should_reset = True
        logger.debug("First visit - reset to 'All Portfolios' view")
    else:
        try:
            last_time = datetime.fromisoformat(last_activity)
            time_diff = current_time - last_time
            if time_diff > timedelta(minutes=30):
                should_reset = True
                logger.debug(f"Inactive for {time_diff.total_seconds() / 60:.1f} minutes - reset to 'All Portfolios'")
        except (ValueError, TypeError):
            should_reset = True
    
    if should_reset:
        app.storage.user['selected_portfolio_id'] = None
        app.storage.user['_portfolio_selector_value'] = None
    
    # Update last activity timestamp
    app.storage.user['last_portfolio_activity'] = current_time.isoformat()
    
    # Create refreshable content container
    @ui.refreshable
    def portfolio_content():
        _portfolio_dashboard_content()
    
    # Portfolio selector in header
    @ui.refreshable
    def header_controls():
        # Use synced value from storage to ensure selector matches page state
        selected_portfolio_id = app.storage.user.get('_portfolio_selector_value', None)
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        portfolio_options = {None: 'All Portfolios'}
        for p in all_portfolios:
            if p.get('type') != 'placeholder':
                portfolio_options[p['id']] = p['name']
        
        ui.select(
            options=portfolio_options,
            value=selected_portfolio_id,
            on_change=lambda e: handle_portfolio_change(e.value)
        ).classes('bg-white text-gray-800 min-w-[250px]').props('outlined dense')
    
    def handle_portfolio_change(portfolio_id):
        app.storage.user['selected_portfolio_id'] = portfolio_id
        app.storage.user['_portfolio_selector_value'] = portfolio_id
        _clear_loading_states()
        header_controls.refresh()  # Refresh selector to show new value
        portfolio_content.refresh()  # Partial refresh instead of full reload
    
    with page_layout('/portfolio', header_content=header_controls):
        portfolio_content()


# ============================================================================
# UI Rendering Functions
# ============================================================================

def _render_charts_section(db, selected_portfolio_id, summary):
    """Render Tier 2: Tabbed Charts Section with performance analysis"""
    
    with ui.card().classes('w-full p-6'):
        # Add anchor for scroll management
        ui.html('<div id="performance-chart-anchor"></div>', sanitize=False)
        
        def update_range(new_range):
            """Update date range and refresh button styling and chart content"""
            app.storage.user['chart_date_range'] = new_range
            _render_date_range_buttons.refresh()
            _render_chart_tabs_content.refresh()
        
        with ui.row().classes('w-full items-center justify-between mb-4 flex-wrap gap-2'):
            ui.label('Performance Analysis').classes('text-xl font-bold')
            _render_date_range_buttons(update_range)
        
        # Render refreshable chart tabs content
        _render_chart_tabs_content(selected_portfolio_id)


@ui.refreshable
def _render_date_range_buttons(update_range):
    """Refreshable date range button row"""
    date_range = app.storage.user.get('chart_date_range', '1y')
    
    with ui.row().classes('gap-1 flex-wrap items-center'):
        for range_opt, label in [('3m', '3M'), ('6m', '6M'), ('1y', '1Y'), ('3y', '3Y'), ('5y', '5Y'), ('all', 'All')]:
            btn = ui.button(
                label,
                on_click=lambda r=range_opt: update_range(r)
            )
            btn.props('flat dense')
            btn.classes('bg-blue-700 text-white' if date_range == range_opt else 'bg-slate-200 text-slate-700')


@ui.refreshable
def _render_chart_tabs_content(selected_portfolio_id):
    """Refreshable chart tabs content - updates without full page reload"""
    db = SessionLocal()
    try:
        # Use cache-first approach for summary data
        summary = _get_summary_with_cache(db, selected_portfolio_id)
        if not summary:
            ui.label('No data available').classes('text-gray-400 text-center py-8')
            return
        
        date_range = app.storage.user.get('chart_date_range', '1y')
        active_chart_tab = app.storage.user.get('active_chart_tab', 'wealth')
        
        # Determine if this is an investment portfolio (not cash, not closed)
        is_investment_portfolio = False
        if selected_portfolio_id is not None:
            portfolios_loader = get_portfolios_loader()
            portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == selected_portfolio_id), {})
            portfolio_type = portfolio_config.get('type', '')
            portfolio_status = portfolio_config.get('status', 'active')
            is_investment_portfolio = (portfolio_type not in ['cash', 'placeholder'] and portfolio_status != 'closed')
        
        # Tabbed chart views
        with ui.tabs().classes('w-full').bind_value(app.storage.user, 'active_chart_tab') as chart_tabs:
            ui.tab('wealth', label='Wealth Trajectory', icon='trending_up')
            # Show Growth Comparison and Risk/Reward for investment portfolios only
            if selected_portfolio_id is None or is_investment_portfolio:
                ui.tab('growth', label='Growth Comparison', icon='compare_arrows')
                ui.tab('risk', label='Risk/Reward', icon='scatter_plot')
        
        with ui.tab_panels(chart_tabs, value=active_chart_tab).classes('w-full').bind_value(app.storage.user, 'active_chart_tab'):
            # Tab 1: Wealth Trajectory (existing chart)
            with ui.tab_panel('wealth'):
                try:
                    base_currency = summary['portfolio'].currency_base
                    chart = _get_chart_with_cache(db, selected_portfolio_id, 'performance', base_currency, date_range)
                    if chart:
                        with ui.element('div').classes('w-full h-80 sm:h-96'):
                            ui.plotly(chart).classes('w-full h-full')
                    else:
                        ui.label('Chart unavailable').classes('text-gray-400 text-center py-8')
                except Exception as e:
                    ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')
                
                # Period statistics for Wealth Trajectory
                try:
                    period_stats = _get_period_stats_with_cache(db, selected_portfolio_id, date_range)
                    if period_stats:
                        base_currency = summary['portfolio'].currency_base
                        
                        range_labels = {'3m': '3 Months', '6m': '6 Months', '1y': '1 Year', '3y': '3 Years', '5y': '5 Years', 'all': 'All Time'}
                        period_label = range_labels.get(date_range, 'Period')
                        ui.label(f'{period_label} Change').classes('text-xs font-semibold text-slate-500 mt-3 mb-1')
                        
                        with ui.row().classes('w-full gap-2 flex-wrap'):
                            # Market Value Change
                            with ui.card().classes('flex-1 min-w-[110px] p-3').style('border-left: 3px solid #5B8FB9'):
                                ui.label('Market Value').classes('text-slate-500 text-[10px] mb-0.5')
                                value_change = period_stats['value_change']
                                value_change_pct = period_stats['value_change_pct']
                                value_color = 'text-emerald-700' if value_change >= 0 else 'text-red-700'
                                ui.label(f"{base_currency} {value_change:+,.2f}").classes(f'text-base font-bold {value_color}')
                                ui.label(format_percentage(value_change_pct)).classes(f'text-xs {value_color}')
                            
                            # Invested Capital Change
                            with ui.card().classes('flex-1 min-w-[110px] p-3').style('border-left: 3px solid #8E7B9E'):
                                ui.label('Invested Capital').classes('text-slate-500 text-[10px] mb-0.5')
                                invested_change = period_stats['invested_change']
                                invested_change_pct = period_stats['invested_change_pct']
                                invested_color = 'text-purple-700' if invested_change >= 0 else 'text-orange-700'
                                ui.label(f"{base_currency} {invested_change:+,.2f}").classes(f'text-base font-bold {invested_color}')
                                ui.label(format_percentage(invested_change_pct)).classes(f'text-xs {invested_color}')
                            
                            # Investment P&L Change
                            with ui.card().classes('flex-1 min-w-[110px] p-3').style('border-left: 3px solid #6C9A8B'):
                                ui.label('Investment P&L').classes('text-slate-500 text-[10px] mb-0.5')
                                pnl_change = period_stats['pnl_change']
                                pnl_change_pct = period_stats['pnl_change_pct']
                                pnl_color = 'text-emerald-700' if pnl_change >= 0 else 'text-red-700'
                                ui.label(f"{base_currency} {pnl_change:+,.2f}").classes(f'text-base font-bold {pnl_color}')
                                ui.label(format_percentage(pnl_change_pct)).classes(f'text-xs {pnl_color}')
                
                except Exception as e:
                    logger.debug(f'Period statistics error: {str(e)}')
            
            # Tab 2: Growth Comparison
            if selected_portfolio_id is None or is_investment_portfolio:
                with ui.tab_panel('growth'):
                    try:
                        base_currency = summary['portfolio'].currency_base
                        chart = _get_chart_with_cache(db, selected_portfolio_id, 'growth', base_currency, date_range)
                        if chart:
                            with ui.element('div').classes('w-full h-80 sm:h-96'):
                                ui.plotly(chart).classes('w-full h-full')
                            
                            # Add appropriate label
                            if selected_portfolio_id is None:
                                ui.label('All portfolios rebased to 100 at period start for fair comparison regardless of size.').classes('text-gray-500 text-xs mt-2 text-center')
                            else:
                                ui.label('NAV (Net Asset Value) tracks portfolio performance normalized to 100 at inception.').classes('text-gray-500 text-xs mt-2 text-center')
                            
                            # Benchmark return explanation
                            ui.label('Benchmark returns shown in legend are annualized for the selected period.').classes('text-gray-500 text-xs text-center')
                        else:
                            ui.label('Chart unavailable').classes('text-gray-400 text-center py-8')
                    except Exception as e:
                        ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')
                    
                    # Period statistics for Growth Comparison
                    try:
                        range_labels = {'3m': '3 Months', '6m': '6 Months', '1y': '1 Year', '3y': '3 Years', '5y': '5 Years', 'all': 'All Time'}
                        period_label = range_labels.get(date_range, 'Period')
                        
                        if selected_portfolio_id is None:
                            # All Portfolios: Show cards for multiple portfolios
                            portfolios_loader = get_portfolios_loader()
                            investment_portfolios = [p for p in portfolios_loader.get_portfolios() 
                                                    if p.get('type') not in ['cash', 'placeholder']]
                            
                            ui.label(f'{period_label} - Portfolio Performance').classes('text-xs font-semibold text-slate-500 mt-3 mb-1')
                            
                            with ui.row().classes('w-full gap-2 flex-wrap'):
                                for portfolio in investment_portfolios[:3]:
                                    pid = portfolio['id']
                                    pname = portfolio['name']
                                    
                                    try:
                                        port_stats = _get_period_stats_with_cache(db, pid, date_range)
                                        if port_stats:
                                            from datetime import date, timedelta
                                            today = date.today()
                                            
                                            # CRITICAL: 'all' must use actual portfolio history
                                            if date_range == 'all':
                                                # Use first snapshot date for this portfolio
                                                first_snap = db.query(Snapshot).filter(
                                                    Snapshot.portfolio_id == pid
                                                ).order_by(Snapshot.snapshot_date).first()
                                                start_date = first_snap.snapshot_date if first_snap else today - timedelta(days=365)
                                            else:
                                                range_map = {'3m': 90, '6m': 180, '1y': 365, '3y': 1095, '5y': 1825}
                                                days = range_map.get(date_range, 365)
                                                start_date = today - timedelta(days=days)
                                            
                                            start_snap = db.query(Snapshot).filter(
                                                Snapshot.portfolio_id == pid,
                                                Snapshot.snapshot_date >= start_date
                                            ).order_by(Snapshot.snapshot_date).first()
                                            
                                            end_snap = db.query(Snapshot).filter(
                                                Snapshot.portfolio_id == pid
                                            ).order_by(Snapshot.snapshot_date.desc()).first()
                                            
                                            if start_snap and end_snap:
                                                start_nav = float(start_snap.nav_price or 100)
                                                end_nav = float(end_snap.nav_price or 100)
                                                nav_change = end_nav - start_nav
                                                nav_change_pct = (nav_change / start_nav * 100) if start_nav > 0 else 0
                                                
                                                # Get portfolio type for benchmark comparison
                                                ptype = portfolio.get('type', 'other')
                                                border_colors = {
                                                    'securities': 'border-emerald-600',
                                                    'commodities': 'border-amber-500',
                                                    'crypto': 'border-indigo-600',
                                                    'other': 'border-slate-600'
                                                }
                                                border_color = border_colors.get(ptype, 'border-slate-600')
                                                
                                                # Calculate benchmark TWR for the ACTUAL period (using snapshot dates)
                                                from service.benchmark_service import get_benchmark_twr_for_portfolio_type, get_benchmark_for_portfolio_type
                                                try:
                                                    actual_start = start_snap.snapshot_date
                                                    actual_end = end_snap.snapshot_date
                                                    benchmark_twr = get_benchmark_twr_for_portfolio_type(ptype, actual_start, actual_end)
                                                    benchmark_config = get_benchmark_for_portfolio_type(ptype)
                                                    if benchmark_config and benchmark_config.get('is_composite'):
                                                        benchmark_label = benchmark_config['label']
                                                    elif benchmark_config and benchmark_config.get('is_blend'):
                                                        benchmark_label = f"{benchmark_config['securities'].get('label', 'SEC')}/{benchmark_config['crypto'].get('label', 'CRYPTO')}"
                                                    elif benchmark_config:
                                                        benchmark_label = benchmark_config.get('label', 'Benchmark')
                                                    else:
                                                        benchmark_label = 'Benchmark'
                                                except:
                                                    benchmark_twr = None
                                                    benchmark_label = 'Benchmark'
                                                
                                                with ui.card().classes(f'flex-1 min-w-[140px] p-3').style(f'border-left: 3px solid {border_color}'):
                                                    ui.label(pname).classes('text-slate-700 text-[11px] font-semibold mb-1 truncate')
                                                    nav_color = 'text-emerald-700' if nav_change >= 0 else 'text-red-700'
                                                    ui.label(f"NAV Growth: {format_percentage(nav_change_pct)}").classes(f'text-base font-bold {nav_color}')
                                                    
                                                    if port_stats and port_stats.get('twr_annualized') is not None:
                                                        twr_ann = port_stats['twr_annualized']
                                                        twr_color = 'text-cyan-700' if twr_ann >= 0 else 'text-red-700'
                                                        ui.label(f"TWR (ann.): {format_percentage(twr_ann)}").classes(f'text-[10px] {twr_color}')
                                                        
                                                        # Show benchmark comparison
                                                        if benchmark_twr is not None:
                                                            alpha = twr_ann - benchmark_twr
                                                            alpha_color = 'text-emerald-600' if alpha >= 0 else 'text-red-500'
                                                            ui.label(f"{benchmark_label}: {benchmark_twr:+.1f}% (α: {alpha:+.1f}%)").classes(f'text-[9px] {alpha_color}')
                                                    
                                                    if port_stats and port_stats.get('xirr') is not None:
                                                        xirr_value = port_stats['xirr']
                                                        xirr_color = 'text-purple-700' if xirr_value >= 0 else 'text-red-700'
                                                        ui.label(f"XIRR: {format_percentage(xirr_value)}").classes(f'text-[10px] {xirr_color}')
                                                    
                                                    if port_stats and port_stats.get('mdd') is not None:
                                                        mdd_value = port_stats['mdd']
                                                        mdd_color = 'text-red-700' if mdd_value < -5 else 'text-orange-600'
                                                        ui.label(f"MDD: {format_percentage(mdd_value)}").classes(f'text-[10px] {mdd_color} mt-1')
                                    except Exception as e:
                                        logger.debug(f"Could not calculate stats for portfolio {pid}: {e}")
                        else:
                            # Individual Portfolio: Show single card
                            port_stats = _get_period_stats_with_cache(db, selected_portfolio_id, date_range)
                            if port_stats:
                                from datetime import date, timedelta
                                today = date.today()
                                
                                # CRITICAL: 'all' must use actual portfolio history
                                if date_range == 'all':
                                    # Use first snapshot date for this portfolio
                                    first_snap = db.query(Snapshot).filter(
                                        Snapshot.portfolio_id == selected_portfolio_id
                                    ).order_by(Snapshot.snapshot_date).first()
                                    start_date = first_snap.snapshot_date if first_snap else today - timedelta(days=365)
                                else:
                                    range_map = {'3m': 90, '6m': 180, '1y': 365, '3y': 1095, '5y': 1825}
                                    days = range_map.get(date_range, 365)
                                    start_date = today - timedelta(days=days)
                                
                                start_snap = db.query(Snapshot).filter(
                                    Snapshot.portfolio_id == selected_portfolio_id,
                                    Snapshot.snapshot_date >= start_date
                                ).order_by(Snapshot.snapshot_date).first()
                                
                                end_snap = db.query(Snapshot).filter(
                                    Snapshot.portfolio_id == selected_portfolio_id
                                ).order_by(Snapshot.snapshot_date.desc()).first()
                                
                                if start_snap and end_snap:
                                    start_nav = float(start_snap.nav_price or 100)
                                    end_nav = float(end_snap.nav_price or 100)
                                    nav_change = end_nav - start_nav
                                    nav_change_pct = (nav_change / start_nav * 100) if start_nav > 0 else 0
                                    
                                    # Get portfolio type for benchmark comparison and border color
                                    portfolio = db.query(Portfolio).filter(Portfolio.id == selected_portfolio_id).first()
                                    if portfolio:
                                        portfolios_loader = get_portfolios_loader()
                                        portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == selected_portfolio_id), {})
                                        ptype = portfolio_config.get('type', 'other')
                                        border_colors = {
                                            'securities': 'border-emerald-600',
                                            'commodities': 'border-amber-500',
                                            'crypto': 'border-indigo-600',
                                            'other': 'border-slate-600'
                                        }
                                        border_color = border_colors.get(ptype, 'border-slate-600')
                                    else:
                                        ptype = 'other'
                                        border_color = 'border-slate-600'
                                    
                                    # Calculate benchmark TWR for the ACTUAL period (using snapshot dates)
                                    from service.benchmark_service import get_benchmark_twr_for_portfolio_type, get_benchmark_for_portfolio_type
                                    try:
                                        actual_start = start_snap.snapshot_date
                                        actual_end = end_snap.snapshot_date
                                        benchmark_twr = get_benchmark_twr_for_portfolio_type(ptype, actual_start, actual_end)
                                        benchmark_config = get_benchmark_for_portfolio_type(ptype)
                                        if benchmark_config and benchmark_config.get('is_composite'):
                                            benchmark_label = benchmark_config['label']
                                        elif benchmark_config and benchmark_config.get('is_blend'):
                                            benchmark_label = f"{benchmark_config['securities'].get('label', 'SEC')}/{benchmark_config['crypto'].get('label', 'CRYPTO')}"
                                        elif benchmark_config:
                                            benchmark_label = benchmark_config.get('label', 'Benchmark')
                                        else:
                                            benchmark_label = 'Benchmark'
                                    except:
                                        benchmark_twr = None
                                        benchmark_label = 'Benchmark'
                                    
                                    ui.label(f'{period_label} - Portfolio Performance').classes('text-xs font-semibold text-slate-500 mt-3 mb-1')
                                    
                                    with ui.card().classes(f'flex-1 min-w-[140px] p-3').style(f'border-left: 3px solid {border_color}'):
                                        ui.label(summary['portfolio'].name).classes('text-slate-700 text-[11px] font-semibold mb-1 truncate')
                                        nav_color = 'text-emerald-700' if nav_change >= 0 else 'text-red-700'
                                        ui.label(f"NAV Growth: {format_percentage(nav_change_pct)}").classes(f'text-base font-bold {nav_color}')
                                        
                                        if port_stats and port_stats.get('twr_annualized') is not None:
                                            twr_ann = port_stats['twr_annualized']
                                            twr_color = 'text-cyan-700' if twr_ann >= 0 else 'text-red-700'
                                            ui.label(f"TWR (ann.): {format_percentage(twr_ann)}").classes(f'text-[10px] {twr_color}')
                                            
                                            # Show benchmark comparison
                                            if benchmark_twr is not None:
                                                alpha = twr_ann - benchmark_twr
                                                alpha_color = 'text-emerald-600' if alpha >= 0 else 'text-red-500'
                                                ui.label(f"{benchmark_label}: {benchmark_twr:+.1f}% (α: {alpha:+.1f}%)").classes(f'text-[9px] {alpha_color}')
                                        
                                        if port_stats and port_stats.get('xirr') is not None:
                                            xirr_value = port_stats['xirr']
                                            xirr_color = 'text-purple-700' if xirr_value >= 0 else 'text-red-700'
                                            ui.label(f"XIRR: {format_percentage(xirr_value)}").classes(f'text-[10px] {xirr_color}')
                                        
                                        if port_stats and port_stats.get('mdd') is not None:
                                            mdd_value = port_stats['mdd']
                                            mdd_color = 'text-red-700' if mdd_value < -5 else 'text-orange-600'
                                            ui.label(f"MDD: {format_percentage(mdd_value)}").classes(f'text-[10px] {mdd_color} mt-1')
                    except Exception as e:
                        logger.debug(f'Growth comparison statistics error: {str(e)}')
                
                # Tab 3: Risk/Reward
                if selected_portfolio_id is None or is_investment_portfolio:
                    with ui.tab_panel('risk'):
                        # Initialize position_stats for use in both chart and cards
                        position_stats = []
                        
                        try:
                            base_currency = summary['portfolio'].currency_base
                            
                            # For individual portfolios, call chart function directly to get both chart and stats
                            if selected_portfolio_id is not None:
                                chart, position_stats = create_position_risk_reward_scatter(db, selected_portfolio_id, date_range)
                            else:
                                # For All Portfolios, use cached chart
                                chart = _get_chart_with_cache(db, selected_portfolio_id, 'risk', base_currency, date_range)
                            
                            if chart:
                                with ui.element('div').classes('w-full h-80 sm:h-96'):
                                    ui.plotly(chart).classes('w-full h-full')
                                
                                # Add appropriate label
                                if selected_portfolio_id is None:
                                    ui.label('Ideal portfolios appear toward the left (lower volatility) and top (higher return).').classes('text-gray-500 text-xs mt-2 text-center')
                                else:
                                    ui.label('Ideal positions appear toward the left (lower volatility) and top (higher return). Size represents position value.').classes('text-gray-500 text-xs mt-2 text-center')
                            else:
                                ui.label('Chart unavailable').classes('text-gray-400 text-center py-8')
                        except Exception as e:
                            ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')
                        
                        # Period statistics for Risk/Reward
                        try:
                            range_labels = {'3m': '3 Months', '6m': '6 Months', '1y': '1 Year', '3y': '3 Years', '5y': '5 Years', 'all': 'All Time'}
                            period_label = range_labels.get(date_range, 'Period')
                            
                            if selected_portfolio_id is None:
                                # All Portfolios: Show cards for multiple portfolios
                                portfolios_loader = get_portfolios_loader()
                                investment_portfolios = [p for p in portfolios_loader.get_portfolios() 
                                                        if p.get('type') not in ['cash', 'placeholder']]
                                
                                ui.label(f'{period_label} - Risk/Reward Metrics').classes('text-xs font-semibold text-slate-500 mt-3 mb-1')
                                
                                with ui.row().classes('w-full gap-2 flex-wrap'):
                                    for portfolio in investment_portfolios[:3]:
                                        pid = portfolio['id']
                                        pname = portfolio['name']
                                        
                                        try:
                                            port_stats = _get_period_stats_with_cache(db, pid, date_range)
                                            if port_stats:
                                                with ui.card().classes('flex-1 min-w-[140px] p-3').style('border-left: 3px solid #4f46e5'):
                                                    ui.label(pname).classes('text-slate-700 text-[11px] font-semibold mb-1 truncate')
                                                    
                                                    # Return: Annualized TWR
                                                    if port_stats['twr_annualized'] is not None:
                                                        twr_value = port_stats['twr_annualized']
                                                        twr_color = 'text-emerald-700' if twr_value >= 0 else 'text-red-700'
                                                        ui.label(f"Return: {format_percentage(twr_value)}").classes(f'text-sm font-bold {twr_color}')
                                                    
                                                    # Volatility: Annualized (< 10%: Green, 10-30%: Yellow, > 30%: Red)
                                                    if port_stats['volatility'] is not None:
                                                        vol_value = port_stats['volatility']
                                                        if vol_value < 10:
                                                            vol_color = 'text-green-700'  # Stable
                                                        elif vol_value <= 30:
                                                            vol_color = 'text-yellow-600'  # Moderate
                                                        else:
                                                            vol_color = 'text-red-700'  # Volatile
                                                        ui.label(f"Volatility: {format_percentage(vol_value)}").classes(f'text-[10px] {vol_color}')
                                                    
                                                    # Sharpe Ratio (> 1.5: Green, 0.5-1.5: Yellow, < 0.5: Red)
                                                    if port_stats['sharpe_ratio'] is not None:
                                                        sharpe_value = port_stats['sharpe_ratio']
                                                        if sharpe_value > 1.5:
                                                            sharpe_color = 'text-green-700'  # Efficient
                                                        elif sharpe_value >= 0.5:
                                                            sharpe_color = 'text-yellow-600'  # Okay
                                                        else:
                                                            sharpe_color = 'text-red-700'  # Inefficient
                                                        ui.label(f"Sharpe: {sharpe_value:.2f}").classes(f'text-[10px] {sharpe_color}')
                                        except Exception as e:
                                            logger.debug(f"Could not calculate stats for portfolio {pid}: {e}")
                            else:
                                # Individual Portfolio: Show cards for each position
                                if position_stats:
                                    ui.label(f'{period_label} - Position Risk/Reward Metrics').classes('text-xs font-semibold text-slate-500 mt-3 mb-1')
                                    
                                    with ui.row().classes('w-full gap-2 flex-wrap'):
                                        for pos_stat in position_stats:
                                            symbol = pos_stat['symbol']
                                            twr_value = pos_stat['twr_annualized']
                                            vol_value = pos_stat['volatility']
                                            sharpe_value = pos_stat['sharpe_ratio']
                                            
                                            # Color for Sharpe ratio
                                            if sharpe_value > 1.0:
                                                border_color = '#22c55e'  # Green
                                            elif sharpe_value > 0.5:
                                                border_color = '#eab308'  # Yellow
                                            else:
                                                border_color = '#ef4444'  # Red
                                            
                                            with ui.card().classes('flex-1 min-w-[140px] p-3').style(f'border-left: 3px solid {border_color}'):
                                                ui.label(symbol).classes('text-slate-700 text-[11px] font-semibold mb-1 truncate')
                                                
                                                # Return: Annualized TWR
                                                twr_color = 'text-emerald-700' if twr_value >= 0 else 'text-red-700'
                                                ui.label(f"Return: {format_percentage(twr_value)}").classes(f'text-sm font-bold {twr_color}')
                                                
                                                # Volatility
                                                if vol_value < 10:
                                                    vol_color = 'text-green-700'
                                                elif vol_value <= 30:
                                                    vol_color = 'text-yellow-600'
                                                else:
                                                    vol_color = 'text-red-700'
                                                ui.label(f"Volatility: {format_percentage(vol_value)}").classes(f'text-[10px] {vol_color}')
                                                
                                                # Sharpe Ratio
                                                if sharpe_value > 1.5:
                                                    sharpe_color = 'text-green-700'
                                                elif sharpe_value >= 0.5:
                                                    sharpe_color = 'text-yellow-600'
                                                else:
                                                    sharpe_color = 'text-red-700'
                                                ui.label(f"Sharpe: {sharpe_value:.2f}").classes(f'text-[10px] {sharpe_color}')
                                else:
                                    ui.label('No position data available for risk analysis.').classes('text-gray-400 text-xs mt-2 italic')
                        except Exception as e:
                            logger.debug(f'Risk/Reward statistics error: {str(e)}')
    finally:
        db.close()


def _render_allocation_charts(db):
    """Render allocation pie charts (All Portfolios view)"""
    
    # Use grid that becomes single column on mobile
    with ui.element('div').classes('grid grid-cols-1 lg:grid-cols-2 w-full gap-4'):
        # Pie 1: All portfolios allocation
        with ui.card().classes('p-6'):
            ui.label('All Portfolios Allocation').classes('text-xl font-bold mb-4')
            try:
                # Get all active portfolio IDs
                portfolios_loader = get_portfolios_loader()
                all_active_ids = [p['id'] for p in portfolios_loader.get_portfolios() if p.get('status') == 'active']
                all_chart = create_portfolio_allocation_chart(db, all_active_ids, "All Portfolios Allocation")
                ui.plotly(all_chart).classes('w-full')
            except Exception as e:
                ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')
        
        # Pie 2: Investment Portfolios (Securities, Commodities, Crypto)
        with ui.card().classes('p-6'):
            # Header with title and tabs
            with ui.row().classes('w-full items-center justify-between mb-4'):
                ui.label('Investment Portfolios').classes('text-xl font-bold')
                
                # Refreshable tabs for switching between market value and invested capital
                @ui.refreshable
                def investment_allocation_tabs():
                    allocation_view = app.storage.user.get('investment_allocation_view', 'market_value')
                    
                    def switch_view(view_type):
                        app.storage.user['investment_allocation_view'] = view_type
                        investment_allocation_tabs.refresh()
                        investment_allocation_chart.refresh()
                    
                    with ui.row().classes('gap-1'):
                        # Market Value tab
                        btn_mv = ui.button(
                            'Market Value',
                            on_click=lambda: switch_view('market_value')
                        )
                        btn_mv.props('flat dense no-caps')
                        btn_mv.classes('text-xs px-2 py-1')
                        if allocation_view == 'market_value':
                            btn_mv.classes('bg-blue-700 text-white')
                        else:
                            btn_mv.classes('bg-slate-200 text-slate-700')
                        
                        # Invested Capital tab
                        btn_ic = ui.button(
                            'Invested Capital',
                            on_click=lambda: switch_view('invested_capital')
                        )
                        btn_ic.props('flat dense no-caps')
                        btn_ic.classes('text-xs px-2 py-1')
                        if allocation_view == 'invested_capital':
                            btn_ic.classes('bg-blue-700 text-white')
                        else:
                            btn_ic.classes('bg-slate-200 text-slate-700')
                
                investment_allocation_tabs()
            
            # Refreshable chart content
            @ui.refreshable
            def investment_allocation_chart():
                allocation_view = app.storage.user.get('investment_allocation_view', 'market_value')
                
                try:
                    # Get investment portfolio IDs from config (exclude cash and placeholder types)
                    portfolios_loader = get_portfolios_loader()
                    investment_ids = [p['id'] for p in portfolios_loader.get_portfolios() 
                                     if p.get('status') == 'active' and p.get('type') not in ['cash', 'placeholder']]
                    
                    if allocation_view == 'market_value':
                        investment_chart = create_portfolio_allocation_chart(db, investment_ids, "Investment Portfolios")
                    else:
                        investment_chart = create_portfolio_invested_capital_allocation_chart(db, investment_ids, "Invested Capital Allocation")
                    
                    ui.plotly(investment_chart).classes('w-full')
                except Exception as e:
                    ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')
            
            investment_allocation_chart()


def _render_performance_comparison_table(db):
    """Render portfolio performance comparison table (All Portfolios view)"""
    
    with ui.card().classes('w-full p-6'):
        ui.label('Portfolio Performance Comparison').classes('text-xl font-bold mb-4')
        
        try:
            from service.benchmark_service import calculate_alpha, get_benchmark_for_portfolio_type, get_benchmark_label
            from datetime import date, timedelta
            from apps.core.helpers import _calculate_twr_between_dates, _calculate_volatility_between_dates, calculate_xirr
            from decimal import Decimal
            import math
            
            # Get non-cash portfolios for comparison
            portfolios_loader = get_portfolios_loader()
            non_cash_portfolios = [p for p in portfolios_loader.get_portfolios() 
                                   if p.get('status') == 'active' and p.get('type') != 'cash']
            
            # Get base currency for column headers
            base_currency = get_global_base_currency()
            today = date.today()
            
            # Try to get aggregate metrics from cache using service layer
            cached_aggregate_summary = get_cached_summary(portfolio_id=None)
            cached_aggregate_period = get_cached_period_stats(portfolio_id=None, period_key='all')
            
            # Calculate aggregate "All Investments" metrics
            all_portfolio_ids = [p['id'] for p in non_cash_portfolios]
            
            # Use cache if available, otherwise calculate live (fallback)
            aggregate_metrics = None
            if cached_aggregate_summary and cached_aggregate_period:
                # ✅ CACHE HIT - Use pre-computed data
                logger.debug("Using cached aggregate metrics")
                try:
                    from apps.core.helpers import get_composite_benchmark_label
                    composite_label = get_composite_benchmark_label()
                    
                    # Check if critical fields are available in cache
                    # For aggregate, these might be NULL if precomputation didn't calculate them
                    if (cached_aggregate_summary.get('years_active') and 
                        cached_aggregate_period.get('twr_annualized') is not None):
                        
                        aggregate_metrics = {
                            'years_active': f"{cached_aggregate_summary['years_active']:.1f}",
                            'xirr': f"{cached_aggregate_period['xirr']:.1f}%" if cached_aggregate_period.get('xirr') else 'N/A',
                            'twr': f"{cached_aggregate_period['twr_annualized']:.1f}%",
                            'alpha': f"{cached_aggregate_period['alpha']:+.1f}%" if cached_aggregate_period.get('alpha') else 'N/A',
                            'mdd': f"{cached_aggregate_period['mdd']:.1f}%" if cached_aggregate_period.get('mdd') else 'N/A',
                            'sharpe': f"{cached_aggregate_period['sharpe_ratio']:.2f}" if cached_aggregate_period.get('sharpe_ratio') else 'N/A',
                            'total_pnl': f"{cached_aggregate_summary['total_pnl']:,.0f}",
                            'unrealized': f"{cached_aggregate_summary['unrealized_pnl']:,.0f}",
                            'realized': f"{cached_aggregate_summary['realized_pnl']:,.0f}"
                        }
                    else:
                        logger.warning("Aggregate cache incomplete (missing critical fields), falling back to live calculation")
                        aggregate_metrics = None
                except Exception as e:
                    logger.error(f"Failed to read aggregate cache: {e}", exc_info=True)
                    aggregate_metrics = None
            
            # Fallback: Calculate live if cache is not available
            if not aggregate_metrics:
                logger.warning("Aggregate cache not available, calculating live (this may be slow)")
                
                # Get all transactions from all portfolios
                all_transactions = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(all_portfolio_ids)
                ).order_by(Transaction.occurred_at).all()
                
                # Import required modules for live calculation
                from apps.core.helpers import get_aligned_aggregate_series, calculate_xirr
                from service.benchmark_service import calculate_composite_benchmark_twr
                import math
                
                if all_transactions:
                    try:
                        # Get first transaction date
                        first_transaction_date = all_transactions[0].occurred_at.date()
                        years_active_total = (date.today() - first_transaction_date).days / 365.25
                        
                        # Use Aligned Aggregation (Correct Method)
                        from apps.core.helpers import get_aligned_aggregate_series
                        
                        # 1. Get Aggregated Series (Forward-Filled, Excludes Cash)
                        agg_series = get_aligned_aggregate_series(db, first_transaction_date, all_portfolio_ids)
                        
                        daily_returns = []
                        total_values = []
                        all_dates = []
                        
                        if agg_series and len(agg_series) >= 2:
                            for i, day_data in enumerate(agg_series):
                                total_values.append(float(day_data['total_value']))
                                all_dates.append(day_data['date'])
                                
                                if i > 0:
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
                                actual_days = (all_dates[-1] - all_dates[0]).days
                                if actual_days < 1:
                                    actual_days = 1
                                
                                # Geometric mean of daily returns, annualized
                                cumulative_return = 1.0
                                for r in daily_returns:
                                    cumulative_return *= (1 + r)
                                
                                annualization_factor = 365 / actual_days
                                aggregate_twr = (pow(cumulative_return, annualization_factor) - 1) * 100
                                
                                # Calculate annualized volatility
                                mean_return = sum(daily_returns) / len(daily_returns)
                                variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
                                daily_volatility = math.sqrt(variance)
                                aggregate_volatility = daily_volatility * math.sqrt(365) * 100
                                
                                # Calculate aggregate Sharpe ratio
                                risk_free_rate = 2.0
                                aggregate_sharpe = (aggregate_twr - risk_free_rate) / aggregate_volatility if aggregate_volatility > 0 else 0
                            else:
                                aggregate_twr = 0.0
                                aggregate_volatility = None
                                aggregate_sharpe = None
                            
                            # Calculate aggregate MDD from UNIT PRICE (NAV) history, not raw value
                            unit_prices = [100.0]
                            for r in daily_returns:
                                unit_prices.append(unit_prices[-1] * (1 + r))
                                
                            aggregate_mdd = 0.0
                            peak = unit_prices[0]
                            for price in unit_prices:
                                if price > peak:
                                    peak = price
                                if peak > 0:
                                    drawdown = ((price - peak) / peak) * 100
                                    if drawdown < aggregate_mdd:
                                        aggregate_mdd = drawdown
                        
                        else:
                            aggregate_twr = 0.0
                            aggregate_mdd = 0.0
                            aggregate_volatility = None
                            aggregate_sharpe = None
                        
                        # Calculate aggregate XIRR using NAV-based method (same as individual portfolios)
                        # Create a synthetic NAV portfolio from the aggregate series
                        aggregate_xirr = None
                        try:
                            if agg_series and len(agg_series) >= 2:
                                # Initialize NAV tracking (like individual portfolios)
                                NAV_INITIAL_PRICE = 100.0
                                
                                first_day = agg_series[0]
                                last_day = agg_series[-1]
                                
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
                                        daily_return = ((curr_value - net_flow) / prev_value) - 1
                                        nav_price = nav_price * (1 + daily_return)
                                    else:
                                        daily_return = 0
                                    
                                    # Update units if there was a flow
                                    if abs(net_flow) > 0.01 and nav_price > 0:
                                        # Positive net_flow = deposit (buy units)
                                        # Negative net_flow = withdrawal (sell units)
                                        units_changed = net_flow / nav_price
                                        nav_units += units_changed
                                
                                # End: Calculate ending value using NAV
                                end_nav_price = nav_price
                                end_nav_units = nav_units
                                
                                # Get actual current value from latest snapshots (may be more recent than agg_series)
                                current_total_value = 0.0
                                for pid in all_portfolio_ids:
                                    latest_snap = db.query(Snapshot).filter(
                                        Snapshot.portfolio_id == pid
                                    ).order_by(Snapshot.snapshot_date.desc()).first()
                                    if latest_snap:
                                        current_total_value += float(latest_snap.total_value_base or 0)
                                
                                # Adjust end NAV price if current value differs from series end
                                if end_nav_units > 0:
                                    end_nav_price = current_total_value / end_nav_units
                                
                                end_date_xirr = date.today()
                                
                                # Build cash flows using NAV method (same as individual portfolios)
                                cashflows = []
                                cashflow_dates = []
                                
                                # Initial investment: start_units × start_nav_price (negative = cash out)
                                cashflows.append(-start_nav_units * start_nav_price)
                                cashflow_dates.append(start_date_xirr)
                                
                                # Add all transactions at their actual cash amounts
                                # These represent external money flowing in/out of the system
                                for txn in all_transactions:
                                    txn_date = txn.occurred_at.date()
                                    if start_date_xirr <= txn_date <= end_date_xirr:
                                        tx_value = abs(float(txn.value_base))
                                        if txn.type in ['buy', 'deposit', 'transfer_in']:
                                            cashflows.append(-tx_value)
                                            cashflow_dates.append(txn_date)
                                        elif txn.type in ['sell', 'withdrawal', 'transfer_out']:
                                            cashflows.append(tx_value)
                                            cashflow_dates.append(txn_date)
                                
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
                                        aggregate_xirr = xirr_result * 100 if xirr_result is not None else None
                                        
                                        if aggregate_xirr is not None:
                                            logger.debug(f"Aggregate XIRR calculated: {aggregate_xirr:.2f}% over {(end_date_xirr - start_date_xirr).days} days")
                                        else:
                                            logger.warning(f"Aggregate XIRR failed to converge with {len(clean_flows)} flows")
                                    else:
                                        logger.warning(f"XIRR (NAV Synthetic) - Not enough non-zero flows")
                                else:
                                    logger.warning(f"XIRR (NAV Synthetic) - Not enough cash flows")
                            else:
                                logger.warning(f"XIRR (NAV Synthetic) - Insufficient aggregate series data")
                                
                        except Exception as e:
                            logger.error(f"XIRR (NAV Synthetic) calculation error: {e}", exc_info=True)
                            
                        # Calculate total P&L (sum of all portfolios)
                        total_unrealized = 0.0
                        total_realized = 0.0
                        for pid in all_portfolio_ids:
                            latest_snap = db.query(Snapshot).filter(
                                Snapshot.portfolio_id == pid
                            ).order_by(Snapshot.snapshot_date.desc()).first()
                            if latest_snap:
                                total_unrealized += float(latest_snap.unrealized_pnl_base or 0)
                                total_realized += float(latest_snap.realized_pnl_base or 0)
                        
                        total_pnl = total_unrealized + total_realized
                        
                        # Calculate Alpha vs Composite Benchmark (for all investments combined)
                        from service.benchmark_service import calculate_composite_benchmark_twr
                        benchmark_twr = calculate_composite_benchmark_twr(first_transaction_date, date.today())
                        aggregate_alpha = aggregate_twr - benchmark_twr if benchmark_twr is not None else None
                        
                        aggregate_metrics = {
                            'years_active': f"{years_active_total:.1f}",
                            'xirr': f"{aggregate_xirr:.1f}%" if aggregate_xirr is not None else 'N/A',
                            'twr': f"{aggregate_twr:.1f}%",
                            'alpha': f"{aggregate_alpha:+.1f}%" if aggregate_alpha is not None else 'N/A',
                            'mdd': f"{aggregate_mdd:.1f}%",
                            'sharpe': f"{aggregate_sharpe:.2f}" if aggregate_sharpe is not None else 'N/A',
                            'total_pnl': f"{total_pnl:,.0f}",
                            'unrealized': f"{total_unrealized:,.0f}",
                            'realized': f"{total_realized:,.0f}"
                        }
                    except Exception as e:
                        logger.error(f"Failed to calculate aggregate metrics (fallback): {e}", exc_info=True)
            
            # Display aggregate summary if available
            if aggregate_metrics:
                with ui.card().classes('w-full p-4 mb-4 bg-slate-50 border-l-4 border-slate-700'):
                    ui.label('Total - All Investments Combined').classes('text-sm font-bold text-slate-700 mb-2')
                    with ui.row().classes('w-full gap-6 flex-wrap text-xs'):
                        with ui.column().classes('gap-1'):
                            ui.label('Years Active').classes('text-gray-500')
                            ui.label(aggregate_metrics['years_active']).classes('font-semibold')
                        with ui.column().classes('gap-1'):
                            ui.label('XIRR (Ann.)').classes('text-gray-500')
                            ui.label(aggregate_metrics['xirr']).classes('font-semibold text-cyan-700')
                        with ui.column().classes('gap-1'):
                            ui.label('TWR (Ann.)').classes('text-gray-500')
                            ui.label(aggregate_metrics['twr']).classes('font-semibold text-emerald-700')
                        with ui.column().classes('gap-1'):
                            from apps.core.helpers import get_composite_benchmark_label
                            composite_label = get_composite_benchmark_label()
                            ui.label(f'Alpha (vs {composite_label})').classes('text-gray-500')
                            alpha_val = aggregate_metrics['alpha']
                            alpha_color = 'text-emerald-600' if '+' in alpha_val else 'text-red-500' if alpha_val != 'N/A' else 'text-gray-500'
                            ui.label(alpha_val).classes(f'font-semibold {alpha_color}')
                        with ui.column().classes('gap-1'):
                            ui.label('MDD').classes('text-gray-500')
                            ui.label(aggregate_metrics['mdd']).classes('font-semibold text-red-700')
                        with ui.column().classes('gap-1'):
                            ui.label('Sharpe').classes('text-gray-500')
                            ui.label(aggregate_metrics['sharpe']).classes('font-semibold text-purple-700')
                    
                    with ui.grid(columns=3).classes('gap-4 text-xs mt-2'):
                        with ui.column().classes('gap-1'):
                            ui.label(f'Unrealized P&L ({base_currency})').classes('text-gray-500')
                            ui.label(aggregate_metrics['unrealized']).classes('font-semibold')
                        with ui.column().classes('gap-1'):
                            ui.label(f'Realized P&L ({base_currency})').classes('text-gray-500')
                            ui.label(aggregate_metrics['realized']).classes('font-semibold')
                        with ui.column().classes('gap-1'):
                            ui.label(f'Total P&L ({base_currency})').classes('text-gray-500')
                            ui.label(aggregate_metrics['total_pnl']).classes('font-semibold text-green-700')
            
            # Build performance rows for each portfolio
            perf_rows = []
            for p in non_cash_portfolios:
                portfolio_id = p['id']
                portfolio_type = p.get('type', 'other')
                
                # Try to get cached data using service layer
                cached_summary = get_cached_summary(portfolio_id)
                cached_period = get_cached_period_stats(portfolio_id, period_key='all')
                
                # Check if we have complete cached data
                if cached_summary and cached_period:
                    # ✅ CACHE HIT - Use pre-computed data
                    logger.debug(f"Using cached performance data for portfolio {portfolio_id}")
                    
                    try:
                        # Get benchmark label from cache or config
                        from service.benchmark_service import get_benchmark_for_portfolio_type, calculate_alpha
                        from apps.core.helpers import get_composite_benchmark_label
                        
                        # Calculate alpha using TWR from summary cache and portfolio start date
                        alpha_str = 'N/A'
                        alpha_color = 'gray'
                        
                        if cached_summary.get('twr') is not None and cached_summary.get('first_snapshot_date'):
                            try:
                                twr_pct = cached_summary['twr']
                                alpha_value = calculate_alpha(
                                    twr_pct, 
                                    portfolio_type, 
                                    cached_summary['first_snapshot_date'], 
                                    today
                                )
                                if alpha_value is not None:
                                    alpha_str = f"{alpha_value:+.2f}%"
                                    alpha_color = 'green' if alpha_value >= 0 else 'red'
                            except Exception as e:
                                logger.warning(f"Failed to calculate alpha for portfolio {portfolio_id}: {e}")
                        
                        # Get benchmark label
                        if cached_period.get('benchmark_symbol'):
                            # Use cached benchmark symbol to generate label
                            if cached_period['benchmark_symbol'] == 'composite':
                                benchmark_label = get_composite_benchmark_label()
                            else:
                                # Get label from config
                                benchmark_config = get_benchmark_for_portfolio_type(portfolio_type)
                                if benchmark_config:
                                    if benchmark_config.get('is_composite'):
                                        benchmark_label = benchmark_config['label']
                                    elif benchmark_config.get('is_blend'):
                                        benchmark_label = f"{benchmark_config['securities'].get('label', 'SEC')}/{benchmark_config['crypto'].get('label', 'CRYPTO')}"
                                    else:
                                        benchmark_label = benchmark_config.get('label', cached_period['benchmark_symbol'])
                                else:
                                    benchmark_label = cached_period['benchmark_symbol']
                        else:
                            benchmark_label = 'N/A'
                        
                        perf_rows.append({
                            'portfolio': p['name'],
                            'years_active': f"{cached_summary['years_active']:.1f}" if cached_summary.get('years_active') else 'N/A',
                            'xirr': f"{cached_summary['xirr']:.1f}%" if cached_summary.get('xirr') else 'N/A',
                            'twr': f"{cached_summary['twr']:.1f}%" if cached_summary.get('twr') else 'N/A',
                            'alpha': alpha_str,
                            'alpha_color': alpha_color,
                            'benchmark': benchmark_label,
                            'mdd': f"{cached_summary['mdd']:.1f}%" if cached_summary.get('mdd') else 'N/A',
                            'sharpe': f"{cached_period['sharpe_ratio']:.2f}" if cached_period.get('sharpe_ratio') else 'N/A',
                            'total_return': f"{cached_summary['overall_return_pct']:.1f}%",
                            'unrealized': f"{cached_summary['unrealized_pnl']:,.0f}" if cached_summary.get('unrealized_pnl') else '0.00',
                            'realized': f"{cached_summary['realized_pnl']:,.0f}" if cached_summary.get('realized_pnl') else '0.00',
                            'total_pnl': f"{cached_summary['total_pnl']:,.0f}",
                        })
                        continue  # Skip to next portfolio (cache hit)
                        
                    except Exception as e:
                        logger.warning(f"Failed to read cache for portfolio {portfolio_id}: {e}, falling back to live calculation")
                
                # ❌ CACHE MISS - Fallback to live calculation
                logger.warning(f"Cache not available for portfolio {portfolio_id}, calculating live (this may be slow)")
                
                from service.portfolio_service import calculate_portfolio_return
                from service.benchmark_service import calculate_alpha, get_benchmark_for_portfolio_type
                from apps.core.helpers import calculate_max_drawdown, calculate_portfolio_statistics
                
                perf = calculate_portfolio_return(db, portfolio_id)
                
                if perf['data_available']:
                    # CRITICAL: Use actual portfolio history for MDD and Sharpe, NOT fixed 10 years
                    # Get first snapshot for this portfolio to determine actual history length
                    first_snap = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == p['id']
                    ).order_by(Snapshot.snapshot_date).first()
                    
                    if first_snap:
                        actual_history_days = (date.today() - first_snap.snapshot_date).days
                        portfolio_start_date = first_snap.snapshot_date
                    else:
                        actual_history_days = 365  # Fallback
                        portfolio_start_date = date.today() - timedelta(days=365)
                    
                    # Calculate lifetime MDD using actual portfolio history
                    lifetime_mdd = calculate_max_drawdown(db, p['id'], actual_history_days)
                    
                    # Calculate lifetime Sharpe ratio using actual portfolio history
                    from apps.core.helpers import calculate_portfolio_statistics
                    try:
                        stats = calculate_portfolio_statistics(
                            db, 
                            p['id'], 
                            start_date=portfolio_start_date,
                            end_date=date.today()
                        )
                        lifetime_sharpe = f"{stats['sharpe_ratio']:.2f}" if stats and stats['sharpe_ratio'] is not None else 'N/A'
                    except:
                        lifetime_sharpe = 'N/A'
                    
                    # Calculate Alpha (excess return vs benchmark)
                    # CRITICAL: Benchmark must be compared over the SAME time period as portfolio
                    try:
                        twr_pct = float(perf['twr_pct'])
                        # Use portfolio's actual first snapshot date for accurate benchmark comparison
                        # This ensures portfolio TWR and benchmark TWR are over the same period
                        alpha_start_date = portfolio_start_date
                        end_date = date.today()
                        
                        alpha = calculate_alpha(twr_pct, portfolio_type, alpha_start_date, end_date)
                        if alpha is not None:
                            alpha_str = f"{alpha:+.2f}%"
                            alpha_color = 'green' if alpha >= 0 else 'red'
                        else:
                            alpha_str = 'N/A'
                            alpha_color = 'gray'
                    except Exception as e:
                        alpha_str = 'N/A'
                        alpha_color = 'gray'
                    
                    # Get benchmark label for this portfolio type
                    benchmark_config = get_benchmark_for_portfolio_type(portfolio_type)
                    if benchmark_config:
                        if benchmark_config.get('is_composite'):
                            benchmark_label = benchmark_config['label']
                        elif benchmark_config.get('is_blend'):
                            benchmark_label = f"{benchmark_config['securities'].get('label', 'SEC')}/{benchmark_config['crypto'].get('label', 'CRYPTO')}"
                        else:
                            benchmark_label = benchmark_config.get('label', 'Benchmark')
                    else:
                        benchmark_label = 'N/A'
                    
                    perf_rows.append({
                        'portfolio': p['name'],
                        'years_active': f"{float(perf['years_active']):.1f}",
                        'xirr': f"{float(perf['xirr_pct']):.1f}%",
                        'twr': f"{float(perf['twr_pct']):.1f}%",
                        'alpha': alpha_str,
                        'alpha_color': alpha_color,
                        'benchmark': benchmark_label,
                        'mdd': f"{lifetime_mdd:.1f}%",
                        'sharpe': lifetime_sharpe,
                        'total_return': f"{float(perf['total_return_pct']):.1f}%",
                        'unrealized': f"{float(perf['unrealized_pnl']):,.0f}",
                        'realized': f"{float(perf['realized_pnl']):,.0f}",
                        'total_pnl': f"{float(perf['total_pnl']):,.0f}",
                    })
                else:
                    perf_rows.append({
                        'portfolio': p['name'],
                        'years_active': 'N/A',
                        'xirr': 'N/A',
                        'twr': 'N/A',
                        'alpha': 'N/A',
                        'alpha_color': 'gray',
                        'benchmark': 'N/A',
                        'mdd': 'N/A',
                        'sharpe': 'N/A',
                        'total_return': 'N/A',
                        'unrealized': 'N/A',
                        'realized': 'N/A',
                        'total_pnl': 'N/A',
                    })
            
            perf_columns = [
                {'name': 'portfolio', 'label': 'Portfolio', 'field': 'portfolio', 'align': 'left', 'sortable': True},
                {'name': 'years_active', 'label': 'Years Active', 'field': 'years_active', 'align': 'right', 'sortable': True},
                {'name': 'xirr', 'label': 'XIRR (Ann.)', 'field': 'xirr', 'align': 'right', 'sortable': True},
                {'name': 'twr', 'label': 'TWR (Ann.)', 'field': 'twr', 'align': 'right', 'sortable': True},
                {'name': 'alpha', 'label': 'Alpha', 'field': 'alpha', 'align': 'right', 'sortable': True},
                {'name': 'benchmark', 'label': 'vs Benchmark', 'field': 'benchmark', 'align': 'left', 'sortable': True},
                {'name': 'mdd', 'label': 'MDD', 'field': 'mdd', 'align': 'right', 'sortable': True},
                {'name': 'sharpe', 'label': 'Sharpe', 'field': 'sharpe', 'align': 'right', 'sortable': True},
                {'name': 'total_return', 'label': 'Total Return', 'field': 'total_return', 'align': 'right', 'sortable': True},
                {'name': 'unrealized', 'label': f'Unrealized ({base_currency})', 'field': 'unrealized', 'align': 'right', 'sortable': True},
                {'name': 'realized', 'label': f'Realized ({base_currency})', 'field': 'realized', 'align': 'right', 'sortable': True},
                {'name': 'total_pnl', 'label': f'Total P&L ({base_currency})', 'field': 'total_pnl', 'align': 'right', 'sortable': True},
            ]
            
            table = ui.table(columns=perf_columns, rows=perf_rows, row_key='portfolio').classes('w-full').props('dense flat wrap-cells')
            table.add_slot('body', r'''
                <q-tr :props="props" :class="props.rowIndex % 2 === 0 ? '' : 'bg-gray-50'">
                    <q-td v-for="col in props.cols" :key="col.name" :props="props" :class="$q.screen.lt.sm ? 'text-xs' : ''">
                        <span v-if="col.name === 'alpha'" :style="{ color: props.row.alpha_color }">{{ col.value }}</span>
                        <span v-else>{{ col.value }}</span>
                    </q-td>
                </q-tr>
            ''')
            
            # Add footnotes explaining metrics and cache usage
            with ui.column().classes('gap-1 mt-2'):
                                
                # Check if any cache was used
                cache_used = cached_aggregate_summary is not None or any(
                    get_cached_summary(p['id']) is not None for p in non_cash_portfolios
                )
                
                if cache_used:
                    # Show last updated timestamp from cache
                    latest_cache_time = None
                    if cached_aggregate_summary and cached_aggregate_summary.get('cached_at'):
                        from datetime import datetime
                        latest_cache_time = datetime.fromisoformat(cached_aggregate_summary['cached_at'])
                    
                    if latest_cache_time:
                        from datetime import datetime
                        time_diff = datetime.now() - latest_cache_time.replace(tzinfo=None)
                        hours_ago = int(time_diff.total_seconds() / 3600)
                        if hours_ago < 1:
                            time_str = f"{int(time_diff.total_seconds() / 60)} minute(s) ago"
                        elif hours_ago < 24:
                            time_str = f"{hours_ago} hour(s) ago"
                        else:
                            time_str = f"{int(time_diff.total_seconds() / 86400)} day(s) ago"
                        ui.label(f'Data from pre-computed cache - last updated: {time_str}').classes('text-xs text-gray-400 italic')
                    else:
                        ui.label('Data from pre-computed cache').classes('text-xs text-gray-400 italic')
                else:
                    ui.label('Calculated live (cache not available - restart worker to populate)').classes('text-xs text-gray-500 italic')
            
        except Exception as e:
            ui.label(f'Performance data unavailable: {str(e)}').classes('text-gray-400 text-center py-8')


def _render_position_allocation(summary):
    """Render position allocation pie chart (single portfolio view)"""
    
    with ui.card().classes('w-full p-6'):
        ui.label('Position Allocation').classes('text-xl font-bold mb-4')
        try:
            base_currency = summary['portfolio'].currency_base
            allocation_chart = create_allocation_chart(summary['positions'], base_currency)
            ui.plotly(allocation_chart).classes('w-full')
        except Exception as e:
            ui.label(f'Chart unavailable: {str(e)}').classes('text-gray-400 text-center py-8')


def _render_positions_table(summary, selected_portfolio_id):
    """Render current positions table"""
    
    with ui.card().classes('w-full p-6'):
        ui.label('Current Positions').classes('text-xl font-bold mb-4')
        
        # Filter out manual cash portfolios from display (but keep in summary for total wealth)
        positions_to_display = summary['positions']
        if selected_portfolio_id is None:  # All Portfolios view
            portfolios_loader = get_portfolios_loader()
            manual_cash_ids = [
                p['id'] for p in portfolios_loader.get_portfolios()
                if p.get('type') == 'cash' and p.get('update_method') == 'manual'
            ]
            positions_to_display = [
                p for p in summary['positions']
                if get_pos_value(p, 'portfolio_id') not in manual_cash_ids
            ]
        
        if positions_to_display:
            # Sort positions: by portfolio_id first, then by value (descending)
            sorted_positions = sorted(
                positions_to_display,
                key=lambda p: (
                    get_pos_value(p, 'portfolio_id'),
                    -float((get_pos_value(p, 'quantity') or 0) * (get_pos_value(p, 'current_price') or 0))  # negative for descending
                )
            )
            
            # Add portfolio column if showing all portfolios
            if selected_portfolio_id is None:
                columns = [
                    {'name': 'portfolio', 'label': 'Portfolio', 'field': 'portfolio', 'align': 'left', 'sortable': True},
                    {'name': 'symbol', 'label': 'Symbol', 'field': 'symbol', 'align': 'left', 'sortable': True},
                    {'name': 'quantity', 'label': 'Quantity', 'field': 'quantity', 'align': 'right', 'sortable': True},
                    {'name': 'current_price', 'label': 'Price', 'field': 'current_price', 'align': 'right', 'sortable': True},
                    {'name': 'value', 'label': 'Value', 'field': 'value', 'align': 'right', 'sortable': True},
                ]
                
                # Get portfolio names from config
                portfolios_loader = get_portfolios_loader()
                portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
                
                rows = [{                    'portfolio': portfolio_names.get(get_pos_value(p, 'portfolio_id'), f'P{get_pos_value(p, "portfolio_id")}'),
                    'portfolio_id': get_pos_value(p, 'portfolio_id'),  # Keep for sorting
                    'symbol': get_pos_value(p, 'symbol'),
                    'quantity': f"{get_pos_value(p, 'quantity'):.4f}",
                    'current_price': format_currency(get_pos_value(p, 'current_price') or 0, summary['portfolio'].currency_base),
                    'value': format_currency((get_pos_value(p, 'quantity') or 0) * (get_pos_value(p, 'current_price') or 0), summary['portfolio'].currency_base),
                    'value_numeric': float((get_pos_value(p, 'quantity') or 0) * (get_pos_value(p, 'current_price') or 0))  # For sorting
                } for p in sorted_positions]
            else:
                columns = [
                    {'name': 'symbol', 'label': 'Symbol', 'field': 'symbol', 'align': 'left', 'sortable': True},
                    {'name': 'quantity', 'label': 'Quantity', 'field': 'quantity', 'align': 'right', 'sortable': True},
                    {'name': 'current_price', 'label': 'Price', 'field': 'current_price', 'align': 'right', 'sortable': True},
                    {'name': 'value', 'label': 'Value', 'field': 'value', 'align': 'right', 'sortable': True},
                ]
                
                rows = [{
                    'symbol': get_pos_value(p, 'symbol'),
                    'quantity': f"{get_pos_value(p, 'quantity'):.4f}",
                    'current_price': format_currency(get_pos_value(p, 'current_price') or 0, summary['portfolio'].currency_base),
                    'value': format_currency((get_pos_value(p, 'quantity') or 0) * (get_pos_value(p, 'current_price') or 0), summary['portfolio'].currency_base),
                    'value_numeric': float((get_pos_value(p, 'quantity') or 0) * (get_pos_value(p, 'current_price') or 0))  # For sorting
                } for p in sorted_positions]
            
            table = ui.table(columns=columns, rows=rows, row_key='symbol').classes('w-full').props('flat')
            table.add_slot('body', r'''
                <q-tr :props="props" :class="props.rowIndex % 2 === 0 ? '' : 'bg-gray-50'">
                    <q-td v-for="col in props.cols" :key="col.name" :props="props">
                        {{ col.value }}
                    </q-td>
                </q-tr>
            ''')
        else:
            with ui.column().classes('w-full items-center py-8'):
                ui.icon('info', size='3rem').classes('text-gray-300 mb-2')
                ui.label('No positions found').classes('text-gray-500')


def _render_diagnostics_section(db, summary):
    """Render Tier 4: System Diagnostics (collapsible)"""
    
    with ui.expansion('System Diagnostics', icon='settings').classes('w-full'):
        ui.label('Verify data freshness and system health. These tables show the latest data for debugging purposes.').classes('text-gray-500 text-sm mb-4')
        
        # 1. Latest Snapshot per Portfolio
        with ui.card().classes('w-full p-6 mb-4'):
            ui.label('Latest Portfolio Snapshots').classes('text-lg font-bold mb-4')
            
            try:
                from datetime import date
                
                # Get active portfolios
                portfolios_loader = get_portfolios_loader()
                active_portfolio_ids = [p['id'] for p in portfolios_loader.get_portfolios() if p.get('status') == 'active']
                portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
                
                # Get latest snapshot for each active portfolio
                latest_snapshots_subq = db.query(
                    Snapshot.portfolio_id,
                    func.max(Snapshot.snapshot_date).label('max_date')
                ).filter(
                    Snapshot.portfolio_id.in_(active_portfolio_ids)
                ).group_by(Snapshot.portfolio_id).subquery()
                
                latest_snapshots = db.query(Snapshot).join(
                    latest_snapshots_subq,
                    and_(
                        Snapshot.portfolio_id == latest_snapshots_subq.c.portfolio_id,
                        Snapshot.snapshot_date == latest_snapshots_subq.c.max_date
                    )
                ).order_by(Snapshot.portfolio_id).all()
                
                if latest_snapshots:
                    base_currency = summary['portfolio'].currency_base
                    snapshot_columns = [
                        {'name': 'portfolio', 'label': 'Portfolio', 'field': 'portfolio', 'align': 'left'},
                        {'name': 'as_of_date', 'label': 'As Of Date', 'field': 'as_of_date', 'align': 'left'},
                        {'name': 'total_value', 'label': 'Market Value', 'field': 'total_value', 'align': 'right'},
                    ]
                    
                    snapshot_rows = [{
                        'portfolio': f"{portfolio_names.get(snap.portfolio_id, f'P{snap.portfolio_id}')}",
                        'as_of_date': snap.snapshot_date.strftime('%Y-%m-%d'),
                        'total_value': format_currency(snap.total_value_base or 0, base_currency),
                    } for snap in latest_snapshots]
                    
                    table = ui.table(columns=snapshot_columns, rows=snapshot_rows, row_key='portfolio').classes('w-full').props('flat')
                    table.add_slot('body', r'''
                        <q-tr :props="props" :class="props.rowIndex % 2 === 0 ? '' : 'bg-gray-50'">
                            <q-td v-for="col in props.cols" :key="col.name" :props="props">
                                {{ col.value }}
                            </q-td>
                        </q-tr>
                    ''')
                else:
                    ui.label('No snapshots available').classes('text-gray-400 text-center py-4')
            
            except Exception as e:
                ui.label(f'Snapshot data unavailable: {str(e)}').classes('text-gray-400 text-center py-4')
        
        # 2. Latest FX Rates
        with ui.card().classes('w-full p-6 mb-4'):
            ui.label('Latest FX Rates').classes('text-lg font-bold mb-4')
            
            try:
                # Get latest FX rate for each pair
                latest_fx_subq = db.query(
                    FxRate.pair,
                    func.max(FxRate.as_of_date).label('max_date')
                ).group_by(FxRate.pair).subquery()
                
                latest_fx_rates = db.query(FxRate).join(
                    latest_fx_subq,
                    and_(
                        FxRate.pair == latest_fx_subq.c.pair,
                        FxRate.as_of_date == latest_fx_subq.c.max_date
                    )
                ).order_by(FxRate.pair).all()
                
                if latest_fx_rates:
                    fx_columns = [
                        {'name': 'pair', 'label': 'Pair', 'field': 'pair', 'align': 'left'},
                        {'name': 'as_of_date', 'label': 'As Of Date', 'field': 'as_of_date', 'align': 'left'},
                        {'name': 'rate', 'label': 'Rate', 'field': 'rate', 'align': 'right'},
                        {'name': 'source', 'label': 'Source', 'field': 'source', 'align': 'left'},
                    ]
                    
                    fx_rows = [{
                        'pair': fx.pair,
                        'as_of_date': fx.as_of_date.strftime('%Y-%m-%d %H:%M:%S'),
                        'rate': f"{fx.rate:.6f}",
                        'source': fx.source or 'N/A',
                    } for fx in latest_fx_rates]
                    
                    table = ui.table(columns=fx_columns, rows=fx_rows, row_key='pair').classes('w-full').props('flat')
                    table.add_slot('body', r'''
                        <q-tr :props="props" :class="props.rowIndex % 2 === 0 ? '' : 'bg-gray-50'">
                            <q-td v-for="col in props.cols" :key="col.name" :props="props">
                                {{ col.value }}
                            </q-td>
                        </q-tr>
                    ''')
                else:
                    ui.label('No FX rates available').classes('text-gray-400 text-center py-4')
            
            except Exception as e:
                ui.label(f'FX rate data unavailable: {str(e)}').classes('text-gray-400 text-center py-4')
        
        # 3. Latest Market Data
        with ui.card().classes('w-full p-6'):
            ui.label('Latest Market Data').classes('text-lg font-bold mb-4')
            
            try:
                from crud.crud_market_fx import get_latest_fx_rate
                from decimal import Decimal
                
                # Get unique symbols from all non-reserved portfolios (matches main query)
                portfolios_loader = get_portfolios_loader()
                portfolio_ids = [
                    p['id'] for p in portfolios_loader.get_portfolios() 
                    if p.get('status') != 'reserved'
                ]
                
                held_positions = db.query(Position).filter(
                    Position.portfolio_id.in_(portfolio_ids),
                    Position.quantity > 0
                ).all()
                
                held_symbols = {get_pos_value(pos, 'symbol') for pos in held_positions}
                
                if not held_symbols:
                    ui.label('No positions with market data').classes('text-gray-400 text-center py-4')
                else:
                    latest_market_subq = db.query(
                        MarketData.symbol,
                        func.max(MarketData.as_of_date).label('max_date')
                    ).filter(
                        MarketData.symbol.in_(held_symbols)
                    ).group_by(MarketData.symbol).subquery()
                    
                    latest_market_data = db.query(MarketData).join(
                        latest_market_subq,
                        and_(
                            MarketData.symbol == latest_market_subq.c.symbol,
                            MarketData.as_of_date == latest_market_subq.c.max_date
                        )
                    ).order_by(MarketData.source, MarketData.symbol).all()
                    
                    if latest_market_data:
                        base_currency = summary['portfolio'].currency_base
                        
                        market_columns = [
                            {'name': 'symbol', 'label': 'Symbol', 'field': 'symbol', 'align': 'left'},
                            {'name': 'as_of_date', 'label': 'As Of Date', 'field': 'as_of_date', 'align': 'left'},
                            {'name': 'price', 'label': 'Price (Native)', 'field': 'price', 'align': 'right'},
                            {'name': 'price_base', 'label': f'Price ({base_currency})', 'field': 'price_base', 'align': 'right'},
                            {'name': 'source', 'label': 'Source', 'field': 'source', 'align': 'left'},
                        ]
                        
                        market_rows = []
                        for md in latest_market_data:
                            price_in_base = md.price
                            if md.currency != base_currency:
                                pair = f"{md.currency}/{base_currency}"
                                fx_record = get_latest_fx_rate(db, pair)
                                if fx_record:
                                    price_in_base = Decimal(str(md.price)) * Decimal(str(fx_record.rate))
                                else:
                                    price_in_base = None
                            
                            market_rows.append({
                                'symbol': md.symbol,
                                'as_of_date': md.as_of_date.strftime('%Y-%m-%d %H:%M:%S'),
                                'price': f"{md.currency} {float(md.price):,.4f}",
                                'price_base': f"{base_currency} {float(price_in_base):,.4f}" if price_in_base else 'N/A',
                                'source': md.source or 'N/A',
                            })
                        
                        table = ui.table(columns=market_columns, rows=market_rows, row_key='symbol').classes('w-full').props('flat')
                        table.add_slot('body', r'''
                            <q-tr :props="props" :class="props.rowIndex % 2 === 0 ? '' : 'bg-gray-50'">
                                <q-td v-for="col in props.cols" :key="col.name" :props="props">
                                    {{ col.value }}
                                </q-td>
                            </q-tr>
                        ''')
                    else:
                        ui.label('No market data available for held positions').classes('text-gray-400 text-center py-4')
            
            except Exception as e:
                ui.label(f'Market data unavailable: {str(e)}').classes('text-gray-400 text-center py-4')
