"""
Backfill Historical Prices Using yfinance
==========================================
Uses yfinance historical data API to backfill accurate historical prices
instead of using current prices timestamped to past dates.

IMPORTANT: For weekends/holidays when markets are closed, automatically
forward-fills from the last available price to ensure complete data coverage.
This matches the behavior of daily jobs and prevents gaps in market data.

Usage:
    python scripts/backfill_historical_prices.py --days 7
    python scripts/backfill_historical_prices.py --start-date 2025-10-13 --end-date 2025-10-14
    python scripts/backfill_historical_prices.py --crypto-only
    python scripts/backfill_historical_prices.py --securities-only
"""

import argparse
import sys
from datetime import date, timedelta, datetime
from pathlib import Path
from decimal import Decimal
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yfinance as yf
from database import SessionLocal
from utils.logging_config import get_logger
from utils.app_config import load_app_config
from crud.crud_market_fx import ingest_market_prices
from service.marketdata_service import get_symbols_for_asset_class, get_symbols_excluding_asset_classes
from sqlalchemy import text

logger = get_logger(__name__)

# Load exclusion config
app_config = load_app_config()
PRICE_SYNC_CONFIG = app_config.get('price_sync', {})
EXCLUDE_SYMBOLS = set(PRICE_SYNC_CONFIG.get('exclude_symbols', []))


def backfill_crypto_prices_for_date(db, target_date: date) -> tuple[int, int]:
    """
    Backfill crypto prices using yfinance historical data.
    
    IMPORTANT: 
    - Only backfills prices for symbols with ACTIVE positions (quantity != 0).
    - For weekends/holidays, forward-fills from last known price for data completeness.
    
    Args:
        db: Database session
        target_date: Date to fetch historical prices for
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    logger.info(f"Backfilling crypto prices for {target_date}")
    
    # Get crypto symbols from database (only active positions with quantity != 0)
    crypto_symbols = get_symbols_for_asset_class(db, 'crypto')
    
    # Apply exclusions
    if EXCLUDE_SYMBOLS:
        crypto_symbols = crypto_symbols - EXCLUDE_SYMBOLS
        excluded = EXCLUDE_SYMBOLS & crypto_symbols
        if excluded:
            logger.info(f"Filtered out {len(excluded)} excluded crypto symbols: {excluded}")
    
    if not crypto_symbols:
        logger.warning("No crypto symbols to backfill")
        return 0, 0
    
    logger.info(f"Fetching historical prices for {len(crypto_symbols)} crypto symbols")
    
    success = 0
    failed = 0
    
    # Convert date to datetime for yfinance
    start_datetime = datetime.combine(target_date, datetime.min.time())
    end_datetime = start_datetime + timedelta(days=1)
    
    for symbol in crypto_symbols:
        try:
            # Construct yfinance ticker (crypto symbols need -USD suffix)
            ticker_symbol = f"{symbol}-USD"
            ticker = yf.Ticker(ticker_symbol)
            
            # Fetch historical data for the specific date
            hist = ticker.history(start=start_datetime, end=end_datetime, interval='1d')
            
            if hist.empty:
                # No trading data (weekend/holiday) - forward-fill from last known price
                logger.debug(f"No trading data for {ticker_symbol} on {target_date}, attempting forward-fill")
                from crud.crud_market_fx import get_latest_price
                last_price = get_latest_price(db, symbol, at_ts=target_date)
                if last_price:
                    price_data = {
                        'symbol': symbol,
                        'price': last_price.price,
                        'currency': 'USD',
                        'source': 'forward_fill',
                        'ts': start_datetime
                    }
                    ingest_result = ingest_market_prices(db, [price_data])
                    if ingest_result[0] > 0:
                        logger.debug(f"  ⏩ {symbol}: Forward-filled ${last_price.price:.2f}")
                        success += 1
                    else:
                        failed += 1
                else:
                    logger.debug(f"No previous price available for {symbol} to forward-fill")
                continue
            
            # Get closing price for the date
            close_price = hist['Close'].iloc[0]
            
            # Create market data record with historical timestamp
            price_data = {
                'symbol': symbol,
                'price': Decimal(str(close_price)),
                'currency': 'USD',
                'source': 'yfinance_historical',
                'ts': start_datetime  # Use historical timestamp
            }
            
            # Ingest into database
            ingest_result = ingest_market_prices(db, [price_data])
            if ingest_result[0] > 0:
                logger.info(f"  ✅ {symbol}: ${close_price:.2f}")
                success += 1
            else:
                logger.error(f"  ❌ {symbol}: Failed to ingest")
                failed += 1
                
        except Exception as e:
            logger.error(f"  ❌ {symbol}: {e}")
            failed += 1
    
    logger.info(f"Crypto prices backfill: {success} succeeded, {failed} failed")
    return success, failed


def backfill_securities_prices_for_date(db, target_date: date) -> tuple[int, int]:
    """
    Backfill securities prices using yfinance historical data.
    
    IMPORTANT: 
    - Only backfills prices for symbols with ACTIVE positions (quantity != 0).
    - For weekends/holidays, forward-fills from last known price for data completeness.
    
    Args:
        db: Database session
        target_date: Date to fetch historical prices for
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    logger.info(f"Backfilling securities prices for {target_date}")
    
    # Get securities symbols (excluding asset classes we don't track)
    # Only returns symbols with active positions (quantity != 0)
    securities_symbols = get_symbols_excluding_asset_classes(
        db,
        ['crypto', 'mutual_fund', 'cash', 'strategic_balancing_reserve']
    )
    
    if not securities_symbols:
        logger.info("No securities symbols to backfill")
        return 0, 0
    
    logger.info(f"Fetching historical prices for {len(securities_symbols)} securities")
    
    success = 0
    failed = 0
    
    # Convert date to datetime for yfinance
    start_datetime = datetime.combine(target_date, datetime.min.time())
    end_datetime = start_datetime + timedelta(days=1)
    
    for symbol in securities_symbols:
        try:
            ticker = yf.Ticker(symbol)
            
            # Fetch historical data for the specific date
            hist = ticker.history(start=start_datetime, end=end_datetime, interval='1d')
            
            if hist.empty:
                # No trading data (weekend/holiday) - forward-fill from last known price
                logger.debug(f"No trading data for {symbol} on {target_date}, attempting forward-fill")
                from crud.crud_market_fx import get_latest_price
                last_price = get_latest_price(db, symbol, at_ts=target_date)
                if last_price:
                    price_data = {
                        'symbol': symbol,
                        'price': last_price.price,
                        'currency': last_price.currency,
                        'source': 'forward_fill',
                        'ts': start_datetime
                    }
                    ingest_result = ingest_market_prices(db, [price_data])
                    if ingest_result[0] > 0:
                        logger.debug(f"  ⏩ {symbol}: Forward-filled {last_price.currency} {last_price.price:.2f}")
                        success += 1
                    else:
                        failed += 1
                else:
                    logger.debug(f"No previous price available for {symbol} to forward-fill")
                continue
            
            # Get closing price for the date
            close_price = hist['Close'].iloc[0]
            
            # Determine currency from ticker info
            try:
                ticker_info = ticker.info
                currency = ticker_info.get('currency', 'USD')
            except:
                currency = 'USD'
            
            # Create market data record with historical timestamp
            price_data = {
                'symbol': symbol,
                'price': Decimal(str(close_price)),
                'currency': currency,
                'source': 'yfinance_historical',
                'ts': start_datetime  # Use historical timestamp
            }
            
            # Ingest into database
            ingest_result = ingest_market_prices(db, [price_data])
            if ingest_result[0] > 0:
                logger.info(f"  ✅ {symbol}: ${close_price:.2f} {currency}")
                success += 1
            else:
                logger.error(f"  ❌ {symbol}: Failed to ingest")
                failed += 1
                
        except Exception as e:
            logger.error(f"  ❌ {symbol}: {e}")
            failed += 1
    
    logger.info(f"Securities prices backfill: {success} succeeded, {failed} failed")
    return success, failed


