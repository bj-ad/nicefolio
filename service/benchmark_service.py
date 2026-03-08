"""
Benchmark Service - Fetches and manages benchmark price data for portfolio comparison.

This service handles:
- Fetching benchmark prices (from yfinance) for comparison with portfolio performance
- Avoiding duplicate market data when benchmarks are also held in portfolios
- Historical backfill for benchmark symbols

IMPORTANT: Benchmarks are stored in the same market_data table as regular holdings.
This allows reuse of existing price data when a benchmark symbol is also held.

CONFIG STRUCTURE:
benchmarks:
  benchmark_securities:
    symbol: VHVE.MI
    label: VHVE
    yfinance_symbol: VHVE.MI
    currency: EUR
  benchmark_crypto:
    symbol: BTC
    label: BTC
    yfinance_symbol: BTC-USD
    currency: USD
  risk_free_rate: 4.0
  benchmark_crypto_ratio: 0.25
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Any
import math

import pandas as pd
import yfinance as yf
from sqlalchemy import cast, Date

from database import SessionLocal
from models import MarketData, Snapshot
from utils.logging_config import get_logger
from utils.app_config import load_app_config, get_global_base_currency
from crud.crud_market_fx import ingest_market_prices, get_latest_price

logger = get_logger("benchmark_service")


# ============================================================================
# CONFIG GETTERS - All benchmark config from app_config.yaml
# ============================================================================

def get_benchmarks_config() -> Dict[str, Any]:
    """
    Get full benchmarks configuration from app_config.yaml.
    
    Returns:
        dict: Benchmarks config with benchmark_securities, benchmark_crypto, etc.
    """
    config = load_app_config()
    return config.get('benchmarks', {})


def get_benchmark_securities_config() -> Dict[str, Any]:
    """Get securities benchmark configuration."""
    benchmarks = get_benchmarks_config()
    return benchmarks.get('benchmark_securities', {})


def get_benchmark_crypto_config() -> Dict[str, Any]:
    """Get crypto benchmark configuration."""
    benchmarks = get_benchmarks_config()
    return benchmarks.get('benchmark_crypto', {})


def get_benchmark_symbols() -> list[str]:
    """
    Get benchmark symbols from config file.
    
    Returns internal symbols (e.g., 'VHVE', 'BTC', '4GLD') without exchange suffixes.
    Symbol mapping system handles the conversion to yfinance format.
    
    Returns:
        list[str]: List of benchmark symbols (e.g., ['VHVE', 'BTC', '4GLD'])
    """
    symbols = []
    sec_config = get_benchmark_securities_config()
    crypto_config = get_benchmark_crypto_config()
    benchmarks = get_benchmarks_config()
    comm_config = benchmarks.get('benchmark_commodities', {})
    
    if sec_config.get('symbol'):
        symbols.append(sec_config['symbol'])
    if crypto_config.get('symbol'):
        symbols.append(crypto_config['symbol'])
    if comm_config.get('symbol'):
        symbols.append(comm_config['symbol'])
    
    return symbols


def get_risk_free_rate() -> float:
    """Get risk-free rate from config (default 4.0%)."""
    benchmarks = get_benchmarks_config()
    return benchmarks.get('risk_free_rate', 4.0)


def get_benchmark_crypto_ratio() -> float:
    """Get crypto ratio for commodities blend (default 0.25 = 25%)."""
    benchmarks = get_benchmarks_config()
    return benchmarks.get('benchmark_crypto_ratio', 0.25)


def get_yfinance_ticker(symbol: str) -> str:
    """
    Convert internal symbol to yfinance ticker format using symbol_mapping infrastructure.
    
    Uses the centralized symbol mapping system:
    - Database SymbolMapping table (primary)
    - config/symbol_mapping.yaml (fallback)
    - Auto-detection (defaults)
    
    Args:
        symbol: Internal symbol (e.g., 'BTC', 'VHVE')
        
    Returns:
        str: yfinance ticker (e.g., 'BTC-USD', 'VHVE.MI')
    """
    from crud.crud_symbol_mapping import get_symbol_mapping
    from database import SessionLocal
    
    db = SessionLocal()
    try:
        # Get currency for this symbol from benchmark config
        currency = get_benchmark_currency(symbol)
        
        # Determine asset class for proper mapping
        crypto_config = get_benchmark_crypto_config()
        asset_class = 'crypto' if crypto_config.get('symbol') == symbol else 'security'
        
        # Use centralized symbol mapping system
        mapping = get_symbol_mapping(db, symbol=symbol, currency=currency, asset_class=asset_class)
        return mapping['yfinance_symbol']
    except Exception as e:
        logger.error(f"Failed to get yfinance ticker for {symbol}: {e}")
        return symbol  # Fallback to symbol as-is
    finally:
        db.close()


def get_benchmark_currency(symbol: str) -> str:
    """
    Get the currency for a benchmark symbol using symbol_mapping infrastructure.
    
    Uses the centralized symbol mapping system:
    - Database SymbolMapping table (primary)
    - config/symbol_mapping.yaml (fallback)
    - Auto-detection (defaults)
    
    Args:
        symbol: Benchmark symbol
        
    Returns:
        str: Currency code (e.g., 'USD', 'EUR')
    """
    from crud.crud_symbol_mapping import get_symbol_mapping
    from database import SessionLocal
    
    db = SessionLocal()
    try:
        # Determine asset class
        crypto_config = get_benchmark_crypto_config()
        asset_class = 'crypto' if crypto_config.get('symbol') == symbol else 'security'
        
        # Try USD first for crypto, then fallback to symbol mapping system
        currency = 'USD' if asset_class == 'crypto' else 'EUR'
        
        # Use centralized symbol mapping system
        mapping = get_symbol_mapping(db, symbol=symbol, currency=currency, asset_class=asset_class)
        return mapping['currency']
    except Exception as e:
        logger.error(f"Failed to get currency for {symbol}: {e}")
        # Final fallback
        return 'USD' if symbol in ['BTC', 'ETH'] else 'EUR'
    finally:
        db.close()


def get_benchmark_label(symbol: str) -> str:
    """
    Get display label for a benchmark symbol from config.
    
    Args:
        symbol: Benchmark symbol
        
    Returns:
        str: Display label (e.g., 'VHVE', 'BTC')
    """
    sec_config = get_benchmark_securities_config()
    crypto_config = get_benchmark_crypto_config()
    
    if sec_config.get('symbol') == symbol:
        return sec_config.get('label', symbol)
    
    if crypto_config.get('symbol') == symbol:
        return crypto_config.get('label', symbol)
    
    return symbol


def get_benchmark_for_portfolio_type(portfolio_type: str) -> Optional[Dict[str, Any]]:
    """
    Get the appropriate benchmark config for a portfolio type.
    
    Args:
        portfolio_type: 'securities', 'crypto', 'commodities', etc.
        
    Returns:
        dict: Benchmark config with symbol, label, currency, etc.
    """
    if portfolio_type == 'securities':
        return get_benchmark_securities_config()
    elif portfolio_type == 'crypto':
        return get_benchmark_crypto_config()
    elif portfolio_type == 'commodities':
        # Commodities use composite benchmark
        from apps.core.helpers import get_composite_benchmark_label
        return {
            'symbol': 'COMPOSITE',
            'label': get_composite_benchmark_label(),
            'is_composite': True
        }
    return None


def has_price_for_date(db, symbol: str, target_date: date) -> bool:
    """
    Check if market data exists for a symbol on a specific date.
    
    Args:
        db: Database session
        symbol: Symbol to check
        target_date: Date to check
        
    Returns:
        bool: True if price exists for that date
    """
    row = db.query(MarketData).filter(
        MarketData.symbol == symbol,
        cast(MarketData.as_of_date, Date) == target_date
    ).first()
    
    return row is not None


def fetch_benchmark_price_for_date(symbol: str, target_date: date) -> Optional[dict]:
    """
    Fetch historical price for a benchmark symbol on a specific date.
    
    Args:
        symbol: Benchmark symbol
        target_date: Date to fetch price for
        
    Returns:
        dict: Price data or None if not available
    """
    try:
        yf_symbol = get_yfinance_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        
        # Fetch historical data for the specific date
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = start_datetime + timedelta(days=1)
        
        hist = ticker.history(start=start_datetime, end=end_datetime, interval='1d')
        
        if hist.empty:
            logger.debug(f"No historical data for {symbol} (yf: {yf_symbol}) on {target_date}")
            return None
        
        close_price = hist['Close'].iloc[0]
        currency = get_benchmark_currency(symbol)
        
        return {
            'symbol': symbol,
            'price': Decimal(str(close_price)),
            'currency': currency,
            'source': 'yfinance_benchmark',
            'ts': start_datetime
        }
        
    except Exception as e:
        logger.error(f"Error fetching benchmark price for {symbol} on {target_date}: {e}")
        return None


def fetch_current_benchmark_price(symbol: str) -> Optional[dict]:
    """
    Fetch current price for a benchmark symbol using same method as securities sync.
    Uses history(period='1d') which returns most recent trading day (works on weekends).
    
    Args:
        symbol: Benchmark symbol
        
    Returns:
        dict: Price data or None if not available
    """
    try:
        yf_symbol = get_yfinance_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        currency = get_benchmark_currency(symbol)
        
        # Use same method as parse_yfinance_price() for consistency
        # period='1d' returns most recent trading day (Friday on weekends)
        hist = ticker.history(period='1d')
        if not hist.empty:
            close_price = hist['Close'].iloc[-1]
            return {
                'symbol': symbol,
                'price': Decimal(str(close_price)),
                'currency': currency,
                'source': 'yfinance_benchmark'
            }
        
        logger.warning(f"No yfinance data for benchmark {symbol}")
        return None
        
    except Exception as e:
        logger.error(f"Error fetching current benchmark price for {symbol}: {e}")
        return None


def get_first_portfolio_snapshot_date() -> Optional[date]:
    """
    Get the date of the first ever portfolio snapshot across all portfolios.
    
    Returns:
        date: First snapshot date or None if no snapshots exist
    """
    db = SessionLocal()
    try:
        first_snapshot = db.query(Snapshot).order_by(Snapshot.snapshot_date).first()
        if first_snapshot:
            return first_snapshot.snapshot_date
        return None
    finally:
        db.close()


def backfill_benchmark_prices(start_date: Optional[date] = None, end_date: Optional[date] = None) -> tuple[int, int]:
    """
    Backfill historical prices for all benchmark symbols.
    Skips dates where prices already exist (avoids duplicates).
    
    Args:
        start_date: Start date for backfill (defaults to first portfolio snapshot)
        end_date: End date for backfill (defaults to yesterday)
        
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    symbols = get_benchmark_symbols()
    if not symbols:
        logger.info("No benchmark symbols configured")
        return 0, 0
    
    # Determine date range
    if start_date is None:
        start_date = get_first_portfolio_snapshot_date()
        if start_date is None:
            logger.info("No portfolio snapshots found, skipping benchmark backfill")
            return 0, 0
    
    if end_date is None:
        end_date = date.today() - timedelta(days=1)  # Exclude today
    
    if start_date > end_date:
        logger.info("Start date is after end date, skipping benchmark backfill")
        return 0, 0
    
    logger.info(f"Backfilling benchmark prices for {symbols} from {start_date} to {end_date}")
    
    db = SessionLocal()
    try:
        success = 0
        failed = 0
        
        current_date = start_date
        while current_date <= end_date:
            for symbol in symbols:
                # Skip if price already exists
                if has_price_for_date(db, symbol, current_date):
                    logger.debug(f"Price already exists for {symbol} on {current_date}, skipping")
                    continue
                
                # Try to fetch actual price for this date
                price_data = fetch_benchmark_price_for_date(symbol, current_date)
                if price_data:
                    result = ingest_market_prices(db, [price_data])
                    if result[0] > 0:
                        logger.info(f"  ✅ {symbol} {current_date}: {price_data['currency']} {price_data['price']:.2f}")
                        success += 1
                    else:
                        failed += 1
                else:
                    # No data for this date (weekend/holiday) - forward-fill from last known price
                    # This ensures consistent data completeness matching daily job behavior
                    logger.debug(f"No trading data for {symbol} on {current_date}, attempting forward-fill")
                    last_price = get_latest_price(db, symbol, at_ts=current_date)
                    if last_price:
                        forward_filled = {
                            'symbol': symbol,
                            'price': last_price.price,
                            'currency': last_price.currency,
                            'source': 'forward_fill',
                            'ts': datetime.combine(current_date, datetime.min.time())
                        }
                        result = ingest_market_prices(db, [forward_filled])
                        if result[0] > 0:
                            logger.debug(f"  ⏩ {symbol} {current_date}: Forward-filled {forward_filled['currency']} {forward_filled['price']:.2f}")
                            success += 1
                        else:
                            failed += 1
                    else:
                        logger.debug(f"No previous price available for {symbol} to forward-fill on {current_date}")
            
            current_date += timedelta(days=1)
        
        logger.info(f"Benchmark backfill complete: {success} succeeded, {failed} failed")
        return success, failed
        
    finally:
        db.close()


