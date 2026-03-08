from service.marketdata_service import sync_crypto_prices, sync_securities_prices
from service.goldtradersth_service import sync_gold_price
from service.fx_service import sync_fx_rates
from service.benchmark_service import sync_benchmark_prices
from service.ibkr_service import run_ibkr_ingestion_flow
from service.binanceth_balance_sync_service import sync_binanceth_with_trade_inference
from service.binancecom_service import run_binancecom_ingestion_flow
from service.crypto_wallet_service import (
    sync_all_wallets,
    sync_all_wallets_with_balance_tracking,
    detect_internal_transfers_for_account,
    detect_internal_transfers_global,
    detect_exchange_wallet_transfers
)
from service.portfolio_service import (
    create_all_snapshots
)
from database import SessionLocal
from models import Account
from utils.logging_config import get_logger
from utils.app_config import load_app_config
from datetime import date, timedelta

logger = get_logger("daily_jobs")


def is_account_active(account_id: int) -> bool:
    """
    Check if an account is active (not closed).
    
    Args:
        account_id: Account ID to check
    
    Returns:
        bool: True if account is active, False if closed or not found
    """
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            logger.warning(f"Account ID {account_id} not found in database")
            return False
        
        if account.status == 'closed':
            logger.info(f"Account '{account.name}' (ID: {account_id}) is closed, skipping sync")
            return False
        
        return True
    finally:
        db.close()

# =============================================================================
# PORTFOLIO COVERAGE SUMMARY
# =============================================================================
# This file contains daily sync jobs for portfolios that support automatic updates.
# Portfolio-to-account mappings are defined in config/portfolio_config.yaml
# and config/source_mapping.yaml. See those files for the current setup.
#
# Supported sync types:
#   - Broker transactions (IBKR Flex Queries)
#   - Exchange transactions (Binance.com, Binance Thailand)
#   - Crypto wallet balances (6 blockchain providers)
#   - Market data (crypto prices, securities prices, FX rates, gold)
#
# Manual-only portfolios (cash, physical assets) are managed via the Web UI.
# =============================================================================

def sync_ibkr_transactions():
    """Syncs IBKR transactions."""
    # Check if IBKR account is active (Account ID: 2)
    if not is_account_active(2):
        return {'new': 0, 'types': {}, 'failed': 0}
    
    logger.info("Starting IBKR transaction sync...")
    try:
        stats = run_ibkr_ingestion_flow()
        logger.info("IBKR transaction sync completed.")
        return stats
    except Exception as e:
        logger.error(f"Error syncing IBKR transactions: {e}", exc_info=True)
        return {'new': 0, 'types': {}, 'failed': 0}

def sync_binanceth_transactions():
    """
    Syncs Binance.th transactions with automated trade inference.
    
    This approach:
    1. Syncs crypto deposits/withdrawals from API
    2. Fetches current balances from API
    3. Compares with previous balances
    4. Infers trades from balance changes
    5. Creates transaction records automatically
    
    Configuration loaded from config/source_mapping.yaml:
    - Account: BinanceTH (account_id and account_name)
    - Portfolio: Default portfolio for BinanceTH transactions
    """
    # Check if Binance.th account is active (Account ID: 3)
    if not is_account_active(3):
        return {'new': 0, 'types': {}, 'failed': 0}
    
    logger.info("Starting Binance.th sync with trade inference...")
    try:
        # Get lookback period from config (default 7 days)
        config = load_app_config()
        days_back = config.get('binanceth', {}).get('sync_lookback_days', 7)
        
        logger.info(f"Syncing last {days_back} days of Binance.th transactions")
        result = sync_binanceth_with_trade_inference(days_back=days_back)
        logger.info(
            f"Binance.th sync completed. "
            f"Deposits: {result.get('deposits_synced', 0)}, "
            f"Withdrawals: {result.get('withdrawals_synced', 0)}, "
            f"Trades inferred: {result.get('trades_inferred', 0)}, "
            f"HODL crypto: {result.get('crypto_balances', 0)}, "
            f"Cash/stablecoins: {result.get('cash_balances', 0)}, "
            f"Skipped: {result.get('skipped_assets', 0)}"
        )
        
        # Convert to notification format
        total_new = result.get('deposits_synced', 0) + result.get('withdrawals_synced', 0) + result.get('trades_inferred', 0)
        types = {}
        if result.get('deposits_synced', 0) > 0:
            types['deposit'] = result.get('deposits_synced', 0)
        if result.get('withdrawals_synced', 0) > 0:
            types['withdrawal'] = result.get('withdrawals_synced', 0)
        if result.get('trades_inferred', 0) > 0:
            types['trade'] = result.get('trades_inferred', 0)
        
        return {'new': total_new, 'types': types, 'failed': 0}
    except Exception as e:
        logger.error(f"Error syncing Binance.th transactions: {e}", exc_info=True)
        return {'new': 0, 'types': {}, 'failed': 0}

