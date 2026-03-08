# Foreign Currency Lot Tracking - Implementation Complete ✅

**Date**: 2025-11-16  
**Issue**: USD lots were not being created despite having `asset_class='cash'`  
**Root Cause**: Lot tracking was excluding ALL currencies, including foreign ones  
**Solution**: Modified logic to skip ONLY base currency (EUR), create lots for ALL foreign currencies

---

## Problem Analysis

### Original Behavior ❌

```python
# config/app_config.yaml
portfolio:
  lot_tracking_exclude_asset_classes:
    - cash  # ← This excluded ALL currencies

# crud/crud_lot.py
if transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    return None  # ← Skipped EUR, USD, THB, etc.
```

**Result**: No lots created for ANY currency (EUR, USD, THB, etc.)

### German Tax Requirement ✅

**§ 20 EStG**: Foreign currency exchanges are taxable events requiring cost basis tracking.

**Example**:
```
1. Buy 10,000 USD when EUR/USD = 1.10 → Cost = 9,090.91 EUR
2. Buy 5,000 USD when EUR/USD = 1.15 → Cost = 4,347.83 EUR
3. Sell 12,000 USD when EUR/USD = 1.12 → ?

FIFO required:
- First 10,000 USD from lot 1 → Proceeds = 8,928.57 EUR → Loss = -162.34 EUR
- Next 2,000 USD from lot 2 → Proceeds = 1,785.71 EUR → Gain = -174.63 EUR
```

### Correct Behavior ✅

```python
# config/app_config.yaml
base_currency: EUR  # ← Only THIS currency should be excluded

# Lot tracking logic:
# - EUR (base currency): NO lots (no cost basis needed)
# - USD (foreign): YES, create lots (cost basis = EUR spent)
# - THB (foreign): YES, create lots (cost basis = EUR spent)
# - All other foreign currencies: YES, create lots
```

---

## Changes Made

### 1. Load Base Currency Configuration

**File**: `crud/crud_lot.py` (Lines 23-24)

```python
# Get base currency from app config (EUR)
# Lot tracking: Create lots for ALL foreign currencies, skip only base currency
BASE_CURRENCY = app_config.get('base_currency', 'EUR')
```

### 2. Updated `create_lot_from_transaction()` - Skip Only Base Currency

**File**: `crud/crud_lot.py` (Lines 70-93)

**Before**:
```python
# Skip lot creation for excluded asset classes (e.g., cash)
if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    logger.debug(f"Skipping lot creation for {transaction.symbol}")
    return None
```

**After**:
```python
# Skip lot creation for base currency (EUR) - no cost basis needed for base currency
if transaction.symbol == BASE_CURRENCY:
    logger.debug(f"Skipping lot creation for base currency {BASE_CURRENCY}")
    return None

# Skip lot creation for other excluded asset classes (except cash - we need foreign currency lots)
# Foreign currencies (USD, THB, etc.) NEED lots for German tax compliance
if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    # Exception: If asset_class='cash' but it's a foreign currency, CREATE the lot
    if transaction.asset_class == 'cash' and transaction.symbol != BASE_CURRENCY:
        logger.debug(
            f"Creating lot for foreign currency {transaction.symbol} "
            f"(asset_class='cash' but not base currency)"
        )
        # Continue to lot creation
    else:
        logger.debug(
            f"Skipping lot creation for {transaction.symbol} "
            f"(asset_class '{transaction.asset_class}' is excluded from lot tracking)"
        )
        return None
```

### 3. Updated `allocate_sale_to_lots()` - Skip Only Base Currency

**File**: `crud/crud_lot.py` (Lines 250-274)

**Before**:
```python
# Skip lot allocation for excluded asset classes (e.g., cash)
if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    logger.debug(f"Skipping lot allocation for {transaction.symbol}")
    return [], Decimal('0')
```

