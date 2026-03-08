from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from typing import Optional
from decimal import Decimal

from models import Transaction
from utils.logging_config import get_logger

logger = get_logger(__name__)

# Import lot functions for incremental lot management
try:
    from crud.crud_lot import create_lot_from_transaction, allocate_sale_to_lots
    LOT_MANAGEMENT_ENABLED = True
except ImportError:
    LOT_MANAGEMENT_ENABLED = False
    logger.warning("Lot management functions not available - incremental lot creation disabled")


def normalize_transaction_type(tx_data: dict, db: Optional[Session] = None) -> str:
    """
    Auto-detect internal portfolio transfers to avoid creating duplicate lots.
    
    Primary use case: Exchange → Hardware Wallet transfers
    - Example: Binance (account 3) → Ledger (account 7) both in same portfolio
    - Should use 'portfolio_transfer' instead of 'transfer_in'/'transfer_out'
    
    Detection Rules (in order of importance):
    1. If crypto_transfer_link_id is set → check if same portfolio → portfolio_transfer
       (Handles ongoing exchange↔wallet transfers)
    2. If source is 'migration_finalization' → portfolio_transfer
       (Legacy safety net for historical data)
    
    Args:
        tx_data: Transaction data dict
        db: Database session (optional, required for Rule 1)
        
    Returns:
        str: Normalized transaction type
    """
    original_type = tx_data.get('type')
    
    # Rule 1: Check crypto_transfer_link_id (PRIMARY - handles exchange↔wallet transfers)
    crypto_transfer_link_id = tx_data.get('crypto_transfer_link_id')
    if crypto_transfer_link_id and db and original_type in ['transfer_in', 'transfer_out']:
        # Find the linked transaction
        linked_tx = db.query(Transaction).filter(
            Transaction.crypto_transfer_link_id == crypto_transfer_link_id,
            Transaction.id != tx_data.get('id')  # Exclude self if updating
        ).first()
        
        if linked_tx:
            current_portfolio = tx_data.get('portfolio_id')
            linked_portfolio = getattr(linked_tx, 'portfolio_id', None)
            
            if current_portfolio is not None and linked_portfolio is not None:
                if int(current_portfolio) == int(linked_portfolio):
                    logger.info(
                        f"Auto-correcting linked transfer to 'portfolio_transfer' "
                        f"(same portfolio: {current_portfolio}, link_id: {crypto_transfer_link_id}, symbol: {tx_data.get('symbol')})"
                    )
                    return 'portfolio_transfer'
    
    # Rule 2: Legacy migration safety (unlikely to trigger in future)
    source = tx_data.get('source')
    if source == 'migration_finalization' and original_type in ['transfer_in', 'transfer_out']:
        logger.info(
            f"Auto-correcting migration_finalization to 'portfolio_transfer' "
            f"(symbol: {tx_data.get('symbol')})"
        )
        return 'portfolio_transfer'
    
    # Default: keep original type
    return str(original_type) if original_type else 'buy'


