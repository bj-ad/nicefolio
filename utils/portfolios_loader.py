"""
Portfolios configuration loader.
Provides singleton access to portfolio_config.yaml with caching and validation.
"""
from typing import Dict, Any, Optional, List
from utils.config_loader import ConfigLoader
from utils.logging_config import get_logger

logger = get_logger(__name__)


class PortfoliosLoader(ConfigLoader):
    """Loader for portfolio_config.yaml configuration."""
    
    _config_filename = 'portfolio_config.yaml'
    
    def _validate_config(self) -> None:
        """Validate portfolios configuration."""
        super()._validate_config()
        
        # Config file is a list directly, not wrapped in a dict
        if not isinstance(self._config, list):
            raise ValueError("Portfolios config must be a list")
        
        portfolios_list = self._config
        
        # Validate each portfolio
        for idx, portfolio in enumerate(portfolios_list):
            if not isinstance(portfolio, dict):
                raise ValueError(f"Portfolio at index {idx} must be a dictionary")
            
            # Check for required fields
            if 'id' not in portfolio:
                raise ValueError(f"Portfolio at index {idx} missing required field 'id'")
            
            if 'name' not in portfolio:
                logger.warning(f"Portfolio {portfolio.get('id')} missing 'name'")
            
            if 'type' not in portfolio:
                logger.warning(f"Portfolio {portfolio.get('id')} missing 'type'")
    
    def get_portfolios(self) -> List[Dict[str, Any]]:
        """
        Get list of all portfolios.
        
        Returns:
            List[Dict]: List of portfolio configurations
        """
        config = self.get_config()
        # Config file is a list directly
        return config if isinstance(config, list) else []
    
    def get_portfolio_by_id(self, portfolio_id: int) -> Optional[Dict[str, Any]]:
        """
        Get portfolio configuration by ID.
        
        Args:
            portfolio_id: Portfolio ID
        
        Returns:
            Dict: Portfolio configuration or None if not found
        """
        portfolios = self.get_portfolios()
        for portfolio in portfolios:
            if portfolio.get('id') == portfolio_id:
                return portfolio
        return None
    
    def get_portfolio_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get portfolio configuration by name.
        
        Args:
            name: Portfolio name
        
        Returns:
            Dict: Portfolio configuration or None if not found
        """
        portfolios = self.get_portfolios()
        for portfolio in portfolios:
            if portfolio.get('name') == name:
                return portfolio
        return None
    
    def get_portfolio_name(self, portfolio_id: int) -> Optional[str]:
        """
        Get portfolio name by ID.
        
        Args:
            portfolio_id: Portfolio ID
        
        Returns:
            str: Portfolio name or None if not found
        """
        portfolio = self.get_portfolio_by_id(portfolio_id)
        return portfolio.get('name') if portfolio else None
    
    def get_portfolio_type(self, portfolio_id: int) -> Optional[str]:
        """
        Get portfolio type by ID.
        
        Args:
            portfolio_id: Portfolio ID
        
        Returns:
            str: Portfolio type or None if not found
        """
        portfolio = self.get_portfolio_by_id(portfolio_id)
        return portfolio.get('type') if portfolio else None
    
    def get_portfolio_currency(self, portfolio_id: int) -> Optional[str]:
        """
        Get portfolio base currency by ID.
        
        Args:
            portfolio_id: Portfolio ID
        
        Returns:
            str: Currency code or None if not found
        """
        portfolio = self.get_portfolio_by_id(portfolio_id)
        return portfolio.get('currency') if portfolio else None
    
    def get_portfolios_by_type(self, portfolio_type: str) -> List[Dict[str, Any]]:
        """
        Get all portfolios of a specific type.
        
        Args:
            portfolio_type: Portfolio type (e.g., 'securities', 'crypto', 'commodities')
        
        Returns:
            List[Dict]: List of matching portfolios
        """
        portfolios = self.get_portfolios()
        return [p for p in portfolios if p.get('type') == portfolio_type]
    
    def get_portfolio_ids(self) -> List[int]:
        """
        Get list of all portfolio IDs.
        
        Returns:
            List[int]: List of portfolio IDs
        """
        portfolios = self.get_portfolios()
        return [p.get('id') for p in portfolios if 'id' in p]
    
    def portfolio_exists(self, portfolio_id: int) -> bool:
        """
        Check if a portfolio exists.
        
        Args:
            portfolio_id: Portfolio ID
        
        Returns:
            bool: True if portfolio exists
        """
        return self.get_portfolio_by_id(portfolio_id) is not None
    
    def get_portfolios_by_account(self, account_id: int) -> List[Dict[str, Any]]:
        """
        Get all portfolios associated with an account.
        
        Args:
            account_id: Account ID
        
        Returns:
            List[Dict]: List of portfolios for the account
        """
        portfolios = self.get_portfolios()
        return [p for p in portfolios if p.get('account_id') == account_id]


# Singleton instance
_portfolios_loader: Optional[PortfoliosLoader] = None


def load_portfolios_config(reload: bool = False) -> Dict[str, Any]:
    """
    Load portfolios configuration (singleton).
    
    Args:
        reload: Force reload of configuration
    
    Returns:
        Dict: Portfolios configuration
    """
    global _portfolios_loader
    if _portfolios_loader is None or reload:
        _portfolios_loader = PortfoliosLoader(reload=reload)
    return _portfolios_loader.get_config()


def get_portfolios_loader() -> PortfoliosLoader:
    """
    Get the portfolios loader instance.
    
    Returns:
        PortfoliosLoader: Singleton loader instance
    """
    global _portfolios_loader
    if _portfolios_loader is None:
        _portfolios_loader = PortfoliosLoader()
    return _portfolios_loader
