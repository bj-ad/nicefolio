"""
Base configuration loader with caching and validation.
Provides a consistent pattern for loading YAML configuration files.
"""
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from utils.logging_config import get_logger

logger = get_logger(__name__)


class ConfigLoader:
    """
    Base class for configuration loaders with caching and validation.
    Implements singleton pattern to ensure config is loaded only once.
    """
    _instance: Optional['ConfigLoader'] = None
    _config: Optional[Dict[str, Any]] = None
    _config_filename: str = None  # To be set by subclasses
    
    def __new__(cls, *args, **kwargs):
        """Implement singleton pattern."""
        if not cls._instance:
            cls._instance = super(ConfigLoader, cls).__new__(cls)
        return cls._instance
    
    def __init__(self, reload: bool = False):
        """
        Initialize the config loader.
        
        Args:
            reload: Force reload of config even if cached
        """
        if reload or self._config is None:
            self._load_config()
    
    def _get_config_path(self) -> Path:
        """
        Get the absolute path to the config file.
        
        Returns:
            Path: Absolute path to config file
        """
        if not self._config_filename:
            raise ValueError("_config_filename must be set by subclass")
        
        # Get the directory containing this file
        utils_dir = Path(__file__).parent
        # Navigate to config directory
        config_dir = utils_dir.parent / 'config'
        config_path = config_dir / self._config_filename
        
        return config_path
    
    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}\n"
                f"Expected at: {config_path.absolute()}"
            )
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f)
            
            # Validate after loading
            self._validate_config()
            
            logger.info(f"Loaded configuration from {config_path.name}")
            
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {config_path.name}: {e}")
        except Exception as e:
            raise RuntimeError(f"Error loading {config_path.name}: {e}")
    
    def _validate_config(self) -> None:
        """
        Validate the loaded configuration.
        Should be overridden by subclasses to implement specific validation.
        """
        if self._config is None:
            raise ValueError("Configuration is None - not loaded properly")
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get the loaded configuration.
        
        Returns:
            Dict: Configuration dictionary
        """
        if self._config is None:
            self._load_config()
        return self._config
    
    def reload(self) -> None:
        """Force reload of configuration from file."""
        logger.info(f"Reloading configuration from {self._config_filename}")
        self._config = None
        self._load_config()
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key with optional default.
        
        Args:
            key: Configuration key (supports dot notation for nested keys)
            default: Default value if key not found
        
        Returns:
            Configuration value or default
        
        Example:
            config.get('scheduler.target_hour_jobs', 5)
        """
        config = self.get_config()
        
        # Support dot notation for nested keys
        keys = key.split('.')
        value = config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value


class ListConfigLoader(ConfigLoader):
    """
    Base class for configuration loaders that load lists of items.
    Used for accounts_config.yaml and portfolio_config.yaml.
    """
    
    def get_config(self) -> list:
        """
        Get the loaded configuration as a list.
        
        Returns:
            list: Configuration list
        """
        if self._config is None:
            self._load_config()
        return self._config if self._config else []
    
    def get_by_id(self, item_id: int) -> Optional[Dict[str, Any]]:
        """
        Get an item from the list by ID.
        
        Args:
            item_id: ID of the item to retrieve
        
        Returns:
            Dict or None if not found
        """
        items = self.get_config()
        for item in items:
            if item.get('id') == item_id:
                return item
        return None
    
    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get an item from the list by name.
        
        Args:
            name: Name of the item to retrieve
        
        Returns:
            Dict or None if not found
        """
        items = self.get_config()
        for item in items:
            if item.get('name') == name:
                return item
        return None
    
    def get_all_ids(self) -> set:
        """
        Get all IDs from the configuration.
        
        Returns:
            set: Set of all IDs
        """
        items = self.get_config()
        return {item.get('id') for item in items if 'id' in item}
    
    def get_all_names(self) -> set:
        """
        Get all names from the configuration.
        
        Returns:
            set: Set of all names
        """
        items = self.get_config()
        return {item.get('name') for item in items if 'name' in item}
