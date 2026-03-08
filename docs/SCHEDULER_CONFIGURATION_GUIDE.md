# Scheduler Configuration Guide

**Date:** October 2, 2025  
**Status:** ✅ Verified and Documented

---

## 📋 Overview

The NiceFolio scheduler runs 4 types of jobs on different schedules to keep portfolios up-to-date. This guide explains what each job does, when it runs, and which portfolios are covered.

---

## ⏰ Job Schedule

### 1. **Data Sync Job** (Daily at 05:30 AM)
**Configuration:** `app_config.yaml` → `scheduler.data_sync_hour/data_sync_minute`  
**Function:** `run_daily_jobs()`  
**Purpose:** Fetch new data from external sources

**What it does:**
- Syncs transactions from brokers/exchanges
- Updates market prices (crypto, securities, FX, gold)
- Detects internal transfers between wallets

**Portfolios affected:** 3, 5, 6

---

### 2. **Position Reconciliation** (Daily at 10:00 PM)
**Configuration:** `app_config.yaml` → `scheduler.position_reconciliation_hour/minute`  
**Function:** `reconcile_positions()`  
**Purpose:** Recalculate portfolio positions from transactions

**What it does:**
- Recalculates position quantities from transaction history
- Ensures position accuracy before snapshot
- Updates position cost basis

**Portfolios affected:** All (1-7)

**⚠️ Important:** Must run BEFORE snapshot creation for accurate values

---

### 3. **Daily Snapshots** (Daily at 11:00 PM)
**Configuration:** `app_config.yaml` → `scheduler.snapshot_hour/snapshot_minute`  
**Function:** `create_snapshots()`  
**Purpose:** Capture end-of-day portfolio values

**What it does:**
- Creates daily snapshots for all portfolios
- Records total value, deposits, withdrawals
- Calculates daily performance metrics

**Portfolios affected:** All (1-7)

**⚠️ Important:** Must run AFTER position reconciliation for accurate data

---

### 4. **Lot Reconciliation** (Weekly, Configurable)
**Configuration:** `app_config.yaml` → `scheduler.weekly_jobs_day/hour/minute`  
**Function:** `reconcile_lots()` (in `worker/weekly_jobs.py`)  
**Purpose:** Recalculate cost basis using FIFO

**What it does:**
- Rebuilds lots from buy/sell transactions
- Allocates sales to purchases using FIFO method
- Recalculates cost basis and gains for all positions

**Portfolios affected:** 3, 4, 5, 6, 7 (portfolios with lots)

**Configuration:**
```yaml
scheduler:
  weekly_jobs_day: sunday     # monday-sunday
  weekly_jobs_hour: 23
  weekly_jobs_minute: 30
```

**Note:** Runs weekly because lot calculation is computationally intensive. Default is Sunday at 23:30, but can be changed in config.

---

## 🎯 Portfolio Coverage

### Portfolio 1: Cash Portfolio
- **Type:** Manual entry only
- **Automatic Sync:** ❌ Not applicable
- **Daily Jobs:** Reconciliation + Snapshots only

### Portfolio 2: Term Deposits Portfolio
- **Type:** Manual entry only
- **Automatic Sync:** ❌ Not applicable
- **Daily Jobs:** Reconciliation + Snapshots only

### Portfolio 3: Securities ✅
- **Account:** Account 2 (Broker Account)
- **Automatic Sync:** ✅ Yes
- **Daily Jobs:**
  - Data Sync: `sync_ibkr_transactions()`
  - Market Data: `sync_securities_prices()` (yfinance)
  - Reconciliation + Snapshots + Lots

### Portfolio 4: Gold ✅
- **Accounts:**
  - Account 1 (Physical Gold) - Manual entry
  - Account 2 (Broker Account) - Gold ETF (GLD)
- **Automatic Sync:** ✅ Partial (Gold ETF only)
- **Daily Jobs:**
  - Data Sync: `sync_ibkr_transactions()` for Gold ETF (GLD)
  - Market Data: `get_gold_price()` (goldtraders.or.th) for physical gold pricing
  - Reconciliation + Snapshots + Lots
- **Note:** 
  - Gold ETF transactions automatically synced from IBKR
  - Physical gold transactions must be entered manually via app.py

### Portfolio 5: Crypto Long ✅
- **Accounts:**
  - Account 3 (Exchange Account A)
  - Account 5 (Hardware Wallet A)
  - Account 6 (Hardware Wallet B)
  - Account 7 (Hardware Wallet C)
- **Automatic Sync:** ✅ Yes
- **Daily Jobs:**
  - Data Sync: 
    - `sync_binanceth_transactions()` (last 7 days)
    - `sync_crypto_wallets_with_balance()` (all wallets, last 7 days)
    - `detect_internal_transfers()` (link transfers between wallets)
  - Market Data: `sync_crypto_prices()` (CoinMarketCap + yfinance)
  - Reconciliation + Snapshots + Lots

### Portfolio 6: Crypto Short ✅
- **Account:** Account 4 (Exchange Account B)
- **Automatic Sync:** ✅ Yes
- **Daily Jobs:**
  - Data Sync: `sync_binancecom_transactions()` (last 7 days)
  - Market Data: `sync_crypto_prices()` (CoinMarketCap + yfinance)
  - Reconciliation + Snapshots + Lots

### Portfolio 7: Funds Portfolio
- **Type:** Closed/Liquidated (historical data only)
- **Automatic Sync:** ❌ Not applicable
- **Daily Jobs:** Reconciliation + Snapshots + Lots only

---

## 📊 Data Flow Diagram

