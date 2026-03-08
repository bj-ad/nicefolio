import os
from datetime import datetime, timezone
from utils.api_client import make_api_call
from utils.logging_config import get_logger
from utils.app_config import get_global_base_currency
from dotenv import load_dotenv

logger = get_logger(__name__)
load_dotenv()

BLOCKFROST_API_KEY = os.getenv("BLOCKFROST_API_KEY")

# Dummy cache for now

def get_balance(address: str) -> dict | None:
    """
    Fetches the ADA balance for a given wallet address using Blockfrost API.
    
    IMPORTANT: Cardano's staking model means ADA is NEVER locked when delegated.
    - All ADA is liquid (can be spent at any time)
    - Rewards are accumulated separately and can be withdrawn
    
    Returns:
        dict: {
            'liquid': float,  # Total ADA (always liquid, even if delegated)
            'rewards': float  # Accumulated staking rewards (pending withdrawal)
        }
    """
    headers = {"project_id": BLOCKFROST_API_KEY}

    try:
        # Step 1: Check if the address is a payment address or staking address
        logger.info(f"Fetching ADA balance for address: {address}")
        blockfrost_address_url = f"https://cardano-mainnet.blockfrost.io/api/v0/addresses/{address}"
        address_response = make_api_call(url=blockfrost_address_url, method="GET", headers=headers)

        if not address_response:
            logger.error(f"Failed to fetch address information for {address}.")
            return None

        # Log whether the input is a payment or staking address
        staking_address = address_response.get("stake_address", address)
        if "stake_address" in address_response:
            logger.info(f"Address {address} resolved to staking address: {staking_address}")
        else:
            logger.info(f"Address {address} is already a staking address.")

        # Step 2: Fetch account balances using the staking address
        blockfrost_account_url = f"https://cardano-mainnet.blockfrost.io/api/v0/accounts/{staking_address}"
        logger.info(f"Fetching account information for staking address: {staking_address}")
        account_response = make_api_call(url=blockfrost_account_url, method="GET", headers=headers)

        if account_response:
            # Extract balance components
            # controlled_amount: Total ADA in wallet (ALWAYS LIQUID, even if delegated)
            # reward_account_balance: Accumulated rewards (pending withdrawal)
            liquid_balance = float(account_response.get("controlled_amount", 0)) / 1e6  # Convert Lovelace to ADA
            rewards_balance = float(account_response.get("reward_account_balance", 0)) / 1e6  # Convert Lovelace to ADA

            # Log the balances fetched
            logger.info(f"Balances fetched for staking address {staking_address}: "
                        f"Liquid={liquid_balance} ADA (always liquid), Rewards={rewards_balance} ADA (pending withdrawal)")

            return {
                "liquid": liquid_balance,
                "rewards": rewards_balance  # Changed from "staked" - ADA is never locked!
            }

        logger.error(f"Failed to fetch ADA account information for staking address: {staking_address}")
        return []
        
    except Exception as e:
        logger.error(f"Error normalizing ADA staking reward: {e}")
        return []


def get_balance_and_staking_info(address: str) -> dict:
    """
    Get comprehensive balance and staking information for an ADA address.
    
    IMPORTANT: Cardano staking model means ADA is NEVER locked.
    - All ADA remains liquid (spendable at any time)
    - Rewards accumulate separately and can be withdrawn
    
    Args:
        address (str): Cardano wallet address
        
    Returns:
        dict: {
            'liquid': float,          # Total ADA (always liquid, even if delegated)
            'rewards': float,         # Accumulated staking rewards (pending withdrawal)
            'total': float,           # liquid + rewards
            'is_delegated': bool      # Whether wallet is delegating to a stake pool
        }
    """
    balance_info = get_balance(address)
    if not balance_info:
        return {
            "liquid": 0.0,
            "rewards": 0.0,
            "total": 0.0,
            "is_delegated": False
        }
    
    liquid = balance_info.get("liquid", 0.0)
    rewards = balance_info.get("rewards", 0.0)
    total = liquid + rewards
    
    return {
        "liquid": liquid,
        "rewards": rewards,
        "total": total,
        "is_delegated": rewards > 0  # If rewards exist, wallet is delegating
    }


