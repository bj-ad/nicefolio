from decimal import Decimal
from datetime import datetime, timezone
from collections import defaultdict
from ibflex import parser
import re

from utils.symbol_normalizer import SymbolNormalizer
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from utils.source_mapping_loader import get_source_mapping_loader
from utils.portfolios_loader import get_portfolios_loader
from utils.accounts_loader import get_accounts_loader

logger = get_logger(__name__)

# --- Helper Functions ---

def preprocess_ibkr_xml(xml_bytes: bytes) -> bytes:
    """
    Pre-process IBKR Flex XML to remove unsupported elements and attributes.
    
    The ibflex library only supports a subset of IBKR Flex API responses.
    This function removes elements/attributes that cause parsing errors.
    
    Args:
        xml_bytes: Raw XML from IBKR Flex API
        
    Returns:
        Cleaned XML bytes ready for ibflex parser
    """
    xml_str = xml_bytes.decode('utf-8')
    
    # Remove unsupported element sections (ibflex doesn't support these at all)
    unsupported_elements = ['FxTransactions', 'InterestAccruals', 'UnbundledCommissionDetails', 'CashReport']
    for element in unsupported_elements:
        pattern = rf'<{element}>.*?</{element}>'
        if re.search(pattern, xml_str, re.DOTALL):
            logger.debug(f"Removing unsupported XML section: {element}")
            xml_str = re.sub(pattern, '', xml_str, flags=re.DOTALL)
    
    # Remove unsupported attributes
    # IBKR returns many attributes that ibflex library doesn't recognize
    unsupported_attrs = [
        'subCategory', 'relatedTradeID', 
        'otherIncome', 'otherIncomeSec', 'otherCommissions', 'otherCommissionsSec',
        'changeInDividendAccruals', 'changeInDividendAccrualsSec',
        'softDollars', 'softDollarsSec', 'net', 'netSec'
    ]
    if any(attr in xml_str for attr in unsupported_attrs):
        logger.debug(f"Removing unsupported XML attributes from CashReport and other sections")
        for attr in unsupported_attrs:
            xml_str = re.sub(rf'\s+{attr}="[^"]*"', '', xml_str)
    
    return xml_str.encode('utf-8')

# --- Configuration Loading ---
# Load configs using centralized loaders
app_config = load_app_config()
symbol_normalizer = SymbolNormalizer()
source_mapping_loader = get_source_mapping_loader()
portfolios_loader = get_portfolios_loader()
accounts_loader = get_accounts_loader()

# --- Extract IBKR specific mappings and constants from the loaded configs ---
IBKR_ACCOUNT_ID = source_mapping_loader.get_account_id('IBKR')
IBKR_DEFAULT_PORTFOLIO_ID = source_mapping_loader.get_default_portfolio_id('IBKR')

# Create a simple lookup dictionary for symbol-to-portfolio mapping
IBKR_SYMBOL_MAPPINGS = source_mapping_loader.get_symbol_to_portfolio_map('IBKR')
IBKR_CASH_PORTFOLIO_ID = source_mapping_loader.get_cash_portfolio_id('IBKR')  # Broker Cash Pool

# Cash symbols that should be treated as cash positions (not securities)
# These should be currencies like USD, EUR, GBP, etc.
if 'ibkr_cash_symbols' not in app_config:
    raise KeyError(
        "ibkr_cash_symbols not configured in app_config.yaml. "
        "This is required to correctly parse IBKR positions. "
        "Please add 'ibkr_cash_symbols: [USD, EUR]' (or your currencies) to config/app_config.yaml"
    )
IBKR_CASH_SYMBOLS = app_config['ibkr_cash_symbols']
IBKR_ETF_SYMBOLS = app_config.get("ibkr_etf_symbols", [])
COMMODITY_ETF_TICKERS = app_config.get("commodity_etf_tickers", [])


def get_base_currency_for_portfolio(portfolio_id: int) -> str:
    """
    Get the global base currency from app_config.yaml.
    
    All portfolios now use the same base currency for consistency.
    Individual accounts can maintain their own native currencies.
    
    Args:
        portfolio_id: Portfolio ID (kept for backwards compatibility)
        
    Returns:
        str: Base currency code (e.g., "EUR", "USD") from app_config
    """
    return get_global_base_currency()


def parse_ibkr_datetime(datetime_str) -> datetime:
    """
    Parse IBKR dateTime field format: yyyyMMdd;HHmmss or yyyyMMdd
    
    Handles both string formats and datetime objects from ibflex parser.
    
    Examples:
        "20251006;155247" -> datetime(2025, 10, 6, 15, 52, 47)
        "20251013" -> datetime(2025, 10, 13, 0, 0, 0)
        datetime(2025, 10, 6, 15, 52, 47) -> datetime(2025, 10, 6, 15, 52, 47)
    
    Args:
        datetime_str: IBKR dateTime string or datetime object
        
    Returns:
        datetime: Parsed datetime object
    """
    if not datetime_str:
        logger.warning("Empty dateTime string, using default midnight")
        return datetime.min
    
    # If already a datetime object, return it
    if isinstance(datetime_str, datetime):
        # Ensure it's timezone-aware (UTC)
        if datetime_str.tzinfo is None:
            return datetime_str.replace(tzinfo=timezone.utc)
        return datetime_str
    
    try:
        if ';' in datetime_str:
            # Format: yyyyMMdd;HHmmss
            date_part, time_part = datetime_str.split(';')
            year = int(date_part[:4])
            month = int(date_part[4:6])
            day = int(date_part[6:8])
            hour = int(time_part[:2])
            minute = int(time_part[2:4])
            second = int(time_part[4:6])
            return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        else:
            # Format: yyyyMMdd (date only)
            year = int(datetime_str[:4])
            month = int(datetime_str[4:6])
            day = int(datetime_str[6:8])
            return datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse dateTime '{datetime_str}': {e}")
        return datetime.min


