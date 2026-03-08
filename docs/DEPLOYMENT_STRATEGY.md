# NiceFolio Deployment Strategy

## Overview

Docker-based deployment strategy supporting dual environments (development and production) with a shared PostgreSQL database, safe milestone-based production updates, and daily automated scheduling.

### Goals

1. **Development Environment**: Active development with live code mounting
2. **Production Environment**: Stable, always-on application
3. **Shared Database**: Single PostgreSQL instance used by both environments
4. **Safe Updates**: Only deploy to production at milestones
5. **Daily Jobs**: Production scheduler runs automated sync and precomputation

---

## Architecture

### Recommended: Shared Database with Separate App Containers

```
project_root/
├── production/                         ← Production
│   ├── compose.yaml                    ← Production services
│   ├── .env                            ← Production config
│   ├── logs/                           ← Production logs
│   └── data/
│       └── postgres/                   ← Database files (SHARED)
│
└── development/                        ← Development
    ├── compose.dev.yaml                ← Dev services (no database)
    ├── .env.dev                        ← Dev config (points to prod DB)
    ├── logs/                           ← Dev logs
    └── [code files]                    ← Active development

Containers:
├── Production
│   ├── nicefolio_gui:8888              ← Production app
│   ├── nicefolio_scheduler             ← Scheduler (daily jobs)
│   └── postgres:5432                   ← Shared database
│
└── Development
    └── nicefolio_dev_gui:8889          ← Dev app (connects to prod DB)
```

**Benefits:**
- Single database, always in sync
- Production scheduler runs daily jobs
- Dev changes don't affect production app
- Different ports (8888 prod, 8889 dev)
- Easy to test changes before deploying

### Port Allocation

| Port | Service                              |
|------|--------------------------------------|
| 5432 | PostgreSQL (production, shared)      |
| 8888 | Production GUI                       |
| 8889 | Development GUI                      |

### Environment Comparison

| Aspect       | Production              | Development                    |
|--------------|-------------------------|--------------------------------|
| Port         | 8888                    | 8889                           |
| Database     | Runs here               | Connects to prod               |
| Scheduler    | Runs (01:00 ICT)        | NOT running                    |
| Code Updates | Git pull (milestones)   | Live-mounted                   |
| Purpose      | Stable, live            | Active development             |

---

## Quick Setup

### Step 1: Production Setup

```bash
# 1. Create production directory and clone repository
mkdir -p /path/to/production
cd /path/to/production
git clone https://github.com/your-username/nicefolio.git .

# 2. Copy production compose template
cp compose.prod.yaml compose.yaml

# 3. Create .env from template
cp template.env .env
nano .env  # Add your API keys and passwords

# 4. Start database
docker compose up -d postgres
sleep 10

# 5. Initialize database
docker compose run --rm nicefolio_gui python init_db.py
docker compose run --rm nicefolio_gui python seed_db.py

# 6. Load historical data
docker compose run --rm nicefolio_gui python scripts/fetch_historical_marketdata.py --stocks-only

# 7. Start all services
docker compose up -d

# 8. Verify
docker compose ps
docker compose logs -f

# 9. Access at http://localhost:8888
```

### Step 2: Development Setup

```bash
# 1. Go to development directory
cd /path/to/development

# 2. Create .env.dev
cat > .env.dev <<EOF
POSTGRES_PASSWORD=your_prod_password_here
DATABASE_URL=postgresql://nicefolio:\$POSTGRES_PASSWORD@host.docker.internal:5432/nicefolio_db
PORT_NICEGUI=8889
ENVIRONMENT=development
# ... copy API keys from production .env
EOF

# 3. Start development container
docker compose -f compose.dev.yaml --env-file .env.dev up -d

# 4. Verify
docker compose -f compose.dev.yaml ps
docker compose -f compose.dev.yaml logs -f

# 5. Access at http://localhost:8889
```

---

## Docker Compose Configuration

### Production (`compose.yaml`)

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    container_name: nicefolio_postgres
    environment:
      POSTGRES_USER: nicefolio
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: nicefolio_db
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nicefolio"]
      interval: 10s
      timeout: 5s
      retries: 5

  nicefolio_gui:
    build: .
    container_name: nicefolio_gui_prod
    environment:
      - DATABASE_URL=postgresql://nicefolio:${POSTGRES_PASSWORD}@postgres:5432/nicefolio_db
      - PORT_NICEGUI=8888
      - PYTHONPATH=/app
    volumes:
      - ./logs:/app/logs
      - ./config:/app/config:ro
    ports:
      - "8888:8888"
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    command: python main.py

  nicefolio_scheduler:
    build: .
    container_name: nicefolio_scheduler_prod
    environment:
      - DATABASE_URL=postgresql://nicefolio:${POSTGRES_PASSWORD}@postgres:5432/nicefolio_db
      - PYTHONPATH=/app
    volumes:
      - ./logs:/app/logs
      - ./config:/app/config:ro
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    command: python worker/scheduler.py

networks:
  default:
    name: nicefolio_network
```

### Development (`compose.dev.yaml`)

```yaml
version: '3.8'

services:
  nicefolio_dev_gui:
    build: .
    container_name: nicefolio_dev_gui
    environment:
      - DATABASE_URL=postgresql://nicefolio:${POSTGRES_PASSWORD}@host.docker.internal:5432/nicefolio_db
      - PORT_NICEGUI=8889
      - PYTHONPATH=/app
      - ENVIRONMENT=development
    volumes:
      - .:/app
      - ./logs:/app/logs
    ports:
      - "8889:8889"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
    command: python main.py

