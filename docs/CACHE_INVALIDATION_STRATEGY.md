# Cache Invalidation Strategy & Deployment

## Overview

The background precomputation system caches portfolio summaries, period statistics, and chart data to provide near-instant dashboard loading. This document covers cache architecture, invalidation strategy, deployment procedures, and operational monitoring.

---

## Cache Architecture

### Cache Tables

| Table | Content | Granularity | Refresh |
|-------|---------|-------------|---------|
| `portfolio_summary_cache` | Total value, P&L, KPI metrics (TWR, XIRR, MDD) | One row per portfolio | Daily batch |
| `period_statistics_cache` | Period changes, returns, volatility, benchmark comparison | One row per (portfolio, period) | Daily batch |
| `chart_data_cache` | Pre-rendered chart data (JSON) — performance, growth, risk | One row per (portfolio, chart_type, period) | Daily batch |

---

## Invalidation Strategy: Daily Batch Refresh

### Rationale

The system follows a **batch architecture** — no real-time invalidation is needed because:
1. Source data changes daily (not intraday)
2. Users expect daily updates
3. Batch processing is simpler and more reliable
4. Cache freshness check prevents stale data

### Daily Job Schedule

```
Job 1: Daily Sync (accounts, transactions, market data)
  ↓
Job 2: Daily Sync (crypto prices, FX rates, gold price)
  ↓
Job 3: Forward-Fill Data (ensure data continuity)
  ↓
Job 4: Precompute Dashboard Data  ← Cache refresh
```

### Cache Freshness Policy

All UI functions use **cache-first with fallback**:

```python
def _get_chart_with_cache(db, portfolio_id, chart_type, currency_base, date_range):
    cached_chart = get_cached_chart(portfolio_id, chart_type, date_range)
    if cached_chart:
        return cached_chart
    # Fallback: compute on-demand (slower but functional)
    return generate_chart(...)
```

Benefits:
- Dashboard always works, even if cache fails
- Graceful degradation (slower but functional)
- No stale data risk (fallback uses fresh computation)

---

## Deployment

### Prerequisites

- Docker Compose running
- Git repository access
- Database backup (recommended)

### Step 1: Backup Database

```bash
docker compose exec -T postgres pg_dump -U nicefolio nicefolio_db \
  | gzip > ~/backup_before_cache_$(date +%Y%m%d_%H%M%S).sql.gz
```

### Step 2: Apply Database Migration

The migration creates the 3 cache tables:

```bash
docker compose exec -T postgres psql -U nicefolio -d nicefolio_db \
  < scripts/migrations/add_cache_tables.sql
```

Verify migration:
```bash
docker compose exec -T postgres psql -U nicefolio -d nicefolio_db -c "\dt *cache*"
```

Expected output:
```
 public | chart_data_cache          | table | nicefolio
 public | period_statistics_cache   | table | nicefolio
 public | portfolio_summary_cache   | table | nicefolio
```

### Step 3: Restart Containers

The updated `docker-entrypoint.sh` automatically precomputes cache on startup:

```bash
docker compose restart
```

### Step 4: Verify Cache Population

```sql
SELECT
    'summaries' as cache_type, COUNT(*) as entries
FROM portfolio_summary_cache
UNION ALL
SELECT 'period_stats', COUNT(*) FROM period_statistics_cache
UNION ALL
SELECT 'charts', COUNT(*) FROM chart_data_cache;
```

---

## Performance

| Metric | Before Cache | After Cache |
|--------|-------------|-------------|
| Dashboard load | 5–10 seconds | < 1 second |
| Per-portfolio KPI | ~500–1000ms | < 10ms |
| Chart retrieval | ~1500–2500ms | < 15ms |
| **Improvement** | — | **90–95% faster** |

---

## Manual Cache Refresh

### When to Manually Refresh

- After manual data import or correction (outside daily sync)
- After config changes affecting calculations
- After bug fixes requiring cache rebuild

### How to Refresh

