import os
import time
import hmac
import hashlib
from urllib.parse import urlencode
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import SessionLocal
from crud.crud_binancecom import (
    ingest_transactions_from_binancecom_trades,
    ingest_transactions_from_binancecom_deposits,
    ingest_transactions_from_binancecom_withdrawals,
)
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import load_app_config
from utils.api_client import make_api_call

logger = get_logger("binancecom_service")

# Load environment variables
load_dotenv()
API_KEY = os.getenv("BINANCECOM_API_KEY")
API_SECRET = os.getenv("BINANCECOM_API_SECRET")
API_URL = "https://api.binance.com"

def _make_signed_request(method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """
    Prepares a signed request and uses the generic client to execute it.
    
    Returns:
        Response data or None if credentials not configured
    """
    if not API_KEY or not API_SECRET:
        logger.warning("Binance.com API credentials not set")
        return None
    
    if params is None:
        params = {}
        
    timestamp = int(time.time() * 1000)
    params["timestamp"] = timestamp
    query_string = urlencode(params)
    signature = hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    params["signature"] = signature
    
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f"{API_URL}{path}"
    
    return make_api_call(url, method=method.upper(), params=params, headers=headers)

def fetch_my_trades(symbol: str, start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    """
    Fetches trades for a specific symbol using /api/v3/myTrades.
    
    This endpoint is used (not allOrders) because:
    - Includes fee information (commission, commissionAsset)
    - Provides individual trade fills (most granular)
    - Required for accurate portfolio tracking
    
    Note: Binance API has 24-hour limit for time window between startTime and endTime.
    For longer periods, use fetch_my_trades_chunked() instead.
    """
    path = "/api/v3/myTrades"
    params = {"symbol": symbol, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
        
    logger.info(f"Fetching trades for {symbol}...")
    trades = _make_signed_request("GET", path, params)
    if trades:
        logger.info(f"Fetched {len(trades)} trades for {symbol}.")
        return trades
    return []

def fetch_my_trades_chunked(symbol: str, days_back: int = 3, limit: int = 1000) -> List[Dict[str, Any]]:
    """
    Fetch trades with automatic 24-hour chunking to work around API limitation.
    
    Strategy:
    - Chunks time period into 23-hour windows (safe margin)
    - Handles overlaps gracefully (CRUD layer uses external_id for deduplication)
    - Recommended: days_back=3 for daily jobs (covers missed runs + buffer)
    
    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        days_back: Number of days to look back (default 3 for safety)
        limit: Max trades per request (default 1000)
    
    Returns:
        List of all trades within the time period
        
    Note on Deduplication:
    - Database uses external_id (trade ID) as unique constraint
    - Overlapping fetches are safe - duplicates are automatically handled
    - This ensures no data loss even if jobs fail or run late
    """
    logger.info(f"Fetching trades for {symbol} (last {days_back} days with 23-hour chunks)...")
    
    all_trades = []
    end_date = now_utc()
    start_date = end_date - timedelta(days=days_back)
    
    # Chunk into 23-hour periods (safe margin from 24-hour limit)
    chunk_hours = 23
    current_date = start_date
    
    while current_date < end_date:
        chunk_start = int(current_date.timestamp() * 1000)
        chunk_end = int((current_date + timedelta(hours=chunk_hours)).timestamp() * 1000)
        
        # Don't exceed end_date
        if chunk_end > int(end_date.timestamp() * 1000):
            chunk_end = int(end_date.timestamp() * 1000)
        
        try:
            trades = fetch_my_trades(
                symbol=symbol, 
                start_time=chunk_start, 
                end_time=chunk_end,
                limit=limit
            )
            
            if trades:
                all_trades.extend(trades)
                logger.debug(f"Chunk {current_date.date()}: {len(trades)} trades")
        except Exception as e:
            logger.warning(f"Failed to fetch chunk starting {current_date.date()}: {e}")
            # Continue with next chunk even if one fails
        
        current_date += timedelta(hours=chunk_hours)
    
    logger.info(f"Total trades fetched for {symbol}: {len(all_trades)}")
    return all_trades

def fetch_deposits(start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    """Fetches deposit history."""
    path = "/sapi/v1/capital/deposit/hisrec"
    params = {"limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time

    logger.info("Fetching deposit history...")
    deposits = _make_signed_request("GET", path, params)
    if deposits:
        logger.info(f"Fetched {len(deposits)} deposits.")
        return deposits
    return []

def fetch_withdrawals(start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    """Fetches withdrawal history."""
    path = "/sapi/v1/capital/withdraw/history"
    params = {"limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time

    logger.info("Fetching withdrawal history...")
    withdrawals = _make_signed_request("GET", path, params)
    if withdrawals:
        logger.info(f"Fetched {len(withdrawals)} withdrawals.")
        return withdrawals
    return []

def run_binancecom_ingestion_flow(from_date: Optional[date] = None, to_date: Optional[date] = None):
    """
    Orchestrates the full workflow of fetching all relevant Binance.com
    data and ingesting it into the database.
    
    Strategy for handling overlaps and missed runs:
    - Uses configurable lookback period (default 7 days from app_config.yaml)
    - Database deduplication via external_id prevents duplicate entries
    - Safe to run multiple times - idempotent operation
    
    Trade Fetching:
    - Uses /api/v3/myTrades (not allOrders) for fee information
    - Automatic 23-hour chunking to bypass API limitation
    - Handles symbol-specific failures gracefully
    
    Deposits/Withdrawals:
    - Direct API calls (no chunking needed - different endpoint limits)
    - Deduplication via external_id (txId)
    """
    # Load configuration
    app_config = load_app_config()
    binancecom_config = app_config.get('binancecom', {})
    default_lookback = binancecom_config.get('sync_lookback_days', 7)
    
    # Determine lookback period
    if from_date and to_date:
        days_back = (to_date - from_date).days + 1
        logger.info(f"Starting Binance.com ingestion flow for period {from_date} to {to_date} ({days_back} days)")
    else:
        days_back = default_lookback
        logger.info(f"Starting Binance.com ingestion flow with {days_back}-day lookback (from config)")
    
    # Early validation: Check API credentials before attempting any operations
    if not API_KEY or not API_SECRET:
        logger.warning("Binance.com API credentials not configured. Skipping sync.")
        return
    
    # Calculate time range for deposits/withdrawals (they don't have 24-hour limit)
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    if from_date:
        start_time = int(datetime.combine(from_date, datetime.min.time()).timestamp() * 1000)
    else:
        start_time = int((now_utc() - timedelta(days=days_back)).timestamp() * 1000)
    
    if to_date:
        end_time = int(datetime.combine(to_date, datetime.max.time()).timestamp() * 1000)
    else:
        end_time = int(now_utc().timestamp() * 1000)

    trading_symbols = app_config.get("trading_symbols", [])
    trading_base_currency = app_config.get("trading_base_currency")
    
    all_trades = []
    
    # Fetch trades using chunked approach (handles 24-hour API limitation)
    logger.info(f"Fetching trades for {len(trading_symbols)} symbols with {days_back}-day lookback...")
    for symbol in trading_symbols:
        trade_symbol = f"{symbol}{trading_base_currency}"
        try:
            trades = fetch_my_trades_chunked(symbol=trade_symbol, days_back=days_back)
            if trades:
                all_trades.extend(trades)
                logger.info(f"  {trade_symbol}: {len(trades)} trades")
        except Exception as e:
            if "symbol does not exist" not in str(e).lower():
                 logger.warning(f"Could not fetch trades for symbol {trade_symbol}: {e}")
            else:
                 logger.debug(f"Symbol {trade_symbol} does not exist, skipping.")
            continue
    
    # Fetch deposits and withdrawals (no chunking needed)
    logger.info("Fetching deposits and withdrawals...")
    all_deposits = fetch_deposits(start_time=start_time, end_time=end_time)
    all_withdrawals = fetch_withdrawals(start_time=start_time, end_time=end_time)

    # Ingest into database
    db = SessionLocal()
    try:
        if all_trades:
            logger.info(f"Ingesting {len(all_trades)} trades into database")
            ingest_transactions_from_binancecom_trades(db, all_trades)
        else:
            logger.info("No trades to ingest")
            
        if all_deposits:
            logger.info(f"Ingesting {len(all_deposits)} deposits into database")
            ingest_transactions_from_binancecom_deposits(db, all_deposits)
        else:
            logger.info("No deposits to ingest")
            
        if all_withdrawals:
            logger.info(f"Ingesting {len(all_withdrawals)} withdrawals into database")
            ingest_transactions_from_binancecom_withdrawals(db, all_withdrawals)
        else:
            logger.info("No withdrawals to ingest")
        
        logger.info("Binance.com ingestion flow completed successfully.")
        
        # Calculate stats for notification
        total_new = len(all_trades) + len(all_deposits) + len(all_withdrawals)
        types = {}
        if len(all_trades) > 0:
            types['trade'] = len(all_trades)
        if len(all_deposits) > 0:
            types['deposit'] = len(all_deposits)
        if len(all_withdrawals) > 0:
            types['withdrawal'] = len(all_withdrawals)
        
        return {
            'new': total_new,
            'types': types,
            'failed': 0
        }
        
    except Exception as e:
        logger.error(f"An error occurred during the Binance.com ingestion flow: {e}", exc_info=True)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    # Default: Uses sync_lookback_days from app_config.yaml (default 7 days)
    run_binancecom_ingestion_flow()
