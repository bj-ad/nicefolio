# Quick Reference: Refactored Market Data Services

## Overview
This guide shows how to use the newly refactored market data, FX, and gold price services.

## Key Changes

### Before (Old API)
```python
from database import SessionLocal
from service.marketdata_service import fetch_and_record_crypto_prices
from service.fx_service import fetch_and_record_fx_rates
from service.goldtradersth_service import get_gold_price

db = SessionLocal()
try:
    crypto_prices = fetch_and_record_crypto_prices(db)
    fx_rates = fetch_and_record_fx_rates(db)
    gold_price = get_gold_price(db)
finally:
    db.close()
```

### After (New API)
```python
from service.marketdata_service import sync_crypto_prices, sync_securities_prices
from service.fx_service import sync_fx_rates
from service.goldtradersth_service import sync_gold_price

# No session management needed - handled internally
sync_crypto_prices()
sync_securities_prices()
sync_fx_rates()
sync_gold_price()
```

## Function Reference

### Market Data Service (`service/marketdata_service.py`)

#### sync_crypto_prices()
Sync cryptocurrency prices from CoinMarketCap, yfinance, and database fallback.
```python
from service.marketdata_service import sync_crypto_prices

# Syncs all crypto symbols from Position table where asset_class == 'crypto'
sync_crypto_prices()
```

**Features:**
- Fetches from CoinMarketCap (primary)
- Falls back to yfinance
- Falls back to database
- Logs success/failure counts

#### sync_securities_prices()
Sync securities (stocks/ETFs) prices from AlphaVantage, yfinance, and database fallback.
```python
from service.marketdata_service import sync_securities_prices

# Syncs all securities from Position table where asset_class != 'crypto' and != 'gold_baht'
sync_securities_prices()
```

**Features:**
- Fetches from AlphaVantage (primary)
- Falls back to yfinance
- Falls back to database
- Logs success/failure counts

#### fetch_crypto_prices_from_coinmarketcap()
Direct API call to CoinMarketCap (cacheable, no database operations).
```python
from service.marketdata_service import fetch_crypto_prices_from_coinmarketcap

# Returns parsed data dict, does not write to database
data = fetch_crypto_prices_from_coinmarketcap()
```

**Use case:** When you need API data without database operations.

#### fetch_price_from_alphavantage(symbol: str)
Direct API call to AlphaVantage (cacheable, no database operations).
```python
from service.marketdata_service import fetch_price_from_alphavantage

# Returns parsed data dict for single symbol
data = fetch_price_from_alphavantage("AAPL")
```

**Use case:** When you need a single stock price without database operations.

### FX Service (`service/fx_service.py`)

#### sync_fx_rates()
Sync FX rates from exchangerate-api, yfinance, and database fallback.
```python
from service.fx_service import sync_fx_rates

# Syncs all FX pairs from environment variable FX_PAIRS
sync_fx_rates()
```

**Features:**
- Fetches from exchangerate-api (primary)
- Falls back to yfinance
- Falls back to database
- Logs success/failure counts

#### fetch_fx_rate_from_exchangerate_api(pair: str)
Direct API call to exchangerate-api (cacheable, no database operations).
```python
from service.fx_service import fetch_fx_rate_from_exchangerate_api

# Returns parsed rate dict
data = fetch_fx_rate_from_exchangerate_api("USD/THB")
```

#### fetch_fx_rate_from_yfinance(pair: str)
Direct API call to yfinance (cacheable, no database operations).
```python
from service.fx_service import fetch_fx_rate_from_yfinance

# Returns parsed rate dict
data = fetch_fx_rate_from_yfinance("USD/THB")
```

### Gold Traders Service (`service/goldtradersth_service.py`)

#### sync_gold_price()
Sync gold price from goldtraders.or.th and database fallback.
```python
from service.goldtradersth_service import sync_gold_price

# Syncs GOLDTHB price
sync_gold_price()
```

**Features:**
- Fetches from goldtraders.or.th
- Falls back to database
- Logs success/failure

#### fetch_gold_price_from_goldtraders()
Direct API call to goldtraders.or.th (cacheable, no database operations).
```python
from service.goldtradersth_service import fetch_gold_price_from_goldtraders

# Returns parsed price dict
data = fetch_gold_price_from_goldtraders()
```

## CRUD Operations

If you need direct database operations without API calls:

```python
from database import SessionLocal
from crud.crud_market_fx import (
    ingest_market_prices,
    ingest_fx_rates,
    get_latest_price,
    get_latest_fx_rate
)

db = SessionLocal()

# Insert/update multiple prices
prices = [
    {'symbol': 'BTC', 'price': 50000.0, 'currency': 'USD', 'source': 'manual'},
    {'symbol': 'ETH', 'price': 3000.0, 'currency': 'USD', 'source': 'manual'}
]
success, failed = ingest_market_prices(db, prices)
print(f"Inserted {success} prices, {failed} failed")

# Insert/update multiple FX rates
rates = [
    {'pair': 'USD/THB', 'rate': 35.5, 'base_currency': 'USD', 'quote_currency': 'THB', 'source': 'manual'}
]
success, failed = ingest_fx_rates(db, rates)
print(f"Inserted {success} rates, {failed} failed")

# Get latest price
price = get_latest_price(db, 'BTC')

# Get latest FX rate
rate = get_latest_fx_rate(db, 'USD/THB')

db.close()
```

