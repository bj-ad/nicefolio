import yaml
from pathlib import Path
from typing import Dict, Optional

class SymbolNormalizer:
    """
    A class to normalize stock symbols based on a YAML configuration file.
    """
    _instance: Optional['SymbolNormalizer'] = None
    _normalization_map: Dict[str, str] = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SymbolNormalizer, cls).__new__(cls)
        return cls._instance

    def __init__(self, config_path: str = 'config/symbol_normalization.yaml'):
        """
        Initializes the SymbolNormalizer by loading the normalization rules.
        The constructor is designed to be a singleton to prevent reloading the file.
        
        Args:
            config_path (str): The path to the symbol normalization YAML file.
        """
        # Load map only if it hasn't been loaded before.
        if not self._normalization_map:
            self._load_normalization_map(config_path)

    def _load_normalization_map(self, config_path: str) -> None:
        """
        Loads the symbol normalization map from the YAML config file and builds
        a reverse mapping for efficient lookups.
        
        Supports two formats:
        1. New simplified format: "BTC": ["BTCUSD", "BTC-USD"]
        2. Old verbose format: "BTC": {"normalized-from": [...], "normalized-to": "BTC"}
        
        Args:
            config_path (str): The path to the YAML configuration file.
        
        Raises:
            FileNotFoundError: If the configuration file cannot be found.
        """
        config_file = Path(config_path)
        if not config_file.is_file():
            raise FileNotFoundError(f"Configuration file not found at: {config_path}")

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        if not config:
            return

        for normalized_symbol, rules in config.items():
            # New simplified format: "BTC": ["BTCUSD", "BTC-USD"]
            if isinstance(rules, list):
                for alias in rules:
                    self._normalization_map[alias] = normalized_symbol
            # Old verbose format: "BTC": {"normalized-from": [...], "normalized-to": "BTC"}
            elif isinstance(rules, dict) and 'normalized-from' in rules:
                if isinstance(rules['normalized-from'], list):
                    for alias in rules['normalized-from']:
                        self._normalization_map[alias] = normalized_symbol
    
    def normalize(self, symbol: str) -> str:
        """
        Normalizes a given stock symbol.
        
        If a normalization rule exists for the symbol, the normalized version is returned.
        Otherwise, the original symbol is returned.
        
        Args:
            symbol (str): The stock symbol to normalize.
            
        Returns:
            str: The normalized stock symbol.
        """
        return self._normalization_map.get(symbol, symbol)

# Example usage:
'''
if __name__ == '__main__':
    try:
        # Initialize the normalizer (it will load the config file)
        normalizer = SymbolNormalizer()

        # Symbols to test
        symbols_to_test = ['BRK.B', 'BRK B', 'GOOG', 'BRK-B']

        print("--- Symbol Normalization Test ---")
        for symbol in symbols_to_test:
            normalized = normalizer.normalize(symbol)
            print(f"Original: '{symbol}' -> Normalized: '{normalized}'")
        
        # Demonstrating the singleton pattern
        print("\n--- Singleton Demonstration ---")
        normalizer2 = SymbolNormalizer()
        print(f"normalizer is normalizer2: {normalizer is normalizer2}")


    except FileNotFoundError as e:
        print(f"Error: {e}")
'''
