"""
Transaction Review Page
Review and edit unverified transactions before they are included in portfolio calculations.
"""

from nicegui import ui, app
from datetime import datetime, timedelta
from decimal import Decimal

from database import SessionLocal
from models import Portfolio, Account, Transaction, MarketData, FxRate, TransactionType
from utils.app_config import get_global_base_currency
from utils.portfolios_loader import get_portfolios_loader
from utils.datetime_utils import now_utc
from apps.core.layout import page_layout
from sqlalchemy import and_


@ui.page('/transaction-review')
def transaction_review():
    """Transaction Review page - Review and edit unverified transactions"""
    
    # Header controls: filters and portfolio selector
    def header_controls():
        show_approved = app.storage.user.get('review_show_approved', False)
        transactions_per_account = app.storage.user.get('review_txs_per_account', 20)
        selected_portfolio_id = app.storage.user.get('review_portfolio_id', None)
        
        with ui.row().classes('items-center gap-2 lg:gap-4 flex-wrap'):
            
            # Show approved toggle
            with ui.row().classes('items-center gap-1 lg:gap-2'):
                # Quasar 'lt-md': Visible only when width < 1024px (Mobile/Tablet)
                ui.label('All:').classes('text-white text-xs lt-md')
                
                # Quasar 'gt-sm': Visible only when width >= 1024px (Desktop)
                ui.label('Show All:').classes('text-white text-sm gt-sm')
                
                ui.switch(value=show_approved, on_change=lambda e: handle_show_approved_change(e.value)).props('color=white dense')

            # Per account selector
            with ui.row().classes('items-center gap-1 lg:gap-2'):
                # 'gt-sm' hides this label on mobile, shows it on desktop
                ui.label('Per Account:').classes('text-white text-sm gt-sm')
                
                ui.select(
                    options={10: '10', 20: '20', 50: '50', 100: '100', 999999: 'All'},
                    value=transactions_per_account,
                    on_change=lambda e: handle_per_account_change(e.value)
                ).classes('bg-white text-gray-800 w-18 lg:w-20').props('outlined dense')

            
            # Portfolio selector
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
            ).classes('bg-white text-gray-800 w-[130px] lg:w-[180px]').props('outlined dense')
    
    def handle_show_approved_change(value):
        app.storage.user['review_show_approved'] = value
        app.storage.user['selected_tx_ids'] = []
        ui.navigate.reload()
    
    def handle_per_account_change(value):
        app.storage.user['review_txs_per_account'] = value
        ui.navigate.reload()
    
    def handle_portfolio_change(portfolio_id):
        app.storage.user['review_portfolio_id'] = portfolio_id
        app.storage.user['selected_tx_ids'] = []
        ui.navigate.reload()
    
    with page_layout('/transaction-review', header_content=header_controls):
        _transaction_review_content_wrapper()


@ui.refreshable
def _transaction_review_content_wrapper():
    """Refreshable wrapper for transaction review - direct render (no spinner needed)"""
    # Transactions load quickly from database (~20-50ms), no spinner needed
    _transaction_review_content()


