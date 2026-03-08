import os
from enum import Enum
from sqlalchemy import (
    create_engine,
    func,
    Column,
    Integer,
    BigInteger,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    Numeric,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    ForeignKey,
    CheckConstraint,
    text,
    event,
    Index,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from database import Base

# ---- Transaction Type Enum ----

class TransactionType(str, Enum):
    """Transaction types used throughout the application."""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BUY = "buy"
    SELL = "sell"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    FEE = "fee"
    DIVIDEND = "dividend"
    DIVIDEND_REINVEST = "dividend_reinvest"
    INTEREST = "interest"
    STAKING_REWARD = "staking_reward"
    STAKING_LOSS = "staking_loss"
    STAKING = "staking"
    WITHHOLDING_TAX = "withholding_tax"
    OPENING_BALANCE = "opening_balance"
    PORTFOLIO_TRANSFER = "portfolio_transfer"
    EXCHANGE = "exchange"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    SPAM = "spam"
    
    @classmethod
    def values(cls):
        """Return list of all transaction type values."""
        return [member.value for member in cls]
    
    @classmethod
    def constraint_string(cls):
        """Generate SQL constraint string for CheckConstraint."""
        types_str = "', '".join(cls.values())
        return f"(type IN ('{types_str}'))"

# ---- ORM Models ----

class Portfolio(Base):
    """
    Portfolio groups related transactions, positions, and snapshots.
    
    Naming conventions:
    - currency_base: The portfolio's base currency for P&L calculations (e.g., EUR)
    """
    __tablename__ = "portfolios"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)                # General Cash (only include in Snapshots), Cash Pool, Securities, Crypto, Gold, Kbank Mutual Funds
    currency_base = Column(String, nullable=False)       # Portfolio base currency (set from app_config.yaml) - NO DEFAULT, must be explicit
    description = Column(Text)
    status = Column(String, default='active', nullable=False)  # 'active' or 'closed'
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    transactions = relationship("Transaction", back_populates="portfolio", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="portfolio", cascade="all, delete-orphan")
    snapshots = relationship("Snapshot", back_populates="portfolio", cascade="all, delete-orphan")
    # Removed accounts relationship - use transactions to link portfolios to accounts

