import os
from dotenv import load_dotenv
import yfinance as yf
from sqlalchemy import func
from utils.api_client import make_api_call
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE
from utils.app_config import load_app_config
from crud.crud_market_fx import (
    ingest_market_prices,
    get_symbols_for_asset_class,
    get_symbols_excluding_asset_classes,
    get_latest_price,
    upsert_market_data
)
from crud.crud_symbol_mapping import get_symbol_mapping
from crud.parsers.marketdata_parser import (
    parse_coinmarketcap_prices,
    parse_yfinance_price
)
from models import Position, MarketData, Transaction
from database import SessionLocal

logger = get_logger("marketdata_service")

load_dotenv()
COINMARKETCAP_API_KEY = os.getenv('COINMARKETCAP_API_KEY')

# Load app config for price sync exclusions
app_config = load_app_config()
PRICE_SYNC_CONFIG = app_config.get('price_sync', {})
EXCLUDE_ASSET_CLASSES = PRICE_SYNC_CONFIG.get('exclude_asset_classes', [])
EXCLUDE_SYMBOLS = set(PRICE_SYNC_CONFIG.get('exclude_symbols', []))


# ============================================================================
# NEW API FUNCTIONS - Pure API calls (cacheable)
# ============================================================================

@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_crypto_prices_from_coinmarketcap(symbols: tuple) -> dict:
    """
    Fetch crypto prices from CoinMarketCap API (pure API call - cacheable).
    
    Args:
        symbols: Tuple of crypto symbols to fetch (tuple is hashable for cache)
        
    Returns:
        dict: Raw API response
    """
    if not COINMARKETCAP_API_KEY or not symbols:
        logger.warning("CoinMarketCap API key not configured or no symbols provided")
        return {}
    
    url = 'https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest'
    headers = {
        'Accepts': 'application/json',
        'X-CMC_PRO_API_KEY': COINMARKETCAP_API_KEY
    }
    params = {'symbol': ','.join(symbols), 'convert': 'USD'}
    
    logger.info(f"Fetching crypto prices from CoinMarketCap for {len(symbols)} symbols")
    return make_api_call(url, method="GET", params=params, headers=headers)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_symbol_currency_from_transactions(db, symbol: str) -> str:
    """
    Get the currency for a symbol from the most recent transaction.
    
    Args:
        db: Database session
        symbol: Symbol to look up
        
    Returns:
        str: Currency code (e.g., 'USD', 'EUR') or 'USD' as default
    """
    try:
        # Query most recent transaction for this symbol
        tx = db.query(Transaction).filter_by(symbol=symbol)\
            .order_by(Transaction.occurred_at.desc()).first()
        
        if tx and tx.currency_native:
            logger.debug(f"Found currency {tx.currency_native} for symbol {symbol} from transaction")
            return tx.currency_native
        
        # Fallback: check Position.currency_native
        position = db.query(Position).filter_by(symbol=symbol).first()
        if position and position.currency_native:
            logger.debug(f"Found currency {position.currency_native} for symbol {symbol} from position")
            return position.currency_native
        
        # No fallback - raise error if currency cannot be determined
        raise ValueError(
            f"Cannot determine currency for symbol {symbol}. "
            f"No transaction or position found with currency_native set."
        )
        
    except ValueError:
        raise  # Re-raise ValueError (our explicit errors)
    except Exception as e:
        raise ValueError(
            f"Error getting currency for {symbol}: {e}. "
            f"Currency must be explicitly set - no fallback to USD."
        )


# ============================================================================
# NEW SYNC FUNCTIONS - Orchestration only (replaces fetch_and_record_*)
# ============================================================================

