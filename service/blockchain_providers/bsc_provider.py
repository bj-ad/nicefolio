import os
from decimal import Decimal
from utils.api_client import make_api_call, extract_json_key
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import load_app_config, get_global_base_currency
from dotenv import load_dotenv
from typing import List, Dict, Union

logger = get_logger(__name__)
load_dotenv()

# Load blockchain configuration
_app_config = load_app_config()
_BSC_CONFIG = _app_config.get('blockchain', {}).get('bsc', {})

# Public BSC RPC nodes (FREE - no API key needed)
BSC_RPC_NODES = [
    "https://bsc-dataseed.binance.org/",     # Binance official
    "https://bsc-dataseed1.defibit.io/",     # Defibit
    "https://bsc-dataseed1.ninicoin.io/",    # Ninicoin
    "https://rpc.ankr.com/bsc",              # Ankr public
]

# Cache configuration

# BNB Native Staking Contract Address (THE actual staking contract)
BSC_STAKING_CONTRACT = "0x0000000000000000000000000000000000002002"  # BSC Native Staking Contract

# BNB Native Staking System Contracts
NATIVE_STAKING_CONTRACTS = {
    "0x0000000000000000000000000000000000002001": "BSC Validator Set",  # System contract
    "0x0000000000000000000000000000000000001000": "System Reward Contract",
    "0x0000000000000000000000000000000000002000": "Slash Contract", 
    "0x0000000000000000000000000000000000002002": "BSC Native Staking Contract",  # MAIN STAKING CONTRACT
    "0x0000000000000000000000000000000000007001": "Staking Contract",
}

# Known BSC validator addresses for native staking
# Loaded from app_config.yaml → blockchain.bsc.validator_addresses
BSC_VALIDATOR_ADDRESSES = _BSC_CONFIG.get('validator_addresses', {})

# Known credit contracts for validators
# Loaded from app_config.yaml → blockchain.bsc.validator_credit_contracts
BSC_VALIDATOR_CREDIT_CONTRACTS = _BSC_CONFIG.get('validator_credit_contracts', {})

# BNB Staking parameters
BNB_UNBONDING_PERIOD_DAYS = 7  # BNB staking unbonding period
BNB_MIN_STAKING_AMOUNT = 1.0   # Minimum BNB staking amount

# BNB Staking method signatures (from actual transactions)
BNB_DELEGATE_METHOD = "0x982ef0a7"      # delegate(address,uint256)
BNB_UNDELEGATE_METHOD = "0x4d99dd16"    # undelegate(address,uint256)
BNB_CLAIM_METHOD = "0xd7c2dfc8"         # claim() - receives undelegated amount + rewards

def get_balance(address: str) -> dict | None:
    """
    Fetches comprehensive BNB balance including liquid, staked, and unstaking amounts.
    
    NOTE: Cache removed to ensure fresh staking data is included.
    The underlying API calls (_fetch_liquid_balance, get_staking_info) have their own caches.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        dict: Contains liquid, staked, unstaking, and total balances
    """
    # Get liquid balance
    liquid_balance = _fetch_liquid_balance(address)
    
    # Get staking information
    staking_info = get_staking_info(address)
    
    if liquid_balance is None:
        return None
        
    return {
        "liquid": liquid_balance,
        "staked": staking_info.get("staked", 0.0),
        "unstaking": staking_info.get("unstaking", 0.0),
        "total": liquid_balance + staking_info.get("staked", 0.0) + staking_info.get("unstaking", 0.0),
        "staking_rewards": staking_info.get("pending_rewards", 0.0)
    }


def _fetch_liquid_balance(address: str) -> float | None:
    """
    Fetch liquid BNB balance using public RPC nodes (FREE).
    Uses eth_getBalance method via JSON-RPC.
    
    Tries multiple RPC endpoints for reliability.
    """
    import json
    
    for rpc_url in BSC_RPC_NODES:
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [address, "latest"],
                "id": 1
            }
            
            headers = {"Content-Type": "application/json"}
            
            response = make_api_call(
                rpc_url,
                method="POST",
                headers=headers,
                data=json.dumps(payload),
                retries=2,
                delay=1,
                timeout=10
            )
            
            if response and "result" in response:
                # Convert hex balance to BNB (Wei to BNB)
                balance_wei = int(response["result"], 16)
                balance_bnb = balance_wei / 1e18
                logger.info(f"Fetched BNB balance for {address} from {rpc_url}: {balance_bnb} BNB")
                return balance_bnb
            else:
                logger.warning(f"Invalid response from {rpc_url}: {response}")
                continue
                
        except Exception as e:
            logger.warning(f"Failed to fetch balance from {rpc_url}: {e}")
            continue
    
    # All RPC nodes failed
    logger.error(f"Failed to fetch liquid BNB balance for {address} from all RPC nodes")
    return None


def _encode_function_call(function_signature: str, params: list = None) -> str:
    """
    Encode a function call with parameters for eth_call.
    
    Args:
        function_signature (str): Function signature like "getValidators(uint256,uint256)"
        params (list): List of parameters to encode
        
    Returns:
        str: Encoded call data (0x + selector + encoded params)
    """
    from web3 import Web3
    
    # Calculate function selector (first 4 bytes of keccak256 hash)
    hash_bytes = Web3.keccak(text=function_signature)
    selector = hash_bytes.hex()[:8]  # First 4 bytes = 8 hex chars
    
    if not params:
        return "0x" + selector
    
    # Encode parameters (simple encoding for uint256 and address types)
    encoded_params = ""
    for param in params:
        if isinstance(param, int):
            # uint256: pad to 32 bytes (64 hex chars)
            encoded_params += format(param, '064x')
        elif isinstance(param, str) and param.startswith("0x"):
            # address: remove 0x and pad to 32 bytes
            encoded_params += param[2:].lower().zfill(64)
        else:
            raise ValueError(f"Unsupported parameter type: {type(param)}")
    
    return "0x" + selector + encoded_params


def _make_rpc_call(method: str, params: list) -> dict | None:
    """
    Make a JSON-RPC call to BSC with automatic failover between nodes.
    
    Args:
        method (str): JSON-RPC method (e.g., 'eth_call', 'eth_getLogs')
        params (list): Method parameters
        
    Returns:
        dict | None: RPC response or None if all nodes fail
    """
    import json
    
    for rpc_url in BSC_RPC_NODES:
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": 1
            }
            
            headers = {"Content-Type": "application/json"}
            
            response = make_api_call(
                rpc_url,
                method="POST",
                headers=headers,
                data=json.dumps(payload),
                retries=2,
                delay=1,
                timeout=10
            )
            
            if response and "result" in response:
                logger.debug(f"RPC call {method} succeeded on {rpc_url}")
                return response
            elif response and "error" in response:
                error_msg = response["error"].get("message", "Unknown error")
                # Don't log "execution reverted" as warning - it's expected for non-existent data
                if "execution reverted" in error_msg.lower():
                    logger.debug(f"RPC call {method} on {rpc_url}: {error_msg}")
                else:
                    logger.warning(f"RPC error from {rpc_url}: {error_msg}")
                continue
            else:
                logger.warning(f"Invalid RPC response from {rpc_url}: {response}")
                continue
                
        except Exception as e:
            logger.warning(f"RPC call {method} failed on {rpc_url}: {e}")
            continue
    
    logger.debug(f"RPC call {method} failed on all nodes")
    return None


def _query_staking_contract(address: str) -> dict:
    """
    Query BSC native staking contract for current staking state using RPC-only methods.
    
    **Fallback Chain (RPC-only, no API dependencies):**
    1. **Credit Contract System (BEP-333)** - Primary method
       - Query getValidatorCreditContract(operator) for each known validator
       - Query getPooledBNB(delegator) from credit contract
       - Returns principal + auto-compounded rewards
    
    2. **Legacy getDelegated()** - Fallback method
       - Query getValidators(0, 100) to get all validators
       - Query getDelegated(delegator, validator) for each
       - Returns staked amount per validator
    
    3. **Return zeros** - If both fail
    
    **Note:** All queries use free RPC nodes only. No API keys or paid services required.
    
    Args:
        address (str): Wallet address to query
        
    Returns:
        dict: Staking information with 'staked', 'unstaking', 'validators' keys
    """
    try:
        total_staked = 0.0
        active_validators = []
        
        # ========== APPROACH 1: Credit Contract System (BNB Chain Fusion) ==========
        # Try querying known validators' credit contracts first
        logger.debug(f"Trying Credit Contract approach for {len(BSC_VALIDATOR_ADDRESSES)} known validators")
        
        for validator_operator, validator_name in BSC_VALIDATOR_ADDRESSES.items():
            try:
                # Step 1: Get validator's credit contract
                credit_contract = _get_validator_credit_contract(validator_operator)
                
                if not credit_contract or credit_contract == "0x0000000000000000000000000000000000000000":
                    logger.debug(f"No credit contract for {validator_name} ({validator_operator})")
                    continue
                
                # Step 2: Query delegator's pooled BNB from credit contract
                pooled_bnb = _get_pooled_bnb_from_credit_contract(credit_contract, address)
                
                if pooled_bnb > 0:
                    total_staked += pooled_bnb
                    active_validators.append({
                        "validator": validator_operator,
                        "validator_name": validator_name,
                        "credit_contract": credit_contract,
                        "amount": pooled_bnb,
                        "method": "credit_contract"  # Track which method found this
                    })
                    logger.info(f"✓ Found {pooled_bnb} BNB staked with {validator_name} via Credit Contract")
                    
            except Exception as e:
                logger.warning(f"Credit Contract query failed for {validator_name}: {e}")
                continue
        
        # If Credit Contract approach found staking, use it
        if total_staked > 0:
            logger.info(f"Credit Contract approach succeeded: {total_staked} BNB staked with {len(active_validators)} validator(s)")
            
            # Query undelegating amount
            unstaking = _query_unstaking_amount(address)
            
            return {
                "staked": total_staked,
                "unstaking": unstaking,
                "validators": active_validators
            }
        
        logger.debug("Credit Contract approach found no staking, trying legacy getDelegated() approach")
        
        # ========== APPROACH 2: Legacy getDelegated() Method ==========
        # Note: getValidators() signature is getValidators(uint256,uint256) not getValidators()
        # Selector 0xb7ab4db5 doesn't exist - correct selector is 0xb08c76c1
        
        selector = _keccak256('getValidators(uint256,uint256)')
        offset = _encode_uint256(0)  # Start at index 0
        limit = _encode_uint256(100)  # Get up to 100 validators
        call_data = selector[2:] + offset + limit
        
        logger.debug("Querying getValidators(0, 100)...")
        
        # Step 1: Get list of ALL validators from contract
        validators_response = _make_rpc_call("eth_call", [{
            "to": BSC_STAKING_CONTRACT,
            "data": "0x" + call_data
        }, "latest"])
        
        if not validators_response or "result" not in validators_response:
            logger.warning("Failed to fetch validators from staking contract")
            return {"staked": 0.0, "unstaking": 0.0, "validators": []}
        
        # Parse validators array from ABI-encoded response
        validators_hex = validators_response["result"]
        validators = _parse_address_array(validators_hex)
        
        if not validators:
            logger.info(f"No validators found in staking contract")
            return {"staked": 0.0, "unstaking": 0.0, "validators": []}
        
        logger.info(f"Found {len(validators)} validators in staking contract, querying delegations...")
        
        # Step 2: Query delegated amount for each validator using getDelegated()
        selector_delegated = _keccak256('getDelegated(address,address)')
        
        for validator in validators:
            try:
                # Encode getDelegated(address delegator, address validator) call
                delegator_param = _encode_address(address)
                validator_param = _encode_address(validator)
                call_data = selector_delegated[2:] + delegator_param + validator_param
                
                delegated_response = _make_rpc_call("eth_call", [{
                    "to": BSC_STAKING_CONTRACT,
                    "data": "0x" + call_data
                }, "latest"])
                
                if delegated_response and "result" in delegated_response:
                    delegated_wei = int(delegated_response["result"], 16)
                    delegated_bnb = delegated_wei / 1e18
                    
                    if delegated_bnb > 0:
                        total_staked += delegated_bnb
                        active_validators.append({
                            "validator": validator,
                            "amount": delegated_bnb,
                            "method": "get_delegated"  # Track which method found this
                        })
                        logger.info(f"✓ Found {delegated_bnb} BNB staked with validator {validator} via getDelegated()")
                        
            except Exception as e:
                logger.debug(f"getDelegated() failed for validator {validator}: {e}")
                continue
        
        # Query undelegating amount
        unstaking = _query_unstaking_amount(address)
        
        logger.info(f"Legacy getDelegated() approach: staked={total_staked} BNB, unstaking={unstaking} BNB")
        
        return {
            "staked": total_staked,
            "unstaking": unstaking,
            "validators": active_validators
        }
        
    except Exception as e:
        logger.error(f"Error querying staking contract for {address}: {e}", exc_info=True)
        return {"staked": 0.0, "unstaking": 0.0, "validators": []}