class Account(Base):
    """
    Account represents a financial account (exchange, wallet, broker, bank).
    
    Naming conventions:
    - currency_native: The account's default/native currency (e.g., USD for IBKR)
    - status: 'active' or 'closed' (synced from accounts_config.yaml)
    """
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    # Removed portfolio_id - accounts can serve multiple portfolios via transactions
    name = Column(Text, nullable=False)  # e.g. 'Binance', 'Ledger Nano X', 'Kraken'
    type = Column(Text, nullable=True)   # 'exchange', 'hardware_wallet', 'broker', 'bank'
    currency_native = Column(Text, nullable=True)  # account's default currency (e.g. USD, EUR, THB)
    status = Column(String, default='active', nullable=False)  # 'active' or 'closed'
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    # Removed portfolio relationship - use transactions to link accounts to portfolios
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")
    crypto_wallets = relationship("CryptoWallet", back_populates="account", cascade="all, delete-orphan")

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(BigInteger, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'))
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=False)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=True)
    type = Column(
        String,
        CheckConstraint(
            TransactionType.constraint_string(),
            name="ck_transaction_type"
        ),
        nullable=False
    )
    symbol = Column(String, nullable=True)                                  # symbol as of API, eg. "BRK.B", "THBUSD=X"
    quantity = Column(Numeric(precision=24, scale=8), nullable=True)        # asset units (+ for in, - for out)
    value_native = Column(Numeric(precision=24, scale=8), nullable=True)    # fiat/cash value in `currency_native`
    currency_native = Column(String, nullable=True)                         # currency of `value_native` and `price`, e.g. USD/THB
    price = Column(Numeric(precision=24, scale=8), nullable=True)           # price per unit in `currency_native`
    fee = Column(Numeric(precision=24, scale=8), nullable=True, default=0)  # fee amount in `fee_currency`
    fee_currency = Column(String, nullable=True)                            # currency of fee (may differ from currency_native)
    exchange_rate_to_base = Column(Numeric(precision=24, scale=8), nullable=True)
    value_base = Column(Numeric(precision=24, scale=8), nullable=True)      # value in portfolio base currency (currency_base)
    currency_base = Column(String(8), nullable=False)                       # base currency at time of transaction - NO DEFAULT, must be set by application
    lot_id = Column(String, nullable=True)
    category = Column(String, nullable=True)                                # 'income', 'external_transfer', 'internal_transfer', 'trade', 'fee', 'tax'
    source = Column(String, nullable=True)                                  # 'BinanceTH', 'IBKR', 'BinanceCOM', 'GoldtradersTH', 'manual', 'data_migration' etc.
    external_id = Column(String, nullable=True)                             # id from external system, e.g. IBKR tradeID   
    notes = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)          # when transaction was edited/reviewed
    asset_class = Column(String, nullable=True)                             # 'stock', 'etf', 'etc', 'gold_baht', 'crypto', 'cash'
    symbol_normalized = Column(String, nullable=True)                       # symbol for app use, eg. "BRK-B", "THBUSD"
    
    # German tax compliance - ISIN tracking (§ 20 EStG requires FIFO per ISIN)
    isin = Column(String(12), nullable=True)                                # International Securities Identification Number (12 chars, NULL for currencies/crypto)
    conid = Column(String(50), nullable=True)                               # IBKR Contract ID for reference
    
    # Data quality control
    reviewed = Column(Boolean, default=False, nullable=False)               # manual review flag for data quality control
    
    # German tax compliance (withholding tax)
    withholding_tax = Column(Numeric(precision=24, scale=8), nullable=True) # tax withheld at source (e.g., dividends)
    withholding_tax_currency = Column(String(8), nullable=True)             # currency of withholding tax
    withholding_tax_country = Column(String(8), nullable=True)              # country that withheld tax (e.g., 'US', 'DE')

    # wallet references and blockchain info
    from_crypto_wallet_id = Column(Integer, ForeignKey("crypto_wallets.id"), nullable=True)
    to_crypto_wallet_id = Column(Integer, ForeignKey("crypto_wallets.id"), nullable=True)
    blockchain_tx_hash = Column(String, nullable=True)
    blockchain_fee = Column(Numeric(precision=24, scale=8), nullable=True)
    crypto_transfer_link_id = Column(Integer, ForeignKey("crypto_transfer_links.id"), nullable=True)

    # Optional fields:
    # trade_date = Column(Date, nullable=True)                                # Separate from occurred_at for settlement tracking
    # settlement_date = Column(Date, nullable=True)                           # For T+2 settlement in stocks
    # dividend_ex_date = Column(Date, nullable=True)                          # For dividend tracking
    # dividend_pay_date = Column(Date, nullable=True)                         # For dividend tracking
    
    # Enhanced crypto fields:
    # gas_used = Column(Numeric(precision=24, scale=8), nullable=True)        # For ETH transactions
    # gas_price = Column(Numeric(precision=24, scale=8), nullable=True)       # For ETH transactions
    
    # Enhanced categorization:
    # subcategory = Column(String, nullable=True)                             # E.g., 'qualified_dividend', 'crypto_mining', 'staking_reward'
    
    # Tax optimization:
    # is_wash_sale = Column(Boolean, default=False, nullable=True)            # For tax loss harvesting
    # related_transaction_id = Column(BigInteger, ForeignKey('transactions.id'), nullable=True)  # Link related transactions

    portfolio = relationship("Portfolio", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
    crypto_transfer_link = relationship("CryptoTransferLink", back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_portfolio_occurred", "portfolio_id", "occurred_at"),
        Index("ix_transactions_account", "account_id"),
        Index("ix_transactions_symbol", "symbol"),
        Index("ix_transactions_from_wallet", "from_crypto_wallet_id"),
        Index("ix_transactions_to_wallet", "to_crypto_wallet_id"),
        Index("ix_transactions_txhash", "blockchain_tx_hash"),
        Index("ix_transactions_link", "crypto_transfer_link_id"),
    )

class MarketData(Base):
    """
    Stores historical market prices for securities, crypto, and commodities.
    
    Naming conventions:
    - as_of_date: The date/time this price applies to (not when recorded)
    - currency: The currency the price is denominated in (e.g., USD, EUR)
    """
    __tablename__ = "market_data"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    as_of_date = Column(TIMESTAMP(timezone=True), nullable=False)  # When the price applies (market close time)
    price = Column(Numeric(precision=24, scale=8), nullable=False)
    currency = Column(String, nullable=False)  # currency of the price (e.g. USD)
    source = Column(String, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint('symbol', 'as_of_date', 'currency', name='uc_marketdata_symbol_ts_currency'),
        Index("ix_marketdata_symbol_ts", "symbol", "as_of_date"),
    )

class FxRate(Base):
    """
    Stores historical FX rates between currency pairs.
    
    Naming conventions:
    - as_of_date: The date/time this rate applies to (not when recorded)
    - base_currency/quote_currency: Standard FX convention (base_currency is OK here)
    """
    __tablename__ = "fx_rates"
    id = Column(Integer, primary_key=True)
    pair = Column(String(16), nullable=False)      # canonical pair e.g. 'USD/THB' or 'USDTHB'
    as_of_date = Column(TIMESTAMP(timezone=True), nullable=False)  # When the rate applies
    rate = Column(Numeric, nullable=False)         # multiplier: 1 unit of base (USD) -> rate units of quote (THB)
    base_currency = Column(String(8), nullable=False)   # e.g. 'USD' (standard FX naming)
    quote_currency = Column(String(8), nullable=False)  # e.g. 'THB'
    source = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint('pair', 'as_of_date', name='uc_fx_pair_ts'),
        Index("ix_fx_pair_ts", "pair", "as_of_date"),
    )

