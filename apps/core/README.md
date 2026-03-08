# Core Layout Module

Common UI components for consistent styling and behavior across NiceFolio applications.

## Overview

The `apps.core.layout` module provides reusable UI components that ensure a consistent look and feel throughout the NiceFolio application suite. All apps should use these components instead of creating custom layouts.

## Quick Start

```python
from apps.core.layout import (
    setup_page_theme,
    create_header,
    create_main_container
)

@ui.page('/my-page')
def my_page():
    # 1. Apply consistent theme
    setup_page_theme()
    
    # 2. Create header
    create_header('My Application')
    
    # 3. Main content container
    with create_main_container():
        # Your page content here
        ui.label('Hello World!')
```

## Components

### Theme Setup

#### `setup_page_theme()`
Applies consistent theme colors and background to the page. **Must be called first** in every page function.

```python
setup_page_theme()  # Grey background + professional color palette
```

### Headers

#### `create_header(app_title, show_logo=True, logo_classes=None)`
Creates the standard NiceFolio header with logo and navigation.

```python
create_header('Portfolio Dashboard')
create_header('Settings', show_logo=False)
```

#### `create_simple_header(app_title, bg_color='bg-primary')`
Creates a simple header for utility pages (like DB viewer).

```python
create_simple_header('🔍 Database Viewer')
```

### Containers

#### `create_main_container(max_width='max-w-6xl', padding='p-4', gap='gap-4')`
Creates the main content container with consistent styling.

```python
with create_main_container():
    # Page content
    pass

# Customize for wider layouts
with create_main_container(max_width='max-w-7xl', padding='p-8'):
    pass
```

### Cards

#### `create_card(title=None, icon=None)`
Creates a card with optional title and icon.

```python
with create_card(title='Portfolio Summary', icon='dashboard'):
    ui.label('Card content here')
```

#### `create_instructions_card(title, instructions, expansion_items=None)`
Creates an instructions card with optional expandable sections.

```python
create_instructions_card(
    title='📝 How to Use',
    instructions=[
        'Step 1: Do this',
        'Step 2: Do that'
    ],
    expansion_items={
        'Advanced Options': ['Option A', 'Option B']
    }
)
```

### Action Buttons

#### `create_action_buttons(buttons)`
Creates a row of action buttons with consistent styling.

```python
create_action_buttons([
    {
        'label': 'Add Item',
        'on_click': add_item_handler,
        'icon': '➕',
        'color': 'bg-green-500'
    },
    {
        'label': 'Refresh',
        'on_click': refresh_handler,
        'icon': '🔄',
        'color': 'bg-blue-500'
    }
])
```

### Statistics

#### `create_stat_card(label, value, icon=None, color='text-primary')`
Creates a single statistic card.

```python
create_stat_card(
    label='Total Value',
    value='$123,456.78',
    icon='account_balance',
    color='text-primary'
)
```

#### `create_stats_row(stats)`
Creates a row of statistic cards.

```python
create_stats_row([
    {
        'label': 'Total Value',
        'value': '$123,456',
        'icon': 'account_balance',
        'color': 'text-primary'
    },
    {
        'label': 'Growth',
        'value': '+15.5%',
        'icon': 'trending_up',
        'color': 'text-positive'
    }
])
```

### Landing Page Cards

#### `create_landing_card(title, description, icon, route, border_color)`
Creates navigation cards for the landing page.

```python
create_landing_card(
    title='Portfolio Dashboard',
    description='Track performance, view positions',
    icon='dashboard',
    route='/portfolio',
    border_color='border-blue-700'
)
```

### Tables

#### `create_table(columns, rows, row_key='id', add_slots=None, event_handlers=None)`
Creates a data table with optional custom slots and event handlers.

```python
table = create_table(
    columns=[
        {'name': 'id', 'label': 'ID', 'field': 'id', 'align': 'left'},
        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'}
    ],
    rows=data,
    event_handlers={
        'view': view_handler,
        'delete': delete_handler
    }
)
```

