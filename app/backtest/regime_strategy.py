from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config

START_CASH_KRW = 1_000_000.0
TRADE_NOTIONAL_KRW = 10_000.0
FEE_RATE = 0.0005
MAX_PRICE_DISTANCE_SECONDS = 300
MARKETS = ("KRW-BTC", "KRW-ETH")


@dataclass
class Position:
    quantity: float
    average_entry_price: float
    entry_ts: str


def main() -> None:
    args = parse_args()
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect_read_only(db_path) as conn:
        summary = run_backtest(conn, mode=args.mode)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the regime strategy.")
    parser.add_argument("--mode", choices=("simple", "persistent"), default="persistent")
    return parser.parse_args()


def connect_read_only(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_backtest(conn: sqlite3.Connection, mode: str = "persistent") -> dict[str, Any]:
    cash = START_CASH_KRW
    positions: dict[str, Position] = {}
    buy_count = 0
    sell_count = 0
    realized_pnl_krw = 0.0
    total_fees_krw = 0.0
    trades: list[dict[str, Any]] = []
    hold_minutes: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    skipped: list[str] = []
    regime_counts = {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0}
    risk_on_streak = 0
    risk_off_streak = 0

    regimes = load_regimes(conn)
    for regime_row in regimes:
        ts = str(regime_row["ts"])
        regime = str(regime_row["regime"])
        if regime in regime_counts:
            regime_counts[regime] += 1
        risk_on_streak, risk_off_streak = update_regime_streaks(regime, risk_on_streak, risk_off_streak)
        action_regime = action_regime_for_mode(mode, regime, risk_on_streak, risk_off_streak)
        price_result = prices_near_timestamp(conn, ts, MARKETS)
        prices = price_result["prices"]
        skipped.extend(f"{ts}: {reason}" for reason in price_result["skipped"])
        missing_prices = [market for market in MARKETS if market not in prices]
        if missing_prices:
            skipped.append(f"{ts}: missing prices for {', '.join(missing_prices)}")

        if action_regime == "RISK_ON":
            for market in MARKETS:
                if market in positions:
                    continue
                price = prices.get(market)
                if price is None:
                    continue
                total_cost = TRADE_NOTIONAL_KRW * (1 + FEE_RATE)
                if cash < total_cost:
                    skipped.append(f"{ts}: insufficient cash to buy {market}")
                    continue
                quantity = TRADE_NOTIONAL_KRW / price
                fee_krw = TRADE_NOTIONAL_KRW * FEE_RATE
                cash -= total_cost
                positions[market] = Position(quantity=quantity, average_entry_price=price, entry_ts=ts)
                total_fees_krw += fee_krw
                buy_count += 1
                trades.append(
                    simulated_trade(
                        ts=ts,
                        side="BUY",
                        market=market,
                        price=price,
                        quantity=quantity,
                        notional_krw=TRADE_NOTIONAL_KRW,
                        fee_krw=fee_krw,
                    )
                )
        elif action_regime == "RISK_OFF":
            for market in list(positions):
                price = prices.get(market)
                if price is None:
                    continue
                position = positions.pop(market)
                notional = position.quantity * price
                fee_krw = notional * FEE_RATE
                cash += notional - fee_krw
                realized_pnl_krw += notional - fee_krw - (position.quantity * position.average_entry_price)
                hold_minutes.append(position_hold_minutes(position, ts))
                total_fees_krw += fee_krw
                sell_count += 1
                trades.append(
                    simulated_trade(
                        ts=ts,
                        side="SELL",
                        market=market,
                        price=price,
                        quantity=position.quantity,
                        notional_krw=notional,
                        fee_krw=fee_krw,
                    )
                )
        elif action_regime == "NEUTRAL":
            pass
        else:
            skipped.append(f"{ts}: unsupported regime {regime}")

        equity_curve.append(
            {
                "ts": ts,
                "regime": regime,
                "equity": estimate_equity(cash, positions, prices),
                "cash": cash,
            }
        )

    latest_prices = latest_prices_for_positions(conn, positions)
    final_equity = estimate_equity(cash, positions, latest_prices)
    unrealized_pnl_krw = unrealized_pnl(positions, latest_prices)
    first_ts = str(regimes[0]["ts"]) if regimes else None
    last_ts = str(regimes[-1]["ts"]) if regimes else None
    latest_equity_point = equity_curve[-1] if equity_curve else None
    if latest_equity_point is not None:
        latest_equity_point = dict(latest_equity_point)
        latest_equity_point["equity"] = final_equity
        latest_equity_point["cash"] = cash

    return {
        "mode": mode,
        "start_cash": START_CASH_KRW,
        "final_equity": final_equity,
        "return_pct": (final_equity - START_CASH_KRW) / START_CASH_KRW * 100,
        "realized_pnl_krw": realized_pnl_krw,
        "unrealized_pnl_krw": unrealized_pnl_krw,
        "total_fees_krw": total_fees_krw,
        "trade_count": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_hours": duration_hours(first_ts, last_ts),
        "average_hold_minutes": average_hold_minutes(hold_minutes),
        "longest_hold_minutes": max(hold_minutes) if hold_minutes else None,
        "shortest_hold_minutes": min(hold_minutes) if hold_minutes else None,
        "regime_counts": regime_counts,
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "positions": serialize_positions(positions, latest_prices),
        "latest_equity_point": latest_equity_point,
        "trades": trades[-20:],
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def update_regime_streaks(regime: str, risk_on_streak: int, risk_off_streak: int) -> tuple[int, int]:
    if regime == "RISK_ON":
        return risk_on_streak + 1, 0
    if regime == "RISK_OFF":
        return 0, risk_off_streak + 1
    return 0, 0


def action_regime_for_mode(mode: str, regime: str, risk_on_streak: int, risk_off_streak: int) -> str:
    if mode == "simple":
        return regime
    if mode != "persistent":
        raise ValueError(f"Unsupported backtest mode: {mode}")
    if regime == "RISK_ON" and risk_on_streak >= 3:
        return "RISK_ON"
    if regime == "RISK_OFF" and risk_off_streak >= 3:
        return "RISK_OFF"
    return "NEUTRAL"


def position_hold_minutes(position: Position, exit_ts: str) -> float:
    return (parse_utc_datetime(exit_ts) - parse_utc_datetime(position.entry_ts)).total_seconds() / 60


def average_hold_minutes(hold_minutes: list[float]) -> float | None:
    if not hold_minutes:
        return None
    return sum(hold_minutes) / len(hold_minutes)


def simulated_trade(
    ts: str,
    side: str,
    market: str,
    price: float,
    quantity: float,
    notional_krw: float,
    fee_krw: float,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "side": side,
        "market": market,
        "price": price,
        "quantity": quantity,
        "notional_krw": notional_krw,
        "fee_krw": fee_krw,
    }


def unrealized_pnl(positions: dict[str, Position], latest_prices: dict[str, float]) -> float:
    pnl = 0.0
    for market, position in positions.items():
        latest_price = latest_prices.get(market)
        if latest_price is None:
            continue
        pnl += (latest_price - position.average_entry_price) * position.quantity
    return pnl


def duration_hours(first_ts: str | None, last_ts: str | None) -> float | None:
    if first_ts is None or last_ts is None:
        return None
    return (parse_utc_datetime(last_ts) - parse_utc_datetime(first_ts)).total_seconds() / 3600


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_regimes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, ts, regime
        FROM market_regimes
        ORDER BY ts ASC, id ASC
        """
    ).fetchall()


def prices_near_timestamp(conn: sqlite3.Connection, ts: str, markets: tuple[str, ...]) -> dict[str, Any]:
    prices = {}
    skipped = []
    for market in markets:
        candle = candle_price_near_timestamp(conn, market, ts)
        if candle is not None:
            prices[market] = candle["price"]
            continue

        ticker = ticker_price_near_timestamp(conn, market, ts)
        if ticker is not None:
            prices[market] = ticker["price"]
            continue

        nearest = nearest_price_distance(conn, market, ts)
        if nearest is not None and nearest > MAX_PRICE_DISTANCE_SECONDS:
            skipped.append(
                f"price too far from timestamp for {market}: distance_seconds={nearest} "
                f"max_distance_seconds={MAX_PRICE_DISTANCE_SECONDS}"
            )
    return {"prices": prices, "skipped": skipped}


def candle_price_near_timestamp(conn: sqlite3.Connection, market: str, ts: str) -> dict[str, float] | None:
    row = conn.execute(
        """
        SELECT
            trade_price,
            ABS(strftime('%s', candle_date_time_utc) - strftime('%s', ?)) AS distance_seconds
        FROM candles
        WHERE market = ?
          AND interval = '1m'
          AND trade_price IS NOT NULL
        ORDER BY distance_seconds ASC
        LIMIT 1
        """,
        (ts, market),
    ).fetchone()
    return price_candidate(row)


def ticker_price_near_timestamp(conn: sqlite3.Connection, market: str, ts: str) -> dict[str, float] | None:
    row = conn.execute(
        """
        SELECT
            trade_price,
            ABS(strftime('%s', collected_at) - strftime('%s', ?)) AS distance_seconds
        FROM ticker_snapshots
        WHERE market = ?
          AND trade_price IS NOT NULL
        ORDER BY distance_seconds ASC
        LIMIT 1
        """,
        (ts, market),
    ).fetchone()
    return price_candidate(row)


def price_candidate(row: sqlite3.Row | None) -> dict[str, float] | None:
    if row is None:
        return None
    distance_seconds = int(row["distance_seconds"])
    if distance_seconds > MAX_PRICE_DISTANCE_SECONDS:
        return None
    return {"price": float(row["trade_price"]), "distance_seconds": float(distance_seconds)}


def nearest_price_distance(conn: sqlite3.Connection, market: str, ts: str) -> int | None:
    row = conn.execute(
        """
        SELECT MIN(distance_seconds) AS distance_seconds
        FROM (
            SELECT ABS(strftime('%s', candle_date_time_utc) - strftime('%s', ?)) AS distance_seconds
            FROM candles
            WHERE market = ? AND interval = '1m' AND trade_price IS NOT NULL
            UNION ALL
            SELECT ABS(strftime('%s', collected_at) - strftime('%s', ?)) AS distance_seconds
            FROM ticker_snapshots
            WHERE market = ? AND trade_price IS NOT NULL
        )
        """,
        (ts, market, ts, market),
    ).fetchone()
    if row is None or row["distance_seconds"] is None:
        return None
    return int(row["distance_seconds"])


def latest_prices_for_positions(conn: sqlite3.Connection, positions: dict[str, Position]) -> dict[str, float]:
    if not positions:
        return {}
    prices = {}
    now = datetime.now(timezone.utc).isoformat()
    for market in positions:
        candle = candle_price_near_timestamp(conn, market, now)
        if candle is not None:
            prices[market] = candle["price"]
            continue
        ticker = ticker_price_near_timestamp(conn, market, now)
        if ticker is not None:
            prices[market] = ticker["price"]
    return prices


def estimate_equity(cash: float, positions: dict[str, Position], prices: dict[str, float]) -> float:
    equity = cash
    for market, position in positions.items():
        price = prices.get(market)
        if price is not None:
            equity += position.quantity * price
    return equity


def max_drawdown_pct(equity_curve: list[dict[str, Any]]) -> float:
    peak = START_CASH_KRW
    max_drawdown = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        if equity > peak:
            peak = equity
        if peak <= 0:
            continue
        drawdown = (peak - equity) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def serialize_positions(positions: dict[str, Position], latest_prices: dict[str, float]) -> dict[str, dict[str, float | None]]:
    output = {}
    for market, position in positions.items():
        latest_price = latest_prices.get(market)
        output[market] = {
            "quantity": position.quantity,
            "average_entry_price": position.average_entry_price,
            "latest_price": latest_price,
            "market_value": position.quantity * latest_price if latest_price is not None else None,
        }
    return output


if __name__ == "__main__":
    main()
