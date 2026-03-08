"""
Crypto Wallet Manager Page
Manage cryptocurrency wallet addresses for tracking on-chain transactions and balances.
"""

from nicegui import ui, app
from database import SessionLocal
from models import CryptoWallet, Account
from sqlalchemy import func
from datetime import datetime
from typing import Optional
import logging

from apps.core.layout import page_layout

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Supported blockchains
SUPPORTED_CHAINS = [
    'BTC',  # Bitcoin
    'ETH',  # Ethereum
    'BSC',  # Binance Smart Chain
    'SOL',  # Solana
    'ADA',  # Cardano
    'XRP',  # Ripple
]

# Global state
wallets_table = None


def get_crypto_wallet_accounts():
    """Get all accounts that can hold crypto wallets"""
    db = SessionLocal()
    try:
        # Get accounts with type 'wallet', 'hardware_wallet', or 'Hardware Wallet'
        # Case-insensitive search to handle different account type formats
        accounts = db.query(Account).filter(
            Account.type.ilike('%wallet%')
        ).all()
        return [(acc.id, f"{acc.name} ({acc.type})") for acc in accounts]
    finally:
        db.close()


def load_wallets():
    """Load all crypto wallets from database"""
    db = SessionLocal()
    try:
        wallets = db.query(CryptoWallet).order_by(CryptoWallet.id).all()
        return [
            {
                'id': w.id,
                'account_id': w.account_id,
                'chain': w.symbol,  # Using 'symbol' attribute (renamed from 'chain')
                'address': w.address,
                'label': w.label or '',
            }
            for w in wallets
        ]
    finally:
        db.close()


def add_wallet(account_id: int, chain: str, wallet_address: str):
    """Add new crypto wallet"""
    db = SessionLocal()
    try:
        # Validate inputs
        if not account_id:
            ui.notify('Account is required', type='negative')
            return False
            
        if not chain or chain not in SUPPORTED_CHAINS:
            ui.notify(f'Invalid blockchain. Must be one of: {", ".join(SUPPORTED_CHAINS)}', type='negative')
            return False
            
        if not wallet_address or not wallet_address.strip():
            ui.notify('Wallet address or xpub is required', type='negative')
            return False
        
        wallet_address = wallet_address.strip()
        
        # Check for duplicates
        existing = db.query(CryptoWallet).filter(
            CryptoWallet.symbol == chain,
            CryptoWallet.address == wallet_address
        ).first()
        
        if existing:
            ui.notify(f'Wallet already exists: {existing.label or existing.address[:20]}...', type='warning')
            return False
        
        # Get account name for auto-generated label
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            ui.notify('Invalid account selected', type='negative')
            return False
        
        # Auto-generate label from account name and chain
        auto_label = f"{account.name} - {chain}"
        
        # Create wallet
        wallet = CryptoWallet(
            account_id=account_id,
            symbol=chain,  # Using 'symbol' attribute (renamed from 'chain')
            address=wallet_address,
            label=auto_label,
        )
        
        db.add(wallet)
        db.commit()
        
        logger.info(f"Added wallet: {chain} - {auto_label}")
        ui.notify(f'✅ Added wallet: {auto_label}', type='positive')
        return True
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding wallet: {e}")
        ui.notify(f'❌ Error: {str(e)}', type='negative')
        return False
    finally:
        db.close()


def delete_wallet(wallet_id: int):
    """Delete crypto wallet"""
    db = SessionLocal()
    try:
        wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
        if wallet:
            label = wallet.label or wallet.address
            db.delete(wallet)
            db.commit()
            ui.notify(f'✅ Deleted wallet: {label}', type='positive')
            return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting wallet: {e}")
        ui.notify(f'❌ Error: {str(e)}', type='negative')
        return False
    finally:
        db.close()


def refresh_table():
    """Refresh the wallets table"""
    global wallets_table
    if wallets_table:
        wallets_table.rows = load_wallets()
        wallets_table.update()


