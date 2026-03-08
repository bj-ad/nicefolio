# Config Files - Template System

This directory contains configuration files for the NiceFolio portfolio tracker.

## 🔒 Security Notice

**Config files are gitignored** to protect sensitive information:
- Account names and IDs
- Portfolio configurations
- Personal preferences
- API configuration details

## 📋 Template Files

Each `.yaml.template` file is a template that gets copied to create the active config:

- `accounts_config.yaml.template` → `accounts_config.yaml`
- `app_config.yaml.template` → `app_config.yaml`
- `portfolio_config.yaml.template` → `portfolio_config.yaml`
- `source_mapping.yaml.template` → `source_mapping.yaml`
- `symbol_mapping.yaml.template` → `symbol_mapping.yaml`
- `symbol_normalization.yaml.template` → `symbol_normalization.yaml`

## 🚀 Automatic Initialization

When running in Docker, configs are **automatically initialized** from templates on container start:

```bash
docker compose up -d
# Config files are created from templates if they don't exist
```

The initialization is handled by `scripts/init_configs.py` which is called by `docker-entrypoint.sh`.

## 🛠️ Manual Setup (Local Development)

For local development outside Docker:

```bash
# Option 1: Use the init script
python scripts/init_configs.py

# Option 2: Copy templates manually
cp config/accounts_config.yaml.template config/accounts_config.yaml
cp config/app_config.yaml.template config/app_config.yaml
cp config/portfolio_config.yaml.template config/portfolio_config.yaml
cp config/source_mapping.yaml.template config/source_mapping.yaml
cp config/symbol_mapping.yaml.template config/symbol_mapping.yaml
cp config/symbol_normalization.yaml.template config/symbol_normalization.yaml
```

## ✏️ Customization

After initialization, customize the config files for your needs:

1. **`accounts_config.yaml`** - Define your accounts (exchanges, brokers, wallets, etc.)
2. **`portfolio_config.yaml`** - Configure your portfolios
3. **`app_config.yaml`** - Application settings (scheduler, notifications, etc.)
4. **Symbol mappings** - Usually don't need changes unless adding custom symbols

## 🔄 Updating Templates

When updating template files:

1. Edit the `.yaml.template` file
2. Commit the template to git
3. Update your active config manually or delete and regenerate

**Note:** Active `.yaml` files are never committed to git for security reasons.

## 📚 File Descriptions

### accounts_config.yaml
Defines all accounts in the system:
- Exchange accounts (Binance, etc.)
- Broker accounts (IBKR, etc.)
- Hardware wallets (Trezor, Ledger)
- Physical assets
- Asset management accounts

### portfolio_config.yaml
Defines portfolios and their properties:
- Portfolio ID, name, and type
- Base currency
- Update method (automatic/manual)
- Account assignments
- Status (active/historical)

### app_config.yaml
Application-wide configuration:
- Scheduler settings (job timing, frequency)
- Base currency
- Tracked symbols (securities, crypto, commodities)
- Notification settings (email, Home Assistant, Telegram)
- Feature flags

### source_mapping.yaml
Maps transaction sources to accounts:
- CSV import source identification
- Account assignment rules

### symbol_mapping.yaml
Maps external symbols to internal symbols:
- Exchange-specific symbol formats
- Stablecoin mappings
- Token address mappings

### symbol_normalization.yaml
Symbol normalization rules:
- Standardizes symbol formats across sources
- Handles variations (BTC vs Bitcoin)

## 🔍 Verification

Verify your configs are properly set up:

```bash
python scripts/validate_configs.py
```

## ❗ Important Notes

1. **Never commit active `.yaml` files** - Only commit `.yaml.template` files
2. **Review templates before using** - Ensure they match your setup
3. **Keep templates generic** - Remove sensitive data before committing templates
4. **Document changes** - Add comments to templates explaining customization points
