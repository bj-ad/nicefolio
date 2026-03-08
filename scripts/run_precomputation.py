#!/usr/bin/env python3
"""
Manually trigger cache precomputation for all portfolios.
Run this after code changes to regenerate cache.
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database import SessionLocal
from service.precomputation_service import precompute_all_portfolios
from utils.logging_config import get_logger

logger = get_logger(__name__)

def main():
    """Run precomputation for all portfolios."""
    logger.info("=" * 80)
    logger.info("MANUAL PRECOMPUTATION STARTED")
    logger.info("=" * 80)
    
    try:
        results = precompute_all_portfolios(force=True)
        
        logger.info("=" * 80)
        logger.info("PRECOMPUTATION COMPLETE")
        logger.info(f"Summary: {results.get('summary', 'N/A')}")
        logger.info("=" * 80)
        
        return 0
        
    except Exception as e:
        logger.error(f"Precomputation failed: {e}", exc_info=True)
        return 1

if __name__ == '__main__':
    sys.exit(main())
