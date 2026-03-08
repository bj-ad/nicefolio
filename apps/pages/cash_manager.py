"""
Cash Manager Page - Manual Portfolio Management
Manage cash positions for Portfolios 1 (Liquid Cash) and 2 (Fixed Deposits)
Monthly updates with historical tracking and snapshot creation
"""

from nicegui import ui, app
from database import SessionLocal
from models import Portfolio, Transaction, CashPosition
from crud.crud_snapshot import create_snapshot, calculate_portfolio_value
from crud.crud_market_fx import get_latest_fx_rate
from sqlalchemy import func, desc
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Optional, List, Dict
import logging

from apps.core.layout import page_layout
from utils.portfolios_loader import get_portfolios_loader

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Portfolio IDs for cash portfolios (loaded from portfolio_config.yaml)
_portfolios_loader = get_portfolios_loader()
LIQUID_CASH_PORTFOLIO_ID = _portfolios_loader.get_portfolio_by_name('Liquid Cash')['id']
FIXED_DEPOSIT_PORTFOLIO_ID = _portfolios_loader.get_portfolio_by_name('Fixed Deposits')['id']

# Global state
positions_container = None
last_update_label = None
totals_label = None


def get_cash_positions(portfolio_id: int) -> List[Dict]:
    """Load all cash positions for a portfolio"""
    db = SessionLocal()
    try:
        positions = db.query(CashPosition).filter(
            CashPosition.portfolio_id == portfolio_id
        ).order_by(CashPosition.id).all()
        
        return [
            {
                'id': p.id,
                'label': p.label,
                'amount': float(p.amount),
                'currency': p.currency,
                'notes': p.notes or '',
                'last_updated': p.last_updated
            }
            for p in positions
        ]
    finally:
        db.close()


def save_cash_position(
    portfolio_id: int,
    label: str,
    amount: Decimal,
    currency: str,
    notes: str = '',
    position_id: Optional[int] = None
) -> bool:
    """Save (create or update) a cash position"""
    db = SessionLocal()
    try:
        if position_id:
            # Update existing position
            position = db.query(CashPosition).filter(CashPosition.id == position_id).first()
            if not position:
                ui.notify('Position not found', type='negative')
                return False
            
            position.label = label
            position.amount = amount
            position.currency = currency
            position.notes = notes
            # NOTE: last_updated is NOT set here - only when snapshot creation succeeds
            
            logger.info(f"Updated position {position_id}: {label} = {amount} {currency}")
            ui.notify(f'✅ Updated: {label}', type='positive')
        else:
            # Create new position
            # NOTE: last_updated will be set by database default (server_default=func.now())
            # but only gets updated when snapshot creation succeeds
            position = CashPosition(
                portfolio_id=portfolio_id,
                label=label,
                amount=amount,
                currency=currency,
                notes=notes
            )
            db.add(position)
            logger.info(f"Created position: {label} = {amount} {currency}")
            ui.notify(f'✅ Added: {label}', type='positive')
        
        db.commit()
        return True
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving position: {e}")
        ui.notify(f'❌ Error: {str(e)}', type='negative')
        return False
    finally:
        db.close()


def delete_cash_position(position_id: int) -> bool:
    """Delete a cash position"""
    db = SessionLocal()
    try:
        position = db.query(CashPosition).filter(CashPosition.id == position_id).first()
        if position:
            label = position.label
            db.delete(position)
            db.commit()
            logger.info(f"Deleted position: {label}")
            ui.notify(f'✅ Deleted: {label}', type='positive')
            return True
        else:
            ui.notify('Position not found', type='negative')
            return False
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting position: {e}")
        ui.notify(f'❌ Error: {str(e)}', type='negative')
        return False
    finally:
        db.close()


def convert_to_base_currency(amount: Decimal, from_currency: str, base_currency: str) -> Decimal:
    """Convert amount to base currency using latest FX rate"""
    if from_currency == base_currency:
        return amount
    
    db = SessionLocal()
    try:
        fx_pair = f"{from_currency}/{base_currency}"
        fx_rate_record = get_latest_fx_rate(db, fx_pair)
        
        if fx_rate_record:
            rate = Decimal(str(fx_rate_record.rate))
            converted = amount * rate
            logger.debug(f"Converted {amount} {from_currency} → {converted} {base_currency} (rate: {rate})")
            return converted
        else:
            logger.warning(f"No FX rate found for {fx_pair}, using 1:1")
            return amount
    finally:
        db.close()


