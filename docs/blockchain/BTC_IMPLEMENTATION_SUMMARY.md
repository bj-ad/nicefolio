# Bitcoin (BTC) Provider Implementation Summary

## Overview
The Bitcoin provider integrates Bitcoin blockchain data into the portfolio tracker with comprehensive support for both single addresses and extended public keys (XPUBs). It handles Bitcoin's unique UTXO model and provides robust transaction tracking with multiple API fallbacks.

## Architecture

### Core Functions
- `get_balance(address)` - Fetches BTC balance for single address
- `get_transactions(address, start_date, end_date, limit)` - Transaction history for address
- `get_balance_or_xpub(address_or_xpub)` - Unified function for addresses or XPUBs
- `get_transactions_unified(address_or_xpub, ...)` - Unified transaction fetching

### XPUB Support Functions
- `get_xpub_addresses(xpub, gap_limit)` - Discover used addresses from XPUB
- `get_balance_for_xpub(xpub, gap_limit)` - Total balance across all XPUB addresses
- `get_transactions_for_xpub(xpub, ...)` - Aggregate transactions from all XPUB addresses

### API Integration
- **Primary**: Blockstream API (blockstream.info) - Fast, reliable Bitcoin explorer
- **Fallback**: Blockchain.com API - Alternative for redundancy
- **Methods**: Address info, transaction history, UTXO tracking

## Key Features

### 1. UTXO Model Handling
Bitcoin's Unspent Transaction Output (UTXO) model is properly handled:

```python
# Balance calculation from UTXO data
received_balance = funded_txo_sum / 1e8  # Total received
spent_balance = spent_txo_sum / 1e8      # Total spent  
current_balance = received_balance - spent_balance
```

### 2. Extended Public Key (XPUB) Support
Comprehensive XPUB functionality for HD wallet integration:

```python
# Address discovery with gap limit
addresses = get_all_addresses_from_xpub(xpub, gap_limit=20)

# Aggregate balance across all derived addresses
total_balance = sum(get_balance(addr) for addr in addresses)
```

### 3. Multi-API Resilience
Automatic failover between Bitcoin APIs:
- Blockstream.info (primary) - Fast, modern API
- Blockchain.com (fallback) - Established, reliable backup

### 4. Transaction Normalization
Converts Bitcoin's complex transaction format into standardized portfolio entries:

```json
{
  "type": "transfer_in|transfer_out|fee",
  "symbol": "BTC",
  "qty": 0.00123456,
  "fee": 0.00001234,
  "blockchain_tx_hash": "abc123...",
  "occurred_at": "2024-01-01T12:00:00Z",
  "source": "btc_blockchain"
}
```

## Bitcoin-Specific Features

### Address Format Support
Supports all major Bitcoin address formats:
- **P2PKH (Legacy)**: Starts with '1' (e.g., 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa)
- **P2SH (Script)**: Starts with '3' (e.g., 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy)  
- **Bech32 (SegWit)**: Starts with 'bc1' (e.g., bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4)

### XPUB Derivation Types
- **xpub**: Legacy derivation (P2PKH addresses)
- **ypub**: SegWit-wrapped derivation (P2SH-P2WPKH addresses)
- **zpub**: Native SegWit derivation (P2WPKH addresses)

### UTXO Transaction Processing
```python
def _normalize_btc_transaction(tx_data, wallet_address):
    # Calculate inputs from wallet address
    total_sent = sum(inputs from wallet_address)
    
    # Calculate outputs to wallet address  
    total_received = sum(outputs to wallet_address)
    
    # Calculate network fee
    fee = total_input_value - total_output_value
    
    # Determine transaction type and amount
    net_amount = total_received - total_sent
```

## Configuration

### Environment Variables
No API keys required - uses public Bitcoin APIs

### Constants
```python
# Satoshi to BTC conversion
SATOSHI_PER_BTC = 100000000  # 1 BTC = 100,000,000 satoshis

# XPUB gap limit for address discovery
DEFAULT_GAP_LIMIT = 20  # Stop searching after 20 consecutive unused addresses
```

