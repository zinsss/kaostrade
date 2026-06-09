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
    raw_json TEXT NOT NULL,
    FOREIGN KEY (market) REFERENCES markets (market)
);

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_market_collected_at
ON orderbook_snapshots (market, collected_at);
