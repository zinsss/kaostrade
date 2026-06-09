from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.data.db import connect, ensure_paper_account, init_schema
from app.paper.state import DEFAULT_ACCOUNT_NAME, DEFAULT_CASH_KRW

DEFAULT_FEE_RATE = 0.0005


class PaperExecutionError(ValueError):
    pass


def execute_paper_buy(
    conn: sqlite3.Connection,
    account_name: str,
    market: str,
    krw_amount: float,
    price: float,
    reason: str | None = None,
) -> dict[str, Any]:
    krw_amount = float(krw_amount)
    price = float(price)
    if krw_amount <= 0:
        raise PaperExecutionError("krw_amount must be greater than zero")
    if price <= 0:
        raise PaperExecutionError("price must be greater than zero")

    with conn:
        now = utc_now()
        account = ensure_paper_account(conn, account_name, DEFAULT_CASH_KRW, now)
        fee_krw = krw_amount * DEFAULT_FEE_RATE
        total_cost = krw_amount + fee_krw
        if float(account["cash_krw"]) < total_cost:
            raise PaperExecutionError("insufficient cash")

        quantity = krw_amount / price
        position = load_position(conn, account["id"], market)
        if position is None:
            new_quantity = quantity
            average_entry_price = price
            conn.execute(
                """
                INSERT INTO paper_positions (account_id, market, quantity, average_entry_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account["id"], market, new_quantity, average_entry_price, now),
            )
        else:
            existing_quantity = float(position["quantity"])
            existing_average = float(position["average_entry_price"])
            new_quantity = existing_quantity + quantity
            average_entry_price = ((existing_quantity * existing_average) + krw_amount) / new_quantity
            conn.execute(
                """
                UPDATE paper_positions
                SET quantity = ?, average_entry_price = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_quantity, average_entry_price, now, position["id"]),
            )

        new_cash = float(account["cash_krw"]) - total_cost
        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_krw = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_cash, now, account["id"]),
        )
        trade_id = insert_paper_trade(
            conn,
            account_id=account["id"],
            ts=now,
            market=market,
            side="BUY",
            price=price,
            quantity=quantity,
            notional_krw=krw_amount,
            fee_krw=fee_krw,
            reason=reason,
        )

    return {
        "account_name": account_name,
        "market": market,
        "side": "BUY",
        "price": price,
        "quantity": quantity,
        "notional_krw": krw_amount,
        "fee_krw": fee_krw,
        "cash_krw": new_cash,
        "position_quantity": new_quantity,
        "average_entry_price": average_entry_price,
        "trade_id": trade_id,
        "reason": reason,
    }


def execute_paper_sell(
    conn: sqlite3.Connection,
    account_name: str,
    market: str,
    quantity: float,
    price: float,
    reason: str | None = None,
) -> dict[str, Any]:
    quantity = float(quantity)
    price = float(price)
    if quantity <= 0:
        raise PaperExecutionError("quantity must be greater than zero")
    if price <= 0:
        raise PaperExecutionError("price must be greater than zero")

    with conn:
        now = utc_now()
        account = ensure_paper_account(conn, account_name, DEFAULT_CASH_KRW, now)
        position = load_position(conn, account["id"], market)
        if position is None:
            raise PaperExecutionError("no position for market")

        existing_quantity = float(position["quantity"])
        if existing_quantity < quantity:
            raise PaperExecutionError("insufficient position quantity")

        notional_krw = quantity * price
        fee_krw = notional_krw * DEFAULT_FEE_RATE
        new_cash = float(account["cash_krw"]) + notional_krw - fee_krw
        remaining_quantity = existing_quantity - quantity

        if remaining_quantity <= 1e-12:
            conn.execute("DELETE FROM paper_positions WHERE id = ?", (position["id"],))
            remaining_quantity = 0.0
            average_entry_price = None
        else:
            average_entry_price = float(position["average_entry_price"])
            conn.execute(
                """
                UPDATE paper_positions
                SET quantity = ?, updated_at = ?
                WHERE id = ?
                """,
                (remaining_quantity, now, position["id"]),
            )

        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_krw = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_cash, now, account["id"]),
        )
        trade_id = insert_paper_trade(
            conn,
            account_id=account["id"],
            ts=now,
            market=market,
            side="SELL",
            price=price,
            quantity=quantity,
            notional_krw=notional_krw,
            fee_krw=fee_krw,
            reason=reason,
        )

    return {
        "account_name": account_name,
        "market": market,
        "side": "SELL",
        "price": price,
        "quantity": quantity,
        "notional_krw": notional_krw,
        "fee_krw": fee_krw,
        "cash_krw": new_cash,
        "position_quantity": remaining_quantity,
        "average_entry_price": average_entry_price,
        "trade_id": trade_id,
        "reason": reason,
    }


def load_position(conn: sqlite3.Connection, account_id: int, market: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, account_id, market, quantity, average_entry_price, updated_at
        FROM paper_positions
        WHERE account_id = ? AND market = ?
        """,
        (account_id, market),
    ).fetchone()


def insert_paper_trade(
    conn: sqlite3.Connection,
    account_id: int,
    ts: str,
    market: str,
    side: str,
    price: float,
    quantity: float,
    notional_krw: float,
    fee_krw: float,
    reason: str | None,
) -> int:
    conn.execute(
        """
        INSERT INTO paper_trades (
            account_id, ts, market, side, price, quantity, notional_krw, fee_krw, reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, ts, market, side, price, quantity, notional_krw, fee_krw, reason),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def latest_ticker_price(conn: sqlite3.Connection, market: str) -> float:
    row = conn.execute(
        """
        SELECT trade_price
        FROM ticker_snapshots
        WHERE market = ? AND trade_price IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (market,),
    ).fetchone()
    if row is None:
        raise PaperExecutionError(f"latest ticker price not found for {market}")
    price = float(row["trade_price"])
    if price <= 0:
        raise PaperExecutionError(f"latest ticker price is invalid for {market}")
    return price


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect(db_path) as conn:
        init_schema(conn)
        price = latest_ticker_price(conn, args.market)
        if args.action == "buy":
            result = execute_paper_buy(
                conn,
                account_name=DEFAULT_ACCOUNT_NAME,
                market=args.market,
                krw_amount=args.amount,
                price=price,
                reason="cli smoke buy",
            )
        else:
            result = execute_paper_sell(
                conn,
                account_name=DEFAULT_ACCOUNT_NAME,
                market=args.market,
                quantity=args.amount,
                price=price,
                reason="cli smoke sell",
            )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute a manual paper trade using the latest ticker price.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    buy_parser = subparsers.add_parser("buy")
    buy_parser.add_argument("market")
    buy_parser.add_argument("amount", type=float, help="KRW notional amount to buy")

    sell_parser = subparsers.add_parser("sell")
    sell_parser.add_argument("market")
    sell_parser.add_argument("amount", type=float, help="Quantity to sell")

    return parser.parse_args()


if __name__ == "__main__":
    main()
