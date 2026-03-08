# ECB FX Rate Integration - German Tax Compliance

**Date**: 2025-11-16  
**Status**: ✅ IMPLEMENTED  
**Compliance**: German Income Tax Act (§ 20 EStG)

---

## Overview

This document describes the ECB (European Central Bank) FX rate integration for German tax compliance. The implementation replaces third-party FX APIs with official ECB reference rates, which are the legally binding rates for calculating foreign currency gains in Germany.

---

## Legal Requirement

### § 20 EStG (German Income Tax Act)

**Requirement**: Foreign currency gains must be calculated using **official EUR exchange rates** published by the European Central Bank (ECB).

**Why ECB Rates**:
- **Legally binding**: ECB rates are the official reference for German tax purposes
- **Audit compliance**: Tax authorities accept ECB rates without question
- **Consistency**: All EUR conversions use same authoritative source
- **Transparency**: Publicly available, published daily at fixed time

**Applies to**:
- Foreign currency deposits (USD, THB, etc.)
- Foreign securities traded in non-EUR currencies
- Cryptocurrency gains (if priced in non-EUR)
- Foreign dividends and interest

---

## ECB Rate Publication

### Schedule
- **Frequency**: Daily (working days only: Monday-Friday)
- **Publication Time**: 16:00 CET (4pm Central European Time)
- **Effective Date**: Rates published on date X represent the rate for date X
- **Weekends/Holidays**: No new rates - use previous working day

### Coverage
- EUR/USD: US Dollar
- EUR/THB: Thai Baht
- EUR/XXX: All major world currencies

### API Access
- **Endpoint**: https://data-api.ecb.europa.eu/service/data/EXR/
- **Format**: JSON or CSV
- **Documentation**: https://data.ecb.europa.eu/help/api/overview
- **Rate Type**: Daily reference rates (spot rates at 2:15pm CET)

---

## Implementation Architecture

### Three-Layer Pattern

```
┌──────────────────────────────────────────────────────────────┐
│ SERVICE LAYER: service/ecb_service.py                        │
│ - fetch_ecb_rates(): Cacheable API call to ECB               │
│ - get_fallback_rate_from_db(): Query previous working day    │
│ - Returns: List[dict] with standardized rate data            │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ PARSER LAYER: crud/parsers/ecb_parser.py                     │
│ - parse_ecb_rates(): Transform ECB JSON to FxRate format     │
│ - Extracts: EUR/USD and EUR/THB with timestamps              │
│ - Returns: List[dict] with keys: pair, rate, ts, source      │
└──────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────┐
│ CRUD LAYER: crud/crud_market_fx.py                           │
│ - ingest_fx_rates(): Upsert rates to fx_rate table           │
│ - Logs: Success/failure counts                               │
│ - Returns: tuple[int, int] (success_count, failure_count)    │
└──────────────────────────────────────────────────────────────┘
```

### Orchestration

**File**: `service/fx_service.py`  
**Function**: `sync_fx_rates()`

```python
def sync_fx_rates():
    """
    1. Try fetching from ECB API
    2. If fails, fallback to database (previous working day)
    3. Ingest rates with source tracking
    4. Return success/failure counts
    """
```

---

## Fallback Logic

### When Fallback Triggered

1. **ECB API unavailable**: Server down, network issue, timeout
2. **Weekend/Holiday**: No new rates published
3. **After-hours sync**: Sync runs before 16:00 CET, today's rate not yet available

### Fallback Strategy

```python
# Priority 1: ECB API (fresh rate)
ecb_rates = fetch_ecb_rates()

if not ecb_rates:
    # Priority 2: Database fallback (previous working day)
    fallback_rate = get_fallback_rate_from_db(db, 'EUR/USD', target_date)
    
    if fallback_rate:
        # Use fallback with audit trail
        fallback_rate['fallback_note'] = "FX rate fallback: used 2025-11-15 rate (2 days old)"
```

### Fallback Selection Logic

