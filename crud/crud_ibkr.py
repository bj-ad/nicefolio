from sqlalchemy.orm import Session
from .parsers.ibkr_parser import parse_ibkr_flex_transactions
from .crud_base import create_transaction_idempotent
from utils.logging_config import get_logger

logger = get_logger(__name__)


def ingest_transactions_list(db: Session, transactions_data: list):
    """
    CRUD layer function: Ingest a list of transaction dicts into the database.
    
    This is a pure database operation function - no parsing, no orchestration.
    Just takes transaction data and creates database records.
    
    Args:
        db: Database session
        transactions_data: List of transaction dicts ready for database insertion
        
    Returns:
        tuple: (success_count, skipped_count, failure_count)
    """
    success_count, failure_count, skipped_count = 0, 0, 0
    
    for tx_data in transactions_data:
        try:
            # Track if transaction existed before
            source = tx_data.get('source')
            ext_id = tx_data.get('external_id')
            existing = None
            if source and ext_id:
                from models import Transaction
                existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
            
            tx = create_transaction_idempotent(db, tx_data)
            
            if tx is None:
                # Transaction skipped (e.g., missing FX rate)
                skipped_count += 1
                logger.warning(f"Skipped IBKR transaction {ext_id} - will retry in next sync")
            elif existing:
                skipped_count += 1
            else:
                success_count += 1
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest transaction {tx_data.get('external_id')}: {e}", exc_info=True)
    
    logger.info(f"IBKR ingestion complete. Success: {success_count}, Skipped: {skipped_count}, Failed: {failure_count}")
    return success_count, skipped_count, failure_count


def ingest_transactions_from_ibkr(db: Session, xml_content: bytes):
    """
    LEGACY FUNCTION - Kept for backward compatibility.
    
    High-level function to ingest transactions from raw IBKR Flex XML content.
    
    NOTE: New code should use the service layer (service/ibkr_service.py) instead.
    This function mixes parser and CRUD concerns, which violates the three-layer pattern.
    
    This function:
    1. Parses trades and cash transactions from Flex Query
    2. Syncs FX cash positions to Portfolio 8 (Broker Cash Pool)
    3. Creates transactions with proper deduplication
    """
    logger.info("Starting ingestion of transactions from IBKR Flex data.")
    
    # Parse regular transactions (trades, dividends, etc.)
    # Cash flows are tracked via transfer_in/transfer_out transactions
    # which are generated automatically when processing stock trades
    transactions_data = parse_ibkr_flex_transactions(xml_content)
    
    # Ingest all transactions
    return ingest_transactions_list(db, transactions_data)