def calculate_total_value(positions: List[Dict], base_currency: str) -> Dict:
    """Calculate total value in base currency with breakdown by currency"""
    total_base = Decimal('0')
    by_currency = {}
    
    for pos in positions:
        amount = Decimal(str(pos['amount']))
        currency = pos['currency']
        
        # Track by currency
        if currency not in by_currency:
            by_currency[currency] = Decimal('0')
        by_currency[currency] += amount
        
        # Convert to base currency
        amount_base = convert_to_base_currency(amount, currency, base_currency)
        total_base += amount_base
    
    return {
        'total_base': total_base,
        'by_currency': by_currency
    }


def create_snapshots_from_positions(snapshot_date: date = None) -> bool:
    """
    Create snapshots for both cash portfolios based on current positions.
    Also forward-fills from last snapshot to snapshot_date if there are gaps.
    
    CRITICAL: This function is ATOMIC - it either:
    1. Creates snapshots AND updates all position timestamps, OR
    2. Rolls back everything if any error occurs
    
    This prevents the bug where positions have updated timestamps but no snapshots exist.
    """
    if snapshot_date is None:
        snapshot_date = date.today()
    
    db = SessionLocal()
    try:
        # Get base currency
        from utils.app_config import load_app_config
        config = load_app_config()
        base_currency = config.get('base_currency', 'EUR')
        
        success_count = 0
        snapshot_timestamp = datetime.now(timezone.utc)
        
        for portfolio_id in [LIQUID_CASH_PORTFOLIO_ID, FIXED_DEPOSIT_PORTFOLIO_ID]:
            # Get portfolio
            portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            if not portfolio:
                logger.error(f"Portfolio {portfolio_id} not found")
                continue
            
            # Get positions
            positions = get_cash_positions(portfolio_id)
            
            if not positions:
                logger.warning(f"No positions found for portfolio {portfolio.name}")
                continue
            
            # Calculate total value
            value_data = calculate_total_value(positions, base_currency)
            total_value = value_data['total_base']
            
            # For cash portfolios, total_invested = total_value (no unrealized gains)
            # Realized P&L = 0 (cash doesn't have gains/losses from price changes)
            
            # Create snapshot
            snapshot = create_snapshot(
                db=db,
                portfolio_id=portfolio_id,
                snapshot_date=snapshot_date,
                total_value_base=total_value,
                currency_base=base_currency,
                total_invested_base=total_value,  # For cash: invested = value
                realized_pnl_base=Decimal('0'),
                unrealized_pnl_base=Decimal('0'),
                notes=f"Manual update via Cash Manager. Positions: {len(positions)}"
            )
            
            logger.info(
                f"Created snapshot for {portfolio.name}: "
                f"{total_value} {base_currency} ({len(positions)} positions)"
            )
            
            # Interpolate between previous and current snapshot (replaces forward-filled values)
            # Note: Forward-fill to today is now handled by daily worker job at 02:15
            interpolate_snapshots(db, portfolio_id, snapshot_date)
            
            # CRITICAL: Update last_updated for ALL positions in this portfolio
            # This happens AFTER snapshot creation succeeds, within same transaction
            position_ids = [p['id'] for p in positions]
            db.query(CashPosition).filter(
                CashPosition.id.in_(position_ids)
            ).update(
                {CashPosition.last_updated: snapshot_timestamp},
                synchronize_session=False
            )
            
            logger.info(f"Updated last_updated for {len(position_ids)} positions in portfolio {portfolio_id}")
            
            success_count += 1
        
        # Commit everything atomically: snapshots + interpolations + timestamp updates
        db.commit()
        
        if success_count == 2:
            ui.notify(f'✅ Created snapshots for {snapshot_date}', type='positive')
            return True
        else:
            ui.notify(f'⚠️ Only created {success_count}/2 snapshots', type='warning')
            return False
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating snapshots: {e}", exc_info=True)
        ui.notify(f'❌ Error creating snapshots: {str(e)}', type='negative')
        return False
    finally:
        db.close()


