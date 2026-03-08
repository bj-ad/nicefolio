# Backfilling Guide

## Overview

Backfilling is the process of filling in missing historical data when the worker fails to run scheduled jobs or when recovering from system downtime.

**What gets backfilled:**
- Market data (crypto prices, securities prices)
- FX rates
- Gold prices
- Portfolio snapshots

**Why backfilling is needed:**
- Worker container downtime (restart, crash, deployment)
- Daily job failures (API errors, network issues)
- Database maintenance windows
- Historical data gaps

---

## Automatic Detection (New in Oct 2025)

The worker now checks for missing data on startup:

```yaml
# config/app_config.yaml
scheduler:
  backfill_on_startup: true
  backfill_lookback_days: 7  # Check last 7 days
```

**What happens on worker startup:**
1. Queries database for missing market data in last N days
2. Queries database for missing snapshots in last N days
3. Logs warnings if gaps detected
4. Provides exact commands to run for backfilling
5. Does **NOT** automatically backfill (manual intervention required)

**Example output:**
```
================================================================================
CHECKING FOR MISSING DATA
================================================================================
Looking back 7 days from today
Found 1 dates missing market data: [datetime.date(2025, 10, 13)]
Run backfill script to fill gaps:
  python scripts/backfill_historical_prices.py --start-date 2025-10-13 --end-date 2025-10-13
  python scripts/backfill_missing_data.py --start-date 2025-10-13 --end-date 2025-10-13 --snapshots-only
Found 1 dates missing snapshots: [datetime.date(2025, 10, 13)]
Run backfill script to fill gaps:
  python scripts/backfill_missing_data.py --start-date 2025-10-13 --end-date 2025-10-13 --snapshots-only
================================================================================
```

---

## Backfill Scripts

### 1. Historical Prices (Recommended)

**Purpose:** Fetch **accurate historical prices** using yfinance API

```bash
# Backfill last 7 days
python scripts/backfill_historical_prices.py --days 7

# Backfill specific date range
python scripts/backfill_historical_prices.py --start-date 2025-10-13 --end-date 2025-10-14

# Crypto only
python scripts/backfill_historical_prices.py --days 7 --crypto-only

# Securities only
python scripts/backfill_historical_prices.py --days 7 --securities-only
```

**Features:**
- ✅ Uses yfinance historical data API
- ✅ Fetches actual closing prices for each date
- ✅ Timestamps data correctly for historical dates
- ✅ Respects exclusion config (HODL-SBR, etc.)
- ✅ Logs success/failure counts
- ⚠️ Cannot fetch current day (historical data only)

**Data sources:**
- `source: yfinance_historical` (instead of `yfinance` or `coinmarketcap`)
- Uses closing prices for each date
- Supports crypto (BTC-USD, ETH-USD) and securities (AAPL, **GLD**)

**Gold ETF vs Physical Gold:**
- **GLD** (Gold ETF): ✅ Backfillable via yfinance historical data
- **GOLDTHB** (goldtraders.th): ⚠️ NOT backfillable (web scraping only)
  - Uses last known price from database for historical dates
  - Source tagged as `backfill_fallback` instead of `goldtraders.or.th`

### 2. Missing Data (Current Prices)

**Purpose:** Fill gaps using **current prices** timestamped to past dates

```bash
# Backfill all data types
python scripts/backfill_missing_data.py --days 7

# Backfill specific date range
python scripts/backfill_missing_data.py --start-date 2025-10-13 --end-date 2025-10-14

# Market data only (crypto, securities, FX, gold)
python scripts/backfill_missing_data.py --days 7 --market-data-only

# Snapshots only
python scripts/backfill_missing_data.py --days 7 --snapshots-only

# Force backfill even if data exists
python scripts/backfill_missing_data.py --days 7 --force
```

**Features:**
- ✅ Automatically detects missing dates
- ✅ Skips dates with existing data (unless --force)
- ✅ Handles all data types (market data + snapshots)
- ⚠️ Uses **current** prices (not historical)
- ⚠️ Less accurate for price-sensitive data

**When to use:**
- Quick fills for non-critical data
- FX rates (usually stable)
- Gold prices (less volatile)
- Testing backfill logic

---

## Recommended Workflow

### Step 1: Detect Missing Data

Check worker logs after container restart:

```bash
docker logs nicefolio_worker | grep "CHECKING FOR MISSING DATA" -A 20
```

Or manually check database:

