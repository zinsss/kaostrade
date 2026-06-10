from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.data.db import connect, init_schema
from app.paper.execution import PaperExecutionError, execute_paper_buy, execute_paper_sell
from app.paper.state import DEFAULT_ACCOUNT_NAME

MAX_TICKER_AGE_SECONDS = 180
MAX_REGIME_AGE_SECONDS = 600
RISK_ON_BUYS = {
    "KRW-BTC": 10_000.0,
    "KRW-ETH": 10_000.0,
}


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect(db_path) as conn:
        init_schema(conn)
        summary = run_paper_strategy(conn, config)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def run_paper_strategy(conn: sqlite3.Connection, config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = paper_strategy_settings(config or {})
    now = datetime.now(timezone.utc)
    regime_row = latest_market_regime(conn)
    if regime_row is None:
        return {"regime": None, "actions": [], "skipped": ["No market regime yet"]}

    regime = str(regime_row["regime"])
    regime_stale_reason = stale_reason(
        label="Latest market regime",
        timestamp=regime_row["ts"],
        max_age_seconds=settings["max_regime_age_seconds"],
        now=now,
    )
    if regime_stale_reason is not None:
        return {"regime": regime, "actions": [], "skipped": [regime_stale_reason]}

    ticker_data = latest_ticker_data(conn)
    account = load_default_account(conn)
    if account is None:
        return {
            "regime": regime,
            "actions": [],
            "skipped": ["Default paper account does not exist"],
        }
    positions = current_positions(conn, account["id"])

    if regime == "RISK_ON":
        actions, skipped = handle_risk_on(conn, positions, ticker_data, settings["max_ticker_age_seconds"], now)
    elif regime == "RISK_OFF":
        actions, skipped = handle_risk_off(conn, positions, ticker_data, settings["max_ticker_age_seconds"], now)
    elif regime == "NEUTRAL":
        actions = []
        skipped = ["Regime is NEUTRAL"]
    else:
        actions = []
        skipped = [f"Unsupported regime: {regime}"]

    return {"regime": regime, "actions": actions, "skipped": skipped}


def paper_strategy_settings(config: dict[str, Any]) -> dict[str, int]:
    strategy_config = config.get("paper_strategy", {})
    if not isinstance(strategy_config, dict):
        strategy_config = {}
    return {
        "max_ticker_age_seconds": int(strategy_config.get("max_ticker_age_seconds", MAX_TICKER_AGE_SECONDS)),
        "max_regime_age_seconds": int(strategy_config.get("max_regime_age_seconds", MAX_REGIME_AGE_SECONDS)),
    }


def handle_risk_on(
    conn: sqlite3.Connection,
    positions: dict[str, dict[str, Any]],
    ticker_data: dict[str, dict[str, Any]],
    max_ticker_age_seconds: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    actions = []
    skipped = []
    buy_plan = [(market, krw_amount) for market, krw_amount in RISK_ON_BUYS.items() if market not in positions]
    skipped.extend(f"{market} position already exists" for market in RISK_ON_BUYS if market in positions)

    stale_reasons = validate_fresh_tickers(
        [market for market, _amount in buy_plan],
        ticker_data,
        max_ticker_age_seconds,
        now,
    )
    if stale_reasons:
        return actions, skipped + stale_reasons

    for market, krw_amount in buy_plan:
        price = float(ticker_data[market]["trade_price"])
        try:
            actions.append(
                execute_paper_buy(
                    conn,
                    account_name=DEFAULT_ACCOUNT_NAME,
                    market=market,
                    krw_amount=krw_amount,
                    price=price,
                    reason="paper strategy RISK_ON",
                )
            )
        except PaperExecutionError as exc:
            skipped.append(f"{market} buy skipped: {exc}")
    return actions, skipped


def handle_risk_off(
    conn: sqlite3.Connection,
    positions: dict[str, dict[str, Any]],
    ticker_data: dict[str, dict[str, Any]],
    max_ticker_age_seconds: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    actions = []
    skipped = []
    if not positions:
        return actions, ["No open paper positions"]

    markets = list(positions)
    stale_reasons = validate_fresh_tickers(markets, ticker_data, max_ticker_age_seconds, now)
    if stale_reasons:
        return actions, stale_reasons

    for market, position in positions.items():
        price = float(ticker_data[market]["trade_price"])
        try:
            actions.append(
                execute_paper_sell(
                    conn,
                    account_name=DEFAULT_ACCOUNT_NAME,
                    market=market,
                    quantity=float(position["quantity"]),
                    price=price,
                    reason="paper strategy RISK_OFF",
                )
            )
        except PaperExecutionError as exc:
            skipped.append(f"{market} sell skipped: {exc}")
    return actions, skipped


def validate_fresh_tickers(
    markets: list[str],
    ticker_data: dict[str, dict[str, Any]],
    max_age_seconds: int,
    now: datetime,
) -> list[str]:
    reasons = []
    for market in markets:
        ticker = ticker_data.get(market)
        if ticker is None:
            reasons.append(f"Latest ticker price missing for {market}")
            continue
        reason = stale_reason(
            label=f"Latest ticker price for {market}",
            timestamp=ticker["collected_at"],
            max_age_seconds=max_age_seconds,
            now=now,
        )
        if reason is not None:
            reasons.append(reason)
    return reasons


def stale_reason(label: str, timestamp: Any, max_age_seconds: int, now: datetime) -> str | None:
    try:
        observed_at = parse_utc_datetime(str(timestamp))
    except ValueError as exc:
        return f"{label} timestamp is invalid: {exc}"
    age_seconds = max(0, int((now - observed_at).total_seconds()))
    if age_seconds > max_age_seconds:
        return f"{label} is stale: age_seconds={age_seconds} max_age_seconds={max_age_seconds}"
    return None


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_default_account(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name, cash_krw, created_at, updated_at
        FROM paper_accounts
        WHERE name = ?
        LIMIT 1
        """,
        (DEFAULT_ACCOUNT_NAME,),
    ).fetchone()


def latest_market_regime(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, ts, regime, reason
        FROM market_regimes
        WHERE source = 'live'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def latest_ticker_data(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT market, trade_price, collected_at
        FROM ticker_snapshots
        WHERE id IN (
            SELECT MAX(id)
            FROM ticker_snapshots
            WHERE trade_price IS NOT NULL
            GROUP BY market
        )
        """
    ).fetchall()
    return {
        row["market"]: {
            "trade_price": float(row["trade_price"]),
            "collected_at": row["collected_at"],
        }
        for row in rows
    }


def current_positions(conn: sqlite3.Connection, account_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT market, quantity, average_entry_price, updated_at
        FROM paper_positions
        WHERE account_id = ?
        ORDER BY market
        """,
        (account_id,),
    ).fetchall()
    return {row["market"]: dict(row) for row in rows}


if __name__ == "__main__":
    main()
