import os
import json
from datetime import datetime, timezone
from utils.api_client import make_api_call
from utils.logging_config import get_logger
from dotenv import load_dotenv
from typing import List, Dict, Union

logger = get_logger(__name__)
load_dotenv()

INFURA_PROJECT_ID = os.getenv("INFURA_PROJECT_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")

# Ethereum V2 API Configuration
ETH_CHAIN_ID = 1  # Ethereum mainnet
ETH_API_URL = "https://api.etherscan.io/v2/api"

# Cache configuration

# Ethereum parameters
ETH_WEI_PER_ETH = 1e18  # 1 ETH = 10^18 Wei
ETH_GWEI_PER_WEI = 1e9  # 1 Gwei = 10^9 Wei

# Infura fallback endpoints
INFURA_URLS = [
    f"https://mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "https://ethereum.publicnode.com",
    "https://rpc.ankr.com/eth"
]

def get_balance(address: str) -> dict | None:
    """
    Fetches comprehensive ETH balance information.
    
    Args:
        address (str): Ethereum wallet address
        
    Returns:
        dict: Contains balance and basic account info
    """
    balance_wei = _fetch_eth_balance(address)
    
    if balance_wei is None:
        return None
        
    balance_eth = balance_wei / ETH_WEI_PER_ETH
    
    return {
        "balance": balance_eth,
        "balance_wei": balance_wei,
        "address": address
    }


def _fetch_eth_balance(address: str) -> int | None:
    """
    Fetch ETH balance in Wei with Etherscan V2 as primary, Infura as fallback.
    
    Args:
        address (str): Ethereum wallet address
        
    Returns:
        int: Balance in Wei, or None if all sources fail
    """
    # Try Etherscan V2 API first (primary)
    if ETHERSCAN_API_KEY:
        try:
            params = {
                "chainid": ETH_CHAIN_ID,
                "module": "account",
                "action": "balance",
                "address": address,
                "tag": "latest",
                "apikey": ETHERSCAN_API_KEY
            }
            
            response = make_api_call(ETH_API_URL, method="GET", params=params,
                                   retries=2, delay=1, timeout=10)
            
            if response and response.get("status") == "1":
                balance_wei = int(response.get("result", 0))
                logger.info(f"Fetched ETH balance for {address} from Etherscan V2: {balance_wei / ETH_WEI_PER_ETH} ETH")
                return balance_wei
                
        except Exception as e:
            logger.warning(f"Failed to fetch ETH balance from Etherscan V2: {e}, trying Infura fallback")
    else:
        logger.warning("ETHERSCAN_API_KEY not configured, using Infura fallback")
    
    # Fallback to Infura/RPC endpoints
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [address, "latest"],
        "id": 1
    }
    
    for provider_url in INFURA_URLS:
        try:
            if not provider_url:  # Skip if no API key configured
                continue
                
            response = make_api_call(provider_url, method="POST", headers=headers,
                                   data=json.dumps(payload), retries=2, delay=1, timeout=10)
            
            if response and response.get("result"):
                balance_wei = int(response["result"], 16)
                logger.info(f"Fetched ETH balance for {address} from Infura fallback: {balance_wei / ETH_WEI_PER_ETH} ETH")
                return balance_wei
                
        except Exception as e:
            logger.warning(f"Failed to fetch ETH balance from {provider_url}: {e}")
            continue
    
    logger.error(f"Failed to fetch ETH balance for {address} from all providers")
    return None

