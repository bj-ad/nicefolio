# Crypto Wallet CRUD — Quick Reference

## Overview

The `crud_crypto_wallet.py` module provides high-level CRUD operations for syncing blockchain wallet transactions into the portfolio tracking system.

### Key Design Decisions

- **No parser required**: Unlike `crud_binancecom.py` or `crud_ibkr.py`, blockchain providers already normalize transaction data — no dedicated parser needed
- **Service layer integration**: Leverages `crypto_wallet_service.py` for data enrichment (wallet IDs, portfolio/account assignment)
- **Idempotent**: Uses `create_transaction_idempotent()` from `crud_base.py` — duplicate transactions are prevented via `source` + `external_id`

### Comparison with Other CRUD Modules

| Feature | binancecom/ibkr CRUD | crypto_wallet CRUD |
|---------|---------------------|-------------------|
| Parser | Dedicated parser file | Not needed (providers normalize) |
| Data Source | Exchange API endpoints | Blockchain APIs |
| Enrichment | Done in parser | Done in service layer |
| Config | `source_mapping.yaml` | `source_mapping.yaml` + wallet model |
| Special Features | — | xpub support for BTC HD wallets |

---

## Quick Start

### Import

```python
from database import SessionLocal
from crud.crud_crypto_wallet import (
    sync_wallet_by_id,
    sync_all_wallets_for_account,
    sync_wallet_by_address,
    sync_wallet_by_xpub,
    get_wallet_transaction_summary,
    calculate_wallet_balance_from_transactions
)
```

### Basic Sync by Wallet ID

```python
db = SessionLocal()
try:
    result = sync_wallet_by_id(db, wallet_id=5, days_back=7)
    if result['success']:
        print(f"Synced {result['transaction_count']} new transactions")
    else:
        print(f"Error: {result['message']}")
finally:
    db.close()
```

### Sync All Wallets in an Account

```python
db = SessionLocal()
try:
    result = sync_all_wallets_for_account(db, account_id=7, days_back=30)
    print(f"Synced {result['wallets_synced']} wallets")
    print(f"New transactions: {result['total_transactions']}")
finally:
    db.close()
```

### Sync by Blockchain Address

```python
db = SessionLocal()
try:
    result = sync_wallet_by_address(
        db,
        address='0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb',
        chain='eth',
        days_back=14
    )
finally:
    db.close()
```

### Sync BTC HD Wallet with xpub

```python
db = SessionLocal()
try:
    result = sync_wallet_by_xpub(
        db,
        xpub='xpub6CUGRUonZSQ4TWt...',
        days_back=30
    )
finally:
    db.close()
```

---

## Common Use Cases

### Daily Sync for All Hardware Wallet Accounts

```python
from database import SessionLocal
from crud.crud_crypto_wallet import sync_all_wallets_for_account
from models import Account

db = SessionLocal()
try:
    hw_accounts = db.query(Account).filter(
        Account.type == 'hardware_wallet'
    ).all()

    for account in hw_accounts:
        result = sync_all_wallets_for_account(db, account.id, days_back=7)
        print(f"{account.name}: {result['total_transactions']} new txs")
finally:
    db.close()
```

### Balance Reconciliation

```python
from crud.crud_crypto_wallet import (
    calculate_wallet_balance_from_transactions,
    sync_wallet_by_id
)

db = SessionLocal()
try:
    # Sync recent transactions
    sync_wallet_by_id(db, wallet_id=5, days_back=7, validate_balance=True)

    # Calculate balance from transactions
    tx_balance = calculate_wallet_balance_from_transactions(db, wallet_id=5, symbol='ETH')

    print(f"Transaction-based balance: {tx_balance['balance']} ETH")
finally:
    db.close()
```

---

## Function Reference

### `sync_wallet_transactions(db, wallet, days_back=7, validate_balance=False)`

Core sync function. Identifies the blockchain provider, fetches transactions for the date range, and ingests them idempotently.

**Returns:** `{'success': bool, 'message': str, 'transaction_count': int, 'total_transactions': int}`

### `sync_all_wallets_for_account(db, account_id, days_back=7, validate_balance=False)`

Sync all wallets in an account.

**Returns:** `{'success': bool, 'message': str, 'wallets_synced': int, 'total_transactions': int}`

