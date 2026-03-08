# Configuration Loaders - Quick Reference Guide

## Overview
Centralized configuration loading system with singleton pattern, caching, and validation.

---

## 1. Source Mapping Loader

### Import
```python
from utils.source_mapping_loader import get_source_mapping_loader
```

### Basic Usage
```python
loader = get_source_mapping_loader()

# Get account ID for a source
account_id = loader.get_account_id('IBKR')  # Returns: 2

# Get default portfolio ID
portfolio_id = loader.get_default_portfolio_id('IBKR')  # Returns: 3

# Get portfolio ID for specific symbol
portfolio_id = loader.get_portfolio_id_for_symbol('IBKR', 'GLD')  # Returns: 4

# Get all symbol mappings for a source
symbol_map = loader.get_symbol_to_portfolio_map('IBKR')
# Returns: {'GLD': 4}

# Get list of all configured sources
sources = loader.get_all_sources()
# Returns: ['IBKR', 'BinanceTH', 'BinanceCOM', ...]
```

### API Methods
| Method | Returns | Description |
|--------|---------|-------------|
| `get_source_mapping(source_name)` | `Optional[Dict]` | Full mapping config for source |
| `get_account_id(source_name)` | `Optional[int]` | Account ID for source |
| `get_default_portfolio_id(source_name)` | `Optional[int]` | Default portfolio ID |
| `get_portfolio_id_for_symbol(source_name, symbol)` | `Optional[int]` | Portfolio ID for symbol |
| `get_symbol_to_portfolio_map(source_name)` | `Dict[str, int]` | Symbol→Portfolio mapping |
| `get_all_sources()` | `List[str]` | All source names |

---

## 2. Accounts Loader

### Import
```python
from utils.accounts_loader import get_accounts_loader
```

### Basic Usage
```python
loader = get_accounts_loader()

# Get all accounts
accounts = loader.get_accounts()
# Returns: [{'id': 1, 'name': '...', 'type': '...', 'currency': '...'}, ...]

# Get account by ID
account = loader.get_account_by_id(2)
# Returns: {'id': 2, 'name': 'Broker Account', 'type': 'Broker', ...}

# Get account by name
account = loader.get_account_by_name('Broker Account')

# Get account name
name = loader.get_account_name(2)  # Returns: 'Broker Account'

# Get accounts by type
brokers = loader.get_accounts_by_type('Broker')
# Returns: [{'id': 2, 'name': 'Broker Account', ...}]

# Check if account exists
exists = loader.account_exists(2)  # Returns: True

# Get all account IDs
ids = loader.get_account_ids()  # Returns: [1, 2, 3, 4, 5, 6, 7]
```

### API Methods
| Method | Returns | Description |
|--------|---------|-------------|
| `get_accounts()` | `List[Dict]` | All accounts |
| `get_account_by_id(account_id)` | `Optional[Dict]` | Account by ID |
| `get_account_by_name(name)` | `Optional[Dict]` | Account by name |
| `get_account_name(account_id)` | `Optional[str]` | Account name |
| `get_account_type(account_id)` | `Optional[str]` | Account type |
| `get_accounts_by_type(account_type)` | `List[Dict]` | Accounts of specific type |
| `get_account_ids()` | `List[int]` | All account IDs |
| `account_exists(account_id)` | `bool` | Whether account exists |

---

## 3. Portfolios Loader

### Import
```python
from utils.portfolios_loader import get_portfolios_loader
```

