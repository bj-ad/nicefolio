"""
CRUD operations for SymbolMapping model.

This module handles the two-tier auto-discovery system for symbol→currency mapping:
- Tier 1 (Primary): Database lookup
- Tier 2 (Fallback): symbol_mapping.yaml config file
- Tier 3 (Default): Hardcoded defaults (USD for US securities/crypto)

Auto-population sources:
- IBKR Flex Query: Uses reported trade.currency
- Crypto sync: Defaults to USD
- Manual config: European ETFs with exchange suffixes
"""

from typing import Optional
from pathlib import Path
import yaml
from sqlalchemy.orm import Session
from utils.logging_config import get_logger
from models import SymbolMapping
from utils.datetime_utils import now_utc

logger = get_logger(__name__)

# Cache for config file (loaded once)
_symbol_mapping_cache: Optional[dict] = None


def load_symbol_mapping() -> dict:
    """
    Load symbol_mapping.yaml configuration file.
    
    Returns:
        dict: Mapping configuration with symbol keys
    """
    global _symbol_mapping_cache
    
    if _symbol_mapping_cache is not None:
        return _symbol_mapping_cache
    
    config_path = Path(__file__).parent.parent / 'config' / 'symbol_mapping.yaml'
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            _symbol_mapping_cache = config or {}
            logger.info(f"Loaded {len(_symbol_mapping_cache)} symbols from symbol_mapping.yaml")
            return _symbol_mapping_cache
    except FileNotFoundError:
        logger.warning(f"symbol_mapping.yaml not found at {config_path}")
        _symbol_mapping_cache = {}
        return {}
    except Exception as e:
        logger.error(f"Failed to load symbol_mapping.yaml: {e}")
        _symbol_mapping_cache = {}
        return {}


def get_symbol_mapping(db: Session, symbol: str, currency: str = 'USD', asset_class: str = 'security') -> dict:
    """
    Get symbol mapping with three-tier fallback.
    
    Uses (symbol, currency) composite lookup to support:
    - Same symbol on different exchanges with same currency (e.g., VUAA on XETRA and Amsterdam)
    - Same symbol in different currencies (e.g., BTC in USD vs EUR)
    
    Priority:
    1. Database (SymbolMapping table) - lookup by (symbol, currency)
    2. Config file (symbol_mapping.yaml)
    3. Hardcoded defaults
    
    Args:
        db: Database session
        symbol: Trading symbol (e.g., "VWCE", "BTC", "VUAA")
        currency: Expected currency (e.g., "EUR", "USD") - defaults to USD
        asset_class: 'security', 'crypto', or 'commodity'
    
    Returns:
        dict: Mapping with keys:
            - symbol: Original symbol
            - yfinance_symbol: Symbol for yfinance API (may include exchange suffix)
            - currency: Price currency (USD, EUR, THB, etc.)
            - exchange: Exchange name (optional)
            - provider: Data provider (yfinance, coinmarketcap, etc.)
            - description: Human-readable description (optional)
    """
    # Tier 1: Database lookup by (symbol, currency)
    mapping = db.query(SymbolMapping).filter_by(symbol=symbol, currency=currency).first()
    
    if mapping:
        logger.debug(f"Symbol {symbol} ({currency}) found in database: {mapping.exchange}")
        return {
            'symbol': symbol,
            'yfinance_symbol': mapping.yfinance_symbol or symbol,
            'currency': mapping.currency,
            'exchange': mapping.exchange,
            'provider': mapping.provider or 'yfinance',
            'description': mapping.description
        }
    
    # Tier 2: Config file lookup
    config_mappings = load_symbol_mapping()
    config_mapping = config_mappings.get(symbol)
    
    if config_mapping and config_mapping.get('currency') == currency:
        logger.info(f"Symbol {symbol} ({currency}) found in symbol_mapping.yaml, auto-populating database")
        
        # Auto-populate database for future lookups
        new_mapping = SymbolMapping(
            symbol=symbol,
            yfinance_symbol=config_mapping.get('yfinance_symbol', symbol),
            currency=config_mapping.get('currency', currency),
            exchange=config_mapping.get('exchange'),
            provider=config_mapping.get('provider', 'yfinance'),
            description=config_mapping.get('description'),
            auto_detected=False,  # Manual config
            last_seen=now_utc()
        )
        
        try:
            db.add(new_mapping)
            db.commit()
            logger.info(f"Added {symbol} ({currency}) to database from config")
        except Exception as e:
            logger.error(f"Failed to save config mapping to database: {e}")
            db.rollback()
        
        return {
            'symbol': symbol,
            'yfinance_symbol': config_mapping.get('yfinance_symbol', symbol),
            'currency': config_mapping.get('currency', currency),
            'exchange': config_mapping.get('exchange'),
            'provider': config_mapping.get('provider', 'yfinance'),
            'description': config_mapping.get('description')
        }
    
    # Tier 3: Hardcoded defaults
    logger.debug(f"Symbol {symbol} ({currency}) not found, using defaults for asset_class={asset_class}")
    
    if asset_class == 'crypto':
        # Crypto: Default to USD with yfinance format
        default_mapping = {
            'symbol': symbol,
            'yfinance_symbol': f"{symbol}-{currency}",
            'currency': currency,
            'exchange': None,
            'provider': 'coinmarketcap',
            'description': None
        }
    else:
        # Securities: Default to symbol as-is
        default_mapping = {
            'symbol': symbol,
            'yfinance_symbol': symbol,
            'currency': currency,
            'exchange': None,
            'provider': 'yfinance',
            'description': None
        }
    
    return default_mapping


