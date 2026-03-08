#!/usr/bin/env python3
"""
Database initialization script.
Creates all tables, indexes, and constraints defined in models.py
Run this before starting the application.

This script is idempotent - safe to run multiple times.
If tables already exist, they won't be recreated.
"""

import sys
from database import engine, Base
from models import (
    Portfolio, Account, Transaction, Position, Lot, Snapshot,
    CryptoWallet, CryptoBalance, CryptoTransferLink,
    MarketData, FxRate,
    # Pre-computation cache tables
    PortfolioSummaryCache, PeriodStatisticsCache, ChartDataCache
)
from utils.logging_config import get_logger

logger = get_logger(__name__)

EXPECTED_TABLES = 14  # Increased from 11 to include 3 new cache tables


def clean_public_schema():
    """
    Drop ALL objects in the public schema.
    This is used when we detect a corrupted/partial state.
    
    IMPORTANT: This also disposes the SQLAlchemy connection pool to ensure
    no stale connections with uncommitted transaction state are reused.
    """
    logger.warning("🧹 Cleaning public schema (dropping all objects)...")
    
    # First, dispose of ALL connections in the pool to clear any uncommitted state
    # This is critical because create_all() may have left a connection with pending transactions
    engine.dispose()
    logger.info("Connection pool disposed")
    
    # Now get a fresh connection and drop the schema
    conn = engine.raw_connection()
    try:
        cursor = conn.cursor()
        
        # Drop everything in public schema and recreate it
        # This is the nuclear option but it's clean and reliable
        cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cursor.execute("CREATE SCHEMA public")
        cursor.execute("GRANT ALL ON SCHEMA public TO public")
        conn.commit()
        
        logger.info("✅ Public schema cleaned")
        cursor.close()
    finally:
        conn.close()
    
    # Dispose again to ensure the next create_all gets a completely fresh connection
    engine.dispose()


def count_schema_objects():
    """Count tables and other objects in public schema."""
    conn = engine.raw_connection()
    try:
        cursor = conn.cursor()
        
        # Count tables
        cursor.execute("""
            SELECT COUNT(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
        """)
        table_count = cursor.fetchone()[0]
        
        # Count indexes
        cursor.execute("""
            SELECT COUNT(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'i'
        """)
        index_count = cursor.fetchone()[0]
        
        cursor.close()
        return table_count, index_count
    finally:
        conn.close()


def init_database():
    """
    Initialize database by creating all tables.
    This is idempotent - safe to run multiple times.
    Existing tables will not be modified.
    """
    from sqlalchemy import inspect
    
    logger.info("🔧 Initializing database schema...")
    logger.info(f"📍 Database URL: {engine.url}")
    
    # Check current state
    table_count, index_count = count_schema_objects()
    logger.info(f"Current state: {table_count} tables, {index_count} indexes")
    
    # Case 1: Schema is complete
    if table_count >= EXPECTED_TABLES:
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        logger.info(f"✅ Database schema already exists ({len(existing_tables)} tables)")
        logger.info(f"📊 Tables: {', '.join(sorted(existing_tables))}")
        return True
    
    # Case 2: Partial state (indexes exist but no tables) - clean and recreate
    if table_count == 0 and index_count > 0:
        logger.warning(f"⚠️  Corrupted state detected: {index_count} indexes but 0 tables")
        clean_public_schema()
    
    # Case 3: Empty or partially empty - create schema
    logger.info("Creating database schema...")
    
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        
        # Verify
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        
        if len(existing_tables) >= EXPECTED_TABLES:
            logger.info("✅ Database schema initialized successfully!")
            logger.info(f"📊 Tables ({len(existing_tables)}): {', '.join(sorted(existing_tables))}")
            return True
        else:
            logger.error(f"❌ Schema creation incomplete: {len(existing_tables)}/{EXPECTED_TABLES} tables")
            return False
            
    except Exception as e:
        error_str = str(e).lower()
        
        # If "already exists" error, try cleaning and recreating
        if "already exists" in error_str:
            logger.warning(f"⚠️  Conflict detected: {e}")
            logger.info("Attempting clean recreation...")
            
            try:
                clean_public_schema()
                Base.metadata.create_all(bind=engine)
                
                inspector = inspect(engine)
                existing_tables = inspector.get_table_names()
                
                if len(existing_tables) >= EXPECTED_TABLES:
                    logger.info("✅ Database schema recreated successfully!")
                    logger.info(f"📊 Tables: {', '.join(sorted(existing_tables))}")
                    return True
                else:
                    logger.error(f"❌ Recreation failed: {len(existing_tables)} tables")
                    return False
                    
            except Exception as e2:
                logger.error(f"❌ Recreation failed: {e2}")
                logger.exception(e2)
                return False
        else:
            logger.error(f"❌ Unexpected error: {e}")
            logger.exception(e)
            return False


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("DATABASE INITIALIZATION")
    logger.info("=" * 60)
    
    success = init_database()
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ INITIALIZATION COMPLETE")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("=" * 60)
        logger.error("❌ INITIALIZATION FAILED")
        logger.error("=" * 60)
        sys.exit(1)
