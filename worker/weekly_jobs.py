import time
import subprocess
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, List

from service.portfolio_service import reconcile_all_lots
from utils.logging_config import get_logger
from utils.app_config import load_app_config
from utils.transaction_price_enrichment import populate_transaction_price
from utils.notifications import send_backup_report

logger = get_logger("weekly_jobs")

# =============================================================================
# WEEKLY JOBS
# =============================================================================
# This file contains jobs that run weekly (typically on Sundays).
# These jobs are computationally intensive and don't need to run daily.
#
# CURRENT JOBS (Sequential execution with sleep intervals):
#   1. Lot Recreation (Sunday night)
#      - Deletes and rebuilds ALL lots and allocations from scratch
#      - Uses FIFO method for cost basis
#      - MUST run first (deletes everything)
#
#   2. Rolling Window Snapshot Recreation
#      - Recreates last 30 days of snapshots
#      - Uses lots from step 1
#      - Handles late transactions, price corrections
#
#   3. Position Recreation
#      - Deletes and rebuilds positions from transactions
#      - Uses lot allocations from step 1
#      - Provides self-correction for dividend reinvestments
#
# CRITICAL: Execution order matters! Lot recreation must run before
# rolling window and position recreation because they depend on lots.
# =============================================================================

def reconcile_lots():
    """
    Recreate all lots from transactions using FIFO.
    
    NOTE: This is NOT a reconciliation - it's a COMPLETE REBUILD!
    
    Process:
    1. Delete ALL lot allocations (FK constraints)
    2. Delete ALL lots
    3. Rebuild lots from buy transactions
    4. Allocate sales using FIFO method
    
    This is computationally intensive and runs weekly because:
    - Daily recreation would be inefficient
    - Cost basis doesn't change significantly day-to-day
    - Weekly ensures accuracy for the new week
    
    MUST run before rolling window and position recreation!
    """
    logger.info("Starting lot recreation job...")
    try:
        results = reconcile_all_lots()
        logger.info(
            f"Lot recreation completed: "
            f"{results.get('lots_created', 0)} lots created, "
            f"{results.get('sales_allocated', 0)} sales allocated"
        )
    except Exception as e:
        logger.error(f"Error in lot recreation: {e}", exc_info=True)


def recreate_rolling_window():
    """
    Recreate snapshots for the rolling window period.
    
    Recreates last N days of snapshots to handle:
    - Late transactions
    - Price corrections
    - New symbols added
    
    Uses lot-based calculations (requires fresh lots from step 1).
    Runs weekly after lot recreation.
    """
    logger.info("Starting rolling window snapshot recreation...")
    try:
        from worker.daily_jobs import recreate_snapshots_rolling_window
        recreate_snapshots_rolling_window()
        logger.info("Rolling window recreation completed")
    except Exception as e:
        logger.error(f"Error in rolling window recreation: {e}", exc_info=True)


