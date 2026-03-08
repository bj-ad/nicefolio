"""
Lot-Based Audit Service

Compares lot-based holdings (tax-relevant FIFO tracking) against actual holdings
from external sources (IBKR Flex Report, blockchain APIs).

This is a critical TAX COMPLIANCE feature:
- German tax law requires accurate lot-based tracking (§ 20 EStG)
- Lots are the source of truth for tax purposes (not positions)
- IBKR positions must match lot remaining quantities
- Crypto wallet balances must match blockchain state

Portfolio Configuration:
- Portfolio IDs are loaded from config files (NOT hardcoded)
- IBKR source mapping defines which portfolios contain IBKR assets
- Uses source_mapping.yaml for symbol → portfolio routing

Blockchain Balance Sources:
========================
Each crypto asset's balance is fetched from its respective blockchain API.
For staking assets, the TOTAL balance (liquid + staked) is used for comparison.

BNB (Binance Smart Chain):
- Provider: bsc_provider.py
- get_balance() returns: {liquid, staked, unstaking, total, staking_rewards}
- Staking: Native BNB staking via BSC Staking Contract (0x...2002)
- The 'total' field includes: liquid + staked + unstaking amounts
- API: BSCscan + RPC nodes for staking contract queries

SOL (Solana):
- Provider: sol_provider.py  
- get_balance(include_staking=True) returns: {liquid, staked, activating, deactivating, total, pending_rewards}
- Staking: Native SOL staking to validators via stake accounts
- The 'total' field includes: liquid + staked + activating + deactivating amounts
- API: Alchemy + public Solana RPC for stake account queries

ADA (Cardano):
- Provider: ada_provider.py
- get_balance() returns: {balance (liquid), staked, rewards_available}
- Staking: Native ADA delegation to stake pools
- Total calculated as: balance + staked
- API: Blockfrost

BTC (Bitcoin):
- Provider: btc_provider.py
- get_balance() returns simple float (no staking on Bitcoin)
- API: Blockchain.info, Blockstream

ETH (Ethereum):
- Provider: eth_provider.py
- get_balance() returns simple float balance
- Note: Staking on Ethereum requires separate beacon chain tracking (not implemented)
- API: Etherscan, Alchemy

XRP (Ripple):
- Provider: xrp_provider.py
- get_balance() returns simple float balance (no staking on XRP)
- API: XRPL (public nodes)

Service Layer Rules:
- Makes API calls to external services
- Uses @cache decorator for expensive operations
- Returns Optional[dict] or None
- NO database operations (only reads)
- NO session management
"""

import os
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, List

from database import SessionLocal
from models import Lot, Position, CryptoWallet, Account, Portfolio, Transaction
from sqlalchemy import func
from crud.parsers.ibkr_parser import parse_all_ibkr_positions
from service.ibkr_service import fetch_flex_reference_code, fetch_flex_statement
from service.blockchain_providers import (
    btc_provider,
    eth_provider,
    bsc_provider,
    sol_provider,
    ada_provider,
    xrp_provider,
)
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from utils.notifications import get_notification_service
from utils.portfolios_loader import get_portfolios_loader
from utils.source_mapping_loader import get_source_mapping_loader
from dotenv import load_dotenv
import yaml

logger = get_logger(__name__)

# Load environment variables
load_dotenv()
FLEX_TOKEN = os.getenv("IBKR_FLEX_TOKEN")
FLEX_QUERY_ID = os.getenv("IBKR_FLEX_QUERY_ID")

# Load config
app_config = load_app_config()
portfolios_loader = get_portfolios_loader()
source_mapping_loader = get_source_mapping_loader()