def _query_unstaking_amount(address: str) -> float:
    """
    Query the amount of BNB currently unstaking (unbonding) for an address.
    
    Args:
        address (str): Wallet address
        
    Returns:
        float: Amount of BNB in unstaking period
    """
    try:
        selector = _keccak256('getUndelegated(address)')
        
        undelegator_param = _encode_address(address)
        call_data = selector[2:] + undelegator_param
        
        undelegated_response = _make_rpc_call("eth_call", [{
            "to": BSC_STAKING_CONTRACT,
            "data": "0x" + call_data
        }, "latest"])
        
        if undelegated_response and "result" in undelegated_response:
            unstaking_wei = int(undelegated_response["result"], 16)
            unstaking = unstaking_wei / 1e18
            return unstaking
        
        return 0.0
        
    except Exception as e:
        logger.warning(f"Failed to query unstaking amount for {address}: {e}")
        return 0.0









def _parse_address_array(hex_data: str) -> list:
    """
    Parse an address[] return value from contract call.
    
    ABI encoding for address[]:
    - First 32 bytes: offset to array data (usually 0x20)
    - Next 32 bytes: array length
    - Following 32 bytes each: address (20 bytes, right-padded)
    
    Args:
        hex_data (str): Hex-encoded ABI data
        
    Returns:
        list: List of address strings (with 0x prefix)
    """
    try:
        if not hex_data or hex_data == "0x":
            return []
        
        # Remove 0x prefix
        data = hex_data[2:] if hex_data.startswith("0x") else hex_data
        
        # Skip first 32 bytes (offset)
        # Read array length from bytes 32-64
        length_hex = data[64:128]
        length = int(length_hex, 16) if length_hex else 0
        
        if length == 0:
            return []
        
        # Parse addresses (each is 32 bytes, but address is last 20 bytes)
        addresses = []
        for i in range(length):
            start = 128 + (i * 64)  # Each address takes 64 hex chars (32 bytes)
            end = start + 64
            addr_hex = data[start:end]
            
            # Address is the last 40 hex chars (20 bytes)
            addr = "0x" + addr_hex[-40:]
            addresses.append(addr)
        
        return addresses
        
    except Exception as e:
        logger.error(f"Error parsing address array: {e}")
        return []


def _keccak256(data: str) -> str:
    """
    Get Keccak-256 function selector for a function signature.
    Uses precomputed selectors for common functions, calculates dynamically for unknown ones.
    
    Args:
        data (str): Function signature (e.g., 'getValidators(uint256,uint256)')
        
    Returns:
        str: Function selector with 0x prefix
    """
    # Precomputed selectors for performance (verified from BNB Chain docs)
    signatures = {
        'getValidators(uint256,uint256)': '0xb08c76c1',
        'getValidatorCreditContract(address)': '0x5a3f5d63',
        'getPooledBNB(address)': '0x4f4cb205',
        'getDelegated(address,address)': '0x28d4b524',
        'getUndelegated(address)': '0x4d99dd16',
        # Standard ERC20 + Liquid Staking methods
        'balanceOf(address)': '0x70a08231',              # Returns shares (ERC20 standard)
        'symbol()': '0x95d89b41',                         # Get token symbol (ERC20 standard)
        'getPooledBNBByShares(uint256)': '0x91faf0b4',   # Credit contract: shares → BNB value
    }
    
    if data in signatures:
        return signatures[data]
    
    # Calculate dynamically for unknown signatures using web3's keccak
    try:
        from web3 import Web3
        hash_bytes = Web3.keccak(text=data)
        selector = '0x' + hash_bytes.hex()[:8]
        logger.debug(f"Calculated selector for {data}: {selector}")
        return selector
    except ImportError:
        logger.error(f"Cannot calculate selector for {data}: web3 not installed")
        return '0x00000000'


def _encode_uint256(value: int) -> str:
    """
    Encode uint256 as 32-byte hex string for ABI.
    
    Args:
        value (int): Integer to encode
        
    Returns:
        str: 64-character hex string (no 0x prefix)
    """
    return hex(value)[2:].zfill(64)


def _encode_address(address: str) -> str:
    """
    Encode address as 32-byte hex string for ABI.
    
    Args:
        address (str): Ethereum address
        
    Returns:
        str: 64-character hex string (no 0x prefix, left-padded)
    """
    addr = address[2:] if address.startswith('0x') else address
    return addr.lower().zfill(64)


def _get_credit_contract_from_tx_logs(delegate_tx_hash: str) -> str | None:
    """
    Extract credit contract address from a delegate transaction's logs.
    
    BNB Chain Fusion (BEP-333): When a user delegates BNB, the delegate transaction
    emits a Transfer event from the credit contract. The first log's "address" field
    contains the credit contract address.
    
    This is a fallback for when StakeHub.getValidatorCreditContract() fails
    (e.g., for migrated or jailed validators).
    
    Args:
        delegate_tx_hash (str): Transaction hash of a delegate transaction
        
    Returns:
        str | None: Credit contract address or None if not found
    """
    try:
        # Get transaction receipt with logs
        response = _make_rpc_call("eth_getTransactionReceipt", [delegate_tx_hash])
        
        if not response or "result" not in response or not response["result"]:
            logger.warning(f"No receipt found for tx {delegate_tx_hash}")
            return None
        
        receipt = response["result"]
        logs = receipt.get("logs", [])
        
        if not logs:
            logger.warning(f"No logs in tx {delegate_tx_hash}")
            return None
        
        # First log in a delegate tx is Transfer from credit contract
        # The "address" field is the credit contract
        first_log = logs[0]
        credit_contract = first_log.get("address")
        
        if credit_contract:
            # Normalize address format
            credit_contract = credit_contract.lower()
            if not credit_contract.startswith("0x"):
                credit_contract = "0x" + credit_contract
            
            logger.info(f"Extracted credit contract {credit_contract} from tx {delegate_tx_hash}")
            return credit_contract
        
        return None
        
    except Exception as e:
        logger.error(f"Error extracting credit contract from tx {delegate_tx_hash}: {e}")
        return None


def _get_validator_credit_contract(validator_operator: str, delegate_tx_hash: str = None) -> str | None:
    """
    Get validator's credit contract address.
    
    BNB Chain Fusion (BEP-333): Each validator has a credit contract that manages
    delegator shares. This function tries:
    1. Check BSC_VALIDATOR_CREDIT_CONTRACTS mapping - for known validators
    2. Query StakeHub.getValidatorCreditContract() - primary on-chain method
    3. Extract from delegate transaction logs - fallback for jailed/migrated validators
    
    Args:
        validator_operator (str): Validator's operator address (e.g., 0x0C5c...)
        delegate_tx_hash (str, optional): A delegate tx hash for fallback extraction
        
    Returns:
        str | None: Credit contract address or None if query fails
    """
    try:
        # FIRST: Check known credit contracts mapping
        validator_lower = validator_operator.lower()
        if validator_lower in BSC_VALIDATOR_CREDIT_CONTRACTS:
            credit_contract = BSC_VALIDATOR_CREDIT_CONTRACTS[validator_lower]
            logger.debug(f"Credit contract for {validator_operator} from mapping: {credit_contract}")
            return credit_contract
        
        # SECOND: Query StakeHub for credit contract
        selector = _keccak256('getValidatorCreditContract(address)')
        operator_param = _encode_address(validator_operator)
        call_data = selector[2:] + operator_param
        
        response = _make_rpc_call("eth_call", [{
            "to": BSC_STAKING_CONTRACT,
            "data": "0x" + call_data
        }, "latest"])
        
        if response and "result" in response:
            result_hex = response["result"]
            if result_hex and result_hex != "0x" and "error" not in result_hex.lower():
                credit_contract = "0x" + result_hex[-40:]
                # Verify it's not zero address
                if credit_contract != "0x0000000000000000000000000000000000000000":
                    logger.debug(f"Credit contract for validator {validator_operator}: {credit_contract}")
                    return credit_contract
        
        # THIRD: Extract from delegate transaction logs
        if delegate_tx_hash:
            logger.info(f"StakeHub query failed, trying fallback: extract from tx logs")
            credit_contract = _get_credit_contract_from_tx_logs(delegate_tx_hash)
            if credit_contract:
                return credit_contract
        
        logger.warning(f"No credit contract found for validator {validator_operator}")
        return None
        
    except Exception as e:
        logger.error(f"Error getting credit contract for validator {validator_operator}: {e}")
        return None