def recreate_positions():
    """
    Recreate positions from transactions for self-correction.
    
    Process:
    1. Delete ALL positions for configured portfolios
    2. Rebuild positions by replaying transactions chronologically
    3. Use lot allocations for accurate cost basis (requires fresh lots)
    
    Provides self-correction for:
    - Dividend reinvestments
    - Position cost basis accuracy
    - Any transaction processing issues
    
    IMPORTANT: Must run AFTER lot recreation because position cost basis
    calculation uses lot allocations.
    
    Dynamically loads portfolios from portfolio_config.yaml:
    - Only processes portfolios with status=active AND update_method=automatic
    - No hardcoded portfolio IDs - fully config-driven
    """
    logger.info("Starting position recreation job...")
    try:
        from database import SessionLocal
        from models import Portfolio, Position
        from crud.crud_position import recreate_positions_from_transactions
        from sqlalchemy import func
        import yaml
        from pathlib import Path
        
        # Check if position recreation is enabled
        config = load_app_config()
        if not config['scheduler'].get('position_recreation_enabled', True):
            logger.info("Position recreation is disabled in config")
            return
        
        # Load portfolio config to filter by update_method
        config_path = Path(__file__).parent.parent / 'config' / 'portfolio_config.yaml'
        with open(config_path, 'r') as f:
            portfolio_configs = yaml.safe_load(f)
        
        # Get automatic portfolio IDs with active status
        automatic_portfolio_ids = {
            p['id'] for p in portfolio_configs 
            if p.get('status') == 'active' and p.get('update_method') == 'automatic'
        }
        
        if not automatic_portfolio_ids:
            logger.warning("No automatic portfolios found in portfolio_config.yaml")
            return
        
        logger.info(f"Found {len(automatic_portfolio_ids)} automatic portfolios: {sorted(automatic_portfolio_ids)}")
        
        db = SessionLocal()
        try:
            # Get portfolios that have positions
            portfolios_with_positions = db.query(
                Portfolio.id,
                Portfolio.name,
                func.count(Position.id).label('position_count')
            ).outerjoin(Position, Portfolio.id == Position.portfolio_id)\
             .filter(Portfolio.id.in_(automatic_portfolio_ids))\
             .group_by(Portfolio.id, Portfolio.name)\
             .having(func.count(Position.id) > 0)\
             .order_by(Portfolio.id)\
             .all()
            
            if not portfolios_with_positions:
                logger.info("No automatic portfolios have positions - skipping position recreation")
                return
            
            logger.info(f"Recreating positions for {len(portfolios_with_positions)} portfolios with positions")
            
            total_positions = 0
            total_transactions = 0
            
            for p_id, p_name, p_count in portfolios_with_positions:
                logger.info(f"Recreating positions for portfolio {p_id} ({p_name}): {p_count} existing positions")
                
                result = recreate_positions_from_transactions(db, p_id, commit=True)
                
                total_positions += result['positions_created']
                total_transactions += result['transactions_processed']
                
                logger.info(
                    f"Portfolio {p_id} ({p_name}): "
                    f"{result['positions_created']} positions from "
                    f"{result['transactions_processed']} transactions"
                )
            
            logger.info(
                f"Position recreation completed: "
                f"{total_positions} total positions from "
                f"{total_transactions} total transactions across "
                f"{len(portfolios_with_positions)} portfolios"
            )
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Error in position recreation: {e}", exc_info=True)


# =============================================================================
# BNB STAKING REWARD TRACKING (German Tax Compliance)
# =============================================================================
# For BNB Chain Fusion liquid staking, rewards accrue continuously.
# German tax (§ 22 Nr. 3 EStG) requires tracking staking income when received.
#
# This job:
# 1. Queries current staked value from blockchain (principal + pending rewards)
# 2. Calculates total rewards ever earned (realized from claims + pending)
# 3. Compares to already recorded staking_reward transactions
# 4. Creates new staking_reward or staking_loss transaction for the difference
#
# Formula:
#   total_rewards_ever = (sum(claims) - sum(undelegates)) + (current_staked - remaining_principal)
#   new_reward = total_rewards_ever - sum(existing_staking_reward_transactions)
# =============================================================================

