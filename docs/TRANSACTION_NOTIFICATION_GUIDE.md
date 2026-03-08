# Transaction Ingestion Notification Guide

**Date**: 2025-12-16  
**Status**: ✅ IMPLEMENTED  
**Feature**: Daily email notifications for new transaction ingestion

---

## Overview

The Portfolio Tracker now sends **daily email notifications** when new transactions are ingested during the automated sync process. This helps you stay informed about portfolio changes and reminds you to review and mark new transactions in the Transactions Overview app.

---

## Features

### 📧 Email Notifications

**When you'll receive notifications:**
- Daily after transaction sync completes (typically ~01:05 AM)
- Only when **new transactions** are ingested (no spam when nothing changes)
- Separate from error notifications (which you already receive)

**What's included:**
- **Total count** of new transactions
- **Account-by-account breakdown** showing which accounts had new activity
- **Transaction type summary** (buy, sell, deposit, withdrawal, transfer, etc.)
- **Failure count** if any transactions failed to sync
- **Unreviewed transaction count** to remind you what needs review
- **Clear call-to-action** to review transactions in the app

### 📊 Account Coverage

The notification tracks transactions from:
- **Broker** (Broker Account) - Account 2
- **Exchange A** - Account 3  
- **Exchange B** - Account 4 (when enabled)
- **Crypto Wallets** - Accounts 5-7 (Hardware Wallet A, Hardware Wallet B, etc.)

---

## Configuration

### Enable/Disable Transaction Notifications

Edit `config/app_config.yaml`:

```yaml
notifications:
  enabled: true  # Master switch - must be true
  
  # Send daily summary when new transactions are ingested
  send_transaction_notifications: true  # Set to false to disable
```

**Default:** `true` (enabled)

### Email Configuration

Transaction notifications use your existing email settings:

```yaml
notifications:
  channels:
    email:
      enabled: true
      smtp_server: smtp.gmail.com
      smtp_port: 587
      smtp_user: your-email@gmail.com
      smtp_password: ${SMTP_PASSWORD}  # Or set in environment variable
      from_email: your-email@gmail.com
      to_emails:
        - your-email@gmail.com
        - other-email@example.com  # Optional: send to multiple recipients
```

**Security Note:** Use environment variable `SMTP_PASSWORD` for production deployments.

---

## Email Example

```
Subject: Portfolio Tracker: 18 New Transactions Ingested

📊 Daily Transaction Sync Summary

Time: 2025-12-16 01:05:23 UTC
Total New Transactions: 18
Unreviewed Transactions: 18

Account Breakdown:
==================================================

📁 Broker:
   New: 3
   Types:
      • buy: 2
      • dividend: 1

📁 Exchange A:
   New: 5
   Types:
      • deposit: 1
      • trade: 4

📁 Crypto Wallets:
   New: 10
   Types:
      • transfer_in: 3
      • transfer_out: 2
      • staking_reward: 5

📝 Action Required:

Please review and mark the 18 unreviewed transaction(s):
  • Go to the Transactions Overview app
  • Review transaction details
  • Mark as reviewed when confirmed

This helps maintain data quality and ensures accurate portfolio tracking.
```

---

## How It Works

### Architecture

```
Scheduler (worker/scheduler.py)
    ↓
Call run_daily_jobs() → Returns stats
    ↓
Daily Sync Jobs (worker/daily_jobs.py)
    ↓ Sync each account, collect stats
    ↓ Return {'accounts_synced': {...}, 'total_new': N}
    ↓
Scheduler receives stats
    ↓
Query unreviewed transaction count
    ↓
Send to Notification Service (utils/notifications.py)
    ↓
Format Email with Account Breakdown
    ↓
Send via SMTP
```

**Key Design Principle:**
- **Scheduler**: Orchestrates jobs, handles notifications
- **Daily Jobs**: Pure business logic, returns stats
- **Notification Service**: Formats and sends emails

This separation ensures clean architecture and maintainability.

### Transaction Counting

**What counts as "new":**
- Transactions created during this sync (not duplicates)
- Successfully ingested transactions (not skipped or failed)
- Transactions from the configured lookback period (default 7 days)

**What doesn't count:**
- Duplicate transactions (already in database via `external_id`)
- Skipped transactions (missing FX rates, will retry next sync)
- Failed transactions (logged separately in error notifications)

### Lookback Periods

Each account has a configurable lookback period in `app_config.yaml`:

```yaml
binanceth:
  sync_lookback_days: 7  # How far back to fetch during daily sync

binancecom:
  sync_lookback_days: 7

crypto_wallets:
  sync_lookback_days: 7
```

**Recommendation:** Keep at 7 days for daily syncs. The database deduplication ensures you won't get duplicate notifications even if the same transaction appears in multiple syncs.

---

## Workflow Integration

### Daily Routine

1. **01:00 AM** - Scheduler starts daily jobs
2. **01:00-01:10 AM** - Transaction sync from all accounts
3. **01:05 AM** - Email notification sent (if new transactions)
4. **Morning** - Check email, see what was synced
5. **During day** - Open Transactions Overview app, review & mark transactions

### Review Process

**Why review transactions?**
- Ensures data quality
- Catches sync errors or misclassifications
- Confirms transaction types are correct
- Validates amounts and currencies

**How to review:**
1. Open the Transactions Overview app (`main.py` or Docker interface)
2. Filter by `reviewed = False`
3. Check each transaction:
   - Correct amount?
   - Correct type (buy/sell/deposit/etc.)?
   - Correct currency?
   - Reasonable price?
4. Mark as reviewed when confirmed

---

## Troubleshooting

### Not Receiving Notifications

**Check 1: Is the feature enabled?**
```bash
# Check config
grep "send_transaction_notifications" config/app_config.yaml
```

Should show: `send_transaction_notifications: true`

**Check 2: Are emails configured?**
```bash
# Check email settings
grep -A 10 "channels:" config/app_config.yaml
```

Verify `email.enabled: true` and SMTP settings are correct.

**Check 3: Were any new transactions ingested?**
```bash
# Check worker logs
docker logs nicefolio_worker --tail 50 | grep "new transactions"
```

You'll only get notifications if `total_new_transactions > 0`.

**Check 4: Test email manually**
```python
from utils.notifications import send_transaction_ingestion_summary

# Send test notification
send_transaction_ingestion_summary(
    accounts_synced={
        'Test Account': {'new': 5, 'types': {'buy': 3, 'sell': 2}, 'failed': 0}
    },
    total_new=5,
    total_reviewed=0
)
```

### Too Many Notifications

If you're getting too many notifications (e.g., historical backfills):

**Option 1: Temporarily disable**
```yaml
notifications:
  send_transaction_notifications: false  # Will still get error alerts
```

**Option 2: Reduce lookback period**
```yaml
binanceth:
  sync_lookback_days: 1  # Only sync last 24 hours
```

**Option 3: Add email filters**
Set up Gmail/Outlook filters to organize transaction notifications into a folder.

---

## Advanced Configuration

### Multiple Email Recipients

Send notifications to multiple people:

```yaml
notifications:
  channels:
    email:
      to_emails:
        - primary@example.com
        - accountant@example.com
        - spouse@example.com
```

### Combine with Other Notification Channels

While transaction notifications currently only support email, you can receive error alerts via:
- **Email** (SMTP)
- **Home Assistant** (REST API)
- **Telegram** (Bot API)
- **Custom Webhooks**

See [NOTIFICATION_SETUP_GUIDE.md](NOTIFICATION_SETUP_GUIDE.md) for details.

---

## Code Reference

### Key Files Modified

1. **[utils/notifications.py](../utils/notifications.py)** - Added `send_transaction_ingestion_summary()`
2. **[worker/scheduler.py](../worker/scheduler.py)** - Capture stats from daily_jobs and send notification (orchestration layer)
3. **[worker/daily_jobs.py](../worker/daily_jobs.py)** - Collect stats from each account and return to scheduler
4. **[service/ibkr_service.py](../service/ibkr_service.py)** - Return transaction stats
5. **[service/binancecom_service.py](../service/binancecom_service.py)** - Return transaction stats
6. **[config/app_config.yaml](../config/app_config.yaml)** - Added `send_transaction_notifications` setting

### Function Signatures

```python
def send_transaction_ingestion_summary(
    accounts_synced: Dict[str, Dict],
    total_new: int = 0,
    total_reviewed: int = 0
) -> bool:
    """
    Send summary of daily transaction ingestion.
    
    Args:
        accounts_synced: Dict with account details
        total_new: Total new transactions across all accounts
        total_reviewed: Count of transactions already marked as reviewed
    
    Returns:
        bool: True if notification sent successfully
    """
```

**Account stats format:**
```python
{
    'Broker': {
        'new': 5,
        'types': {'buy': 3, 'sell': 2},
        'failed': 0
    },
    'Exchange A': {
        'new': 12,
        'types': {'deposit': 2, 'trade': 10},
        'failed': 0
    }
}
```

---

## Testing

### Manual Test

```python
# In Python REPL or test script
from utils.notifications import send_transaction_ingestion_summary

# Test with sample data
send_transaction_ingestion_summary(
    accounts_synced={
        'Broker': {
            'new': 3,
            'types': {'buy': 2, 'dividend': 1},
            'failed': 0
        },
        'Exchange A': {
            'new': 5,
            'types': {'deposit': 1, 'trade': 4},
            'failed': 0
        }
    },
    total_new=8,
    total_reviewed=0
)
```

### Integration Test

Run the daily jobs manually and check email:

```bash
# Run daily jobs manually
docker exec nicefolio_worker python -c "from worker.daily_jobs import run_daily_jobs; run_daily_jobs()"

# Check logs
docker logs nicefolio_worker --tail 100 | grep -i "notification"
```

---

## Future Enhancements

Potential improvements (not yet implemented):

- [ ] **Telegram support** for transaction notifications
- [ ] **Digest mode**: One weekly email instead of daily
- [ ] **Threshold-based**: Only notify if > X transactions
- [ ] **Symbol-specific**: Alert for specific assets only
- [ ] **Amount-based**: Only notify for large transactions
- [ ] **HTML email**: Rich formatting with tables and colors
- [ ] **Direct links**: Deep links to specific transactions in app

---

## Support

**Questions or issues?**
1. Check logs: `docker logs nicefolio_worker --tail 100`
2. Verify config: `cat config/app_config.yaml | grep -A 20 notifications`
3. Test email manually (see Testing section above)

**Related Documentation:**
- [NOTIFICATION_SETUP_GUIDE.md](NOTIFICATION_SETUP_GUIDE.md) - General notification setup
- [SCHEDULER_CONFIGURATION_GUIDE.md](SCHEDULER_CONFIGURATION_GUIDE.md) - Daily job scheduling
- [TIMEZONE_UTC_STANDARDIZATION.md](TIMEZONE_UTC_STANDARDIZATION.md) - Timestamp handling
