from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from app.backtest.candle_strategy import (
    DEFAULT_DAYS,
    DEFAULT_FEE_RATE,
    DEFAULT_INTERVAL,
    connect_read_only,
    filter_signals_by_gap,
    load_candles,
    parse_utc_datetime,
    strategy_signals,
    validate_strategy_interval,
)
from app.backtest.strategy_profiles import get_strategy_profile, profile_names
from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config

DEFAULT_STATE_PATH = "data/paper_candidate_v1.json"
START_CASH_KRW = 1_000_000.0
TRADE_NOTIONAL_KRW = 10_000.0


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    profile = get_strategy_profile(args.profile)
    state_path = Path(args.state_path)

    state = initial_state() if args.reset or args.start_now else load_state(state_path)
    with connect_read_only(db_path) as conn:
        if args.start_now:
            result = initialize_from_latest_candles(conn, profile, state)
        else:
            result = run_simulator(conn, profile, state)

    save_state(state_path, result["state"])
    print_simulator_result(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local JSON paper simulation for a strategy profile.")
    parser.add_argument("--profile", choices=profile_names(), required=True)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--start-now", action="store_true")
    return parser.parse_args()


def initial_state() -> dict[str, Any]:
    return {
        "cash_krw": START_CASH_KRW,
        "positions": {},
        "trade_log": [],
        "last_processed_timestamp_by_market": {},
        "realized_pnl_krw": 0.0,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return initial_state()
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    return normalize_state(state)


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    normalized = initial_state()
    normalized.update(state)
    normalized["cash_krw"] = float(normalized.get("cash_krw", START_CASH_KRW))
    normalized["positions"] = dict(normalized.get("positions") or {})
    normalized["trade_log"] = list(normalized.get("trade_log") or [])
    normalized["last_processed_timestamp_by_market"] = dict(
        normalized.get("last_processed_timestamp_by_market") or {}
    )
    normalized["realized_pnl_krw"] = float(normalized.get("realized_pnl_krw", 0.0))
    return normalized


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def initialize_from_latest_candles(
    conn: sqlite3.Connection,
    profile: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    state = normalize_state(state)
    strategy = str(profile["strategy"])
    interval = str(profile.get("interval", DEFAULT_INTERVAL))
    validate_strategy_interval(strategy, interval)

    markets = list(profile.get("markets") or [])
    if not markets:
        raise ValueError("Strategy profile has no markets")

    latest_prices: dict[str, float] = {}
    for market in markets:
        candles = load_candles(conn, market, interval, int(profile.get("days", DEFAULT_DAYS)))
        if not candles:
            continue
        latest_prices[market] = candles[-1].price
        state["last_processed_timestamp_by_market"][market] = candles[-1].ts

    return {
        "state": state,
        "summary": summarize_state(state, latest_prices),
        "actions": [],
        "action_message": "Initialized from latest candles; no historical actions",
    }


def run_simulator(conn: sqlite3.Connection, profile: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    state = normalize_state(state)
    strategy = str(profile["strategy"])
    interval = str(profile.get("interval", DEFAULT_INTERVAL))
    validate_strategy_interval(strategy, interval)

    markets = list(profile.get("markets") or [])
    if not markets:
        raise ValueError("Strategy profile has no markets")

    signals_by_market: dict[str, list[dict[str, Any]]] = {}
    latest_prices: dict[str, float] = {}
    latest_timestamps: dict[str, str] = {}
    for market in markets:
        candles = load_candles(conn, market, interval, int(profile.get("days", DEFAULT_DAYS)))
        if not candles:
            signals_by_market[market] = []
            continue
        latest_prices[market] = candles[-1].price
        latest_timestamps[market] = candles[-1].ts
        signals_by_market[market] = unprocessed_signals_for_market(profile, market, candles, state)

    actions = []
    for signal in sorted(
        (signal for signals in signals_by_market.values() for signal in signals),
        key=lambda item: (item["ts"], item["market"]),
    ):
        action = apply_signal(state, signal, float(profile.get("fee_rate", DEFAULT_FEE_RATE)))
        if action is not None:
            actions.append(action)

    for market, latest_ts in latest_timestamps.items():
        state["last_processed_timestamp_by_market"][market] = latest_ts

    summary = summarize_state(state, latest_prices)
    return {"state": state, "summary": summary, "actions": actions}


def unprocessed_signals_for_market(
    profile: dict[str, Any],
    market: str,
    candles: list[Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    accepted_signals = filter_signals_by_gap(
        strategy_signals(
            str(profile["strategy"]),
            candles,
            bollinger_period=int(profile["bollinger_period"]),
            bollinger_stddev=float(profile["bollinger_stddev"]),
            rsi_buy_threshold=float(profile["rsi_buy_threshold"]),
            rsi_sell_threshold=float(profile["rsi_sell_threshold"]),
        ),
        int(profile.get("min_signal_gap_minutes", 0)),
    )
    last_processed = state["last_processed_timestamp_by_market"].get(market)
    if not last_processed:
        return accepted_signals
    last_processed_dt = parse_utc_datetime(last_processed)
    return [signal for signal in accepted_signals if parse_utc_datetime(signal["ts"]) > last_processed_dt]


def apply_signal(state: dict[str, Any], signal: dict[str, Any], fee_rate: float) -> dict[str, Any] | None:
    side = str(signal["signal"])
    market = str(signal["market"])
    price = float(signal["price"])
    if side == "BUY":
        return apply_buy_signal(state, market, str(signal["ts"]), price, fee_rate)
    if side == "SELL":
        return apply_sell_signal(state, market, str(signal["ts"]), price, fee_rate)
    return None


def apply_buy_signal(
    state: dict[str, Any],
    market: str,
    ts: str,
    price: float,
    fee_rate: float,
) -> dict[str, Any] | None:
    if market in state["positions"]:
        return None
    notional_krw = TRADE_NOTIONAL_KRW
    fee_krw = notional_krw * fee_rate
    total_cost = notional_krw + fee_krw
    if state["cash_krw"] < total_cost:
        return None

    quantity = notional_krw / price
    state["cash_krw"] -= total_cost
    state["positions"][market] = {
        "quantity": quantity,
        "average_entry_price": price,
        "entry_ts": ts,
        "cost_basis_krw": notional_krw,
    }
    trade = trade_record(ts, market, "BUY", price, quantity, notional_krw, fee_krw, "SIGNAL_BUY")
    state["trade_log"].append(trade)
    return trade


def apply_sell_signal(
    state: dict[str, Any],
    market: str,
    ts: str,
    price: float,
    fee_rate: float,
) -> dict[str, Any] | None:
    position = state["positions"].pop(market, None)
    if position is None:
        return None

    quantity = float(position["quantity"])
    notional_krw = quantity * price
    fee_krw = notional_krw * fee_rate
    realized_pnl = notional_krw - fee_krw - float(position.get("cost_basis_krw", quantity * float(position["average_entry_price"])))
    state["cash_krw"] += notional_krw - fee_krw
    state["realized_pnl_krw"] += realized_pnl
    trade = trade_record(ts, market, "SELL", price, quantity, notional_krw, fee_krw, "SIGNAL_SELL")
    trade["realized_pnl_krw"] = realized_pnl
    state["trade_log"].append(trade)
    return trade


def trade_record(
    ts: str,
    market: str,
    side: str,
    price: float,
    quantity: float,
    notional_krw: float,
    fee_krw: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "timestamp": ts,
        "market": market,
        "side": side,
        "price": price,
        "quantity": quantity,
        "notional_krw": notional_krw,
        "fee_krw": fee_krw,
        "reason": reason,
    }


def summarize_state(state: dict[str, Any], latest_prices: dict[str, float]) -> dict[str, Any]:
    position_value = 0.0
    unrealized_pnl = 0.0
    for market, position in state["positions"].items():
        latest_price = latest_prices.get(market)
        if latest_price is None:
            continue
        quantity = float(position["quantity"])
        value = quantity * latest_price
        position_value += value
        unrealized_pnl += value - float(position.get("cost_basis_krw", quantity * float(position["average_entry_price"])))

    cash = float(state["cash_krw"])
    return {
        "cash": cash,
        "equity": cash + position_value,
        "realized_pnl": float(state.get("realized_pnl_krw", 0.0)),
        "unrealized_pnl": unrealized_pnl,
        "open_positions": len(state["positions"]),
        "trade_count": len(state["trade_log"]),
    }


def print_simulator_result(result: dict[str, Any]) -> None:
    console = Console(width=140)
    summary = result["summary"]

    summary_table = Table(title="Paper Candidate Simulator")
    summary_table.add_column("cash", justify="right")
    summary_table.add_column("equity", justify="right")
    summary_table.add_column("realized_pnl", justify="right")
    summary_table.add_column("unrealized_pnl", justify="right")
    summary_table.add_column("open_positions", justify="right")
    summary_table.add_column("trade_count", justify="right")
    summary_table.add_row(
        format_krw(summary["cash"]),
        format_krw(summary["equity"]),
        format_krw(summary["realized_pnl"]),
        format_krw(summary["unrealized_pnl"]),
        str(summary["open_positions"]),
        str(summary["trade_count"]),
    )
    console.print(summary_table)

    actions_table = Table(title="Latest Actions")
    actions_table.add_column("market")
    actions_table.add_column("timestamp")
    actions_table.add_column("side")
    actions_table.add_column("price", justify="right")
    actions_table.add_column("notional_krw", justify="right")
    actions_table.add_column("fee_krw", justify="right")
    actions_table.add_column("reason")

    latest_actions = result["actions"][-10:]
    if latest_actions:
        for action in latest_actions:
            actions_table.add_row(
                action["market"],
                action["timestamp"],
                action["side"],
                f"{float(action['price']):.8f}",
                format_krw(action["notional_krw"]),
                format_krw(action["fee_krw"]),
                action["reason"],
            )
    else:
        actions_table.add_row("-", "-", "-", "-", "-", "-", result.get("action_message", "No new actions"))

    console.print(actions_table)


def format_krw(value: float) -> str:
    return f"{float(value):,.2f}"


if __name__ == "__main__":
    main()
