"""
Backfill Benchmark Prices
=========================
Fetches historical prices for benchmark symbols from yfinance.
Runs on container startup to ensure benchmark data is available from
the first portfolio snapshot date.

Usage:
    python scripts/backfill_benchmark_prices.py
    python scripts/backfill_benchmark_prices.py --start-date 2024-01-01
    python scripts/backfill_benchmark_prices.py --days 30
"""

import argparse
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from service.benchmark_service import (
    get_benchmark_symbols,
    backfill_benchmark_prices,
    get_first_portfolio_snapshot_date
)
from utils.logging_config import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Backfill historical prices for benchmark symbols'
    )
    
    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date (YYYY-MM-DD). Defaults to first portfolio snapshot date.'
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date (YYYY-MM-DD). Defaults to yesterday.'
    )
    
    parser.add_argument(
        '--days',
        type=int,
        help='Number of days to look back from today (alternative to --start-date)'
    )
    
    args = parser.parse_args()
    
    # Get benchmark symbols
    symbols = get_benchmark_symbols()
    if not symbols:
        logger.info("No benchmark symbols configured in app_config.yaml")
        return 0
    
    # Determine date range
    if args.days:
        end_date = date.today() - timedelta(days=1)  # Exclude today
        start_date = date.today() - timedelta(days=args.days)
    elif args.start_date:
        start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        if args.end_date:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()
        else:
            end_date = date.today() - timedelta(days=1)
    else:
        # Default: from first portfolio snapshot to yesterday
        start_date = get_first_portfolio_snapshot_date()
        end_date = date.today() - timedelta(days=1)
        
        if start_date is None:
            logger.info("No portfolio snapshots found, nothing to backfill")
            return 0
    
    logger.info("=" * 80)
    logger.info("BACKFILL BENCHMARK PRICES")
    logger.info("=" * 80)
    logger.info(f"Benchmark symbols: {symbols}")
    logger.info(f"Date range: {start_date} to {end_date}")
    logger.info(f"Total days: {(end_date - start_date).days + 1}")
    logger.info("=" * 80)
    
    # Run backfill
    success, failed = backfill_benchmark_prices(start_date=start_date, end_date=end_date)
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Success: {success}, Failed: {failed}")
    logger.info("=" * 80)
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
