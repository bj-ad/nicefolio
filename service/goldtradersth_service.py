import requests
from typing import Optional
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE
from models import MarketData
from database import SessionLocal
from crud.crud_market_fx import ingest_market_prices
from crud.parsers.marketdata_parser import parse_goldtraders_json, parse_goldtraders_html
from models import Position

logger = get_logger("goldtradersth_service")

GOLD_SYMBOL = "GOLDTHB"
GOLD_API_URL = "https://www.goldtraders.or.th/api/GoldPrices/Latest?readjson=true"
GOLD_LEGACY_URL = "https://www.goldtraders.or.th/default.aspx"


@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_gold_price_from_goldtraders() -> Optional[dict]:
    """
    Fetch gold price from goldtraders.or.th JSON API (cacheable API call only).
    
    Returns:
        dict: Parsed gold price data or None if fetch fails
            - symbol: GOLDTHB
            - price: Gold price in Baht (bid price)
            - currency: THB
            - source: goldtraders.or.th
    """
    logger.info("Fetching gold price from goldtraders.or.th JSON API")
    try:
        response = requests.get(GOLD_API_URL, timeout=10)
        if response.status_code == 200:
            logger.info("Successfully fetched gold price from goldtraders.or.th API")
            return parse_goldtraders_json(response.json())
        else:
            logger.warning(f"goldtraders.or.th API returned status code {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching gold price from goldtraders.or.th API: {e}", exc_info=True)
        return None


@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_gold_price_from_goldtraders_legacy() -> Optional[dict]:
    """
    Fetch gold price from goldtraders.or.th using legacy HTML scraping (cacheable API call only).
    
    This is a fallback method for when the JSON API is unavailable.
    
    Returns:
        dict: Parsed gold price data or None if fetch fails
            - symbol: GOLDTHB
            - price: Gold price in Baht
            - currency: THB
            - source: goldtraders.or.th
    """
    logger.info("Fetching gold price from goldtraders.or.th/default.aspx (legacy HTML)")
    try:
        response = requests.get(GOLD_LEGACY_URL, timeout=10)
        if response.status_code == 200:
            logger.info("Successfully connected to goldtraders.or.th (legacy)")
            return parse_goldtraders_html(response.text)
        else:
            logger.warning(f"goldtraders.or.th (legacy) returned status code {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching gold price from goldtraders.or.th (legacy): {e}", exc_info=True)
        return None


def sync_gold_price():
    """
    Sync gold price from goldtraders.or.th with multi-layer fallback.
    
    This is the main entry point for gold price synchronization.
    Follows the three-layer pattern with multiple failover strategies:
    1. Check if GOLDTHB position exists with non-zero quantity
    2. Try JSON API (modern)
    3. Try HTML scraping (legacy)
    4. Database fallback
    
    - Service layer: fetch_gold_price_from_goldtraders() and fetch_gold_price_from_goldtraders_legacy() (API calls)
    - Parser layer: parse_goldtraders_json() and parse_goldtraders_html() (transformations)
    - CRUD layer: ingest_market_prices() (database operations)
    """
    db = SessionLocal()
    try:
        # Check if there's an active position for GOLDTHB
        position = db.query(Position).filter(
            Position.symbol == GOLD_SYMBOL,
            Position.quantity != 0
        ).first()
        
        if not position:
            logger.info(f"No active position found for {GOLD_SYMBOL}, skipping goldtraders.or.th scraping")
            return
        
        logger.info(f"Active position found for {GOLD_SYMBOL} (quantity: {position.quantity}), proceeding with price sync")
        
        # Try fetching from JSON API (primary source)
        price_data = fetch_gold_price_from_goldtraders()
        
        # Fallback to legacy HTML scraping if JSON API fails
        if not price_data:
            logger.info("JSON API fetch failed, trying legacy HTML scraping")
            price_data = fetch_gold_price_from_goldtraders_legacy()
        
        # Fallback to database if both methods fail
        if not price_data:
            logger.info("All goldtraders.or.th methods failed, trying database fallback")
            fallback = db.query(MarketData).filter_by(symbol=GOLD_SYMBOL).order_by(MarketData.as_of_date.desc()).first()
            if fallback:
                price_data = {
                    'symbol': GOLD_SYMBOL,
                    'price': fallback.price,
                    'currency': 'THB',
                    'source': 'fallback'
                }
                logger.info(f"Using database fallback: {fallback.price} THB (from {fallback.as_of_date})")
            else:
                logger.warning(f"No gold price available for {GOLD_SYMBOL} from any source")
                return
        
        # Ingest the price
        success, failed = ingest_market_prices(db, [price_data])
        if success:
            logger.info(f"Gold price sync complete: {price_data['price']} THB (source: {price_data['source']})")
        else:
            logger.error("Failed to ingest gold price")
        
    except Exception as e:
        logger.error(f"Error syncing gold price: {e}", exc_info=True)
    finally:
        db.close()
