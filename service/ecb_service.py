"""
ECB (European Central Bank) FX Rate Service

Fetches official EUR reference rates from ECB for German tax compliance.
ECB publishes daily rates at 16:00 CET on working days (Monday-Friday).

Tax Compliance Notes:
- German tax law (§ 20 EStG) requires using official ECB rates for foreign currency gains
- ECB rates are the legally binding reference for EUR conversions
- Rates are published at 4pm CET and represent the previous day's rate
- No rates on weekends/holidays - use previous working day rate

Timezone Handling:
- All timestamps stored in UTC (database timestamps are UTC)
- ECB publishes at 16:00 CET (15:00 UTC winter, 14:00 UTC summer due to DST)
- Safety checks prevent using same-day rates before 16:00 CET

API Documentation:
https://data.ecb.europa.eu/help/api/overview
https://data.ecb.europa.eu/help/api/data
"""

import requests
from datetime import datetime, timedelta
from typing import Optional, List
import pytz
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE
from crud.parsers.ecb_parser import parse_ecb_rates
from utils.api_client import make_api_call

logger = get_logger("ecb_service")

# ECB API endpoint for daily reference rates
# D = daily frequency
# ..USD+THB = filter for USD and THB
ECB_API_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+THB.EUR.SP00.A"

# ECB publication time: 16:00 CET + 1 hour buffer = 17:00 CET
# (16:00 UTC winter / 15:00 UTC summer + 1 hour buffer)
ECB_PUBLICATION_HOUR_CET = 17

# Timezones
UTC = pytz.UTC
CET = pytz.timezone('Europe/Berlin')  # CET/CEST with automatic DST handling

def get_safe_ecb_fetch_date() -> Optional[datetime]:
    """
    Get safe date for ECB rate fetch with 17:00 CET publication check.
    
    CRITICAL FOR TAX COMPLIANCE:
    ECB publishes rates at 16:00 CET. We use 17:00 CET buffer to ensure rates are available.
    
    Rules:
    - Before 17:00 CET on working day: Return None (skip - rate not published yet)
    - After 17:00 CET on working day: Return today's date (rate is published)
    - Weekend: Return Friday's date (last working day)
    
    This ensures:
    1. We never fetch/store unpublished rates (compliance)
    2. Transactions on same day before 17:00 are skipped and processed next day
    3. High quality data only in database
    
    Returns:
        Optional[datetime]: Safe date to fetch rates for (UTC timezone), or None if too early
    """
    # Get current time in CET (handles DST automatically)
    now_cet = datetime.now(CET)
    now_utc = datetime.now(UTC)
    
    logger.debug(f"Current time: {now_cet.strftime('%Y-%m-%d %H:%M:%S %Z')} (CET)")
    logger.debug(f"Current time: {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} (UTC)")
    
    # Check if weekend
    if now_cet.weekday() >= 5:  # Saturday (5) or Sunday (6)
        # Weekend - use Friday (last working day)
        days_back = now_cet.weekday() - 4  # Friday is day 4
        safe_date = (now_utc - timedelta(days=days_back)).date()
        logger.info(
            f"📅 Weekend - using Friday {safe_date} "
            f"(current: {now_cet.strftime('%A')})"
        )
        return datetime.combine(safe_date, datetime.min.time()).replace(tzinfo=UTC)
    
    # Working day - check if before 17:00 CET
    if now_cet.hour < ECB_PUBLICATION_HOUR_CET:
        # Too early - rate not published yet
        # Return None to signal: skip today, process next day
        logger.warning(
            f"⏰ Before 17:00 CET - ECB rate not published yet. "
            f"Skipping today's sync (current: {now_cet.strftime('%H:%M')} CET). "
            f"Today's transactions will be processed in next sync."
        )
        return None
    
    # Working day after 17:00 CET - use today
    safe_date = now_utc.date()
    logger.info(f"✅ Working day after 17:00 CET - using today {safe_date}")
    return datetime.combine(safe_date, datetime.min.time()).replace(tzinfo=UTC)


