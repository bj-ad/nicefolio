# Symbol Mapping Best Practices

**Date:** January 2025  
**Context:** European ETF handling and data source clarifications

---

## 🎯 TL;DR - European ETF Workflow

**BEFORE buying any European ETF:**

1. Add to `config/symbol_mapping.yaml`
2. Restart container: `docker compose restart nicefolio_gui`
3. Then execute buy order
4. Price sync automatically works ✅

**Why?** European securities need exchange suffixes (VWCE.DE, VUAA.AS) that yfinance requires. Without pre-configuration, price sync will fail.

---

## Complete Workflow: Adding VUAA

### Step 1: Edit Config (BEFORE Purchase)

**File:** `config/symbol_mapping.yaml`

```yaml
VUAA:
  yfinance_symbol: "VUAA.DE"  # ← Exchange suffix for XETRA
  currency: "EUR"
  exchange: "XETRA"
  description: "Vanguard S&P 500 UCITS ETF"
  provider: "yfinance"
```

**Exchange Suffixes:**
- XETRA (Germany): `.DE`
- Euronext Amsterdam (Netherlands): `.AS`
- London Stock Exchange (UK): `.L`
- SIX Swiss Exchange (Switzerland): `.SW`
- US exchanges: No suffix needed

### Step 2: Restart Container

```bash
cd /path/to/nicefolio
docker compose restart nicefolio_gui
```

This reloads `symbol_mapping.yaml` into memory. **Config is only read on startup!**

### Step 3: Execute Buy Order

Use IBKR to buy VUAA normally. IBKR reports:
- `symbol="VUAA"`
- `currency="EUR"`
- `exchange="XETRA"`

Transaction is imported and stored in database.

### Step 4: Wait for Daily Price Sync (1:00 AM ICT)

Daily job runs `sync_securities_prices()`:

```
1. Query database: "Which securities need prices?"
   → Finds VUAA (EUR)

2. Look up symbol mapping: get_symbol_mapping(db, 'VUAA', 'EUR')
   ├─ Check database (SymbolMapping table) → ❌ Not found (first time)
   ├─ Check symbol_mapping.yaml → ✅ FOUND
   │    VUAA: {yfinance_symbol: "VUAA.DE", currency: "EUR"}
   └─ Auto-populate database with this mapping for future

3. Fetch price: yf.Ticker("VUAA.DE")
   → ✅ SUCCESS (yfinance finds VUAA on XETRA)

4. Store price in MarketData table
```

**Result:** Price appears correctly! Future syncs use database (fast lookup).

---

## What If You DON'T Pre-Configure?

### Scenario: Buy First, Configure Later

1. Execute buy order for VWCE
2. Transaction imported: `symbol=VWCE, currency=EUR, exchange=XETRA`
3. Daily price sync runs:
   - Look up mapping: `get_symbol_mapping(db, 'VWCE', 'EUR')`
   - Database: ❌ Not found
   - YAML: ❌ Not found
   - **Defaults:** `yfinance_symbol='VWCE'` (no exchange suffix!)
4. Try to fetch price: `yf.Ticker("VWCE")`
   - ❌ **FAILS** - yfinance can't find ticker without `.DE` suffix
5. No price available → portfolio value calculation incomplete

### How to Fix After the Fact

1. Add VWCE to `symbol_mapping.yaml` with `"VWCE.DE"`
2. Restart container
3. **Delete wrong database entry** (optional but recommended):
   ```sql
   DELETE FROM symbol_mappings WHERE symbol='VWCE' AND currency='EUR';
   ```
4. Wait for next daily sync (or trigger manually)
5. Price sync consults YAML → creates correct mapping → fetches price ✅

---

## Data Source Clarification

### Current Price Sources

**NiceFolio uses yfinance for ALL securities pricing.**

| Asset Class        | Primary Source            | Fallback | Notes                     |
| ------------------ | ------------------------- | -------- | ------------------------- |
| **Crypto**         | CoinMarketCap             | yfinance | Requires CMC API key      |
| **Securities**     | **yfinance**              | Database | US & European stocks/ETFs |
| **Gold (GOLDTHB)** | goldtraders.or.th scraper | Database | Thai gold bar prices      |
| **FX Rates**       | ECB API                   | Database | EUR/USD, EUR/THB          |