class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'))
    symbol = Column(String, nullable=False)
    quantity = Column(Numeric(precision=24, scale=8), nullable=False)
    cost_basis_base = Column(Numeric(precision=24, scale=8), nullable=False)  # in base currency
    currency_base = Column(String(8), nullable=False)   # base currency for cost_basis_base - NO DEFAULT, must be set by application
    avg_price_base = Column(Numeric(precision=24, scale=8), nullable=True)
    cost_basis_native = Column(Numeric(precision=24, scale=8), nullable=True)   # in original/native currency
    currency_native = Column(String, nullable=True)      
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)
    asset_class = Column(String, nullable=True)
    symbol_normalized = Column(String, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    portfolio = relationship("Portfolio", back_populates="positions")

    __table_args__ = (
        Index("ix_positions_portfolio_symbol", "portfolio_id", "symbol"),
        Index("ix_positions_portfolio_qty", "portfolio_id", "quantity"),  # Fast filtering by portfolio + active positions
        Index("ix_positions_symbol", "symbol"),  # Fast lookups by symbol across portfolios
    )

class Snapshot(Base):
    """
    Daily portfolio snapshot storing values and P&L at a point in time.
    
    Naming conventions:
    - currency_base: The base currency for all _base amounts
    - *_base: Amounts denominated in base currency
    
    CRITICAL DISTINCTION:
    - total_invested_base: COST BASIS (sum of cost_basis from open positions/lots)
      This is the accounting cost of currently held assets.
    - Net Invested Capital: deposits_base - withdrawals_base (for display/analysis)
      This is the actual cash flow into/out of the portfolio.
      Use calculate_net_invested_capital() helper for this calculation.
    
    Example: If you deposit $1,000, then withdraw positions worth $400 (cost basis $300):
    - total_invested_base = $700 (cost basis of remaining positions)
    - Net Invested Capital = $600 (deposits $1,000 - withdrawals $400)
    """
    __tablename__ = "snapshots"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'))
    snapshot_date = Column(Date, nullable=False)
    total_value_base = Column(Numeric(precision=24, scale=8), nullable=False)
    currency_base = Column(String(8), nullable=False)  # base currency at time of snapshot - NO DEFAULT, must be set by application
    total_invested_base = Column(Numeric(precision=24, scale=8), nullable=False)  # COST BASIS (not net invested capital!)
    realized_pnl_base = Column(Numeric(precision=24, scale=8), nullable=False)
    unrealized_pnl_base = Column(Numeric(precision=24, scale=8), nullable=False)
    deposits_base = Column(Numeric(precision=24, scale=8), default=0)
    withdrawals_base = Column(Numeric(precision=24, scale=8), default=0)
    
    # NAV (Net Asset Value) tracking for normalized portfolio performance comparison
    # Allows comparing portfolios of different sizes by tracking unit price growth
    # Starting price is 100.0 on portfolio inception, units are calculated based on cash flows
    nav_units = Column(Numeric(precision=24, scale=8), nullable=True)   # Number of "units" in the portfolio
    nav_price = Column(Numeric(precision=24, scale=8), nullable=True)   # Price per unit (starts at 100.0)
    
    notes = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    portfolio = relationship("Portfolio", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint('portfolio_id', 'snapshot_date', name='uc_snapshot_portfolio_date'),
        Index("ix_snapshots_portfolio_date", "portfolio_id", "snapshot_date"),  # Fast date-based queries
    )

class Lot(Base):
    """
    Tracks individual acquisition lots for FIFO cost basis calculation.
    German tax law (§ 20 EStG) requires FIFO per ISIN globally across all accounts.
    
    Naming conventions:
    - _base suffix: Amount in portfolio base currency (EUR)
    - _native suffix: Amount in original transaction currency
    - total_cost_basis: Includes acquisition fees (per tax compliance)
    """
    __tablename__ = "lots"
    
    # Primary identification
    lot_id = Column(String, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    transaction_id = Column(BigInteger, ForeignKey('transactions.id'), nullable=True)  # Link to source transaction
    
    # Asset identification
    symbol = Column(String, nullable=False)
    isin = Column(String(12), nullable=True)                                # International Securities Identification Number (NULL for currencies/crypto)
    conid = Column(String(50), nullable=True)                               # IBKR Contract ID for reference
    
    # Acquisition details
    acquired_at = Column(TIMESTAMP(timezone=True), nullable=False)          # When the lot was acquired
    quantity = Column(Numeric(precision=24, scale=8), nullable=False)       # Original quantity acquired
    remaining_quantity = Column(Numeric(precision=24, scale=8), nullable=False)  # Quantity still held
    
    # Cost basis - Base currency (for P&L calculations)
    total_cost_basis_base = Column(Numeric(precision=24, scale=8), nullable=False)  # Total cost including fees
    fee_base = Column(Numeric(precision=24, scale=8), nullable=True)        # Acquisition fee in base currency
    currency_base = Column(String(8), nullable=False)                       # Base currency code (e.g., EUR)
    
    # Cost basis - Native currency (for audit trail)
    total_cost_basis_native = Column(Numeric(precision=24, scale=8), nullable=True)  # Total cost in native currency
    fee_native = Column(Numeric(precision=24, scale=8), nullable=True)      # Acquisition fee in native currency
    currency_native = Column(String(8), nullable=True)                      # Native currency code (e.g., USD)
    
    # Audit trail
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    portfolio = relationship("Portfolio", backref="lots")
    source_transaction = relationship("Transaction", foreign_keys=[transaction_id])

    __table_args__ = (
        Index("ix_lots_portfolio_symbol_acquired", "portfolio_id", "symbol", "acquired_at"),
        Index("ix_lots_isin_acquired", "isin", "acquired_at"),              # For global FIFO by ISIN
        Index("ix_lots_symbol_acquired", "symbol", "acquired_at"),          # For global FIFO by symbol (currencies/crypto)
    )


class LotAllocation(Base):
    """
    Tracks how sell/disposal transactions are allocated to acquisition lots.
    Stores realized gain/loss for each allocation for accurate P&L and tax reporting.
    
    Naming conventions:
    - _base suffix: Amount in portfolio base currency
    - allocated_: Refers to the portion of the lot being disposed
    - sale_: Refers to the disposal transaction proceeds/fees
    """
    __tablename__ = "lot_allocations"
    
    id = Column(BigInteger, primary_key=True)
    transaction_id = Column(BigInteger, ForeignKey('transactions.id'), nullable=False)  # The sell/disposal transaction
    lot_id = Column(String, ForeignKey('lots.lot_id'), nullable=False)      # The acquisition lot used
    
    # Allocation details
    allocated_at = Column(TIMESTAMP(timezone=True), nullable=True)          # When the allocation was made
    allocated_quantity = Column(Numeric(precision=24, scale=8), nullable=False)  # Quantity disposed from this lot
    
    # Cost basis of disposed portion
    allocated_cost_basis_base = Column(Numeric(precision=24, scale=8), nullable=False)  # Cost basis for allocated qty
    
    # Sale proceeds and fees - Base currency
    gross_proceeds_base = Column(Numeric(precision=24, scale=8), nullable=False)  # Sale proceeds before fees
    sale_fee_base = Column(Numeric(precision=24, scale=8), nullable=True)   # Disposal/sale fee
    net_proceeds_base = Column(Numeric(precision=24, scale=8), nullable=False)  # Gross proceeds minus sale fee
    
    # Sale proceeds and fees - Native currency (for audit trail)
    gross_proceeds_native = Column(Numeric(precision=24, scale=8), nullable=True)  # Sale proceeds before fees in native currency
    sale_fee_native = Column(Numeric(precision=24, scale=8), nullable=True)   # Disposal/sale fee in native currency
    net_proceeds_native = Column(Numeric(precision=24, scale=8), nullable=True)  # Gross proceeds minus sale fee in native currency
    currency_native = Column(String(8), nullable=True)                      # Native currency code
    
    # Realized P&L
    realized_gain_base = Column(Numeric(precision=24, scale=8), nullable=False)  # Net proceeds - cost basis
    currency_base = Column(String(8), nullable=False)                       # Base currency code
    
    # Audit trail
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)
    
    transaction = relationship("Transaction", backref="lot_allocations")
    lot = relationship("Lot", backref="allocations")
    
    __table_args__ = (
        Index("ix_lot_allocations_transaction", "transaction_id"),
        Index("ix_lot_allocations_lot", "lot_id"),
    )

class CryptoWallet(Base):
    __tablename__ = "crypto_wallets"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)  # Ledger Nano X
    symbol = Column(String, nullable=False)        # 'BTC', 'ETH', 'BNB', 'SOL', 'ADA', 'XRP'
    address = Column(String, nullable=False)       # Address or extended public key (xpub/ypub/zpub for BTC)
    gap_limit = Column(Integer, default=20)        # Max unused addresses to check (for xpub derivation)
    
    label = Column(String, nullable=True)          # friendly name like 'BTC addr #12'
    notes = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)  # Last transaction sync timestamp

    account = relationship("Account", back_populates="crypto_wallets")
    crypto_balances = relationship("CryptoBalance", back_populates="crypto_wallet", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_crypto_wallets_address", "address"),
        Index("ix_crypto_wallets_symbol", "symbol"),
    )

