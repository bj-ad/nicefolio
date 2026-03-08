"""
Crypto Staking Manager Page

Dashboard showing staking allocation (liquid/staked/total) and managing BNB staking transactions.
BNB staking requires manual transaction hash entry since it uses BSC smart contracts.

Data Source:
- Staking allocation from CryptoBalance table (latest blockchain API snapshot per wallet)
- BNB staking tx history stored in StakingTransaction table
"""

from nicegui import ui
from database import SessionLocal

from apps.core.layout import page_layout

from crud.crud_staking_tx import (
    add_staking_transaction,
    get_staking_transactions,
    delete_staking_transaction,
    calculate_staking_summary,
    update_staking_transaction
)
from models import CryptoWallet, CryptoBalance
from service.blockchain_providers import bsc_provider
from sqlalchemy import func, and_
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Global state
current_wallet_id = None
tx_table = None


def get_crypto_wallets_by_symbol(db, symbol):
    """Get all crypto wallets for a specific symbol."""
    return db.query(CryptoWallet).filter_by(symbol=symbol).all()


def get_staking_allocation(db):
    """
    Get staking allocation breakdown from CryptoBalance table.
    
    CryptoBalance now stores ONE record per wallet/symbol/balance_type (UPSERT semantics),
    so we just query all records for staking-capable assets.
    """
    staking_symbols = ['BNB', 'SOL', 'ADA', 'ETH']
    allocation_data = []
    
    for symbol in staking_symbols:
        wallets = db.query(CryptoWallet).filter(CryptoWallet.symbol == symbol).all()
        
        for wallet in wallets:
            # Get all balance records for this wallet/symbol (one per balance_type)
            balances = db.query(CryptoBalance).filter(
                CryptoBalance.wallet_id == wallet.id,
                CryptoBalance.symbol == symbol
            ).all()
            
            if not balances:
                continue
            
            # Build breakdown dict
            breakdown = {b.balance_type: Decimal(str(b.balance)) for b in balances}
            
            # Get as_of timestamp from any record (all should be same)
            as_of = balances[0].as_of_date if balances else None
            
            liquid = breakdown.get('liquid', Decimal('0'))
            staked = breakdown.get('staked', Decimal('0'))
            activating = breakdown.get('activating', Decimal('0'))
            deactivating = breakdown.get('deactivating', Decimal('0'))
            unstaking = breakdown.get('unstaking', Decimal('0'))
            rewards = breakdown.get('rewards', Decimal('0')) + breakdown.get('pending_rewards', Decimal('0')) + breakdown.get('staking_rewards', Decimal('0'))
            total = breakdown.get('total', liquid + staked)
            
            # Only include if there's some balance
            if total > 0 or staked > 0:
                allocation_data.append({
                    'symbol': symbol,
                    'wallet_label': wallet.label or f"{wallet.address[:8]}...{wallet.address[-6:]}",
                    'liquid': liquid,
                    'staked': staked,
                    'activating': activating,
                    'deactivating': deactivating,
                    'unstaking': unstaking,
                    'rewards': rewards,
                    'total': total,
                    'as_of': as_of
                })
    
    return allocation_data


async def refresh_transactions():
    """Refresh the transactions table."""
    global tx_table, current_wallet_id
    
    if not current_wallet_id:
        return
    
    db = SessionLocal()
    try:
        txs = get_staking_transactions(db, wallet_id=current_wallet_id, symbol='BNB')
        
        rows = []
        for tx in txs:
            rows.append({
                'tx_hash': tx.tx_hash[:10] + '...' + tx.tx_hash[-8:],
                'tx_hash_full': tx.tx_hash,
                'type': tx.tx_type,
                'amount': f"{float(tx.amount) if tx.amount else 'Pending'} BNB",
                'block': tx.block_number or 'Pending',
                'status': '✅ Processed' if tx.processed_at else '⏳ Pending'
            })
        
        tx_table.update_rows(rows)
        
    finally:
        db.close()


