# XRP Provider Implementation Summary

## Overview
The XRP provider integrates XRP Ledger (XRPL) data into the portfolio tracker with special focus on XRP's unique account reserve system. Unlike other cryptocurrencies, XRP requires permanent reserves that cannot be spent, making accurate balance calculations critical.

## Architecture

### Core Functions
- `get_balance(address)` - Fetches comprehensive balance including reserves
- `get_transactions(address, start_date, end_date, limit)` - Transaction history with reserve tracking
- `get_balance_unified(address)` - Spendable balance only
- `get_transactions_unified(address, ...)` - Standardized transaction format

### API Integration
- **Primary**: XRPL Public API servers (xrplcluster.com, xrpl.ws)
- **Failover**: Multiple XRPL servers with automatic switching
- **Methods**: account_info, account_tx for comprehensive data

### Transaction Categories
- `transfer_in` - Incoming XRP payments
- `transfer_out` - Outgoing XRP payments
- `fee` - Transaction fees (network fees + reserve implications)

## Key Features

### 1. Reserve System Handling
XRP's unique reserve requirements are fully integrated:

```python
# Reserve calculation
base_reserve = 10 XRP    # Account existence requirement
owner_reserve = 2 XRP    # Per owned object (trust lines, offers)
total_reserved = base_reserve + (owner_count * owner_reserve)
spendable = total_balance - total_reserved
```

### 2. Account Creation Detection
- Monitors first transaction to detect account opening
- Tracks the "account opening fee" (base reserve)
- Creates special transaction record for reserve lockup

### 3. Multi-Server Failover
Automatic switching between XRPL servers:
- xrplcluster.com (primary)
- s1.ripple.com (fallback)
- s2.ripple.com (secondary fallback)

### 4. Comprehensive Balance Breakdown
```json
{
  "total": 125.5,
  "available": 113.5,      // Spendable amount
  "reserved": 12.0,        // Locked reserves
  "reserve_breakdown": {
    "base_reserve": 10.0,
    "owner_count": 1,
    "owner_reserve": 2.0
  }
}
```

## Reserve System Details

### Base Reserve (10 XRP)
- Required for account activation
- Cannot be spent - permanently locked
- Prevents spam account creation

### Owner Reserve (2 XRP per object)
- Additional reserve for each owned ledger object
- Objects include: trust lines, offers, escrows
- Automatically calculated from account data

### Account Opening Process
1. First transaction creates account
2. Base reserve (10 XRP) is permanently locked
3. Special transaction record created for tracking
4. Subsequent transactions operate normally

## Transaction Normalization

### Standard Payment Processing
```python
def _normalize_xrp_payment(tx, meta, wallet_address, occurred_at, tx_hash):
    # Handle incoming/outgoing amounts
    # Calculate network fees
    # Detect reserve implications
    # Create normalized transaction records
```

### Account Creation Handling
```python
def _process_account_creation(delivered_amount, occurred_at, tx_hash):
    # Detect first transaction
    # Create transfer_in for delivered amount
    # Create fee record for account opening reserve
    # Mark with special notes
```

## Configuration

### Environment Variables
No API keys required - uses public XRPL endpoints

### Constants
```python
XRP_BASE_RESERVE = 10.0     # Current base reserve
XRP_OWNER_RESERVE = 2.0     # Current owner reserve per object
XRP_DROPS_PER_XRP = 1000000 # XRP denomination conversion
```

### Cache Settings
- Balance cache: 10 minutes (600s)
- Transaction cache: 5 minutes (300s)

## Error Handling

### Server Failover
```python
XRPL_SERVERS = [
    "https://xrplcluster.com",
    "https://s1.ripple.com:51234", 
    "https://s2.ripple.com:51234"
]
```

### Graceful Degradation
- Automatic server switching on failure
- Error logging with server identification
- Retry logic with exponential backoff

## Testing

### Test Coverage
- Address validation (classic and X-Address formats)
- Reserve calculation accuracy
- Account creation detection
- Multi-server failover
- Balance vs spendable amount distinction

### Sample Test Address
- Ripple address: `rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH`

## Special Considerations

### Account Existence
- Non-existent accounts return zero balance
- Account summary explains reserve requirements
- First transaction requires minimum funding

### Address Formats
- **Classic**: Starts with 'r', 25-34 characters
- **X-Address**: Starts with 'X', 47-48 characters
- Both formats supported and validated

### Reserve Changes
- Reserve amounts can change via network amendments
- Current implementation uses stable historical values
- Ready for dynamic reserve fetching in future

## Unified Functions

### Balance Functions
```python
get_balance_unified(address)              # Spendable amount only
get_total_balance_including_reserves(address)  # Complete breakdown
get_account_summary(address)              # Full account analysis
```

### Utility Functions
```python
validate_xrp_address(address)            # Format validation
get_reserve_requirements()               # Current reserve info
```

## Integration Points

### Portfolio Service
- Spendable balance used for portfolio calculations
- Total balance available for reporting
- Reserve information preserved for transparency

### Transaction Processing
- All transactions normalized to standard format
- Reserve implications clearly documented
- Account opening fees properly categorized

## Performance Optimizations

### Caching Strategy
- Intelligent caching respects XRPL's fast confirmation times
- Balance data cached longer than transaction data
- Cache invalidation on server errors

### API Efficiency
- Minimal requests through strategic caching
- Batch transaction fetching where possible
- Parallel processing for balance + transactions

## Future Enhancements

### Planned Features
1. Dynamic reserve requirement fetching
2. Trust line and issued currency support
3. Escrow and check transaction handling
4. Payment channel integration

### Extensibility
- Modular design supports additional XRPL features
- Ready for multi-currency (IOUs) expansion
- Plugin architecture for custom transaction types

## Usage Examples

### Check Spendable Balance
```python
from service.blockchain_providers.xrp_provider import get_balance_unified

spendable = get_balance_unified("rYourXRPAddress")
print(f"Spendable XRP: {spendable}")
```

### Get Complete Account Info
```python
from service.blockchain_providers.xrp_provider import get_account_summary

summary = get_account_summary("rYourXRPAddress")
print(f"Total: {summary['total_xrp']} XRP")
print(f"Spendable: {summary['spendable_xrp']} XRP") 
print(f"Reserved: {summary['reserved_xrp']} XRP")
print(f"Note: {summary['reserve_note']}")
```

### Transaction History with Reserves
```python
from service.blockchain_providers.xrp_provider import get_transactions_unified

transactions = get_transactions_unified("rYourXRPAddress", limit=20)
for tx in transactions:
    if 'opening fee' in tx.get('notes', ''):
        print(f"Account creation: {tx['qty']} XRP locked as reserve")
```

## Dependencies
- `utils.api_client` for HTTP requests
- `cachetools` for intelligent caching
- `datetime` for timestamp handling
- No external API keys required

This implementation provides complete XRP Ledger integration with accurate reserve handling and comprehensive account management.