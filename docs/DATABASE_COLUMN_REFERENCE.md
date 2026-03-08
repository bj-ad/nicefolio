# Database Column Quick Reference

**Purpose:** Quick lookup for inconsistent date/timestamp column names across models.

---

## Date/Timestamp Columns

| Model           | Column Name     | Type          | Description               | Example Usage        |
| --------------- | --------------- | ------------- | ------------------------- | -------------------- |
| **Transaction** | `occurred_at`   | TIMESTAMP(TZ) | When transaction happened | `tx.occurred_at`     |
| **Snapshot**    | `snapshot_date` | DATE          | Daily snapshot date       | `snap.snapshot_date` |
| **MarketData**  | `ts`            | TIMESTAMP(TZ) | When price was recorded   | `md.ts`              |
| **FxRate**      | `ts`            | TIMESTAMP(TZ) | When rate was recorded    | `fx.ts`              |
| **Lot**         | `buy_date`      | TIMESTAMP(TZ) | When lot was purchased    | `lot.buy_date`       |
| **Position**    | `last_updated`  | TIMESTAMP(TZ) | Last position update      | `pos.last_updated`   |

## Common Query Patterns

### Filter by Date Range

```python
# Transactions
from datetime import datetime, timedelta
start = datetime(2025, 1, 1, tzinfo=timezone.utc)
end = datetime(2025, 12, 31, tzinfo=timezone.utc)

db.query(Transaction).filter(
    Transaction.occurred_at >= start,
    Transaction.occurred_at < end
).all()

# Snapshots
from datetime import date
start_date = date(2025, 1, 1)
end_date = date(2025, 12, 31)

db.query(Snapshot).filter(
    Snapshot.snapshot_date >= start_date,
    Snapshot.snapshot_date <= end_date
).all()

# MarketData
db.query(MarketData).filter(
    MarketData.ts >= start,
    MarketData.ts < end
).all()

# FxRate
db.query(FxRate).filter(
    FxRate.ts >= start,
    FxRate.ts < end
).all()
```

### Order by Date

```python
# Transactions (oldest first)
db.query(Transaction).order_by(Transaction.occurred_at.asc()).all()

# Snapshots (newest first)
db.query(Snapshot).order_by(Snapshot.snapshot_date.desc()).all()

# MarketData (newest first)
db.query(MarketData).order_by(MarketData.ts.desc()).all()
```

### Get Latest/Earliest

```python
# Latest transaction
db.query(func.max(Transaction.occurred_at)).scalar()

# Earliest snapshot
db.query(func.min(Snapshot.snapshot_date)).scalar()

# Latest market data timestamp
db.query(func.max(MarketData.ts)).scalar()
```

### Extract Date from Timestamp

```python
# For occurred_at (TIMESTAMP)
from sqlalchemy import cast, Date
db.query(cast(Transaction.occurred_at, Date)).filter(
    cast(Transaction.occurred_at, Date) == date(2025, 11, 29)
).all()

# For snapshot_date (already DATE)
db.query(Snapshot).filter(
    Snapshot.snapshot_date == date(2025, 11, 29)
).all()
```

## Why These Names?

**Historical reasons** - columns were named at different times:
- `occurred_at` - semantic (when it occurred)
- `snapshot_date` - prefixed (what it is)
- `ts` - abbreviated (timestamp)
- `buy_date` - action-based (when bought)

**Current state:** No standardization. Use this reference to avoid lookup errors.

**Future:** May standardize to uniform naming, but requires database migration.

---

## Quick Copy-Paste Snippets

```python
# Transaction date filter
Transaction.occurred_at >= start_date

# Snapshot date filter
Snapshot.snapshot_date == target_date

# MarketData timestamp filter
MarketData.ts >= start_datetime

# FxRate timestamp filter
FxRate.ts >= start_datetime

# Lot purchase date filter
Lot.buy_date >= start_date
```

## Common Mistakes to Avoid

❌ **Wrong:**
```python
db.query(Transaction).filter(Transaction.date == ...)  # No 'date' column
db.query(Snapshot).filter(Snapshot.date == ...)        # No 'date' column
db.query(MarketData).filter(MarketData.date == ...)    # No 'date' column
```

✅ **Correct:**
```python
db.query(Transaction).filter(Transaction.occurred_at == ...)
db.query(Snapshot).filter(Snapshot.snapshot_date == ...)
db.query(MarketData).filter(MarketData.ts == ...)
```

---

**Last Updated:** November 29, 2025
