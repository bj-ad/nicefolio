# Benchmark Comparison System — Architecture

## Overview

The benchmark comparison system allows portfolios to be compared against market benchmarks. It supports both **individual benchmarks** (e.g., VHVE for securities, BTC for crypto) and a **composite benchmark** built from configurable target allocations with periodic rebalancing.

---

## Design Principles

### 1. No Data Duplication
Benchmark prices are stored in the same `market_data` table as regular asset prices:
- If you hold BTC in your portfolio, benchmark sync skips fetching BTC again
- The `has_price_for_date()` check prevents duplicate API calls

### 2. Centralized Symbol Mapping
Benchmark symbols use the same symbol mapping infrastructure as portfolio assets:

```
Priority Order:
1. Database: SymbolMapping table (primary source)
2. Config File: config/symbol_mapping.yaml (fallback)
3. Auto-detection: Hardcoded defaults (last resort)
```

### 3. Consistent Price Source Infrastructure
Benchmarks use the same price fetching logic as portfolio holdings:

**Crypto Benchmarks (BTC):**
```
CoinMarketCap API (primary) -> yfinance (fallback) -> database (last resort)
```

**Securities Benchmarks (VHVE):**
```
yfinance with symbol_mapping (VHVE -> VHVE.MI for Milan exchange)
```

### 4. All Values from Config
No hardcoded symbols, weights, or labels — everything is driven by `app_config.yaml` and `symbol_mapping.yaml`.

---

## Configuration

### `config/app_config.yaml` — Benchmark Settings

```yaml
benchmarks:
  benchmark_securities:
    symbol: VHVE
    label: VHVE
  benchmark_crypto:
    symbol: BTC
    label: BTC
  risk_free_rate: 4.0

  # Composite benchmark (target allocation strategy)
  target_allocations:
    securities: 0.70    # 70%
    crypto: 0.20        # 20%
    commodities: 0.10   # 10%
  rebalancing_period: monthly  # monthly | quarterly | yearly
```

**What belongs in app_config.yaml:**
- Benchmark symbol names and display labels
- Risk-free rate
- Target allocation weights
- Rebalancing frequency

**What does NOT belong in app_config.yaml:**
- `yfinance_symbol` — handled by `symbol_mapping.yaml`
- `currency`, `exchange`, `provider` — handled by `symbol_mapping.yaml`

### `config/symbol_mapping.yaml` — Symbol Definitions

```yaml
VHVE:
  yfinance_symbol: "VHVE.MI"
  currency: "EUR"
  exchange: "Milan"
  description: "Vanguard FTSE Developed World UCITS ETF (Securities Benchmark)"
  provider: "yfinance"
```

Crypto symbols (e.g., BTC) typically don't need explicit mapping — auto-detection maps `BTC` -> `BTC-USD`.

---

## Individual Benchmarks

### Portfolio Type -> Benchmark Mapping

```
portfolio_type  ->  benchmark
-------------------------------
securities      ->  VHVE (100%)
crypto          ->  BTC (100%)
commodities     ->  75% VHVE + 25% BTC (blended)
```

### Data Flow: Daily Price Sync

```
1. sync_crypto_prices()
   -> Fetches BTC from CoinMarketCap -> stores in market_data
      -> benchmark_service sees BTC exists -> skips

2. sync_securities_prices()
   -> Fetches VHVE from yfinance (via symbol_mapping) -> stores in market_data
      -> benchmark_service sees VHVE exists -> skips

3. sync_benchmark_prices()
   -> Only fetches benchmark symbols NOT already in market_data
```

### Service Layer (`service/benchmark_service.py`)

**Config Getters:**
```python
get_benchmark_securities_config()  # -> {symbol: 'VHVE', label: 'VHVE'}
get_benchmark_crypto_config()      # -> {symbol: 'BTC', label: 'BTC'}
get_risk_free_rate()               # -> 4.0
```

**Benchmark Calculations:**
```python
calculate_benchmark_twr(symbol, start, end)
calculate_blended_benchmark_twr(start, end)
calculate_alpha(portfolio_twr, portfolio_type, ...)
get_benchmark_twr_for_portfolio_type(type, ...)
```

---

## Composite Benchmark

The composite benchmark simulates a diversified portfolio with configurable target allocations and periodic rebalancing. It provides a "Target Composite" comparison that captures the **rebalancing bonus** effect (sell high, buy low).

### How It Works

1. Fetch daily prices for all component symbols (securities, crypto, commodities benchmarks)
2. Start with a hypothetical portfolio valued at 100
3. Apply daily returns to each allocation slice
4. On rebalancing dates, reset back to target weights
5. Output a daily NAV series for comparison

### Core Functions (`apps/core/helpers.py`)

#### `prepare_composite_benchmark_data(db, start_date)`
- Fetches prices for all benchmark symbols from `market_data`
- Returns aligned DataFrame with forward-filled prices
- Benchmark only starts when ALL components have data

#### `calculate_rebalanced_benchmark(market_df, target_allocations, rebalancing_period)`
- Simulates portfolio starting at 100
- Applies daily returns to each slice
- Rebalances on schedule:
  - `monthly`: First day of each month
  - `quarterly`: First day of Jan, Apr, Jul, Oct
  - `yearly`: First day of each year
- Returns Series indexed by date