### Cache Settings
```python
btc_cache = TTLCache(maxsize=1024, ttl=600)  # 10-minute cache
```

## API Endpoints

### Blockstream API (Primary)
- **Base URL**: `https://blockstream.info/api/`
- **Balance**: `address/{address}` - Returns UTXO statistics
- **Transactions**: `address/{address}/txs` - Returns transaction list
- **Features**: Fast, modern, no rate limits

### Blockchain.com API (Fallback)  
- **Base URL**: `https://blockchain.info/`
- **Balance**: `rawaddr/{address}` - Returns address info
- **Transactions**: Embedded in address response
- **Features**: Established, reliable, comprehensive data

## Transaction Categories

### Transfer In (Receiving BTC)
```python
{
    'type': 'transfer_in',
    'qty': received_amount_btc,
    'fee': 0,  # Receiver doesn't pay fees
    'notes': 'BTC received via blockchain transaction'
}
```

### Transfer Out (Sending BTC)
```python
{
    'type': 'transfer_out', 
    'qty': sent_amount_btc,
    'fee': transaction_fee_btc,
    'notes': 'BTC sent via blockchain transaction'
}
```

### Transaction Fee (Network Fee Only)
```python
{
    'type': 'fee',
    'qty': fee_amount_btc,
    'fee': 0,
    'notes': 'BTC transaction fee'
}
```

## XPUB Implementation Details

### Address Discovery Process
1. **Derivation**: Generate addresses using BIP44/49/84 derivation paths
2. **Gap Detection**: Check each address for transaction history
3. **Gap Limit**: Stop after finding N consecutive unused addresses
4. **Aggregation**: Combine data from all used addresses

### HD Wallet Support
```python
# Standard derivation paths
BIP44_PATH = "m/44'/0'/0'/{change}/{index}"  # Legacy (xpub)
BIP49_PATH = "m/49'/0'/0'/{change}/{index}"  # SegWit-wrapped (ypub) 
BIP84_PATH = "m/84'/0'/0'/{change}/{index}"  # Native SegWit (zpub)
```

### Performance Optimization
- **Parallel Processing**: Fetch balances/transactions for multiple addresses concurrently
- **Intelligent Caching**: Cache address data to avoid redundant API calls
- **Gap Limit**: Configurable to balance completeness vs performance

## Error Handling

### API Resilience
```python
def get_balance(address):
    # Try Blockstream first
    response = make_api_call(blockstream_url)
    if response:
        return parse_blockstream_balance(response)
    
    # Fallback to Blockchain.com
    return _fetch_btc_fallback_balance(address)
```

### XPUB Error Management
- **Invalid XPUB**: Validate format before processing
- **Network Errors**: Graceful degradation with partial results
- **Rate Limiting**: Respect API limits with backoff strategies

## Testing

### Test Coverage
- **Address Validation**: All Bitcoin address formats (P2PKH, P2SH, Bech32)
- **XPUB Validation**: xpub/ypub/zpub format validation
- **Balance Fetching**: Single addresses and XPUB aggregation
- **Transaction Processing**: Complex multi-input/output transactions
- **API Fallback**: Primary/fallback API switching
- **Date Filtering**: Transaction history date range filtering