**Option 1: Force Refresh via Script**
```bash
docker compose exec nicefolio_gui python -c \
  "from service.precomputation_service import precompute_all_portfolios; \
   result = precompute_all_portfolios(force=True); \
   print('Cache refreshed:', result)"
```

**Option 2: Clear All Cache (daily job will rebuild)**
```sql
DELETE FROM chart_data_cache;
DELETE FROM period_statistics_cache;
DELETE FROM portfolio_summary_cache;
```

**Option 3: Clear Specific Portfolio Cache**
```sql
DELETE FROM chart_data_cache WHERE portfolio_id = :id;
DELETE FROM period_statistics_cache WHERE portfolio_id = :id;
DELETE FROM portfolio_summary_cache WHERE portfolio_id = :id;
```

---

## Monitoring

### Key Metrics

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Cache hit rate | > 95% | < 90% |
| Precomputation job time | < 30 seconds | Job failure |
| Cache freshness | < 24 hours | > 25 hours |

### Monitoring Queries

```sql
-- Cache statistics
SELECT
    'summaries' as cache_type,
    COUNT(*) as entries,
    MAX(updated_at) as last_update
FROM portfolio_summary_cache
UNION ALL
SELECT 'period_stats', COUNT(*), MAX(updated_at) FROM period_statistics_cache
UNION ALL
SELECT 'charts', COUNT(*), MAX(updated_at) FROM chart_data_cache;

-- Cache age per portfolio
SELECT
    portfolio_id,
    portfolio_name,
    updated_at,
    EXTRACT(EPOCH FROM (NOW() - updated_at))/3600 as hours_old
FROM portfolio_summary_cache
ORDER BY updated_at;
```

### Scheduler Verification

```bash
docker compose logs nicefolio_gui | grep "Job 4/4"
```

---

## Rollback

### Option 1: Keep Migration, Disable Cache

The UI automatically falls back to on-demand computation if cache is empty. Simply clearing the cache tables is sufficient — the dashboard remains functional (just slower).

### Option 2: Full Rollback

```bash
# Restore database from pre-cache backup
gunzip -c ~/backup_before_cache_*.sql.gz \
  | docker compose exec -T postgres psql -U nicefolio -d nicefolio_db

# Roll back code
git checkout HEAD~1

# Restart
docker compose restart
```

---

## Troubleshooting

### Cache Not Updating

**Symptoms:** Dashboard shows old data, cache timestamps > 24 hours old

**Solutions:**
1. Check scheduler logs for job failures
2. Manually trigger refresh with `force=True`
3. Verify database connectivity
4. Check for data sync failures in upstream jobs

### Frequent Cache Misses

**Solutions:**
1. Verify precomputation job completed
2. Check `date_range` parameter matching (cache keys must match exactly)
3. Verify `portfolio_id` matching (`None` for "All Portfolios")
4. Run force refresh to rebuild cache

---

## Future Enhancements

### Real-Time Invalidation (If Needed)

If real-time transactions are added:

```sql
CREATE OR REPLACE FUNCTION invalidate_portfolio_cache()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM chart_data_cache WHERE portfolio_id = NEW.portfolio_id;
    DELETE FROM period_statistics_cache WHERE portfolio_id = NEW.portfolio_id;
    DELETE FROM portfolio_summary_cache WHERE portfolio_id = NEW.portfolio_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER transaction_cache_invalidation
AFTER INSERT OR UPDATE OR DELETE ON transactions
FOR EACH ROW EXECUTE FUNCTION invalidate_portfolio_cache();
```

---

## Files Involved

| File | Role |
|------|------|
| `service/precomputation_service.py` | Daily batch precomputation |
| `service/cache_service.py` | Fast cache retrieval with fallback |
| `scripts/migrations/add_cache_tables.sql` | Database migration |
| `models.py` | Cache model classes |
| `worker/scheduler.py` | Job 4/4 for daily precomputation |
| `apps/pages/portfolio.py` | Cache-integrated dashboard |
| `docker-entrypoint.sh` | Cache precomputation on startup |