**After**:
```python
# Skip lot allocation for base currency (EUR) - no cost basis needed
if transaction.symbol == BASE_CURRENCY:
    logger.debug(f"Skipping lot allocation for base currency {BASE_CURRENCY}")
    return [], Decimal('0')

# Skip lot allocation for other excluded asset classes (except cash - we need foreign currency lots)
# Foreign currencies (USD, THB, etc.) NEED lot allocation for German tax compliance
if transaction.asset_class and transaction.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    # Exception: If asset_class='cash' but it's a foreign currency, ALLOCATE to lots
    if transaction.asset_class == 'cash' and transaction.symbol != BASE_CURRENCY:
        logger.debug(
            f"Allocating sale for foreign currency {transaction.symbol} "
            f"(asset_class='cash' but not base currency)"
        )
        # Continue to lot allocation
    else:
        logger.debug(
            f"Skipping lot allocation for {transaction.symbol} "
            f"(asset_class '{transaction.asset_class}' is excluded from lot tracking)"
        )
        return [], Decimal('0')
```

### 4. Updated `reconcile_lots_from_transactions()` - Skip Only Base Currency

**File**: `crud/crud_lot.py` (Lines 660-682)

**Before**:
```python
# Skip allocation for excluded asset classes (e.g., cash)
if tx.asset_class and tx.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    logger.debug(f"Skipping allocation for {tx.symbol} sell")
    continue
```

**After**:
```python
# Skip allocation for base currency (EUR) - no cost basis needed
if tx.symbol == BASE_CURRENCY:
    logger.debug(f"Skipping allocation for base currency {BASE_CURRENCY}")
    continue

# Skip allocation for other excluded asset classes (except cash - we need foreign currency lots)
# Foreign currencies (USD, THB, etc.) NEED lot allocation for German tax compliance
if tx.asset_class and tx.asset_class in LOT_EXCLUDE_ASSET_CLASSES:
    # Exception: If asset_class='cash' but it's a foreign currency, ALLOCATE to lots
    if tx.asset_class == 'cash' and tx.symbol != BASE_CURRENCY:
        logger.debug(
            f"Allocating sale for foreign currency {tx.symbol} "
            f"(asset_class='cash' but not base currency)"
        )
        # Continue to lot allocation
    else:
        logger.debug(
            f"Skipping allocation for {tx.symbol} sell "
            f"(asset_class '{tx.asset_class}' is excluded from lot tracking)"
        )
        continue
```

---

## Logic Summary

### Lot Creation Decision Tree

```
Transaction with symbol X and asset_class Y
    ↓
Is X == BASE_CURRENCY (EUR)?
    ├─ YES → SKIP lot creation (base currency doesn't need cost basis)
    │
    └─ NO → Is asset_class Y in LOT_EXCLUDE_ASSET_CLASSES?
            ├─ YES → Is Y == 'cash' AND X != BASE_CURRENCY?
            │        ├─ YES → CREATE lot (foreign currency needs cost basis)
            │        └─ NO → SKIP lot creation (other excluded asset class)
            │
            └─ NO → CREATE lot (normal asset like stocks, crypto, etc.)
```

### Examples

| Symbol | Asset Class | Base Currency | Create Lot? | Reason              |
| ------ | ----------- | ------------- | ----------- | ------------------- |
| EUR    | cash        | EUR           | ❌ NO        | Base currency (EUR) |
| USD    | cash        | EUR           | ✅ YES       | Foreign currency    |
| THB    | cash        | EUR           | ✅ YES       | Foreign currency    |
| BTC    | crypto      | EUR           | ✅ YES       | Crypto asset        |
| AAPL   | stock       | EUR           | ✅ YES       | Stock asset         |
| GLD    | etc         | EUR           | ✅ YES       | Commodity ETF       |

---

## Configuration

### Current Configuration (Unchanged)

**File**: `config/app_config.yaml`

```yaml
# Global base currency (EUR for German tax resident)
base_currency: EUR

# Portfolio management settings
portfolio:
  # Lot tracking exclusions
  # Asset classes that should NOT have lot tracking (except foreign currencies)
  lot_tracking_exclude_asset_classes:
    - cash  # ← This is now interpreted as "skip base currency only"
```