def auto_populate_symbol_mapping(
    db: Session,
    symbol: str,
    currency: str,
    exchange: Optional[str] = None,
    provider: Optional[str] = 'yfinance',
    source: str = 'auto',
    description: Optional[str] = None
) -> Optional[SymbolMapping]:
    """
    Auto-populate symbol mapping from parsers (IBKR, crypto sync).
    
    Uses (symbol, currency) composite lookup to support:
    - Same symbol on different exchanges with same currency (e.g., VUAA on XETRA and Amsterdam)
    - Same symbol in different currencies (e.g., BTC in USD vs EUR)
    
    This function is called by:
    - IBKR parser: Uses trade.currency from Flex Query
    - Crypto sync: Defaults to USD
    - Exchange parsers: Uses reported currency
    
    Args:
        db: Database session
        symbol: Trading symbol
        currency: Price currency (from trade or default)
        exchange: Exchange name (optional)
        provider: Data provider (default: yfinance)
        source: Source of mapping (ibkr_flex_query, crypto_sync, etc.)
        description: Human-readable description (optional)
    
    Returns:
        SymbolMapping: Created or existing mapping
    """
    # Check if mapping already exists for (symbol, currency) combination
    existing = db.query(SymbolMapping).filter_by(symbol=symbol, currency=currency).first()
    
    if existing:
        # Update last_seen timestamp
        existing.last_seen = now_utc()
        try:
            db.commit()
            logger.debug(f"Updated last_seen for {symbol} ({currency})")
        except Exception as e:
            logger.error(f"Failed to update last_seen for {symbol} ({currency}): {e}")
            db.rollback()
        return existing
    
    # Create new mapping for (symbol, currency) combination
    new_mapping = SymbolMapping(
        symbol=symbol,
        yfinance_symbol=symbol,  # Default, can be overridden by config
        currency=currency,
        exchange=exchange,
        provider=provider,
        description=description,
        auto_detected=True,
        last_seen=now_utc()
    )
    
    try:
        db.add(new_mapping)
        db.commit()
        logger.info(f"Auto-populated {symbol} ({currency}) from {source}")
        return new_mapping
    except Exception as e:
        logger.error(f"Failed to auto-populate {symbol} ({currency}): {e}")
        db.rollback()
        return None


def update_symbol_mapping(
    db: Session,
    symbol: str,
    currency: str,
    yfinance_symbol: Optional[str] = None,
    exchange: Optional[str] = None,
    provider: Optional[str] = None,
    description: Optional[str] = None
) -> Optional[SymbolMapping]:
    """
    Update existing symbol mapping.
    
    Uses (symbol, currency) composite lookup.
    
    Args:
        db: Database session
        symbol: Trading symbol to update
        currency: Currency of the mapping to update
        yfinance_symbol: New yfinance symbol (optional)
        exchange: New exchange (optional)
        provider: New provider (optional)
        description: New description (optional)
    
    Returns:
        SymbolMapping: Updated mapping or None if not found
    """
    mapping = db.query(SymbolMapping).filter_by(symbol=symbol, currency=currency).first()
    
    if not mapping:
        logger.warning(f"Symbol {symbol} ({currency}) not found in database for update")
        return None
    
    # Update fields if provided
    if yfinance_symbol is not None:
        mapping.yfinance_symbol = yfinance_symbol
    if exchange is not None:
        mapping.exchange = exchange
    if provider is not None:
        mapping.provider = provider
    if description is not None:
        mapping.description = description
    
    mapping.updated_at = now_utc()
    
    try:
        db.commit()
        logger.info(f"Updated mapping for {symbol} ({currency})")
        return mapping
    except Exception as e:
        logger.error(f"Failed to update mapping for {symbol} ({currency}): {e}")
        db.rollback()
        return None


def get_all_symbol_mappings(db: Session) -> list[SymbolMapping]:
    """
    Get all symbol mappings from database.
    
    Args:
        db: Database session
    
    Returns:
        list[SymbolMapping]: All symbol mappings, ordered by symbol then currency
    """
    return db.query(SymbolMapping).order_by(SymbolMapping.symbol, SymbolMapping.currency).all()


def delete_symbol_mapping(db: Session, symbol: str, currency: str) -> bool:
    """
    Delete a symbol mapping.
    
    Uses (symbol, currency) composite lookup.
    
    Args:
        db: Database session
        symbol: Symbol to delete
        currency: Currency of the mapping to delete
    
    Returns:
        bool: True if deleted, False if not found
    """
    mapping = db.query(SymbolMapping).filter_by(symbol=symbol, currency=currency).first()
    
    if not mapping:
        logger.warning(f"Symbol {symbol} ({currency}) not found for deletion")
        return False
    
    try:
        db.delete(mapping)
        db.commit()
        logger.info(f"Deleted mapping for {symbol} ({currency})")
        return True
    except Exception as e:
        logger.error(f"Failed to delete mapping for {symbol} ({currency}): {e}")
        db.rollback()
        return False
