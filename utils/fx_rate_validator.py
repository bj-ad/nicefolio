"""
FX Rate Validator - German Tax Compliance

Validates FX rate usage for transactions to ensure compliance with:
1. ECB publication timing (16:00 CET + 1 hour buffer = 17:00 CET)
2. German tax law (§ 20 EStG) - must use official ECB rates

Safety Checks:
- Prevents using same-day FX rates before 17:00 CET
- Ensures fallback rates are used appropriately
- Validates transaction timestamps against rate availability
- Transactions before 17:00 CET are skipped and processed next day
"""

from datetime import datetime, date
from typing import Optional, Tuple
import pytz
from decimal import Decimal
from utils.logging_config import get_logger

logger = get_logger("fx_rate_validator")

# Timezones
UTC = pytz.UTC
CET = pytz.timezone('Europe/Berlin')  # CET/CEST with automatic DST handling

# ECB publication time: 16:00 CET + 1 hour buffer = 17:00 CET
ECB_PUBLICATION_HOUR_CET = 17


def validate_fx_rate_for_transaction(
    transaction_date: datetime,
    fx_rate_date: datetime,
    fx_rate_source: str,
    allow_same_day: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Validate FX rate usage for a transaction.
    
    German tax compliance requires:
    1. ECB rates published at 16:00 CET (we use 17:00 CET buffer)
    2. Transactions before 17:00 CET must use previous day's rate OR be skipped
    3. Fallback rates allowed when ECB unavailable
    
    Args:
        transaction_date: Transaction occurred_at timestamp
        fx_rate_date: FX rate timestamp from database
        fx_rate_source: 'ecb' or 'ecb_fallback'
        allow_same_day: If True, allows same-day rate (for testing/backfill)
    
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
            - (True, None): Valid
            - (False, "error message"): Invalid with reason
    
    Examples:
        # Valid: Transaction at 18:00 CET using today's ECB rate
        >>> validate_fx_rate_for_transaction(
        ...     datetime(2025, 11, 16, 18, 0, 0, tzinfo=CET),
        ...     datetime(2025, 11, 16, 0, 0, 0, tzinfo=UTC),
        ...     'ecb'
        ... )
        (True, None)
        
        # Invalid: Transaction at 10:00 CET trying to use today's rate
        >>> validate_fx_rate_for_transaction(
        ...     datetime(2025, 11, 16, 10, 0, 0, tzinfo=CET),
        ...     datetime(2025, 11, 16, 0, 0, 0, tzinfo=UTC),
        ...     'ecb'
        ... )
        (False, "Transaction at 10:00 CET cannot use same-day rate (ECB publishes at 17:00 CET)")
    """
    # Ensure timezone-aware datetimes
    if transaction_date.tzinfo is None:
        logger.warning("Transaction date has no timezone - assuming UTC")
        transaction_date = UTC.localize(transaction_date)
    
    if fx_rate_date.tzinfo is None:
        logger.warning("FX rate date has no timezone - assuming UTC")
        fx_rate_date = UTC.localize(fx_rate_date)
    
    # Convert to CET for publication time check
    tx_date_cet = transaction_date.astimezone(CET)
    
    # Get dates (ignore time component)
    tx_date_only = tx_date_cet.date()
    fx_rate_date_only = fx_rate_date.date()
    
    # Check if using same-day rate
    if tx_date_only == fx_rate_date_only:
        # Same-day rate - check if transaction is after 17:00 CET
        if tx_date_cet.hour < ECB_PUBLICATION_HOUR_CET and not allow_same_day:
            error_msg = (
                f"⚠️  Transaction at {tx_date_cet.strftime('%H:%M')} CET "
                f"cannot use same-day rate (ECB publishes at {ECB_PUBLICATION_HOUR_CET}:00 CET). "
                f"Transaction should be skipped and processed in next sync."
            )
            logger.warning(error_msg)
            return False, error_msg
        
        logger.debug(
            f"✅ Same-day rate valid: Transaction at {tx_date_cet.strftime('%H:%M')} CET "
            f"(after {ECB_PUBLICATION_HOUR_CET}:00 CET)"
        )
        return True, None
    
    # Check if rate is from the past (normal case)
    if fx_rate_date_only < tx_date_only:
        # Using historical rate - always valid
        days_diff = (tx_date_only - fx_rate_date_only).days
        
        if fx_rate_source == 'ecb_fallback':
            logger.debug(
                f"✅ Using fallback rate from {fx_rate_date_only} "
                f"({days_diff} days before transaction)"
            )
        else:
            logger.debug(
                f"✅ Using ECB rate from {fx_rate_date_only} "
                f"({days_diff} days before transaction)"
            )
        return True, None
    
    # Rate is from the future (should never happen)
    error_msg = (
        f"❌ FX rate date {fx_rate_date_only} is AFTER transaction date {tx_date_only}. "
        f"Cannot use future rate for past transaction."
    )
    logger.error(error_msg)
    return False, error_msg


def is_transaction_safe_for_same_day_rate(transaction_date: datetime) -> bool:
    """
    Check if transaction can use same-day ECB rate.
    
    Transaction must be after 16:00 CET to use same-day rate.
    
    Args:
        transaction_date: Transaction occurred_at timestamp
    
    Returns:
        bool: True if safe to use same-day rate, False if must use previous day
    """
    if transaction_date.tzinfo is None:
        transaction_date = UTC.localize(transaction_date)
    
    tx_date_cet = transaction_date.astimezone(CET)
    
    is_safe = tx_date_cet.hour >= ECB_PUBLICATION_HOUR_CET
    
    if not is_safe:
        logger.info(
            f"⏰ Transaction at {tx_date_cet.strftime('%Y-%m-%d %H:%M')} CET "
            f"is before {ECB_PUBLICATION_HOUR_CET}:00 CET - use previous day rate"
        )
    
    return is_safe


def get_recommended_fx_rate_date(transaction_date: datetime) -> date:
    """
    Get recommended FX rate date for transaction.
    
    Applies 16:00 CET safety rule:
    - Transaction >= 16:00 CET: Use same day
    - Transaction < 16:00 CET: Use previous day
    
    Args:
        transaction_date: Transaction occurred_at timestamp
    
    Returns:
        date: Recommended date to fetch FX rate for
    """
    if transaction_date.tzinfo is None:
        transaction_date = UTC.localize(transaction_date)
    
    tx_date_cet = transaction_date.astimezone(CET)
    
    if tx_date_cet.hour >= ECB_PUBLICATION_HOUR_CET:
        # After 17:00 CET - use same day
        return tx_date_cet.date()
    else:
        # Before 17:00 CET - use previous day
        from datetime import timedelta
        return (tx_date_cet - timedelta(days=1)).date()