# Load symbol_mapping.yaml
def load_symbol_mapping_config():
    """Load symbol mapping configuration."""
    try:
        with open('config/symbol_mapping.yaml', 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not load symbol_mapping.yaml: {e}")
        return {}

symbol_mapping_config = load_symbol_mapping_config()

# Blockchain provider mapping
PROVIDER_MAP = {
    'BTC': btc_provider,
    'ETH': eth_provider,
    'BNB': bsc_provider,
    'SOL': sol_provider,
    'ADA': ada_provider,
    'XRP': xrp_provider,
}

# Tolerance for floating point comparisons (8 decimal places for crypto)
QUANTITY_TOLERANCE = Decimal('0.00000001')

# Tolerance for cash/currency comparisons (2 decimal places)
CASH_TOLERANCE = Decimal('0.01')

# VALUE-BASED tolerance: Discrepancy value must exceed this to be flagged
# If abs(quantity_diff × price) <= VALUE_TOLERANCE, treat as match
# This handles cases where tiny quantity differences have negligible value
VALUE_TOLERANCE = Decimal('0.01')  # 0.01 in base currency


def get_latest_market_price(symbol: str) -> Optional[Decimal]:
    """
    Get latest market price for a symbol from market_data table.
    
    Used for value-based tolerance checking in position audits.
    
    Args:
        symbol: Symbol to lookup (e.g., 'BTC', 'VOO', base currency)
        
    Returns:
        Latest price as Decimal, or None if not found
    """
    from models import MarketData
    from sqlalchemy import desc
    
    db = SessionLocal()
    try:
        latest_price = db.query(MarketData).filter(
            MarketData.symbol == symbol
        ).order_by(
            desc(MarketData.as_of_date)
        ).first()
        
        if latest_price:
            return Decimal(str(latest_price.price))
        
        logger.debug(f"No market price found for {symbol}")
        return None
        
    finally:
        db.close()


def should_exclude_symbol_from_audit(symbol: str) -> bool:
    """
    Check if a symbol should be excluded from IBKR audit.
    
    Symbols are excluded if:
    - auto_sync is false in symbol_mapping.yaml (e.g., GOLDTHB)
    - Manually managed assets not tracked in IBKR
    
    Args:
        symbol: Symbol to check
        
    Returns:
        True if symbol should be excluded from audit
    """
    try:
        mapping = symbol_mapping_config.get(symbol, {})
        
        if mapping:
            # Check auto_sync field
            auto_sync = mapping.get('auto_sync', True)
            if auto_sync is False:
                logger.debug(f"Excluding {symbol} from audit (auto_sync=false)")
                return True
            
            # Check provider - exclude manual/scraper sources
            provider = mapping.get('provider', '')
            if 'scraper' in provider.lower() or 'manual' in provider.lower():
                logger.debug(f"Excluding {symbol} from audit (manual provider: {provider})")
                return True
        
        return False
        
    except Exception as e:
        logger.warning(f"Error checking symbol exclusion for {symbol}: {e}")
        return False


def get_ibkr_portfolio_ids() -> Dict[str, int]:
    """
    Get portfolio IDs for IBKR assets from config files.
    
    Uses source_mapping.yaml to determine which portfolios contain IBKR assets.
    
    Returns:
        Dict with keys: 'securities', 'commodities', 'cash'
    """
    # Get IBKR default portfolio (Securities)
    securities_id = source_mapping_loader.get_default_portfolio_id('IBKR')
    
    # Get commodity portfolio from symbol mappings
    symbol_map = source_mapping_loader.get_symbol_to_portfolio_map('IBKR')
    
    # Commodity ETF tickers defined in app_config.yaml
    commodity_tickers = app_config.get('commodity_etf_tickers', ['GLD', '4GLD', 'IAU', 'GLDM', 'SLV'])
    
    # Find commodity portfolio ID from first commodity ticker
    commodities_id = None
    for ticker in commodity_tickers:
        if ticker in symbol_map:
            commodities_id = symbol_map[ticker]
            break
    
    # If not found in symbol map, try to get by portfolio name
    if commodities_id is None:
        commodities_portfolio = portfolios_loader.get_portfolio_by_name('Commodities')
        commodities_id = commodities_portfolio.get('id') if commodities_portfolio else None
    
    # Get cash portfolio from symbol mappings (base currency + foreign currencies)
    cash_currencies = app_config.get('ibkr_cash_symbols')
    if not cash_currencies:
        raise KeyError(
            "ibkr_cash_symbols not configured in app_config.yaml. "
            "This is required to identify cash positions. "
            "Please add 'ibkr_cash_symbols: [USD, EUR]' (or your currencies) to config/app_config.yaml"
        )
    cash_id = None
    for currency in cash_currencies:
        if currency in symbol_map:
            cash_id = symbol_map[currency]
            break
    
    # If not found, try by portfolio name
    if cash_id is None:
        cash_portfolio = portfolios_loader.get_portfolio_by_name('Broker Cash Pool')
        cash_id = cash_portfolio.get('id') if cash_portfolio else None
    
    result = {
        'securities': securities_id,
        'commodities': commodities_id,
        'cash': cash_id,
    }
    
    logger.info(f"IBKR portfolio IDs from config: {result}")
    return result


def get_crypto_account_ids(db) -> List[int]:
    """
    Get all account IDs that have active crypto wallets.
    
    This enables account-based auditing (not portfolio-based).
    Stablecoins can be in any portfolio (e.g., Portfolio 8 Broker Cash Pool)
    as long as they belong to a crypto wallet account.
    
    Args:
        db: Database session
        
    Returns:
        List of account IDs with active crypto wallets
    """
    account_ids = db.query(CryptoWallet.account_id).join(
        Account, CryptoWallet.account_id == Account.id
    ).filter(
        Account.status == 'active'
    ).distinct().all()
    
    account_ids = [aid[0] for aid in account_ids]
    logger.info(f"Found {len(account_ids)} accounts with active crypto wallets")
    return account_ids


def fetch_ibkr_positions() -> Optional[Dict]:
    """
    Fetch current positions from IBKR Flex Report.
    
    Service layer function - makes API call to IBKR.
    No caching - we want fresh data for audit.
    
    Returns:
        Dict with 'securities' and 'cash' position lists, or None on failure
    """
    if not FLEX_TOKEN or not FLEX_QUERY_ID:
        logger.error("IBKR Flex credentials not configured")
        return None
    
    try:
        logger.info("Fetching IBKR Flex Report for position audit...")
        ref_code = fetch_flex_reference_code(FLEX_TOKEN, FLEX_QUERY_ID)
        xml_content = fetch_flex_statement(FLEX_TOKEN, ref_code)
        
        # Parse positions using parser layer
        positions = parse_all_ibkr_positions(xml_content)
        
        if positions:
            logger.info(
                f"IBKR positions retrieved: {positions['total_securities']} securities, "
                f"{positions['total_cash']} cash positions"
            )
        
        return positions
        
    except Exception as e:
        logger.error(f"Failed to fetch IBKR positions: {e}", exc_info=True)
        return None


def fetch_crypto_wallet_balance(wallet: CryptoWallet) -> Optional[Dict]:
    """
    Fetch current balance from blockchain for a wallet.
    
    Service layer function - makes API call to blockchain.
    Returns detailed balance breakdown including staking.
    
    Args:
        wallet: CryptoWallet model instance
        
    Returns:
        Dict with balance details including staking info, or None on failure
    """
    provider = PROVIDER_MAP.get(wallet.symbol.upper())
    
    if not provider:
        logger.warning(f"No provider found for chain: {wallet.symbol}")
        return None
    
    try:
        identifier = str(wallet.address) if wallet.address else ''
        if not identifier.strip():
            logger.warning(f"Wallet {wallet.id} has no address defined")
            return None
        
        symbol = wallet.symbol.upper()
        
        # Handle different provider response formats
        if symbol == 'SOL':
            # SOL includes staking info with include_staking=True
            balance_data = provider.get_balance(identifier, include_staking=True)
            if balance_data:
                return {
                    'total': Decimal(str(balance_data.get('total', 0))),
                    'liquid': Decimal(str(balance_data.get('liquid', 0))),
                    'staked': Decimal(str(balance_data.get('staked', 0))),
                    'activating': Decimal(str(balance_data.get('activating', 0))),
                    'deactivating': Decimal(str(balance_data.get('deactivating', 0))),
                    'pending_rewards': Decimal(str(balance_data.get('pending_rewards', 0))),
                    'source': 'Solana RPC (Alchemy + Public)',
                }
        elif symbol == 'ADA':
            balance_data = provider.get_balance(identifier)
            if balance_data:
                liquid = Decimal(str(balance_data.get('balance', 0)))
                staked = Decimal(str(balance_data.get('staked', 0)))
                return {
                    'total': liquid + staked,
                    'liquid': liquid,
                    'staked': staked,
                    'rewards_available': Decimal(str(balance_data.get('rewards_available', 0))),
                    'source': 'Blockfrost API',
                }
        elif symbol == 'BNB':
            # BNB staking uses liquid staking model - need to query credit contract
            # for actual staked value INCLUDING auto-compounded rewards
            balance_data = provider.get_balance(identifier)
            if balance_data:
                liquid = Decimal(str(balance_data.get('liquid', 0)))
                unstaking = Decimal(str(balance_data.get('unstaking', 0)))
                
                # Get live staking value from credit contract (includes compounded rewards)
                staking_value = provider.get_current_staking_value(identifier)
                if staking_value.get('success'):
                    # Use credit contract value - this is the REAL on-chain staked amount
                    staked = Decimal(str(staking_value.get('current_staked_value', 0)))
                    source = 'BSC RPC (credit contract query for staked value)'
                else:
                    # Fallback to calculated value if credit contract query fails
                    staked = Decimal(str(balance_data.get('staked', 0)))
                    source = 'BSCscan + RPC (fallback: calculated staked)'
                    logger.warning(
                        f"Credit contract query failed for {identifier}, using fallback: "
                        f"{staking_value.get('error')}"
                    )
                
                return {
                    'total': liquid + staked + unstaking,
                    'liquid': liquid,
                    'staked': staked,
                    'unstaking': unstaking,
                    'staking_rewards': Decimal('0'),  # Already included in staked via credit contract
                    'source': source,
                }
        elif symbol == 'BTC':
            balance_data = provider.get_balance(identifier)
            if balance_data is not None:
                # BTC returns float for xpub or dict for address
                if isinstance(balance_data, dict):
                    total = balance_data.get('balance', 0)
                else:
                    total = balance_data
                return {
                    'total': Decimal(str(total)),
                    'liquid': Decimal(str(total)),
                    'staked': Decimal('0'),  # No staking on Bitcoin
                    'source': 'Blockchain.info / Blockstream',
                }
        elif symbol == 'ETH':
            balance_data = provider.get_balance(identifier)
            if balance_data is not None:
                # ETH returns dict with 'balance' key
                if isinstance(balance_data, dict):
                    total = balance_data.get('balance', 0)
                else:
                    total = balance_data
                return {
                    'total': Decimal(str(total)),
                    'liquid': Decimal(str(total)),
                    'staked': Decimal('0'),  # Beacon chain staking not tracked
                    'source': 'Etherscan / Alchemy',
                }
        elif symbol == 'XRP':
            balance_data = provider.get_balance(identifier)
            if balance_data is not None:
                # XRP returns dict with 'total' key
                if isinstance(balance_data, dict):
                    total = balance_data.get('total', 0)
                else:
                    total = balance_data
                return {
                    'total': Decimal(str(total)),
                    'liquid': Decimal(str(total)),
                    'staked': Decimal('0'),  # No staking on XRP
                    'source': 'XRPL Public Nodes',
                }
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to fetch balance for {wallet.symbol} wallet {wallet.id}: {e}")
        return None


def get_lot_based_holdings(
    db,
    portfolio_ids: List[int],
    exclude_base_currency: bool = False
) -> Dict[str, Decimal]:
    """
    Get holdings from Lot table remaining quantities (TAX-RELEVANT).
    
    This is the SOURCE OF TRUTH for tax purposes:
    - German tax law (§ 20 EStG) requires FIFO lot tracking
    - Lot.remaining_quantity reflects actual tax basis
    - Position table is for GUI display only
    - Audits MUST use lot-based holdings to catch discrepancies
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        exclude_base_currency: Exclude base currency lots
        
    Returns:
        Dict mapping symbol to total remaining quantity from Lot table
    """
    base_currency = get_global_base_currency()
    
    # Query Lot table for remaining quantities - this is tax-relevant
    query = db.query(Lot).filter(
        Lot.portfolio_id.in_(portfolio_ids),
        Lot.remaining_quantity > 0
    )
    
    if exclude_base_currency:
        query = query.filter(Lot.symbol != base_currency)
    
    lots = query.all()
    
    holdings = {}
    for lot in lots:
        symbol = lot.symbol
        remaining_qty = Decimal(str(lot.remaining_quantity)) if lot.remaining_quantity else Decimal('0')
        
        # Aggregate by symbol (sum remaining quantities across all lots)
        if symbol in holdings:
            holdings[symbol] += remaining_qty
        else:
            holdings[symbol] = remaining_qty
    
    logger.debug(f"Lot-based holdings for portfolios {portfolio_ids}: {len(holdings)} symbols")
    return holdings


def get_holdings_with_base_currency(
    db,
    portfolio_ids: List[int],
    include_base_currency_from_position: bool = True
) -> Dict[str, Decimal]:
    """
    Get holdings including base currency for cash pool audits.
    
    RATIONALE FOR BASE CURRENCY HANDLING:
    - Base currency (EUR, USD) doesn't create lots - it's the reference currency
    - For audit purposes, we need to verify base currency holdings exist
    - Position table is our internal "source of truth" for base currency
    - This provides the most meaningful comparison: Position (internal) vs IBKR (external)
    
    DESIGN DECISION:
    - Use Lot table for all non-base assets (tax-relevant, FIFO tracking)
    - Use Position table ONLY for base currency in cash pool
    - This gives us complete audit coverage without hardcoding currency symbols
    
    Args:
        db: Database session
        portfolio_ids: List of portfolio IDs to include
        include_base_currency_from_position: Add base currency from Position table
        
    Returns:
        Dict mapping symbol to remaining quantity (lots + base currency position)
    """
    base_currency = get_global_base_currency()
    
    # Get lot-based holdings (excludes base currency - lots don't exist for it)
    holdings = get_lot_based_holdings(db, portfolio_ids, exclude_base_currency=True)
    
    # Add base currency from Position table if requested
    if include_base_currency_from_position:
        base_position = db.query(Position).filter(
            Position.portfolio_id.in_(portfolio_ids),
            Position.symbol == base_currency
        ).first()
        
        if base_position and base_position.quantity:
            base_qty = Decimal(str(base_position.quantity))
            holdings[base_currency] = base_qty
            logger.debug(
                f"Added base currency {base_currency} from Position table: {base_qty} "
                f"(no lots exist for base currency)"
            )
        else:
            logger.debug(
                f"No base currency position found for {base_currency} in portfolios {portfolio_ids}"
            )
    
    logger.debug(
        f"Holdings for portfolios {portfolio_ids}: {len(holdings)} symbols "
        f"(including base currency: {include_base_currency_from_position})"
    )
    return holdings


def compare_positions(
    calculated: Dict[str, Decimal],
    actual: Dict[str, Decimal],
    tolerance: Decimal = QUANTITY_TOLERANCE,
    use_value_tolerance: bool = True,
    exclude_auto_sync_false: bool = False
) -> Dict:
    """
    Compare calculated holdings against actual holdings.
    
    Uses DUAL tolerance system:
    1. Quantity tolerance: For initial filtering tiny amounts
    2. Value tolerance: Only flag as discrepancy if value diff > 0.01 base currency
    
    ALL differences are shown in results with both symbol and base currency value.
    Only differences exceeding 0.01 base currency value are flagged as discrepancies.
    
    Args:
        calculated: Holdings from lots/transactions
        actual: Holdings from external source (IBKR, blockchain)
        tolerance: Maximum allowed quantity difference for filtering dust
        use_value_tolerance: Apply value-based tolerance (0.01 base currency)
        exclude_auto_sync_false: Exclude symbols with auto_sync=false from audit
        
    Returns:
        Dict with comparison results including 'minor_differences' for < 0.01 base currency
    """
    base_currency = get_global_base_currency()
    
    matches = []
    discrepancies = []
    minor_differences = []  # Differences < 0.01 base currency (shown but not flagged)
    missing_in_calculated = []
    missing_in_actual = []
    
    all_symbols = set(calculated.keys()) | set(actual.keys())
    
    # Filter out excluded symbols if requested
    if exclude_auto_sync_false:
        excluded_symbols = [s for s in all_symbols if should_exclude_symbol_from_audit(s)]
        for symbol in excluded_symbols:
            logger.info(f"Excluding {symbol} from audit (auto_sync=false or manual)")
            all_symbols.discard(symbol)
    
    for symbol in sorted(all_symbols):
        calc_qty = calculated.get(symbol, Decimal('0'))
        actual_qty = actual.get(symbol, Decimal('0'))
        difference = calc_qty - actual_qty
        
        if symbol not in calculated:
            # Filter out tiny amounts (e.g., IBKR dust like $0.000004)
            if abs(actual_qty) <= tolerance:
                logger.debug(f"Ignoring tiny external amount for {symbol}: {actual_qty} (below tolerance {tolerance})")
                continue
            missing_in_calculated.append({
                'symbol': symbol,
                'actual_qty': float(actual_qty),
                'message': f'{symbol}: Found in external source ({actual_qty}) but not in calculated positions'
            })
        elif symbol not in actual:
            # Filter out tiny amounts in calculated (shouldn't happen normally)
            if abs(calc_qty) <= tolerance:
                logger.debug(f"Ignoring tiny calculated amount for {symbol}: {calc_qty} (below tolerance {tolerance})")
                continue
            missing_in_actual.append({
                'symbol': symbol,
                'calculated_qty': float(calc_qty),
                'message': f'{symbol}: Found in calculated positions ({calc_qty}) but not in external source'
            })
        elif abs(difference) > tolerance:
            # Get price for value-based tolerance
            price = get_latest_market_price(symbol)
            value_diff_base = None
            is_significant = True
            
            if use_value_tolerance and price:
                value_diff_base = abs(difference) * price
                # Only flag as discrepancy if value > 0.01 base currency
                is_significant = value_diff_base > VALUE_TOLERANCE
            
            diff_entry = {
                'symbol': symbol,
                'calculated_qty': float(calc_qty),
                'actual_qty': float(actual_qty),
                'difference': float(difference),
                'difference_abs': float(abs(difference)),
                'price': float(price) if price else None,
                'value_diff_base': float(value_diff_base) if value_diff_base else None,
                'message': f'{symbol}: DB={calc_qty:.8f}, External={actual_qty:.8f}, Diff={difference:.8f}'
            }
            
            if value_diff_base:
                diff_entry['message'] += f' ({base_currency}{value_diff_base:.4f})'
            
            if is_significant:
                discrepancies.append(diff_entry)
            else:
                # Show minor difference but don't flag as discrepancy
                diff_entry['reason'] = f'Below value tolerance of {base_currency}0.01'
                minor_differences.append(diff_entry)
        else:
            matches.append({
                'symbol': symbol,
                'quantity': float(calc_qty),
            })
    
    return {
        'matches': matches,
        'discrepancies': discrepancies,
        'minor_differences': minor_differences,
        'missing_in_calculated': missing_in_calculated,
        'missing_in_actual': missing_in_actual,
        'total_checked': len(all_symbols),
        'total_matches': len(matches),
        'total_discrepancies': len(discrepancies) + len(missing_in_calculated) + len(missing_in_actual),
        'total_minor_differences': len(minor_differences),
    }


def audit_ibkr_securities() -> Dict:
    """
    Audit IBKR securities positions against Lot table remaining quantities.
    
    Includes both Securities (Portfolio 3) and Commodities (Portfolio 4).
    Portfolio IDs are loaded from config files.
    
    Compares:
    - IBKR Flex Report OpenPositions (external truth)
    - Lot table remaining quantities (tax-relevant source of truth)
    
    TAX COMPLIANCE: Uses lot-based holdings (not positions) per German tax law.
    
    Returns:
        Audit result dict with comparison details
    """
    # Get portfolio IDs from config
    ibkr_portfolios = get_ibkr_portfolio_ids()
    securities_id = ibkr_portfolios.get('securities')
    commodities_id = ibkr_portfolios.get('commodities')
    
    portfolio_ids = [pid for pid in [securities_id, commodities_id] if pid is not None]
    
    logger.info(f"Starting IBKR securities audit for portfolios {portfolio_ids}")
    
    db = SessionLocal()
    try:
        # Get external positions from IBKR
        ibkr_data = fetch_ibkr_positions()
        
        if not ibkr_data:
            return {
                'success': False,
                'error': 'Failed to fetch IBKR positions',
                'portfolio_ids': portfolio_ids,
            }
        
        # Build actual holdings from IBKR (securities only)
        actual_holdings = {}
        position_details = []
        for pos in ibkr_data.get('securities', []):
            symbol = pos['symbol']
            # Skip cash-category assets
            if pos.get('asset_class') == 'CASH':
                continue
            actual_holdings[symbol] = pos['quantity']
            position_details.append({
                'symbol': symbol,
                'quantity': float(pos['quantity']),
                'cost_basis': float(pos.get('cost_basis', 0)),
                'market_value': float(pos.get('market_value', 0)),
                'asset_class': pos.get('asset_class', 'STK'),
            })
        
        # Get holdings from Lot table (tax-relevant source of truth)
        calculated_holdings = get_lot_based_holdings(db, portfolio_ids, exclude_base_currency=True)
        
        # Compare with value tolerance and symbol exclusions
        # This filters out symbols like GOLDTHB (auto_sync=false) that aren't in IBKR
        comparison = compare_positions(
            calculated_holdings, 
            actual_holdings,
            tolerance=QUANTITY_TOLERANCE,
            use_value_tolerance=True,
            exclude_auto_sync_false=True
        )
        
        result = {
            'success': True,
            'portfolio_ids': portfolio_ids,
            'portfolio_types': ['Securities', 'Commodities'],
            'source': 'IBKR Flex Report (OpenPositions)',
            'report_date': ibkr_data.get('report_date', ''),
            'ibkr_account': ibkr_data.get('account_id', ''),
            'position_details': position_details,
            'calculated_holdings': {k: float(v) for k, v in calculated_holdings.items()},
            'comparison': comparison,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        
        if comparison['total_discrepancies'] > 0:
            logger.warning(
                f"IBKR securities audit found {comparison['total_discrepancies']} discrepancies "
                f"for portfolios {portfolio_ids}"
            )
        else:
            logger.info(
                f"IBKR securities audit passed: {comparison['total_matches']} positions match"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error auditing IBKR securities: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'portfolio_ids': portfolio_ids,
        }
    finally:
        db.close()


def audit_ibkr_cash() -> Dict:
    """
    Audit cash positions against Lot table remaining quantities.
    
    Portfolio ID is loaded from config files.
    
    Compares:
    - IBKR Flex Report FxPositions (external truth) - ALL currencies including base
    - Lot table remaining quantities (tax-relevant source of truth)
    
    Note: Base currency IS included for complete audit.
    
    TAX COMPLIANCE: Uses lot-based holdings (not positions) per German tax law.
    
    Returns:
        Audit result dict with comparison details
    """
    # Get portfolio ID from config
    ibkr_portfolios = get_ibkr_portfolio_ids()
    portfolio_id = ibkr_portfolios.get('cash')
    
    if portfolio_id is None:
        logger.warning("Cash portfolio not found in config")
        return {
            'success': False,
            'error': 'Cash portfolio not configured',
        }
    
    logger.info(f"Starting cash audit for portfolio {portfolio_id}")
    
    base_currency = get_global_base_currency()
    
    db = SessionLocal()
    try:
        # Get external positions from IBKR
        ibkr_data = fetch_ibkr_positions()
        
        if not ibkr_data:
            return {
                'success': False,
                'error': 'Failed to fetch IBKR positions',
                'portfolio_id': portfolio_id,
            }
        
        # Build actual holdings from IBKR (ALL cash including base currency)
        actual_holdings = {}
        cash_details = []
        for pos in ibkr_data.get('cash', []):
            symbol = pos['symbol']
            # Include ALL currencies including base currency
            actual_holdings[symbol] = pos['quantity']
            cash_details.append({
                'currency': symbol,
                'quantity': float(pos['quantity']),
                'market_value': float(pos.get('market_value', 0)),
            })
        
        # Get holdings including base currency from Position table
        # RATIONALE: Base currency doesn't create lots (it's the reference currency)
        # For cash audit, we compare Position table (internal truth) vs IBKR (external truth)
        # All other currencies use lot-based holdings (tax-relevant FIFO tracking)
        calculated_holdings = get_holdings_with_base_currency(
            db, 
            [portfolio_id], 
            include_base_currency_from_position=True
        )
        
        # Build position_details list showing DB holdings for all currencies
        position_details = []
        for symbol in sorted(set(list(calculated_holdings.keys()) + list(actual_holdings.keys()))):
            db_qty = calculated_holdings.get(symbol, Decimal('0'))
            ibkr_qty = actual_holdings.get(symbol, Decimal('0'))
            position_details.append({
                'symbol': symbol,
                'db_quantity': float(db_qty),
                'ibkr_quantity': float(ibkr_qty),
                'difference': float(db_qty - ibkr_qty),
            })
        
        # Compare with cash tolerance and value checking
        comparison = compare_positions(
            calculated_holdings, 
            actual_holdings, 
            tolerance=CASH_TOLERANCE,
            use_value_tolerance=True,
            exclude_auto_sync_false=False  # Don't exclude for cash (currencies are all valid)
        )
        
        result = {
            'success': True,
            'portfolio_id': portfolio_id,
            'portfolio_type': 'Cash Pool',
            'source': 'IBKR Flex Report (FxPositions)',
            'report_date': ibkr_data.get('report_date', ''),
            'base_currency': base_currency,
            'position_details': position_details,
            'cash_details': cash_details,
            'calculated_holdings': {k: float(v) for k, v in calculated_holdings.items()},
            'comparison': comparison,
            'note': f'Lot-based audit for foreign currencies; {base_currency} from Position table (base currency has no lots)',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        
        if comparison['total_discrepancies'] > 0:
            logger.warning(
                f"Cash audit found {comparison['total_discrepancies']} discrepancies "
                f"for portfolio {portfolio_id}"
            )
        else:
            logger.info(
                f"Cash audit passed: {comparison['total_matches']} positions match"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error auditing cash: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'portfolio_id': portfolio_id,
        }
    finally:
        db.close()


def audit_crypto_wallets() -> Dict:
    """
    Audit crypto wallet holdings against blockchain balances.
    
    ACCOUNT-BASED AUDIT (not portfolio-based):
    - Gets all accounts with active crypto wallets
    - Aggregates Lot table remaining quantities for crypto portfolios
    - Uses tax-relevant lot-based tracking
    
    Compares:
    - Blockchain balance from API (external truth) - INCLUDES STAKING
    - Lot table remaining quantities (tax-relevant source of truth)
    
    IMPORTANT: For assets with staking (BNB, SOL, ADA), the TOTAL balance
    (liquid + staked + unstaking/activating) is used for comparison.
    This ensures staked amounts are properly accounted for.
    
    TAX COMPLIANCE: Uses lot-based holdings (not positions) per German tax law.
    
    Returns:
        Audit result dict with comparison details
    """
    db = SessionLocal()
    try:
        # Get all active crypto wallets
        wallets = db.query(CryptoWallet).join(
            Account, CryptoWallet.account_id == Account.id
        ).filter(Account.status == 'active').all()
        
        if not wallets:
            logger.info("No active crypto wallets found")
            return {
                'success': True,
                'audit_type': 'account_based',
                'message': 'No active crypto wallets configured',
                'wallets_checked': 0,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
        
        # Get account IDs for reference
        account_ids = get_crypto_account_ids(db)
        
        logger.info(f"Starting crypto wallet audit for {len(account_ids)} accounts with {len(wallets)} wallets")
        
        # Get crypto portfolio IDs from config
        portfolios_loader = get_portfolios_loader()
        crypto_portfolio_ids = [
            p['id'] for p in portfolios_loader.get_portfolios()
            if p.get('type') == 'crypto' and p.get('status') == 'active'
        ]
        
        # Get holdings from Lot table (tax-relevant source of truth)
        # Aggregate by symbol across all crypto portfolios
        calculated_by_symbol: Dict[str, Decimal] = {}
        
        lots = db.query(Lot).filter(
            Lot.portfolio_id.in_(crypto_portfolio_ids),
            Lot.remaining_quantity > 0
        ).all()
        
        account_holdings = {}  # For display in report
        for lot in lots:
            symbol = lot.symbol
            remaining_qty = Decimal(str(lot.remaining_quantity)) if lot.remaining_quantity else Decimal('0')
            
            if symbol in calculated_by_symbol:
                calculated_by_symbol[symbol] += remaining_qty
            else:
                calculated_by_symbol[symbol] = remaining_qty
            
            # Track by portfolio for reporting
            portfolio_id = lot.portfolio_id
            if portfolio_id not in account_holdings:
                portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
                account_holdings[portfolio_id] = {
                    'name': portfolio.name if portfolio else f'Portfolio {portfolio_id}',
                    'holdings': {}
                }
            if symbol in account_holdings[portfolio_id]['holdings']:
                account_holdings[portfolio_id]['holdings'][symbol] += float(remaining_qty)
            else:
                account_holdings[portfolio_id]['holdings'][symbol] = float(remaining_qty)
        
        logger.info(f"Calculated holdings from Lot table: {len(calculated_by_symbol)} symbols across {len(crypto_portfolio_ids)} portfolios")
        
        # Aggregate actual blockchain balances by symbol (from wallets)
        actual_by_symbol: Dict[str, Decimal] = {}
        wallet_details = []
        
        for wallet in wallets:
            symbol = wallet.symbol.upper()
            
            # Get account info for grouping
            account = db.query(Account).filter(Account.id == wallet.account_id).first()
            account_name = account.name if account else f"Account {wallet.account_id}"
            
            # Fetch blockchain balance with full details
            balance_info = fetch_crypto_wallet_balance(wallet)
            
            # Safely handle address display (kept for debugging, not shown in email)
            address_str = str(wallet.address) if wallet.address else ''
            display_address = address_str[:20] + '...' if len(address_str) > 20 else address_str
            
            wallet_info = {
                'wallet_id': wallet.id,
                'account_id': wallet.account_id,
                'account_name': account_name,
                'symbol': symbol,
                'address': display_address,
                'full_address': address_str,
                'status': 'success' if balance_info else 'failed',
            }
            
            if balance_info:
                # Use TOTAL balance (includes staking)
                total_balance = balance_info.get('total', Decimal('0'))
                
                wallet_info.update({
                    'blockchain_total': float(total_balance),
                    'blockchain_liquid': float(balance_info.get('liquid', Decimal('0'))),
                    'blockchain_staked': float(balance_info.get('staked', Decimal('0'))),
                    'source': balance_info.get('source', 'Unknown'),
                })
                
                # Add extra fields for staking assets
                if 'unstaking' in balance_info:
                    wallet_info['blockchain_unstaking'] = float(balance_info['unstaking'])
                if 'activating' in balance_info:
                    wallet_info['blockchain_activating'] = float(balance_info['activating'])
                if 'deactivating' in balance_info:
                    wallet_info['blockchain_deactivating'] = float(balance_info['deactivating'])
                if 'pending_rewards' in balance_info:
                    wallet_info['pending_rewards'] = float(balance_info['pending_rewards'])
                if 'staking_rewards' in balance_info:
                    wallet_info['staking_rewards'] = float(balance_info['staking_rewards'])
                
                if symbol not in actual_by_symbol:
                    actual_by_symbol[symbol] = Decimal('0')
                actual_by_symbol[symbol] += total_balance
            
            wallet_details.append(wallet_info)
        
        # Compare by symbol with value tolerance
        comparison = compare_positions(
            calculated_by_symbol, 
            actual_by_symbol, 
            tolerance=QUANTITY_TOLERANCE,
            use_value_tolerance=True,
            exclude_auto_sync_false=False  # Crypto symbols don't use auto_sync field
        )
        
        result = {
            'success': True,
            'audit_type': 'account_based',
            'accounts_audited': len(account_ids),
            'source': 'Blockchain APIs (see wallet details for specifics)',
            'wallets_checked': len(wallets),
            'wallet_details': wallet_details,
            'account_holdings': {  # Per-portfolio holdings from Position table (for debugging discrepancies)
                acc_id: {
                    'name': info['name'],
                    'holdings': {k: float(v) for k, v in info['holdings'].items()}
                } for acc_id, info in account_holdings.items()
            },
            'calculated_holdings': {k: float(v) for k, v in calculated_by_symbol.items()},
            'actual_holdings': {k: float(v) for k, v in actual_by_symbol.items()},
            'comparison': comparison,
            'note': 'Lot-based audit: Uses Lot table remaining quantities (tax-relevant source of truth)',
            'staking_note': 'For BNB, SOL, ADA: total includes liquid + staked amounts',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        
        if comparison['total_discrepancies'] > 0:
            logger.warning(
                f"Crypto wallet audit found {comparison['total_discrepancies']} discrepancies "
                f"for {len(account_ids)} accounts"
            )
        else:
            logger.info(
                f"Crypto wallet audit passed: {comparison['total_matches']} symbols match"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error auditing crypto wallets: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'audit_type': 'account_based',
        }
    finally:
        db.close()


def run_full_position_audit() -> Dict:
    """
    Run complete lot-based audit across all tracked portfolios.
    
    TAX COMPLIANCE: Compares lot remaining quantities (tax-relevant) against
    external sources (IBKR, blockchain APIs).
    
    Audits:
    1. IBKR Securities (Securities + Commodities portfolios)
    2. Cash Pool (all cash holdings including base currency)
    3. Crypto Wallets (blockchain holdings with staking)
    
    Returns:
        Complete audit result dict with all details
    """
    logger.info("=" * 80)
    logger.info("STARTING FULL LOT-BASED AUDIT (TAX-RELEVANT)")
    logger.info("=" * 80)
    
    timestamp = datetime.now(timezone.utc)
    
    results = {
        'timestamp': timestamp.isoformat(),
        'audits': {},
        'summary': {
            'total_portfolios': 0,
            'total_discrepancies': 0,
            'portfolios_with_issues': [],
        }
    }
    
    # 1. IBKR Securities (includes both Securities and Commodities portfolios)
    logger.info("Audit 1/3: IBKR Securities + Commodities")
    logger.info("-" * 80)
    ibkr_securities = audit_ibkr_securities()
    results['audits']['ibkr_securities'] = ibkr_securities
    results['summary']['total_portfolios'] += 1
    
    if ibkr_securities.get('success') and ibkr_securities.get('comparison', {}).get('total_discrepancies', 0) > 0:
        results['summary']['portfolios_with_issues'].append('IBKR Securities/Commodities')
        results['summary']['total_discrepancies'] += ibkr_securities['comparison']['total_discrepancies']
    elif not ibkr_securities.get('success'):
        results['summary']['portfolios_with_issues'].append('IBKR Securities (FAILED)')
    
    # 2. Cash Pool (all currencies including base)
    logger.info("Audit 2/3: Cash Pool")
    logger.info("-" * 80)
    ibkr_cash = audit_ibkr_cash()
    results['audits']['ibkr_cash'] = ibkr_cash
    results['summary']['total_portfolios'] += 1
    
    if ibkr_cash.get('success') and ibkr_cash.get('comparison', {}).get('total_discrepancies', 0) > 0:
        results['summary']['portfolios_with_issues'].append('Cash Pool')
        results['summary']['total_discrepancies'] += ibkr_cash['comparison']['total_discrepancies']
    elif not ibkr_cash.get('success'):
        results['summary']['portfolios_with_issues'].append('Cash Pool (FAILED)')
    
    # 3. Crypto Wallets
    logger.info("Audit 3/3: Crypto Wallets (with staking)")
    logger.info("-" * 80)
    crypto_wallets = audit_crypto_wallets()
    results['audits']['crypto_wallets'] = crypto_wallets
    results['summary']['total_portfolios'] += 1
    
    if crypto_wallets.get('success') and crypto_wallets.get('comparison', {}).get('total_discrepancies', 0) > 0:
        results['summary']['portfolios_with_issues'].append('Crypto Wallets')
        results['summary']['total_discrepancies'] += crypto_wallets['comparison']['total_discrepancies']
    elif not crypto_wallets.get('success'):
        results['summary']['portfolios_with_issues'].append('Crypto Wallets (FAILED)')
    
    # Summary
    logger.info("=" * 80)
    if results['summary']['total_discrepancies'] > 0:
        logger.warning(
            f"POSITION AUDIT COMPLETED WITH {results['summary']['total_discrepancies']} DISCREPANCIES "
            f"in: {', '.join(results['summary']['portfolios_with_issues'])}"
        )
    elif results['summary']['portfolios_with_issues']:
        logger.error(
            f"POSITION AUDIT COMPLETED WITH FAILURES: "
            f"{', '.join(results['summary']['portfolios_with_issues'])}"
        )
    else:
        logger.info("POSITION AUDIT COMPLETED SUCCESSFULLY - ALL POSITIONS MATCH")
    logger.info("=" * 80)
    
    return results


def format_detailed_audit_report(audit_results: Dict) -> str:
    """
    Format audit results into a detailed human-readable report.
    
    Args:
        audit_results: Results from run_full_position_audit()
        
    Returns:
        Formatted report string
    """
    timestamp = audit_results.get('timestamp', datetime.now(timezone.utc).isoformat())
    summary = audit_results.get('summary', {})
    
    has_discrepancies = summary.get('total_discrepancies', 0) > 0
    has_failures = any('FAILED' in issue for issue in summary.get('portfolios_with_issues', []))
    
    if has_discrepancies:
        header = "🚨 TAX COMPLIANCE ALERT - POSITION DISCREPANCIES FOUND"
    elif has_failures:
        header = "⚠️ WARNING - SOME AUDITS FAILED"
    else:
        header = "✅ POSITION AUDIT PASSED - ALL POSITIONS VERIFIED"
    
    report = f"""
{'='*70}
{header}
{'='*70}

Audit Timestamp: {timestamp}

SUMMARY:
  Portfolios Audited: {summary.get('total_portfolios', 0)}
  Total Discrepancies: {summary.get('total_discrepancies', 0)}
  Issues: {', '.join(summary.get('portfolios_with_issues', [])) or 'None'}
"""
    
    audits = audit_results.get('audits', {})
    
    # IBKR Securities
    if 'ibkr_securities' in audits:
        sec = audits['ibkr_securities']
        report += f"""
{'='*70}
IBKR SECURITIES & COMMODITIES
{'='*70}
  Source: {sec.get('source', 'N/A')}
  Report Date: {sec.get('report_date', 'N/A')}
  IBKR Account: {sec.get('ibkr_account', 'N/A')}
  Status: {'✅ SUCCESS' if sec.get('success') else '❌ FAILED'}
"""
        if not sec.get('success'):
            report += f"  Error: {sec.get('error', 'Unknown')}\n"
        else:
            comparison = sec.get('comparison', {})
            report += f"  Positions Checked: {comparison.get('total_checked', 0)}\n"
            report += f"  Matches: {comparison.get('total_matches', 0)}\n"
            report += f"  Discrepancies: {comparison.get('total_discrepancies', 0)}\n"
            
            # Show minor differences count if present
            base_currency = get_global_base_currency()
            minor_count = comparison.get('total_minor_differences', 0)
            if minor_count > 0:
                report += f"  Minor Differences (<{base_currency}0.01): {minor_count}\n"
            
            # Detail all positions
            report += "\n  POSITION DETAILS (Database vs IBKR):\n"
            report += f"  {'-'*65}\n"
            
            for match in comparison.get('matches', []):
                report += f"  ✅ {match['symbol']}: {match['quantity']:.6f}\n"
            
            # Show minor differences (not flagged as discrepancies)
            for minor in comparison.get('minor_differences', []):
                value_str = f" ({base_currency} {minor.get('value_diff_base', 0):,.4f})" if minor.get('value_diff_base') else ""
                report += f"  ℹ️  {minor['symbol']}: DB={minor['calculated_qty']:,.6f}, IBKR={minor['actual_qty']:,.6f}, Diff={minor['difference']:,.6f}{value_str}\n"
            
            for disc in comparison.get('discrepancies', []):
                value_str = f" ({base_currency} {disc.get('value_diff_base', 0):,.4f})" if disc.get('value_diff_base') else ""
                report += f"  ⚠️  {disc['symbol']}: DB={disc['calculated_qty']:,.6f}, IBKR={disc['actual_qty']:,.6f}, Diff={disc['difference']:,.6f}{value_str}\n"
            
            for missing in comparison.get('missing_in_calculated', []):
                report += f"  ❓ {missing['symbol']}: In IBKR ({missing['actual_qty']:.6f}) but NOT in DB\n"
            
            for missing in comparison.get('missing_in_actual', []):
                report += f"  ❓ {missing['symbol']}: In DB ({missing['calculated_qty']:.6f}) but NOT in IBKR\n"
    
    # Cash Pool (all currencies)
    if 'ibkr_cash' in audits:
        cash = audits['ibkr_cash']
        report += f"""
{'='*70}
CASH POOL
{'='*70}
  Source: {cash.get('source', 'N/A')}
  Report Date: {cash.get('report_date', 'N/A')}
  Status: {'✅ SUCCESS' if cash.get('success') else '❌ FAILED'}
"""
        if not cash.get('success'):
            report += f"  Error: {cash.get('error', 'Unknown')}\n"
        else:
            comparison = cash.get('comparison', {})
            report += f"  Currencies Checked: {comparison.get('total_checked', 0)}\n"
            report += f"  Matches: {comparison.get('total_matches', 0)}\n"
            report += f"  Discrepancies: {comparison.get('total_discrepancies', 0)}\n"
            
            # Show audit methodology note
            if 'note' in cash:
                report += f"  Note: {cash['note']}\n"
            
            # Show minor differences count if present
            base_currency = get_global_base_currency()
            minor_count = comparison.get('total_minor_differences', 0)
            if minor_count > 0:
                report += f"  Minor Differences (<{base_currency}0.01): {minor_count}\n"
            
            report += "\n  CASH DETAILS (Database vs IBKR):\n"
            report += f"  {'-'*65}\n"
            
            for match in comparison.get('matches', []):
                report += f"  ✅ {match['symbol']}: {match['quantity']:,.2f}\n"
            
            # Show minor differences
            for minor in comparison.get('minor_differences', []):
                value_str = f" ({base_currency} {minor.get('value_diff_base', 0):,.4f})" if minor.get('value_diff_base') else ""
                report += f"  ℹ️  {minor['symbol']}: DB={minor['calculated_qty']:,.2f}, IBKR={minor['actual_qty']:,.2f}, Diff={minor['difference']:,.2f}{value_str}\n"
            
            for disc in comparison.get('discrepancies', []):
                value_str = f" ({base_currency} {disc.get('value_diff_base', 0):,.4f})" if disc.get('value_diff_base') else ""
                report += f"  ⚠️  {disc['symbol']}: DB={disc['calculated_qty']:,.2f}, IBKR={disc['actual_qty']:,.2f}, Diff={disc['difference']:,.2f}{value_str}\n"
            
            for missing in comparison.get('missing_in_calculated', []):
                report += f"  ❓ {missing['symbol']}: In IBKR ({missing['actual_qty']:,.2f}) but NOT in DB\n"
            
            for missing in comparison.get('missing_in_actual', []):
                report += f"  ❓ {missing['symbol']}: In DB ({missing['calculated_qty']:,.2f}) but NOT in IBKR\n"
    
    # Crypto Wallets
    if 'crypto_wallets' in audits:
        crypto = audits['crypto_wallets']
        report += f"""
{'='*70}
CRYPTO WALLETS (ACCOUNT-BASED AUDIT)
{'='*70}
  Source: Blockchain APIs
  Accounts Audited: {crypto.get('accounts_audited', 0)}
  Wallets Checked: {crypto.get('wallets_checked', 0)}
  Status: {'✅ SUCCESS' if crypto.get('success') else '❌ FAILED'}
"""
        if not crypto.get('success'):
            report += f"  Error: {crypto.get('error', 'Unknown')}\n"
        else:
            report += "\n  INDIVIDUAL WALLET BALANCES (From Blockchain):\n"
            report += f"  {'-'*65}\n"
            
            # Group wallets by account for better readability
            wallets_by_account = {}
            for wallet in crypto.get('wallet_details', []):
                account_name = wallet.get('account_name', 'Unknown Account')
                if account_name not in wallets_by_account:
                    wallets_by_account[account_name] = []
                wallets_by_account[account_name].append(wallet)
            
            # Display by account - no tick marks, just balances (ticks are for audit results only)
            for account_name in sorted(wallets_by_account.keys()):
                wallets = wallets_by_account[account_name]
                report += f"\n  {account_name}:\n"
                
                for wallet in wallets:
                    symbol = wallet.get('symbol', '?')
                    
                    if wallet.get('status') == 'success':
                        total = wallet.get('blockchain_total', 0)
                        liquid = wallet.get('blockchain_liquid', 0)
                        staked = wallet.get('blockchain_staked', 0)
                        source = wallet.get('source', 'N/A')
                        
                        # Plain display without tick mark - this is just a summary, not an audit result
                        report += f"    {symbol}: {total:,.8f}"
                        if staked > 0:
                            report += f" (Liquid: {liquid:,.8f}, Staked: {staked:,.8f})"
                        report += f"\n        Source: {source}\n"
                        
                        # Extra staking fields
                        if wallet.get('blockchain_unstaking', 0) > 0:
                            report += f"        Unstaking: {wallet['blockchain_unstaking']:,.8f}\n"
                        if wallet.get('blockchain_activating', 0) > 0:
                            report += f"        Activating: {wallet['blockchain_activating']:,.8f}\n"
                        if wallet.get('blockchain_deactivating', 0) > 0:
                            report += f"        Deactivating: {wallet['blockchain_deactivating']:,.8f}\n"
                    else:
                        # Only show error indicator when fetch actually failed
                        report += f"    ❌ {symbol}: FETCH FAILED\n"
            
            # Aggregate comparison at the end
            base_currency = get_global_base_currency()
            comparison = crypto.get('comparison', {})
            report += f"""
  {'-'*65}
  AGGREGATE COMPARISON (Total Database vs Total Blockchain):
  {'-'*65}
  Symbols Checked: {comparison.get('total_checked', 0)}
  Matches: {comparison.get('total_matches', 0)}
  Discrepancies: {comparison.get('total_discrepancies', 0)}
  Minor Differences (<{base_currency}0.01): {comparison.get('total_minor_differences', 0)}

"""
            
            if comparison.get('total_discrepancies', 0) == 0 and comparison.get('total_minor_differences', 0) == 0:
                report += "  ✅ All symbols match perfectly!\n"
                for match in comparison.get('matches', []):
                    report += f"    ✅ {match['symbol']}: {match['quantity']:,.8f}\n"
            else:
                # Show matches briefly
                if comparison.get('matches'):
                    report += "  Matching Symbols:\n"
                    for match in comparison.get('matches', [])[:5]:  # Show first 5
                        report += f"    ✅ {match['symbol']}: {match['quantity']:,.8f}\n"
                    if len(comparison.get('matches', [])) > 5:
                        report += f"    ... and {len(comparison.get('matches', [])) - 5} more\n"
                
                # Show minor differences (not flagged as discrepancies)
                if comparison.get('minor_differences'):
                    report += f"\n  ℹ️  MINOR DIFFERENCES (Below {base_currency} 0.01 threshold):\n"
                    for minor in comparison.get('minor_differences', []):
                        symbol = minor['symbol']
                        db_qty = minor['calculated_qty']
                        blockchain_qty = minor['actual_qty']
                        diff = minor['difference']
                        value = minor.get('value_diff_base', 0)
                        
                        report += f"    ℹ️  {symbol}:\n"
                        report += f"        Database: {db_qty:,.8f}\n"
                        report += f"        Blockchain: {blockchain_qty:,.8f}\n"
                        report += f"        Difference: {diff:,.8f} ({base_currency} {value:,.4f})\n"
                
                # Show discrepancies in detail
                if comparison.get('discrepancies'):
                    report += f"\n  ⚠️  DISCREPANCIES (Exceeding {base_currency} 0.01 threshold):\n"
                    for disc in comparison.get('discrepancies', []):
                        symbol = disc['symbol']
                        db_qty = disc['calculated_qty']
                        blockchain_qty = disc['actual_qty']
                        diff = disc['difference']
                        value = disc.get('value_diff_base', 0)
                        
                        report += f"    ⚠️  {symbol}:\n"
                        report += f"        Database: {db_qty:,.8f}\n"
                        report += f"        Blockchain: {blockchain_qty:,.8f}\n"
                        report += f"        Difference: {diff:,.8f} ({base_currency} {value:,.4f})\n"
                
                if comparison.get('missing_in_calculated'):
                    report += "\n  ❓ In Blockchain but NOT in Database:\n"
                    for missing in comparison.get('missing_in_calculated', []):
                        report += f"    ❓ {missing['symbol']}: {missing['actual_qty']:,.8f}\n"
                
                if comparison.get('missing_in_actual'):
                    report += "\n  ❓ In Database but NOT in Blockchain:\n"
                    for missing in comparison.get('missing_in_actual', []):
                        report += f"    ❓ {missing['symbol']}: {missing['calculated_qty']:,.8f}\n"
    
    report += f"""
{'='*70}
END OF AUDIT REPORT
{'='*70}

Next Steps:
- If discrepancies found, investigate unsynced transactions
- Run lot recreation if needed: docker exec nicefolio_worker python scripts/recreate_lots.py
- Re-run audit after fixes: docker exec nicefolio_worker python -c "from service.audit_service import run_full_position_audit; import json; print(json.dumps(run_full_position_audit(), indent=2, default=str))"
"""
    
    return report


def send_audit_notification(audit_results: Dict) -> bool:
    """
    Send notification with detailed audit results.
    
    ALWAYS sends notification (not just on discrepancies) with full report.
    This is a critical security feature to confirm audits are running.
    
    Args:
        audit_results: Results from run_full_position_audit()
        
    Returns:
        True if notification sent successfully
    """
    service = get_notification_service()
    
    if not service.enabled:
        logger.debug("Notifications disabled - skipping audit notification")
        return False
    
    summary = audit_results.get('summary', {})
    discrepancies = summary.get('total_discrepancies', 0)
    issues = summary.get('portfolios_with_issues', [])
    
    # Determine severity and subject
    if discrepancies > 0:
        severity = "critical"
        subject = f"⚠️ NiceFolio: Position Audit Found {discrepancies} Discrepancies"
    elif any('FAILED' in issue for issue in issues):
        severity = "warning"
        subject = "🚨 NiceFolio: Position Audit Failed"
    else:
        severity = "info"
        subject = "✅ NiceFolio: Position Audit Passed - All Verified"
    
    # Build detailed report
    message = format_detailed_audit_report(audit_results)
    
    # Send to all enabled channels
    success = False
    
    if service.channels.get('email', {}).get('enabled'):
        if service._send_email(subject, message):
            success = True
    
    if service.channels.get('home_assistant', {}).get('enabled'):
        if service._send_home_assistant(subject, message, severity):
            success = True
    
    if service.channels.get('telegram', {}).get('enabled'):
        if service._send_telegram(subject, message):
            success = True
    
    if success:
        logger.info(f"Audit notification sent: {subject}")
    else:
        logger.warning("Failed to send audit notification to any channel")
    
    return success


# Convenience function for weekly job
def run_position_audit_job() -> None:
    """
    Run position audit as a scheduled job.
    
    This function is called by the weekly scheduler.
    It runs the full audit and ALWAYS sends notification with detailed report.
    """
    logger.info("Starting weekly position audit job...")
    
    try:
        results = run_full_position_audit()
        
        # ALWAYS send notification with detailed report
        send_audit_notification(results)
        
        logger.info("Position audit job completed")
        
    except Exception as e:
        logger.error(f"Error in position audit job: {e}", exc_info=True)
        
        # Send failure notification
        from utils.notifications import send_job_failure_alert
        send_job_failure_alert(
            job_name="Position Audit",
            error_message=str(e),
            job_type="weekly",
            additional_info={'context': 'Tax compliance audit failed'}
        )


if __name__ == "__main__":
    # Run audit when executed directly
    import json
    results = run_full_position_audit()
    print(json.dumps(results, indent=2, default=str))
