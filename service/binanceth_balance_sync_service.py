"""
Binance.th Automated Balance Sync Service
Automatically syncs balances and infers trades from balance changes.
This service is designed to run in daily jobs.
"""

from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import SessionLocal
from models import Transaction, Account, CryptoBalance
from service.binanceth_service import fetch_account_balances
from service.binanceth_crypto_sync_service import sync_all_crypto_transactions
from utils.logging_config import get_logger
from utils.datetime_utils import now_utc
from utils.app_config import load_app_config
from utils.source_mapping_loader import get_source_mapping_loader

logger = get_logger(__name__)

# Load config
config = load_app_config()
binanceth_config = config.get('binanceth', {})
TRADING_FEE_PERCENT = Decimal(str(binanceth_config.get('trading_fee_percent', 0.25)))

# Load account and portfolio IDs from source_mapping.yaml (config-driven approach)
source_mapping_loader = get_source_mapping_loader()
BINANCETH_ACCOUNT_ID = source_mapping_loader.get_account_id('BinanceTH')
BINANCETH_DEFAULT_PORTFOLIO_ID = source_mapping_loader.get_default_portfolio_id('BinanceTH')


def get_stored_balances(account_id: int) -> Dict[str, Decimal]:
    """
    Get stored balances from CryptoBalance table for an exchange account.
    
    Args:
        account_id: Exchange account ID (not wallet_id)
    
    Returns:
        dict: {asset: balance}
    """
    db = SessionLocal()
    try:
        balances = db.query(CryptoBalance).filter(
            CryptoBalance.account_id == account_id
        ).all()
        
        return {b.symbol: Decimal(str(b.balance)) for b in balances}
        
    finally:
        db.close()