#### `get_composite_benchmark_label()`
- Generates dynamic label from config: `"Target Composite 70/20/10"`
- Updates automatically when config changes

### Service Integration (`service/benchmark_service.py`)

```python
get_composite_benchmark_normalized_series(start_date, end_date)
# -> Normalized series (rebased to 100) for Growth Comparison chart

calculate_composite_benchmark_twr(start_date, end_date)
# -> Annualized TWR for legend labels and risk/reward

calculate_composite_benchmark_volatility(start_date, end_date)
# -> Annualized volatility (sqrt(252) factor) for risk/reward scatter

get_benchmark_risk_reward_data()
# -> Returns 3 benchmarks: securities, crypto, composite
```

### Chart Integration (`apps/core/charts.py`)

**Growth Comparison Chart:**
- Securities benchmark: black line (width 2)
- Crypto benchmark: grey line (width 2)
- Composite benchmark: amber/orange line (width 2.5)
- All lines show TWR% in legend

**Risk/Reward Scatter:**
- Three efficiency lines from risk-free rate through each benchmark
- Color coding:
  - Securities: dark grey (`#1f2937`)
  - Crypto: light grey (`#6b7280`)
  - Composite: amber (`#f59e0b`)

### Limitations

1. **Data availability**: Composite only starts when ALL components have price data
2. **Rebalancing assumptions**: No transaction costs, perfect execution at close prices
3. **Weekend/holiday handling**: Forward-fill for non-trading days; rebalancing triggers on first available trading day

---

## Adding a New Benchmark

### If symbol is already held in a portfolio

Only update `app_config.yaml` — prices are already synced:
```yaml
benchmarks:
  benchmark_securities:
    symbol: VOO    # Changed from VHVE
    label: VOO
```

### If symbol is NOT held in any portfolio

1. Add to `symbol_mapping.yaml`:
```yaml
VUSA:
  yfinance_symbol: "VUSA.L"
  currency: "GBP"
  exchange: "London"
  description: "Vanguard S&P 500 UCITS ETF"
  provider: "yfinance"
```

2. Update `app_config.yaml`:
```yaml
benchmarks:
  benchmark_securities:
    symbol: VUSA
    label: VUSA
```

3. Restart: `docker compose restart nicefolio_gui`

---

## Verification

```sql
-- Check benchmark prices exist
SELECT symbol, as_of_date, price, currency
FROM market_data
WHERE symbol IN ('VHVE', 'BTC')
ORDER BY as_of_date DESC
LIMIT 10;

-- Check symbol mapping
SELECT symbol, yfinance_symbol, currency, exchange
FROM symbol_mapping
WHERE symbol IN ('VHVE', 'BTC');
```

---

## Three-Layer Architecture Compliance

| Layer | Responsibility | Benchmark Files |
|-------|---------------|-----------------|
| **Service** | API orchestration, no direct calculations | `benchmark_service.py` |
| **Helper** | Pure transformation functions | `apps/core/helpers.py` |
| **Chart** | Visualization only | `apps/core/charts.py` |

Database impact: **zero new tables, zero new columns** — all benchmark data uses existing `market_data`. Composite benchmark is calculated on-the-fly and cached at the service layer.

---

## Architecture Diagram

```
+-----------------------------------------------------+
| config/app_config.yaml                              |
|   benchmarks:                                       |
|     benchmark_securities: {symbol: VHVE}            |
|     benchmark_crypto: {symbol: BTC}                 |
|     target_allocations: {sec: 0.70, crypto: 0.20,   |
|                          commodities: 0.10}         |
|     rebalancing_period: monthly                     |
+---------------------------+-------------------------+
                            |
                            v
+-----------------------------------------------------+
| config/symbol_mapping.yaml                          |
|   VHVE: {yfinance_symbol: "VHVE.MI", currency: EUR}|
|   (BTC auto-detected: BTC -> BTC-USD)               |
+---------------------------+-------------------------+
                            |
                            v
+-----------------------------------------------------+
| service/benchmark_service.py                        |
|   Individual: TWR, alpha, risk/reward per type      |
|   Composite: normalized series, TWR, volatility     |
+---------------------------+-------------------------+
                            |
                            v
+-----------------------------------------------------+
| Database: market_data table                         |
|   VHVE | 61.50 | EUR | yfinance                    |
|   BTC  | 45000 | USD | coinmarketcap               |
|   (shared with portfolio holdings)                  |
+-----------------------------------------------------+
```

---

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| "No yfinance_symbol configured for X" | Missing from `symbol_mapping.yaml` | Add entry with yfinance_symbol, currency, exchange |
| Duplicate price fetching | `has_price_for_date()` not matching | Verify symbol name matches exactly (case-sensitive) |
| Alpha calculation returns None | Missing benchmark prices for date range | Run `backfill_benchmark_prices.py` |
| Composite benchmark starts late | Component data availability | Composite requires ALL components to have data — expected behavior |

---

## Best Practices

1. **Use internal symbols** in `app_config.yaml` (e.g., `VHVE`, not `VHVE.MI`)
2. **Centralize symbol mappings** — define once in `symbol_mapping.yaml`, never duplicate
3. **Reuse existing infrastructure** — benchmarks use the same sync and mapping code as portfolio assets
4. **Hold benchmark symbols** in a portfolio when practical — ensures data quality and prevents duplicate fetching
