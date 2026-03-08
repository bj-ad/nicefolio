# Binance.th Complete Workflow with Hardware Wallet

**Updated:** December 10, 2025  
**Version:** 2.0 (Reflects portfolio_transfer changes and cash tracking removal)

---

## 🎯 Overview

This guide covers the complete workflow for managing crypto assets that flow through Binance.th from a hardware wallet, including exchange to fiat (THB) and final withdrawal.

**Key Changes (December 10, 2025):**
- ✅ **Crypto deposits/withdrawals now use `portfolio_transfer` type** - Lot-neutral from the start
- ✅ **THB deposit/withdrawal UI removed** - Cash balances not actively tracked
- ✅ **Transactions allocated to Broker Cash Portfolio (portfolio_id=8)** - Proper segregation from manual cash updates
- ✅ **THB withdrawal values are now negative** - Consistent with broker cash transaction patterns

---

## 📋 Workflow Phases

### Phase 1: Hardware Wallet → Binance.th (Crypto Deposit)

**What happens:**
- You transfer crypto (e.g., ADA, BTC, ETH) from hardware wallet to Binance.th exchange

**Recording method:**
- ✅ **AUTOMATIC** via Binance.th API sync
- Transaction type: **`portfolio_transfer`** (lot-neutral)
- Portfolio: Crypto (portfolio_id=5)

**Process:**
1. Navigate to: http://localhost:8080/binanceth-sync
2. Click **"1️⃣ Sync Crypto Transactions"**
3. System fetches deposits/withdrawals from Binance.th API (last 7 days)
4. Creates `portfolio_transfer` transactions with `source='binanceth_crypto_sync'`

**Transaction Structure:**
```python
Transaction(
    type='portfolio_transfer',  # Lot-neutral (preserves original lots)
    symbol='ADA',
    quantity=500,  # Positive for incoming
    value_native=500,  # In ADA, not THB!
    currency_native='ADA',
    portfolio_id=5,  # Crypto portfolio
    source='binanceth_crypto_sync',
    notes='Crypto transfer from hardware wallet. Status: Completed, Network: Cardano'
)
```

**Important Notes:**
- These are **immediately created as `portfolio_transfer`** - no conversion needed
- Daily hardware wallet sync will detect matching transfers and link them via `crypto_transfer_link` table
- Transactions are recorded in **native asset units** (ADA, BTC), not fiat equivalent
- No manual entry needed unless API sync fails

**Lot Tracking:**
- Original lots from hardware wallet are preserved
- Cost basis maintained
- Purchase dates unchanged
- No gain/loss realized

---

### Phase 2: Crypto → Fiat Exchange (ADA → THB)

**What happens:**
- You exchange crypto for Thai Baht (THB) on Binance.th

**Recording method:**
- ⚠️ **MANUAL RECOMMENDED** (for accuracy)
- Transaction type: `sell` (crypto) + `buy` (THB) pair
- Portfolio: Broker Cash Portfolio (portfolio_id=8)

**Process:**

1. **Perform trade in Binance.th app/website**
   - Example: Exchange 500 ADA → 5,000 THB

2. **Capture balance snapshot (BEFORE recording trade)**
   - Click **"2️⃣ Capture Balance Snapshot"**
   - This saves current balances for future reference

3. **Record the trade manually**
   - Click **"3️⃣ Record Crypto → Fiat Trade"**
   - Fill in:
     - **From Asset:** `ADA`
     - **From Amount:** `500`
     - **To Asset:** `THB`
     - **To Amount:** `5000`
     - **Trade Date:** (select date)
     - **Notes:** e.g., "Easy Buy/Sell"
   - Click **"Record Trade"**

**Why Manual Entry?**
- Binance.th API doesn't provide detailed trade history
- Multiple trades from same deposit require precise allocation
- Manual entry ensures accurate cost basis for tax reporting
- Example complex scenario: 10,000 THB → BTC + ETH + XRP + ADA + SOL

**Transaction Structure:**
```python
# Sell transaction (crypto going out)
Transaction(
    type='sell',
    symbol='ADA',
    quantity=-500,  # Negative for outgoing
    value_native=500,
    currency_native='ADA',
    portfolio_id=8,  # Broker Cash Portfolio
    source='binanceth_inferred',
    category='trade'
)

# Buy transaction (fiat coming in)
Transaction(
    type='buy',
    symbol='THB',
    quantity=5000,  # Positive for incoming
    value_native=5000,
    currency_native='THB',
    portfolio_id=8,  # Broker Cash Portfolio
    source='binanceth_inferred',
    category='trade'
)
```

**Lot Impact:**
- Sell transaction **closes lots** from crypto holdings
- Realizes capital gains/losses
- Buy transaction **opens new THB lot** (usually immediately closed by withdrawal)

---

### Phase 3: Fiat Withdrawal (THB → Bank Account)

**What happens:**
- You withdraw Thai Baht from Binance.th to your bank account

**Recording method:**
- ❌ **NOT AVAILABLE IN UI** (commented out as of Dec 10, 2025)
- Reason: Cash balances are not actively tracked
- Alternative: Record manually via Python if needed