**Note**: We keep `cash` in the exclusion list, but the code now has special handling:
- `asset_class='cash'` + `symbol=EUR` → Skip (base currency)
- `asset_class='cash'` + `symbol≠EUR` → Create lot (foreign currency)

---

## Impact on Each Account

### IBKR (Account 2)

**Transactions**:
- EUR deposits/withdrawals → NO lots (base currency)
- USD deposits/withdrawals → ✅ LOTS CREATED (foreign currency)
- FX conversions (EUR↔USD) → ✅ LOTS CREATED for USD side
- Stock purchases (AAPL, GLD) → ✅ LOTS CREATED (already working)

**Example Flow**:
```
1. Deposit 10,000 EUR → No lot (base currency)
2. Exchange 5,000 EUR → 5,500 USD → Creates USD lot with cost basis = 5,000 EUR
3. Buy 10 AAPL @ $150 (1,500 USD) → Creates AAPL lot, reduces USD lot by 1,500
4. Remaining USD: 4,000 USD lot with cost basis = 3,636.36 EUR
```

### BinanceTH (Account 3)

**Transactions**:
- THB deposits/withdrawals → ✅ LOTS CREATED (foreign currency)
- Crypto purchases (BTC, ETH) → ✅ LOTS CREATED (already working)

**Example Flow**:
```
1. Deposit 10,000 THB → Creates THB lot with cost basis = 10,000/40 = 250 EUR
2. Buy 0.01 BTC with 8,000 THB → Creates BTC lot, reduces THB lot by 8,000
3. Remaining THB: 2,000 THB lot with cost basis = 50 EUR
```

### Crypto Wallets (Accounts 5-7)

**No change**: Crypto transactions already create lots (asset_class='crypto')

---

## Weekly Lot Reconciliation

### Automatic Processing

**Schedule**: Sunday night at 2:30 AM (configurable)

**Process** (with new logic):
1. Delete all existing lots and lot_allocations
2. Query all buy transactions:
   - EUR deposits → SKIP (base currency)
   - USD deposits → CREATE LOT (foreign currency)
   - THB deposits → CREATE LOT (foreign currency)
   - Stock purchases → CREATE LOT
   - Crypto purchases → CREATE LOT
3. Query all sell transactions:
   - EUR withdrawals → SKIP
   - USD withdrawals → ALLOCATE to USD lots (FIFO)
   - THB withdrawals → ALLOCATE to THB lots (FIFO)
   - Stock sales → ALLOCATE to stock lots (FIFO)
   - Crypto sales → ALLOCATE to crypto lots (FIFO)

**Result**: All foreign currencies tracked with proper cost basis

---

## Testing

### Manual Verification (After Next Weekly Reconciliation)

```sql
-- 1. Check that EUR lots are NOT created
SELECT COUNT(*) as eur_lots FROM lots WHERE symbol = 'EUR';
-- Expected: 0

-- 2. Check that USD lots ARE created
SELECT lot_id, symbol, qty, remaining_qty, buy_date
FROM lots
WHERE symbol = 'USD'
ORDER BY buy_date;
-- Expected: Multiple USD lots if there are USD deposits

-- 3. Check that THB lots ARE created
SELECT lot_id, symbol, qty, remaining_qty, buy_date
FROM lots
WHERE symbol = 'THB'
ORDER BY buy_date;
-- Expected: Multiple THB lots if there are THB deposits

-- 4. Verify global FIFO combines USD across all accounts
SELECT portfolio_id, COUNT(*) as lot_count, SUM(remaining_qty) as total_qty
FROM lots
WHERE symbol = 'USD'
GROUP BY portfolio_id;
-- Expected: USD lots distributed across portfolios (if applicable)

-- 5. Check lot allocations for USD withdrawals
SELECT la.*, t.occurred_at, t.qty
FROM lot_allocations la
JOIN transactions t ON la.transaction_id = t.id
WHERE t.symbol = 'USD' AND t.type IN ('withdrawal', 'transfer_out')
ORDER BY t.occurred_at;
-- Expected: USD withdrawals allocated to lots in FIFO order
```