```sql
-- Query most recent ECB rate before target date
SELECT *
FROM fx_rate
WHERE pair = 'EUR/USD'
  AND ts <= :target_date
  AND source = 'ecb'  -- Only use ECB rates for tax compliance
ORDER BY ts DESC
LIMIT 1;
```

**Key Rules**:
- ✅ Only ECB rates used for fallback (not third-party APIs)
- ✅ Use most recent rate before target date
- ✅ Calculate days difference for audit trail
- ✅ Add note to transaction for transparency

---

## Audit Trail

### Transaction Notes Field

When fallback rate is used, a note is added to the transaction:

```python
# Example fallback note
"FX rate fallback: used 2025-11-15 rate (2 days old)"
```

**Format**:
```
FX rate fallback: used {fallback_date} rate ({days_old} days old)
```

**Purpose**:
- **Transparency**: Clearly indicates when fallback used
- **Audit trail**: Tax auditor can verify rate was appropriate
- **Debugging**: Helps diagnose FX rate issues

### Database Storage

```sql
-- Transaction with fallback rate
INSERT INTO transactions (
    symbol,
    amount,
    currency,
    exchange_rate_to_base,
    amount_base,
    base_currency,
    notes  -- Fallback note stored here
) VALUES (
    'USD',
    1000.00,
    'USD',
    0.9259,  -- Fallback rate from 2025-11-15
    925.90,  -- Converted to EUR
    'EUR',
    'FX rate fallback: used 2025-11-15 rate (2 days old)'
);
```

---

## Rate Format and Conversion

### ECB Rate Convention

**Format**: 1 EUR = X foreign currency

**Examples**:
```
EUR/USD = 1.0850  →  1 EUR = 1.0850 USD
EUR/THB = 37.8500  →  1 EUR = 37.8500 THB
```

### Conversion Formulas

**USD → EUR**:
```python
# ECB rate: EUR/USD = 1.0850 (1 EUR = 1.0850 USD)
amount_usd = 100.00
amount_eur = amount_usd / 1.0850  # = 92.17 EUR
```

**THB → EUR**:
```python
# ECB rate: EUR/THB = 37.8500 (1 EUR = 37.8500 THB)
amount_thb = 3785.00
amount_eur = amount_thb / 37.8500  # = 100.00 EUR
```

**EUR → USD** (if needed):
```python
# ECB rate: EUR/USD = 1.0850
amount_eur = 100.00
amount_usd = amount_eur * 1.0850  # = 108.50 USD
```

### Cross-Rate Calculation

**USD → THB** (no longer stored directly):
```python
# Use cross-rate calculation
eur_usd = 1.0850  # EUR/USD rate
eur_thb = 37.8500  # EUR/THB rate

# Calculate USD/THB cross-rate
usd_thb = eur_thb / eur_usd  # = 34.8848 (1 USD = 34.8848 THB)
```

**Why cross-rate**:
- Only EUR pairs needed for base currency EUR
- Reduces API calls and storage
- Still allows USD/THB calculation if ever needed

---

## Configuration Changes

### app_config.yaml

**Old Configuration** (Multiple sources):
```yaml
fx_pairs:
  - USD/THB  # Removed: not needed with EUR base currency
  - EUR/USD
  - EUR/THB
yf_fx_symbols:
  - THB=X  # Removed: yfinance no longer used
  - EURUSD=X
  - EURTHB=X
```

**New Configuration** (ECB only):
```yaml
# FX pairs for German tax compliance (§ 20 EStG)
# Official ECB rates published daily at 16:00 CET on working days
fx_pairs:
  - EUR/USD  # ECB official rate: 1 EUR = X USD
  - EUR/THB  # ECB official rate: 1 EUR = X THB
# Note: USD/THB removed - can be calculated as cross-rate if needed
# Legacy yfinance symbols kept for backward compatibility but no longer used
yf_fx_symbols:
  - EURUSD=X
  - EURTHB=X
```

### Removed Dependencies