### AlphaVantage Status

**AlphaVantage code exists but is OPTIONAL and NOT ACTIVELY USED.**

**Code Location:** `service/marketdata_service.py` (lines 217-229)

**Behavior:**
```python
# Try AlphaVantage ONLY IF API key is set
if ALPHAVANTAGE_API_KEY:
    # Fetch from AlphaVantage
    # ...

# Fallback to yfinance for missing symbols
for symbol in missing:
    yf_ticker = yf.Ticker(symbol)
    # ...
```

**Your Setup:** No `ALPHAVANTAGE_API_KEY` environment variable → AlphaVantage code never runs → You're using **yfinance-only** workflow already.

**Why Keep the Code?**
- Harmless fallback if someone has an AlphaVantage API key
- Provides additional data source option for future
- Doesn't affect performance (only runs if configured)

**Documentation Simplification:** For user-facing docs, treat AlphaVantage as "optional advanced feature" and focus on yfinance workflow.

---

## Three-Tier Lookup System

### Priority Order

When `sync_securities_prices()` needs a price:

```python
mapping = get_symbol_mapping(db, symbol='VUAA', currency='EUR')
```

**Lookup Priority:**
1. **Database (SymbolMapping table)** - Fastest, auto-populated
2. **Config File (symbol_mapping.yaml)** - Manual overrides
3. **Hardcoded Defaults** - USD for securities, CMC for crypto

### Tier 1: Database Lookup

```python
mapping = db.query(SymbolMapping).filter_by(symbol='VUAA', currency='EUR').first()
```

**If found:** Return immediately (fastest path for recurring syncs)

**If not found:** Continue to Tier 2

### Tier 2: YAML Config Lookup

```python
config_mappings = load_symbol_mapping()  # Cached in memory
config_mapping = config_mappings.get('VUAA')

if config_mapping and config_mapping.get('currency') == 'EUR':
    # Found in YAML! Auto-populate database for future
    new_mapping = SymbolMapping(
        symbol='VUAA',
        yfinance_symbol='VUAA.DE',
        currency='EUR',
        exchange='XETRA',
        auto_detected=False  # From config, not auto-detected
    )
    db.add(new_mapping)
    db.commit()
    
    return {'yfinance_symbol': 'VUAA.DE', 'currency': 'EUR'}
```

**If found:** Auto-populate database, return mapping

**If not found:** Continue to Tier 3

### Tier 3: Hardcoded Defaults

```python
# Default behavior for unknown symbols
if asset_class == 'crypto':
    return {'yfinance_symbol': f"{symbol}-{currency}", 'currency': currency}
else:
    # Securities default - often WRONG for European ETFs!
    return {'yfinance_symbol': symbol, 'currency': currency}
```

**This is why pre-configuration is critical for European ETFs!**

---

## Multi-Exchange Handling

### Composite Key: (symbol, currency)

Database constraint:
```sql
CONSTRAINT uc_symbol_currency UNIQUE (symbol, currency)
```

### Example: VUAA on Multiple Exchanges

**Scenario:** You buy VUAA on:
1. XETRA (Germany) - EUR
2. Euronext Amsterdam (Netherlands) - EUR

**Same symbol, same currency, different exchanges.**

### Database Behavior

**First trade (XETRA):**
```python
auto_populate_symbol_mapping(db, 'VUAA', 'EUR', 'XETRA')
# Creates: (VUAA, EUR) → yfinance_symbol="VUAA.DE"
```

**Second trade (Amsterdam):**
```python
auto_populate_symbol_mapping(db, 'VUAA', 'EUR', 'EURONEXT_AMSTERDAM')
# Finds existing (VUAA, EUR) mapping
# Updates last_seen timestamp
# DOES NOT create duplicate
```

**Result:** One mapping for (VUAA, EUR), used for both exchanges.

