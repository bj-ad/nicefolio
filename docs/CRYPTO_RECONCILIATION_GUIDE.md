# Crypto Balance Reconciliation - Step-by-Step Guide

**Goal:** Fix crypto position discrepancies by using your ACTUAL current balances as ground truth.

---

## Step 1: Fill in Ground Truth Balances

**File:** `CURRENT_BALANCES_GROUND_TRUTH.md`

**What to do:**
1. Open the file
2. Log into each account (Exchange Account A, Exchange Account B, Hardware Wallet A, Hardware Wallet C)
3. Write down the ACTUAL balance for each crypto
4. Fill in the numbers in the file (replace the 0.00000000 placeholders)
5. Save the file

**Example:**
```yaml
### Account 3: Exchange Account A
BTC: 0.12345678  # What Exchange Account A shows RIGHT NOW
ETH: 0.98765000  # What Exchange Account A shows RIGHT NOW
```

**Important:**
- Include staked balances in the totals
- Double-check each number
- Make sure portfolio totals match the sum of accounts

---

## Step 2: Run the Reconciliation Script

**Command:**
```bash
cd /path/to/nicefolio
git pull  # Get the latest files
docker compose exec nicefolio_gui python -c "import sys; sys.path.insert(0, '/app'); exec(open('/app/scripts/reconcile_crypto_balances.py').read())"
```

**What it does:**
1. ✅ Reads your ground truth balances
2. ✅ Checks what we have from API calls (blockchain + exchange)
3. ✅ Finds duplicate transactions
4. ✅ Calculates what migration data SHOULD be
5. ✅ Generates SQL to fix everything

**Output:**
- Detailed analysis printed to screen
- SQL file saved to `/tmp/crypto_reconciliation_fix.sql`

---

## Step 3: Review the Generated SQL

**Command:**
```bash
docker compose exec nicefolio_gui cat /tmp/crypto_reconciliation_fix.sql
```

**What to check:**
- Duplicates being deleted make sense
- Migration corrections look reasonable
- Numbers match your expectations

---

## Step 4: Backup Database (CRITICAL!)

**Command:**
```bash
cd /path/to/nicefolio
docker compose exec nicefolio_db pg_dump -U nicefolio nicefolio_db > backup_before_crypto_fix_$(date +%Y%m%d).sql
```

**Verify backup exists:**
```bash
ls -lh backup_before_crypto_fix_*.sql
```

---

## Step 5: Apply the Fix

**Command:**
```bash
docker compose exec -T nicefolio_db psql -U nicefolio -d nicefolio_db < /tmp/crypto_reconciliation_fix.sql
```

Or manually execute inside the database:
```bash
docker compose exec nicefolio_db psql -U nicefolio -d nicefolio_db

# Then paste the SQL from the file
```

---

## Step 6: Verify the Fix

**Run position reconciliation:**
```bash
docker compose exec nicefolio_gui python -c "
from service.portfolio_service import reconcile_all_positions
result = reconcile_all_positions()
print(f'Reconciled: {result}')
"
```

**Check for discrepancies:**
```bash
docker compose logs nicefolio_gui | grep "position mismatch"
```

**Expected result:** NO position mismatches (or very small ones due to rounding)

---

## Step 7: Check Balances Match Ground Truth

**Command:**
```bash
docker compose exec -T nicefolio_gui python -c "
from database import SessionLocal
from models import Transaction
from sqlalchemy import func

db = SessionLocal()
try:
    # Portfolio 5
    print('Portfolio 5 (Crypto Long) - Current Balances:')
    
    for symbol in ['BTC', 'ETH', 'BNB', 'ADA', 'SOL', 'XRP']:
        total = db.query(func.sum(Transaction.qty)).filter(
            Transaction.portfolio_id == 5,
            Transaction.symbol == symbol
        ).scalar() or 0
        
        print(f'  {symbol:6s}: {float(total):15.8f}')
    
    print()
    print('Portfolio 6 (Crypto Short) - Current Balances:')
    
    for symbol in ['BTC', 'ETH', 'USDT', 'USDC']:
        total = db.query(func.sum(Transaction.qty)).filter(
            Transaction.portfolio_id == 6,
            Transaction.symbol == symbol
        ).scalar() or 0
        
        print(f'  {symbol:6s}: {float(total):15.8f}')
finally:
    db.close()
"
```

**Compare these numbers to your ground truth file - they should MATCH EXACTLY!**

---

## Troubleshooting

### If Numbers Still Don't Match

1. Check if worker ran during the process (pause it if needed):
   ```bash
   docker compose stop nicefolio_worker
   ```

2. Re-run the reconciliation script

3. Check for any transactions with NULL qty:
   ```bash
   docker compose exec -T nicefolio_gui python -c "
   from database import SessionLocal
   from models import Transaction
   
   db = SessionLocal()
   null_qty = db.query(Transaction).filter(
       Transaction.portfolio_id.in_([5, 6]),
       Transaction.qty.is_(None)
   ).count()
   print(f'Transactions with NULL qty: {null_qty}')
   db.close()
   "
   ```

### If You Need to Restore Backup

```bash
cd /path/to/nicefolio
docker compose exec -T nicefolio_db psql -U nicefolio -d nicefolio_db < backup_before_crypto_fix_YYYYMMDD.sql
```

---

## After Successful Reconciliation

1. **Restart worker:**
   ```bash
   docker compose start nicefolio_worker
   ```

2. **Future API syncs will be clean** because:
   - We removed duplicates
   - Migration data now complements API data correctly
   - Each subsequent sync adds only new transactions

3. **Monitor tomorrow's run** to ensure no new duplicates

---

## Summary of What This Process Does

**Before:**
- Migration data + API data = Wrong totals (duplicates/overlaps)
- Position reconciliation shows mismatches

**After:**
- Migration data + API data = Your ground truth balances EXACTLY
- Position reconciliation shows NO mismatches
- Future syncs build correctly on this foundation

---

**Questions? Issues?** Let me know and I'll help debug!