def sync_binancecom_transactions():
    """Syncs Binance.com transactions using configured lookback period."""
    # Check if Binance.com account is active (Account ID: 4)
    if not is_account_active(4):
        return {'new': 0, 'types': {}, 'failed': 0}
    
    logger.info("Starting Binance.com transaction sync...")
    try:
        # Get lookback period from config (default 90 days)
        config = load_app_config()
        days_back = config.get('binancecom', {}).get('sync_lookback_days', 90)
        
        today = date.today()
        start_date = today - timedelta(days=days_back)
        
        logger.info(f"Syncing last {days_back} days of Binance.com transactions")
        stats = run_binancecom_ingestion_flow(from_date=start_date, to_date=today)
        logger.info("Binance.com transaction sync completed.")
        return stats
    except Exception as e:
        logger.error(f"Error syncing Binance.com transactions: {e}", exc_info=True)
        return {'new': 0, 'types': {}, 'failed': 0}

def sync_crypto_wallets():
    """
    Syncs crypto wallet transactions.
    Uses lookback period from app_config.yaml (default: 7 days).
    """
    logger.info("Starting crypto wallet transaction sync...")
    try:
        # Uses DEFAULT_WALLET_LOOKBACK_DAYS from app_config.yaml
        sync_all_wallets(sync_transactions=True)
        logger.info("Crypto wallet transaction sync completed.")
    except Exception as e:
        logger.error(f"Error syncing crypto wallet transactions: {e}", exc_info=True)


def sync_crypto_wallets_with_balance():
    """
    Syncs crypto wallet transactions AND records balance snapshots.
    This is the enhanced version that maintains balance history.
    
    Uses lookback period from app_config.yaml (default: 7 days).
    For daily incremental sync, the config typically sets this to 1 day.
    """
    logger.info("Starting crypto wallet sync with balance tracking...")
    try:
        # Get lookback period from config (default 7 days)
        config = load_app_config()
        days_back = config.get('crypto_wallets', {}).get('sync_lookback_days', 7)
        
        logger.info(f"Syncing last {days_back} days of crypto wallet transactions")
        result = sync_all_wallets_with_balance_tracking(days_back=days_back)
        logger.info(
            f"Crypto wallet sync with balance completed. "
            f"Successful: {result.get('successful', 0)}, Failed: {result.get('failed', 0)}"
        )
        
        # sync_all_wallets_with_balance_tracking returns:
        # {'total_wallets': N, 'successful': N, 'failed': N, 'details': [...]  }
        # It doesn't track transaction counts, only wallet sync success
        # Report wallets synced for notification purposes
        total_wallets_synced = result.get('successful', 0)
        
        return {
            'new': 0,  # Can't determine from current implementation
            'types': {},
            'failed': result.get('failed', 0),
            'wallets_synced': total_wallets_synced
        }
    except Exception as e:
        logger.error(f"Error syncing crypto wallets with balance tracking: {e}", exc_info=True)
        return {'new': 0, 'types': {}, 'failed': 0, 'wallets_synced': 0}


def detect_internal_transfers():
    """
    Auto-detect internal transfers between ALL crypto wallets and exchanges.
    
    Two-pass detection:
    1. Wallet ↔ Wallet transfers (cross-account): Wallet A ↔ Wallet B, etc.
    2. Exchange ↔ Wallet transfers: Exchange ↔ Wallet (most common pattern!)
    
    This replaces the old per-account detection which missed most real transfers.
    """
    logger.info("Starting comprehensive internal transfer detection...")
    try:
        total_links = 0
        
        # Pass 1: Wallet-to-wallet transfers (cross-account)
        logger.info("Pass 1: Detecting wallet ↔ wallet transfers...")
        result1 = detect_internal_transfers_global(lookback_days=7)
        
        if result1.get('success'):
            links1 = result1.get('links_created', 0)
            total_links += links1
            logger.info(f"Pass 1 complete: {links1} wallet ↔ wallet links created")
        else:
            logger.error(f"Pass 1 failed: {result1.get('error')}")
        
        # Pass 2: Exchange-to-wallet transfers (Binance, IBKR, etc.)
        logger.info("Pass 2: Detecting exchange ↔ wallet transfers...")
        result2 = detect_exchange_wallet_transfers(lookback_days=7)
        
        if result2.get('success'):
            links2 = result2.get('links_created', 0)
            total_links += links2
            logger.info(f"Pass 2 complete: {links2} exchange ↔ wallet links created")
        else:
            logger.error(f"Pass 2 failed: {result2.get('error')}")
        
        logger.info(f"Internal transfer detection completed. Total links created: {total_links}")
        logger.info(f"  - Wallet ↔ Wallet: {result1.get('links_created', 0)}")
        logger.info(f"  - Exchange ↔ Wallet: {result2.get('links_created', 0)}")
            
    except Exception as e:
        logger.error(f"Error in internal transfer detection: {e}", exc_info=True)

