from sqlalchemy.orm import Session
from .parsers.binanceth_parser import (
    parse_binanceth_trades,
    parse_binanceth_deposits,
    parse_binanceth_withdrawals,
)
from .crud_base import create_transaction_idempotent
from utils.logging_config import get_logger

logger = get_logger(__name__)

def ingest_transactions_from_binanceth_trades(db: Session, trades_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.th API trade data."""
    logger.info("Starting ingestion of trades from Binance.th API data.")
    
    transactions_data = parse_binanceth_trades(trades_data)
    
    success_count, failure_count = 0, 0
    for tx_data in transactions_data:
        try:
            create_transaction_idempotent(db, tx_data)
            success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest trade {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Trade ingestion complete. Success: {success_count}, Failed: {failure_count}")

def ingest_transactions_from_binanceth_deposits(db: Session, deposits_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.th API deposit data."""
    logger.info("Starting ingestion of deposits from Binance.th API data.")
    
    transactions_data = parse_binanceth_deposits(deposits_data)
    
    success_count, failure_count = 0, 0
    for tx_data in transactions_data:
        try:
            create_transaction_idempotent(db, tx_data)
            success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest deposit {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Deposit ingestion complete. Success: {success_count}, Failed: {failure_count}")

def ingest_transactions_from_binanceth_withdrawals(db: Session, withdrawals_data: list[dict]):
    """High-level function to ingest transactions from raw Binance.th API withdrawal data."""
    logger.info("Starting ingestion of withdrawals from Binance.th API data.")
    
    transactions_data = parse_binanceth_withdrawals(withdrawals_data)
    
    success_count, failure_count = 0, 0
    for tx_data in transactions_data:
        try:
            create_transaction_idempotent(db, tx_data)
            success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest withdrawal {tx_data.get('external_id')}: {e}", exc_info=True)
            
    logger.info(f"Withdrawal ingestion complete. Success: {success_count}, Failed: {failure_count}")