def show_add_wallet_dialog():
    """Show dialog to add new wallet"""
    
    with ui.dialog() as dialog, ui.card().classes('w-96'):
        ui.label('Add Crypto Wallet').classes('text-xl font-bold mb-4')
        
        # Get accounts for dropdown
        accounts = get_crypto_wallet_accounts()
        if not accounts:
            ui.label('⚠️ No wallet accounts found. Please create an account first.').classes('text-red-500')
            ui.button('Close', on_click=dialog.close).classes('mt-4')
            dialog.open()
            return
        
        # Form fields
        account_select = ui.select(
            label='Account *',
            options={acc[0]: acc[1] for acc in accounts},
            value=accounts[0][0]  # Default to first account
        ).classes('w-full')
        
        chain_select = ui.select(
            label='Blockchain *',
            options=SUPPORTED_CHAINS,
            value='BTC'
        ).classes('w-full')
        
        address_input = ui.input(
            label='Wallet Address / Extended Public Key (xpub) *',
            placeholder='Enter wallet address or xpub for multi-address tracking'
        ).classes('w-full')
        
        ui.label('💡 Tip: Use xpub for BTC/ADA to track multiple derived addresses').classes('text-xs text-blue-500 mt-2')
        ui.label('🏷️ Label will be auto-generated from account name and blockchain').classes('text-xs text-gray-500 mt-1')
        
        # Buttons
        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('Cancel', on_click=dialog.close).classes('bg-gray-500')
            ui.button('Add Wallet', on_click=lambda: (
                add_wallet(
                    account_select.value,
                    chain_select.value,
                    address_input.value.strip()
                ) and refresh_table() and dialog.close()
            )).classes('bg-blue-500')
    
    dialog.open()


def show_wallet_details(wallet):
    """Show wallet details dialog"""
    
    # Create a safe display name
    display_name = wallet["label"] if wallet["label"] else \
                   (wallet["address"][:10] + "..." if wallet["address"] else \
                   (wallet.get("xpub", "")[:10] + "..." if wallet.get("xpub") else "Wallet"))
    
    with ui.dialog() as dialog, ui.card().classes('w-96'):
        ui.label(f'Wallet Details: {display_name}').classes('text-xl font-bold mb-4')
        
        with ui.column().classes('w-full gap-2'):
            ui.label(f'ID: {wallet["id"]}')
            ui.label(f'Account ID: {wallet["account_id"] or "None"}')
            ui.label(f'Blockchain: {wallet["chain"]}')
            
            if wallet["address"]:
                ui.label(f'Address:').classes('font-bold mt-2')
                ui.input(value=wallet["address"]).props('readonly').classes('w-full font-mono text-xs')
            
            if wallet.get("xpub"):
                ui.label(f'Extended Public Key:').classes('font-bold mt-2')
                ui.textarea(value=wallet["xpub"]).props('readonly').classes('w-full font-mono text-xs')
        
        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('Close', on_click=dialog.close)
    
    dialog.open()


def wallet_manager_content():
    """Main content for crypto wallet manager"""
    global wallets_table
    
    # Instructions card
    with ui.card().classes('w-full mb-4'):
        ui.label('📝 Instructions').classes('text-xl font-bold mb-2')
        ui.label('Add your crypto wallet addresses to enable automated balance tracking.')
        ui.label('The scheduler will sync balances daily from blockchain APIs.')
        with ui.expansion('Supported Blockchains', icon='info').classes('mt-2'):
            for chain in SUPPORTED_CHAINS:
                ui.label(f'• {chain}')
    
    # Action buttons
    with ui.row().classes('w-full justify-between items-center mb-4'):
        ui.button('➕ Add Wallet', on_click=show_add_wallet_dialog).classes('bg-green-500')
        ui.button('🔄 Refresh', on_click=refresh_table).classes('bg-blue-500')
    
    # Wallets table
    with ui.card().classes('w-full mb-4'):
        with ui.row().classes('items-center gap-2 mb-2'):
            ui.icon('account_balance_wallet', size='sm').classes('text-primary')
            ui.label('Crypto Wallets').classes('text-xl font-bold')
        
        wallets_table = ui.table(
            columns=[
                {'name': 'id', 'label': 'ID', 'field': 'id', 'align': 'left'},
                {'name': 'chain', 'label': 'Blockchain', 'field': 'chain', 'align': 'left'},
                {'name': 'address', 'label': 'Address', 'field': 'address', 'align': 'left'},
                {'name': 'label', 'label': 'Label', 'field': 'label', 'align': 'left'},
                {'name': 'actions', 'label': 'Actions', 'field': 'id', 'align': 'center'},
            ],
            rows=load_wallets(),
            row_key='id'
        ).classes('w-full')
        
        # Custom cell rendering
        wallets_table.add_slot('body-cell-address', '''
            <q-td :props="props">
                <div class="font-mono text-xs">
                    {{ props.row.address ? props.row.address.substring(0, 20) + '...' : 'N/A' }}
                </div>
            </q-td>
        ''')
        
        wallets_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat dense icon="visibility" size="sm" @click="$parent.$emit('view', props.row)" />
                <q-btn flat dense icon="delete" size="sm" color="red" @click="$parent.$emit('delete', props.row.id)" />
            </q-td>
        ''')
        
        wallets_table.on('view', lambda e: show_wallet_details(e.args))
        wallets_table.on('delete', lambda e: delete_wallet(e.args) and refresh_table())

@ui.page('/wallet-manager')
def wallet_manager_page():
    """Crypto wallet manager page"""
    
    with page_layout('/wallet-manager'):
        wallet_manager_content()
