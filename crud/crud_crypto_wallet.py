from sqlalchemy.orm import Session
from datetime import date, timedelta
from models import CryptoWallet
from service.crypto_wallet_service import (
    _sync_wallet_transactions,
    _fetch_wallet_balance_for_validation,
    PROVIDER_MAP
)
from utils.logging_config import get_logger

logger = get_logger(__name__)


def sync_wallet_transactions(
    db: Session,
    wallet: CryptoWallet,
    days_back: int = 7,
    validate_balance: bool = False
) -> dict:
    """
    High-level function to sync transactions for a specific crypto wallet.
    
    This function leverages the blockchain provider layer and service layer to:
    - Fetch transactions from blockchain APIs (via providers)
    - Normalize and enrich transaction data (via service)
    - Insert transactions into database with idempotency (via crud_base)
    
    Args:
        db: Database session
        wallet: CryptoWallet instance to sync
        days_back: Number of days back to sync transactions (default: 7)
        validate_balance: Whether to fetch and validate current balance (default: False)
    
    Returns:
        dict: Summary of sync operation with keys 'success', 'message', 'transaction_count'
    """
    logger.info(f"Starting transaction sync for wallet {wallet.id} ({wallet.symbol})")
    
    try:
        # Get the appropriate blockchain provider
        provider = PROVIDER_MAP.get(wallet.symbol.lower())
        if not provider:
            error_msg = f"No provider found for chain: {wallet.symbol}"
            logger.error(error_msg)
            return {'success': False, 'message': error_msg, 'transaction_count': 0}
        
        # Calculate date range
        end_date = date.today()
        start_date = end_date - timedelta(days=days_back)
        
        # Count existing transactions before sync for comparison
        from models import Transaction
        initial_count = db.query(Transaction).filter(
            (Transaction.from_crypto_wallet_id == wallet.id) |
            (Transaction.to_crypto_wallet_id == wallet.id)
        ).count()
        
        # Sync transactions using service layer
        _sync_wallet_transactions(db, wallet, provider, start_date, end_date)
        
        # Count transactions after sync
        final_count = db.query(Transaction).filter(
            (Transaction.from_crypto_wallet_id == wallet.id) |
            (Transaction.to_crypto_wallet_id == wallet.id)
        ).count()
        
        new_transactions = final_count - initial_count
        
        # Optional: Validate balance for reconciliation
        if validate_balance:
            _fetch_wallet_balance_for_validation(wallet, provider)
        
        success_msg = f"Successfully synced wallet {wallet.id}. New transactions: {new_transactions}"
        logger.info(success_msg)
        
        return {
            'success': True,
            'message': success_msg,
            'transaction_count': new_transactions,
            'total_transactions': final_count
        }
        
    except Exception as e:
        error_msg = f"Error syncing wallet {wallet.id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'success': False, 'message': error_msg, 'transaction_count': 0}


def sync_all_wallets_for_account(
    db: Session,
    account_id: int,
    days_back: int = 7,
    validate_balance: bool = False
) -> dict:
    """
    Sync all crypto wallets for a specific account.
    
    Args:
        db: Database session
        account_id: Account ID to sync wallets for
        days_back: Number of days back to sync (default: 7)
        validate_balance: Whether to validate balances (default: False)
    
    Returns:
        dict: Summary with 'success', 'message', 'wallets_synced', 'total_transactions'
    """
    logger.info(f"Syncing all wallets for account {account_id}")
    
    try:
        # Get all wallets for this account
        wallets = db.query(CryptoWallet).filter(
            CryptoWallet.account_id == account_id
        ).all()
        
        if not wallets:
            msg = f"No wallets found for account {account_id}"
            logger.warning(msg)
            return {'success': True, 'message': msg, 'wallets_synced': 0, 'total_transactions': 0}
        
        logger.info(f"Found {len(wallets)} wallets for account {account_id}")
        
        total_new_transactions = 0
        wallets_synced = 0
        
        for wallet in wallets:
            result = sync_wallet_transactions(db, wallet, days_back, validate_balance)
            if result['success']:
                wallets_synced += 1
                total_new_transactions += result['transaction_count']
        
        success_msg = f"Synced {wallets_synced} wallets with {total_new_transactions} new transactions"
        logger.info(success_msg)
        
        return {
            'success': True,
            'message': success_msg,
            'wallets_synced': wallets_synced,
            'total_transactions': total_new_transactions
        }
        
    except Exception as e:
        error_msg = f"Error syncing wallets for account {account_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'success': False, 'message': error_msg, 'wallets_synced': 0, 'total_transactions': 0}


def sync_wallet_by_id(
    db: Session,
    wallet_id: int,
    days_back: int = 7,
    validate_balance: bool = False
) -> dict:
    """
    Sync a specific crypto wallet by its ID.
    
    Args:
        db: Database session
        wallet_id: Wallet ID to sync
        days_back: Number of days back to sync (default: 7)
        validate_balance: Whether to validate balance (default: False)
    
    Returns:
        dict: Summary of sync operation
    """
    logger.info(f"Syncing wallet by ID: {wallet_id}")
    
    try:
        wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
        
        if not wallet:
            error_msg = f"Wallet not found: {wallet_id}"
            logger.error(error_msg)
            return {'success': False, 'message': error_msg, 'transaction_count': 0}
        
        return sync_wallet_transactions(db, wallet, days_back, validate_balance)
        
    except Exception as e:
        error_msg = f"Error syncing wallet {wallet_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'success': False, 'message': error_msg, 'transaction_count': 0}


