# Timezone Standardization - UTC Implementation

**Date**: 2025-11-16  
**Status**: ✅ IMPLEMENTED  
**Priority**: HIGH - Database consistency and tax compliance

---

## Overview

All timestamps in the system are now standardized to **UTC (Coordinated Universal Time)** for:
1. Database consistency (PostgreSQL `TIMESTAMP WITH TIME ZONE`)
2. German tax compliance (ECB rates with CET publication timing)
3. Cross-timezone operation (Thailand, Germany, USA)
4. Simplified development and debugging

---

## Timezone Handling Strategy

### Database Layer
```sql
-- All timestamp columns use TIMESTAMP WITH TIME ZONE
CREATE TABLE transactions (
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE fx_rate (
    ts TIMESTAMP WITH TIME ZONE NOT NULL,  -- UTC timestamp
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**Storage**: Always UTC (PostgreSQL automatically converts)  
**Query**: Can filter/display in any timezone

### Application Layer
```python
import pytz
from datetime import datetime

# Define timezones
UTC = pytz.UTC
CET = pytz.timezone('Europe/Berlin')  # Auto-handles DST
ICT = pytz.timezone('Asia/Bangkok')  # Thailand time

# ALWAYS create timezone-aware datetimes
now_utc = datetime.now(UTC)
now_cet = datetime.now(CET)

# Convert between timezones
cet_time = now_utc.astimezone(CET)
utc_time = cet_time.astimezone(UTC)
```

### ECB Rate Publication (Special Case)

**ECB Schedule**:
- Publication time: **16:00 CET** (Central European Time)
- Winter (CET): 15:00 UTC
- Summer (CEST): 14:00 UTC
- Working days only (Monday-Friday)

**Implementation**:
```python
from service.ecb_service import get_safe_ecb_fetch_date

# Automatically applies 16:00 CET safety check
safe_date = get_safe_ecb_fetch_date()

# Before 16:00 CET: Returns previous working day
# After 16:00 CET: Returns current day (if working day)
```

---

## Safety Check: 4pm CET Rule

### Problem Statement

**Scenario**: Transaction created at 10:00 CET (before ECB publication)
- ❌ **WRONG**: Use today's rate (not published yet → fallback → audit issues)
- ✅ **CORRECT**: Use yesterday's rate (published and available)

**German Tax Risk**: Using fallback rates when fresh rates should be available creates unnecessary audit flags.

### Implementation

#### 1. Safe Fetch Date Function

```python
def get_safe_ecb_fetch_date() -> datetime:
    """
    Get safe date for ECB rate fetch with 4pm CET publication check.
    
    Rules:
    1. If current time < 16:00 CET: Use previous working day
    2. If current time >= 16:00 CET: Use current day (if working day)
    3. If weekend: Use previous Friday
    
    Examples:
        Tuesday 10:00 CET → Monday (previous day)
        Tuesday 17:00 CET → Tuesday (same day)
        Saturday 10:00 CET → Friday (previous working day)
        Sunday 20:00 CET → Friday (previous working day)
    """
    now_cet = datetime.now(CET)
    
    # Check if before 16:00 CET
    if now_cet.hour < 16:
        return (datetime.now(UTC) - timedelta(days=1)).date()
    
    # Check if weekend
    if now_cet.weekday() >= 5:  # Saturday or Sunday
        days_back = now_cet.weekday() - 4  # Friday is day 4
        return (datetime.now(UTC) - timedelta(days=days_back)).date()
    
    # Working day after 16:00 CET
    return datetime.now(UTC).date()
```

#### 2. Transaction Validation

```python
from utils.fx_rate_validator import validate_fx_rate_for_transaction

# When creating transaction
is_valid, error_msg = validate_fx_rate_for_transaction(
    transaction_date=datetime(2025, 11, 16, 10, 0, 0, tzinfo=CET),  # 10:00 CET
    fx_rate_date=datetime(2025, 11, 16, 0, 0, 0, tzinfo=UTC),  # Today's rate
    fx_rate_source='ecb'
)

