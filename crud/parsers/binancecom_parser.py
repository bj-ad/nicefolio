from datetime import datetime
from decimal import Decimal
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from utils.source_mapping_loader import get_source_mapping_loader

logger = get_logger(__name__)

# --- Configuration Loading ---
app_config = load_app_config()
source_mapping_loader = get_source_mapping_loader()

BINANCECOM_ACCOUNT_ID = source_mapping_loader.get_account_id('BinanceCOM')
BINANCECOM_DEFAULT_PORTFOLIO_ID = source_mapping_loader.get_default_portfolio_id('BinanceCOM')
BINANCECOM_CASH_PORTFOLIO_ID = source_mapping_loader.get_cash_portfolio_id('BinanceCOM')
TRADING_BASE_CURRENCY = app_config.get("trading_base_currency")
STABLECOIN_SYMBOLS = set(app_config.get("stablecoin_symbols", []))

def parse_binancecom_trades(trades_data: list[dict]) -> list[dict]:
    """
    Parses a list of trade data from the Binance.com API /api/v3/myTrades endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    
    German Tax Compliance:
    - Stablecoins (USDT, USDC, etc.) are crypto assets, not fiat currency
    - Trading BTC/USDT creates TWO transactions (exchange):
      1. Dispose USDT (taxable event) → Portfolio 8
      2. Acquire/Dispose BTC → Portfolio 6
    - This ensures proper cost basis tracking and FIFO allocation
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(trades_data)} trades from Binance.com data.")

    for trade in trades_data:
        try:
            base_currency = trade['symbol'].replace(TRADING_BASE_CURRENCY, '')
            is_stablecoin_pair = TRADING_BASE_CURRENCY in STABLECOIN_SYMBOLS
            
            if is_stablecoin_pair:
                # Crypto-to-Crypto exchange (German tax: two taxable events)
                # Example: Buy 0.01 BTC with 500 USDT
                #   → Dispose 500 USDT (from Portfolio 8)
                #   → Acquire 0.01 BTC (to Portfolio 6)
                
                if trade['isBuyer']:
                    # Buying base currency with stablecoin
                    # Transaction 1: Dispose stablecoin (negative qty)
                    stablecoin_tx = {
                        'external_id': f"{trade['id']}_stablecoin",
                        'source': 'binancecom',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': TRADING_BASE_CURRENCY,
                        'quantity': -Decimal(trade['quoteQty']),  # Negative = disposal
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal('0'),  # Fee handled in crypto transaction
                        'fee_currency': None,
                        'asset_class': 'crypto',
                        'symbol_normalized': TRADING_BASE_CURRENCY,
                        'account_id': BINANCECOM_ACCOUNT_ID,
                        'portfolio_id': BINANCECOM_CASH_PORTFOLIO_ID,  # Portfolio 8
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Disposed {trade['quoteQty']} {TRADING_BASE_CURRENCY} for {trade['qty']} {base_currency}"
                    }
                    transactions.append(stablecoin_tx)
                    
                    # Transaction 2: Acquire crypto (positive qty)
                    crypto_tx = {
                        'external_id': f"{trade['id']}_crypto",
                        'source': 'binancecom',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': base_currency,
                        'quantity': Decimal(trade['qty']),  # Positive = acquisition
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(trade['commission']),
                        'fee_currency': trade['commissionAsset'],
                        'asset_class': 'crypto',
                        'symbol_normalized': base_currency,
                        'account_id': BINANCECOM_ACCOUNT_ID,
                        'portfolio_id': BINANCECOM_DEFAULT_PORTFOLIO_ID,  # Portfolio 6: Crypto Trading (CLOSED)
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Acquired {trade['qty']} {base_currency} for {trade['quoteQty']} {TRADING_BASE_CURRENCY}"
                    }
                    transactions.append(crypto_tx)
                    
                else:
                    # Selling base currency for stablecoin
                    # Transaction 1: Dispose crypto (negative qty)
                    crypto_tx = {
                        'external_id': f"{trade['id']}_crypto",
                        'source': 'binancecom',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': base_currency,
                        'quantity': -Decimal(trade['qty']),  # Negative = disposal
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal(trade['commission']),
                        'fee_currency': trade['commissionAsset'],
                        'asset_class': 'crypto',
                        'symbol_normalized': base_currency,
                        'account_id': BINANCECOM_ACCOUNT_ID,
                        'portfolio_id': BINANCECOM_DEFAULT_PORTFOLIO_ID,  # Portfolio 6: Crypto Trading (CLOSED)
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Disposed {trade['qty']} {base_currency} for {trade['quoteQty']} {TRADING_BASE_CURRENCY}"
                    }
                    transactions.append(crypto_tx)
                    
                    # Transaction 2: Acquire stablecoin (positive qty)
                    stablecoin_tx = {
                        'external_id': f"{trade['id']}_stablecoin",
                        'source': 'binancecom',
                        'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                        'type': 'exchange',
                        'symbol': TRADING_BASE_CURRENCY,
                        'quantity': Decimal(trade['quoteQty']),  # Positive = acquisition
                        'price': None,
                        'value_native': None,
                        'currency_native': None,
                        'fee': Decimal('0'),  # Fee handled in crypto transaction
                        'fee_currency': None,
                        'asset_class': 'crypto',
                        'symbol_normalized': TRADING_BASE_CURRENCY,
                        'account_id': BINANCECOM_ACCOUNT_ID,
                        'portfolio_id': BINANCECOM_CASH_PORTFOLIO_ID,  # Portfolio 8
                        'currency_base': portfolio_base_currency,
                        'notes': f"Exchange: Acquired {trade['quoteQty']} {TRADING_BASE_CURRENCY} for {trade['qty']} {base_currency}"
                    }
                    transactions.append(stablecoin_tx)
                    
                logger.debug(f"Created exchange transactions for stablecoin pair: {base_currency}/{TRADING_BASE_CURRENCY}")
                
            else:
                # Regular fiat-paired trade (e.g., BTC/USD on Binance.US)
                # Keep original single-transaction behavior
                tx_data = {
                    'external_id': str(trade['id']),
                    'source': 'binancecom',
                    'occurred_at': datetime.fromtimestamp(trade['time'] / 1000),
                    'type': 'buy' if trade['isBuyer'] else 'sell',
                    'symbol': base_currency,
                    'quantity': Decimal(trade['qty']),
                    'price': Decimal(trade['price']),
                    'value_native': Decimal(trade['quoteQty']),
                    'currency_native': TRADING_BASE_CURRENCY,
                    'fee': Decimal(trade['commission']),
                    'fee_currency': trade['commissionAsset'],
                    'asset_class': 'crypto',
                    'symbol_normalized': base_currency,
                    'account_id': BINANCECOM_ACCOUNT_ID,
                    'portfolio_id': BINANCECOM_DEFAULT_PORTFOLIO_ID,
                    'currency_base': portfolio_base_currency,
                }
                transactions.append(tx_data)
        except (KeyError, TypeError) as e:
            logger.error(f"Skipping trade due to parsing error: {e}. Trade data: {trade}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions

def parse_binancecom_deposits(deposits_data: list[dict]) -> list[dict]:
    """
    Parses a list of deposit data from the Binance.com API /sapi/v1/capital/deposit/hisrec endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(deposits_data)} deposits from Binance.com data.")

    for deposit in deposits_data:
        try:
            tx_data = {
                'external_id': str(deposit['txId']),
                'source': 'binancecom',
                'occurred_at': datetime.fromtimestamp(deposit['insertTime'] / 1000),
                'type': 'deposit',
                'symbol': deposit['coin'],
                'quantity': Decimal(deposit['amount']),
                'price': None,
                'value_native': None,
                'currency_native': None,
                'fee': Decimal('0'),
                'fee_currency': deposit['coin'],
                'asset_class': 'crypto',
                'symbol_normalized': deposit['coin'],
                'account_id': BINANCECOM_ACCOUNT_ID,
                'portfolio_id': BINANCECOM_DEFAULT_PORTFOLIO_ID,
                'currency_base': portfolio_base_currency,
            }
            transactions.append(tx_data)
        except (KeyError, TypeError) as e:
            logger.error(f"Skipping deposit due to parsing error: {e}. Deposit data: {deposit}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions

def parse_binancecom_withdrawals(withdrawals_data: list[dict]) -> list[dict]:
    """
    Parses a list of withdrawal data from the Binance.com API /sapi/v1/capital/withdraw/history endpoint
    and transforms it into a list of dictionaries matching the Transaction model.
    """
    transactions = []
    portfolio_base_currency = get_global_base_currency()
    logger.info(f"Parsing {len(withdrawals_data)} withdrawals from Binance.com data.")

    for withdrawal in withdrawals_data:
        try:
            tx_data = {
                'external_id': str(withdrawal['id']),
                'source': 'binancecom',
                'occurred_at': datetime.fromtimestamp(withdrawal['applyTime'] / 1000),
                'type': 'withdrawal',
                'symbol': withdrawal['coin'],
                'quantity': -Decimal(withdrawal['amount']), # Negative for withdrawal
                'price': None,
                'value_native': None,
                'currency_native': None,
                'fee': Decimal(withdrawal['transactionFee']),
                'fee_currency': withdrawal['coin'],
                'asset_class': 'crypto',
                'symbol_normalized': withdrawal['coin'],
                'account_id': BINANCECOM_ACCOUNT_ID,
                'portfolio_id': BINANCECOM_DEFAULT_PORTFOLIO_ID,
                'currency_base': portfolio_base_currency,
            }
            transactions.append(tx_data)
        except (KeyError, TypeError) as e:
            logger.error(f"Skipping withdrawal due to parsing error: {e}. Withdrawal data: {withdrawal}")
            continue
            
    logger.info(f"Successfully parsed {len(transactions)} transactions.")
    return transactions