class CryptoBalance(Base):
    """
    Stores CURRENT crypto balance/staking allocation for wallets.
    
    Design:
    - One record per wallet_id/symbol/balance_type combination
    - UPSERT semantics: updated on each sync (no historical accumulation)
    - Provides staking breakdown: liquid, staked, activating, deactivating, etc.
    - as_of_date: When this balance was last fetched from blockchain
    """
    __tablename__ = "crypto_balances"
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("crypto_wallets.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # For exchange accounts
    symbol = Column(String, nullable=False)
    balance_type = Column(String, nullable=False)  # 'liquid', 'staked', 'activating', 'deactivating', etc.
    balance = Column(Numeric(precision=24, scale=8), nullable=False)
    as_of_date = Column(TIMESTAMP(timezone=True), nullable=False)  # When this balance was fetched
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    crypto_wallet = relationship("CryptoWallet", back_populates="crypto_balances")
    account = relationship("Account")

    __table_args__ = (
        # Ensure exactly one of wallet_id or account_id is set
        CheckConstraint(
            '(wallet_id IS NOT NULL AND account_id IS NULL) OR (wallet_id IS NULL AND account_id IS NOT NULL)',
            name='ck_wallet_or_account'
        ),
        # One record per wallet/symbol/balance_type (no timestamp in constraint)
        UniqueConstraint('wallet_id', 'symbol', 'balance_type', name='uc_wallet_symbol_type'),
        UniqueConstraint('account_id', 'symbol', 'balance_type', name='uc_account_symbol_type'),
        Index("ix_crypto_balances_wallet_symbol", "wallet_id", "symbol"),
        Index("ix_crypto_balances_account_symbol", "account_id", "symbol"),
    )

class CryptoTransferLink(Base):
    """Links related crypto transfer transactions (e.g., transfer_out and transfer_in)."""
    __tablename__ = "crypto_transfer_links"
    id = Column(Integer, primary_key=True)
    tx_hash = Column(String, nullable=True, index=True)
    source = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), nullable=True)

    transactions = relationship("Transaction", back_populates="crypto_transfer_link", cascade="all, delete-orphan")


