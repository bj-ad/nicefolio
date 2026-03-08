from utils.api_client import make_api_call, extract_json_key
from utils.logging_config import get_logger
from utils.xpub_utils import get_all_addresses_from_xpub, validate_xpub
from utils.app_config import load_app_config
from typing import List, Dict, Union

logger = get_logger(__name__)

# Load blockchain configuration
app_config = load_app_config()
BTC_CONFIG = app_config.get('blockchain', {}).get('btc', {})
DEFAULT_GAP_LIMIT = BTC_CONFIG.get('gap_limit', 20)
MAX_ADDRESSES = BTC_CONFIG.get('max_addresses', 1000)

def get_balance(address: str) -> float | None:
    """
    Fetch BTC balance from Blockstream with fallback to Blockchain.com.
    Automatically detects if address is xpub and uses appropriate method.
    
    NOTE: Cache removed - database serves as the cache for balance data.
    Daily sync pattern means fresh data is needed, not cached values.

    Args:
        address (str): Bitcoin wallet address or xpub.

    Returns:
        float: BTC balance or None if both APIs fail.
    """
    # Check if this is an xpub (starts with xpub, ypub, or zpub)
    if address and (address.startswith('xpub') or address.startswith('ypub') or address.startswith('zpub')):
        logger.info(f"Detected xpub format, using HD wallet balance function for {address[:20]}...")
        return get_balance_for_xpub(address)
    
    # Regular address
    blockstream_url = f"https://blockstream.info/api/address/{address}"
    response = make_api_call(blockstream_url, retries=3, delay=2, timeout=10)

    if response:
        received_balance = extract_json_key(response, ["chain_stats", "funded_txo_sum"], 0) / 1e8
        spent_balance = extract_json_key(response, ["chain_stats", "spent_txo_sum"], 0) / 1e8
        return received_balance - spent_balance

    # Fallback to Blockchain.com API
    logger.info("Falling back to Blockchain.com API for BTC balance.")
    return _fetch_btc_fallback_balance(address)

def _fetch_btc_fallback_balance(address: str) -> float | None:
    """
    Fetch BTC balance from Blockchain.com API as a fallback.

    Args:
        address (str): Bitcoin wallet address.

    Returns:
        float: BTC balance or None if the API call fails.
    """
    fallback_url = f"https://blockchain.info/rawaddr/{address}"
    response = make_api_call(fallback_url, retries=1, delay=0, timeout=10)  # Single attempt for fallback

    if response:
        return extract_json_key(response, ["final_balance"], 0) / 1e8  # Convert satoshis to BTC

    logger.error(f"Failed to fetch BTC balance from Blockchain.com for {address}.")
    return None

