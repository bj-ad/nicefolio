# Database Schema Quick Reference

**Date:** October 1, 2025  
**Schema Version:** 1.0

---

## 🗂️ Core Tables

### Portfolios (7 rows)
Organizational containers for different asset strategies

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Portfolio ID (1-7) |
| `name` | VARCHAR | Cash Portfolio, Securities, Gold, Crypto Long, etc. |
| `base_currency` | VARCHAR | THB or USD |
| `description` | TEXT | Purpose and strategy |
| `created_at` | TIMESTAMP | Auto-generated |

**Relationships:**
- `portfolios` ← `transactions.portfolio_id` (many)
- `portfolios` ← `positions.portfolio_id` (many)
- `portfolios` ← `snapshots.portfolio_id` (many)

---

### Accounts (7 rows)
Custodians/platforms where assets are held

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Account ID (1-7) |
| `name` | TEXT | Broker, Exchange Account B, Physical Storage, etc. |
| `type` | TEXT | broker, exchange, hardware_wallet, physical |
| `currency` | TEXT | Account base currency (USD, THB) |
| `created_at` | TIMESTAMP | Auto-generated |

**Relationships:**
- `accounts` ← `transactions.account_id` (many)
- `accounts` ← `crypto_wallets.account_id` (many)

**Key Design:** ❌ NO `portfolio_id` - accounts serve multiple portfolios via transactions!

---

### Transactions (junction table)
Financial events linking portfolios ↔ accounts

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT PK | Transaction ID |
| **`portfolio_id`** | **INTEGER FK** | **→ portfolios.id** |
| **`account_id`** | **INTEGER FK** | **→ accounts.id** |
| `occurred_at` | TIMESTAMP | When transaction occurred |
| `type` | VARCHAR | buy, sell, deposit, withdrawal, transfer_in/out, fee, dividend, etc. |
| `symbol` | VARCHAR | Asset symbol (AAPL, BTC, GLD) |
| `qty` | NUMERIC(24,8) | Quantity (+ in, - out) |
| `amount` | NUMERIC(24,8) | Fiat value in `currency` |
| `currency` | VARCHAR | Currency of amount (USD, THB) |
| `price` | NUMERIC(24,8) | Unit price |
| `fee` | NUMERIC(24,8) | Transaction fee |
| `fee_currency` | VARCHAR | Fee currency |
| `exchange_rate_to_base` | NUMERIC(24,8) | FX rate to portfolio base |
| `amount_base` | NUMERIC(24,8) | Cached amount in portfolio base currency |
| `lot_id` | VARCHAR | Lot identifier for cost basis |
| `category` | VARCHAR | income, trade, external_transfer, internal_transfer, fee, tax |
| `source` | VARCHAR | BinanceTH, Broker, BinanceCOM, GoldtradersTH, manual |
| `external_id` | VARCHAR | ID from external system |
| `notes` | TEXT | User notes |
| `asset_class` | VARCHAR | stocks, etf, gold_etf, gold_baht, crypto, cash |
| `symbol_normalized` | VARCHAR | Normalized symbol for app use |
| `from_crypto_wallet_id` | INTEGER FK | → crypto_wallets.id |
| `to_crypto_wallet_id` | INTEGER FK | → crypto_wallets.id |
| `blockchain_tx_hash` | VARCHAR | On-chain transaction hash |
| `blockchain_fee` | NUMERIC(24,8) | Blockchain network fee |
| `crypto_transfer_link_id` | INTEGER FK | → crypto_transfer_links.id |
| `created_at` | TIMESTAMP | Auto-generated |

**Indexes:**
- `ix_transactions_portfolio_occurred` (portfolio_id, occurred_at)
- `ix_transactions_account` (account_id)
- `ix_transactions_symbol` (symbol)
- `ix_transactions_from_wallet` (from_crypto_wallet_id)
- `ix_transactions_to_wallet` (to_crypto_wallet_id)
- `ix_transactions_txhash` (blockchain_tx_hash)
- `ix_transactions_link` (crypto_transfer_link_id)

---