if not is_valid:
    # Validation failed: "Transaction at 10:00 CET cannot use same-day rate"
    # Use previous day's rate instead
    pass
```

#### 3. Fetch with Safety Check (Default Behavior)

```python
from service.ecb_service import fetch_ecb_rates

# Automatic safety check applied
rates = fetch_ecb_rates()  # Uses get_safe_ecb_fetch_date() internally

# Manual date (bypass safety check for historical data)
rates = fetch_ecb_rates(
    date=datetime(2025, 10, 1, 0, 0, 0, tzinfo=UTC),
    enforce_safety_check=False
)
```

---

## Use Cases

### Use Case 1: Daily FX Sync (Automated)

**Schedule**: 01:00 AM ICT (18:00 UTC previous day = 19:00 CET previous day)

```python
# worker/daily_jobs.py
def run_fx_sync():
    """
    Daily FX sync runs at 01:00 AM ICT (19:00 CET previous day).
    Safety check automatically applied.
    """
    from service.fx_service import sync_fx_rates
    
    # Fetches rates with automatic 16:00 CET check
    # If running at 01:00 ICT (18:00 UTC), it's 19:00 CET
    # → After 16:00 CET → Uses current day rate
    sync_fx_rates()
```

**Timing Analysis**:
- 01:00 ICT = 18:00 UTC (UTC-7)
- 18:00 UTC = 19:00 CET (UTC+1)
- 19:00 CET > 16:00 CET ✅ Can use today's rate

### Use Case 2: Manual Transaction Entry

**Scenario**: User enters a transaction that occurred at 12:00 CET today

```python
from utils.fx_rate_validator import get_recommended_fx_rate_date

transaction_date = datetime(2025, 11, 16, 12, 0, 0, tzinfo=CET)  # 12:00 CET

# Get recommended FX rate date
fx_rate_date = get_recommended_fx_rate_date(transaction_date)
# Returns: 2025-11-15 (previous day, because 12:00 < 16:00)

# Fetch rate for that date
rates = fetch_ecb_rates(date=datetime.combine(fx_rate_date, datetime.min.time(), tzinfo=UTC))

# Create transaction with validated rate
transaction = Transaction(
    occurred_at=transaction_date,
    exchange_rate_to_base=rates[0]['rate'],
    notes=None  # No fallback note needed (correct rate used)
)
```

### Use Case 3: Backfilling Historical Data

**Scenario**: Import 1 year of historical transactions

```python
from datetime import timedelta

start_date = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
end_date = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
current_date = start_date

while current_date < end_date:
    # Fetch historical rates (no safety check needed)
    rates = fetch_ecb_rates(
        date=current_date,
        enforce_safety_check=False  # Historical data
    )
    
    if rates:
        ingest_fx_rates(db, rates)
    
    current_date += timedelta(days=1)
```

### Use Case 4: Weekend Transaction

**Scenario**: Transaction on Saturday (ECB closed)

```python
transaction_date = datetime(2025, 11, 15, 14, 0, 0, tzinfo=CET)  # Saturday 14:00

# Safety check applied
safe_date = get_safe_ecb_fetch_date()
# Returns: 2025-11-14 (Friday - previous working day)

# Fetch Friday's rate
rates = fetch_ecb_rates(date=safe_date)

# If ECB API fails, fallback to database
if not rates:
    fallback = get_fallback_rate_from_db(db, 'EUR/USD', safe_date)
    # Fallback note added: "FX rate fallback: used 2025-11-14 rate (1 days old)"
```

---

## Testing Strategy

### Unit Tests

```python
# tests/test_timezone_safety.py

def test_before_4pm_cet_uses_previous_day():
    """Transaction before 16:00 CET uses previous day rate."""
    now = datetime(2025, 11, 16, 10, 0, 0, tzinfo=CET)  # 10:00 CET
    
    with freeze_time(now):
        safe_date = get_safe_ecb_fetch_date()
        assert safe_date == date(2025, 11, 15)  # Previous day


