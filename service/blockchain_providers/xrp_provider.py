import json
from utils.api_client import make_api_call
from utils.logging_config import get_logger
from typing import List, Dict, Union

logger = get_logger(__name__)

# Cache configuration

# XRP network parameters
XRP_DROPS_PER_XRP = 1e6  # 1 XRP = 1,000,000 drops
XRP_BASE_RESERVE = 10.0  # Current base reserve (changes with network amendments)
XRP_OWNER_RESERVE = 2.0  # Reserve per owned object
XRP_DEFAULT_FEE = 0.00001  # Default transaction fee in XRP

# XRPL public servers (with failover)
XRPL_SERVERS = [
    "https://xrplcluster.com",
    "https://s1.ripple.com:51234",
    "https://s2.ripple.com:51234"
]

def get_balance(address: str) -> dict | None:
    """
    Fetches comprehensive XRP balance including available, reserved, and total amounts.
    
    Args:
        address (str): XRP wallet address
        
    Returns:
        dict: Contains total, available, reserved balances and account info
    """
    account_info = _fetch_account_info(address)
    
    if not account_info:
        return None
    
    account_data = account_info.get("account_data", {})
    
    # Get balance in drops and convert to XRP
    balance_drops = int(account_data.get("Balance", 0))
    total_balance = balance_drops / XRP_DROPS_PER_XRP
    
    # Calculate reserve requirements
    reserve_info = _calculate_reserve_requirements(account_data)
    
    # Available balance = total - reserve
    available_balance = max(0, total_balance - reserve_info["total_reserve"])
    
    return {
        "total": total_balance,
        "available": available_balance,
        "reserved": reserve_info["total_reserve"],
        "reserve_breakdown": reserve_info,
        "account_exists": True,
        "sequence": account_data.get("Sequence", 0)
    }


def _fetch_account_info(address: str) -> dict | None:
    """Fetch account info from XRPL with failover."""
    headers = {"Content-Type": "application/json"}
    payload = {
        "method": "account_info",
        "params": [{
            "account": address,
            "ledger_index": "validated"
        }]
    }
    
    # Try multiple servers for reliability
    for server_url in XRPL_SERVERS:
        try:
            response = make_api_call(server_url, method="POST", headers=headers, 
                                   data=json.dumps(payload), retries=2, delay=1, timeout=10)
            
            if response and response.get("result", {}).get("account_data"):
                logger.info(f"Fetched XRP account info for {address} from {server_url}")
                return response.get("result", {})
                
        except Exception as e:
            logger.warning(f"Failed to fetch from {server_url}: {e}")
            continue
    
    logger.error(f"Failed to fetch XRP account info for {address} from all servers")
    return None


def _calculate_reserve_requirements(account_data: dict) -> dict:
    """
    Calculate XRP reserve requirements based on account objects.
    
    The XRP Ledger requires accounts to maintain a reserve:
    - Base reserve (currently ~10 XRP) for the account itself
    - Owner reserve (currently ~2 XRP) for each object owned (trust lines, offers, etc.)
    """
    base_reserve = XRP_BASE_RESERVE
    owner_count = account_data.get("OwnerCount", 0)
    owner_reserve = owner_count * XRP_OWNER_RESERVE
    total_reserve = base_reserve + owner_reserve
    
    return {
        "base_reserve": base_reserve,
        "owner_count": owner_count,
        "owner_reserve": owner_reserve,
        "total_reserve": total_reserve,
        "note": "Reserve amounts are locked and not spendable"
    }