### Dialogs

#### `create_dialog(title, content_fn, actions=None)`
Creates a dialog with custom content.

```python
def dialog_content(dialog):
    ui.label('Dialog content here')
    ui.input('Name')

dialog = create_dialog(
    title='Add Item',
    content_fn=dialog_content,
    actions=[
        {'label': 'Save', 'on_click': save_handler},
        {'label': 'Cancel', 'on_click': lambda d=dialog: d.close()}
    ]
)
dialog.open()
```

### Empty States

#### `create_empty_state(icon, title, description=None)`
Creates an empty state placeholder.

```python
create_empty_state(
    icon='inbox',
    title='No items found',
    description='Try adding some items to get started'
)
```

### Notifications

#### `show_notification(message, type='info', position='top', timeout=3)`
Shows a notification message.

```python
show_notification('Item saved successfully!', 'positive')
show_notification('An error occurred', 'negative')
show_notification('Please wait...', 'info', timeout=0)  # No auto-hide
```

Types: `'info'`, `'positive'`, `'negative'`, `'warning'`

### Formatting Helpers

#### `format_currency(value, currency=None)`
Formats a value as currency.

```python
format_currency(1234.56)  # "USD 1,234.56"
format_currency(1234.56, 'THB')  # "THB 1,234.56"
```

#### `format_percentage(value)`
Formats a value as percentage.

```python
format_percentage(15.5)  # "+15.50%"
format_percentage(-5.25)  # "-5.25%"
```

## Best Practices

1. **Always call `setup_page_theme()` first** in your page function
2. **Use `create_header()` for consistency** - don't create custom headers
3. **Wrap content in `create_main_container()`** for proper spacing and responsive layout
4. **Use layout components instead of raw NiceGUI** when available
5. **Follow the established color scheme** - primary (blue), secondary (green), accent (indigo)

## Example: Complete Page

```python
from nicegui import ui
from apps.core.layout import *

@ui.page('/my-app')
def my_app_page():
    # Step 1: Theme
    setup_page_theme()
    
    # Step 2: Header
    create_header('My Application')
    
    # Step 3: Container
    with create_main_container():
        
        # Instructions
        create_instructions_card(
            title='📝 Instructions',
            instructions=['Do this', 'Then that']
        )
        
        # Actions
        create_action_buttons([
            {'label': 'Add', 'on_click': add_handler, 'icon': '➕'},
            {'label': 'Refresh', 'on_click': refresh_handler, 'icon': '🔄'}
        ])
        
        # Statistics
        create_stats_row([
            {'label': 'Total', 'value': '42', 'icon': 'dashboard'}
        ])
        
        # Table
        with create_card(title='Data Table'):
            create_table(
                columns=[...],
                rows=[...]
            )
```

## Demo

Run the layout demo to see all components in action:

```bash
python apps/core/layout_demo.py
```

Or visit: http://localhost:8080/layout-demo

## Color Palette

- **Primary**: `#1e40af` (Deep Blue) - Trust, stability
- **Secondary**: `#059669` (Emerald) - Growth, money
- **Accent**: `#4f46e5` (Indigo) - Technology
- **Positive**: `#10b981` (Success Green)
- **Negative**: `#dc2626` (Alert Red)
- **Info**: `#0891b2` (Cyan)
- **Warning**: `#f59e0b` (Amber)

## Migration Guide

### Before (Old Pattern)
```python
def my_page():
    ui.query('body').classes('bg-gray-50')
    ui.colors(primary='#1e40af', ...)
    
    with ui.header().classes('...'):
        ui.label('My App')
    
    with ui.column().classes('w-full max-w-6xl mx-auto p-4'):
        # content
```

### After (New Pattern)
```python
def my_page():
    setup_page_theme()
    create_header('My App')
    
    with create_main_container():
        # content
```

## Contributing

When adding new reusable components:
1. Add to `apps/core/layout.py`
2. Document in this README
3. Add example to `layout_demo.py`
4. Follow existing naming conventions