def _get_pooled_bnb_from_credit_contract(credit_contract: str, delegator: str) -> float:
    """
    Query a credit contract for delegator's total pooled BNB (principal + rewards).
    
    BNB Chain Fusion (BEP-333): Credit contracts auto-compound rewards, so
    the returned value includes both initial delegation and all auto-compounded 
    staking rewards.
    
    Uses two approaches:
    1. balanceOf(address) + getPooledBNBByShares(shares) - More reliable (standard ERC20)
    2. getPooledBNB(address) - Fallback (may not exist on all credit contracts)
    
    Args:
        credit_contract (str): Credit contract address
        delegator (str): Delegator wallet address
        
    Returns:
        float: Total pooled BNB (principal + rewards) or 0.0 if query fails
    """
    # ========== APPROACH 1: balanceOf + getPooledBNBByShares (from concept note) ==========
    # This is more reliable as it uses standard ERC20 balanceOf method
    try:
        # Step 1: Get delegator's shares balance
        balance_selector = _keccak256('balanceOf(address)')
        delegator_param = _encode_address(delegator)
        balance_call_data = balance_selector[2:] + delegator_param
        
        balance_response = _make_rpc_call("eth_call", [{
            "to": credit_contract,
            "data": "0x" + balance_call_data
        }, "latest"])
        
        if balance_response and "result" in balance_response:
            shares_wei = int(balance_response["result"], 16)
            
            if shares_wei > 0:
                # Step 2: Convert shares to BNB value
                pooled_selector = _keccak256('getPooledBNBByShares(uint256)')
                shares_param = _encode_uint256(shares_wei)
                pooled_call_data = pooled_selector[2:] + shares_param
                
                pooled_response = _make_rpc_call("eth_call", [{
                    "to": credit_contract,
                    "data": "0x" + pooled_call_data
                }, "latest"])
                
                if pooled_response and "result" in pooled_response:
                    pooled_wei = int(pooled_response["result"], 16)
                    pooled_bnb = pooled_wei / 1e18
                    shares_bnb = shares_wei / 1e18
                    
                    logger.info(f"✓ Staking value via balanceOf+getPooledBNBByShares: "
                               f"shares={shares_bnb:.8f}, value={pooled_bnb:.8f} BNB "
                               f"(credit contract: {credit_contract[:10]}...)")
                    return pooled_bnb
                    
    except Exception as e:
        logger.debug(f"balanceOf+getPooledBNBByShares failed for {credit_contract}: {e}")
    
    # ========== APPROACH 2: getPooledBNB (fallback) ==========
    try:
        selector = _keccak256('getPooledBNB(address)')
        delegator_param = _encode_address(delegator)
        call_data = selector[2:] + delegator_param
        
        response = _make_rpc_call("eth_call", [{
            "to": credit_contract,
            "data": "0x" + call_data
        }, "latest"])
        
        if response and "result" in response:
            pooled_wei = int(response["result"], 16)
            pooled_bnb = pooled_wei / 1e18
            
            if pooled_bnb > 0:
                logger.info(f"✓ Staking value via getPooledBNB: {pooled_bnb:.8f} BNB "
                           f"(credit contract: {credit_contract[:10]}...)")
            
            return pooled_bnb
        
        return 0.0
        
    except Exception as e:
        logger.error(f"Error getting pooled BNB from credit contract {credit_contract}: {e}")
        return 0.0


def get_staking_info(address: str) -> dict:
    """
    Fetches comprehensive BNB staking information including staked, unstaking, and rewards.
    
    Uses transaction-based tracking from database plus RPC queries for current state.
    Requires staking transaction hashes to be stored in CryptoStakingTransaction table.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        dict: Staking information with staked, unstaking, and pending_rewards amounts
    """
    try:
        # Get staking info from transaction history
        staking_data = _calculate_staking_from_transactions(address)
        
        logger.info(f"Staking info for {address}: staked={staking_data['staked']}, "
                   f"unstaking={staking_data['unstaking']}, rewards={staking_data['rewards']}")
        
        # Add pending_rewards and unbonding_entries for compatibility
        staking_data.setdefault("pending_rewards", 0.0)
        staking_data.setdefault("unbonding_entries", [])
        
        return staking_data
        
    except Exception as e:
        logger.error(f"Error fetching BNB staking info for address {address}: {e}")
        return {
            "staked": 0.0,
            "unstaking": 0.0,
            "pending_rewards": 0.0,
            "rewards": 0.0,
            "unbonding_entries": [],
            "validators": []
        }


def _fetch_transaction_details(tx_hash: str) -> dict | None:
    """
    Fetch transaction details via RPC.
    
    Args:
        tx_hash (str): Transaction hash
        
    Returns:
        dict: Transaction details with amount, block, method, validator
    """
    try:
        # Fetch transaction receipt (contains events with amounts)
        receipt_response = _make_rpc_call("eth_getTransactionReceipt", [tx_hash])
        if not receipt_response or "result" not in receipt_response:
            return None
        receipt_result = receipt_response["result"]
        
        # Fetch transaction data (contains method signature)
        tx_response = _make_rpc_call("eth_getTransactionByHash", [tx_hash])
        if not tx_response or "result" not in tx_response:
            return None
        tx_result = tx_response["result"]
        
        # Parse amount from events
        # Look specifically at events emitted by the staking contract (0x...2002)
        amount = 0.0
        validator = None
        
        logs = receipt_result.get("logs", [])
        for log in logs:
            # Only process events from the staking contract
            if log.get("address", "").lower() != BSC_STAKING_CONTRACT.lower():
                continue
            
            # Amount is in the event data
            if log.get("data") and log["data"] != "0x":
                try:
                    # Event data is hex-encoded, may have multiple values
                    # Take last 32 bytes (64 hex chars) for uint256 amount
                    data_hex = log["data"]
                    if data_hex.startswith("0x"):
                        data_hex = data_hex[2:]
                    
                    # Take last 64 characters (32 bytes = 256 bits for uint256)
                    amount_hex = data_hex[-64:]
                    amount_wei = int(amount_hex, 16)
                    amount = float(Decimal(amount_wei) / Decimal(10**18))
                except Exception as e:
                    logger.debug(f"Could not parse amount from log data: {e}")
            
            # Validator address is in topic 1 (indexed parameter)
            if len(log.get("topics", [])) > 1:
                validator_topic = log["topics"][1]
                # Extract address from 32-byte topic (remove padding)
                validator = "0x" + validator_topic[-40:]
        
        method = tx_result.get("input", "")[:10]
        block = int(tx_result.get("blockNumber", "0x0"), 16)
        
        return {
            "amount": amount,
            "block": block,
            "method": method,
            "validator": validator,
            "status": receipt_result.get("status") == "0x1"
        }
        
    except Exception as e:
        logger.error(f"Error fetching transaction {tx_hash}: {e}")
        return None


def _calculate_staking_from_transactions(address: str) -> dict:
    """
    Calculate staking balance and rewards from stored transaction hashes.
    
    Formula:
    - Current staked = sum(delegates) - sum(undelegates)
    - Rewards = sum(claims) - sum(undelegates)
    
    Note: claim amount includes undelegated amount + rewards
    
    Args:
        address (str): Wallet address
        
    Returns:
        dict: Staking data with staked, unstaking, rewards, validators
    """
    from database import SessionLocal
    from models import CryptoStakingTransaction, CryptoWallet
    
    db = SessionLocal()
    try:
        # Get wallet_id for this address (case-insensitive)
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address.ilike(address),
            CryptoWallet.symbol == "BNB"
        ).first()
        
        if not wallet:
            logger.warning(f"No BNB wallet found for address {address}")
            return {"staked": 0.0, "unstaking": 0.0, "rewards": 0.0, "validators": []}
        
        # Get all staking transactions
        staking_txs = db.query(CryptoStakingTransaction).filter(
            CryptoStakingTransaction.wallet_id == wallet.id,
            CryptoStakingTransaction.symbol == "BNB"
        ).all()
        
        if not staking_txs:
            logger.info(f"No staking transactions found for {address}")
            return {"staked": 0.0, "unstaking": 0.0, "rewards": 0.0, "validators": []}
        
        # Process unprocessed transactions
        from datetime import datetime
        for tx in staking_txs:
            if tx.amount is None:  # Not yet processed
                details = _fetch_transaction_details(tx.tx_hash)
                if details:
                    tx.amount = details["amount"]
                    tx.block_number = details["block"]
                    tx.validator_address = details["validator"]
                    tx.processed_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info(f"Processed {tx.tx_type} tx {tx.tx_hash}: {tx.amount} BNB")
        
        # Calculate balances
        total_delegated = float(sum(
            tx.amount for tx in staking_txs
            if tx.tx_type == "delegate" and tx.amount
        ))
        
        total_undelegated = float(sum(
            tx.amount for tx in staking_txs
            if tx.tx_type == "undelegate" and tx.amount
        ))
        
        total_claimed = float(sum(
            tx.amount for tx in staking_txs
            if tx.tx_type == "claim" and tx.amount
        ))
        
        # Calculate current staked (delegates - claims)
        # The claim removes BNB from staking, not the undelegate
        current_staked = total_delegated - total_claimed
        
        # Calculate realized rewards (claimed - undelegated)
        # Claim contains undelegated amount + rewards accumulated during waiting period
        rewards = total_claimed - total_undelegated
        
        # Get validators
        validators = list(set(
            tx.validator_address for tx in staking_txs
            if tx.validator_address
        ))
        
        logger.info(f"Calculated staking for {address}: "
                   f"delegated={total_delegated}, undelegated={total_undelegated}, "
                   f"claimed={total_claimed}, staked={current_staked}, rewards={rewards}")
        
        return {
            "staked": current_staked,
            "unstaking": 0.0,  # Not tracked separately
            "rewards": rewards,
            "validators": validators,
            "total_delegated": total_delegated,
            "total_undelegated": total_undelegated,
            "total_claimed": total_claimed
        }
        
    except Exception as e:
        logger.error(f"Error calculating staking from transactions: {e}", exc_info=True)
        return {"staked": 0.0, "unstaking": 0.0, "rewards": 0.0, "validators": []}
    finally:
        db.close()


def get_current_staking_value(
    address: str, 
    validator_operator: str = None, 
    delegate_tx_hash: str = None,
    credit_contract: str = None
) -> dict:
    """
    Get current staking value directly from blockchain (for German tax compliance).
    
    This function queries the credit contract to get the CURRENT value of staked BNB,
    which includes both principal AND accumulated rewards (due to liquid staking model).
    
    For German tax compliance (§ 22 Nr. 3 EStG):
    - Call this weekly to track staking reward accumulation
    - The difference between two readings = rewards earned in that period
    - Formula: weekly_rewards = current_value - previous_value - new_delegates + claims
    
    Args:
        address (str): BSC wallet address (delegator)
        validator_operator (str): Optional validator operator address. If not provided,
                                  uses the first validator from BSC_VALIDATOR_ADDRESSES.
        delegate_tx_hash (str): Optional delegate tx hash for credit contract fallback.
                                If StakeHub query fails (e.g., jailed validator), the
                                credit contract can be extracted from transaction logs.
        credit_contract (str): Optional credit contract address. If provided, skips
                               the discovery process entirely. Use this when you know
                               the credit contract (e.g., from previous discovery).
        
    Returns:
        dict: {
            'current_staked_value': float,  # Principal + accumulated rewards
            'shares': float,                 # Credit token shares held
            'validator_operator': str,
            'validator_name': str,
            'credit_contract': str,
            'timestamp': str,                # ISO timestamp of query
            'success': bool,
            'error': str | None
        }
    """
    from datetime import datetime, timezone
    
    result = {
        'current_staked_value': 0.0,
        'shares': 0.0,
        'validator_operator': None,
        'validator_name': None,
        'credit_contract': None,
        'timestamp': datetime.now(timezone.utc).isoformat() + 'Z',
        'success': False,
        'error': None
    }
    
    try:
        # Use provided validator or first from known validators
        if validator_operator:
            validator_name = BSC_VALIDATOR_ADDRESSES.get(validator_operator.lower(), "Unknown Validator")
        else:
            if not BSC_VALIDATOR_ADDRESSES:
                result['error'] = "No validators configured in BSC_VALIDATOR_ADDRESSES"
                return result
            validator_operator = list(BSC_VALIDATOR_ADDRESSES.keys())[0]
            validator_name = BSC_VALIDATOR_ADDRESSES[validator_operator]
        
        result['validator_operator'] = validator_operator
        result['validator_name'] = validator_name
        
        # Step 1: Get credit contract (use provided, or discover)
        if credit_contract:
            # Use directly provided credit contract
            discovered_contract = credit_contract
            logger.debug(f"Using provided credit contract: {credit_contract}")
        else:
            # Discover via StakeHub or tx logs fallback
            discovered_contract = _get_validator_credit_contract(validator_operator, delegate_tx_hash)
        
        if not discovered_contract or discovered_contract == "0x0000000000000000000000000000000000000000":
            result['error'] = f"No credit contract found for validator {validator_operator}"
            return result
        
        result['credit_contract'] = discovered_contract
        
        # Step 2: Get shares balance (balanceOf)
        balance_selector = _keccak256('balanceOf(address)')
        delegator_param = _encode_address(address)
        balance_call_data = balance_selector[2:] + delegator_param
        
        balance_response = _make_rpc_call("eth_call", [{
            "to": discovered_contract,
            "data": "0x" + balance_call_data
        }, "latest"])
        
        if not balance_response or "result" not in balance_response:
            result['error'] = "Failed to query balanceOf from credit contract"
            return result
        
        shares_wei = int(balance_response["result"], 16)
        result['shares'] = shares_wei / 1e18
        
        if shares_wei == 0:
            result['success'] = True
            result['current_staked_value'] = 0.0
            logger.info(f"No staking found for {address} with validator {validator_name}")
            return result
        
        # Step 3: Convert shares to BNB value (getPooledBNBByShares)
        pooled_selector = _keccak256('getPooledBNBByShares(uint256)')
        shares_param = _encode_uint256(shares_wei)
        pooled_call_data = pooled_selector[2:] + shares_param
        
        pooled_response = _make_rpc_call("eth_call", [{
            "to": discovered_contract,
            "data": "0x" + pooled_call_data
        }, "latest"])
        
        if not pooled_response or "result" not in pooled_response:
            result['error'] = "Failed to query getPooledBNBByShares from credit contract"
            return result
        
        pooled_wei = int(pooled_response["result"], 16)
        result['current_staked_value'] = pooled_wei / 1e18
        result['success'] = True
        
        logger.info(f"✓ Current staking value for {address}: {result['current_staked_value']:.8f} BNB "
                   f"(shares: {result['shares']:.8f}, validator: {validator_name})")
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting current staking value for {address}: {e}", exc_info=True)
        result['error'] = str(e)
        return result


def scan_all_validators_for_staking(address: str) -> dict:
    """
    Scan ALL active validators to find where address has staked BNB.
    
    This is useful for discovering staking positions when the validator is unknown.
    Uses the concept note's approach of checking balanceOf on each credit contract.
    
    Rate limit safe: ~100 RPC calls, well within 10K/5min limit.
    
    Args:
        address (str): BSC wallet address to scan
        
    Returns:
        dict: {
            'total_staked_value': float,
            'validators': [{
                'operator': str,
                'credit_contract': str,
                'shares': float,
                'staked_value': float,
                'symbol': str
            }],
            'scan_time_seconds': float,
            'validators_scanned': int,
            'success': bool
        }
    """
    import time
    from datetime import datetime
    
    start_time = time.time()
    
    result = {
        'total_staked_value': 0.0,
        'validators': [],
        'scan_time_seconds': 0.0,
        'validators_scanned': 0,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'success': False
    }
    
    try:
        # Step 1: Get all validators from StakeHub
        selector = _keccak256('getValidators(uint256,uint256)')
        offset = _encode_uint256(0)
        limit = _encode_uint256(100)
        call_data = selector[2:] + offset + limit
        
        validators_response = _make_rpc_call("eth_call", [{
            "to": BSC_STAKING_CONTRACT,
            "data": "0x" + call_data
        }, "latest"])
        
        if not validators_response or "result" not in validators_response:
            logger.warning("Failed to fetch validators from StakeHub")
            return result
        
        validators = _parse_address_array(validators_response["result"])
        result['validators_scanned'] = len(validators)
        
        logger.info(f"Scanning {len(validators)} validators for staking positions...")
        
        # Step 2: Check each validator's credit contract
        for validator in validators:
            try:
                # Get credit contract for this validator
                credit_contract = _get_validator_credit_contract(validator)
                
                if not credit_contract or credit_contract == "0x0000000000000000000000000000000000000000":
                    continue
                
                # Check balanceOf
                balance_selector = _keccak256('balanceOf(address)')
                delegator_param = _encode_address(address)
                balance_call_data = balance_selector[2:] + delegator_param
                
                balance_response = _make_rpc_call("eth_call", [{
                    "to": credit_contract,
                    "data": "0x" + balance_call_data
                }, "latest"])
                
                if not balance_response or "result" not in balance_response:
                    continue
                
                shares_wei = int(balance_response["result"], 16)
                
                if shares_wei > 0:
                    # Found staking! Get the value
                    pooled_selector = _keccak256('getPooledBNBByShares(uint256)')
                    shares_param = _encode_uint256(shares_wei)
                    pooled_call_data = pooled_selector[2:] + shares_param
                    
                    pooled_response = _make_rpc_call("eth_call", [{
                        "to": credit_contract,
                        "data": "0x" + pooled_call_data
                    }, "latest"])
                    
                    staked_value = 0.0
                    if pooled_response and "result" in pooled_response:
                        staked_value = int(pooled_response["result"], 16) / 1e18
                    
                    # Try to get symbol for validator name
                    symbol = "Unknown"
                    try:
                        symbol_selector = _keccak256('symbol()')
                        symbol_response = _make_rpc_call("eth_call", [{
                            "to": credit_contract,
                            "data": symbol_selector
                        }, "latest"])
                        if symbol_response and "result" in symbol_response:
                            # Decode string from ABI
                            hex_data = symbol_response["result"][2:]
                            if len(hex_data) > 128:
                                str_len = int(hex_data[64:128], 16)
                                symbol = bytes.fromhex(hex_data[128:128+str_len*2]).decode('utf-8')
                    except:
                        pass
                    
                    validator_info = {
                        'operator': validator,
                        'credit_contract': credit_contract,
                        'shares': shares_wei / 1e18,
                        'staked_value': staked_value,
                        'symbol': symbol
                    }
                    result['validators'].append(validator_info)
                    result['total_staked_value'] += staked_value
                    
                    logger.info(f"✓ Found staking: {staked_value:.8f} BNB with {symbol} ({validator[:10]}...)")
                    
            except Exception as e:
                logger.debug(f"Error checking validator {validator}: {e}")
                continue
        
        result['scan_time_seconds'] = time.time() - start_time
        result['success'] = True
        
        logger.info(f"Scan complete: {result['total_staked_value']:.8f} BNB staked across "
                   f"{len(result['validators'])} validator(s) (scanned {result['validators_scanned']} in "
                   f"{result['scan_time_seconds']:.2f}s)")
        
        return result
        
    except Exception as e:
        logger.error(f"Error scanning validators: {e}", exc_info=True)
        result['scan_time_seconds'] = time.time() - start_time
        return result


def get_comprehensive_balance(address: str) -> dict:
    """
    Get comprehensive BNB balance information including the solution for unstaking period.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        dict: Complete balance breakdown preventing balance drops during unstaking
    """
    balance_info = get_balance(address)
    if not balance_info:
        return {
            "liquid": 0.0,
            "staked": 0.0,
            "unstaking": 0.0,
            "total": 0.0,
            "total_including_unstaking": 0.0,  # This solves the unstaking period issue
            "pending_rewards": 0.0,
            "is_staking": False,
            "unbonding_entries": []
        }
    
    # Calculate totals - this is key to solving the unstaking period balance drop
    liquid = balance_info.get("liquid", 0.0)
    staked = balance_info.get("staked", 0.0) 
    unstaking = balance_info.get("unstaking", 0.0)
    
    return {
        "liquid": liquid,
        "staked": staked,
        "unstaking": unstaking,  # BNB in unbonding period (not earning rewards but still owned)
        "total": liquid + staked,  # Traditional calculation (would drop during unstaking)
        "total_including_unstaking": liquid + staked + unstaking,  # SOLUTION: Include unstaking BNB
        "pending_rewards": balance_info.get("staking_rewards", 0.0),
        "is_staking": staked > 0 or unstaking > 0,
        "unbonding_entries": balance_info.get("unbonding_entries", [])
    }


def _fetch_staking_transactions(address: str, limit: int = 50) -> list:
    """
    Fetch staking transactions for a BNB address.
    
    NOTE: BSC RPC nodes don't provide transaction history - only balance queries.
    Transaction history requires BscScan API which has rate limits.
    
    For BNB staking tracking, use the CryptoStakingTransaction table with manually
    entered tx hashes. The system will auto-detect transaction details.
    
    Args:
        address (str): BSC wallet address
        limit (int): Maximum number of transactions to fetch
        
    Returns:
        list: Empty list (not available via RPC)
    """
    logger.debug(f"_fetch_staking_transactions: Not available via RPC for {address[:10]}...")
    return []


def _fetch_staking_rewards(address: str, limit: int = 50) -> list:
    """
    Fetch staking reward transactions for a BNB address.
    
    NOTE: BSC RPC nodes don't provide transaction history - only balance queries.
    BNB staking rewards are auto-compounded into staked balance (no separate claims needed).
    Use get_current_staking_value() to get current staked amount including rewards.
    
    Args:
        address (str): BSC wallet address
        limit (int): Maximum number of rewards to fetch
        
    Returns:
        list: Empty list (not available via RPC)
    """
    logger.debug(f"_fetch_staking_rewards: Not available via RPC for {address[:10]}...")
    return []


def _fetch_bnb_transactions(address: str, limit: int = 50) -> list:
    """
    Fetch regular BNB transactions for a wallet address.
    
    NOTE: BSC RPC nodes don't provide transaction history - only balance queries.
    Transaction history requires BscScan API which has rate limits.
    
    For BNB transaction tracking, use manual transaction entry.
    
    Args:
        address (str): BSC wallet address
        limit (int): Maximum number of transactions to fetch
        
    Returns:
        list: Empty list (not available via RPC)
    """
    logger.debug(f"_fetch_bnb_transactions: Not available via RPC for {address[:10]}...")
    return []


def get_staking_history(address: str, limit: int = 50) -> list:
    """
    Get staking-related transaction history for a BNB address.
    
    Args:
        address (str): BSC wallet address
        limit (int): Maximum number of staking transactions to fetch
        
    Returns:
        list: List of normalized staking transactions and rewards
    """
    logger.info(f"Fetching BNB staking history for address {address}")
    
    # Get staking transactions and rewards
    staking_transactions = _fetch_staking_transactions(address, limit)
    staking_rewards = _fetch_staking_rewards(address, limit)
    
    # Combine and normalize
    all_staking_data = staking_transactions + staking_rewards
    
    normalized_staking = []
    for item in all_staking_data:
        normalized_item = _normalize_bnb_transaction(item, address)
        if normalized_item:
            normalized_staking.extend(normalized_item)
    
    # Sort by timestamp (newest first)
    normalized_staking.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_staking)} staking-related transactions for {address}")
    return normalized_staking


def get_unstaking_schedule(address: str) -> list:
    """
    Get the unstaking schedule showing when unstaked BNB will become liquid.
    This helps track the unbonding period and predict when balance will change.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        list: Schedule of unstaking completions with amounts and dates
    """
    staking_info = get_staking_info(address)
    return staking_info.get("unbonding_entries", [])


def get_effective_balance_with_unstaking(address: str) -> dict:
    """
    SOLUTION FOR UNSTAKING PERIOD BALANCE DROPS
    
    This function provides different balance calculations to handle the unstaking period:
    1. liquid_only: Only liquid BNB (drops during unstaking)
    2. traditional_total: liquid + staked (drops during unstaking) 
    3. effective_total: liquid + staked + unstaking (PREVENTS DROPS)
    
    Use 'effective_total' for portfolio tracking to avoid balance drops during unstaking periods.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        dict: Multiple balance calculations with unstaking period handling
    """
    comprehensive_balance = get_comprehensive_balance(address)
    
    liquid = comprehensive_balance.get("liquid", 0.0)
    staked = comprehensive_balance.get("staked", 0.0)
    unstaking = comprehensive_balance.get("unstaking", 0.0)
    
    return {
        "liquid_only": liquid,
        "traditional_total": liquid + staked,  # This would drop during unstaking
        "effective_total": liquid + staked + unstaking,  # This prevents drops
        "breakdown": {
            "liquid": liquid,
            "staked": staked,
            "unstaking": unstaking,
            "pending_rewards": comprehensive_balance.get("pending_rewards", 0.0)
        },
        "unstaking_schedule": comprehensive_balance.get("unbonding_entries", []),
        "recommendation": "Use 'effective_total' for portfolio tracking to avoid balance drops during unstaking periods"
    }
    
    return staking_data




