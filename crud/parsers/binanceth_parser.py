from datetime import datetime
from decimal import Decimal
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from utils.source_mapping_loader import get_source_mapping_loader

logger = get_logger(__name__)

# --- Configuration Loading ---
app_config = load_app_config()
source_mapping_loader = get_source_mapping_loader()

BINANCETH_ACCOUNT_ID = source_mapping_loader.get_account_id('BinanceTH')
BINANCETH_DEFAULT_PORTFOLIO_ID = source_mapping_loader.get_default_portfolio_id('BinanceTH')
BINANCETH_CASH_PORTFOLIO_ID = source_mapping_loader.get_cash_portfolio_id('BinanceTH')

CRYPTO_SYMBOLS = set(app_config.get("hodl_symbols", []))
STABLECOIN_SYMBOLS = set(app_config.get("stablecoin_symbols", []))

def parse_binanceth_trades(trades_data: list[dict]) -> list[dict]:
    """
    Parses a list of trade data from the Binance.th API /api/v1/userTrades endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    Handles both crypto-to-fiat and crypto-to-crypto trades.
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(trades_data)} trades from Binance.th data.")

    for trade in trades_data:
        try:
            symbol = trade['symbol']
            quote_currency = None

            # Identify quote currency
            if symbol.endswith("THB"):
                quote_currency = "THB"
                base_currency = symbol[:-3]
            else:
                for crypto in CRYPTO_SYMBOLS:
                    if symbol.endswith(crypto) and crypto != symbol:
                        base_currency = symbol[:-len(crypto)]
                        if base_currency in CRYPTO_SYMBOLS:
                            quote_currency = crypto
                            break
                if not quote_currency:
                    for crypto in CRYPTO_SYMBOLS:
                        if symbol.startswith(crypto) and len(symbol) > len(crypto):
                            base_currency = crypto
                            quote_currency = symbol[len(crypto):]
                            if quote_currency in CRYPTO_SYMBOLS:
                                break
                    else: # if no break
                        logger.warning(f"Could not determine base/quote for {symbol}, skipping.")
                        continue

            is_buyer = trade['isBuyer']
            qty = Decimal(trade['qty'])
            price = Decimal(trade['price'])
            quote_qty = Decimal(trade['quoteQty'])
            
            # Check if either side is a stablecoin (German tax compliance)
            is_stablecoin_pair = (base_currency in STABLECOIN_SYMBOLS or 
                                 quote_currency in STABLECOIN_SYMBOLS)
            
            if quote_currency != "THB" and is_stablecoin_pair:
                # Crypto-to-Stablecoin exchange (German tax: two taxable events)
                # Use type='exchange' with signed quantities
                
                if is_buyer:
                    # Buying base currency with quote currency (dispose quote, acquire base)
                    # Transaction 1: Dispose quote currency (negative qty)
                    quote_portfolio = (BINANCETH_CASH_PORTFOLIO_ID if quote_currency in STABLECOIN_SYMBOLS 
                                      else BINANCETH_DEFAULT_PORTFOLIO_ID)
                    transactions.append({
                        'external_id': f"{trade['id']}_quote",
                        'source': 'binanceth',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': quote_currency,
                        'quantity': -quote_qty,  # Negative = disposal
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(0),  # Fee handled in base transaction
                        'fee_currency': None,
                        'asset_class': 'crypto',
                        'symbol_normalized': quote_currency,
                        'account_id': BINANCETH_ACCOUNT_ID,
                        'portfolio_id': quote_portfolio,  # Portfolio 8 if stablecoin
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Disposed {quote_qty} {quote_currency} for {qty} {base_currency}"
                    })
                    
                    # Transaction 2: Acquire base currency (positive qty)
                    base_portfolio = (BINANCETH_CASH_PORTFOLIO_ID if base_currency in STABLECOIN_SYMBOLS 
                                     else BINANCETH_DEFAULT_PORTFOLIO_ID)
                    transactions.append({
                        'external_id': f"{trade['id']}_base",
                        'source': 'binanceth',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': base_currency,
                        'quantity': qty,  # Positive = acquisition
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(trade['commission']),
                        'fee_currency': trade['commissionAsset'],
                        'asset_class': 'crypto',
                        'symbol_normalized': base_currency,
                        'account_id': BINANCETH_ACCOUNT_ID,
                        'portfolio_id': base_portfolio,  # Portfolio 8 if stablecoin
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Acquired {qty} {base_currency} for {quote_qty} {quote_currency}"
                    })
                    
                else:  # is_seller
                    # Selling base currency for quote currency (dispose base, acquire quote)
                    # Transaction 1: Dispose base currency (negative qty)
                    base_portfolio = (BINANCETH_CASH_PORTFOLIO_ID if base_currency in STABLECOIN_SYMBOLS 
                                     else BINANCETH_DEFAULT_PORTFOLIO_ID)
                    transactions.append({
                        'external_id': f"{trade['id']}_base",
                        'source': 'binanceth',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': base_currency,
                        'quantity': -qty,  # Negative = disposal
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(trade['commission']),
                        'fee_currency': trade['commissionAsset'],
                        'asset_class': 'crypto',
                        'symbol_normalized': base_currency,
                        'account_id': BINANCETH_ACCOUNT_ID,
                        'portfolio_id': base_portfolio,  # Portfolio 8 if stablecoin
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Disposed {qty} {base_currency} for {quote_qty} {quote_currency}"
                    })
                    
                    # Transaction 2: Acquire quote currency (positive qty)
                    quote_portfolio = (BINANCETH_CASH_PORTFOLIO_ID if quote_currency in STABLECOIN_SYMBOLS 
                                      else BINANCETH_DEFAULT_PORTFOLIO_ID)
                    transactions.append({
                        'external_id': f"{trade['id']}_quote",
                        'source': 'binanceth',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': quote_currency,
                        'quantity': quote_qty,  # Positive = acquisition
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(0),  # Fee handled in base transaction
                        'fee_currency': None,
                        'asset_class': 'crypto',
                        'symbol_normalized': quote_currency,
                        'account_id': BINANCETH_ACCOUNT_ID,
                        'portfolio_id': quote_portfolio,  # Portfolio 8 if stablecoin
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Acquired {quote_qty} {quote_currency} for {qty} {base_currency}"
                    })
                    
            elif quote_currency != "THB":
                # Legacy crypto-to-crypto (non-stablecoin pairs)
                # Keep original buy/sell behavior for backward compatibility
                if is_buyer:
                    # Sell quote currency, buy base currency
                    transactions.append({
                        'external_id': f"{trade['id']}-sell", 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'sell', 'symbol': quote_currency, 'quantity': -quote_qty, 'price': 1 / price, 'value_native': -qty, 'currency_native': base_currency,
                        'fee': Decimal(0), 'fee_currency': trade['commissionAsset'], 'asset_class': 'crypto', 'symbol_normalized': quote_currency,
                        'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                        'currency_base': portfolio_base_currency,
                    })
                    transactions.append({
                        'external_id': f"{trade['id']}-buy", 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'buy', 'symbol': base_currency, 'quantity': qty, 'price': price, 'value_native': quote_qty, 'currency_native': quote_currency,
                        'fee': Decimal(trade['commission']), 'fee_currency': trade['commissionAsset'], 'asset_class': 'crypto', 'symbol_normalized': base_currency,
                        'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                        'currency_base': portfolio_base_currency,
                    })
                else: # is_seller
                    # Sell base currency, buy quote currency
                    transactions.append({
                        'external_id': f"{trade['id']}-sell", 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'sell', 'symbol': base_currency, 'quantity': -qty, 'price': price, 'value_native': -quote_qty, 'currency_native': quote_currency,
                        'fee': Decimal(trade['commission']), 'fee_currency': trade['commissionAsset'], 'asset_class': 'crypto', 'symbol_normalized': base_currency,
                        'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                        'currency_base': portfolio_base_currency,
                    })
                    transactions.append({
                        'external_id': f"{trade['id']}-buy", 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'buy', 'symbol': quote_currency, 'quantity': quote_qty, 'price': 1 / price, 'value_native': qty, 'currency_native': base_currency,
                        'fee': Decimal(0), 'fee_currency': trade['commissionAsset'], 'asset_class': 'crypto', 'symbol_normalized': quote_currency,
                        'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                        'currency_base': portfolio_base_currency,
                    })
            else: # Regular trade with THB
                transactions.append({
                    'external_id': str(trade['id']), 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                    'type': 'buy' if is_buyer else 'sell', 'symbol': base_currency, 'quantity': qty, 'price': price, 'value_native': quote_qty, 'currency_native': quote_currency,
                    'fee': Decimal(trade['commission']), 'fee_currency': trade['commissionAsset'], 'asset_class': 'crypto', 'symbol_normalized': base_currency,
                    'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                    'currency_base': portfolio_base_currency,
                })

        except (KeyError, TypeError) as e:
            logger.error(f"Skipping trade due to parsing error: {e}. Trade data: {trade}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions

def parse_binanceth_deposits(deposits_data: list[dict]) -> list[dict]:
    """
    Parses a list of deposit data from the Binance.th API /api/v1/capital/deposit/history endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(deposits_data)} deposits from Binance.th data.")

    for deposit in deposits_data:
        try:
            tx_data = {
                'external_id': str(deposit['txId']), 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(deposit['insertTime'] / 1000),
                'type': 'deposit', 'symbol': deposit['coin'], 'quantity': Decimal(deposit['amount']), 'price': None, 'value_native': None, 'currency_native': None,
                'fee': Decimal('0'), 'fee_currency': deposit['coin'], 'asset_class': 'crypto' if deposit['coin'] != 'THB' else 'cash',
                'symbol_normalized': deposit['coin'], 'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                'currency_base': portfolio_base_currency,
            }
            transactions.append(tx_data)
        except (KeyError, TypeError) as e:
            logger.error(f"Skipping deposit due to parsing error: {e}. Deposit data: {deposit}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions

def parse_binanceth_withdrawals(withdrawals_data: list[dict]) -> list[dict]:
    """
    Parses a list of withdrawal data from the Binance.th API /api/v1/capital/withdraw/history endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(withdrawals_data)} withdrawals from Binance.th data.")

    for withdrawal in withdrawals_data:
        try:
            tx_data = {
                'external_id': str(withdrawal['id']), 'source': 'binanceth', 'occurred_at': datetime.fromtimestamp(withdrawal['applyTime'] / 1000),
                'type': 'withdrawal', 'symbol': withdrawal['coin'], 'quantity': -Decimal(withdrawal['amount']), 'price': None, 'value_native': None, 'currency_native': None,
                'fee': Decimal(withdrawal['transactionFee']), 'fee_currency': withdrawal['coin'], 'asset_class': 'crypto' if withdrawal['coin'] != 'THB' else 'cash',
                'symbol_normalized': withdrawal['coin'], 'account_id': BINANCETH_ACCOUNT_ID, 'portfolio_id': BINANCETH_DEFAULT_PORTFOLIO_ID,
                'currency_base': portfolio_base_currency,
            }
            transactions.append(tx_data)
        except (KeyError, TypeError) as e:
            logger.error(f"Skipping withdrawal due to parsing error: {e}. Withdrawal data: {withdrawal}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions
