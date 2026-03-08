"""
Recreate Snapshots - Rolling Window Strategy

This script implements a hybrid snapshot recreation strategy:

1. AUTOMATIC PORTFOLIOS:
   - Recreate last 30 days daily
   - Handles late transactions, price corrections, new symbols
   - Ensures data consistency
   - Portfolios selected from portfolio_config.yaml where:
     * status = "active"
     * update_method = "automatic"

2. MANUAL PORTFOLIOS:
   - Interpolate between manual updates (smooth curve)
   - Forward-fill from last update to today
   - Handled by cash_manager.py when user saves

Usage:
    # Recreate last 30 days for automatic portfolios (from config)
    python scripts/recreate_snapshots_rolling_window.py
    
    # Custom date range
    python scripts/recreate_snapshots_rolling_window.py --days 60
    
    # Specific date range
    python scripts/recreate_snapshots_rolling_window.py --start-date 2025-10-01 --end-date 2025-11-27
    
    # Override: specific portfolios (comma-separated IDs)
    python scripts/recreate_snapshots_rolling_window.py --portfolios 3,5
    
    # Dry run (show what would be recreated)
    python scripts/recreate_snapshots_rolling_window.py --dry-run
"""

import sys
from pathlib import Path
from datetime import date, timedelta, datetime
from decimal import Decimal
import argparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from sqlalchemy.orm import Session
from models import Portfolio
from crud.crud_snapshot import create_daily_snapshot
from utils.logging_config import get_logger
from utils.app_config import load_app_config
from utils.portfolios_loader import get_portfolios_loader
from service.marketdata_service import sync_securities_prices

logger = get_logger(__name__)


def fetch_missing_historical_prices(db: Session, symbols: list, target_date: date) -> int:
    """
    Fetch missing historical prices for the given symbols and date.
    
    Uses yfinance to download historical prices for securities.
    
    Args:
        db: Database session
        symbols: List of symbols with missing prices
        target_date: Date to fetch prices for
    
    Returns:
        int: Number of prices successfully fetched
    """
    try:
        import yfinance as yf
        from crud.crud_market_fx import ingest_market_data_batch
        from crud.crud_symbol_mapping import load_symbol_mapping
        
        # Get symbol mapping for European ETFs
        symbol_mapping = load_symbol_mapping()
        
        fetched_count = 0
        prices_to_insert = []
        
        # Date range: target_date +/- 1 day (to ensure we get the price)
        start_date = target_date - timedelta(days=1)
        end_date = target_date + timedelta(days=1)
        
        for symbol in symbols:
            try:
                # Check if symbol needs mapping (e.g., VUAA -> VUAA.MI)
                yf_symbol = symbol
                currency = 'USD'  # Default assumption
                
                if symbol in symbol_mapping:
                    mapping = symbol_mapping[symbol]
                    yf_symbol = mapping.get('yfinance_symbol', symbol)
                    currency = mapping.get('currency', 'USD')
                
                logger.debug(f"Fetching {symbol} (yfinance: {yf_symbol}) for {target_date}")
                
                # Download price data
                ticker = yf.Ticker(yf_symbol)
                hist = ticker.history(start=start_date, end=end_date, auto_adjust=False)
                
                if not hist.empty:
                    for date_idx, row in hist.iterrows():
                        price_date = date_idx.date()
                        if price_date == target_date:
                            prices_to_insert.append({
                                'symbol': symbol,  # Use portfolio symbol, not yfinance symbol
                                'ts': datetime.combine(price_date, datetime.min.time()),
                                'price': float(row['Close']),
                                'currency': currency,  # Use mapped currency
                                'source': 'yfinance_backfill'
                            })
                            fetched_count += 1
                            logger.info(f"  📥 Fetched {symbol}: {currency} {row['Close']:.2f} on {price_date}")
                            break
                else:
                    logger.warning(f"  ⚠️ No data returned for {symbol} ({yf_symbol})")
                    
            except Exception as e:
                logger.warning(f"  ⚠️ Failed to fetch {symbol}: {e}")
                continue
        
        # Batch insert fetched prices
        if prices_to_insert:
            success, failed = ingest_market_data_batch(db, prices_to_insert)
            logger.info(f"  💾 Inserted {success} prices, {failed} failed")
            return success
        
        return 0
        
    except ImportError:
        logger.error("yfinance not installed - cannot fetch missing prices")
        return 0
    except Exception as e:
        logger.error(f"Error fetching historical prices: {e}")
        return 0