@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_ecb_rates(date: Optional[datetime] = None, enforce_safety_check: bool = True) -> Optional[List[dict]]:
    """
    Fetch official EUR reference rates from ECB (cacheable API call only).
    
    ECB publishes rates at 16:00 CET on working days.
    Returns rates for EUR/USD and EUR/THB.
    
    Args:
        date: Optional date to fetch historical rates for (defaults to safe current date)
        enforce_safety_check: If True, enforces 4pm CET safety check for same-day rates
    
    Returns:
        List[dict]: Parsed rate data with keys:
            - pair: str (e.g., "EUR/USD")
            - rate: Decimal
            - base_currency: str ("EUR")
            - quote_currency: str (e.g., "USD")
            - ts: datetime (UTC timezone)
            - source: str ("ecb")
        Returns None if fetch fails
    
    Safety Check:
        When enforce_safety_check=True (default), prevents fetching same-day rates
        before 16:00 CET to avoid missing fresh ECB publication. Transactions
        created before 16:00 CET will automatically use previous working day rate.
    
    Notes:
        - ECB rates are expressed as 1 EUR = X foreign currency
        - Example: EUR/USD = 1.0850 means 1 EUR = 1.0850 USD
        - For transaction conversion: amount_eur = amount_usd / ecb_rate
        - All timestamps returned in UTC for consistency
    """
    logger.info("Fetching official EUR reference rates from ECB")
    
    # Apply safety check if no explicit date provided
    if date is None and enforce_safety_check:
        date = get_safe_ecb_fetch_date()
        logger.info(f"🔒 Safety check applied: Using date {date.date()} (UTC)")
    
    # Build URL with optional date filter
    url = ECB_API_URL
    if date:
        # Format: YYYY-MM-DD
        date_str = date.strftime('%Y-%m-%d')
        url += f"?startPeriod={date_str}&endPeriod={date_str}"
    else:
        # Get latest available rate (last 2 days to handle weekends)
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=2)
        url += f"?startPeriod={start_date.strftime('%Y-%m-%d')}&endPeriod={end_date.strftime('%Y-%m-%d')}"
    
    # Add format parameter for JSON response
    url += "&format=jsondata"
    
    try:
        logger.debug(f"ECB API request: {url}")
        response = make_api_call(url, method="GET", timeout=10)
        
        if response:
            rates = parse_ecb_rates(response)
            if rates:
                logger.info(f"Successfully fetched {len(rates)} ECB rates")
                for rate in rates:
                    logger.info(
                        f"  {rate['pair']}: {rate['rate']:.6f} "
                        f"(date: {rate['ts'].date()}, source: {rate['source']})"
                    )
                return rates
            else:
                logger.warning("ECB API returned data but parser found no rates")
                return None
        else:
            logger.warning("ECB API returned no data")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching ECB rates: {e}", exc_info=True)
        return None


def get_fallback_rate_from_db(db, pair: str, target_date: datetime) -> Optional[dict]:
    """
    Get fallback FX rate from database (previous working day).
    
    Used when ECB API fails or no rate available for target date (weekend/holiday).
    
    Args:
        db: Database session
        pair: Currency pair (e.g., "EUR/USD")
        target_date: Date we need rate for
    
    Returns:
        dict: Rate data with keys:
            - pair: str
            - rate: Decimal
            - base_currency: str
            - quote_currency: str
            - ts: datetime (of fallback rate, NOT target date)
            - source: str
            - fallback_note: str (for transaction notes field)
        Returns None if no fallback available
    """
    from models import FxRate
    from sqlalchemy import desc
    
    # Query most recent rate before target date
    fallback_rate = db.query(FxRate).filter(
        FxRate.pair == pair,
        FxRate.as_of_date <= target_date,
        FxRate.source == 'ecb'  # Only use ECB rates for tax compliance
    ).order_by(desc(FxRate.as_of_date)).first()
    
    if not fallback_rate:
        logger.error(f"No ECB fallback rate found for {pair} before {target_date.date()}")
        return None
    
    days_diff = (target_date.date() - fallback_rate.as_of_date.date()).days
    
    fallback_note = f"FX rate fallback: used {fallback_rate.as_of_date.date()} rate ({days_diff} days old)"
    
    logger.info(
        f"Using fallback rate for {pair}: {fallback_rate.rate:.6f} "
        f"from {fallback_rate.as_of_date.date()} (target: {target_date.date()}, {days_diff} days old)"
    )
    
    return {
        'pair': fallback_rate.pair,
        'rate': fallback_rate.rate,
        'base_currency': fallback_rate.base_currency,
        'quote_currency': fallback_rate.quote_currency,
        'as_of_date': fallback_rate.as_of_date,
        'source': 'ecb_fallback',
        'fallback_note': fallback_note
    }