def get_staking_history(address: str, limit: int = 50) -> list:
    """
    Get only staking rewards history for an ADA address.
    
    Args:
        address (str): Cardano wallet address
        limit (int): Maximum number of rewards to fetch
        
    Returns:
        list: List of normalized staking reward transactions
    """
    logger.info(f"Fetching ADA staking history for address {address}")
    
    staking_rewards = _fetch_ada_staking_rewards(address, limit)
    
    normalized_rewards = []
    for reward in staking_rewards:
        normalized_reward = _normalize_ada_staking_reward(reward, address)
        if normalized_reward:
            normalized_rewards.extend(normalized_reward)
    
    # Sort by timestamp (newest first)
    normalized_rewards.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_rewards)} staking rewards for {address}")
    return normalized_rewards

# Cache for transaction data (shorter TTL since transactions can be more time-sensitive)

def get_transactions(address: str, start_date: str = None, end_date: str = None, limit: int = 50) -> list:
    """
    Fetches transaction history for a given ADA address including staking rewards.
    
    Args:
        address (str): Cardano wallet address
        start_date (str): Start date in YYYY-MM-DD format (optional)
        end_date (str): End date in YYYY-MM-DD format (optional)
        limit (int): Maximum number of transactions to fetch (default: 50)
    
    Returns:
        list: List of normalized transaction dictionaries ready for Transaction model
    """
    logger.info(f"Fetching ADA transactions for address {address}")
    
    # Get regular transactions
    transactions = _fetch_ada_transactions(address, limit)
    
    # Get staking rewards
    staking_rewards = _fetch_ada_staking_rewards(address, limit)
    
    # Combine and sort all transactions
    all_transactions = transactions + staking_rewards
    
    # Filter by date range if provided
    if start_date or end_date:
        all_transactions = _filter_transactions_by_date(all_transactions, start_date, end_date)
    
    # Normalize transactions for the Transaction model
    normalized_transactions = []
    for tx in all_transactions:
        normalized_tx = _normalize_ada_transaction(tx, address)
        if normalized_tx:
            normalized_transactions.extend(normalized_tx)
    
    # Sort by timestamp (newest first)
    normalized_transactions.sort(key=lambda x: x.get('occurred_at'), reverse=True)
    
    logger.info(f"Retrieved {len(normalized_transactions)} normalized ADA transactions for {address}")
    return normalized_transactions


def _fetch_ada_transactions(address: str, limit: int = 50) -> list:
    """Fetch regular transactions from Blockfrost API."""
    headers = {"project_id": BLOCKFROST_API_KEY}
    
    try:
        # Fetch transactions for the address
        url = f"https://cardano-mainnet.blockfrost.io/api/v0/addresses/{address}/transactions"
        params = {"count": min(limit, 100), "order": "desc"}  # Blockfrost max is 100 per page
        
        response = make_api_call(url=url, method="GET", headers=headers, params=params)
        
        if not response:
            logger.warning(f"No transaction data returned for address {address}")
            return []
        
        # Fetch detailed transaction data
        detailed_transactions = []
        for tx_info in response[:limit]:  # Limit to requested number
            # Extract tx_hash from the transaction info dict
            tx_hash = tx_info.get('tx_hash') if isinstance(tx_info, dict) else tx_info
            if not tx_hash:
                logger.warning(f"Could not extract tx_hash from transaction info: {tx_info}")
                continue
            
            tx_detail = _fetch_ada_transaction_detail(tx_hash, headers)
            if tx_detail:
                tx_detail['_type'] = 'regular'  # Mark as regular transaction
                detailed_transactions.append(tx_detail)
        
        logger.info(f"Fetched {len(detailed_transactions)} regular transactions for {address}")
        return detailed_transactions
        
    except Exception as e:
        logger.error(f"Error fetching ADA transactions for address {address}: {e}")
        return []


def _fetch_ada_transaction_detail(tx_hash: str, headers: dict) -> dict:
    """Fetch detailed transaction information."""
    try:
        # Get transaction details
        tx_url = f"https://cardano-mainnet.blockfrost.io/api/v0/txs/{tx_hash}"
        tx_data = make_api_call(url=tx_url, method="GET", headers=headers)
        
        if not tx_data:
            return None
            
        # Get UTXOs (inputs and outputs)
        utxos_url = f"https://cardano-mainnet.blockfrost.io/api/v0/txs/{tx_hash}/utxos"
        utxos_data = make_api_call(url=utxos_url, method="GET", headers=headers)
        
        if utxos_data:
            tx_data['utxos'] = utxos_data
        
        return tx_data
        
    except Exception as e:
        logger.error(f"Error fetching transaction detail for {tx_hash}: {e}")
        return None


