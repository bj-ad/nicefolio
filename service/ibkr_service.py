import os
import requests
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Dict
from database import SessionLocal
from crud.crud_ibkr import ingest_transactions_from_ibkr
from crud.parsers.ibkr_parser import parse_ibkr_flex_transactions
from dotenv import load_dotenv
from utils.logging_config import get_logger

logger = get_logger("ibkr_service")

# Load environment variables
load_dotenv()
FLEX_TOKEN = os.getenv("IBKR_FLEX_TOKEN")
FLEX_QUERY_ID = os.getenv("IBKR_FLEX_QUERY_ID")


def fetch_flex_reference_code(token: str, query_id: str) -> str:
    """
    Step 1: Request report generation and get ReferenceCode.
    
    Note: Date range is configured in IBKR Flex Query settings, not via API parameters.
    Custom date parameters cause error 1003 "Statement is not available".
    """
    base_url = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
    params = {"t": token, "q": query_id, "v": 3}
    # Removed date parameters - use flex query's configured date range instead

    response = requests.get(base_url, params=params)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    status = root.findtext("Status")
    if status != "Success":
        error_msg = root.findtext("ErrorMessage")
        logger.error(f"Flex SendRequest failed: {error_msg} (Code: {root.findtext('ErrorCode')})")
        raise Exception(f"Flex SendRequest failed: {error_msg}")
    
    ref_code = root.findtext("ReferenceCode")
    logger.info(f"Generated Flex reference code: {ref_code}")
    return ref_code


def fetch_flex_statement(token: str, reference_code: str) -> bytes:
    """Step 2: Retrieve the generated report using ReferenceCode."""
    base_url = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
    params = {"t": token, "q": reference_code, "v": 3}
    logger.info("Waiting 20 seconds for the report to generate...")
    time.sleep(20)
    response = requests.get(base_url, params=params)
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
        if root.findtext("Status") == "Fail":
            error_msg = root.findtext("ErrorMessage")
            logger.error(f"Flex GetStatement failed: {error_msg} (Code: {root.findtext('ErrorCode')})")
            raise Exception(f"Flex GetStatement failed: {error_msg}")
    except ET.ParseError:
        pass # This is expected for a valid report
    logger.info("Successfully fetched Flex statement.")
    return response.content


def run_ibkr_ingestion_flow():
    """
    Orchestrates the full workflow of fetching IBKR data and ingesting it.
    
    Service layer orchestration:
    1. Fetch IBKR Flex Query data
    2. Parse transactions and FX positions
    3. Calculate cash balance reconciliation adjustments
    4. Pass everything to CRUD layer for database operations
    
    Note: Date range is configured in the IBKR Flex Query settings (Query ID: 1295520).
    The flex query determines what data is returned, not API parameters.
    
    Returns:
        dict: Transaction statistics {'new': int, 'types': dict, 'failed': int}
    """
    logger.info("Starting IBKR ingestion flow using flex query's configured date range")
    db = SessionLocal()
    try:
        # Step 1: Fetch data from IBKR API
        ref_code = fetch_flex_reference_code(FLEX_TOKEN, FLEX_QUERY_ID)
        xml_content = fetch_flex_statement(FLEX_TOKEN, ref_code)
        
        # Step 2: Parse transactions (Parser layer - pure transformation)
        # Cash flows are tracked via transfer_in/transfer_out transactions
        # which are generated automatically when processing stock trades
        transactions_data = parse_ibkr_flex_transactions(xml_content)
        
        # Step 3: Pass to CRUD layer for database operations
        from crud.crud_ibkr import ingest_transactions_list
        success_count, skipped_count, failure_count = ingest_transactions_list(db, transactions_data)
        
        logger.info("IBKR ingestion flow completed successfully.")
        
        # Return simple stats (not used for notifications anymore)
        return {
            'new': success_count,
            'failed': failure_count
        }
        
    except Exception as e:
        logger.error(f"An error occurred during the IBKR ingestion flow: {e}", exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    # Run with flex query's configured date range
    run_ibkr_ingestion_flow()