async def add_transaction(wallet_select, tx_hash_input, tx_type_select):
    """Add a new BNB staking transaction."""
    global current_wallet_id
    
    wallet_id = wallet_select.value
    tx_hash = tx_hash_input.value.strip()
    tx_type = tx_type_select.value
    
    if not wallet_id:
        ui.notify("Please select a wallet", type='warning')
        return
    
    if not tx_hash:
        ui.notify("Please enter a transaction hash", type='warning')
        return
    
    if not tx_hash.startswith('0x'):
        tx_hash = '0x' + tx_hash
    
    db = SessionLocal()
    try:
        # Add transaction to database
        staking_tx, message = add_staking_transaction(
            db, wallet_id, tx_hash, tx_type, 'BNB'
        )
        
        if not staking_tx:
            ui.notify(message, type='negative')
            return
        
        ui.notify(f"Transaction added: {tx_hash[:10]}...{tx_hash[-8:]}", type='positive')
        
        # Commit the transaction first
        db.commit()
        
        # Fetch transaction details from blockchain
        ui.notify("Fetching transaction details from blockchain...", type='info')
        
        try:
            # Get transaction receipt for block number and status
            receipt = bsc_provider._make_rpc_call("eth_getTransactionReceipt", [tx_hash])
            
            # Get transaction details for value (amount)
            tx_data = bsc_provider._make_rpc_call("eth_getTransactionByHash", [tx_hash])
            
            if not receipt or "result" not in receipt or not receipt["result"]:
                ui.notify("⚠️ Transaction not found on blockchain. It may be pending or invalid.", type='warning')
            elif not tx_data or "result" not in tx_data or not tx_data["result"]:
                ui.notify("⚠️ Could not fetch transaction data.", type='warning')
            else:
                receipt_result = receipt["result"]
                tx_result = tx_data["result"]
                
                # Get block number from receipt
                block_number = int(receipt_result.get("blockNumber", "0x0"), 16) if receipt_result.get("blockNumber") else None
                
                # Get amount from transaction value (for delegate)
                amount = None
                value_hex = tx_result.get("value", "0x0")
                if value_hex and value_hex != "0x0":
                    try:
                        amount_wei = int(value_hex, 16)
                        amount = Decimal(amount_wei) / Decimal(10**18)
                    except Exception as e:
                        logger.warning(f"Could not parse transaction value: {e}")
                
                # For claims, the amount comes from logs (received BNB), not sent value
                if tx_type == 'claim' and (amount is None or amount == 0):
                    logs = receipt_result.get("logs", [])
                    for log in logs:
                        topics = log.get("topics", [])
                        data = log.get("data", "0x")
                        
                        # Standard Transfer event
                        if len(topics) >= 3 and topics[0] == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
                            if data and data != "0x":
                                try:
                                    amount_wei = int(data, 16)
                                    amount = Decimal(amount_wei) / Decimal(10**18)
                                    break
                                except:
                                    pass
                        
                        # Staking contract claim event (first 32 bytes of data)
                        elif log.get("address", "").lower() == "0x0000000000000000000000000000000000002002":
                            if data and data != "0x" and len(data) >= 66:
                                try:
                                    first_word = data[2:66]
                                    amount_wei = int(first_word, 16)
                                    amount = Decimal(amount_wei) / Decimal(10**18)
                                    break
                                except:
                                    pass
                
                # For undelegates, amount also comes from logs
                if tx_type == 'undelegate' and (amount is None or amount == 0):
                    logs = receipt_result.get("logs", [])
                    for log in logs:
                        topics = log.get("topics", [])
                        data = log.get("data", "0x")
                        
                        # Transfer event (burning credit tokens)
                        if len(topics) >= 3 and topics[0] == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
                            if data and data != "0x":
                                try:
                                    amount_wei = int(data, 16)
                                    amount = Decimal(amount_wei) / Decimal(10**18)
                                    break
                                except:
                                    pass
                
                # Update transaction with parsed details
                update_staking_transaction(
                    db,
                    tx_hash,
                    amount=amount,
                    block_number=block_number,
                    mark_processed=True
                )
                
                if amount:
                    ui.notify(f"✅ Fetched details: {float(amount):.6f} BNB (block {block_number})", type='positive')
                else:
                    ui.notify(f"✅ Transaction recorded (block {block_number}), amount could not be parsed", type='info')
        
        except Exception as e:
            logger.error(f"Error fetching tx details: {e}", exc_info=True)
            ui.notify(f"Added, but could not fetch details: {str(e)}", type='warning')
        
        # Refresh table
        await refresh_transactions()
        
        # Clear input
        tx_hash_input.value = ''
        
    finally:
        db.close()


async def delete_tx(tx_hash_full):
    """Delete a transaction."""
    db = SessionLocal()
    try:
        success, message = delete_staking_transaction(db, tx_hash_full)
        
        if success:
            ui.notify(f"Transaction deleted", type='positive')
            await refresh_transactions()
        else:
            ui.notify(message, type='negative')
    finally:
        db.close()


