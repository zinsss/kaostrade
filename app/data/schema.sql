CREATE TABLE IF NOT EXISTS markets (
    market TEXT PRIMARY KEY,
    korean_name TEXT,
    english_name TEXT,
    market_warning TEXT,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticker_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    bithumb_timestamp INTEGER,
    trade_timestamp INTEGER,
    opening_price REAL,
    high_price REAL,
    low_price REAL,
    trade_price REAL,
    prev_closing_price REAL,
    change TEXT,
    change_price REAL,
    change_rate REAL,
    signed_change_price REAL,
    signed_change_rate REAL,
    trade_volume REAL,
    acc_trade_price REAL,
    acc_trade_price_24h REAL,
    acc_trade_volume REAL,
    acc_trade_volume_24h REAL,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (market) REFERENCES markets (market)
);

CREATE INDEX IF NOT EXISTS idx_ticker_snapshots_market_collected_at
ON ticker_snapshots (market, collected_at);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    bithumb_timestamp INTEGER,
    total_ask_size REAL,
    total_bid_size REAL,
    best_ask_price REAL,
    best_bid_price REAL,
    spread_pct REAL,
    ask_depth_5 REAL,
    bid_depth_5 REAL,
    imbalance_5 REAL,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (market) REFERENCES markets (market)
);

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_market_collected_at
ON orderbook_snapshots (market, collected_at);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    interval TEXT NOT NULL,
    candle_date_time_utc TEXT NOT NULL,
    candle_date_time_kst TEXT,
    opening_price REAL,
    high_price REAL,
    low_price REAL,
    trade_price REAL,
    candle_acc_trade_price REAL,
    candle_acc_trade_volume REAL,
    timestamp INTEGER,
    raw_json TEXT NOT NULL,
    UNIQUE (market, interval, candle_date_time_utc),
    FOREIGN KEY (market) REFERENCES markets (market)
);

CREATE INDEX IF NOT EXISTS idx_candles_market_interval_time
ON candles (market, interval, candle_date_time_utc);

CREATE TABLE IF NOT EXISTS market_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',
    btc_return_1h REAL,
    eth_return_1h REAL,
    median_return_1h REAL,
    positive_ratio REAL,
    average_spread_pct REAL,
    average_imbalance_5 REAL,
    market_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_market_features_ts
ON market_features (ts);

CREATE TABLE IF NOT EXISTS market_regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',
    regime TEXT NOT NULL,
    reason TEXT NOT NULL,
    market_features_id INTEGER,
    btc_return_1h REAL,
    eth_return_1h REAL,
    median_return_1h REAL,
    positive_ratio REAL,
    average_spread_pct REAL,
    average_imbalance_5 REAL,
    market_count INTEGER,
    FOREIGN KEY (market_features_id) REFERENCES market_features (id)
);

CREATE INDEX IF NOT EXISTS idx_market_regimes_ts
ON market_regimes (ts);

CREATE TABLE IF NOT EXISTS paper_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    cash_krw REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    market TEXT NOT NULL,
    quantity REAL NOT NULL,
    average_entry_price REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, market),
    FOREIGN KEY (account_id) REFERENCES paper_accounts (id)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    market TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    notional_krw REAL NOT NULL,
    fee_krw REAL NOT NULL,
    reason TEXT,
    FOREIGN KEY (account_id) REFERENCES paper_accounts (id)
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_account_market
ON paper_positions (account_id, market);

CREATE INDEX IF NOT EXISTS idx_paper_trades_account_ts
ON paper_trades (account_id, ts);

