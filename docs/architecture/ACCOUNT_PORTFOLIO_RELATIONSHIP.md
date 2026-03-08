# Account-Portfolio Relationship Design

**Date:** October 1, 2025  
**Decision:** Accounts do NOT have `portfolio_id` - portfolios are linked via transactions

---

## 🎯 Problem Statement

**Question:** Should the `accounts` table have a `portfolio_id` foreign key?

**Example Use Case:**
- **Broker account** holds both:
  - Stocks/ETFs → **Securities portfolio** (portfolio_id=3)
  - Gold ETFs (GLD, IAU) → **Gold portfolio** (portfolio_id=4)
- **Exchange Account B account** holds:
  - Long-term crypto → **Crypto Long portfolio** (portfolio_id=5)
  - Short-term trading → **Crypto Short portfolio** (portfolio_id=6)

**Conclusion:** This is a **many-to-many relationship** - a single account can serve multiple portfolios!

---

## ✅ Correct Design: No portfolio_id on Accounts

### Database Schema

**Accounts Table:**
```sql
CREATE TABLE accounts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,           -- 'Broker Account', 'Exchange Account B'
    type       TEXT,                    -- 'exchange', 'broker', 'hardware_wallet'
    currency   TEXT,                    -- 'USD', 'THB'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- NO portfolio_id column!
```

**Transactions Table:**
```sql
CREATE TABLE transactions (
    id              BIGINT PRIMARY KEY,
    portfolio_id    INTEGER REFERENCES portfolios(id),  -- ✅ Portfolio link here
    account_id      INTEGER REFERENCES accounts(id),     -- ✅ Account link here
    occurred_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    type            VARCHAR CHECK (type IN ('buy', 'sell', ...)),
    symbol          VARCHAR,
    qty             NUMERIC(24,8),
    amount          NUMERIC(24,8),
    currency        VARCHAR,
    -- ... other fields
);
```

**Positions Table:**
```sql
CREATE TABLE positions (
    id              INTEGER PRIMARY KEY,
    portfolio_id    INTEGER REFERENCES portfolios(id),  -- ✅ Portfolio link here
    symbol          VARCHAR NOT NULL,
    quantity        NUMERIC(24,8) NOT NULL,
    cost_basis_base NUMERIC(24,8) NOT NULL,
    -- ... other fields
);
-- NO account_id - positions aggregate across all accounts in portfolio
```

---

## 🔄 Relationship Model

```
┌─────────────┐
│  Portfolio  │  (7 portfolios)
│ (Securities)│
└──────┬──────┘
       │
       │ Has many transactions
       ▼
┌─────────────┐         ┌──────────────┐
│ Transaction │◄────────│   Account    │  (7 accounts)
│ (AAPL buy)  │ from    │    (Broker)    │
└─────────────┘         └──────────────┘
       │
       │ Same account, different portfolio
       ▼
┌─────────────┐
│ Transaction │
│ (GLD buy)   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Portfolio  │
│   (Gold)    │
└─────────────┘
```

**Key Insight:** The **transaction** determines which portfolio it belongs to, not the account!

---

## 📊 Real-World Examples

### Example 1: Broker Account Serving Two Portfolios

**Scenario:** User buys AAPL stock and GLD gold ETF in same Broker account

```sql
-- Transaction 1: Stock purchase → Securities portfolio
INSERT INTO transactions (portfolio_id, account_id, type, symbol, qty)
VALUES (3, 2, 'buy', 'AAPL', 10);
-- portfolio_id=3 (Securities), account_id=2 (Broker)

-- Transaction 2: Gold ETF purchase → Gold portfolio
INSERT INTO transactions (portfolio_id, account_id, type, symbol, qty)
VALUES (4, 2, 'buy', 'GLD', 5);
-- portfolio_id=4 (Gold), account_id=2 (Broker)
```

**Result:**
- Both transactions use **same account** (Broker, id=2)
- But go to **different portfolios** (Securities vs Gold)
- No conflict, no data duplication!

### Example 2: Exchange Account B for HODL and Trading

```sql
-- Transaction 1: BTC buy for long-term hold
INSERT INTO transactions (portfolio_id, account_id, type, symbol, qty)
VALUES (5, 4, 'buy', 'BTC', 0.5);
-- portfolio_id=5 (Crypto Long), account_id=4 (Exchange Account B)

-- Transaction 2: ETH trade
INSERT INTO transactions (portfolio_id, account_id, type, symbol, qty)
VALUES (6, 4, 'buy', 'ETH', 10);
-- portfolio_id=6 (Crypto Short), account_id=4 (Exchange Account B)
```

