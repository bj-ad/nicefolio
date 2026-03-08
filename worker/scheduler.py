import schedule
import time
import traceback
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from utils.app_config import load_app_config
from utils.logging_config import get_logger, rotate_log_file_daily
from utils.notifications import send_job_failure_alert, send_transaction_ingestion_summary
from database import SessionLocal
from sqlalchemy import text
from worker.daily_jobs import (
    run_daily_jobs,
    create_snapshots,
    forward_fill_manual_portfolios,
    collect_unreviewed_transactions
)
from worker.weekly_jobs import run_weekly_jobs
from service.precomputation_service import precompute_all_portfolios

logger = get_logger("scheduler")

# Heartbeat file for health checks
HEARTBEAT_FILE = Path("/tmp/worker_heartbeat.txt")

# Load configuration
config = load_app_config()
JOBS_START_HOUR = config["scheduler"]["start_hour"]
JOBS_START_MINUTE = config["scheduler"]["start_minute"]
SLEEP_SECONDS = config["scheduler"]["sleep_between_jobs"]
WEEKLY_JOBS_DAY = config["scheduler"].get("weekly_jobs_day", "sunday").lower()
BACKFILL_ON_STARTUP = config["scheduler"].get("backfill_on_startup", True)
BACKFILL_LOOKBACK_DAYS = config["scheduler"].get("backfill_lookback_days", 7)