def _fetch_ada_staking_rewards(address: str, limit: int = 50) -> list:
    """Fetch staking rewards from Blockfrost API."""
    headers = {"project_id": BLOCKFROST_API_KEY}
    
    try:
        # First, get the stake address for this payment address
        address_info_url = f"https://cardano-mainnet.blockfrost.io/api/v0/addresses/{address}"
        address_info = make_api_call(url=address_info_url, method="GET", headers=headers)
        
        if not address_info:
            logger.warning(f"Could not get address info for {address}")
            return []
        
        stake_address = address_info.get('stake_address')
        if not stake_address:
            logger.info(f"No stake address found for {address} - no staking rewards")
            return []
        
        # Fetch rewards for the stake address
        rewards_url = f"https://cardano-mainnet.blockfrost.io/api/v0/accounts/{stake_address}/rewards"
        params = {"count": min(limit, 100), "order": "desc"}
        
        rewards_data = make_api_call(url=rewards_url, method="GET", headers=headers, params=params)
        
        if not rewards_data:
            logger.info(f"No staking rewards found for stake address {stake_address}")
            return []
        
        # Mark rewards with type and add context
        for reward in rewards_data:
            reward['_type'] = 'staking_reward'
            reward['_stake_address'] = stake_address
            reward['_payment_address'] = address
        
        logger.info(f"Fetched {len(rewards_data)} staking rewards for {address}")
        return rewards_data
        
    except Exception as e:
        logger.error(f"Error fetching ADA staking rewards for address {address}: {e}")
        return []


def _filter_transactions_by_date(transactions: list, start_date: str = None, end_date: str = None) -> list:
    """
    Filter transactions by date range.
    
    NOTE: Staking rewards are NOT filtered by date because:
    1. They are ongoing/periodic (not one-time events)
    2. API doesn't provide accurate timestamps
    3. External ID prevents duplicates on daily sync
    4. This matches Solana behavior
    """
    if not start_date and not end_date:
        return transactions
    
    from datetime import datetime
    
    filtered = []
    for tx in transactions:
        # Get timestamp based on transaction type
        if tx.get('_type') == 'staking_reward':
            # Always include staking rewards (date filtering not applicable)
            # They have datetime.now() timestamps and external_id prevents duplicates
            filtered.append(tx)
            continue
        else:
            # For regular transactions, use block_time
            tx_timestamp = tx.get('block_time', 0)
        
        if not tx_timestamp:
            continue
            
        tx_date = datetime.fromtimestamp(tx_timestamp).date()
        
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


def _normalize_ada_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize an ADA transaction into Transaction model format.
    Handles both regular transactions and staking rewards.
    
    Args:
        tx_data (dict): Raw transaction data from Blockfrost API
        wallet_address (str): The wallet address we're tracking
        
    Returns:
        list: List of normalized transaction dictionaries
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        tx_type = tx_data.get('_type', 'regular')
        
        if tx_type == 'staking_reward':
            return _normalize_ada_staking_reward(tx_data, wallet_address)
        else:
            return _normalize_ada_regular_transaction(tx_data, wallet_address)
            
    except Exception as e:
        logger.error(f"Error normalizing ADA transaction: {e}")
        return []


