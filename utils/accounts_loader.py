"""
Accounts configuration loader.
Provides singleton access to accounts_config.yaml with caching and validation.
"""
from typing import Dict, Any, Optional, List
from utils.config_loader import ConfigLoader
from utils.logging_config import get_logger

logger = get_logger(__name__)


class AccountsLoader(ConfigLoader):
    """Loader for accounts_config.yaml configuration."""
    
    _config_filename = 'accounts_config.yaml'
    
    def _validate_config(self) -> None:
        """Validate accounts configuration."""
        super()._validate_config()
        
        # Config file is a list directly, not wrapped in a dict
        if not isinstance(self._config, list):
            raise ValueError("Accounts config must be a list")
        
        accounts_list = self._config
        
        # Validate each account
        for idx, account in enumerate(accounts_list):
            if not isinstance(account, dict):
                raise ValueError(f"Account at index {idx} must be a dictionary")
            
            # Check for required fields
            if 'id' not in account:
                raise ValueError(f"Account at index {idx} missing required field 'id'")
            
            if 'name' not in account:
                logger.warning(f"Account {account.get('id')} missing 'name'")
            
            if 'type' not in account:
                logger.warning(f"Account {account.get('id')} missing 'type'")
    
    def get_accounts(self) -> List[Dict[str, Any]]:
        """
        Get list of all accounts.
        
        Returns:
            List[Dict]: List of account configurations
        """
        config = self.get_config()
        # Config file is a list directly
        return config if isinstance(config, list) else []
    
    def get_account_by_id(self, account_id: int) -> Optional[Dict[str, Any]]:
        """
        Get account configuration by ID.
        
        Args:
            account_id: Account ID
        
        Returns:
            Dict: Account configuration or None if not found
        """
        accounts = self.get_accounts()
        for account in accounts:
            if account.get('id') == account_id:
                return account
        return None
    
    def get_account_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get account configuration by name.
        
        Args:
            name: Account name
        
        Returns:
            Dict: Account configuration or None if not found
        """
        accounts = self.get_accounts()
        for account in accounts:
            if account.get('name') == name:
                return account
        return None
    
    def get_account_name(self, account_id: int) -> Optional[str]:
        """
        Get account name by ID.
        
        Args:
            account_id: Account ID
        
        Returns:
            str: Account name or None if not found
        """
        account = self.get_account_by_id(account_id)
        return account.get('name') if account else None
    
    def get_account_type(self, account_id: int) -> Optional[str]:
        """
        Get account type by ID.
        
        Args:
            account_id: Account ID
        
        Returns:
            str: Account type or None if not found
        """
        account = self.get_account_by_id(account_id)
        return account.get('type') if account else None
    
    def get_accounts_by_type(self, account_type: str) -> List[Dict[str, Any]]:
        """
        Get all accounts of a specific type.
        
        Args:
            account_type: Account type (e.g., 'brokerage', 'exchange', 'wallet')
        
        Returns:
            List[Dict]: List of matching accounts
        """
        accounts = self.get_accounts()
        return [acc for acc in accounts if acc.get('type') == account_type]
    
    def get_account_ids(self) -> List[int]:
        """
        Get list of all account IDs.
        
        Returns:
            List[int]: List of account IDs
        """
        accounts = self.get_accounts()
        return [acc.get('id') for acc in accounts if 'id' in acc]
    
    def account_exists(self, account_id: int) -> bool:
        """
        Check if an account exists.
        
        Args:
            account_id: Account ID
        
        Returns:
            bool: True if account exists
        """
        return self.get_account_by_id(account_id) is not None


# Singleton instance
_accounts_loader: Optional[AccountsLoader] = None


def load_accounts_config(reload: bool = False) -> Dict[str, Any]:
    """
    Load accounts configuration (singleton).
    
    Args:
        reload: Force reload of configuration
    
    Returns:
        Dict: Accounts configuration
    """
    global _accounts_loader
    if _accounts_loader is None or reload:
        _accounts_loader = AccountsLoader(reload=reload)
    return _accounts_loader.get_config()


def get_accounts_loader() -> AccountsLoader:
    """
    Get the accounts loader instance.
    
    Returns:
        AccountsLoader: Singleton loader instance
    """
    global _accounts_loader
    if _accounts_loader is None:
        _accounts_loader = AccountsLoader()
    return _accounts_loader
