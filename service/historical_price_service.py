"""
Historical Price Service

Service layer for fetching historical cryptocurrency prices.
Uses existing MarketData table first, falls back to yfinance API if needed.

Layer: Service (API orchestration + database queries)
Dependencies: MarketData model, yfinance
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, Dict
import yfinance as yf
from sqlalchemy.orm import Session

from database import SessionLocal
from models import MarketData
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE

logger = get_logger(__name__)

# Map crypto symbols to yfinance tickers
YFINANCE_TICKERS = {
    'BTC': 'BTC-USD',
    'ETH': 'ETH-USD',
    'SOL': 'SOL-USD',
    'BNB': 'BNB-USD',
    'ADA': 'ADA-USD',
    'XRP': 'XRP-USD',
}


@cache(ttl=CACHE_TTL * 365, maxsize=CACHE_MAXSIZE * 10)  # Cache for 1 year (historical data immutable)
def get_historical_price(symbol: str, target_date: date, db: Optional[Session] = None) -> Optional[Decimal]:
    """
    Fetch historical crypto price for a specific date.
    
    Strategy:
    1. Check database (MarketData table) first
    2. If not found, fetch from yfinance and store
    3. Return price in USD
    
    Args:
        symbol: Crypto symbol (BTC, ETH, SOL, BNB, ADA, XRP)
        target_date: Date to fetch price for
        db: Optional database session (creates new if not provided)
        
    Returns:
        Price in USD as Decimal, or None if not found
        
    Examples:
        >>> get_historical_price('BTC', date(2020, 9, 1))
        Decimal('11679.22')
        
        >>> get_historical_price('ETH', date(2021, 1, 1))
        Decimal('737.71')
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        # Step 1: Check database
        price_from_db = _get_price_from_database(db, symbol, target_date)
        if price_from_db:
            logger.info(f"Found historical price for {symbol} on {target_date} in database: ${price_from_db}")
            return price_from_db
        
        # Step 2: Fetch from yfinance
        logger.info(f"Price not in database, fetching {symbol} from yfinance for {target_date}")
        price_from_yfinance = _fetch_and_store_from_yfinance(db, symbol, target_date)
        
        if price_from_yfinance:
            logger.info(f"Fetched and stored {symbol} price: ${price_from_yfinance}")
            return price_from_yfinance
        else:
            logger.warning(f"Could not fetch historical price for {symbol} on {target_date}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching historical price for {symbol} on {target_date}: {e}", exc_info=True)
        return None
    finally:
        if close_db:
            db.close()


def _get_price_from_database(db: Session, symbol: str, target_date: date) -> Optional[Decimal]:
    """
    Query database for historical price on specific date.
    
    Args:
        db: Database session
        symbol: Crypto symbol
        target_date: Target date
        
    Returns:
        Price as Decimal or None
    """
    try:
        # Query for price on target date (any time during the day)
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())
        
        price_record = db.query(MarketData).filter(
            MarketData.symbol == symbol,
            MarketData.as_of_date >= start_dt,
            MarketData.as_of_date <= end_dt,
            MarketData.currency == 'USD'
        ).order_by(MarketData.as_of_date.desc()).first()
        
        if price_record:
            return price_record.price
        
        return None
        
    except Exception as e:
        logger.error(f"Database query error for {symbol} on {target_date}: {e}")
        return None


def _fetch_and_store_from_yfinance(db: Session, symbol: str, target_date: date) -> Optional[Decimal]:
    """
    Fetch historical price from yfinance and store in database.
    
    Args:
        db: Database session
        symbol: Crypto symbol
        target_date: Target date
        
    Returns:
        Price as Decimal or None
    """
    try:
        # Map symbol to yfinance ticker
        ticker = YFINANCE_TICKERS.get(symbol)
        if not ticker:
            logger.warning(f"Unknown symbol for yfinance: {symbol}")
            return None
        
        # Fetch data for the target date (need range of dates for yfinance)
        start_date = target_date
        end_date = target_date + timedelta(days=1)
        
        logger.info(f"Fetching {ticker} from yfinance for date range {start_date} to {end_date}")
        
        # Download data
        ticker_obj = yf.Ticker(ticker)
        hist = ticker_obj.history(start=start_date, end=end_date)
        
        if hist.empty:
            # Try a wider range (sometimes data is delayed)
            logger.info(f"No data for exact date, trying wider range")
            start_date = target_date - timedelta(days=3)
            end_date = target_date + timedelta(days=1)
            hist = ticker_obj.history(start=start_date, end=end_date)
        
        if hist.empty:
            logger.warning(f"No data from yfinance for {ticker} around {target_date}")
            return None
        
        # Get closing price (use the first available date if exact match not found)
        close_price = hist['Close'].iloc[0]
        
        if close_price is None or close_price <= 0:
            logger.warning(f"Invalid price from yfinance: {close_price}")
            return None
        
        price_decimal = Decimal(str(close_price))
        
        # Store in database
        market_data = MarketData(
            symbol=symbol,
            ts=datetime.combine(target_date, datetime.min.time()),
            price=price_decimal,
            currency='USD',
            source='yfinance_historical'
        )
        
        db.add(market_data)
        db.commit()
        
        logger.info(f"Stored {symbol} price ${price_decimal} for {target_date} from yfinance")
        
        return price_decimal
        
    except Exception as e:
        logger.error(f"Error fetching from yfinance for {symbol} on {target_date}: {e}", exc_info=True)
        db.rollback()
        return None