def get_automatic_portfolio_ids() -> list:
    """
    Get automatic portfolio IDs from portfolio_config.yaml.
    
    Returns portfolios where:
    - status = "active"
    - update_method = "automatic"
    """
    portfolios_loader = get_portfolios_loader()
    all_portfolios = portfolios_loader.get_portfolios()
    automatic_ids = [
        p['id'] for p in all_portfolios 
        if p.get('status') == 'active' and p.get('update_method') == 'automatic'
    ]
    logger.info(f"Loaded {len(automatic_ids)} automatic portfolios from config: {automatic_ids}")
    return automatic_ids


def get_portfolio_name(db, portfolio_id: int) -> str:
    """Get portfolio name"""
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    return portfolio.name if portfolio else f"Portfolio {portfolio_id}"


def recreate_snapshots_for_date(db, portfolio_id: int, snapshot_date: date, dry_run: bool = False) -> tuple[bool, list]:
    """
    Recreate snapshot for a specific portfolio and date.
    
    Uses lot-based historical calculation for accurate reconstruction of past positions.
    
    If missing prices are detected (and date != today), attempt to fetch them.
    
    Returns:
        tuple[bool, list]: (success, missing_prices)
    """
    try:
        if dry_run:
            logger.info(f"  [DRY RUN] Would recreate snapshot for {get_portfolio_name(db, portfolio_id)} on {snapshot_date}")
            return True, []
        
        # Create/recreate snapshot using historical lot-based calculation
        # This is more resource-intensive but provides accurate historical positions
        snapshot, missing_prices = create_daily_snapshot(
            db, portfolio_id, snapshot_date, 
            use_historical_calculation=True  # Force lot-based calculation for historical accuracy
        )
        
        if snapshot:
            if missing_prices and snapshot_date < date.today():
                # Missing prices detected for historical date - try to fetch them
                logger.warning(
                    f"  ⚠️ {snapshot_date}: Missing prices for {', '.join(missing_prices)} - attempting download..."
                )
                
                # Attempt to fetch missing prices
                fetched_count = fetch_missing_historical_prices(db, missing_prices, snapshot_date)
                
                if fetched_count > 0:
                    # Retry snapshot creation with newly fetched prices
                    logger.info(f"  📥 Fetched {fetched_count} prices, recreating snapshot...")
                    snapshot, missing_prices = create_daily_snapshot(
                        db, portfolio_id, snapshot_date,
                        use_historical_calculation=True  # Still use lot-based calculation
                    )
                    
                    if missing_prices:
                        logger.warning(
                            f"  ⚠️ {snapshot_date}: {get_portfolio_name(db, portfolio_id)} "
                            f"= {snapshot.total_value_base} EUR (still missing: {', '.join(missing_prices)})"
                        )
                    else:
                        logger.info(
                            f"  ✅ {snapshot_date}: {get_portfolio_name(db, portfolio_id)} "
                            f"= {snapshot.total_value_base} EUR (all prices fetched)"
                        )
                else:
                    logger.warning(
                        f"  ⚠️ {snapshot_date}: {get_portfolio_name(db, portfolio_id)} "
                        f"= {snapshot.total_value_base} EUR (⚠️ missing: {', '.join(missing_prices)})"
                    )
            elif missing_prices:
                # Today's date - don't fetch, prices may not be available yet
                logger.info(
                    f"  ✅ {snapshot_date}: {get_portfolio_name(db, portfolio_id)} "
                    f"= {snapshot.total_value_base} EUR (⚠️ missing: {', '.join(missing_prices)} - today, skipping fetch)"
                )
            else:
                logger.debug(
                    f"  ✅ {snapshot_date}: {get_portfolio_name(db, portfolio_id)} "
                    f"= {snapshot.total_value_base} EUR"
                )
            return True, missing_prices
        else:
            logger.warning(f"  ❌ Failed to create snapshot for {snapshot_date}")
            return False, []
            
    except Exception as e:
        logger.error(f"  ❌ Error creating snapshot for {snapshot_date}: {e}")
        return False, []