def run_daily_jobs():
    logger.info("Starting daily jobs (FULL MODE - All Syncing Active except binanceCOM)...")

    # =========================================================================
    # TRANSACTION SYNCING: All sources active
    # =========================================================================
    
    # Sync all sources first
    sync_ibkr_transactions()
    sync_binanceth_transactions()
    # sync_binancecom_transactions()
    sync_crypto_wallets_with_balance()
    
    # Auto-detect internal transfers (NEW FEATURE)
    # Run after wallet sync to link newly imported transfers
    detect_internal_transfers()

    # =========================================================================
    # MARKET DATA SYNC: Active
    # =========================================================================

    # FX rates
    logger.info("Syncing FX rates...")
    sync_fx_rates()
    logger.info("FX rates updated.")

    # Crypto prices
    logger.info("Syncing crypto prices...")
    sync_crypto_prices()
    logger.info("Crypto prices updated.")

    # Securities prices
    logger.info("Syncing securities prices...")
    sync_securities_prices()
    logger.info("Securities prices updated.")

    # Gold price
    try:
        sync_gold_price()
        logger.info("Gold price updated.")
    except Exception as e:
        logger.error(f"Error updating gold price: {e}")

    # Benchmark prices (for portfolio comparison charts)
    # Fetches current prices for benchmarks, including those not held in portfolio
    # Upsert logic prevents duplicates if already synced via securities/crypto sync
    try:
        logger.info("Syncing benchmark prices...")
        sync_benchmark_prices()
        logger.info("Benchmark prices updated.")
    except Exception as e:
        logger.error(f"Error updating benchmark prices: {e}")

    logger.info("Daily jobs completed (Migration Mode).")


def collect_unreviewed_transactions():
    """
    Collect all unreviewed transactions for notification.
    
    This function should be called AFTER all jobs (daily + weekly) complete
    to ensure transactions created during jobs (like staking rewards) are included.
    
    Returns:
        dict: {
            'accounts_synced': {
                'Account Name': {
                    'new': count,
                    'types': {'type': count},
                    'symbols': {'symbol': count},
                    'failed': 0
                }
            },
            'total_new': total_count
        }
    """
    from database import SessionLocal
    from models import Transaction, Account
    from sqlalchemy import func
    
    db = SessionLocal()
    try:
        # Get unreviewed transaction counts grouped by account
        # IMPORTANT: No account.status filter - include ALL unreviewed transactions
        # This ensures staking rewards, manual entries, and inactive account 
        # transactions are all included in the notification
        unreviewed_by_account = db.query(
            Account.name,
            Account.id,
            func.count(Transaction.id).label('count')
        ).join(
            Transaction, Transaction.account_id == Account.id
        ).filter(
            Transaction.reviewed == False
        ).group_by(
            Account.name, Account.id
        ).all()
        
        # Get transaction type and symbol breakdown for each account
        accounts_synced = {}
        total_unreviewed = 0
        
        for account_name, account_id, count in unreviewed_by_account:
            if count > 0:
                # Get type breakdown for this account
                type_breakdown = db.query(
                    Transaction.type,
                    func.count(Transaction.id).label('count')
                ).filter(
                    Transaction.account_id == account_id,
                    Transaction.reviewed == False
                ).group_by(
                    Transaction.type
                ).all()
                
                types = {tx_type: type_count for tx_type, type_count in type_breakdown}
                
                # Get symbol breakdown for this account
                symbol_breakdown = db.query(
                    Transaction.symbol,
                    func.count(Transaction.id).label('count')
                ).filter(
                    Transaction.account_id == account_id,
                    Transaction.reviewed == False
                ).group_by(
                    Transaction.symbol
                ).all()
                
                symbols = {symbol: sym_count for symbol, sym_count in symbol_breakdown}
                
                accounts_synced[account_name] = {
                    'new': count,
                    'types': types,
                    'symbols': symbols,
                    'failed': 0
                }
                total_unreviewed += count
        
        logger.info(f"Collected {total_unreviewed} unreviewed transactions from {len(accounts_synced)} accounts")
        
        return {
            'accounts_synced': accounts_synced,
            'total_new': total_unreviewed
        }
        
    finally:
        db.close()


