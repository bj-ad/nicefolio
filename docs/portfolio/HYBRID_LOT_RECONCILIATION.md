# Hybrid Lot Reconciliation System

**Created:** October 3, 2025  
**Status:** ✅ Implemented  
**Version:** 1.0

---

## Table of Contents
- [Overview](#overview)
- [Why Hybrid Approach?](#why-hybrid-approach)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Benefits](#benefits)
- [Usage Guide](#usage-guide)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Overview

The hybrid lot reconciliation system combines **incremental lot creation** during transaction ingestion with **weekly full reconciliation** to provide real-time cost basis tracking while maintaining data accuracy.

### Key Components

1. **Incremental Lot Management** (NEW)
   - Creates lots immediately when buy transactions are ingested
   - Allocates sales to lots using FIFO when sell transactions are ingested
   - Runs: Every time a transaction is created

2. **Weekly Full Reconciliation** (EXISTING)
   - Deletes all lots and rebuilds from scratch
   - Catches any discrepancies from incremental process
   - Runs: Every Sunday at 2:30 AM

3. **Manual Reconciliation** (NEW)
   - On-demand lot reconciliation for specific symbols or all symbols
   - Use cases: After manual corrections, before tax filing, troubleshooting
   - Run: `python scripts/reconcile_lots.py`

---

## Why Hybrid Approach?

### Problem Statement

**Before Hybrid Implementation:**
- Lots only existed 1 day per week (after Sunday's full reconciliation)
- Cost basis data unavailable for 6 days per week
- Tax reports and portfolio analytics had 6-day gaps

### Comparison of Approaches

| Approach               | Pros                          | Cons                                    | Decision     |
| ---------------------- | ----------------------------- | --------------------------------------- | ------------ |
| **Weekly Only**        | Simple, clean slate           | 6-day data gap, no real-time cost basis | ❌ Rejected   |
| **Daily Full Rebuild** | Always current data           | Expensive (delete all lots daily)       | ❌ Rejected   |
| **Hybrid**             | Real-time data + weekly audit | Slightly more complex                   | ✅ **Chosen** |

### Why Hybrid Wins

1. **Real-time Cost Basis**: Lots created immediately, no waiting for Sunday
2. **Performance**: Incremental updates are fast (only new transactions)
3. **Accuracy**: Weekly reconciliation catches any incremental errors
4. **Flexibility**: Manual reconciliation available when needed
5. **Safety**: Graceful failure handling ensures transactions never break

---

## Architecture

### Three-Layer Lot Management

```
Transaction Ingestion (crud/crud_base.py)
    ↓
    create_transaction_idempotent()
    ↓
    ├─ Create/Update Transaction in DB
    ↓
    └─ If NEW transaction → Try incremental lot management
        ↓
        ├─ BUY transaction?
        │   └─ create_lot_from_transaction(db, tx)
        │       → Creates new lot with FIFO tracking
        │
        ├─ SELL transaction?
        │   └─ allocate_sale_to_lots(db, tx)
        │       → Allocates to oldest lots first (FIFO)
        │       → Calculates realized gains
        │
        └─ Error?
            → Log warning (don't fail transaction)
            → Weekly reconciliation will fix
```

### Fail-Safe Design

**Critical Principle:** Transaction ingestion must NEVER fail due to lot management errors.

```python
# In crud/crud_base.py
if is_new_transaction and LOT_MANAGEMENT_ENABLED:
    try:
        # Try to create/allocate lot
        create_lot_from_transaction(db, tx)
    except Exception as e:
        # Log warning but DON'T raise exception
        logger.warning(
            f"Failed to create lot for tx {tx.id}: {e}. "
            "Will be corrected during weekly reconciliation."
        )
        # Transaction was still created successfully!
```

**Safety Net:** Weekly reconciliation on Sunday will catch and fix any errors from incremental process.

---

## How It Works

### Incremental Lot Creation (Real-time)

#### Buy Transactions
When a buy transaction is ingested:

```python
# Example: User buys 0.5 BTC at $50,000
create_transaction_idempotent(db, {
    'type': 'buy',
    'symbol': 'BTC',
    'quantity': 0.5,
    'price': 50000,
    'currency': 'USD'
})

# Automatically creates lot:
Lot(
    symbol='BTC',
    purchase_date=transaction.date,
    purchase_price=50000,
    quantity_purchased=0.5,
    quantity_remaining=0.5,
    transaction_id=transaction.id
)
```

#### Sell Transactions
When a sell transaction is ingested:

```python
# Example: User sells 0.3 BTC at $55,000
create_transaction_idempotent(db, {
    'type': 'sell',
    'symbol': 'BTC',
    'quantity': 0.3,
    'price': 55000,
    'currency': 'USD'
})

# Automatically allocates to oldest lots (FIFO):
# 1. Find oldest lot with remaining quantity
# 2. Allocate from that lot
# 3. Calculate realized gain: (55000 - 50000) * 0.3 = $1,500
# 4. Update lot: quantity_remaining = 0.5 - 0.3 = 0.2

LotAllocation(
    lot_id=oldest_lot.id,
    transaction_id=sell_transaction.id,
    quantity_allocated=0.3,
    realized_gain=1500.00
)
```

### Weekly Full Reconciliation (Sunday 2:30 AM)

**Purpose:** Catch any errors from incremental process, provide clean slate.

**Process:**
1. Delete all existing lots and lot allocations
2. Query all buy transactions (type = 'buy', 'transfer_in', 'deposit')
3. Create lots for each buy transaction
4. Query all sell transactions (type = 'sell', 'transfer_out', 'withdrawal')
5. Allocate each sale to lots using FIFO
6. Recalculate all realized gains

**Outcome:** Clean, accurate lot data for the new week.

### Manual Reconciliation (On-demand)

**Use Cases:**
- After manual transaction corrections
- Before tax filing season
- After discovering lot discrepancies
- Testing lot reconciliation logic

**Usage:**
```bash
# Reconcile all symbols
python scripts/reconcile_lots.py

# Reconcile specific symbol only
python scripts/reconcile_lots.py --symbol BTC

# Dry run (preview without changes)
python scripts/reconcile_lots.py --dry-run
```

---

## Benefits

### 1. Real-time Cost Basis Tracking
- **Before:** Cost basis only available 1 day per week (Sunday)
- **After:** Cost basis available 24/7 in real-time
- **Impact:** Portfolio analytics and tax reports always accurate

### 2. Performance Optimization
- **Incremental:** Only processes new transactions (fast)
- **Weekly:** Processes all transactions once per week (thorough)
- **Result:** Best of both worlds - fast updates with periodic accuracy check

### 3. Data Accuracy Guarantee
- **Incremental:** May occasionally have errors (API failures, edge cases)
- **Weekly:** Always correct (full rebuild from source of truth)
- **Result:** Self-healing system - errors automatically corrected within 7 days

### 4. Operational Flexibility
- **Automatic:** Incremental updates happen automatically
- **Scheduled:** Weekly reconciliation runs automatically
- **Manual:** On-demand reconciliation available when needed
- **Result:** Zero-maintenance system with manual override capability

### 5. Graceful Failure Handling
- **Critical:** Transaction ingestion never fails due to lot errors
- **Resilient:** Lot errors logged but don't break data pipeline
- **Self-healing:** Weekly reconciliation fixes any lot issues
- **Result:** Robust system that prioritizes transaction data integrity

---

## Usage Guide

### For Developers

#### Adding New Transaction Ingestion Points

If you create a new CRUD file that ingests transactions, it automatically gets lot management:

```python
# In your new crud/crud_newexchange.py
from crud.crud_base import create_transaction_idempotent

def ingest_newexchange_transactions(db: Session, account_id: int):
    """
    Ingest transactions from new exchange.
    
    Lot management happens automatically via create_transaction_idempotent()!
    """
    for trade in fetch_trades_from_newexchange(account_id):
        # This will automatically create/allocate lots
        tx = create_transaction_idempotent(db, {
            'account_id': account_id,
            'type': trade['type'],  # 'buy' or 'sell'
            'symbol': trade['symbol'],
            'quantity': trade['quantity'],
            'price': trade['price'],
            'currency': 'USD',
            'date': trade['date']
        })
```

**No additional code needed!** Lot management is automatic.

#### Disabling Incremental Lot Management

If you need to temporarily disable incremental lot management:

```python
# In crud/crud_base.py
LOT_MANAGEMENT_ENABLED = False  # Set to False to disable
```

**When to disable:**
- During migration/testing
- During batch historical data loads (use weekly reconciliation instead)
- If troubleshooting lot-related issues

**Re-enable after:**
```python
LOT_MANAGEMENT_ENABLED = True  # Back to normal operation
```

### For Operations

#### Running Manual Reconciliation

**Scenario 1: After Manual Transaction Corrections**
```bash
# You manually fixed some BTC transactions in the database
# Now reconcile BTC lots to reflect the changes
python scripts/reconcile_lots.py --symbol BTC
```

**Scenario 2: Before Tax Filing**
```bash
# Before generating tax reports, ensure all lots are accurate
python scripts/reconcile_lots.py

# Review the summary output
# Lots will be rebuilt from scratch using FIFO
```

**Scenario 3: Troubleshooting Lot Discrepancies**
```bash
# First, preview what would happen
python scripts/reconcile_lots.py --symbol ETH --dry-run

# If it looks good, execute
python scripts/reconcile_lots.py --symbol ETH
```

#### Monitoring Weekly Reconciliation

**Check Scheduler Logs:**
```bash
# View scheduler logs to verify weekly reconciliation runs
docker logs nicefolio-scheduler -f --tail=100

# Look for:
# [2025-10-06 02:30:00] Running weekly lot reconciliation
# [2025-10-06 02:35:00] Lot reconciliation complete: 1,234 lots created
```

**Check Worker Logs:**
```bash
# View worker logs for detailed reconciliation output
docker logs nicefolio-worker -f --tail=100

# Look for:
# Reconciling lots for symbol BTC
# Created 45 lots from 45 buy transactions
# Allocated 32 sales to lots
# Realized gains calculated: $12,345.67
```

---

## Monitoring

### Key Metrics to Track

#### 1. Incremental Lot Creation Success Rate
```python
# In application logs (app.log)
# Look for:
"Created lot {lot_id} for transaction {tx_id}"  # Success
"Failed to create lot for tx {tx_id}: {error}"  # Warning (weekly reconciliation will fix)
```

**Healthy system:** Few or no lot creation warnings  
**Unhealthy system:** Many lot creation warnings (investigate root cause)

#### 2. Weekly Reconciliation Performance
```python
# In worker logs
# Look for:
"Lot reconciliation complete. Success: {success_count}, Failed: {failed_count}"
```

**Healthy system:** High success count, zero or low failed count  
**Unhealthy system:** High failed count (investigate transaction data quality)

#### 3. Cost Basis Data Availability
```sql
-- Check if lots exist for your holdings
SELECT 
    symbol,
    COUNT(*) as open_lots,
    SUM(quantity_remaining) as total_remaining
FROM lots
WHERE quantity_remaining > 0
GROUP BY symbol;
```

**Healthy system:** Lots exist for all holdings  
**Unhealthy system:** Missing lots for some symbols (run manual reconciliation)

### Alerts to Set Up

1. **Lot Creation Failure Rate > 10%**
   - Alert: "High lot creation failure rate detected"
   - Action: Investigate transaction data quality or lot management code

2. **Weekly Reconciliation Failed**
   - Alert: "Weekly lot reconciliation did not complete"
   - Action: Check worker logs, run manual reconciliation if needed

3. **Missing Lots for Holdings**
   - Alert: "Cost basis data missing for some holdings"
   - Action: Run manual reconciliation for affected symbols

---

## Troubleshooting

### Issue 1: Lot Creation Warnings During Transaction Ingestion

**Symptom:**
```
WARNING: Failed to create lot for tx 12345: [error message].
Will be corrected during weekly reconciliation.
```

**Cause:**
- Lot management encountered an error (missing data, edge case, etc.)
- Transaction was still created successfully (good!)
- Lot will be created during weekly reconciliation (also good!)

**Action:**
- No immediate action required (system is self-healing)
- If warnings are frequent, investigate root cause
- Review transaction data quality
- Check for edge cases in lot management code

**Resolution:**
- Weekly reconciliation will fix lots automatically
- Or run manual reconciliation for immediate fix:
  ```bash
  python scripts/reconcile_lots.py --symbol <SYMBOL>
  ```

### Issue 2: Cost Basis Data Missing After Transaction Ingestion

**Symptom:**
- New transaction was ingested successfully
- Cost basis/lot data not available for that transaction

**Cause:**
- Incremental lot creation failed for that transaction
- Error was logged as warning (see Issue 1)

**Action:**
1. Check if LOT_MANAGEMENT_ENABLED is set to True:
   ```python
   # In crud/crud_base.py
   LOT_MANAGEMENT_ENABLED = True  # Should be True
   ```

2. Check application logs for lot creation warnings:
   ```bash
   grep "Failed to create lot" logs/app.log
   ```

3. Run manual reconciliation for affected symbol:
   ```bash
   python scripts/reconcile_lots.py --symbol <SYMBOL>
   ```

**Resolution:**
- Manual reconciliation will create missing lots
- Or wait for Sunday's weekly reconciliation

### Issue 3: Lot Discrepancies Detected

**Symptom:**
- Lot quantities don't match expected holdings
- Realized gains seem incorrect
- Cost basis calculations off

**Cause:**
- Possible causes:
  - Manual transaction edits not reflected in lots
  - Incremental lot allocation errors accumulated
  - Edge case in FIFO allocation logic

**Action:**
1. Run dry-run reconciliation to preview changes:
   ```bash
   python scripts/reconcile_lots.py --symbol <SYMBOL> --dry-run
   ```

2. If preview looks correct, execute reconciliation:
   ```bash
   python scripts/reconcile_lots.py --symbol <SYMBOL>
   ```

3. Verify results by checking lot summary:
   ```python
   from crud.crud_lot import get_lot_summary_by_symbol
   from database import SessionLocal
   
   db = SessionLocal()
   summary = get_lot_summary_by_symbol(db)
   for item in summary:
       print(f"{item['symbol']}: {item['open_lot_count']} lots, "
             f"{item['total_remaining_qty']} remaining")
   db.close()
   ```

**Resolution:**
- Full reconciliation rebuilds lots from scratch
- All discrepancies will be corrected
- Weekly reconciliation will prevent future accumulation

### Issue 4: Weekly Reconciliation Not Running

**Symptom:**
- Cost basis data becoming stale
- Lot creation warnings accumulating
- No reconciliation logs on Sundays

**Cause:**
- Scheduler container not running
- Cron job misconfigured
- Worker container issues

**Action:**
1. Check scheduler container status:
   ```bash
   docker ps | grep nicefolio-scheduler
   ```

2. Check scheduler logs:
   ```bash
   docker logs nicefolio-scheduler --tail=100
   ```

3. Verify cron configuration in worker/weekly_jobs.py:
   ```python
   # Should be: Sunday at 2:30 AM
   @cron('30 2 * * 0')
   def reconcile_lots():
       ...
   ```

4. Restart scheduler container if needed:
   ```bash
   docker-compose restart scheduler
   ```

**Resolution:**
- Scheduler will resume weekly reconciliation
- Run manual reconciliation to catch up:
  ```bash
  python scripts/reconcile_lots.py
  ```

### Issue 5: Performance Issues During Reconciliation

**Symptom:**
- Reconciliation takes very long time
- Database performance degraded during reconciliation
- Timeouts during reconciliation

**Cause:**
- Large number of transactions to process
- Inefficient queries or indexing
- Database resource constraints

**Action:**
1. Reconcile symbols individually instead of all at once:
   ```bash
   # Instead of reconciling everything:
   # python scripts/reconcile_lots.py
   
   # Reconcile high-volume symbols one at a time:
   python scripts/reconcile_lots.py --symbol BTC
   python scripts/reconcile_lots.py --symbol ETH
   python scripts/reconcile_lots.py --symbol SOL
   ```

2. Schedule reconciliation during low-traffic periods
   - Default: Sunday 2:30 AM (good)
   - Avoid: During business hours or high-activity times

3. Check database indexes on key tables:
   ```sql
   -- Ensure these indexes exist:
   CREATE INDEX idx_lots_symbol ON lots(symbol);
   CREATE INDEX idx_lots_remaining ON lots(quantity_remaining);
   CREATE INDEX idx_transactions_type ON transactions(type);
   CREATE INDEX idx_transactions_symbol ON transactions(symbol);
   ```

**Resolution:**
- Performance improvements will reduce reconciliation time
- Incremental lot management reduces need for full reconciliation
- System remains functional during reconciliation

---

## Configuration Reference

### Feature Flag
```python
# In crud/crud_base.py
LOT_MANAGEMENT_ENABLED = True  # Enable/disable incremental lot management
```

**When to disable:**
- During large historical data loads (temporarily disable, then run weekly reconciliation)
- During testing/debugging of lot management code
- If experiencing performance issues with incremental updates

**When to enable:**
- Normal operation (default)
- After disabling for migration/testing

### Weekly Reconciliation Schedule
```python
# In worker/weekly_jobs.py
@cron('30 2 * * 0')  # Sunday at 2:30 AM
def reconcile_lots():
    """Full lot reconciliation - runs weekly."""
    reconcile_all_lots()
```

**Cron format:** `minute hour day_of_month month day_of_week`
- Current: `30 2 * * 0` = 2:30 AM every Sunday
- Alternative: `30 2 * * 1` = 2:30 AM every Monday

**When to change:**
- If Sunday conflicts with other maintenance
- If need more frequent full reconciliation (not recommended)
- If timezone considerations require different time

---

## Best Practices

### 1. Trust the System
- **Incremental lot creation** provides real-time data (good enough for daily use)
- **Weekly reconciliation** ensures accuracy (catch errors within 7 days)
- **Manual reconciliation** available for urgent needs (use sparingly)

### 2. Monitor Lot Creation Warnings
- Occasional warnings are normal (network issues, API failures, edge cases)
- Frequent warnings indicate a problem (investigate root cause)
- Weekly reconciliation will fix warnings automatically

### 3. Use Manual Reconciliation Strategically
- **Before tax filing:** Ensure all data accurate
- **After manual corrections:** Sync lots with edited transactions
- **For troubleshooting:** Diagnose lot discrepancies
- **NOT for routine use:** Trust weekly reconciliation for normal operation

### 4. Maintain Transaction Data Quality
- Good lots require good transactions
- Ensure transaction ingestion handles edge cases
- Validate transaction data before ingestion
- Clean transaction data = clean lot data

### 5. Test Changes Carefully
- Use `--dry-run` flag to preview reconciliation changes
- Test on specific symbols before running on all symbols
- Monitor logs after making lot management changes
- Verify cost basis calculations after reconciliation

---

## Related Documentation

- **[POSITION_RECONCILIATION_FIX.md](./POSITION_RECONCILIATION_FIX.md)** - Position reconciliation patterns
- **[../../readme.txt](../../readme.txt)** - Overall system architecture
- **[../architecture/ARCHITECTURE_PATTERNS_SUMMARY.txt](../architecture/ARCHITECTURE_PATTERNS_SUMMARY.txt)** - Three-layer pattern justification
- **[../../.github/copilot-instructions.md](../../.github/copilot-instructions.md)** - Coding standards and patterns

---

## Changelog

### Version 1.0 (October 3, 2025)
- ✅ Initial implementation of hybrid lot reconciliation
- ✅ Incremental lot creation during transaction ingestion
- ✅ Weekly full reconciliation maintained as safety net
- ✅ Manual reconciliation script created
- ✅ Graceful failure handling implemented
- ✅ Comprehensive documentation written

### Future Enhancements
- [ ] Add lot reconciliation metrics to monitoring dashboard
- [ ] Create Grafana dashboard for lot management health
- [ ] Add automated testing for lot reconciliation logic
- [ ] Optimize performance for large portfolios (>10,000 lots)
- [ ] Add support for non-FIFO methods (LIFO, Specific Identification)

---

**Questions or Issues?**
- Check troubleshooting section above
- Review application logs for warnings/errors
- Run manual reconciliation with `--dry-run` to diagnose
- Contact development team if issues persist

**System Status:** ✅ Production Ready (October 3, 2025)