def recreate_rolling_window(
    start_date: date,
    end_date: date,
    portfolio_ids: list = None,
    dry_run: bool = False
) -> dict:
    """
    Recreate snapshots for automatic portfolios within date range.
    
    Args:
        start_date: Start of date range
        end_date: End of date range (inclusive)
        portfolio_ids: List of portfolio IDs to process (default: automatic portfolios from config)
        dry_run: If True, only show what would be done without making changes
    
    Returns:
        dict: Summary of operations
    """
    if portfolio_ids is None:
        portfolio_ids = get_automatic_portfolio_ids()
    
    db = SessionLocal()
    try:
        # Summary
        total_days = (end_date - start_date).days + 1
        total_operations = total_days * len(portfolio_ids)
        
        logger.info("=" * 80)
        logger.info("ROLLING WINDOW SNAPSHOT RECREATION")
        logger.info("=" * 80)
        logger.info(f"Date range: {start_date} to {end_date} ({total_days} days)")
        logger.info(f"Portfolios: {len(portfolio_ids)} automatic portfolios")
        logger.info(f"Total operations: {total_operations}")
        if dry_run:
            logger.info("⚠️  DRY RUN MODE - No changes will be made")
        logger.info("=" * 80)
        
        # Show portfolio names
        for portfolio_id in portfolio_ids:
            portfolio_name = get_portfolio_name(db, portfolio_id)
            logger.info(f"  • Portfolio {portfolio_id}: {portfolio_name}")
        
        logger.info("")
        
        # Process each date
        results = {
            'total_operations': total_operations,
            'successful': 0,
            'failed': 0,
            'missing_prices': set()
        }
        
        current_date = start_date
        while current_date <= end_date:
            logger.info(f"Processing {current_date}...")
            
            for portfolio_id in portfolio_ids:
                success = recreate_snapshots_for_date(db, portfolio_id, current_date, dry_run)
                
                if success:
                    results['successful'] += 1
                else:
                    results['failed'] += 1
            
            current_date += timedelta(days=1)
        
        # Final summary
        logger.info("")
        logger.info("=" * 80)
        logger.info("RECREATION COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Successful: {results['successful']}/{total_operations}")
        logger.info(f"Failed: {results['failed']}/{total_operations}")
        
        if results['missing_prices']:
            logger.info(f"Symbols with missing prices: {', '.join(sorted(results['missing_prices']))}")
        
        logger.info("=" * 80)
        
        return results
        
    except Exception as e:
        logger.error(f"Rolling window recreation failed: {e}", exc_info=True)
        return {'error': str(e)}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description='Recreate snapshots with rolling window strategy'
    )
    
    # Date range options
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to look back from today (default: 30)'
    )
    date_group.add_argument(
        '--start-date',
        type=str,
        help='Start date (YYYY-MM-DD) - requires --end-date'
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date (YYYY-MM-DD) - requires --start-date'
    )
    
    parser.add_argument(
        '--portfolios',
        type=str,
        help='Comma-separated portfolio IDs (default: 3,4,5,8)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    
    args = parser.parse_args()
    
    # Validate date arguments
    if args.start_date and not args.end_date:
        parser.error('--start-date requires --end-date')
    if args.end_date and not args.start_date:
        parser.error('--end-date requires --start-date')
    
    # Calculate date range
    if args.start_date:
        start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()
    else:
        end_date = date.today()
        start_date = end_date - timedelta(days=args.days - 1)
    
    # Validate date range
    if start_date > end_date:
        logger.error("Start date must be before or equal to end date")
        return 1
    
    if end_date > date.today():
        logger.error("End date cannot be in the future")
        return 1
    
    # Parse portfolio IDs
    if args.portfolios:
        portfolio_ids = [int(pid.strip()) for pid in args.portfolios.split(',')]
    else:
        # Load automatic portfolios from portfolio_config.yaml
        portfolio_ids = get_automatic_portfolio_ids()
    
    # Run recreation
    results = recreate_rolling_window(
        start_date=start_date,
        end_date=end_date,
        portfolio_ids=portfolio_ids,
        dry_run=args.dry_run
    )
    
    # Exit code based on results
    if 'error' in results:
        return 1
    elif results['failed'] > 0:
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