class CryptoStakingTransaction(Base):
    """
    Stores blockchain staking transaction hashes for chains where APIs don't provide transaction lists.
    
    Purpose:
    - Store staking tx hashes (delegate, undelegate, claim)
    - System fetches tx details via RPC (eth_getTransactionByHash, eth_getTransactionReceipt)
    - Calculate staked balance and rewards from transaction history
    
    Use case: BNB staking where BscScan API doesn't work
    - User provides tx hashes (one-time setup or as they stake)
    - System automatically parses amounts and calculates:
      * Current staked = sum(delegates) - sum(undelegates)
      * Rewards = claimed - undelegated (claim contains undelegated + rewards)
    
    Example workflow:
    1. User delegates 1.4 BNB → Add delegate tx hash
    2. User undelegates 0.6 BNB → Add undelegate tx hash (starts unstaking)
    3. After 7 days, user claims → Add claim tx hash (receives 0.606 BNB = 0.6 + 0.006 rewards)
    4. System calculates: Still staked = 1.4 - 0.6 = 0.8 BNB, Rewards = 0.606 - 0.6 = 0.006 BNB
    """
    __tablename__ = "crypto_staking_transactions"
    
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("crypto_wallets.id"), nullable=False)
    tx_hash = Column(String, nullable=False, unique=True, index=True)  # Blockchain transaction hash
    tx_type = Column(String, nullable=False)  # 'delegate', 'undelegate', 'claim'
    symbol = Column(String, nullable=False)  # 'BNB', 'SOL', etc.
    amount = Column(Numeric(precision=24, scale=8), nullable=True)  # Parsed from transaction (can be NULL until processed)
    validator_address = Column(String, nullable=True)  # Validator/pool address
    block_number = Column(BigInteger, nullable=True)  # Block number
    processed_at = Column(TIMESTAMP(timezone=True), nullable=True)  # When tx was fetched and parsed
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # When tx was added to DB
    
    # New fields for accurate reward calculation (added Nov 30, 2025)
    staked_balance_snapshot = Column(Numeric(precision=24, scale=8), nullable=True)  # Total staked principal at undelegate time
    accumulated_rewards_snapshot = Column(Numeric(precision=24, scale=8), nullable=True)  # Accumulated rewards at undelegate time
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=True)  # Blockchain transaction timestamp (from block data)
    linked_tx_hash = Column(String, nullable=True)  # For claim: links to undelegate tx_hash to get reward percentage
    
    crypto_wallet = relationship("CryptoWallet")
    
    __table_args__ = (
        Index("ix_crypto_staking_txs_wallet_symbol", "wallet_id", "symbol"),
        Index("ix_crypto_staking_txs_type", "tx_type"),
        Index("ix_crypto_staking_txs_linked", "linked_tx_hash"),
        Index("ix_crypto_staking_txs_occurred_at", "occurred_at"),
    )


