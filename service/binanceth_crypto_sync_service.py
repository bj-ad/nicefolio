"""
Binance.th Crypto Sync Service
Fetches crypto deposits/withdrawals from Binance.th API and stores in Transaction table.
Extends existing binanceth_service.py API calls.
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Transaction, Account, Portfolio
from service.binanceth_service import fetch_deposits, fetch_withdrawals
from service.crypto_transfer_service import _populate_price_and_value
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import load_app_config

logger = get_logger(__name__)

# Load config
config = load_app_config()
binanceth_config = config.get('binanceth', {})


def get_binanceth_account_id() -> Optional[int]:
    """Get the Binance.th account ID from database."""
    db = SessionLocal()
    try:
        account = db.query(Account).filter(
            Account.name == 'Binance.th'
        ).first()
        
        if not account:
            logger.error("Binance.th account not found in database")
            return None
        
        return account.id  # type: ignore
        
    finally:
        db.close()


def sync_crypto_deposits(
    account_id: int,
    portfolio_id: int,
    days_back: int = 7
) -> tuple[int, int]:
    """
    Sync crypto deposits from Binance.th API.
    Creates portfolio_transfer transactions (lot-neutral).
    
    Args:
        account_id: Binance.th account ID
        portfolio_id: Portfolio ID for transactions (from source_mapping config)
        days_back: Number of days to look back
    
    Returns:
        tuple[int, int]: (success_count, failed_count)
    """
    # Use config value if not specified
    if days_back is None:
        days_back = binanceth_config.get('sync_lookback_days', 7)
    
    logger.info(f"Starting crypto deposit sync for account {account_id}, last {days_back} days")
    
    # Calculate time range
    now = now_utc()
    start_date = now - timedelta(days=days_back)
    start_time = int(start_date.timestamp() * 1000)
    end_time = int(now.timestamp() * 1000)
    
    # Get deposits from API
    deposits = fetch_deposits(start_time=start_time, end_time=end_time)
    
    if not deposits:
        logger.info("No deposits found from API")
        return 0, 0
    
    # Filter for crypto only (exclude THB)
    crypto_deposits = [d for d in deposits if d.get('coin') != 'THB']
    
    logger.info(f"Found {len(crypto_deposits)} crypto deposits from API")
    
    if not crypto_deposits:
        return 0, 0
    
    # Store in database
    db = SessionLocal()
    success = 0
    skipped = 0
    failed = 0
    
    # Get portfolio base currency
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    base_currency = portfolio.currency_base if portfolio else 'EUR'
    
    try:
        for deposit in crypto_deposits:
            try:
                # Parse insertTime to get occurred_at and external_id
                insert_time = deposit.get('insertTime')
                if isinstance(insert_time, int):
                    # Unix timestamp in milliseconds
                    occurred_at = datetime.fromtimestamp(insert_time / 1000)
                    external_id = str(insert_time)
                elif isinstance(insert_time, str):
                    # Try parsing as datetime string
                    try:
                        occurred_at = datetime.strptime(insert_time, "%Y-%m-%d %H:%M:%S")
                        external_id = insert_time.replace(" ", "_").replace(":", "")
                    except ValueError:
                        logger.error(f"Unknown insertTime format: {insert_time}")
                        failed += 1
                        continue
                else:
                    logger.error(f"insertTime is neither int nor string: {type(insert_time)}")
                    failed += 1
                    continue
                
                # Check if already exists
                existing = db.query(Transaction).filter(
                    Transaction.account_id == account_id,
                    Transaction.external_id == external_id
                ).first()
                
                if existing:
                    logger.debug(f"Deposit {external_id} already exists, skipping")
                    skipped += 1
                    continue
                
                # Create transaction as portfolio_transfer (lot-neutral)
                # These are transfers TO Binance.th FROM hardware wallet
                # They will be matched with corresponding hardware wallet transfers during daily sync
                # value_native and currency_native will be populated by _populate_price_and_value() with USD values
                tx = Transaction(
                    portfolio_id=portfolio_id,
                    account_id=account_id,
                    occurred_at=occurred_at,
                    type='portfolio_transfer',  # Changed from 'deposit' to 'portfolio_transfer'
                    symbol=deposit['coin'],
                    quantity=Decimal(str(deposit['amount'])),
                    value_native=None,  # Will be populated with USD value by transfer service
                    fee=Decimal('0'),  # Deposits typically have no fee on Binance.th side
                    currency_native=None,  # Will be set to 'USD' by transfer service
                    currency_base=base_currency,
                    source='binanceth_crypto_sync',
                    category='transfer',
                    external_id=external_id,
                    notes=f"Crypto transfer from hardware wallet. Status: {deposit['status']}, Network: {deposit.get('network', 'N/A')}"
                )
                
                db.add(tx)
                db.flush()  # Get transaction ID
                
                # Populate price and value fields with USD values
                _populate_price_and_value(db, tx)
                
                success += 1
                
            except Exception as e:
                logger.error(f"Failed to process deposit {deposit.get('insertTime')}: {e}")
                failed += 1
                continue
        
        db.commit()
        logger.info(f"Crypto deposit sync complete. New: {success}, Skipped: {skipped}, Failed: {failed}")
        # Return success+skipped as total "success" for compatibility
        return success + skipped, failed
        
    except Exception as e:
        db.rollback()
        logger.error(f"Database error during deposit sync: {e}", exc_info=True)
        return success, failed
    finally:
        db.close()


def sync_crypto_withdrawals(
    account_id: int,
    portfolio_id: int,
    days_back: int = 7
) -> tuple[int, int]:
    """
    Sync crypto withdrawals from Binance.th API.
    Creates portfolio_transfer transactions (lot-neutral).
    
    Args:
        account_id: Binance.th account ID
        portfolio_id: Portfolio ID for transactions (from source_mapping config)
        days_back: Number of days to look back
    
    Returns:
        tuple[int, int]: (success_count, failed_count)
    """
    # Use config value if not specified
    if days_back is None:
        days_back = binanceth_config.get('sync_lookback_days', 7)
    
    logger.info(f"Starting crypto withdrawal sync for account {account_id}, last {days_back} days")
    
    # Calculate time range
    now = now_utc()
    start_date = now - timedelta(days=days_back)
    start_time = int(start_date.timestamp() * 1000)
    end_time = int(now.timestamp() * 1000)
    
    # Get withdrawals from API
    withdrawals = fetch_withdrawals(start_time=start_time, end_time=end_time)
    
    if not withdrawals:
        logger.info("No withdrawals found from API")
        return 0, 0
    
    # Filter for crypto only (exclude THB)
    crypto_withdrawals = [w for w in withdrawals if w.get('coin') != 'THB']
    
    logger.info(f"Found {len(crypto_withdrawals)} crypto withdrawals from API")
    
    if not crypto_withdrawals:
        return 0, 0
    
    # Store in database
    db = SessionLocal()
    success = 0
    skipped = 0
    failed = 0
    
    # Get portfolio base currency
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    base_currency = portfolio.currency_base if portfolio else 'EUR'
    
    try:
        for withdrawal in crypto_withdrawals:
            try:
                apply_time = withdrawal['applyTime']
                
                # Parse timestamp - handle both int (milliseconds) and string (datetime) formats
                if isinstance(apply_time, int):
                    # Unix timestamp in milliseconds
                    occurred_at = datetime.fromtimestamp(apply_time / 1000)
                    external_id = str(apply_time)
                elif isinstance(apply_time, str):
                    # Try parsing as datetime string (e.g., "2025-08-26 00:46:29")
                    try:
                        occurred_at = datetime.strptime(apply_time, "%Y-%m-%d %H:%M:%S")
                        external_id = apply_time.replace(" ", "_").replace(":", "")  # "20250826_004629"
                    except ValueError:
                        logger.error(f"Unknown applyTime format: {apply_time}")
                        failed += 1
                        continue
                else:
                    logger.error(f"applyTime is neither int nor string: {type(apply_time)}")
                    failed += 1
                    continue
                
                # Check if already exists
                existing = db.query(Transaction).filter(
                    Transaction.account_id == account_id,
                    Transaction.external_id == external_id
                ).first()
                
                if existing:
                    logger.debug(f"Withdrawal {external_id} already exists, skipping")
                    skipped += 1
                    continue
                
                # Create transaction as portfolio_transfer (lot-neutral)
                # These are transfers FROM Binance.th TO hardware wallet
                # They will be matched with corresponding hardware wallet transfers during daily sync
                # value_native and currency_native will be populated by _populate_price_and_value() with USD values
                tx = Transaction(
                    portfolio_id=portfolio_id,
                    account_id=account_id,
                    occurred_at=occurred_at,
                    type='portfolio_transfer',  # Changed from 'withdrawal' to 'portfolio_transfer'
                    symbol=withdrawal['coin'],
                    quantity=-Decimal(str(withdrawal['amount'])),  # Negative for outgoing
                    value_native=None,  # Will be populated with USD value by transfer service
                    fee=Decimal(str(withdrawal.get('transactionFee', 0))),
                    currency_native=None,  # Will be set to 'USD' by transfer service
                    currency_base=base_currency,
                    source='binanceth_crypto_sync',
                    category='transfer',
                    external_id=external_id,
                    notes=f"Crypto transfer to hardware wallet. Status: {withdrawal['status']}, Network: {withdrawal.get('network', 'N/A')}"
                )
                
                db.add(tx)
                db.flush()  # Get transaction ID
                
                # Populate price and value fields with USD values
                _populate_price_and_value(db, tx)
                
                success += 1
                
            except Exception as e:
                logger.error(f"Failed to process withdrawal {withdrawal.get('applyTime')}: {e}")
                failed += 1
                continue
        
        db.commit()
        logger.info(f"Crypto withdrawal sync complete. New: {success}, Skipped: {skipped}, Failed: {failed}")
        # Return success+skipped as total "success" for compatibility
        return success + skipped, failed
        
    except Exception as e:
        db.rollback()
        logger.error(f"Database error during withdrawal sync: {e}", exc_info=True)
        return success, failed
    finally:
        db.close()


def sync_all_crypto_transactions(
    account_id: int,
    portfolio_id: int,
    days_back: int = 7
) -> tuple[int, int]:
    """
    Sync both deposits and withdrawals.
    
    Args:
        account_id: Binance.th account ID
        portfolio_id: Portfolio ID for transactions
        days_back: Number of days to look back (default: 7)
    
    Returns:
        tuple[int, int]: (success_count, failure_count) combined
    """
    logger.info("Starting full crypto transaction sync")
    
    # Sync deposits (returns new + skipped, failed)
    deposit_success, deposit_failed = sync_crypto_deposits(account_id, portfolio_id, days_back)
    
    # Sync withdrawals (returns new + skipped, failed)
    withdrawal_success, withdrawal_failed = sync_crypto_withdrawals(account_id, portfolio_id, days_back)
    
    # Note: success includes both new and skipped transactions
    total_success = deposit_success + withdrawal_success
    total_failed = deposit_failed + withdrawal_failed
    
    logger.info(f"Full crypto sync complete. Total success: {total_success}, Total failed: {total_failed}")
    return total_success, total_failed


def get_crypto_transaction_summary(account_id: int, days: int = 7) -> str:
    """
    Get a summary of recent crypto transactions for display in UI.
    
    Args:
        account_id: Binance.th account ID
        days: Number of days to look back
    
    Returns:
        str: Summary text
    """
    db = SessionLocal()
    try:
        cutoff = now_utc() - timedelta(days=days)
        
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.occurred_at >= cutoff,
            Transaction.source == 'binanceth_crypto_sync'
        ).order_by(Transaction.occurred_at.desc()).all()
        
        if not transactions:
            return f"No crypto transactions found in last {days} days"
        
        summary_lines = [f"Last {days} days crypto transactions:"]
        
        for tx in transactions:
            summary_lines.append(
                f"  {tx.occurred_at.strftime('%Y-%m-%d %H:%M')}: "
                f"{tx.type.upper()} {tx.value_native} {tx.symbol}"
            )
        
        return "\n".join(summary_lines)
        
    finally:
        db.close()