# NOTE: Position reconciliation deleted (obsolete)
# Positions are now updated by:
#   1. update_position_from_transaction() after each transaction (real-time)
#   2. recreate_positions_from_transactions() weekly (self-correction)
# The old daily reconciliation used proportional cost reduction which
# overwrote correct lot-based cost basis calculations.
# See DIVIDEND_REINVESTMENT_BUG_FIX_COMPLETE.md for details.


def create_snapshots():
    """
    Create daily snapshots for all portfolios.
    This captures the portfolio value at the end of the day.
    
    Automatically processes portfolios from portfolio_config.yaml where:
    - status = "active"
    - update_method = "automatic"
    
    Manual portfolios are handled by cash_manager.py with interpolation.
    """
    logger.info("Starting snapshot creation job...")
    try:
        results = create_all_snapshots()
        logger.info(
            f"Snapshot creation completed: "
            f"{results.get('snapshots_created', 0)} created, "
            f"{results.get('snapshots_failed', 0)} failed"
        )
    except Exception as e:
        logger.error(f"Error in snapshot creation: {e}", exc_info=True)


def forward_fill_manual_portfolios():
    """
    Forward-fill manual portfolios (1, 2) to fill missing snapshots.
    
    This ensures manual portfolios always have complete snapshot data:
    - Fills gaps between existing snapshots (error correction)
    - Creates today's snapshot if missing
    
    Logic:
    1. Get latest snapshot date
    2. Check for gaps from latest_date to today
    3. Fill all missing dates with forward-filled values
    
    Use case:
    - Auto-correction: If daily job failed, fills missing dates
    - Ensures charts always have continuous data
    - User enters manual data on Nov 27, but Nov 28 missing → fills Nov 28, 29, ..., today
    
    Manual portfolios:
    - Portfolio 1: Liquid Cash
    - Portfolio 2: Fixed Deposits
    """
    logger.info("Starting forward-fill for manual portfolios...")
    try:
        from database import SessionLocal
        from models import Snapshot, Portfolio
        from crud.crud_snapshot import create_snapshot
        from utils.portfolios_loader import get_portfolios_loader
        from decimal import Decimal
        
        db = SessionLocal()
        try:
            # Get manual portfolios from config (status=active, update_method=manual)
            portfolios_loader = get_portfolios_loader()
            all_portfolios = portfolios_loader.get_portfolios()
            manual_portfolio_ids = [
                p['id'] for p in all_portfolios 
                if p.get('status') == 'active' and p.get('update_method') == 'manual'
            ]
            
            if not manual_portfolio_ids:
                logger.info("No manual portfolios found in config")
                return
            
            logger.info(f"Forward-filling {len(manual_portfolio_ids)} manual portfolios: {manual_portfolio_ids}")
            
            today = date.today()
            total_filled = 0
            
            for portfolio_id in manual_portfolio_ids:
                # Get latest snapshot (any date up to today)
                latest_snapshot = db.query(Snapshot).filter(
                    Snapshot.portfolio_id == portfolio_id,
                    Snapshot.snapshot_date <= today
                ).order_by(Snapshot.snapshot_date.desc()).first()
                
                if not latest_snapshot:
                    logger.warning(f"No previous snapshot found for portfolio {portfolio_id}, skipping")
                    continue
                
                # Calculate gap from latest snapshot to today
                latest_date = latest_snapshot.snapshot_date
                gap_days = (today - latest_date).days
                
                if gap_days == 0:
                    logger.info(f"Portfolio {portfolio_id} already up-to-date (snapshot exists for today)")
                    continue
                
                logger.info(f"Portfolio {portfolio_id}: Filling {gap_days} day gap from {latest_date} to {today}")
                
                # Fill ALL missing dates from latest_date + 1 to today
                filled_count = 0
                for day_offset in range(1, gap_days + 1):
                    fill_date = latest_date + timedelta(days=day_offset)
                    
                    # Check if snapshot already exists (shouldn't, but safety check)
                    existing = db.query(Snapshot).filter(
                        Snapshot.portfolio_id == portfolio_id,
                        Snapshot.snapshot_date == fill_date
                    ).first()
                    
                    if existing:
                        logger.debug(f"  Snapshot already exists for {fill_date}, skipping")
                        continue
                    
                    # Create forward-filled snapshot
                    # For forward-fill, NAV values stay the same (no value change, no cash flow)
                    create_snapshot(
                        db=db,
                        portfolio_id=portfolio_id,
                        snapshot_date=fill_date,
                        total_value_base=latest_snapshot.total_value_base,
                        currency_base=latest_snapshot.currency_base,
                        total_invested_base=latest_snapshot.total_invested_base,
                        realized_pnl_base=latest_snapshot.realized_pnl_base or Decimal('0'),
                        unrealized_pnl_base=latest_snapshot.unrealized_pnl_base or Decimal('0'),
                        deposits_base=latest_snapshot.deposits_base or Decimal('0'),
                        withdrawals_base=latest_snapshot.withdrawals_base or Decimal('0'),
                        nav_units=latest_snapshot.nav_units,
                        nav_price=latest_snapshot.nav_price,
                        notes=f"Forward-filled from {latest_snapshot.snapshot_date} (daily job)"
                    )
                    filled_count += 1
                
                logger.info(
                    f"Portfolio {portfolio_id}: Filled {filled_count} missing snapshots "
                    f"({latest_snapshot.total_value_base} {latest_snapshot.currency_base})"
                )
                total_filled += filled_count
            
            db.commit()
            logger.info(f"Forward-fill completed: {total_filled} total snapshots created across all portfolios")
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Error in forward-fill for manual portfolios: {e}", exc_info=True)


