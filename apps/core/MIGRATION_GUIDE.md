# Migration Guide: Converting Pages to Use Layout Module

This guide helps you convert existing NiceFolio pages to use the new `apps.core.layout` module.

## Step 1: Add Imports

**Replace:**
```python
from nicegui import ui, app
```

**With:**
```python
from nicegui import ui, app
from apps.core.layout import (
    setup_page_theme,
    create_header,
    create_main_container,
    # Add other components you need
)
```

## Step 2: Replace Theme Setup

**Replace:**
```python
ui.query('body').classes('bg-gray-50')
ui.colors(
    primary='#1e40af',
    secondary='#059669',
    # ... more colors
)
```

**With:**
```python
setup_page_theme()
```

## Step 3: Replace Header

**Replace:**
```python
with ui.header().classes('items-center justify-between bg-slate-800 px-4 py-2 sm:px-6 sm:py-3'):
    with ui.row().classes('items-center gap-2 sm:gap-4 cursor-pointer').on('click', lambda: ui.navigate.to('/')):
        ui.image('/static/logo-transparent.png').classes('w-8 h-8 sm:w-10 sm:h-10 rounded-lg')
        ui.label('NiceFolio').classes('text-xl sm:text-2xl font-bold text-white')
    ui.label('My Application').classes('text-sm sm:text-base text-white')
```

**With:**
```python
create_header('My Application')
```

## Step 4: Replace Main Container

**Replace:**
```python
with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-4'):
    # content
```

**With:**
```python
with create_main_container():
    # content
```

**Or with custom sizing:**
```python
with create_main_container(max_width='max-w-7xl', padding='p-8', gap='gap-6'):
    # content
```

## Step 5: Replace Action Buttons (Optional)

**Replace:**
```python
with ui.row().classes('w-full justify-between items-center'):
    ui.button('➕ Add Item', on_click=add_handler).classes('bg-green-500')
    ui.button('🔄 Refresh', on_click=refresh_handler).classes('bg-blue-500')
```

**With:**
```python
create_action_buttons([
    {'label': 'Add Item', 'on_click': add_handler, 'icon': '➕', 'color': 'bg-green-500'},
    {'label': 'Refresh', 'on_click': refresh_handler, 'icon': '🔄', 'color': 'bg-blue-500'}
])
```

## Step 6: Replace Instructions Card (Optional)

**Replace:**
```python
with ui.card().classes('w-full'):
    ui.label('📝 Instructions').classes('text-xl font-bold mb-2')
    ui.label('Step 1: Do this')
    ui.label('Step 2: Do that')
    
    with ui.expansion('Advanced', icon='info').classes('mt-2'):
        with ui.column().classes('gap-1'):
            ui.label('• Option A')
            ui.label('• Option B')
```

**With:**
```python
create_instructions_card(
    title='📝 Instructions',
    instructions=[
        'Step 1: Do this',
        'Step 2: Do that'
    ],
    expansion_items={
        'Advanced': ['Option A', 'Option B']
    }
)
```

## Step 7: Replace Statistics (Optional)

**Replace:**
```python
with ui.row().classes('w-full justify-around'):
    with ui.card().classes('p-4'):
        with ui.column().classes('items-center'):
            ui.icon('account_balance', size='lg').classes('text-primary')
            ui.label('Total Value').classes('text-sm text-gray-600 mt-2')
            ui.label('$123,456').classes('text-2xl font-bold text-primary')
```

**With:**
```python
create_stats_row([
    {
        'label': 'Total Value',
        'value': '$123,456',
        'icon': 'account_balance',
        'color': 'text-primary'
    }
])
```

## Step 8: Replace Notifications (Optional)

**Replace:**
```python
ui.notify('Success!', type='positive', position='top')
```

**With:**
```python
show_notification('Success!', 'positive')
```

## Step 9: Use Format Helpers (Optional)

**Replace:**
```python
def format_currency(value, currency=None):
    if currency is None:
        currency = get_global_base_currency()
    return f"{currency} {value:,.2f}"

formatted = format_currency(1234.56)
```

**With:**
```python
from apps.core.layout import format_currency

formatted = format_currency(1234.56)
```

## Complete Before/After Example

### Before
```python
from nicegui import ui, app

@ui.page('/my-page')
def my_page():
    ui.query('body').classes('bg-gray-50')
    ui.colors(primary='#1e40af', secondary='#059669')
    
    with ui.header().classes('items-center justify-between bg-slate-800 px-4 py-2'):
        with ui.row().classes('items-center gap-2 cursor-pointer').on('click', lambda: ui.navigate.to('/')):
            ui.image('/static/logo-transparent.png').classes('w-8 h-8')
            ui.label('NiceFolio').classes('text-xl font-bold text-white')
        ui.label('My Application').classes('text-sm text-white')
    
    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-4'):
        with ui.card().classes('w-full'):
            ui.label('📝 Instructions').classes('text-xl font-bold mb-2')
            ui.label('Step 1: Do this')
            ui.label('Step 2: Do that')
        
        with ui.row().classes('w-full justify-between'):
            ui.button('➕ Add', on_click=add_handler).classes('bg-green-500')
            ui.button('🔄 Refresh', on_click=refresh_handler)
        
        with ui.card().classes('w-full'):
            ui.label('Content')
```

### After
```python
from nicegui import ui, app
from apps.core.layout import (
    setup_page_theme,
    create_header,
    create_main_container,
    create_instructions_card,
    create_action_buttons
)

@ui.page('/my-page')
def my_page():
    setup_page_theme()
    create_header('My Application')
    
    with create_main_container():
        create_instructions_card(
            title='📝 Instructions',
            instructions=['Step 1: Do this', 'Step 2: Do that']
        )
        
        create_action_buttons([
            {'label': 'Add', 'on_click': add_handler, 'icon': '➕', 'color': 'bg-green-500'},
            {'label': 'Refresh', 'on_click': refresh_handler, 'icon': '🔄'}
        ])
        
        with ui.card().classes('w-full'):
            ui.label('Content')
```

## Benefits Checklist

After migration, your page should:
- ✅ Have consistent header across all pages
- ✅ Use consistent colors and theme
- ✅ Have proper responsive spacing
- ✅ Be easier to maintain
- ✅ Have less boilerplate code
- ✅ Follow NiceFolio design patterns

## Testing Your Migration

1. Run your application
2. Navigate to your migrated page
3. Verify:
   - Header looks correct and navigates home on click
   - Colors match other pages
   - Layout is responsive (test on mobile)
   - All functionality still works
   - No console errors

## Need Help?

- See `apps/core/README.md` for full documentation
- Run `apps/core/layout_demo.py` to see examples
- Check existing refactored pages: `main.py`, `apps/db_viewer.py`, `apps/crypto_wallet_manager.py`
