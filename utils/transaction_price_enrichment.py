"""
Transaction Price Enrichment Utility

Enriches transactions with price data when not provided by source APIs.
Used for blockchain transactions (staking rewards, fees, transfers) where
blockchain APIs provide quantities but not USD values.

This handles:
1. Fetching historical prices for a transaction's symbol at occurred_at timestamp
2. Calculating value_native (price * qty) in transaction currency
3. Calculating value_base with FX conversion to portfolio base currency
4. Updating transaction records with proper price/value/exchange_rate data

Used by:
- crypto_wallet_service.py (enriching blockchain transactions)
- weekly_jobs.py (enriching BNB staking rewards)
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from models import Transaction, MarketData, FxRate
from crud.crud_market_fx import get_latest_price, get_latest_fx_rate
from service.marketdata_service import (
    fetch_crypto_prices_from_coinmarketcap,
    sync_crypto_prices
)
from utils.logging_config import get_logger
from database import SessionLocal

logger = get_logger(__name__)


def fetch_price_for_transaction(
    db: Session, 
    symbol: str, 
    occurred_at: datetime,
    currency: str = 'USD'
) -> Optional[Decimal]:
    """
    Fetch price for a symbol at a specific timestamp.
    
    Strategy:
    1. Try database (historical data)
    2. If within last 30 days, sync current prices and use those
    3. Return None if unavailable
    
    Args:
        db: Database session
        symbol: Asset symbol (BTC, ETH, ADA, etc.)
        occurred_at: Timestamp when transaction occurred
        currency: Price currency (default USD)
        
    Returns:
        Decimal: Price or None if unavailable
    """
    try:
        # 1. Try database first (historical data)
        market_data = get_latest_price(db, symbol, at_ts=occurred_at)
        
        if market_data:
            logger.debug(f"Found historical price for {symbol} at {occurred_at}: {market_data.price}")
            return Decimal(str(market_data.price))
        
        # 2. If transaction is recent (within 30 days), sync current prices
        days_ago = (datetime.now() - occurred_at).days
        if days_ago <= 30:
            logger.info(f"No historical price for {symbol}, syncing current prices (transaction was {days_ago} days ago)")
            sync_crypto_prices()  # This will populate market_data table
            
            # Try again after sync
            market_data = get_latest_price(db, symbol, at_ts=datetime.now())
            if market_data:
                logger.info(f"Found price for {symbol} after sync: {market_data.price}")
                return Decimal(str(market_data.price))
        
        # 3. No price available
        logger.warning(f"No price available for {symbol} at {occurred_at} (currency: {currency})")
        return None
        
    except Exception as e:
        logger.error(f"Error fetching price for {symbol} at {occurred_at}: {e}", exc_info=True)
        return None


def populate_transaction_price(
    db: Session,
    transaction: Transaction,
    base_currency: str = 'USD'
) -> bool:
    """
    Populate price, value_native, value_base, and exchange_rate_to_base for a transaction.
    
    Calculates:
    - price: Unit price in currency_native (e.g. USD)
    - value_native: Total fiat value (price * qty) in currency_native
    - value_base: Total fiat value in currency_base (portfolio base currency)
    - exchange_rate_to_base: Conversion rate from currency_native to currency_base
    - currency_base: Set to base_currency parameter
    
    Args:
        db: Database session
        transaction: Transaction object to populate
        base_currency: Portfolio base currency (default USD)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Skip if already populated
        if transaction.price is not None and transaction.value_native is not None:
            logger.debug(f"Transaction {transaction.id} already has price data, skipping")
            return True
        
        # Set currency if missing (blockchain sync doesn't set it for staking rewards)
        tx_currency = transaction.currency_native or 'USD'
        if transaction.currency_native is None:
            logger.info(f"Transaction {transaction.id} has no currency_native, defaulting to USD")
            transaction.currency_native = 'USD'
            tx_currency = 'USD'
        
        # Get price for symbol
        price = fetch_price_for_transaction(
            db, 
            transaction.symbol, 
            transaction.occurred_at,
            tx_currency
        )
        
        if price is None:
            logger.warning(f"Cannot populate price for transaction {transaction.id} ({transaction.symbol})")
            return False
        
        # Calculate amount (price * quantity) - preserves sign for outflows
        qty = Decimal(str(transaction.quantity))
        amount = price * qty
        
        # Get exchange rate to base currency
        tx_currency = transaction.currency_native or 'USD'
        if tx_currency == base_currency:
            exchange_rate = Decimal('1.0')
        else:
            # Get FX rate at transaction time
            pair = f"{tx_currency}/{base_currency}"
            fx_rate = get_latest_fx_rate(db, pair, at_ts=transaction.occurred_at)
            if fx_rate:
                exchange_rate = Decimal(str(fx_rate.rate))
            else:
                logger.warning(f"No FX rate for {pair}, using 1.0")
                exchange_rate = Decimal('1.0')
        
        # Calculate value in base currency
        value_base = amount * exchange_rate
        
        # Update transaction with CORRECT field names (see models.py Transaction)
        transaction.price = price
        transaction.value_native = amount           # fiat value in currency_native
        transaction.value_base = value_base         # fiat value in currency_base
        transaction.exchange_rate_to_base = exchange_rate
        transaction.currency_base = base_currency   # portfolio base currency
        
        db.commit()
        
        logger.info(
            f"Populated transaction {transaction.id} ({transaction.symbol}): "
            f"price={price}, value_native={amount} {transaction.currency_native}, "
            f"value_base={value_base} {base_currency}"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Error populating price for transaction {transaction.id}: {e}", exc_info=True)
        db.rollback()
        return False


def backfill_staking_rewards(base_currency: str = 'USD') -> tuple[int, int]:
    """
    Backfill prices for all staking_reward transactions missing price data.
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    db = SessionLocal()
    success = 0
    failed = 0
    
    try:
        # Find all staking_reward transactions without prices
        transactions = db.query(Transaction).filter(
            Transaction.type == 'staking_reward',
            Transaction.price == None
        ).all()
        
        logger.info(f"Found {len(transactions)} staking_reward transactions to backfill")
        
        for tx in transactions:
            if populate_transaction_price(db, tx, base_currency):
                success += 1
            else:
                failed += 1
        
        logger.info(f"Backfill complete: {success} succeeded, {failed} failed")
        return success, failed
        
    except Exception as e:
        logger.error(f"Error during backfill: {e}", exc_info=True)
        return success, failed
    finally:
        db.close()


def backfill_transaction_type(transaction_type: str, base_currency: str = 'USD') -> tuple[int, int]:
    """
    Backfill prices for all transactions of a specific type missing price data.
    
    Args:
        transaction_type: Transaction type to backfill (fee, staking_reward, interest, etc.)
        base_currency: Portfolio base currency
        
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    db = SessionLocal()
    success = 0
    failed = 0
    
    try:
        transactions = db.query(Transaction).filter(
            Transaction.type == transaction_type,
            Transaction.price == None
        ).all()
        
        logger.info(f"Found {len(transactions)} {transaction_type} transactions to backfill")
        
        for tx in transactions:
            if populate_transaction_price(db, tx, base_currency):
                success += 1
            else:
                failed += 1
        
        logger.info(f"Backfill complete for {transaction_type}: {success} succeeded, {failed} failed")
        return success, failed
        
    except Exception as e:
        logger.error(f"Error during {transaction_type} backfill: {e}", exc_info=True)
        return success, failed
    finally:
        db.close()


if __name__ == "__main__":
    """Run backfill for staking rewards."""
    import sys
    
    print("=" * 80)
    print("BACKFILL PRICES FOR STAKING REWARDS")
    print("=" * 80)
    
    # Check for transaction type argument
    if len(sys.argv) > 1:
        tx_type = sys.argv[1]
        print(f"\nBackfilling {tx_type} transactions...\n")
        success, failed = backfill_transaction_type(tx_type)
    else:
        print("\nBackfilling staking_reward transactions...\n")
        success, failed = backfill_staking_rewards()
    
    print("\n" + "=" * 80)
    print(f"BACKFILL COMPLETE: {success} succeeded, {failed} failed")
    print("=" * 80)
