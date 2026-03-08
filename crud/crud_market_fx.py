from models import MarketData, FxRate, Position
from datetime import datetime, timezone
from sqlalchemy import select, func
from datetime import datetime
from utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================================
# NEW FUNCTIONS - Following binancecom/ibkr CRUD pattern
# ============================================================================

def ingest_market_prices(db, prices_data: list[dict]) -> tuple[int, int]:
    """
    High-level function to ingest market price data.
    Follows pattern from crud_binancecom.py and crud_ibkr.py
    
    Args:
        db: Database session
        prices_data: List of price dictionaries from parser with keys:
            - symbol, price, currency, source, ts (optional)
    
    Returns:
        tuple: (success_count, failure_count)
    """
    logger.info(f"Starting ingestion of {len(prices_data)} market prices")
    
    ts = func.now()
    success_count, failure_count = 0, 0
    
    for price_data in prices_data:
        try:
            # Add timestamp if not present
            if 'ts' not in price_data:
                price_data['ts'] = ts
            
            # Upsert with existing function
            upsert_market_data(
                db,
                symbol=price_data['symbol'],
                ts=price_data['ts'],
                price=price_data['price'],
                currency=price_data['currency'],
                source=price_data['source']
            )
            success_count += 1
            
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest price for {price_data.get('symbol')}: {e}")
    
    logger.info(f"Price ingestion complete. Success: {success_count}, Failed: {failure_count}")
    return success_count, failure_count


def ingest_fx_rates(db, fx_data: list[dict]) -> tuple[int, int]:
    """
    High-level function to ingest FX rate data.
    Follows pattern from crud_binancecom.py and crud_ibkr.py
    
    Args:
        db: Database session
        fx_data: List of FX rate dictionaries from parser with keys:
            - pair, rate, base_currency, quote_currency, source, ts (optional)
    
    Returns:
        tuple: (success_count, failure_count)
    """
    logger.info(f"Starting ingestion of {len(fx_data)} FX rates")
    
    ts = func.now()
    success_count, failure_count = 0, 0
    
    for rate_data in fx_data:
        try:
            if 'ts' not in rate_data:
                rate_data['ts'] = ts
            
            upsert_fx_rate(
                db,
                pair=rate_data['pair'],
                ts=rate_data['ts'],
                rate=rate_data['rate'],
                base_currency=rate_data['base_currency'],
                quote_currency=rate_data['quote_currency'],
                source=rate_data['source']
            )
            success_count += 1
            
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to ingest rate for {rate_data.get('pair')}: {e}")
    
    logger.info(f"FX rate ingestion complete. Success: {success_count}, Failed: {failure_count}")
    return success_count, failure_count


def get_symbols_for_asset_class(db, asset_class: str) -> set:
    """
    Get unique symbols from positions table for a given asset class.
    Only returns symbols with non-zero quantity (active positions).
    
    IMPORTANT: This filtering is for MARKET_DATA only.
    FX rates (EUR/USD, EUR/THB) are fetched separately and are NOT filtered by positions.
    
    Args:
        db: Database session
        asset_class: Asset class to filter by (e.g., 'crypto', 'stock', 'etf')
        
    Returns:
        set: Unique symbols with active positions (quantity != 0)
    """
    return {
        row.symbol 
        for row in db.query(Position.symbol)
        .filter(Position.asset_class == asset_class)
        .filter(Position.quantity != 0)  # Only active positions
        .distinct()
    }


def get_symbols_excluding_asset_classes(db, exclude_classes: list[str]) -> set:
    """
    Get unique symbols from positions table excluding certain asset classes.
    Only returns symbols with non-zero quantity (active positions).
    
    IMPORTANT: This filtering is for MARKET_DATA only.
    FX rates (EUR/USD, EUR/THB) are fetched separately and are NOT filtered by positions.
    
    Args:
        db: Database session
        exclude_classes: List of asset classes to exclude
        
    Returns:
        set: Unique symbols with active positions (quantity != 0)
    """
    query = db.query(Position.symbol).distinct()
    query = query.filter(Position.quantity != 0)  # Only active positions
    for asset_class in exclude_classes:
        query = query.filter(Position.asset_class != asset_class)
    
    return {row.symbol for row in query}


# ============================================================================
# EXISTING FUNCTIONS - Kept for compatibility
# ============================================================================

def upsert_market_data(db, symbol, ts, price, currency, source):
    """
    Upsert market data with date-based deduplication.
    
    Note: The unique constraint is on (symbol, as_of_date) but as_of_date includes time.
    We need to check for existing records by date only to prevent duplicates when
    running multiple times per day.
    """
    from sqlalchemy import cast, Date
    
    # Normalize ts to a datetime object if it's a SQL expression
    if hasattr(ts, 'compile'):
        # It's a SQLAlchemy expression (like func.now()), use current datetime in UTC
        check_date = datetime.now(timezone.utc).date()
        insert_ts = datetime.now(timezone.utc)
    elif isinstance(ts, datetime):
        check_date = ts.date()
        insert_ts = ts
    else:
        # Assume it's already a date-like object
        check_date = ts
        insert_ts = ts
    
    # Find existing record for this symbol on this date
    row = db.query(MarketData).filter(
        MarketData.symbol == symbol,
        cast(MarketData.as_of_date, Date) == check_date
    ).order_by(MarketData.as_of_date.desc()).first()
    
    if row:
        # Update existing record
        row.price = price
        row.currency = currency
        row.source = source
        row.as_of_date = insert_ts  # Update timestamp to latest
        db.add(row)
        db.commit()
        return row
    
    # Create new record
    row = MarketData(symbol=symbol, as_of_date=insert_ts, price=price, currency=currency, source=source)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def get_latest_price(db, symbol, at_ts=None):
    q = db.query(MarketData).filter(MarketData.symbol == symbol)
    if at_ts:
        q = q.filter(MarketData.as_of_date <= at_ts)
    return q.order_by(MarketData.as_of_date.desc()).limit(1).one_or_none()

def upsert_fx_rate(db, pair, ts, rate, base_currency, quote_currency, source):
    """
    Upsert FX rate with date-based deduplication.
    
    Note: The unique constraint is on (pair, as_of_date) but as_of_date includes time.
    We need to check for existing records by date only to prevent duplicates when
    running multiple times per day.
    """
    from sqlalchemy import cast, Date
    
    # Normalize ts to a datetime object if it's a SQL expression
    if hasattr(ts, 'compile'):
        # It's a SQLAlchemy expression (like func.now()), use current datetime in UTC
        check_date = datetime.now(timezone.utc).date()
        insert_ts = datetime.now(timezone.utc)
    elif isinstance(ts, datetime):
        check_date = ts.date()
        insert_ts = ts
    else:
        # Assume it's already a date-like object
        check_date = ts
        insert_ts = ts
    
    # Find existing record for this pair on this date
    row = db.query(FxRate).filter(
        FxRate.pair == pair,
        cast(FxRate.as_of_date, Date) == check_date
    ).order_by(FxRate.as_of_date.desc()).first()
    
    if row:
        # Update existing record
        row.rate = rate
        row.base_currency = base_currency
        row.quote_currency = quote_currency
        row.source = source
        row.as_of_date = insert_ts  # Update timestamp to latest
        db.add(row)
        db.commit()
        return row
    
    # Create new record
    row = FxRate(pair=pair, as_of_date=insert_ts, rate=rate, base_currency=base_currency, quote_currency=quote_currency, source=source)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def get_latest_fx_rate(db, pair, at_ts=None):
    """
    Get the latest FX rate for a currency pair.
    Calculates inverse pairs automatically (e.g., THB/EUR will find EUR/THB and invert).
    
    Database Storage Strategy:
    - Only EUR/X rates are stored (ECB provides these)
    - Inverse rates (X/EUR) are calculated on-the-fly when needed
    - This avoids data duplication and ensures consistency
    
    Args:
        db: Database session
        pair: Currency pair in format "BASE/QUOTE" (e.g., "THB/EUR")
        at_ts: Optional timestamp to get historical rate
        
    Returns:
        FxRate record with rate field, or None if not found
    """
    from decimal import Decimal
    
    logger.debug(f"🔎 get_latest_fx_rate() CALLED: pair={pair}, at_ts={at_ts}")
    
    # Try direct lookup first
    q = db.query(FxRate).filter(FxRate.pair == pair)
    if at_ts:
        q = q.filter(FxRate.as_of_date <= at_ts)
    direct_result = q.order_by(FxRate.as_of_date.desc()).limit(1).one_or_none()
    
    if direct_result:
        logger.debug(f"✅ Found DIRECT pair: {direct_result.pair}, rate={direct_result.rate}, as_of_date={direct_result.as_of_date}")
        return direct_result
    
    # Try inverse pair (e.g., if looking for THB/EUR, try EUR/THB and invert)
    if '/' in pair:
        base, quote = pair.split('/')
        inverse_pair = f"{quote}/{base}"
        
        logger.debug(f"🔄 Direct pair not found, trying INVERSE: {inverse_pair}")
        
        q = db.query(FxRate).filter(FxRate.pair == inverse_pair)
        if at_ts:
            q = q.filter(FxRate.as_of_date <= at_ts)
        inverse_result = q.order_by(FxRate.as_of_date.desc()).limit(1).one_or_none()
        
        if inverse_result and inverse_result.rate and Decimal(str(inverse_result.rate)) != 0:
            # Create a temporary FxRate object with inverted rate
            from models import FxRate as FxRateModel
            inverted = FxRateModel()
            inverted.id = inverse_result.id
            inverted.pair = pair  # Use requested pair
            inverted.as_of_date = inverse_result.as_of_date
            original_rate = Decimal(str(inverse_result.rate))
            inverted_rate = Decimal('1') / original_rate
            inverted.rate = inverted_rate
            inverted.base_currency = base
            inverted.quote_currency = quote
            inverted.source = inverse_result.source
            inverted.created_at = inverse_result.created_at
            
            logger.debug(
                f"✅ Found INVERSE pair: {inverse_result.pair}, rate={original_rate}\n"
                f"   Inverting: 1 / {original_rate} = {inverted_rate}\n"
                f"   Returning: pair={pair}, rate={inverted_rate}"
            )
            
            return inverted
        else:
            logger.debug(f"❌ Inverse pair {inverse_pair} not found or has zero rate")
    
    logger.debug(f"❌ No FX rate found for {pair} (direct or inverse)")
    return None
