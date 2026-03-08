#!/usr/bin/env python3
"""
Configuration validation script.
Validates all YAML config files for structure, required fields, and cross-references.
"""
import sys
from typing import List, Tuple
from pathlib import Path

from utils.logging_config import get_logger
from utils.accounts_loader import get_accounts_loader
from utils.portfolios_loader import get_portfolios_loader
from utils.source_mapping_loader import get_source_mapping_loader
from utils.app_config import load_app_config
from utils.symbol_normalizer import SymbolNormalizer

logger = get_logger(__name__)


def validate_accounts_config() -> List[str]:
    """Validate accounts configuration."""
    errors = []
    
    try:
        loader = get_accounts_loader()
        accounts = loader.get_accounts()
        
        if not accounts:
            errors.append("accounts_config.yaml: No accounts defined")
            return errors
        
        # Check for duplicate IDs
        account_ids = [acc.get('id') for acc in accounts]
        if len(account_ids) != len(set(account_ids)):
            errors.append("accounts_config.yaml: Duplicate account IDs found")
        
        # Check for duplicate names
        account_names = [acc.get('name') for acc in accounts]
        if len(account_names) != len(set(account_names)):
            errors.append("accounts_config.yaml: Duplicate account names found")
        
        # Validate each account
        for idx, acc in enumerate(accounts):
            if not acc.get('id'):
                errors.append(f"accounts_config.yaml: Account at index {idx} missing 'id'")
            if not acc.get('name'):
                errors.append(f"accounts_config.yaml: Account at index {idx} missing 'name'")
            if not acc.get('type'):
                errors.append(f"accounts_config.yaml: Account at index {idx} missing 'type'")
        
        logger.info(f"✓ Validated {len(accounts)} accounts")
        
    except Exception as e:
        errors.append(f"accounts_config.yaml: Failed to load - {str(e)}")
    
    return errors


def validate_portfolios_config() -> List[str]:
    """Validate portfolios configuration."""
    errors = []
    
    try:
        loader = get_portfolios_loader()
        portfolios = loader.get_portfolios()
        
        if not portfolios:
            errors.append("portfolio_config.yaml: No portfolios defined")
            return errors
        
        # Check for duplicate IDs
        portfolio_ids = [p.get('id') for p in portfolios]
        if len(portfolio_ids) != len(set(portfolio_ids)):
            errors.append("portfolio_config.yaml: Duplicate portfolio IDs found")
        
        # Check for duplicate names
        portfolio_names = [p.get('name') for p in portfolios]
        if len(portfolio_names) != len(set(portfolio_names)):
            errors.append("portfolio_config.yaml: Duplicate portfolio names found")
        
        # Validate each portfolio
        for idx, p in enumerate(portfolios):
            if not p.get('id'):
                errors.append(f"portfolio_config.yaml: Portfolio at index {idx} missing 'id'")
            if not p.get('name'):
                errors.append(f"portfolio_config.yaml: Portfolio at index {idx} missing 'name'")
            if not p.get('type'):
                errors.append(f"portfolio_config.yaml: Portfolio at index {idx} missing 'type'")
            # NOTE: base_currency is now global in app_config.yaml, not per-portfolio
        
        logger.info(f"✓ Validated {len(portfolios)} portfolios")
        
    except Exception as e:
        errors.append(f"portfolio_config.yaml: Failed to load - {str(e)}")
    
    return errors


def validate_source_mapping_config() -> List[str]:
    """Validate source mapping configuration."""
    errors = []
    
    try:
        loader = get_source_mapping_loader()
        sources = loader.get_all_sources()
        
        if not sources:
            errors.append("source_mapping.yaml: No sources defined")
            return errors
        
        # Validate each source
        for source_name in sources:
            mapping = loader.get_source_mapping(source_name)
            if not mapping:
                errors.append(f"source_mapping.yaml: Empty mapping for '{source_name}'")
                continue
            
            if 'account_id' not in mapping:
                errors.append(f"source_mapping.yaml: Source '{source_name}' missing 'account_id'")
            
            if 'default_portfolio_id' not in mapping:
                errors.append(f"source_mapping.yaml: Source '{source_name}' missing 'default_portfolio_id'")
        
        logger.info(f"✓ Validated {len(sources)} data sources")
        
    except Exception as e:
        errors.append(f"source_mapping.yaml: Failed to load - {str(e)}")
    
    return errors