def parse_ibkr_flex_transactions(xml_bytes: bytes) -> list[dict]:
    """
    Parses raw IBKR Flex Query XML bytes into a list of standardized transaction data dictionaries.
    
    Enhancements:
    - Proper dateTime parsing using parse_ibkr_datetime()
    - Calculate amount_base with fxRateToBase
    - Net withholding taxes with dividends
    - Add company name and conid to notes field
    - Detect dividend reinvestment patterns
    - Set base_currency field
    - Pre-process XML to remove unsupported elements and attributes
    
    Note: Parser returns raw data. CRUD layer (crud_base.py) handles FX rate conversion
    for transactions where exchange_rate_to_base is None or 0.
    """
    if not xml_bytes:
        logger.warning("IBKR XML content is empty. Nothing to parse.")
        return []

    # Pre-process XML to remove unsupported elements/attributes
    logger.info("Pre-processing IBKR XML for transaction parsing")
    xml_bytes = preprocess_ibkr_xml(xml_bytes)
    
    # STEP 1: Build conid → ISIN mapping from SecurityInfo elements
    # German tax law (§ 20 EStG) requires FIFO per ISIN globally
    import xml.etree.ElementTree as ET
    security_mapping = {}
    try:
        root = ET.fromstring(xml_bytes)
        for sec_info in root.findall('.//SecurityInfo'):
            conid = sec_info.get('conid')
            isin = sec_info.get('isin')
            symbol = sec_info.get('symbol')
            if conid and isin:
                security_mapping[conid] = {
                    'isin': isin,
                    'symbol': symbol
                }
        logger.info(f"Built security mapping for {len(security_mapping)} securities with ISIN")
    except Exception as e:
        logger.warning(f"Failed to extract SecurityInfo/ISIN mapping: {e}")
        # Continue without ISIN - it will be NULL for these transactions
    
    parsed = parser.parse(xml_bytes)
    rows = []
    
    for stmt in parsed.FlexStatements:
        # Build commission map with company descriptions (if UnbundledCommissionDetails exist)
        commission_map = {}
        for comm in getattr(stmt, "UnbundledCommissionDetails", []):
            commission_map[comm.tradeID] = {
                "fee": abs(Decimal(comm.totalCommission)),  # IBKR reports negative, store as positive
                "fee_currency": comm.currency,
                "description": getattr(comm, "description", None),
                "conid": getattr(comm, "conid", None),
            }

        # --- Process Trades ---
        for trade in getattr(stmt, "Trades", []):
            symbol = trade.symbol.upper()
            qty = Decimal(trade.quantity)
            
            # FX trades are now tracked as exchange transactions in Portfolio 8
            # This provides complete cash flow visibility
            # FxPositions still used for balance reconciliation
            
            price = Decimal(trade.tradePrice)
            amount = qty * price
            
            # Extract commission from UnbundledCommissionDetails (if available) or directly from Trade element
            commission_data = commission_map.get(trade.tradeID, {})
            if not commission_data:
                # Fallback: Extract commission directly from Trade element (ibCommission attribute)
                ib_commission = getattr(trade, 'ibCommission', None)
                ib_commission_currency = getattr(trade, 'ibCommissionCurrency', None)
                if ib_commission is not None:
                    # Determine fee currency - use commission currency, then trade currency
                    # No fallback to hardcoded USD - if both are missing, it's a data quality issue
                    fee_currency = ib_commission_currency if ib_commission_currency else getattr(trade, 'currency', None)
                    if not fee_currency:
                        logger.error(f"IBKR trade {trade.tradeID} has commission but no currency. "
                                   f"Both ibCommissionCurrency and trade.currency are missing. "
                                   f"Skipping transaction.")
                        continue  # Skip - better to fail than guess wrong currency
                    commission_data = {
                        "fee": abs(Decimal(ib_commission)),  # IBKR reports negative, store as positive
                        "fee_currency": fee_currency,
                        "description": getattr(trade, 'description', None),
                        "conid": getattr(trade, 'conid', None),
                    }
            symbol_normalized = symbol_normalizer.normalize(symbol)
            
            # Get fxRateToBase and currency from IBKR Flex report
            # IBKR Flex reports should always include currency per transaction
            # If currency is missing, this indicates a data quality issue that should be investigated
            currency = getattr(trade, 'currency', None)
            
            # Validate currency is present (fail loudly if missing)
            if not currency:
                logger.error(f"IBKR trade {trade.tradeID} for {symbol} missing currency field. "
                           f"This is unexpected - IBKR Flex reports should include currency. "
                           f"Skipping transaction.")
                continue  # Skip this transaction - better to fail than guess wrong currency
            
            fx_rate_to_base = Decimal(trade.fxRateToBase) if hasattr(trade, 'fxRateToBase') and trade.fxRateToBase else None
            
            # Determine Portfolio ID using the new mapping logic (use normalized symbol for lookup)
            portfolio_id = IBKR_SYMBOL_MAPPINGS.get(symbol_normalized, IBKR_DEFAULT_PORTFOLIO_ID)
            base_currency = get_base_currency_for_portfolio(portfolio_id) if portfolio_id else get_global_base_currency()
            
            # If fx_rate_to_base not provided, calculate it
            if fx_rate_to_base is None:
                if currency == base_currency:
                    fx_rate_to_base = Decimal('1.0')
                else:
                    # This will be filled later by the CRUD layer using FX rates from database
                    fx_rate_to_base = Decimal('0.0')  # Marker for "needs FX lookup"
            
            # Calculate amount_base (will be recalculated in CRUD if fx_rate_to_base is 0)
            amount_base = amount * fx_rate_to_base if fx_rate_to_base != Decimal('0.0') else Decimal('0.0')

            # Parse dateTime properly
            date_time_str = getattr(trade, "dateTime", None)
            occurred_at = parse_ibkr_datetime(date_time_str) if date_time_str else datetime.combine(trade.tradeDate, datetime.min.time())

            # Build notes field with company name and conid
            notes = None
            company_description = commission_data.get("description")
            conid = getattr(trade, "conid", None) or commission_data.get("conid")
            if company_description and conid:
                notes = f"{company_description} - conid {conid}"
            elif company_description:
                notes = company_description
            elif conid:
                notes = f"conid {conid}"
            
            # Extract ISIN from security mapping (German tax compliance § 20 EStG)
            isin = None
            if conid and conid in security_mapping:
                isin = security_mapping[conid].get('isin')
                logger.debug(f"Enriched {symbol} (conid: {conid}) with ISIN: {isin}")

            # Handle FX trades - Create exchange transactions in Portfolio 8 (Broker Cash Pool)
            if symbol in IBKR_CASH_SYMBOLS:
                base_ccy, quote_ccy = symbol.split('.')
                broker_cash_pool_id = 8  # Portfolio 8 is the Broker Cash Pool
                base_currency = get_base_currency_for_portfolio(broker_cash_pool_id)
                
                # Extract commission details
                fee_amount = commission_data.get("fee", Decimal(0))
                fee_currency = commission_data.get("fee_currency", base_ccy)
                
                # Transaction 1: Base currency movement (e.g., EUR -400)
                # NOTE: For exchange transactions, price should be 1.0 (e.g., 1 EUR costs 1 EUR)
                # This is consistent with deposit/withdrawal pattern where price = 1.0
                # Exchange rate information is captured in exchange_rate_to_base field
                rows.append({
                    "portfolio_id": broker_cash_pool_id, 
                    "account_id": IBKR_ACCOUNT_ID, 
                    "external_id": f"IBKR_FX_{trade.tradeID}_{base_ccy}",
                    "symbol": base_ccy, 
                    "symbol_normalized": base_ccy, 
                    "quantity": qty,  # Positive = buy base currency, Negative = sell base currency
                    "price": Decimal(1),  # 1 EUR costs 1 EUR (consistent with cash transaction pattern)
                    "value_native": qty,  # Amount in base currency
                    "currency_native": base_ccy, 
                    "occurred_at": occurred_at,
                    "type": "exchange",  # Changed from buy/sell to exchange
                    "fee": Decimal(0),  # Fee will be separate transaction
                    "fee_currency": base_ccy, 
                    "exchange_rate_to_base": fx_rate_to_base,
                    "value_base": amount_base,
                    "currency_base": base_currency,
                    "asset_class": "cash", 
                    "category": "trade", 
                    "source": "IBKR",
                    "notes": f"FX exchange: {symbol} - {notes}" if notes else f"FX exchange: {symbol}",
                    "isin": None,  # Currencies have no ISIN
                    "conid": conid,
                })
                
                # Transaction 2: Quote currency movement (e.g., USD +468)
                # NOTE: For exchange transactions, price should be 1.0 (e.g., 1 USD costs 1 USD)
                # This is consistent with deposit/withdrawal pattern where price = 1.0
                # Exchange rate information is captured in exchange_rate_to_base field
                rows.append({
                    "portfolio_id": broker_cash_pool_id, 
                    "account_id": IBKR_ACCOUNT_ID, 
                    "external_id": f"IBKR_FX_{trade.tradeID}_{quote_ccy}",
                    "symbol": quote_ccy, 
                    "symbol_normalized": quote_ccy, 
                    "quantity": -amount,  # Opposite direction: sell base = buy quote
                    "price": Decimal(1),  # 1 USD costs 1 USD (consistent with cash transaction pattern)
                    "value_native": -amount, 
                    "currency_native": quote_ccy,
                    "occurred_at": occurred_at,
                    "type": "exchange",  # Changed from buy/sell to exchange
                    "fee": Decimal(0),  # Fee will be separate transaction
                    "fee_currency": quote_ccy,
                    "exchange_rate_to_base": fx_rate_to_base, 
                    "value_base": -amount * fx_rate_to_base,
                    "currency_base": base_currency,
                    "asset_class": "cash", 
                    "category": "trade", 
                    "source": "IBKR",
                    "notes": f"FX exchange: {symbol} - {notes}" if notes else f"FX exchange: {symbol}",
                    "isin": None,  # Currencies have no ISIN
                    "conid": conid,
                })
                
                # Transaction 3: Commission (if non-zero)
                # FX commissions are always in the quote currency (USD for EUR.USD)
                if fee_amount > 0:
                    # Parser returns raw fee data - CRUD layer handles FX conversion
                    # Set exchange_rate_to_base to None if currencies differ
                    # This triggers automatic FX lookup in crud_base.py
                    fee_fx_rate = Decimal(1) if fee_currency == base_currency else None
                    fee_amount_base = -fee_amount if fee_currency == base_currency else None  # FIX: Must be negative (money out)
                    
                    rows.append({
                        "portfolio_id": broker_cash_pool_id,
                        "account_id": IBKR_ACCOUNT_ID,
                        "external_id": f"IBKR_FX_FEE_{trade.tradeID}_{fee_currency}",
                        "symbol": fee_currency,
                        "symbol_normalized": fee_currency,
                        "quantity": -fee_amount,  # Negative = money out
                        "price": Decimal(1),  # 1 EUR costs 1 EUR (consistent with exchange/deposit pattern)
                        "value_native": -fee_amount,
                        "currency_native": fee_currency,
                        "occurred_at": occurred_at,
                        "type": "fee",
                        "fee": Decimal(0),
                        "fee_currency": fee_currency,
                        "exchange_rate_to_base": fee_fx_rate,  # None triggers FX lookup in CRUD layer
                        "value_base": fee_amount_base,  # None will be calculated in CRUD layer (must be negative)
                        "currency_base": base_currency,
                        "asset_class": "cash",
                        "category": "trade",
                        "source": "IBKR",
                        "notes": f"FX commission for trade {trade.tradeID}",
                        "isin": None,
                        "conid": conid,
                    })
            
            # Handle regular security trades
            else:
                # Determine asset class:
                # - Commodity ETFs (GLD, 4GLD, SLV, etc.) → "etc" (Exchange Traded Commodities)
                # - Regular ETFs (VOO, VGK) → "etf"
                # - Everything else → "stock"
                # Use normalized symbol for consistent matching
                if symbol_normalized in COMMODITY_ETF_TICKERS:
                    asset_class = "etc"
                elif symbol_normalized in IBKR_ETF_SYMBOLS:
                    asset_class = "etf"
                else:
                    asset_class = "stock"
                
                # Stock/ETF purchase transaction in investment portfolio
                rows.append({
                    "portfolio_id": portfolio_id,
                    "account_id": IBKR_ACCOUNT_ID,
                    "external_id": trade.tradeID, "symbol": symbol_normalized, "symbol_normalized": symbol_normalized,
                    "quantity": qty, "price": price, "value_native": amount, "currency_native": currency,
                    "occurred_at": occurred_at,
                    "type": "buy" if qty > 0 else "sell", "fee": commission_data.get("fee", Decimal(0)),
                    "fee_currency": commission_data.get("fee_currency", currency), 
                    "exchange_rate_to_base": fx_rate_to_base,
                    "value_base": amount_base,
                    "currency_base": base_currency,
                    "asset_class": asset_class, "category": "trade", "source": "IBKR",
                    "notes": notes,
                    "isin": isin,  # ISIN for German tax compliance (§ 20 EStG)
                    "conid": conid,  # IBKR contract ID
                })
                
                # Create corresponding cash flow in Portfolio 8 (Broker Cash Pool)
                # This shows complete cash flow: 
                # - BUY: EUR deposit → USD exchange → Stock purchase (cash outflow)
                # - SELL: Stock sale → USD cash inflow
                broker_cash_pool_id = 8
                broker_base_currency = get_base_currency_for_portfolio(broker_cash_pool_id)
                
                # Calculate cash flow amount (include fees)
                total_cash_flow = amount + commission_data.get("fee", Decimal(0))
                
                if qty > 0:  # BUY: Cash outflow from Portfolio 8
                    rows.append({
                        "portfolio_id": broker_cash_pool_id,
                        "account_id": IBKR_ACCOUNT_ID,
                        "external_id": f"IBKR_CASH_OUT_{trade.tradeID}",
                        "symbol": currency,  # Currency of the purchase (e.g., USD)
                        "symbol_normalized": currency,
                        "quantity": -total_cash_flow,  # Negative = cash outflow
                        "price": Decimal('1.0'),  # Cash valued at 1:1
                        "value_native": -total_cash_flow,
                        "currency_native": currency,
                        "occurred_at": occurred_at,
                        "type": "transfer_out",
                        "fee": Decimal(0),  # Fee already included in amount
                        "fee_currency": currency,
                        "exchange_rate_to_base": fx_rate_to_base,
                        "value_base": -total_cash_flow * fx_rate_to_base if fx_rate_to_base and fx_rate_to_base != Decimal('0.0') else Decimal('0.0'),
                        "currency_base": broker_base_currency,
                        "asset_class": "cash",
                        "category": "internal_transfer",
                        "source": "IBKR",
                        "notes": f"Cash outflow for stock purchase: {symbol_normalized} (qty: {qty}) in Portfolio {portfolio_id}",
                        "isin": None,  # Cash transactions have no ISIN
                        "conid": None,
                    })
                else:  # SELL: Cash inflow to Portfolio 8
                    # For sells, amount is negative (cost), we want proceeds (positive)
                    sale_proceeds = abs(total_cash_flow)
                    rows.append({
                        "portfolio_id": broker_cash_pool_id,
                        "account_id": IBKR_ACCOUNT_ID,
                        "external_id": f"IBKR_CASH_IN_{trade.tradeID}",
                        "symbol": currency,  # Currency of the sale (e.g., USD)
                        "symbol_normalized": currency,
                        "quantity": sale_proceeds,  # Positive = cash inflow
                        "price": Decimal('1.0'),  # Cash valued at 1:1
                        "value_native": sale_proceeds,
                        "currency_native": currency,
                        "occurred_at": occurred_at,
                        "type": "transfer_in",
                        "fee": Decimal(0),  # Fee already included in amount
                        "fee_currency": currency,
                        "exchange_rate_to_base": fx_rate_to_base,
                        "value_base": sale_proceeds * fx_rate_to_base if fx_rate_to_base and fx_rate_to_base != Decimal('0.0') else Decimal('0.0'),
                        "currency_base": broker_base_currency,
                        "asset_class": "cash",
                        "category": "internal_transfer",
                        "source": "IBKR",
                        "notes": f"Cash inflow from stock sale: {symbol_normalized} (qty: {qty}) in Portfolio {portfolio_id}",
                        "isin": None,  # Cash transactions have no ISIN
                        "conid": None,
                    })

        # --- Process CashTransactions with dividend/withholding tax netting ---
        # First, collect all cash transactions
        cash_txs = []
        for ct in getattr(stmt, "CashTransactions", []):
            amount = Decimal(ct.amount)
            transaction_type_str = str(ct.type).lower() if ct.type else ""
            symbol = getattr(ct, "symbol", None)
            conid = getattr(ct, "conid", None)
            
            # For deposits/withdrawals without symbol, use currency as symbol
            # (e.g., EUR deposit → symbol=EUR)
            if not symbol and ("deposit" in transaction_type_str or "withdrawal" in transaction_type_str):
                symbol = ct.currency
            
            # Categorize transaction type first
            if "deposit" in transaction_type_str or "withdrawal" in transaction_type_str:
                tx_type, category = ("deposit", "external_transfer") if amount > 0 else ("withdrawal", "external_transfer")
            elif "dividend" in transaction_type_str:
                tx_type, category = "dividend", "income"
            elif "withholding" in transaction_type_str or "whtax" in transaction_type_str:
                # Handles both "Withholding Tax" and "cashaction.whtax" formats
                tx_type, category = "withholding_tax", "tax"
            elif "fee" in transaction_type_str:
                tx_type, category = "fee", "fee"
            else:
                tx_type, category = "interest", "income"
            
            # Determine portfolio based on transaction type and symbol
            # CASH TRANSACTIONS (deposits, withdrawals, dividends, interest, fees) → Broker Cash Pool
            # Exception: If symbol maps to commodity/specific portfolio, use that mapping
            portfolio_id = IBKR_CASH_PORTFOLIO_ID
            
            if symbol:
                # Normalize symbol first for consistent lookup
                symbol_temp_normalized = symbol_normalizer.normalize(symbol.upper())
                # Check if normalized symbol maps to specific portfolio
                if symbol_temp_normalized in IBKR_SYMBOL_MAPPINGS:
                    portfolio_id = IBKR_SYMBOL_MAPPINGS[symbol_temp_normalized]
                # Otherwise keep default cash portfolio for cash transactions
            
            # For cash transactions without a symbol (interest, fees), use currency as symbol
            # IMPORTANT: Dividends and withholding tax MUST keep their stock symbol for DRIP detection
            if portfolio_id == IBKR_CASH_PORTFOLIO_ID and tx_type in ['interest', 'fee'] and not symbol:
                symbol = ct.currency  # e.g., USD interest → symbol=USD
            # If still no symbol, use currency as fallback
            if not symbol:
                symbol = ct.currency

            # Parse dateTime
            date_time_str = getattr(ct, "dateTime", None)
            occurred_at = parse_ibkr_datetime(date_time_str) if date_time_str else datetime.min

            # Get base currency for this portfolio
            base_currency = get_base_currency_for_portfolio(portfolio_id) if portfolio_id else get_global_base_currency()
            
            # Calculate FX rate to portfolio base currency
            # IBKR's fxRateToBase is always relative to USD (IBKR's base currency)
            # For EUR-based portfolios, we need to handle this differently:
            # - If transaction currency == portfolio base currency: FX rate = 1.0 (no conversion)
            # - If transaction currency != portfolio base currency: Use market FX rate (will be fetched by CRUD layer)
            if ct.currency == base_currency:
                # Same currency as portfolio base - no conversion needed
                fx_rate_to_base = Decimal('1.0')
                amount_base = amount
            else:
                # Different currency - mark for FX conversion by CRUD layer
                # IBKR's fxRateToBase is USD-centric, not useful for EUR-based portfolios
                fx_rate_to_base = None  # Will be fetched from market FX rates by CRUD layer
                amount_base = None  # Will be calculated by CRUD layer

            symbol_normalized = symbol_normalizer.normalize(symbol.upper()) if symbol else None
            asset_class = "cash"
            if symbol:
                # Determine asset class:
                # - Commodity ETFs (GLD, 4GLD, SLV, etc.) → "etc" (Exchange Traded Commodities)
                # - Regular ETFs (VOO, VGK) → "etf"
                # - Everything else → "stock"
                # Use normalized symbol for consistent matching
                if symbol_normalized in COMMODITY_ETF_TICKERS:
                    asset_class = "etc"
                elif symbol_normalized in IBKR_ETF_SYMBOLS:
                    asset_class = "etf"
                else:
                    asset_class = "stock"
            
            # Build notes with description and conid
            description = getattr(ct, "description", None)
            notes = None
            if description and conid:
                notes = f"{description} - conid {conid}"
            elif description:
                notes = description
            elif conid:
                notes = f"conid {conid}"
            
            # For cash transactions (deposits, withdrawals), qty represents the currency amount
            # Price is 1.0 since 1 EUR = 1 EUR (qty equals amount for cash)
            
            # Extract ISIN for this transaction (if available)
            isin = None
            if conid and conid in security_mapping:
                isin = security_mapping[conid].get('isin')
                logger.debug(f"Enriched CorporateAction {symbol} (conid: {conid}) with ISIN: {isin}")
            
            cash_txs.append({
                "portfolio_id": portfolio_id,
                "account_id": IBKR_ACCOUNT_ID,
                "external_id": ct.transactionID,
                "symbol": symbol_normalized,
                "symbol_normalized": symbol_normalized,
                "quantity": amount, "price": Decimal("1.0"), "value_native": amount, "currency_native": ct.currency,
                "occurred_at": occurred_at,
                "type": tx_type, "fee": Decimal(0), "fee_currency": ct.currency,
                "exchange_rate_to_base": fx_rate_to_base,
                "value_base": amount_base,
                "currency_base": base_currency,
                "asset_class": asset_class, "category": category, "source": "IBKR", "notes": notes,
                "isin": isin,  # ISIN for German tax compliance (§ 20 EStG)
                "conid": conid,  # IBKR contract ID (temporary field for matching)
            })
        
        # Net dividends and withholding taxes
        # Group by (symbol, date) to find matching dividend+withholding pairs
        dividend_map = defaultdict(list)
        withholding_map = defaultdict(list)
        
        for tx in cash_txs:
            if tx["type"] == "dividend" and tx["symbol"]:
                key = (tx["symbol"], tx["occurred_at"].date())
                dividend_map[key].append(tx)
            elif tx["type"] == "withholding_tax" and tx["symbol"]:
                key = (tx["symbol"], tx["occurred_at"].date())
                withholding_map[key].append(tx)
        
        # Match dividends with withholding taxes
        netted_ids = set()
        for key in dividend_map:
            if key in withholding_map:
                # Net the amounts
                dividends = dividend_map[key]
                withholdings = withholding_map[key]
                
                # Sum all withholding taxes for this symbol/date
                total_withholding = sum(wh["value_native"] for wh in withholdings)
                # Only sum value_base if it exists (will be None for USD transactions in EUR portfolio)
                total_withholding_base = sum(wh["value_base"] for wh in withholdings if wh["value_base"] is not None)
                
                # Update the first dividend transaction with netted amount
                if dividends:
                    div_tx = dividends[0]
                    
                    # Store withholding tax info for German tax compliance
                    # (Withholding value is negative, so use absolute value)
                    div_tx["withholding_tax"] = abs(total_withholding)
                    div_tx["withholding_tax_currency"] = div_tx["currency_native"]
                    
                    # Extract withholding tax country from IBKR withholding transaction description
                    # IBKR format: "SYMBOL(ISIN) CASH DIVIDEND ... - CC TAX" where CC is 2-letter country code
                    # Examples: "- US TAX", "- TW TAX", "- IE TAX"
                    tax_country = None
                    if withholdings and withholdings[0].get("notes"):
                        # Extract country code from first withholding transaction using regex: "- ([A-Z]{2}) TAX"
                        match = re.search(r'- ([A-Z]{2}) TAX', withholdings[0]["notes"])
                        if match:
                            tax_country = match.group(1)
                        else:
                            logger.warning(f"Could not extract withholding tax country from description: {withholdings[0]['notes']}")
                    div_tx["withholding_tax_country"] = tax_country
                    
                    # Net the values
                    div_tx["value_native"] += total_withholding  # Withholding is negative
                    # Only net value_base if both dividend and withholding have it calculated
                    if div_tx["value_base"] is not None and total_withholding_base:
                        div_tx["value_base"] += total_withholding_base
                    # Otherwise, value_base will be calculated by CRUD layer using market FX rates
                    div_tx["quantity"] = div_tx["value_native"]  # For cash, quantity = net value
                    
                    # Update notes to indicate net dividend
                    if div_tx["notes"]:
                        div_tx["notes"] = f"Net dividend: {div_tx['notes']}"
                    else:
                        div_tx["notes"] = "Net dividend after withholding tax"
                    
                    # Mark withholding transactions as netted
                    for wh in withholdings:
                        netted_ids.add(wh["external_id"])
                    
                    # Log with country code (or NULL if not found)
                    country_str = tax_country if tax_country else "NULL"
                    logger.info(f"Netted dividend for {key[0]} on {key[1]}: gross={dividends[0]['value_native'] - total_withholding}, withholding={abs(total_withholding)} {country_str}, net={div_tx['value_native']}")
                    
                    # NOTE: USD dividend transactions are NOT created separately here (Dec 2025 fix)
                    # The dividend on the stock symbol (e.g., VGK dividend) is already recorded.
                    # In cash portfolios, update_position_from_transaction() redirects stock symbol
                    # dividends to update the currency position (e.g., VGK dividend → USD position).
                    # Creating a separate USD dividend would cause DOUBLE-COUNTING of dividend income.
        
        # Add cash transactions to rows, excluding netted withholding taxes
        for tx in cash_txs:
            if tx["external_id"] not in netted_ids:
                # Remove temporary conid field
                tx.pop("conid", None)
                rows.append(tx)
        
        logger.info(f"Netted {len(netted_ids)} withholding tax transactions")

    # Detect dividend reinvestment patterns
    rows = detect_dividend_reinvestment(rows)
    
    logger.info(f"Parsed {len(rows)} transactions from IBKR Flex XML")
    return rows