def get_historical_prices_bulk(
    symbol: str,
    start_date: date,
    end_date: date,
    db: Optional[Session] = None
) -> Dict[date, Decimal]:
    """
    Fetch historical prices for a date range.
    More efficient than calling get_historical_price() repeatedly.
    
    Args:
        symbol: Crypto symbol
        start_date: Start of date range
        end_date: End of date range (inclusive)
        db: Optional database session
        
    Returns:
        Dictionary mapping date -> price
        
    Example:
        >>> prices = get_historical_prices_bulk('BTC', date(2020, 9, 1), date(2020, 9, 30))
        >>> len(prices)
        30
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        prices = {}
        
        # Query database for all prices in range
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        price_records = db.query(MarketData).filter(
            MarketData.symbol == symbol,
            MarketData.as_of_date >= start_dt,
            MarketData.as_of_date <= end_dt,
            MarketData.currency == 'USD'
        ).order_by(MarketData.as_of_date).all()
        
        # Group by date (in case multiple prices per day)
        for record in price_records:
            record_date = record.as_of_date.date()
            if record_date not in prices:
                prices[record_date] = record.price
        
        logger.info(f"Found {len(prices)} prices for {symbol} from {start_date} to {end_date} in database")
        
        # Check if we have all dates (accounting for weekends/holidays)
        # For crypto, we should have data for every day
        expected_days = (end_date - start_date).days + 1
        if len(prices) < expected_days * 0.9:  # Allow 10% missing (holidays, etc.)
            logger.warning(f"Missing some dates for {symbol}, have {len(prices)}/{expected_days} days")
        
        return prices
        
    except Exception as e:
        logger.error(f"Error fetching bulk prices for {symbol}: {e}", exc_info=True)
        return {}
    finally:
        if close_db:
            db.close()


def validate_price_data_availability(start_date: date, end_date: date) -> Dict[str, bool]:
    """
    Check if we have price data for all required cryptos in date range.
    
    Args:
        start_date: Start of date range
        end_date: End of date range
        
    Returns:
        Dictionary mapping symbol -> has_data (bool)
        
    Example:
        >>> validate_price_data_availability(date(2020, 9, 1), date(2020, 12, 31))
        {'BTC': True, 'ETH': True, 'SOL': True, 'BNB': True, 'ADA': True, 'XRP': True}
    """
    db = SessionLocal()
    try:
        results = {}
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        for symbol in YFINANCE_TICKERS.keys():
            count = db.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.as_of_date >= start_dt,
                MarketData.as_of_date <= end_dt,
                MarketData.currency == 'USD'
            ).count()
            
            expected_days = (end_date - start_date).days + 1
            has_data = count >= expected_days * 0.9  # 90% coverage acceptable
            
            results[symbol] = has_data
            
            if has_data:
                logger.info(f"{symbol}: ✅ {count}/{expected_days} days available")
            else:
                logger.warning(f"{symbol}: ⚠️  Only {count}/{expected_days} days available")
        
        return results
        
    except Exception as e:
        logger.error(f"Error validating price data: {e}")
        return {symbol: False for symbol in YFINANCE_TICKERS.keys()}
    finally:
        db.close()


if __name__ == '__main__':
    """Test the historical price service."""
    from datetime import date
    
    print("="*80)
    print("TESTING HISTORICAL PRICE SERVICE")
    print("="*80)
    
    # Test 1: Get a specific historical price
    print("\nTest 1: Get BTC price on 2020-09-01")
    price = get_historical_price('BTC', date(2020, 9, 1))
    print(f"Result: ${price}")
    
    # Test 2: Get bulk prices
    print("\nTest 2: Get BTC prices for September 2020")
    prices = get_historical_prices_bulk('BTC', date(2020, 9, 1), date(2020, 9, 30))
    print(f"Result: {len(prices)} days of data")
    if prices:
        first_date = min(prices.keys())
        last_date = max(prices.keys())
        print(f"  First: {first_date} -> ${prices[first_date]}")
        print(f"  Last:  {last_date} -> ${prices[last_date]}")
    
    # Test 3: Validate data availability for migration period
    print("\nTest 3: Validate data availability (2020-09-01 to 2024-11-17)")
    availability = validate_price_data_availability(date(2020, 9, 1), date(2024, 11, 17))
    print("Results:")
    for symbol, has_data in availability.items():
        status = "✅" if has_data else "❌"
        print(f"  {symbol}: {status}")
