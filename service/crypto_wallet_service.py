from database import SessionLocal
from models import CryptoWallet, Transaction, Account
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from utils.datetime_utils import now_utc
from crud.crud_base import create_transaction_idempotent
from crud.crud_crypto_balance import record_balance_snapshot, reconcile_balance
from service.crypto_transfer_service import (
    auto_detect_internal_transfers, 
    auto_detect_internal_transfers_global,
    auto_detect_exchange_wallet_transfers
)
# Import price enrichment utility for staking rewards and fees
from utils.transaction_price_enrichment import populate_transaction_price
from service.blockchain_providers import (
    btc_provider,
    eth_provider,
    bsc_provider,
    sol_provider,
    ada_provider,
    xrp_provider,
)
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

logger = get_logger(__name__)

# Load app config
config = load_app_config()
DEFAULT_WALLET_LOOKBACK_DAYS = config.get('crypto_wallets', {}).get('sync_lookback_days', 7)
# Overlap when syncing from last_updated (in days) - uses the same lookback config for consistency
SYNC_OVERLAP_DAYS = DEFAULT_WALLET_LOOKBACK_DAYS

PROVIDER_MAP = {
    'btc': btc_provider,
    'eth': eth_provider,
    'bnb': bsc_provider,  # BNB (Binance Smart Chain)
    'sol': sol_provider,
    'ada': ada_provider,
    'xrp': xrp_provider,
}

# Native token symbol mapping (symbol → symbol)
# After renaming 'chain' to 'symbol', this mapping is no longer needed
# as the wallet.symbol already contains the correct native token symbol
# Kept for backward compatibility during transition period
NATIVE_TOKEN_MAP = {
    'BTC': 'BTC',
    'ETH': 'ETH',
    'BNB': 'BNB',  # Renamed from BSC
    'SOL': 'SOL',
    'ADA': 'ADA',
    'XRP': 'XRP',
}

def sync_all_wallets(sync_transactions: bool = True, days_back: Optional[int] = None):
    """
    Fetches transactions for all configured crypto wallets and ingests them into the transaction-based system.
    Balance tracking is now derived from transaction history rather than direct balance queries.
    Only syncs wallets from accounts with status='active'.
    
    Args:
        sync_transactions (bool): Whether to sync transaction history (default: True)
        days_back (Optional[int]): Number of days back to sync transactions (default: from app_config.yaml)
    """
    if days_back is None:
        days_back = DEFAULT_WALLET_LOOKBACK_DAYS
    
    logger.info(f"Starting crypto wallet sync (lookback: {days_back} days)...")
    db = SessionLocal()
    try:
        # Get wallets, joining with accounts to check status
        wallets = db.query(CryptoWallet).join(Account, CryptoWallet.account_id == Account.id).filter(Account.status == 'active').all()
        
        # Log if any accounts were skipped
        all_wallets_count = db.query(CryptoWallet).count()
        if all_wallets_count > len(wallets):
            skipped = all_wallets_count - len(wallets)
            logger.info(f"Found {len(wallets)} active wallets to sync ({skipped} wallets skipped from closed accounts)")
        else:
            logger.info(f"Found {len(wallets)} wallets to sync.")
        
        for wallet in wallets:
            provider = PROVIDER_MAP.get(wallet.symbol.lower())
            if not provider:
                logger.warning(f"No provider found for chain: {wallet.symbol}")
                continue

            logger.info(f"Syncing {wallet.symbol} wallet: {wallet.address}")

            # Sync transactions (primary focus for transaction-based system)
            if sync_transactions:
                _sync_wallet_transactions(db, wallet, days_back)
            
            # Optional: Still fetch current balance for validation/reconciliation
            _fetch_wallet_balance_for_validation(wallet, provider)

    except Exception as e:
        logger.error(f"An error occurred during wallet sync: {e}", exc_info=True)
    finally:
        db.close()