def detect_dividend_reinvestment(transactions: list[dict]) -> list[dict]:
    """
    Detect dividend reinvestment patterns and mark them appropriately.
    
    DRIP Detection Criteria:
    1. Buy occurs 1-7 days after a dividend of same symbol
    2. Buy amount is 65-100% of dividend amount (accounts for withholding tax up to 35%)
    3. Commission rate is reasonable (< 1% of buy amount)
    
    IBKR Commission Structure:
    - Regular trades: max($0.35 minimum, per-share rate × shares)
    - DRIP trades: NO minimum, just per-share rate × shares (typically 0.1-0.2%)
    
    IBKR DRIP Pattern:
    - Dividend received with withholding tax deduction
    - 1-4 days later: fractional buy with no commission minimum
    - Buy amount ≈ 65-99% of gross dividend (depending on tax rate: 0-35%)
    - Commission = pure per-share rate (no $0.35 minimum floor)
    
    Examples from flex report:
    - TSM: $5.56 div → $4.49 buy (80.8%, $0.0045 = 0.10%, 4 days) ✓
    - VGK: $3.26 div → $2.27 buy (69.8%, $0.0023 = 0.10%, 1 day) ✓
    - VOO: $3.46 div → $2.40 buy (69.4%, $0.0024 = 0.10%, 1 day) ✓
    - Large DRIP example: $500 div → $450 buy (90%, $0.45 = 0.10%) ✓
    - Regular: $300 buy ($0.35 = 0.12% but has minimum enforced) ✗
    
    The key is percentage matching + time window. Commission can be any amount
    for DRIP (scales with dividend size) since there's no minimum floor.
    
    Args:
        transactions: List of parsed transactions
        
    Returns:
        List of transactions with dividend reinvestments marked
    """
    from decimal import Decimal
    
    # Separate dividends and buys by symbol
    dividends = []
    buys = []
    
    for idx, tx in enumerate(transactions):
        if tx["type"] == "dividend" and tx["symbol"]:
            dividends.append((idx, tx))
        elif tx["type"] == "buy" and tx["symbol"] and tx.get("category") == "trade":
            buys.append((idx, tx))
    
    # Find matching dividend + buy pairs using percentage-based matching
    reinvestment_count = 0
    matched_buy_indices = set()  # Track matched buys to avoid double-matching
    
    for div_idx, div_tx in dividends:
        div_symbol = div_tx["symbol"]
        div_date = div_tx["occurred_at"].date()
        # Use value_native for dividend amount (what we actually received in USD)
        div_amount = float(div_tx.get("value_native", 0))
        
        if div_amount <= 0:
            continue
        
        # Look for buys of same symbol within 7 days AFTER dividend
        for buy_idx, buy_tx in buys:
            if buy_idx in matched_buy_indices:
                continue  # Already matched to another dividend
            
            if buy_tx["symbol"] != div_symbol:
                continue
            
            buy_date = buy_tx["occurred_at"].date()
            days_diff = (buy_date - div_date).days
            
            # Check if buy is within 1-7 days after dividend
            if not (1 <= days_diff <= 7):
                continue
            
            # Use value_native for buy amount (total USD spent)
            buy_amount = float(buy_tx.get("value_native", 0))
            buy_fee = abs(float(buy_tx.get("fee", 0)))
            
            if buy_amount <= 0:
                continue
            
            # Calculate percentage: buy should be 70-100% of dividend
            # (accounts for withholding tax, which varies by country/security)
            reinvestment_pct = (buy_amount / div_amount) * 100
            
            # Calculate commission as percentage of buy amount
            # DRIP trades: ~0.1-0.2% (pure per-share rate, no $0.35 minimum)
            # Regular trades: Usually $0.35 minimum dominates for smaller trades
            commission_pct = (buy_fee / buy_amount * 100) if buy_amount > 0 else 0
            
            # DRIP indicators:
            # 1. Buy amount is 65-100% of dividend (after tax deduction)
            #    Note: Some withholding rates can be 30%+, so 65% threshold catches those
            # 2. Buy occurs 1-7 days after dividend (already checked above)
            # 3. Commission rate is reasonable (< 1% to allow for any dividend size)
            #
            # We can't use absolute commission threshold since DRIP scales:
            # - Small dividend ($5): DRIP fee ~$0.005 (0.1%)
            # - Large dividend ($500): DRIP fee ~$0.50 (0.1%)
            # Both are DRIP, different absolute fees, same percentage
            is_percentage_match = 65 <= reinvestment_pct <= 100
            is_reasonable_commission = commission_pct < 1.0  # Allow up to 1% commission rate
            
            if is_percentage_match and is_reasonable_commission:
                # This is a dividend reinvestment
                buy_tx["type"] = "dividend_reinvest"  # Mark as DRIP transaction type
                buy_tx["category"] = "income"  # Changed from 'trade' to 'income' to mark as DRIP
                if buy_tx.get("notes"):
                    buy_tx["notes"] = f"[DRIP from ${div_amount:.2f} div on {div_date}] {buy_tx['notes']}"
                else:
                    buy_tx["notes"] = f"[DRIP from ${div_amount:.2f} div on {div_date}]"
                
                # NOTE: We do NOT create a separate USD outflow here because:
                # The buy transaction already generates a transfer_out for the USD spent.
                # Creating an additional outflow would double-count the USD flow.
                # The DRIP detection only marks the stock buy as dividend_reinvest type.
                
                matched_buy_indices.add(buy_idx)
                reinvestment_count += 1
                logger.info(
                    f"Detected DRIP: {div_symbol} ${div_amount:.2f} div on {div_date} → "
                    f"${buy_amount:.2f} buy on {buy_date} ({reinvestment_pct:.1f}%, comm={commission_pct:.2f}%)"
                )
    
    if reinvestment_count > 0:
        logger.info(f"Detected {reinvestment_count} dividend reinvestment transactions")
    
    return transactions


