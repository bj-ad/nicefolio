# Worker and GUI Architecture - Health Check Strategy

## Current Architecture (Correct Design)

```
┌──────────────┐
│  PostgreSQL  │
│   Database   │
└──────┬───────┘
       │
       ├─────────────┐
       │             │
┌──────▼──────┐ ┌───▼────────┐
│ GUI Service │ │   Worker   │
│  (NiceGUI)  │ │ (Scheduler)│
└─────────────┘ └────────────┘
   Independent      Independent
```

### Key Design Principles

1. **Services are independent** - GUI doesn't wait for worker
2. **Cache-first with graceful fallback** - GUI tries cache, falls back to live computation
3. **No maintenance page needed** - System handles concurrent access gracefully

---

## 1. Worker Health Check (Improved)

### ❌ Old Check (Inadequate)
```yaml
# Only verified database connectivity, not scheduler health
test: ["CMD", "python", "-c", "from database import SessionLocal; db = SessionLocal(); db.close()"]
```

**Problem:** Shows "healthy" even if scheduler is crashed/stuck

### ✅ New Check (Better)
```yaml
# Verifies scheduler is actively running via heartbeat file
test: ["CMD-SHELL", "python /app/worker/healthcheck.py || exit 1"]
```

**How it works:**
- Scheduler writes timestamp to `/tmp/worker_heartbeat.txt` every 60 seconds
- Health check script verifies heartbeat is less than 5 minutes old
- If heartbeat is stale → scheduler is stuck → unhealthy

**Benefits:**
- Detects actual scheduler failures (infinite loop, deadlock, crash)
- Docker can restart worker automatically if unhealthy
- Monitoring systems can alert on unhealthy status

---

## 2. Should GUI Wait for Worker? **NO**

### Why Current Design is Correct

**Scenario 1: Worker startup delay**
- Worker takes 2-5 minutes to start (backfill, initialization)
- GUI would be blocked for 2-5 minutes
- **User impact:** Can't access portfolio even though GUI is ready

**Scenario 2: Worker failure**
- Worker crashes or can't start
- GUI would never start
- **User impact:** Entire application down (not just slow)

**Scenario 3: Worker busy with daily jobs**
- Daily job runs at 21:00, takes 10-15 minutes
- GUI would be blocked during peak usage hours
- **User impact:** Application unavailable during most active time

### Current Graceful Degradation is Better

```python
# Cache service already handles this perfectly
def get_cached_summary(portfolio_id):
    cache = get_from_cache()
    
    if cache and is_recent(cache):
        return cache  # ✅ Fast path (10ms)
    else:
        return compute_live(portfolio_id)  # ✅ Slow path (2-5s) but WORKS
```

**User experience:**
- ✅ GUI always accessible
- ✅ Shows cached data instantly if available (99% of time)
- ✅ Falls back to live computation if cache missing (slow but works)
- ✅ User sees loading indicators during slow path

---

## 3. What Happens During Daily Jobs?

### Timeline of Daily Job at 21:00

```
21:00:00 - Worker starts daily jobs
21:00:05 - Data sync begins (APIs, blockchain, etc.)
21:05:00 - Snapshot creation begins
21:10:00 - Manual portfolio forward-fill
21:15:00 - Cache pre-computation begins
21:20:00 - Cache pre-computation completes
21:20:00 - Jobs complete ✓
```

### If User Accesses GUI During Jobs

**21:05 (During data sync):**
- Cache is still fresh (last updated yesterday at 21:20)
- User gets cached data → **Fast response (10ms)**
- No impact on user experience

**21:17 (During cache pre-computation):**
- Old cache is 24 hours old → still within 28-hour window
- User gets cached data → **Fast response (10ms)**
- OR if cache was invalidated: falls back to live computation → **Slow but works (2-5s)**

**Key insight:** Cache freshness window (28 hours) allows jobs to run without affecting users

---

## 4. Do We Need a Maintenance Page? **NO**

### Why Maintenance Page is Wrong Approach

