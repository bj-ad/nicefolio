"""
Chart creation functions for NiceFolio portfolio dashboard.

This module contains all Plotly chart generation functions used in the portfolio dashboard,
including performance charts, allocation charts, and comparison charts.
"""

import plotly.graph_objects as go
from datetime import date, timedelta, datetime
from collections import defaultdict
from decimal import Decimal
from typing import Optional, List
from nicegui import app

from database import SessionLocal
from models import Snapshot, Portfolio, Position, FxRate, MarketData, Transaction
from utils.portfolios_loader import get_portfolios_loader
from utils.app_config import get_global_base_currency
from utils.logging_config import get_logger
from apps.core.helpers import calculate_max_drawdown, get_aligned_aggregate_series, calculate_net_invested_capital
from service.portfolio_service import calculate_portfolio_return

logger = get_logger(__name__)


def create_performance_chart(db, portfolio_id=None, base_currency=None, date_range='1y'):
    """
    Create portfolio performance chart.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID. If None, aggregates all portfolios (3, 5, 8)
        base_currency: Base currency for y-axis label. If None, uses global base currency.
        date_range: Date range to display ('1m', '1y', '3y', '5y', 'all'). Default is '1y'.
    
    Returns:
        plotly.graph_objects.Figure
    """
    # Calculate date cutoff based on range
    today = date.today()
    if date_range == 'custom':
        # Use custom dates from storage
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            cutoff_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            today = datetime.strptime(to_str, '%Y-%m-%d').date()
        else:
            cutoff_date = today - timedelta(days=365)
    else:
        range_mapping = {
            '1m': today - timedelta(days=30),
            '3m': today - timedelta(days=90),
            '6m': today - timedelta(days=180),
            '1y': today - timedelta(days=365),
            '3y': today - timedelta(days=3*365),
            '5y': today - timedelta(days=5*365),
            'all': None  # No cutoff
        }
        cutoff_date = range_mapping.get(date_range, range_mapping['1y'])
    
    # Get base currency if not provided
    if base_currency is None:
        if portfolio_id:
            portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            base_currency = portfolio.currency_base if portfolio else get_global_base_currency()
        else:
            base_currency = get_global_base_currency()
    
    if portfolio_id:
        # Single portfolio view - apply date range filter
        query = db.query(Snapshot).filter(Snapshot.portfolio_id == portfolio_id)
        if cutoff_date:
            query = query.filter(Snapshot.snapshot_date >= cutoff_date)
        # Add upper bound filter for custom date ranges
        query = query.filter(Snapshot.snapshot_date <= today)
        snapshots = query.order_by(Snapshot.snapshot_date).all()
        
        if not snapshots:
            # Return empty chart
            fig = go.Figure()
            fig.add_annotation(
                text="No historical data available",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=16, color="#888")
            )
            fig.update_layout(template="plotly_white", height=400)
            return fig
        
        dates = [s.snapshot_date for s in snapshots]
        values = [float(s.total_value_base) for s in snapshots]
        # CRITICAL: Use net invested capital (deposits - withdrawals), not cost basis
        invested = [calculate_net_invested_capital(s) for s in snapshots]
        # Total PnL = unrealized + realized (includes profits from sold positions)
        pnl = [float((s.unrealized_pnl_base or Decimal('0')) + (s.realized_pnl_base or Decimal('0'))) for s in snapshots]
        
        # Get portfolio type for color matching
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        portfolios_loader = get_portfolios_loader()
        portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), {})
        ptype = portfolio_config.get('type', 'other')
        
        # Color mapping by portfolio type (matching KPI cards)
        type_colors = {
            'securities': '#059669',  # Emerald-600
            'commodities': '#f59e0b',  # Amber-500
            'crypto': '#4f46e5',  # Indigo-600
            'other': '#64748b'  # Slate-500
        }
        value_color = type_colors.get(ptype, '#64748b')
        
        # Create chart
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=dates,
            y=values,
            mode='lines',
            name='Market Value',
            line=dict(color=value_color, width=2)  # Use portfolio type color
        ))
        
        fig.add_trace(go.Scatter(
            x=dates,
            y=invested,
            mode='lines',
            name='Invested Capital',
            line=dict(color='#6b7280', width=2)  # Grey solid
        ))
        
        fig.add_trace(go.Scatter(
            x=dates,
            y=pnl,
            mode='lines',
            name='Investment P&L',
            line=dict(color='#10b981', width=2)  # Green
        ))
        
    else:
        # All portfolios aggregated view
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        
        # Separate investment portfolios from cash portfolios
        # Investment portfolios: type NOT 'cash' AND status NOT 'reserved'
        investment_portfolio_ids = [
            p['id'] for p in all_portfolios 
            if p.get('type') not in ['cash', 'placeholder'] and p.get('status') != 'reserved'
        ]
        
        # All portfolios (for Market Value)
        all_portfolio_ids = [p['id'] for p in all_portfolios if p.get('status') != 'reserved']
        
        # Get snapshots for all portfolios - apply date range filter
        query = db.query(Snapshot).filter(Snapshot.portfolio_id.in_(all_portfolio_ids))
        if cutoff_date:
            query = query.filter(Snapshot.snapshot_date >= cutoff_date)
        # Add upper bound filter for custom date ranges
        query = query.filter(Snapshot.snapshot_date <= today)
        snapshots = query.order_by(Snapshot.snapshot_date).all()
        
        if not snapshots:
            # Return empty chart
            fig = go.Figure()
            fig.add_annotation(
                text="No historical data available",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=16, color="#888")
            )
            fig.update_layout(template="plotly_white", height=400)
            return fig
        
        # Build a structure: {date: {portfolio_id: value}}
        date_portfolio_values = defaultdict(dict)
        for snap in snapshots:
            date_portfolio_values[snap.snapshot_date][snap.portfolio_id] = snap.total_value_base or Decimal('0')
        
        # Get all unique dates across all portfolios
        all_dates = sorted(date_portfolio_values.keys())
        
        # For each portfolio, forward-fill missing dates (for Market Value - all portfolios)
        portfolio_series_all = {pid: {} for pid in all_portfolio_ids}
        
        for pid in all_portfolio_ids:
            last_value = Decimal('0')
            for snapshot_date in all_dates:
                if pid in date_portfolio_values[snapshot_date]:
                    last_value = date_portfolio_values[snapshot_date][pid]
                portfolio_series_all[pid][snapshot_date] = last_value
        
        # Build invested and pnl series ONLY for investment portfolios (exclude cash)
        date_portfolio_invested = defaultdict(dict)
        date_portfolio_pnl = defaultdict(dict)  # Total PnL = unrealized + realized
        for snap in snapshots:
            if snap.portfolio_id in investment_portfolio_ids:  # Only investment portfolios
                # CRITICAL: Net invested capital = deposits - withdrawals (cash flow based)
                net_invested = Decimal(str(calculate_net_invested_capital(snap)))
                date_portfolio_invested[snap.snapshot_date][snap.portfolio_id] = net_invested
                # Total PnL includes realized gains (important for closed portfolios and sold positions)
                total_pnl = (snap.unrealized_pnl_base or Decimal('0')) + (snap.realized_pnl_base or Decimal('0'))
                date_portfolio_pnl[snap.snapshot_date][snap.portfolio_id] = total_pnl
        
        # For each investment portfolio, forward-fill missing invested values
        portfolio_invested_series = {pid: {} for pid in investment_portfolio_ids}
        portfolio_pnl_series = {pid: {} for pid in investment_portfolio_ids}
        # Also build market value series for INVESTMENT portfolios only (for Market Value line)
        portfolio_series_investment = {pid: {} for pid in investment_portfolio_ids}
        
        for pid in investment_portfolio_ids:
            last_invested = Decimal('0')
            last_pnl = Decimal('0')
            last_value = Decimal('0')
            for snapshot_date in all_dates:
                if pid in date_portfolio_invested[snapshot_date]:
                    last_invested = date_portfolio_invested[snapshot_date][pid]
                portfolio_invested_series[pid][snapshot_date] = last_invested
                
                if pid in date_portfolio_pnl[snapshot_date]:
                    last_pnl = date_portfolio_pnl[snapshot_date][pid]
                portfolio_pnl_series[pid][snapshot_date] = last_pnl
                
                if pid in date_portfolio_values[snapshot_date]:
                    last_value = date_portfolio_values[snapshot_date][pid]
                portfolio_series_investment[pid][snapshot_date] = last_value
        
        # Calculate aggregate values for each date
        complete_dates = all_dates
        
        # Total Liquid Assets: sum ALL portfolios (including cash)
        complete_values_all = [
            float(sum(portfolio_series_all[pid][d] for pid in all_portfolio_ids))
            for d in all_dates
        ]
        
        # Market Value: sum INVESTMENT portfolios only (exclude cash)
        complete_values_investment = [
            float(sum(portfolio_series_investment[pid][d] for pid in investment_portfolio_ids))
            for d in all_dates
        ]
        
        # Invested Capital: sum INVESTMENT portfolios only (exclude cash)
        complete_invested = [
            float(sum(portfolio_invested_series[pid][d] for pid in investment_portfolio_ids))
            for d in all_dates
        ]
        
        # Investment P&L: sum INVESTMENT portfolios only (exclude cash)
        complete_pnl = [
            float(sum(portfolio_pnl_series[pid][d] for pid in investment_portfolio_ids))
            for d in all_dates
        ]
        
        if not complete_dates:
            # No dates with complete data for all portfolios
            fig = go.Figure()
            fig.add_annotation(
                text="Insufficient data - not all portfolios have snapshots on the same dates",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=16, color="#888")
            )
            fig.update_layout(template="plotly_white", height=400)
            return fig
        
        dates = complete_dates
        values_all = complete_values_all
        values_investment = complete_values_investment
        invested = complete_invested
        pnl = complete_pnl
        
        # Create chart with 4 lines for "All Portfolios"
        fig = go.Figure()
        
        # Line 1: Total Liquid Assets (all portfolios including cash) - hidden by default, toggleable
        fig.add_trace(go.Scatter(
            x=dates,
            y=values_all,
            mode='lines',
            name='Total Liquid Assets',
            line=dict(color='#1e40af', width=2),  # Blue-700
            visible='legendonly'  # Hidden by default, can be toggled via legend
        ))
        
        # Line 2: Market Value (investment portfolios only, excludes cash)
        fig.add_trace(go.Scatter(
            x=dates,
            y=values_investment,
            mode='lines',
            name='Market Value',
            line=dict(color='#334155', width=2)  # Slate-700
        ))
        
        # Line 3: Invested Capital (investment portfolios only)
        fig.add_trace(go.Scatter(
            x=dates,
            y=invested,
            mode='lines',
            name='Invested Capital',
            line=dict(color='#6b7280', width=2)  # Grey solid
        ))
        
        # Line 4: Investment P&L (investment portfolios only)
        fig.add_trace(go.Scatter(
            x=dates,
            y=pnl,
            mode='lines',
            name='Investment P&L',
            line=dict(color='#10b981', width=2)  # Green
        ))
    
    # Configure responsive layout
    fig.update_layout(
        template="plotly_white",
        xaxis_title=None,       # changed from 'Date'
        xaxis=dict(
            tickfont=dict(size=10),  # Smaller date labels for mobile
            tickangle=-45  # Angled for mobile readability
        ),
        yaxis_title=None,       # changed from f"Value ({base_currency})"
        yaxis=dict(
            rangemode='tozero'  # Force y-axis to start at 0
        ),

        # Add the Y-axis label as a small annotation at the top-left
        annotations=[
            dict(
                x=0, y=1.08, # Positioned slightly above the plot area
                xref="paper", yref="paper",
                text=f"Value ({base_currency})",
                showarrow=False,
                font=dict(size=11, color="gray"),
                xanchor='center'
            )
        ],

        hovermode='x unified',
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.25,  # Pushed lower to avoid date overlap
            xanchor="center",
            x=0.5
        ),
        margin=dict(l=10, r=10, t=50, b=100),  # Optimized for mobile
        autosize=True,
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_portfolio_allocation_chart(db, portfolio_ids, title="Portfolio Allocation"):
    """
    Create portfolio allocation pie chart showing value distribution across portfolios.
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        title: Chart title
    
    Returns:
        plotly.graph_objects.Figure
    """
    if not portfolio_ids:
        fig = go.Figure()
        fig.add_annotation(
            text="No portfolios selected",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Get latest snapshot for EACH portfolio individually (may be different dates)
    latest_snapshots = []
    for pid in portfolio_ids:
        latest_snap = db.query(Snapshot).filter(
            Snapshot.portfolio_id == pid
        ).order_by(Snapshot.snapshot_date.desc()).first()
        
        if latest_snap:
            latest_snapshots.append(latest_snap)
    
    if not latest_snapshots:
        fig = go.Figure()
        fig.add_annotation(
            text="No snapshot data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Get portfolio names and types
    portfolios_loader = get_portfolios_loader()
    portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
    portfolio_types = {p['id']: p.get('type', 'other') for p in portfolios_loader.get_portfolios()}
    
    # Color mapping by portfolio type (matching Tier 1 KPI cards)
    type_colors = {
        'securities': '#059669',
        'commodities': '#f59e0b',
        'crypto': '#4f46e5',
        'other': '#64748b'
    }
    
    def generate_color_shades(base_color, count, index):
        """Generate color shades for multiple portfolios of the same type."""
        if count == 1:
            return base_color
        
        # Convert hex to RGB
        base_color = base_color.lstrip('#')
        r, g, b = int(base_color[0:2], 16), int(base_color[2:4], 16), int(base_color[4:6], 16)
        
        # Generate shade variation (lighter to darker)
        # For slate, vary from lighter (#94a3b8) to darker (#475569)
        factor = 0.7 + (index * 0.6 / max(count - 1, 1))  # Range from 0.7 to 1.3
        r = int(min(255, max(0, r * factor)))
        g = int(min(255, max(0, g * factor)))
        b = int(min(255, max(0, b * factor)))
        
        return f'#{r:02x}{g:02x}{b:02x}'
    
    # Sort snapshots by portfolio_id to ensure consistent ordering
    latest_snapshots = sorted(latest_snapshots, key=lambda s: s.portfolio_id)
    
    # Count portfolios by type to determine if we need color variations
    type_counts = {}
    type_indices = {}
    for snap in latest_snapshots:
        ptype = portfolio_types.get(snap.portfolio_id, 'other')
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        type_indices[ptype] = 0
    
    # Build labels, values, and colors with shade variations
    labels = []
    values = []
    colors = []
    for snap in latest_snapshots:
        portfolio_name = portfolio_names.get(snap.portfolio_id, f"Portfolio {snap.portfolio_id}")
        value = float(snap.total_value_base or Decimal('0'))
        if value > 0:  # Only include portfolios with positive value
            labels.append(portfolio_name)
            values.append(value)
            ptype = portfolio_types.get(snap.portfolio_id, 'other')
            base_color = type_colors.get(ptype, '#64748b')
            
            # Apply shade variation if multiple portfolios of same type
            count = type_counts.get(ptype, 1)
            index = type_indices[ptype]
            colors.append(generate_color_shades(base_color, count, index))
            type_indices[ptype] += 1
    
    if not values:
        fig = go.Figure()
        fig.add_annotation(
            text="No portfolio values available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Calculate total for center annotation
    total_value = sum(values)
    base_currency = get_global_base_currency()
    
    # Optimized version: percent inside, legend below
    # This works well on both mobile (big pie) and desktop (clean layout)
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        textinfo='percent',
        texttemplate='%{percent:.1%}',
        textposition='inside',
        textfont=dict(size=11, color='white'),
        hovertemplate='<b>%{label}</b><br>Value: %{value:,.0f} ' + base_currency + '<br>Percent: %{percent}<extra></extra>',
        marker=dict(
            colors=colors,
            line=dict(width=0)
        ),
        sort=False,  # Maintain order by portfolio_id, don't sort by value
        direction='clockwise'  # Clockwise display for intuitive alignment with legend
    )])
    
    fig.update_layout(
        template="plotly_white",
        height=400,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.25,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
            itemwidth=30
        ),
        margin=dict(l=10, r=10, t=50, b=100),
        annotations=[dict(
            text=f"Total<br>{total_value:,.0f}<br>{base_currency}",
            x=0.5, y=0.5,
            font=dict(size=14, color="#333333"),
            showarrow=False
        )],
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_portfolio_invested_capital_allocation_chart(db, portfolio_ids, title="Invested Capital Allocation"):
    """
    Create portfolio allocation pie chart showing invested capital distribution across portfolios.
    Uses cache with fallback to live calculation.
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        title: Chart title
    
    Returns:
        plotly.graph_objects.Figure
    """
    if not portfolio_ids:
        fig = go.Figure()
        fig.add_annotation(
            text="No portfolios selected",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Try to get invested capital from cache first (fast path)
    from service.cache_service import get_cached_summary
    
    portfolio_data = []
    cache_miss = False
    
    for pid in portfolio_ids:
        cached = get_cached_summary(pid)
        if cached and cached.get('total_invested', 0) > 0:
            portfolio_data.append({
                'portfolio_id': pid,
                'invested': float(cached['total_invested'])
            })
        else:
            cache_miss = True
            break
    
    # Fallback to database query if cache miss
    if cache_miss or not portfolio_data:
        logger.debug("Cache miss for invested capital allocation, falling back to database query")
        portfolio_data = []
        
        for pid in portfolio_ids:
            # Get latest snapshot for this portfolio
            latest_snap = db.query(Snapshot).filter(
                Snapshot.portfolio_id == pid
            ).order_by(Snapshot.snapshot_date.desc()).first()
            
            if latest_snap:
                invested = float(calculate_net_invested_capital(latest_snap))
                if invested > 0:
                    portfolio_data.append({
                        'portfolio_id': pid,
                        'invested': invested
                    })
    
    if not portfolio_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No invested capital data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Get portfolio names and types
    portfolios_loader = get_portfolios_loader()
    portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
    portfolio_types = {p['id']: p.get('type', 'other') for p in portfolios_loader.get_portfolios()}
    
    # Color mapping by portfolio type (matching Tier 1 KPI cards)
    type_colors = {
        'securities': '#059669',
        'commodities': '#f59e0b',
        'crypto': '#4f46e5',
        'other': '#64748b'
    }
    
    def generate_color_shades(base_color, count, index):
        """Generate color shades for multiple portfolios of the same type."""
        if count == 1:
            return base_color
        
        # Convert hex to RGB
        base_color = base_color.lstrip('#')
        r, g, b = int(base_color[0:2], 16), int(base_color[2:4], 16), int(base_color[4:6], 16)
        
        # Generate shade variation (lighter to darker)
        # For slate, vary from lighter (#94a3b8) to darker (#475569)
        factor = 0.7 + (index * 0.6 / max(count - 1, 1))  # Range from 0.7 to 1.3
        r = int(min(255, max(0, r * factor)))
        g = int(min(255, max(0, g * factor)))
        b = int(min(255, max(0, b * factor)))
        
        return f'#{r:02x}{g:02x}{b:02x}'
    
    # Sort portfolio_data by portfolio_id to ensure consistent ordering with market value chart
    portfolio_data = sorted(portfolio_data, key=lambda p: p['portfolio_id'])
    
    # Count portfolios by type to determine if we need color variations
    type_counts = {}
    type_indices = {}
    for item in portfolio_data:
        pid = item['portfolio_id']
        ptype = portfolio_types.get(pid, 'other')
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        type_indices[ptype] = 0
    
    # Build labels, values, and colors with shade variations
    labels = []
    values = []
    colors = []
    for item in portfolio_data:
        pid = item['portfolio_id']
        portfolio_name = portfolio_names.get(pid, f"Portfolio {pid}")
        invested = item['invested']
        if invested > 0:
            labels.append(portfolio_name)
            values.append(invested)
            ptype = portfolio_types.get(pid, 'other')
            base_color = type_colors.get(ptype, '#64748b')
            
            # Apply shade variation if multiple portfolios of same type
            count = type_counts.get(ptype, 1)
            index = type_indices[ptype]
            colors.append(generate_color_shades(base_color, count, index))
            type_indices[ptype] += 1
    
    if not values:
        fig = go.Figure()
        fig.add_annotation(
            text="No invested capital available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350, title=title)
        return fig
    
    # Calculate total for center annotation
    total_invested = sum(values)
    base_currency = get_global_base_currency()
    
    # Optimized version: percent inside, legend below
    # This works well on both mobile (big pie) and desktop (clean layout)
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        textinfo='percent',
        texttemplate='%{percent:.1%}',
        textposition='inside',
        textfont=dict(size=11, color='white'),
        hovertemplate='<b>%{label}</b><br>Invested: %{value:,.0f} ' + base_currency + '<br>Percent: %{percent}<extra></extra>',
        marker=dict(
            colors=colors,
            line=dict(width=0)
        ),
        sort=False,  # Maintain order by portfolio_id, don't sort by value
        direction='clockwise'  # Clockwise display for intuitive alignment with legend
    )])
    
    fig.update_layout(
        template="plotly_white",
        height=400,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.25,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
            itemwidth=30
        ),
        margin=dict(l=10, r=10, t=50, b=100),
        annotations=[dict(
            text=f"Total<br>{total_invested:,.0f}<br>{base_currency}",
            x=0.5, y=0.5,
            font=dict(size=14, color="#333333"),
            showarrow=False
        )],
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_allocation_chart(positions, base_currency=None):
    """
    Create asset allocation pie chart.
    
    Args:
        positions: List of Position objects or dicts (from cache)
        base_currency: Base currency for value display
    
    Returns:
        plotly.graph_objects.Figure
    """
    # Helper to get value from position (dict or object)
    def get_val(p, key, default=None):
        return p.get(key, default) if isinstance(p, dict) else getattr(p, key, default)
    
    if base_currency is None:
        base_currency = get_global_base_currency()
    
    if not positions:
        fig = go.Figure()
        fig.add_annotation(
            text="No positions",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig
    
    labels = [get_val(p, 'symbol') for p in positions]
    values = [float((get_val(p, 'quantity') or 0) * (get_val(p, 'current_price') or 0)) for p in positions]
    
    # Filter out zero/negative values
    filtered_data = [(l, v) for l, v in zip(labels, values) if v > 0]
    if not filtered_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No positions with value",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig
    
    labels, values = zip(*filtered_data)
    total_value = sum(values)
    
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        textinfo='label+percent',
        texttemplate='%{label}<br>%{percent:.1%}',
        textposition='outside',
        hovertemplate='<b>%{label}</b><br>Value: %{value:,.0f} ' + base_currency + '<br>Percent: %{percent}<extra></extra>',
        marker=dict(
            colors=['#059669', '#f59e0b', '#4f46e5', '#06b6d4', '#8b5cf6', '#ec4899', '#10b981', '#f97316', '#6366f1', '#14b8a6'],
            line=dict(width=0)
        )
    )])
    
    fig.update_layout(
        template="plotly_white",
        height=400,
        showlegend=False,
        margin=dict(l=10, r=10, t=50, b=100),
        annotations=[dict(
            text=f"Total<br>{total_value:,.0f}<br>{base_currency}",
            x=0.5, y=0.5,
            font=dict(size=14, color="#333333"),
            showarrow=False
        )],
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_normalized_performance_chart(db, portfolio_ids, date_range='1y'):
    """
    Create chart with all portfolios rebased to 100 at start of period.
    Allows fair comparison regardless of portfolio size.
    Also includes benchmark symbols for comparison.
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        date_range: Date range ('1m', '1y', '3y', '5y', 'all')
    
    Returns:
        plotly.graph_objects.Figure
    """
    from service.benchmark_service import (
        get_benchmark_symbols, 
        get_benchmark_normalized_series,
        get_benchmark_label,
        get_benchmark_securities_config,
        get_benchmark_crypto_config,
        calculate_benchmark_twr
    )
    
    today = date.today()
    if date_range == 'custom':
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            cutoff_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            today = datetime.strptime(to_str, '%Y-%m-%d').date()
        else:
            cutoff_date = today - timedelta(days=365)
    else:
        range_mapping = {
            '1m': today - timedelta(days=30),
            '3m': today - timedelta(days=90),
            '6m': today - timedelta(days=180),
            '1y': today - timedelta(days=365),
            '3y': today - timedelta(days=3*365),
            '5y': today - timedelta(days=5*365),
            'all': None
        }
        cutoff_date = range_mapping.get(date_range, range_mapping['1y'])
    
    fig = go.Figure()
    
    # Get portfolio names and types
    portfolios_loader = get_portfolios_loader()
    portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
    portfolio_types = {p['id']: p.get('type', 'other') for p in portfolios_loader.get_portfolios()}
    
    # Color mapping by portfolio type (matching Tier 1 KPI cards)
    type_colors = {
        'securities': '#059669',  # Emerald-600
        'commodities': '#f59e0b',  # Amber-500
        'crypto': '#4f46e5',  # Indigo-600
        'other': '#64748b'  # Slate-500
    }
    
    # Calculate 'All Investments' combined line using Aligned Aggregation
    # Use earliest available date if 'all', else cutoff_date
    start_date = cutoff_date if cutoff_date else date(2020, 1, 1)
    
    # Get aggregated series with forward-filling (excludes cash)
    agg_series = get_aligned_aggregate_series(db, start_date, portfolio_ids)
    
    if agg_series and len(agg_series) >= 2:
        all_dates = [d['date'] for d in agg_series]
        
        # Calculate NAV series (starts at 100)
        aggregate_nav = [100.0]
        
        for i in range(1, len(agg_series)):
            prev = agg_series[i-1]
            curr = agg_series[i]
            
            v_start = float(prev['total_value'])
            v_end = float(curr['total_value'])
            
            # Net Cash Flow = Change in Cumulative Cash Flows
            # (Curr_Dep - Prev_Dep) - (Curr_WD - Prev_WD)
            daily_net_flow = float(
                (curr['deposits'] - prev['deposits']) - 
                (curr['withdrawals'] - prev['withdrawals'])
            )
            
            if v_start > 0:
                # Chain-Linking formula: (End - Flow) / Start
                daily_growth = (v_end - daily_net_flow) / v_start
                aggregate_nav.append(aggregate_nav[-1] * daily_growth)
            elif v_end > 0 and daily_net_flow > 0:
                # Initial Funding - No performance change
                aggregate_nav.append(aggregate_nav[-1])
            else:
                aggregate_nav.append(aggregate_nav[-1])
        
        # Add 'All Investments' line
        fig.add_trace(go.Scatter(
            x=all_dates,
            y=aggregate_nav,
            mode='lines',
            name='All Investments',
            line=dict(color='#64748b', width=2.5, dash='solid'),  # Slate color
            hovertemplate='%{y:.2f}<extra></extra>'
        ))
    
    # Add individual portfolio lines
    for idx, pid in enumerate(portfolio_ids):
        # Get snapshots for this portfolio
        query = db.query(Snapshot).filter(Snapshot.portfolio_id == pid)
        if cutoff_date:
            query = query.filter(Snapshot.snapshot_date >= cutoff_date)
        query = query.filter(Snapshot.snapshot_date <= today)
        snapshots = query.order_by(Snapshot.snapshot_date).all()
        
        if len(snapshots) < 2:
            continue
        
        # Use NAV price for normalized comparison (excludes cashflow effects)
        dates = [s.snapshot_date for s in snapshots]
        nav_prices = [float(s.nav_price) if s.nav_price else 100.0 for s in snapshots]
        
        # Normalize to 100 at start of period
        if nav_prices and nav_prices[0] != 0:
            first_nav = nav_prices[0]
            nav_normalized = [(nav / first_nav) * 100 for nav in nav_prices]
        else:
            nav_normalized = nav_prices
        
        ptype = portfolio_types.get(pid, 'other')
        color = type_colors.get(ptype, '#64748b')
        
        fig.add_trace(go.Scatter(
            x=dates,
            y=nav_normalized,
            mode='lines',
            name=portfolio_names.get(pid, f'Portfolio {pid}'),
            line=dict(color=color, width=2.5),
            hovertemplate='%{y:.2f}<extra></extra>'
        ))
    
    # Add benchmark lines - solid thin lines in black/grey
    start_date = cutoff_date if cutoff_date else date(2020, 1, 1)  # Default for 'all'
    
    # Securities benchmark (black)
    sec_config = get_benchmark_securities_config()
    if sec_config.get('symbol'):
        sec_symbol = sec_config['symbol']
        sec_label = sec_config.get('label', sec_symbol)
        benchmark_data = get_benchmark_normalized_series(sec_symbol, start_date, today)
        if benchmark_data:
            dates = [d for d, _ in benchmark_data]
            values = [v for _, v in benchmark_data]
            
            # Calculate TWR for tooltip
            sec_twr = calculate_benchmark_twr(sec_symbol, start_date, today)
            twr_str = f" ({sec_twr:+.1f}%)" if sec_twr is not None else ""
            
            fig.add_trace(go.Scatter(
                x=dates,
                y=values,
                mode='lines',
                name=f'{sec_label}{twr_str}',
                line=dict(color='#374151', width=1.5, dash='solid'),  # Grey-700 (matching risk/reward securities benchmark)
                hovertemplate=f'{sec_label}: %{{y:.2f}}<extra></extra>'
            ))
    
    # Crypto benchmark (grey)
    crypto_config = get_benchmark_crypto_config()
    if crypto_config.get('symbol'):
        crypto_symbol = crypto_config['symbol']
        crypto_label = crypto_config.get('label', crypto_symbol)
        benchmark_data = get_benchmark_normalized_series(crypto_symbol, start_date, today)
        if benchmark_data:
            dates = [d for d, _ in benchmark_data]
            values = [v for _, v in benchmark_data]
            
            # Calculate TWR for tooltip
            crypto_twr = calculate_benchmark_twr(crypto_symbol, start_date, today)
            twr_str = f" ({crypto_twr:+.1f}%)" if crypto_twr is not None else ""
            
            fig.add_trace(go.Scatter(
                x=dates,
                y=values,
                mode='lines',
                name=f'{crypto_label}{twr_str}',
                line=dict(color='#9ca3af', width=1.5, dash='solid'),  # Grey-400 (matching risk/reward crypto benchmark)
                hovertemplate=f'{crypto_label}: %{{y:.2f}}<extra></extra>'
            ))
    
    # Add baseline at 100
    
    # Composite benchmark (dark grey)
    from service.benchmark_service import get_composite_benchmark_normalized_series
    from apps.core.helpers import get_composite_benchmark_label
    composite_label = get_composite_benchmark_label()
    composite_data = get_composite_benchmark_normalized_series(start_date, today)
    if composite_data:
        c_dates = [d for d, _ in composite_data]
        c_values = [v for _, v in composite_data]
        
        # Calculate TWR for legend
        from service.benchmark_service import calculate_composite_benchmark_twr
        composite_twr = calculate_composite_benchmark_twr(start_date, today)
        twr_str = f" ({composite_twr:+.1f}%)" if composite_twr is not None else ""
        
        fig.add_trace(go.Scatter(
            x=c_dates,
            y=c_values,
            mode='lines',
            name=f'{composite_label}{twr_str}',
            line=dict(color='#4b5563', width=1.5, dash='solid'),  # Grey-600 (matching risk/reward composite benchmark)
            hovertemplate=f'{composite_label}: %{{y:.2f}}<extra></extra>'
        ))
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.5,)
    
    fig.update_layout(
        template="plotly_white",
        xaxis_title=None,
        xaxis=dict(
            tickfont=dict(size=10),
            tickangle=-45  # Angled for mobile readability
        ),
        yaxis_title=None,
        yaxis=dict(
            rangemode='tozero'
        ),
        annotations=[
            dict(
                x=0, y=1.08,
                xref="paper", yref="paper",
                text="NAV Growth",
                showarrow=False,
                font=dict(size=11, color="gray"),
                xanchor='center'
            )
        ],
        hovermode='x unified',
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=10, r=10, t=50, b=100),
        height=400,
        autosize=True,
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_single_portfolio_nav_chart(db, portfolio_id, date_range='1y'):
    """
    Create NAV growth chart for a single portfolio.
    Shows NAV price development over time, normalized to 100 at start of period.
    Also includes benchmark symbols for comparison.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        date_range: Date range ('3m', '1y', '3y', '5y', 'all', 'custom')
    
    Returns:
        plotly.graph_objects.Figure
    """
    from service.benchmark_service import (
        get_benchmark_normalized_series,
        get_benchmark_securities_config,
        get_benchmark_crypto_config,
        calculate_benchmark_twr
    )
    
    today = date.today()
    if date_range == 'custom':
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            cutoff_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            today = datetime.strptime(to_str, '%Y-%m-%d').date()
        else:
            cutoff_date = today - timedelta(days=365)
    else:
        range_mapping = {
            '3m': today - timedelta(days=90),
            '6m': today - timedelta(days=180),
            '1y': today - timedelta(days=365),
            '3y': today - timedelta(days=3*365),
            '5y': today - timedelta(days=5*365),
            'all': None
        }
        cutoff_date = range_mapping.get(date_range, range_mapping['1y'])
    
    # Get snapshots for this portfolio
    # First get all snapshots, then filter by cutoff_date if needed
    # This ensures we start from portfolio's first snapshot, not before
    query = db.query(Snapshot).filter(Snapshot.portfolio_id == portfolio_id)
    query = query.filter(Snapshot.snapshot_date <= today)
    snapshots = query.order_by(Snapshot.snapshot_date).all()
    
    # If cutoff_date is specified, filter to snapshots >= cutoff_date
    if cutoff_date and snapshots:
        snapshots = [s for s in snapshots if s.snapshot_date >= cutoff_date]
    
    if len(snapshots) < 2:
        fig = go.Figure()
        fig.add_annotation(
            text="Insufficient NAV data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=400)
        return fig
    
    # Get portfolio name and type
    portfolios_loader = get_portfolios_loader()
    portfolio_name = next((p['name'] for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), f'Portfolio {portfolio_id}')
    portfolio_config = next((p for p in portfolios_loader.get_portfolios() if p['id'] == portfolio_id), {})
    ptype = portfolio_config.get('type', 'other')
    
    # Color mapping by portfolio type
    type_colors = {
        'securities': '#059669',  # Emerald-600
        'commodities': '#f59e0b',  # Amber-500
        'crypto': '#4f46e5',  # Indigo-600
        'other': '#64748b'  # Slate-500
    }
    portfolio_color = type_colors.get(ptype, '#64748b')
    
    # Extract NAV prices
    dates = [s.snapshot_date for s in snapshots]
    nav_prices = [float(s.nav_price) if s.nav_price else 100.0 for s in snapshots]
    
    # Normalize to 100 at start of period (not inception)
    first_nav = nav_prices[0] if nav_prices and nav_prices[0] != 0 else 100.0
    nav_normalized = [(nav / first_nav) * 100 for nav in nav_prices]
    
    # Calculate growth percentage from start of period
    growth_pct = [(nav / first_nav - 1) * 100 for nav in nav_prices]
    
    fig = go.Figure()
    
    # NAV normalized line (rebased to 100 at start of period)
    fig.add_trace(go.Scatter(
        x=dates,
        y=nav_normalized,
        mode='lines',
        name=portfolio_name,
        line=dict(color=portfolio_color, width=2),  # Use portfolio type color
        hovertemplate='NAV: %{y:.2f}<br>Growth: %{customdata:+.2f}%<extra></extra>',
        customdata=growth_pct
    ))
    
    # Add benchmark lines - solid thin lines in black/grey
    # Use portfolio's first snapshot date as start, not cutoff_date
    start_date = dates[0] if dates else (cutoff_date if cutoff_date else date(2020, 1, 1))
    
    # Securities benchmark (black)
    sec_config = get_benchmark_securities_config()
    if sec_config.get('symbol'):
        sec_symbol = sec_config['symbol']
        sec_label = sec_config.get('label', sec_symbol)
        benchmark_data = get_benchmark_normalized_series(sec_symbol, start_date, today)
        if benchmark_data:
            b_dates = [d for d, _ in benchmark_data]
            b_values = [v for _, v in benchmark_data]
            
            # Calculate TWR for legend
            sec_twr = calculate_benchmark_twr(sec_symbol, start_date, today)
            twr_str = f" ({sec_twr:+.1f}%)" if sec_twr is not None else ""
            
            fig.add_trace(go.Scatter(
                x=b_dates,
                y=b_values,
                mode='lines',
                name=f'{sec_label}{twr_str}',
                line=dict(color='#374151', width=2, dash='solid'),  # Uniform width 2
                hovertemplate=f'{sec_label}: %{{y:.2f}}<extra></extra>'
            ))
    
    # Crypto benchmark (grey)
    crypto_config = get_benchmark_crypto_config()
    if crypto_config.get('symbol'):
        crypto_symbol = crypto_config['symbol']
        crypto_label = crypto_config.get('label', crypto_symbol)
        benchmark_data = get_benchmark_normalized_series(crypto_symbol, start_date, today)
        if benchmark_data:
            b_dates = [d for d, _ in benchmark_data]
            b_values = [v for _, v in benchmark_data]
            
            # Calculate TWR for legend
            crypto_twr = calculate_benchmark_twr(crypto_symbol, start_date, today)
            twr_str = f" ({crypto_twr:+.1f}%)" if crypto_twr is not None else ""
            
            fig.add_trace(go.Scatter(
                x=b_dates,
                y=b_values,
                mode='lines',
                name=f'{crypto_label}{twr_str}',
                line=dict(color='#9ca3af', width=2, dash='solid'),  # Uniform width 2
                hovertemplate=f'{crypto_label}: %{{y:.2f}}<extra></extra>'
            ))
    
    # Composite benchmark (dark grey)
    from service.benchmark_service import get_composite_benchmark_normalized_series
    from apps.core.helpers import get_composite_benchmark_label
    composite_label = get_composite_benchmark_label()
    composite_data = get_composite_benchmark_normalized_series(start_date, today)
    if composite_data:
        c_dates = [d for d, _ in composite_data]
        c_values = [v for _, v in composite_data]
        
        # Calculate TWR for legend
        from service.benchmark_service import calculate_composite_benchmark_twr
        composite_twr = calculate_composite_benchmark_twr(start_date, today)
        twr_str = f" ({composite_twr:+.1f}%)" if composite_twr is not None else ""
        
        fig.add_trace(go.Scatter(
            x=c_dates,
            y=c_values,
            mode='lines',
            name=f'{composite_label}{twr_str}',
            line=dict(color='#4b5563', width=2, dash='solid'),  # Grey-600, same width as others
            hovertemplate=f'{composite_label}: %{{y:.2f}}<extra></extra>'
        ))

    
    # Add baseline at 100 (start of period)
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.5,
                  annotation_text="Period Start (100)", annotation_position="bottom right")
    
    # Calculate y-axis range to ensure 100 baseline is visible
    all_values = nav_normalized.copy()
    for cfg in [sec_config, crypto_config]:
        if cfg.get('symbol'):
            bdata = get_benchmark_normalized_series(cfg['symbol'], start_date, today)
            if bdata:
                all_values.extend([v for _, v in bdata])
    
    min_val = min(all_values) if all_values else 100
    max_val = max(all_values) if all_values else 100
    y_min = min(min_val * 0.95, 95)  # Start slightly below lowest point, but at least 95
    y_max = max_val * 1.05  # Add 5% padding at top
    
    fig.update_layout(
        template="plotly_white",
        xaxis_title=None,
        xaxis=dict(
            tickfont=dict(size=10),
            tickangle=-45  # Angled for mobile readability
        ),
        yaxis_title=None,
        yaxis=dict(
            range=[y_min, y_max]
        ),
        annotations=[
            dict(
                x=0, y=1.08,
                xref="paper", yref="paper",
                text="NAV Price",
                showarrow=False,
                font=dict(size=11, color="gray"),
                xanchor='center'
            )
        ],
        hovermode='x unified',
        showlegend=True,  # Show legend with benchmarks
        legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=10, r=10, t=50, b=100),
        height=400,
        autosize=True,
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig


def create_position_risk_reward_scatter(db, portfolio_id, date_range='1y'):
    """
    Create scatter plot showing volatility vs return for individual positions within a portfolio.
    Uses market data to calculate price-based returns and volatility for each holding.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID to analyze
        date_range: Date range for calculations ('3m', '1y', '3y', '5y', 'all', 'custom')
    
    Returns:
        tuple: (plotly.graph_objects.Figure, list of position stats dicts)
    """
    from datetime import date, timedelta, datetime
    from decimal import Decimal
    import math
    
    # Calculate date range
    today = date.today()
    if date_range == 'custom':
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            start_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(to_str, '%Y-%m-%d').date()
        else:
            start_date = today - timedelta(days=365)
            end_date = today
    else:
        range_mapping = {'3m': 90, '1y': 365, '3y': 3*365, '5y': 5*365, 'all': 3650}
        days_back = range_mapping.get(date_range, 365)
        start_date = today - timedelta(days=days_back)
        end_date = today
    
    # Get open positions for this portfolio
    positions = db.query(Position).filter(
        Position.portfolio_id == portfolio_id,
        Position.quantity > 0
    ).all()
    
    if not positions:
        fig = go.Figure()
        fig.add_annotation(
            text="No open positions in this portfolio",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig, []
    
    position_stats = []
    
    for pos in positions:
        symbol = pos.symbol_normalized or pos.symbol
        
        # Get market data for this symbol within the date range
        market_data = db.query(MarketData).filter(
            MarketData.symbol == symbol,
            MarketData.as_of_date >= start_date,
            MarketData.as_of_date <= end_date
        ).order_by(MarketData.as_of_date).all()
        
        # If not enough data in range, try to get earliest available
        if len(market_data) < 2:
            market_data = db.query(MarketData).filter(
                MarketData.symbol == symbol
            ).order_by(MarketData.as_of_date).limit(365).all()
        
        if len(market_data) < 2:
            logger.debug(f"Insufficient market data for {symbol}")
            continue
        
        # Calculate daily returns
        prices = [float(md.price) for md in market_data]
        dates_md = [md.as_of_date for md in market_data]
        
        if not prices or prices[0] == 0:
            continue
        
        # Calculate daily returns
        daily_returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = (prices[i] / prices[i-1]) - 1
                daily_returns.append(ret)
        
        if len(daily_returns) < 10:
            logger.debug(f"Not enough daily returns for {symbol}: {len(daily_returns)}")
            continue
        
        # Calculate TWR (total return for period)
        twr = (prices[-1] / prices[0] - 1) * 100
        
        # Calculate actual days in data
        actual_days = (dates_md[-1] - dates_md[0]).days if hasattr(dates_md[0], 'day') else (dates_md[-1].date() - dates_md[0].date()).days
        if actual_days < 1:
            actual_days = 1
        
        # Annualize TWR
        twr_factor = 1 + (twr / 100)
        annualization_factor = 365 / actual_days
        twr_annualized = (pow(abs(twr_factor), annualization_factor) - 1) * 100
        if twr_factor < 0:
            twr_annualized = -twr_annualized
        
        # Calculate annualized volatility (std dev of daily returns * sqrt(252))
        if len(daily_returns) > 1:
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            daily_volatility = math.sqrt(variance)
            annualized_volatility = daily_volatility * math.sqrt(252) * 100
        else:
            annualized_volatility = 0
        
        # Calculate Sharpe ratio (assuming 4% risk-free rate, matching zone display)
        rf_rate = 4.0
        if annualized_volatility > 0:
            sharpe_ratio = (twr_annualized - rf_rate) / annualized_volatility
        else:
            sharpe_ratio = 0
        
        # Get position value for sizing
        position_value = float(pos.quantity * Decimal(str(prices[-1]))) if prices else 0
        
        position_stats.append({
            'symbol': symbol,
            'twr_annualized': twr_annualized,
            'volatility': annualized_volatility,
            'sharpe_ratio': sharpe_ratio,
            'position_value': position_value,
            'quantity': float(pos.quantity),
            'current_price': prices[-1] if prices else 0,
            'data_days': actual_days
        })
    
    if not position_stats:
        fig = go.Figure()
        fig.add_annotation(
            text="Insufficient market data for position analysis.\nMarket data may need to be synced.",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#888"),
            align="center"
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig, []
    
    # Create scatter plot
    fig = go.Figure()
    
    symbols = [ps['symbol'] for ps in position_stats]
    volatilities = [ps['volatility'] for ps in position_stats]
    returns = [ps['twr_annualized'] for ps in position_stats]
    values = [ps['position_value'] for ps in position_stats]
    sharpes = [ps['sharpe_ratio'] for ps in position_stats]
    
    # Scale marker sizes based on position values
    if values:
        min_val = min(values) if min(values) > 0 else 1
        max_val = max(values) if max(values) > min_val else min_val * 2
        marker_sizes = [15 + 25 * (val - min_val) / (max_val - min_val) for val in values]
    else:
        marker_sizes = [25] * len(symbols)
    
    risk_free_rate = 4.0
    
    # Find earliest actual market data date for benchmark alignment
    earliest_data_date = None
    for ps in position_stats:
        # Get first market data date for this symbol
        first_md = db.query(MarketData).filter(
            MarketData.symbol == ps['symbol'],
            MarketData.as_of_date >= start_date,
            MarketData.as_of_date <= end_date
        ).order_by(MarketData.as_of_date).first()
        if first_md:
            md_date = first_md.as_of_date.date() if isinstance(first_md.as_of_date, datetime) else first_md.as_of_date
            if earliest_data_date is None or md_date < earliest_data_date:
                earliest_data_date = md_date
    
    # Use earliest actual data date for benchmarks, or fall back to start_date
    benchmark_start_date = earliest_data_date if earliest_data_date else start_date
    
    # Get benchmark data using aligned start date
    from service.benchmark_service import get_benchmark_risk_reward_data
    benchmark_data = get_benchmark_risk_reward_data(benchmark_start_date, end_date)
    
    # Calculate axis ranges with padding
    max_vol_data = max(volatilities) if volatilities else 50
    if benchmark_data:
        max_vol_data = max(max_vol_data, max(b['volatility'] for b in benchmark_data))
    x_range_max = max(max_vol_data * 1.2, 5)  # Add 20% padding, at least 5% range
    
    min_ret_data = min(returns) if returns else -10
    max_ret_data = max(returns) if returns else 50
    if benchmark_data:
        min_ret_data = min(min_ret_data, min(b['twr_annualized'] for b in benchmark_data))
        max_ret_data = max(max_ret_data, max(b['twr_annualized'] for b in benchmark_data))
    y_range_min = min(min_ret_data * 1.2 if min_ret_data < 0 else 0, -5)
    y_range_max = max(max_ret_data * 1.2, 5)
    
    max_vol = x_range_max
    max_ret = y_range_max
    min_ret = y_range_min
    
    # Draw efficiency lines through benchmarks (from risk-free rate)
    if benchmark_data:
        for b in benchmark_data:
            if b['volatility'] > 0:
                # Calculate slope: (return - risk_free) / volatility
                slope = (b['twr_annualized'] - risk_free_rate) / b['volatility']
                
                # Line from (0, risk_free_rate) through benchmark to edge
                end_y = slope * max_vol + risk_free_rate
                
                # Choose color based on benchmark type
                if b['benchmark_type'] == 'securities':
                    line_color = '#1f2937'  # Dark grey for securities
                elif b['benchmark_type'] == 'crypto':
                    line_color = '#6b7280'  # Light grey for crypto
                elif b['benchmark_type'] == 'composite':
                    line_color = '#f59e0b'  # Amber for composite
                else:
                    line_color = '#9ca3af'  # Default grey
                
                fig.add_trace(go.Scatter(
                    x=[0, max_vol],
                    y=[risk_free_rate, end_y],
                    mode='lines',
                    name=f"{b['label']} Efficiency (Sharpe {b['sharpe_ratio']:.2f})",
                    line=dict(color=line_color, width=1.5, dash='solid'),
                    showlegend=True,
                    hoverinfo='skip'
                ))
    
    # Zone backgrounds based on Sharpe ratios (matching all portfolios view)
    # Zone 1: Alpha Zone (Above Sharpe 1.0) - Elite efficiency
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{1.0 * max_vol + risk_free_rate} L {max_vol},{max_ret} L 0,{max_ret} Z",
        fillcolor="rgba(34, 197, 94, 0.08)",  # Green tint - Elite
        line=dict(width=0),
        layer="below"
    )
    
    # Zone 2: Efficiency Zone (Between Sharpe 0.5 and 1.0)
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{0.5 * max_vol + risk_free_rate} L {max_vol},{1.0 * max_vol + risk_free_rate} Z",
        fillcolor="rgba(234, 179, 8, 0.08)",  # Yellow tint
        line=dict(width=0),
        layer="below"
    )
    
    # Zone 3: Danger Zone (Below Sharpe 0.5)
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{0.5 * max_vol + risk_free_rate} L {max_vol},{min_ret} L 0,{min_ret} Z",
        fillcolor="rgba(239, 68, 68, 0.08)",  # Red tint
        line=dict(width=0),
        layer="below"
    )
    
    # Add benchmark markers - grey round dots (matching all portfolios view)
    if benchmark_data:
        for b in benchmark_data:
            # Color coding by benchmark type
            if b['benchmark_type'] == 'securities':
                marker_color = '#374151'  # Dark grey for securities
            elif b['benchmark_type'] == 'crypto':
                marker_color = '#9ca3af'  # Light grey for crypto
            elif b['benchmark_type'] == 'composite':
                marker_color = '#4b5563'  # Grey-600 for composite
            else:
                marker_color = '#9ca3af'  # Default grey
            
            fig.add_trace(go.Scatter(
                x=[b['volatility']],
                y=[b['twr_annualized']],
                mode='markers+text',
                text=[b['label']],
                textposition='top center',
                textfont=dict(size=10, color='#666'),
                marker=dict(
                    size=12,
                    color=marker_color,
                    symbol='circle',
                    line=dict(width=2, color='white')
                ),
                showlegend=False,
                hovertemplate=f"<b>{b['label']}</b><br>Volatility: {b['volatility']:.1f}%<br>TWR (Ann.): {b['twr_annualized']:.1f}%<br>Sharpe: {b['sharpe_ratio']:.2f}<extra></extra>"
            ))
    
    # Color by Sharpe ratio
    colors = []
    for sharpe in sharpes:
        if sharpe > 1.0:
            colors.append('#22c55e')  # Green - excellent
        elif sharpe > 0.5:
            colors.append('#eab308')  # Yellow - decent
        else:
            colors.append('#ef4444')  # Red - poor
    
    # Add scatter points
    fig.add_trace(go.Scatter(
        x=volatilities,
        y=returns,
        mode='markers+text',
        text=symbols,
        textposition='top center',
        marker=dict(
            size=marker_sizes,
            color=colors,
            line=dict(width=2, color='white')
        ),
        showlegend=False,
        hovertemplate='<b>%{text}</b><br>Return: %{y:.1f}%<br>Volatility: %{x:.1f}%<br>Sharpe: %{customdata:.2f}<extra></extra>',
        customdata=sharpes
    ))
    
    # Add grey dotted line at y=0 (zero return line)
    fig.add_hline(y=0, line_dash="dot", line_color="#d1d5db", line_width=1.5, layer="below")
    
    fig.update_layout(
        template="plotly_white",
        xaxis_title=None,
        xaxis=dict(
            range=[0, x_range_max],
            autorange=False,
            fixedrange=False,
            tickfont=dict(size=10)
        ),
        yaxis_title=None,
        yaxis=dict(
            range=[y_range_min, y_range_max],
            autorange=False,
            fixedrange=False,
            zeroline=False  # Disable built-in zeroline, using custom one instead
        ),
        annotations=[
            dict(
                x=0, y=1.08,
                xref="paper", yref="paper",
                text="Return (Ann. %)",
                showarrow=False,
                font=dict(size=11, color="gray"),
                xanchor='left'
            ),
            dict(
                x=0.5, y=-0.18,
                xref="paper", yref="paper",
                text="Volatility (Ann. %)",
                showarrow=False,
                font=dict(size=10, color="gray"),
                xanchor='center'
            )
        ],
        hovermode='closest',
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=10, r=10, t=50, b=100),
        height=400,
        autosize=True,
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig, position_stats