# NOTE: sync_ibkr_cash_positions() function was REMOVED (Dec 2025)
# 
# Cash balances are now correctly tracked via:
# - transfer_out: Cash outflow when buying securities (Portfolio 8)
# - transfer_in: Cash inflow when selling securities (Portfolio 8)
# - deposit/withdrawal: External cash movements
# - exchange: Currency conversions
#
# The FxPosition data from IBKR is NOT used because the transfer_in/transfer_out
# transactions already accurately track all cash flows. Using FxPosition caused
# double-counting of cash balances.


# =============================================================================
# POSITION PARSING FUNCTIONS (for Audit Service)
# =============================================================================
# These functions parse positions from IBKR Flex Report for comparison against
# calculated positions from lots/transactions. Used for TAX COMPLIANCE auditing.

def parse_ibkr_open_positions(xml_bytes: bytes) -> list[dict] | None:
    """
    Parse OpenPositions from IBKR Flex Report XML.
    
    Used by audit_service.py to compare broker-reported positions against 
    calculated positions from transactions/lots.
    
    Returns list of position dicts with:
    - symbol: str (e.g., "VOO", "AAPL")
    - quantity: Decimal (position size)
    - currency: str (e.g., "USD", "EUR") 
    - cost_basis: Decimal (costBasisMoney from IBKR)
    - market_value: Decimal (positionValue from IBKR)
    - unrealized_pnl: Decimal (fifoPnlUnrealized from IBKR)
    - asset_class: str (e.g., "STK")
    - description: str (security description)
    - conid: str (IBKR contract ID)
    
    Args:
        xml_bytes: Raw XML from IBKR Flex Report
        
    Returns:
        List of position dicts or None on failure
    """
    import xml.etree.ElementTree as ET_pos
    
    try:
        root = ET_pos.fromstring(xml_bytes)
        positions = []
        
        # Find OpenPositions section
        open_positions = root.findall('.//OpenPosition')
        
        if not open_positions:
            logger.warning("No OpenPosition elements found in IBKR Flex Report")
            return []
        
        logger.info(f"Found {len(open_positions)} open positions in IBKR Flex Report")
        
        for pos in open_positions:
            try:
                symbol = pos.get('symbol', '').strip()
                if not symbol:
                    logger.warning("Skipping position with empty symbol")
                    continue
                
                # Clean symbol (remove trailing 'd' that IBKR adds for fractional shares)
                if symbol.endswith('d'):
                    symbol = symbol[:-1]
                
                quantity_str = pos.get('position', '0')
                cost_basis_str = pos.get('costBasisMoney', '0')
                market_value_str = pos.get('positionValue', '0')
                unrealized_pnl_str = pos.get('fifoPnlUnrealized', '0')
                
                position_data = {
                    'symbol': symbol,
                    'quantity': Decimal(quantity_str),
                    'currency': pos.get('currency', 'USD'),
                    'cost_basis': Decimal(cost_basis_str),
                    'market_value': Decimal(market_value_str),
                    'unrealized_pnl': Decimal(unrealized_pnl_str),
                    'asset_class': pos.get('assetCategory', 'STK'),
                    'description': pos.get('description', ''),
                    'conid': pos.get('conid', ''),
                }
                
                positions.append(position_data)
                logger.debug(f"Parsed position: {symbol} = {position_data['quantity']}")
                
            except (ValueError, TypeError) as e:
                symbol = pos.get('symbol', 'unknown')
                logger.warning(f"Failed to parse position for {symbol}: {e}")
                continue
        
        logger.info(f"Successfully parsed {len(positions)} IBKR positions")
        return positions
        
    except ET_pos.ParseError as e:
        logger.error(f"Failed to parse IBKR Flex Report XML: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error parsing IBKR positions: {e}", exc_info=True)
        return None


