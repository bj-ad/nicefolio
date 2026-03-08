# Staking Reward Calculation Logic (BNB & SOL)

## Problem Statement

For BNB and SOL, staking rewards auto-compound into the staked balance. We need to track:
1. Principal amount staked
2. Rewards accumulated (auto-compounded)
3. Rewards realized when claimed

## Correct Calculation Method

### Data Tracked in `crypto_staking_transactions`:
- `delegate`: Amount moved to staking (principal)
- `undelegate`: Request to unstake (amount includes principal + rewards at that moment)
- `claim`: Amount received (principal + rewards)

### Key Insight:
**The `undelegate` amount tells us the staked balance (principal + rewards) at that moment**

### Formula:
```
When UNDELEGATE is recorded:
  Staked Balance = undelegate_amount (principal + accumulated rewards)
  Principal = SUM(previous delegations) - SUM(previous claims)
  Accumulated Rewards = Staked Balance - Principal
  Reward % = Accumulated Rewards / Staked Balance

When CLAIM happens:
  Claimed Amount = claim_amount
  Staking Reward = Claimed Amount * Reward %
  → Create `staking_reward` transaction in main table
```

### Example Workflow:

**Step 1: Delegate 10 BNB**
```
crypto_staking_transactions:
  - tx_hash: 0x111, type: delegate, amount: 10
  
Main transactions: (none yet)
Staked Balance: 10 BNB (principal)
```

**Step 2: Time passes, balance grows to 10.5 BNB**
(Auto-compounding, no transaction)

**Step 3: Undelegate 5 BNB**
```
User checks wallet: Staked balance = 10.5 BNB
User enters: undelegate, amount: 5.0 BNB

crypto_staking_transactions:
  - tx_hash: 0x222, type: undelegate, amount: 5.0, staked_balance_snapshot: 10.5
  
Calculation:
  Principal = 10 - 0 = 10 BNB
  Accumulated Rewards = 10.5 - 10 = 0.5 BNB
  Reward % = 0.5 / 10.5 = 4.76%
  
Main transactions: (none yet - waiting for claim)
```

**Step 4: Claim 5 BNB**
```
crypto_staking_transactions:
  - tx_hash: 0x333, type: claim, amount: 5.0, links_to_undelegate: 0x222
  
Calculation:
  Staking Reward = 5.0 * 4.76% = 0.238 BNB
  
Main transactions:
  - type: staking_reward, symbol: BNB, qty: 0.238, occurred_at: claim_timestamp
  ✅ Creates lot (taxable income)
```

## Implementation Plan

### 1. Update `crypto_staking_transactions` table
Add columns:
- `staked_balance_snapshot` NUMERIC(24,8): Total staked (principal + rewards) at undelegate time
- `occurred_at` TIMESTAMP: Blockchain transaction timestamp
- `linked_tx_hash` VARCHAR: For claim → undelegate linking

### 2. Update Staking Manager UI
**For UNDELEGATE:**
- Add field: "Current Staked Balance" (user must check wallet and enter)
- This captures principal + auto-compounded rewards

**For CLAIM:**
- Optionally link to previous undelegate (dropdown)
- Button: "Process Claim → Calculate Reward"

### 3. Create Processing Function
```python
def process_staking_claim(db, claim_tx_hash):
    """
    Process a claim transaction and create staking_reward in main table.
    
    Steps:
    1. Get claim transaction
    2. Find matching undelegate (by linked_tx_hash or wallet + closest timestamp)
    3. Calculate reward % from undelegate's staked_balance_snapshot
    4. Create staking_reward transaction with calculated amount
    5. Mark both as processed
    """
```

### 4. SOL Automation
SOL provider can automatically detect:
- Delegate operations (create crypto_staking_transactions record)
- Deactivate operations (undelegate) - **fetch stake account balance automatically**
- Withdraw operations (claim)

For SOL: Use `_fetch_stake_accounts()` to get current staked balance automatically!

## Database Migration Needed

```sql
-- Add new columns to crypto_staking_transactions
ALTER TABLE crypto_staking_transactions 
  ADD COLUMN staked_balance_snapshot NUMERIC(24,8),
  ADD COLUMN occurred_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN linked_tx_hash VARCHAR;

-- Create index for linking
CREATE INDEX ix_crypto_staking_txs_linked ON crypto_staking_transactions(linked_tx_hash);
```

## Tax Compliance Note

Only the `staking_reward` transaction (created when claim is processed) creates a lot and is taxable.
The delegate/undelegate/claim records in `crypto_staking_transactions` are just tracking data.

This matches German tax law (§ 20 EStG):
- Staking rewards are taxable when **realized/received**
- Auto-compounding is not a taxable event
- Only the reward portion of a claim is taxable income