# Cache for transaction data (shorter TTL since transactions can be more time-sensitive)
def get_transactions(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Fetches transaction history for a given BNB address.
    
    IMPORTANT: BSC RPC nodes do NOT provide transaction history endpoints.
    They only support balance queries and specific transaction lookups by hash.
    
    This function returns an empty list as transaction history is not available via RPC.
    
    For BNB transaction tracking:
    - Use crypto_staking_transactions table for staking events (manually entered tx hashes)
    - Use manual transaction entry for transfers
    - Auto-detection of staking events happens when tx hashes are manually added
    
    Args:
        address (str): BSC wallet address
        start_date (str): Start date in YYYY-MM-DD format (ignored)
        end_date (str): End date in YYYY-MM-DD format (ignored)
        limit (int): Maximum number of transactions to fetch (ignored)
    
    Returns:
        list: Empty list (transaction history not available via RPC for BNB)
    """
    logger.warning(f"BNB transaction history sync disabled - RPC nodes don't provide transaction history. "
                   f"Use manual transaction entry or staking transaction tracking.")
    return []














def _is_staking_transaction(to_addr: str, from_addr: str, tx: dict) -> bool:
    """Determine if a transaction is related to staking operations."""
    # Check for known staking contract addresses
    staking_addresses = set(addr.lower() for addr in NATIVE_STAKING_CONTRACTS.keys())
    
    # Check for system contract interactions (often used for staking)
    system_contracts = {
        "0x0000000000000000000000000000000000002001",  # Validator set contract
        "0x0000000000000000000000000000000000001000",  # System reward contract
    }
    
    return (to_addr in staking_addresses or 
            from_addr in staking_addresses or
            to_addr in system_contracts or
            from_addr in system_contracts)


def _is_staking_reward_transaction(tx: dict, address: str) -> bool:
    """Determine if a transaction is a staking reward."""
    # Look for small incoming transactions from system/validator contracts
    to_addr = tx.get("to", "").lower()
    from_addr = tx.get("from", "").lower()
    value = int(tx.get("value", 0))
    
    # Rewards typically come from system contracts and are relatively small amounts
    if (to_addr == address.lower() and 
        value > 0 and 
        (from_addr.startswith("0x000000000000000000000000000000000000") or 
         from_addr in NATIVE_STAKING_CONTRACTS)):
        return True
    
    return False


def _process_staking_transactions(transactions: list, address: str) -> dict:
    """
    Process staking transactions to determine current staking state.
    This is the KEY SOLUTION for handling the unstaking period challenge.
    
    For BSC Native Staking:
    - Delegation: User sends BNB to staking contract (0x2002) with validator in input data
    - Undelegation: User calls staking contract with value=0, triggers internal transfer back
    - Claim: User calls staking contract to complete unbonding (may have value or not)
    """
    from datetime import datetime, timedelta
    
    staking_data = {
        "staked": 0.0,
        "unstaking": 0.0, 
        "pending_rewards": 0.0,
        "unbonding_entries": []
    }
    
    current_time = now_utc()
    
    # Track staking operations by their lifecycle
    staking_operations = []
    reward_total = 0.0
    
    for tx in transactions:
        try:
            timestamp = int(tx.get("timeStamp", 0))
            tx_time = datetime.fromtimestamp(timestamp)
            value = int(tx.get("value", 0)) / 1e18  # Convert Wei to BNB
            
            to_addr = tx.get("to", "").lower()
            from_addr = tx.get("from", "").lower()
            wallet_addr = address.lower()
            input_data = tx.get("input", "0x")
            
            # BSC Native Staking: Check if transaction is to/from the staking contract
            is_to_staking_contract = (to_addr == BSC_STAKING_CONTRACT.lower())
            is_from_staking_contract = (from_addr == BSC_STAKING_CONTRACT.lower())
            
            # Delegation: User → Staking Contract with value > 0
            if from_addr == wallet_addr and is_to_staking_contract and value > 0:
                # Check if this is a delegate transaction (method ID: 0x982ef0a7)
                if input_data.startswith("0x982ef0a7"):
                    staking_operations.append({
                        "type": "stake",
                        "amount": value,
                        "timestamp": tx_time,
                        "tx_hash": tx.get("hash"),
                        "validator": "staking_contract"
                    })
                    logger.debug(f"Found delegation: {value} BNB at {tx_time}")
            
            # Undelegation: User → Staking Contract with value=0 (method: 0x4d99dd16)
            elif from_addr == wallet_addr and is_to_staking_contract and value == 0:
                if input_data.startswith("0x4d99dd16"):
                    # Undelegation - need to decode amount from input data
                    # Input format: 0x4d99dd16 + validator (32 bytes) + shares/amount (32 bytes)
                    if len(input_data) >= 138:  # 0x + 8 + 64 + 64
                        try:
                            amount_hex = input_data[74:138]  # Get the amount parameter
                            undelegated_amount = int(amount_hex, 16) / 1e18
                            
                            staking_operations.append({
                                "type": "unstake_request",
                                "amount": undelegated_amount,
                                "timestamp": tx_time,
                                "tx_hash": tx.get("hash"),
                                "validator": "staking_contract"
                            })
                            logger.debug(f"Found undelegation request: {undelegated_amount} BNB at {tx_time}")
                        except Exception as e:
                            logger.warning(f"Could not decode undelegation amount: {e}")
                
                elif input_data.startswith("0xd7c2dfc8"):
                    # Claim - completes the undelegation (no amount in input, BNB already liquid)
                    logger.debug(f"Found claim transaction at {tx_time}, tx: {tx.get('hash')}")
            
            # Receiving BNB from staking (rewards or completed unstaking)
            elif to_addr == wallet_addr and is_from_staking_contract and value > 0:
                # This could be a reward or completed unstaking
                # For now, treat small amounts as rewards, larger as unstaking completion
                if value < 0.01:  # Small amount, likely a reward
                    reward_total += value
                    logger.debug(f"Found reward: {value} BNB at {tx_time}")
                else:
                    # Larger amount, likely unstaking completion
                    # Check if it's still in unbonding period
                    days_since_tx = (current_time - tx_time).days
                    
                    if days_since_tx < BNB_UNBONDING_PERIOD_DAYS:
                        staking_operations.append({
                            "type": "unstaking",
                            "amount": value,
                            "timestamp": tx_time,
                            "completion_time": tx_time + timedelta(days=BNB_UNBONDING_PERIOD_DAYS),
                            "tx_hash": tx.get("hash"),
                            "validator": "staking_contract",
                            "status": "unbonding"
                        })
                        logger.debug(f"Found unstaking (in unbonding): {value} BNB at {tx_time}")
                    else:
                        staking_operations.append({
                            "type": "unstaked_completed",
                            "amount": value,
                            "timestamp": tx_time,
                            "tx_hash": tx.get("hash"),
                            "validator": "staking_contract",
                            "status": "completed"
                        })
                        logger.debug(f"Found unstaking (completed): {value} BNB at {tx_time}")
            
            # Legacy staking (liquid staking protocols, other validators)
            elif from_addr == wallet_addr and _is_staking_address(to_addr) and value > 0:
                staking_operations.append({
                    "type": "stake",
                    "amount": value,
                    "timestamp": tx_time,
                    "tx_hash": tx.get("hash"),
                    "validator": to_addr
                })
                
            elif to_addr == wallet_addr and _is_staking_address(from_addr) and value > 0:
                days_since_tx = (current_time - tx_time).days
                
                if days_since_tx < BNB_UNBONDING_PERIOD_DAYS:
                    staking_operations.append({
                        "type": "unstaking",
                        "amount": value,
                        "timestamp": tx_time,
                        "completion_time": tx_time + timedelta(days=BNB_UNBONDING_PERIOD_DAYS),
                        "tx_hash": tx.get("hash"),
                        "validator": from_addr,
                        "status": "unbonding"
                    })
                else:
                    staking_operations.append({
                        "type": "unstaked_completed",
                        "amount": value,
                        "timestamp": tx_time,
                        "tx_hash": tx.get("hash"),
                        "validator": from_addr,
                        "status": "completed"
                    })
                    
        except Exception as e:
            logger.warning(f"Error processing staking transaction: {e}")
    
    # Calculate current balances from operations
    current_staked = 0.0
    current_unstaking = 0.0
    unbonding_entries = []
    
    for op in staking_operations:
        if op["type"] == "stake":
            current_staked += op["amount"]
        elif op["type"] == "unstake_request":
            # Undelegation request - subtract from staked amount
            current_staked -= op["amount"]
            logger.debug(f"Subtracting {op['amount']} BNB from staked (undelegation)")
        elif op["type"] == "unstaking":
            current_unstaking += op["amount"]
            unbonding_entries.append({
                "amount": op["amount"],
                "completion_time": op["completion_time"],
                "tx_hash": op["tx_hash"],
                "validator": op["validator"]
            })
        elif op["type"] == "unstaked_completed":
            # This BNB should now be liquid, subtract from staked
            current_staked -= op["amount"]
    
    staking_data.update({
        "staked": max(0.0, current_staked),  # Ensure non-negative
        "unstaking": current_unstaking,  # KEY: This prevents balance drops during unbonding
        "pending_rewards": reward_total,
        "unbonding_entries": unbonding_entries
    })
    
    logger.debug(f"Processed {len(staking_operations)} staking operations: "
                f"staked={staking_data['staked']}, unstaking={staking_data['unstaking']}")
    
    return staking_data


def _is_staking_address(address: str) -> bool:
    """Check if an address is related to staking (validators or system contracts)."""
    addr_lower = address.lower()
    
    # Check system contracts
    if addr_lower in [addr.lower() for addr in NATIVE_STAKING_CONTRACTS.keys()]:
        return True
    
    # Check validator addresses 
    if addr_lower in [addr.lower() for addr in BSC_VALIDATOR_ADDRESSES.keys()]:
        return True
        
    # Check for system contract patterns
    if addr_lower.startswith("0x000000000000000000000000000000000000"):
        return True
        
    return False


def _is_reward_transaction(tx: dict, address: str) -> bool:
    """Enhanced detection for BNB staking rewards."""
    from_addr = tx.get("from", "").lower()
    to_addr = tx.get("to", "").lower()
    value = int(tx.get("value", 0)) / 1e18
    
    # Must be incoming to the user
    if to_addr != address.lower():
        return False
    
    # Must have value
    if value <= 0:
        return False
        
    # Check if from a known staking/reward address
    if _is_staking_address(from_addr):
        # Small amounts from staking addresses are likely rewards
        if value < BNB_MIN_STAKING_AMOUNT:  # Rewards are typically smaller than minimum staking
            return True
            
    # Check for reward distribution patterns
    # Rewards often come from system contracts or have specific patterns
    if from_addr in [addr.lower() for addr in NATIVE_STAKING_CONTRACTS.keys()]:
        return True
    
    return False


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


def _normalize_bnb_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize a BNB transaction into Transaction model format.
    Handles regular transactions, staking operations, and rewards.
    
    Args:
        tx_data (dict): Raw transaction data from RPC or manual entry
        wallet_address (str): The wallet address we're tracking
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    try:
        tx_type = tx_data.get('_type', 'regular')
        
        if tx_type == 'staking_reward':
            return _normalize_bnb_staking_reward(tx_data, wallet_address)
        elif tx_type == 'staking_claim_return':
            return _normalize_bnb_staking_claim_return(tx_data, wallet_address)
        elif tx_type == 'staking':
            return _normalize_bnb_staking_transaction(tx_data, wallet_address)
        else:
            return _normalize_bnb_regular_transaction(tx_data, wallet_address)
            
    except Exception as e:
        logger.error(f"Error normalizing BNB transaction: {e}")
        return []


def _normalize_bnb_regular_transaction(tx_data: dict, wallet_address: str) -> list:
    """Normalize a regular BNB transaction."""
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        tx_hash = tx_data.get('hash')
        timestamp = int(tx_data.get('timeStamp', 0))
        gas_used = int(tx_data.get('gasUsed', 0))
        gas_price = int(tx_data.get('gasPrice', 0))
        value = int(tx_data.get('value', 0)) / 1e18  # Convert Wei to BNB
        
        if not tx_hash or not timestamp:
            logger.warning(f"Missing required transaction data: hash={tx_hash}, timestamp={timestamp}")
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        fee_bnb = (gas_used * gas_price) / 1e18 if gas_used and gas_price else 0
        
        from_addr = tx_data.get('from', '').lower()
        to_addr = tx_data.get('to', '').lower()
        wallet_addr = wallet_address.lower()
        
        # Determine transaction direction
        if to_addr == wallet_addr and from_addr != wallet_addr:
            # Incoming transaction
            if value > 0:
                transactions.append({
                    'type': 'transfer_in',
                    'symbol': 'BNB',
                    'symbol_normalized': 'BNB',
                    'quantity': value,
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,  # Receiver doesn't pay gas fee
                    'fee_currency': None,
                    'blockchain_tx_hash': tx_hash,
                    'occurred_at': occurred_at,
                    'source': 'bsc_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"bnb_{tx_hash}_in",
                    'notes': f"BNB received from {from_addr}"
                })
                
        elif from_addr == wallet_addr and to_addr != wallet_addr:
            # Outgoing transaction
            if value > 0:
                transactions.append({
                    'type': 'transfer_out',
                    'symbol': 'BNB',
                    'symbol_normalized': 'BNB',
                    'quantity': -value,  # NEGATIVE - transfer out is an outflow
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,  # Fee has its own separate transaction
                    'fee_currency': None,
                    'blockchain_tx_hash': tx_hash,
                    'occurred_at': occurred_at,
                    'source': 'bsc_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"bnb_{tx_hash}_out",
                    'notes': f"BNB sent to {to_addr}"
                })
        
        # Always create fee record if there was a gas fee and this address initiated the transaction
        if fee_bnb > 0 and from_addr == wallet_addr:
            # Determine transaction context for fee notes
            if value > 0 and to_addr != wallet_addr:
                fee_context = "transfer_out"
            elif to_addr == wallet_addr:
                fee_context = "transfer_in"
            else:
                fee_context = "contract_interaction"
            
            transactions.append({
                'type': 'fee',
                'symbol': 'BNB',
                'symbol_normalized': 'BNB',
                'quantity': -fee_bnb,  # NEGATIVE - fee is an outflow/consumption of assets
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'bsc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"bnb_{tx_hash}_fee",
                'notes': f"BNB network fee ({fee_context}) | tx: {tx_hash}"
            })
            
    except Exception as e:
        logger.error(f"Error normalizing BNB regular transaction {tx_data.get('hash', 'unknown')}: {e}")
        return []
    
    return transactions


def _normalize_bnb_staking_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize a BNB staking-related transaction.
    
    NEW: Automatically detects and records staking events in crypto_staking_transactions
    by checking method ID in transaction input data.
    """
    from datetime import datetime, timezone
    
    try:
        tx_hash = tx_data.get('hash')
        timestamp = int(tx_data.get('timeStamp', 0))
        value = int(tx_data.get('value', 0)) / 1e18
        
        if not tx_hash or not timestamp:
            return []
        
        # AUTO-DETECTION: Check method ID to identify staking event type
        input_data = tx_data.get('input', '')
        if input_data and len(input_data) >= 10:
            method_id = input_data[:10]  # First 4 bytes (10 chars with 0x)
            
            # Delegate: 0x982ef0a7
            if method_id == BNB_DELEGATE_METHOD:
                logger.debug(f"Auto-detected DELEGATE transaction: {tx_hash}")
                _auto_record_delegate_event(tx_hash, wallet_address, tx_data)
            
            # Undelegate: 0x4d99dd16
            elif method_id == BNB_UNDELEGATE_METHOD:
                logger.debug(f"Auto-detected UNDELEGATE transaction: {tx_hash}")
                _auto_record_undelegate_event(tx_hash, wallet_address, tx_data)
            
            # Claim: 0xd7c2dfc8
            elif method_id == BNB_CLAIM_METHOD:
                logger.debug(f"Auto-detected CLAIM transaction: {tx_hash}")
                _auto_record_claim_event(tx_hash, wallet_address, tx_data)
        
        # Continue with normal transaction normalization for main transactions table
        if value <= 0:
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        from_addr = tx_data.get('from', '').lower()
        to_addr = tx_data.get('to', '').lower()
        wallet_addr = wallet_address.lower()
        
        # Determine if this is staking or unstaking
        if from_addr == wallet_addr:
            # Staking operation
            return [{
                'type': 'staking',
                'symbol': 'BNB',
                'symbol_normalized': 'BNB',
                'quantity': value,
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'bsc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"bnb_{tx_hash}_stake",
                'notes': f"BNB staked to validator {to_addr}"
            }]
        else:
            # Unstaking operation (or completion of unstaking)
            return [{
                'type': 'transfer_in',  # Treat as incoming when unstaking completes
                'symbol': 'BNB',
                'symbol_normalized': 'BNB',
                'quantity': value,
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,
                'fee_currency': None,
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'bsc_blockchain',
                'asset_class': 'crypto',
                'external_id': f"bnb_{tx_hash}_unstake",
                'notes': f"BNB unstaked from validator {from_addr}"
            }]
            
    except Exception as e:
        logger.error(f"Error normalizing BNB staking transaction: {e}")
        return []


def _normalize_bnb_staking_reward(reward_data: dict, wallet_address: str) -> list:
    """Normalize a BNB staking reward into Transaction model format."""
    from datetime import datetime, timezone
    
    try:
        # Get global base currency from config
        base_currency = get_global_base_currency()
        
        tx_hash = reward_data.get('hash')
        timestamp = int(reward_data.get('timeStamp', 0))
        value = int(reward_data.get('value', 0)) / 1e18
        from_addr = reward_data.get('from', '')
        
        if not tx_hash or not timestamp or value <= 0:
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        return [{
            'type': 'staking_reward',
            'symbol': 'BNB',
            'symbol_normalized': 'BNB',
            'quantity': value,
            'value_native': None,
            'currency_native': None,
            'currency_base': base_currency,  # From config (e.g., EUR)
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'bsc_blockchain',
            'asset_class': 'crypto',
            'external_id': f"bnb_{tx_hash}_reward",
            'notes': f"BNB staking reward from {from_addr}"
        }]
        
    except Exception as e:
        logger.error(f"Error normalizing BNB staking reward: {e}")
        return []


def _normalize_bnb_staking_claim_return(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize a BNB staking claim return (internal transaction) into Transaction model format.
    
    When you undelegate/claim from BSC native staking, the staking contract (0x2002)
    sends BNB back to you via an INTERNAL transaction. This amount includes both:
    1. The original delegation amount (principal)
    2. Accumulated staking rewards
    
    This function creates TWO transaction records:
    1. transfer_in: The full amount returned (principal + rewards)
    2. staking_reward: The reward portion only (for tax reporting)
    
    The reward calculation is approximate since we don't have perfect tracking of
    individual delegation amounts. We use a conservative estimate based on typical
    BSC staking APY (~5-7% annually).
    
    Args:
        tx_data (dict): Internal transaction data from RPC or manual entry
        wallet_address (str): The wallet address we're tracking
        
    Returns:
        list: List of normalized transaction dictionaries (transfer_in + staking_reward)
    """
    from datetime import datetime, timezone
    
    try:
        # Internal transactions use different field names
        tx_hash = tx_data.get('hash')
        timestamp = int(tx_data.get('timeStamp', 0))
        value = int(tx_data.get('value', 0)) / 1e18  # Total amount returned (principal + rewards)
        from_addr = tx_data.get('from', '').lower()
        
        if not tx_hash or not timestamp or value <= 0:
            logger.debug(f"Skipping internal tx - missing data or zero value")
            return []
        
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        # Estimate reward portion
        # For BSC native staking, rewards are typically 5-7% APY and auto-compound
        # We'll use a conservative 3% estimate for the reward portion
        # This is better than not tracking rewards at all
        estimated_reward_percentage = 0.03  # 3% conservative estimate
        estimated_reward = value * estimated_reward_percentage
        principal = value - estimated_reward
        
        logger.info(f"Processing staking claim return: {value} BNB (estimated reward: {estimated_reward} BNB)")
        
        transactions = []
        
        # 1. Create transfer_in record for the full amount returned
        transactions.append({
            'type': 'transfer_in',
            'symbol': 'BNB',
            'symbol_normalized': 'BNB',
            'quantity': value,
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'bsc_blockchain',
            'asset_class': 'crypto',
            'external_id': f"bnb_{tx_hash}_claim_return",
            'notes': f"BNB returned from staking contract (principal: {principal:.6f}, rewards: {estimated_reward:.6f})"
        })
        
        # 2. Create staking_reward record for the reward portion (for tax reporting)
        transactions.append({
            'type': 'staking_reward',
            'symbol': 'BNB',
            'symbol_normalized': 'BNB',
            'quantity': estimated_reward,
            'value_native': None,
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': tx_hash,
            'occurred_at': occurred_at,
            'source': 'bsc_blockchain',
            'asset_class': 'crypto',
            'external_id': f"bnb_{tx_hash}_staking_reward",
            'notes': f"Estimated BNB staking reward from claim (3% of returned amount, may need manual adjustment)"
        })
        
        return transactions
        
    except Exception as e:
        logger.error(f"Error normalizing BNB staking claim return: {e}")
        return []


# UNIFIED FUNCTIONS FOR CONSISTENCY WITH OTHER PROVIDERS

def get_balance_unified(address: str, include_unstaking: bool = True) -> float:
    """
    Unified function to get BNB balance with option to include unstaking amounts.
    
    Args:
        address (str): BSC wallet address
        include_unstaking (bool): Whether to include unstaking BNB to prevent balance drops
        
    Returns:
        float: Total BNB balance
    """
    if include_unstaking:
        balance_info = get_effective_balance_with_unstaking(address)
        return balance_info.get("effective_total", 0.0)
    else:
        balance_info = get_balance(address)
        return balance_info.get("total", 0.0) if balance_info else 0.0


def get_transactions_unified(address: str, start_date: str = None, end_date: str = None, 
                           limit: int = 50, include_staking: bool = True) -> list:
    """
    Unified function to get all BNB transactions with optional staking inclusion.
    
    Args:
        address (str): BSC wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions
        include_staking (bool): Whether to include staking transactions
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    if include_staking:
        return get_transactions(address, start_date, end_date, limit)
    else:
        # Get only regular transactions
        regular_transactions = _fetch_bnb_transactions(address, limit)
        
        if start_date or end_date:
            regular_transactions = _filter_transactions_by_date(regular_transactions, start_date, end_date)
        
        normalized_transactions = []
        for tx in regular_transactions:
            normalized_tx = _normalize_bnb_regular_transaction(tx, address)
            if normalized_tx:
                normalized_transactions.extend(normalized_tx)
        
        normalized_transactions.sort(key=lambda x: x.get('occurred_at'), reverse=True)
        return normalized_transactions


def get_staking_summary(address: str) -> dict:
    """
    Get comprehensive staking summary including the unstaking period solution.
    
    Args:
        address (str): BSC wallet address
        
    Returns:
        dict: Comprehensive staking information with unstaking period handling
    """
    balance_info = get_comprehensive_balance(address)
    unstaking_schedule = get_unstaking_schedule(address)
    
    # Calculate when next unstaking completes
    next_completion = None
    if unstaking_schedule:
        from datetime import datetime
        next_completion = min([entry["completion_time"] for entry in unstaking_schedule])
    
    return {
        "liquid_bnb": balance_info.get("liquid", 0.0),
        "staked_bnb": balance_info.get("staked", 0.0),
        "unstaking_bnb": balance_info.get("unstaking", 0.0),  # KEY: Prevents balance drops
        "pending_rewards": balance_info.get("pending_rewards", 0.0),
        "total_owned": balance_info.get("total_including_unstaking", 0.0),  # SOLUTION
        "is_actively_staking": balance_info.get("is_staking", False),
        "unbonding_entries_count": len(unstaking_schedule),
        "next_unstaking_completion": next_completion,
        "unstaking_period_days": BNB_UNBONDING_PERIOD_DAYS,
        "balance_calculation_method": "Includes unstaking BNB to prevent drops during unbonding period"
    }


def process_staking_tx_hash(tx_hash: str, wallet_address: str) -> dict:
    """
    Process a manually entered staking transaction hash.
    Fetches transaction details via RPC and auto-detects the type (delegate/undelegate/claim).
    
    This is the main entry point for BNB staking since we can't fetch transaction history
    via RPC (no txlist endpoint). User manually enters tx hash, we fetch details and process.
    
    Args:
        tx_hash: Transaction hash to process
        wallet_address: BNB wallet address
    
    Returns:
        dict: Result with 'success', 'type', 'message' keys
    """
    try:
        # Fetch transaction details via RPC
        response = _make_rpc_call("eth_getTransactionByHash", [tx_hash])
        
        if not response or 'result' not in response or not response['result']:
            return {
                'success': False,
                'type': None,
                'message': f"Transaction {tx_hash} not found on blockchain"
            }
        
        tx_data = response['result']
        
        # Convert RPC response format to expected format
        normalized_tx_data = {
            'hash': tx_data.get('hash'),
            'from': tx_data.get('from'),
            'to': tx_data.get('to'),
            'value': tx_data.get('value', '0x0'),
            'input': tx_data.get('input', ''),
            'blockNumber': int(tx_data.get('blockNumber', '0x0'), 16),
            'timeStamp': 0  # Will be fetched from block
        }
        
        # Fetch block to get timestamp
        block_number = normalized_tx_data['blockNumber']
        if block_number > 0:
            block_response = _make_rpc_call("eth_getBlockByNumber", [hex(block_number), False])
            if block_response and 'result' in block_response:
                timestamp_hex = block_response['result'].get('timestamp', '0x0')
                normalized_tx_data['timeStamp'] = int(timestamp_hex, 16)
        
        # Check method ID to determine transaction type
        input_data = normalized_tx_data.get('input', '')
        if not input_data or len(input_data) < 10:
            return {
                'success': False,
                'type': None,
                'message': f"Transaction {tx_hash} is not a staking transaction (no method ID)"
            }
        
        method_id = input_data[:10]
        
        # Detect and process based on method ID
        if method_id == BNB_DELEGATE_METHOD:
            _auto_record_delegate_event(tx_hash, wallet_address, normalized_tx_data)
            return {
                'success': True,
                'type': 'delegate',
                'message': f"Delegate transaction {tx_hash} processed successfully"
            }
        
        elif method_id == BNB_UNDELEGATE_METHOD:
            _auto_record_undelegate_event(tx_hash, wallet_address, normalized_tx_data)
            return {
                'success': True,
                'type': 'undelegate',
                'message': f"Undelegate transaction {tx_hash} processed successfully"
            }
        
        elif method_id == BNB_CLAIM_METHOD:
            _auto_record_claim_event(tx_hash, wallet_address, normalized_tx_data)
            return {
                'success': True,
                'type': 'claim',
                'message': f"Claim transaction {tx_hash} processed and rewards calculated"
            }
        
        else:
            return {
                'success': False,
                'type': None,
                'message': f"Transaction {tx_hash} is not a recognized staking transaction (method ID: {method_id})"
            }
        
    except Exception as e:
        logger.error(f"Error processing staking tx hash {tx_hash}: {e}", exc_info=True)
        return {
            'success': False,
            'type': None,
            'message': f"Error: {str(e)}"
        }


# ============================================================================
# AUTO-DETECTION: Staking Event Recording During Daily Sync
# ============================================================================

def _auto_record_delegate_event(tx_hash: str, wallet_address: str, tx_data: dict):
    """
    Automatically create a crypto_staking_transactions record for a delegate event.
    Called during daily sync when delegate transaction is detected.
    
    Args:
        tx_hash: Transaction hash
        wallet_address: BNB wallet address
        tx_data: Raw transaction data from RPC or manual entry
    """
    from crud.crud_staking_tx import add_staking_transaction
    from models import CryptoWallet
    from database import SessionLocal
    from datetime import datetime, timezone
    
    db = None
    try:
        db = SessionLocal()
        
        # Get wallet from address (case-insensitive)
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address.ilike(wallet_address),
            CryptoWallet.symbol == 'BNB'
        ).first()
        
        if not wallet:
            logger.warning(f"No BNB wallet found for address {wallet_address}")
            return
        
        # Check if already recorded
        from models import CryptoStakingTransaction
        existing = db.query(CryptoStakingTransaction).filter_by(
            tx_hash=tx_hash
        ).first()
        
        if existing:
            logger.debug(f"Delegate event {tx_hash} already recorded, skipping")
            return
        
        # Parse delegate amount from transaction value
        value_hex = tx_data.get('value', '0x0')
        if isinstance(value_hex, str) and value_hex.startswith('0x'):
            amount = int(value_hex, 16) / 1e18
        else:
            amount = int(value_hex) / 1e18
        
        # Parse validator address from method parameters
        input_data = tx_data.get('input', '')
        validator_address = None
        if len(input_data) >= 74:  # 10 (0x + method) + 64 (address parameter)
            # Address is in the first parameter (bytes 10-74)
            validator_address = '0x' + input_data[34:74]  # Skip method ID and padding
        
        # Get block details
        block_number = int(tx_data.get('blockNumber', 0))
        timestamp = int(tx_data.get('timeStamp', 0))
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None
        
        # Create staking transaction record
        add_staking_transaction(
            db=db,
            wallet_id=wallet.id,
            tx_hash=tx_hash,
            tx_type='delegate',
            symbol='BNB',
            amount=amount,
            validator_address=validator_address,
            block_number=block_number,
            occurred_at=occurred_at
        )
        
        db.commit()
        logger.info(f"✅ Auto-recorded delegate: {amount} BNB to {validator_address[:10]}... (tx: {tx_hash[:10]}...)")
        
    except Exception as e:
        logger.error(f"Failed to auto-record delegate event {tx_hash}: {e}", exc_info=True)
        if db:
            db.rollback()
    finally:
        if db:
            db.close()


def _auto_record_undelegate_event(tx_hash: str, wallet_address: str, tx_data: dict):
    """
    Automatically create a crypto_staking_transactions record for an undelegate event.
    Includes fetching staked_balance_snapshot for later reward calculation.
    
    Args:
        tx_hash: Transaction hash
        wallet_address: BNB wallet address
        tx_data: Raw transaction data from RPC or manual entry
    """
    from crud.crud_staking_tx import add_staking_transaction
    from models import CryptoWallet, CryptoStakingTransaction
    from database import SessionLocal
    from datetime import datetime, timezone
    from decimal import Decimal
    
    db = None
    try:
        db = SessionLocal()
        
        # Get wallet from address (case-insensitive)
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address.ilike(wallet_address),
            CryptoWallet.symbol == 'BNB'
        ).first()
        
        if not wallet:
            logger.warning(f"No BNB wallet found for address {wallet_address}")
            return
        
        # Check if already recorded
        existing = db.query(CryptoStakingTransaction).filter_by(
            tx_hash=tx_hash
        ).first()
        
        if existing:
            logger.debug(f"Undelegate event {tx_hash} already recorded, skipping")
            return
        
        # Fetch transaction receipt to get event logs
        # Undelegate events emit TWO values in the log data:
        # [0] = principal (amount originally staked)
        # [1] = total (principal + accumulated rewards)
        receipt = _make_rpc_call("eth_getTransactionReceipt", [tx_hash])
        
        amount = 0.0
        principal_snapshot = Decimal('0')
        total_with_rewards = Decimal('0')
        
        if receipt and 'result' in receipt and receipt['result']:
            logs = receipt['result'].get('logs', [])
            # Find staking contract log
            for log in logs:
                if log.get('address', '').lower() == BSC_STAKING_CONTRACT.lower():
                    data = log.get('data', '')
                    if data and data != '0x' and len(data) >= 130:  # At least 2 x 32 bytes (64 chars each)
                        try:
                            # Remove 0x prefix
                            hex_data = data[2:]
                            # First 32 bytes (64 chars) = principal
                            principal_hex = hex_data[0:64]
                            principal_wei = int(principal_hex, 16)
                            principal_snapshot = Decimal(str(principal_wei)) / Decimal('1000000000000000000')
                            
                            # Second 32 bytes (64 chars) = total (principal + rewards)
                            total_hex = hex_data[64:128]
                            total_wei = int(total_hex, 16)
                            total_with_rewards = Decimal(str(total_wei)) / Decimal('1000000000000000000')
                            
                            # The undelegate amount is the total returned
                            amount = float(total_with_rewards)
                            
                            logger.debug(f"Parsed undelegate log: principal={principal_snapshot} BNB, "
                                       f"total={total_with_rewards} BNB, "
                                       f"rewards={total_with_rewards - principal_snapshot} BNB")
                            break
                        except Exception as e:
                            logger.warning(f"Failed to parse undelegate log data: {e}")
                            continue
        
        # Fallback to transaction value if no amount in logs
        if amount == 0:
            amount = int(tx_data.get('value', 0)) / 1e18
            logger.warning(f"Could not parse undelegate log, using tx value: {amount} BNB")
        
        # Parse validator address from method parameters
        input_data = tx_data.get('input', '')
        validator_address = None
        if len(input_data) >= 74:
            validator_address = '0x' + input_data[34:74]
        
        # Calculate snapshots from parsed log data
        # staked_balance_snapshot = principal that was staked
        # accumulated_rewards_snapshot = rewards earned on that principal
        staked_balance_snapshot = principal_snapshot
        accumulated_rewards_snapshot = total_with_rewards - principal_snapshot
        
        # If we couldn't parse from logs, fall back to querying current state
        if staked_balance_snapshot == 0 and accumulated_rewards_snapshot == 0:
            logger.warning(f"Could not parse snapshots from logs, querying current staking state")
            staking_info = get_staking_info(wallet_address)
            staked_balance_snapshot = Decimal(str(staking_info.get('staked', 0.0)))
            accumulated_rewards_snapshot = Decimal(str(staking_info.get('rewards', 0.0)))
        
        # Get block details
        block_number = int(tx_data.get('blockNumber', 0))
        timestamp = int(tx_data.get('timeStamp', 0))
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None
        
        # Create staking transaction record with snapshot
        add_staking_transaction(
            db=db,
            wallet_id=wallet.id,
            tx_hash=tx_hash,
            tx_type='undelegate',
            symbol='BNB',
            amount=amount,
            validator_address=validator_address,
            block_number=block_number,
            occurred_at=occurred_at,
            staked_balance_snapshot=staked_balance_snapshot,
            accumulated_rewards_snapshot=accumulated_rewards_snapshot
        )
        
        db.commit()
        logger.info(f"✅ Auto-recorded undelegate: {amount} BNB (staked: {staked_balance_snapshot}, rewards: {accumulated_rewards_snapshot}) (tx: {tx_hash[:10]}...)")
        
    except Exception as e:
        logger.error(f"Failed to auto-record undelegate event {tx_hash}: {e}", exc_info=True)
        if db:
            db.rollback()
    finally:
        if db:
            db.close()


