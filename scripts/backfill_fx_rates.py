#!/usr/bin/env python3
"""
FX Rate Backfill Script - ECB Only (Tax Compliance)
===================================================

Backfills missing FX rates from ECB for German tax compliance.
CRITICAL: Uses ONLY actual ECB published rates (no forward-filling, no synthetic rates).

Tax Compliance:
- German law requires official ECB rates only
- NO forward-filling on holidays/weekends (gaps are legitimate)
- Only stores rates actually published by ECB
- For transactions on non-trading days, application uses fallback at transaction time

Required pairs (from config/app_config.yaml):
  - EUR/USD (ECB official rate: 1 EUR = X USD)
  - EUR/THB (ECB official rate: 1 EUR = X THB)

Usage:
    python scripts/backfill_fx_rates.py --days 30
    python scripts/backfill_fx_rates.py --start-date 2025-12-15 --end-date 2025-12-25
    python scripts/backfill_fx_rates.py --fill-all-gaps  # Complete historical backfill
    in docker: docker compose exec -T nicefolio_gui python3 scripts/backfill_fx_rates.py --start-date 2025-12-15 --end-date 2025-12-24
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from models import FxRate
from sqlalchemy import func, and_, cast, Date
from utils.logging_config import get_logger
from service.ecb_service import fetch_ecb_rates
from utils.app_config import load_app_config
from crud.crud_market_fx import ingest_fx_rates

logger = get_logger(__name__)

# Load FX pairs from config
app_config = load_app_config()
FX_PAIRS = app_config.get('fx_pairs', ['EUR/USD', 'EUR/THB'])


def get_missing_dates(db, start_date: date, end_date: date) -> Dict[str, List[date]]:
    """
    Find dates with missing FX rates for configured pairs.
    
    Returns:
        Dict with pair as key and list of missing dates as value
    """
    logger.info(f"Checking for gaps from {start_date} to {end_date}")
    
    missing_by_pair = {}
    
    for pair in FX_PAIRS:
        missing_dates = []
        current = start_date
        
        while current <= end_date:
            # Skip weekends (ECB doesn't publish on weekends)
            if current.weekday() < 5:  # Monday=0, Friday=4
                count = db.query(FxRate).filter(
                    and_(
                        FxRate.pair == pair,
                        cast(FxRate.as_of_date, Date) == current
                    )
                ).count()
                
                if count == 0:
                    missing_dates.append(current)
            
            current += timedelta(days=1)
        
        if missing_dates:
            missing_by_pair[pair] = missing_dates
            logger.info(f"{pair}: {len(missing_dates)} missing dates")
        else:
            logger.info(f"{pair}: ✅ No gaps")
    
    return missing_by_pair


def backfill_date(db, target_date: date) -> tuple[int, int]:
    """
    Backfill FX rates for a specific date using ECB.
    
    Args:
        db: Database session
        target_date: Date to backfill
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    logger.info(f"Backfilling FX rates for {target_date}")
    
    # Skip weekends
    if target_date.weekday() >= 5:
        logger.info(f"  Skipping {target_date} (weekend - ECB doesn't publish)")
        return 0, 0
    
    # Convert date to datetime for ECB API
    target_datetime = datetime.combine(target_date, datetime.min.time())
    
    try:
        # Fetch from ECB (with safety check disabled for historical dates)
        ecb_rates = fetch_ecb_rates(date=target_datetime, enforce_safety_check=False)
        
        if ecb_rates:
            # Ingest rates
            success, failed = ingest_fx_rates(db, ecb_rates)
            
            for rate in ecb_rates:
                if success > 0:
                    logger.info(f"  ✅ {rate['pair']}: {rate['rate']:.6f} (source: {rate['source']})")
            
            return success, failed
        else:
            # No ECB data available (holiday/weekend) - this is legitimate, don't fill
            logger.info(f"  ⏭️  No ECB data for {target_date} (holiday/weekend - gap is legitimate)")
            return 0, 0  # Not a failure - gaps are expected and correct
            
    except Exception as e:
        logger.error(f"  ❌ Error backfilling {target_date}: {e}", exc_info=True)
        return 0, 1

