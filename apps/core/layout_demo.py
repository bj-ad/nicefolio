"""
Example usage of apps.core.layout module
This file demonstrates all the available layout components.
"""

from nicegui import ui
from apps.core.layout import (
    setup_page_theme,
    create_header,
    create_simple_header,
    create_main_container,
    create_card,
    create_action_buttons,
    create_instructions_card,
    create_stat_card,
    create_stats_row,
    create_landing_card,
    create_table,
    create_dialog,
    create_empty_state,
    create_page_tabs,
    format_currency,
    format_percentage,
    show_notification
)


@ui.page('/layout-demo')
def layout_demo_page():
    """Demo page showing all layout components"""
    
    # 1. Apply theme (should be first)
    setup_page_theme()
    
    # 2. Create header
    create_header('Layout Demo Page')
    
    # 3. Main content container
    with create_main_container():
        
        # Section 1: Action Buttons
        ui.label('Action Buttons').classes('text-2xl font-bold mt-4 mb-2')
        create_action_buttons([
            {
                'label': 'Primary Action',
                'on_click': lambda: show_notification('Primary clicked!', 'positive'),
                'icon': '✓',
                'color': 'bg-primary'
            },
            {
                'label': 'Secondary Action',
                'on_click': lambda: show_notification('Secondary clicked!', 'info'),
                'icon': '⚙',
                'color': 'bg-secondary'
            }
        ])
        
        # Section 2: Statistics Row
        ui.label('Statistics Cards').classes('text-2xl font-bold mt-6 mb-2')
        create_stats_row([
            {
                'label': 'Total Value',
                'value': format_currency(123456.78, 'USD'),
                'icon': 'account_balance',
                'color': 'text-primary'
            },
            {
                'label': 'Growth',
                'value': format_percentage(15.5),
                'icon': 'trending_up',
                'color': 'text-positive'
            },
            {
                'label': 'Positions',
                'value': '42',
                'icon': 'pie_chart',
                'color': 'text-secondary'
            }
        ])
        
        # Section 3: Instructions Card
        ui.label('Instructions Card').classes('text-2xl font-bold mt-6 mb-2')
        create_instructions_card(
            title='📝 How to Use',
            instructions=[
                'Step 1: Read the documentation',
                'Step 2: Import the layout module',
                'Step 3: Use the components in your page'
            ],
            expansion_items={
                'Available Components': [
                    'Headers (with/without logo)',
                    'Action buttons',
                    'Statistics cards',
                    'Data tables',
                    'Instructions cards'
                ]
            }
        )
        
        # Section 4: Table Example
        ui.label('Data Table').classes('text-2xl font-bold mt-6 mb-2')
        sample_data = [
            {'id': 1, 'name': 'Item A', 'value': 100, 'status': 'Active'},
            {'id': 2, 'name': 'Item B', 'value': 200, 'status': 'Pending'},
            {'id': 3, 'name': 'Item C', 'value': 300, 'status': 'Completed'}
        ]
        
        create_table(
            columns=[
                {'name': 'id', 'label': 'ID', 'field': 'id', 'align': 'left'},
                {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'},
                {'name': 'value', 'label': 'Value', 'field': 'value', 'align': 'right'},
                {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'center'}
            ],
            rows=sample_data
        )
        
        # Section 5: Empty State
        ui.label('Empty State').classes('text-2xl font-bold mt-6 mb-2')
        with ui.card().classes('w-full'):
            create_empty_state(
                icon='inbox',
                title='No Data Available',
                description='This is what an empty state looks like'
            )
        
        # Section 6: Notification Demo
        ui.label('Notifications').classes('text-2xl font-bold mt-6 mb-2')
        with ui.row().classes('gap-2'):
            ui.button('Info', on_click=lambda: show_notification('Info message', 'info'))
            ui.button('Success', on_click=lambda: show_notification('Success message', 'positive'))
            ui.button('Warning', on_click=lambda: show_notification('Warning message', 'warning'))
            ui.button('Error', on_click=lambda: show_notification('Error message', 'negative'))


if __name__ in {'__main__', '__mp_main__'}:
    ui.run(title='Layout Demo', port=8080)