def calculate_bnb_staking_rewards_for_wallet(
    db,
    wallet_id: int,
    wallet_address: str,
    account_id: int,
    portfolio_id: int,
    base_currency: str
) -> Dict:
    """
    Calculate BNB staking rewards for a single wallet.
    
    Returns:
        dict with calculation results and any transaction created
    """
    from models import CryptoStakingTransaction, Transaction
    from crud.crud_base import create_transaction_idempotent
    from service.blockchain_providers.bsc_provider import get_current_staking_value
    
    result = {
        'wallet_id': wallet_id,
        'success': False,
        'total_delegated': Decimal('0'),
        'total_undelegated': Decimal('0'),
        'total_claimed': Decimal('0'),
        'realized_rewards': Decimal('0'),
        'remaining_principal': Decimal('0'),
        'current_staked_value': Decimal('0'),
        'pending_rewards': Decimal('0'),
        'total_rewards_ever': Decimal('0'),
        'already_recorded': Decimal('0'),
        'new_reward': Decimal('0'),
        'transaction_created': None,
        'error': None
    }
    
    try:
        # 1. Get staking transactions from CryptoStakingTransaction table
        staking_txs = db.query(CryptoStakingTransaction).filter(
            CryptoStakingTransaction.wallet_id == wallet_id,
            CryptoStakingTransaction.symbol == 'BNB'
        ).all()
        
        if not staking_txs:
            result['error'] = "No staking transactions found"
            return result
        
        # Get first delegate tx for credit contract fallback (if StakeHub query fails)
        delegate_tx_hash = None
        validator_address = None
        for tx in staking_txs:
            if tx.tx_type == 'delegate' and tx.tx_hash:
                delegate_tx_hash = tx.tx_hash
                validator_address = tx.validator_address
                break
        
        # Calculate totals from staking transactions
        # Note: CryptoStakingTransaction uses 'amount', not 'value_native'
        for tx in staking_txs:
            if tx.amount is None:
                continue
            amount = Decimal(str(tx.amount))
            if tx.tx_type == 'delegate':
                result['total_delegated'] += amount
            elif tx.tx_type == 'undelegate':
                result['total_undelegated'] += amount
            elif tx.tx_type == 'claim':
                result['total_claimed'] += amount
        
        # 2. Calculate realized rewards (from claims)
        result['realized_rewards'] = result['total_claimed'] - result['total_undelegated']
        
        # 3. Calculate remaining principal
        result['remaining_principal'] = result['total_delegated'] - result['total_undelegated']
        
        # If no remaining principal, no pending rewards possible
        if result['remaining_principal'] <= 0:
            result['pending_rewards'] = Decimal('0')
            result['current_staked_value'] = Decimal('0')
        else:
            # 4. Query current staked value from blockchain
            # Pass validator_address and delegate_tx_hash for credit contract discovery
            staking_info = get_current_staking_value(
                wallet_address,
                validator_operator=validator_address,
                delegate_tx_hash=delegate_tx_hash
            )
            
            if not staking_info.get('success'):
                result['error'] = f"Failed to query blockchain: {staking_info.get('error')}"
                return result
            
            result['current_staked_value'] = Decimal(str(staking_info['current_staked_value']))
            
            # 5. Calculate pending rewards
            result['pending_rewards'] = result['current_staked_value'] - result['remaining_principal']
        
        # 6. Total rewards ever earned
        result['total_rewards_ever'] = result['realized_rewards'] + result['pending_rewards']
        
        # 7. Get already recorded staking_reward/staking_loss transactions
        recorded_rewards = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.symbol == 'BNB',
            Transaction.type.in_(['staking_reward', 'staking_loss']),
            Transaction.source == 'weekly_staking_tracker'
        ).all()
        
        for tx in recorded_rewards:
            if tx.quantity:
                result['already_recorded'] += Decimal(str(tx.quantity))
        
        # 8. Calculate new reward to record
        result['new_reward'] = result['total_rewards_ever'] - result['already_recorded']
        
        # 9. Create transaction if there's a meaningful difference
        # Use 0.00000001 as minimum threshold (8 decimal places)
        min_threshold = Decimal('0.00000001')
        
        if abs(result['new_reward']) >= min_threshold:
            now = datetime.now(timezone.utc)
            
            if result['new_reward'] > 0:
                # Create staking_reward transaction
                tx_type = 'staking_reward'
                tx_qty = float(result['new_reward'])
                tx_notes = (
                    f"Weekly staking reward: {float(result['new_reward']):.8f} BNB. "
                    f"Total ever: {float(result['total_rewards_ever']):.8f}, "
                    f"Previously recorded: {float(result['already_recorded']):.8f}"
                )
            else:
                # Create staking_loss transaction (slashing)
                tx_type = 'staking_loss'
                tx_qty = float(result['new_reward'])  # Negative value
                tx_notes = (
                    f"Weekly staking loss (slashing): {float(result['new_reward']):.8f} BNB. "
                    f"Total ever: {float(result['total_rewards_ever']):.8f}, "
                    f"Previously recorded: {float(result['already_recorded']):.8f}"
                )
            
            # Generate unique external_id for idempotency
            week_str = now.strftime('%Y-W%W')
            external_id = f"bnb_staking_{wallet_id}_{week_str}"
            
            tx_data = {
                'type': tx_type,
                'portfolio_id': portfolio_id,
                'account_id': account_id,
                'symbol': 'BNB',
                'symbol_normalized': 'BNB',
                'quantity': tx_qty,
                'value_native': None,  # Will be enriched with price
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'occurred_at': now,
                'source': 'weekly_staking_tracker',
                'external_id': external_id,
                'asset_class': 'crypto',
                'currency_base': base_currency,
                'notes': tx_notes,
                'from_crypto_wallet_id': wallet_id
            }
            
            created_tx = create_transaction_idempotent(db, tx_data)
            if created_tx:
                # Enrich transaction with price data
                # Uses USD as native currency, then converts to base currency
                price_success = populate_transaction_price(db, created_tx, base_currency=base_currency)
                if price_success:
                    db.commit()  # Commit price enrichment
                    logger.info(
                        f"Created {tx_type} transaction for wallet {wallet_id}: "
                        f"{tx_qty:.8f} BNB @ ${created_tx.price:.2f} = ${created_tx.value_native:.2f}"
                    )
                else:
                    logger.warning(
                        f"Created {tx_type} transaction for wallet {wallet_id}: "
                        f"{tx_qty:.8f} BNB (price enrichment failed - will retry later)"
                    )
                
                result['transaction_created'] = {
                    'id': created_tx.id,
                    'type': tx_type,
                    'quantity': tx_qty,
                    'price_enriched': price_success
                }
        else:
            logger.info(
                f"Wallet {wallet_id}: No significant change to record "
                f"(delta: {float(result['new_reward']):.10f} BNB)"
            )
        
        result['success'] = True
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error calculating staking rewards for wallet {wallet_id}: {e}", exc_info=True)
        return result