### Basic Usage
```python
loader = get_portfolios_loader()

# Get all portfolios
portfolios = loader.get_portfolios()
# Returns: [{'id': 1, 'name': '...', 'type': '...', 'base_currency': '...'}, ...]

# Get portfolio by ID
portfolio = loader.get_portfolio_by_id(3)
# Returns: {'id': 3, 'name': 'Securities', 'type': 'securities', ...}

# Get portfolio by name
portfolio = loader.get_portfolio_by_name('Securities')

# Get portfolio name
name = loader.get_portfolio_name(3)  # Returns: 'Securities'

# Get portfolio type
ptype = loader.get_portfolio_type(5)  # Returns: 'crypto'

# Get portfolio currency
currency = loader.get_portfolio_currency(3)  # Returns: 'USD'

# Get portfolios by type
crypto = loader.get_portfolios_by_type('crypto')
# Returns: [{'id': 5, ...}, {'id': 6, ...}]

# Get portfolios by account
portfolios = loader.get_portfolios_by_account(2)

# Check if portfolio exists
exists = loader.portfolio_exists(3)  # Returns: True

# Get all portfolio IDs
ids = loader.get_portfolio_ids()  # Returns: [1, 2, 3, 4, 5, 6, 7]
```

### API Methods
| Method | Returns | Description |
|--------|---------|-------------|
| `get_portfolios()` | `List[Dict]` | All portfolios |
| `get_portfolio_by_id(portfolio_id)` | `Optional[Dict]` | Portfolio by ID |
| `get_portfolio_by_name(name)` | `Optional[Dict]` | Portfolio by name |
| `get_portfolio_name(portfolio_id)` | `Optional[str]` | Portfolio name |
| `get_portfolio_type(portfolio_id)` | `Optional[str]` | Portfolio type |
| `get_portfolio_currency(portfolio_id)` | `Optional[str]` | Base currency |
| `get_portfolios_by_type(portfolio_type)` | `List[Dict]` | Portfolios of type |
| `get_portfolio_ids()` | `List[int]` | All portfolio IDs |
| `portfolio_exists(portfolio_id)` | `bool` | Whether portfolio exists |
| `get_portfolios_by_account(account_id)` | `List[Dict]` | Portfolios for account |

---

## 4. Common Patterns

### Pattern: Get Account and Portfolio for a Source
```python
from utils.source_mapping_loader import get_source_mapping_loader
from utils.accounts_loader import get_accounts_loader
from utils.portfolios_loader import get_portfolios_loader

source_loader = get_source_mapping_loader()
accounts_loader = get_accounts_loader()
portfolios_loader = get_portfolios_loader()

# Get account ID
account_id = source_loader.get_account_id('IBKR')

# Get account details
account = accounts_loader.get_account_by_id(account_id)
print(f"Account: {account['name']} ({account['type']})")

# Get portfolio ID for symbol
portfolio_id = source_loader.get_portfolio_id_for_symbol('IBKR', 'AAPL')

# Get portfolio details
portfolio = portfolios_loader.get_portfolio_by_id(portfolio_id)
print(f"Portfolio: {portfolio['name']} ({portfolio['base_currency']})")
```

### Pattern: Process All Accounts of a Type
```python
from utils.accounts_loader import get_accounts_loader

loader = get_accounts_loader()

# Get all brokerage accounts
for account in loader.get_accounts_by_type('Broker'):
    print(f"Processing {account['name']}...")
    # Do something with each broker account
```

### Pattern: Validate Cross-References
```python
from utils.source_mapping_loader import get_source_mapping_loader
from utils.accounts_loader import get_accounts_loader
from utils.portfolios_loader import get_portfolios_loader

source_loader = get_source_mapping_loader()
accounts_loader = get_accounts_loader()
portfolios_loader = get_portfolios_loader()

# Check if source references valid account
for source_name in source_loader.get_all_sources():
    account_id = source_loader.get_account_id(source_name)
    if not accounts_loader.account_exists(account_id):
        print(f"ERROR: {source_name} references invalid account {account_id}")
    
    portfolio_id = source_loader.get_default_portfolio_id(source_name)
    if not portfolios_loader.portfolio_exists(portfolio_id):
        print(f"ERROR: {source_name} references invalid portfolio {portfolio_id}")
```

### Pattern: Reload Configuration
```python
from utils.source_mapping_loader import load_source_mapping

# Force reload (e.g., after config file update)
config = load_source_mapping(reload=True)
```

---

## 5. Migration Examples

