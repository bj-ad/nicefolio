#!/usr/bin/env python3
"""
Config Initialization Script
Copies template config files to active configs if they don't exist.
Safe to run multiple times (idempotent).

Usage:
    python scripts/init_configs.py
    
Called automatically by docker-entrypoint.sh on container start.
"""
import os
import shutil
from pathlib import Path

# Config directory
CONFIG_DIR = Path(__file__).parent.parent / "config"

# Config files that should be initialized from templates
CONFIG_FILES = [
    "accounts_config.yaml",
    "app_config.yaml",
    "portfolio_config.yaml",
    "source_mapping.yaml",
    "symbol_mapping.yaml",
    "symbol_normalization.yaml",
]


def init_config_file(config_name: str) -> bool:
    """
    Initialize a config file from its template if it doesn't exist.
    
    Args:
        config_name: Name of the config file (e.g., "accounts_config.yaml")
        
    Returns:
        bool: True if file was created, False if it already exists
    """
    config_path = CONFIG_DIR / config_name
    template_path = CONFIG_DIR / f"{config_name}.template"
    
    # Check if config already exists
    if config_path.exists():
        return False
    
    # Check if template exists
    if not template_path.exists():
        print(f"⚠️  Warning: Template not found: {template_path}")
        return False
    
    # Copy template to config
    try:
        shutil.copy2(template_path, config_path)
        print(f"✅ Created {config_name} from template")
        return True
    except Exception as e:
        print(f"❌ Failed to create {config_name}: {e}")
        return False


def main():
    """Initialize all config files from templates if needed."""
    print("=" * 60)
    print("Config Initialization")
    print("=" * 60)
    
    # Ensure config directory exists
    if not CONFIG_DIR.exists():
        print(f"❌ Config directory not found: {CONFIG_DIR}")
        return 1
    
    print(f"📁 Config directory: {CONFIG_DIR}")
    print()
    
    created_count = 0
    existing_count = 0
    
    for config_name in CONFIG_FILES:
        was_created = init_config_file(config_name)
        if was_created:
            created_count += 1
        else:
            config_path = CONFIG_DIR / config_name
            if config_path.exists():
                existing_count += 1
            # If neither exists, warning was already printed
    
    print()
    print("=" * 60)
    print("Summary:")
    print(f"  - Created: {created_count} config file(s)")
    print(f"  - Already exists: {existing_count} config file(s)")
    
    if created_count > 0:
        print()
        print("⚠️  NOTE: Review and customize the created config files!")
        print("   Especially check accounts_config.yaml and portfolio_config.yaml")
    
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    exit(main())
