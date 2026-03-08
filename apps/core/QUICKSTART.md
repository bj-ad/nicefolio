# Layout Module - Quick Reference

## Import Statement

```python
from apps.core.layout import (
    setup_page_theme,
    create_header,
    create_main_container,
    # ... add other components as needed
)
```

## Page Template

```python
from nicegui import ui
from apps.core.layout import setup_page_theme, create_header, create_main_container

@ui.page('/my-page')
def my_page():
    # 1. Theme (required)
    setup_page_theme()
    
    # 2. Header (required)
    create_header('Page Title')
    
    # 3. Content (required)
    with create_main_container():
        # Your page content here
        pass
```

## Most Common Components

```python
# Header
create_header('App Name')

# Container
with create_main_container():
    pass

# Card
with create_card(title='Title', icon='dashboard'):
    ui.label('Content')

# Buttons
create_action_buttons([
    {'label': 'Add', 'on_click': handler, 'icon': '➕', 'color': 'bg-green-500'},
    {'label': 'Refresh', 'on_click': handler, 'icon': '🔄'}
])

# Stats
create_stats_row([
    {'label': 'Total', 'value': '$1,234', 'icon': 'account_balance'}
])

# Notification
show_notification('Success!', 'positive')
```

## Colors

```python
'bg-primary'      # Blue
'bg-secondary'    # Green  
'bg-accent'       # Indigo
'bg-positive'     # Success Green
'bg-negative'     # Error Red
'bg-warning'      # Amber
'bg-info'         # Cyan

'text-primary'    # Blue text
'text-positive'   # Green text
# etc.
```

## Full Component List

| Component                    | Usage                                 |
| ---------------------------- | ------------------------------------- |
| `setup_page_theme()`         | Apply theme (call first)              |
| `create_header()`            | Standard header with logo             |
| `create_simple_header()`     | Simple header (no logo)               |
| `create_main_container()`    | Main content wrapper                  |
| `create_card()`              | Card with optional title              |
| `create_action_buttons()`    | Row of action buttons                 |
| `create_instructions_card()` | Instructions with expandable sections |
| `create_stat_card()`         | Single statistic card                 |
| `create_stats_row()`         | Row of statistics                     |
| `create_landing_card()`      | Navigation card for landing page      |
| `create_table()`             | Data table with custom slots          |
| `create_dialog()`            | Dialog with custom content            |
| `create_empty_state()`       | Empty state placeholder               |
| `create_page_tabs()`         | Page-level tabs                       |
| `show_notification()`        | Show notification message             |
| `format_currency()`          | Format as currency                    |
| `format_percentage()`        | Format as percentage                  |

## See Also

- **`apps/core/README.md`** - Full documentation
- **`apps/core/layout_demo.py`** - Interactive demo
- **`http://localhost:8080/layout-demo`** - Run demo in browser