class SymbolMapping(Base):
    """
    Maps trading symbols to their market data providers and currencies.
    Auto-populated from:
    - IBKR transactions (currency from Flex Query)
    - Crypto symbols (default USD)
    - Manual config (symbol_mapping.yaml)
    
    Uses (symbol, currency) composite unique constraint to support:
    - Same symbol on different exchanges with same currency
      Example: VUAA on XETRA and Amsterdam, both EUR → one price source is enough
    - Same symbol in different currencies
      Example: BTC in USD vs BTC in EUR
    """
    __tablename__ = "symbol_mappings"
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)  # NOT unique alone
    yfinance_symbol = Column(String, nullable=True)  # e.g., "VWCE.DE" for XETRA
    currency = Column(String(8), nullable=False)  # Price currency (USD, EUR, THB, etc.)
    exchange = Column(String, nullable=True)  # Exchange name (optional, can be NULL if price is same across exchanges)
    provider = Column(String, nullable=True)  # yfinance, coinmarketcap, goldtraders_scraper
    description = Column(String, nullable=True)  # Human-readable description
    auto_detected = Column(Boolean, default=False)  # True if auto-populated, False if manual
    last_seen = Column(TIMESTAMP(timezone=True), nullable=True)  # Last time this symbol appeared in transactions
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    last_updated = Column(TIMESTAMP(timezone=True), onupdate=func.now())
    
    __table_args__ = (
        # CRITICAL: (symbol, currency) composite unique constraint
        # Allows VUAA on XETRA and Amsterdam (both EUR) to use one mapping
        # Also allows BTC-USD and BTC-EUR as separate mappings
        UniqueConstraint('symbol', 'currency', name='uc_symbol_currency'),
        Index("ix_symbol_mappings_symbol", "symbol"),
        Index("ix_symbol_mappings_currency", "currency"),
        Index("ix_symbol_mappings_symbol_currency", "symbol", "currency"),
    )