**Current Status:**
THB deposit/withdrawal functionality has been **removed from the UI** because:
- Cash balances in Binance.th are transient (used only for exchanges)
- Tracking cash separately adds complexity without portfolio tracking benefit
- Focus is on crypto holdings and crypto-to-fiat exchanges

**If you need to record THB withdrawal via Python:**

```python
from service.binanceth_manual_service import record_thb_transaction
from datetime import datetime
from decimal import Decimal

# Record THB withdrawal
tx_id = record_thb_transaction(
    portfolio_id=8,  # Broker Cash Portfolio
    account_id=<your_binanceth_account_id>,
    transaction_type='withdrawal',
    amount_thb=Decimal('5000.00'),
    fee_thb=Decimal('20.00'),  # Default withdrawal fee from config
    transaction_date=datetime(2025, 12, 10),
    notes='Bank transfer to MyBank'
)
```

**Transaction Structure:**
```python
Transaction(
    type='withdrawal',
    symbol='THB',
    quantity=-5000,  # Negative for outgoing
    value_native=-5000,  # NEGATIVE (updated Dec 10, 2025)
    currency_native='THB',
    fee=20,  # Separate field (always positive)
    exchange_rate_to_base=0.026,  # THB/EUR rate if available
    value_base=-130,  # Negative EUR equivalent
    portfolio_id=8,  # Broker Cash Portfolio (changed from Cash Portfolio)
    source='binanceth_manual',
    category='external_transfer'
)
```

**Key Changes from Previous Version:**
- ✅ `value_native` is now **negative** for withdrawals (was positive before)
- ✅ `portfolio_id` is now **8 (Broker Cash Portfolio)** (was 1 - Cash Portfolio before)
- ✅ `value_base` is now **negative** for withdrawals (was positive before)
- Fee remains separate and always positive

---

## 📊 Portfolio Allocation Summary

| Transaction Type                               | Portfolio        | Portfolio ID | Update Method   |
| ---------------------------------------------- | ---------------- | ------------ | --------------- |
| Crypto deposit/withdrawal (portfolio_transfer) | Crypto           | 5            | Automatic       |
| Crypto → Fiat trade (sell/buy)                 | Broker Cash Portfolio | 8            | Manual          |
| Fiat withdrawal (if recorded)                  | Broker Cash Portfolio | 8            | Manual (Python) |

**Why Broker Cash Portfolio?**
- Separates automatic daily broker syncs from manual monthly cash updates
- Cash Portfolio (portfolio_id=1) is for bank accounts and physical cash
- Broker Cash Portfolio (portfolio_id=8) is for auto-synced cash from IBKR and Binance.th

**Previous Incorrect Behavior:**
- ❌ All Binance.th transactions went to Cash Portfolio (portfolio_id=1)
- ❌ Mixed manual and automatic updates in same portfolio
- ❌ THB withdrawal value_native was positive (inconsistent with broker cash pattern)

---

## ❓ Frequently Asked Questions

### Q: Will crypto deposits automatically become portfolio_transfers?

**A: They already are!**

As of December 10, 2025, crypto deposits/withdrawals are **immediately created as `portfolio_transfer` type**. No conversion or waiting needed.

**How it works:**
1. You click "Sync Crypto Transactions"
2. System creates `portfolio_transfer` transactions from Binance.th API
3. Daily hardware wallet sync (01:00 ICT) detects matching transfers on hardware wallet side
4. System links the two `portfolio_transfer` transactions via `crypto_transfer_link` table
5. Both sides preserve original lots (lot-neutral)

**No manual conversion needed** - transactions are created correctly from the start.

---

### Q: Why are crypto deposits recorded in BTC/ADA instead of THB value?

**A: This is CORRECT behavior!**

Crypto transfers should be recorded in **native asset units**, not fiat equivalent.

**Reasoning:**
- These are asset transfers, not currency conversions
- Recording in native units preserves accurate lot tracking
- Value in EUR/THB is calculated dynamically based on market prices
- Consistent with how securities and other assets are recorded

**Example - CORRECT:**
```
Deposit: 500 ADA
- symbol: 'ADA'
- quantity: 500 (in ADA)
- value_native: 500 (in ADA)
- currency_native: 'ADA'
- value_base: 140 EUR (calculated from market price ~0.28 EUR/ADA)
```

**Example - WRONG:**
```
Deposit: 500 ADA
- symbol: 'ADA'
- quantity: 5000 (in THB) ❌ WRONG!
- value_native: 5000 (in THB) ❌ WRONG!
- currency_native: 'THB' ❌ WRONG!
```

---

### Q: Why was THB deposit/withdrawal removed from the UI?

**A: Cash balances are not actively tracked.**

**Reasoning:**
- Focus is on crypto holdings and crypto-to-fiat exchanges
- THB cash balance in Binance.th is transient (used only for immediate exchanges/withdrawals)
- Tracking cash separately adds complexity without portfolio value
- Broker Cash Portfolio already captures cash impact from trades

**What you should track:**
- ✅ Crypto deposits/withdrawals (automatic via API)
- ✅ Crypto → Fiat exchanges (manual for accuracy)
- ❌ Fiat deposits/withdrawals (not needed for portfolio tracking)

