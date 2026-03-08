"""
ECB (European Central Bank) FX Rate Parser

Transforms ECB API responses to database FxRate format.
ECB API returns data in JSON format with structure documented at:
https://data.ecb.europa.eu/help/api/data

Timezone Handling:
- ECB rates have no time component (just YYYY-MM-DD dates)
- We convert to UTC datetime at 00:00:00 for consistency
- Database timestamps stored in UTC (TIMESTAMP WITH TIME ZONE)

Response Structure:
{
    "dataSets": [{
        "series": {
            "0:0:0:0:0": {
                "observations": {
                    "0": [1.0850, 0],  # [value, status]
                    "1": [1.0862, 0]
                }
            }
        }
    }],
    "structure": {
        "dimensions": {
            "observation": [{
                "values": [
                    {"id": "2025-11-15", "name": "15 November 2025"},
                    {"id": "2025-11-16", "name": "16 November 2025"}
                ]
            }],
            "series": [
                {"values": [{"id": "D"}]},  # Frequency: Daily
                {"values": [{"id": "USD"}]}, # Currency
                {"values": [{"id": "EUR"}]}, # Quote currency
                {"values": [{"id": "SP00"}]}, # Exchange rate type
                {"values": [{"id": "A"}]}  # Series variation
            ]
        }
    }
}
"""

from decimal import Decimal
from datetime import datetime
from typing import List, Optional
import pytz
from utils.logging_config import get_logger

logger = get_logger("ecb_parser")

# Use UTC timezone for all timestamps
UTC = pytz.UTC


def parse_ecb_rates(response: dict) -> Optional[List[dict]]:
    """
    Parse ECB API response to FxRate format.
    
    Args:
        response: ECB API JSON response
    
    Returns:
        List[dict]: Standardized rate data with keys:
            - pair: str (e.g., "EUR/USD", "EUR/THB")
            - rate: Decimal (1 EUR = X foreign currency)
            - base_currency: str ("EUR")
            - quote_currency: str (e.g., "USD", "THB")
            - ts: datetime (with timezone UTC)
            - source: str ("ecb")
        Returns None if parsing fails
    
    Notes:
        - ECB rates are always EUR/XXX (1 EUR = X foreign currency)
        - Rate represents exchange rate at 4pm CET on that date
        - Multiple series in response (one per currency: USD, THB)
    """
    try:
        if 'dataSets' not in response:
            logger.warning("ECB response missing 'dataSets' key")
            return None
        
        if 'structure' not in response:
            logger.warning("ECB response missing 'structure' key")
            return None
        
        data_sets = response['dataSets']
        structure = response['structure']
        
        if not data_sets or not data_sets[0].get('series'):
            logger.warning("ECB response has no series data")
            return None
        
        # Extract dimension metadata
        dimensions = structure.get('dimensions', {})
        observation_dimension = dimensions.get('observation', [{}])[0]
        series_dimensions = dimensions.get('series', [])
        
        # Get date values from observation dimension
        date_values = observation_dimension.get('values', [])
        if not date_values:
            logger.warning("ECB response has no date values")
            return None
        
        # Get currency from series dimensions (index 1 is currency)
        if len(series_dimensions) < 2:
            logger.warning("ECB response missing series dimensions")
            return None
        
        series_data = data_sets[0]['series']
        all_rates = []
        
        # Iterate through each series (one per currency: USD, THB)
        for series_key, series_value in series_data.items():
            observations = series_value.get('observations', {})
            if not observations:
                continue
            
            # Extract currency from series key (e.g., "0:0:0:0:0" -> index 1)
            # Series key format: "freq:currency:quoteCcy:exrType:seriesVar"
            series_key_parts = series_key.split(':')
            if len(series_key_parts) < 2:
                logger.warning(f"Invalid series key format: {series_key}")
                continue
            
            currency_index = int(series_key_parts[1])
            currency_dimension = series_dimensions[1].get('values', [])
            
            if currency_index >= len(currency_dimension):
                logger.warning(f"Currency index {currency_index} out of range")
                continue
            
            currency = currency_dimension[currency_index].get('id')
            if not currency:
                logger.warning(f"No currency found for index {currency_index}")
                continue
            
            # Parse each observation (date -> rate)
            for obs_index_str, obs_value in observations.items():
                try:
                    obs_index = int(obs_index_str)
                    
                    # Get date for this observation
                    if obs_index >= len(date_values):
                        logger.warning(f"Observation index {obs_index} out of range")
                        continue
                    
                    date_str = date_values[obs_index].get('id')
                    if not date_str:
                        continue
                    
                    # Parse date (format: YYYY-MM-DD) and convert to UTC datetime
                    # ECB rates have no time component - use 00:00:00 UTC for consistency
                    ts = datetime.strptime(date_str, '%Y-%m-%d')
                    ts = UTC.localize(ts)  # Make timezone-aware (UTC)
                    
                    # Extract rate value (first element of observation array)
                    if not obs_value or not isinstance(obs_value, list):
                        logger.warning(f"Invalid observation value: {obs_value}")
                        continue
                    
                    rate_value = obs_value[0]
                    if rate_value is None:
                        logger.warning(f"No rate value for {currency} on {date_str}")
                        continue
                    
                    rate = Decimal(str(rate_value))
                    
                    # Build standardized rate data
                    rate_data = {
                        'pair': f'EUR/{currency}',
                        'rate': rate,
                        'base_currency': 'EUR',
                        'quote_currency': currency,
                        'ts': ts,
                        'source': 'ecb'
                    }
                    
                    all_rates.append(rate_data)
                    logger.debug(
                        f"Parsed ECB rate: {rate_data['pair']} = {rate_data['rate']:.6f} "
                        f"on {ts.date()}"
                    )
                    
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse observation {obs_index_str}: {e}")
                    continue
        
        if not all_rates:
            logger.warning("No rates extracted from ECB response")
            return None
        
        logger.info(f"Successfully parsed {len(all_rates)} rates from ECB response")
        return all_rates
        
    except Exception as e:
        logger.error(f"Failed to parse ECB response: {e}", exc_info=True)
        return None