def interpolate_snapshots(db, portfolio_id: int, new_snapshot_date: date):
    """
    Interpolate snapshots between previous manual update and new update.
    
    This creates a smooth curve by filling gaps with interpolated values.
    Forward-fill to today is handled by the daily worker job.
    
    Logic:
    1. Find previous MANUAL entry date from cash_positions (robust, not based on notes parsing)
    2. Calculate gap in days
    3. Create interpolated snapshots for each day in the gap
    4. OVERWRITES any existing forward-filled snapshots with interpolated values
    
    Example:
        Oct 31: 50,000 EUR (previous manual entry from cash_positions)
        Nov 1-26: Forward-filled by daily job (will be overwritten)
        Nov 27: 65,000 EUR (new manual entry) ← User just entered
        
        Result:
        Oct 31: 50,000 EUR ━━┓ (preserved - manual entry)
        Nov 1:  50,556 EUR    ┃
        Nov 2:  51,111 EUR    ┃ Linear interpolation
        ...                   ┃ (REPLACES forward-filled values)
        Nov 26: 64,444 EUR    ┃
        Nov 27: 65,000 EUR ━━┛ (preserved - manual entry)
        Nov 28+: Handled by daily forward-fill job
    
    This creates a nice-looking graph without harsh steps!
    
    How we identify manual entries:
    - cash_positions.last_updated is set whenever user updates via Cash Manager
    - Query: MAX(last_updated) WHERE last_updated < new_snapshot_date
    - This gives us the previous manual entry date (robust, not dependent on notes)
    """
    from models import Snapshot, CashPosition
    from sqlalchemy import func
    
    try:
        # Get the new snapshot (just created by user)
        new_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date == new_snapshot_date
        ).first()
        
        if not new_snapshot:
            logger.warning(f"No snapshot found at {new_snapshot_date} for portfolio {portfolio_id}")
            return
        
        # Find previous MANUAL entry date from cash_positions
        # This is robust - cash_positions.last_updated is set when user enters data
        # Use func.date() to extract date from timestamp for comparison
        previous_manual_date_result = db.query(
            func.date(func.max(CashPosition.last_updated))
        ).filter(
            CashPosition.portfolio_id == portfolio_id,
            func.date(CashPosition.last_updated) < new_snapshot_date
        ).scalar()
        
        if not previous_manual_date_result:
            logger.info(f"No previous manual entry found in cash_positions for portfolio {portfolio_id}")
            return
        
        # Result is already a date object from func.date()
        previous_manual_date = previous_manual_date_result
        
        # Get the snapshot from that manual entry date
        previous_snapshot = db.query(Snapshot).filter(
            Snapshot.portfolio_id == portfolio_id,
            Snapshot.snapshot_date == previous_manual_date
        ).first()
        
        # INTERPOLATE from previous to new (if gap exists)
        if previous_snapshot:
            gap_days = (new_snapshot_date - previous_snapshot.snapshot_date).days
            
            if gap_days > 1:
                # Calculate daily increments (linear interpolation)
                value_diff = new_snapshot.total_value_base - previous_snapshot.total_value_base
                invested_diff = new_snapshot.total_invested_base - previous_snapshot.total_invested_base
                
                daily_value_increment = value_diff / Decimal(str(gap_days))
                daily_invested_increment = invested_diff / Decimal(str(gap_days))
                
                # Interpolate NAV price as well (units stay constant during interpolation)
                # For cash portfolios, NAV units are based on deposits which don't change during interpolation
                prev_nav_units = previous_snapshot.nav_units
                prev_nav_price = previous_snapshot.nav_price or Decimal('100')
                new_nav_price = new_snapshot.nav_price or Decimal('100')
                nav_price_diff = new_nav_price - prev_nav_price
                daily_nav_price_increment = nav_price_diff / Decimal(str(gap_days))
                
                logger.info(
                    f"Interpolating {gap_days} days: "
                    f"{previous_snapshot.total_value_base} → {new_snapshot.total_value_base} "
                    f"(Δ{daily_value_increment}/day)"
                )
                
                # Fill the gap with interpolated values (OVERWRITES forward-filled snapshots)
                for day_offset in range(1, gap_days):
                    interpolate_date = previous_snapshot.snapshot_date + timedelta(days=day_offset)
                    
                    # Calculate interpolated value for this day
                    interpolated_value = (
                        previous_snapshot.total_value_base + 
                        (daily_value_increment * Decimal(str(day_offset)))
                    )
                    interpolated_invested = (
                        previous_snapshot.total_invested_base + 
                        (daily_invested_increment * Decimal(str(day_offset)))
                    )
                    
                    # Interpolate NAV price (units stay constant)
                    interpolated_nav_price = prev_nav_price + (daily_nav_price_increment * Decimal(str(day_offset)))
                    
                    # Create or update snapshot with interpolated value
                    existing = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == portfolio_id,
                        Snapshot.snapshot_date == interpolate_date
                    ).first()
                    
                    if existing:
                        # Update existing (OVERWRITE forward-filled value with interpolated)
                        existing.total_value_base = interpolated_value
                        existing.total_invested_base = interpolated_invested
                        existing.nav_units = prev_nav_units  # Units stay constant
                        existing.nav_price = interpolated_nav_price
                        existing.notes = f"Interpolated between manual updates"
                        logger.debug(f"  Overwrote forward-filled snapshot at {interpolate_date}")
                    else:
                        # Create new
                        create_snapshot(
                            db=db,
                            portfolio_id=portfolio_id,
                            snapshot_date=interpolate_date,
                            total_value_base=interpolated_value,
                            currency_base=new_snapshot.currency_base,
                            total_invested_base=interpolated_invested,
                            realized_pnl_base=Decimal('0'),
                            unrealized_pnl_base=Decimal('0'),
                            nav_units=prev_nav_units,
                            nav_price=interpolated_nav_price,
                            notes=f"Interpolated between manual updates (day {day_offset}/{gap_days})"
                        )
                
                db.commit()
                logger.info(f"Interpolated {gap_days - 1} snapshots between manual updates")
        
    except Exception as e:
        logger.error(f"Error interpolating snapshots: {e}", exc_info=True)