**Problem it tries to solve:**
- Users might experience slow response during cache computation

**Why it's the wrong solution:**

1. **Jobs run at 21:00 (peak usage time in Asia/Europe)**
   - Blocking users for 20 minutes = terrible UX
   - Most users would just close the app

2. **Cache window prevents the problem**
   - Old cache is still valid for 28 hours
   - Users see cached data during job execution
   - Zero impact on UX

3. **Fallback works fine**
   - If cache is invalidated, live computation kicks in
   - 2-5 seconds is annoying but acceptable
   - Better than "maintenance mode" message

4. **Jobs can fail**
   - If maintenance mode triggers on job start
   - Job fails → maintenance mode stuck forever
   - Manual intervention needed

### Better Approach: Progressive Enhancement

**Current implementation already has this:**

```python
# Phase 1: Show cached wealth card instantly (~10ms)
if cached_summary:
    render_wealth_card(cached_summary)
else:
    show_loading_skeleton()
    async_load_and_render()

# Phase 2: Load heavy sections asynchronously
async def load_charts():
    # Try cache → Fast
    # Fallback to live → Slow but works
```

**User sees:**
- ✅ Instant page load with main wealth card
- ✅ Skeleton loaders for slower sections
- ✅ Progressive rendering as data loads
- ✅ No jarring "maintenance mode" message

---

## 5. Edge Cases Handled

### Case 1: Cache Corruption
**Symptom:** Cache service returns invalid data
**Handling:** Exceptions caught → falls back to live computation
**User impact:** Slow page load once, then cached again

### Case 2: Worker Crash During Job
**Symptom:** Job fails mid-execution, cache partially updated
**Handling:** 
- Next query gets stale cache (still within 28 hours)
- OR gets live computation if cache corrupted
- Next job run fixes cache
**User impact:** May see slightly stale data temporarily

### Case 3: Database Lock During Cache Write
**Symptom:** Worker writing cache, GUI reading simultaneously
**Handling:** PostgreSQL MVCC handles this automatically
- Reader sees old version (snapshot isolation)
- Writer completes atomically
**User impact:** None (database-level concurrency control)

### Case 4: Extremely Slow Live Computation
**Symptom:** User with complex portfolio, cache miss → 30+ second query
**Handling:** 
- Browser timeout may occur
- User sees error message
- Cache is populated for next request
**User impact:** One bad page load, then fast forever
**Alternative:** Could add request timeout → show "Please try again" message

---

## 6. Monitoring and Alerts

### What to Monitor

**Worker Health:**
```bash
# Check worker heartbeat age
docker exec nicefolio_worker cat /tmp/worker_heartbeat.txt

# Docker healthcheck status
docker inspect nicefolio_worker | grep Health -A 10
```

**Cache Freshness:**
```sql
-- Check cache age
SELECT 
    portfolio_id,
    snapshot_date,
    computed_at,
    NOW() - computed_at AS age
FROM portfolio_summary_cache
ORDER BY computed_at DESC;
```

**GUI Performance:**
```bash
# Check for slow query logs
docker logs nicefolio_gui | grep "computing live summary"
```

### Alert Thresholds

- ⚠️ Worker heartbeat > 5 minutes → restart worker
- ⚠️ Cache age > 30 hours → investigate job failures
- ⚠️ Frequent "cache miss" logs → cache not being populated

---

## Summary

| Question                       | Answer                      | Reason                                  |
| ------------------------------ | --------------------------- | --------------------------------------- |
| **Better worker healthcheck?** | ✅ Yes - heartbeat file      | Detects actual scheduler failures       |
| **GUI wait for worker?**       | ❌ No - independent services | Better reliability and UX               |
| **Maintenance page?**          | ❌ No - graceful degradation | Cache window + fallback works better    |
| **What during daily jobs?**    | ✅ Works fine                | 28-hour cache window allows jobs to run |

**Key insight:** The architecture is already correct. The cache-first with graceful fallback approach handles all edge cases without need for service dependencies or maintenance pages.
