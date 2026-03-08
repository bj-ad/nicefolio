"""
Common Layout Components for NiceFolio Applications
Provides reusable UI components for consistent styling and behavior.
"""

from nicegui import ui
from contextlib import contextmanager


# =============================================================================
# Navigation Drawer Layout
# =============================================================================

# Navigation structure with sub-menus
NAV_ITEMS = [
    {
        'name': 'Dashboard',
        'icon': 'dashboard',
        'route': '/',
    },
    {
        'name': 'Transactions',
        'icon': 'receipt_long',
        'route': '/transaction-review',
    },
    {
        'name': 'Tax Reports',
        'icon': 'description',
        'route': '/tax-reports',
    },
    {
        'name': 'Tools',
        'icon': 'build',
        'children': [
            {'name': 'Crypto Wallet Manager', 'icon': 'account_balance_wallet', 'route': '/wallet-manager'},
            {'name': 'Staking Manager', 'icon': 'lock', 'route': '/staking'},
            {'name': 'Cash Manager', 'icon': 'account_balance', 'route': '/cash-manager'},
        ]
    },
    {
        'name': 'System',
        'icon': 'settings',
        'children': [
            {'name': 'Database Viewer', 'icon': 'storage', 'route': '/db-viewer'},
            {'name': 'Settings', 'icon': 'tune', 'route': '/settings'},
        ]
    },
]


def _create_nav_item(item: dict, current_route: str, drawer):
    """Create a single navigation item (with or without children)."""
    is_active = item.get('route') == current_route
    
    if 'children' in item:
        # Sub-menu with expansion
        with ui.expansion(text=item['name'], icon=item['icon']).classes('w-full nav-expansion'):
            for child in item['children']:
                child_active = child['route'] == current_route
                with ui.item(on_click=lambda r=child['route']: ui.navigate.to(r)).classes(
                    'cursor-pointer rounded-lg mx-2 ' + ('bg-blue-100 text-blue-800' if child_active else 'hover:bg-gray-100')
                ):
                    with ui.item_section().props('avatar'):
                        ui.icon(child['icon']).classes('text-lg')
                    with ui.item_section():
                        ui.item_label(child['name'])
    else:
        # Direct link
        with ui.item(on_click=lambda r=item['route']: ui.navigate.to(r)).classes(
            'cursor-pointer rounded-lg mx-2 ' + ('bg-blue-100 text-blue-800 font-semibold' if is_active else 'hover:bg-gray-100')
        ):
            with ui.item_section().props('avatar'):
                ui.icon(item['icon']).classes('text-xl')
            with ui.item_section():
                ui.item_label(item['name']).classes('text-base')


def _get_page_title(route: str) -> str:
    """Get page title from route."""
    route_titles = {
        '/': 'Dashboard',
        '/portfolio': 'Dashboard',
        '/transaction-review': 'Transactions',
        '/tax-reports': 'Tax Reports',
        '/wallet-manager': 'Crypto Wallet Manager',
        '/staking': 'Staking Manager',
        '/cash-manager': 'Cash Manager',
        '/db-viewer': 'Database Viewer',
        '/settings': 'Settings',
    }
    return route_titles.get(route, 'NiceFolio')


def _get_page_icon(route: str) -> str:
    """Get page icon from route."""
    route_icons = {
        '/': 'dashboard',
        '/portfolio': 'dashboard',
        '/transaction-review': 'receipt_long',
        '/tax-reports': 'description',
        '/wallet-manager': 'account_balance_wallet',
        '/staking': 'lock',
        '/cash-manager': 'account_balance',
        '/db-viewer': 'storage',
        '/settings': 'tune',
    }
    return route_icons.get(route, 'dashboard')


