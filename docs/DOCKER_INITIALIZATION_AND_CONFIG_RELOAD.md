# Docker Initialization and Configuration Reload

## Overview

NiceFolio uses an **idempotent initialization pattern** that ensures the application always starts with the correct database schema and configuration, even after updates or config changes.

## Initialization Process

Every time a Docker container starts, the following steps are executed automatically:

### 1. Docker Entrypoint Script (`docker-entrypoint.sh`)

The entrypoint script orchestrates the startup sequence:

```bash
Step 0: Install Dependencies (if needed)
  └─ System packages (gcc, postgresql-client, etc.)
  └─ Python packages from requirements.txt
  └─ Cached after first run (fast on subsequent starts)

Step 1: Wait for PostgreSQL
  └─ Polls database until ready (max 30 attempts)
  └─ Ensures database is available before proceeding

Step 2: Initialize Database Schema (init_db.py)
  └─ Creates all tables, indexes, constraints
  └─ IDEMPOTENT: Safe to run multiple times
  └─ Existing tables are not modified or dropped
  └─ New tables/columns are added automatically

Step 3: Seed Configuration Data (seed_db.py)
  └─ Syncs portfolio_config.yaml → portfolios table
  └─ Syncs accounts_config.yaml → accounts table
  └─ IDEMPOTENT: Updates existing records, creates new ones
  └─ Non-critical: Continues even if files are missing

Step 4: Start Application
  └─ GUI: python main.py
  └─ Worker: python worker/scheduler.py
```

## Configuration Reload

### app_config.yaml Reload Strategy

The `app_config.yaml` is loaded at **container startup** by the entrypoint script. Changes to this file require a container restart to take effect.

**Why?**
- The config is cached in memory for performance (避免repeated file I/O)
- The scheduler reads config once at startup (schedules are set)
- Reloading mid-execution could cause inconsistent state

**How to apply changes:**
```bash
# Development (local)
# Just restart the container - entrypoint will reload config
docker compose restart nicefolio_gui
docker compose restart nicefolio_worker

# Production (NiceFolio)
cd /path/to/nicefolio
git pull  # Get latest config changes
docker compose restart nicefolio_gui
docker compose restart nicefolio_worker
```

### Manual Config Reload (Advanced)

For development/testing, you can force reload the config without restarting:

```python
from utils.app_config import reload_app_config

# Force reload from disk
config = reload_app_config()

# Now use the fresh config
sync_lookback_days = config['binancecom']['sync_lookback_days']
```

**⚠️ Warning:** This only reloads the config cache. It does NOT:
- Reschedule jobs in the scheduler (requires restart)
- Update already-running processes
- Reload configs cached by other modules

**Best Practice:** Always restart containers to apply config changes.

### portfolio_config.yaml and accounts_config.yaml Reload

These configs are reloaded **on every container restart** via `seed_db.py`.

**What happens:**
1. Container starts
2. Entrypoint runs `seed_db.py`
3. Script reads YAML files
4. Updates database records (UPSERT logic)
5. Application starts with fresh data

**Example workflow:**
```bash
# 1. Edit portfolio_config.yaml
vim config/portfolio_config.yaml

# 2. Restart container (triggers seed_db.py)
docker compose restart nicefolio_gui

# 3. Changes are now in database
# No manual sync needed!
```

## Why This Approach?

### ✅ Benefits

1. **Zero Manual Steps**
   - No need to run `init_db.py` or `seed_db.py` manually
   - Config changes applied automatically on restart
   - Schema migrations applied automatically

2. **Idempotent Operations**
   - Safe to restart containers anytime
   - No risk of duplicate data or errors
   - Failed startups can be retried safely

3. **Development-Production Parity**
   - Same initialization process in dev and prod
   - Consistent behavior across environments
   - Easy to reproduce issues

4. **Git-Driven Deployment**
   ```bash
   # Production deployment workflow
   cd /path/to/nicefolio
   git pull  # Get code + config changes
   docker compose restart  # Apply everything
   ```

5. **Self-Healing**
   - Missing tables? Created on restart.
   - Outdated config? Updated on restart.
   - Stale cache? Cleared on restart.

### 🚀 Performance