```bash
docker exec nicefolio_worker python -c "
from worker.scheduler import check_and_backfill_missing_data
check_and_backfill_missing_data()
"
```

### Step 2: Backfill Market Data (Historical Prices)

Use **historical prices script** for accuracy:

```bash
# Development (local)
cd /path/to/nicefolio
python scripts/backfill_historical_prices.py --start-date 2025-10-13 --end-date 2025-10-13

# Production (Docker)
cd /path/to/nicefolio
docker exec nicefolio_worker python scripts/backfill_historical_prices.py \
  --start-date 2025-10-13 --end-date 2025-10-13
```

**Expected output:**
```
================================================================================
BACKFILL HISTORICAL PRICES (yfinance)
================================================================================
Date range: 2025-10-13 to 2025-10-13
Total days: 1
Mode: All assets (crypto + securities)
================================================================================

Processing 2025-10-13...
Backfilling crypto prices for 2025-10-13
Fetching historical prices for 6 crypto symbols
  ✅ BTC: $114580.00
  ✅ ETH: $4210.00
  ✅ BNB: $1278.00
  ✅ SOL: $202.00
  ✅ XRP: $2.60
  ✅ ADA: $0.72
Crypto prices backfill: 6 succeeded, 0 failed

================================================================================
BACKFILL COMPLETE
================================================================================
Dates processed: 1
Crypto prices: 6 succeeded, 0 failed
Securities prices: 0 succeeded, 0 failed
Total: 6 succeeded, 0 failed
================================================================================
```

### Step 3: Backfill Snapshots

After market data is backfilled, create snapshots:

```bash
# Development (local)
python scripts/backfill_missing_data.py --start-date 2025-10-13 --end-date 2025-10-13 --snapshots-only

# Production (Docker)
docker exec nicefolio_worker python scripts/backfill_missing_data.py \
  --start-date 2025-10-13 --end-date 2025-10-13 --snapshots-only
```

**Expected output:**
```
================================================================================
BACKFILL MISSING DATA
================================================================================
Date range: 2025-10-13 to 2025-10-13
Total days: 1
Mode: Snapshots only
================================================================================
Found 1 dates missing snapshots

Backfilling 1 dates...
================================================================================

Processing 2025-10-13...
Backfilling snapshots for 2025-10-13
  - Created 4 snapshots, 0 failed

================================================================================
BACKFILL COMPLETE
================================================================================
Dates processed: 1
Snapshots: 4 created, 0 failed
================================================================================
```

### Step 4: Verify

Check that data was filled correctly:

```bash
# Check market data
docker exec nicefolio_db psql -U portfolio_user -d portfolio_db -c "
SELECT DATE(ts AT TIME ZONE 'UTC') as date, symbol, price, source
FROM market_data
WHERE DATE(ts AT TIME ZONE 'UTC') = '2025-10-13'
ORDER BY symbol;
"

# Check snapshots
docker exec nicefolio_db psql -U portfolio_user -d portfolio_db -c "
SELECT snapshot_date, portfolio_id, total_value_usd
FROM snapshots
WHERE snapshot_date = '2025-10-13'
ORDER BY portfolio_id;
"
```

---

## Common Scenarios

### Scenario 1: Worker Down for 1 Day (Oct 13)

**Problem:** Worker container restarted Oct 14, missed Oct 13 daily job

**Solution:**
```bash
# 1. Backfill market data (historical prices)
docker exec nicefolio_worker python scripts/backfill_historical_prices.py \
  --start-date 2025-10-13 --end-date 2025-10-13

# 2. Backfill snapshots
docker exec nicefolio_worker python scripts/backfill_missing_data.py \
  --start-date 2025-10-13 --end-date 2025-10-13 --snapshots-only
```

### Scenario 2: Worker Down for Multiple Days

**Problem:** Worker down Oct 10-13 (4 days)

**Solution:**
```bash
# 1. Backfill market data for entire range
docker exec nicefolio_worker python scripts/backfill_historical_prices.py \
  --start-date 2025-10-10 --end-date 2025-10-13

# 2. Backfill snapshots for entire range
docker exec nicefolio_worker python scripts/backfill_missing_data.py \
  --start-date 2025-10-10 --end-date 2025-10-13 --snapshots-only
```

### Scenario 3: API Rate Limit Hit

**Problem:** CoinMarketCap API rate limit exceeded during daily job

**Solution:**
```bash
# Use yfinance as fallback (automatic in daily jobs)
# If still missing data, backfill manually:
docker exec nicefolio_worker python scripts/backfill_historical_prices.py \
  --days 1 --crypto-only
```