def sync_benchmark_prices() -> tuple[int, int]:
    """
    Sync current prices for all benchmark symbols.
    Uses same yfinance method as securities sync for consistency.
    
    Skips symbols that already have price data for today (avoids redundant API calls).
    yfinance history(period='1d') returns most recent trading day, so this works on weekends.
    
    This is called during daily jobs to ensure benchmark data is up-to-date.
    
    Returns:
        tuple[int, int]: (success_count, failure_count)
    """
    symbols = get_benchmark_symbols()
    if not symbols:
        logger.info("No benchmark symbols configured")
        return 0, 0
    
    logger.info(f"Syncing current prices for {len(symbols)} benchmark symbols: {symbols}")
    
    db = SessionLocal()
    try:
        today = date.today()
        all_prices = []
        
        for symbol in symbols:
            # Skip if price already exists for today (avoid duplicate fetches)
            if has_price_for_date(db, symbol, today):
                logger.info(f"Price already exists for {symbol} on {today}, skipping")
                continue
            
            # Fetch price using same method as securities sync
            # This returns Friday's price on weekends automatically
            price_data = fetch_current_benchmark_price(symbol)
            if price_data:
                logger.info(f"Fetched {symbol}: {price_data['currency']} {price_data['price']:.2f}")
                all_prices.append(price_data)
            else:
                logger.warning(f"Could not fetch price for benchmark {symbol}")
        
        # Ingest all prices
        if all_prices:
            success, failed = ingest_market_prices(db, all_prices)
            logger.info(f"Benchmark price sync complete: {success} succeeded, {failed} failed")
            return success, failed
        else:
            logger.info("No new benchmark prices to sync (all prices up-to-date)")
            return 0, 0
            
    finally:
        db.close()