@contextmanager
def page_layout(current_route: str = '/', page_title: str = None, max_width: str = 'max-w-7xl', header_content=None):
    """
    Create the global page layout with navigation drawer.
    
    Usage:
        with page_layout('/portfolio'):
            # Your page content here
            ui.label('Hello World')
        
        # With custom header content:
        def my_header():
            ui.select(...).classes('bg-white')
        
        with page_layout('/portfolio', header_content=my_header):
            # Your page content here
    
    Args:
        current_route: Current page route for active highlighting
        page_title: Optional page title override
        max_width: Maximum width class (default: 'max-w-7xl'). 
                   Options: 'max-w-full', 'max-w-screen-2xl', 'max-w-7xl', 'max-w-6xl', etc.
        header_content: Optional callback function to render custom content in header (e.g., dropdowns)
    """
    # Apply theme
    setup_page_theme()
    
    # Add custom CSS for drawer styling
    ui.add_css('''
        .nav-expansion .q-expansion-item__content {
            padding-left: 0 !important;
        }
        .nav-expansion .q-item {
            padding-left: 16px;
        }
        .q-drawer {
            background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
        }
        .q-drawer .q-item {
            color: #e2e8f0;
        }
        .q-drawer .q-item:hover {
            background: rgba(255,255,255,0.1) !important;
        }
        .q-drawer .q-expansion-item__container {
            color: #e2e8f0;
        }
        .q-drawer .bg-blue-100 {
            background: rgba(59, 130, 246, 0.3) !important;
        }
        .q-drawer .bg-blue-100 .q-item__label {
            color: #93c5fd !important;
            font-weight: 600;
        }
    ''')
    
    title = page_title or _get_page_title(current_route)
    
    # Create left drawer (responsive behavior handled by breakpoint)
    # Starts closed by default - user opens with menu button
    # breakpoint=1024: overlay on mobile (<1024px), fixed sidebar on desktop (>=1024px)
    with ui.left_drawer(value=False, fixed=True, bordered=True).classes('bg-slate-800').props('width=280 breakpoint=1024') as drawer:
        # Navigation items only (no header)
        with ui.list().classes('w-full mt-2'):
            for item in NAV_ITEMS:
                _create_nav_item(item, current_route, drawer)
        
        # Spacer
        ui.space()
        
        # Version/footer at bottom of drawer
        with ui.row().classes('w-full justify-center p-4 border-t border-slate-700'):
            ui.label('v3.0').classes('text-xs text-slate-500')

    # Header with menu, logo, page title, and optional custom content
    with ui.header(elevated=True).classes('bg-slate-800 text-white'):
        with ui.row().classes('w-full items-center gap-3 px-4 py-0'):
            # Left: Menu button + Logo + App name
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=drawer.toggle).props('flat color=white round dense')
                
                def navigate_to_all_portfolios():
                    """Navigate to dashboard with 'All Portfolios' selected"""
                    from nicegui import app
                    app.storage.user['selected_portfolio_id'] = None
                    app.storage.user['_portfolio_selector_value'] = None
                    ui.navigate.to('/')
                
                with ui.row().classes('items-center gap-2 cursor-pointer').on('click', navigate_to_all_portfolios):
                    ui.image('/static/logo-transparent.png').classes('w-8 h-8 rounded-lg')
                    # Check environment and show DEV branding in red if DEV mode
                    from utils.app_config import load_app_config
                    config = load_app_config()
                    environment = config.get('environment', 'PROD')
                    if environment == 'DEV':
                        ui.label('Development').classes('text-lg font-bold text-red-500')
                    else:
                        ui.label('NiceFolio').classes('text-lg font-bold')
            
            # Divider
            ui.separator().props('vertical').classes('bg-slate-600')
            
            # Page title
            ui.label(title).classes('text-base text-slate-300 font-medium')
            
            # Spacer to push custom content to the right
            ui.space()
            
            # Right: Custom header content (e.g., dropdowns)
            if header_content:
                header_content()

    # Main content area
    with ui.column().classes(f'w-full {max_width} mx-auto p-4 sm:p-6 gap-4'):
        yield  # This is where page content goes