### `sync_wallet_by_id(db, wallet_id, days_back=7, validate_balance=False)`

Sync by wallet database ID.

### `sync_wallet_by_address(db, address, chain, days_back=7, validate_balance=False)`

Sync by blockchain address and chain (`'btc'`, `'eth'`, `'bsc'`, `'sol'`, `'ada'`, `'xrp'`).

### `sync_wallet_by_xpub(db, xpub, days_back=7, validate_balance=False)`

Sync BTC HD wallet by extended public key (xpub/ypub/zpub).

### `get_wallet_transaction_summary(db, wallet_id)`

**Returns:** `{'wallet_id': int, 'total_transactions': int, 'earliest_transaction': datetime, 'latest_transaction': datetime, 'transaction_types': dict}`

### `calculate_wallet_balance_from_transactions(db, wallet_id, symbol=None)`

Calculates balance from transaction history using `sum_qty_by_wallet_symbol()`.

**Returns:** `{'wallet_id': int, 'chain': str, 'symbol': str, 'balance': float, 'address': str}`

> **Note:** Transaction-based balance may differ from blockchain API balance due to pending transactions or sync gaps.

---

## Integration Architecture

### Supported Blockchains

| Chain | Provider | Notes |
|-------|----------|-------|
| BTC | `btc_provider.py` | Single address + xpub (HD wallet) |
| ETH | `eth_provider.py` | — |
| BSC | `bsc_provider.py` | Staking contract support |
| SOL | `sol_provider.py` | Staking flag support |
| ADA | `ada_provider.py` | — |
| XRP | `xrp_provider.py` | — |

### Data Flow

```
Blockchain Provider (btc/eth/bsc/sol/ada/xrp)
  → Normalized transaction dict
      → crypto_wallet_service.py (enrichment: wallet IDs, portfolio, account)
          → crud_base.create_transaction_idempotent() (dedup via source + external_id)
              → Database (transactions table)
```

### Provider Output Format

```python
{
    'type': 'transfer_in',          # transfer_in | transfer_out | fee | staking_reward | staking
    'symbol': 'BTC',
    'qty': 0.00123456,
    'fee': 0.00001,
    'fee_currency': 'BTC',
    'blockchain_tx_hash': 'abc123...',
    'occurred_at': datetime(...),
    'source': 'btc_blockchain',
    'asset_class': 'crypto',
    'external_id': 'btc_abc123..._in',
    'notes': 'BTC received from blockchain transaction'
}
```

### Database Models

**CryptoWallet:**
- `chain`: Blockchain type (`'btc'`, `'eth'`, `'bsc'`, etc.)
- `address`: Single blockchain address (optional if using xpub)
- `xpub`: Extended public key for BTC HD wallets (optional)
- `derivation_path`, `address_type`, `gap_limit`: BTC HD wallet settings
- `bnb_staking_contract`: BSC staking contract address
- `is_sol_staking`: Solana staking flag
- `label`, `notes`: User-facing metadata

---

## Best Practices

1. **Incremental syncs**: Use `days_back=7` for daily syncs to minimize API calls
2. **Full syncs**: Perform `days_back=365` monthly for complete history
3. **Balance validation**: Enable `validate_balance=True` weekly for reconciliation
4. **Error handling**: Always check `result['success']` before proceeding
5. **Session management**: Always close database sessions in `finally` blocks
6. **Rate limits**: Blockchain API rate limits are handled by provider-level caching

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No transactions returned | Check date range, verify provider API is working, increase `days_back` |
| Balance mismatch | Pending transactions may not be reflected; run full sync with larger `days_back` |
| xpub sync fails | Verify xpub format, check `gap_limit` setting, ensure chain is `'btc'` |
| Slow sync performance | Reduce `days_back`, sync specific wallets instead of all accounts |

---

## Related Files

| File | Role |
|------|------|
| `crud/crud_crypto_wallet.py` | Main CRUD module |
| `crud/crud_base.py` | Base CRUD utilities (idempotent insert, balance sums) |
| `service/crypto_wallet_service.py` | Service layer (enrichment, provider dispatch) |
| `service/blockchain_providers/` | Individual blockchain provider modules |
| `models.py` | Database models (`CryptoWallet`, `Transaction`) |
| `config/source_mapping.yaml` | Account and portfolio assignment config |
