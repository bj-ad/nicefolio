#!/bin/bash
# Docker Entrypoint Script for NiceFolio
# Ensures database and configuration are initialized before starting the application
#
# This script:
# 1. Waits for PostgreSQL to be ready
# 2. Initializes database schema (idempotent - safe to run multiple times)
# 3. Seeds configuration data from YAML files (idempotent)
# 4. Starts the main application
#
# All steps are idempotent and safe to run on every container restart.
# This ensures:
# - Schema changes are automatically applied
# - Config file changes (app_config.yaml, portfolio_config.yaml, etc.) are reloaded
# - Application always starts with correct database state

set -e  # Exit on error

echo "=========================================="
echo "NiceFolio Docker Entrypoint"
echo "=========================================="

# Step 0: Create application user if it doesn't exist (for file ownership)
# Configurable via APP_USER, APP_UID, APP_GID environment variables
APP_USER=${APP_USER:-appuser}
USER_ID=${APP_UID:-1000}
GROUP_ID=${APP_GID:-1000}

if ! id "$APP_USER" &>/dev/null; then
    echo "Creating $APP_USER user..."
    groupadd -g $GROUP_ID $APP_USER 2>/dev/null || true
    useradd -u $USER_ID -g $GROUP_ID -m -s /bin/bash $APP_USER 2>/dev/null || true
    echo "✅ $APP_USER user created (UID:$USER_ID, GID:$GROUP_ID)"
else
    echo "✅ $APP_USER user already exists"
fi

# Step 1: Install system dependencies and Python packages (if needed)
if [ ! -f /tmp/.packages_installed ]; then
    echo ""
    echo "=========================================="
    echo "Step 1: Installing Dependencies"
    echo "=========================================="
    
    # Install system dependencies
    echo "📦 Installing system packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        gcc g++ cmake build-essential pkg-config \
        libsecp256k1-dev postgresql-client restic \
        gosu
    
    # Install Python packages
    echo ""
    echo "🐍 Installing Python packages..."
    pip install --no-cache-dir -r requirements.txt
    
    # Mark as installed
    touch /tmp/.packages_installed
    echo ""
    echo "✅ Dependencies installed"
else
    echo "✅ Dependencies already installed (skipping)"
fi

# Function to wait for PostgreSQL
wait_for_postgres() {
    echo "⏳ Waiting for PostgreSQL to be ready..."
    max_attempts=30
    attempt=0
    
    until PGPASSWORD=$POSTGRES_PASSWORD psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\q' 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "❌ PostgreSQL did not become ready in time"
            exit 1
        fi
        echo "   Attempt $attempt/$max_attempts - waiting 2 seconds..."
        sleep 2
    done
    
    echo "✅ PostgreSQL is ready!"
}

# Step 2: Initialize config files from templates (idempotent)
echo ""
echo "=========================================="
echo "Step 2/5: Initialize Config Files"
echo "=========================================="
python scripts/init_configs.py
# Continue even if config init fails (dev environments may not need all configs)

# Step 2: Wait for database
wait_for_postgres

# Check if initialization should be skipped (to prevent race conditions)
if [ "${SKIP_INIT}" = "true" ]; then
    echo ""
    echo "=========================================="
    echo "⏭️  SKIP_INIT=true - Skipping Initialization"
    echo "=========================================="
    echo "Database initialization steps (3-6) will be skipped."
    echo "These steps run in the worker container to prevent race conditions."
    echo ""
else
    # Step 3: Initialize database schema (idempotent)
    echo ""
    echo "=========================================="
    echo "Step 3/6: Initialize Database Schema"
    echo "=========================================="
    python init_db.py
    if [ $? -ne 0 ]; then
        echo "❌ Database initialization failed"
        exit 1
    fi

    # Step 4: Seed configuration data (idempotent)
    echo ""
    echo "=========================================="
    echo "Step 4/6: Seed Configuration Data"
    echo "=========================================="
    python seed_db.py
    # Note: seed_db.py exits with 0 even on failure (non-critical)
    # We continue even if seeding fails (config files might not exist in dev)

    # Step 5: Backfill benchmark prices (for portfolio comparison charts)
    echo ""
    echo "=========================================="
    echo "Step 5/6: Backfill Benchmark Prices"
    echo "=========================================="
    echo "📊 Fetching historical benchmark prices from first portfolio snapshot..."
    python scripts/backfill_benchmark_prices.py
    # Non-critical: continue even if benchmark backfill fails
    echo "✅ Benchmark price backfill completed"

    # Step 6: Precompute dashboard cache (for instant loading)
    echo ""
    echo "=========================================="
    echo "Step 6/8: Precompute Dashboard Cache"
    echo "=========================================="
    echo "🚀 Pre-computing portfolio summaries, statistics, and charts..."
    python -c "from service.precomputation_service import precompute_all_portfolios; result = precompute_all_portfolios(force=True); print(f'✅ Cache precomputation complete: {result[\"portfolios_processed\"]} portfolios, {result[\"charts_cached\"]} charts cached')" || echo "⚠️  Cache precomputation failed - dashboard will compute on demand"
fi

# Step 7: Fix log file ownership (ensure app user can write)
echo ""
echo "=========================================="
echo "Step 7/8: Fix Log File Ownership"
echo "=========================================="

echo "📝 Setting ownership of logs directory to $APP_USER:$APP_USER..."

# Create logs directory if it doesn't exist
mkdir -p /app/logs

# Change ownership of logs directory and all files within
chown -R $APP_USER:$APP_USER /app/logs

# Set permissions to allow app user to write
chmod -R 755 /app/logs

# Create .nicegui directory for user storage and fix permissions
mkdir -p /app/.nicegui
chown -R $APP_USER:$APP_USER /app/.nicegui
chmod -R 755 /app/.nicegui

echo "✅ Log file and NiceGUI storage ownership fixed"

# Step 8: Start the application as non-root user
echo ""
echo "=========================================="
echo "Step 8/8: Starting Application as $APP_USER"
echo "=========================================="
echo "Command: $@"
echo "=========================================="
echo ""

# Execute the main command as app user (not root)
# gosu is already installed in Step 1
exec gosu $APP_USER "$@"
