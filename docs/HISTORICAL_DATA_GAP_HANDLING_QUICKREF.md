# Historical Data Gap Handling — Quick Reference

## At a Glance

**Script:** `scripts/fetch_historical_marketdata.py`

- Fetches historical market data from yfinance
- Detects missing dates (gaps) in time series
- Forward-fills small gaps (≤3 days) automatically
- Warns about large gaps (>3 days) requiring manual review
- Idempotent — safe to run multiple times

## Gap Handling Rules

```
1–3 days missing  →  Forward-fill with last known value
4+ days missing   →  WARNING (no forward-fill)
```

| Gap Size | Action | Rationale |
|----------|--------|-----------|
| 1 day | Forward-fill | Single holiday |
| 2 days | Forward-fill | Weekend |
| 3 days | Forward-fill | Extended weekend (Fri holiday + Sat/Sun) |
| 4+ days | **Warning only** | Extended holiday or data issue — manual review |

## Configuration

```python
MAX_FORWARD_FILL_DAYS = 3   # Forward-fill up to 3 consecutive days
WARN_GAP_THRESHOLD = 4      # Warn if gap exceeds 3 days
```

## Quick Commands

```bash
# Fetch everything
python scripts/fetch_historical_marketdata.py

# Fetch specific asset classes
python scripts/fetch_historical_marketdata.py --fx-only
python scripts/fetch_historical_marketdata.py --crypto-only
python scripts/fetch_historical_marketdata.py --stocks-only
```

## Before Portfolio Migrations

**Always run this first:**
```bash
python scripts/fetch_historical_marketdata.py
```

Then check for warnings:
```bash
grep "LARGE GAPS DETECTED" logs/app.log
```

Verify coverage:
```sql
SELECT symbol, MIN(DATE(ts)), MAX(DATE(ts)), COUNT(*)
FROM market_data
GROUP BY symbol;
```

---

## Core Function

```python
def detect_and_fill_gaps(data, start_date, end_date, symbol, data_type="price"):
    """Detect gaps in time series data and forward-fill small gaps.

    Returns:
        (filled_data, warnings)
        - filled_data: Original data + forward-filled records for gaps <= 3 days
        - warnings: List of (start_date, end_date) tuples for gaps > 3 days
    """
```

Each fetch function (`fetch_fx_historical`, `fetch_crypto_historical`, `fetch_stocks_historical`):
1. Fetches data from yfinance
2. Converts to internal format
3. Calls `detect_and_fill_gaps()`
4. Ingests all data (original + filled)
5. Reports warnings at the end

---

## Idempotency

**Safe to run multiple times — no duplicates created.**

- Database `upsert_*` functions use unique constraint on `(symbol, ts)`
- Second run sees no missing dates (already filled)
- Forward-filled records identified by `_filled` suffix in `source` column

---

## Crypto Markets (24/7 Trading)

Crypto markets trade continuously, so gaps indicate **data provider issues**, not market closures:
- Weekend gaps → API doesn’t provide granular weekend data
- Forward-filling is reasonable (represents market continuity)
- Large gaps → API outage or incomplete historical data — investigate

---

## Verification Queries

```sql
-- Check forward-filled records
SELECT symbol, DATE(ts), price, source
FROM market_data
WHERE source LIKE "%_filled"
ORDER BY symbol, ts;

-- Count original vs filled records
SELECT
    CASE WHEN source LIKE "%_filled" THEN "Filled" ELSE "Original" END as type,
    COUNT(*)
FROM market_data
GROUP BY 1;
```

---

## Architecture

```
fetch_*_historical()              # Service layer — API calls
    ↓
detect_and_fill_gaps()            # Parser layer — transformation
    ↓
ingest_*()                        # CRUD layer — database operations
    ↓
upsert_*()                        # Idempotent database writes
```

---

## Troubleshooting

| Issue | Likely Cause | Solution |
|-------|-------------|----------|
| Many large gap warnings | Extended holidays or incomplete API data | Check holiday calendar, verify yfinance availability |
| Script runs slowly | Years of data for many symbols | Use `--fx-only`, `--crypto-only`, or `--stocks-only` |
| Duplicate records | Should not happen (upsert) | Check unique constraints, report as bug |

---

## Key Points

- Weekends (2 days) — automatically filled
- Holidays (≤3 days) — automatically filled
- Extended periods (4+ days) — warned, not filled
- Idempotent — safe to run multiple times
- Transparent — filled records marked with `_filled` suffix
