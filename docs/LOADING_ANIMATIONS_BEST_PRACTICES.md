# Loading Animations Best Practices for NiceGUI

This guide provides best practices for implementing loading animations in NiceGUI applications, specifically for the NiceFolio portfolio tracker.

## Table of Contents
1. [Core Concept](#core-concept)
2. [When to Use Loading Animations](#when-to-use-loading-animations)
3. [Implementation Patterns](#implementation-patterns)
4. [Common Pitfalls](#common-pitfalls)
5. [Architecture Considerations](#architecture-considerations)
6. [Examples](#examples)

---

## Core Concept

### The `ui.timer(0)` Pattern

NiceGUI's recommended pattern for perceived performance:

```python
from nicegui import ui
import asyncio

def my_page():
    # 1. Create skeleton UI immediately
    container = ui.card()
    with container:
        ui.spinner(size='xl')
        ui.label('Loading...')
    
    # 2. Define async loading function
    async def load_data():
        await asyncio.sleep(0.1)  # Let skeleton render
        data = fetch_heavy_data()  # Your actual data loading
        
        # 3. Replace skeleton with real content
        container.clear()
        with container:
            render_actual_content(data)
    
    # 4. Trigger with zero-delay timer
    ui.timer(0, load_data, once=True)
```

**Why This Works:**
- The page renders immediately with skeleton UI (instant feedback)
- Timer schedules the async function to run after the current render cycle
- User sees something immediately instead of a blank page
- Actual data loads without blocking the UI thread

---

## When to Use Loading Animations

### ✅ Good Use Cases
- **Heavy database queries** (> 500ms)
- **API calls to external services**
- **Complex calculations** (data aggregation, chart generation)
- **Large data transformations**
- **Initial page load with multiple data sources**

### ❌ Don't Use For
- Simple CRUD operations (< 100ms)
- Static content rendering
- Configuration loading
- Form submissions (use inline feedback instead)
- Navigation between pages (NiceGUI handles this)

---

## Implementation Patterns

### Pattern 1: Simple Container Replacement

**Best for:** Single data source, straightforward UI

```python
def simple_page():
    # Skeleton
    container = ui.card().classes('w-full p-6')
    with container:
        ui.spinner(size='xl', color='primary')
        ui.label('Loading data...').classes('text-gray-500')
    
    async def load():
        await asyncio.sleep(0.05)  # Ensure skeleton visible
        data = get_data()
        
        container.clear()
        with container:
            # Render actual content
            ui.label(f'Data: {data}')
    
    ui.timer(0, load, once=True)
```

### Pattern 2: Multiple Containers

**Best for:** Complex pages with independent sections

```python
def complex_page():
    # Multiple skeleton containers
    header_container = ui.card()
    with header_container:
        ui.spinner()
        ui.label('Loading header...')
    
    content_container = ui.card()
    with content_container:
        ui.spinner()
        ui.label('Loading content...')
    
    async def load_all():
        await asyncio.sleep(0.05)
        
        # Load independently
        header_data = get_header()
        content_data = get_content()
        
        # Replace each skeleton
        header_container.clear()
        with header_container:
            render_header(header_data)
        
        content_container.clear()
        with content_container:
            render_content(content_data)
    
    ui.timer(0, load_all, once=True)
```

### Pattern 3: Progressive Loading

**Best for:** Multiple data sources with different load times

```python
def progressive_page():
    # Create all containers upfront
    fast_container = ui.card()
    slow_container = ui.card()
    
    async def load_fast():
        await asyncio.sleep(0.05)
        data = get_fast_data()  # < 100ms
        fast_container.clear()
        with fast_container:
            render_fast(data)
    
    async def load_slow():
        await asyncio.sleep(0.1)  # Show skeleton longer
        data = get_slow_data()  # > 1s
        slow_container.clear()
        with slow_container:
            render_slow(data)
    
    # Trigger both independently
    ui.timer(0, load_fast, once=True)
    ui.timer(0, load_slow, once=True)
```

### Pattern 4: Hybrid DB + CPU-Intensive Calculations ⚡

**Best for:** Heavy calculations on database data (XIRR, benchmarks, complex analytics)

**The Problem:** You cannot pass database Sessions to `run.cpu_bound()` (they're not picklable), but you also shouldn't run CPU-intensive calculations in the main thread (UI becomes unresponsive).

**The Solution:** Three-phase approach - fetch data, close DB, then calculate in separate process.

```python
from nicegui import ui, run
import pandas as pd

def analytics_dashboard():
    """Dashboard with heavy XIRR/benchmark calculations"""
    container = ui.card().classes('w-full p-6')
    with container:
        ui.spinner(size='xl')
        ui.label('Calculating portfolio analytics...')
    
    async def load_complex_dashboard():
        # ============================================================
        # PHASE 1: DB FETCH (I/O Bound - Main Thread)
        # Fast because we're only fetching rows, not calculating
        # ============================================================
        db = SessionLocal()
        try:
            # Fetch raw SQLAlchemy models
            snapshots = db.query(Snapshot).filter_by(portfolio_id=1).all()
            transactions = db.query(Transaction).filter_by(portfolio_id=1).all()
            
            # CRITICAL: Convert to picklable format BEFORE closing session
            # Option A: Convert to dict
            snapshot_data = [
                {
                    'date': s.snapshot_date,
                    'nav': float(s.nav),
                    'units': float(s.units)
                }
                for s in snapshots
            ]
            
            transaction_data = [
                {
                    'date': t.transaction_date,
                    'amount': float(t.amount),
                    'type': t.transaction_type
                }
                for t in transactions
            ]
            
            # Option B: Convert to pandas (also picklable)
            # df_snapshots = pd.DataFrame([s.__dict__ for s in snapshots])
            
        finally:
            db.close()  # ✅ Connection freed immediately
        
        # ============================================================
        # PHASE 2: HEAVY MATH (CPU Bound - Separate Process)
        # UI remains responsive while XIRR calculates on another core
        # ============================================================
        # Pass clean, picklable data to CPU-intensive function
        results = await run.cpu_bound(
            calculate_portfolio_analytics,
            snapshot_data,
            transaction_data
        )
        
        # ============================================================
        # PHASE 3: RENDER (UI Update - Main Thread)
        # ============================================================
        container.clear()
        with container:
            # Render charts with calculated results
            render_xirr_chart(results['xirr'])
            render_twr_chart(results['twr'])
            render_benchmark_comparison(results['benchmark_data'])
    
    ui.timer(0, load_complex_dashboard, once=True)


# CPU-intensive function (runs in separate process)
def calculate_portfolio_analytics(snapshots, transactions):
    """
    Pure function for heavy calculations.
    
    IMPORTANT: This function must:
    1. Be picklable (no closures, no DB connections)
    2. Accept only picklable arguments (dict, list, pandas, primitives)
    3. Return only picklable results
    """
    import numpy as np
    from scipy.optimize import newton
    
    # Heavy XIRR calculation
    xirr = calculate_xirr_from_cashflows(transactions)
    
    # Heavy TWR calculation with daily snapshots
    twr = calculate_twr_geometric_linking(snapshots)
    
    # Heavy benchmark calculations
    benchmark_data = calculate_alpha_and_beta(snapshots, transactions)
    
    return {
        'xirr': xirr,
        'twr': twr,
        'benchmark_data': benchmark_data
    }
```

**Why This Works:**

1. **DB Phase is Fast**: Fetching rows from database is I/O-bound and fast (< 100ms typically)
2. **Session Closes Quickly**: Connection is freed before heavy math starts
3. **Data is Detached**: Converting to dict/pandas detaches from SQLAlchemy session (no lazy loading issues)
4. **Math Runs in Parallel**: `run.cpu_bound()` runs calculation in separate CPU core
5. **UI Stays Responsive**: Main thread continues handling UI events while math runs

**Common Use Cases:**
- XIRR calculations with many transactions
- TWR with daily geometric linking
- Monte Carlo simulations
- Benchmark alpha/beta calculations
- Portfolio optimization algorithms
- Large data aggregations with numpy/pandas

**Key Points:**
- ✅ Always close DB before calling `run.cpu_bound()`
- ✅ Convert SQLAlchemy models to dict/pandas/primitives
- ✅ Keep calculation functions pure (no closures, no external state)
- ✅ Return picklable results only
- ❌ Don't pass Session objects to `run.cpu_bound()`
- ❌ Don't keep DB connection open during heavy math

---

## Common Pitfalls

### ❌ Pitfall 1: Double Wrapping Containers

**Problem:** Existing functions already create card containers

```python
# BAD - Creates nested cards
def broken_page():
    container = ui.card()  # Outer wrapper
    with container:
        ui.spinner()
    
    async def load():
        container.clear()
        with container:
            render_section()  # This ALSO creates a card!
            # Result: card inside card (broken layout)

# FIX: Either remove skeleton wrapper OR refactor functions
def fixed_page():
    # Option A: No wrapper, let functions create their own
    async def load():
        render_section()  # Creates its own card
    ui.timer(0, load, once=True)
    
    # Option B: Refactor functions to accept container
    container = ui.card()
    async def load():
        container.clear()
        with container:
            render_section_content()  # Content only, no card
    ui.timer(0, load, once=True)
```

### ❌ Pitfall 2: Using `run.cpu_bound` with Database Sessions

**Problem:** SQLAlchemy Sessions cannot be pickled for multiprocessing

```python
from nicegui import run
from database import SessionLocal

# BAD - Session can't be pickled!
async def broken_load():
    db = SessionLocal()
    result = await run.cpu_bound(query_database, db)  # ❌ PicklingError!

# FIX: Don't use run.cpu_bound with Sessions
async def fixed_load():
    db = SessionLocal()
    try:
        result = query_database(db)  # ✅ Direct call
        # Database I/O is already non-blocking at OS level
    finally:
        db.close()
```

**When to use `run.cpu_bound`:**
- ✅ Pure CPU-intensive calculations (no I/O)
- ✅ Data transformations (pandas, numpy operations)
- ✅ Image processing, video encoding
- ✅ Financial calculations (XIRR, TWR, Monte Carlo)
- ✅ Machine learning inference
- ⚠️ Only with picklable arguments (dict, list, pandas, primitives)
- ❌ Database operations (already I/O-bound, plus Session not picklable)
- ❌ API calls (use httpx async client instead)
- ❌ File operations (already I/O-bound)

**The Hybrid Pattern (DB + Heavy Math):**
See [Pattern 4](#pattern-4-hybrid-db--cpu-intensive-calculations-) for the correct way to combine database queries with CPU-intensive calculations:
1. Fetch data from DB (fast, I/O-bound)
2. Close DB connection
3. Convert to picklable format (dict/pandas)
4. Pass to `run.cpu_bound()` for heavy calculations
5. Render results

### ❌ Pitfall 3: Variable Scoping Issues

**Problem:** Reassigning container variables creates local scope

```python
# BAD - allocations_container becomes local variable
container = ui.card()
allocations_container = None  # Outer scope

async def load():
    if condition:
        allocations_container = ui.card()  # ❌ Creates local variable!
        # Now checking allocations_container elsewhere fails
    
    if allocations_container:  # ❌ UnboundLocalError!
        allocations_container.clear()

# FIX: Don't reassign, create new variables or use containers properly
container = ui.card()

async def load():
    if condition:
        new_container = ui.card()  # ✅ Different name
        with new_container:
            render_content()
    
    if container:  # ✅ Uses outer scope
        container.clear()
```

### ❌ Pitfall 4: Forgetting to Close Resources

**Problem:** Database connections and file handles leak

```python
# BAD - No cleanup
async def broken_load():
    db = SessionLocal()
    data = query(db)
    # db connection never closed!

# FIX: Always use try/finally
async def fixed_load():
    db = SessionLocal()
    try:
        data = query(db)
        container.clear()
        with container:
            render(data)
    finally:
        db.close()  # ✅ Always closes
```

---

## Architecture Considerations

### NiceFolio's Three-Layer Architecture

The loading animation pattern must respect the three-layer architecture:

```
┌─────────────────────────────────────────────────┐
│ UI LAYER (NiceGUI Page)                        │
│ - Creates skeleton containers                   │
│ - Triggers async loading with ui.timer(0)      │
│ - Replaces skeleton with real content           │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ SERVICE LAYER (service/*.py)                    │
│ - Fetches data from APIs                        │
│ - Returns Optional[dict] or Optional[List[dict]]│
│ - Uses @cache decorator                         │
│ - NO database operations                        │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ CRUD LAYER (crud/*.py)                          │
│ - Database operations only                      │
│ - Accepts SessionLocal() from UI layer          │
│ - Returns tuple[int, int] or model instances    │
│ - NO @cache decorator                           │
└─────────────────────────────────────────────────┘
```

### Implementation Strategy

```python
# UI LAYER - Page with loading animation
def portfolio_page():
    # 1. Create skeleton
    container = ui.card()
    with container:
        ui.spinner()
        ui.label('Loading portfolio...')
    
    # 2. Async load
    async def load():
        db = SessionLocal()
        try:
            # 3. Call service layer (API + cache)
            prices = fetch_crypto_prices()  # Service layer
            
            # 4. Call CRUD layer (database)
            summary = get_portfolio_summary(db, portfolio_id=1)  # CRUD layer
            
            # 5. Replace skeleton
            container.clear()
            with container:
                render_portfolio(summary, prices)
        finally:
            db.close()
    
    ui.timer(0, load, once=True)
```

---

## Examples

### Example 1: Portfolio Dashboard (Full Implementation)

```python
def portfolio_dashboard():
    """Portfolio dashboard with loading animation"""
    selected_portfolio_id = app.storage.user.get('selected_portfolio_id', None)
    
    # Skeleton
    wealth_container = ui.card().classes('w-full p-6')
    with wealth_container:
        with ui.column().classes('w-full items-center py-8'):
            ui.spinner(size='xl', color='white')
            ui.label('Loading portfolio summary...').classes('text-white')
    
    charts_container = ui.card().classes('w-full p-6 mt-4')
    with charts_container:
        with ui.column().classes('w-full items-center').style('min-height: 400px'):
            ui.spinner(size='xl', color='primary')
            ui.label('Preparing charts...').classes('text-gray-500')
    
    # Async load
    async def load_dashboard():
        db = SessionLocal()
        try:
            await asyncio.sleep(0.05)
            
            # Get data
            summary = get_portfolio_summary(db, portfolio_id=selected_portfolio_id)
            
            if not summary:
                # Show error
                wealth_container.clear()
                with wealth_container:
                    ui.label('No data available').classes('text-xl text-gray-500')
                charts_container.clear()
                return
            
            # Render wealth summary
            wealth_container.clear()
            with wealth_container:
                render_wealth_summary(summary)
            
            # Render charts
            charts_container.clear()
            with charts_container:
                render_performance_charts(db, selected_portfolio_id, summary)
        
        except Exception as e:
            logger.error(f"Error loading dashboard: {e}", exc_info=True)
            wealth_container.clear()
            with wealth_container:
                ui.label(f'Error: {str(e)}').classes('text-red-500')
            charts_container.clear()
        
        finally:
            db.close()
    
    ui.timer(0, load_dashboard, once=True)
```

### Example 2: Simple Data Table

```python
def data_table_page():
    """Data table with loading skeleton"""
    container = ui.card().classes('w-full p-6')
    with container:
        # Table skeleton
        with ui.column().classes('w-full gap-2'):
            ui.spinner(size='lg')
            ui.label('Loading records...')
            # Optional: Show skeleton rows
            for _ in range(5):
                ui.skeleton().classes('h-12 w-full')
    
    async def load_table():
        db = SessionLocal()
        try:
            await asyncio.sleep(0.05)
            records = db.query(MyModel).all()
            
            container.clear()
            with container:
                ui.table(
                    columns=[
                        {'name': 'name', 'label': 'Name', 'field': 'name'},
                        {'name': 'value', 'label': 'Value', 'field': 'value'},
                    ],
                    rows=[{'name': r.name, 'value': r.value} for r in records]
                )
        finally:
            db.close()
    
    ui.timer(0, load_table, once=True)
```

### Example 3: Reusable Loading Component

```python
def create_loading_card(message='Loading...', size='xl', height='200px'):
    """Reusable loading skeleton component"""
    card = ui.card().classes('w-full p-6')
    with card:
        with ui.column().classes('w-full items-center justify-center gap-2').style(f'min-height: {height}'):
            ui.spinner(size=size, color='primary')
            ui.label(message).classes('text-gray-500')
    return card

# Usage
def my_page():
    container = create_loading_card('Loading data...', size='xl', height='400px')
    
    async def load():
        data = get_data()
        container.clear()
        with container:
            render_content(data)
    
    ui.timer(0, load, once=True)
```

---

## Testing Loading Animations

### Manual Testing Checklist

- [ ] Skeleton appears immediately on page load
- [ ] Spinner is visible and animated
- [ ] Loading message is clear and helpful
- [ ] Content appears after skeleton (no flash)
- [ ] Error states are handled gracefully
- [ ] Database connections are closed properly
- [ ] No console errors in browser
- [ ] Works on slow connections (throttle network)

### Simulate Slow Loading

```python
async def load_data():
    await asyncio.sleep(2)  # Artificial delay
    data = get_data()
    # ... render
```

### Error Handling Template

```python
async def load_with_error_handling():
    db = SessionLocal()
    try:
        await asyncio.sleep(0.05)
        data = get_data(db)
        
        container.clear()
        with container:
            render_content(data)
    
    except Exception as e:
        logger.error(f"Error loading data: {e}", exc_info=True)
        container.clear()
        with container:
            with ui.column().classes('w-full p-8 bg-red-50 rounded'):
                ui.icon('error', size='3rem').classes('text-red-500 mb-4')
                ui.label('Error loading data').classes('text-xl font-bold text-red-600')
                ui.label(str(e)).classes('text-red-500 mt-2 font-mono text-sm')
                ui.button('Retry', on_click=lambda: ui.timer(0, load_with_error_handling, once=True))
    
    finally:
        db.close()
```

---

## Performance Tips

1. **Keep skeleton delay minimal** (50-100ms)
2. **Load data in parallel when possible**
3. **Use progressive loading for independent sections**
4. **Cache expensive operations** (service layer)
5. **Optimize database queries** (use indexes, limit results)
6. **Consider pagination** for large datasets
7. **Profile slow operations** (logging, timing)

---

## Summary

**DO:**
- ✅ Use `ui.timer(0, async_func, once=True)` pattern
- ✅ Show skeleton UI immediately
- ✅ Close database sessions in `finally` blocks
- ✅ Handle errors gracefully with user-friendly messages
- ✅ Keep loading animations simple and tasteful

**DON'T:**
- ❌ Create nested containers (double wrapping)
- ❌ Use `run.cpu_bound` with database Sessions
- ❌ Reassign container variables (scoping issues)
- ❌ Forget to close resources
- ❌ Add loading animations to fast operations (< 100ms)

**Remember:** Loading animations are about **perceived performance**, not actual speed. They provide immediate feedback and make the app feel responsive even when data takes time to load.