def store_balances(account_id: int, balances: List[Dict]) -> Dict[str, int]:
    """
    Store current balances from Binance.th, categorizing them appropriately:
    1. HODL crypto (BTC, ETH, etc.) → CryptoBalance table (for Portfolio 5)
    2. Cash (THB + stablecoins) → Position table (for Portfolio 8 - Broker Cash Pool)
    3. Other assets (ERA, NXPC, etc.) → Skip with debug log
    
    Args:
        account_id: Exchange account ID (Binance.th = account_id 3)
        balances: List of balance dicts from API
    
    Returns:
        dict: {
            'crypto_stored': int,  # HODL crypto balances stored
            'cash_stored': int,    # Cash/stablecoin positions updated
            'skipped': int         # Unknown/non-tracked assets skipped
        }
    """
    from utils.app_config import load_app_config
    from utils.source_mapping_loader import get_source_mapping_loader
    from crud.crud_position import get_or_create_position
    
    # Load configuration
    config = load_app_config()
    HODL_SYMBOLS = set(config.get('hodl_symbols', []))
    STABLECOIN_SYMBOLS = set(config.get('stablecoin_symbols', []))
    CASH_PORTFOLIO_ID = get_source_mapping_loader().get_cash_portfolio_id('BinanceTH')
    
    db = SessionLocal()
    crypto_count = 0
    cash_count = 0
    skipped_count = 0
    
    try:
        now = now_utc()
        
        for balance in balances:
            asset = balance['asset']
            free = Decimal(str(balance.get('free', 0)))
            locked = Decimal(str(balance.get('locked', 0)))
            total = free + locked
            
            # Skip zero balances
            if total == 0:
                continue
            
            # Category 1: HODL Crypto → CryptoBalance table
            if asset in HODL_SYMBOLS:
                existing = db.query(CryptoBalance).filter(
                    CryptoBalance.account_id == account_id,
                    CryptoBalance.symbol == asset,
                    CryptoBalance.balance_type == 'liquid'
                ).first()
                
                if existing:
                    existing.balance = total  # type: ignore
                    existing.ts = now  # type: ignore
                else:
                    new_balance = CryptoBalance(
                        account_id=account_id,
                        symbol=asset,
                        balance_type='liquid',
                        balance=total,
                        ts=now
                    )
                    db.add(new_balance)
                
                crypto_count += 1
                logger.debug(f"Stored HODL crypto balance: {asset} = {total}")
            
            # Category 2: Cash (THB + Stablecoins) → Position table in Portfolio 8
            elif asset == 'THB' or asset in STABLECOIN_SYMBOLS:
                # Get or create position in Broker Cash Pool (Portfolio 8)
                position = get_or_create_position(
                    db=db,
                    portfolio_id=CASH_PORTFOLIO_ID,
                    symbol=asset,
                    asset_class='cash',
                    symbol_normalized=asset
                )
                
                # Update position quantity (cash balance)
                position.quantity = total  # type: ignore
                position.last_updated = now  # type: ignore
                
                # For cash, cost basis = quantity (1:1)
                position.cost_basis_base = total  # type: ignore
                position.avg_price_base = Decimal('1')  # type: ignore
                
                cash_count += 1
                logger.info(f"Updated cash position in Portfolio 8: {asset} = {total}")
            
            # Category 3: Other assets → Skip
            else:
                skipped_count += 1
                logger.debug(f"Skipping non-tracked asset {asset} (balance: {total})")
        
        db.commit()
        
        logger.info(
            f"Balance storage complete for account {account_id}: "
            f"{crypto_count} HODL crypto, {cash_count} cash, {skipped_count} skipped"
        )
        
        return {
            'crypto_stored': crypto_count,
            'cash_stored': cash_count,
            'skipped': skipped_count
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to store balances: {e}", exc_info=True)
        return {'crypto_stored': 0, 'cash_stored': 0, 'skipped': 0}
    finally:
        db.close()


def infer_trades_from_balance_changes(
    account_id: int,
    portfolio_id: int,
    old_balances: Dict[str, Decimal],
    new_balances: Dict[str, Decimal],
    time_window_hours: int = 24
) -> Tuple[int, int]:
    """
    DEPRECATED: This function has been disabled.
    
    Infer trades by comparing old and new balances.
    
    DEPRECATION REASON:
    - Creates unreliable/duplicate transactions
    - Cannot distinguish between trades vs deposits/withdrawals accurately  
    - Currency and price data is missing/incorrect (uses crypto symbol instead of USD)
    - Trades should be entered manually through the GUI
    
    Logic (historical, no longer active):
    1. Find assets that decreased (sold/spent)
    2. Find assets that increased (bought/received)
    3. Match decreases with increases to form trades
    4. Filter out known deposits/withdrawals
    5. Create inferred trade transactions
    
    Args:
        account_id: Account ID
        portfolio_id: Portfolio ID
        old_balances: Previous balances {asset: amount}
        new_balances: Current balances {asset: amount}
        time_window_hours: Look back this many hours for deposits/withdrawals
    
    Returns:
        tuple[int, int]: (trades_created, trades_failed)
    """
    logger.info(f"Inferring trades from balance changes for account {account_id}")
    
    # Calculate balance changes
    all_assets = set(old_balances.keys()) | set(new_balances.keys())
    
    increases = {}  # Assets that increased
    decreases = {}  # Assets that decreased
    
    for asset in all_assets:
        old_amount = old_balances.get(asset, Decimal('0'))
        new_amount = new_balances.get(asset, Decimal('0'))
        change = new_amount - old_amount
        
        if change > 0:
            increases[asset] = change
        elif change < 0:
            decreases[asset] = abs(change)
    
    logger.info(f"Balance changes: {len(increases)} increases, {len(decreases)} decreases")
    
    if not increases or not decreases:
        logger.info("No balance changes that indicate trades")
        return 0, 0
    
    # Filter out known deposits/withdrawals
    cutoff_time = now_utc() - timedelta(hours=time_window_hours)
    
    db = SessionLocal()
    try:
        # Get recent deposits/withdrawals/portfolio_transfers
        recent_transfers = db.query(Transaction).filter(
            and_(
                Transaction.account_id == account_id,
                Transaction.type.in_(['deposit', 'withdrawal', 'portfolio_transfer']),
                Transaction.occurred_at >= cutoff_time
            )
        ).all()
        
        # Remove known transfers from increases/decreases
        for transfer in recent_transfers:
            symbol = transfer.symbol
            value = abs(Decimal(str(transfer.value_native)))
            
            if transfer.type == 'deposit' and symbol in increases:
                # This increase is explained by a deposit
                increases[symbol] = max(Decimal('0'), increases[symbol] - value)
                if increases[symbol] == 0:
                    del increases[symbol]
            elif transfer.type == 'withdrawal' and symbol in decreases:
                # This decrease is explained by a withdrawal
                decreases[symbol] = max(Decimal('0'), decreases[symbol] - value)
                if decreases[symbol] == 0:
                    del decreases[symbol]
        
        logger.info(f"After filtering transfers: {len(increases)} increases, {len(decreases)} decreases")
        
        if not increases or not decreases:
            logger.info("All balance changes explained by deposits/withdrawals")
            return 0, 0
        
        # Infer trades: Match decreases (sold) with increases (bought)
        # For simplicity, we'll create trades for each combination
        # In reality, users might do: THB → BTC, BTC → ETH in sequence
        # We'll detect these as individual trades
        
        success = 0
        failed = 0
        now = now_utc()
        
        # Strategy: Find the most likely trades
        # 1. If THB decreased and crypto increased → THB → Crypto trade
        # 2. If crypto decreased and THB increased → Crypto → THB trade
        # 3. If crypto A decreased and crypto B increased → Crypto → Crypto trade
        
        if 'THB' in decreases:
            # THB was spent, find what was bought
            thb_spent = decreases['THB']
            
            for bought_asset, bought_amount in increases.items():
                if bought_asset == 'THB':
                    continue
                
                # Create THB → Crypto trade
                try:
                    create_inferred_trade_pair(
                        db=db,
                        portfolio_id=portfolio_id,
                        account_id=account_id,
                        from_asset='THB',
                        from_amount=thb_spent,
                        to_asset=bought_asset,
                        to_amount=bought_amount,
                        trade_date=now,
                        notes=f'Inferred from balance change (auto-detected)'
                    )
                    success += 1
                    logger.info(f"Created inferred trade: {thb_spent} THB → {bought_amount} {bought_asset}")
                except Exception as e:
                    logger.error(f"Failed to create trade THB → {bought_asset}: {e}")
                    failed += 1
            
            # Remove THB from decreases after processing
            del decreases['THB']
        
        if 'THB' in increases:
            # THB was received, find what was sold
            thb_received = increases['THB']
            
            for sold_asset, sold_amount in decreases.items():
                if sold_asset == 'THB':
                    continue
                
                # Create Crypto → THB trade
                try:
                    create_inferred_trade_pair(
                        db=db,
                        portfolio_id=portfolio_id,
                        account_id=account_id,
                        from_asset=sold_asset,
                        from_amount=sold_amount,
                        to_asset='THB',
                        to_amount=thb_received,
                        trade_date=now,
                        notes=f'Inferred from balance change (auto-detected)'
                    )
                    success += 1
                    logger.info(f"Created inferred trade: {sold_amount} {sold_asset} → {thb_received} THB")
                except Exception as e:
                    logger.error(f"Failed to create trade {sold_asset} → THB: {e}")
                    failed += 1
            
            # Remove THB from increases after processing
            del increases['THB']
        
        # Handle crypto-to-crypto trades (if any remain)
        # For each decrease, try to match with an increase
        for sold_asset, sold_amount in list(decreases.items()):
            for bought_asset, bought_amount in list(increases.items()):
                try:
                    create_inferred_trade_pair(
                        db=db,
                        portfolio_id=portfolio_id,
                        account_id=account_id,
                        from_asset=sold_asset,
                        from_amount=sold_amount,
                        to_asset=bought_asset,
                        to_amount=bought_amount,
                        trade_date=now,
                        notes=f'Inferred crypto-crypto trade (auto-detected)'
                    )
                    success += 1
                    logger.info(f"Created inferred trade: {sold_amount} {sold_asset} → {bought_amount} {bought_asset}")
                    
                    # Remove from lists to avoid double-counting
                    if sold_asset in decreases:
                        del decreases[sold_asset]
                    if bought_asset in increases:
                        del increases[bought_asset]
                    break  # Move to next sold asset
                    
                except Exception as e:
                    logger.error(f"Failed to create trade {sold_asset} → {bought_asset}: {e}")
                    failed += 1
        
        logger.info(f"Trade inference complete: {success} created, {failed} failed")
        return success, failed
        
    finally:
        db.close()


def create_inferred_trade_pair(
    db: Session,
    portfolio_id: int,
    account_id: int,
    from_asset: str,
    from_amount: Decimal,
    to_asset: str,
    to_amount: Decimal,
    trade_date: datetime,
    notes: str = None
) -> None:
    """
    Create a pair of transactions representing a trade.
    
    Args:
        db: Database session
        portfolio_id: Portfolio ID
        account_id: Account ID
        from_asset: Asset sold/spent
        from_amount: Amount sold/spent
        to_asset: Asset bought/received
        to_amount: Amount bought/received
        trade_date: Trade timestamp
        notes: Optional notes
    """
    # Generate unique pair ID
    pair_id = f"binanceth_auto_{trade_date.strftime('%Y%m%d_%H%M%S')}_{from_asset}_{to_asset}"
    
    # Calculate exchange rate
    exchange_rate = to_amount / from_amount if from_amount > 0 else Decimal('0')
    
    # Get portfolio base currency
    from models import Portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    base_currency = portfolio.currency_base if portfolio else 'EUR'
    
    # Transaction 1: Sell (from_asset going out)
    sell_tx = Transaction(
        portfolio_id=portfolio_id,
        account_id=account_id,
        occurred_at=trade_date,
        type='sell',
        symbol=from_asset,
        quantity=-from_amount,  # Negative for outgoing
        value_native=from_amount,
        currency_native=from_asset,
        currency_base=base_currency,
        source='binanceth_auto_inferred',
        category='trade',
        external_id=pair_id,
        notes=notes or f'Auto-inferred: {from_asset} → {to_asset}'
    )
    db.add(sell_tx)
    
    # Transaction 2: Buy (to_asset coming in)
    buy_tx = Transaction(
        portfolio_id=portfolio_id,
        account_id=account_id,
        occurred_at=trade_date,
        type='buy',
        symbol=to_asset,
        quantity=to_amount,  # Positive for incoming
        value_native=to_amount,
        currency_native=to_asset,
        currency_base=base_currency,
        price=exchange_rate,
        source='binanceth_auto_inferred',
        category='trade',
        external_id=pair_id,
        notes=notes or f'Auto-inferred: {from_asset} → {to_asset}'
    )
    db.add(buy_tx)
    
    db.commit()


def sync_binanceth_with_trade_inference(days_back: int = 90) -> Dict[str, int]:
    """
    Complete Binance.th sync workflow:
    1. Get old balances from database
    2. Sync crypto deposits/withdrawals
    3. Get new balances from API
    4. Infer trades from balance changes
    5. Store new balances
    
    This is the main function to call from daily jobs.
    Account and portfolio IDs are loaded from config/source_mapping.yaml
    
    Args:
        days_back: Days to look back for deposits/withdrawals (default: 90)
    
    Returns:
        dict: Summary of operations with keys:
            - deposits_synced: Number of deposits synced
            - withdrawals_synced: Number of withdrawals synced  
            - trades_inferred: Number of trades inferred
            - balances_updated: Number of balances updated
            - errors: Number of errors encountered
    """
    # Load IDs from config (config-driven approach like IBKR and Binance.com)
    account_id = BINANCETH_ACCOUNT_ID
    portfolio_id = BINANCETH_DEFAULT_PORTFOLIO_ID
    
    # Validate configuration
    if account_id is None or portfolio_id is None:
        logger.error(
            "BinanceTH configuration missing in source_mapping.yaml. "
            f"Account ID: {account_id}, Portfolio ID: {portfolio_id}"
        )
        return {
            'deposits_synced': 0,
            'withdrawals_synced': 0,
            'trades_inferred': 0,
            'balances_updated': 0,
            'errors': 1
        }
    
    logger.info(
        f"Starting complete Binance.th sync "
        f"(Account ID: {account_id}, Portfolio ID: {portfolio_id})"
    )
    
    summary = {
        'deposits_synced': 0,
        'withdrawals_synced': 0,
        'trades_inferred': 0,
        'balances_updated': 0,
        'errors': 0
    }
    
    try:
        # Step 1: Get old balances
        old_balances = get_stored_balances(account_id)
        logger.info(f"Retrieved {len(old_balances)} stored balances")
        
        # Step 2: Sync crypto deposits/withdrawals
        deposits_success, deposits_failed = sync_all_crypto_transactions(
            account_id=account_id,
            portfolio_id=portfolio_id,
            days_back=days_back
        )
        summary['deposits_synced'] = deposits_success
        summary['errors'] += deposits_failed
        
        # Step 3: Get new balances from API
        new_balances_raw = fetch_account_balances()
        if not new_balances_raw:
            logger.warning("Failed to fetch new balances from API")
            return summary
        
        # Convert to dict format
        new_balances = {}
        for balance in new_balances_raw:
            asset = balance['asset']
            free = Decimal(str(balance.get('free', 0)))
            locked = Decimal(str(balance.get('locked', 0)))
            total = free + locked
            if total > 0:
                new_balances[asset] = total
        
        logger.info(f"Retrieved {len(new_balances)} current balances from API")
        
        # Step 4: Trade inference DISABLED - unreliable and creates duplicate/incorrect transactions
        # Balance-based trade inference was removed because:
        # 1. It cannot distinguish between trades vs deposits/withdrawals accurately
        # 2. It creates duplicate transactions when actual trades are already recorded
        # 3. Currency and price data is missing/incorrect (uses crypto symbol instead of fiat)
        # 4. Trades should be entered manually through the GUI or via proper API if available
        # DEPRECATED: infer_trades_from_balance_changes() - see GitHub issue for details
        logger.info("Trade inference is disabled - trades should be entered manually via GUI")
        summary['trades_inferred'] = 0
        
        # Step 5: Store new balances (categorized by type)
        balance_result = store_balances(account_id, new_balances_raw)
        summary['balances_updated'] = balance_result.get('crypto_stored', 0) + balance_result.get('cash_stored', 0)
        summary['crypto_balances'] = balance_result.get('crypto_stored', 0)
        summary['cash_balances'] = balance_result.get('cash_stored', 0)
        summary['skipped_assets'] = balance_result.get('skipped', 0)
        logger.info(
            f"Stored balances: {balance_result.get('crypto_stored', 0)} HODL crypto, "
            f"{balance_result.get('cash_stored', 0)} cash/stablecoins, "
            f"{balance_result.get('skipped', 0)} skipped"
        )
        
        logger.info(f"Binance.th sync complete: {summary}")
        return summary
        
    except Exception as e:
        logger.error(f"Error during Binance.th sync: {e}", exc_info=True)
        summary['errors'] += 1
        return summary
