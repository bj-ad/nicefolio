"""
CRUD operations for CryptoTransferLink model.
Handles simple database operations for transfer links only.

For transfer detection and linking orchestration, use:
    service/crypto_transfer_service.py
    
This module contains ONLY database operations:
- create_transfer_link()
- link_transactions()
- get_linked_transactions()
- get_transfer_link_by_transaction_id()
- unlink_transfer()
- get_all_transfer_links_for_account()
- validate_transfer_link()
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import CryptoTransferLink, Transaction, CryptoWallet
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc

logger = get_logger(__name__)


def create_transfer_link(
    db: Session,
    tx_hash: str,
    source: str,
    note: Optional[str] = None
) -> CryptoTransferLink:
    """
    Create a new transfer link.
    
    Args:
        db: Database session
        tx_hash: Primary transaction hash
        source: Source of the link (e.g., 'manual', 'auto_detected')
        note: Optional note about the link
    
    Returns:
        CryptoTransferLink: Created link
    """
    # Check if link already exists
    existing = db.query(CryptoTransferLink).filter(
        CryptoTransferLink.tx_hash == tx_hash
    ).first()
    
    if existing:
        logger.debug(f"Transfer link for {tx_hash} already exists")
        return existing
    
    link = CryptoTransferLink(
        tx_hash=tx_hash,
        source=source,
        note=note,
        created_at=now_utc()
    )
    
    db.add(link)
    db.commit()
    db.refresh(link)
    
    logger.info(f"Created transfer link for {tx_hash} from source {source}")
    return link


def link_transactions(
    db: Session,
    tx_hash: str,
    transaction_ids: List[int],
    source: str,
    note: Optional[str] = None
) -> Tuple[CryptoTransferLink, int]:
    """
    Link multiple transactions together (e.g., send from one wallet, receive in another).
    
    Args:
        db: Database session
        tx_hash: Primary transaction hash
        transaction_ids: List of transaction IDs to link
        source: Source of the link (e.g., 'manual', 'auto_detected')
        note: Optional note
    
    Returns:
        tuple: (CryptoTransferLink, number of transactions linked)
    """
    # Create or get link
    link = create_transfer_link(db, tx_hash, source, note)
    
    # Link all transactions
    linked_count = 0
    for tx_id in transaction_ids:
        tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
        if tx:
            tx.crypto_transfer_link_id = link.id
            linked_count += 1
            logger.debug(f"Linked transaction {tx_id} to link {link.id}")
        else:
            logger.warning(f"Transaction {tx_id} not found")
    
    db.commit()
    logger.info(f"Linked {linked_count} transactions to transfer link {link.id}")
    
    return link, linked_count


def get_linked_transactions(
    db: Session,
    tx_hash: str
) -> List[Transaction]:
    """
    Get all transactions linked to a transfer link.
    
    Args:
        db: Database session
        tx_hash: Transaction hash of the link
    
    Returns:
        list: List of linked Transaction records
    """
    link = db.query(CryptoTransferLink).filter(
        CryptoTransferLink.tx_hash == tx_hash
    ).first()
    
    if not link:
        logger.debug(f"No transfer link found for {tx_hash}")
        return []
    
    transactions = db.query(Transaction).filter(
        Transaction.crypto_transfer_link_id == link.id
    ).all()
    
    return transactions


def get_transfer_link_by_transaction_id(
    db: Session,
    transaction_id: int
) -> Optional[Dict]:
    """
    Get transfer link details for a specific transaction.
    
    Args:
        db: Database session
        transaction_id: Transaction ID
    
    Returns:
        dict: Transfer link info with all linked transactions, or None
    """
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    
    if not tx or not tx.crypto_transfer_link_id:
        return None
    
    link = db.query(CryptoTransferLink).filter(
        CryptoTransferLink.id == tx.crypto_transfer_link_id
    ).first()
    
    if not link:
        return None
    
    # Get all linked transactions
    linked_txs = db.query(Transaction).filter(
        Transaction.crypto_transfer_link_id == link.id
    ).all()
    
    return {
        'link_id': link.id,
        'tx_hash': link.tx_hash,
        'source': link.source,
        'note': link.note,
        'created_at': link.created_at,
        'transactions': [
            {
                'id': t.id,
                'type': t.type,
                'symbol': t.symbol,
                'quantity': float(t.quantity) if t.quantity else 0,
                'occurred_at': t.occurred_at,
                'blockchain_tx_hash': t.blockchain_tx_hash
            }
            for t in linked_txs
        ]
    }


def unlink_transfer(
    db: Session,
    tx_hash: str
) -> int:
    """
    Remove transfer link and unlink all associated transactions.
    
    Args:
        db: Database session
        tx_hash: Transaction hash of the link
    
    Returns:
        int: Number of transactions unlinked
    """
    link = db.query(CryptoTransferLink).filter(
        CryptoTransferLink.tx_hash == tx_hash
    ).first()
    
    if not link:
        logger.warning(f"Transfer link for {tx_hash} not found")
        return 0
    
    # Unlink all transactions
    transactions = db.query(Transaction).filter(
        Transaction.crypto_transfer_link_id == link.id
    ).all()
    
    for tx in transactions:
        tx.crypto_transfer_link_id = None
    
    # Delete link
    db.delete(link)
    db.commit()
    
    count = len(transactions)
    logger.info(f"Unlinked {count} transactions and deleted transfer link {link.id}")
    
    return count


def get_all_transfer_links_for_account(
    db: Session,
    account_id: int,
    limit: Optional[int] = None
) -> List[Dict]:
    """
    Get all transfer links for an account's wallets.
    
    Args:
        db: Database session
        account_id: Account ID
        limit: Maximum number of links to return
    
    Returns:
        list: List of transfer link details
    """
    # Get wallet IDs for account
    wallet_ids = db.query(CryptoWallet.id).filter(
        CryptoWallet.account_id == account_id
    ).all()
    wallet_ids = [w[0] for w in wallet_ids]
    
    if not wallet_ids:
        return []
    
    # Get all transactions with links
    query = db.query(Transaction).filter(
        and_(
            or_(
                Transaction.from_crypto_wallet_id.in_(wallet_ids),
                Transaction.to_crypto_wallet_id.in_(wallet_ids)
            ),
            Transaction.crypto_transfer_link_id.isnot(None)
        )
    )
    
    if limit:
        query = query.limit(limit)
    
    transactions = query.all()
    
    # Group by link ID
    links_dict = {}
    for tx in transactions:
        link_id = tx.crypto_transfer_link_id
        if link_id not in links_dict:
            link = db.query(CryptoTransferLink).filter(CryptoTransferLink.id == link_id).first()
            if link:
                links_dict[link_id] = {
                    'link_id': link.id,
                    'tx_hash': link.tx_hash,
                    'source': link.source,
                    'note': link.note,
                    'created_at': link.created_at,
                    'transactions': []
                }
        
        if link_id in links_dict:
            # Determine wallet_id based on transaction type
            wallet_id = tx.from_crypto_wallet_id if tx.type == 'transfer_out' else tx.to_crypto_wallet_id
            
            links_dict[link_id]['transactions'].append({
                'id': tx.id,
                'wallet_id': wallet_id,
                'type': tx.type,
                'symbol': tx.symbol,
                'quantity': float(tx.quantity) if tx.quantity else 0,
                'occurred_at': tx.occurred_at,
                'blockchain_tx_hash': tx.blockchain_tx_hash
            })
    
    return list(links_dict.values())


def validate_transfer_link(
    db: Session,
    link_id: int
) -> Dict:
    """
    Validate a transfer link for consistency.
    Check that send/receive amounts match (accounting for fees).
    
    Args:
        db: Database session
        link_id: Transfer link ID
    
    Returns:
        dict: Validation report
    """
    link = db.query(CryptoTransferLink).filter(CryptoTransferLink.id == link_id).first()
    
    if not link:
        return {'valid': False, 'error': 'Link not found'}
    
    # Get all linked transactions
    transactions = db.query(Transaction).filter(
        Transaction.crypto_transfer_link_id == link_id
    ).all()
    
    if len(transactions) < 2:
        return {
            'valid': False,
            'link_id': link_id,
            'error': 'Transfer link must have at least 2 transactions',
            'transaction_count': len(transactions)
        }
    
    # Group by transaction type
    sends = [tx for tx in transactions if tx.type == 'transfer_out']
    receives = [tx for tx in transactions if tx.type == 'transfer_in']
    
    if not sends or not receives:
        return {
            'valid': False,
            'link_id': link_id,
            'error': 'Transfer link must have both send and receive transactions',
            'sends': len(sends),
            'receives': len(receives)
        }
    
    # Check amounts (group by symbol) - use qty for crypto amounts
    symbols = set(tx.symbol for tx in transactions)
    discrepancies = {}
    
    for symbol in symbols:
        send_amount = sum(abs(float(tx.quantity)) for tx in sends if tx.symbol == symbol and tx.quantity)
        receive_amount = sum(abs(float(tx.quantity)) for tx in receives if tx.symbol == symbol and tx.quantity)
        
        # Allow 1% tolerance for fees
        if abs(send_amount - receive_amount) > send_amount * 0.01:
            discrepancies[symbol] = {
                'send_amount': float(send_amount),
                'receive_amount': float(receive_amount),
                'difference': float(receive_amount - send_amount),
                'percentage_diff': float((receive_amount - send_amount) / send_amount * 100) if send_amount > 0 else 0
            }
    
    valid = len(discrepancies) == 0
    
    return {
        'valid': valid,
        'link_id': link_id,
        'tx_hash': link.tx_hash,
        'transaction_count': len(transactions),
        'sends': len(sends),
        'receives': len(receives),
        'symbols': list(symbols),
        'discrepancies': discrepancies
    }