def refresh_ui():
    """Refresh all UI components"""
    global positions_container, last_update_label, totals_label
    
    if positions_container:
        positions_container.clear()
        render_portfolios(positions_container)
    
    if last_update_label:
        update_last_update_label()
    
    if totals_label:
        update_totals_label()


def update_last_update_label():
    """Update the last update timestamp"""
    db = SessionLocal()
    try:
        # Get most recent update from either portfolio
        last_update = db.query(func.max(CashPosition.last_updated)).filter(
            CashPosition.portfolio_id.in_([LIQUID_CASH_PORTFOLIO_ID, FIXED_DEPOSIT_PORTFOLIO_ID])
        ).scalar()
        
        if last_update and last_update_label:
            last_update_label.text = f"Last Updated: {last_update.strftime('%Y-%m-%d %H:%M')}"
    finally:
        db.close()


def update_totals_label():
    """Update the combined total label"""
    from utils.app_config import load_app_config
    config = load_app_config()
    base_currency = config.get('base_currency', 'EUR')
    
    # Calculate totals for both portfolios
    liquid_positions = get_cash_positions(LIQUID_CASH_PORTFOLIO_ID)
    fixed_positions = get_cash_positions(FIXED_DEPOSIT_PORTFOLIO_ID)
    
    liquid_total = calculate_total_value(liquid_positions, base_currency)['total_base']
    fixed_total = calculate_total_value(fixed_positions, base_currency)['total_base']
    combined_total = liquid_total + fixed_total
    
    if totals_label:
        totals_label.text = f"COMBINED TOTAL: {combined_total:,.2f} {base_currency}"