def _sync_wallet_transactions(db: Session, wallet: CryptoWallet, days_back: int = 2):
    """
    Sync transactions for a single wallet using incremental fetching.
    
    Uses wallet.last_updated to determine the start date for fetching transactions.
    If last_updated is None, fetches transactions from days_back days ago.
    
    Args:
        db: Database session
        wallet: CryptoWallet instance to sync
        days_back: Default number of days to look back if no last_updated (default: 2)
    """
    from datetime import datetime, timedelta, timezone
    from crud.crud_base import create_transaction_idempotent
    
    try:
        # Get the provider for this chain
        provider = PROVIDER_MAP.get(wallet.symbol.lower())
        if not provider:
            logger.warning(f"No provider found for chain: {wallet.symbol}")
            return
        
        # Check if provider supports transaction fetching
        if not hasattr(provider, 'get_transactions') and not hasattr(provider, 'get_transactions_unified'):
            logger.warning(f"Provider for {wallet.symbol} does not support transaction fetching yet")
            return
        
        # Determine start date for fetching transactions (incremental sync with wallet.last_updated)
        if wallet.last_updated:
            # Fetch transactions since last sync with overlap to catch any missed
            # Uses configured lookback days for consistency with initial sync
            effective_start_date = wallet.last_updated - timedelta(days=SYNC_OVERLAP_DAYS)
            logger.info(f"Wallet {wallet.id} last synced at {wallet.last_updated}, fetching from {effective_start_date} ({SYNC_OVERLAP_DAYS} day overlap)")
        else:
            # First sync - fetch last N days
            effective_start_date = datetime.now(timezone.utc) - timedelta(days=days_back)
            logger.info(f"Wallet {wallet.id} never synced, fetching last {days_back} days from {effective_start_date}")
        
        end_date = datetime.now(timezone.utc)
        
        # Use address (BTC provider will auto-detect xpub format)
        if wallet.address and wallet.address.strip():
            identifier = wallet.address
            logger.info(f"Fetching transactions for {wallet.symbol} address {identifier[:30]}... from {effective_start_date} to {end_date}")
        else:
            logger.error(f"Wallet {wallet.id} has no address defined")
            return
        
        # Fetch transactions from blockchain provider (BTC provider auto-detects xpub)
        transactions = provider.get_transactions(
            address=identifier,
            start_date=effective_start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            limit=100  # Configurable limit
        )
        
        if not transactions:
            logger.info(f"No transactions found for address {identifier[:30]}...")
            # Update last_updated even if no transactions (prevents re-checking same period)
            wallet.last_updated = end_date
            db.commit()
            return
        
        logger.info(f"Processing {len(transactions)} transactions for address {identifier[:30]}...")
        
        # Process and ingest each transaction
        new_count = 0
        skipped_count = 0
        failure_count = 0
        
        for tx_data in transactions:
            try:
                # Add wallet-specific metadata
                enriched_tx_data = _enrich_transaction_data(tx_data, wallet)
                
                # Check if transaction already exists (idempotency check)
                source = enriched_tx_data.get('source')
                ext_id = enriched_tx_data.get('external_id')
                tx_hash = enriched_tx_data.get('blockchain_tx_hash')
                tx_type = enriched_tx_data.get('type')
                
                # Primary check: source + external_id
                if source and ext_id:
                    existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
                    if existing:
                        skipped_count += 1
                        continue
                
                # Secondary check: blockchain_tx_hash + account + type pattern
                # This catches transactions that were manually linked as portfolio_transfer
                # or have different external_id formats
                if tx_hash and wallet.account_id:
                    # Map incoming/outgoing types for matching
                    type_patterns = [tx_type]
                    if tx_type == 'transfer_in':
                        type_patterns.extend(['portfolio_transfer', 'deposit'])
                    elif tx_type == 'transfer_out':
                        type_patterns.extend(['portfolio_transfer', 'withdrawal'])
                    
                    existing_by_hash = db.query(Transaction).filter(
                        Transaction.blockchain_tx_hash == tx_hash,
                        Transaction.account_id == wallet.account_id,
                        Transaction.type.in_(type_patterns)
                    ).first()
                    if existing_by_hash:
                        skipped_count += 1
                        continue
                
                # Use the base CRUD function to create transaction with idempotency
                transaction = create_transaction_idempotent(db, enriched_tx_data)
                
                if transaction is None:
                    # Transaction skipped (e.g., missing FX rate)
                    skipped_count += 1
                    logger.warning(f"Skipped crypto transaction {ext_id} - will retry in next sync")
                    continue
                
                # Enrich transactions with price data if value_native is missing
                # This handles ALL transaction types from blockchain providers that lack price data
                if transaction.value_native is None:
                    tx_type = transaction.type
                    # Enrich ALL transaction types that are missing price data
                    # Blockchain APIs don't provide USD values, so we need to populate them
                    # Include 'spam' to properly record the (tiny) value of spam transactions
                    enrichable_types = ['staking_reward', 'interest', 'dividend', 'fee', 
                                       'transfer_in', 'transfer_out', 'spam']
                    if tx_type in enrichable_types:
                        logger.debug(f"Enriching {tx_type} transaction {transaction.id} with price data")
                        # Use portfolio's base currency, falling back to global base currency
                        tx_base_currency = transaction.currency_base or get_global_base_currency()
                        populate_transaction_price(db, transaction, base_currency=tx_base_currency)
                
                new_count += 1
                
            except Exception as e:
                failure_count += 1
                logger.error(f"Failed to ingest transaction {tx_data.get('external_id', 'unknown')}: {e}")
        
        # Update last_updated timestamp after successful sync
        wallet.last_updated = end_date
        db.commit()
        
        logger.info(f"Transaction sync complete for {wallet.symbol} address {identifier[:30]}. New: {new_count}, Skipped (already exist): {skipped_count}, Failed: {failure_count}")
        
    except Exception as e:
        identifier = str(wallet.address) if wallet.address else "unknown"
        logger.error(f"Error syncing transactions for {wallet.symbol} address {identifier[:30]}: {e}", exc_info=True)