class CashPosition(Base):
    """
    Cash Position - Manual cash holdings tracker for Portfolios 1 (Liquid Cash) and 2 (Fixed Deposits)
    
    Purpose:
    - Store individual cash positions (e.g., "Bank A Savings", "Bank B Fixed Deposit 12m")
    - Track historical values (don't lose old entries when updating)
    - Support multiple currencies
    - Provide basis for monthly snapshot creation
    
    Workflow:
    1. User opens Cash Manager app monthly
    2. Pre-filled with last known values
    3. User edits amounts/adds new positions
    4. Click "Save & Create Snapshot":
       - Updates all CashPosition records
       - Creates Snapshot for both portfolios
       - Forward-fills from last snapshot to today
    
    Example positions:
    - Portfolio 1 (Liquid Cash): Bank A Savings, Fintech EUR, Bank B Savings
    - Portfolio 2 (Fixed Deposits): Bank B Fixed 12m, Work Savings Plan
    """
    __tablename__ = "cash_positions"
    
    id = Column(BigInteger, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    label = Column(String(200), nullable=False)  # e.g., "Bank Savings", "Fixed Deposit"
    amount = Column(Numeric(20, 2), nullable=False)  # Current value
    currency = Column(String(10), nullable=False)  # THB, EUR, USD, etc.
    notes = Column(Text, nullable=True)  # Optional notes
    last_updated = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    # Relationship
    portfolio = relationship("Portfolio")
    
    __table_args__ = (
        Index("ix_cash_positions_portfolio_id", "portfolio_id"),
    )


# =============================================================================
# PRE-COMPUTED CACHE TABLES
# =============================================================================
# These tables store pre-computed portfolio data for instant dashboard loading.
# Data is computed once daily after sync jobs complete.
# See docs/BACKGROUND_PRECOMPUTATION_IMPLEMENTATION.md for details.

class PortfolioSummaryCache(Base):
    """
    Stores pre-computed portfolio summaries for fast retrieval.
    
    Updated daily after sync jobs complete. Dashboard reads from cache
    instead of computing expensive aggregations on every page load.
    Includes KPI metrics (twr, xirr, mdd) for instant dashboard rendering.
    """
    __tablename__ = "portfolio_summary_cache"
    
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True)  # NULL for "All Portfolios"
    
    # Summary Data
    total_value = Column(Numeric(20, 2), nullable=False)
    total_invested = Column(Numeric(20, 2), nullable=False)
    total_pnl = Column(Numeric(20, 2), nullable=False)
    realized_pnl = Column(Numeric(20, 2), nullable=True)
    unrealized_pnl = Column(Numeric(20, 2), nullable=True)
    overall_return_pct = Column(Numeric(10, 4), nullable=False)
    currency_base = Column(String(8), nullable=False)
    
    # HPR Changes (Holding Period Return)
    hpr_7d = Column(Numeric(10, 4), nullable=True)
    hpr_30d = Column(Numeric(10, 4), nullable=True)
    hpr_365d = Column(Numeric(10, 4), nullable=True)
    
    # KPI Metrics (lifetime performance)
    twr = Column(Numeric(10, 4), nullable=True)  # Time-Weighted Return %
    xirr = Column(Numeric(10, 4), nullable=True)  # Internal Rate of Return %
    mdd = Column(Numeric(10, 4), nullable=True)  # Maximum Drawdown %
    years_active = Column(Numeric(6, 2), nullable=True)  # Years since first snapshot
    first_snapshot_date = Column(Date, nullable=True)  # Date of first snapshot for this portfolio
    
    # Metadata
    snapshot_date = Column(Date, nullable=False)
    computed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('portfolio_id', 'snapshot_date', name='uc_portfolio_summary_cache'),
        Index("ix_portfolio_summary_cache_lookup", "portfolio_id", "snapshot_date"),
    )


