"""
Service layer for crypto transfer detection and linking.

This module handles the orchestration of detecting and linking related crypto transfers
between wallets and exchanges. It follows the three-layer architecture:
- Service Layer: Orchestration, business logic, no direct DB commits
- CRUD Layer: Simple database operations only
- Parser Layer: Data transformation (not needed here)

Functions moved from crud/crud_crypto_transfer_link.py because they:
- Make complex business logic decisions
- Query multiple tables and make comparisons
- Create/update multiple records in one operation
- Manage transaction type conversions
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import CryptoTransferLink, Transaction, CryptoWallet, Account
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import get_global_base_currency
from crud.crud_market_fx import get_latest_price, get_latest_fx_rate
from crud.crud_crypto_transfer_link import create_transfer_link

logger = get_logger(__name__)

# Exchange sources that trigger transfer detection
EXCHANGE_SOURCES = ['binanceth_crypto_sync', 'binancecom', 'IBKR']


def _get_account_name(db: Session, account_id: int) -> str:
    """Get account name by ID, or return 'Unknown' if not found."""
    account = db.query(Account).filter(Account.id == account_id).first()
    return account.name if account else f'Account {account_id}'


def _get_wallet_label(db: Session, wallet_id: int) -> str:
    """Get wallet label by ID, or return 'Unknown' if not found."""
    wallet = db.query(CryptoWallet).filter(CryptoWallet.id == wallet_id).first()
    if wallet:
        return wallet.label or f'{wallet.symbol} wallet'
    return f'Wallet {wallet_id}'


def _populate_price_and_value(db: Session, tx: Transaction) -> bool:
    """
    Populate price and value fields for a transaction using MarketData and FxRate tables.
    
    Uses database tables only (no API calls):
    - MarketData: Get price in source currency (typically USD for crypto)
    - FxRate: Convert to portfolio base currency (from config, e.g., EUR)
    
    Field definitions (matching existing transaction patterns):
    - price: Price per unit in currency_native (e.g., USD per BTC)
    - value_native: Fiat value in currency_native (e.g., USD total)
    - currency_native: Currency of price and value_native (e.g., USD)
    - value_base: Fiat value in portfolio base currency (e.g., EUR)
    - currency_base: Portfolio base currency (e.g., EUR)
    - exchange_rate_to_base: Conversion rate from currency_native to currency_base
    
    IMPORTANT: This function does NOT store incorrect currency values.
    If proper FX conversion cannot be performed, the transaction is left without
    price/value data rather than storing incorrect values.
    
    Returns:
        bool: True if price/value was successfully populated, False otherwise
    """
    if not tx.symbol or not tx.quantity:
        logger.warning(f"Cannot populate price: missing symbol or quantity")
        return False
    
    # Get base currency from config (not hardcoded)
    base_currency = get_global_base_currency()
    
    # Ensure transaction has correct currency_base
    if not tx.currency_base:
        tx.currency_base = base_currency
    
    try:
        # Get price from MarketData table
        market_data = get_latest_price(db, tx.symbol, at_ts=tx.occurred_at)
        if not market_data or not market_data.price:
            logger.warning(f"No market data found for {tx.symbol} at {tx.occurred_at}")
            return False
        
        price_per_unit = Decimal(str(market_data.price))
        price_currency = market_data.currency  # The currency of the price (e.g., 'USD')
        
        # Set price and value_native in the market data currency (usually USD)
        tx.price = price_per_unit
        tx.value_native = tx.quantity * price_per_unit  # Value preserves sign (negative for outflows)
        tx.currency_native = price_currency  # USD (not the crypto symbol!)
        
        # Convert to portfolio base currency
        if price_currency == tx.currency_base:
            # Price is already in base currency - no conversion needed
            tx.value_base = tx.value_native
            tx.exchange_rate_to_base = Decimal('1')
            logger.debug(f"Set price for {tx.symbol}: {tx.price} {price_currency}, value: {tx.value_native} {price_currency}")
            return True
        else:
            # Need FX conversion (e.g., USD → EUR)
            fx_pair = f"{price_currency}/{tx.currency_base}"
            fx_rate = get_latest_fx_rate(db, fx_pair, at_ts=tx.occurred_at)
            
            if fx_rate and fx_rate.rate:
                rate = Decimal(str(fx_rate.rate))
                tx.exchange_rate_to_base = rate
                tx.value_base = tx.value_native * rate  # EUR value
                logger.debug(
                    f"Set price for {tx.symbol}: {tx.price} {price_currency}, "
                    f"value_native: {tx.value_native} {price_currency}, "
                    f"value_base: {tx.value_base} {tx.currency_base} (rate: {rate})"
                )
                return True
            else:
                # NO FALLBACK - We do NOT store incorrect currency values
                # A German tax lawyer would correctly reject any financial record
                # that shows values in wrong currencies. This is not "better than nothing" -
                # it's worse, because it creates incorrect tax documentation.
                logger.error(
                    f"CANNOT populate price for {tx.symbol}: No FX rate found for {fx_pair} at {tx.occurred_at}. "
                    f"Transaction will NOT have price/value data to ensure regulatory compliance. "
                    f"Please ensure FX rates are synced before processing transfers."
                )
                return False
                
    except Exception as e:
        logger.error(f"Error populating price for {tx.symbol}: {e}")
        return False


def auto_detect_internal_transfers(
    db: Session,
    account_id: int,
    lookback_days: int = 30,
    amount_tolerance: float = 0.0001,
    time_window_hours: int = 24
) -> int:
    """
    Auto-detect and link internal transfers within a single account's crypto wallets.
    Detects transfers between wallets of the same account only.
    
    For cross-account detection, use auto_detect_internal_transfers_global().
    
    Args:
        db: Database session
        account_id: Account ID to scan wallets for
        lookback_days: How many days back to scan
        amount_tolerance: Acceptable amount difference (for fees)
        time_window_hours: Time window to match send/receive
    
    Returns:
        int: Number of links created
    """
    cutoff_date = now_utc() - timedelta(days=lookback_days)
    base_currency = get_global_base_currency()
    
    # Get crypto wallets for this specific account
    wallets = db.query(CryptoWallet).filter(
        CryptoWallet.account_id == account_id
    ).all()
    wallet_ids = [w.id for w in wallets]
    
    if len(wallet_ids) < 2:
        logger.info(f"Account {account_id} has less than 2 crypto wallets, skipping intra-account auto-detect")
        return 0
    
    logger.info(f"Auto-detecting internal transfers within account {account_id} ({len(wallet_ids)} wallets)")
    
    # Get unlinked transfer_out transactions (sends) from this account's wallets
    send_txs = db.query(Transaction).filter(
        and_(
            Transaction.from_crypto_wallet_id.in_(wallet_ids),
            Transaction.type == 'transfer_out',
            Transaction.crypto_transfer_link_id.is_(None),
            Transaction.occurred_at >= cutoff_date
        )
    ).order_by(Transaction.occurred_at.desc()).all()
    
    links_created = 0
    
    for send_tx in send_txs:
        # Look for matching receive in another wallet of the SAME account
        time_start = send_tx.occurred_at - timedelta(hours=time_window_hours)
        time_end = send_tx.occurred_at + timedelta(hours=time_window_hours)
        
        # Amount range with tolerance (use qty for crypto amount)
        amount = abs(float(send_tx.quantity)) if send_tx.quantity else 0
        if amount == 0:
            continue
        
        amount_min = Decimal(str(amount * (1 - amount_tolerance)))
        amount_max = Decimal(str(amount * (1 + amount_tolerance)))
        
        # Find potential matches in same account's wallets only
        receive_txs = db.query(Transaction).filter(
            and_(
                Transaction.to_crypto_wallet_id.in_(wallet_ids),
                Transaction.to_crypto_wallet_id != send_tx.from_crypto_wallet_id,  # Different wallet
                Transaction.type == 'transfer_in',
                Transaction.symbol == send_tx.symbol,  # Same symbol
                Transaction.crypto_transfer_link_id.is_(None),  # Not already linked
                Transaction.occurred_at >= time_start,
                Transaction.occurred_at <= time_end,
                Transaction.quantity >= amount_min,
                Transaction.quantity <= amount_max
            )
        ).all()
        
        if receive_txs:
            # Take the best match (closest amount)
            best_match = min(
                receive_txs,
                key=lambda rx: abs(float(rx.quantity) - amount)
            )
            
            # Create link
            try:
                # Get wallet labels for better notes
                from_wallet = _get_wallet_label(db, send_tx.from_crypto_wallet_id)
                to_wallet = _get_wallet_label(db, best_match.to_crypto_wallet_id)
                
                tx_hash = send_tx.external_id or f"internal_{send_tx.id}_{best_match.id}"
                note = f"Intra-account transfer: {from_wallet} → {to_wallet} ({send_tx.symbol})"
                
                link = crud_crypto_transfer_link.create_transfer_link(
                    db=db,
                    tx_hash=tx_hash,
                    source='auto_detected_intra_account',
                    note=note
                )
                
                # Link both transactions
                send_tx.crypto_transfer_link_id = link.id
                best_match.crypto_transfer_link_id = link.id
                db.commit()
                
                links_created += 1
                logger.info(f"Linked internal transfer: TX {send_tx.id} → TX {best_match.id} ({note})")
                
            except Exception as e:
                logger.error(f"Error linking transactions {send_tx.id} and {best_match.id}: {e}")
                db.rollback()
    
    logger.info(f"Auto-detect internal transfers for account {account_id}: created {links_created} links")
    return links_created


def auto_detect_internal_transfers_global(
    db: Session,
    lookback_days: int = 30,
    amount_tolerance: float = 0.0001,
    time_window_hours: int = 24
) -> int:
    """
    Auto-detect and link internal transfers between ANY crypto wallets in the system.
    Detects transfers across account boundaries (e.g., Trezor S1 → Trezor E2).
    
    This is the recommended function for comprehensive internal transfer detection.
    
    Args:
        db: Database session
        lookback_days: How many days back to scan
        amount_tolerance: Acceptable amount difference (for fees)
        time_window_hours: Time window to match send/receive
    
    Returns:
        int: Number of links created
    """
    cutoff_date = now_utc() - timedelta(days=lookback_days)
    base_currency = get_global_base_currency()
    
    # Get ALL crypto wallets in the system (not filtered by account)
    wallets = db.query(CryptoWallet).all()
    wallet_ids = [w.id for w in wallets]
    
    if len(wallet_ids) < 2:
        logger.info(f"System has less than 2 crypto wallets total, skipping auto-detect")
        return 0
    
    logger.info(f"Auto-detecting internal transfers across ALL {len(wallet_ids)} crypto wallets (cross-account detection enabled)")
    
    # Get unlinked transfer_out transactions (sends) from ANY wallet
    send_txs = db.query(Transaction).filter(
        and_(
            Transaction.from_crypto_wallet_id.in_(wallet_ids),
            Transaction.type == 'transfer_out',
            Transaction.crypto_transfer_link_id.is_(None),
            Transaction.occurred_at >= cutoff_date
        )
    ).order_by(Transaction.occurred_at.desc()).all()
    
    links_created = 0
    
    for send_tx in send_txs:
        # Look for matching receive in ANY OTHER wallet (including different accounts)
        time_start = send_tx.occurred_at - timedelta(hours=time_window_hours)
        time_end = send_tx.occurred_at + timedelta(hours=time_window_hours)
        
        # Amount range with tolerance (use qty for crypto amount, not fiat amount)
        amount = abs(float(send_tx.quantity)) if send_tx.quantity else 0
        if amount == 0:
            continue
        
        amount_min = Decimal(str(amount * (1 - amount_tolerance)))
        amount_max = Decimal(str(amount * (1 + amount_tolerance)))
        
        # Find potential matches in ANY wallet (cross-account)
        receive_txs = db.query(Transaction).filter(
            and_(
                Transaction.to_crypto_wallet_id.in_(wallet_ids),
                Transaction.to_crypto_wallet_id != send_tx.from_crypto_wallet_id,  # Different wallet
                Transaction.type == 'transfer_in',
                Transaction.symbol == send_tx.symbol,  # Same symbol
                Transaction.crypto_transfer_link_id.is_(None),  # Not already linked
                Transaction.occurred_at >= time_start,
                Transaction.occurred_at <= time_end,
                Transaction.quantity >= amount_min,
                Transaction.quantity <= amount_max
            )
        ).all()
        
        if receive_txs:
            # Take the best match (closest amount)
            best_match = min(
                receive_txs,
                key=lambda rx: abs(float(rx.quantity) - amount)
            )
            
            # Create link
            try:
                # Get wallet labels for better notes
                from_wallet = _get_wallet_label(db, send_tx.from_crypto_wallet_id)
                to_wallet = _get_wallet_label(db, best_match.to_crypto_wallet_id)
                qty_str = f"{abs(float(send_tx.quantity)):.8f}".rstrip('0').rstrip('.')
                
                link = create_transfer_link(
                    db,
                    tx_hash=send_tx.blockchain_tx_hash or f"internal_{send_tx.id}_{best_match.id}",
                    source='auto_detected',
                    note=f"Internal wallet transfer: {send_tx.symbol} {qty_str} from {from_wallet} to {to_wallet}"
                )
                
                # Link both transactions
                send_tx.crypto_transfer_link_id = link.id
                best_match.crypto_transfer_link_id = link.id
                
                # Convert to portfolio_transfer (lot-neutral) for both sides
                send_tx.type = 'portfolio_transfer'
                best_match.type = 'portfolio_transfer'
                
                # Ensure currency_base is set from config
                send_tx.currency_base = base_currency
                best_match.currency_base = base_currency
                
                # Update notes to be more descriptive
                fee_str = ""
                if send_tx.fee and send_tx.fee > 0:
                    fee_str = f" (network fee: {float(send_tx.fee):.8f} {send_tx.symbol})".rstrip('0').rstrip('.')
                send_tx.notes = f"Portfolio transfer: {qty_str} {send_tx.symbol} from {from_wallet} to {to_wallet}{fee_str}"
                best_match.notes = f"Portfolio transfer: {qty_str} {send_tx.symbol} received at {to_wallet} from {from_wallet}"
                
                # Populate price and value for both transactions
                _populate_price_and_value(db, send_tx)
                _populate_price_and_value(db, best_match)
                
                # Create separate fee transaction if there's a fee on the send side (fees consume lots via FIFO)
                if send_tx.fee and send_tx.fee > 0:
                    fee_qty_str = f"{float(send_tx.fee):.8f}".rstrip('0').rstrip('.')
                    fee_tx = Transaction(
                        portfolio_id=send_tx.portfolio_id,
                        account_id=send_tx.account_id,
                        occurred_at=send_tx.occurred_at,
                        type='fee',
                        symbol=send_tx.symbol,
                        quantity=-send_tx.fee,  # Negative - this amount is consumed from lots
                        value_native=send_tx.fee,
                        currency_native=send_tx.symbol,
                        currency_base=base_currency,
                        source=send_tx.source,
                        category='fee',
                        asset_class='crypto',
                        external_id=f'{send_tx.external_id}_network_fee',
                        notes=f'Network fee: {fee_qty_str} {send_tx.symbol} for transfer from {from_wallet} to {to_wallet}',
                        crypto_transfer_link_id=link.id
                    )
                    db.add(fee_tx)
                    db.flush()  # Get ID for price population
                    _populate_price_and_value(db, fee_tx)
                    logger.info(f"Created fee transaction: -{send_tx.fee} {send_tx.symbol}")
                    # Clear fee from original (now tracked separately)
                    send_tx.fee = Decimal('0')
                
                db.commit()
                
                links_created += 1
                logger.info(
                    f"Linked cross-account internal transfer: {send_tx.id} ({send_tx.quantity} {send_tx.symbol}) "
                    f"from wallet {send_tx.from_crypto_wallet_id} -> {best_match.id} ({best_match.quantity} {best_match.symbol}) "
                    f"to wallet {best_match.to_crypto_wallet_id}"
                )
                
            except Exception as e:
                logger.error(f"Failed to create link for send_tx {send_tx.id}: {e}")
                db.rollback()
    
    logger.info(f"Global auto-detect complete. Created {links_created} transfer links across all accounts")
    return links_created


def auto_detect_exchange_wallet_transfers(
    db: Session,
    lookback_days: int = 30,
    amount_tolerance: float = 0.0001,
    time_window_hours: int = 24
) -> int:
    """
    Auto-detect and link transfers between exchanges (Binance.th, Binance.com) and crypto wallets.
    
    This detects the most common transfer patterns:
    - Exchange withdrawal → Wallet receive (Binance → Trezor)
    - Wallet send → Exchange deposit (Trezor → Binance)
    
    Matches transactions by:
    - Same symbol (e.g., BTC)
    - Similar amount (within tolerance for network fees)
    - Similar time (within time window)
    - Different sources (one from exchange, one from blockchain)
    
    Args:
        db: Database session
        lookback_days: How many days back to scan
        amount_tolerance: Acceptable amount difference (for network fees, typically 0.01%)
        time_window_hours: Time window to match transactions (default 24 hours)
    
    Returns:
        int: Number of links created
    """
    cutoff_date = now_utc() - timedelta(days=lookback_days)
    base_currency = get_global_base_currency()
    links_created = 0
    
    logger.info(f"Auto-detecting exchange ↔ wallet transfers (lookback: {lookback_days} days)")
    
    # ============================================================================
    # Pattern 1: Exchange Withdrawal → Wallet Receive
    # ============================================================================
    
    # Get unlinked withdrawals from exchanges
    exchange_withdrawals = db.query(Transaction).filter(
        and_(
            Transaction.type == 'withdrawal',
            Transaction.source.in_(EXCHANGE_SOURCES),
            Transaction.crypto_transfer_link_id.is_(None),
            Transaction.occurred_at >= cutoff_date,
            Transaction.asset_class == 'crypto'
        )
    ).order_by(Transaction.occurred_at.desc()).all()
    
    logger.info(f"Found {len(exchange_withdrawals)} unlinked exchange withdrawals")
    
    for withdrawal in exchange_withdrawals:
        # Skip if no amount
        amount = abs(float(withdrawal.quantity)) if withdrawal.quantity else 0
        if amount == 0:
            continue
        
        # Calculate amount range with tolerance
        amount_min = Decimal(str(amount * (1 - amount_tolerance)))
        amount_max = Decimal(str(amount * (1 + amount_tolerance)))
        
        # Time window
        time_start = withdrawal.occurred_at - timedelta(hours=time_window_hours)
        time_end = withdrawal.occurred_at + timedelta(hours=time_window_hours)
        
        # Find matching receives in crypto wallets
        matching_receives = db.query(Transaction).filter(
            and_(
                Transaction.type == 'transfer_in',
                Transaction.to_crypto_wallet_id.isnot(None),  # Has wallet
                Transaction.symbol == withdrawal.symbol,  # Same symbol
                Transaction.crypto_transfer_link_id.is_(None),  # Not already linked
                Transaction.occurred_at >= time_start,
                Transaction.occurred_at <= time_end,
                Transaction.quantity >= amount_min,
                Transaction.quantity <= amount_max
            )
        ).all()
        
        if matching_receives:
            # Take the best match (closest amount and time)
            best_match = min(
                matching_receives,
                key=lambda rx: (
                    abs(float(rx.quantity) - amount),  # Amount difference
                    abs((rx.occurred_at - withdrawal.occurred_at).total_seconds())  # Time difference
                )
            )
            
            # Create link
            try:
                # Get account and wallet names for better notes
                from_account = _get_account_name(db, withdrawal.account_id)
                to_wallet = _get_wallet_label(db, best_match.to_crypto_wallet_id)
                qty_str = f"{abs(float(withdrawal.quantity)):.8f}".rstrip('0').rstrip('.')
                
                link = create_transfer_link(
                    db,
                    tx_hash=best_match.blockchain_tx_hash or f"exchange_wallet_{withdrawal.id}_{best_match.id}",
                    source='auto_detected',
                    note=f"Exchange → Wallet: {withdrawal.symbol} {qty_str} from {from_account} to {to_wallet}"
                )
                
                # Link both transactions
                withdrawal.crypto_transfer_link_id = link.id
                best_match.crypto_transfer_link_id = link.id
                
                # Convert to portfolio_transfer (lot-neutral) for both sides
                withdrawal.type = 'portfolio_transfer'
                best_match.type = 'portfolio_transfer'
                
                # Ensure currency_base is set from config
                withdrawal.currency_base = base_currency
                best_match.currency_base = base_currency
                
                # Update notes to be more descriptive
                fee_str = ""
                if withdrawal.fee and withdrawal.fee > 0:
                    fee_str = f" (network fee: {float(withdrawal.fee):.8f} {withdrawal.symbol})".rstrip('0').rstrip('.')
                withdrawal.notes = f"Portfolio transfer: {qty_str} {withdrawal.symbol} from {from_account} to {to_wallet}{fee_str}"
                best_match.notes = f"Portfolio transfer: {qty_str} {withdrawal.symbol} received at {to_wallet} from {from_account}"
                
                # Populate price and value for both transactions
                _populate_price_and_value(db, withdrawal)
                _populate_price_and_value(db, best_match)
                
                # Create separate fee transaction if there's a fee (fees consume lots via FIFO)
                if withdrawal.fee and withdrawal.fee > 0:
                    fee_qty_str = f"{float(withdrawal.fee):.8f}".rstrip('0').rstrip('.')
                    fee_tx = Transaction(
                        portfolio_id=withdrawal.portfolio_id,
                        account_id=withdrawal.account_id,
                        occurred_at=withdrawal.occurred_at,
                        type='fee',
                        symbol=withdrawal.symbol,
                        quantity=-withdrawal.fee,  # Negative - this amount is consumed from lots
                        value_native=withdrawal.fee,
                        currency_native=withdrawal.symbol,
                        currency_base=base_currency,
                        source=withdrawal.source,
                        category='fee',
                        asset_class='crypto',
                        external_id=f'{withdrawal.external_id}_network_fee',
                        notes=f'Network fee: {fee_qty_str} {withdrawal.symbol} for transfer from {from_account} to {to_wallet}',
                        crypto_transfer_link_id=link.id
                    )
                    db.add(fee_tx)
                    db.flush()  # Get ID for price population
                    _populate_price_and_value(db, fee_tx)
                    logger.info(f"Created fee transaction: -{withdrawal.fee} {withdrawal.symbol}")
                    # Clear fee from original (now tracked separately)
                    withdrawal.fee = Decimal('0')
                
                db.commit()
                
                links_created += 1
                logger.info(
                    f"Linked exchange withdrawal → wallet: {withdrawal.id} ({withdrawal.quantity} {withdrawal.symbol} "
                    f"from {withdrawal.source}) -> {best_match.id} (wallet {best_match.to_crypto_wallet_id})"
                )
                
            except Exception as e:
                logger.error(f"Failed to create link for withdrawal {withdrawal.id}: {e}")
                db.rollback()
    
    # ============================================================================
    # Pattern 2: Wallet Send → Exchange Deposit
    # ============================================================================
    
    # Get unlinked sends from crypto wallets
    wallet_sends = db.query(Transaction).filter(
        and_(
            Transaction.type == 'transfer_out',
            Transaction.from_crypto_wallet_id.isnot(None),  # Has wallet
            Transaction.crypto_transfer_link_id.is_(None),
            Transaction.occurred_at >= cutoff_date
        )
    ).order_by(Transaction.occurred_at.desc()).all()
    
    logger.info(f"Found {len(wallet_sends)} unlinked wallet sends")
    
    for send_tx in wallet_sends:
        # Skip if no amount
        amount = abs(float(send_tx.quantity)) if send_tx.quantity else 0
        if amount == 0:
            continue
        
        # Calculate amount range with tolerance
        amount_min = Decimal(str(amount * (1 - amount_tolerance)))
        amount_max = Decimal(str(amount * (1 + amount_tolerance)))
        
        # Time window
        time_start = send_tx.occurred_at - timedelta(hours=time_window_hours)
        time_end = send_tx.occurred_at + timedelta(hours=time_window_hours)
        
        # Find matching deposits in exchanges
        matching_deposits = db.query(Transaction).filter(
            and_(
                Transaction.type == 'deposit',
                Transaction.source.in_(EXCHANGE_SOURCES),
                Transaction.symbol == send_tx.symbol,  # Same symbol
                Transaction.crypto_transfer_link_id.is_(None),  # Not already linked
                Transaction.occurred_at >= time_start,
                Transaction.occurred_at <= time_end,
                Transaction.quantity >= amount_min,
                Transaction.quantity <= amount_max,
                Transaction.asset_class == 'crypto'
            )
        ).all()
        
        if matching_deposits:
            # Take the best match (closest amount and time)
            best_match = min(
                matching_deposits,
                key=lambda dx: (
                    abs(float(dx.quantity) - amount),  # Amount difference
                    abs((dx.occurred_at - send_tx.occurred_at).total_seconds())  # Time difference
                )
            )
            
            # Create link
            try:
                # Get wallet and account names for better notes
                from_wallet = _get_wallet_label(db, send_tx.from_crypto_wallet_id)
                to_account = _get_account_name(db, best_match.account_id)
                qty_str = f"{abs(float(send_tx.quantity)):.8f}".rstrip('0').rstrip('.')
                
                link = create_transfer_link(
                    db,
                    tx_hash=send_tx.blockchain_tx_hash or f"wallet_exchange_{send_tx.id}_{best_match.id}",
                    source='auto_detected',
                    note=f"Wallet → Exchange: {send_tx.symbol} {qty_str} from {from_wallet} to {to_account}"
                )
                
                # Link both transactions
                send_tx.crypto_transfer_link_id = link.id
                best_match.crypto_transfer_link_id = link.id
                
                # Convert to portfolio_transfer (lot-neutral) for both sides
                send_tx.type = 'portfolio_transfer'
                best_match.type = 'portfolio_transfer'
                
                # Ensure currency_base is set from config
                send_tx.currency_base = base_currency
                best_match.currency_base = base_currency
                
                # Update notes to be more descriptive
                fee_str = ""
                if send_tx.fee and send_tx.fee > 0:
                    fee_str = f" (network fee: {float(send_tx.fee):.8f} {send_tx.symbol})".rstrip('0').rstrip('.')
                send_tx.notes = f"Portfolio transfer: {qty_str} {send_tx.symbol} from {from_wallet} to {to_account}{fee_str}"
                best_match.notes = f"Portfolio transfer: {qty_str} {send_tx.symbol} received at {to_account} from {from_wallet}"
                
                # Populate price and value for both transactions
                _populate_price_and_value(db, send_tx)
                _populate_price_and_value(db, best_match)
                
                # Create separate fee transaction if there's a fee on the send side (fees consume lots via FIFO)
                if send_tx.fee and send_tx.fee > 0:
                    fee_qty_str = f"{float(send_tx.fee):.8f}".rstrip('0').rstrip('.')
                    fee_tx = Transaction(
                        portfolio_id=send_tx.portfolio_id,
                        account_id=send_tx.account_id,
                        occurred_at=send_tx.occurred_at,
                        type='fee',
                        symbol=send_tx.symbol,
                        quantity=-send_tx.fee,  # Negative - this amount is consumed from lots
                        value_native=send_tx.fee,
                        currency_native=send_tx.symbol,
                        currency_base=base_currency,
                        source=send_tx.source,
                        category='fee',
                        asset_class='crypto',
                        external_id=f'{send_tx.external_id}_network_fee',
                        notes=f'Network fee: {fee_qty_str} {send_tx.symbol} for transfer from {from_wallet} to {to_account}',
                        crypto_transfer_link_id=link.id
                    )
                    db.add(fee_tx)
                    db.flush()  # Get ID for price population
                    _populate_price_and_value(db, fee_tx)
                    logger.info(f"Created fee transaction: -{send_tx.fee} {send_tx.symbol}")
                    # Clear fee from original (now tracked separately)
                    send_tx.fee = Decimal('0')
                
                db.commit()
                
                links_created += 1
                logger.info(
                    f"Linked wallet → exchange deposit: {send_tx.id} (wallet {send_tx.from_crypto_wallet_id}) -> "
                    f"{best_match.id} ({best_match.quantity} {best_match.symbol} to {best_match.source})"
                )
                
            except Exception as e:
                logger.error(f"Failed to create link for send_tx {send_tx.id}: {e}")
                db.rollback()
    
    logger.info(f"Exchange ↔ Wallet detection complete. Created {links_created} transfer links")
    return links_created


def auto_detect_all_transfers(
    db: Session,
    lookback_days: int = 30,
    amount_tolerance: float = 0.0001,
    time_window_hours: int = 24
) -> Dict[str, int]:
    """
    Run all transfer detection patterns.
    
    Convenience function that runs:
    1. Exchange ↔ Wallet transfers
    2. Internal wallet transfers (cross-account)
    
    Args:
        db: Database session
        lookback_days: How many days back to scan
        amount_tolerance: Acceptable amount difference
        time_window_hours: Time window to match transactions
    
    Returns:
        dict: Summary of links created by pattern
    """
    logger.info(f"Running comprehensive transfer detection (lookback: {lookback_days} days)")
    
    results = {
        'exchange_wallet': 0,
        'internal_wallet': 0,
        'total': 0
    }
    
    # Pattern 1: Exchange ↔ Wallet
    results['exchange_wallet'] = auto_detect_exchange_wallet_transfers(
        db, lookback_days, amount_tolerance, time_window_hours
    )
    
    # Pattern 2: Internal wallet transfers
    results['internal_wallet'] = auto_detect_internal_transfers_global(
        db, lookback_days, amount_tolerance, time_window_hours
    )
    
    results['total'] = results['exchange_wallet'] + results['internal_wallet']
    
    logger.info(
        f"Transfer detection complete. Exchange↔Wallet: {results['exchange_wallet']}, "
        f"Internal: {results['internal_wallet']}, Total: {results['total']}"
    )
    
    return results