- **First start:** 30-60 seconds (install dependencies)
- **Subsequent starts:** 5-10 seconds (cached dependencies)
- **Config reload:** Instant (restart only reloads config, not dependencies)

### 📋 Initialization Logs

Check container logs to verify initialization:

```bash
# Watch GUI initialization
docker compose logs -f nicefolio_gui

# Watch Worker initialization
docker compose logs -f nicefolio_worker

# Look for these log sections:
# - "Step 0: Installing Dependencies" (first time only)
# - "Step 1/3: Initialize Database Schema"
# - "Step 2/3: Seed Configuration Data"
# - "Step 3/3: Starting Application"
```

## Troubleshooting

### Container fails to start

**Check logs:**
```bash
docker compose logs nicefolio_gui
docker compose logs nicefolio_worker
```

**Common issues:**

1. **PostgreSQL not ready**
   - Wait 30 seconds and check again
   - Database might still be initializing

2. **Database credentials wrong**
   - Check `.env` file
   - Ensure `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` are set

3. **Config file syntax error**
   - Validate YAML syntax: `python -c "import yaml; yaml.safe_load(open('config/app_config.yaml'))"`
   - Check for tabs (use spaces in YAML)

4. **Dependency installation fails**
   - Check network connectivity
   - Try: `docker compose down && docker compose up -d` (fresh start)

### Config changes not applied

**Verify restart:**
```bash
# Check container restart time
docker compose ps

# Force restart
docker compose restart nicefolio_gui
docker compose restart nicefolio_worker
```

**Check if config was reloaded:**
```bash
# View initialization logs
docker compose logs nicefolio_gui | grep "Seeding database"
docker compose logs nicefolio_worker | grep "Seeding database"

# Should see:
# "Seeding database with configuration data..."
# "Portfolios synced successfully"
# "Accounts synced successfully"
```

### Schema changes not applied

**Check init_db.py logs:**
```bash
docker compose logs nicefolio_gui | grep "Initialize Database"

# Should see:
# "Initializing database schema..."
# "Database schema initialized successfully!"
# "Tables in database (X): ..."
```

**If tables are missing:**
```bash
# Force recreation
docker compose down
docker compose up -d

# This will:
# 1. Stop all containers
# 2. Start fresh
# 3. Run init_db.py from scratch
```

## Best Practices

### 1. Always Use Git for Config Changes

```bash
# Development
vim config/app_config.yaml
git add config/app_config.yaml
git commit -m "Update sync_lookback_days to 7"
git push

# Production
cd /path/to/nicefolio
git pull
docker compose restart
```

### 2. Test Config Changes Locally First

```bash
# Development environment
cd /path/to/nicefolio
vim config/app_config.yaml
docker compose -f compose.dev.yaml restart

# Verify in logs
docker compose -f compose.dev.yaml logs | grep "sync_lookback_days"

# If good, commit and deploy to production
```

### 3. Verify After Config Changes

```bash
# After restart, check logs
docker compose logs nicefolio_worker | tail -50

# Look for:
# - "Scheduled daily data sync at XX:XX"
# - "Scheduled position reconciliation at XX:XX"
# - "Scheduled snapshot creation at XX:XX"
# - "Scheduled weekly jobs at XX:XX on Sundays"

# Verify times match your app_config.yaml
```

### 4. Use Environment Variables for Secrets

**Good:**
```yaml
# .env file
BINANCECOM_API_KEY=xxx
BINANCECOM_API_SECRET=yyy
```

**Bad:**
```yaml
# app_config.yaml (DON'T DO THIS)
binancecom:
  api_key: xxx  # ❌ Don't commit secrets!
```

## Summary

| Aspect                           | Approach                   | Trigger                           |
| -------------------------------- | -------------------------- | --------------------------------- |
| **Database Schema**              | Automatic via `init_db.py` | Container start                   |
| **Portfolio/Account Config**     | Automatic via `seed_db.py` | Container start                   |
| **App Config (app_config.yaml)** | Loaded at startup, cached  | Container start                   |
| **Config Reload**                | Restart container          | Manual (`docker compose restart`) |
| **Schema Migrations**            | Automatic on restart       | Container start (idempotent)      |
| **Dependency Updates**           | Cached after first install | Container rebuild                 |

**Key Takeaway:** Just restart containers to apply changes. The entrypoint handles everything else automatically.
