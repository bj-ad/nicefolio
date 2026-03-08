"""
NiceFolio Settings Page
Provides a GUI for editing configuration files and running maintenance jobs.

Features:
- Edit app_config.yaml, portfolio_config.yaml, accounts_config.yaml
- Save changes and restart containers
- Run daily jobs (sync transactions) manually
- Run weekly jobs (recalculate lots, positions, snapshots) manually
"""

import os
import sys
import yaml
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from nicegui import ui, app

from apps.core.layout import page_layout

from utils.logging_config import get_logger

logger = get_logger("settings_manager")

# Config file paths
CONFIG_DIR = Path(__file__).parent.parent.parent / 'config'
CONFIG_FILES = {
    'app_config': CONFIG_DIR / 'app_config.yaml',
    'portfolio_config': CONFIG_DIR / 'portfolio_config.yaml',
    'accounts_config': CONFIG_DIR / 'accounts_config.yaml',
    'source_mapping': CONFIG_DIR / 'source_mapping.yaml',
    'symbol_mapping': CONFIG_DIR / 'symbol_mapping.yaml',
    'symbol_normalization': CONFIG_DIR / 'symbol_normalization.yaml',
}

# Container names for restart
CONTAINER_NAMES = ['nicefolio_gui', 'nicefolio_worker']


# =============================================================================
# Job Runner - Background execution with log streaming
# =============================================================================