def show_add_position_dialog(portfolio_id: int, portfolio_name: str):
    """Show dialog to add new cash position"""
    
    with ui.dialog() as dialog, ui.card().classes('w-96'):
        ui.label(f'Add Position - {portfolio_name}').classes('text-xl font-bold mb-4')
        
        # Form fields
        label_input = ui.input(
            label='Label *',
            placeholder='e.g., K-Bank Savings, SCB Fixed 12m'
        ).classes('w-full')
        
        amount_input = ui.number(
            label='Amount *',
            format='%.2f',
            value=0.00
        ).classes('w-full')
        
        currency_select = ui.select(
            label='Currency *',
            options=['EUR', 'USD', 'THB', 'GBP', 'JPY', 'CNY'],
            value='EUR'
        ).classes('w-full')
        
        notes_input = ui.textarea(
            label='Notes (optional)',
            placeholder='Add any additional information...'
        ).classes('w-full')
        
        # Buttons
        with ui.row().classes('w-full justify-end mt-4 gap-2'):
            ui.button('Cancel', on_click=dialog.close).classes('bg-gray-500')
            ui.button('Add Position', on_click=lambda: (
                save_cash_position(
                    portfolio_id,
                    label_input.value.strip(),
                    Decimal(str(amount_input.value)),
                    currency_select.value,
                    notes_input.value.strip()
                ) and refresh_ui() and dialog.close()
            )).classes('bg-blue-500')
    
    dialog.open()


def show_edit_position_dialog(position: Dict, portfolio_name: str):
    """Show dialog to edit existing cash position"""
    
    with ui.dialog() as dialog, ui.card().classes('w-96'):
        ui.label(f'Edit Position - {portfolio_name}').classes('text-xl font-bold mb-4')
        
        # Form fields pre-filled
        label_input = ui.input(
            label='Label *',
            value=position['label']
        ).classes('w-full')
        
        amount_input = ui.number(
            label='Amount *',
            format='%.2f',
            value=position['amount']
        ).classes('w-full')
        
        currency_select = ui.select(
            label='Currency *',
            options=['EUR', 'USD', 'THB', 'GBP', 'JPY', 'CNY'],
            value=position['currency']
        ).classes('w-full')
        
        notes_input = ui.textarea(
            label='Notes (optional)',
            value=position['notes']
        ).classes('w-full')
        
        # Buttons
        with ui.row().classes('w-full justify-end mt-4 gap-2'):
            ui.button('Cancel', on_click=dialog.close).classes('bg-gray-500')
            ui.button('Save Changes', on_click=lambda: (
                save_cash_position(
                    position['id'],  # Pass position_id to update
                    label_input.value.strip(),
                    Decimal(str(amount_input.value)),
                    currency_select.value,
                    notes_input.value.strip(),
                    position_id=position['id']
                ) and refresh_ui() and dialog.close()
            )).classes('bg-green-500')
    
    dialog.open()


def show_delete_confirmation(position_id: int, position_label: str):
    """Show confirmation dialog for deletion"""
    
    with ui.dialog() as dialog, ui.card().classes('w-96'):
        ui.label('Confirm Deletion').classes('text-xl font-bold mb-4')
        ui.label(f'Are you sure you want to delete "{position_label}"?').classes('mb-4')
        ui.label('This action cannot be undone.').classes('text-red-500 text-sm mb-4')
        
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancel', on_click=dialog.close).classes('bg-gray-500')
            ui.button('Delete', on_click=lambda: (
                delete_cash_position(position_id) and refresh_ui() and dialog.close()
            )).classes('bg-red-500')
    
    dialog.open()