## Parser Operations

If you need to parse API responses manually:

```python
from crud.parsers.marketdata_parser import (
    parse_coinmarketcap_prices,
    parse_yfinance_price,
    parse_alphavantage_price,
    parse_goldtraders_html
)
from crud.parsers.fx_parser import (
    parse_exchangerate_api_rate,
    parse_yfinance_fx_rate
)

# Parse CoinMarketCap response
api_response = {...}  # Raw API response
prices = parse_coinmarketcap_prices(api_response)

# Parse yfinance ticker
import yfinance as yf
ticker = yf.Ticker("BTC-USD")
price_data = parse_yfinance_price("BTC", ticker)

# Parse FX rate
fx_response = {...}  # Raw API response
rate_data = parse_exchangerate_api_rate(fx_response, "USD/THB")
```

## Migration Guide

### Step 1: Update Daily Jobs
In `worker/daily_jobs.py`:

```python
# OLD CODE (remove this)
from database import SessionLocal
from service.marketdata_service import fetch_and_record_crypto_prices, fetch_and_record_securities_prices
from service.fx_service import fetch_and_record_fx_rates
from service.goldtradersth_service import get_gold_price

db = SessionLocal()
try:
    fetch_and_record_crypto_prices(db)
    fetch_and_record_securities_prices(db)
    fetch_and_record_fx_rates(db)
    get_gold_price(db)
finally:
    db.close()

# NEW CODE (replace with this)
from service.marketdata_service import sync_crypto_prices, sync_securities_prices
from service.fx_service import sync_fx_rates
from service.goldtradersth_service import sync_gold_price

sync_crypto_prices()
sync_securities_prices()
sync_fx_rates()
sync_gold_price()
```

### Step 2: Search for Usage
Search your codebase for these deprecated functions:
- `fetch_and_record_crypto_prices`
- `fetch_and_record_securities_prices`
- `fetch_and_record_fx_rates`
- `get_gold_price`

Replace with:
- `sync_crypto_prices()`
- `sync_securities_prices()`
- `sync_fx_rates()`
- `sync_gold_price()`

### Step 3: Remove Session Management
The new functions handle session management internally, so you can remove:
```python
db = SessionLocal()
try:
    # ... operations
finally:
    db.close()
```

## Testing

### Test Sync Functions
```python
from service.marketdata_service import sync_crypto_prices

# Test crypto sync
sync_crypto_prices()
# Check logs/app.log for results
```

### Test API Functions
```python
from service.marketdata_service import fetch_crypto_prices_from_coinmarketcap

# Test API call only
data = fetch_crypto_prices_from_coinmarketcap()
print(data)
```

### Test CRUD Functions
```python
from database import SessionLocal
from crud.crud_market_fx import ingest_market_prices

db = SessionLocal()
prices = [{'symbol': 'TEST', 'price': 100.0, 'currency': 'USD', 'source': 'test'}]
success, failed = ingest_market_prices(db, prices)
print(f"Success: {success}, Failed: {failed}")
db.close()
```

## Logging

All functions log to `logs/app.log`:
- API call results
- Success/failure counts
- Error details with stack traces
- Fallback usage

Example log output:
```
2025-01-15 10:00:00 - marketdata_service - INFO - Syncing crypto prices from CoinMarketCap
2025-01-15 10:00:01 - marketdata_service - INFO - CoinMarketCap returned 10 prices
2025-01-15 10:00:02 - crud_market_fx - INFO - Crypto price sync complete: 10 succeeded, 0 failed
```

## Best Practices

1. **Use sync_* functions** for production code (handles everything)
2. **Use fetch_* functions** when you need API data without database operations
3. **Use CRUD functions directly** when you have data from other sources
4. **Use parser functions** when you have raw API responses to transform
5. **Always check logs** for sync results and errors

## Troubleshooting

### Issue: "No crypto positions found"
**Solution:** Ensure Position table has records with asset_class == 'crypto'

### Issue: "API key not configured"
**Solution:** Check .env file for:
- COINMARKETCAP_API_KEY
- ALPHAVANTAGE_API_KEY
- EXCHANGE_RATE_API_KEY

### Issue: "No price available from any source"
**Solution:** Check:
1. API keys are valid
2. Symbols are correct
3. Database has fallback data
4. Network connectivity

### Issue: "Deprecation warnings"
**Solution:** Update code to use new sync_* functions instead of legacy functions
