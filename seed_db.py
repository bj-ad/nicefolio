#!/usr/bin/env python3
"""
Seed database with initial configuration data.
Run AFTER init_db.py to populate portfolios and accounts from YAML configs.

This script syncs data from:
- config/portfolio_config.yaml -> Portfolio table
- config/accounts_config.yaml -> Account table

This is safe to run multiple times - it will update existing records.
"""

import sys
from database import SessionLocal
from utils.logging_config import get_logger

logger = get_logger(__name__)

def seed_database():
    """Populate database with config data"""
    db = SessionLocal()
    
    try:
        logger.info("🌱 Seeding database with configuration data...")
        
        # Import here to avoid circular dependencies
        from scripts.sync_portfolios import sync_portfolios_from_config
        from scripts.sync_accounts import sync_accounts_from_config
        
        # Sync portfolios from portfolio_config.yaml
        logger.info("📂 Syncing portfolios from config/portfolio_config.yaml...")
        try:
            sync_portfolios_from_config(db)
            logger.info("   ✅ Portfolios synced successfully")
        except Exception as e:
            logger.warning(f"   ⚠️  Portfolio sync skipped: {e}")
        
        # Sync accounts from accounts_config.yaml
        logger.info("👤 Syncing accounts from config/accounts_config.yaml...")
        try:
            sync_accounts_from_config(db)
            logger.info("   ✅ Accounts synced successfully")
        except Exception as e:
            logger.warning(f"   ⚠️  Account sync skipped: {e}")
        
        db.commit()
        logger.info("✅ Database seeded successfully!")
        
        return True
        
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Seeding failed: {e}", exc_info=True)
        return False
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("DATABASE SEEDING")
    logger.info("=" * 60)
    
    success = seed_database()
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ SEEDING COMPLETE")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("=" * 60)
        logger.error("❌ SEEDING FAILED (non-critical)")
        logger.error("=" * 60)
        # Exit with 0 so Docker doesn't fail if configs are missing
        # This is optional seeding, not critical for app startup
        sys.exit(0)