def track_bnb_staking_rewards():
    """
    Track BNB staking rewards for all wallets with staking transactions.
    
    This job:
    1. Finds all BNB wallets with staking transactions
    2. For each wallet, calculates total rewards (realized + pending)
    3. Creates staking_reward or staking_loss transactions for any delta
    
    German tax compliance: Creates weekly record of staking income.
    """
    logger.info("Starting BNB staking reward tracking...")
    
    from database import SessionLocal
    from models import CryptoWallet, CryptoStakingTransaction, Account, Transaction, Portfolio
    from sqlalchemy import distinct
    
    db = SessionLocal()
    
    try:
        # Find all BNB wallets with staking transactions
        wallets_with_staking = db.query(CryptoWallet).join(
            CryptoStakingTransaction,
            CryptoStakingTransaction.wallet_id == CryptoWallet.id
        ).filter(
            CryptoStakingTransaction.symbol == 'BNB'
        ).distinct().all()
        
        if not wallets_with_staking:
            logger.info("No BNB wallets with staking transactions found")
            return
        
        logger.info(f"Found {len(wallets_with_staking)} BNB wallet(s) with staking transactions")
        
        total_rewards_created = 0
        total_losses_created = 0
        
        for wallet in wallets_with_staking:
            # Get account info
            account = db.query(Account).filter_by(id=wallet.account_id).first()
            if not account:
                logger.warning(f"Wallet {wallet.id} has no associated account")
                continue
            
            # Get portfolio_id from existing transactions for this account
            sample_tx = db.query(Transaction).filter(
                Transaction.account_id == wallet.account_id,
                Transaction.symbol == 'BNB',
                Transaction.portfolio_id.isnot(None)
            ).first()
            
            if not sample_tx:
                logger.warning(f"Cannot determine portfolio for wallet {wallet.id}")
                continue
            
            portfolio_id = sample_tx.portfolio_id
            
            # Get base currency from portfolio - MUST exist, no fallbacks
            portfolio = db.query(Portfolio).filter_by(id=portfolio_id).first()
            if not portfolio:
                raise ValueError(
                    f"Portfolio {portfolio_id} not found for wallet {wallet.id}. "
                    f"Cannot process staking without valid portfolio."
                )
            if not portfolio.currency_base:
                raise ValueError(
                    f"Portfolio {portfolio_id} has no currency_base set. "
                    f"Cannot process staking without base currency."
                )
            currency_base = portfolio.currency_base
            
            logger.info(
                f"Processing wallet {wallet.id} "
                f"(Account: {account.name}, Portfolio: {portfolio_id})"
            )
            
            # Calculate and create transaction
            result = calculate_bnb_staking_rewards_for_wallet(
                db=db,
                wallet_id=wallet.id,
                wallet_address=wallet.address,
                account_id=wallet.account_id,
                portfolio_id=portfolio_id,
                base_currency=currency_base  # Still called base_currency in function signature
            )
            
            if result['success']:
                if result['transaction_created']:
                    if result['transaction_created']['type'] == 'staking_reward':
                        total_rewards_created += 1
                    else:
                        total_losses_created += 1
                
                logger.info(
                    f"Wallet {wallet.id} summary: "
                    f"Delegated={float(result['total_delegated']):.4f}, "
                    f"Principal={float(result['remaining_principal']):.4f}, "
                    f"Current={float(result['current_staked_value']):.4f}, "
                    f"TotalRewards={float(result['total_rewards_ever']):.6f}, "
                    f"NewReward={float(result['new_reward']):.6f}"
                )
            else:
                logger.warning(f"Wallet {wallet.id} failed: {result['error']}")
        
        logger.info(
            f"BNB staking reward tracking completed: "
            f"{total_rewards_created} rewards, {total_losses_created} losses created"
        )
        
    except Exception as e:
        logger.error(f"Error in BNB staking reward tracking: {e}", exc_info=True)
    finally:
        db.close()


