import os
from typing import Optional
from sqlalchemy import func
from utils.logging_config import get_logger
from utils.cache_config import cache, CACHE_TTL, CACHE_MAXSIZE
from utils.app_config import load_app_config
from utils.datetime_utils import now_utc
from crud.crud_market_fx import upsert_fx_rate, ingest_fx_rates
from models import FxRate
from database import SessionLocal
from dotenv import load_dotenv

# ECB integration for German tax compliance
from service.ecb_service import fetch_ecb_rates, get_fallback_rate_from_db

logger = get_logger("fx_service")

load_dotenv()

# Load FX pairs from app_config.yaml
# FX rates are ALWAYS fetched for these pairs regardless of positions
# (German tax compliance requires consistent EUR/USD and EUR/THB rates)
app_config = load_app_config()
FX_PAIRS = app_config.get('fx_pairs', ['EUR/USD', 'EUR/THB'])

logger.info(f"FX pairs configured: {FX_PAIRS}")


# ============================================================================
# ECB ONLY - NO YFINANCE FOR FX (Tax Compliance)
# ============================================================================
# CRITICAL: German tax law requires ECB rates for FX conversions.
# yfinance FX data is NOT acceptable for tax compliance.
# ALL FX rates must come from ECB.
# ============================================================================


def sync_fx_rates():
    """
    Sync official EUR reference rates from ECB for German tax compliance.
    
    IMPORTANT: FX rates are ALWAYS fetched for configured pairs (EUR/USD, EUR/THB)
    regardless of whether there are positions. This is different from market_data
    which only fetches prices for symbols with active positions.
    
    Flow:
    1. Check if after 17:00 CET (safety check - rate must be published)
    2. If before 17:00 CET: Skip sync (transactions will be processed next day)
    3. If after 17:00 CET: Fetch from ECB API
    4. If ECB API fails: Fallback to previous working day rate from database
    5. Ingest rates with source tracking for audit trail
    
    Rates:
    - EUR/USD: Official ECB rate (1 EUR = X USD)
    - EUR/THB: Official ECB rate (1 EUR = X THB)
    
    Tax Compliance:
    - German tax law (§ 20 EStG) requires ECB rates for foreign currency gains
    - ECB rates are the legally binding reference
    - Safety check ensures only published rates are stored (17:00 CET buffer)
    - Transactions before 17:00 CET are skipped and processed next day
    - Fallback rates are tracked in transaction notes for audit
    """
    logger.info("="*80)
    logger.info("Starting FX rate sync from ECB (German tax compliance)")
    logger.info("="*80)
    
    db = SessionLocal()
    try:
        # Fetch latest ECB rates with safety check (EUR/USD and EUR/THB)
        # Returns None if before 17:00 CET (rate not published yet)
        ecb_rates = fetch_ecb_rates()
        
        if ecb_rates is None:
            # Before 17:00 CET - skip sync (not an error, just too early)
            logger.info("⏰ Skipping FX sync: Before 17:00 CET (ECB rate not published yet)")
            logger.info("   Today's transactions will be processed in next sync (natural overlap)")
            logger.info("="*80)
            return
        
        if ecb_rates:
            # Use fresh ECB rates
            logger.info(f"✅ Successfully fetched {len(ecb_rates)} rates from ECB")
            success, failed = ingest_fx_rates(db, ecb_rates)
            logger.info(f"FX rate sync complete: {success} succeeded, {failed} failed")
        else:
            # ECB API failed after 17:00 CET - use database fallback
            logger.warning("⚠️  ECB API unavailable, using database fallback")
            
            fallback_rates = []
            required_pairs = ['EUR/USD', 'EUR/THB']
            
            today = now_utc()
            
            for pair in required_pairs:
                fallback_rate = get_fallback_rate_from_db(db, pair, today)
                if fallback_rate:
                    fallback_rates.append(fallback_rate)
                else:
                    logger.error(f"❌ No fallback rate available for {pair}")
            
            if fallback_rates:
                success, failed = ingest_fx_rates(db, fallback_rates)
                logger.info(
                    f"FX rate sync complete (fallback): {success} succeeded, {failed} failed"
                )
            else:
                logger.error("❌ No FX rates available from ECB or database fallback")
        
    except Exception as e:
        logger.error(f"Error syncing FX rates: {e}", exc_info=True)
    finally:
        db.close()
    
    logger.info("="*80)