**If you absolutely need to track cash:**
- Use Python function `record_thb_transaction()` directly
- Or wait for potential UI re-enable in future update

---

### Q: What's the difference between `portfolio_transfer` and `transfer_out`/`transfer_in`?

**A: Lot tracking behavior.**

| Type                 | Behavior                                    | Use Case                                   |
| -------------------- | ------------------------------------------- | ------------------------------------------ |
| `portfolio_transfer` | **Lot-neutral** - Preserves original lots   | Moving assets between accounts you control |
| `transfer_out`       | **Closes lots** - Realizes gains/losses     | Selling, gifting, disposing                |
| `transfer_in`        | **Opens new lots** - Creates new cost basis | Buying, receiving, acquiring               |

**Example Scenario:**

You have 100 ADA purchased at 0.50 EUR each (cost basis: 50 EUR total).

**Case 1: Transfer to Binance.th** (portfolio_transfer)
- ✅ Lot preserved: 100 ADA @ 0.50 EUR cost basis
- ✅ No gain/loss realized
- ✅ Original purchase date maintained
- ✅ Used for: Hardware wallet ↔ Binance.th transfers

**Case 2: Sell ADA for THB** (transfer_out → becomes `sell`)
- ❌ Lot closed: Realize gain/loss based on current market price
- If current price is 0.70 EUR: Gain = (0.70 - 0.50) × 100 = 20 EUR
- Used for: Crypto → Fiat exchanges

**Case 3: Buy ADA with THB** (transfer_in → becomes `buy`)
- ❌ New lot opened: 100 ADA @ current market price
- New cost basis established
- New purchase date set
- Used for: Fiat → Crypto purchases

---

## 🔧 Configuration

### Lookback Period

Crypto sync lookback period is configured in `config/app_config.yaml`:

```yaml
binanceth:
  sync_lookback_days: 7  # How many days back to fetch transactions
```

**When changed:**
- No code restart needed
- Takes effect on next sync operation
- GUI displays current value dynamically

### Default Fees

THB withdrawal fee is configured in `config/app_config.yaml`:

```yaml
binanceth:
  thb_withdrawal_fee: 20.00  # Default fee in THB
```

---

## 🐛 Troubleshooting

### Issue: Crypto deposits show in BTC/ADA instead of THB

**Status:** ✅ This is CORRECT behavior (not a bug)

**Explanation:**
- Crypto transfers are recorded in native asset units
- Value in fiat is calculated separately based on market prices
- See FAQ above for detailed explanation

---

### Issue: FX rate shows NULL / Value in EUR is 0

**Cause:** ECB (European Central Bank) publishes FX rates once daily at ~22:00 ICT (16:00 CET)

**Solution:**
- Wait for daily FX sync at 01:00 ICT (runs after ECB publishes)
- Or manually trigger FX rate update:
  ```bash
  cd /path/to/nicefolio
  docker compose exec nicefolio_worker python -c "from service.fx_service import sync_fx_rates; sync_fx_rates()"
  ```

**When FX rates are available:**
- `exchange_rate_to_base` will be populated
- `value_base` will show EUR equivalent
- Historical rates are backfilled automatically

---

### Issue: THB withdrawal shows positive value

**Status:** ✅ FIXED (December 10, 2025)

**Previous behavior:**
- `value_native` was positive for withdrawals (inconsistent)

**Current behavior:**
- `value_native` is negative for withdrawals (consistent with broker cash)
- `quantity` is negative
- `fee` is separate and positive
- See Phase 3 above for transaction structure

---

### Issue: Transactions went to wrong portfolio (Cash Portfolio instead of Broker Cash Portfolio)

**Status:** ✅ FIXED (December 10, 2025)

**Previous behavior:**
- All Binance.th transactions used first portfolio (Cash Portfolio, id=1)

**Current behavior:**
- Hardcoded to use Broker Cash Portfolio (portfolio_id=8)
- Proper segregation of manual vs automatic cash updates

---

## 📚 Related Documentation

- **Main Architecture:** `/docs/AI_AGENT_ONBOARDING.md`
- **Portfolio Configuration:** `/config/portfolio_config.yaml`
- **App Configuration:** `/config/app_config.yaml`
- **FX Rates:** `/docs/ECB_FX_RATE_COMPLIANCE.md`
- **Crypto Wallet Sync:** `/docs/crypto/CRYPTO_WALLET_MANAGER_GUIDE.md`

---

## 🔄 Version History

### Version 2.0 (December 10, 2025)
- ✅ Changed crypto deposits/withdrawals to `portfolio_transfer` type (lot-neutral from start)
- ✅ Removed THB deposit/withdrawal UI (cash not tracked)
- ✅ Changed portfolio allocation from Cash Portfolio (1) to Broker Cash Portfolio (8)
- ✅ Fixed THB withdrawal value_native to be negative
- ✅ Updated all documentation and examples

### Version 1.0 (December 9, 2025)
- Initial documentation
- Covered basic workflow
- Included portfolio_transfer explanation
- Added FX rate troubleshooting