def _enrich_transaction_data(tx_data: dict, wallet: CryptoWallet) -> dict:
    """
    Enrich transaction data with wallet-specific information for the Transaction model.
    
    CRITICAL: Sets from_crypto_wallet_id and to_crypto_wallet_id for internal transfer detection.
    
    Wallet ID Logic:
    - Incoming transactions (ADD to wallet): Set to_crypto_wallet_id
      * transfer_in, deposit, buy, staking_reward, dividend, interest
    - Outgoing transactions (REMOVE from wallet): Set from_crypto_wallet_id
      * transfer_out, withdrawal, sell, fee
    
    Args:
        tx_data: Raw transaction data from blockchain provider
        wallet: CryptoWallet instance
        
    Returns:
        dict: Enriched transaction data ready for Transaction model
    """
    tx_type = tx_data.get('type')
    
    # Transactions that ADD assets to this wallet (incoming)
    # Include 'spam' - these are unsolicited airdrops that technically add to wallet
    if tx_type in ['transfer_in', 'deposit', 'buy', 'staking_reward', 'dividend', 'interest', 'spam']:
        tx_data['to_crypto_wallet_id'] = wallet.id
        logger.debug(f"Set to_crypto_wallet_id={wallet.id} for {tx_type}")
    
    # Transactions that REMOVE assets from this wallet (outgoing)
    elif tx_type in ['transfer_out', 'withdrawal', 'sell', 'fee']:
        tx_data['from_crypto_wallet_id'] = wallet.id
        logger.debug(f"Set from_crypto_wallet_id={wallet.id} for {tx_type}")
    
    # Add portfolio and account information
    # Note: Account model doesn't have portfolio_id - need to determine portfolio another way
    # For now, use portfolio_id 5 (Crypto HODL) for crypto wallet transactions
    # TODO: Make this configurable per wallet or account
    tx_data['portfolio_id'] = 5  # Crypto HODL portfolio
    tx_data['account_id'] = wallet.account_id
    tx_data['currency_base'] = 'EUR'  # Crypto HODL portfolio uses EUR as base currency
    
    # Add category for transaction classification
    tx_type = tx_data.get('type')
    if tx_type in ['transfer_in', 'transfer_out']:
        tx_data['category'] = 'external_transfer'  # Blockchain transfers are external
    elif tx_type == 'fee':
        tx_data['category'] = 'fee'
    elif tx_type in ['staking_reward', 'dividend', 'interest']:
        tx_data['category'] = 'income'  # Passive income from holding crypto
    
    return tx_data