### Positions
Current holdings aggregated within each portfolio

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Position ID |
| **`portfolio_id`** | **INTEGER FK** | **→ portfolios.id** |
| `symbol` | VARCHAR | Asset symbol |
| `quantity` | NUMERIC(24,8) | Current quantity held |
| `cost_basis_base` | NUMERIC(24,8) | Total cost in portfolio base currency |
| `avg_price_base` | NUMERIC(24,8) | Average price paid in base currency |
| `cost_basis_native` | NUMERIC(24,8) | Cost in original currency |
| `currency_native` | VARCHAR | Original currency |
| `last_updated` | TIMESTAMP | When position was last updated |
| `asset_class` | VARCHAR | Asset classification |
| `symbol_normalized` | VARCHAR | Normalized symbol |
| `created_at` | TIMESTAMP | Auto-generated |

**Index:** `ix_positions_portfolio_symbol` (portfolio_id, symbol)

**Note:** Positions aggregate holdings across ALL accounts in a portfolio

---

## 🪙 Crypto-Specific Tables

### Crypto Wallets
Blockchain addresses associated with accounts

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Wallet ID |
| `account_id` | INTEGER FK | → accounts.id |
| `blockchain` | VARCHAR | BTC, ETH, BSC, etc. |
| `address` | VARCHAR | Blockchain address |
| `label` | VARCHAR | User label |
| `xpub` | VARCHAR | Extended public key (for HD wallets) |
| `derivation_path` | VARCHAR | BIP44/49/84 path |
| `wallet_type` | VARCHAR | hardware, exchange_deposit, hot_wallet |
| `is_active` | BOOLEAN | Active status |
| `last_synced` | TIMESTAMP | Last blockchain sync |
| `created_at` | TIMESTAMP | Auto-generated |

**Unique:** (blockchain, address)

---

### Crypto Balances
Snapshot of crypto holdings per wallet

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Balance ID |
| `crypto_wallet_id` | INTEGER FK | → crypto_wallets.id |
| `token_symbol` | VARCHAR | BTC, ETH, USDT, etc. |
| `balance` | NUMERIC(24,8) | Token balance |
| `timestamp` | TIMESTAMP | When balance was recorded |
| `source` | VARCHAR | blockchain_scan, api, manual |
| `created_at` | TIMESTAMP | Auto-generated |

---

### Crypto Transfer Links
Links on-chain transfers to off-chain transactions

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Link ID |
| `blockchain_tx_hash` | VARCHAR | On-chain transaction hash |
| `blockchain` | VARCHAR | BTC, ETH, BSC, etc. |
| `from_address` | VARCHAR | Sender address |
| `to_address` | VARCHAR | Recipient address |
| `amount` | NUMERIC(24,8) | Transfer amount |
| `token_symbol` | VARCHAR | Token transferred |
| `timestamp` | TIMESTAMP | On-chain timestamp |
| `is_internal` | BOOLEAN | Internal transfer (between own wallets) |
| `notes` | TEXT | User notes |
| `created_at` | TIMESTAMP | Auto-generated |

---

## 💹 Market Data Tables

### Market Data
Historical price data

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Data ID |
| `symbol` | VARCHAR | Asset symbol |
| `ts` | TIMESTAMP | Price timestamp |
| `price` | NUMERIC(24,8) | Price value |
| `currency` | VARCHAR | Price currency (USD, THB) |
| `source` | VARCHAR | yahoo, binance, coingecko |
| `created_at` | TIMESTAMP | Auto-generated |

**Unique:** (symbol, ts)  
**Index:** `ix_marketdata_symbol_ts` (symbol, ts)

---

### FX Rates
Foreign exchange rates

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Rate ID |
| `pair` | VARCHAR(16) | Currency pair (USD/THB, USDTHB) |
| `ts` | TIMESTAMP | Rate timestamp |
| `rate` | NUMERIC | Exchange rate (1 base = rate quote units) |
| `base_currency` | VARCHAR(8) | Base currency (USD) |
| `quote_currency` | VARCHAR(8) | Quote currency (THB) |
| `source` | VARCHAR(64) | yahoo, xe, manual |
| `created_at` | TIMESTAMP | Auto-generated |

**Unique:** (pair, ts)  
**Index:** `ix_fx_pair_ts` (pair, ts)

---

## 📸 Snapshot Tables

### Snapshots
Daily portfolio valuation snapshots

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Snapshot ID |
| `portfolio_id` | INTEGER FK | → portfolios.id |
| `snapshot_date` | DATE | Date of snapshot |
| `total_value_native` | NUMERIC(24,8) | Total value in native currency |
| `total_value_base` | NUMERIC(24,8) | Total value in base currency |
| `cash_balance` | NUMERIC(24,8) | Cash portion |
| `invested_balance` | NUMERIC(24,8) | Invested portion |
| `realized_pnl` | NUMERIC(24,8) | Realized profit/loss |
| `unrealized_pnl` | NUMERIC(24,8) | Unrealized profit/loss |
| `created_at` | TIMESTAMP | Auto-generated |

