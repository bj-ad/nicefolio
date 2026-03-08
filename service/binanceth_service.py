"""
Binance.th API Service
Provides low-level API access functions for Binance.th exchange.

Core Functions:
- fetch_account_balances() - Get current balances
- fetch_deposits() - Get deposit history
- fetch_withdrawals() - Get withdrawal history

Note: Trade history is NOT available for auto-convert/easy buy-sell transactions.
Use balance-based trade inference instead (see binanceth_balance_sync_service.py).
"""

import os
import time
import hmac
import hashlib
from urllib.parse import urlencode
from dotenv import load_dotenv
from typing import List, Dict, Optional

from utils.logging_config import get_logger
from utils.api_client import make_api_call

logger = get_logger(__name__)

# Load environment variables
load_dotenv()
API_KEY = os.getenv("BINANCETH_API_KEY")
API_SECRET = os.getenv("BINANCETH_API_SECRET")
API_URL = "https://api.binance.th"

def _make_signed_request(method: str, path: str, params: dict = None) -> Optional[Dict]:
    """
    Make a signed request to Binance.th API.
    
    Args:
        method: HTTP method (GET, POST, DELETE)
        path: API endpoint path
        params: Query parameters
    
    Returns:
        Response data or None on error
    """
    if not API_KEY or not API_SECRET:
        logger.error("Binance.th API credentials not set")
        return None
    
    if params is None:
        params = {}
    
    # Add timestamp and signature
    timestamp = int(time.time() * 1000)
    params["timestamp"] = timestamp
    query_string = urlencode(params)
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f"{API_URL}{path}"
    
    return make_api_call(url, method=method.upper(), params=params, headers=headers)

def fetch_account_balances() -> List[Dict]:
    """
    Fetch current account balances from Binance.th.
    
    Returns:
        list: List of balance dicts with keys:
            - asset: str (e.g., 'BTC', 'ETH', 'THB')
            - free: str (available balance)
            - locked: str (locked in orders)
    
    Example:
        >>> balances = fetch_account_balances()
        >>> [{'asset': 'BTC', 'free': '0.001', 'locked': '0'}, ...]
    """
    path = "/api/v1/account"
    params = {}
    
    logger.info("Fetching account balances from Binance.th...")
    response = _make_signed_request("GET", path, params)
    
    if not response:
        logger.warning("No response from balance API")
        return []
    
    if 'balances' not in response:
        logger.warning("No 'balances' key in API response")
        return []
    
    balances = response['balances']
    # Filter to non-zero balances
    non_zero = [
        b for b in balances
        if float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0
    ]
    
    logger.info(f"Fetched {len(non_zero)} non-zero balances")
    return non_zero


def fetch_deposits(start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 1000) -> List[Dict]:
    """
    Fetch deposit history from Binance.th.
    
    Note: THB deposits do NOT appear in this history. Only crypto deposits.
    
    Args:
        start_time: Start timestamp in milliseconds (optional)
        end_time: End timestamp in milliseconds (optional)
        limit: Maximum number of records (default: 1000)
    
    Returns:
        list: List of deposit dicts with keys:
            - coin: str (asset symbol)
            - amount: str (deposit amount)
            - insertTime: int (timestamp in milliseconds)
            - status: int (0=pending, 1=success, etc.)
            - network: str (deposit network)
    
    Example:
        >>> deposits = fetch_deposits(start_time=1696000000000)
        >>> [{'coin': 'BTC', 'amount': '0.01', 'insertTime': 1696000000000, ...}, ...]
    """
    path = "/api/v1/capital/deposit/history"
    params = {"limit": limit}
    
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    
    logger.info(f"Fetching deposit history (limit: {limit})...")
    deposits = _make_signed_request("GET", path, params)
    
    if not deposits:
        logger.info("No deposits found")
        return []
    
    logger.info(f"Fetched {len(deposits)} deposits")
    return deposits


def fetch_withdrawals(start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 1000) -> List[Dict]:
    """
    Fetch withdrawal history from Binance.th.
    
    Note: THB withdrawals do NOT appear in this history. Only crypto withdrawals.
    
    Args:
        start_time: Start timestamp in milliseconds (optional)
        end_time: End timestamp in milliseconds (optional)
        limit: Maximum number of records (default: 1000)
    
    Returns:
        list: List of withdrawal dicts with keys:
            - coin: str (asset symbol)
            - amount: str (withdrawal amount)
            - applyTime: int (timestamp in milliseconds)
            - status: int (0=pending, 1=success, etc.)
            - network: str (withdrawal network)
            - transactionFee: str (network fee)
    
    Example:
        >>> withdrawals = fetch_withdrawals(start_time=1696000000000)
        >>> [{'coin': 'BTC', 'amount': '0.01', 'applyTime': 1696000000000, ...}, ...]
    """
    path = "/api/v1/capital/withdraw/history"
    params = {"limit": limit}
    
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    
    logger.info(f"Fetching withdrawal history (limit: {limit})...")
    withdrawals = _make_signed_request("GET", path, params)
    
    if not withdrawals:
        logger.info("No withdrawals found")
        return []
    
    logger.info(f"Fetched {len(withdrawals)} withdrawals")
    return withdrawals


# Note: Removed fetch_my_trades() and run_binanceth_ingestion_flow()
# Reason: Trading pairs API does not include auto-convert/easy buy-sell transactions
# Solution: Use balance-based trade inference (binanceth_balance_sync_service.py)
