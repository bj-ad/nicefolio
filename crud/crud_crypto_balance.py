"""
CRUD operations for CryptoBalance model.

CryptoBalance stores the CURRENT staking allocation for each wallet.
- One record per wallet_id/symbol/balance_type combination
- UPSERT semantics: update if exists, insert if not
- No historical accumulation - always shows current state
- Updated by daily blockchain sync
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import CryptoBalance, CryptoWallet
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Tuple
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc

logger = get_logger(__name__)


def upsert_balance(
    db: Session,
    wallet_id: int,
    symbol: str,
    balances: Dict[str, float],
    timestamp: Optional[datetime] = None
) -> Tuple[int, int]:
    """
    Upsert current balance for a wallet.
    
    Creates or updates balance records. Each wallet/symbol/balance_type
    combination has exactly ONE record that gets updated on each sync.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        symbol: Asset symbol (e.g., 'BTC', 'ETH', 'SOL')
        balances: Dict with balance types as keys: 'liquid', 'staked', 'activating', etc.
        timestamp: When the balance was fetched (defaults to now)
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    if timestamp is None:
        timestamp = now_utc()
    
    success = 0
    failed = 0
    
    logger.debug(f"Upserting balance for wallet {wallet_id}, symbol {symbol}")
    
    for balance_type, balance_value in balances.items():
        # Skip None values
        if balance_value is None:
            continue
        
        # Convert to Decimal
        try:
            balance_decimal = Decimal(str(balance_value))
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid balance value for {balance_type}: {balance_value} - {e}")
            failed += 1
            continue
        
        try:
            # Find existing record (by wallet_id/symbol/balance_type only - no timestamp)
            existing = db.query(CryptoBalance).filter(
                and_(
                    CryptoBalance.wallet_id == wallet_id,
                    CryptoBalance.symbol == symbol,
                    CryptoBalance.balance_type == balance_type
                )
            ).first()
            
            if existing:
                # Update existing record
                existing.balance = balance_decimal
                existing.as_of_date = timestamp
                existing.last_updated = now_utc()
                logger.debug(f"Updated {balance_type}: {balance_value}")
            else:
                # Create new record
                balance_record = CryptoBalance(
                    wallet_id=wallet_id,
                    symbol=symbol,
                    balance_type=balance_type,
                    balance=balance_decimal,
                    as_of_date=timestamp
                )
                db.add(balance_record)
                logger.debug(f"Created {balance_type}: {balance_value}")
            
            success += 1
            
        except Exception as e:
            logger.error(f"Failed to upsert {balance_type} balance: {e}")
            failed += 1
    
    try:
        db.commit()
        logger.debug(f"Balance upsert complete. Success: {success}, Failed: {failed}")
    except Exception as e:
        logger.error(f"Failed to commit balance upsert: {e}")
        db.rollback()
        return 0, success + failed
    
    return success, failed


# Alias for backward compatibility
record_balance_snapshot = upsert_balance


def get_balance(
    db: Session,
    wallet_id: int,
    symbol: str
) -> Dict[str, float]:
    """
    Get current balance breakdown for a wallet.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        symbol: Asset symbol
    
    Returns:
        dict: Balance breakdown by type (e.g., {'liquid': 100.0, 'staked': 50.0, 'total': 150.0})
    """
    balances = db.query(CryptoBalance).filter(
        and_(
            CryptoBalance.wallet_id == wallet_id,
            CryptoBalance.symbol == symbol
        )
    ).all()
    
    if not balances:
        return {}
    
    return {b.balance_type: float(b.balance) for b in balances}


# Alias for backward compatibility
get_latest_balance = get_balance


def get_all_balances_for_wallet(
    db: Session,
    wallet_id: int
) -> Dict[str, Dict[str, float]]:
    """
    Get all current balances for a wallet across all symbols.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
    
    Returns:
        dict: Balances by symbol, e.g., {'BNB': {'liquid': 0.5, 'staked': 1.0}, 'SOL': {...}}
    """
    balances = db.query(CryptoBalance).filter(
        CryptoBalance.wallet_id == wallet_id
    ).all()
    
    result = {}
    for b in balances:
        if b.symbol not in result:
            result[b.symbol] = {}
        result[b.symbol][b.balance_type] = float(b.balance)
    
    return result


def reconcile_balance(
    db: Session,
    wallet_id: int,
    symbol: str,
    blockchain_balance: Dict[str, float]
) -> Dict:
    """
    Compare stored balance vs blockchain API balance.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        symbol: Asset symbol
        blockchain_balance: Balance from blockchain API (dict with balance types)
    
    Returns:
        dict: Reconciliation report with discrepancies
    """
    stored_balance = get_balance(db, wallet_id, symbol)
    
    if not stored_balance:
        logger.warning(f"No stored balance found for wallet {wallet_id}, symbol {symbol}")
        return {
            'wallet_id': wallet_id,
            'symbol': symbol,
            'stored_balance': {},
            'blockchain_balance': blockchain_balance,
            'discrepancies': {},
            'reconciled': False,
            'message': 'No stored balance found'
        }
    
    # Compare
    discrepancies = {}
    all_types = set(list(stored_balance.keys()) + list(blockchain_balance.keys()))
    
    for balance_type in all_types:
        stored = stored_balance.get(balance_type, 0.0)
        blockchain = blockchain_balance.get(balance_type, 0.0)
        
        # Allow for small floating point differences
        if abs(stored - blockchain) > 0.00000001:
            discrepancies[balance_type] = {
                'stored': stored,
                'blockchain': blockchain,
                'difference': blockchain - stored,
                'percentage_diff': ((blockchain - stored) / stored * 100) if stored > 0 else 0
            }
    
    reconciled = len(discrepancies) == 0
    
    if reconciled:
        logger.debug(f"Balance reconciled for wallet {wallet_id}, symbol {symbol}")
    else:
        logger.warning(f"Balance discrepancies for wallet {wallet_id}, symbol {symbol}: {discrepancies}")
    
    return {
        'wallet_id': wallet_id,
        'symbol': symbol,
        'stored_balance': stored_balance,
        'blockchain_balance': blockchain_balance,
        'discrepancies': discrepancies,
        'reconciled': reconciled
    }


def get_balance_summary_for_wallet(
    db: Session,
    wallet_id: int
) -> Dict:
    """
    Get summary of all balances for a wallet.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
    
    Returns:
        dict: Summary with wallet info and balances
    """
    wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
    if not wallet:
        logger.error(f"Wallet {wallet_id} not found")
        return {}
    
    balances = get_all_balances_for_wallet(db, wallet_id)
    
    return {
        'wallet_id': wallet_id,
        'chain': wallet.symbol,
        'address': wallet.address,
        'balances': balances
    }


def delete_balances_for_wallet(
    db: Session,
    wallet_id: int,
    symbol: Optional[str] = None
) -> int:
    """
    Delete balance records for a wallet.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        symbol: Optional symbol filter
    
    Returns:
        int: Number of records deleted
    """
    query = db.query(CryptoBalance).filter(CryptoBalance.wallet_id == wallet_id)
    
    if symbol:
        query = query.filter(CryptoBalance.symbol == symbol)
    
    deleted = query.delete()
    db.commit()
    
    logger.info(f"Deleted {deleted} balance records for wallet {wallet_id}" + 
                (f", symbol {symbol}" if symbol else ""))
    
    return deleted