def parse_ibkr_fx_positions(xml_bytes: bytes) -> list[dict] | None:
    """
    Parse FxPositions (cash balances) from IBKR Flex Report XML.
    
    Returns list of cash position dicts with:
    - symbol: str (currency code, e.g., "EUR", "USD")
    - quantity: Decimal (cash balance)
    - currency: str (same as symbol for FX positions)
    - market_value: Decimal (value in functional currency)
    - asset_class: str ("CASH")
    
    Args:
        xml_bytes: Raw XML from IBKR Flex Report
        
    Returns:
        List of FX position dicts or None on failure
    """
    import xml.etree.ElementTree as ET_pos
    
    try:
        root = ET_pos.fromstring(xml_bytes)
        fx_positions = []
        
        # Find FxPositions section
        fx_elements = root.findall('.//FxPosition')
        
        if not fx_elements:
            logger.info("No FxPosition elements found in IBKR Flex Report")
            return []
        
        logger.info(f"Found {len(fx_elements)} FX positions in IBKR Flex Report")
        
        for fx in fx_elements:
            try:
                currency = fx.get('fxCurrency', '').strip()
                if not currency:
                    logger.warning("Skipping FX position with empty currency")
                    continue
                
                quantity_str = fx.get('quantity', '0')
                value_str = fx.get('value', '0')
                
                fx_data = {
                    'symbol': currency,
                    'quantity': Decimal(quantity_str),
                    'currency': currency,
                    'market_value': Decimal(value_str),
                    'asset_class': 'CASH',
                    'description': f'{currency} Cash Balance',
                }
                
                fx_positions.append(fx_data)
                logger.debug(f"Parsed FX position: {currency} = {fx_data['quantity']}")
                
            except (ValueError, TypeError) as e:
                currency = fx.get('fxCurrency', 'unknown')
                logger.warning(f"Failed to parse FX position for {currency}: {e}")
                continue
        
        logger.info(f"Successfully parsed {len(fx_positions)} IBKR FX positions")
        return fx_positions
        
    except ET_pos.ParseError as e:
        logger.error(f"Failed to parse IBKR Flex Report XML: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error parsing IBKR FX positions: {e}", exc_info=True)
        return None


