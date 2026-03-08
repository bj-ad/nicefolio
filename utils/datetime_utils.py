"""
Datetime utility functions for consistent UTC timezone handling.

Following UTC standardization convention:
- All timestamps must be timezone-aware using UTC
- Use now_utc() instead of datetime.now()
- Use to_utc() to convert naive datetimes to UTC-aware
"""

import pytz
from datetime import datetime, date, timedelta
from typing import Optional

# UTC timezone constant
UTC = pytz.UTC


def now_utc() -> datetime:
    """
    Get current time in UTC (timezone-aware).
    
    Returns:
        datetime: Current UTC time with timezone info
        
    Example:
        >>> timestamp = now_utc()
        >>> timestamp.tzinfo  # pytz.UTC
    """
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """
    Convert naive or aware datetime to UTC-aware datetime.
    
    Args:
        dt: Datetime to convert (naive or aware)
        
    Returns:
        datetime: UTC-aware datetime
        
    Example:
        >>> naive_dt = datetime(2025, 11, 15, 10, 0, 0)
        >>> utc_dt = to_utc(naive_dt)
        >>> utc_dt.tzinfo  # pytz.UTC
    """
    if dt.tzinfo is None:
        # Naive datetime - assume UTC and localize
        return UTC.localize(dt)
    # Already aware - convert to UTC
    return dt.astimezone(UTC)


def utc_date_range(days_back: int, end_date: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """
    Calculate UTC datetime range from days_back to now (or end_date).
    
    Args:
        days_back: Number of days to look back
        end_date: End datetime (defaults to now_utc())
        
    Returns:
        tuple[datetime, datetime]: (start_datetime, end_datetime) both in UTC
        
    Example:
        >>> start, end = utc_date_range(7)  # Last 7 days
        >>> start < end  # True
    """
    if end_date is None:
        end_date = now_utc()
    else:
        end_date = to_utc(end_date)
    
    start_date = end_date - timedelta(days=days_back)
    return start_date, end_date


def today_utc() -> date:
    """
    Get today's date in UTC.
    
    Returns:
        date: Today's date based on UTC timezone
        
    Example:
        >>> today = today_utc()
    """
    return now_utc().date()