### Example 3: Query All Broker Transactions Across Portfolios

```sql
-- Get all transactions from Broker account
SELECT 
    t.id,
    p.name AS portfolio,
    t.type,
    t.symbol,
    t.qty,
    t.occurred_at
FROM transactions t
JOIN portfolios p ON t.portfolio_id = p.id
WHERE t.account_id = 2  -- Broker
ORDER BY t.occurred_at;
```

**Result:**
```
portfolio  | type | symbol | qty
-----------+------+--------+-----
Securities | buy  | AAPL   | 10
Securities | buy  | MSFT   | 5
Gold       | buy  | GLD    | 5
Securities | sell | AAPL   | 2
```

### Example 4: Query Securities Portfolio Holdings (All Accounts)

```sql
-- Get current positions in Securities portfolio
SELECT 
    symbol,
    quantity,
    cost_basis_base
FROM positions
WHERE portfolio_id = 3;  -- Securities
```

**Note:** Positions aggregate holdings across ALL accounts (Broker, Schwab, etc.) that trade securities!

---

## ❌ Why portfolio_id on Accounts Would Be Wrong

### Problem 1: Forced One-to-One Relationship
```sql
-- BAD: Account can only belong to ONE portfolio
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    portfolio_id INTEGER REFERENCES portfolios(id),  -- ❌ Forces single portfolio
    name TEXT
);
```

**Consequence:** Can't have Broker hold both stocks (Securities) and gold (Gold)!

### Problem 2: Data Duplication
If we tried to work around this by creating duplicate accounts:
```sql
-- BAD: Duplicate accounts for each portfolio
INSERT INTO accounts VALUES (2, 3, 'Broker Account (Securities)');
INSERT INTO accounts VALUES (8, 4, 'Broker Account (Gold)');
```