```
05:30 AM - DATA SYNC
│
├─ IBKR → Portfolio 3 (Securities)
├─ Exchange Account A → Portfolio 5 (Crypto Long)
├─ Exchange Account B → Portfolio 6 (Crypto Short)
├─ Wallets (5-7) → Portfolio 5 (Crypto Long)
│
├─ Market Prices:
│  ├─ Crypto (CoinMarketCap + yfinance)
│  ├─ Securities (yfinance)
│  ├─ FX Rates (exchangerate-api.com)
│  └─ Gold (goldtraders.or.th)
│
└─ Internal Transfer Detection

10:00 PM - POSITION RECONCILIATION
│
└─ All Portfolios (1-7)

11:00 PM - DAILY SNAPSHOTS
│
└─ All Portfolios (1-7)

Sunday 11:30 PM - LOT RECONCILIATION
│
└─ Portfolios with Lots (3-7)
```

---

## 🔧 Configuration Reference

### app_config.yaml
```yaml
scheduler:
  # 1. Main data sync (05:30 AM daily)
  data_sync_hour: 5
  data_sync_minute: 30
  
  # 2. Position reconciliation (10:00 PM daily)
  position_reconciliation_hour: 22
  position_reconciliation_minute: 0
  
  # 3. Daily snapshots (11:00 PM daily)
  snapshot_hour: 23
  snapshot_minute: 0
  
  # 4. Weekly jobs (configurable day and time)
  weekly_jobs_day: sunday     # Options: monday, tuesday, wednesday, thursday, friday, saturday, sunday
  weekly_jobs_hour: 23
  weekly_jobs_minute: 30
```

---

## ✅ Verification Results

**All required functions present:**
- ✅ sync_ibkr_transactions()
- ✅ sync_binanceth_transactions()
- ✅ sync_binancecom_transactions()
- ✅ sync_crypto_wallets_with_balance()
- ✅ detect_internal_transfers()
- ✅ sync_crypto_prices()
- ✅ sync_securities_prices()
- ✅ sync_fx_rates()
- ✅ get_gold_price()
- ✅ reconcile_positions()
- ✅ create_snapshots()
- ✅ reconcile_lots()

**Portfolio coverage:**
- ✅ Portfolio 3 (Securities) - IBKR sync configured
- ✅ Portfolio 4 (Gold) - IBKR sync for Gold ETF + Manual for physical gold
- ✅ Portfolio 5 (Crypto Long) - Exchange Account A + Wallets configured
- ✅ Portfolio 6 (Crypto Short) - Exchange Account B configured

---

## 📝 Notes

### Why 3 Different Time Windows?

1. **05:30 AM (Data Sync):** Early morning to capture overnight trading activity and get fresh market data before the day starts

2. **10:00 PM (Position Reconciliation):** Late evening to process the day's transactions after markets close

3. **11:00 PM (Snapshots):** End of day to capture final portfolio values (after reconciliation completes)

### Why Weekly Lot Reconciliation?

Lot reconciliation is computationally intensive because it:
- Rebuilds entire lot history from all transactions
- Allocates sales to purchases using FIFO
- Recalculates cost basis and gains for all positions

Running it daily would be inefficient. Weekly reconciliation (Sunday night) ensures:
- Cost basis stays accurate
- Minimal performance impact
- Fresh data for the new week

### Manual Entry Portfolios

Portfolios 1 and 2 require full manual transaction entry:
- Portfolio 1 (Cash Portfolio): Personal cash holdings
- Portfolio 2 (Term Deposits Portfolio): Bank deposits

Portfolio 4 (Gold) is hybrid:
- Gold ETF (GLD): Automatically synced from IBKR
- Physical Gold: Manual entry via app.py

These manual transactions are entered through the NiceGUI app (`app.py`) interface.

---

## 🚀 Quick Start

### Run the Scheduler
```bash
python worker/scheduler.py
```

The scheduler will:
1. Load configuration from `app_config.yaml`
2. Schedule all 4 job types
3. Run jobs at configured times
4. Retry failed jobs up to 4 times (30-minute intervals)
5. Log all activity to `logs/app.log`

### Test Individual Jobs
```python
from worker.daily_jobs import run_daily_jobs, reconcile_positions, create_snapshots
from worker.weekly_jobs import reconcile_lots, run_weekly_jobs

# Test data sync
run_daily_jobs()

# Test position reconciliation
reconcile_positions()

# Test snapshot creation
create_snapshots()

# Test lot reconciliation (weekly job)
reconcile_lots()

# Test all weekly jobs
run_weekly_jobs()
```

---

## 🐛 Troubleshooting

### Job Fails to Run
1. Check `logs/app.log` for error messages
2. Verify API credentials are configured (IBKR, Binance, etc.)
3. Test individual functions in Python REPL
4. Check network connectivity to external APIs

### Missing Transactions
1. Verify account is properly configured in `accounts_config.yaml`
2. Check date range in sync functions (default: last 7 days)
3. Verify API credentials have correct permissions
4. Check if transactions exist in source system

### Incorrect Portfolio Values
1. Ensure position reconciliation runs before snapshots
2. Verify market prices are syncing correctly
3. Check FX rates for currency conversions
4. Run lot reconciliation if cost basis seems wrong

---

## 📚 Related Documentation

- **daily_jobs.py:** Contains all daily job implementations with portfolio coverage comments
- **weekly_jobs.py:** Contains weekly job implementations (lot reconciliation, etc.)
- **scheduler.py:** Manages job scheduling and retries
- **app_config.yaml:** Configuration file with timing settings
- **AI_AGENT_ONBOARDING.md:** Complete developer guide
- **SYNC_READINESS_REPORT.md:** Portfolio compatibility verification

---

**Last Updated:** October 2, 2025  
**Verified By:** AI Agent (GitHub Copilot)