# Cache for transaction data
def get_transactions(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Fetches transaction history for a given ETH address with comprehensive gas fee tracking.
    
    Args:
        address (str): Ethereum wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions to fetch (default: 50)
    
    Returns:
        list: List of normalized transaction dictionaries ready for Transaction model
    """
    logger.info(f"Fetching ETH transactions for address {address}")
    
    # Get regular ETH transactions
    regular_transactions = _fetch_eth_transactions(address, limit)
    
    # Get internal transactions (contract interactions)
    internal_transactions = _fetch_internal_transactions(address, limit // 2)
    
    # Combine all transactions
    all_transactions = regular_transactions + internal_transactions
    
    # Filter by date range if provided
    if start_date or end_date:
        all_transactions = _filter_transactions_by_date(all_transactions, start_date, end_date)
    
    # Normalize transactions for the Transaction model
    normalized_transactions = []
    for tx in all_transactions:
        normalized_tx = _normalize_eth_transaction(tx, address)
        if normalized_tx:
            normalized_transactions.extend(normalized_tx)
    
    # Sort by timestamp (newest first)  
    normalized_transactions.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_transactions)} normalized ETH transactions for {address}")
    return normalized_transactions


def _fetch_eth_transactions(address: str, limit: int = 50) -> list:
    """Fetch regular ETH transactions using Etherscan V2 API."""
    if not ETHERSCAN_API_KEY:
        logger.warning("ETHERSCAN_API_KEY not configured, skipping transaction fetch")
        return []
    
    try:
        params = {
            "chainid": ETH_CHAIN_ID,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": min(limit, 100),  # Etherscan max is 100 per request
            "sort": "desc",
            "apikey": ETHERSCAN_API_KEY
        }
        
        response = make_api_call(ETH_API_URL, method="GET", params=params,
                               retries=3, delay=2, timeout=15)
        
        if response and response.get("status") == "1":
            transactions = response.get("result", [])
            # Mark as regular transactions
            for tx in transactions:
                tx['_type'] = 'regular'
            
            logger.info(f"Fetched {len(transactions)} regular ETH transactions for {address}")
            return transactions[:limit]
            
    except Exception as e:
        logger.error(f"Error fetching ETH transactions for address {address}: {e}")
    
    return []


def _fetch_internal_transactions(address: str, limit: int = 25) -> list:
    """Fetch internal transactions (contract calls) using Etherscan V2 API."""
    if not ETHERSCAN_API_KEY:
        return []
    
    try:
        params = {
            "chainid": ETH_CHAIN_ID,
            "module": "account", 
            "action": "txlistinternal",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": min(limit, 100),
            "sort": "desc",
            "apikey": ETHERSCAN_API_KEY
        }
        
        response = make_api_call(ETH_API_URL, method="GET", params=params,
                               retries=3, delay=2, timeout=15)
        
        if response and response.get("status") == "1":
            transactions = response.get("result", [])
            # Mark as internal transactions
            for tx in transactions:
                tx['_type'] = 'internal'
            
            logger.info(f"Fetched {len(transactions)} internal ETH transactions for {address}")
            return transactions[:limit]
            
    except Exception as e:
        logger.error(f"Error fetching internal ETH transactions for address {address}: {e}")
    
    return []


def _filter_transactions_by_date(transactions: list, start_date: str = None, end_date: str = None) -> list:
    """Filter transactions by date range."""
    if not start_date and not end_date:
        return transactions
    
    from datetime import datetime
    
    filtered = []
    for tx in transactions:
        timestamp = int(tx.get("timeStamp", 0))
        if not timestamp:
            continue
            
        tx_date = datetime.fromtimestamp(timestamp).date()
        
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


def _normalize_eth_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize an ETH transaction into Transaction model format.
    Handles comprehensive gas fee tracking and contract interactions.
    
    Args:
        tx_data (dict): Raw transaction data from Etherscan API
        wallet_address (str): The wallet address we're tracking
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        tx_type = tx_data.get('_type', 'regular')
        
        hash_tx = tx_data.get('hash')
        timestamp = int(tx_data.get('timeStamp', 0))
        value_wei = int(tx_data.get('value', 0))
        value_eth = value_wei / ETH_WEI_PER_ETH
        
        from_addr = tx_data.get('from', '').lower()
        to_addr = tx_data.get('to', '').lower()
        wallet_addr = wallet_address.lower()
        
        if not hash_tx or not timestamp:
            logger.warning(f"Missing required transaction data: hash={hash_tx}, timestamp={timestamp}")
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        # Calculate gas fees (only for regular transactions)
        gas_fee_eth = 0
        if tx_type == 'regular' and from_addr == wallet_addr:
            gas_used = int(tx_data.get('gasUsed', 0))
            gas_price = int(tx_data.get('gasPrice', 0))
            gas_fee_eth = (gas_used * gas_price) / ETH_WEI_PER_ETH
        
        # Determine transaction direction
        if to_addr == wallet_addr and from_addr != wallet_addr and value_eth > 0:
            # Incoming transaction
            transactions.append({
                'type': 'transfer_in',
                'symbol': 'ETH',
                'symbol_normalized': 'ETH',
                'quantity': value_eth,
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,  # Receiver doesn't pay gas fee
                'fee_currency': None,
                'blockchain_tx_hash': hash_tx,
                'occurred_at': occurred_at,
                'source': 'eth_blockchain',
                'asset_class': 'crypto',
                'external_id': f"eth_{hash_tx}_in",
                'notes': f"ETH received from {from_addr}" + (f" (via contract)" if tx_type == 'internal' else "")
            })
            
        elif from_addr == wallet_addr and to_addr != wallet_addr and value_eth > 0:
            # Outgoing transaction
            transactions.append({
                'type': 'transfer_out',
                'symbol': 'ETH',
                'symbol_normalized': 'ETH',
                'quantity': -value_eth,  # NEGATIVE - transfer out is an outflow
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,  # Fee has its own separate transaction
                'fee_currency': None,
                'blockchain_tx_hash': hash_tx,
                'occurred_at': occurred_at,
                'source': 'eth_blockchain',
                'asset_class': 'crypto',
                'external_id': f"eth_{hash_tx}_out",
                'notes': f"ETH sent to {to_addr}" + (f" (via contract)" if tx_type == 'internal' else "")
            })
        
        # Always create gas fee record for regular transactions if this address paid the fee
        if gas_fee_eth > 0 and from_addr == wallet_addr and tx_type == 'regular':
            # Determine transaction context for fee notes
            is_contract = _is_contract_interaction(tx_data)
            if value_eth > 0 and to_addr != wallet_addr:
                fee_context = "transfer_out"
            elif is_contract:
                fee_context = "contract_interaction"
            else:
                fee_context = "transaction"
            
            transactions.append({
                'type': 'fee',
                'symbol': 'ETH',
                'symbol_normalized': 'ETH',
                'quantity': -gas_fee_eth,  # NEGATIVE - fee is an outflow/consumption of assets
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': hash_tx,
                'occurred_at': occurred_at,
                'source': 'eth_blockchain',
                'asset_class': 'crypto',
                'external_id': f"eth_{hash_tx}_fee",
                'notes': f"ETH network fee ({fee_context}) | tx: {hash_tx}"
            })
            
    except Exception as e:
        logger.error(f"Error normalizing ETH transaction {tx_data.get('hash', 'unknown')}: {e}")
        return []
    
    return transactions


def _is_contract_interaction(tx_data: dict) -> bool:
    """Determine if a transaction is a smart contract interaction."""
    # Contract interactions typically have input data
    input_data = tx_data.get('input', '0x')
    
    # If input is not just '0x', it's likely a contract call
    return input_data and input_data != '0x'


# Unified Functions
def get_balance_unified(address: str) -> dict:
    """
    Unified function to get comprehensive ETH balance information.
    
    Args:
        address (str): Ethereum wallet address
    
    Returns:
        dict: Unified balance information with standard format
    """
    try:
        balance_info = get_balance(address)
        
        return {
            'success': True,
            'blockchain': 'ethereum',
            'address': address,
            'balance': balance_info,
            'timestamp': datetime.now(timezone.utc),
            'provider': 'eth_provider',
            'supports_staking': False,
            'metadata': {
                'network': 'mainnet',
                'currency_native': 'ETH',
                'providers_used': balance_info.get('providers_used', []),
                'gas_estimation_available': True,
                'contract_interaction_supported': True
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_balance_unified for ETH address {address}: {e}")
        return {
            'success': False,
            'blockchain': 'ethereum',
            'address': address,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc),
            'provider': 'eth_provider'
        }


def get_transactions_unified(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> dict:
    """
    Unified function to get comprehensive ETH transaction history.
    
    Args:
        address (str): Ethereum wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)  
        limit (int): Maximum transactions to fetch (default: 50)
    
    Returns:
        dict: Unified transaction information with standard format
    """
    try:
        transactions = get_transactions(address, start_date, end_date, limit)
        
        # Calculate summary statistics
        total_transactions = len(transactions)
        incoming_count = len([tx for tx in transactions if tx['type'] == 'transfer_in'])
        outgoing_count = len([tx for tx in transactions if tx['type'] == 'transfer_out'])
        fee_count = len([tx for tx in transactions if tx['type'] == 'fee'])
        
        total_fees = sum(tx.get('fee', 0) for tx in transactions if tx.get('fee'))
        
        return {
            'success': True,
            'blockchain': 'ethereum',
            'address': address,
            'transactions': transactions,
            'summary': {
                'total_transactions': total_transactions,
                'incoming_transactions': incoming_count,
                'outgoing_transactions': outgoing_count,
                'fee_transactions': fee_count,
                'total_fees_eth': total_fees,
                'date_range': {
                    'start_date': start_date,
                    'end_date': end_date,
                    'limit_applied': limit
                }
            },
            'timestamp': datetime.now(timezone.utc),
            'provider': 'eth_provider',
            'metadata': {
                'network': 'mainnet',
                'includes_internal_tx': True,
                'gas_tracking_enabled': True,
                'contract_interactions_detected': any(_is_contract_interaction({'input': tx.get('notes', '')}) 
                                                    for tx in transactions),
                'api_sources': ['etherscan']
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_transactions_unified for ETH address {address}: {e}")
        return {
            'success': False,
            'blockchain': 'ethereum', 
            'address': address,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc),
            'provider': 'eth_provider'
        }


def validate_eth_address(address: str) -> dict:
    """
    Validate an Ethereum address format and checksums.
    
    Args:
        address (str): Ethereum address to validate
        
    Returns:
        dict: Validation result with detailed information
    """
    import re
    
    validation_result = {
        'is_valid': False,
        'address': address,
        'issues': [],
        'normalized_address': None,
        'address_type': None,
        'checksum_valid': None
    }
    
    if not address or not isinstance(address, str):
        validation_result['issues'].append('Address is empty or not a string')
        return validation_result
    
    # Remove '0x' prefix if present
    clean_address = address.lower()
    if clean_address.startswith('0x'):
        clean_address = clean_address[2:]
        hex_address = address
    else:
        hex_address = '0x' + address
        
    # Check length (40 hex characters)
    if len(clean_address) != 40:
        validation_result['issues'].append(f'Invalid length: {len(clean_address)} characters (expected 40)')
        return validation_result
    
    # Check hex format
    if not re.match('^[0-9a-fA-F]{40}$', clean_address):
        validation_result['issues'].append('Contains invalid hexadecimal characters')
        return validation_result
    
    # Normalize to checksummed format
    try:
        normalized_address = _to_checksum_address(hex_address)
        validation_result['normalized_address'] = normalized_address
        
        # Validate checksum if original had mixed case
        if address != address.lower() and address != address.upper():
            validation_result['checksum_valid'] = (address == normalized_address)
            if not validation_result['checksum_valid']:
                validation_result['issues'].append('Invalid checksum')
        else:
            validation_result['checksum_valid'] = True
        
    except Exception as e:
        validation_result['issues'].append(f'Checksum validation failed: {e}')
        return validation_result
    
    # Determine address type
    if clean_address == '0' * 40:
        validation_result['address_type'] = 'zero_address'
        validation_result['issues'].append('Zero address (0x000...000)')
    else:
        validation_result['address_type'] = 'standard'
    
    validation_result['is_valid'] = len(validation_result['issues']) == 0
    
    return validation_result


def _to_checksum_address(address: str) -> str:
    """
    Convert an Ethereum address to its checksummed format (EIP-55).
    
    Args:
        address (str): Ethereum address with or without '0x' prefix
        
    Returns:
        str: Checksummed address
    """
    import hashlib
    
    # Remove 0x prefix and convert to lowercase
    address = address.lower().replace('0x', '')
    
    # Get Keccak-256 hash of the address
    address_hash = hashlib.sha3_256(address.encode()).hexdigest()
    
    checksum_address = '0x'
    
    for i, char in enumerate(address):
        if int(address_hash[i], 16) >= 8:
            checksum_address += char.upper()
        else:
            checksum_address += char.lower()
            
    return checksum_address


def get_eth_gas_price() -> dict:
    """
    Get current ETH gas price information from Etherscan V2 API.
    
    Returns:
        dict: Gas price information in Gwei and Wei
    """
    try:
        if ETHERSCAN_API_KEY:
            # Try Etherscan V2 gas oracle
            params = {
                "chainid": ETH_CHAIN_ID,
                "module": "gastracker",
                "action": "gasoracle", 
                "apikey": ETHERSCAN_API_KEY
            }
            
            response = make_api_call(ETH_API_URL, method="GET", params=params,
                                   retries=2, delay=1, timeout=10)
            
            if response and response.get("status") == "1":
                result = response.get("result", {})
                
                return {
                    'success': True,
                    'source': 'etherscan',
                    'timestamp': datetime.now(timezone.utc),
                    'gas_prices': {
                        'slow_gwei': float(result.get('SafeGasPrice', 0)),
                        'standard_gwei': float(result.get('ProposeGasPrice', 0)),
                        'fast_gwei': float(result.get('FastGasPrice', 0)),
                        'slow_wei': int(float(result.get('SafeGasPrice', 0)) * ETH_GWEI_PER_WEI),
                        'standard_wei': int(float(result.get('ProposeGasPrice', 0)) * ETH_GWEI_PER_WEI),
                        'fast_wei': int(float(result.get('FastGasPrice', 0)) * ETH_GWEI_PER_WEI)
                    }
                }
        
        # Fallback - return estimated values
        logger.warning("Could not fetch real-time gas prices, returning estimates")
        return {
            'success': False,
            'source': 'estimated',
            'timestamp': datetime.now(timezone.utc),
            'gas_prices': {
                'slow_gwei': 20.0,
                'standard_gwei': 25.0, 
                'fast_gwei': 30.0,
                'slow_wei': int(20 * ETH_GWEI_PER_WEI),
                'standard_wei': int(25 * ETH_GWEI_PER_WEI),
                'fast_wei': int(30 * ETH_GWEI_PER_WEI)
            },
            'note': 'Estimated gas prices - API unavailable'
        }
        
    except Exception as e:
        logger.error(f"Error fetching ETH gas prices: {e}")
        return {
            'success': False,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc)
        }