**Problems:**
- Same real-world account (Broker #12345) appears twice in database
- Confusing for users: "Which Broker is this transaction from?"
- Complex queries to aggregate account-level data
- API credentials would be duplicated

### Problem 3: Can't Track Account-Level Metrics
```sql
-- GOOD: Query total assets in Broker across all portfolios
SELECT 
    a.name,
    SUM(p.quantity * md.price) AS total_value
FROM positions p
JOIN transactions t ON t.portfolio_id = p.portfolio_id AND t.symbol = p.symbol
JOIN accounts a ON t.account_id = a.id
JOIN market_data md ON p.symbol = md.symbol
WHERE a.id = 2  -- Broker
GROUP BY a.name;
```

With `portfolio_id` on accounts, this query becomes impossible!

---

## ✅ Benefits of Current Design

### 1. **Flexibility**: One account serves multiple portfolios
```python
# Broker can hold any asset type
ibkr_account = Account(name="Broker Account", type="broker")

# Transactions determine portfolio assignment
Transaction(account=ibkr_account, portfolio=securities, symbol="AAPL")
Transaction(account=ibkr_account, portfolio=gold, symbol="GLD")
```

### 2. **Accurate Representation**: Models real world
- Real Broker account = one account in database ✅
- No artificial account splitting
- Natural mapping of transactions to portfolios

### 3. **Simple Queries**: Clear join paths
```sql
-- Portfolio → Transactions → Accounts
-- Accounts → Transactions → Portfolios
-- Clean many-to-many through transaction junction table
```

### 4. **Scalability**: Easy to add new portfolios
```python
# User creates new "Dividend Income" portfolio
# No need to modify existing accounts!
# Just assign new transactions to new portfolio
Transaction(account=ibkr_account, portfolio=dividend_income, type="dividend")
```

---

## 🏗️ SQLAlchemy Model Relationships

**Portfolio Model:**
```python
class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    base_currency = Column(String, default='USD')
    
    # Portfolio has many transactions
    transactions = relationship("Transaction", back_populates="portfolio")
    # Portfolio has many positions (aggregated across accounts)
    positions = relationship("Position", back_populates="portfolio")
    # NO direct accounts relationship!
```

**Account Model:**
```python
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    type = Column(Text)  # 'exchange', 'broker', 'hardware_wallet'
    currency = Column(Text)
    
    # Account has many transactions (across multiple portfolios)
    transactions = relationship("Transaction", back_populates="account")
    # NO portfolio_id foreign key!
    # NO portfolio relationship!
```

**Transaction Model (Junction):**
```python
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(BigInteger, primary_key=True)
    
    # Links to both portfolio and account
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'))  # ✅
    account_id = Column(Integer, ForeignKey('accounts.id'))      # ✅
    
    # Relationships
    portfolio = relationship("Portfolio", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
```

---

## 🔍 Query Patterns

### Get All Accounts Used by a Portfolio
```python
# Get all accounts that have transacted in Securities portfolio
from sqlalchemy import distinct

accounts = db.query(Account).join(Transaction).filter(
    Transaction.portfolio_id == 3  # Securities
).distinct().all()

# Result: [Broker, Schwab, ...]
```

### Get All Portfolios Served by an Account
```python
# Get all portfolios that Broker has transacted in
portfolios = db.query(Portfolio).join(Transaction).filter(
    Transaction.account_id == 2  # Broker
).distinct().all()

# Result: [Securities, Gold, ...]
```

### Get Account-Level Balance Across Portfolios
```python
# Total value in Broker across all portfolios
from sqlalchemy import func

total = db.query(
    func.sum(Position.quantity * MarketData.price)
).join(Transaction).filter(
    Transaction.account_id == 2  # Broker
).scalar()
```

---

## 📋 Configuration Files

**accounts_config.yaml** (NO portfolio_id):
```yaml
accounts:
  - id: 1
    name: "Physical Storage"
    type: "Physical"
    currency: "THB"

  - id: 2
    name: "Broker Account"
    type: "Broker"
    currency: "USD"
    # NO portfolio_id field!
```

**Why no portfolio_id in config?**
- Accounts are infrastructure/custody layer
- Portfolios are organizational/reporting layer
- Transactions bridge the two
- Accounts don't "belong" to portfolios, they "serve" them

---

## 🎯 CSV Import Implications

When importing historical data:

```python
# CSV row example:
# Date,Account,Portfolio,Symbol,Qty,Price
# 2024-01-15,Broker,Securities,AAPL,10,150.00

def import_transaction(csv_row):
    account = get_account_by_name(csv_row['Account'])      # Broker
    portfolio = get_portfolio_by_name(csv_row['Portfolio']) # Securities
    
    transaction = Transaction(
        portfolio_id=portfolio.id,  # ✅ From CSV
        account_id=account.id,      # ✅ From CSV
        symbol=csv_row['Symbol'],
        qty=csv_row['Qty'],
        # ... other fields
    )
    db.add(transaction)
```

**The CSV specifies BOTH account and portfolio** - this is natural and explicit!

---

## 🚀 Data Migration Note

**Before (incorrect schema):**
```sql
-- accounts had portfolio_id column (constraint removed)
ALTER TABLE accounts DROP COLUMN portfolio_id;
```

**After (correct schema):**
```sql
-- No portfolio_id on accounts
-- Transactions handle the many-to-many relationship
```

**Result:** Clean many-to-many design that accurately models reality!

---

## 📊 Current Database State

**Portfolios (7):**
1. Cash Portfolio (THB)
2. Term Deposits Portfolio (THB)
3. Securities (USD)
4. Gold (THB)
5. Crypto Long (USD)
6. Crypto Short (USD)
7. Funds Portfolio (THB)

**Accounts (7):**
1. Physical Storage (Physical, THB)
2. Broker Account (Broker, USD)
3. Exchange Account A (Exchange, THB)
4. Exchange Account B (Exchange, USD)
5. Hardware Wallet A (Hardware Wallet, USD)
6. Hardware Wallet B (Hardware Wallet, USD)
7. Hardware Wallet C (Hardware Wallet, USD)

**Relationship:** Many-to-many through `transactions` table ✅

---

## 💡 Summary

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **portfolio_id on accounts?** | ❌ NO | Accounts serve multiple portfolios |
| **portfolio_id on transactions?** | ✅ YES | Transactions assign to portfolios |
| **portfolio_id on positions?** | ✅ YES | Positions aggregate within portfolio |
| **Relationship type** | Many-to-Many | Via transaction junction table |
| **Real-world mapping** | 1:1 | One database account = one real account |

---

**Status:** ✅ **CORRECT DESIGN IMPLEMENTED**

**Files Modified:**
- `models.py` - Removed `portfolio_id` from Account model
- Database schema recreated with correct relationships

**Next Steps:**
- Import historical transactions (will link accounts to portfolios naturally)
- Build queries to analyze account-level and portfolio-level metrics
- Dashboard showing portfolio composition across accounts
