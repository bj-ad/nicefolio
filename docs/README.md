# NiceFolio Documentation

Comprehensive documentation for the NiceFolio portfolio tracking application.

---

## Quick Start

### For New Developers
1. [AI_AGENT_ONBOARDING.md](AI_AGENT_ONBOARDING.md) — System overview and architecture
2. [DEPLOYMENT_STRATEGY.md](DEPLOYMENT_STRATEGY.md) — Docker setup and deployment
3. Browse the category sections below for your area of interest

### For Operations
1. [SCHEDULER_CONFIGURATION_GUIDE.md](SCHEDULER_CONFIGURATION_GUIDE.md) — Daily job scheduling
2. [DOCKER_INITIALIZATION_AND_CONFIG_RELOAD.md](DOCKER_INITIALIZATION_AND_CONFIG_RELOAD.md) — Container lifecycle
3. [NOTIFICATION_SETUP_GUIDE.md](NOTIFICATION_SETUP_GUIDE.md) — Email/Telegram alerts

---

## Documentation Index

### Architecture & Design (4 docs)

| Document | Description |
|----------|-------------|
| [ACCOUNT_PORTFOLIO_RELATIONSHIP](architecture/ACCOUNT_PORTFOLIO_RELATIONSHIP.md) | Many-to-many design between accounts and portfolios |
| [ARCHITECTURE_ANALYSIS_PARSERS](architecture/ARCHITECTURE_ANALYSIS_PARSERS.md) | Why parsers exist for exchanges but not blockchain providers |
| [DATABASE_SCHEMA_REFERENCE](architecture/DATABASE_SCHEMA_REFERENCE.md) | Quick reference for all database tables and columns |
| [UI_FRAMEWORK_DECISION_FINAL](architecture/UI_FRAMEWORK_DECISION_FINAL.md) | NiceGUI selection rationale (vs Streamlit/Gradio) |

### Blockchain Providers (7 docs)

| Document | Description |
|----------|-------------|
| [BLOCKCHAIN_PROVIDERS_COMPLETE](blockchain/BLOCKCHAIN_PROVIDERS_COMPLETE.md) | Overview of all 6 blockchain integrations |
| [ADA_IMPLEMENTATION_SUMMARY](blockchain/ADA_IMPLEMENTATION_SUMMARY.md) | Cardano — Blockfrost API, staking rewards |
| [BSC_IMPLEMENTATION_SUMMARY](blockchain/BSC_IMPLEMENTATION_SUMMARY.md) | BNB Smart Chain — BscScan, native staking |
| [BTC_IMPLEMENTATION_SUMMARY](blockchain/BTC_IMPLEMENTATION_SUMMARY.md) | Bitcoin — UTXO model, xpub HD wallets |
| [ETH_IMPLEMENTATION_SUMMARY](blockchain/ETH_IMPLEMENTATION_SUMMARY.md) | Ethereum — Etherscan V2, gas tracking |
| [SOL_IMPLEMENTATION_SUMMARY](blockchain/SOL_IMPLEMENTATION_SUMMARY.md) | Solana — stake accounts, warmup/cooldown |
| [XRP_IMPLEMENTATION_SUMMARY](blockchain/XRP_IMPLEMENTATION_SUMMARY.md) | XRP — reserves, multi-server failover |

### Configuration (2 docs)

| Document | Description |
|----------|-------------|
| [CONFIG_LOADERS_QUICK_REFERENCE](config/CONFIG_LOADERS_QUICK_REFERENCE.md) | API reference for all YAML config loaders |
| [DOCKER_USER_CONFIG](config/DOCKER_USER_CONFIG.md) | Docker container user/UID/GID setup |

### Crypto Wallets (3 docs)

| Document | Description |
|----------|-------------|
| [CRYPTO_WALLET_CRUD_QUICKSTART](crypto/CRYPTO_WALLET_CRUD_QUICKSTART.md) | Quick reference for wallet sync operations |
| [CRYPTO_WALLET_MANAGER_GUIDE](CRYPTO_WALLET_MANAGER_GUIDE.md) | GUI-based wallet management |
| [CRYPTO_RECONCILIATION_GUIDE](CRYPTO_RECONCILIATION_GUIDE.md) | Balance verification and reconciliation |

### Market Data (1 doc)

| Document | Description |
|----------|-------------|
| [MARKETDATA_SERVICES_QUICK_REFERENCE](marketdata/MARKETDATA_SERVICES_QUICK_REFERENCE.md) | API reference for FX, crypto, stock, and gold services |

### Portfolio Management (5 docs)

