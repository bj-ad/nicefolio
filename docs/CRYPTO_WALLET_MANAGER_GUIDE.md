# Crypto Wallet Manager - Quick Start Guide

**Access:** http://localhost:8890 (Docker) or http://localhost:8081 (Standalone)

---

## 🚀 Quick Start

### Start the Wallet Manager

**Option 1: Docker (Recommended)**
```bash
docker-compose up nicefolio_gui
# Access at http://localhost:8890
```

**Option 2: Standalone**
```bash
python apps/crypto_wallet_manager.py
# Access at http://localhost:8081
```

**Option 3: VS Code Task**
1. Press `Ctrl+Shift+P`
2. Type: `Tasks: Run Task`
3. Select: `Run Crypto Wallet Manager`
4. Access at http://localhost:8081

---

## 📝 Adding Wallets

### Prerequisites
You need **either**:
- **Wallet Address** - For single address tracking
- **xpub (Extended Public Key)** - For BTC/ADA multi-address tracking

### Supported Blockchains
- **BTC** - Bitcoin (supports xpub for HD wallets)
- **ETH** - Ethereum (supports ERC-20 tokens)
- **BSC** - Binance Smart Chain (supports BEP-20 tokens)
- **SOL** - Solana
- **ADA** - Cardano (supports xpub for multi-address)
- **XRP** - Ripple

### Adding a Wallet

1. Click **"➕ Add Wallet"**
2. Select **Account** (optional - for organizing wallets)
3. Select **Blockchain** (BTC, ETH, BSC, etc.)
4. Enter **either**:
   - **Wallet Address** - For single address tracking
   - **xpub** - For HD wallet tracking (BTC, ADA only)
5. Enter **Label** (e.g., "Ledger Nano X - BTC")
6. Click **"Add Wallet"**

### Example: Bitcoin Hardware Wallet
```
Account: Hardware Wallet
Blockchain: BTC
Address: (leave empty)
xpub: xpub6CUGRUonZSQ4TWtTMmzXdrXDtypWKiKrhko4egpiMZbpiaQL2jkwSB1icqYh2cfDfVxdx4df189oLKnC5fSwqPfgyP3hooxujYzAu3fDVmz
Label: Ledger Nano X - BTC
```

### Example: Ethereum Single Address
```
Account: MetaMask
Blockchain: ETH
Address: 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb
xpub: (leave empty)
Label: MetaMask - ETH Main
```

---

## 🔧 Managing Wallets

### View Wallet Details
Click the **eye icon** (👁️) to see:
- Full address or xpub
- Account association
- Blockchain type
- Active status

### Toggle Active/Inactive
Click the **power icon** (⚡) to:
- **Activate** - Include in scheduler sync
- **Deactivate** - Exclude from scheduler sync (but keep record)

**Use case:** Temporarily disable a wallet without deleting it

### Delete Wallet
Click the **trash icon** (🗑️) to permanently remove:
- Wallet record
- Associated balance history

⚠️ **Warning:** This cannot be undone!

---

## 🤖 How Scheduler Uses Wallets

### Daily Sync (22:00 ICT)
1. Scheduler runs `sync_crypto_wallets_with_balance()`
2. Service queries **active wallets only**
3. For each wallet:
   - Fetches balance from blockchain provider
   - Parses response (liquid + staked balances)
   - Inserts snapshot into `CryptoBalance` table
4. Results logged with success/failure counts

### What Gets Synced
- **Balance snapshots** (not transactions)
- **Liquid balances** (available for transfer)
- **Staked balances** (locked in staking)
- **Token balances** (ERC-20, BEP-20, etc.)

### Example Data Flow
```
Wallet: 0x742d35Cc... (ETH)
↓
Blockchain Provider (Etherscan API)
↓
Parser (normalize to CryptoBalance format)
↓
CRUD (insert into crypto_balances table)
```

---

## 📊 Checking Results

### Via Database
```python
from database import SessionLocal
from models import CryptoBalance, CryptoWallet
from sqlalchemy import func

db = SessionLocal()

# Count wallets
wallet_count = db.query(func.count(CryptoWallet.id)).scalar()
print(f"Total wallets: {wallet_count}")

# Count balance records
balance_count = db.query(func.count(CryptoBalance.id)).scalar()
print(f"Total balance snapshots: {balance_count}")

# Latest balances
latest = db.query(CryptoBalance).order_by(CryptoBalance.ts.desc()).limit(10).all()
for b in latest:
    print(f"{b.symbol}: {b.balance} ({b.balance_type}) @ {b.ts}")

db.close()
```