def setup_page_theme():
    """
    Apply consistent theme across all NiceFolio pages.
    Call this at the beginning of each page function.
    """
    # Add grey background to entire page
    ui.query('body').classes('bg-gray-50')
    
    # Professional financial app theme
    ui.colors(
        primary='#1e40af',    # Deep blue - trust/stability
        secondary='#059669',  # Emerald - growth/money
        accent='#4f46e5',     # Indigo - technology
        positive='#10b981',   # Success green
        negative='#dc2626',   # Alert red
        info='#0891b2',       # Cyan - information
        warning='#f59e0b'     # Amber - caution
    )


def create_header(app_title: str = 'NiceFolio', show_logo: bool = True, logo_classes: str = None):
    """
    Create consistent header with logo and app title.
    
    Args:
        app_title: Main application title (default: 'NiceFolio')
        show_logo: Whether to show the logo (default: True)
        logo_classes: Optional CSS classes for logo customization
    
    Returns:
        ui.header context manager
    """
    header = ui.header().classes('items-center justify-between bg-slate-800 px-4 py-2 sm:px-6 sm:py-3')
    
    with header:
        with ui.row().classes('items-center gap-2 sm:gap-4 cursor-pointer').on('click', lambda: ui.navigate.to('/')):
            if show_logo:
                logo_cls = logo_classes or 'w-8 h-8 sm:w-10 sm:h-10 rounded-lg'
                ui.image('/static/logo-transparent.png').classes(logo_cls)
            ui.label('NiceFolio').classes('text-xl sm:text-2xl font-bold text-white')
        
        ui.label(app_title).classes('text-sm sm:text-base text-white')
    
    return header


def create_simple_header(app_title: str, bg_color: str = 'bg-primary'):
    """
    Create simple header without logo (for utility pages like DB viewer).
    
    Args:
        app_title: Application title
        bg_color: Background color class (default: 'bg-primary')
    
    Returns:
        ui.header context manager
    """
    header = ui.header().classes(f'{bg_color} text-white shadow-lg')
    
    with header:
        ui.label(app_title).classes('text-h5')
        ui.label('NiceFolio').classes('text-subtitle2 ml-auto')
    
    return header


def create_main_container(max_width: str = 'max-w-6xl', padding: str = 'p-4', gap: str = 'gap-4'):
    """
    Create main content container with consistent styling.
    
    Args:
        max_width: Maximum width class (default: 'max-w-6xl')
        padding: Padding class (default: 'p-4')
        gap: Gap between children (default: 'gap-4')
    
    Returns:
        ui.column context manager
    """
    return ui.column().classes(f'w-full {max_width} mx-auto {padding} {gap}')


def create_card(title: str = None, icon: str = None):
    """
    Create card with optional title and icon.
    
    Args:
        title: Optional card title
        icon: Optional icon name (Material Icons)
    
    Returns:
        ui.card context manager
    """
    card = ui.card().classes('w-full')
    
    with card:
        if title:
            with ui.row().classes('items-center gap-2 mb-2'):
                if icon:
                    ui.icon(icon, size='sm').classes('text-primary')
                ui.label(title).classes('text-xl font-bold')
    
    return card


def create_action_buttons(buttons: list):
    """
    Create row of action buttons.
    
    Args:
        buttons: List of button configs, each with:
            - label: Button label
            - on_click: Click handler
            - icon: Optional icon
            - color: Optional color class (default: 'bg-primary')
    
    Returns:
        ui.row context manager with buttons
    """
    row = ui.row().classes('w-full justify-between items-center')
    
    with row:
        for btn_config in buttons:
            label = btn_config.get('label', 'Button')
            on_click = btn_config.get('on_click', lambda: None)
            icon = btn_config.get('icon')
            color = btn_config.get('color', 'bg-primary')
            
            button_label = f'{icon} {label}' if icon else label
            ui.button(button_label, on_click=on_click).classes(color)
    
    return row