class JobRunner:
    """
    Manages background job execution with real-time log streaming.
    Jobs continue running even if the user closes the browser tab.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.current_job: Optional[str] = None
        self.job_thread: Optional[threading.Thread] = None
        self.job_logs: List[str] = []
        self.job_start_time: Optional[datetime] = None
        self.job_end_time: Optional[datetime] = None
        self.job_success: Optional[bool] = None
        self._log_callbacks: List[Callable[[str], None]] = []
    
    def is_running(self) -> bool:
        """Check if a job is currently running."""
        return self.job_thread is not None and self.job_thread.is_alive()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current job status."""
        return {
            'running': self.is_running(),
            'job_name': self.current_job,
            'start_time': self.job_start_time,
            'end_time': self.job_end_time,
            'success': self.job_success,
            'log_count': len(self.job_logs),
        }
    
    def get_logs(self, last_n: int = 100) -> List[str]:
        """Get the last N log lines."""
        return self.job_logs[-last_n:]
    
    def add_log_callback(self, callback: Callable[[str], None]):
        """Add a callback to be called when new logs are added."""
        self._log_callbacks.append(callback)
    
    def remove_log_callback(self, callback: Callable[[str], None]):
        """Remove a log callback."""
        if callback in self._log_callbacks:
            self._log_callbacks.remove(callback)
    
    def _log(self, message: str):
        """Add a log message and notify callbacks."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {message}"
        self.job_logs.append(log_line)
        
        # Keep logs from getting too large (max 5000 lines)
        if len(self.job_logs) > 5000:
            self.job_logs = self.job_logs[-4000:]
        
        # Notify callbacks
        for callback in self._log_callbacks:
            try:
                callback(log_line)
            except Exception:
                pass  # Ignore callback errors
    
    def run_daily_jobs(self) -> bool:
        """Run daily jobs (sync transactions) in background."""
        if self.is_running():
            logger.warning("Cannot start daily jobs - another job is already running")
            return False
        
        self.current_job = "Daily Jobs (Sync Transactions)"
        self.job_logs = []
        self.job_start_time = datetime.now()
        self.job_end_time = None
        self.job_success = None
        
        def run():
            try:
                self._log("=" * 60)
                self._log("STARTING DAILY JOBS (Sync Transactions)")
                self._log("=" * 60)
                self._log("")
                
                # Import here to avoid circular imports
                from worker.daily_jobs import (
                    sync_ibkr_transactions,
                    sync_binanceth_transactions,
                    sync_binancecom_transactions,
                    sync_crypto_wallets_with_balance,
                    detect_internal_transfers,
                    sync_fx_rates,
                    sync_crypto_prices,
                    sync_securities_prices,
                    sync_gold_price,
                    create_snapshots,
                    forward_fill_manual_portfolios,
                )
                from service.marketdata_service import sync_crypto_prices, sync_securities_prices
                from service.fx_service import sync_fx_rates
                from service.goldtradersth_service import sync_gold_price
                
                # Transaction syncing
                self._log("Step 1/10: Syncing IBKR transactions...")
                try:
                    sync_ibkr_transactions()
                    self._log("  ✓ IBKR sync complete")
                except Exception as e:
                    self._log(f"  ✗ IBKR sync failed: {e}")
                
                self._log("Step 2/10: Syncing Binance.th transactions...")
                try:
                    sync_binanceth_transactions()
                    self._log("  ✓ Binance.th sync complete")
                except Exception as e:
                    self._log(f"  ✗ Binance.th sync failed: {e}")
                
                self._log("Step 3/10: Syncing Binance.com transactions...")
                try:
                    sync_binancecom_transactions()
                    self._log("  ✓ Binance.com sync complete")
                except Exception as e:
                    self._log(f"  ✗ Binance.com sync failed: {e}")
                
                self._log("Step 4/10: Syncing crypto wallets with balance tracking...")
                try:
                    sync_crypto_wallets_with_balance()
                    self._log("  ✓ Crypto wallet sync complete")
                except Exception as e:
                    self._log(f"  ✗ Crypto wallet sync failed: {e}")
                
                self._log("Step 5/10: Detecting internal transfers...")
                try:
                    detect_internal_transfers()
                    self._log("  ✓ Internal transfer detection complete")
                except Exception as e:
                    self._log(f"  ✗ Internal transfer detection failed: {e}")
                
                # Market data syncing
                self._log("Step 6/10: Syncing FX rates...")
                try:
                    sync_fx_rates()
                    self._log("  ✓ FX rates sync complete")
                except Exception as e:
                    self._log(f"  ✗ FX rates sync failed: {e}")
                
                self._log("Step 7/10: Syncing crypto prices...")
                try:
                    sync_crypto_prices()
                    self._log("  ✓ Crypto prices sync complete")
                except Exception as e:
                    self._log(f"  ✗ Crypto prices sync failed: {e}")
                
                self._log("Step 8/10: Syncing securities prices...")
                try:
                    sync_securities_prices()
                    self._log("  ✓ Securities prices sync complete")
                except Exception as e:
                    self._log(f"  ✗ Securities prices sync failed: {e}")
                
                self._log("Step 9/10: Syncing gold price...")
                try:
                    sync_gold_price()
                    self._log("  ✓ Gold price sync complete")
                except Exception as e:
                    self._log(f"  ✗ Gold price sync failed: {e}")
                
                # Snapshot creation
                self._log("Step 10/10: Creating daily snapshots...")
                try:
                    create_snapshots()
                    self._log("  ✓ Snapshots created")
                except Exception as e:
                    self._log(f"  ✗ Snapshot creation failed: {e}")
                
                # Forward-fill manual portfolios
                self._log("Bonus: Forward-filling manual portfolios...")
                try:
                    forward_fill_manual_portfolios()
                    self._log("  ✓ Manual portfolios forward-filled")
                except Exception as e:
                    self._log(f"  ✗ Manual portfolio forward-fill failed: {e}")
                
                self._log("")
                self._log("=" * 60)
                self._log("DAILY JOBS COMPLETED SUCCESSFULLY")
                self._log("=" * 60)
                
                self.job_success = True
                
            except Exception as e:
                self._log(f"")
                self._log(f"ERROR: Daily jobs failed with exception: {e}")
                self._log("=" * 60)
                self.job_success = False
                logger.error(f"Daily jobs failed: {e}", exc_info=True)
            
            finally:
                self.job_end_time = datetime.now()
                if self.job_start_time:
                    duration = (self.job_end_time - self.job_start_time).total_seconds()
                    self._log(f"Duration: {duration:.1f} seconds")
        
        self.job_thread = threading.Thread(target=run, daemon=True)
        self.job_thread.start()
        logger.info("Daily jobs started in background")
        return True
    
    def run_weekly_jobs(self) -> bool:
        """Run weekly jobs (recalculate lots, positions, snapshots) in background."""
        if self.is_running():
            logger.warning("Cannot start weekly jobs - another job is already running")
            return False
        
        self.current_job = "Weekly Jobs (Recalculate Lots/Positions/Snapshots)"
        self.job_logs = []
        self.job_start_time = datetime.now()
        self.job_end_time = None
        self.job_success = None
        
        def run():
            try:
                self._log("=" * 60)
                self._log("STARTING WEEKLY JOBS")
                self._log("(Recalculate Lots, Positions, Snapshots)")
                self._log("=" * 60)
                self._log("")
                self._log("⚠️  This may take 10-20 minutes. Please wait...")
                self._log("")
                
                # Import here to avoid circular imports
                from worker.weekly_jobs import (
                    reconcile_lots,
                    recreate_rolling_window,
                    recreate_positions,
                )
                from utils.app_config import load_app_config
                
                config = load_app_config()
                sleep_seconds = config['scheduler'].get('sleep_between_jobs', 60)
                
                # Step 1: Lot Recreation
                self._log("Step 1/3: Recreating lots (FIFO allocation)...")
                self._log("  This deletes and rebuilds ALL lots and allocations from scratch")
                try:
                    reconcile_lots()
                    self._log("  ✓ Lot recreation complete")
                except Exception as e:
                    self._log(f"  ✗ Lot recreation failed: {e}")
                
                self._log(f"  Sleeping {sleep_seconds}s to let database settle...")
                time.sleep(sleep_seconds)
                
                # Step 2: Rolling Window Snapshots
                self._log("Step 2/3: Recreating rolling window snapshots (30 days)...")
                try:
                    recreate_rolling_window()
                    self._log("  ✓ Rolling window snapshots complete")
                except Exception as e:
                    self._log(f"  ✗ Rolling window snapshots failed: {e}")
                
                self._log(f"  Sleeping {sleep_seconds}s to let database settle...")
                time.sleep(sleep_seconds)
                
                # Step 3: Position Recreation
                self._log("Step 3/3: Recreating positions from transactions...")
                try:
                    recreate_positions()
                    self._log("  ✓ Position recreation complete")
                except Exception as e:
                    self._log(f"  ✗ Position recreation failed: {e}")
                
                self._log("")
                self._log("=" * 60)
                self._log("WEEKLY JOBS COMPLETED SUCCESSFULLY")
                self._log("=" * 60)
                
                self.job_success = True
                
            except Exception as e:
                self._log(f"")
                self._log(f"ERROR: Weekly jobs failed with exception: {e}")
                self._log("=" * 60)
                self.job_success = False
                logger.error(f"Weekly jobs failed: {e}", exc_info=True)
            
            finally:
                self.job_end_time = datetime.now()
                if self.job_start_time:
                    duration = (self.job_end_time - self.job_start_time).total_seconds()
                    self._log(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
        
        self.job_thread = threading.Thread(target=run, daemon=True)
        self.job_thread.start()
        logger.info("Weekly jobs started in background")
        return True
    
    def run_bnb_staking_tracking(self) -> bool:
        """Run BNB staking reward tracking in background."""
        if self.is_running():
            logger.warning("Cannot start BNB staking tracking - another job is already running")
            return False
        
        self.current_job = "BNB Staking Reward Tracking"
        self.job_logs = []
        self.job_start_time = datetime.now()
        self.job_end_time = None
        self.job_success = None
        
        def run():
            try:
                self._log("=" * 60)
                self._log("STARTING BNB STAKING REWARD TRACKING")
                self._log("(German Tax Compliance - § 22 Nr. 3 EStG)")
                self._log("=" * 60)
                self._log("")
                
                # Import here to avoid circular imports
                from worker.weekly_jobs import track_bnb_staking_rewards
                
                self._log("Tracking BNB staking rewards...")
                self._log("  - Querying current staked values from blockchain")
                self._log("  - Calculating realized + pending rewards")
                self._log("  - Creating staking_reward transactions for new rewards")
                self._log("")
                
                try:
                    track_bnb_staking_rewards()
                    self._log("  ✓ BNB staking tracking complete")
                except Exception as e:
                    self._log(f"  ✗ BNB staking tracking failed: {e}")
                    raise
                
                self._log("")
                self._log("=" * 60)
                self._log("BNB STAKING TRACKING COMPLETED SUCCESSFULLY")
                self._log("=" * 60)
                
                self.job_success = True
                
            except Exception as e:
                self._log(f"")
                self._log(f"ERROR: BNB staking tracking failed with exception: {e}")
                self._log("=" * 60)
                self.job_success = False
                logger.error(f"BNB staking tracking failed: {e}", exc_info=True)
            
            finally:
                self.job_end_time = datetime.now()
                if self.job_start_time:
                    duration = (self.job_end_time - self.job_start_time).total_seconds()
                    self._log(f"Duration: {duration:.1f} seconds")
        
        self.job_thread = threading.Thread(target=run, daemon=True)
        self.job_thread.start()
        logger.info("BNB staking tracking started in background")
        return True
    
    def run_position_audit(self) -> bool:
        """Run position audit (passive, read-only) in background with email notification."""
        if self.is_running():
            logger.warning("Cannot start position audit - another job is already running")
            return False
        
        self.current_job = "Position Audit (Tax Compliance)"
        self.job_logs = []
        self.job_start_time = datetime.now()
        self.job_end_time = None
        self.job_success = None
        
        def run():
            try:
                self._log("=" * 60)
                self._log("STARTING POSITION AUDIT")
                self._log("(Passive observation only - no database writes)")
                self._log("=" * 60)
                self._log("")
                
                # Import here to avoid circular imports
                from service.audit_service import run_full_position_audit, send_audit_notification
                
                self._log("Auditing positions:")
                self._log("  1. IBKR Securities & Commodities")
                self._log("  2. Cash Pool (all currencies)")
                self._log("  3. Crypto Wallets (with staking)")
                self._log("")
                
                self._log("Running full position audit...")
                results = run_full_position_audit()
                
                summary = results.get('summary', {})
                discrepancies = summary.get('total_discrepancies', 0)
                issues = summary.get('portfolios_with_issues', [])
                
                if discrepancies > 0:
                    self._log(f"  ⚠️  Found {discrepancies} discrepancies in: {', '.join(issues)}")
                elif issues:
                    self._log(f"  ❌ Some audits failed: {', '.join(issues)}")
                else:
                    self._log("  ✅ All positions verified - no discrepancies")
                
                self._log("")
                self._log("Sending audit notification email...")
                
                try:
                    if send_audit_notification(results):
                        self._log("  ✓ Notification sent successfully")
                    else:
                        self._log("  ⚠️  Notification not sent (may be disabled)")
                except Exception as e:
                    self._log(f"  ✗ Notification failed: {e}")
                
                self._log("")
                self._log("=" * 60)
                self._log("POSITION AUDIT COMPLETED")
                self._log("=" * 60)
                
                self.job_success = True
                
            except Exception as e:
                self._log(f"")
                self._log(f"ERROR: Position audit failed with exception: {e}")
                self._log("=" * 60)
                self.job_success = False
                logger.error(f"Position audit failed: {e}", exc_info=True)
            
            finally:
                self.job_end_time = datetime.now()
                if self.job_start_time:
                    duration = (self.job_end_time - self.job_start_time).total_seconds()
                    self._log(f"Duration: {duration:.1f} seconds")
        
        self.job_thread = threading.Thread(target=run, daemon=True)
        self.job_thread.start()
        logger.info("Position audit started in background")
        return True


# Global job runner instance
job_runner = JobRunner()


# =============================================================================
# Config File Operations
# =============================================================================

def load_config_file(config_name: str) -> Optional[str]:
    """Load a config file and return its content as string."""
    if config_name not in CONFIG_FILES:
        logger.error(f"Unknown config file: {config_name}")
        return None
    
    try:
        with open(CONFIG_FILES[config_name], 'r') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load {config_name}: {e}")
        return None


def save_config_file(config_name: str, content: str) -> tuple[bool, str]:
    """
    Save content to a config file.
    Validates YAML syntax before saving.
    
    Returns:
        tuple[bool, str]: (success, message)
    """
    if config_name not in CONFIG_FILES:
        return False, f"Unknown config file: {config_name}"
    
    # Validate YAML syntax
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return False, f"Invalid YAML syntax: {e}"
    
    config_path = CONFIG_FILES[config_name]
    
    try:
        # Write new content directly (no backup since configs are in git)
        with open(config_path, 'w') as f:
            f.write(content)
        
        logger.info(f"Saved {config_name}")
        return True, "Saved successfully."
        
    except Exception as e:
        logger.error(f"Failed to save {config_name}: {e}")
        return False, f"Failed to save: {e}"


def restart_application():
    """
    Restart the application using the 'Suicide & Revive' pattern.
    
    Docker container is configured with 'restart: unless-stopped',
    so when we exit the Python process, Docker automatically restarts it.
    """
    logger.info("Restarting application via GUI request (sys.exit)")
    print("Restarting application...")
    sys.exit(0)


# =============================================================================
# Settings Page UI Components
# =============================================================================

def create_config_editor_tab(tab_name: str, config_name: str, description: str):
    """Create a config editor tab panel."""
    content = load_config_file(config_name)
    
    with ui.column().classes('w-full gap-4'):
        # Description
        ui.label(description).classes('text-gray-600 mb-2')
        
        # Editor
        editor = ui.textarea(value=content or '# Failed to load config').props(
            'outlined rows=25 style="font-family: monospace; font-size: 12px;"'
        ).classes('w-full')
        
        # Action buttons
        with ui.row().classes('gap-4 mt-4'):
            async def save_config():
                success, message = save_config_file(config_name, editor.value)
                if success:
                    ui.notify(message, type='positive')
                    await show_restart_dialog()
                else:
                    ui.notify(message, type='negative')
            
            ui.button('💾 Save Changes', on_click=save_config).props('color=primary')
            
            def reload_config():
                new_content = load_config_file(config_name)
                if new_content:
                    editor.value = new_content
                    ui.notify(f'Reloaded {config_name}', type='info')
                else:
                    ui.notify(f'Failed to reload {config_name}', type='negative')
            
            ui.button('🔄 Reload', on_click=reload_config).props('color=secondary flat')


async def show_restart_dialog():
    """Show dialog to confirm application restart."""
    with ui.dialog() as dialog, ui.card().classes('p-6 max-w-2xl'):
        ui.label('Restart GUI?').classes('text-xl font-bold mb-4')
        
        ui.markdown('''
**Config saved successfully!** 

**Restart GUI now?** This will reload the web interface with the new config.

⚠️ **Important:** 
- **GUI container** restarts automatically (this page will disconnect briefly)
- **Worker container** continues running - most configs are read dynamically on each job run
- **Scheduler changes** (job times/days) require manual worker restart:

```bash
docker compose restart nicefolio_worker
```

**To restart both containers:**
```bash
docker compose restart nicefolio_gui nicefolio_worker
```
        ''').classes('text-gray-700 mb-4')
        
        with ui.row().classes('gap-4 justify-end'):
            ui.button('Later', on_click=dialog.close).props('flat')
            ui.button('Restart GUI Now', on_click=lambda: (dialog.close(), restart_application())).props('color=primary')
    
    dialog.open()


def create_maintenance_tab():
    """Create the maintenance jobs tab panel."""
    
    # State for log display
    log_container = None
    status_label = None
    
    with ui.column().classes('w-full gap-6'):
        # Description
        with ui.card().classes('w-full p-4 bg-blue-50'):
            ui.label('🔧 Maintenance Jobs').classes('text-xl font-bold mb-2')
            ui.markdown('''
**These jobs run automatically according to the scheduler, but you can trigger them manually here.**

- **Sync Transactions (Daily Jobs):** Fetches transactions from IBKR, Binance, crypto wallets, 
  updates market prices (for **today**), and creates daily snapshots. Takes ~5-10 minutes.
  
- **Recalculate All (Weekly Jobs):** Recreates lots using FIFO, recalculates positions, 
  and regenerates the last 30 days of snapshots. Takes ~15-20 minutes.
  
- **BNB Staking Rewards:** Tracks BNB staking rewards for German tax compliance (§ 22 Nr. 3 EStG).
  Creates staking_reward transactions for new rewards. Takes ~1-2 minutes.

- **Run Audit:** Compares database positions against external sources (IBKR, blockchain).
  Passive observation only - no database writes. Sends detailed email report. Takes ~2-3 minutes.

Jobs run in the background and will continue even if you close this page.
            ''')
        
        # Job status
        with ui.card().classes('w-full p-4'):
            ui.label('Job Status').classes('text-lg font-bold mb-2')
            
            status = job_runner.get_status()
            
            if status['running']:
                with ui.row().classes('items-center gap-2'):
                    ui.spinner('dots', size='lg').classes('text-blue-500')
                    status_label = ui.label(f"Running: {status['job_name']}")
                    if status['start_time']:
                        elapsed = (datetime.now() - status['start_time']).total_seconds()
                        ui.label(f"({elapsed:.0f}s elapsed)").classes('text-gray-500')
            elif status['job_name'] and status['end_time']:
                icon = '✓' if status['success'] else '✗'
                color = 'text-green-600' if status['success'] else 'text-red-600'
                with ui.row().classes('items-center gap-2'):
                    ui.label(f"{icon} Last job: {status['job_name']}").classes(color)
                    ui.label(f"at {status['end_time'].strftime('%H:%M:%S')}").classes('text-gray-500')
            else:
                ui.label('No jobs running').classes('text-gray-500')
        
        # Action buttons
        with ui.row().classes('gap-4 flex-wrap'):
            def start_daily_jobs():
                if job_runner.is_running():
                    ui.notify('A job is already running. Please wait for it to complete.', type='warning')
                    return
                if job_runner.run_daily_jobs():
                    ui.notify('Daily jobs started. Check logs below for progress.', type='positive')
                    ui.navigate.reload()  # Refresh to show running status
                else:
                    ui.notify('Failed to start daily jobs', type='negative')
            
            def start_weekly_jobs():
                if job_runner.is_running():
                    ui.notify('A job is already running. Please wait for it to complete.', type='warning')
                    return
                if job_runner.run_weekly_jobs():
                    ui.notify('Weekly jobs started. Check logs below for progress.', type='positive')
                    ui.navigate.reload()  # Refresh to show running status
                else:
                    ui.notify('Failed to start weekly jobs', type='negative')
            
            def start_bnb_staking():
                if job_runner.is_running():
                    ui.notify('A job is already running. Please wait for it to complete.', type='warning')
                    return
                if job_runner.run_bnb_staking_tracking():
                    ui.notify('BNB staking tracking started. Check logs below for progress.', type='positive')
                    ui.navigate.reload()  # Refresh to show running status
                else:
                    ui.notify('Failed to start BNB staking tracking', type='negative')
            
            def start_position_audit():
                if job_runner.is_running():
                    ui.notify('A job is already running. Please wait for it to complete.', type='warning')
                    return
                if job_runner.run_position_audit():
                    ui.notify('Position audit started. Results will be emailed. Check logs below for progress.', type='positive')
                    ui.navigate.reload()  # Refresh to show running status
                else:
                    ui.notify('Failed to start position audit', type='negative')
            
            ui.button(
                '🔄 Sync Transactions (Daily Jobs)',
                on_click=start_daily_jobs
            ).props('color=primary').classes('px-6')
            
            ui.button(
                '🔁 Recalculate All (Weekly Jobs)',
                on_click=start_weekly_jobs
            ).props('color=secondary').classes('px-6')
            
            ui.button(
                '💰 BNB Staking Rewards',
                on_click=start_bnb_staking
            ).props('color=warning').classes('px-6')
            
            ui.button(
                '🔍 Run Audit',
                on_click=start_position_audit
            ).props('color=info').classes('px-6').tooltip('Run position audit (passive, read-only) and send email notification')
        
        # Log viewer
        with ui.card().classes('w-full p-4'):
            with ui.row().classes('items-center justify-between mb-2'):
                ui.label('📋 Job Logs').classes('text-lg font-bold')
                
                def refresh_logs():
                    ui.navigate.reload()
                
                ui.button('Refresh', on_click=refresh_logs, icon='refresh').props('flat dense')
            
            logs = job_runner.get_logs(last_n=200)
            
            if logs:
                log_text = '\n'.join(logs)
            else:
                log_text = '(No logs yet - run a job to see output)'
            
            # Log display with auto-scroll
            log_area = ui.textarea(value=log_text).props(
                'outlined readonly rows=20 style="font-family: monospace; font-size: 11px;"'
            ).classes('w-full bg-gray-100 text-gray-900')
            
            # Auto-refresh logs while job is running (but don't reload entire page)
            if job_runner.is_running():
                ui.label('🔄 Logs auto-refreshing every 5 seconds while job is running...').classes('text-gray-500 text-sm mt-2')
                
                # Track if component is still active (prevents orphaned timers)
                is_active = {'value': True}
                current_timer = {'ref': None}  # Store current timer reference
                
                def auto_refresh_logs():
                    # Stop if component was destroyed or job finished
                    if not is_active['value'] or not job_runner.is_running():
                        return
                    
                    try:
                        # Update just the log textarea instead of reloading entire page
                        new_logs = job_runner.get_logs(last_n=200)
                        if new_logs:
                            log_area.value = '\n'.join(new_logs)
                        # Schedule next refresh only if still active
                        if is_active['value']:
                            current_timer['ref'] = ui.timer(5.0, auto_refresh_logs, once=True)
                    except RuntimeError as e:
                        # Component was destroyed (parent slot deleted), stop refreshing
                        if 'parent slot' in str(e).lower():
                            logger.debug('Log refresh timer stopped - component destroyed')
                            is_active['value'] = False
                    except Exception as e:
                        # Other errors, stop refreshing
                        logger.warning(f'Log refresh timer error: {e}')
                        is_active['value'] = False
                
                # Cancel timer chain when page is destroyed
                def cleanup():
                    is_active['value'] = False
                    if current_timer['ref']:
                        try:
                            current_timer['ref'].cancel()
                        except Exception:
                            pass
                
                ui.context.client.on_disconnect(cleanup)
                
                current_timer['ref'] = ui.timer(5.0, auto_refresh_logs, once=True)


def create_settings_page_content():
    """Create the main settings page content with tabs."""
    
    with ui.column().classes('w-full gap-4'):
        
        # Tabs for different sections
        with ui.tabs().classes('w-full') as tabs:
            maintenance_tab = ui.tab('Maintenance', icon='build')
            app_config_tab = ui.tab('App Config', icon='tune')
            portfolio_config_tab = ui.tab('Portfolios', icon='folder')
            accounts_config_tab = ui.tab('Accounts', icon='account_circle')
            source_mapping_tab = ui.tab('Source Mapping', icon='link')
            symbol_mapping_tab = ui.tab('Symbol Mapping', icon='swap_horiz')
            symbol_norm_tab = ui.tab('Symbol Normalization', icon='transform')
        
        with ui.tab_panels(tabs, value=maintenance_tab).classes('w-full'):
            
            # Maintenance tab (default)
            with ui.tab_panel(maintenance_tab):
                create_maintenance_tab()
            
            # App Config tab
            with ui.tab_panel(app_config_tab):
                create_config_editor_tab(
                    'App Config',
                    'app_config',
                    'Application settings: scheduler, market data symbols, cache settings, notifications, etc.'
                )
            
            # Portfolio Config tab
            with ui.tab_panel(portfolio_config_tab):
                create_config_editor_tab(
                    'Portfolio Config',
                    'portfolio_config',
                    'Portfolio definitions: names, types, update methods (automatic/manual), status (active/closed).'
                )
            
            # Accounts Config tab
            with ui.tab_panel(accounts_config_tab):
                create_config_editor_tab(
                    'Accounts Config',
                    'accounts_config',
                    'Account definitions: brokers, exchanges, wallets, their types and status.'
                )
            
            # Source Mapping tab
            with ui.tab_panel(source_mapping_tab):
                create_config_editor_tab(
                    'Source Mapping',
                    'source_mapping',
                    'Maps external data sources to internal account identifiers for transaction import.'
                )
            
            # Symbol Mapping tab
            with ui.tab_panel(symbol_mapping_tab):
                create_config_editor_tab(
                    'Symbol Mapping',
                    'symbol_mapping',
                    'Maps non-standard symbol formats from various exchanges and brokers to standardized symbols.'
                )
            
            # Symbol Normalization tab
            with ui.tab_panel(symbol_norm_tab):
                create_config_editor_tab(
                    'Symbol Normalization',
                    'symbol_normalization',
                    'Defines standardized symbol formats for different asset classes (crypto, stocks, commodities).'
                )


@ui.page('/settings')
def settings_page():
    """Settings page"""
    with page_layout('/settings'):
        create_settings_page_content()