def get_transactions(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Fetches transaction history for a given BTC address or xpub from Blockstream API with Blockchain.info fallback.
    Automatically detects if address is xpub and uses appropriate method.
    
    NOTE: Cache removed - database serves as the cache for transaction data.
    Daily sync pattern means fresh data is needed, not cached values.
    
    Args:
        address (str): Bitcoin wallet address or xpub
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions to fetch (default: 50)
    
    Returns:
        list: List of normalized transaction dictionaries ready for Transaction model
    """
    logger.info(f"Fetching BTC transactions for address {address}")
    
    # Check if this is an xpub (starts with xpub, ypub, or zpub)
    if address and (address.startswith('xpub') or address.startswith('ypub') or address.startswith('zpub')):
        logger.info(f"Detected xpub format, using HD wallet transaction function for {address[:20]}...")
        return get_transactions_unified(address_or_xpub=address, start_date=start_date, end_date=end_date, limit=limit)
    
    # Try Blockstream API first
    transactions = _fetch_blockstream_transactions(address, limit)
    
    # Fallback to Blockchain.info if Blockstream fails
    if not transactions:
        logger.info("Falling back to Blockchain.info API for BTC transactions.")
        transactions = _fetch_blockchain_info_transactions(address, limit)
    
    # Filter by date range if provided
    if start_date or end_date:
        transactions = _filter_transactions_by_date(transactions, start_date, end_date)
    
    # Normalize transactions for the Transaction model
    normalized_transactions = []
    for tx in transactions:
        normalized_tx = _normalize_transaction(tx, address)
        if normalized_tx:
            normalized_transactions.extend(normalized_tx)  # Can produce multiple records per tx
    
    logger.info(f"Retrieved {len(normalized_transactions)} normalized BTC transactions for {address}")
    return normalized_transactions

def get_xpub_addresses(xpub: str, gap_limit: int = 20) -> List[str]:
    """
    Get all used addresses derived from an xpub.
    
    NOTE: Cache removed - database serves as the cache for address data.
    
    Args:
        xpub: Extended public key
        gap_limit: Gap limit for address discovery (default: 20)
        
    Returns:
        List of Bitcoin addresses that have been used
    """
    if not validate_xpub(xpub):
        logger.error(f"Invalid xpub provided: {xpub[:20]}...")
        return []
    
    logger.info(f"Discovering addresses for xpub: {xpub[:20]}... (gap_limit: {gap_limit})")
    
    try:
        addresses = get_all_addresses_from_xpub(xpub, gap_limit=gap_limit)
        logger.info(f"Found {len(addresses)} used addresses for xpub")
        return addresses
    except Exception as e:
        logger.error(f"Error discovering addresses for xpub: {e}")
        return []


def get_balance_for_xpub(xpub: str, gap_limit: int = None) -> float:
    """
    Get total balance for all addresses derived from an xpub.
    
    Args:
        xpub: Extended public key
        gap_limit: Gap limit for address discovery (default: from config)
        
    Returns:
        Total BTC balance across all derived addresses
    """
    if gap_limit is None:
        gap_limit = DEFAULT_GAP_LIMIT
    
    addresses = get_xpub_addresses(xpub, gap_limit)
    if not addresses:
        logger.warning(f"No addresses found for xpub: {xpub[:20]}...")
        return 0.0
    
    total_balance = 0.0
    logger.info(f"Fetching balances for {len(addresses)} addresses...")
    
    for address in addresses:
        try:
            balance = get_balance(address)
            if balance is not None:
                total_balance += balance
                if balance > 0:
                    logger.debug(f"Address {address}: {balance} BTC")
        except Exception as e:
            logger.warning(f"Failed to get balance for address {address}: {e}")
    
    logger.info(f"Total xpub balance: {total_balance} BTC across {len(addresses)} addresses")
    return total_balance


def get_transactions_for_xpub(xpub: str, start_date: str = None, end_date: str = None, 
                             limit: int = 100, gap_limit: int = 20) -> List[Dict]:
    """
    Get all transactions for addresses derived from an xpub.
    
    IMPORTANT: For xpub wallets, we need to calculate net effect across ALL wallet addresses,
    not just individual addresses. This prevents duplicate transactions and correctly handles
    change outputs (where both input and change addresses belong to the same wallet).
    
    Args:
        xpub: Extended public key
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        limit: Maximum transactions per address
        gap_limit: Gap limit for address discovery
        
    Returns:
        List of normalized transaction dictionaries
    """
    addresses = get_xpub_addresses(xpub, gap_limit)
    if not addresses:
        logger.warning(f"No addresses found for xpub: {xpub[:20]}...")
        return []
    
    # Create a set for fast lookup
    wallet_addresses = set(addresses)
    logger.info(f"Fetching transactions for {len(addresses)} addresses derived from xpub")
    
    # Collect raw transactions (not normalized) across all addresses
    raw_transactions = {}  # txid -> raw tx data
    
    for i, address in enumerate(addresses):
        try:
            logger.debug(f"Fetching raw transactions for address {i+1}/{len(addresses)}: {address}")
            
            # Fetch raw transactions from API (not normalized)
            txs = _fetch_blockstream_transactions(address, limit)
            if not txs:
                txs = _fetch_blockchain_info_transactions(address, limit)
            
            # Deduplicate by txid
            for tx in txs:
                txid = tx.get('txid') or tx.get('hash')
                if txid and txid not in raw_transactions:
                    raw_transactions[txid] = tx
                    
        except Exception as e:
            logger.error(f"Failed to get transactions for address {address}: {e}")
    
    logger.info(f"Found {len(raw_transactions)} unique transactions for xpub")
    
    # Filter by date range if provided
    if start_date or end_date:
        raw_transactions = {txid: tx for txid, tx in raw_transactions.items() 
                           if _is_transaction_in_date_range(tx, start_date, end_date)}
    
    # Normalize each transaction with xpub-aware logic
    normalized_transactions = []
    for txid, tx_data in raw_transactions.items():
        try:
            normalized_tx = _normalize_xpub_transaction(tx_data, wallet_addresses, xpub)
            if normalized_tx:
                normalized_transactions.extend(normalized_tx)
        except Exception as e:
            logger.error(f"Failed to normalize xpub transaction {txid}: {e}")
    
    # Sort by timestamp (newest first)
    normalized_transactions.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_transactions)} normalized transactions from xpub")
    return normalized_transactions


def _is_transaction_in_date_range(tx_data: dict, start_date: str = None, end_date: str = None) -> bool:
    """Check if a transaction falls within the specified date range."""
    from datetime import datetime
    
    # Get timestamp based on API source
    if tx_data.get('_source') == 'blockchain_info':
        tx_time = tx_data.get('time', 0)
    else:  # Blockstream format
        tx_time = tx_data.get('status', {}).get('block_time', 0)
    
    if not tx_time:
        return False
        
    tx_date = datetime.fromtimestamp(tx_time).date()
    
    if start_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        if tx_date < start_dt:
            return False
            
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
        if tx_date > end_dt:
            return False
            
    return True


def _normalize_xpub_transaction(tx_data: dict, wallet_addresses: set, xpub: str) -> list:
    """
    Normalize a BTC transaction for an xpub wallet.
    
    Calculates net effect across ALL wallet addresses to correctly handle:
    - Change outputs (internal transfers within same wallet)
    - Consolidation transactions
    - Multi-input transactions
    
    Args:
        tx_data: Raw transaction data from API
        wallet_addresses: Set of all addresses belonging to this xpub
        xpub: Extended public key (for notes)
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        # Extract common fields based on API source
        if tx_data.get('_source') == 'blockchain_info':
            txid = tx_data.get('hash')
            timestamp = tx_data.get('time', 0)
            fee_satoshis = tx_data.get('fee', 0)
            inputs = tx_data.get('inputs', [])
            outputs = tx_data.get('out', [])
        else:  # Blockstream format
            txid = tx_data.get('txid')
            timestamp = tx_data.get('status', {}).get('block_time', 0)
            fee_satoshis = tx_data.get('fee', 0)
            inputs = tx_data.get('vin', [])
            outputs = tx_data.get('vout', [])
        
        if not txid or not timestamp:
            logger.warning(f"Missing required transaction data: txid={txid}, timestamp={timestamp}")
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        fee_btc = fee_satoshis / 1e8 if fee_satoshis else 0
        
        # Calculate total received and sent across ALL wallet addresses
        total_received = 0  # Total BTC received by ANY wallet address
        total_sent = 0      # Total BTC sent from ANY wallet address
        
        source = tx_data.get('_source')
        
        # Check outputs for received funds (to any wallet address)
        for output in outputs:
            output_address = None
            if source == 'blockchain_info':
                output_address = output.get('addr')
            else:
                output_address = output.get('scriptpubkey_address')
            
            if output_address in wallet_addresses:
                value_satoshis = output.get('value', 0)
                total_received += value_satoshis / 1e8
        
        # Check inputs for sent funds (from any wallet address)
        for input_tx in inputs:
            input_address = None
            if source == 'blockchain_info':
                prev_out = input_tx.get('prev_out', {})
                input_address = prev_out.get('addr')
                value_satoshis = prev_out.get('value', 0)
            else:
                prevout = input_tx.get('prevout', {})
                input_address = prevout.get('scriptpubkey_address')
                value_satoshis = prevout.get('value', 0)
            
            if input_address in wallet_addresses:
                total_sent += value_satoshis / 1e8
        
        # Determine if wallet paid the fee (has inputs from wallet addresses)
        wallet_paid_fee = total_sent > 0
        
        # Calculate TRUE net amount (what the wallet actually gained/lost)
        # For sent transactions: net = received - sent (negative if sending out)
        # For received transactions: net = received (positive)
        net_amount = total_received - total_sent
        
        # Add xpub info to notes
        xpub_info = f"[xpub: {xpub[:20]}...]"
        
        if net_amount > 0 and not wallet_paid_fee:
            # Pure incoming transfer (no fee paid = not our transaction)
            transactions.append({
                'type': 'transfer_in',
                'symbol': 'BTC',
                'symbol_normalized': 'BTC',
                'quantity': net_amount,
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,  # We didn't pay the fee
                'fee_currency': 'BTC',
                'blockchain_tx_hash': txid,
                'occurred_at': occurred_at,
                'source': 'btc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"btc_{txid}",
                'notes': f"BTC received {xpub_info}"
            })
            
        elif net_amount < 0 or (net_amount == 0 and wallet_paid_fee):
            # Outgoing transfer or pure fee transaction
            # The amount SENT is: abs(net_amount) + fee (since fee is deducted from our wallet)
            amount_sent = abs(net_amount) if net_amount < 0 else 0
            
            if amount_sent > 0:
                transactions.append({
                    'type': 'transfer_out', 
                    'symbol': 'BTC',
                    'symbol_normalized': 'BTC',
                    'quantity': -amount_sent,  # NEGATIVE - transfer out is an outflow
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,  # Fee has its own separate transaction
                    'fee_currency': None,
                    'blockchain_tx_hash': txid,
                    'occurred_at': occurred_at,
                    'source': 'btc_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"btc_{txid}",
                    'notes': f"BTC sent {xpub_info}"
                })
            
            # Create separate fee transaction
            if fee_btc > 0:
                transactions.append({
                    'type': 'fee',
                    'symbol': 'BTC', 
                    'symbol_normalized': 'BTC',
                    'quantity': -fee_btc,  # NEGATIVE - fee is an outflow
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,
                    'fee_currency': None,
                    'blockchain_tx_hash': txid,
                    'occurred_at': occurred_at,
                    'source': 'btc_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"btc_{txid}_fee",
                    'notes': f"BTC network fee (transfer_out) | tx: {txid}"
                })
                
        elif net_amount > 0 and wallet_paid_fee:
            # Received BTC but we paid the fee - unusual but possible (e.g., consolidation)
            # The amount is net + fee since we paid the fee
            transactions.append({
                'type': 'transfer_in',
                'symbol': 'BTC',
                'symbol_normalized': 'BTC',
                'quantity': net_amount,  # Net amount received after fee
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': fee_btc,
                'fee_currency': 'BTC',
                'blockchain_tx_hash': txid,
                'occurred_at': occurred_at,
                'source': 'btc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"btc_{txid}",
                'notes': f"BTC received (consolidation) {xpub_info}"
            })
            
            # Create separate fee transaction
            if fee_btc > 0:
                transactions.append({
                    'type': 'fee',
                    'symbol': 'BTC', 
                    'symbol_normalized': 'BTC',
                    'quantity': -fee_btc,  # NEGATIVE - fee is an outflow
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,
                    'fee_currency': None,
                    'blockchain_tx_hash': txid,
                    'occurred_at': occurred_at,
                    'source': 'btc_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"btc_{txid}_fee",
                    'notes': f"BTC network fee (consolidation) | tx: {txid}"
                })
            
    except Exception as e:
        logger.error(f"Error normalizing xpub BTC transaction {tx_data.get('txid', 'unknown')}: {e}")
        return []
    
    return transactions


def get_balance_or_xpub(address_or_xpub: str, gap_limit: int = 20) -> float:
    """
    Unified function to get balance from either a single address or xpub.
    
    Args:
        address_or_xpub: Bitcoin address or extended public key
        gap_limit: Gap limit for xpub address discovery
        
    Returns:
        BTC balance
    """
    if validate_xpub(address_or_xpub):
        return get_balance_for_xpub(address_or_xpub, gap_limit)
    else:
        return get_balance(address_or_xpub)


def get_transactions_unified(address_or_xpub: str, start_date: str = None, end_date: str = None, 
                           limit: int = 100, gap_limit: int = 20) -> List[Dict]:
    """
    Unified function to get transactions from either a single address or xpub.
    
    Args:
        address_or_xpub: Bitcoin address or extended public key
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        limit: Maximum transactions per address
        gap_limit: Gap limit for xpub address discovery
        
    Returns:
        List of normalized transaction dictionaries
    """
    if validate_xpub(address_or_xpub):
        return get_transactions_for_xpub(address_or_xpub, start_date, end_date, limit, gap_limit)
    else:
        return get_transactions(address_or_xpub, start_date, end_date, limit)


def _fetch_blockstream_transactions(address: str, limit: int = 50) -> list:
    """Fetch transactions from Blockstream API."""
    try:
        # Blockstream API endpoint for address transactions
        url = f"https://blockstream.info/api/address/{address}/txs"
        
        all_transactions = []
        last_seen_txid = None
        
        while len(all_transactions) < limit:
            # Build URL with pagination if needed
            current_url = url
            if last_seen_txid:
                current_url = f"{url}?after_txid={last_seen_txid}"
            
            response = make_api_call(current_url, retries=3, delay=2, timeout=15)
            
            if not response or not isinstance(response, list):
                break
                
            if not response:  # Empty response means no more transactions
                break
                
            all_transactions.extend(response)
            last_seen_txid = response[-1].get('txid') if response else None
            
            # Break if we got less than expected (likely last page)
            if len(response) < 25:  # Blockstream typically returns 25 per page
                break
        
        return all_transactions[:limit]  # Ensure we don't exceed the limit
        
    except Exception as e:
        logger.error(f"Error fetching transactions from Blockstream for {address}: {e}")
        return []


def _fetch_blockchain_info_transactions(address: str, limit: int = 50) -> list:
    """Fetch transactions from Blockchain.info API as fallback."""
    try:
        url = f"https://blockchain.info/rawaddr/{address}?limit={limit}"
        response = make_api_call(url, retries=2, delay=1, timeout=15)
        
        if response and 'txs' in response:
            # Add source identifier to distinguish API format
            for tx in response['txs']:
                tx['_source'] = 'blockchain_info'
            return response['txs']
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching transactions from Blockchain.info for {address}: {e}")
        return []


def _filter_transactions_by_date(transactions: list, start_date: str = None, end_date: str = None) -> list:
    """Filter transactions by date range."""
    if not start_date and not end_date:
        return transactions
    
    from datetime import datetime
    
    filtered = []
    for tx in transactions:
        # Get timestamp based on API source
        if tx.get('_source') == 'blockchain_info':
            tx_time = tx.get('time', 0)
        else:  # Blockstream format
            tx_time = tx.get('status', {}).get('block_time', 0)
        
        if not tx_time:
            continue
            
        tx_date = datetime.fromtimestamp(tx_time).date()
        
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


def _normalize_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize a transaction into Transaction model format.
    May return multiple records for complex transactions (multiple inputs/outputs).
    
    Args:
        tx_data (dict): Raw transaction data from API
        wallet_address (str): The wallet address we're tracking
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        # Extract common fields based on API source
        if tx_data.get('_source') == 'blockchain_info':
            txid = tx_data.get('hash')
            timestamp = tx_data.get('time', 0)
            fee_satoshis = tx_data.get('fee', 0)
            inputs = tx_data.get('inputs', [])
            outputs = tx_data.get('out', [])
        else:  # Blockstream format
            txid = tx_data.get('txid')
            timestamp = tx_data.get('status', {}).get('block_time', 0)
            fee_satoshis = tx_data.get('fee', 0)
            inputs = tx_data.get('vin', [])
            outputs = tx_data.get('vout', [])
        
        if not txid or not timestamp:
            logger.warning(f"Missing required transaction data: txid={txid}, timestamp={timestamp}")
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        fee_btc = fee_satoshis / 1e8 if fee_satoshis else 0
        
        # Determine if this address received or sent funds
        total_received = 0
        total_sent = 0
        
        # Check outputs for received funds
        for output in outputs:
            if _is_output_for_address(output, wallet_address, tx_data.get('_source')):
                value_satoshis = _get_output_value(output, tx_data.get('_source'))
                total_received += value_satoshis / 1e8
        
        # Check inputs for sent funds
        for input_tx in inputs:
            if _is_input_from_address(input_tx, wallet_address, tx_data.get('_source')):
                value_satoshis = _get_input_value(input_tx, tx_data.get('_source'))
                total_sent += value_satoshis / 1e8
        
        # Create transaction records based on net effect
        net_amount = total_received - total_sent
        
        if net_amount > 0:
            # Net positive: received BTC
            transactions.append({
                'type': 'transfer_in',
                'symbol': 'BTC',
                'symbol_normalized': 'BTC',
                'quantity': net_amount,
                'value_native': None,  # We don't have USD value from blockchain APIs
                'currency_native': None,
                'price': None,
                'fee': fee_btc if total_sent > 0 else 0,  # Only apply fee if we sent something
                'fee_currency': 'BTC',
                'blockchain_tx_hash': txid,
                'occurred_at': occurred_at,
                'source': 'btc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"btc_{txid}_in",
                'notes': f"BTC received from blockchain transaction"
            })
            
        elif net_amount < 0:
            # Net negative: sent BTC
            transactions.append({
                'type': 'transfer_out', 
                'symbol': 'BTC',
                'symbol_normalized': 'BTC',
                'quantity': net_amount,  # Keep negative - transfer out is an outflow
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,  # Fee has its own separate transaction
                'fee_currency': None,
                'blockchain_tx_hash': txid,
                'occurred_at': occurred_at,
                'source': 'btc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"btc_{txid}_out",
                'notes': f"BTC sent via blockchain transaction"
            })
        
        # If there was both sending and receiving (complex tx), create fee record
        if total_sent > 0 and total_received > 0 and fee_btc > 0:
            transactions.append({
                'type': 'fee',
                'symbol': 'BTC', 
                'symbol_normalized': 'BTC',
                'quantity': -fee_btc,  # NEGATIVE - fee is an outflow/consumption of assets
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': txid,
                'occurred_at': occurred_at,
                'source': 'btc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"btc_{txid}_fee",
                'notes': f"BTC transaction fee"
            })
            
    except Exception as e:
        logger.error(f"Error normalizing BTC transaction {tx_data.get('txid', 'unknown')}: {e}")
        return []
    
    return transactions


def _is_output_for_address(output: dict, address: str, source: str = None) -> bool:
    """Check if an output belongs to the specified address."""
    if source == 'blockchain_info':
        return output.get('addr') == address
    else:  # Blockstream format
        return output.get('scriptpubkey_address') == address


def _is_input_from_address(input_tx: dict, address: str, source: str = None) -> bool:
    """Check if an input originates from the specified address."""
    if source == 'blockchain_info':
        prev_out = input_tx.get('prev_out', {})
        return prev_out.get('addr') == address
    else:  # Blockstream format
        prevout = input_tx.get('prevout', {})
        return prevout.get('scriptpubkey_address') == address


def _get_output_value(output: dict, source: str = None) -> int:
    """Get the value of an output in satoshis."""
    return output.get('value', 0)


def _get_input_value(input_tx: dict, source: str = None) -> int:
    """Get the value of an input in satoshis."""
    if source == 'blockchain_info':
        prev_out = input_tx.get('prev_out', {})
        return prev_out.get('value', 0)
    else:  # Blockstream format
        prevout = input_tx.get('prevout', {})
        return prevout.get('value', 0)