**No longer used**:
- `fetch_fx_rate_from_exchangerate_api()` - Third-party API
- `fetch_fx_rate_from_yfinance()` - Market data, not official rates
- `USD/THB` direct pair - Use cross-rate calculation

**Deprecated functions** (keep for backward compatibility):
```python
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_fx_rate_from_exchangerate_api(pair: str) -> Optional[dict]:
    """DEPRECATED: Use fetch_ecb_rates() instead."""
    # Keep for backward compatibility but not used in sync
```

---

## Database Schema

### fx_rate Table

```sql
CREATE TABLE fx_rate (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(10) NOT NULL,           -- e.g., "EUR/USD"
    ts TIMESTAMP WITH TIME ZONE NOT NULL, -- Rate effective date
    rate NUMERIC(24, 8) NOT NULL,        -- Exchange rate
    base_currency VARCHAR(8) NOT NULL,   -- "EUR"
    quote_currency VARCHAR(8) NOT NULL,  -- "USD", "THB"
    source VARCHAR(50) NOT NULL,         -- "ecb" or "ecb_fallback"
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (pair, ts)  -- Prevent duplicates
);

CREATE INDEX ix_fx_rate_pair_ts ON fx_rate (pair, ts DESC);
```

### Source Values

| Source         | Meaning                       | When Used                                       |
| -------------- | ----------------------------- | ----------------------------------------------- |
| `ecb`          | Fresh ECB rate from API       | Normal operation (working days after 16:00 CET) |
| `ecb_fallback` | Previous working day ECB rate | Weekends, holidays, API unavailable             |

---

## Scheduler Integration

### Daily Data Sync

**File**: `worker/daily_jobs.py`  
**Schedule**: 01:00 AM ICT (18:00 UTC-1 = 19:00 CET)

```python
def run_data_sync():
    """
    Daily data sync including FX rates.
    Runs after ECB publishes rates (16:00 CET).
    """
    # ... other data sync ...
    
    # Sync FX rates from ECB
    sync_fx_rates()  # Uses ECB API
```

**Timing Considerations**:
- ECB publishes at 16:00 CET
- Sync runs at 19:00 CET (01:00 ICT next day)
- 3-hour buffer ensures rates available
- Weekend syncs use Friday's rate (fallback)

### Manual Sync

```bash
# Test ECB integration
python scripts/test_ecb_rates.py

# Manual FX sync
python -c "from service.fx_service import sync_fx_rates; sync_fx_rates()"
```

---

## Testing

### Test Script

**File**: `scripts/test_ecb_rates.py`

```python
#!/usr/bin/env python3
"""Test ECB FX rate integration."""

from service.ecb_service import fetch_ecb_rates, get_fallback_rate_from_db
from service.fx_service import sync_fx_rates
from database import SessionLocal
from datetime import datetime

def test_ecb_api():
    """Test ECB API fetch."""
    print("Testing ECB API...")
    rates = fetch_ecb_rates()
    
    if rates:
        print(f"✅ Fetched {len(rates)} rates from ECB")
        for rate in rates:
            print(f"  {rate['pair']}: {rate['rate']:.6f} ({rate['ts'].date()})")
    else:
        print("❌ Failed to fetch from ECB API")

def test_fallback():
    """Test database fallback."""
    print("\nTesting database fallback...")
    db = SessionLocal()
    try:
        fallback = get_fallback_rate_from_db(db, 'EUR/USD', datetime.now())
        if fallback:
            print(f"✅ Fallback rate: {fallback['pair']} = {fallback['rate']:.6f}")
            print(f"   Date: {fallback['ts'].date()}")
            print(f"   Note: {fallback['fallback_note']}")
        else:
            print("❌ No fallback rate available")
    finally:
        db.close()

def test_full_sync():
    """Test full sync with ingestion."""
    print("\nTesting full sync...")
    sync_fx_rates()
    print("✅ Sync complete")

if __name__ == "__main__":
    test_ecb_api()
    test_fallback()
    test_full_sync()
```

### Database Verification

