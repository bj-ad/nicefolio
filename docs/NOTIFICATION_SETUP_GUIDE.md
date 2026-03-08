# Notification System Setup Guide

## Overview

The Portfolio Tracker now supports automated notifications for job failures and critical alerts through multiple channels:

- 📧 **Email** (SMTP)
- 🏠 **Home Assistant** (REST API)
- 💬 **Telegram** (Bot API)
- 🔗 **Custom Webhooks** (any HTTP endpoint)

## Quick Start

### 1. Enable Notifications

Edit `config/app_config.yaml`:

```yaml
notifications:
  enabled: true  # Change from false to true
```

### 2. Configure Your Preferred Channel

Choose one or more channels and configure them in `config/app_config.yaml`.

---

## Channel Setup Guides

### 📧 Email (Gmail Example)

**Best for:** Simple email alerts

```yaml
notifications:
  enabled: true
  channels:
    email:
      enabled: true
      smtp_server: smtp.gmail.com
      smtp_port: 587
      smtp_user: your-email@gmail.com
      smtp_password: ""  # Set via SMTP_PASSWORD env var
      from_email: your-email@gmail.com
      to_emails:
        - your-email@gmail.com
```

**Gmail Setup:**

1. Enable 2FA on your Google account
2. Go to: https://myaccount.google.com/apppasswords
3. Generate App Password
4. Set environment variable:
   ```bash
   export SMTP_PASSWORD="your-app-password"
   ```

**Docker Setup:**

Add to `compose.yaml`:
```yaml
services:
  nicefolio_worker:
    environment:
      - SMTP_PASSWORD=${SMTP_PASSWORD}
```

Then create `.env` file:
```
SMTP_PASSWORD=your-app-password
```

---

### 🏠 Home Assistant (Recommended!)

**Best for:** Smart home integration, mobile notifications, persistent alerts

```yaml
notifications:
  enabled: true
  channels:
    home_assistant:
      enabled: true
      url: http://homeassistant.local:8123
      token: ""  # Set via HOME_ASSISTANT_TOKEN env var
      service: notify.notify  # or notify.mobile_app_phone
```

**Setup:**

1. **Generate Long-Lived Access Token:**
   - Open Home Assistant
   - Click your profile (bottom left)
   - Scroll to "Long-Lived Access Tokens"
   - Click "Create Token"
   - Name it "Portfolio Tracker"
   - Copy the token

2. **Set Environment Variable:**
   ```bash
   export HOME_ASSISTANT_TOKEN="your-token-here"
   ```

3. **Test from Terminal:**
   ```bash
   curl -X POST http://homeassistant.local:8123/api/services/notify/notify \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"message": "Test from Portfolio Tracker"}'
   ```

**Docker Setup:**

Add to `compose.yaml`:
```yaml
services:
  nicefolio_worker:
    environment:
      - HOME_ASSISTANT_TOKEN=${HOME_ASSISTANT_TOKEN}
```

**Notification Services:**

- `notify.notify` - Default (all devices)
- `notify.mobile_app_YOUR_PHONE` - Specific phone
- `notify.telegram` - If you have Telegram integration
- `persistent_notification.create` - UI notification only

**Example Home Assistant Automation:**

```yaml
automation:
  - alias: "Portfolio Tracker Alert"
    trigger:
      - platform: event
        event_type: call_service
        event_data:
          domain: notify
          service: notify
    condition:
      - condition: template
        value_template: "{{ 'Portfolio Tracker' in trigger.event.data.service_data.title }}"
    action:
      - service: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.service_data.title }}"
          message: "{{ trigger.event.data.service_data.message }}"
          data:
            priority: high
            ttl: 0
            channel: portfolio_alerts
```

---

### 💬 Telegram

**Best for:** Instant mobile notifications

```yaml
notifications:
  enabled: true
  channels:
    telegram:
      enabled: true
      bot_token: ""  # Set via TELEGRAM_BOT_TOKEN env var
      chat_id: "123456789"  # Your chat ID
```

**Setup:**

1. **Create Telegram Bot:**
   - Open Telegram and search for `@BotFather`
   - Send `/newbot`
   - Follow instructions to create bot
   - Copy the bot token

2. **Get Your Chat ID:**
   - Send a message to your bot
   - Visit: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
   - Look for `"chat":{"id": YOUR_CHAT_ID}`

3. **Set Environment Variables:**
   ```bash
   export TELEGRAM_BOT_TOKEN="your-bot-token"
   ```

4. **Update Config:**
   ```yaml
   telegram:
     enabled: true
     chat_id: "YOUR_CHAT_ID"  # From step 2
   ```

**Group Chats:**

1. Add bot to group
2. Get group chat ID (negative number like `-123456789`)
3. Use group chat ID in config

---

### 🔗 Custom Webhook

**Best for:** Discord, Slack, custom monitoring systems

```yaml
notifications:
  enabled: true
  channels:
    webhook:
      enabled: true
      url: https://your-webhook-url.com/portfolio-tracker
      method: POST
      headers:
        Content-Type: application/json
```

**Payload Format:**

```json
{
  "timestamp": "2025-12-07T10:30:00",
  "severity": "🚨 CRITICAL",
  "job_name": "Data Sync",
  "job_type": "daily",
  "error_message": "Connection timeout",
  "source": "portfolio_tracker"
}
```

**Discord Example:**

```yaml
webhook:
  enabled: true
  url: https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
  method: POST
  headers:
    Content-Type: application/json
```

**IFTTT Example:**