### Before (Old Pattern)
```python
import yaml

# Hard-coded path
with open("config/source_mapping.yaml", "r") as f:
    source_mapping = yaml.safe_load(f)

# Manual dict navigation
IBKR_MAPPING = source_mapping['IBKR']
IBKR_ACCOUNT_ID = IBKR_MAPPING['account_id']

# Complex dict comprehension
IBKR_SYMBOL_MAPPINGS = {
    symbol: mapping['portfolio_id']
    for mapping in IBKR_MAPPING.get('symbol_mappings', [])
    for symbol in mapping.get('symbols', [])
}
```

### After (New Pattern)
```python
from utils.source_mapping_loader import get_source_mapping_loader

# Singleton with caching
loader = get_source_mapping_loader()

# Clean API calls
IBKR_ACCOUNT_ID = loader.get_account_id('IBKR')

# Built-in method
IBKR_SYMBOL_MAPPINGS = loader.get_symbol_to_portfolio_map('IBKR')
```

---

## 6. Validation

### Run Validation Script
```bash
cd /workspaces/portfolio-tracker_v3
PYTHONPATH=/workspaces/portfolio-tracker_v3:$PYTHONPATH python scripts/validate_configs.py
```

### Expected Output
```
============================================================
Portfolio Tracker Configuration Validation
============================================================

Validating Accounts Config...
  ✓ Validated 7 accounts
  ✓ Accounts Config passed

Validating Portfolios Config...
  ✓ Validated 7 portfolios
  ✓ Portfolios Config passed

Validating Source Mapping Config...
  ✓ Validated 6 data sources
  ✓ Source Mapping Config passed

Validating App Config...
  ✓ app_config.yaml valid
  ✓ App Config passed

Validating Symbol Normalization...
  ✓ symbol_normalization.yaml valid
  ✓ Symbol Normalization passed

Validating Cross-References...
  ✓ All cross-references valid
  ✓ Cross-References passed

============================================================
✅ All configuration files are valid!
============================================================
```

---

## 7. Performance

### Caching Behavior
- **First call**: Loads YAML from file, parses, caches result
- **Subsequent calls**: Returns cached result (instant)
- **Reload**: Use `reload=True` to force re-read from disk

### Example
```python
from utils.accounts_loader import get_accounts_loader
import time

# First call - loads from file
start = time.time()
loader = get_accounts_loader()
accounts1 = loader.get_accounts()
time1 = time.time() - start

# Second call - returns cached
start = time.time()
accounts2 = loader.get_accounts()
time2 = time.time() - start

print(f"First call: {time1*1000:.2f}ms")
print(f"Second call: {time2*1000:.4f}ms (cached)")
# Typical: First call ~5ms, Second call ~0.001ms
```

---

## 8. Error Handling

All loaders handle errors gracefully:

```python
from utils.source_mapping_loader import get_source_mapping_loader

loader = get_source_mapping_loader()

# Returns None if source doesn't exist (no exception)
account_id = loader.get_account_id('NonExistentSource')
if account_id is None:
    print("Source not found")

# Check existence before accessing
if loader.get_source_mapping('MySource'):
    # Safe to use
    account_id = loader.get_account_id('MySource')
```

---

## 9. Type Safety

All methods have proper type hints for IDE support:

```python
from utils.portfolios_loader import get_portfolios_loader
from typing import Optional, Dict, Any

loader = get_portfolios_loader()

# IDE knows return type is Optional[Dict[str, Any]]
portfolio: Optional[Dict[str, Any]] = loader.get_portfolio_by_id(3)

# IDE knows return type is List[Dict[str, Any]]
portfolios: List[Dict[str, Any]] = loader.get_portfolios()
```

---

## Summary

✅ **Simple**: 1-2 lines instead of 10-15  
✅ **Fast**: Cached after first load  
✅ **Safe**: Validation on load, None returns (no exceptions)  
✅ **Clean**: No hard-coded paths  
✅ **Typed**: Full IDE autocomplete support  

**Use these loaders instead of directly opening YAML files!**