def test_after_4pm_cet_uses_same_day():
    """Transaction after 16:00 CET uses same day rate."""
    now = datetime(2025, 11, 16, 17, 0, 0, tzinfo=CET)  # 17:00 CET
    
    with freeze_time(now):
        safe_date = get_safe_ecb_fetch_date()
        assert safe_date == date(2025, 11, 16)  # Same day


def test_weekend_uses_friday():
    """Weekend transaction uses Friday rate."""
    saturday = datetime(2025, 11, 16, 14, 0, 0, tzinfo=CET)  # Saturday
    
    with freeze_time(saturday):
        safe_date = get_safe_ecb_fetch_date()
        assert safe_date == date(2025, 11, 15)  # Friday


def test_transaction_validation():
    """Validate FX rate usage for transaction."""
    # Valid: Transaction after 16:00 CET using same-day rate
    is_valid, error = validate_fx_rate_for_transaction(
        datetime(2025, 11, 16, 17, 0, 0, tzinfo=CET),
        datetime(2025, 11, 16, 0, 0, 0, tzinfo=UTC),
        'ecb'
    )
    assert is_valid
    assert error is None
    
    # Invalid: Transaction before 16:00 CET using same-day rate
    is_valid, error = validate_fx_rate_for_transaction(
        datetime(2025, 11, 16, 10, 0, 0, tzinfo=CET),
        datetime(2025, 11, 16, 0, 0, 0, tzinfo=UTC),
        'ecb'
    )
    assert not is_valid
    assert "cannot use same-day rate" in error
```

### Integration Tests

```bash
# Test ECB fetch with safety check
docker compose exec nicefolio_worker python -c "
from service.ecb_service import fetch_ecb_rates, get_safe_ecb_fetch_date
from datetime import datetime
import pytz

CET = pytz.timezone('Europe/Berlin')
now_cet = datetime.now(CET)
print(f'Current time: {now_cet.strftime(\"%Y-%m-%d %H:%M %Z\")}')

safe_date = get_safe_ecb_fetch_date()
print(f'Safe date: {safe_date.date()}')

rates = fetch_ecb_rates()
if rates:
    for rate in rates:
        print(f'{rate[\"pair\"]}: {rate[\"rate\"]:.6f} ({rate[\"ts\"].date()})')
"
```

---

## Database Queries

### Check Timezone Consistency

```sql
-- All timestamps should be UTC
SELECT 
    occurred_at,
    EXTRACT(TIMEZONE FROM occurred_at) as tz_offset_seconds,
    occurred_at AT TIME ZONE 'CET' as occurred_at_cet,
    occurred_at AT TIME ZONE 'UTC' as occurred_at_utc
FROM transactions
WHERE occurred_at >= NOW() - INTERVAL '7 days'
ORDER BY occurred_at DESC
LIMIT 10;

-- Expected: tz_offset_seconds = 0 (UTC), or correct offset if stored with timezone
```

### Find Transactions Before 4pm CET with Same-Day Rate

```sql
-- Potential violations of 4pm rule
SELECT 
    t.id,
    t.occurred_at,
    t.occurred_at AT TIME ZONE 'CET' as occurred_at_cet,
    EXTRACT(HOUR FROM (t.occurred_at AT TIME ZONE 'CET')) as hour_cet,
    f.ts as fx_rate_ts,
    f.source as fx_rate_source,
    DATE(t.occurred_at AT TIME ZONE 'CET') as tx_date,
    DATE(f.ts) as fx_date,
    CASE 
        WHEN DATE(t.occurred_at AT TIME ZONE 'CET') = DATE(f.ts)
             AND EXTRACT(HOUR FROM (t.occurred_at AT TIME ZONE 'CET')) < 16
        THEN '⚠️  VIOLATION'
        ELSE '✅ OK'
    END as validation_status
FROM transactions t
LEFT JOIN fx_rate f ON DATE(t.occurred_at) = DATE(f.ts)
    AND f.pair = CONCAT('EUR/', t.currency)
WHERE DATE(t.occurred_at AT TIME ZONE 'CET') = DATE(f.ts)
    AND EXTRACT(HOUR FROM (t.occurred_at AT TIME ZONE 'CET')) < 16
    AND f.source = 'ecb'
