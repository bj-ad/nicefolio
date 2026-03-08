"""
Parser module for market data from various sources.
Transforms raw API/web responses into standardized MarketData format.
"""
from decimal import Decimal
from datetime import datetime
from bs4 import BeautifulSoup
from utils.logging_config import get_logger

logger = get_logger(__name__)


def parse_coinmarketcap_prices(api_response: dict, symbols: list) -> list[dict]:
    """
    Parse CoinMarketCap API response into MarketData format.
    
    Args:
        api_response: Raw API response from CoinMarketCap
        symbols: List of symbols to parse
    
    Returns:
        list[dict]: List of price data dicts with keys:
            - symbol: Asset symbol
            - price: Price as Decimal
            - currency: Currency code
            - source: Data source identifier
    """
    prices = []
    
    if not api_response or 'data' not in api_response:
        logger.warning("Invalid CoinMarketCap response")
        return prices
    
    for symbol in symbols:
        try:
            symbol_data = api_response['data'].get(symbol)
            if not symbol_data:
                continue
                
            price_data = {
                'symbol': symbol,
                'price': Decimal(str(symbol_data['quote']['USD']['price'])),
                'currency': 'USD',
                'source': 'coinmarketcap',
            }
            prices.append(price_data)
            
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse CoinMarketCap data for {symbol}: {e}")
            continue
    
    logger.info(f"Parsed {len(prices)}/{len(symbols)} symbols from CoinMarketCap")
    return prices


def parse_yfinance_price(symbol: str, yf_ticker_data, currency: str = 'USD') -> dict | None:
    """
    Parse yfinance ticker data into MarketData format.
    
    Extracts currency from yfinance's ticker.info if available, otherwise uses
    the provided currency parameter (from symbol mapping or transaction history).
    
    Args:
        symbol: Asset symbol
        yf_ticker_data: yfinance Ticker object
        currency: Fallback currency if yfinance doesn't provide it (default: USD)
        
    Returns:
        dict: Price data or None if failed
    """
    try:
        history = yf_ticker_data.history(period="1d")
        if history.empty:
            logger.warning(f"No yfinance data for {symbol}")
            return None
            
        price = history["Close"].iloc[-1]
        
        # Try to get currency from yfinance info (most reliable)
        actual_currency = currency  # fallback
        try:
            info = yf_ticker_data.info
            if info and 'currency' in info and info['currency']:
                actual_currency = info['currency']
                if actual_currency != currency:
                    logger.info(f"Currency override for {symbol}: config={currency} → yfinance={actual_currency}")
        except Exception as e:
            logger.debug(f"Could not fetch currency from yfinance info for {symbol}: {e}")
        
        return {
            'symbol': symbol,
            'price': Decimal(str(price)),
            'currency': actual_currency,
            'source': 'yfinance'
        }
        
    except Exception as e:
        logger.warning(f"Failed to parse yfinance data for {symbol}: {e}")
        return None


def parse_alphavantage_price(symbol: str, api_response: dict) -> dict | None:
    """
    Parse AlphaVantage API response into MarketData format.
    
    Args:
        symbol: Asset symbol
        api_response: Raw API response from AlphaVantage
        
    Returns:
        dict: Price data or None if failed
    """
    try:
        # Check for API limit/error messages
        if 'Note' in api_response:
            logger.warning(f"AlphaVantage rate limit hit for {symbol}: {api_response['Note']}")
            return None
        
        if 'Information' in api_response:
            logger.warning(f"AlphaVantage info for {symbol}: {api_response['Information']}")
            return None
        
        if 'Error Message' in api_response:
            logger.warning(f"AlphaVantage error for {symbol}: {api_response['Error Message']}")
            return None
        
        time_series = api_response.get("Time Series (Daily)", {})
        if not time_series:
            logger.warning(f"No time series data for {symbol} from AlphaVantage")
            return None
            
        latest_date = max(time_series.keys())
        price = time_series[latest_date]["4. close"]
        
        return {
            'symbol': symbol,
            'price': Decimal(str(price)),
            'currency': 'USD',
            'source': 'alphavantage',
            'ts': latest_date  # AlphaVantage provides date string
        }
        
    except Exception as e:
        logger.warning(f"Failed to parse AlphaVantage data for {symbol}: {e}")
        return None


def parse_goldtraders_json(api_response: dict, symbol: str = 'GOLDTHB') -> dict | None:
    """
    Parse GoldTraders Thailand JSON API response to extract gold price.
    
    API Endpoint: https://www.goldtraders.or.th/api/GoldPrices/Latest?readjson=true
    
    Args:
        api_response: JSON response from goldtraders.or.th API
        symbol: Symbol to use (default: GOLDTHB)
        
    Returns:
        dict: Price data or None if failed
            - symbol: GOLDTHB
            - price: Gold bar buy price (bid price) in THB
            - currency: THB
            - source: goldtraders.or.th
    
    JSON Response structure:
        {
            "bL_BuyPrice": 73000.0,    # Gold bar buy (bid) price
            "bL_SellPrice": 73100.0,   # Gold bar sell (ask) price
            "asTime": "2026-01-24T09:10:00",
            "goldSpot": 4987.0,
            "bahtPerUSD": 31.0,
            ...
        }
    """
    try:
        if not api_response or not isinstance(api_response, dict):
            logger.warning("Invalid goldtraders.or.th API response")
            return None
        
        # Extract gold bar buy price (bid price)
        buy_price = api_response.get('bL_BuyPrice')
        
        if buy_price is None:
            logger.warning("Missing 'bL_BuyPrice' in goldtraders.or.th API response")
            return None
        
        # Convert to Decimal with 2 decimal places (Thai Baht has satang: 1 THB = 100 satang)
        price = Decimal(str(buy_price)).quantize(Decimal('0.01'))
        
        logger.info(f"Successfully parsed gold price: {price} THB (from JSON API)")
        
        return {
            'symbol': symbol,
            'price': price,
            'currency': 'THB',
            'source': 'goldtraders.or.th'
        }
        
    except (ValueError, TypeError, KeyError) as e:
        logger.warning(f"Failed to parse goldtraders.or.th JSON: {e}")
        return None


# DEPRECATED: Old HTML scraper - kept for reference
def parse_goldtraders_html(html_content: str, symbol: str = 'GOLDTHB') -> dict | None:
    """
    DEPRECATED: Use parse_goldtraders_json() instead.
    
    Old HTML scraper for goldtraders.or.th (website redesigned in Jan 2026).
    Kept for reference only - no longer functional.
    """
    logger.warning("parse_goldtraders_html() is deprecated. Website now uses JSON API.")
    return None