def parse_all_ibkr_positions(xml_bytes: bytes) -> dict | None:
    """
    Parse all positions (securities + FX) from IBKR Flex Report.
    
    Convenience function that combines OpenPositions and FxPositions.
    Used by audit_service.py for position verification.
    
    Returns:
        Dict with:
        - securities: List of security positions
        - cash: List of FX/cash positions  
        - report_date: str (from FlexStatement)
        - account_id: str (IBKR account ID)
    """
    import xml.etree.ElementTree as ET_pos
    
    try:
        root = ET_pos.fromstring(xml_bytes)
        
        # Get report metadata
        flex_stmt = root.find('.//FlexStatement')
        report_date = flex_stmt.get('toDate', '') if flex_stmt is not None else ''
        account_id = flex_stmt.get('accountId', '') if flex_stmt is not None else ''
        
        # Parse both position types
        securities = parse_ibkr_open_positions(xml_bytes) or []
        cash = parse_ibkr_fx_positions(xml_bytes) or []
        
        result = {
            'securities': securities,
            'cash': cash,
            'report_date': report_date,
            'account_id': account_id,
            'total_securities': len(securities),
            'total_cash': len(cash),
        }
        
        logger.info(
            f"Parsed IBKR positions for account {account_id}: "
            f"{len(securities)} securities, {len(cash)} cash positions"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to parse all IBKR positions: {e}", exc_info=True)
        return None