def _fetch_wallet_balance_for_validation(wallet: CryptoWallet, provider):
    """
    Fetch current balance for validation purposes (optional in transaction-based system).
    This can be used for reconciliation to ensure transaction history matches actual balance.
    Supports both single addresses and xpub (for BTC).
    
    Args:
        wallet: CryptoWallet instance
        provider: Blockchain provider module
    """
    try:
        balance = None
        
        # Get wallet address (BTC provider auto-detects xpub format)
        if wallet.address and wallet.address.strip():
            identifier = wallet.address
            
            # Handle different provider response formats
            if wallet.symbol.lower() == 'sol':
                # SOL provider automatically discovers staking - no manual flag needed
                balance_data = provider.get_balance(identifier, include_staking=True)
                if balance_data:
                    balance = balance_data.get('total', 0)  # Use total which includes liquid + staked
            elif wallet.symbol.lower() == 'ada':
                balance_data = provider.get_balance(identifier)
                if balance_data:
                    # User requested: only show staked amount
                    balance = balance_data.get('staked', 0)
            elif wallet.symbol.lower() == 'bnb':
                # BNB (BSC) provider automatically discovers staking - no manual contract needed
                balance_data = provider.get_balance(identifier)
                if balance_data:
                    balance = balance_data.get('total', 0)  # Use total which includes liquid + staked
                else:
                    balance = provider.get_balance(identifier) if isinstance(provider.get_balance(identifier), (int, float)) else 0
            else:
                # BTC provider auto-detects xpub format and calls appropriate function
                balance = provider.get_balance(identifier)
        else:
            logger.warning(f"Wallet {wallet.id} has no address defined")
            return

        if balance is not None:
            logger.info(f"Current balance for {identifier}: {balance} {wallet.symbol.upper()}")
            # TODO: Could store this for reconciliation purposes if needed
        else:
            logger.warning(f"Could not fetch balance for {wallet.symbol} address {identifier[:30]}")
            
    except Exception as e:
        identifier = str(wallet.address) if wallet.address else "unknown"
        logger.error(f"Error fetching balance for {wallet.symbol} address {identifier[:30]}: {e}")


def sync_wallet_by_address(address: str, chain: str, days_back: int = 7):
    """
    Sync a specific wallet by address and chain.
    
    Args:
        address: Wallet address
        chain: Blockchain chain (btc, eth, etc.)
        days_back: Number of days back to sync
    """
    logger.info(f"Syncing specific wallet: {address} on {chain}")
    db = SessionLocal()
    try:
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address == address,
            CryptoWallet.symbol.ilike(chain)
        ).first()
        
        if not wallet:
            logger.error(f"Wallet not found: {address} on {chain}")
            return
        
        provider = PROVIDER_MAP.get(chain.lower())
        if not provider:
            logger.error(f"No provider found for chain: {chain}")
            return
        
        _sync_wallet_transactions(db, wallet, days_back)
        
    except Exception as e:
        logger.error(f"Error syncing wallet {address}: {e}", exc_info=True)
    finally:
        db.close()


def sync_wallet_by_xpub(xpub: str, days_back: int = 7):
    """
    Sync a specific BTC wallet by xpub.
    
    Args:
        xpub: Extended public key
        days_back: Number of days back to sync
    """
    logger.info(f"Syncing BTC wallet by xpub: {xpub[:20]}...")
    db = SessionLocal()
    try:
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.xpub == xpub,
            CryptoWallet.symbol.ilike('btc')
        ).first()
        
        if not wallet:
            logger.error(f"BTC wallet not found for xpub: {xpub[:20]}...")
            return
        
        provider = PROVIDER_MAP.get('btc')
        if not provider:
            logger.error("No BTC provider found")
            return
        
        _sync_wallet_transactions(db, wallet, days_back)
        
    except Exception as e:
        logger.error(f"Error syncing xpub wallet {xpub[:20]}...: {e}", exc_info=True)
    finally:
        db.close()