def _auto_record_claim_event(tx_hash: str, wallet_address: str, tx_data: dict):
    """
    Automatically create a crypto_staking_transactions record for a claim event.
    Links to most recent undelegate and calculates staking rewards.
    
    Args:
        tx_hash: Transaction hash
        wallet_address: BNB wallet address
        tx_data: Raw transaction data from RPC or manual entry
    """
    from crud.crud_staking_tx import add_staking_transaction, get_most_recent_undelegate, process_staking_claim
    from models import CryptoWallet, CryptoStakingTransaction
    from database import SessionLocal
    from datetime import datetime, timezone
    
    db = None
    try:
        db = SessionLocal()
        
        # Get wallet from address (case-insensitive)
        wallet = db.query(CryptoWallet).filter(
            CryptoWallet.address.ilike(wallet_address),
            CryptoWallet.symbol == 'BNB'
        ).first()
        
        if not wallet:
            logger.warning(f"No BNB wallet found for address {wallet_address}")
            return
        
        # Check if already recorded
        existing = db.query(CryptoStakingTransaction).filter_by(
            tx_hash=tx_hash
        ).first()
        
        if existing:
            logger.debug(f"Claim event {tx_hash} already recorded, skipping")
            return
        
        # Fetch transaction receipt to get event logs
        receipt = _make_rpc_call("eth_getTransactionReceipt", [tx_hash])
        
        amount = 0.0
        validator_address = None
        
        if receipt and 'result' in receipt:
            logs = receipt['result'].get('logs', [])
            for log in logs:
                data = log.get('data', '')
                topics = log.get('topics', [])
                
                if data and data != '0x' and len(data) >= 66:
                    # Parse claim amount
                    try:
                        amount_wei = int(data[:66], 16)
                        amount = amount_wei / 1e18
                        
                        # Parse validator from topics (usually topic[1])
                        if len(topics) > 1:
                            validator_hex = topics[1]
                            if len(validator_hex) >= 66:
                                validator_address = '0x' + validator_hex[-40:]
                        
                        if amount > 0:
                            break
                    except:
                        continue
        
        # Find most recent undelegate for this validator
        linked_tx_hash = get_most_recent_undelegate(db, wallet.id, validator_address)
        
        # Get block details
        block_number = int(tx_data.get('blockNumber', 0))
        timestamp = int(tx_data.get('timeStamp', 0))
        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None
        
        # Create claim record
        add_staking_transaction(
            db=db,
            wallet_id=wallet.id,
            tx_hash=tx_hash,
            tx_type='claim',
            symbol='BNB',
            amount=amount,
            validator_address=validator_address,
            block_number=block_number,
            occurred_at=occurred_at,
            linked_tx_hash=linked_tx_hash
        )
        
        db.commit()
        
        # Auto-process claim to calculate and create staking_reward transaction
        if linked_tx_hash:
            try:
                process_staking_claim(db, wallet.id, tx_hash, linked_tx_hash)
                db.commit()
                logger.info(f"✅ Auto-recorded claim: {amount} BNB (linked to {linked_tx_hash[:10]}...) (tx: {tx_hash[:10]}...)")
            except Exception as e:
                logger.error(f"Failed to process staking claim {tx_hash}: {e}", exc_info=True)
                db.rollback()
        else:
            logger.warning(f"⚠️ Auto-recorded claim {tx_hash[:10]}... but no linked undelegate found")
        
    except Exception as e:
        logger.error(f"Failed to auto-record claim event {tx_hash}: {e}", exc_info=True)
        if db:
            db.rollback()
    finally:
        if db:
            db.close()


def validate_bsc_address(address: str) -> bool:
    """
    Validate BSC address format.
    
    Args:
        address (str): BSC address to validate
        
    Returns:
        bool: True if valid BSC address
    """
    import re
    
    if not address:
        return False
    
    # BSC addresses are Ethereum-style addresses (42 chars, starts with 0x)
    pattern = r'^0x[a-fA-F0-9]{40}$'
    return bool(re.match(pattern, address))


# LEGACY COMPATIBILITY FUNCTIONS

def get_bnb_balance(address: str) -> float:
    """Legacy function - use get_balance_unified instead."""
    logger.warning("get_bnb_balance is deprecated, use get_balance_unified instead")
    return get_balance_unified(address, include_unstaking=True)


def get_bnb_staking_balance(address: str) -> float:
    """Legacy function - use get_staking_summary instead."""
    logger.warning("get_bnb_staking_balance is deprecated, use get_staking_summary instead")
    balance_info = get_balance(address)
    return balance_info.get("staked", 0.0) + balance_info.get("unstaking", 0.0) if balance_info else 0.0