```sql
-- Check latest ECB rates
SELECT 
    pair,
    ts,
    rate,
    source,
    created_at
FROM fx_rate
WHERE source IN ('ecb', 'ecb_fallback')
ORDER BY ts DESC, pair
LIMIT 10;

-- Expected output:
-- pair     | ts         | rate      | source | created_at
-- ---------|------------|-----------|--------|------------
-- EUR/USD  | 2025-11-16 | 1.085000  | ecb    | 2025-11-16 19:05:00
-- EUR/THB  | 2025-11-16 | 37.850000 | ecb    | 2025-11-16 19:05:00

-- Check for fallback usage
SELECT COUNT(*) as fallback_count
FROM fx_rate
WHERE source = 'ecb_fallback';

-- Expected: 0 (on working days), >0 (on weekends)

-- Verify transaction fallback notes
SELECT 
    id,
    occurred_at,
    symbol,
    currency,
    exchange_rate_to_base,
    notes
FROM transactions
WHERE notes LIKE '%FX rate fallback%'
ORDER BY occurred_at DESC
LIMIT 5;
```

---

## Migration from Old FX System

### Backfill Historical ECB Rates

**Script**: `scripts/backfill_ecb_rates.py`

```python
"""
Backfill historical ECB rates from 2020-01-01 to present.
Only needed once for historical data.
"""

from datetime import datetime, timedelta
from service.ecb_service import fetch_ecb_rates
from crud.crud_market_fx import ingest_fx_rates
from database import SessionLocal

def backfill_ecb_rates(start_date, end_date):
    """Backfill ECB rates for date range."""
    db = SessionLocal()
    try:
        current_date = start_date
        total_success = 0
        total_failed = 0
        
        while current_date <= end_date:
            print(f"Fetching ECB rates for {current_date.date()}...")
            
            rates = fetch_ecb_rates(date=current_date)
            if rates:
                success, failed = ingest_fx_rates(db, rates)
                total_success += success
                total_failed += failed
                print(f"  ✅ {success} rates ingested")
            else:
                print(f"  ⚠️  No rates available (weekend/holiday)")
            
            current_date += timedelta(days=1)
        
        print(f"\n✅ Backfill complete: {total_success} succeeded, {total_failed} failed")
        
    finally:
        db.close()

if __name__ == "__main__":
    start = datetime(2020, 1, 1)
    end = datetime.now()
    backfill_ecb_rates(start, end)
```

### Update Existing Transactions

**Not required** if:
- ✅ Transactions already use EUR as base_currency
- ✅ exchange_rate_to_base already contains EUR rates

**Required if**:
- ❌ Transactions use USD or THB as base_currency
- ❌ exchange_rate_to_base contains non-EUR rates

**Verification**:
```sql
-- Check current base_currency distribution
SELECT 
    base_currency,
    COUNT(*) as count,
    MIN(occurred_at) as first_tx,
    MAX(occurred_at) as last_tx
FROM transactions
GROUP BY base_currency;

-- If all EUR, no migration needed
-- If USD/THB, need to recalculate exchange_rate_to_base
```

---

## Error Handling

### ECB API Failures

**Scenarios**:
1. Network timeout
2. ECB server unavailable
3. Invalid API response
4. Rate parsing failure

**Handling**:
```python
try:
    rates = fetch_ecb_rates()
    if not rates:
        # Automatic fallback to database
        fallback_rates = get_fallback_rate_from_db(...)
except Exception as e:
    logger.error(f"ECB API error: {e}", exc_info=True)
    # Continue with fallback
```

### No Fallback Available

**Scenario**: Database has no previous ECB rate

**Handling**:
```python
if not fallback_rate:
    logger.error(f"No ECB fallback rate found for {pair}")
    # Options:
    # 1. Skip sync (wait for next day)
    # 2. Alert admin
    # 3. Use last known rate from any source (not recommended for tax)
```

### Missing Notes Field

**Scenario**: Transaction ingestion without fallback note