def check_and_fix_fx_gaps(start_date, end_date):
    """
    Check for FX rate gaps and automatically fix them.
    
    Args:
        start_date: Start of date range to check
        end_date: End of date range to check
    
    Returns:
        tuple: (gaps_found, gaps_fixed, error_message)
    """
    from models import FxRate
    from sqlalchemy import and_, cast, Date
    from utils.app_config import load_app_config
    
    db = SessionLocal()
    try:
        # Get configured FX pairs
        app_config = load_app_config()
        fx_pairs = app_config.get('fx_pairs', ['EUR/USD', 'EUR/THB'])
        
        logger.info(f"Checking FX gaps for pairs: {fx_pairs}")
        
        # Check each pair for gaps (skip weekends)
        all_gaps = {}
        current = start_date
        
        while current <= end_date:
            if current.weekday() < 5:  # Weekday only
                for pair in fx_pairs:
                    count = db.query(FxRate).filter(
                        and_(
                            FxRate.pair == pair,
                            cast(FxRate.as_of_date, Date) == current
                        )
                    ).count()
                    
                    if count == 0:
                        if pair not in all_gaps:
                            all_gaps[pair] = []
                        all_gaps[pair].append(current)
            
            current += timedelta(days=1)
        
        if not all_gaps:
            logger.info("✅ No FX rate gaps found")
            return 0, 0, None
        
        # Count total gaps
        total_gaps = sum(len(dates) for dates in all_gaps.values())
        logger.warning(f"Found FX gaps: {total_gaps} total")
        for pair, dates in all_gaps.items():
            logger.warning(f"  {pair}: {len(dates)} missing dates")
        
        # Auto-fix: Run backfill script
        logger.info("Attempting automatic FX gap fix...")
        
        try:
            import subprocess
            
            # Get unique dates to backfill
            all_dates = set()
            for dates in all_gaps.values():
                all_dates.update(dates)
            all_dates = sorted(all_dates)
            
            if all_dates:
                min_date = all_dates[0]
                max_date = all_dates[-1]
                
                cmd = [
                    'python3',
                    'scripts/backfill_fx_rates.py',
                    '--start-date', str(min_date),
                    '--end-date', str(max_date)
                ]
                
                logger.info(f"Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    logger.info("✅ FX gaps automatically fixed")
                    return total_gaps, total_gaps, None
                else:
                    error_msg = f"Backfill failed: {result.stderr}"
                    logger.error(error_msg)
                    return total_gaps, 0, error_msg
        
        except Exception as e:
            error_msg = f"Auto-fix failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return total_gaps, 0, error_msg
    
    finally:
        db.close()


def check_and_fix_market_data_gaps(start_date, end_date):
    """
    Check for market data gaps and automatically fix them.
    Only checks for symbols with active positions (quantity != 0).
    
    Args:
        start_date: Start of date range to check
        end_date: End of date range to check
    
    Returns:
        tuple: (gaps_found, gaps_fixed, error_message)
    """
    db = SessionLocal()
    try:
        # Check for dates missing market data
        query = text("""
            SELECT generate_series::date
            FROM generate_series(CAST(:start_date AS date), CAST(:end_date AS date), '1 day'::interval)
            WHERE NOT EXISTS (
                SELECT 1 FROM market_data
                WHERE DATE(as_of_date AT TIME ZONE 'UTC') = generate_series::date
                AND source IN ('coinmarketcap', 'yfinance', 'yfinance_historical')
                LIMIT 1
            )
            ORDER BY generate_series
        """)
        
        result = db.execute(query, {'start_date': start_date, 'end_date': end_date})
        missing_dates = [row[0] for row in result]
        
        if not missing_dates:
            logger.info("✅ No market data gaps found")
            return 0, 0, None
        
        logger.warning(f"Found market data gaps: {len(missing_dates)} dates")
        logger.warning(f"  Dates: {missing_dates[:10]}{'...' if len(missing_dates) > 10 else ''}")
        
        # Auto-fix: Run backfill script
        logger.info("Attempting automatic market data gap fix...")
        
        try:
            import subprocess
            
            cmd = [
                'python3',
                'scripts/backfill_historical_prices.py',
                '--start-date', str(missing_dates[0]),
                '--end-date', str(missing_dates[-1])
            ]
            
            logger.info(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode == 0:
                logger.info("✅ Market data gaps automatically fixed")
                return len(missing_dates), len(missing_dates), None
            else:
                error_msg = f"Backfill failed: {result.stderr}"
                logger.error(error_msg)
                return len(missing_dates), 0, error_msg
        
        except Exception as e:
            error_msg = f"Auto-fix failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return len(missing_dates), 0, error_msg
    
    finally:
        db.close()


def check_and_backfill_missing_data():
    """
    Check for missing FX rates and market data, and automatically fix gaps.
    
    BEHAVIOR:
    - Automatically attempts to fix all gaps within lookback window
    - Only sends notifications if automatic fix fails
    - FX gaps are always checked (not filtered by positions, skips weekends)
    - Market data gaps only for symbols with active positions
    - Respects legitimate gaps: FX on weekends, securities on holidays
    - After lookback window expires, gaps are considered legitimate (non-trading days)
    """
    if not BACKFILL_ON_STARTUP:
        logger.info("Gap detection is disabled")
        return
    
    logger.info("=" * 80)
    logger.info("CHECKING FOR DATA GAPS (FX + Market Data)")
    logger.info("=" * 80)
    logger.info(f"Looking back {BACKFILL_LOOKBACK_DAYS} days from today")
    logger.info("Auto-fix: ENABLED (standard behavior)")
    
    end_date = date.today() - timedelta(days=1)  # Don't include today
    start_date = end_date - timedelta(days=BACKFILL_LOOKBACK_DAYS - 1)
    
    from utils.notifications import send_job_failure_alert
    
    # Check FX gaps
    logger.info("\n1. Checking FX rate gaps (EUR/USD, EUR/THB)...")
    fx_gaps_found, fx_gaps_fixed, fx_error = check_and_fix_fx_gaps(start_date, end_date)
    
    # Check market data gaps
    logger.info("\n2. Checking market data gaps (active positions only)...")
    md_gaps_found, md_gaps_fixed, md_error = check_and_fix_market_data_gaps(start_date, end_date)
    
    # Send notifications only if auto-fix failed
    notifications_sent = False
    
    if fx_gaps_found > 0 and fx_gaps_fixed == 0:
        logger.error(f"❌ FX gaps NOT fixed: {fx_gaps_found} gaps remain")
        send_job_failure_alert(
            job_name="FX Rate Gap Auto-Fix",
            error_message=fx_error or "Failed to automatically fix FX rate gaps",
            job_type="daily",
            additional_info={
                "gaps_found": fx_gaps_found,
                "date_range": f"{start_date} to {end_date}",
                "pairs": "EUR/USD, EUR/THB",
                "manual_fix": f"python scripts/backfill_fx_rates.py --start-date {start_date} --end-date {end_date}",
                "severity": "HIGH - Tax compliance requires ECB FX rates"
            }
        )
        notifications_sent = True
    
    if md_gaps_found > 0 and md_gaps_fixed == 0:
        logger.error(f"❌ Market data gaps NOT fixed: {md_gaps_found} dates remain")
        send_job_failure_alert(
            job_name="Market Data Gap Auto-Fix",
            error_message=md_error or "Failed to automatically fix market data gaps",
            job_type="daily",
            additional_info={
                "gaps_found": md_gaps_found,
                "date_range": f"{start_date} to {end_date}",
                "manual_fix": f"python scripts/backfill_historical_prices.py --start-date {start_date} --end-date {end_date}",
                "severity": "MEDIUM - Affects portfolio valuations"
            }
        )
        notifications_sent = True
    
    # Summary
    logger.info("\n" + "="*80)
    logger.info("GAP CHECK SUMMARY")
    logger.info("="*80)
    logger.info(f"FX Rates: {fx_gaps_found} gaps found, {fx_gaps_fixed} fixed")
    logger.info(f"Market Data: {md_gaps_found} gaps found, {md_gaps_fixed} fixed")
    
    if fx_gaps_found == 0 and md_gaps_found == 0:
        logger.info("✅ No gaps found - data is complete!")
    elif fx_gaps_found == fx_gaps_fixed and md_gaps_found == md_gaps_fixed:
        logger.info("✅ All gaps automatically fixed!")
    elif notifications_sent:
        logger.error("❌ Some gaps could not be fixed - notifications sent")
    
    logger.info("="*80)


def run_all_jobs_sequential():
    """
    Run all jobs sequentially with sleep intervals.
    
    DAILY JOB SEQUENCE (Mon-Sun):
    1. Data Sync (~10 minutes)
       - Sync IBKR, Binance.th, Binance.com, crypto wallets
       - Update market prices (crypto, securities, FX, gold)
    2. Sleep (configured interval, default 5 minutes)
    3. Daily Snapshots (~30 seconds)
       - Create snapshots for automatic portfolios
    4. Sleep (configured interval)
    5. Manual Portfolio Forward-Fill (~10 seconds)
       - Forward-fill manual portfolios to today
    
    WEEKLY JOB SEQUENCE (Sunday only, after daily jobs):
    6. Sleep (configured interval)
    7. BNB Staking Reward Tracking (creates staking_reward transactions)
    8. Sleep (configured interval)
    9. Lot Recreation (~8 minutes)
       - Delete and rebuild ALL lots and allocations (includes new staking)
    10. Sleep (configured interval)
    11. Rolling Window Snapshots (~5 minutes)
        - Recreate last 30 days using lot-based calculation (includes staking)
    12. Sleep (configured interval)
    13. Position Recreation (~3 minutes)
        - Rebuild positions from transactions using lot allocations (reflects staking)
    
    Each job WAITS for previous to complete before starting.
    Sleep intervals allow database to settle and system to stabilize.
    Handles growing database without timing conflicts.
    """
    logger.info("=" * 80)
    logger.info("STARTING SEQUENTIAL JOB EXECUTION")
    logger.info("=" * 80)
    
    sleep_minutes = SLEEP_SECONDS / 60
    
    try:
        # DAILY JOB 1: Data Sync
        logger.info("Job 1/4 (Daily): Data Sync")
        logger.info("-" * 80)
        logger.info("Syncing IBKR, Binance, crypto wallets, and market prices...")
        try:
            sync_stats = run_daily_jobs()
            logger.info(f"✅ Data sync completed. Sleeping {sleep_minutes:.1f} minutes...")
            
            # NOTE: Transaction notification moved to after weekly jobs
            # This ensures Sunday's staking_reward transactions are included
            # (staking rewards created by weekly_jobs, so must query after)
                
        except Exception as e:
            error_msg = f"Data sync failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            logger.error(f"❌ {error_msg}")
            send_job_failure_alert(
                job_name="Data Sync",
                error_message=error_msg,
                job_type="daily",
                additional_info={
                    "stage": "1/4 Daily Jobs",
                    "next_action": "Will retry tomorrow at scheduled time"
                }
            )
            raise  # Re-raise to skip subsequent jobs
        
        time.sleep(SLEEP_SECONDS)
        
        # DAILY JOB 2: Daily Snapshots
        logger.info("Job 2/4 (Daily): Snapshot Creation")
        logger.info("-" * 80)
        logger.info("Creating daily snapshots for automatic portfolios...")
        try:
            create_snapshots()
            logger.info(f"✅ Snapshots completed. Sleeping {sleep_minutes:.1f} minutes...")
        except Exception as e:
            error_msg = f"Snapshot creation failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            logger.error(f"❌ {error_msg}")
            send_job_failure_alert(
                job_name="Daily Snapshots",
                error_message=error_msg,
                job_type="daily",
                additional_info={
                    "stage": "2/4 Daily Jobs",
                    "data_sync": "Completed successfully",
                    "next_action": "Will retry tomorrow at scheduled time"
                }
            )
            # Don't raise - continue to forward fill
        
        time.sleep(SLEEP_SECONDS)
        
        # DAILY JOB 3: Manual Forward Fill
        logger.info("Job 3/4 (Daily): Manual Portfolio Forward-Fill")
        logger.info("-" * 80)
        logger.info("Forward-filling manual portfolios (1, 2) to today...")
        try:
            forward_fill_manual_portfolios()
            logger.info("✅ Forward-fill completed.")
        except Exception as e:
            error_msg = f"Manual portfolio forward-fill failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            logger.error(f"❌ {error_msg}")
            send_job_failure_alert(
                job_name="Manual Portfolio Forward-Fill",
                error_message=error_msg,
                job_type="daily",
                additional_info={
                    "stage": "3/4 Daily Jobs",
                    "affected_portfolios": "1, 2 (Cash portfolios)",
                    "impact": "Manual portfolios may have gaps until next run"
                }
            )
            # Don't raise - continue to precomputation
        
        time.sleep(SLEEP_SECONDS)
        
        # DAILY JOB 4: Pre-compute Dashboard Data
        logger.info("Job 4/4 (Daily): Pre-compute Dashboard Data")
        logger.info("-" * 80)
        logger.info("Pre-computing portfolio summaries, statistics, and charts...")
        try:
            results = precompute_all_portfolios(force=True)
            if 'error' in results:
                raise Exception(results['error'])
            logger.info(
                f"✅ Pre-computation completed: "
                f"{results.get('portfolios_processed', 0)} portfolios, "
                f"{results.get('summaries_cached', 0)} summaries, "
                f"{results.get('period_stats_cached', 0)} period stats, "
                f"{results.get('charts_cached', 0)} charts"
            )
        except Exception as e:
            error_msg = f"Dashboard pre-computation failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            logger.error(f"❌ {error_msg}")
            send_job_failure_alert(
                job_name="Dashboard Pre-computation",
                error_message=error_msg,
                job_type="daily",
                additional_info={
                    "stage": "4/4 Daily Jobs",
                    "impact": "Dashboard will use live computation (slower) until next run",
                    "next_action": "Will retry tomorrow at scheduled time"
                }
            )
            # Don't raise - non-critical, dashboard falls back to live computation
        
        # WEEKLY JOBS (if configured day)
        is_weekly_day = datetime.today().strftime('%A').lower() == WEEKLY_JOBS_DAY
        
        if is_weekly_day:
            logger.info("=" * 80)
            logger.info(f"WEEKLY JOBS DAY ({WEEKLY_JOBS_DAY.upper()}) - Starting Weekly Tasks")
            logger.info("=" * 80)
            logger.info(f"Sleeping {sleep_minutes:.1f} minutes before weekly jobs...")
            time.sleep(SLEEP_SECONDS)
            
            logger.info("Weekly Jobs: Lot Recreation → Rolling Window → Position Recreation")
            logger.info("-" * 80)
            try:
                run_weekly_jobs()  # Contains all 3 weekly jobs with internal sleep intervals
                logger.info("✅ Weekly jobs completed.")
            except Exception as e:
                error_msg = f"Weekly jobs failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
                logger.error(f"❌ {error_msg}")
                send_job_failure_alert(
                    job_name="Weekly Jobs",
                    error_message=error_msg,
                    job_type="weekly",
                    additional_info={
                        "jobs": "Lot Recreation + Rolling Window Snapshots + Position Recreation",
                        "severity": "HIGH - affects cost basis and historical accuracy",
                        "next_run": f"Next {WEEKLY_JOBS_DAY.capitalize()}",
                        "manual_fix": "Can run scripts/regenerate_all.sh manually"
                    }
                )
                raise  # Weekly jobs are critical
        else:
            logger.info(f"Not {WEEKLY_JOBS_DAY} - skipping weekly jobs")
        
        # Collect unreviewed transactions AFTER all jobs (daily + weekly)
        # This ensures transactions created during jobs are included:
        # - BNB staking rewards (created Sunday by weekly jobs)
        # - All other synced transactions from daily jobs
        # - Manual entries and inactive account transactions
        logger.info("Collecting unreviewed transactions for notification...")
        sync_stats = collect_unreviewed_transactions()
        
        # Send transaction notification if there are unreviewed transactions
        if sync_stats and sync_stats.get('total_new', 0) > 0:
            total_unreviewed = sync_stats['total_new']
            accounts_synced = sync_stats['accounts_synced']
            
            logger.info(f"Sending transaction notification: {total_unreviewed} unreviewed transactions")
            
            send_transaction_ingestion_summary(
                accounts_synced=accounts_synced,
                total_new=total_unreviewed,
                total_reviewed=0  # All in notification are unreviewed
            )
        else:
            logger.info("No unreviewed transactions - skipping notification")
        
        logger.info("=" * 80)
        logger.info("ALL JOBS COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Error during job execution: {e}", exc_info=True)
        logger.info("Job execution failed - will retry at next scheduled time")


# Schedule the main jobs
def schedule_jobs():
    """
    Schedule the sequential job execution.
    
    All jobs run sequentially starting at configured time (default: 01:00 AM).
    Jobs wait for previous to complete before starting.
    Weekly jobs run on configured day (default: Sunday).
    """
    job_time = f"{JOBS_START_HOUR:02d}:{JOBS_START_MINUTE:02d}"
    sleep_minutes = SLEEP_SECONDS / 60
    
    # Schedule log rotation for external logs
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    restic_log = os.path.join(log_dir, 'restic_backup.log')
    schedule.every().hour.do(lambda: rotate_log_file_daily(restic_log))
    
    # Schedule the sequential execution
    schedule.every().day.at(job_time).do(run_all_jobs_sequential)
    
    logger.info("=" * 80)
    logger.info("SCHEDULER CONFIGURATION")
    logger.info("=" * 80)
    logger.info(f"Jobs start time: {job_time}")
    logger.info(f"Sleep between jobs: {sleep_minutes:.1f} minutes ({SLEEP_SECONDS} seconds)")
    logger.info(f"Weekly jobs day: {WEEKLY_JOBS_DAY.capitalize()}")
    logger.info("=" * 80)
    logger.info("DAILY JOB SEQUENCE (Mon-Sun):")
    logger.info("  1. Data Sync")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  2. Daily Snapshots")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  3. Manual Portfolio Forward-Fill")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  4. Pre-compute Dashboard Cache")
    logger.info("")
    logger.info(f"WEEKLY JOB SEQUENCE ({WEEKLY_JOBS_DAY.capitalize()} only, after daily jobs):")
    logger.info(f"  → Sleep {sleep_minutes:.1f} min")
    logger.info("  4. BNB Staking Reward Tracking (creates transactions)")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  5. Lot Recreation (delete & rebuild ALL lots, includes new staking)")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  6. Rolling Window Snapshots (last 30 days, includes staking rewards)")
    logger.info(f"     → Sleep {sleep_minutes:.1f} min")
    logger.info("  7. Position Recreation (rebuild from transactions, reflects staking)")
    logger.info("=" * 80)
    
    # Run scheduler loop
    logger.info(f"Scheduler started. Next run: {job_time}")
    logger.info("Checking for pending jobs every 60 seconds...")
    logger.info(f"Heartbeat file: {HEARTBEAT_FILE}")
    
    while True:
        # Write heartbeat for health checks
        try:
            HEARTBEAT_FILE.write_text(datetime.now().isoformat())
        except Exception as e:
            logger.warning(f"Failed to write heartbeat: {e}")
        
        schedule.run_pending()
        time.sleep(60)

# Start the scheduler
if __name__ == "__main__":
    logger.info("Portfolio Tracker Worker - Sequential Scheduler")
    logger.info("=" * 80)
    
    # Check for missing data on startup
    if BACKFILL_ON_STARTUP:
        check_and_backfill_missing_data()
    
    # Start scheduler
    schedule_jobs()