class PeriodStatisticsCache(Base):
    """
    Stores pre-computed period statistics (3M, 6M, 1Y, 3Y, 5Y, All).
    
    Period-specific metrics like TWR, XIRR, volatility, Sharpe ratio are
    expensive to compute. Cache them for instant retrieval.
    Includes benchmark comparison (benchmark_twr, alpha) for performance context.
    """
    __tablename__ = "period_statistics_cache"
    
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True)  # NULL for "All Portfolios"
    period_key = Column(String(10), nullable=False)  # '3m', '6m', '1y', '3y', '5y', 'all'
    
    # Period Statistics
    value_change = Column(Numeric(20, 2), nullable=False)
    value_change_pct = Column(Numeric(10, 4), nullable=False)
    invested_change = Column(Numeric(20, 2), nullable=False)
    invested_change_pct = Column(Numeric(10, 4), nullable=False)
    pnl_change = Column(Numeric(20, 2), nullable=False)
    pnl_change_pct = Column(Numeric(10, 4), nullable=False)
    
    # TWR Performance
    twr_return = Column(Numeric(10, 4), nullable=True)
    twr_annualized = Column(Numeric(10, 4), nullable=True)
    xirr = Column(Numeric(10, 4), nullable=True)
    volatility = Column(Numeric(10, 4), nullable=True)
    sharpe_ratio = Column(Numeric(10, 4), nullable=True)
    max_drawdown = Column(Numeric(10, 4), nullable=True)
    
    # Benchmark Comparison
    benchmark_twr = Column(Numeric(10, 4), nullable=True)  # Benchmark TWR for same period
    alpha = Column(Numeric(10, 4), nullable=True)  # Portfolio TWR - Benchmark TWR
    benchmark_symbol = Column(String(20), nullable=True)  # e.g., 'VHVE', 'BTC', 'composite'
    
    # Date Range
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    
    # Metadata
    computed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('portfolio_id', 'period_key', 'end_date', name='uc_period_stats_cache'),
        Index("ix_period_stats_lookup", "portfolio_id", "period_key", "end_date"),
    )


class ChartDataCache(Base):
    """
    Stores pre-computed chart data as JSON for instant rendering.
    
    Plotly figures are serialized to JSON and stored in the database.
    This allows the dashboard to render charts instantly without
    querying and processing historical data.
    """
    __tablename__ = "chart_data_cache"
    
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True)  # NULL for "All Portfolios"
    chart_type = Column(String(50), nullable=False)  # 'performance', 'growth_comparison', etc.
    period_key = Column(String(10), nullable=False)  # '3m', '6m', '1y', '3y', '5y', 'all'
    
    # Chart Data (JSON) - Using Text instead of JSONB for broader compatibility
    chart_json = Column(Text, nullable=False)
    
    # Metadata
    computed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('portfolio_id', 'chart_type', 'period_key', name='uc_chart_data_cache'),
        Index("ix_chart_data_lookup", "portfolio_id", "chart_type", "period_key"),
    )


class PositionCache(Base):
    """
    Stores pre-computed position data with enriched prices for fast retrieval.
    
    Eliminates expensive Position + MarketData + FX queries during dashboard load.
    Each position includes its current price already converted to portfolio base currency.
    Updated daily by precomputation service after market data sync.
    """
    __tablename__ = "position_cache"
    
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    
    # Position Data
    symbol = Column(String(50), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    current_price = Column(Numeric(20, 8), nullable=True)  # In portfolio base currency
    value = Column(Numeric(20, 2), nullable=True)  # quantity × current_price (in base currency)
    
    # Metadata for transparency
    price_currency = Column(String(8), nullable=True)  # Original currency of the price source
    price_source = Column(String(50), nullable=True)  # 'market_data', 'fx_rate', 'manual', etc.
    is_cash_position = Column(Boolean, default=False)  # Flag for CashPosition entries
    currency = Column(String(8), nullable=True)  # For cash positions: the currency held
    
    # Timestamps
    snapshot_date = Column(Date, nullable=False)
    computed_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('portfolio_id', 'symbol', 'snapshot_date', name='uc_position_cache'),
        Index("ix_position_cache_lookup", "portfolio_id", "snapshot_date"),
        Index("ix_position_cache_symbol", "symbol"),
    )


# ---- End of models.py ----