---

### Lots
Tax lot tracking for cost basis

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Lot ID |
| `transaction_id` | BIGINT FK | → transactions.id (acquisition) |
| `symbol` | VARCHAR | Asset symbol |
| `quantity` | NUMERIC(24,8) | Lot quantity |
| `acquired_at` | TIMESTAMP | When acquired |
| `cost_basis` | NUMERIC(24,8) | Total cost basis |
| `cost_per_unit` | NUMERIC(24,8) | Cost per unit |
| `remaining_qty` | NUMERIC(24,8) | Unsold quantity |
| `status` | VARCHAR | open, closed, partial |
| `created_at` | TIMESTAMP | Auto-generated |

---

## 🔗 Relationship Diagram

```
portfolios (7)
    ↓ has many
transactions (junction) ← links to → accounts (7)
    ↓ has many
positions (aggregated)

accounts (7)
    ↓ has many
crypto_wallets
    ↓ has many
crypto_balances

transactions
    ↓ references
crypto_transfer_links
```

---

## 🎯 Key Design Principles

### 1. Portfolio ↔ Account Relationship
- **Many-to-Many** via `transactions` table
- Accounts can serve multiple portfolios (Broker → Securities + Gold)
- Portfolios aggregate from multiple accounts (Securities → Broker + Schwab)

### 2. Positions vs Transactions
- **Transactions**: Individual buy/sell events (immutable history)
- **Positions**: Aggregated current holdings (derived from transactions)

### 3. Currency Handling
- Portfolio has `base_currency` (THB or USD)
- Transaction has `currency` (transaction currency) and `amount_base` (converted)
- All P&L calculations use base currency

### 4. Crypto Architecture
- `accounts` = custody layer (Broker, Binance, hardware wallet)
- `crypto_wallets` = blockchain layer (addresses, xpubs)
- `crypto_balances` = on-chain holdings snapshot
- `crypto_transfer_links` = on-chain ↔ off-chain reconciliation

---

## 📊 Current Data

**7 Portfolios:**
1. Cash Portfolio (THB) - General cash holdings
2. Term Deposits Portfolio (THB) - Cash-equivalent pools
3. Securities (USD) - Stocks and ETFs
4. Gold (THB) - Physical and ETF gold
5. Crypto Long (USD) - Long-term crypto
6. Crypto Short (USD) - Short-term crypto
7. Funds Portfolio (THB) - mutual funds

**7 Accounts:**
1. Physical Storage (Physical, THB)
2. Broker Account (Broker, USD)
3. Exchange Account A (Exchange, THB)
4. Exchange Account B (Exchange, USD)
5. Hardware Wallet A (Hardware Wallet, USD)
6. Hardware Wallet B (Hardware Wallet, USD)
7. Hardware Wallet C (Hardware Wallet, USD)

---

## 🚀 Query Examples

### Get All Transactions for Portfolio
```sql
SELECT t.*, a.name AS account_name
FROM transactions t
JOIN accounts a ON t.account_id = a.id
WHERE t.portfolio_id = 3  -- Securities
ORDER BY t.occurred_at DESC;
```

### Get Current Positions in Portfolio
```sql
SELECT symbol, quantity, cost_basis_base, avg_price_base
FROM positions
WHERE portfolio_id = 3  -- Securities
ORDER BY symbol;
```

### Get All Portfolios Served by Account
```sql
SELECT DISTINCT p.id, p.name
FROM portfolios p
JOIN transactions t ON t.portfolio_id = p.id
WHERE t.account_id = 2  -- Broker
ORDER BY p.id;
```

### Get Account-Level Balance
```sql
-- Total value in Broker across all portfolios
SELECT 
    a.name,
    SUM(p.quantity * md.price) AS total_value
FROM positions p
JOIN transactions t ON t.portfolio_id = p.portfolio_id AND t.symbol = p.symbol
JOIN accounts a ON t.account_id = a.id
JOIN market_data md ON p.symbol = md.symbol
WHERE a.id = 2  -- Broker
  AND md.ts = (SELECT MAX(ts) FROM market_data WHERE symbol = p.symbol)
GROUP BY a.name;
```

---

**Last Updated:** October 1, 2025  
**Schema Version:** 1.0  
**Total Tables:** 11