def recreate_snapshots_rolling_window():
    """
    Recreate last 30 days of snapshots for automatic portfolios.
    
    Purpose:
    - Handle late-arriving transactions (IBKR Flex 2-day delay)
    - Fix price corrections (e.g., EUR/USD currency fixes)
    - Download missing historical prices for new symbols
    - Ensure data consistency
    
    Automatically selects portfolios from portfolio_config.yaml where:
    - status = "active"
    - update_method = "automatic"
    
    Note: Manual portfolios use daily forward-fill (see forward_fill_manual_portfolios)
    """
    logger.info("Starting rolling window snapshot recreation (30 days)...")
    try:
        from datetime import date, timedelta
        from scripts.recreate_snapshots_rolling_window import recreate_rolling_window
        from utils.portfolios_loader import get_portfolios_loader
        
        # Get automatic portfolios from config (status=active, update_method=automatic)
        portfolios_loader = get_portfolios_loader()
        all_portfolios = portfolios_loader.get_portfolios()
        automatic_portfolio_ids = [
            p['id'] for p in all_portfolios 
            if p.get('status') == 'active' and p.get('update_method') == 'automatic'
        ]
        
        logger.info(f"Found {len(automatic_portfolio_ids)} automatic portfolios: {automatic_portfolio_ids}")
        
        end_date = date.today()
        start_date = end_date - timedelta(days=30)
        
        results = recreate_rolling_window(
            start_date=start_date,
            end_date=end_date,
            portfolio_ids=automatic_portfolio_ids,
            dry_run=False
        )
        
        if 'error' in results:
            logger.error(f"Rolling window recreation failed: {results['error']}")
        else:
            logger.info(
                f"Rolling window recreation completed: "
                f"{results['successful']}/{results['total_operations']} successful"
            )
    except Exception as e:
        logger.error(f"Error in rolling window recreation: {e}", exc_info=True)


if __name__ == "__main__":
    """
    Manual test execution - run one function at a time to test fixes incrementally.
    
    Uncomment ONE function to test, run with:
        docker exec nicefolio_worker_prod python worker/daily_jobs.py
    
    Monitor logs for warnings/errors. Stop immediately if issues found.
    
    Recommended test order:
    1. sync_crypto_wallets_with_balance() - Tests incremental sync fix
    2. sync_market_prices() - Tests cash exclusion fix
    3. sync_ibkr_transactions() - Tests symbol normalization fix
    4. run_daily_data_sync() - Tests everything together
    """
    logger.info("=== MANUAL TEST EXECUTION - Single Function Mode ===")
    
    # TEST 1: Crypto Wallet Incremental Sync (98% API reduction expected)
    # sync_crypto_wallets_with_balance()
    
    # TEST 2: Market Prices (EUR warning should be gone)
    # sync_securities_prices()
    
    # TEST 3: IBKR Transactions (BRK B should normalize to BRK-B)
    # sync_ibkr_transactions()
    
    # TEST 4: Binance.th Transactions
    # sync_binanceth_transactions()
    
    # TEST 5: Binance.com Transactions
    # sync_binancecom_transactions()
    
    # TEST 6: Full Daily Sync (all transactions)
    # run_daily_data_sync()
    
    # TEST 7: Position Reconciliation
    # reconcile_positions()
    
    # TEST 8: Snapshot Creation
    # create_snapshots()
    
    logger.info("=== Manual test execution completed ===")

