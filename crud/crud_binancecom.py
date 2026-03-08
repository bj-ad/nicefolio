from sqlalchemy.orm import Session
from .parsers.binancecom_parser import (
    parse_binancecom_trades,
    parse_binancecom_deposits,
    parse_binancecom_withdrawals,
)
from .crud_base import create_transaction_idempotent
from utils.logging_config import get_logger

logger = get_logger(__name__)

def ingest_transactions_from_binancecom_trades(db: Session, trades_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.com API trade data."""
    logger.info("Starting ingestion of trades from Binance.com API data.")
    
    transactions_data = parse_binancecom_trades(trades_data)
    
    success_count, failure_count, skipped_count = 0, 0, 0
    for tx_data in transactions_data:
        try:
            # Check if transaction already exists
            from models import Transaction
            source = tx_data.get('source')
            ext_id = tx_data.get('external_id')
            existing = None
            if source and ext_id:
                existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
            
            tx = create_transaction_idempotent(db, tx_data)
            
            if tx is None:
                # Transaction skipped (e.g., missing FX rate)
                skipped_count += 1
                logger.warning(f"Skipped trade {ext_id} - will retry in next sync")
            elif existing:
                skipped_count += 1
            else:
                success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest trade {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Trade ingestion complete. Success: {success_count}, Skipped: {skipped_count}, Failed: {failure_count}")

def ingest_transactions_from_binancecom_deposits(db: Session, deposits_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.com API deposit data."""
    logger.info("Starting ingestion of deposits from Binance.com API data.")
    
    transactions_data = parse_binancecom_deposits(deposits_data)
    
    success_count, failure_count, skipped_count = 0, 0, 0
    for tx_data in transactions_data:
        try:
            # Check if transaction already exists
            from models import Transaction
            source = tx_data.get('source')
            ext_id = tx_data.get('external_id')
            existing = None
            if source and ext_id:
                existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
            
            tx = create_transaction_idempotent(db, tx_data)
            
            if tx is None:
                # Transaction skipped (e.g., missing FX rate)
                skipped_count += 1
                logger.warning(f"Skipped deposit {ext_id} - will retry in next sync")
            elif existing:
                skipped_count += 1
            else:
                success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest deposit {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Deposit ingestion complete. Success: {success_count}, Skipped: {skipped_count}, Failed: {failure_count}")

def ingest_transactions_from_binancecom_withdrawals(db: Session, withdrawals_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.com API withdrawal data."""
    logger.info("Starting ingestion of withdrawals from Binance.com API data.")
    
    transactions_data = parse_binancecom_withdrawals(withdrawals_data)
    
    success_count, failure_count, skipped_count = 0, 0, 0
    for tx_data in transactions_data:
        try:
            # Check if transaction already exists
            from models import Transaction
            source = tx_data.get('source')
            ext_id = tx_data.get('external_id')
            existing = None
            if source and ext_id:
                existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
            
            tx = create_transaction_idempotent(db, tx_data)
            
            if tx is None:
                # Transaction skipped (e.g., missing FX rate)
                skipped_count += 1
                logger.warning(f"Skipped withdrawal {ext_id} - will retry in next sync")
            elif existing:
                skipped_count += 1
            else:
                success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest withdrawal {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Withdrawal ingestion complete. Success: {success_count}, Skipped: {skipped_count}, Failed: {failure_count}")