def create_btc_xpub_wallet(account_id: int, xpub: str, label: Optional[str] = None, 
                          gap_limit: int = 20) -> int:
    """
    Create a new BTC wallet using xpub.
    
    Args:
        account_id: Account ID to associate with
        xpub: Extended public key (xpub/ypub/zpub - address type auto-detected from prefix)
        label: Friendly label for the wallet
        gap_limit: Gap limit for address discovery (default: 20)
        
    Returns:
        Wallet ID of created wallet
        
    Note:
        Address type is automatically determined from xpub prefix:
        - xpub = Legacy (P2PKH) - addresses start with 1
        - ypub = SegWit (P2SH-P2WPKH) - addresses start with 3
        - zpub = Native SegWit (P2WPKH) - addresses start with bc1
    """
    from utils.xpub_utils import validate_xpub
    
    if not validate_xpub(xpub):
        raise ValueError(f"Invalid xpub provided: {xpub[:20]}...")
    
    db = SessionLocal()
    try:
        # Check if xpub already exists
        existing = db.query(CryptoWallet).filter(CryptoWallet.xpub == xpub).first()
        if existing:
            logger.warning(f"Wallet with xpub already exists: ID {existing.id}")
            return existing.id
        
        # Create new wallet (address type auto-detected from xpub prefix)
        wallet = CryptoWallet(
            account_id=account_id,
            chain='BTC',
            xpub=xpub,
            gap_limit=gap_limit,
            label=label or f"BTC xpub wallet ({xpub[:20]}...)"
        )
        
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
        
        logger.info(f"Created BTC xpub wallet: ID {wallet.id}, Label: {wallet.label}")
        return wallet.id
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating BTC xpub wallet: {e}")
        raise
    finally:
        db.close()


def sync_wallet_with_balance_tracking(
    wallet_id: int,
    days_back: int = 7,
    record_balance: bool = True
) -> dict:
    """
    Sync wallet transactions and record balance snapshot.
    This is the enhanced version that tracks balance history.
    
    Args:
        wallet_id: CryptoWallet ID
        days_back: Number of days back to sync
        record_balance: Whether to record balance snapshot (default: True)
    
    Returns:
        dict: Sync results with transaction and balance counts
    """
    logger.info(f"Syncing wallet {wallet_id} with balance tracking")
    db = SessionLocal()
    
    try:
        wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
        if not wallet:
            logger.error(f"Wallet {wallet_id} not found")
            return {'success': False, 'error': 'Wallet not found'}
        
        provider = PROVIDER_MAP.get(wallet.symbol.lower())
        if not provider:
            logger.error(f"No provider found for chain: {wallet.symbol}")
            return {'success': False, 'error': 'Provider not found'}
        
        # Sync transactions (uses incremental sync with wallet.last_updated)
        _sync_wallet_transactions(db, wallet, days_back)
        
        result = {
            'success': True,
            'wallet_id': wallet_id,
            'chain': wallet.symbol,
            'transactions_synced': True
        }
        
        # Record balance snapshot if requested
        if record_balance:
            balance_result = _record_wallet_balance_snapshot(db, wallet, provider)
            result['balance_recorded'] = balance_result
        
        return result
        
    except Exception as e:
        logger.error(f"Error syncing wallet {wallet_id} with balance tracking: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def _record_wallet_balance_snapshot(
    db,
    wallet: CryptoWallet,
    provider
) -> dict:
    """
    Fetch and record current balance snapshot for a wallet.
    Handles different provider response formats (liquid, staked, etc.).
    
    Args:
        db: Database session
        wallet: CryptoWallet instance
        provider: Blockchain provider module
    
    Returns:
        dict: Result with success/failure counts
    """
    try:
        balance_data = None
        # Map chain to native token symbol (e.g., BSC → BNB)
        symbol = NATIVE_TOKEN_MAP.get(wallet.symbol.upper(), wallet.symbol.upper())
        
        # Fetch balance based on chain
        if wallet.symbol.lower() == 'sol':
            # SOL provider automatically discovers staking
            balance_data = provider.get_balance(wallet.address, include_staking=True)
        elif wallet.symbol.lower() == 'ada':
            balance_data = provider.get_balance(wallet.address)
        elif wallet.symbol.lower() == 'bnb':
            # BNB provider automatically discovers staking
            balance_data = provider.get_balance(wallet.address)
        else:
            # Simple balance for BTC, ETH, XRP, ADA
            balance = provider.get_balance(wallet.address)
            
            # Handle different provider return formats
            if isinstance(balance, dict):
                # ETH provider returns {'balance': value}
                if 'balance' in balance:
                    balance = balance['balance']
                # XRP provider returns {'total': value, 'available': value, 'reserved': value}
                elif 'total' in balance:
                    balance = balance['total']
                # ADA provider returns {'total': value, 'available': value, 'rewards': value}
                elif 'available' in balance:
                    balance = balance.get('total', balance.get('available', 0))
            
            balance_data = {'liquid': balance} if balance is not None else None
        
        if not balance_data:
            logger.warning(f"Could not fetch balance for wallet {wallet.id}")
            return {'success': 0, 'failed': 0}
        
        # Record balance snapshot
        success, failed = record_balance_snapshot(
            db=db,
            wallet_id=wallet.id,
            symbol=symbol,
            balances=balance_data,
            timestamp=now_utc()
        )
        
        # Log balance with appropriate detail based on what's available
        if wallet.symbol.lower() in ['sol', 'bnb']:
            # For chains with staking, show breakdown
            liquid = balance_data.get('liquid', 0)
            staked = balance_data.get('staked', 0)
            total = balance_data.get('total', liquid + staked)
            logger.info(
                f"Recorded balance snapshot for wallet {wallet.id} ({symbol}): "
                f"liquid={liquid:.8f}, staked={staked:.8f}, total={total:.8f}"
            )
        elif wallet.symbol.lower() == 'ada':
            # ADA has rewards
            liquid = balance_data.get('liquid', 0)
            rewards = balance_data.get('rewards', 0)
            logger.info(
                f"Recorded balance snapshot for wallet {wallet.id} ({symbol}): "
                f"liquid={liquid:.8f}, rewards={rewards:.8f}"
            )
        else:
            # Simple balance
            logger.info(f"Recorded balance snapshot for wallet {wallet.id} ({symbol}): {balance_data}")
        
        return {'success': success, 'failed': failed, 'balances': balance_data}
        
    except Exception as e:
        logger.error(f"Error recording balance snapshot for wallet {wallet.id}: {e}", exc_info=True)
        return {'success': 0, 'failed': 1, 'error': str(e)}


