# AI Agent Onboarding Guide
**Portfolio Tracker v3 - Complete Development Context**

> This document provides everything an AI agent needs to understand, extend, and complete the codebase.
> Read this BEFORE making any code changes or additions.

---

## Table of Contents
1. [Quick Start](#quick-start)
2. [Architectural Principles](#architectural-principles)
3. [Coding Standards & Conventions](#coding-standards--conventions)
4. [Pattern Recognition](#pattern-recognition)
5. [When to Use Which Pattern](#when-to-use-which-pattern)
6. [Common Tasks & Solutions](#common-tasks--solutions)
7. [Testing Guidelines](#testing-guidelines)
8. [What's Complete vs Incomplete](#whats-complete-vs-incomplete)
9. [Common Pitfalls to Avoid](#common-pitfalls-to-avoid)
10. [Decision Framework](#decision-framework)

---

## Quick Start

### First Things to Read
1. **readme.txt** - Complete architecture overview (336 lines)
2. **ARCHITECTURE_PATTERNS_SUMMARY.txt** - Why the architecture exists
3. **This file** - How to extend the codebase correctly

### Architecture in 30 Seconds
```
Service Layer (API calls, cacheable, NO database)
    ↓
CRUD Layer (Database operations, logging, error counting)
    ↓
Parser Layer (Transform API responses to model format)
```

**Golden Rule:** Each layer does ONE thing. Don't mix responsibilities.

---

## Architectural Principles

### The Three-Layer Pattern

#### Service Layer (`service/*.py`)
**Responsibility:** API orchestration ONLY

**Rules:**
- ✅ Make API calls to external services
- ✅ Use @cache decorator (API calls are expensive)
- ✅ Return parsed data or None
- ✅ Handle API-specific errors
- ❌ NO database operations
- ❌ NO session management
- ❌ NO direct model imports (except for type hints)

**Example:**
```python
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_crypto_prices_from_coinmarketcap() -> Optional[dict]:
    """Cacheable API call only - no database operations."""
    try:
        response = make_api_call(url, headers=headers)
        return parse_coinmarketcap_prices(response)  # Call parser
    except Exception as e:
        logger.error(f"API error: {e}", exc_info=True)
        return None
```

#### CRUD Layer (`crud/*.py`)
**Responsibility:** Database operations with business logic

**Rules:**
- ✅ Create/Read/Update/Delete database records
- ✅ Log all operations with counts
- ✅ Return tuple[int, int] for (success_count, failure_count)
- ✅ Handle database errors gracefully
- ✅ Use transactions where appropriate
- ✅ Call parsers to transform data
- ❌ NO API calls
- ❌ NO external service dependencies

**Example:**
```python
def ingest_market_prices(db: Session, prices: List[dict]) -> tuple[int, int]:
    """
    Ingest market prices with logging and error counting.
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    success = 0
    failed = 0
    
    logger.info(f"Starting ingestion of {len(prices)} market prices")
    
    for price_data in prices:
        try:
            # Create or update database record
            result = upsert_market_data(db, **price_data)
            success += 1
        except Exception as e:
            logger.error(f"Failed to ingest price for {price_data.get('symbol')}: {e}")
            failed += 1
    
    logger.info(f"Price ingestion complete. Success: {success}, Failed: {failed}")
    return success, failed
```

#### Parser Layer (`crud/parsers/*.py`)
**Responsibility:** Transform raw API responses to database model format

**Rules:**
- ✅ Pure transformation functions (no I/O)
- ✅ Validate and sanitize data
- ✅ Return standardized dict format or None
- ✅ Handle missing/malformed data gracefully
- ✅ Use Decimal for financial values
- ❌ NO API calls
- ❌ NO database operations
- ❌ NO external dependencies

**Example:**
```python
def parse_coinmarketcap_prices(api_response: dict) -> List[dict] | None:
    """
    Parse CoinMarketCap API response into MarketData format.
    
    Returns:
        List[dict]: Standardized price data with keys:
            - symbol: str
            - price: Decimal
            - currency: str
            - source: str
    """
    try:
        if 'data' not in api_response:
            logger.warning("No 'data' key in CoinMarketCap response")
            return None
        
        prices = []
        for symbol, data in api_response['data'].items():
            try:
                price = data['quote']['USD']['price']
                prices.append({
                    'symbol': symbol,
                    'price': Decimal(str(price)),
                    'currency': 'USD',
                    'source': 'coinmarketcap'
                })
            except (KeyError, TypeError) as e:
                logger.warning(f"Failed to parse {symbol}: {e}")
                continue
        
        return prices if prices else None
        
    except Exception as e:
        logger.error(f"Failed to parse CoinMarketCap response: {e}")
        return None
```

### Special Case: Blockchain Providers

**Pattern:** Service → Provider (with embedded normalization) → CRUD

**Why different?**
- Blockchain APIs return protocol-specific formats (UTXO vs account model)
- Transformation logic is tightly coupled to API structure
- Each blockchain is fundamentally different
- API failover requires understanding data format

**Example:**
```python
# service/blockchain_providers/btc_provider.py
def get_transactions(address: str, days_back: int = 30) -> List[dict]:
    """
    Fetch Bitcoin transactions with embedded normalization.
    
    This combines API call + transformation because:
    1. UTXO model is complex (inputs/outputs need interpretation)
    2. Address derivation from xpub requires protocol knowledge
    3. Failover between APIs requires format understanding
    """
    # Fetch from API
    response = make_api_call(f"https://blockchain.info/rawaddr/{address}")
    
    # Transform immediately (embedded normalization)
    transactions = []
    for tx in response.get('txs', []):
        # Complex UTXO → transaction mapping
        normalized_tx = {
            'type': 'transfer_in' if is_incoming(tx, address) else 'transfer_out',
            'amount': calculate_amount(tx, address),
            # ... protocol-specific logic
        }
        transactions.append(normalized_tx)
    
    return transactions
```

---

## Coding Standards & Conventions

### Naming Conventions

#### Functions
```python
# Service Layer - API calls
fetch_*_from_*()          # Example: fetch_crypto_prices_from_coinmarketcap()
sync_*()                  # Example: sync_crypto_prices() (orchestration)

# CRUD Layer - Database operations
ingest_*()               # Example: ingest_market_prices()
get_*()                  # Example: get_latest_price()
create_*()               # Example: create_transaction_idempotent()
upsert_*()               # Example: upsert_market_data()

# Parser Layer - Transformations
parse_*()                # Example: parse_coinmarketcap_prices()

# Blockchain Providers
get_transactions()       # Standard interface for all providers
get_balance()            # Standard interface for all providers
```

#### Return Types
```python
# Service functions
-> Optional[dict]        # Single result
-> Optional[List[dict]]  # Multiple results
-> None                  # On failure

# CRUD functions
-> tuple[int, int]       # (success_count, failure_count)
-> Optional[Model]       # Single database record
-> List[Model]           # Multiple database records

# Parser functions
-> dict | None           # Single parsed item
-> List[dict] | None     # Multiple parsed items
```

### Import Organization
```python
# Standard library
import os
from typing import Optional, List
from decimal import Decimal

# Third-party
import requests
import yfinance as yf
from sqlalchemy import func

# Local - Layer dependencies
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE
from utils.api_client import make_api_call
from crud.parsers.marketdata_parser import parse_coinmarketcap_prices
from crud.crud_market_fx import ingest_market_prices
from models import MarketData
from database import SessionLocal

# Logger at module level
logger = get_logger(__name__)
```

### Error Handling Pattern
```python
# Service Layer - Log and return None
try:
    response = make_api_call(url)
    return parse_response(response)
except Exception as e:
    logger.error(f"API call failed: {e}", exc_info=True)
    return None

# CRUD Layer - Log and count failures
success = 0
failed = 0
for item in items:
    try:
        process_item(item)
        success += 1
    except Exception as e:
        logger.error(f"Failed to process {item}: {e}")
        failed += 1
return success, failed

# Parser Layer - Validate and return None
try:
    if 'required_key' not in data:
        logger.warning("Missing required key")
        return None
    return transformed_data
except Exception as e:
    logger.warning(f"Parse failed: {e}")
    return None
```

### Logging Standards
```python
# Use appropriate log levels
logger.info("Starting sync operation")        # Normal operations
logger.warning("API rate limit hit")          # Recoverable issues
logger.error("Database connection failed")    # Serious errors

# Include context in messages
logger.info(f"Synced {count} transactions for account {account_id}")
logger.error(f"Failed to parse {symbol}: {error_message}")

# Use exc_info for stack traces
logger.error(f"Unexpected error: {e}", exc_info=True)

# Log success/failure counts
logger.info(f"Ingestion complete. Success: {success}, Failed: {failed}")
```

### Caching Guidelines
```python
# Cache API calls (expensive, repeatable)
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_price_from_api(symbol: str) -> Optional[dict]:
    return make_api_call(url)

# DON'T cache database operations (state changes)
def ingest_prices(db: Session, prices: List[dict]) -> tuple[int, int]:
    # No @cache decorator
    pass

# DON'T cache functions with session parameters
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def bad_function(session):  # ❌ Session in cache key!
    pass
```

---

## Pattern Recognition

### How to Identify Which Layer You're Working In

| If you see... | You're in... | You should... |
|--------------|-------------|---------------|
| `make_api_call()`, `requests.get()` | Service Layer | Return data, use @cache, NO database |
| `db.query()`, `db.add()`, `db.commit()` | CRUD Layer | Log operations, count results |
| `BeautifulSoup()`, `data.get()`, `Decimal()` | Parser Layer | Validate, transform, return dict |
| `SessionLocal()`, `try/finally` | Orchestration | Manage sessions, call service+crud |

### Code Smell Detection

❌ **Bad:** Service layer with database operations
```python
# service/marketdata_service.py
def fetch_prices(session):  # ❌ Session parameter in service!
    data = make_api_call(url)
    db.add(MarketData(...))  # ❌ Database operation in service!
    db.commit()
```

✅ **Good:** Proper separation
```python
# service/marketdata_service.py
@cache(ttl=300)
def fetch_prices() -> Optional[dict]:
    return make_api_call(url)

# crud/crud_market_fx.py
def ingest_prices(db: Session, prices: List[dict]) -> tuple[int, int]:
    # Database operations here
```

---

## When to Use Which Pattern

### Decision Tree

```
START: Need to add new functionality
    │
    ├─ Fetching from external API?
    │   └─ YES → Create/update SERVICE layer function
    │       ├─ Add @cache decorator
    │       ├─ Call parser for transformation
    │       └─ Return Optional[dict] or Optional[List[dict]]
    │
    ├─ Saving to database?
    │   └─ YES → Create/update CRUD layer function
    │       ├─ NO @cache decorator
    │       ├─ Log operations with counts
    │       └─ Return tuple[int, int] for batch ops
    │
    ├─ Transforming API response format?
    │   └─ YES → Create/update PARSER layer function
    │       ├─ Pure transformation (no I/O)
    │       ├─ Use Decimal for money
    │       └─ Return dict or List[dict]
    │
    ├─ New blockchain integration?
    │   └─ YES → Create BLOCKCHAIN PROVIDER
    │       ├─ Embed normalization (protocol-specific)
    │       ├─ Implement get_transactions() interface
    │       └─ Handle protocol quirks (UTXO, account model, etc.)
    │
    └─ Orchestrating multiple layers?
        └─ YES → Create ORCHESTRATION function
            ├─ Manage SessionLocal()
            ├─ Call service → parser → crud
            └─ Handle try/finally for cleanup
```

### Examples by Use Case

#### Adding a New Price Source
```python
# 1. Create parser (crud/parsers/marketdata_parser.py)
def parse_new_api_price(response: dict, symbol: str) -> dict | None:
    # Transform API response

# 2. Create service function (service/marketdata_service.py)
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_price_from_new_api(symbol: str) -> Optional[dict]:
    response = make_api_call(url)
    return parse_new_api_price(response, symbol)

# 3. Add to sync orchestration
def sync_crypto_prices():
    # Try CoinMarketCap
    # Try yfinance
    # Try new_api  ← Add here
    # Fallback to database
```

#### Adding a New Exchange
```python
# 1. Create parser (crud/parsers/newexchange_parser.py)
def parse_newexchange_trades(api_response: dict) -> List[dict]:
    # Return Transaction-compatible dicts

# 2. Create CRUD (crud/crud_newexchange.py)
def ingest_newexchange_transactions(db: Session, account_id: int) -> tuple[int, int]:
    # Call service, parse, insert transactions

# 3. Create service (service/newexchange_service.py)
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_trades_from_newexchange(account_id: int) -> Optional[dict]:
    # API call only
```

#### Adding a New Blockchain
```python
# Create provider (service/blockchain_providers/newchain_provider.py)
def get_transactions(address: str, days_back: int = 30) -> List[dict]:
    """
    Fetch and normalize transactions.
    Embedded normalization due to protocol-specific logic.
    """
    # 1. Fetch from blockchain API
    # 2. Normalize to Transaction format (embedded)
    # 3. Return List[dict]

def get_balance(address: str) -> Decimal:
    """Get current balance for address."""
    # Protocol-specific balance calculation

# Update crypto_wallet_service.py
BLOCKCHAIN_PROVIDERS = {
    'BTC': btc_provider,
    'ETH': eth_provider,
    'NEWCHAIN': newchain_provider,  # ← Add here
}
```

---

## Common Tasks & Solutions

### Task: Add Failover for Existing API

**Before:**
```python
def sync_prices():
    prices = fetch_from_api_a()
    ingest_market_prices(db, prices)
```

**After:**
```python
def sync_prices():
    prices = fetch_from_api_a()
    
    if not prices:
        logger.info("API A failed, trying API B")
        prices = fetch_from_api_b()
    
    if not prices:
        logger.info("All APIs failed, using database fallback")
        prices = get_last_known_prices(db)
    
    if prices:
        ingest_market_prices(db, prices)
```

### Task: Add New Data Field to Existing Model

1. **Update Model** (`models.py`)
```python
class MarketData(Base):
    # ... existing fields
    volume = Column(Numeric(20, 8), nullable=True)  # ← New field
```

2. **Update Parser** to include new field
```python
def parse_api_response(data: dict) -> dict:
    return {
        'symbol': data['symbol'],
        'price': Decimal(data['price']),
        'volume': Decimal(data['volume']) if 'volume' in data else None,  # ← Add
        'currency': 'USD',
        'source': 'api'
    }
```

3. **Update CRUD** if needed (usually automatic with ORM)

4. **Generate migration** (if using Alembic)

### Task: Deprecate Old Function, Add New One

```python
# NEW FUNCTION - Recommended
def sync_crypto_prices():
    """New function following established patterns."""
    # Implementation

# LEGACY FUNCTION - Keep for compatibility
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_and_record_crypto_prices(session):
    """DEPRECATED: Use sync_crypto_prices() instead."""
    import warnings
    warnings.warn(
        "fetch_and_record_crypto_prices() is deprecated. Use sync_crypto_prices() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    
    # Call new function internally
    sync_crypto_prices()
    
    # Return expected format for backward compatibility
    return get_latest_prices(session)
```

---

## Testing Guidelines

### Unit Test Structure
```python
def test_parser_function():
    """Test parser with mock data."""
    # Arrange
    mock_response = {
        'data': {'BTC': {'quote': {'USD': {'price': 50000}}}}
    }
    
    # Act
    result = parse_coinmarketcap_prices(mock_response)
    
    # Assert
    assert result is not None
    assert len(result) == 1
    assert result[0]['symbol'] == 'BTC'
    assert result[0]['price'] == Decimal('50000')
```

### Integration Test Pattern
```python
def test_full_sync_flow():
    """Test complete flow from API to database."""
    # Setup test database
    db = create_test_session()
    
    try:
        # Execute sync
        sync_crypto_prices()
        
        # Verify database state
        prices = db.query(MarketData).all()
        assert len(prices) > 0
        
    finally:
        db.close()
        cleanup_test_db()
```

### What to Test
- ✅ Parser functions with various API response formats
- ✅ CRUD functions with valid/invalid data
- ✅ Error handling (malformed data, missing keys)
- ✅ Edge cases (empty results, None values)
- ❌ Don't mock external APIs in unit tests (use parsers directly)
- ❌ Don't test library code (SQLAlchemy, requests, etc.)

---

## What's Complete vs Incomplete

### ✅ Complete & Working
- **Service Layer**: binancecom, binanceth, ibkr, marketdata, fx, goldtradersth, crypto_wallet
- **CRUD Layer**: crud_base, crud_binancecom, crud_binanceth, crud_ibkr, crud_crypto_wallet, crud_market_fx
- **Parsers**: binancecom, binanceth, ibkr, marketdata, fx
- **Blockchain Providers**: BTC, ETH, BSC, SOL, ADA, XRP (6 blockchains)
- **Utils**: api_client, logging_config, cache_config, symbol_normalizer, app_config, xpub_utils
- **Architecture**: Three-layer pattern fully implemented

### ⚠️ Partially Complete
- **worker/daily_jobs.py**: Needs update to use new sync functions (still using deprecated APIs)
- **Testing**: Some test files exist but coverage is incomplete
- **Documentation**: Core docs complete, but could add more examples

### ❌ Missing / TODO
- **Portfolio calculations**: portfolio_service.py may need enhancement
- **Reconciliation**: reconciliation_service.py implementation details
- **UI**: Streamlit interface (start.py) may be incomplete
- **Database migrations**: Alembic setup may not exist
- **Comprehensive test suite**: Need more integration tests

### 🔄 Migration Pending (Phase 4)
- Update worker/daily_jobs.py to use:
  - `sync_crypto_prices()` instead of `fetch_and_record_crypto_prices()`
  - `sync_securities_prices()` instead of `fetch_and_record_securities_prices()`
  - `sync_fx_rates()` instead of `fetch_and_record_fx_rates()`
  - `sync_gold_price()` instead of `get_gold_price()`

---

## Common Pitfalls to Avoid

### ❌ Pitfall #1: Mixing Layers
```python
# BAD - Service layer doing database operations
@cache(ttl=300)
def fetch_and_save_prices(session):
    data = make_api_call(url)
    session.add(MarketData(...))  # ❌ Wrong layer!
    session.commit()
```

**Fix:** Split into service (API) and CRUD (database) functions.

### ❌ Pitfall #2: Caching Database Operations
```python
# BAD - Caching a database query
@cache(ttl=300)
def get_latest_price(db: Session, symbol: str):  # ❌ Stale data!
    return db.query(MarketData).filter_by(symbol=symbol).first()
```

**Fix:** Only cache API calls. Database queries should always be fresh.

### ❌ Pitfall #3: Parser Doing I/O
```python
# BAD - Parser making API calls
def parse_prices(symbol: str):
    response = requests.get(f"https://api.example.com/{symbol}")  # ❌ I/O in parser!
    return parse_response(response)
```

**Fix:** Service layer makes API calls, parser only transforms data.

### ❌ Pitfall #4: Inconsistent Return Types
```python
# BAD - Sometimes returns dict, sometimes returns None, sometimes raises exception
def fetch_price(symbol: str):
    if symbol == "BTC":
        return {'price': 50000}
    elif symbol == "INVALID":
        raise ValueError("Invalid symbol")  # ❌ Inconsistent!
    else:
        return None
```

**Fix:** Always return Optional[dict] and handle errors with logging.

### ❌ Pitfall #5: Missing Error Context
```python
# BAD - Generic error message
logger.error("Failed to process")  # ❌ What failed? Why?
```

**Fix:** Include context in every log message.
```python
logger.error(f"Failed to ingest price for {symbol}: {error_message}")
```

---

## Decision Framework

### When You're Unsure

Ask yourself these questions:

1. **"Does this function make external API calls?"**
   - YES → Service layer, add @cache, return Optional[dict]
   - NO → Continue to question 2

2. **"Does this function write to the database?"**
   - YES → CRUD layer, return tuple[int, int], log operations
   - NO → Continue to question 3

3. **"Does this function transform data formats?"**
   - YES → Parser layer, pure transformation, return dict
   - NO → Continue to question 4

4. **"Is this blockchain-specific protocol logic?"**
   - YES → Blockchain provider, embed normalization
   - NO → Re-evaluate requirements

### When Patterns Conflict

**Scenario:** Function needs to do both API call AND database operation.

**Solution:** Split into two functions:
```python
# Service layer - API only
@cache(ttl=300)
def fetch_data_from_api() -> Optional[dict]:
    return make_api_call(url)

# CRUD layer - Database only
def ingest_data(db: Session, data: dict) -> tuple[int, int]:
    # Save to database

# Orchestration - Combine them
def sync_data():
    db = SessionLocal()
    try:
        data = fetch_data_from_api()
        if data:
            ingest_data(db, data)
    finally:
        db.close()
```

---

## Quick Reference Card

### Function Template - Service Layer
```python
@cache(ttl=CACHE_TTL, maxsize=CACHE_MAXSIZE)
def fetch_data_from_source(param: str) -> Optional[dict]:
    """Fetch data from external API (cacheable)."""
    try:
        response = make_api_call(url, params={...})
        return parse_source_response(response, param)
    except Exception as e:
        logger.error(f"Failed to fetch from source: {e}", exc_info=True)
        return None
```

### Function Template - CRUD Layer
```python
def ingest_data(db: Session, items: List[dict]) -> tuple[int, int]:
    """Ingest data with logging and error counting."""
    success = 0
    failed = 0
    
    logger.info(f"Starting ingestion of {len(items)} items")
    
    for item in items:
        try:
            # Database operation
            result = create_or_update(db, item)
            success += 1
        except Exception as e:
            logger.error(f"Failed to ingest {item.get('id')}: {e}")
            failed += 1
    
    logger.info(f"Ingestion complete. Success: {success}, Failed: {failed}")
    return success, failed
```

### Function Template - Parser Layer
```python
def parse_source_response(response: dict, param: str) -> dict | None:
    """Transform API response to database model format."""
    try:
        if 'required_key' not in response:
            logger.warning(f"Missing required_key in response for {param}")
            return None
        
        return {
            'field1': response['key1'],
            'field2': Decimal(str(response['key2'])),
            'field3': response.get('key3', 'default'),
            'source': 'source_name'
        }
        
    except Exception as e:
        logger.warning(f"Failed to parse response for {param}: {e}")
        return None
```

### Orchestration Template
```python
def sync_data():
    """Orchestrate data sync from source to database."""
    db = SessionLocal()
    try:
        # 1. Fetch from primary source
        data = fetch_data_from_source_a()
        
        # 2. Fallback to secondary source
        if not data:
            logger.info("Primary source failed, trying fallback")
            data = fetch_data_from_source_b()
        
        # 3. Database fallback
        if not data:
            logger.info("All sources failed, using database fallback")
            data = get_last_known_data(db)
        
        # 4. Ingest
        if data:
            success, failed = ingest_data(db, data)
            logger.info(f"Sync complete: {success} succeeded, {failed} failed")
        else:
            logger.warning("No data available from any source")
            
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)
    finally:
        db.close()
```

---

## Resources

### Key Documentation Files
- **readme.txt** - Complete architecture overview
- **ARCHITECTURE_PATTERNS_SUMMARY.txt** - Pattern justification
- **ARCHITECTURE_ANALYSIS_PARSERS.md** - Detailed pattern analysis
- **MARKETDATA_FX_GOLDTRADERS_REFACTORING_COMPLETE.md** - Refactoring case study
- **MARKETDATA_SERVICES_QUICK_REFERENCE.md** - API reference
- **CRYPTO_WALLET_CRUD_QUICKSTART.md** - Crypto wallet usage guide
- **BLOCKCHAIN_PROVIDERS_COMPLETE.md** - Blockchain provider reference
- **GOLD_SCRAPING_VERIFICATION.md** - Web scraping validation

### Implementation Guides by Blockchain
- **BTC_IMPLEMENTATION_SUMMARY.md** - Bitcoin (UTXO model)
- **ETH_IMPLEMENTATION_SUMMARY.md** - Ethereum (account model, ERC-20)
- **BSC_IMPLEMENTATION_SUMMARY.md** - Binance Smart Chain (BEP-20)
- **SOL_IMPLEMENTATION_SUMMARY.md** - Solana (program-based)
- **ADA_IMPLEMENTATION_SUMMARY.md** - Cardano (UTXO + staking)
- **XRP_IMPLEMENTATION_SUMMARY.md** - Ripple (account + trust lines)

---

## Final Checklist for AI Agents

Before making any code changes, verify:

- [ ] I understand which layer this code belongs to (Service/CRUD/Parser)
- [ ] I'm following the naming conventions for this layer
- [ ] I'm using the correct return type for this layer
- [ ] I'm handling errors appropriately for this layer
- [ ] I'm logging with sufficient context
- [ ] I'm NOT mixing layer responsibilities
- [ ] I'm following the existing patterns in similar files
- [ ] I've read the relevant documentation files
- [ ] I understand why the pattern exists (not just copying blindly)
- [ ] My code is consistent with the architectural principles

---

**Remember:** The architecture exists for a reason. When in doubt, look at existing implementations of similar functionality. The codebase is self-documenting through consistent patterns.

**Questions to ask:**
- "Is there already a service that does something similar?"
- "What pattern did they use?"
- "Why did they make that choice?"
- "Should I follow the same pattern or is my case different?"

**Good luck!** 🚀
