"""
CRUD operations for crypto staking transactions.

Used for blockchains where transaction list APIs don't work (e.g., BNB).
Stores tx hashes manually, fetches details via RPC automatically.
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import CryptoStakingTransaction, CryptoWallet
from typing import List, Optional, Dict, Tuple
from decimal import Decimal
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def add_staking_transaction(
    db: Session,
    wallet_id: int,
    tx_hash: str,
    tx_type: str,
    symbol: str,
    amount: Optional[Decimal] = None,
    validator_address: Optional[str] = None,
    block_number: Optional[int] = None,
    occurred_at: Optional[datetime] = None,
    staked_balance_snapshot: Optional[Decimal] = None,
    accumulated_rewards_snapshot: Optional[Decimal] = None,
    linked_tx_hash: Optional[str] = None
) -> Tuple[Optional[CryptoStakingTransaction], str]:
    """
    Add a staking transaction hash to database with optional parsed details.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        tx_hash: Transaction hash (0x...)
        tx_type: 'delegate', 'undelegate', or 'claim'
        symbol: 'BNB', 'SOL', etc.
        amount: Optional parsed amount
        validator_address: Optional validator/pool address
        block_number: Optional block number
        occurred_at: Optional transaction timestamp
        staked_balance_snapshot: Optional total staked principal at undelegate time
        accumulated_rewards_snapshot: Optional accumulated rewards at undelegate time
        linked_tx_hash: Optional linked undelegate tx hash (for claims)
    
    Returns:
        Tuple of (transaction object or None, status message)
    """
    try:
        # Validate tx_type
        valid_types = ['delegate', 'undelegate', 'claim']
        if tx_type not in valid_types:
            return None, f"Invalid tx_type. Must be one of: {valid_types}"
        
        # Check if tx_hash already exists
        existing = db.query(CryptoStakingTransaction).filter_by(tx_hash=tx_hash).first()
        if existing:
            return None, f"Transaction {tx_hash} already exists"
        
        # Verify wallet exists
        wallet = db.query(CryptoWallet).filter_by(id=wallet_id).first()
        if not wallet:
            return None, f"Wallet ID {wallet_id} not found"
        
        # Create new staking transaction record
        staking_tx = CryptoStakingTransaction(
            wallet_id=wallet_id,
            tx_hash=tx_hash,
            tx_type=tx_type,
            symbol=symbol,
            amount=amount,
            validator_address=validator_address,
            block_number=block_number,
            occurred_at=occurred_at,
            staked_balance_snapshot=staked_balance_snapshot,
            accumulated_rewards_snapshot=accumulated_rewards_snapshot,
            linked_tx_hash=linked_tx_hash,
            created_at=datetime.now(timezone.utc)
        )
        
        db.add(staking_tx)
        db.flush()  # Flush to get ID without committing (caller handles commit)
        
        logger.info(f"Added {tx_type} transaction {tx_hash} for wallet {wallet_id}")
        return staking_tx, "Transaction added successfully"
        
    except Exception as e:
        logger.error(f"Error adding staking transaction: {e}")
        return None, f"Error: {str(e)}"


def get_staking_transactions(
    db: Session,
    wallet_id: Optional[int] = None,
    symbol: Optional[str] = None,
    tx_type: Optional[str] = None,
    unprocessed_only: bool = False
) -> List[CryptoStakingTransaction]:
    """
    Get staking transactions with optional filters.
    
    Args:
        db: Database session
        wallet_id: Filter by wallet ID
        symbol: Filter by symbol
        tx_type: Filter by transaction type
        unprocessed_only: Only return transactions not yet processed
    
    Returns:
        List of CryptoStakingTransaction objects
    """
    try:
        query = db.query(CryptoStakingTransaction)
        
        if wallet_id:
            query = query.filter_by(wallet_id=wallet_id)
        
        if symbol:
            query = query.filter_by(symbol=symbol)
        
        if tx_type:
            query = query.filter_by(tx_type=tx_type)
        
        if unprocessed_only:
            query = query.filter(CryptoStakingTransaction.processed_at.is_(None))
        
        return query.order_by(CryptoStakingTransaction.created_at.desc()).all()
        
    except Exception as e:
        logger.error(f"Error fetching staking transactions: {e}")
        return []


def update_staking_transaction(
    db: Session,
    tx_hash: str,
    amount: Optional[Decimal] = None,
    validator_address: Optional[str] = None,
    block_number: Optional[int] = None,
    mark_processed: bool = False
) -> Tuple[bool, str]:
    """
    Update staking transaction details after fetching from blockchain.
    
    Args:
        db: Database session
        tx_hash: Transaction hash
        amount: Parsed amount
        validator_address: Validator/pool address
        block_number: Block number
        mark_processed: Whether to mark as processed
    
    Returns:
        Tuple of (success bool, message)
    """
    try:
        staking_tx = db.query(CryptoStakingTransaction).filter_by(tx_hash=tx_hash).first()
        
        if not staking_tx:
            return False, f"Transaction {tx_hash} not found"
        
        if amount is not None:
            staking_tx.amount = amount
        
        if validator_address:
            staking_tx.validator_address = validator_address
        
        if block_number:
            staking_tx.block_number = block_number
        
        if mark_processed:
            staking_tx.processed_at = datetime.now(timezone.utc)
        
        db.commit()
        
        logger.info(f"Updated staking transaction {tx_hash}")
        return True, "Transaction updated successfully"
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating staking transaction: {e}")
        return False, f"Error: {str(e)}"


def delete_staking_transaction(db: Session, tx_hash: str) -> Tuple[bool, str]:
    """
    Delete a staking transaction.
    
    Args:
        db: Database session
        tx_hash: Transaction hash
    
    Returns:
        Tuple of (success bool, message)
    """
    try:
        staking_tx = db.query(CryptoStakingTransaction).filter_by(tx_hash=tx_hash).first()
        
        if not staking_tx:
            return False, f"Transaction {tx_hash} not found"
        
        db.delete(staking_tx)
        db.commit()
        
        logger.info(f"Deleted staking transaction {tx_hash}")
        return True, "Transaction deleted successfully"
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting staking transaction: {e}")
        return False, f"Error: {str(e)}"


def calculate_staking_summary(db: Session, wallet_id: int, symbol: str) -> Dict:
    """
    Calculate staking summary from transaction history.
    
    Formula:
    - Current staked = sum(delegates) - sum(claims)
    - Rewards realized = sum(claims) - sum(undelegates)
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        symbol: Cryptocurrency symbol
    
    Returns:
        Dict with staking summary
    """
    try:
        txs = get_staking_transactions(db, wallet_id=wallet_id, symbol=symbol)
        
        total_delegated = Decimal(0)
        total_undelegated = Decimal(0)
        total_claimed = Decimal(0)
        
        for tx in txs:
            if tx.amount is None:
                continue
            
            if tx.tx_type == 'delegate':
                total_delegated += tx.amount
            elif tx.tx_type == 'undelegate':
                total_undelegated += tx.amount
            elif tx.tx_type == 'claim':
                total_claimed += tx.amount
        
        # Current staked = delegated - claimed (claim removes from staking, not undelegate)
        current_staked = total_delegated - total_claimed
        
        # Rewards = claimed - undelegated (claim includes undelegated + rewards)
        rewards_realized = total_claimed - total_undelegated
        
        return {
            'total_delegated': float(total_delegated),
            'total_undelegated': float(total_undelegated),
            'total_claimed': float(total_claimed),
            'current_staked': float(current_staked),
            'rewards_realized': float(rewards_realized),
            'transaction_count': len(txs)
        }
        
    except Exception as e:
        logger.error(f"Error calculating staking summary: {e}")
        return {
            'total_delegated': 0.0,
            'total_undelegated': 0.0,
            'total_claimed': 0.0,
            'current_staked': 0.0,
            'rewards_realized': 0.0,
            'transaction_count': 0
        }


def get_most_recent_undelegate(
    db: Session,
    wallet_id: int,
    validator_address: Optional[str] = None
) -> Optional[str]:
    """
    Find the most recent undelegate transaction for a wallet (optionally for specific validator).
    Used to link claim transactions to their corresponding undelegate.
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        validator_address: Optional validator address to filter by
    
    Returns:
        Transaction hash of most recent undelegate, or None if not found
    """
    try:
        query = db.query(CryptoStakingTransaction).filter(
            and_(
                CryptoStakingTransaction.wallet_id == wallet_id,
                CryptoStakingTransaction.tx_type == 'undelegate'
            )
        )
        
        # Filter by validator if specified
        if validator_address:
            query = query.filter_by(validator_address=validator_address)
        
        # Get most recent by occurred_at, fallback to created_at
        undelegate = query.order_by(
            CryptoStakingTransaction.occurred_at.desc().nullslast(),
            CryptoStakingTransaction.created_at.desc()
        ).first()
        
        if undelegate:
            logger.debug(f"Found most recent undelegate: {undelegate.tx_hash} for wallet {wallet_id}")
            return undelegate.tx_hash
        
        logger.warning(f"No undelegate transaction found for wallet {wallet_id}, validator {validator_address}")
        return None
        
    except Exception as e:
        logger.error(f"Error finding most recent undelegate: {e}")
        return None


def process_staking_claim(
    db: Session,
    wallet_id: int,
    claim_tx_hash: str,
    linked_undelegate_tx_hash: str
) -> Tuple[bool, str]:
    """
    Process a claim transaction to calculate and create staking_reward transaction.
    
    Uses the staked_balance_snapshot from the linked undelegate to calculate
    the reward percentage, then applies it to the claim amount.
    
    Formula:
        Principal = SUM(delegates) - SUM(previous claims)
        Accumulated Rewards = staked_balance_snapshot - Principal
        Reward % = Accumulated Rewards / staked_balance_snapshot
        Staking Reward = claim_amount * reward %
    
    Args:
        db: Database session
        wallet_id: CryptoWallet ID
        claim_tx_hash: Hash of claim transaction
        linked_undelegate_tx_hash: Hash of linked undelegate transaction
    
    Returns:
        Tuple of (success bool, message)
    """
    from models import Transaction
    from crud.crud_base import create_transaction_idempotent
    
    try:
        # Get claim transaction
        claim_tx = db.query(CryptoStakingTransaction).filter_by(
            tx_hash=claim_tx_hash
        ).first()
        
        if not claim_tx:
            return False, f"Claim transaction {claim_tx_hash} not found"
        
        # Get linked undelegate transaction
        undelegate_tx = db.query(CryptoStakingTransaction).filter_by(
            tx_hash=linked_undelegate_tx_hash
        ).first()
        
        if not undelegate_tx:
            return False, f"Linked undelegate transaction {linked_undelegate_tx_hash} not found"
        
        # Validate we have necessary data
        if not claim_tx.amount or claim_tx.amount <= 0:
            return False, "Claim amount is missing or invalid"
        
        if not undelegate_tx.staked_balance_snapshot or undelegate_tx.staked_balance_snapshot <= 0:
            return False, "Linked undelegate missing staked_balance_snapshot"
        
        # Calculate principal (sum of all delegates - sum of previous claims)
        all_txs = db.query(CryptoStakingTransaction).filter_by(
            wallet_id=wallet_id,
            symbol=claim_tx.symbol
        ).all()
        
        total_delegated = Decimal('0')
        total_claimed_before = Decimal('0')
        
        for tx in all_txs:
            if not tx.amount:
                continue
            
            if tx.tx_type == 'delegate':
                total_delegated += tx.amount
            elif tx.tx_type == 'claim' and tx.occurred_at and claim_tx.occurred_at:
                # Only count claims before this one
                if tx.occurred_at < claim_tx.occurred_at:
                    total_claimed_before += tx.amount
        
        principal = total_delegated - total_claimed_before
        
        # Calculate staking reward - two methods depending on data availability:
        # 
        # METHOD 1: If we have snapshots from undelegate logs (accurate, on-chain data)
        # Use the accumulated_rewards_snapshot which was parsed from the undelegate event
        #
        # METHOD 2: If snapshots are missing/zero (old transactions, RPC nodes purged data)
        # Calculate reward as difference between claim amount and undelegate amount
        # This works because: claim = principal + rewards, undelegate = principal
        
        accumulated_rewards_snapshot = undelegate_tx.accumulated_rewards_snapshot or Decimal('0')
        
        if accumulated_rewards_snapshot > 0:
            # METHOD 1: We have accurate snapshot from transaction logs
            total_staked_with_rewards = undelegate_tx.staked_balance_snapshot + accumulated_rewards_snapshot
            accumulated_rewards = accumulated_rewards_snapshot
            
            # Calculate reward percentage (rewards / total)
            if total_staked_with_rewards > 0:
                reward_percentage = accumulated_rewards / total_staked_with_rewards
            else:
                reward_percentage = Decimal('0')
            
            staking_reward = claim_tx.amount * reward_percentage
            
            logger.info(f"Reward calculation (from snapshots): "
                       f"staked={undelegate_tx.staked_balance_snapshot}, "
                       f"rewards={accumulated_rewards_snapshot}, "
                       f"total={total_staked_with_rewards}, "
                       f"reward_pct={reward_percentage*100:.4f}%, "
                       f"claim={claim_tx.amount}, "
                       f"staking_reward={staking_reward}")
        else:
            # METHOD 2: No snapshot available, calculate from claim vs undelegate difference
            # Claim amount includes principal + rewards
            # Undelegate amount is just principal
            # Therefore: rewards = claim - undelegate
            staking_reward = claim_tx.amount - undelegate_tx.amount
            
            logger.info(f"Reward calculation (from amounts): "
                       f"claim={claim_tx.amount}, "
                       f"undelegate={undelegate_tx.amount}, "
                       f"staking_reward={staking_reward}")
        
        # Check if staking_reward transaction already exists
        existing_reward = db.query(Transaction).filter_by(
            blockchain_tx_hash=claim_tx_hash,
            type='staking_reward'
        ).first()
        
        if existing_reward:
            logger.info(f"Staking reward transaction already exists for {claim_tx_hash}")
            return True, "Staking reward already recorded"
        
        # Create staking_reward transaction in main transactions table
        # Get portfolio_id from wallet's account
        wallet = db.query(CryptoWallet).filter_by(id=wallet_id).first()
        portfolio_id = None
        currency_base = 'EUR'  # Default to EUR
        
        if wallet and wallet.account_id:
            from models import Account, Portfolio
            account = db.query(Account).filter_by(id=wallet.account_id).first()
            if account and account.portfolio_id:
                portfolio_id = account.portfolio_id
                portfolio = db.query(Portfolio).filter_by(id=portfolio_id).first()
                if portfolio and portfolio.currency_base:
                    currency_base = portfolio.currency_base
        
        reward_tx_data = {
            'type': 'staking_reward',
            'symbol': claim_tx.symbol,
            'symbol_normalized': claim_tx.symbol,
            'quantity': float(staking_reward),
            'value_native': None,  # Will be populated by price enrichment
            'currency_native': None,
            'price': None,
            'fee': 0,
            'fee_currency': None,
            'blockchain_tx_hash': claim_tx_hash,
            'occurred_at': claim_tx.occurred_at,
            'source': 'staking_claim_calculation',
            'asset_class': 'crypto',
            'external_id': f"{claim_tx.symbol.lower()}_{claim_tx_hash}_staking_reward",
            'notes': f"Staking reward from claim (calculated: {float(reward_percentage)*100:.2f}% of {float(claim_tx.amount)} {claim_tx.symbol})",
            'portfolio_id': portfolio_id,
            'currency_base': currency_base,
        }
        
        # Add wallet reference for enrichment
        if wallet:
            reward_tx_data['from_crypto_wallet_id'] = wallet.id
        
        reward_tx = create_transaction_idempotent(db, reward_tx_data)
        
        if reward_tx:
            logger.info(f"✅ Created staking_reward: {float(staking_reward)} {claim_tx.symbol} "
                       f"({float(reward_percentage)*100:.2f}% of {float(claim_tx.amount)})")
            return True, f"Staking reward calculated and recorded: {float(staking_reward)} {claim_tx.symbol}"
        else:
            return False, "Failed to create staking_reward transaction"
        
    except Exception as e:
        logger.error(f"Error processing staking claim: {e}", exc_info=True)
        return False, f"Error: {str(e)}"
