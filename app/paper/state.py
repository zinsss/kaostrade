from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.data.db import connect, ensure_paper_account, init_schema

DEFAULT_ACCOUNT_NAME = "default"
DEFAULT_CASH_KRW = 1_000_000.0


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect(db_path) as conn:
        init_schema(conn)
        account = ensure_paper_account(
            conn,
            name=DEFAULT_ACCOUNT_NAME,
            cash_krw=DEFAULT_CASH_KRW,
            now=datetime.now(timezone.utc).isoformat(),
        )
        summary = paper_state_summary(conn, account["id"])
        conn.commit()

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def paper_state_summary(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    account = load_account(conn, account_id)
    positions = load_positions(conn, account_id)
    latest_trades = load_latest_trades(conn, account_id, limit=5)
    latest_prices = load_latest_ticker_prices(conn)
    position_value = estimate_position_value(positions, latest_prices)

    return {
        "account": {
            "id": account["id"],
            "name": account["name"],
            "cash_krw": account["cash_krw"],
            "created_at": account["created_at"],
            "updated_at": account["updated_at"],
        },
        "positions": positions,
        "latest_trades": latest_trades,
        "total_equity_estimate_krw": account["cash_krw"] + position_value,
    }


def load_account(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, name, cash_krw, created_at, updated_at
        FROM paper_accounts
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Paper account not found: {account_id}")
    return dict(row)


def load_positions(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT market, quantity, average_entry_price, updated_at
        FROM paper_positions
        WHERE account_id = ?
        ORDER BY market
        """,
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_latest_trades(conn: sqlite3.Connection, account_id: int, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ts, market, side, price, quantity, notional_krw, fee_krw, reason
        FROM paper_trades
        WHERE account_id = ?
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (account_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_latest_ticker_prices(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT market, trade_price
        FROM ticker_snapshots
        WHERE id IN (
            SELECT MAX(id)
            FROM ticker_snapshots
            WHERE trade_price IS NOT NULL
            GROUP BY market
        )
        """
    ).fetchall()
    return {row["market"]: float(row["trade_price"]) for row in rows}


def estimate_position_value(positions: list[dict[str, Any]], latest_prices: dict[str, float]) -> float:
    value = 0.0
    for position in positions:
        price = latest_prices.get(position["market"])
        if price is None:
            continue
        value += float(position["quantity"]) * price
    return value


if __name__ == "__main__":
    main()