def sync_wallet_by_address(
    db: Session,
    address: str,
    chain: str,
    days_back: int = 7,
    validate_balance: bool = False
) -> dict:
    """
    Sync a crypto wallet by its address and chain.
    
    Args:
        db: Database session
        address: Wallet address
        chain: Blockchain chain (e.g., 'btc', 'eth', 'bsc')
        days_back: Number of days back to sync (default: 7)
        validate_balance: Whether to validate balance (default: False)
    
    Returns:
        dict: Summary of sync operation
    """
    logger.info(f"Syncing wallet by address: {address} on {chain}")
    
    try:
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address == address,
            CryptoWallet.symbol.ilike(chain)
        ).first()
        
        if not wallet:
            error_msg = f"Wallet not found: {address} on {chain}"
            logger.error(error_msg)
            return {'success': False, 'message': error_msg, 'transaction_count': 0}
        
        return sync_wallet_transactions(db, wallet, days_back, validate_balance)
        
    except Exception as e:
        error_msg = f"Error syncing wallet {address}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'success': False, 'message': error_msg, 'transaction_count': 0}


def sync_wallet_by_xpub(
    db: Session,
    xpub: str,
    days_back: int = 7,
    validate_balance: bool = False
) -> dict:
    """
    Sync a BTC wallet by its extended public key (xpub).
    
    Args:
        db: Database session
        xpub: Extended public key
        days_back: Number of days back to sync (default: 7)
        validate_balance: Whether to validate balance (default: False)
    
    Returns:
        dict: Summary of sync operation
    """
    logger.info(f"Syncing BTC wallet by xpub: {xpub[:20]}...")
    
    try:
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.xpub == xpub,
            CryptoWallet.symbol.ilike('btc')
        ).first()
        
        if not wallet:
            error_msg = f"BTC wallet not found for xpub: {xpub[:20]}..."
            logger.error(error_msg)
            return {'success': False, 'message': error_msg, 'transaction_count': 0}
        
        return sync_wallet_transactions(db, wallet, days_back, validate_balance)
        
    except Exception as e:
        error_msg = f"Error syncing xpub wallet {xpub[:20]}...: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'success': False, 'message': error_msg, 'transaction_count': 0}


def get_wallet_transaction_summary(db: Session, wallet_id: int) -> dict:
    """
    Get transaction summary for a specific wallet.
    
    Args:
        db: Database session
        wallet_id: Wallet ID
    
    Returns:
        dict: Transaction summary with counts and date ranges
    """
    from models import Transaction
    from sqlalchemy import func
    
    try:
        # Query transactions related to this wallet
        transactions = db.query(Transaction).filter(
            (Transaction.from_crypto_wallet_id == wallet_id) |
            (Transaction.to_crypto_wallet_id == wallet_id)
        )
        
        total_count = transactions.count()
        
        if total_count == 0:
            return {
                'wallet_id': wallet_id,
                'total_transactions': 0,
                'earliest_transaction': None,
                'latest_transaction': None,
                'transaction_types': {}
            }
        
        # Get date range
        earliest = transactions.order_by(Transaction.occurred_at.asc()).first()
        latest = transactions.order_by(Transaction.occurred_at.desc()).first()
        
        # Get transaction type breakdown
        type_counts = db.query(
            Transaction.type,
            func.count(Transaction.id)
        ).filter(
            (Transaction.from_crypto_wallet_id == wallet_id) |
            (Transaction.to_crypto_wallet_id == wallet_id)
        ).group_by(Transaction.type).all()
        
        transaction_types = {tx_type: count for tx_type, count in type_counts}
        
        return {
            'wallet_id': wallet_id,
            'total_transactions': total_count,
            'earliest_transaction': earliest.occurred_at if earliest else None,
            'latest_transaction': latest.occurred_at if latest else None,
            'transaction_types': transaction_types
        }
        
    except Exception as e:
        logger.error(f"Error getting transaction summary for wallet {wallet_id}: {e}")
        return {
            'wallet_id': wallet_id,
            'error': str(e)
        }


def calculate_wallet_balance_from_transactions(db: Session, wallet_id: int, symbol: str = None) -> dict:
    """
    Calculate wallet balance based on transaction history using the crud_base helper.
    
    Args:
        db: Database session
        wallet_id: Wallet ID
        symbol: Optional symbol to filter by (e.g., 'BTC', 'ETH')
    
    Returns:
        dict: Balance information
    """
    from crud.crud_base import sum_qty_by_wallet_symbol
    from models import Transaction
    
    try:
        wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
        
        if not wallet:
            return {'error': f'Wallet {wallet_id} not found'}
        
        # If no symbol specified, use the chain's native token
        if symbol is None:
            symbol = wallet.symbol.upper()
        
        # Use the base function to sum quantities
        balance = sum_qty_by_wallet_symbol(db, wallet_id, symbol)
        
        # Check if address is an xpub (starts with xpub/ypub/zpub)
        is_xpub = wallet.address and any(wallet.address.startswith(prefix) for prefix in ['xpub', 'ypub', 'zpub'])
        display_address = f"xpub:{wallet.address[:20]}..." if is_xpub else wallet.address
        
        return {
            'wallet_id': wallet_id,
            'chain': wallet.symbol,
            'symbol': symbol,
            'balance': float(balance),
            'address': display_address
        }
        
    except Exception as e:
        logger.error(f"Error calculating balance for wallet {wallet_id}: {e}")
        return {
            'wallet_id': wallet_id,
            'error': str(e)
        }
