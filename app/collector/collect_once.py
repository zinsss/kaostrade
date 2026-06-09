from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.data.db import connect, init_schema, insert_orderbook_snapshots, insert_ticker_snapshots, upsert_markets
from app.exchange.bithumb_public import BithumbPublicClient

CONFIG_PATH = Path('/app/config.yaml')
DEFAULT_DB_PATH = '/app/data/kaostrade.sqlite'


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get('database', {}).get('path', DEFAULT_DB_PATH)
    collected_at = datetime.now(timezone.utc).isoformat()

    with BithumbPublicClient() as bithumb:
        krw_markets = [market for market in bithumb.get_markets() if market.get('market', '').startswith('KRW-')]
        selected_markets = filter_static_whitelist(krw_markets, config)
        market_symbols = [market['market'] for market in selected_markets]
        tickers = bithumb.get_tickers(market_symbols)
        orderbooks = bithumb.get_orderbooks(market_symbols)

    with connect(db_path) as conn:
        init_schema(conn)
        markets_count = upsert_markets(conn, selected_markets, collected_at)
        ticker_count = insert_ticker_snapshots(conn, tickers, collected_at)
        orderbook_count = insert_orderbook_snapshots(conn, orderbooks, collected_at)
        conn.commit()

    print(
        'Collected '
        f'markets={markets_count} '
        f'ticker_snapshots={ticker_count} '
        f'orderbook_snapshots={orderbook_count} '
        f'db={db_path}'
    )


def load_config(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f'Config must be a mapping: {path}')
    return config


def filter_static_whitelist(markets: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    whitelist = config.get('collector', {}).get('static_whitelist', [])
    if not whitelist:
        return markets

    available_by_symbol = {market['market']: market for market in markets}
    selected = [available_by_symbol[symbol] for symbol in whitelist if symbol in available_by_symbol]
    missing = [symbol for symbol in whitelist if symbol not in available_by_symbol]
    if missing:
        print(f"Skipping unavailable whitelist markets: {', '.join(missing)}")
    return selected


if __name__ == '__main__':
    main()