| Document | Description |
|----------|-------------|
| [POSITION_LOT_SNAPSHOT_IMPLEMENTATION](portfolio/POSITION_LOT_SNAPSHOT_IMPLEMENTATION.md) | Core position/lot/snapshot system |
| [HYBRID_LOT_RECONCILIATION](portfolio/HYBRID_LOT_RECONCILIATION.md) | Incremental + weekly lot reconciliation |
| [POSITION_CACHE_IMPLEMENTATION](POSITION_CACHE_IMPLEMENTATION.md) | Position cache table and precomputation |
| [FOREIGN_CURRENCY_LOT_TRACKING](FOREIGN_CURRENCY_LOT_TRACKING.md) | Multi-currency lot cost basis tracking |
| [HARDCODED_PORTFOLIO_IDS](../HARDCODED_PORTFOLIO_IDS.md) | Tech debt: hardcoded portfolio IDs to migrate to config |

### Benchmarks & Metrics (2 docs)

| Document | Description |
|----------|-------------|
| [BENCHMARK_ARCHITECTURE](BENCHMARK_ARCHITECTURE.md) | Individual + composite benchmark system |
| [STAKING_REWARD_CALCULATION_LOGIC](STAKING_REWARD_CALCULATION_LOGIC.md) | Auto-compounding reward calculations |

### Caching & Performance (2 docs)

| Document | Description |
|----------|-------------|
| [CACHE_INVALIDATION_STRATEGY](CACHE_INVALIDATION_STRATEGY.md) | Dashboard cache architecture and deployment |
| [LOADING_ANIMATIONS_BEST_PRACTICES](LOADING_ANIMATIONS_BEST_PRACTICES.md) | NiceGUI loading patterns (skeletons, timers) |

### Deployment & Operations (5 docs)

| Document | Description |
|----------|-------------|
| [DEPLOYMENT_STRATEGY](DEPLOYMENT_STRATEGY.md) | Docker Compose dual-environment setup |
| [DOCKER_INITIALIZATION_AND_CONFIG_RELOAD](DOCKER_INITIALIZATION_AND_CONFIG_RELOAD.md) | Container startup and config reloading |
| [SCHEDULER_CONFIGURATION_GUIDE](SCHEDULER_CONFIGURATION_GUIDE.md) | 4-job daily scheduler with portfolio coverage |
| [NOTIFICATION_SETUP_GUIDE](NOTIFICATION_SETUP_GUIDE.md) | Email/Telegram/Home Assistant notifications |
| [TRANSACTION_NOTIFICATION_GUIDE](TRANSACTION_NOTIFICATION_GUIDE.md) | Daily transaction ingestion alerts |

### Data Handling (5 docs)

| Document | Description |
|----------|-------------|
| [BACKFILLING_GUIDE](BACKFILLING_GUIDE.md) | Historical data backfilling procedures |
| [HISTORICAL_DATA_GAP_HANDLING_QUICKREF](HISTORICAL_DATA_GAP_HANDLING_QUICKREF.md) | Gap detection and forward-fill rules |
| [ECB_FX_RATE_COMPLIANCE](ECB_FX_RATE_COMPLIANCE.md) | ECB exchange rate sourcing and compliance |
| [TIMEZONE_UTC_STANDARDIZATION](TIMEZONE_UTC_STANDARDIZATION.md) | UTC standardization strategy |
| [SYMBOL_MAPPING_BEST_PRACTICES](SYMBOL_MAPPING_BEST_PRACTICES.md) | Adding European ETFs and symbol configuration |

### Exchange Integrations (1 doc)

| Document | Description |
|----------|-------------|
| [BINANCETH_COMPLETE_WORKFLOW](BINANCETH_COMPLETE_WORKFLOW_WITH_HARDWARE_WALLET.md) | Binance Thailand fiat/crypto workflow |

### System Reference (5 docs)

| Document | Description |
|----------|-------------|
| [AI_AGENT_ONBOARDING](AI_AGENT_ONBOARDING.md) | System overview for developers and AI agents |
| [DATABASE_COLUMN_REFERENCE](DATABASE_COLUMN_REFERENCE.md) | Column-level database reference |
| [WORKER_GUI_ARCHITECTURE](WORKER_GUI_ARCHITECTURE.md) | Worker/GUI independence and health checks |
| [STAKING_AND_LOT_TRACKING_ANALYSIS](STAKING_AND_LOT_TRACKING_ANALYSIS.md) | Cross-chain staking model analysis |
| [SOL_QUICK_REFERENCE](SOL_QUICK_REFERENCE.md) | Solana sync optimization and RPC settings |

---

## Documentation Statistics

- **Total**: 42 documents across 7 subdirectories
- **Categories**: Architecture, Blockchain, Config, Crypto, Market Data, Portfolio, Operations
- **Code Examples**: 200+ snippets with SQL, Python, Bash, and YAML

---

## Naming Conventions

| Suffix | Meaning |
|--------|---------|
| `_IMPLEMENTATION` | Implementation details and code reference |
| `_SUMMARY` | Concise overview of a subsystem |
| `_QUICKSTART` / `_QUICKREF` | Quick reference with examples |
| `_ANALYSIS` | Design analysis or architecture rationale |
| `_GUIDE` | Step-by-step operational guide |
| `_STRATEGY` | Architecture decision and deployment strategy |