def sync_crypto_prices():
    """
    Orchestrates fetching and syncing crypto prices.
    Entry point for scheduler/jobs - replaces fetch_and_record_crypto_prices.
    """
    logger.info("Starting crypto price sync...")
    db = SessionLocal()
    
    try:
        # 1. Get symbols from database
        symbols = get_symbols_for_asset_class(db, 'crypto')
        if not symbols:
            logger.info("No crypto positions found")
            return
        
        # 2. Filter out excluded symbols
        original_count = len(symbols)
        if EXCLUDE_SYMBOLS:
            symbols = symbols - EXCLUDE_SYMBOLS
            excluded = EXCLUDE_SYMBOLS & symbols
            if excluded:
                logger.info(f"Filtered out {len(excluded)} excluded crypto symbols: {excluded}")
        
        if not symbols:
            logger.info("All crypto symbols are excluded from price sync")
            return
        
        logger.info(f"Syncing prices for {len(symbols)} crypto symbols")
        all_prices = []
        
        # 2. Try CoinMarketCap (convert to tuple for cache key)
        symbols_tuple = tuple(symbols)
        logger.info(f"Fetching crypto prices from CoinMarketCap for {len(symbols)} symbols")
        cmc_response = fetch_crypto_prices_from_coinmarketcap(symbols_tuple)
        cmc_prices = parse_coinmarketcap_prices(cmc_response, list(symbols))
        
        # Log each price fetched
        for price in cmc_prices:
            logger.info(f"Successfully fetched {price['symbol']}: ${price['price']:.2f} (source: coinmarketcap)")
        
        all_prices.extend(cmc_prices)
        
        # 3. Fallback to yfinance for missing symbols
        fetched_symbols = {p['symbol'] for p in cmc_prices}
        missing = symbols - fetched_symbols
        
        if missing:
            logger.info(f"Fetching {len(missing)} symbols from yfinance as fallback")
            for symbol in missing:
                # Get currency from transactions/positions (usually USD for crypto)
                currency = get_symbol_currency_from_transactions(db, symbol)
                
                # Get symbol mapping (checks DB → YAML → defaults)
                mapping = get_symbol_mapping(db, symbol=symbol, currency=currency, asset_class='crypto')
                yf_symbol = mapping['yfinance_symbol']
                mapped_currency = mapping['currency']
                
                logger.debug(f"Fetching {symbol} as '{yf_symbol}' in {mapped_currency}")
                
                # Fetch from yfinance
                yf_ticker = yf.Ticker(yf_symbol)
                price_data = parse_yfinance_price(symbol, yf_ticker, mapped_currency)
                if price_data:
                    logger.info(f"Successfully fetched {symbol}: {mapped_currency} {price_data['price']:.2f} (yfinance: {yf_symbol})")
                    all_prices.append(price_data)
                else:
                    logger.warning(f"Failed to fetch {symbol} from yfinance (ticker: {yf_symbol})")
        
        # 4. Database fallback for still-missing symbols
        fetched_symbols = {p['symbol'] for p in all_prices}
        still_missing = symbols - fetched_symbols
        
        if still_missing:
            logger.info(f"Using database fallback for {len(still_missing)} symbols")
            for symbol in still_missing:
                fallback = get_latest_price(db, symbol)
                if fallback:
                    all_prices.append({
                        'symbol': symbol,
                        'price': fallback.price,
                        'currency': fallback.currency,
                        'source': 'fallback'
                    })
                else:
                    logger.warning(f"No price available for {symbol} from any source")
        
        # 5. Ingest all prices
        if all_prices:
            success, failed = ingest_market_prices(db, all_prices)
            logger.info(f"Crypto price sync complete: {success} succeeded, {failed} failed")
        else:
            logger.warning("No crypto prices to ingest")
        
    except Exception as e:
        logger.error(f"Error syncing crypto prices: {e}", exc_info=True)
    finally:
        db.close()


def sync_securities_prices():
    """
    Orchestrates fetching and syncing securities prices.
    Entry point for scheduler/jobs - replaces fetch_and_record_securities_prices.
    
    Excludes asset classes from price_sync.exclude_asset_classes config:
    - crypto (handled by separate crypto price sync)
    - gold_baht (handled by Gold Traders scraper)
    - cash (handled by FX sync)
    - mutual_fund, strategic_balancing_reserve, etc.
    
    IMPORTANT: Ensure config file has correct exclude_asset_classes values!
    """
    logger.info("Starting securities price sync...")
    db = SessionLocal()
    
    try:
        # Get exclusion list from config
        exclude_classes = EXCLUDE_ASSET_CLASSES
        
        # Get securities symbols (exclude configured asset classes)
        symbols = get_symbols_excluding_asset_classes(db, exclude_classes)
        
        # Filter out specifically excluded symbols from config
        if EXCLUDE_SYMBOLS:
            original_count = len(symbols)
            symbols = symbols - EXCLUDE_SYMBOLS
            if original_count != len(symbols):
                logger.info(f"Filtered out {original_count - len(symbols)} excluded symbols: {EXCLUDE_SYMBOLS & set(symbols)}")
        
        if not symbols:
            logger.info("No securities positions found")
            return
        
        logger.info(f"Syncing prices for {len(symbols)} securities (excluded asset classes: {exclude_classes})")
        all_prices = []
        
        # Fetch prices from yfinance
        logger.info(f"Fetching {len(symbols)} symbols from yfinance")
        for symbol in symbols:
                # Get currency from transactions/positions
                currency = get_symbol_currency_from_transactions(db, symbol)
                
                # Get symbol mapping (checks DB → YAML → defaults)
                mapping = get_symbol_mapping(db, symbol=symbol, currency=currency, asset_class='security')
                yf_symbol = mapping['yfinance_symbol']
                mapped_currency = mapping['currency']
                
                logger.debug(f"Fetching {symbol} as '{yf_symbol}' in {mapped_currency}")
                
                # Fetch from yfinance
                yf_ticker = yf.Ticker(yf_symbol)
                price_data = parse_yfinance_price(symbol, yf_ticker, mapped_currency)
                if price_data:
                    logger.info(f"Successfully fetched {symbol}: {mapped_currency} {price_data['price']:.2f} (yfinance: {yf_symbol})")
                    all_prices.append(price_data)
                else:
                    logger.warning(f"Failed to fetch {symbol} from yfinance (ticker: {yf_symbol})")
        
        # Database fallback for still-missing symbols
        fetched_symbols = {p['symbol'] for p in all_prices}
        still_missing = symbols - fetched_symbols
        
        if still_missing:
            logger.info(f"Using database fallback for {len(still_missing)} symbols")
            for symbol in still_missing:
                fallback = get_latest_price(db, symbol)
                if fallback:
                    all_prices.append({
                        'symbol': symbol,
                        'price': fallback.price,
                        'currency': fallback.currency,
                        'source': 'fallback'
                    })
                else:
                    logger.warning(f"No price available for {symbol} from any source")
        
        # Ingest all prices
        if all_prices:
            success, failed = ingest_market_prices(db, all_prices)
            logger.info(f"Securities price sync complete: {success} succeeded, {failed} failed")
        else:
            logger.warning("No securities prices to ingest")
        
    except Exception as e:
        logger.error(f"Error syncing securities prices: {e}", exc_info=True)
    finally:
        db.close()