def main():
    parser = argparse.ArgumentParser(
        description='Backfill historical prices using yfinance'
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
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date (YYYY-MM-DD) - requires --start-date'
    )
    
    # Asset class filters
    parser.add_argument(
        '--crypto-only',
        action='store_true',
        help='Only backfill crypto prices'
    )
    parser.add_argument(
        '--securities-only',
        action='store_true',
        help='Only backfill securities prices'
    )
    
    args = parser.parse_args()
    
    # Validate date arguments
    if args.start_date and not args.end_date:
        parser.error('--start-date requires --end-date')
    if args.end_date and not args.start_date:
        parser.error('--end-date requires --start-date')
    
    # Calculate date range
    if args.days:
        end_date = date.today() - timedelta(days=1)  # Don't include today
        start_date = end_date - timedelta(days=args.days - 1)
    else:
        start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()
    
    # Validate date range
    if start_date > end_date:
        logger.error("Start date must be before or equal to end date")
        return 1
    
    if end_date >= date.today():
        logger.error("End date must be in the past (yfinance historical data only)")
        return 1
    
    logger.info("=" * 80)
    logger.info("BACKFILL HISTORICAL PRICES (yfinance)")
    logger.info("=" * 80)
    logger.info(f"Date range: {start_date} to {end_date}")
    logger.info(f"Total days: {(end_date - start_date).days + 1}")
    
    if args.crypto_only:
        logger.info("Mode: Crypto only")
    elif args.securities_only:
        logger.info("Mode: Securities only")
    else:
        logger.info("Mode: All assets (crypto + securities)")
    
    logger.info("=" * 80)
    
    db = SessionLocal()
    try:
        summary = {
            'total_dates': (end_date - start_date).days + 1,
            'crypto_success': 0,
            'crypto_failed': 0,
            'securities_success': 0,
            'securities_failed': 0
        }
        
        # Process each date in range
        current_date = start_date
        while current_date <= end_date:
            logger.info(f"\nProcessing {current_date}...")
            
            # Backfill crypto prices
            if not args.securities_only:
                crypto_success, crypto_failed = backfill_crypto_prices_for_date(db, current_date)
                summary['crypto_success'] += crypto_success
                summary['crypto_failed'] += crypto_failed
            
            # Backfill securities prices
            if not args.crypto_only:
                sec_success, sec_failed = backfill_securities_prices_for_date(db, current_date)
                summary['securities_success'] += sec_success
                summary['securities_failed'] += sec_failed
            
            current_date += timedelta(days=1)
        
        # Final summary
        logger.info("\n" + "=" * 80)
        logger.info("BACKFILL COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Dates processed: {summary['total_dates']}")
        
        if not args.securities_only:
            logger.info(
                f"Crypto prices: {summary['crypto_success']} succeeded, "
                f"{summary['crypto_failed']} failed"
            )
        
        if not args.crypto_only:
            logger.info(
                f"Securities prices: {summary['securities_success']} succeeded, "
                f"{summary['securities_failed']} failed"
            )
        
        total_success = summary['crypto_success'] + summary['securities_success']
        total_failed = summary['crypto_failed'] + summary['securities_failed']
        logger.info(f"Total: {total_success} succeeded, {total_failed} failed")
        logger.info("=" * 80)
        
        return 0
        
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