def create_staking_manager_content():
    """Create the staking manager page content."""
    global current_wallet_id, tx_table
    
    db = SessionLocal()
    
    try:
        with ui.column().classes('w-full gap-6'):
            
            # ===================================================================
            # STAKING ALLOCATION BREAKDOWN (from CryptoBalance - latest snapshot)
            # ===================================================================
            with ui.card().classes('w-full p-6'):
                ui.label('📊 Staking Allocation').classes('text-2xl font-bold mb-4')
                ui.label('Liquid vs Staked breakdown from latest blockchain API sync').classes('text-sm text-gray-500 mb-4')
                
                allocation_data = get_staking_allocation(db)
                
                if allocation_data:
                    # Create allocation table
                    allocation_columns = [
                        {'name': 'symbol', 'label': 'Asset', 'field': 'symbol', 'align': 'left'},
                        {'name': 'wallet', 'label': 'Wallet', 'field': 'wallet', 'align': 'left'},
                        {'name': 'liquid', 'label': 'Liquid', 'field': 'liquid', 'align': 'right'},
                        {'name': 'staked', 'label': 'Staked', 'field': 'staked', 'align': 'right'},
                        {'name': 'pending', 'label': 'Pending', 'field': 'pending', 'align': 'right'},
                        {'name': 'total', 'label': 'Total', 'field': 'total', 'align': 'right'},
                        {'name': 'staked_pct', 'label': '% Staked', 'field': 'staked_pct', 'align': 'right'},
                        {'name': 'as_of', 'label': 'Last Sync', 'field': 'as_of', 'align': 'right'},
                    ]
                    
                    allocation_rows = []
                    for item in allocation_data:
                        total = item['total']
                        staked = item['staked']
                        staked_pct = (staked / total * 100) if total > 0 else 0
                        
                        # Pending = activating + deactivating + unstaking
                        pending = item['activating'] + item['deactivating'] + item['unstaking']
                        
                        allocation_rows.append({
                            'symbol': item['symbol'],
                            'wallet': item['wallet_label'],
                            'liquid': f"{float(item['liquid']):.8f}",
                            'staked': f"{float(staked):.8f}",
                            'pending': f"{float(pending):.8f}" if pending > 0 else "-",
                            'total': f"{float(total):.8f}",
                            'staked_pct': f"{float(staked_pct):.1f}%",
                            'as_of': item['as_of'].strftime('%Y-%m-%d %H:%M') if item['as_of'] else '-'
                        })
                    
                    ui.table(
                        columns=allocation_columns,
                        rows=allocation_rows,
                        row_key='symbol'
                    ).classes('w-full')
                    
                    # Summary totals by asset
                    ui.label('Summary by Asset').classes('text-lg font-bold mt-6 mb-2')
                    
                    # Aggregate by symbol
                    summary = {}
                    for item in allocation_data:
                        sym = item['symbol']
                        if sym not in summary:
                            summary[sym] = {'liquid': Decimal('0'), 'staked': Decimal('0'), 'total': Decimal('0')}
                        summary[sym]['liquid'] += item['liquid']
                        summary[sym]['staked'] += item['staked']
                        summary[sym]['total'] += item['total']
                    
                    with ui.row().classes('w-full gap-4 flex-wrap'):
                        for sym, totals in sorted(summary.items()):
                            staked_pct = (totals['staked'] / totals['total'] * 100) if totals['total'] > 0 else 0
                            with ui.card().classes('p-4 min-w-48'):
                                ui.label(sym).classes('text-xl font-bold')
                                with ui.column().classes('gap-1'):
                                    ui.label(f"💧 Liquid: {float(totals['liquid']):.6f}").classes('text-sm')
                                    ui.label(f"🔒 Staked: {float(totals['staked']):.6f}").classes('text-sm')
                                    ui.label(f"📊 Total: {float(totals['total']):.6f}").classes('text-sm font-medium')
                                    ui.linear_progress(value=float(staked_pct)/100, show_value=False).classes('w-full mt-2')
                                    ui.label(f"{float(staked_pct):.1f}% staked").classes('text-xs text-gray-500')
                    
                    ui.label('ℹ️ Data from CryptoBalance table - updated by daily blockchain sync').classes('text-sm text-gray-500 mt-4')
                else:
                    ui.label('No staking data available. Run a wallet sync to populate.').classes('text-gray-500 text-center py-4')
            
            # ===================================================================
            # BNB MANUAL STAKING TRANSACTIONS
            # ===================================================================
            with ui.card().classes('w-full p-6'):
                ui.label('🔐 BNB Staking Transactions').classes('text-2xl font-bold mb-4')
                ui.label('Manually add transaction hashes for delegate/undelegate/claim operations.').classes('text-sm text-gray-600 mb-4')
                
                # Wallet selector
                bnb_wallets = get_crypto_wallets_by_symbol(db, 'BNB')
                
                if not bnb_wallets:
                    with ui.column().classes('w-full items-center py-8'):
                        ui.icon('info', size='3rem').classes('text-gray-300 mb-2')
                        ui.label('No BNB wallets configured').classes('text-gray-500')
                    return
                
                wallet_options = {w.id: f"{w.symbol} - {w.address[:10]}...{w.address[-8:]} ({w.label or 'No label'})" for w in bnb_wallets}
                
                with ui.row().classes('w-full gap-4 items-end mb-4'):
                    wallet_select = ui.select(
                        label='Select Wallet',
                        options=wallet_options,
                        value=bnb_wallets[0].id if bnb_wallets else None,
                        on_change=lambda e: handle_wallet_change(e.value)
                    ).classes('flex-1')
                    
                    tx_hash_input = ui.input(
                        label='Transaction Hash',
                        placeholder='0x...'
                    ).classes('flex-1')
                    
                    tx_type_select = ui.select(
                        label='Type',
                        options={'delegate': 'Delegate', 'undelegate': 'Undelegate', 'claim': 'Claim Rewards'},
                        value='delegate'
                    ).classes('flex-1')
                    
                    ui.button(
                        'Add Transaction',
                        on_click=lambda: add_transaction(wallet_select, tx_hash_input, tx_type_select),
                        icon='add'
                    ).props('flat').classes('bg-yellow-500 text-white')
                
                # Transaction history
                ui.label('Transaction History').classes('text-lg font-bold mt-6 mb-2')
                
                # Set initial wallet
                current_wallet_id = bnb_wallets[0].id if bnb_wallets else None
                
                def handle_wallet_change(wallet_id):
                    global current_wallet_id
                    current_wallet_id = wallet_id
                    ui.run_javascript(f'window.dispatchEvent(new Event("refresh-transactions"))')
                
                columns = [
                    {'name': 'tx_hash', 'label': 'TX Hash', 'field': 'tx_hash', 'align': 'left'},
                    {'name': 'type', 'label': 'Type', 'field': 'type', 'align': 'left'},
                    {'name': 'amount', 'label': 'Amount', 'field': 'amount', 'align': 'right'},
                    {'name': 'block', 'label': 'Block', 'field': 'block', 'align': 'right'},
                    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'center'},
                    {'name': 'actions', 'label': 'Actions', 'field': 'actions', 'align': 'center'},
                ]
                
                txs = get_staking_transactions(db, wallet_id=current_wallet_id, symbol='BNB')
                
                rows = []
                for tx in txs:
                    rows.append({
                        'tx_hash': tx.tx_hash[:10] + '...' + tx.tx_hash[-8:],
                        'tx_hash_full': tx.tx_hash,
                        'type': tx.tx_type,
                        'amount': f"{float(tx.amount) if tx.amount else 'Pending'} BNB",
                        'block': tx.block_number or 'Pending',
                        'status': '✅ Processed' if tx.processed_at else '⏳ Pending',
                        'actions': tx.tx_hash
                    })
                
                tx_table = ui.table(
                    columns=columns,
                    rows=rows,
                    row_key='tx_hash_full'
                ).classes('w-full')
                
                # Add delete buttons in table
                tx_table.add_slot('body-cell-actions', '''
                    <q-td :props="props">
                        <q-btn flat dense icon="delete" color="negative" @click="$parent.$emit('delete', props.row.tx_hash_full)" />
                    </q-td>
                ''')
                
                tx_table.on('delete', lambda e: delete_tx(e.args))
            
    finally:
        db.close()


@ui.page('/staking')
def staking_page():
    """Staking manager page"""
    with page_layout('/staking'):
        create_staking_manager_content()


@ui.page('/bnb-staking')
def bnb_staking_page():
    """BNB staking page - redirects to unified staking manager"""
    ui.navigate.to('/staking')