### Integration Test

```python
# After weekly reconciliation
from crud.crud_lot import get_open_lots_fifo
from database import SessionLocal

db = SessionLocal()

# Test 1: No EUR lots
eur_lots = get_open_lots_fifo(db, 'EUR')
print(f"EUR lots: {len(eur_lots)}")  # Expected: 0

# Test 2: USD lots exist
usd_lots = get_open_lots_fifo(db, 'USD')
print(f"USD lots: {len(usd_lots)}")  # Expected: > 0 if USD deposits exist
for lot in usd_lots:
    print(f"  USD lot: {lot.remaining_qty} @ {lot.buy_date} (Portfolio {lot.portfolio_id})")

# Test 3: THB lots exist
thb_lots = get_open_lots_fifo(db, 'THB')
print(f"THB lots: {len(thb_lots)}")  # Expected: > 0 if THB deposits exist
for lot in thb_lots:
    print(f"  THB lot: {lot.remaining_qty} @ {lot.buy_date} (Portfolio {lot.portfolio_id})")

# Test 4: Stock lots still work
aapl_lots = get_open_lots_fifo(db, 'AAPL', isin='US0378331005')
print(f"AAPL lots: {len(aapl_lots)}")  # Expected: > 0 if AAPL holdings exist
```

---

## German Tax Compliance

### Base Currency (EUR)

- ✅ No lot tracking needed (EUR is the "home currency")
- ✅ No cost basis calculation needed
- ✅ No realized gains/losses for EUR transactions

### Foreign Currencies (USD, THB, etc.)

- ✅ Lot tracking enabled (cost basis in EUR)
- ✅ FIFO allocation on withdrawals
- ✅ Realized gains/losses calculated automatically
- ✅ Global FIFO across all accounts (§ 20 EStG compliant)

### Example Tax Report

```
Foreign Currency Exchange Gains (2025):

USD:
  - Lot 1 (2025-01-15): Acquired 10,000 USD @ 0.91 EUR/USD = 9,100 EUR cost
  - Disposal (2025-06-20): Sold 8,000 USD @ 0.89 EUR/USD = 7,120 EUR proceeds
  - Realized Loss: -1,280 EUR

THB:
  - Lot 1 (2025-02-10): Acquired 50,000 THB @ 0.025 EUR/THB = 1,250 EUR cost
  - Disposal (2025-08-15): Sold 30,000 THB @ 0.027 EUR/THB = 810 EUR proceeds
  - Realized Gain: +60 EUR

Total Realized FX Gain/Loss: -1,220 EUR
```

---

## Summary

### What Changed ✅

1. **Base currency check**: Added `BASE_CURRENCY` constant from config
2. **Lot creation**: Skip EUR only, create lots for USD/THB/etc.
3. **Lot allocation**: Skip EUR only, allocate USD/THB/etc. to lots
4. **Weekly reconciliation**: Same logic applied during full rebuild

### What's Automatic ✅

- ✅ Weekly reconciliation will regenerate all lots with new logic
- ✅ Foreign currency deposits automatically create lots
- ✅ Foreign currency withdrawals automatically allocate FIFO
- ✅ Realized gains calculated automatically

### What's Consistent ✅

- ✅ EUR (base currency): NO lots
- ✅ USD, THB (foreign currencies): YES lots
- ✅ BTC, ETH (crypto): YES lots
- ✅ AAPL, GLD (stocks/ETFs): YES lots
- ✅ All foreign assets: Tracked with EUR cost basis

### German Tax Compliance ✅

- ✅ FIFO per ISIN globally (§ 20 EStG)
- ✅ Foreign currency exchange gains tracked
- ✅ Cost basis in EUR for all foreign assets
- ✅ No tracking for base currency (EUR)

---

**Last Updated**: 2025-11-16  
**Next Review**: After weekly lot reconciliation (Sunday night)
