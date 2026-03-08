"""
Parser module for FX rates from various sources.
Transforms raw API responses into standardized FxRate format.
"""
from decimal import Decimal
from utils.logging_config import get_logger

logger = get_logger(__name__)


def parse_exchangerate_api_rate(api_response: dict, pair: str) -> dict | None:
    """
    Parse exchangerate-api.com response into FxRate format.
    
    Args:
        api_response: Raw API response dict
        pair: FX pair like 'USD/THB'
        
    Returns:
        dict: FX rate data or None if failed
            - pair: FX pair
            - rate: Exchange rate as Decimal
            - base_currency: Base currency code
            - quote_currency: Quote currency code
            - source: Data source identifier
    """
    try:
        base, quote = pair.split('/')
        
        if 'conversion_rates' not in api_response:
            logger.warning(f"No conversion_rates in response for {pair}")
            return None
            
        rate = api_response['conversion_rates'].get(quote)
        if not rate:
            logger.warning(f"Rate for {quote} not found in response")
            return None
            
        return {
            'pair': pair,
            'rate': Decimal(str(rate)),
            'base_currency': base,
            'quote_currency': quote,
            'source': 'exchangerate-api'
        }
        
    except ValueError as e:
        logger.error(f"Invalid pair format '{pair}': {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to parse exchangerate-api data for {pair}: {e}")
        return None


# ============================================================================
# REMOVED: parse_yfinance_fx_rate()
# 
# CRITICAL FOR TAX COMPLIANCE:
# German tax law requires ECB rates for FX conversions.
# yfinance FX data is NOT acceptable.
# ALL FX rates must come from ECB (ecb_parser.py)
# 
# This function was removed on 2025-12-25 to prevent contamination of FX data.
# If you need FX rates, use service/ecb_service.py
# ============================================================================