def get_benchmark_prices_for_range(symbol: str, start_date: date, end_date: date) -> list[tuple[date, Decimal]]:
    """
    Get benchmark prices for a date range from the database.
    
    Args:
        symbol: Benchmark symbol
        start_date: Start date
        end_date: End date
        
    Returns:
        list[tuple[date, Decimal]]: List of (date, price) tuples
    """
    db = SessionLocal()
    try:
        rows = db.query(MarketData).filter(
            MarketData.symbol == symbol,
            cast(MarketData.as_of_date, Date) >= start_date,
            cast(MarketData.as_of_date, Date) <= end_date
        ).order_by(MarketData.as_of_date).all()
        
        return [(row.as_of_date.date() if isinstance(row.as_of_date, datetime) else row.as_of_date, row.price) for row in rows]
        
    finally:
        db.close()


def get_benchmark_normalized_series(symbol: str, start_date: date, end_date: date) -> list[tuple[date, float]]:
    """
    Get normalized benchmark prices (rebased to 100 at start) for charting.
    
    Args:
        symbol: Benchmark symbol
        start_date: Start date
        end_date: End date
        
    Returns:
        list[tuple[date, float]]: List of (date, normalized_value) tuples
    """
    prices = get_benchmark_prices_for_range(symbol, start_date, end_date)
    
    if not prices:
        return []
    
    first_price = float(prices[0][1])
    if first_price <= 0:
        return []
    
    return [(d, (float(p) / first_price) * 100) for d, p in prices]