networks:
  default:
    name: nicefolio_dev_network
```

### Environment File (`.env` template)

```env
# Database
POSTGRES_PASSWORD=your_secure_password_here
DATABASE_URL=postgresql://nicefolio:your_secure_password_here@postgres:5432/nicefolio_db

# Application
PORT_NICEGUI=8888
ENVIRONMENT=production

# API Keys
COINMARKETCAP_API_KEY=your_key_here
IBKR_FLEX_TOKEN=your_token_here
IBKR_FLEX_QUERY_ID=your_query_id_here
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here

# Timezone
TZ=Asia/Bangkok
```

---

## Daily Workflow

### Working in Development

```bash
cd /path/to/development

# Make code changes in VS Code (live-mounted)
# Restart if needed
docker compose -f compose.dev.yaml restart

# View logs
docker compose -f compose.dev.yaml logs -f --tail=50

# Access dev app: http://localhost:8889
```

### Deploying to Production (Milestones)

```bash
# 1. Commit and push from development
cd /path/to/development
git add .
git commit -m "Milestone: Feature XYZ"
git push origin main

# 2. Pull to production
cd /path/to/production
git pull origin main

# 3. Rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d

# 4. Verify
docker compose logs -f --tail=50
# http://localhost:8888
```

---

## Database Safety

**Both environments share the same database.**

```
SAFE operations (read-only):
  ✅ Viewing data, running reports, testing queries

CAREFUL operations (write):
  ⚠️ Adding/modifying transactions, changing portfolios, manual syncs

DANGEROUS operations:
  ❌ Running migrations in dev (test on copy first!)
  ❌ Deleting data, schema changes
  ❌ Running scheduler in dev (scheduler only in prod!)
```

### Testing Database Changes

```bash
# 1. Backup production database first
docker compose exec postgres pg_dump -U nicefolio nicefolio_db > backup_$(date +%Y%m%d).sql

# 2. Test on a separate test database if needed
# 3. Only apply to production when confirmed safe
```

---

## Maintenance Commands

### Production

```bash
cd /path/to/production

# View logs
docker compose logs -f nicefolio_gui
docker compose logs -f nicefolio_scheduler

# Restart
docker compose restart

# Stop (keeps database)
docker compose stop nicefolio_gui nicefolio_scheduler

# Full shutdown (data persists in ./data)
docker compose down

# Update to latest code
git pull origin main
docker compose down
docker compose build --no-cache
docker compose up -d

# Backup database
docker compose exec postgres pg_dump -U nicefolio nicefolio_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore database
docker compose exec -T postgres psql -U nicefolio nicefolio_db < backup.sql

# Check database size
docker compose exec postgres psql -U nicefolio -c "SELECT pg_size_pretty(pg_database_size('nicefolio_db'));"

# PostgreSQL shell
docker compose exec postgres psql -U nicefolio nicefolio_db
```

### Development

```bash
cd /path/to/development

docker compose -f compose.dev.yaml logs -f
docker compose -f compose.dev.yaml restart
docker compose -f compose.dev.yaml down
docker compose -f compose.dev.yaml --env-file .env.dev up -d

# Shell access
docker compose -f compose.dev.yaml exec nicefolio_dev_gui bash

# Run scripts in container
docker compose -f compose.dev.yaml exec nicefolio_dev_gui python scripts/validate_configs.py
```

---

## Emergency Procedures

### Rollback Production

```bash
cd /path/to/production

# Stop services
docker compose down

# Rollback code
git log --oneline        # Find previous commit
git reset --hard <hash>  # Reset to known good state

# Restart
docker compose up -d
```

### Restore Database from Backup

```bash
cd /path/to/production

# Stop services
docker compose down

# Start database only
docker compose up -d postgres
sleep 5

# Restore
docker compose exec -T postgres psql -U nicefolio nicefolio_db < backup_YYYYMMDD.sql

# Restart all services
docker compose up -d
```

---

## Pre-Deployment Checklist

- [ ] Code tested in development environment
- [ ] All tests passing
- [ ] Configuration files reviewed
- [ ] API keys verified in production `.env`
- [ ] Database backup created
- [ ] Git committed and pushed
- [ ] Documentation updated
- [ ] Scheduler timing verified (01:00 ICT)
- [ ] Port conflicts checked
- [ ] Logs reviewed for errors

---

## Troubleshooting

### Dev container can't connect to production database

```bash
# 1. Verify production postgres is running
docker compose ps postgres

# 2. Check postgres is listening
sudo netstat -tulpn | grep 5432

# 3. Test connection from host
psql -h localhost -p 5432 -U nicefolio -d nicefolio_db

# 4. Check dev DATABASE_URL
grep DATABASE_URL .env.dev
# Should be: postgresql://nicefolio:password@host.docker.internal:5432/nicefolio_db
```

### Port already in use

```bash
sudo lsof -i :8888  # or :8889
docker compose down
# OR change PORT_NICEGUI in .env
```

### Scheduler running in both environments

```bash
# Ensure compose.dev.yaml has NO scheduler service
# Only nicefolio_dev_gui should exist in dev compose
docker compose -f compose.dev.yaml down
```

### Changes not reflecting in production

```bash
cd /path/to/production
git pull origin main
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose logs -f
```