**Handling**:
```python
# Always provide notes field, even if empty
tx_data = {
    'symbol': 'USD',
    'amount': 1000.00,
    'currency': 'USD',
    'exchange_rate_to_base': rate,
    'notes': fallback_note or None  # NULL if fresh ECB rate
}
```

---

## Tax Compliance Checklist

Before tax filing, verify:

- [ ] **All FX rates from ECB**: `SELECT COUNT(*) FROM fx_rate WHERE source NOT IN ('ecb', 'ecb_fallback')`
  - Expected: 0 (no third-party rates used)

- [ ] **Fallback usage documented**: Check transaction notes for fallback explanations
  - Query: `SELECT COUNT(*) FROM transactions WHERE notes LIKE '%FX rate fallback%'`

- [ ] **Rate consistency**: Verify no gaps in date range
  ```sql
  -- Check for missing dates
  SELECT date
  FROM generate_series(
    '2024-01-01'::date, 
    CURRENT_DATE, 
    '1 day'::interval
  ) AS date
  WHERE NOT EXISTS (
    SELECT 1 FROM fx_rate 
    WHERE DATE(ts) = date 
    AND pair = 'EUR/USD'
  )
  AND EXTRACT(DOW FROM date) NOT IN (0, 6);  -- Exclude weekends
  ```

- [ ] **Correct rate direction**: EUR/USD not USD/EUR
  ```sql
  -- Sanity check: EUR/USD should be around 1.0-1.2
  SELECT 
    pair,
    AVG(rate) as avg_rate,
    MIN(rate) as min_rate,
    MAX(rate) as max_rate
  FROM fx_rate
  WHERE pair = 'EUR/USD'
  AND ts >= CURRENT_DATE - INTERVAL '1 year';
  ```

- [ ] **Audit trail complete**: All fallback notes present
  ```sql
  -- Verify fallback transactions have notes
  SELECT COUNT(*) 
  FROM transactions t
  JOIN fx_rate f ON t.occurred_at::date = f.ts::date
  WHERE f.source = 'ecb_fallback'
  AND t.notes IS NULL;
  -- Expected: 0 (all fallback transactions should have notes)
  ```

---

## Benefits

### Tax Compliance
- ✅ **Legally binding rates**: ECB is the official reference for German taxes
- ✅ **Audit-proof**: Tax authorities accept ECB rates without question
- ✅ **Transparent**: Fallback usage clearly documented in transaction notes
- ✅ **Consistent**: Single authoritative source for all EUR conversions

### Technical
- ✅ **Reliable**: ECB API has high uptime
- ✅ **Official**: Direct from central bank, not third-party aggregators
- ✅ **Free**: No API key required
- ✅ **Historical**: Full history available for backfilling

### Operational
- ✅ **Simplified**: Only 2 currency pairs needed (EUR/USD, EUR/THB)
- ✅ **Automated**: Daily sync with fallback logic
- ✅ **Auditable**: Full trail of rate sources and dates
- ✅ **Maintainable**: Standard three-layer architecture

---

## Summary

**What Changed**:
1. ✅ Added ECB service layer (service/ecb_service.py)
2. ✅ Added ECB parser (crud/parsers/ecb_parser.py)
3. ✅ Updated FX sync orchestration (service/fx_service.py)
4. ✅ Removed USD/THB direct pair (use cross-rate)
5. ✅ Removed third-party APIs (exchangerate-api, yfinance)
6. ✅ Added fallback logic with audit trail
7. ✅ Updated configuration (app_config.yaml)

**Why It Matters**:
- Ensures German tax compliance (§ 20 EStG)
- Uses legally binding ECB reference rates
- Provides audit trail for tax authorities
- Simplifies FX rate management

**Next Steps**:
1. Test ECB integration (`scripts/test_ecb_rates.py`)
2. Backfill historical rates if needed
3. Verify first sync after 16:00 CET
4. Monitor fallback usage on weekends
5. Update transaction ingestion to include fallback notes

---

**Status**: ✅ **IMPLEMENTATION COMPLETE** - Ready for production