def _normalize_ada_regular_transaction(tx_data: dict, wallet_address: str) -> list:
    """
    Normalize a regular ADA transaction.
    
    Fee handling:
    - The 'fee' field on transfer_in/transfer_out is metadata (positive value)
    - A separate 'fee' type transaction is created for lot tracking (negative quantity)
    - We always create a separate fee transaction when the wallet paid a fee
    """
    from datetime import datetime, timezone
    
    transactions = []
    
    try:
        tx_hash = tx_data.get('hash')
        block_time = tx_data.get('block_time')
        fees = tx_data.get('fees', '0')
        
        if not tx_hash or not block_time:
            logger.warning(f"Missing required transaction data: hash={tx_hash}, block_time={block_time}")
            return []
        
        occurred_at = datetime.fromtimestamp(block_time, tz=timezone.utc)
        fee_ada = int(fees) / 1e6 if fees else 0  # Convert lovelace to ADA
        
        utxos = tx_data.get('utxos', {})
        inputs = utxos.get('inputs', [])
        outputs = utxos.get('outputs', [])
        
        # Calculate net effect for this address
        total_received = 0
        total_sent = 0
        
        # Check outputs for received ADA
        for output in outputs:
            if output.get('address') == wallet_address:
                for amount in output.get('amount', []):
                    if amount.get('unit') == 'lovelace':
                        total_received += int(amount.get('quantity', 0)) / 1e6
        
        # Check inputs for sent ADA
        for input_utxo in inputs:
            if input_utxo.get('address') == wallet_address:
                for amount in input_utxo.get('amount', []):
                    if amount.get('unit') == 'lovelace':
                        total_sent += int(amount.get('quantity', 0)) / 1e6
        
        # Determine if this wallet paid the fee (has inputs from this address)
        wallet_paid_fee = total_sent > 0
        
        # Calculate TRUE net amount
        net_amount = total_received - total_sent
        
        if net_amount > 0 and not wallet_paid_fee:
            # Pure incoming: received ADA and didn't pay fee
            transactions.append({
                'type': 'transfer_in',
                'symbol': 'ADA',
                'symbol_normalized': 'ADA',
                'quantity': net_amount,
                'value_native': None,
                'currency_native': None,
                'price': None,
                'fee': 0,  # We didn't pay the fee
                'fee_currency': 'ADA',
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'ada_blockchain',
                'asset_class': 'crypto',
                'external_id': f"ada_{tx_hash}",
                'notes': f"ADA received from blockchain transaction"
            })
            
        elif net_amount < 0:
            # Outgoing: sent ADA (net_amount is already negative - use as-is for outflow)
            # SPECIAL CASE: If net_amount equals -fee, this is a pure fee transaction
            # (e.g., staking delegation, governance vote) - NOT a transfer_out
            actual_transfer = abs(net_amount) - fee_ada
            
            if actual_transfer > 0.000001:  # Small threshold for floating point comparison
                # Genuine transfer out (after subtracting fee)
                transactions.append({
                    'type': 'transfer_out',
                    'symbol': 'ADA', 
                    'symbol_normalized': 'ADA',
                    'quantity': -actual_transfer,  # NEGATIVE - transfer out is an outflow (excluding fee)
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                'fee': 0,  # Fee has its own separate transaction
                'fee_currency': None,
                'blockchain_tx_hash': tx_hash,
                'occurred_at': occurred_at,
                'source': 'ada_blockchain',
                'asset_class': 'crypto',
                'external_id': f"ada_{tx_hash}",
                'notes': f"ADA sent via blockchain transaction"
            })
            
                # Create separate fee transaction for lot tracking
                if fee_ada > 0:
                    transactions.append({
                        'type': 'fee',
                        'symbol': 'ADA',
                        'symbol_normalized': 'ADA', 
                        'quantity': -fee_ada,  # NEGATIVE - fee is an outflow/consumption
                        'value_native': None,
                        'currency_native': None,
                        'price': None,
                        'fee': 0,
                        'fee_currency': None,
                        'blockchain_tx_hash': tx_hash,
                        'occurred_at': occurred_at,
                        'source': 'ada_blockchain',
                        'asset_class': 'crypto',
                        'external_id': f"ada_{tx_hash}_fee",
                        'notes': f"ADA network fee (transfer_out) | tx: {tx_hash}"
                    })
            else:
                # Pure fee transaction (staking delegation, governance, etc.)
                # net_amount is negative but equals the fee - no actual transfer
                if fee_ada > 0:
                    transactions.append({
                        'type': 'fee',
                        'symbol': 'ADA',
                        'symbol_normalized': 'ADA', 
                        'quantity': -fee_ada,  # NEGATIVE - fee is an outflow/consumption
                        'value_native': None,
                        'currency_native': None,
                        'price': None,
                        'fee': 0,
                        'fee_currency': None,
                        'blockchain_tx_hash': tx_hash,
                        'occurred_at': occurred_at,
                        'source': 'ada_blockchain',
                        'asset_class': 'crypto',
                        'external_id': f"ada_{tx_hash}_fee",
                        'notes': f"ADA network fee (staking_delegation) | tx: {tx_hash}"
                    })
                
        elif net_amount == 0 and wallet_paid_fee:
            # Pure fee transaction (e.g., governance vote, staking delegation)
            # Only create the fee transaction
            if fee_ada > 0:
                transactions.append({
                    'type': 'fee',
                    'symbol': 'ADA',
                    'symbol_normalized': 'ADA', 
                    'quantity': -fee_ada,  # NEGATIVE - fee is an outflow/consumption
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,
                    'fee_currency': None,
                    'blockchain_tx_hash': tx_hash,
                    'occurred_at': occurred_at,
                    'source': 'ada_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"ada_{tx_hash}_fee",
                    'notes': f"ADA network fee (staking_delegation) | tx: {tx_hash}"
                })
                
        elif net_amount > 0 and wallet_paid_fee:
            # Received ADA but paid fee (e.g., reward claim, consolidation)
            # NOTE: For staking reward claims, the rewards are tracked per epoch separately,
            # so we only record the fee here, not the reward amount
            if fee_ada > 0:
                transactions.append({
                    'type': 'fee',
                    'symbol': 'ADA',
                    'symbol_normalized': 'ADA', 
                    'quantity': -fee_ada,  # NEGATIVE - fee is an outflow/consumption
                    'value_native': None,
                    'currency_native': None,
                    'price': None,
                    'fee': 0,
                    'fee_currency': None,
                    'blockchain_tx_hash': tx_hash,
                    'occurred_at': occurred_at,
                    'source': 'ada_blockchain',
                    'asset_class': 'crypto',
                    'external_id': f"ada_{tx_hash}_fee",
                    'notes': f"ADA network fee (consolidation) | tx: {tx_hash}"
                })
            
    except Exception as e:
        logger.error(f"Error normalizing ADA regular transaction {tx_data.get('hash', 'unknown')}: {e}")
        return []
    
    return transactions