def _transaction_review_content():
    """Transaction review page content"""
    
    # State management - use app.storage.user to persist across refreshes
    selected_portfolio_id = app.storage.user.get('review_portfolio_id', None)
    show_approved = app.storage.user.get('review_show_approved', False)
    transactions_per_account = app.storage.user.get('review_txs_per_account', 20)  # Default: 20 transactions per account
    
    # Initialize selected_tx_ids in storage if not exists
    if 'selected_tx_ids' not in app.storage.user:
        app.storage.user['selected_tx_ids'] = []
    
    # Use SessionLocal for database
    db = SessionLocal()
    try:
        # Determine which portfolios to show
        if selected_portfolio_id:
            # Single portfolio view
            portfolio = db.query(Portfolio).filter(Portfolio.id == selected_portfolio_id).first()
            
            if not portfolio:
                with ui.card().classes('w-full p-8 text-center'):
                    ui.icon('error', size='4rem').classes('text-red-400 mb-4')
                    ui.label('Portfolio not found').classes('text-xl text-red-500')
                return
            
            portfolio_filter = [selected_portfolio_id]
            portfolio_name = portfolio.name
            portfolio_base = portfolio.currency_base
        else:
            # All portfolios view - exclude only placeholders
            portfolios_loader = get_portfolios_loader()
            all_portfolios = portfolios_loader.get_portfolios()
            portfolio_filter = [p['id'] for p in all_portfolios if p.get('type') != 'placeholder']
            portfolio_name = 'All Portfolios'
            portfolio_base = get_global_base_currency()  # Use global base currency from app_config.yaml
        
        # Get all accounts that have transactions in the selected portfolio(s)
        # Filter based on show_approved toggle
        if show_approved:
            # Show all transactions (approved and unapproved)
            accounts_with_txs = db.query(Account).join(Transaction).filter(
                Transaction.portfolio_id.in_(portfolio_filter)
            ).distinct().order_by(Account.name).all()
        else:
            # Show only unapproved transactions (default)
            accounts_with_txs = db.query(Account).join(Transaction).filter(
                Transaction.portfolio_id.in_(portfolio_filter),
                Transaction.reviewed == False
            ).distinct().order_by(Account.name).all()
        
        # Function to create manual transaction (defined here for use in multiple places)
        async def create_manual_transaction():
            """Open dialog to create a manual transaction"""
            # Get all portfolios and accounts for dropdowns
            db_dialog = SessionLocal()
            try:
                # Get portfolios (exclude placeholders)
                portfolios_loader = get_portfolios_loader()
                all_portfolios = portfolios_loader.get_portfolios()
                portfolio_options = {p['id']: p['name'] for p in all_portfolios if p.get('type') != 'placeholder'}
                
                # Get all accounts
                all_accounts = db_dialog.query(Account).order_by(Account.name).all()
                account_options = {acc.id: f"{acc.name} ({acc.type or 'N/A'})" for acc in all_accounts}
                
                # Transaction type options - from TransactionType enum
                tx_type_options = TransactionType.values()
                
                # Currency options - hardcoded (ECB supports EUR, USD, THB)
                currency_options = ['EUR', 'THB', 'USD']
                
                # Form data dictionary
                form_data = {
                    'portfolio_id': list(portfolio_options.keys())[0] if portfolio_options else None,
                    'account_id': list(account_options.keys())[0] if account_options else None,
                    'occurred_at': None,
                    'type': 'sell',
                    'symbol': '',
                    'quantity': '',
                    'price': '',
                    'value_native': '',
                    'currency_native': 'EUR',
                    'fee': '0',
                    'withholding_tax': '0',
                    'notes': '',
                    'exchange_rate_to_base': '',
                    'fetched_fx_rate': None  # Store fetched FX rate
                }
                
                with ui.dialog().props('persistent') as dialog, ui.card().classes('p-6 w-full max-w-4xl'):
                    ui.label('Create Manual Transaction').classes('text-2xl font-bold mb-4')
                    ui.label('Enter transaction details. FX rates will be fetched automatically.').classes('text-sm text-gray-600 mb-4')
                    
                    # Create a column for the form
                    with ui.column().classes('gap-2 w-full'):
                        
                        # Row 1: Portfolio and Account
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Portfolio *').classes('text-sm font-semibold')
                                portfolio_select = ui.select(
                                    options=portfolio_options,
                                    value=form_data['portfolio_id'],
                                    on_change=lambda e: form_data.update({'portfolio_id': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Account *').classes('text-sm font-semibold')
                                account_select = ui.select(
                                    options=account_options,
                                    value=form_data['account_id'],
                                    on_change=lambda e: form_data.update({'account_id': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 2: Date and Type
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Date *').classes('text-sm font-semibold')                                
                                max_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                                date_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_date_change(e.value)
                                ).props(f'type=date outlined dense max={max_date}').classes('w-full')
                                ui.label('Past dates only').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Transaction Type *').classes('text-sm font-semibold')
                                type_select = ui.select(
                                    options=tx_type_options,
                                    value=form_data['type'],
                                    on_change=lambda e: form_data.update({'type': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 3: Symbol and Quantity
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Symbol').classes('text-sm font-semibold')                                
                                symbol_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'symbol': e.value})
                                ).props('outlined dense').classes('w-full')
                                ui.label('For stocks/crypto').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Quantity').classes('text-sm font-semibold')                                
                                quantity_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_quantity_change(e.value)
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Negative for sell/outflows').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 4: Value Native and Price (auto-calculated)
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Value * (gross, before fees)').classes('text-sm font-semibold')                                
                                value_native_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_value_change(e.value)
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Negative for sell/outflows').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Price per Unit').classes('text-sm font-semibold')                                
                                price_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'price': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Auto-calculated').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 5: Currency and FX Rate
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Currency (Native) *').classes('text-sm font-semibold')
                                currency_select = ui.select(
                                    options=currency_options,
                                    value=form_data['currency_native'],
                                    on_change=lambda e: handle_currency_change(e.value)
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Exchange Rate to Base').classes('text-sm font-semibold')                                
                                fx_rate_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'exchange_rate_to_base': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                fx_status_label = ui.label('').classes('text-xs text-gray-600')
                                ui.label('Auto-fetched').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 6: Fee and Withholding Tax (deducted separately)
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Fee').classes('text-sm font-semibold')                                
                                fee_input = ui.input(
                                    value='0',
                                    on_change=lambda e: form_data.update({'fee': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Deducted separately').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Withholding Tax').classes('text-sm font-semibold')                                
                                tax_input = ui.input(
                                    value='0',
                                    on_change=lambda e: form_data.update({'withholding_tax': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Deducted separately').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 7: Notes
                        with ui.column().classes('w-full gap-1'):
                            ui.label('Notes (Optional)').classes('text-sm font-semibold')
                            notes_input = ui.textarea(
                                value='',
                                on_change=lambda e: form_data.update({'notes': e.value})
                            ).props('outlined').classes('w-full')
                        
                        # Helper functions for auto-population and calculations
                        def handle_date_change(date_str):
                            """Handle date change and fetch FX rate"""
                            form_data['occurred_at'] = date_str
                            fetch_fx_rate()
                        
                        def handle_currency_change(currency):
                            """Handle currency change and fetch FX rate"""
                            form_data['currency_native'] = currency
                            fetch_fx_rate()
                        
                        def handle_quantity_change(quantity_str):
                            """Handle quantity change and auto-calculate price"""
                            form_data['quantity'] = quantity_str
                            calculate_price()
                        
                        def handle_value_change(value_str):
                            """Handle value change and auto-calculate price"""
                            form_data['value_native'] = value_str
                            calculate_price()
                        
                        def calculate_price():
                            """Auto-calculate price from value / quantity"""
                            try:
                                if form_data['value_native'] and form_data['quantity']:
                                    value = float(form_data['value_native'])
                                    quantity = float(form_data['quantity'])
                                    if quantity != 0:
                                        price = abs(value / quantity)
                                        form_data['price'] = f'{price:.8f}'
                                        price_input.set_value(form_data['price'])
                            except (ValueError, ZeroDivisionError):
                                pass  # Ignore calculation errors, user can enter manually
                        
                        def fetch_fx_rate():
                            """Fetch FX rate from database based on date and currency"""
                            if not form_data['occurred_at'] or not form_data['currency_native']:
                                return
                            
                            db_fx = SessionLocal()
                            try:
                                # Get the selected portfolio's base currency
                                portfolio = db_fx.query(Portfolio).filter(Portfolio.id == form_data['portfolio_id']).first()
                                if not portfolio:
                                    fx_status_label.set_text('⚠️ Portfolio not found')
                                    return
                                
                                base_currency = portfolio.currency_base
                                native_currency = form_data['currency_native']
                                
                                # If currencies are the same, rate is 1.0
                                if base_currency == native_currency:
                                    form_data['exchange_rate_to_base'] = '1.0'
                                    form_data['fetched_fx_rate'] = Decimal('1.0')
                                    fx_rate_input.set_value('1.0')
                                    fx_status_label.set_text('✓ Same currency, rate = 1.0')
                                    return
                                
                                # Parse the date
                                tx_date = datetime.strptime(form_data['occurred_at'], '%Y-%m-%d').date()
                                
                                # ECB FX rate lookup with cross-rate calculation
                                # ECB only publishes rates with EUR as base: EUR/USD, EUR/THB
                                # For cross-rates (e.g., USD→THB), we calculate: rate = (EUR/THB) / (EUR/USD)
                                
                                # Case 1: native == EUR (e.g., EUR→USD or EUR→THB)
                                if native_currency == 'EUR':
                                    # Look for EUR/base (e.g., EUR/USD) on or before transaction date
                                    fx_pair = f"EUR/{base_currency}"
                                    fx_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == fx_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if fx_record and fx_record.rate:
                                        rate = fx_record.rate
                                        form_data['exchange_rate_to_base'] = str(rate)
                                        form_data['fetched_fx_rate'] = rate
                                        fx_rate_input.set_value(str(rate))
                                        fx_status_label.set_text(f'✓ Fetched: {rate} ({fx_record.as_of_date})')
                                    else:
                                        fx_status_label.set_text(f'⚠️ No rate found for {fx_pair}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                                
                                # Case 2: base == EUR (e.g., USD→EUR or THB→EUR)
                                elif base_currency == 'EUR':
                                    # Look for EUR/native (e.g., EUR/USD) on or before transaction date
                                    fx_pair = f"EUR/{native_currency}"
                                    fx_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == fx_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if fx_record and fx_record.rate:
                                        rate = Decimal('1.0') / fx_record.rate
                                        form_data['exchange_rate_to_base'] = str(rate)
                                        form_data['fetched_fx_rate'] = rate
                                        fx_rate_input.set_value(str(rate))
                                        fx_status_label.set_text(f'✓ Fetched: {rate} ({fx_record.as_of_date})')
                                    else:
                                        fx_status_label.set_text(f'⚠️ No rate found for {fx_pair}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                                
                                # Case 3: Cross-rate (e.g., USD→THB or THB→USD)
                                else:
                                    # Need two rates on or before transaction date: EUR/native and EUR/base
                                    # Formula: native→base = (EUR/base) / (EUR/native)
                                    eur_native_pair = f"EUR/{native_currency}"
                                    eur_base_pair = f"EUR/{base_currency}"
                                    
                                    eur_native_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == eur_native_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    eur_base_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == eur_base_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if eur_native_record and eur_native_record.rate and eur_base_record and eur_base_record.rate:
                                        cross_rate = eur_base_record.rate / eur_native_record.rate
                                        form_data['exchange_rate_to_base'] = str(cross_rate)
                                        form_data['fetched_fx_rate'] = cross_rate
                                        fx_rate_input.set_value(str(cross_rate))
                                        fx_status_label.set_text(f'✓ Cross-rate: {cross_rate} (from {eur_native_record.as_of_date}, {eur_base_record.as_of_date})')
                                    else:
                                        missing = []
                                        if not eur_native_record:
                                            missing.append(eur_native_pair)
                                        if not eur_base_record:
                                            missing.append(eur_base_pair)
                                        fx_status_label.set_text(f'⚠️ Missing rates: {", ".join(missing)}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                            
                            except Exception as e:
                                fx_status_label.set_text(f'❌ Error fetching FX rate: {str(e)}')
                                form_data['exchange_rate_to_base'] = ''
                                form_data['fetched_fx_rate'] = None
                            finally:
                                db_fx.close()
                        
                        # Validation and Save
                        with ui.row().classes('gap-2 justify-end w-full mt-6'):
                            ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat').classes('bg-gray-500 text-white')
                            ui.button('Create Transaction', on_click=lambda: dialog.submit(True)).props('flat').classes('bg-emerald-700 text-white')
                
                result = await dialog
                
                if result:
                    # Validate required fields (only value_native is required, quantity/price are optional)
                    errors = []
                    
                    if not form_data['portfolio_id']:
                        errors.append('Portfolio is required')
                    if not form_data['account_id']:
                        errors.append('Account is required')
                    if not form_data['occurred_at']:
                        errors.append('Date is required')
                    if not form_data['type']:
                        errors.append('Transaction type is required')
                    if not form_data['value_native']:
                        errors.append('Value (Native Currency) is required - enter the gross value before fees/taxes')
                    if not form_data['currency_native']:
                        errors.append('Currency is required')
                    if not form_data['exchange_rate_to_base']:
                        errors.append('Exchange rate is required (should be auto-fetched)')
                    
                    if errors:
                        ui.notify('Validation errors:\n' + '\n'.join(errors), type='negative', position='top', timeout=5000)
                        return
                    
                    # Create the transaction
                    db_create = SessionLocal()
                    try:
                        # Get portfolio base currency
                        portfolio = db_create.query(Portfolio).filter(Portfolio.id == form_data['portfolio_id']).first()
                        if not portfolio:
                            ui.notify('Portfolio not found', type='negative')
                            return
                        
                        # Parse datetime
                        occurred_at = datetime.strptime(form_data['occurred_at'], '%Y-%m-%d')
                        
                        # Calculate value_base
                        value_native = Decimal(form_data['value_native'])
                        exchange_rate = Decimal(form_data['exchange_rate_to_base'])
                        value_base = value_native * exchange_rate
                        
                        # Prepare notes with manual entry prefix
                        user_notes = form_data['notes'].strip() if form_data['notes'] else ''
                        notes_text = f"Manually entered transaction - {user_notes}" if user_notes else "Manually entered transaction"
                        
                        # Create transaction object
                        new_tx = Transaction(
                            portfolio_id=form_data['portfolio_id'],
                            account_id=form_data['account_id'],
                            occurred_at=occurred_at,
                            type=form_data['type'],
                            symbol=form_data['symbol'] or None,
                            quantity=Decimal(form_data['quantity']) if form_data['quantity'] else None,
                            price=Decimal(form_data['price']) if form_data['price'] else None,
                            value_native=value_native,
                            currency_native=form_data['currency_native'],
                            value_base=value_base,
                            currency_base=portfolio.currency_base,
                            exchange_rate_to_base=exchange_rate,
                            fee=Decimal(form_data['fee']) if form_data['fee'] else Decimal('0'),
                            fee_currency=form_data['currency_native'],  # Fee in same currency as transaction
                            withholding_tax=Decimal(form_data['withholding_tax']) if form_data['withholding_tax'] else Decimal('0'),
                            withholding_tax_currency=form_data['currency_native'] if form_data['withholding_tax'] and float(form_data['withholding_tax']) > 0 else None,
                            notes=notes_text,
                            source='manual',  # Mark as manually entered
                            reviewed=True,  # Manual transactions are pre-approved
                            last_updated=now_utc()
                        )
                        
                        db_create.add(new_tx)
                        db_create.commit()
                        
                        ui.notify(f'Transaction created successfully (TX-{new_tx.id})', type='positive')
                        _transaction_review_content_wrapper.refresh()
                    
                    except Exception as e:
                        db_create.rollback()
                        ui.notify(f'Error creating transaction: {str(e)}', type='negative', timeout=5000)
                    finally:
                        db_create.close()
            
            finally:
                db_dialog.close()
        
        if not accounts_with_txs:
            with ui.card().classes('w-full p-8 text-center'):
                ui.icon('info', size='4rem').classes('text-gray-400 mb-4')
                if show_approved:
                    ui.label('No transactions found').classes('text-xl text-gray-500')
                    ui.label(f'No transactions in {portfolio_name}').classes('text-gray-400 mt-2')
                else:
                    ui.label('No unapproved transactions found').classes('text-xl text-gray-500')
                    ui.label(f'All transactions in {portfolio_name} have been reviewed').classes('text-gray-400 mt-2')
                
                # Add "Create Transaction" button
                with ui.row().classes('justify-center mt-6'):
                    ui.button('Create Transaction', on_click=create_manual_transaction, icon='add_circle').props('flat').classes('bg-purple-700 text-white')
            return
        
        # Portfolio info card
        with ui.card().classes('w-full p-6 bg-blue-900'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.column().classes('gap-2'):
                    ui.label(f'{portfolio_name}').classes('text-2xl font-bold text-white')
                    ui.label(f'Base Currency: {portfolio_base}').classes('text-white opacity-90')
                    if show_approved:
                        ui.badge('Showing: All Transactions', color='amber').classes('text-white')
                    else:
                        ui.badge('Showing: Unapproved Only', color='green').classes('text-white')
                
                # Summary statistics
                total_unreviewed = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(portfolio_filter),
                    Transaction.reviewed == False
                ).count()
                
                total_transactions = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(portfolio_filter)
                ).count()
                
                with ui.column().classes('items-end gap-2'):
                    ui.label(f'{total_unreviewed} Unreviewed').classes('text-3xl font-bold text-white')
                    ui.label(f'{total_transactions} Total Transactions').classes('text-white opacity-90')
        
        # Bulk actions bar - refreshable component
        @ui.refreshable
        def bulk_actions_bar():
            """Refreshable bulk actions bar that updates when selections change"""
            # Get current selection count
            selected_count = len(app.storage.user.get('selected_tx_ids', []))
            
            with ui.card().classes('w-full p-4 bg-slate-100 border-l-4 border-blue-700'):
                with ui.row().classes('items-center justify-between w-full flex-wrap gap-2'):
                    with ui.column().classes('gap-2 w-full lg:w-auto lg:flex-row lg:items-center'):
                        ui.label(f'{selected_count} selected').classes('text-lg font-semibold text-slate-800')
                        ui.button('Select All', on_click=select_all, icon='select_all').props('flat dense').classes('bg-blue-700 text-white')
                        ui.button('Deselect All', on_click=deselect_all, icon='deselect').props('flat dense').classes('bg-slate-600 text-white')
                        with ui.button(
                            f'Mark {selected_count} as Correct' if selected_count > 0 else 'Mark Selected as Correct',
                            on_click=mark_selected_correct,
                            icon='check_circle'
                        ).props('flat').classes(
                            'bg-emerald-700 text-white' if selected_count > 0 else 'bg-gray-300 text-gray-600'
                        ) as mark_button:
                            if selected_count == 0:
                                mark_button.props('disable')
                    
                    with ui.row().classes('items-center gap-2 flex-wrap'):
                        ui.button('Create Transaction', on_click=create_manual_transaction, icon='add_circle').props('flat dense').classes('bg-purple-700 text-white')
                                
        async def create_manual_transaction():
            """Open dialog to create a manual transaction"""
            # Get all portfolios and accounts for dropdowns
            db_dialog = SessionLocal()
            try:
                # Get portfolios (exclude placeholders)
                portfolios_loader = get_portfolios_loader()
                all_portfolios = portfolios_loader.get_portfolios()
                portfolio_options = {p['id']: p['name'] for p in all_portfolios if p.get('type') != 'placeholder'}
                
                # Get all accounts
                all_accounts = db_dialog.query(Account).order_by(Account.name).all()
                account_options = {acc.id: f"{acc.name} ({acc.type or 'N/A'})" for acc in all_accounts}
                
                # Transaction type options - from TransactionType enum
                tx_type_options = TransactionType.values()
                
                # Currency options - hardcoded (ECB supports EUR, USD, THB)
                currency_options = ['EUR', 'THB', 'USD']
                
                # Form data dictionary
                form_data = {
                    'portfolio_id': list(portfolio_options.keys())[0] if portfolio_options else None,
                    'account_id': list(account_options.keys())[0] if account_options else None,
                    'occurred_at': None,
                    'type': 'sell',
                    'symbol': '',
                    'quantity': '',
                    'price': '',
                    'value_native': '',
                    'currency_native': 'EUR',
                    'fee': '0',
                    'withholding_tax': '0',
                    'notes': '',
                    'exchange_rate_to_base': '',
                    'fetched_fx_rate': None  # Store fetched FX rate
                }
                
                with ui.dialog().props('persistent') as dialog, ui.card().classes('p-6 w-full max-w-4xl'):
                    ui.label('Create Manual Transaction').classes('text-2xl font-bold mb-4')
                    ui.label('Enter transaction details. FX rates will be fetched automatically.').classes('text-sm text-gray-600 mb-4')
                    
                    # Create a column for the form
                    with ui.column().classes('gap-2 w-full'):
                        
                        # Row 1: Portfolio and Account
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Portfolio *').classes('text-sm font-semibold')
                                portfolio_select = ui.select(
                                    options=portfolio_options,
                                    value=form_data['portfolio_id'],
                                    on_change=lambda e: form_data.update({'portfolio_id': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Account *').classes('text-sm font-semibold')
                                account_select = ui.select(
                                    options=account_options,
                                    value=form_data['account_id'],
                                    on_change=lambda e: form_data.update({'account_id': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 2: Date and Type
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Date *').classes('text-sm font-semibold')                                
                                max_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                                date_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_date_change(e.value)
                                ).props(f'type=date outlined dense max={max_date}').classes('w-full')
                                ui.label('Past dates only').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Transaction Type *').classes('text-sm font-semibold')
                                type_select = ui.select(
                                    options=tx_type_options,
                                    value=form_data['type'],
                                    on_change=lambda e: form_data.update({'type': e.value})
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 3: Symbol and Quantity
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Symbol').classes('text-sm font-semibold')                                
                                symbol_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'symbol': e.value})
                                ).props('outlined dense').classes('w-full')
                                ui.label('For stocks/crypto').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Quantity').classes('text-sm font-semibold')                                
                                quantity_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_quantity_change(e.value)
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Negative for sell/outflows').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 4: Value Native and Price (auto-calculated)
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Value * (gross, before fees)').classes('text-sm font-semibold')                                
                                value_native_input = ui.input(
                                    value='',
                                    on_change=lambda e: handle_value_change(e.value)
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Negative for sell/outflows').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Price per Unit').classes('text-sm font-semibold')                                
                                price_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'price': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Auto-calculated').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 5: Currency and FX Rate
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Currency (Native) *').classes('text-sm font-semibold')
                                currency_select = ui.select(
                                    options=currency_options,
                                    value=form_data['currency_native'],
                                    on_change=lambda e: handle_currency_change(e.value)
                                ).classes('w-full').props('outlined dense')
                                ui.label('').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Exchange Rate to Base').classes('text-sm font-semibold')                                
                                fx_rate_input = ui.input(
                                    value='',
                                    on_change=lambda e: form_data.update({'exchange_rate_to_base': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                fx_status_label = ui.label('').classes('text-xs text-gray-600')
                                ui.label('Auto-fetched').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                        
                        # Row 6: Fee and Withholding Tax (deducted separately)
                        with ui.row().classes('gap-3 w-full flex-wrap'):
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Fee').classes('text-sm font-semibold')                                
                                fee_input = ui.input(
                                    value='0',
                                    on_change=lambda e: form_data.update({'fee': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Deducted separately').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                            
                            with ui.column().classes('flex-1 min-w-[200px] gap-1'):
                                ui.label('Withholding Tax').classes('text-sm font-semibold')                                
                                tax_input = ui.input(
                                    value='0',
                                    on_change=lambda e: form_data.update({'withholding_tax': e.value})
                                ).props('outlined dense type=number step=any').classes('w-full')
                                ui.label('Deducted separately').classes('text-xs text-gray-500')
                                ui.label('').classes('text-xs text-gray-500')
                                                        
                        # Row 7: Notes
                        with ui.column().classes('w-full gap-1'):
                            ui.label('Notes (Optional)').classes('text-sm font-semibold')
                            notes_input = ui.textarea(
                                value='',
                                on_change=lambda e: form_data.update({'notes': e.value})
                            ).props('outlined').classes('w-full')
                        
                        # Helper functions for auto-population and calculations
                        def handle_date_change(date_str):
                            """Handle date change and fetch FX rate"""
                            form_data['occurred_at'] = date_str
                            fetch_fx_rate()
                        
                        def handle_currency_change(currency):
                            """Handle currency change and fetch FX rate"""
                            form_data['currency_native'] = currency
                            fetch_fx_rate()
                        
                        def handle_quantity_change(quantity_str):
                            """Handle quantity change and auto-calculate price"""
                            form_data['quantity'] = quantity_str
                            calculate_price()
                        
                        def handle_value_change(value_str):
                            """Handle value change and auto-calculate price"""
                            form_data['value_native'] = value_str
                            calculate_price()
                        
                        def calculate_price():
                            """Auto-calculate price from value / quantity"""
                            try:
                                if form_data['value_native'] and form_data['quantity']:
                                    value = float(form_data['value_native'])
                                    quantity = float(form_data['quantity'])
                                    if quantity != 0:
                                        price = abs(value / quantity)  # Use absolute for price calculation
                                        form_data['price'] = str(price)
                                        price_input.set_value(f'{price:.8f}')
                            except (ValueError, ZeroDivisionError):
                                pass  # Ignore calculation errors, user can enter manually
                        
                        def fetch_fx_rate():
                            """Fetch FX rate from database based on date and currency"""
                            if not form_data['occurred_at'] or not form_data['currency_native']:
                                return
                            
                            db_fx = SessionLocal()
                            try:
                                # Get the selected portfolio's base currency
                                portfolio = db_fx.query(Portfolio).filter(Portfolio.id == form_data['portfolio_id']).first()
                                if not portfolio:
                                    fx_status_label.set_text('⚠️ Portfolio not found')
                                    return
                                
                                base_currency = portfolio.currency_base
                                native_currency = form_data['currency_native']
                                
                                # If currencies are the same, rate is 1.0
                                if base_currency == native_currency:
                                    form_data['exchange_rate_to_base'] = '1.0'
                                    form_data['fetched_fx_rate'] = Decimal('1.0')
                                    fx_rate_input.set_value('1.0')
                                    fx_status_label.set_text('✓ Same currency, rate = 1.0')
                                    return
                                
                                # Parse the date
                                tx_date = datetime.strptime(form_data['occurred_at'], '%Y-%m-%d').date()
                                
                                # ECB FX rate lookup with cross-rate calculation
                                # ECB only publishes rates with EUR as base: EUR/USD, EUR/THB
                                # For cross-rates (e.g., USD→THB), we calculate: rate = (EUR/THB) / (EUR/USD)
                                
                                # Case 1: native == EUR (e.g., EUR→USD or EUR→THB)
                                if native_currency == 'EUR':
                                    # Look for EUR/base (e.g., EUR/USD) on or before transaction date
                                    fx_pair = f"EUR/{base_currency}"
                                    fx_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == fx_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if fx_record and fx_record.rate:
                                        # Direct: 1 EUR = rate base_currency
                                        rate = float(fx_record.rate)
                                        form_data['exchange_rate_to_base'] = str(rate)
                                        form_data['fetched_fx_rate'] = Decimal(str(rate))
                                        fx_rate_input.set_value(str(rate))
                                        fx_status_label.set_text(f'✓ Fetched: {fx_record.pair} = {rate:.6f}')
                                    else:
                                        fx_status_label.set_text(f'⚠️ No ECB rate found for {fx_pair}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                                        fx_rate_input.set_value('')
                                
                                # Case 2: base == EUR (e.g., USD→EUR or THB→EUR)
                                elif base_currency == 'EUR':
                                    # Look for EUR/native (e.g., EUR/USD) on or before transaction date
                                    fx_pair = f"EUR/{native_currency}"
                                    fx_record = db_fx.query(FxRate).filter(
                                        FxRate.pair == fx_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if fx_record and fx_record.rate:
                                        # Inverse: 1 native = 1/rate EUR
                                        rate = 1.0 / float(fx_record.rate)
                                        form_data['exchange_rate_to_base'] = str(rate)
                                        form_data['fetched_fx_rate'] = Decimal(str(rate))
                                        fx_rate_input.set_value(str(rate))
                                        fx_status_label.set_text(f'✓ Calculated from {fx_record.pair} = {float(fx_record.rate):.6f} → rate = {rate:.6f}')
                                    else:
                                        fx_status_label.set_text(f'⚠️ No ECB rate found for {fx_pair}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                                        fx_rate_input.set_value('')
                                
                                # Case 3: Cross-rate (e.g., USD→THB or THB→USD)
                                else:
                                    # Need two rates on or before transaction date: EUR/native and EUR/base
                                    # Formula: native→base = (EUR/base) / (EUR/native)
                                    eur_native_pair = f"EUR/{native_currency}"
                                    eur_base_pair = f"EUR/{base_currency}"
                                    
                                    eur_native_rate = db_fx.query(FxRate).filter(
                                        FxRate.pair == eur_native_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    eur_base_rate = db_fx.query(FxRate).filter(
                                        FxRate.pair == eur_base_pair,
                                        FxRate.source == 'ecb',
                                        FxRate.as_of_date <= tx_date
                                    ).order_by(FxRate.as_of_date.desc()).first()
                                    
                                    if eur_native_rate and eur_base_rate and eur_native_rate.rate and eur_base_rate.rate:
                                        # Cross-rate calculation
                                        rate = float(eur_base_rate.rate) / float(eur_native_rate.rate)
                                        form_data['exchange_rate_to_base'] = str(rate)
                                        form_data['fetched_fx_rate'] = Decimal(str(rate))
                                        fx_rate_input.set_value(str(rate))
                                        fx_status_label.set_text(
                                            f'✓ Cross-rate: {eur_base_pair}={float(eur_base_rate.rate):.4f} ÷ '
                                            f'{eur_native_pair}={float(eur_native_rate.rate):.4f} = {rate:.6f}'
                                        )
                                    else:
                                        missing = []
                                        if not eur_native_rate:
                                            missing.append(eur_native_pair)
                                        if not eur_base_rate:
                                            missing.append(eur_base_pair)
                                        fx_status_label.set_text(f'⚠️ Missing ECB rates: {", ".join(missing)}')
                                        form_data['exchange_rate_to_base'] = ''
                                        form_data['fetched_fx_rate'] = None
                                        fx_rate_input.set_value('')
                            
                            except Exception as e:
                                fx_status_label.set_text(f'❌ Error fetching FX rate: {str(e)}')
                                form_data['exchange_rate_to_base'] = ''
                                form_data['fetched_fx_rate'] = None
                            finally:
                                db_fx.close()
                        
                        # Validation and Save
                        with ui.row().classes('gap-2 justify-end w-full mt-6'):
                            ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat').classes('bg-gray-500 text-white')
                            ui.button('Create Transaction', on_click=lambda: dialog.submit(True)).props('flat').classes('bg-emerald-700 text-white')
                
                result = await dialog
                
                if result:
                    # Validate required fields (only value_native is required, quantity/price are optional)
                    errors = []
                    
                    if not form_data['portfolio_id']:
                        errors.append('Portfolio is required')
                    if not form_data['account_id']:
                        errors.append('Account is required')
                    if not form_data['occurred_at']:
                        errors.append('Date is required')
                    if not form_data['type']:
                        errors.append('Transaction type is required')
                    if not form_data['value_native']:
                        errors.append('Value (Native Currency) is required - enter the gross value before fees/taxes')
                    if not form_data['currency_native']:
                        errors.append('Currency is required')
                    if not form_data['exchange_rate_to_base']:
                        errors.append('Exchange rate is required (should be auto-fetched)')
                    
                    if errors:
                        ui.notify('Validation errors:\n' + '\n'.join(errors), type='negative', position='top', timeout=5000)
                        return
                    
                    # Create the transaction
                    db_create = SessionLocal()
                    try:
                        # Get portfolio base currency
                        portfolio = db_create.query(Portfolio).filter(Portfolio.id == form_data['portfolio_id']).first()
                        if not portfolio:
                            ui.notify('Portfolio not found', type='negative')
                            return
                        
                        # Parse datetime
                        occurred_at = datetime.strptime(form_data['occurred_at'], '%Y-%m-%d')
                        
                        # Calculate value_base
                        value_native = Decimal(form_data['value_native'])
                        exchange_rate = Decimal(form_data['exchange_rate_to_base'])
                        value_base = value_native * exchange_rate
                        
                        # Prepare notes with manual entry prefix
                        user_notes = form_data['notes'].strip() if form_data['notes'] else ''
                        notes_text = f"Manually entered transaction - {user_notes}" if user_notes else "Manually entered transaction"
                        
                        # Create transaction object
                        new_tx = Transaction(
                            portfolio_id=form_data['portfolio_id'],
                            account_id=form_data['account_id'],
                            occurred_at=occurred_at,
                            type=form_data['type'],
                            symbol=form_data['symbol'] or None,
                            quantity=Decimal(form_data['quantity']) if form_data['quantity'] else None,
                            price=Decimal(form_data['price']) if form_data['price'] else None,
                            value_native=value_native,
                            currency_native=form_data['currency_native'],
                            value_base=value_base,
                            currency_base=portfolio.currency_base,
                            exchange_rate_to_base=exchange_rate,
                            fee=Decimal(form_data['fee']) if form_data['fee'] else Decimal('0'),
                            fee_currency=form_data['currency_native'],  # Fee in same currency as transaction
                            withholding_tax=Decimal(form_data['withholding_tax']) if form_data['withholding_tax'] else Decimal('0'),
                            withholding_tax_currency=form_data['currency_native'] if form_data['withholding_tax'] and float(form_data['withholding_tax']) > 0 else None,
                            notes=notes_text,
                            source='manual',  # Mark as manually entered
                            reviewed=True,  # Manual transactions are pre-approved
                            last_updated=now_utc()
                        )
                        
                        db_create.add(new_tx)
                        db_create.commit()
                        
                        ui.notify(f'Transaction created successfully (TX-{new_tx.id})', type='positive')
                        _transaction_review_content_wrapper.refresh()
                    
                    except Exception as e:
                        db_create.rollback()
                        ui.notify(f'Error creating transaction: {str(e)}', type='negative', timeout=5000)
                    finally:
                        db_create.close()
            
            finally:
                db_dialog.close()
        
        def mark_selected_correct():
            """Mark all selected transactions as reviewed"""
            selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
            if not selected_tx_ids:
                ui.notify('No transactions selected', type='warning')
                return
            
            # Create new session for this operation
            db_bulk = SessionLocal()
            try:
                db_bulk.query(Transaction).filter(
                    Transaction.id.in_(selected_tx_ids)
                ).update({'reviewed': True}, synchronize_session=False)
                db_bulk.commit()
                ui.notify(f'{len(selected_tx_ids)} transaction(s) marked as correct', type='positive')
                app.storage.user['selected_tx_ids'] = []
                bulk_actions_bar.refresh()  # Update counter
                _transaction_review_content_wrapper.refresh()
            except Exception as e:
                db_bulk.rollback()
                ui.notify(f'Error: {str(e)}', type='negative')
            finally:
                db_bulk.close()
        
        def select_all():
            """Select all transactions (filtered by show_approved setting)"""
            if show_approved:
                # Select all transactions (approved and unapproved)
                all_tx_ids = [tx.id for account in accounts_with_txs 
                              for tx in db.query(Transaction).filter(
                                  Transaction.portfolio_id.in_(portfolio_filter),
                                  Transaction.account_id == account.id
                              ).all()]
            else:
                # Select only unapproved transactions
                all_tx_ids = [tx.id for account in accounts_with_txs 
                              for tx in db.query(Transaction).filter(
                                  Transaction.portfolio_id.in_(portfolio_filter),
                                  Transaction.account_id == account.id,
                                  Transaction.reviewed == False
                              ).all()]
            app.storage.user['selected_tx_ids'] = all_tx_ids
            ui.notify(f'Selected {len(all_tx_ids)} transaction(s)', type='info')
            _transaction_review_content_wrapper.refresh()  # Refresh all to update checkboxes
        
        def deselect_all():
            """Deselect all transactions"""
            count = len(app.storage.user.get('selected_tx_ids', []))
            app.storage.user['selected_tx_ids'] = []
            ui.notify(f'Deselected {count} transaction(s)', type='info')
            _transaction_review_content_wrapper.refresh()  # Refresh all to update checkboxes
        
        # Render the bulk actions bar
        bulk_actions_bar()
        
        # Iterate through each account
        for account in accounts_with_txs:
            # Get transactions for this account in selected portfolio(s)
            # Filter based on show_approved toggle and apply limit
            if show_approved:
                # Show all transactions (approved and unapproved)
                account_txs_query = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(portfolio_filter),
                    Transaction.account_id == account.id
                ).order_by(Transaction.occurred_at.desc())
                
                # Get total count before applying limit
                total_count = account_txs_query.count()
                
                # Apply limit if not showing all
                if transactions_per_account < 999999:
                    account_txs = account_txs_query.limit(transactions_per_account).all()
                else:
                    account_txs = account_txs_query.all()
            else:
                # Show only unapproved transactions (default)
                account_txs_query = db.query(Transaction).filter(
                    Transaction.portfolio_id.in_(portfolio_filter),
                    Transaction.account_id == account.id,
                    Transaction.reviewed == False
                ).order_by(Transaction.occurred_at.desc())
                
                # Get total count before applying limit
                total_count = account_txs_query.count()
                
                # Apply limit if not showing all
                if transactions_per_account < 999999:
                    account_txs = account_txs_query.limit(transactions_per_account).all()
                else:
                    account_txs = account_txs_query.all()
            
            if not account_txs:
                continue  # Skip accounts with no matching transactions
            
            with ui.card().classes('w-full p-6'):
                # Account header
                with ui.row().classes('items-center justify-between w-full mb-4'):
                    with ui.column():
                        ui.label(f'Account: {account.name}').classes('text-xl font-bold')
                        if show_approved:
                            unapproved_count = sum(1 for tx in account_txs if not tx.reviewed)
                            if len(account_txs) < total_count:
                                ui.label(f'Showing {len(account_txs)} of {total_count} transaction(s) ({unapproved_count} unapproved)').classes('text-sm text-gray-500')
                            else:
                                ui.label(f'{len(account_txs)} transaction(s) ({unapproved_count} unapproved)').classes('text-sm text-gray-500')
                        else:
                            if len(account_txs) < total_count:
                                ui.label(f'Showing {len(account_txs)} of {total_count} unapproved transaction(s)').classes('text-sm text-gray-500')
                            else:
                                ui.label(f'{len(account_txs)} unapproved transaction(s)').classes('text-sm text-gray-500')
                    
                    ui.badge(f'{account.type or "N/A"}', color='blue').classes('text-white')
                
                # Transactions list
                for tx in account_txs:
                    # Get the portfolio for this transaction to show portfolio name if viewing all
                    if not selected_portfolio_id:
                        tx_portfolio = db.query(Portfolio).filter(Portfolio.id == tx.portfolio_id).first()
                        tx_portfolio_name = tx_portfolio.name if tx_portfolio else f'Portfolio {tx.portfolio_id}'
                        _create_transaction_card(tx, db, portfolio_base, bulk_actions_bar.refresh, show_portfolio=True, portfolio_name=tx_portfolio_name, account_name=account.name)
                    else:
                        _create_transaction_card(tx, db, portfolio_base, bulk_actions_bar.refresh, account_name=account.name)
    
    except Exception as e:
        with ui.card().classes('w-full p-6 bg-red-50'):
            ui.icon('error', size='2rem').classes('text-red-500 mb-2')
            ui.label('Error loading transactions').classes('text-lg font-bold text-red-600')
            ui.label(str(e)).classes('text-red-500 mt-2 font-mono text-sm')
    finally:
        db.close()


def _create_transaction_card(tx: 'Transaction', db, base_currency: str, bulk_actions_bar_refresh, show_portfolio: bool = False, portfolio_name: str = None, account_name: str = None):
    """Create an editable transaction card with checkbox selection
    
    Args:
        bulk_actions_bar_refresh: Function to refresh the bulk actions bar counter
    """
    
    # Store transaction data for editing - using dict to maintain state
    edit_data = {
        'type': tx.type,
        'symbol': tx.symbol or '',
        'quantity': str(tx.quantity) if tx.quantity else '',
        'value_native': str(tx.value_native) if tx.value_native else '',
        'currency_native': tx.currency_native or '',
        'price': str(tx.price) if tx.price else '',
        'fee': str(tx.fee) if tx.fee is not None else '0',
        'withholding_tax': str(tx.withholding_tax) if tx.withholding_tax is not None else '0',
        'exchange_rate_to_base': str(tx.exchange_rate_to_base) if tx.exchange_rate_to_base is not None else '',
        'notes': tx.notes or '',
    }
    
    # Create a container for the card that will be replaced on edit
    card_container = ui.column().classes('w-full')
    
    @ui.refreshable
    def render_card_content(editing=False):
        """Refreshable content for the transaction card"""
        
        def toggle_selection():
            """Toggle transaction selection"""
            selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
            if tx.id in selected_tx_ids:
                selected_tx_ids.remove(tx.id)
            else:
                selected_tx_ids.append(tx.id)
            app.storage.user['selected_tx_ids'] = selected_tx_ids
            bulk_actions_bar_refresh()  # Refresh counter only
            render_card_content.refresh()  # Update checkbox state
        
        def mark_as_correct():
            """Mark transaction as reviewed without editing"""
            # Create new session for this operation
            db_action = SessionLocal()
            try:
                # Get fresh transaction object in this session
                tx_to_update = db_action.query(Transaction).filter(Transaction.id == tx.id).first()
                if tx_to_update:
                    tx_to_update.reviewed = True
                    db_action.commit()
                    ui.notify('Transaction marked as correct', type='positive')
                    # Remove from selection if selected
                    selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
                    if tx.id in selected_tx_ids:
                        selected_tx_ids.remove(tx.id)
                        app.storage.user['selected_tx_ids'] = selected_tx_ids
                    # Refresh content wrapper to update the list (maintains scroll position)
                    _transaction_review_content_wrapper.refresh()
                else:
                    ui.notify('Transaction not found', type='warning')
            except Exception as e:
                db_action.rollback()
                ui.notify(f'Error: {str(e)}', type='negative')
            finally:
                db_action.close()
        
        def save_changes():
            """Save edited transaction"""
            # Create new session for this operation
            db_action = SessionLocal()
            try:
                # Get fresh transaction object in this session
                tx_to_update = db_action.query(Transaction).filter(Transaction.id == tx.id).first()
                if not tx_to_update:
                    ui.notify('Transaction not found', type='warning')
                    return
                
                # Update transaction fields from edit_data
                tx_to_update.type = edit_data['type']
                tx_to_update.symbol = edit_data['symbol'] or None
                tx_to_update.quantity = float(edit_data['quantity']) if edit_data['quantity'] else None
                tx_to_update.value_native = float(edit_data['value_native']) if edit_data['value_native'] else None
                tx_to_update.currency_native = edit_data['currency_native'] or None
                tx_to_update.price = float(edit_data['price']) if edit_data['price'] else None
                tx_to_update.fee = float(edit_data['fee']) if edit_data['fee'] else 0
                tx_to_update.withholding_tax = float(edit_data['withholding_tax']) if edit_data['withholding_tax'] else 0
                tx_to_update.exchange_rate_to_base = float(edit_data['exchange_rate_to_base']) if edit_data['exchange_rate_to_base'] else None
                tx_to_update.notes = edit_data['notes'] or None
                tx_to_update.reviewed = True  # Mark as reviewed when saving
                tx_to_update.last_updated = now_utc()  # Track when edited
                
                # Automatically populate currency_base from portfolio if not set
                # This ensures currency_base is always set (required field)
                if not tx_to_update.currency_base:
                    portfolio = db_action.query(Portfolio).filter(Portfolio.id == tx_to_update.portfolio_id).first()
                    if portfolio:
                        tx_to_update.currency_base = portfolio.currency_base
                
                # Calculate value_base from value_native and exchange_rate_to_base
                # No external API calls - use only the entered values
                if tx_to_update.value_native is not None and tx_to_update.exchange_rate_to_base is not None:
                    tx_to_update.value_base = float(tx_to_update.value_native) * float(tx_to_update.exchange_rate_to_base)
                
                db_action.commit()
                ui.notify('Transaction updated and marked as reviewed', type='positive')
                # Remove from selection if selected
                selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
                if tx.id in selected_tx_ids:
                    selected_tx_ids.remove(tx.id)
                    app.storage.user['selected_tx_ids'] = selected_tx_ids
                _transaction_review_content_wrapper.refresh()
            except Exception as e:
                db_action.rollback()
                ui.notify(f'Error saving: {str(e)}', type='negative')
            finally:
                db_action.close()
        
        def toggle_to_edit():
            """Switch to edit mode"""
            card_container.clear()
            with card_container:
                with ui.card().classes('w-full p-4 mb-3 border-l-4 border-blue-900 bg-blue-50'):
                    render_card_content(editing=True)
        
        def cancel_edit():
            """Cancel editing"""
            # Restore original values
            edit_data.update({
                'type': tx.type,
                'symbol': tx.symbol or '',
                'quantity': str(tx.quantity) if tx.quantity else '',
                'value_native': str(tx.value_native) if tx.value_native else '',
                'currency_native': tx.currency_native or '',
                'price': str(tx.price) if tx.price else '',
                'fee': str(tx.fee) if tx.fee is not None else '0',
                'withholding_tax': str(tx.withholding_tax) if tx.withholding_tax is not None else '0',
                'exchange_rate_to_base': str(tx.exchange_rate_to_base) if tx.exchange_rate_to_base is not None else '',
                'notes': tx.notes or '',
            })
            card_container.clear()
            with card_container:
                with ui.card().classes('w-full p-4 mb-3 border-l-4 border-blue-900 bg-blue-50'):
                    render_card_content(editing=False)
        
        async def confirm_delete():
            """Show confirmation dialog and delete transaction if confirmed"""
            with ui.dialog().props('persistent') as dialog, ui.card().classes('p-6'):
                ui.label('Confirm Deletion').classes('text-xl font-bold mb-4')
                ui.label(f'Are you sure you want to delete this transaction?').classes('mb-2')
                with ui.row().classes('gap-2 mt-4 mb-2'):
                    ui.badge(f'TX-{tx.id}', color='gray').classes('text-white')
                    ui.label(tx.occurred_at.strftime('%Y-%m-%d %H:%M:%S')).classes('text-sm')
                
                ui.label('⚠️ This action cannot be undone!').classes('text-red-600 font-semibold mb-4 mt-4')
                
                with ui.row().classes('gap-2 justify-end w-full'):
                    ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat').classes('bg-gray-500 text-white')
                    ui.button('Delete', on_click=lambda: dialog.submit(True)).props('flat').classes('bg-red-700 text-white')
            
            result = await dialog
            
            if result:
                # User confirmed deletion
                db_delete = SessionLocal()
                try:
                    tx_to_delete = db_delete.query(Transaction).filter_by(id=tx.id).first()
                    if tx_to_delete:
                        db_delete.delete(tx_to_delete)
                        db_delete.commit()
                        ui.notify(f'Transaction TX-{tx.id} deleted successfully', type='positive')
                        # Remove from selection if selected
                        selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
                        if tx.id in selected_tx_ids:
                            selected_tx_ids.remove(tx.id)
                            app.storage.user['selected_tx_ids'] = selected_tx_ids
                        _transaction_review_content_wrapper.refresh()
                    else:
                        ui.notify('Transaction not found', type='warning')
                except Exception as e:
                    db_delete.rollback()
                    ui.notify(f'Error deleting transaction: {str(e)}', type='negative')
                finally:
                    db_delete.close()
        
        # Get current selection state
        selected_tx_ids = app.storage.user.get('selected_tx_ids', [])
        is_selected = tx.id in selected_tx_ids
        
        # MAIN LAYOUT ROW - Added 'flex-wrap' here to prevent overflow
        with ui.row().classes('items-start justify-between w-full gap-4 flex-wrap'):
            # Checkbox column
            with ui.column().classes('items-center justify-start pt-2'):
                ui.checkbox(value=is_selected, on_change=lambda: toggle_selection()).props('size=lg')
            
            # Left column - Transaction details (flex-1 to take available space)
            with ui.column().classes('flex-1 gap-2 min-w-[250px]'):
                # Transaction ID and date header
                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.badge(f'TX-{tx.id}', color='gray').classes('text-white')
                    ui.label(tx.occurred_at.strftime('%Y-%m-%d %H:%M:%S')).classes('text-sm text-gray-600')
                    ui.badge(tx.type, color='indigo').classes('text-white')
                    # Show portfolio badge if viewing all portfolios
                    if show_portfolio and portfolio_name:
                        ui.badge(portfolio_name, color='deep-purple').classes('text-white')
                    # Always show account badge
                    if account_name:
                        ui.badge(account_name, color='teal').classes('text-white')
                    # Show approved status badge
                    if tx.reviewed:
                        ui.badge('✓ Approved', color='green').classes('text-white')
                
                # --- DISPLAY MODE ---
                if not editing:
                    # UPDATED: Use a responsive CSS Grid instead of a Flex Row
                    # This ensures columns align perfectly and wrap automatically without overflowing
                    with ui.element('div').classes('w-full grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 mt-2'):
                        
                        def info_col(label, value, is_mono=True):
                            with ui.column().classes('gap-0 overflow-hidden'):
                                ui.label(label).classes('text-xs text-gray-500 font-semibold truncate w-full')
                                cls = 'text-sm break-all'  # break-all prevents long strings from expanding width
                                if is_mono:
                                    cls += ' font-mono'
                                ui.label(value).classes(cls)
                        
                        info_col('Symbol', tx.symbol or 'N/A')
                        info_col('Quantity', f'{tx.quantity:.8f}' if tx.quantity else '0.00')
                        info_col(f'Value ({tx.currency_native or "-"})', f'{tx.value_native:.2f}' if tx.value_native else '0.00')
                        info_col('Price', f'{tx.price:.8f}' if tx.price else '0.00')
                        
                        # Conditional/Optional fields
                        fee_val = tx.fee if tx.fee is not None else 0
                        with ui.column().classes('gap-0 overflow-hidden'):
                            ui.label('Fee').classes('text-xs text-gray-500 font-semibold')
                            ui.label(f'{fee_val:.2f}').classes('text-sm font-mono break-all' + (' text-gray-400' if fee_val == 0 else ''))
                        
                        tax_val = tx.withholding_tax if tx.withholding_tax is not None else 0
                        with ui.column().classes('gap-0 overflow-hidden'):
                            ui.label('W. Tax').classes('text-xs text-gray-500 font-semibold')
                            ui.label(f'{tax_val:.2f}').classes('text-sm font-mono break-all' + (' text-gray-400' if tax_val == 0 else ''))
                        
                        if tx.value_base:
                            info_col(f'Value Base ({base_currency})', f'{tx.value_base:.2f}')
                    
                    if tx.notes:
                        with ui.column().classes('gap-1 mt-2 w-full'):
                            ui.label('Notes').classes('text-xs text-gray-500 font-semibold')
                            ui.label(tx.notes).classes('text-sm text-gray-700 italic break-words w-full')
                
                # --- EDIT MODE ---
                else:
                    # UPDATED: Responsive grid for edit inputs too
                    with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 mt-2'):
                        
                        def edit_field(label, key, placeholder=''):
                            with ui.column().classes('gap-1 w-full'):
                                ui.label(label).classes('text-xs text-gray-500 font-semibold')
                                ui.input(placeholder=placeholder, value=edit_data[key], 
                                        on_change=lambda e, k=key: edit_data.update({k: e.value})
                                        ).props('dense outlined').classes('w-full')
                        
                        with ui.column().classes('gap-1 w-full'):
                            ui.label('Type').classes('text-xs text-gray-500 font-semibold')
                            ui.select(
                                options=['deposit', 'withdrawal', 'buy', 'sell', 'transfer_in', 'transfer_out', 
                                        'fee', 'dividend', 'dividend_reinvest', 'interest', 'staking_reward', 
                                        'staking', 'withholding_tax', 'opening_balance', 'portfolio_transfer', 
                                        'exchange', 'balance_adjustment', 'spam'],
                                value=edit_data['type'],
                                on_change=lambda e: edit_data.update({'type': e.value})
                            ).props('dense outlined').classes('w-full')
                        
                        edit_field('Symbol', 'symbol')
                        edit_field('Quantity', 'quantity', '0.00000000')
                        edit_field('Value', 'value_native', '0.00')
                        edit_field('Currency', 'currency_native', 'USD')
                        edit_field('Price', 'price', '0.00')
                        edit_field('Fee', 'fee', '0.00')
                        edit_field('Withholding Tax', 'withholding_tax', '0.00')
                        edit_field('Exch Rate to Base', 'exchange_rate_to_base')
                    
                    with ui.column().classes('gap-1 mt-2 w-full'):
                        ui.label('Notes').classes('text-xs text-gray-500 font-semibold')
                        ui.textarea(value=edit_data['notes'], on_change=lambda e: edit_data.update({'notes': e.value})).props('dense outlined rows=2').classes('w-full')
            
            # Right column - Actions
            with ui.column().classes('gap-2 items-end mt-2 lg:mt-0'):
                if not editing:
                    ui.button('Mark Correct', on_click=mark_as_correct, icon='check_circle').props('flat dense').classes('bg-emerald-700 text-white')
                    ui.button('Edit', on_click=toggle_to_edit, icon='edit').props('flat dense').classes('bg-blue-700 text-white')
                else:
                    ui.button('Save', on_click=save_changes, icon='save').props('flat dense').classes('bg-emerald-700 text-white')
                    ui.button('Delete', on_click=confirm_delete, icon='delete').props('flat dense').classes('bg-red-700 text-white')
                    ui.button('Cancel', on_click=cancel_edit, icon='cancel').props('flat dense').classes('bg-gray-500 text-white')
    
    # Initial render
    with card_container:
        with ui.card().classes('w-full p-4 mb-3 border-l-4 border-blue-900 bg-blue-50'):
            render_card_content(editing=False)