def create_risk_reward_scatter(db, portfolio_ids, date_range='1y'):
    """
    Create scatter plot showing volatility (annualized std dev) vs return (TWR annualized).
    Ideal portfolios appear in top-left (high return, low volatility).
    Includes benchmark symbols with efficiency lines drawn through them.
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        date_range: Date range for period-specific calculations ('3m', '1y', '3y', '5y', 'all', 'custom')
    
    Returns:
        plotly.graph_objects.Figure
    """
    from datetime import date, timedelta, datetime
    from nicegui import app
    from apps.core.helpers import _calculate_twr_between_dates, _calculate_volatility_between_dates
    from service.benchmark_service import get_benchmark_risk_reward_data, get_risk_free_rate
    
    # Calculate date range and check minimum period
    today = date.today()
    if date_range == 'custom':
        from_str = app.storage.user.get('chart_custom_from')
        to_str = app.storage.user.get('chart_custom_to')
        if from_str and to_str:
            start_date = datetime.strptime(from_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(to_str, '%Y-%m-%d').date()
            days_back = (end_date - start_date).days
        else:
            days_back = 365
            start_date = today - timedelta(days=days_back)
            end_date = today
    else:
        range_mapping = {
            '3m': 90,
            '6m': 180,
            '1y': 365,
            '3y': 3*365,
            '5y': 5*365,
            'all': 3650
        }
        days_back = range_mapping.get(date_range, 365)
        start_date = today - timedelta(days=days_back)
        end_date = today
    
    # Check minimum period requirement (3 months = ~90 days)
    if days_back < 90:
        fig = go.Figure()
        fig.add_annotation(
            text="Risk/Reward analysis requires at least 3 months of data.\nPlease select a longer time period.",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#888"),
            align="center"
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig
    
    portfolios_loader = get_portfolios_loader()
    portfolio_names = {p['id']: p['name'] for p in portfolios_loader.get_portfolios()}
    
    # Color mapping by portfolio type (matching KPI cards)
    portfolio_types = {p['id']: p.get('type', 'other') for p in portfolios_loader.get_portfolios()}
    type_colors = {
        'securities': '#059669',  # Emerald (matches securities tier1)
        'commodities': '#f59e0b',  # Amber
        'crypto': '#4f46e5',  # Indigo
        'other': '#64748b'  # Slate
    }
    
    names = []
    twrs_annualized = []
    volatilities = []
    sharpe_ratios = []
    colors = []
    portfolio_values = []
    
    risk_free_rate = get_risk_free_rate()
    
    # Find the earliest actual snapshot date across all portfolios for benchmark alignment
    earliest_snapshot_date = None
    for pid in portfolio_ids:
        first_snap = db.query(Snapshot).filter(
            Snapshot.portfolio_id == pid,
            Snapshot.snapshot_date >= start_date,
            Snapshot.snapshot_date <= end_date
        ).order_by(Snapshot.snapshot_date).first()
        if first_snap:
            if earliest_snapshot_date is None or first_snap.snapshot_date < earliest_snapshot_date:
                earliest_snapshot_date = first_snap.snapshot_date
    
    # Use earliest actual snapshot date for benchmarks, or fall back to start_date
    benchmark_start_date = earliest_snapshot_date if earliest_snapshot_date else start_date
    
    for pid in portfolio_ids:
        try:
            # Get actual snapshot date range for this portfolio
            snapshots = db.query(Snapshot).filter(
                Snapshot.portfolio_id == pid,
                Snapshot.snapshot_date >= start_date,
                Snapshot.snapshot_date <= end_date
            ).order_by(Snapshot.snapshot_date).all()
            
            if len(snapshots) < 2:
                continue
            
            # Use actual first and last snapshot dates
            actual_start_date = snapshots[0].snapshot_date
            actual_end_date = snapshots[-1].snapshot_date
            actual_days = (actual_end_date - actual_start_date).days
            
            if actual_days < 1:
                actual_days = 1
            
            # Calculate period-specific TWR using actual dates
            twr_period = _calculate_twr_between_dates(db, pid, actual_start_date, actual_end_date)
            
            # Annualize TWR
            if twr_period is not None:
                twr_factor = 1 + (twr_period / 100)
                annualization_factor = 365 / actual_days
                twr_annualized = (pow(twr_factor, annualization_factor) - 1) * 100
            else:
                continue
            
            # Calculate period-specific annualized volatility
            volatility = _calculate_volatility_between_dates(db, pid, actual_start_date, actual_end_date)
            if volatility is None:
                continue
            
            # Calculate Sharpe ratio
            sharpe = (twr_annualized - risk_free_rate) / volatility if volatility > 0 else 0
            
            names.append(portfolio_names.get(pid, f'Portfolio {pid}'))
            twrs_annualized.append(twr_annualized)
            volatilities.append(volatility)
            sharpe_ratios.append(round(sharpe, 2))
            colors.append(type_colors.get(portfolio_types.get(pid, 'other'), '#64748b'))
            
            # Get latest portfolio value for sizing
            latest_snapshot = db.query(Snapshot).filter(
                Snapshot.portfolio_id == pid
            ).order_by(Snapshot.snapshot_date.desc()).first()
            value = float(latest_snapshot.total_value_base) if latest_snapshot else 100000
            portfolio_values.append(value)
        except Exception as e:
            logger.warning(f"Could not calculate risk/reward for portfolio {pid}: {e}")
            continue
    
    if not names:
        fig = go.Figure()
        fig.add_annotation(
            text="No portfolio data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color="#888")
        )
        fig.update_layout(template="plotly_white", height=350)
        return fig
    
    fig = go.Figure()
    
    # Scale marker sizes based on portfolio values (15-35 range for subtle difference)
    if portfolio_values:
        min_val = min(portfolio_values)
        max_val = max(portfolio_values)
        if max_val > min_val:
            marker_sizes = [15 + 20 * (val - min_val) / (max_val - min_val) for val in portfolio_values]
        else:
            marker_sizes = [25] * len(portfolio_values)
    else:
        marker_sizes = [25] * len(names)
    
    # Get benchmark data using the earliest actual snapshot date for proper alignment
    # This ensures benchmarks are calculated over the same period as the actual portfolio data
    benchmark_data = get_benchmark_risk_reward_data(benchmark_start_date, end_date)
    
    # Calculate axis ranges with padding
    max_vol_data = max(volatilities) if volatilities else 10
    if benchmark_data:
        max_vol_data = max(max_vol_data, max(b['volatility'] for b in benchmark_data))
    x_range_max = max(max_vol_data * 1.2, 5)  # Add 20% padding, at least 5% range
    
    min_twr = min(twrs_annualized) if twrs_annualized else -10
    max_twr = max(twrs_annualized) if twrs_annualized else 10
    if benchmark_data:
        min_twr = min(min_twr, min(b['twr_annualized'] for b in benchmark_data))
        max_twr = max(max_twr, max(b['twr_annualized'] for b in benchmark_data))
    y_range_min = min(min_twr * 1.2 if min_twr < 0 else 0, -5)
    y_range_max = max(max_twr * 1.2, 5)
    
    max_vol = x_range_max
    max_ret = y_range_max
    min_ret = y_range_min
    
    # Draw efficiency lines through benchmarks (from risk-free rate)
    if benchmark_data:
        for b in benchmark_data:
            if b['volatility'] > 0:
                # Calculate slope: (return - risk_free) / volatility
                slope = (b['twr_annualized'] - risk_free_rate) / b['volatility']
                
                # Line from (0, risk_free_rate) through benchmark to edge
                end_y = slope * max_vol + risk_free_rate
                
                # Choose color based on benchmark type
                if b['benchmark_type'] == 'securities':
                    line_color = '#1f2937'  # Dark grey for securities
                elif b['benchmark_type'] == 'crypto':
                    line_color = '#6b7280'  # Light grey for crypto
                elif b['benchmark_type'] == 'composite':
                    line_color = '#4b5563'  # Grey-600 for composite
                else:
                    line_color = '#9ca3af'  # Default grey
                
                fig.add_trace(go.Scatter(
                    x=[0, max_vol],
                    y=[risk_free_rate, end_y],
                    mode='lines',
                    name=f"{b['label']} Efficiency (Sharpe {b['sharpe_ratio']:.2f})",
                    line=dict(color=line_color, width=1.5, dash='solid'),
                    showlegend=True,
                    hoverinfo='skip'
                ))
    
    # Zone backgrounds based on Sharpe ratios
    # Zone 1: Alpha Zone (Above Sharpe 1.0) - Elite efficiency
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{1.0 * max_vol + risk_free_rate} L {max_vol},{max_ret} L 0,{max_ret} Z",
        fillcolor="rgba(34, 197, 94, 0.08)",  # Green tint - Elite
        line=dict(width=0),
        layer="below"
    )
    
    # Zone 2: Efficiency Zone (Between Sharpe 0.5 and 1.0)
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{0.5 * max_vol + risk_free_rate} L {max_vol},{1.0 * max_vol + risk_free_rate} Z",
        fillcolor="rgba(234, 179, 8, 0.08)",  # Yellow tint
        line=dict(width=0),
        layer="below"
    )
    
    # Zone 3: Danger Zone (Below Sharpe 0.5)
    fig.add_shape(
        type="path",
        path=f"M 0,{risk_free_rate} L {max_vol},{0.5 * max_vol + risk_free_rate} L {max_vol},{min_ret} L 0,{min_ret} Z",
        fillcolor="rgba(239, 68, 68, 0.08)",  # Red tint
        line=dict(width=0),
        layer="below"
    )
    
    # Add portfolio markers with Sharpe ratio in tooltip
    fig.add_trace(go.Scatter(
        x=volatilities,
        y=twrs_annualized,
        mode='markers+text',
        text=names,
        textposition='top center',
        textfont=dict(size=10),
        marker=dict(
            size=marker_sizes,
            color=colors,
            line=dict(width=2, color='white')
        ),
        customdata=sharpe_ratios,
        showlegend=False,
        hovertemplate='<b>%{text}</b><br>Volatility: %{x:.1f}%<br>TWR (Ann.): %{y:.1f}%<br>Sharpe: %{customdata:.2f}<extra></extra>'
    ))
    
    # Calculate and add 'All Investments' combined marker
    # Use Aligned Aggregation (excl. Cash) - professional fund accounting approach
    try:
        # Get aggregated series using the same start date as benchmarks for consistency
        agg_series = get_aligned_aggregate_series(db, benchmark_start_date, portfolio_ids)
        
        if agg_series and len(agg_series) >= 2:
            import math
            
            # Calculate daily returns
            daily_returns = []
            
            for i in range(1, len(agg_series)):
                prev = agg_series[i-1]
                curr = agg_series[i]
                
                v_start = float(prev['total_value'])
                v_end = float(curr['total_value'])
                
                # Net Cash Flow = Change in Cumulative Cash Flows
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
                # Check actual days for annualization
                actual_days = (agg_series[-1]['date'] - agg_series[0]['date']).days
                if actual_days < 1:
                    actual_days = 1
                
                # Geometric mean of daily returns, annualized
                cumulative_return = 1.0
                for r in daily_returns:
                    cumulative_return *= (1 + r)
                
                twr_factor = cumulative_return
                annualization_factor = 365 / actual_days
                combined_twr = (pow(twr_factor, annualization_factor) - 1) * 100
                
                # Calculate annualized volatility
                mean_return = sum(daily_returns) / len(daily_returns)
                variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
                daily_volatility = math.sqrt(variance)
                combined_volatility = daily_volatility * math.sqrt(365) * 100
                
                # Calculate Sharpe
                combined_sharpe = (combined_twr - risk_free_rate) / combined_volatility if combined_volatility > 0 else 0
                
                # Update axis ranges if needed
                if combined_volatility > max_vol_data:
                    x_range_max = combined_volatility * 1.2
                if combined_twr > max_twr:
                    y_range_max = combined_twr * 1.2
                elif combined_twr < min_twr:
                    y_range_min = combined_twr * 1.2
                
                # Add 'All Investments' marker - slate diamond, fixed size
                fig.add_trace(go.Scatter(
                    x=[combined_volatility],
                    y=[combined_twr],
                    mode='markers+text',
                    text=['All Investments'],
                    textposition='top center',
                    textfont=dict(size=10, color='#64748b'),
                    marker=dict(
                        size=18,
                        color='#64748b',  # Slate color
                        symbol='diamond',
                        line=dict(width=2, color='white')
                    ),
                    showlegend=False,
                    hovertemplate=f'<b>All Investments</b><br>Volatility: {combined_volatility:.1f}%<br>TWR (Ann.): {combined_twr:.1f}%<br>Sharpe: {combined_sharpe:.2f}<extra></extra>'
                ))
    except Exception as e:
        logger.warning(f"Could not calculate combined All Investments marker: {e}")
    
    # Add benchmark markers - grey round dots
    if benchmark_data:
        for b in benchmark_data:
            # Color coding by benchmark type
            if b['benchmark_type'] == 'securities':
                marker_color = '#374151'  # Dark grey for securities
            elif b['benchmark_type'] == 'crypto':
                marker_color = '#9ca3af'  # Light grey for crypto
            elif b['benchmark_type'] == 'composite':
                marker_color = '#4b5563'  # Grey-600 for composite
            else:
                marker_color = '#9ca3af'  # Default grey
            
            fig.add_trace(go.Scatter(
                x=[b['volatility']],
                y=[b['twr_annualized']],
                mode='markers+text',
                text=[b['label']],
                textposition='top center',
                textfont=dict(size=10, color='#666'),
                marker=dict(
                    size=12,
                    color=marker_color,
                    symbol='circle',
                    line=dict(width=2, color='white')
                ),
                showlegend=False,
                hovertemplate=f"<b>{b['label']}</b><br>Volatility: {b['volatility']:.1f}%<br>TWR (Ann.): {b['twr_annualized']:.1f}%<br>Sharpe: {b['sharpe_ratio']:.2f}<extra></extra>"
            ))
    
    # Add grey dotted line at y=0 (zero return line)
    fig.add_hline(y=0, line_dash="dot", line_color="#d1d5db", line_width=1.5, layer="below")
    
    fig.update_layout(
        template="plotly_white",
        xaxis_title=None,
        xaxis=dict(
            range=[0, x_range_max],
            autorange=False,
            fixedrange=False,
            tickfont=dict(size=10)
        ),
        yaxis_title=None,
        yaxis=dict(
            range=[y_range_min, y_range_max],
            autorange=False,
            fixedrange=False,
            zeroline=False  # Disable built-in zeroline, using custom one instead
        ),
        annotations=[
            dict(
                x=0, y=1.08,
                xref="paper", yref="paper",
                text="Return (Ann. %)",
                showarrow=False,
                font=dict(size=11, color="gray"),
                xanchor='left'
            ),
            dict(
                x=0.5, y=-0.18,
                xref="paper", yref="paper",
                text="Volatility (Ann. %)",
                showarrow=False,
                font=dict(size=10, color="gray"),
                xanchor='center'
            )
        ],
        hovermode='closest',
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=10, r=10, t=50, b=100),
        height=400,
        autosize=True,
        modebar={'remove': ['resetScale2d', 'select2d', 'lasso2d']}
    )
    
    return fig