def _normalize_ada_staking_reward(reward_data: dict, wallet_address: str) -> list:
    """Normalize an ADA staking reward into Transaction model format."""
    from datetime import datetime, timezone
    
    try:
        epoch = reward_data.get('epoch')
        amount = reward_data.get('amount')
        pool_id = reward_data.get('pool_id', 'unknown')
        
        if not epoch or not amount:
            logger.warning(f"Missing required reward data: epoch={epoch}, amount={amount}")
            return []
        
        # Convert lovelace to ADA
        ada_amount = int(amount) / 1e6
        
        if ada_amount <= 0:
            return []  # Skip zero or negative rewards
        
        # FIXED: Get actual epoch end timestamp from Blockfrost API for German tax compliance
        # Rewards are paid at the END of each epoch, so we need the epoch end time
        epoch_timestamp = _get_ada_epoch_end_timestamp(epoch)
        
        if not epoch_timestamp:
            # Fallback: Use current time if API call fails (better than no data)
            logger.warning(f"Failed to get epoch {epoch} timestamp, using current time as fallback")
            epoch_timestamp = datetime.now(timezone.utc)
        
        # Create unique external ID for this reward
        external_id = f"ada_staking_reward_{epoch}_{pool_id}_{wallet_address}"
        
        # Get global base currency from config
        base_currency = get_global_base_currency()
        
        return [{
            'type': 'staking_reward',
            'symbol': 'ADA',
            'symbol_normalized': 'ADA',
            'quantity': ada_amount,
            'value_native': None,
            'currency_native': None,
            'currency_base': base_currency,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': None,  # Staking rewards don't have tx hashes
            'occurred_at': epoch_timestamp,  # FIXED: Actual epoch end time from blockchain API
            'source': 'ada_blockchain',
            'asset_class': 'crypto',
            'external_id': external_id,
            'notes': f"ADA staking reward from epoch {epoch}, pool {pool_id} (paid at epoch end)"
        }]
        
    except Exception as e:
        logger.error(f"Error normalizing ADA staking reward: {e}")
        return []


def _get_ada_epoch_end_timestamp(epoch: int) -> datetime | None:
    """Get the actual end timestamp for a Cardano epoch from Blockfrost API."""
    from datetime import datetime, timezone
    
    headers = {"project_id": BLOCKFROST_API_KEY}
    
    try:
        # Query Blockfrost for epoch details
        url = f"https://cardano-mainnet.blockfrost.io/api/v0/epochs/{epoch}"
        response = make_api_call(url=url, method="GET", headers=headers)
        
        if response and 'end_time' in response:
            # end_time is Unix timestamp (seconds since epoch)
            end_timestamp = int(response['end_time'])
            epoch_end = datetime.fromtimestamp(end_timestamp, tz=timezone.utc)
            logger.debug(f"Epoch {epoch} ended at {epoch_end.isoformat()}")
            return epoch_end
        else:
            logger.warning(f"No end_time in Blockfrost response for epoch {epoch}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to get epoch {epoch} timestamp from Blockfrost: {e}")
        return None