**Why This Works:**
- VUAA on XETRA and Amsterdam are the **same security** (same ISIN: IE00B3XXRP09)
- Price is identical across EUR exchanges (arbitrage ensures this)
- One yfinance lookup (`yf.Ticker("VUAA.DE")`) provides price for both holdings

### Example: BTC in Multiple Currencies

**Scenario:** You track BTC in:
1. Coinbase (USD)
2. Kraken (EUR)

**Same symbol, different currencies.**

### Database Behavior

**USD holdings:**
```python
auto_populate_symbol_mapping(db, 'BTC', 'USD')
# Creates: (BTC, USD) → yfinance_symbol="BTC-USD"
```

**EUR holdings:**
```python
auto_populate_symbol_mapping(db, 'BTC', 'EUR')
# Creates: (BTC, EUR) → yfinance_symbol="BTC-EUR"
```

**Result:** Two separate mappings, two separate prices.

**Why This Works:**
- BTC-USD and BTC-EUR are different trading pairs
- Prices differ (exchange rate + spread)
- Portfolio needs both prices for accurate valuation

---

## Common Scenarios

### Scenario 1: Adding Multiple European ETFs at Once

**File:** `config/symbol_mapping.yaml`

```yaml
# Add all at once before any purchases
VWCE:
  yfinance_symbol: "VWCE.DE"
  currency: "EUR"
  exchange: "XETRA"
  description: "Vanguard FTSE All-World"

VUAA:
  yfinance_symbol: "VUAA.DE"
  currency: "EUR"
  exchange: "XETRA"
  description: "Vanguard S&P 500"

4GLD:
  yfinance_symbol: "4GLD.DE"
  currency: "EUR"
  exchange: "XETRA"
  description: "Xetra-Gold"

VUSA:
  yfinance_symbol: "VUSA.L"
  currency: "GBP"
  exchange: "LSE"
  description: "Vanguard S&P 500 (London)"
```

**Then:** Restart container once, all ETFs configured for life.

### Scenario 2: US Securities (No Pre-Config Needed)

**US securities work without configuration** because yfinance doesn't need exchange suffixes:

- ✅ VOO (Vanguard S&P 500) - Works as-is
- ✅ VGK (Vanguard European Stock) - Works as-is
- ✅ BND (Vanguard Total Bond) - Works as-is

**Default behavior sufficient:** `yfinance_symbol=symbol` works for US tickers.

### Scenario 3: Gold in THB (Special Case)

**File:** `config/symbol_mapping.yaml`

```yaml
GOLDTHB:
  provider: "goldtraders_scraper"  # ← Custom scraper, not yfinance
  currency: "THB"
  description: "Physical Gold Baht (Gold Traders Thailand)"
  auto_sync: false  # Manual trigger only
```

**Why Special:**
- No yfinance ticker exists for Thai gold bars
- Custom scraper (`service/goldtradersth_service.py`) scrapes goldtraders.or.th
- Price in Thai Baht per gold baht weight unit
- Not synced daily (manual trigger to avoid rate limiting)

### Scenario 4: Stablecoins (USDT, USDC, DAI)

**NO configuration needed** - automatically handled as crypto.

**Behavior:**
- USDT, USDC, DAI detected from blockchain transactions
- Auto-categorized as `asset_class='crypto'`
- Default: `yfinance_symbol="{symbol}-USD"` (e.g., "USDT-USD")
- Fetched from CoinMarketCap or yfinance
- **Tracked as crypto for German tax compliance** (taxable events, cost basis, holding period)

**Why Not Cash:**
- German tax law treats stablecoins as cryptocurrency
- Cannot use "treated as cash" simplification anymore
- Must track each buy/sell as taxable event
- Portfolio tracker correctly handles as crypto

---

## Troubleshooting

### Problem: "Price sync failed for VWCE"

**Symptoms:**
- Error log: `yfinance: No data found for ticker "VWCE"`
- Portfolio value missing VWCE holdings

**Diagnosis:**
```bash
# Check database mapping
docker exec -it nicefolio_db psql -U user -d portfolio_db -c \
  "SELECT * FROM symbol_mappings WHERE symbol='VWCE';"

# If yfinance_symbol is "VWCE" (no .DE suffix) → WRONG
```