# Cache for transaction data
def get_transactions(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Fetches transaction history for a given XRP address including account reserve handling.
    
    Args:
        address (str): XRP wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions to fetch (default: 50)
    
    Returns:
        list: List of normalized transaction dictionaries ready for Transaction model
    """
    logger.info(f"Fetching XRP transactions for address {address}")
    
    # Get transaction history
    transactions = _fetch_xrp_transactions(address, limit)
    
    # Filter by date range if provided
    if start_date or end_date:
        transactions = _filter_transactions_by_date(transactions, start_date, end_date)
    
    # Normalize transactions for the Transaction model
    normalized_transactions = []
    
    # Check if this is the first transaction (account creation) and handle reserve
    is_first_transaction = _is_account_creation_sequence(transactions, address)
    
    for i, tx in enumerate(transactions):
        normalized_tx = _normalize_xrp_transaction(tx, address, is_first_transaction and i == len(transactions) - 1)
        if normalized_tx:
            normalized_transactions.extend(normalized_tx)
    
    # Sort by timestamp (newest first)
    normalized_transactions.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_transactions)} normalized XRP transactions for {address}")
    return normalized_transactions


def _fetch_xrp_transactions(address: str, limit: int = 50) -> list:
    """Fetch XRP transaction history."""
    headers = {"Content-Type": "application/json"}
    payload = {
        "method": "account_tx",
        "params": [{
            "account": address,
            "ledger_index_min": -1,
            "ledger_index_max": -1,
            "limit": min(limit, 400),  # XRPL limit
            "forward": False  # Most recent first
        }]
    }
    
    # Try multiple servers for reliability
    for server_url in XRPL_SERVERS:
        try:
            response = make_api_call(server_url, method="POST", headers=headers,
                                   data=json.dumps(payload), retries=3, delay=2, timeout=15)
            
            if response and response.get("result", {}).get("transactions"):
                transactions = response["result"]["transactions"]
                logger.info(f"Fetched {len(transactions)} XRP transactions for {address}")
                return transactions[:limit]
                
        except Exception as e:
            logger.warning(f"Failed to fetch transactions from {server_url}: {e}")
            continue
    
    logger.error(f"Failed to fetch XRP transactions for {address} from all servers")
    return []


def _is_account_creation_sequence(transactions: list, address: str) -> bool:
    """Check if the transaction list includes the account creation transaction."""
    if not transactions:
        return False
    
    # Look for the first transaction where this address appears as destination
    # and has a low sequence number (typically 0 or 1)
    for tx in reversed(transactions):  # Check oldest first
        tx_data = tx.get("tx", {})
        meta = tx.get("meta", {})
        
        destination = tx_data.get("Destination")
        sequence = tx_data.get("Sequence", 0)
        
        # This is likely account creation if:
        # 1. Address is the destination
        # 2. Low sequence number
        # 3. Transaction was successful
        if (destination == address and 
            sequence <= 2 and 
            meta.get("TransactionResult") == "tesSUCCESS"):
            return True
    
    return False


def _filter_transactions_by_date(transactions: list, start_date: str = None, end_date: str = None) -> list:
    """Filter transactions by date range."""
    if not start_date and not end_date:
        return transactions
    
    from datetime import datetime, timezone
    
    # XRPL epoch starts January 1, 2000 (00:00 UTC)
    RIPPLE_EPOCH_OFFSET = 946684800
    
    filtered = []
    for tx in transactions:
        close_time = tx.get("tx", {}).get("date")
        if not close_time:
            continue
            
        # Convert Ripple timestamp to Unix timestamp
        unix_timestamp = close_time + RIPPLE_EPOCH_OFFSET
        tx_date = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).date()
        
        # Apply date filters
        if start_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            if tx_date < start_dt:
                continue
                
        if end_date:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            if tx_date > end_dt:
                continue
                
        filtered.append(tx)
    
    return filtered


def _normalize_xrp_transaction(tx_data: dict, wallet_address: str, is_account_creation: bool = False) -> list:
    """
    Normalize an XRP transaction into Transaction model format.
    Handles account reserve requirements for first transactions.
    
    Args:
        tx_data (dict): Raw transaction data from XRPL
        wallet_address (str): The wallet address we're tracking
        is_account_creation (bool): Whether this is the account creation transaction
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        tx = tx_data.get("tx", {})
        meta = tx_data.get("meta", {})
        
        tx_hash = tx.get("hash")
        tx_type = tx.get("TransactionType")
        close_time = tx.get("date")
        fee_drops = int(tx.get("Fee", 0))
        
        if not tx_hash or not close_time:
            logger.warning(f"Missing required transaction data: hash={tx_hash}, date={close_time}")
            return []
        
        # Convert Ripple timestamp to Unix timestamp
        RIPPLE_EPOCH_OFFSET = 946684800
        unix_timestamp = close_time + RIPPLE_EPOCH_OFFSET
        occurred_at = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
        fee_xrp = fee_drops / XRP_DROPS_PER_XRP
        
        # Only process successful transactions
        if meta.get("TransactionResult") != "tesSUCCESS":
            return []
        
        # Handle different transaction types
        if tx_type == "Payment":
            return _normalize_xrp_payment(tx, meta, wallet_address, occurred_at, tx_hash, fee_xrp, is_account_creation)
        elif tx_type in ["TrustSet", "OfferCreate", "OfferCancel"]:
            # These transactions affect reserves but don't transfer XRP
            return _normalize_xrp_reserve_transaction(tx, meta, wallet_address, occurred_at, tx_hash, fee_xrp)
        else:
            # Generic transaction with just fee
            return _normalize_xrp_fee_transaction(tx, meta, wallet_address, occurred_at, tx_hash, fee_xrp)
            
    except Exception as e:
        logger.error(f"Error normalizing XRP transaction {tx_data.get('tx', {}).get('hash', 'unknown')}: {e}")
        return []


def _normalize_xrp_payment(tx: dict, meta: dict, wallet_address: str, occurred_at, tx_hash: str, fee_xrp: float, is_account_creation: bool) -> list:
    """Normalize an XRP payment transaction."""
    transactions = []
    
    account = tx.get("Account")
    destination = tx.get("Destination")
    amount = tx.get("Amount")
    
    # Only handle XRP payments (not IOUs)
    if not isinstance(amount, str):
        return []
    
    amount_drops = int(amount)
    amount_xrp = amount_drops / XRP_DROPS_PER_XRP
    
    # Spam threshold: Transactions of 0.000001 XRP or less are marked as spam
    # These are typically dust attacks or spam transactions
    SPAM_THRESHOLD = 0.000001
    
    if destination == wallet_address and account != wallet_address:
        # Incoming payment - check if it's spam
        is_spam = amount_xrp <= SPAM_THRESHOLD
        
        transactions.append({
            'type': 'spam' if is_spam else 'transfer_in',
            'symbol': 'XRP',
            'symbol_normalized': 'XRP',
            'quantity': amount_xrp,
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,  # Receiver doesn't pay fee
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'xrp_blockchain',
            'asset_class': 'crypto',
            'external_id': f"xrp_{tx_hash}_in",
            'notes': f"XRP {'spam/dust transaction' if is_spam else f'received from {account}'}"
        })
        
        # SPECIAL HANDLING: If this is account creation, add reserve fee record
        if is_account_creation and amount_xrp >= XRP_BASE_RESERVE:
            transactions.append({
                'type': 'fee',
                'symbol': 'XRP',
                'symbol_normalized': 'XRP',
                'quantity': -XRP_BASE_RESERVE,  # NEGATIVE - reserve is locked (outflow)
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'xrp_blockchain',
                'asset_class': 'crypto',
                'external_id': f"xrp_{tx_hash}_reserve",
                'notes': f"XRP account reserve fee (locked, not spendable) - {XRP_BASE_RESERVE} XRP reserved for account maintenance"
            })
        
    elif account == wallet_address and destination != wallet_address:
        # Outgoing payment
        transactions.append({
            'type': 'transfer_out',
            'symbol': 'XRP',
            'symbol_normalized': 'XRP',
            'quantity': -amount_xrp,  # NEGATIVE - transfer out is an outflow
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,  # Fee has its own separate transaction
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'xrp_blockchain',
            'asset_class': 'crypto',
            'external_id': f"xrp_{tx_hash}_out",
            'notes': f"XRP sent to {destination}"
        })
    
    # Always create fee record if there was a transaction fee and this address paid it
    if fee_xrp > 0 and account == wallet_address:
        # Determine transaction context for fee notes
        if account == wallet_address and destination != wallet_address:
            fee_context = "transfer_out"
        elif destination == wallet_address:
            fee_context = "transfer_in"
        else:
            fee_context = "transaction"
        
        transactions.append({
            'type': 'fee',
            'symbol': 'XRP',
            'symbol_normalized': 'XRP',
            'quantity': -fee_xrp,  # NEGATIVE - fee is an outflow/consumption of assets
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'xrp_blockchain',
            'asset_class': 'crypto',
            'external_id': f"xrp_{tx_hash}_fee",
            'notes': f"XRP network fee ({fee_context}) | tx: {tx_hash}"
        })
    
    return transactions


def _normalize_xrp_reserve_transaction(tx: dict, meta: dict, wallet_address: str, occurred_at, tx_hash: str, fee_xrp: float) -> list:
    """Normalize XRP transactions that affect reserves (TrustSet, Offers, etc.)."""
    transactions = []
    
    account = tx.get("Account")
    tx_type = tx.get("TransactionType")
    
    # Only process if this address initiated the transaction
    if account != wallet_address:
        return []
    
    # Create fee record
    if fee_xrp > 0:
        transactions.append({
            'type': 'fee',
            'symbol': 'XRP',
            'symbol_normalized': 'XRP',
            'quantity': -fee_xrp,  # NEGATIVE - fee is an outflow/consumption of assets
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'xrp_blockchain',
            'asset_class': 'crypto',
            'external_id': f"xrp_{tx_hash}_fee",
            'notes': f"XRP network fee ({tx_type.lower()}) | tx: {tx_hash}"
        })
    
    # Add reserve impact note if creating new objects
    if tx_type == "TrustSet" and tx.get("LimitAmount", {}).get("value", "0") != "0":
        transactions.append({
            'type': 'fee',
            'symbol': 'XRP', 
            'symbol_normalized': 'XRP',
            'quantity': -XRP_OWNER_RESERVE,  # NEGATIVE - owner reserve is locked (outflow)
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'xrp_blockchain',
            'asset_class': 'crypto',
            'external_id': f"xrp_{tx_hash}_owner_reserve",
            'notes': f"XRP owner reserve for trust line creation ({XRP_OWNER_RESERVE} XRP locked)"
        })
    
    return transactions


def _normalize_xrp_fee_transaction(tx: dict, meta: dict, wallet_address: str, occurred_at, tx_hash: str, fee_xrp: float) -> list:
    """Normalize generic XRP transactions that only involve fees."""
    account = tx.get("Account")
    tx_type = tx.get("TransactionType")
    
    # Only process if this address paid the fee
    if account != wallet_address or fee_xrp <= 0:
        return []
    
    return [{
        'type': 'fee',
        'symbol': 'XRP',
        'symbol_normalized': 'XRP',
        'quantity': -fee_xrp,  # NEGATIVE - fee is an outflow/consumption of assets
        'value_native': None,
        'currency_native': None,
        'price': None,
        'fee': 0,
        'fee_currency': None,
        'blockchain_tx_hash': tx_hash,
        'occurred_at': occurred_at,
        'source': 'xrp_blockchain',
        'asset_class': 'crypto',
        'external_id': f"xrp_{tx_hash}_fee",
        'notes': f"XRP network fee ({tx_type.lower()}) | tx: {tx_hash}"
    }]


# UNIFIED FUNCTIONS FOR CONSISTENCY WITH OTHER PROVIDERS

def get_balance_unified(address: str) -> float:
    """
    Unified function to get spendable XRP balance (excluding reserves).
    
    Args:
        address (str): XRP wallet address
        
    Returns:
        float: Available XRP balance (total minus reserves)
    """
    balance_info = get_balance(address)
    return balance_info.get("available", 0.0) if balance_info else 0.0


def get_total_balance_including_reserves(address: str) -> dict:
    """
    Get comprehensive XRP balance information including reserves explanation.
    
    Args:
        address (str): XRP wallet address
        
    Returns:
        dict: Complete balance breakdown with reserve information
    """
    balance_info = get_balance(address)
    
    if not balance_info:
        return {
            "total": 0.0,
            "available": 0.0,
            "reserved": 0.0,
            "reserve_explanation": "Account does not exist or no balance found"
        }
    
    return {
        "total": balance_info.get("total", 0.0),
        "available": balance_info.get("available", 0.0),  # Spendable amount
        "reserved": balance_info.get("reserved", 0.0),   # Locked for account/objects
        "reserve_breakdown": balance_info.get("reserve_breakdown", {}),
        "account_sequence": balance_info.get("sequence", 0),
        "reserve_explanation": f"Reserve includes {balance_info.get('reserve_breakdown', {}).get('base_reserve', 0)} XRP base reserve + {balance_info.get('reserve_breakdown', {}).get('owner_reserve', 0)} XRP for {balance_info.get('reserve_breakdown', {}).get('owner_count', 0)} owned objects"
    }


def get_transactions_unified(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Unified function to get all XRP transactions including reserve handling.
    
    Args:
        address (str): XRP wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    return get_transactions(address, start_date, end_date, limit)


def get_account_summary(address: str) -> dict:
    """
    Get comprehensive XRP account summary including reserve information.
    
    Args:
        address (str): XRP wallet address
        
    Returns:
        dict: Complete account information with reserve explanation
    """
    balance_info = get_balance(address)
    
    if not balance_info:
        return {
            "account_exists": False,
            "total_xrp": 0.0,
            "spendable_xrp": 0.0,
            "reserved_xrp": 0.0,
            "reserve_note": "Account does not exist - first transaction will require base reserve"
        }
    
    reserve_breakdown = balance_info.get("reserve_breakdown", {})
    
    return {
        "account_exists": True,
        "total_xrp": balance_info.get("total", 0.0),
        "spendable_xrp": balance_info.get("available", 0.0),
        "reserved_xrp": balance_info.get("reserved", 0.0),
        "base_reserve": reserve_breakdown.get("base_reserve", 0.0),
        "owner_count": reserve_breakdown.get("owner_count", 0),
        "owner_reserve": reserve_breakdown.get("owner_reserve", 0.0),
        "sequence_number": balance_info.get("sequence", 0),
        "reserve_note": f"Base reserve ({reserve_breakdown.get('base_reserve', 0)} XRP) is locked and cannot be spent. Additional {reserve_breakdown.get('owner_reserve', 0)} XRP locked for {reserve_breakdown.get('owner_count', 0)} owned objects."
    }


def validate_xrp_address(address: str) -> bool:
    """
    Validate XRP address format.
    
    Args:
        address (str): XRP address to validate
        
    Returns:
        bool: True if valid XRP address
    """
    import re
    
    if not address:
        return False
    
    # XRP addresses (classic format) start with 'r' and are 25-34 characters
    # This is basic validation - for production, use proper base58 validation
    classic_pattern = r'^r[a-zA-Z0-9]{24,33}$'
    
    # X-Address format (newer format) starts with 'X'
    x_address_pattern = r'^X[a-zA-Z0-9]{46,47}$'
    
    return bool(re.match(classic_pattern, address) or re.match(x_address_pattern, address))


def get_reserve_requirements() -> dict:
    """
    Get current XRP reserve requirements.
    
    Returns:
        dict: Current reserve amounts and explanation
    """
    return {
        "base_reserve": XRP_BASE_RESERVE,
        "owner_reserve_per_object": XRP_OWNER_RESERVE,
        "explanation": {
            "base_reserve": f"Every XRP account must maintain a minimum of {XRP_BASE_RESERVE} XRP as a base reserve",
            "owner_reserve": f"Each owned object (trust line, offer, etc.) requires an additional {XRP_OWNER_RESERVE} XRP reserve",
            "purpose": "Reserves prevent spam and ensure network stability. Reserved XRP cannot be spent.",
            "note": "Reserve amounts can change through network amendments but are currently stable"
        }
    }


# LEGACY COMPATIBILITY FUNCTIONS

def get_xrp_balance(address: str) -> float:
    """Legacy function - use get_balance_unified instead."""
    logger.warning("get_xrp_balance is deprecated, use get_balance_unified instead")
    return get_balance_unified(address)


def get_xrp_total_balance(address: str) -> float:
    """Legacy function - use get_total_balance_including_reserves instead.""" 
    logger.warning("get_xrp_total_balance is deprecated, use get_total_balance_including_reserves instead")
    balance_info = get_total_balance_including_reserves(address)
    return balance_info.get("total", 0.0)