def render_portfolio_column(container, portfolio_id: int, portfolio_name: str):
    """Render a portfolio column with its positions"""
    from utils.app_config import load_app_config
    config = load_app_config()
    base_currency = config.get('base_currency', 'EUR')
    
    with container:
        with ui.card().classes('w-full h-full'):
            # Header
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label(portfolio_name).classes('text-2xl font-bold')
                ui.button(
                    icon='add',
                    on_click=lambda: show_add_position_dialog(portfolio_id, portfolio_name)
                ).props('fab-mini color=blue-500').tooltip('Add Position')
            
            ui.separator()
            
            # Get positions
            positions = get_cash_positions(portfolio_id)
            
            if not positions:
                ui.label('No positions yet. Click + to add one.').classes('text-gray-500 mt-4 text-center')
            else:
                # Render each position
                for pos in positions:
                    with ui.card().classes('w-full mb-2 bg-gray-100'):
                        with ui.row().classes('w-full justify-between items-center'):
                            # Label and amount
                            with ui.column().classes('flex-grow'):
                                ui.label(pos['label']).classes('font-bold text-gray-900')
                                ui.label(f"{pos['amount']:,.2f} {pos['currency']}").classes('text-lg text-blue-600')
                                if pos['notes']:
                                    ui.label(pos['notes']).classes('text-xs text-gray-600')
                            
                            # Action buttons
                            with ui.row().classes('gap-1'):
                                ui.button(
                                    icon='edit',
                                    on_click=lambda p=pos: show_edit_position_dialog(p, portfolio_name)
                                ).props('flat dense').tooltip('Edit')
                                ui.button(
                                    icon='delete',
                                    on_click=lambda pid=pos['id'], label=pos['label']: show_delete_confirmation(pid, label)
                                ).props('flat dense color=red').tooltip('Delete')
            
            ui.separator().classes('my-4')
            
            # Total for this portfolio
            if positions:
                value_data = calculate_total_value(positions, base_currency)
                total_base = value_data['total_base']
                by_currency = value_data['by_currency']
                
                ui.label('Total:').classes('font-bold text-gray-400')
                ui.label(f"{total_base:,.2f} {base_currency}").classes('text-2xl font-bold text-green-400')
                
                # Show breakdown by currency
                if len(by_currency) > 1:
                    ui.label('Breakdown:').classes('text-xs text-gray-500 mt-2')
                    for curr, amt in by_currency.items():
                        ui.label(f"  • {amt:,.2f} {curr}").classes('text-xs text-gray-400')


def render_portfolios(container):
    """Render both portfolio columns"""
    with container:
        with ui.grid(columns=2).classes('w-full gap-4'):
            # Column 1: Liquid Cash
            render_portfolio_column(
                ui.column().classes('col-span-1'),
                LIQUID_CASH_PORTFOLIO_ID,
                'LIQUID CASH (Portfolio 1)'
            )
            
            # Column 2: Fixed Deposits
            render_portfolio_column(
                ui.column().classes('col-span-1'),
                FIXED_DEPOSIT_PORTFOLIO_ID,
                'FIXED DEPOSITS (Portfolio 2)'
            )


def cash_manager_content():
    """Main cash manager page content"""
    global positions_container, last_update_label, totals_label
    
    # Info banner
    with ui.card().classes('w-full bg-blue-900'):
        with ui.row().classes('items-center gap-4 p-4'):
            with ui.column().classes('flex-grow'):
                ui.label('Monthly Cash Portfolio Updates').classes('text-white text-xl font-bold')
                ui.label('Enter your current cash positions and save to create snapshots. Values are forward-filled until next update.').classes('text-white opacity-90')
    
    # Last update info
    last_update_label = ui.label('').classes('text-gray-400')
    update_last_update_label()
    
    ui.separator()
    
    # Portfolios container
    positions_container = ui.column().classes('w-full')
    render_portfolios(positions_container)
    
    ui.separator().classes('my-6')
    
    # Combined total
    totals_label = ui.label('').classes('text-3xl font-bold text-center text-green-400 mb-4')
    update_totals_label()
    
    # Action buttons
    with ui.row().classes('w-full justify-end gap-4 mt-6'):
        ui.button(
            'Cancel',
            icon='cancel',
            on_click=lambda: ui.navigate.to('/')
        ).props('flat').classes('bg-gray-600')
        
        ui.button(
            'Save & Create Snapshot',
            icon='save',
            on_click=lambda: create_snapshots_from_positions() and ui.notify('✅ Snapshot saved! Forward-fill applied.', type='positive')
        ).props('').classes('bg-green-600 text-white text-lg px-8 py-4')


@ui.page('/cash-manager')
def cash_manager():
    """Cash manager page"""
    
    with page_layout('/cash-manager'):
        cash_manager_content()