**Fix:**
1. Add to `config/symbol_mapping.yaml`:
   ```yaml
   VWCE:
     yfinance_symbol: "VWCE.DE"
     currency: "EUR"
     exchange: "XETRA"
   ```
2. Restart: `docker compose restart nicefolio_gui`
3. Delete wrong DB entry:
   ```sql
   DELETE FROM symbol_mappings WHERE symbol='VWCE' AND currency='EUR';
   ```
4. Wait for next sync or trigger manually

### Problem: "Same ETF on multiple exchanges shows wrong price"

**Example:** Bought VUAA on both XETRA and Amsterdam, but price only appears for XETRA holdings.

**Explanation:** By design! One mapping per (symbol, currency) pair. VUAA price is the same on both exchanges (EUR), so one price suffices.

**Verification:**
```sql
SELECT * FROM symbol_mappings WHERE symbol='VUAA' AND currency='EUR';
-- Should return exactly 1 row
```

**Portfolio Calculation:**
- XETRA holdings: 10 shares × €90.50 = €905
- Amsterdam holdings: 5 shares × €90.50 = €452.50
- **Same price used for both** (correct behavior)

### Problem: "BTC price in EUR not updating"

**Symptoms:**
- BTC-USD price updates correctly
- BTC-EUR price stale or missing

**Diagnosis:**
```sql
SELECT * FROM symbol_mappings WHERE symbol='BTC';
-- Should show TWO rows: (BTC, USD) and (BTC, EUR)
```

**If only one row exists:**
1. Price sync not detecting EUR BTC holdings
2. Check transactions: `SELECT DISTINCT currency FROM crypto_balances WHERE symbol='BTC';`
3. Manually add mapping:
   ```python
   auto_populate_symbol_mapping(db, 'BTC', 'EUR')
   ```

### Problem: "Config changes not taking effect"

**Cause:** Config file only loaded on container startup.

**Solution:** Always restart after editing `symbol_mapping.yaml`:
```bash
docker compose restart nicefolio_gui
```

**Verification:**
```bash
# Check if config reloaded
docker logs nicefolio_gui 2>&1 | grep "symbol_mapping.yaml"
# Should show recent timestamp
```

---

## Summary Checklist

### ✅ Before Buying European ETF
- [ ] Add to `config/symbol_mapping.yaml` with correct exchange suffix
- [ ] Verify currency matches IBKR (usually EUR)
- [ ] Restart container: `docker compose restart nicefolio_gui`
- [ ] Execute buy order

### ✅ Daily Operations
- [ ] Daily sync runs automatically at 1:00 AM ICT
- [ ] Check logs for price sync errors
- [ ] Verify portfolio values update correctly

### ✅ Adding New Exchange
- [ ] European ETF → Requires pre-configuration
- [ ] US security → No configuration needed
- [ ] Crypto → Automatically handled
- [ ] Gold → Use GOLDTHB config (already set)

### ✅ Troubleshooting
- [ ] Check `symbol_mappings` table for wrong entries
- [ ] Verify `yfinance_symbol` has correct exchange suffix
- [ ] Restart container after config changes
- [ ] Delete wrong DB entries if needed
- [ ] Trigger manual sync to test: `python scripts/sync_prices.py` (if script exists)

---

## Reference

**Key Files:**
- `config/symbol_mapping.yaml` - Manual symbol overrides
- `crud/crud_symbol_mapping.py` - Three-tier lookup logic
- `models.py` - SymbolMapping database model
- `service/marketdata_service.py` - Price sync orchestration

**Documentation:**
- `docs/SYMBOL_MAPPING_COMPOSITE_KEY_UPDATE.md` - Technical design details
- `docs/AI_AGENT_ONBOARDING.md` - Full architecture guide
- `.github/copilot-instructions.md` - Coding patterns

**Database Table:**
```sql
\d symbol_mappings  -- View schema in psql
SELECT * FROM symbol_mappings ORDER BY last_seen DESC;  -- Recent mappings
```

---

**Questions?** Check logs first: `docker logs nicefolio_gui | grep -i "price sync"`