def sync_all_wallets_with_balance_tracking(
    account_id: Optional[int] = None,
    days_back: int = 7
) -> dict:
    """
    Sync all wallets (or wallets for specific account) with balance tracking.
    Only syncs wallets from accounts with status='active'.
    
    Args:
        account_id: Optional account ID to filter wallets
        days_back: Number of days back to sync
    
    Returns:
        dict: Summary of sync results
    """
    logger.info(f"Syncing all wallets with balance tracking (account_id={account_id})")
    db = SessionLocal()
    
    try:
        # Get wallets, joining with accounts to check status
        query = db.query(CryptoWallet).join(Account, CryptoWallet.account_id == Account.id)
        
        if account_id:
            query = query.filter(CryptoWallet.account_id == account_id)
        
        # Filter to only active accounts
        query = query.filter(Account.status == 'active')
        
        wallets = query.all()
        
        # Log if any accounts were skipped
        all_wallets_count = db.query(CryptoWallet).filter(
            CryptoWallet.account_id == account_id if account_id else True
        ).count()
        
        if all_wallets_count > len(wallets):
            skipped = all_wallets_count - len(wallets)
            logger.info(f"Found {len(wallets)} active wallets to sync ({skipped} wallets skipped from closed accounts)")
        else:
            logger.info(f"Found {len(wallets)} wallets to sync")
        
        results = {
            'total_wallets': len(wallets),
            'successful': 0,
            'failed': 0,
            'details': []
        }
        
        for wallet in wallets:
            wallet_result = sync_wallet_with_balance_tracking(
                wallet_id=wallet.id,
                days_back=days_back,
                record_balance=True
            )
            
            if wallet_result.get('success'):
                results['successful'] += 1
            else:
                results['failed'] += 1
            
            results['details'].append(wallet_result)
        
        logger.info(f"Sync complete. Successful: {results['successful']}, Failed: {results['failed']}")
        return results
        
    except Exception as e:
        logger.error(f"Error in bulk wallet sync: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def reconcile_wallet_balance(wallet_id: int) -> dict:
    """
    Reconcile stored balance vs blockchain API balance.
    
    Args:
        wallet_id: CryptoWallet ID
    
    Returns:
        dict: Reconciliation report
    """
    logger.info(f"Reconciling balance for wallet {wallet_id}")
    db = SessionLocal()
    
    try:
        wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
        if not wallet:
            return {'error': 'Wallet not found'}
        
        provider = PROVIDER_MAP.get(wallet.symbol.lower())
        if not provider:
            return {'error': 'Provider not found'}
        
        # Fetch current blockchain balance
        symbol = wallet.symbol.upper()
        balance_data = None
        
        if wallet.symbol.lower() == 'sol':
            # SOL provider automatically discovers staking
            balance_data = provider.get_balance(wallet.address, include_staking=True)
        elif wallet.symbol.lower() == 'ada':
            balance_data = provider.get_balance(wallet.address)
        elif wallet.symbol.lower() == 'bnb':
            # BNB provider automatically discovers staking
            balance_data = provider.get_balance(wallet.address)
        else:
            balance = provider.get_balance(wallet.address)
            balance_data = {'liquid': balance} if balance is not None else None
        
        if not balance_data:
            return {'error': 'Could not fetch blockchain balance'}
        
        # Reconcile with stored balance
        report = reconcile_balance(db, wallet_id, symbol, balance_data)
        
        logger.info(f"Reconciliation complete for wallet {wallet_id}. Reconciled: {report['reconciled']}")
        return report
        
    except Exception as e:
        logger.error(f"Error reconciling wallet {wallet_id}: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def detect_internal_transfers_for_account(
    account_id: int,
    lookback_days: int = 30
) -> dict:
    """
    Auto-detect and link internal transfers between wallets of an account.
    
    DEPRECATED: Use detect_internal_transfers_global() for comprehensive detection.
    This only detects transfers within a single account.
    
    Args:
        account_id: Account ID
        lookback_days: Number of days to look back
    
    Returns:
        dict: Results with number of links created
    """
    logger.info(f"Detecting internal transfers for account {account_id} (intra-account only)")
    db = SessionLocal()
    
    try:
        links_created = auto_detect_internal_transfers(
            db=db,
            account_id=account_id,
            lookback_days=lookback_days
        )
        
        return {
            'success': True,
            'account_id': account_id,
            'links_created': links_created
        }
        
    except Exception as e:
        logger.error(f"Error detecting internal transfers for account {account_id}: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def detect_internal_transfers_global(
    lookback_days: int = 30
) -> dict:
    """
    Auto-detect and link internal transfers between ANY crypto wallets in the system.
    
    This is the RECOMMENDED function for comprehensive internal transfer detection.
    Detects transfers across account boundaries:
    - Hardware wallet → Hardware wallet (cross-account)
    - Trezor S1 → Trezor E2
    - Ledger → Trezor
    
    Args:
        lookback_days: Number of days to look back
    
    Returns:
        dict: Results with number of links created
    """
    logger.info(f"Detecting internal transfers globally (cross-account detection)")
    db = SessionLocal()
    
    try:
        links_created = auto_detect_internal_transfers_global(
            db=db,
            lookback_days=lookback_days
        )
        
        return {
            'success': True,
            'links_created': links_created
        }
        
    except Exception as e:
        logger.error(f"Error detecting internal transfers globally: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


def detect_exchange_wallet_transfers(
    lookback_days: int = 30
) -> dict:
    """
    Auto-detect and link transfers between exchanges and crypto wallets.
    
    This detects the MOST COMMON transfer patterns:
    - Exchange withdrawal → Wallet receive (Binance → Trezor)
    - Wallet send → Exchange deposit (Trezor → Binance)
    
    Args:
        lookback_days: Number of days to look back
    
    Returns:
        dict: Results with number of links created
    """
    logger.info(f"Detecting exchange ↔ wallet transfers (Binance, IBKR ↔ Hardware wallets)")
    db = SessionLocal()
    
    try:
        links_created = auto_detect_exchange_wallet_transfers(
            db=db,
            lookback_days=lookback_days
        )
        
        return {
            'success': True,
            'links_created': links_created
        }
        
    except Exception as e:
        logger.error(f"Error detecting exchange ↔ wallet transfers: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
    finally:
        db.close()


if __name__ == "__main__":
    sync_all_wallets()