def main():
    parser = argparse.ArgumentParser(
        description='Backfill FX rates from ECB (tax compliance)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill last 30 days
  python scripts/backfill_fx_rates.py --days 30
  
  # Backfill specific date range
  python scripts/backfill_fx_rates.py --start-date 2025-12-15 --end-date 2025-12-25
  
  # Complete historical backfill (all gaps)
  python scripts/backfill_fx_rates.py --fill-all-gaps
        """
    )
    
    # Date range options
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        '--days',
        type=int,
        help='Number of days to look back from today'
    )
    date_group.add_argument(
        '--start-date',
        type=str,
        help='Start date (YYYY-MM-DD) - requires --end-date'
    )
    date_group.add_argument(
        '--fill-all-gaps',
        action='store_true',
        help='Fill all historical gaps (from first FX record to today)'
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date (YYYY-MM-DD) - requires --start-date'
    )
    
    args = parser.parse_args()
    
    # Validate date arguments
    if args.start_date and not args.end_date:
        parser.error('--start-date requires --end-date')
    if args.end_date and not args.start_date:
        parser.error('--end-date requires --start-date')
    
    db = SessionLocal()
    
    try:
        logger.info("="*80)
        logger.info("FX RATE BACKFILL - ECB ONLY (Tax Compliance)")
        logger.info("="*80)
        logger.info(f"Configured FX pairs: {FX_PAIRS}")
        logger.info(f"Source: ECB (European Central Bank) exclusively")
        
        # Calculate date range
        if args.days:
            end_date = date.today()
            start_date = end_date - timedelta(days=args.days)
        elif args.fill_all_gaps:
            # Find earliest FX rate
            earliest = db.query(func.min(FxRate.as_of_date)).scalar()
            if earliest:
                start_date = earliest.date()
            else:
                start_date = date(2020, 1, 1)  # Fallback
            end_date = date.today()
            logger.info(f"Complete historical backfill requested")
        else:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()
        
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info("="*80)
        
        # Find gaps
        missing_by_pair = get_missing_dates(db, start_date, end_date)
        
        if not missing_by_pair:
            logger.info("✅ No gaps found - FX rates are complete!")
            return 0
        
        # Calculate total missing
        total_missing = sum(len(dates) for dates in missing_by_pair.values())
        logger.info(f"\nTotal missing dates across all pairs: {total_missing}")
        
        # Collect unique dates to backfill
        all_missing_dates = set()
        for dates in missing_by_pair.values():
            all_missing_dates.update(dates)
        all_missing_dates = sorted(all_missing_dates)
        
        logger.info(f"Unique dates to backfill: {len(all_missing_dates)}")
        logger.info("="*80)
        
        # Backfill each date
        total_success = 0
        total_failed = 0
        
        for i, missing_date in enumerate(all_missing_dates, 1):
            logger.info(f"\n[{i}/{len(all_missing_dates)}] Backfilling {missing_date}")
            success, failed = backfill_date(db, missing_date)
            total_success += success
            total_failed += failed
            
            # Small delay to be nice to ECB API
            if i < len(all_missing_dates):
                import time
                time.sleep(0.5)
        
        # Summary
        logger.info("\n" + "="*80)
        logger.info("BACKFILL COMPLETE")
        logger.info("="*80)
        logger.info(f"Total rates inserted: {total_success}")
        logger.info(f"Total failures: {total_failed}")
        
        # Verify no gaps remain
        logger.info("\n" + "="*80)
        logger.info("VERIFICATION")
        logger.info("="*80)
        remaining_gaps = get_missing_dates(db, start_date, end_date)
        
        if not remaining_gaps:
            logger.info("✅ Verification passed - no gaps remain!")
        else:
            logger.warning("⚠️ Some gaps still exist:")
            for pair, dates in remaining_gaps.items():
                logger.warning(f"  {pair}: {len(dates)} gaps remaining")
        
        return 0
        
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1
    
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