### Via DB Viewer
1. Access http://localhost:8889
2. Navigate to `CryptoBalance` table
3. Filter by wallet_id, symbol, or timestamp

---

## 🐛 Troubleshooting

### Wallet Not Syncing?
**Check wallet is active:**
```sql
SELECT id, chain, label, is_active 
FROM crypto_wallets 
WHERE is_active = false;
```

**Activate it:**
Click the power icon in the frontend, or:
```sql
UPDATE crypto_wallets SET is_active = true WHERE id = X;
```

### Balance Not Updated?
**Check scheduler logs:**
```bash
docker logs -f nicefolio_worker | grep crypto
```

**Expected output at 22:00 ICT:**
```
INFO Starting crypto wallet balance sync...
INFO Synced 5 wallets: 5 succeeded, 0 failed
INFO Balance ingestion complete. Success: 15, Failed: 0
```

### Invalid Address Format?
Each blockchain has different address formats:
- **BTC:** Starts with 1, 3, or bc1
- **ETH/BSC:** Starts with 0x (42 chars)
- **SOL:** Base58 encoded (32-44 chars)
- **ADA:** Starts with addr1
- **XRP:** Starts with r (25-34 chars)

**Validate address** before adding to avoid API errors.

### xpub Not Working?
**Only supported for:**
- **BTC** - HD wallet derivation
- **ADA** - Multi-address tracking

**For other chains:** Use individual addresses

---

## 🎯 Best Practices

### 1. Use Descriptive Labels
✅ Good: "Ledger Nano X - BTC Savings"  
❌ Bad: "Wallet 1"

### 2. Link to Accounts
Organize wallets by account (hardware wallet, exchange, etc.)

### 3. Don't Duplicate Wallets
Check existing wallets before adding. The system prevents exact duplicates, but similar addresses can be confusing.

### 4. Deactivate, Don't Delete
If you stop using a wallet, deactivate it to preserve balance history.

### 5. Test with One Wallet First
Add one wallet, wait for scheduler sync (22:00 ICT), verify results, then add more.

---

## 📋 Wallet Configuration Checklist

Before running scheduler:
- [ ] Wallet addresses added via frontend
- [ ] Each wallet has descriptive label
- [ ] All wallets set to "Active"
- [ ] Addresses validated (correct format for blockchain)
- [ ] Account associations configured (optional)
- [ ] Tested with DB viewer to confirm wallets exist

After first sync:
- [ ] Check scheduler logs for errors
- [ ] Verify CryptoBalance records created
- [ ] Confirm balance values are correct
- [ ] Review any failed syncs in logs

---

## 🔗 Related Documentation

- **Backend:** `/app/docs/crypto/CRYPTO_WALLET_CRUD_QUICKSTART.md`
- **Scheduler:** `/app/docs/SCHEDULER_CONFIGURATION_GUIDE.md`
- **Blockchain Providers:** `/app/docs/blockchain/BLOCKCHAIN_PROVIDERS_COMPLETE.md`
- **Database Schema:** `/app/models.py` (CryptoWallet, CryptoBalance)

---

## 🎓 Examples

### Portfolio 5 (Crypto Long) Setup
```
1. Add Binance.th exchange wallet (Account 3)
2. Add hardware wallet addresses (Accounts 5-7):
   - Ledger Nano X (BTC via xpub)
   - Trezor (ETH address)
   - Paper wallet (ADA via xpub)
3. All set to Active
4. Scheduler syncs at 22:00 ICT daily
5. Position reconciliation creates Portfolio 5 positions
6. Snapshot captures total crypto HODL value
```

### Multi-Chain Hardware Wallet
```
Account: Ledger Nano X (hardware_wallet)

Wallet 1:
- Chain: BTC
- xpub: xpub6CUGRUonZSQ4...
- Label: Ledger - BTC

Wallet 2:
- Chain: ETH
- Address: 0x742d35Cc...
- Label: Ledger - ETH

Wallet 3:
- Chain: ADA
- xpub: addr_xvk1...
- Label: Ledger - ADA
```

---

**Need help?** Check the logs: `docker logs -f nicefolio_worker`

**Last Updated:** October 2, 2025