def create_instructions_card(title: str, instructions: list, expansion_items: dict = None):
    """
    Create instructions card with optional expandable sections.
    
    Args:
        title: Card title
        instructions: List of instruction strings
        expansion_items: Dict of {title: [items]} for expandable sections
    
    Returns:
        ui.card context manager
    """
    card = ui.card().classes('w-full')
    
    with card:
        ui.label(title).classes('text-xl font-bold mb-2')
        
        for instruction in instructions:
            ui.label(instruction)
        
        if expansion_items:
            for exp_title, items in expansion_items.items():
                with ui.expansion(exp_title, icon='info').classes('mt-2'):
                    with ui.column().classes('gap-1'):
                        for item in items:
                            ui.label(f'• {item}')
    
    return card


def create_stat_card(label: str, value: str, icon: str = None, color: str = 'text-primary'):
    """
    Create a single statistic card.
    
    Args:
        label: Stat label
        value: Stat value
        icon: Optional icon name
        color: Text color class for value
    
    Returns:
        ui.card context manager
    """
    card = ui.card().classes('p-4')
    
    with card:
        with ui.column().classes('items-center'):
            if icon:
                ui.icon(icon, size='lg').classes(color)
            ui.label(label).classes('text-sm text-gray-600 mt-2')
            ui.label(value).classes(f'text-2xl font-bold {color}')
    
    return card


def create_stats_row(stats: list):
    """
    Create row of statistic cards.
    
    Args:
        stats: List of stat configs, each with:
            - label: Stat label
            - value: Stat value
            - icon: Optional icon
            - color: Optional color class
    
    Returns:
        ui.row context manager with stat cards
    """
    row = ui.row().classes('w-full justify-around')
    
    with row:
        for stat in stats:
            create_stat_card(
                label=stat.get('label', ''),
                value=stat.get('value', ''),
                icon=stat.get('icon'),
                color=stat.get('color', 'text-primary')
            )
    
    return row


def create_landing_card(title: str, description: str, icon: str, route: str, border_color: str = 'border-blue-700'):
    """
    Create a landing page navigation card.
    
    Args:
        title: Card title
        description: Card description
        icon: Material icon name
        route: Navigation route
        border_color: Left border color class
    
    Returns:
        ui.card context manager
    """
    card = ui.card().classes(
        f'cursor-pointer hover:shadow-xl transition-all duration-300 border-l-4 {border_color} rounded-lg pointer-events-auto'
    ).on('click', lambda: ui.navigate.to(route))
    
    with card:
        with ui.column().classes('p-8 gap-3 items-center pointer-events-none'):
            ui.icon(icon, size='4rem').classes(border_color.replace('border-', 'text-'))
            ui.label(title).classes('text-2xl font-bold text-center text-slate-800')
            ui.label(description).classes('text-slate-600 text-center text-sm')
    
    return card


def create_table(columns: list, rows: list, row_key: str = 'id', add_slots: dict = None, event_handlers: dict = None):
    """
    Create table with optional custom slots and event handlers.
    
    Args:
        columns: List of column dicts with 'name', 'label', 'field', 'align'
        rows: List of row data dicts
        row_key: Row key field name
        add_slots: Dict of {slot_name: slot_html} for custom rendering
        event_handlers: Dict of {event_name: handler_function}
    
    Returns:
        ui.table instance
    """
    table = ui.table(
        columns=columns,
        rows=rows,
        row_key=row_key
    ).classes('w-full')
    
    if add_slots:
        for slot_name, slot_html in add_slots.items():
            table.add_slot(slot_name, slot_html)
    
    if event_handlers:
        for event_name, handler in event_handlers.items():
            table.on(event_name, handler)
    
    return table


def format_currency(value, currency=None):
    """
    Format value as currency. Uses provided currency or falls back to global base currency.
    
    Args:
        value: Numeric value to format
        currency: Currency code (optional)
    
    Returns:
        Formatted string (e.g., "USD 1,234.56")
    """
    if currency is None:
        from utils.app_config import get_global_base_currency
        currency = get_global_base_currency()
    if value is None:
        return f"{currency} 0.00"
    return f"{currency} {value:,.2f}"


def format_percentage(value):
    """
    Format value as percentage.
    
    Args:
        value: Numeric value
    
    Returns:
        Formatted string (e.g., "+5.25%")
    """
    if value is None:
        return "0.00%"
    return f"{value:+.2f}%"