def run_position_audit():
    """
    Run position audit to compare lot-based calculations against external sources.
    
    TAX COMPLIANCE FEATURE:
    - Compares IBKR positions against calculated positions from lots
    - Compares crypto wallet balances against blockchain state
    - Sends notification if discrepancies found
    
    This runs AFTER all reconciliation jobs to verify data integrity.
    """
    logger.info("Starting position audit job...")
    try:
        from service.audit_service import run_position_audit_job
        run_position_audit_job()
        logger.info("Position audit completed")
    except Exception as e:
        logger.error(f"Error in position audit: {e}", exc_info=True)


def check_backup_integrity():
    """
    Run restic check to verify backup integrity.
    Sends email report with results.
    """
    logger.info("Starting backup integrity check...")
    
    # Check if restic is installed
    try:
        subprocess.run(["restic", "version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("Restic not installed or not found. Skipping integrity check.")
        return

    # Check if credentials file exists
    if not subprocess.run(["test", "-f", "/app/.restic_env"]).returncode == 0:
        logger.warning("Restic credentials file not found at /app/.restic_env. Skipping integrity check.")
        return

    try:
        # Run restic check
        # We use bash to source the env file first
        cmd = "source /app/.restic_env && restic check"
        
        logger.info("Running 'restic check'...")
        result = subprocess.run(
            ['bash', '-c', cmd],
            capture_output=True,
            text=True
        )
        
        output = result.stdout
        if result.stderr:
            output += "\n\nSTDERR:\n" + result.stderr
            
        success = result.returncode == 0
        
        if success:
            logger.info("Backup integrity check PASSED")
        else:
            logger.error("Backup integrity check FAILED")
            logger.error(output)
            
        # Send report
        send_backup_report(output, success)
        
    except Exception as e:
        logger.error(f"Error in backup integrity check: {e}", exc_info=True)
        send_backup_report(f"Exception running check: {e}", False)


def run_weekly_jobs():
    """
    Main orchestrator for all weekly jobs.
    
    CRITICAL EXECUTION ORDER:
    1. BNB Staking Reward Tracking (creates staking_reward transactions - MUST RUN FIRST!)
    2. Sleep (allow system to stabilize)
    3. Lot Recreation (deletes & rebuilds ALL lots, includes new staking transactions)
    4. Sleep (allow system to stabilize)
    5. Rolling Window Snapshots (uses lots from step 3, includes staking rewards)
    6. Sleep (allow system to stabilize)
    7. Position Recreation (uses lot allocations from step 3, reflects staking rewards)
    8. Sleep (allow system to stabilize)
    9. Position Audit (compares calculated vs actual positions - TAX COMPLIANCE)
    10. Sleep (allow system to stabilize)
    11. Backup Integrity Check (verifies Restic repository health)
    
    WHY THIS ORDER MATTERS:
    - BNB staking creates new transactions that must be included in lots/snapshots/positions
    - If staking runs AFTER lot recreation, new rewards won't appear until next week
    - Lots must exist before snapshots (snapshots use lot-based calculations)
    - Positions must use lot allocations (for accurate cost basis)
    - Position audit runs LAST to verify final data integrity against external sources
    
    Sleep intervals configured in app_config.yaml (default: 5 minutes).
    """
    logger.info("=" * 80)
    logger.info("STARTING WEEKLY JOBS")
    logger.info("=" * 80)
    
    # Load config for sleep interval
    config = load_app_config()
    sleep_seconds = config['scheduler'].get('sleep_between_jobs', 300)  # Default 5 minutes
    sleep_minutes = sleep_seconds / 60
    
    try:
        # Step 1: BNB Staking Reward Tracking (MUST run first - creates transactions)
        logger.info("Weekly Job 1/5: BNB Staking Reward Tracking")
        logger.info("-" * 80)
        logger.info("Creating staking_reward transactions for German tax compliance...")
        track_bnb_staking_rewards()
        logger.info(f"BNB staking tracking completed. Sleeping {sleep_minutes:.1f} minutes...")
        time.sleep(sleep_seconds)
        
        # Step 2: Lot Recreation (includes new staking transactions from step 1)
        logger.info("Weekly Job 2/5: Lot Recreation")
        logger.info("-" * 80)
        logger.info("Rebuilding ALL lots from transactions (includes new staking rewards)...")
        reconcile_lots()
        logger.info(f"Lot recreation completed. Sleeping {sleep_minutes:.1f} minutes...")
        time.sleep(sleep_seconds)
        
        # Step 3: Rolling Window Snapshot Recreation (uses lots from step 2)
        logger.info("Weekly Job 3/5: Rolling Window Snapshots (30 days)")
        logger.info("-" * 80)
        logger.info("Recreating snapshots with staking rewards included...")
        recreate_rolling_window()
        logger.info(f"Rolling window completed. Sleeping {sleep_minutes:.1f} minutes...")
        time.sleep(sleep_seconds)
        
        # Step 4: Position Recreation (uses lot allocations from step 2)
        logger.info("Weekly Job 4/5: Position Recreation")
        logger.info("-" * 80)
        logger.info("Rebuilding positions with staking rewards reflected...")
        recreate_positions()
        logger.info(f"Position recreation completed. Sleeping {sleep_minutes:.1f} minutes...")
        time.sleep(sleep_seconds)
        
        # Step 5: Position Audit (TAX COMPLIANCE - verify calculated vs actual)
        logger.info("Weekly Job 5/6: Position Audit (Tax Compliance)")
        logger.info("-" * 80)
        logger.info("Comparing calculated positions against external sources...")
        run_position_audit()
        logger.info(f"Position audit completed. Sleeping {sleep_minutes:.1f} minutes...")
        time.sleep(sleep_seconds)

        # Step 6: Backup Integrity Check
        logger.info("Weekly Job 6/6: Backup Integrity Check")
        logger.info("-" * 80)
        logger.info("Verifying Restic backup integrity...")
        check_backup_integrity()
        logger.info("Backup integrity check completed.")
        
        logger.info("=" * 80)
        logger.info("WEEKLY JOBS COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Error during weekly jobs: {e}", exc_info=True)
        logger.info("Weekly jobs failed - will retry next week")