def create_transaction_idempotent(db: Session, tx_data: dict) -> Optional[Transaction]:
    """
    Creates a transaction if it doesn't already exist based on source and external_id.
    
    Returns None if transaction should be skipped (e.g., missing FX rate).
    
    NEW (Hybrid Approach): Also creates/allocates lots incrementally:
    - For BUY transactions: Creates a new lot
    - For SELL transactions: Allocates to existing lots using FIFO
    
    This provides real-time lot tracking between weekly full reconciliations.
    
    IMPORTANT: Auto-detects internal portfolio transfers and normalizes them to
    'portfolio_transfer' type to avoid creating duplicate lots.
    
    Args:
        db: Database session
        tx_data: Transaction data dict
    
    Returns:
        Transaction: Created or existing transaction
    """
    # VALIDATION: Ensure currency_base is provided (Phase 3 requirement)
    if 'currency_base' not in tx_data or tx_data['currency_base'] is None:
        raise ValueError(
            f"Transaction missing required 'currency_base' field. "
            f"Source: {tx_data.get('source')}, External ID: {tx_data.get('external_id')}, "
            f"Symbol: {tx_data.get('symbol')}, Type: {tx_data.get('type')}"
        )
    
    # Auto-normalize transaction type (detect internal portfolio transfers)
    tx_data['type'] = normalize_transaction_type(tx_data, db)
    
    # Handle missing exchange rate (marked as 0.0 or None by parser)
    if tx_data.get('exchange_rate_to_base') in (Decimal('0.0'), None):
        currency_native = tx_data.get('currency_native')
        currency_base = tx_data.get('currency_base')
        occurred_at = tx_data.get('occurred_at')
        
        if currency_native and currency_base and currency_native != currency_base and occurred_at:
            # Try to fetch FX rate from database
            from crud.crud_market_fx import get_latest_fx_rate
            pair = f"{currency_native}/{currency_base}"
            fx_rate_obj = get_latest_fx_rate(db, pair, at_ts=occurred_at)
            
            if fx_rate_obj:
                fx_rate = Decimal(str(fx_rate_obj.rate))  # Extract rate from FxRate object
                tx_data['exchange_rate_to_base'] = fx_rate
                tx_data['value_base'] = tx_data.get('value_native', Decimal('0')) * fx_rate
                
                # Add fallback note if using ecb_fallback source
                if hasattr(fx_rate_obj, 'source') and fx_rate_obj.source == 'ecb_fallback':
                    days_diff = (occurred_at.date() - fx_rate_obj.as_of_date.date()).days
                    fallback_note = f"FX rate fallback: used {fx_rate_obj.as_of_date.date()} rate ({days_diff} days old)"
                    
                    if tx_data.get('notes'):
                        tx_data['notes'] += f"; {fallback_note}"
                    else:
                        tx_data['notes'] = fallback_note
                    
                    logger.info(f"Fetched FX rate for {pair} on {occurred_at.date()}: {fx_rate} (fallback from {fx_rate_obj.as_of_date.date()})")
                else:
                    logger.info(f"Fetched FX rate for {pair} on {occurred_at.date()}: {fx_rate}")
            else:
                # CRITICAL: No FX rate available - skip transaction
                # It will be retried in next sync (7-day lookback window)
                logger.error(
                    f"❌ SKIPPING TRANSACTION: No FX rate found for {pair} on {occurred_at.date()}. "
                    f"Transaction will be retried in next sync. "
                    f"Source: {tx_data.get('source')}, External ID: {tx_data.get('external_id')}, "
                    f"Symbol: {tx_data.get('symbol')}"
                )
                return None  # Skip this transaction
        else:
            # Same currency or missing data - default to 1.0
            tx_data['exchange_rate_to_base'] = Decimal('1.0')
            tx_data['value_base'] = tx_data.get('value_native', Decimal('0'))
    
    source = tx_data.get('source')
    ext_id = tx_data.get('external_id')
    is_new_transaction = False
    
    if source and ext_id:
        existing = db.query(Transaction).filter_by(source=source, external_id=ext_id).one_or_none()
        if existing:
            logger.debug(f"Skipping existing transaction from source '{source}' with external_id '{ext_id}'")
            return existing
    
    tx = Transaction(**tx_data)
    db.add(tx)
    try:
        db.commit()
        is_new_transaction = True
    except IntegrityError:
        db.rollback()
        if source and ext_id:
            logger.warning(f"IntegrityError on commit, rolling back. Finding existing tx for {source}/{ext_id}")
            return db.query(Transaction).filter_by(source=source, external_id=ext_id).one()
        raise
    db.refresh(tx)
    
    # NEW: Incremental lot management (only for new transactions)
    if is_new_transaction and LOT_MANAGEMENT_ENABLED:
        try:
            if tx.type in ['buy', 'transfer_in', 'deposit', 'staking_reward', 'interest', 'dividend']:
                # Create lot for income/acquisition transactions
                # staking_reward/interest/dividend are treated like buys (acquire new assets)
                lot = create_lot_from_transaction(db, tx)
                if lot:
                    logger.debug(f"Created lot {lot.lot_id} for transaction {tx.id} ({tx.symbol}, type={tx.type})")
                else:
                    logger.debug(f"No lot created for transaction {tx.id} (may not be applicable)")
            
            elif tx.type in ['sell', 'transfer_out', 'withdrawal', 'fee', 'staking_loss']:
                # Allocate to existing lots using FIFO
                # fee/staking_loss treated like sell (reduces position)
                allocations, realized_gain = allocate_sale_to_lots(db, tx)
                if allocations:
                    logger.debug(
                        f"Allocated {tx.type} transaction {tx.id} ({tx.symbol}): "
                        f"{len(allocations)} lots, realized gain: {realized_gain}"
                    )
                else:
                    logger.debug(f"No lot allocation for transaction {tx.id} (may have no open lots)")
        
        except Exception as e:
            # Don't fail transaction creation if lot management fails
            # Weekly reconciliation will fix any issues
            logger.warning(
                f"Failed to create/allocate lot for transaction {tx.id}: {e}. "
                f"Will be corrected during weekly reconciliation.",
                exc_info=True
            )
    
    # CRITICAL FIX: Update position for all relevant transaction types
    # This ensures positions reflect ALL transactions, including dividend_reinvest
    if is_new_transaction:
        try:
            from crud.crud_position import update_position_from_transaction
            result = update_position_from_transaction(db, tx)
            if result:
                logger.debug(f"Updated position for transaction {tx.id} ({tx.symbol}, type={tx.type})")
        except Exception as e:
            # Log but don't fail - position recreation will fix it
            logger.warning(
                f"Failed to update position for transaction {tx.id}: {e}. "
                f"Will be corrected during position recreation.",
                exc_info=True
            )
    
    return tx

def sum_qty_by_wallet_symbol(db, wallet_id, symbol, up_to_ts=None):
    """Calculates the sum of 'quantity' for a given symbol in a wallet."""
    q = db.query(func.coalesce(func.sum(Transaction.quantity), 0)).filter(
        Transaction.from_crypto_wallet_id == wallet_id,
        Transaction.symbol_normalized == symbol
    )
    if up_to_ts:
        q = q.filter(Transaction.occurred_at <= up_to_ts)
    return q.scalar() or 0
