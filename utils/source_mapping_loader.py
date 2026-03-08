"""
Source mapping configuration loader.
Provides singleton access to source_mapping.yaml with caching and validation.
"""
from typing import Dict, Any, Optional, List
from utils.config_loader import ConfigLoader
from utils.logging_config import get_logger

logger = get_logger(__name__)


class SourceMappingLoader(ConfigLoader):
    """Loader for source_mapping.yaml configuration."""
    
    _config_filename = 'source_mapping.yaml'
    
    def _validate_config(self) -> None:
        """Validate source mapping configuration."""
        super()._validate_config()
        
        if not isinstance(self._config, dict):
            raise ValueError("Source mapping config must be a dictionary")
        
        # Validate each source mapping
        for source_name, mapping in self._config.items():
            if not isinstance(mapping, dict):
                raise ValueError(f"Mapping for '{source_name}' must be a dictionary")
            
            # Check for required fields
            if 'account_id' not in mapping:
                logger.warning(f"Source '{source_name}' missing 'account_id'")
            
            if 'default_portfolio_id' not in mapping:
                logger.warning(f"Source '{source_name}' missing 'default_portfolio_id'")
    
    def get_source_mapping(self, source_name: str) -> Optional[Dict[str, Any]]:
        """
        Get mapping configuration for a specific source.
        
        Args:
            source_name: Name of the data source (e.g., 'IBKR', 'BinanceTH')
        
        Returns:
            Dict with mapping configuration or None if not found
        """
        config = self.get_config()
        return config.get(source_name)
    
    def get_account_id(self, source_name: str) -> Optional[int]:
        """
        Get account ID for a source.
        
        Args:
            source_name: Name of the data source
        
        Returns:
            int: Account ID or None
        """
        mapping = self.get_source_mapping(source_name)
        return mapping.get('account_id') if mapping else None
    
    def get_default_portfolio_id(self, source_name: str) -> Optional[int]:
        """
        Get default portfolio ID for a source.
        
        Args:
            source_name: Name of the data source
        
        Returns:
            int: Portfolio ID or None
        """
        mapping = self.get_source_mapping(source_name)
        return mapping.get('default_portfolio_id') if mapping else None
    
    def get_cash_portfolio_id(self, source_name: str) -> Optional[int]:
        """
        Get cash portfolio ID for a source.
        
        Used for routing cash/stablecoin transactions to the appropriate
        cash portfolio (e.g., Broker Cash Pool).
        Falls back to default_portfolio_id if cash_portfolio_id is not configured.
        
        Args:
            source_name: Name of the data source
        
        Returns:
            int: Cash portfolio ID, or default portfolio ID, or None
        """
        mapping = self.get_source_mapping(source_name)
        if not mapping:
            return None
        return mapping.get('cash_portfolio_id', mapping.get('default_portfolio_id'))
    
    def get_portfolio_id_for_symbol(
        self, 
        source_name: str, 
        symbol: str
    ) -> Optional[int]:
        """
        Get portfolio ID for a specific symbol from a source.
        Falls back to default portfolio if no symbol mapping exists.
        
        Args:
            source_name: Name of the data source
            symbol: Asset symbol
        
        Returns:
            int: Portfolio ID or None
        """
        mapping = self.get_source_mapping(source_name)
        if not mapping:
            return None
        
        # Check symbol mappings
        symbol_mappings = mapping.get('symbol_mappings', [])
        for sym_map in symbol_mappings:
            if symbol in sym_map.get('symbols', []):
                return sym_map.get('portfolio_id')
        
        # Fall back to default
        return mapping.get('default_portfolio_id')
    
    def get_symbol_to_portfolio_map(self, source_name: str) -> Dict[str, int]:
        """
        Get a flat dictionary mapping symbols to portfolio IDs for a source.
        
        Args:
            source_name: Name of the data source
        
        Returns:
            Dict[str, int]: Symbol to portfolio_id mapping
        """
        mapping = self.get_source_mapping(source_name)
        if not mapping:
            return {}
        
        result = {}
        symbol_mappings = mapping.get('symbol_mappings', [])
        
        for sym_map in symbol_mappings:
            portfolio_id = sym_map.get('portfolio_id')
            for symbol in sym_map.get('symbols', []):
                result[symbol] = portfolio_id
        
        return result
    
    def get_all_sources(self) -> List[str]:
        """
        Get list of all configured data sources.
        
        Returns:
            List[str]: List of source names
        """
        config = self.get_config()
        return list(config.keys())


# Singleton instance
_source_mapping_loader: Optional[SourceMappingLoader] = None


def load_source_mapping(reload: bool = False) -> Dict[str, Any]:
    """
    Load source mapping configuration (singleton).
    
    Args:
        reload: Force reload of configuration
    
    Returns:
        Dict: Source mapping configuration
    """
    global _source_mapping_loader
    if _source_mapping_loader is None or reload:
        _source_mapping_loader = SourceMappingLoader(reload=reload)
    return _source_mapping_loader.get_config()


def get_source_mapping_loader() -> SourceMappingLoader:
    """
    Get the source mapping loader instance.
    
    Returns:
        SourceMappingLoader: Singleton loader instance
    """
    global _source_mapping_loader
    if _source_mapping_loader is None:
        _source_mapping_loader = SourceMappingLoader()
    return _source_mapping_loader