def calculate_benchmark_twr(symbol: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate Time-Weighted Return for a benchmark symbol (ANNUALIZED).
    
    For benchmarks, TWR calculation:
    1. Total return = (end_price / start_price - 1) * 100
    2. Annualized TWR = ((end_price/start_price)^(365/days) - 1) * 100
    
    This matches the portfolio TWR calculation which is also annualized,
    ensuring accurate alpha (excess return) comparisons.
    
    Args:
        symbol: Benchmark symbol
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Annualized TWR as percentage (e.g., 15.5 for 15.5% annual return)
    """
    prices = get_benchmark_prices_for_range(symbol, start_date, end_date)
    
    if len(prices) < 2:
        return None
    
    first_price = float(prices[0][1])
    last_price = float(prices[-1][1])
    
    if first_price <= 0:
        return None
    
    # Calculate cumulative growth factor
    cumulative_growth_factor = last_price / first_price
    
    # Calculate number of days in the period
    days = (end_date - start_date).days
    
    if days <= 0:
        return None
    
    # Annualize the TWR using the same formula as portfolio TWR
    # For periods < 1 year, this still works and gives the total return
    years = days / 365.25
    
    if years > 0:
        if cumulative_growth_factor > 0:
            annualized_twr = (pow(cumulative_growth_factor, 1.0 / years) - 1.0) * 100
            return round(annualized_twr, 2)
        else:
            return 0.0
    else:
        # For very short periods, return total return
        total_twr = (cumulative_growth_factor - 1.0) * 100
        return round(total_twr, 2)


def calculate_benchmark_volatility(symbol: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate annualized volatility for a benchmark symbol.
    
    Uses daily price returns and annualizes the standard deviation.
    
    Args:
        symbol: Benchmark symbol
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Annualized volatility as percentage (e.g., 20.5 for 20.5% volatility)
    """
    import math
    
    prices = get_benchmark_prices_for_range(symbol, start_date, end_date)
    
    if len(prices) < 10:  # Need enough data points
        return None
    
    # Calculate daily returns
    daily_returns = []
    for i in range(1, len(prices)):
        prev_price = float(prices[i-1][1])
        curr_price = float(prices[i][1])
        
        if prev_price > 0:
            daily_return = (curr_price / prev_price) - 1
            daily_returns.append(daily_return)
    
    if len(daily_returns) < 5:
        return None
    
    # Calculate standard deviation
    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
    std_dev = math.sqrt(variance)
    
    # Annualize: multiply by sqrt(252) for trading days
    annualized_volatility = std_dev * math.sqrt(252) * 100
    
    return round(annualized_volatility, 2)


def get_benchmark_risk_reward_data(start_date: date, end_date: date) -> list[dict]:
    """
    Get risk/reward data for all benchmark symbols for scatter chart.
    
    Returns data for:
    - Securities benchmark (VHVE)
    - Crypto benchmark (BTC)
    - Composite benchmark (Target Composite with rebalancing)
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        list[dict]: List of dicts with keys: symbol, label, benchmark_type, twr, 
                    volatility, twr_annualized, sharpe_ratio
    """
    results = []
    risk_free_rate = get_risk_free_rate()
    
    # Get securities benchmark
    sec_config = get_benchmark_securities_config()
    if sec_config.get('symbol'):
        symbol = sec_config['symbol']
        # calculate_benchmark_twr returns ANNUALIZED TWR
        twr_annualized = calculate_benchmark_twr(symbol, start_date, end_date)
        volatility = calculate_benchmark_volatility(symbol, start_date, end_date)
        
        if twr_annualized is not None and volatility is not None:
            # Calculate Sharpe ratio
            sharpe = (twr_annualized - risk_free_rate) / volatility if volatility > 0 else 0
            
            results.append({
                'symbol': symbol,
                'label': sec_config.get('label', symbol),
                'benchmark_type': 'securities',
                'twr': twr_annualized,  # Already annualized
                'twr_annualized': round(twr_annualized, 2),
                'volatility': volatility,
                'sharpe_ratio': round(sharpe, 2)
            })
    
    # Get crypto benchmark
    crypto_config = get_benchmark_crypto_config()
    if crypto_config.get('symbol'):
        symbol = crypto_config['symbol']
        # calculate_benchmark_twr returns ANNUALIZED TWR
        twr_annualized = calculate_benchmark_twr(symbol, start_date, end_date)
        volatility = calculate_benchmark_volatility(symbol, start_date, end_date)
        
        if twr_annualized is not None and volatility is not None:
            # Calculate Sharpe ratio
            sharpe = (twr_annualized - risk_free_rate) / volatility if volatility > 0 else 0
            
            results.append({
                'symbol': symbol,
                'label': crypto_config.get('label', symbol),
                'benchmark_type': 'crypto',
                'twr': twr_annualized,  # Already annualized
                'twr_annualized': round(twr_annualized, 2),
                'volatility': volatility,
                'sharpe_ratio': round(sharpe, 2)
            })
    
    # Get composite benchmark
    from apps.core.helpers import get_composite_benchmark_label
    composite_twr = calculate_composite_benchmark_twr(start_date, end_date)
    composite_volatility = calculate_composite_benchmark_volatility(start_date, end_date)
    
    if composite_twr is not None and composite_volatility is not None:
        # Calculate Sharpe ratio
        sharpe = (composite_twr - risk_free_rate) / composite_volatility if composite_volatility > 0 else 0
        
        results.append({
            'symbol': 'COMPOSITE',
            'label': get_composite_benchmark_label(),
            'benchmark_type': 'composite',
            'twr': composite_twr,
            'twr_annualized': composite_twr,  # Already annualized
            'volatility': composite_volatility,
            'sharpe_ratio': round(sharpe, 2)
        })
    
    return results


def calculate_blended_benchmark_twr(start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate blended benchmark TWR for commodities portfolios.
    Uses configured ratio (default 75% securities, 25% crypto).
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Blended TWR as percentage
    """
    sec_config = get_benchmark_securities_config()
    crypto_config = get_benchmark_crypto_config()
    crypto_ratio = get_benchmark_crypto_ratio()
    sec_ratio = 1.0 - crypto_ratio
    
    sec_twr = None
    crypto_twr = None
    
    if sec_config.get('symbol'):
        sec_twr = calculate_benchmark_twr(sec_config['symbol'], start_date, end_date)
    
    if crypto_config.get('symbol'):
        crypto_twr = calculate_benchmark_twr(crypto_config['symbol'], start_date, end_date)
    
    if sec_twr is None and crypto_twr is None:
        return None
    
    # Calculate weighted average
    if sec_twr is not None and crypto_twr is not None:
        blended = (sec_twr * sec_ratio) + (crypto_twr * crypto_ratio)
        return round(blended, 2)
    elif sec_twr is not None:
        return round(sec_twr, 2)
    elif crypto_twr is not None:
        return round(crypto_twr, 2)
    
    return None


def get_benchmark_twr_for_portfolio_type(portfolio_type: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Get benchmark TWR for a specific portfolio type.
    
    Uses:
    - Securities: VHVE benchmark
    - Crypto: BTC benchmark
    - Commodities: Composite benchmark with target allocations
    - All/Mixed: Composite benchmark with target allocations
    
    Args:
        portfolio_type: Portfolio type ('securities', 'crypto', 'commodities', or 'all'/'mixed')
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Benchmark TWR as percentage
    """
    if portfolio_type == 'securities':
        sec_config = get_benchmark_securities_config()
        if sec_config.get('symbol'):
            return calculate_benchmark_twr(sec_config['symbol'], start_date, end_date)
    elif portfolio_type == 'crypto':
        crypto_config = get_benchmark_crypto_config()
        if crypto_config.get('symbol'):
            return calculate_benchmark_twr(crypto_config['symbol'], start_date, end_date)
    elif portfolio_type in ['commodities', 'all', 'mixed', None]:
        # Use composite benchmark for commodities and aggregate portfolios
        return calculate_composite_benchmark_twr(start_date, end_date)
    
    return None


def calculate_alpha(portfolio_twr: float, portfolio_type: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate alpha (excess return) vs appropriate benchmark.
    
    Alpha = Portfolio TWR - Benchmark TWR
    
    Uses composite benchmark for 'commodities', 'all', and 'mixed' portfolios,
    specific benchmarks for securities (VHVE) and crypto (BTC).
    
    Args:
        portfolio_twr: Portfolio's TWR percentage
        portfolio_type: 'securities', 'crypto', 'commodities', 'all', or 'mixed'
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Alpha as percentage (positive = outperformance)
    """
    benchmark_twr = get_benchmark_twr_for_portfolio_type(portfolio_type, start_date, end_date)
    
    if benchmark_twr is None:
        return None
    
    return round(portfolio_twr - benchmark_twr, 2)


# ============================================================================
# COMPOSITE BENCHMARK (Target Allocation with Rebalancing)
# ============================================================================

def get_composite_benchmark_normalized_series(start_date: date, end_date: date) -> list[tuple[date, float]]:
    """
    Get normalized series (rebased to 100) for composite benchmark.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        list[tuple[date, float]]: List of (date, normalized_value) tuples
    """
    try:
        from apps.core.helpers import prepare_composite_benchmark_data, calculate_rebalanced_benchmark
        
        db = SessionLocal()
        try:
            # Get target allocations from config
            config = load_app_config()
            benchmarks = config.get('benchmarks', {})
            target_allocations = benchmarks.get('target_allocations', {})
            rebalancing_period = benchmarks.get('rebalancing_period', 'monthly')
            
            if not target_allocations:
                logger.warning("No target_allocations configured for composite benchmark")
                return []
            
            # Prepare data
            market_df = prepare_composite_benchmark_data(db, start_date)
            if market_df.empty:
                return []
            
            # Calculate benchmark
            benchmark_series = calculate_rebalanced_benchmark(market_df, target_allocations, rebalancing_period)
            if benchmark_series.empty:
                return []
            
            # Convert index to date for comparison (handles timezone-aware datetime)
            benchmark_series.index = pd.to_datetime(benchmark_series.index).date
            
            # Filter to requested date range
            mask = (benchmark_series.index >= start_date) & (benchmark_series.index <= end_date)
            filtered_series = benchmark_series[mask]
            
            if len(filtered_series) < 2:
                logger.info(f"Insufficient data for composite benchmark: {len(filtered_series)} data points")
                return []
            
            # Normalize to 100 at start_date
            first_value = filtered_series.iloc[0]
            if first_value == 0:
                logger.warning("First value of composite benchmark is 0, cannot normalize")
                return []
            
            normalized_series = (filtered_series / first_value) * 100
            # Convert to list of tuples (idx is already a date object after conversion)
            result = [(idx if isinstance(idx, date) else idx.date(), float(val)) for idx, val in normalized_series.items()]
            logger.debug(f"Composite benchmark normalized series: {len(result)} data points from {result[0][0]} to {result[-1][0]}")
            return result
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Failed to get composite benchmark normalized series: {e}")
        return []


def calculate_composite_benchmark_twr(start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate TWR for composite benchmark.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        float: TWR as percentage, or None if data unavailable
    """
    try:
        from apps.core.helpers import prepare_composite_benchmark_data, calculate_rebalanced_benchmark
        
        db = SessionLocal()
        try:
            # Get target allocations from config
            config = load_app_config()
            benchmarks = config.get('benchmarks', {})
            target_allocations = benchmarks.get('target_allocations', {})
            rebalancing_period = benchmarks.get('rebalancing_period', 'monthly')
            
            if not target_allocations:
                return None
            
            # Prepare data
            market_df = prepare_composite_benchmark_data(db, start_date)
            if market_df.empty:
                return None
            
            # Calculate benchmark
            benchmark_series = calculate_rebalanced_benchmark(market_df, target_allocations, rebalancing_period)
            if benchmark_series.empty:
                return None
            
            # Convert index to date for comparison (handles timezone-aware datetime)
            benchmark_series.index = pd.to_datetime(benchmark_series.index).date
            
            # Filter to requested date range
            mask = (benchmark_series.index >= start_date) & (benchmark_series.index <= end_date)
            filtered_series = benchmark_series[mask]
            
            if len(filtered_series) < 2:
                logger.info(f"Insufficient data for composite benchmark TWR: {len(filtered_series)} data points")
                return None
            
            # Calculate TWR
            first_value = filtered_series.iloc[0]
            last_value = filtered_series.iloc[-1]
            
            if first_value <= 0:
                return None
            
            cumulative_growth_factor = last_value / first_value
            
            # Calculate number of days
            days = (end_date - start_date).days
            if days <= 0:
                return None
            
            # Annualize the TWR
            years = days / 365.25
            if years > 0 and cumulative_growth_factor > 0:
                annualized_twr = (pow(cumulative_growth_factor, 1.0 / years) - 1.0) * 100
                return round(annualized_twr, 2)
            
            return None
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Failed to calculate composite benchmark TWR: {e}")
        return None


def calculate_composite_benchmark_volatility(start_date: date, end_date: date) -> Optional[float]:
    """
    Calculate annualized volatility for composite benchmark.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        float: Annualized volatility as percentage, or None if insufficient data
    """
    try:
        from apps.core.helpers import prepare_composite_benchmark_data, calculate_rebalanced_benchmark
        
        db = SessionLocal()
        try:
            # Get target allocations from config
            config = load_app_config()
            benchmarks = config.get('benchmarks', {})
            target_allocations = benchmarks.get('target_allocations', {})
            rebalancing_period = benchmarks.get('rebalancing_period', 'monthly')
            
            if not target_allocations:
                return None
            
            # Prepare data
            market_df = prepare_composite_benchmark_data(db, start_date)
            if market_df.empty:
                return None
            
            # Calculate benchmark
            benchmark_series = calculate_rebalanced_benchmark(market_df, target_allocations, rebalancing_period)
            if benchmark_series.empty:
                return None
            
            
            # Convert index to date for comparison (handles timezone-aware datetime)
            benchmark_series.index = pd.to_datetime(benchmark_series.index).date
            
            # Filter to requested date range
            mask = (benchmark_series.index >= start_date) & (benchmark_series.index <= end_date)
            filtered_series = benchmark_series[mask]
            filtered_series = benchmark_series[mask]
            
            
            if len(filtered_series) < 5:
                logger.info(f"Insufficient data for composite benchmark volatility: {len(filtered_series)} data points")
                return None
            
            # Calculate daily returns
            daily_returns = []
            for i in range(1, len(filtered_series)):
                prev_value = filtered_series.iloc[i-1]
                curr_value = filtered_series.iloc[i]
                
                if prev_value > 0:
                    daily_return = (curr_value / prev_value) - 1
                    daily_returns.append(daily_return)
            
            if len(daily_returns) < 5:
                return None
            
            # Calculate standard deviation
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
            std_dev = math.sqrt(variance)
            
            # Annualize: multiply by sqrt(252) for trading days
            annualized_volatility = std_dev * math.sqrt(252) * 100
            
            return round(annualized_volatility, 2)
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Failed to calculate composite benchmark volatility: {e}")
        return None