```yaml
webhook:
  enabled: true
  url: https://maker.ifttt.com/trigger/portfolio_alert/with/key/YOUR_KEY
  method: POST
```

---

## Testing

### Test All Channels

```bash
python scripts/test_notifications.py
```

### Test Specific Channel

```bash
# Email
python scripts/test_notifications.py --channel email

# Home Assistant
python scripts/test_notifications.py --channel home_assistant

# Telegram
python scripts/test_notifications.py --channel telegram

# Webhook
python scripts/test_notifications.py --channel webhook
```

### Check Configuration Only

```bash
python scripts/test_notifications.py --check-only
```

---

## What Gets Notified

### Automatic Notifications (Failure Alerts)

**Daily Jobs:**
- Data Sync failures (IBKR, Binance, crypto wallets, market prices)
- Daily Snapshot creation failures
- Manual Portfolio Forward-Fill failures

**Weekly Jobs:**
- Lot Recreation failures (🚨 CRITICAL)
- Rolling Window Snapshot regeneration failures
- Position Recreation failures (🚨 CRITICAL)

**Startup:**
- Missing data detection (warnings only, not critical)

### Optional Success Notifications

Enable in config:
```yaml
notifications:
  send_success_notifications: true
```

Usually only useful for Home Assistant dashboards or monitoring systems.

---

## Notification Message Format

### Failure Alert Example

```
🚨 CRITICAL Job Failure Alert

Job: Weekly Lot Recreation
Type: WEEKLY
Time: 2025-12-07 03:15:00

Error:
Connection timeout when accessing database

Additional Information:
  jobs: Lot Recreation + Rolling Window Snapshots + Position Recreation
  severity: HIGH - affects cost basis and historical accuracy
  next_run: Next Sunday
  manual_fix: Can run scripts/regenerate_all.sh manually

Please check the logs for more details:
  docker logs nicefolio_worker --tail 100

Or in the app logs:
  logs/app.log
```

---

## Security Best Practices

### 1. Use Environment Variables (Recommended)

**Never commit sensitive credentials to git!**

```bash
# .env file (git-ignored)
SMTP_PASSWORD=your-app-password
HOME_ASSISTANT_TOKEN=your-token
TELEGRAM_BOT_TOKEN=your-bot-token
```

### 2. Docker Compose

```yaml
services:
  nicefolio_worker:
    env_file:
      - .env
    environment:
      - SMTP_PASSWORD=${SMTP_PASSWORD}
      - HOME_ASSISTANT_TOKEN=${HOME_ASSISTANT_TOKEN}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
```

### 3. File Permissions

```bash
chmod 600 .env
chmod 600 config/app_config.yaml
```

---

## Troubleshooting

### No Notifications Received

1. **Check if enabled:**
   ```bash
   python scripts/test_notifications.py --check-only
   ```

2. **Test specific channel:**
   ```bash
   python scripts/test_notifications.py --channel email
   ```

3. **Check logs:**
   ```bash
   docker logs nicefolio_worker | grep notification
   ```

### Gmail "Less Secure App" Error

Gmail requires App Passwords (not your regular password):
1. Enable 2FA on Google account
2. Generate App Password
3. Use App Password in SMTP_PASSWORD

### Home Assistant Connection Refused

1. Check Home Assistant is accessible:
   ```bash
   curl http://homeassistant.local:8123/api/
   ```

2. Verify token:
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     http://homeassistant.local:8123/api/
   ```

3. Check Docker network (if running in containers):
   - Use `http://homeassistant:8123` if on same Docker network
   - Use `http://HOST_IP:8123` if different networks

### Telegram Bot Not Responding

1. Verify bot token:
   ```bash
   curl https://api.telegram.org/botYOUR_BOT_TOKEN/getMe
   ```

2. Check chat ID:
   ```bash
   curl https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```

3. Make sure you sent at least one message to the bot first

---

## Multiple Channels

You can enable multiple channels simultaneously:

```yaml
notifications:
  enabled: true
  channels:
    email:
      enabled: true
      # ... email config
    
    home_assistant:
      enabled: true
      # ... HA config
    
    telegram:
      enabled: true
      # ... telegram config
```

All enabled channels will receive notifications.

---

## FAQ

**Q: Will I get spammed with notifications?**
A: No. Notifications are only sent for:
- Job failures (errors that prevent normal operation)
- Weekly job failures (more critical)
- Not sent for normal operations or minor warnings

**Q: Can I customize the notification messages?**
A: Yes! Edit `utils/notifications.py` and modify the message templates in `send_job_failure_alert()`.

**Q: Can I add more notification channels?**
A: Yes! Add new methods to the `NotificationService` class in `utils/notifications.py`. Examples: Discord, Slack, Pushover, etc.

**Q: What happens if notifications fail?**
A: The system logs the error but continues normal operation. Notification failures don't affect portfolio tracking.

**Q: Do I need to restart the worker after config changes?**
A: Yes, restart the worker container:
```bash
docker compose restart nicefolio_worker
```

**Q: Can I get notifications for successful jobs?**
A: Yes, enable `send_success_notifications: true` in config. Usually only sent to Home Assistant (less intrusive).

---

## Next Steps

1. Choose your notification channel(s)
2. Configure credentials in `config/app_config.yaml` or environment variables
3. Test with `python scripts/test_notifications.py`
4. Restart worker: `docker compose restart nicefolio_worker`
5. Wait for next scheduled job or trigger a manual test failure

Now you'll get alerted immediately when something goes wrong, instead of checking logs manually! 🎉