### Scenario 4: Weekend/Holiday Gaps

**Problem:** Markets closed on weekends, no securities prices

**Solution:**
- **Normal behavior** - securities markets closed
- Crypto prices should still update (24/7 markets)
- Snapshots use last known securities prices
- No backfill needed unless crypto prices also missing

### Scenario 5: Database Maintenance

**Problem:** Database was down for backup restoration

**Solution:**
```bash
# Check for gaps in last 7 days
docker exec nicefolio_worker python -c "
from worker.scheduler import check_and_backfill_missing_data
check_and_backfill_missing_data()
"

# Backfill detected gaps using provided commands
```

---

## Troubleshooting

### Issue: "No historical data for SYMBOL on DATE"

**Cause:** yfinance doesn't have data for that date (weekend, holiday, delisted)

**Solution:**
- Check if market was closed (weekends, holidays)
- Try adjacent dates: `--start-date 2025-10-10 --end-date 2025-10-14`
- For crypto, should always have data (24/7 markets)

### Issue: "Failed to ingest price for SYMBOL"

**Cause:** Database constraint violation or duplicate data

**Solution:**
```bash
# Check existing data
docker exec nicefolio_db psql -U portfolio_user -d portfolio_db -c "
SELECT ts, symbol, price, source
FROM market_data
WHERE symbol = 'BTC' AND DATE(ts AT TIME ZONE 'UTC') = '2025-10-13';
"

# Use --force to overwrite if needed
python scripts/backfill_missing_data.py --force --start-date 2025-10-13 --end-date 2025-10-13
```

### Issue: "Snapshots created: 0, failed: 1"

**Cause:** Missing market data or portfolio configuration issue

**Solution:**
1. Backfill market data first (snapshots depend on prices)
2. Check for missing prices:
   ```bash
   docker exec nicefolio_db psql -U portfolio_user -d portfolio_db -c "
   SELECT DISTINCT symbol FROM positions WHERE portfolio_id = 5;
   "
   # Then check if prices exist for those symbols
   ```
3. Check portfolio status (closed portfolios skipped)

### Issue: API Rate Limits

**Cause:** Too many API calls in short period

**Solution:**
- **yfinance:** Usually unlimited for historical data
- **CoinMarketCap:** 333 calls/day on free tier
- Backfill in smaller batches: `--days 1` at a time
- Wait 24 hours if rate limit hit

---

## Configuration

### Enable/Disable Startup Check

```yaml
# config/app_config.yaml
scheduler:
  backfill_on_startup: true   # Enable detection
  backfill_lookback_days: 7   # Check last 7 days
```

To disable:
```yaml
scheduler:
  backfill_on_startup: false
```

### Adjust Lookback Window

```yaml
# Check last 14 days instead of 7
scheduler:
  backfill_lookback_days: 14
```

**Recommendations:**
- Development: `backfill_lookback_days: 3` (short window)
- Production: `backfill_lookback_days: 7` (catch weekly gaps)
- After long downtime: `backfill_lookback_days: 30` (full month)

---

## Best Practices

1. **Check on Startup:** Always check worker logs after deployment
2. **Historical First:** Use `backfill_historical_prices.py` for market data (accurate)
3. **Snapshots Second:** Create snapshots after market data is backfilled
4. **Verify Results:** Query database to confirm data was inserted
5. **Weekend Awareness:** Securities markets closed, crypto continues
6. **Rate Limit Awareness:** Don't backfill large ranges rapidly
7. **Manual Intervention:** Backfilling requires manual approval (safety measure)

---

## Future Enhancements (TODO)

- [ ] Automatic backfilling (configurable thresholds)
- [ ] FX rate backfilling using ECB historical API
- [ ] Transaction backfilling (resync from sources)
- [ ] Parallel backfilling (multiple dates simultaneously)
- [ ] Smart retry on API failures
- [ ] Notification system for missing data detection

---

## Related Documentation

- `docs/SCHEDULER_CONFIGURATION_GUIDE.md` - Daily job scheduling
- `docs/DOCKER_INITIALIZATION_AND_CONFIG_RELOAD.md` - Container startup
- `.github/copilot-instructions.md` - Three-layer architecture
- `worker/daily_jobs.py` - Daily sync implementation
- `worker/scheduler.py` - Scheduler with startup check

---

**Last Updated:** October 2025  
**Contributors:** AI Agent, Portfolio Tracker Team
