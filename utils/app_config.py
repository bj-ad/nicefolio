import os
import yaml
from typing import Any, Dict

_config: Dict[str, Any] = {}
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../config/app_config.yaml")

def load_app_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    Loads the application configuration from app_config.yaml.

    The configuration is loaded only once and cached for subsequent calls
    to improve performance by avoiding repeated file I/O.
    
    Args:
        force_reload: If True, reloads the config from disk even if cached.
                     Useful for picking up config changes without restarting.
    
    Returns:
        Dictionary containing the application configuration
    """
    global _config
    if not _config or force_reload:
        with open(CONFIG_PATH, "r") as f:
            _config = yaml.safe_load(f)
    return _config

def reload_app_config() -> Dict[str, Any]:
    """
    Force reload of the application configuration from disk.
    
    Use this to pick up changes to app_config.yaml without restarting the application.
    
    Returns:
        Dictionary containing the reloaded configuration
    """
    return load_app_config(force_reload=True)


def get_global_base_currency() -> str:
    """
    Get the global base currency from app_config.yaml.
    
    All portfolios use this currency for reporting and calculations.
    Individual accounts can maintain their own native currencies.
    
    Returns:
        str: Global base currency code (e.g., "EUR", "USD", "THB")
        
    Raises:
        KeyError: If base_currency is not configured in app_config.yaml
    """
    config = load_app_config()
    if 'base_currency' not in config:
        raise KeyError(
            "base_currency not configured in app_config.yaml. "
            "This is required to prevent data corruption. "
            "Please add 'base_currency: EUR' (or your currency) to config/app_config.yaml"
        )
    return config['base_currency']