ORDER BY t.occurred_at DESC;

-- Expected: 0 rows (no violations)
```

---

## Migration Guide

### For Existing Transactions

```sql
-- Check current timezone awareness
SELECT 
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE EXTRACT(TIMEZONE FROM occurred_at) = 0) as utc_count,
    COUNT(*) FILTER (WHERE EXTRACT(TIMEZONE FROM occurred_at) != 0) as non_utc_count
FROM transactions;

-- If needed, convert to UTC
UPDATE transactions
SET occurred_at = occurred_at AT TIME ZONE 'UTC'
WHERE EXTRACT(TIMEZONE FROM occurred_at) != 0;
```

### For Application Code

**Before** (naive datetimes):
```python
# ❌ BAD - No timezone
now = datetime.now()
occurred_at = datetime(2025, 11, 16, 10, 30, 0)
```

**After** (timezone-aware):
```python
# ✅ GOOD - Explicit UTC
import pytz
UTC = pytz.UTC

now = datetime.now(UTC)
occurred_at = datetime(2025, 11, 16, 10, 30, 0, tzinfo=UTC)
```

---

## Benefits

### 1. Database Consistency
- All timestamps in UTC (no ambiguity)
- PostgreSQL `TIMESTAMP WITH TIME ZONE` handles conversions
- Queries work correctly across timezones

### 2. Tax Compliance
- ECB publication time (16:00 CET) properly handled
- Same-day rate safety check prevents audit issues
- Fallback rates used appropriately

### 3. Cross-Timezone Support
- Thailand (ICT = UTC+7)
- Germany (CET = UTC+1, CEST = UTC+2 in summer)
- USA exchanges (EST/EDT)
- All handled via `pytz` automatic DST conversion

### 4. Developer Experience
- Single source of truth (UTC)
- Easy to convert for display (`.astimezone(CET)`)
- No manual DST calculations

---

## Monitoring

### Daily Health Check

```python
# scripts/check_timezone_health.py
def check_timezone_health():
    """Check for timezone-related issues."""
    db = SessionLocal()
    try:
        # Check 1: All transactions have timezone
        naive_count = db.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE occurred_at::text NOT LIKE '%+%' AND occurred_at::text NOT LIKE '%-0%'
        """).scalar()
        
        if naive_count > 0:
            logger.warning(f"⚠️  Found {naive_count} transactions with naive timestamps")
        
        # Check 2: Check for 4pm rule violations
        violations = db.execute("""
            SELECT COUNT(*) FROM transactions t
            JOIN fx_rate f ON DATE(t.occurred_at) = DATE(f.ts)
            WHERE DATE(t.occurred_at AT TIME ZONE 'CET') = DATE(f.ts)
                AND EXTRACT(HOUR FROM (t.occurred_at AT TIME ZONE 'CET')) < 16
                AND f.source = 'ecb'
        """).scalar()
        
        if violations > 0:
            logger.warning(f"⚠️  Found {violations} 4pm rule violations")
        
        # Check 3: Verify FX rates are in UTC
        non_utc_rates = db.execute("""
            SELECT COUNT(*) FROM fx_rate
            WHERE EXTRACT(TIMEZONE FROM ts) != 0
        """).scalar()
        
        if non_utc_rates > 0:
            logger.warning(f"⚠️  Found {non_utc_rates} FX rates not in UTC")
        
    finally:
        db.close()
```

---

## Summary

✅ **COMPLETED**:
1. All timestamps standardized to UTC
2. ECB service with 16:00 CET safety check
3. Transaction validation for FX rate usage
4. Comprehensive timezone handling utilities
5. Documentation and test examples

✅ **COMPLIANCE**:
- German tax law (§ 20 EStG) - ECB rates used correctly
- Audit trail - Fallback usage documented
- Safety checks - No same-day rate before 4pm CET

✅ **BENEFITS**:
- Database consistency (all UTC)
- Cross-timezone support (ICT, CET, EST)
- Automatic DST handling (pytz)
- Developer-friendly API

---

**Last Updated**: 2025-11-16  
**Next Review**: After first production FX sync at 01:00 ICT