def validate_cross_references() -> List[str]:
    """Validate cross-references between configs."""
    errors = []
    
    try:
        accounts_loader = get_accounts_loader()
        portfolios_loader = get_portfolios_loader()
        source_mapping_loader = get_source_mapping_loader()
        
        account_ids = set(accounts_loader.get_account_ids())
        portfolio_ids = set(portfolios_loader.get_portfolio_ids())
        
        # Check source_mapping references
        for source_name in source_mapping_loader.get_all_sources():
            # Check account_id
            account_id = source_mapping_loader.get_account_id(source_name)
            if account_id and account_id not in account_ids:
                errors.append(
                    f"source_mapping.yaml: Source '{source_name}' references "
                    f"non-existent account_id {account_id}"
                )
            
            # Check default_portfolio_id
            portfolio_id = source_mapping_loader.get_default_portfolio_id(source_name)
            if portfolio_id and portfolio_id not in portfolio_ids:
                errors.append(
                    f"source_mapping.yaml: Source '{source_name}' references "
                    f"non-existent default_portfolio_id {portfolio_id}"
                )
            
            # Check symbol_mappings portfolio_ids
            mapping = source_mapping_loader.get_source_mapping(source_name)
            for sym_map in mapping.get('symbol_mappings', []):
                pid = sym_map.get('portfolio_id')
                if pid and pid not in portfolio_ids:
                    errors.append(
                        f"source_mapping.yaml: Source '{source_name}' symbol mapping "
                        f"references non-existent portfolio_id {pid}"
                    )
        
        # Check portfolios reference valid accounts
        for portfolio in portfolios_loader.get_portfolios():
            acc_id = portfolio.get('account_id')
            if acc_id and acc_id not in account_ids:
                errors.append(
                    f"portfolio_config.yaml: Portfolio '{portfolio.get('name')}' "
                    f"references non-existent account_id {acc_id}"
                )
        
        if not errors:
            logger.info("✓ All cross-references valid")
        
    except Exception as e:
        errors.append(f"Cross-reference validation failed: {str(e)}")
    
    return errors


def validate_app_config() -> List[str]:
    """Validate application configuration."""
    errors = []
    
    try:
        config = load_app_config()
        
        # Check for global base_currency (NEW REQUIREMENT)
        if 'base_currency' not in config:
            errors.append("app_config.yaml: Missing required 'base_currency' field")
        elif not isinstance(config['base_currency'], str) or len(config['base_currency']) != 3:
            errors.append("app_config.yaml: 'base_currency' must be a 3-letter currency code (e.g., EUR, USD, THB)")
        
        # Check for critical settings
        required_keys = ['trading_base_currency', 'hodl_symbols']
        for key in required_keys:
            if key not in config:
                errors.append(f"app_config.yaml: Missing required key '{key}'")
        
        # Validate scheduler settings
        scheduler = config.get('scheduler', {})
        if scheduler:
            hour_keys = ['snapshot_hour', 'position_reconciliation_hour', 'lot_reconciliation_hour']
            for key in hour_keys:
                val = scheduler.get(key)
                if val is not None and (not isinstance(val, int) or val < 0 or val > 23):
                    errors.append(f"app_config.yaml: Invalid scheduler.{key} (must be 0-23)")
        
        # Validate portfolio settings
        portfolio = config.get('portfolio', {})
        if portfolio:
            lot_method = portfolio.get('lot_method')
            if lot_method and lot_method not in ['FIFO', 'LIFO']:
                errors.append(f"app_config.yaml: Invalid portfolio.lot_method '{lot_method}'")
        
        if not errors:
            logger.info("✓ app_config.yaml valid")
        
    except Exception as e:
        errors.append(f"app_config.yaml: Failed to load - {str(e)}")
    
    return errors


def validate_symbol_normalization() -> List[str]:
    """Validate symbol normalization configuration."""
    errors = []
    
    try:
        normalizer = SymbolNormalizer()
        
        # Just try to access the normalizer - it loads internally
        # If it loads without error, consider it valid
        logger.info("✓ symbol_normalization.yaml valid")
        
    except Exception as e:
        errors.append(f"symbol_normalization.yaml: Failed to load - {str(e)}")
    
    return errors


def main():
    """Run all validation checks."""
    print("\n" + "="*60)
    print("NiceFolio Configuration Validation")
    print("="*60 + "\n")
    
    all_errors = []
    
    # Run individual validations
    validations = [
        ("Accounts Config", validate_accounts_config),
        ("Portfolios Config", validate_portfolios_config),
        ("Source Mapping Config", validate_source_mapping_config),
        ("App Config", validate_app_config),
        ("Symbol Normalization", validate_symbol_normalization),
        ("Cross-References", validate_cross_references),
    ]
    
    for name, validator in validations:
        print(f"\nValidating {name}...")
        errors = validator()
        if errors:
            all_errors.extend(errors)
            for error in errors:
                print(f"  ✗ {error}")
        else:
            print(f"  ✓ {name} passed")
    
    # Summary
    print("\n" + "="*60)
    if all_errors:
        print(f"❌ Validation FAILED with {len(all_errors)} error(s):")
        for error in all_errors:
            print(f"  - {error}")
        print("="*60 + "\n")
        sys.exit(1)
    else:
        print("✅ All configuration files are valid!")
        print("="*60 + "\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