### Sample Test Addresses
- **Genesis Block**: `1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa` (Satoshi's first address)
- **Multi-Sig Example**: `3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy`
- **SegWit Example**: `bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4`

## Special Considerations

### Transaction Complexity
Bitcoin transactions can be highly complex with multiple inputs and outputs:
- **Many-to-Many**: Single transaction can send from multiple addresses to multiple addresses
- **Change Addresses**: Unused inputs returned as "change" to new addresses
- **Fee Calculation**: Total inputs minus total outputs equals network fee

### UTXO Implications
- **Balance Accuracy**: Must sum unspent outputs, not just transaction amounts
- **Transaction Fees**: Paid by sender, included in transaction structure
- **Address Reuse**: Single address can receive multiple separate payments

### Wallet Integration
- **HD Wallets**: Full XPUB support for hierarchical deterministic wallets
- **Gap Limit**: Configurable to match wallet software behavior
- **Derivation Paths**: Support for BIP44/49/84 standards

## Performance Optimizations

### Caching Strategy
```python
@cached(btc_cache)  # 10-minute cache for balance data
def get_balance(address):
    # Cached to reduce API calls for frequently checked addresses
```

### API Efficiency
- **Batch Processing**: Process multiple addresses in parallel for XPUBs
- **Request Optimization**: Minimize API calls through intelligent caching
- **Failover Logic**: Quick failover to backup APIs on errors

### Memory Management
- **Bounded Caches**: Prevent memory growth with TTL and size limits
- **Streaming Processing**: Handle large XPUB transaction lists efficiently
- **Lazy Loading**: Load transaction details only when needed

## Integration Points

### Portfolio Service Integration
- **Balance Aggregation**: Sum balances across all XPUB-derived addresses
- **Transaction Deduplication**: Handle same transaction appearing across multiple addresses
- **Fee Attribution**: Properly attribute transaction fees to sending addresses

### Symbol Normalization
- **BTC Symbol**: Consistent 'BTC' symbol across all transactions
- **Precision Handling**: 8 decimal places (satoshi precision)
- **Amount Formatting**: Proper conversion between satoshi and BTC units

## Usage Examples

### Single Address Balance
```python
from service.blockchain_providers.btc_provider import get_balance

balance = get_balance("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
print(f"Address balance: {balance} BTC")
```

### XPUB Wallet Balance
```python
from service.blockchain_providers.btc_provider import get_balance_for_xpub

xpub = "xpub6CUGRUonZSQ4TWtTMmzX..."
total_balance = get_balance_for_xpub(xpub, gap_limit=20)
print(f"Total wallet balance: {total_balance} BTC")
```

### Unified Function (Auto-detect)
```python
from service.blockchain_providers.btc_provider import get_balance_or_xpub

# Works with either address or XPUB
balance = get_balance_or_xpub("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
balance = get_balance_or_xpub("xpub6CUGRUonZSQ4TWtTMmzX...")
```

### Transaction History
```python
from service.blockchain_providers.btc_provider import get_transactions_unified

transactions = get_transactions_unified(
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    start_date="2024-01-01",
    end_date="2024-12-31",
    limit=100
)

for tx in transactions:
    print(f"{tx['type']}: {tx['qty']} BTC (fee: {tx['fee']} BTC)")
```

## Future Enhancements

### Planned Features
1. **Lightning Network**: Integration with Lightning Network for instant payments
2. **Multi-Sig Support**: Enhanced multi-signature transaction handling
3. **Taproot Support**: Native Taproot (P2TR) address support
4. **Real-time Updates**: WebSocket connections for real-time balance updates

### Extensibility
- **Custom Derivation Paths**: Support for non-standard derivation schemes
- **Alternative Networks**: Testnet and regtest support
- **Advanced UTXO**: Coin control and UTXO selection strategies

## Dependencies
- `utils.api_client` - HTTP request handling with retries
- `utils.xpub_utils` - XPUB validation and address derivation
- `utils.logging_config` - Structured logging
- `cachetools` - Intelligent caching with TTL
- No external API keys required

## Historical Significance
The Bitcoin provider handles the original cryptocurrency with special attention to:
- **Genesis Block**: First block mined by Satoshi Nakamoto (January 3, 2009)
- **UTXO Model**: First implementation of the unspent transaction output model
- **HD Wallets**: Hierarchical Deterministic wallet support (BIP32/44/49/84)
- **SegWit**: Segregated Witness transaction format support

This implementation provides comprehensive Bitcoin blockchain integration with full XPUB support, multi-API resilience, and proper UTXO model handling, making it suitable for both individual address tracking and complete HD wallet management.