def show_notification(message: str, type: str = 'info', position: str = 'top', timeout: int = 3):
    """
    Show notification message.
    
    Args:
        message: Notification message
        type: 'info', 'positive', 'negative', 'warning'
        position: 'top', 'bottom', 'left', 'right', 'center'
        timeout: Timeout in seconds (0 for no auto-hide)
    """
    ui.notify(message, type=type, position=position, timeout=timeout * 1000 if timeout else 0)


def create_dialog(title: str, content_fn, actions: list = None):
    """
    Create dialog with custom content and actions.
    
    Args:
        title: Dialog title
        content_fn: Function to render dialog content (called with dialog context)
        actions: List of action button configs with 'label' and 'on_click'
    
    Returns:
        ui.dialog instance
    """
    dialog = ui.dialog()
    
    with dialog, ui.card().classes('w-full max-w-2xl'):
        ui.label(title).classes('text-xl font-bold mb-4')
        
        # Call content function to render custom content
        content_fn(dialog)
        
        # Default actions if none provided
        if actions is None:
            actions = [{'label': 'Close', 'on_click': lambda: dialog.close()}]
        
        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            for action in actions:
                ui.button(action['label'], on_click=action['on_click'])
    
    return dialog


def create_loading_spinner(message: str = 'Loading...'):
    """
    Create loading spinner with message.
    
    Args:
        message: Loading message
    
    Returns:
        ui.spinner instance
    """
    with ui.row().classes('items-center gap-2'):
        spinner = ui.spinner(size='lg')
        ui.label(message)
    return spinner


def create_empty_state(icon: str, title: str, description: str = None):
    """
    Create empty state placeholder.
    
    Args:
        icon: Material icon name
        title: Empty state title
        description: Optional description text
    
    Returns:
        ui.column context manager
    """
    col = ui.column().classes('items-center justify-center p-8 text-center')
    
    with col:
        ui.icon(icon, size='4rem').classes('text-gray-400')
        ui.label(title).classes('text-xl text-gray-600 mt-4')
        if description:
            ui.label(description).classes('text-sm text-gray-500 mt-2')
    
    return col


def create_page_tabs(tabs: list, active_tab: str = None):
    """
    Create page-level tabs navigation.
    
    Args:
        tabs: List of tab configs with 'name', 'label', 'icon'
        active_tab: Active tab name (optional)
    
    Returns:
        ui.tabs instance
    """
    tabs_ui = ui.tabs()
    
    with tabs_ui:
        for tab in tabs:
            ui.tab(
                tab['name'],
                label=tab.get('label', tab['name']),
                icon=tab.get('icon')
            )
    
    return tabs_ui


def apply_responsive_classes(element, mobile: str = '', tablet: str = '', desktop: str = ''):
    """
    Apply responsive classes to an element.
    
    Args:
        element: NiceGUI element
        mobile: Classes for mobile (default)
        tablet: Classes for tablet (sm:)
        desktop: Classes for desktop (lg:)
    
    Returns:
        Element with classes applied
    """
    classes = []
    if mobile:
        classes.append(mobile)
    if tablet:
        classes.append(f'sm:{tablet}')
    if desktop:
        classes.append(f'lg:{desktop}')
    
    if classes:
        element.classes(' '.join(classes))
    
    return element


def create_footer():
    """
    Create consistent footer across all NiceFolio pages.
    
    Returns:
        ui.footer context manager
    """
    footer = ui.footer().classes('bg-slate-800 text-white py-4')
    
    with footer:
        with ui.row().classes('w-full max-w-6xl mx-auto justify-between items-center px-4'):
            ui.label('NiceFolio © 2024').classes('text-sm text-gray-400')
            with ui.row().classes('gap-4'):
                ui.link('Home', '/').classes('text-sm text-gray-400 hover:text-white')
                ui.link('Portfolio', '/portfolio').classes('text-sm text-gray-400 hover:text-white')
                ui.link('Settings', '/settings').classes('text-sm text-gray-400 hover:text-white')
    
    return footer
