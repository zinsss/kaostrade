from __future__ import annotations

import sqlite3
import unittest

from app.paper.status import (
    format_timestamp_map,
    latest_candle_prices,
    position_metrics,
    recent_trades,
    status_summary,
)


def sample_state() -> dict:
    return {
        "cash_krw": 990_000.0,
        "positions": {
            "KRW-BTC": {
                "quantity": 0.1,
                "average_entry_price": 100_000.0,
                "entry_ts": "2026-01-01T00:00:00",
                "cost_basis_krw": 10_000.0,
            }
        },
        "trade_log": [
            {"timestamp": f"2026-01-01T00:{index:02d}:00", "market": "KRW-BTC", "side": "BUY"}
            for index in range(12)
        ],
        "last_processed_timestamp_by_market": {
            "KRW-SOL": "2026-01-01T00:02:00",
            "KRW-BTC": "2026-01-01T00:01:00",
        },
        "realized_pnl_krw": 123.45,
    }


class PaperStatusTests(unittest.TestCase):
    def test_status_summary_marks_positions_to_latest_price(self) -> None:
        summary = status_summary(sample_state(), {"KRW-BTC": 110_000.0})

        self.assertEqual(summary["cash"], 990_000.0)
        self.assertEqual(summary["equity"], 1_001_000.0)
        self.assertEqual(summary["realized_pnl"], 123.45)
        self.assertEqual(summary["unrealized_pnl"], 1_000.0)
        self.assertEqual(summary["open_positions"], 1)
        self.assertEqual(summary["trade_count"], 12)

    def test_status_summary_falls_back_to_cost_basis_when_price_missing(self) -> None:
        summary = status_summary(sample_state(), {})

        self.assertEqual(summary["equity"], 1_000_000.0)
        self.assertEqual(summary["unrealized_pnl"], 0.0)


    def test_position_metrics_use_latest_price_when_available(self) -> None:
        position = sample_state()["positions"]["KRW-BTC"]
        metrics = position_metrics(position, 110_000.0)

        self.assertEqual(metrics["latest_price"], 110_000.0)
        self.assertEqual(metrics["market_value_krw"], 11_000.0)
        self.assertEqual(metrics["unrealized_pnl_krw"], 1_000.0)

    def test_position_metrics_fall_back_to_cost_basis_without_latest_price(self) -> None:
        position = sample_state()["positions"]["KRW-BTC"]
        metrics = position_metrics(position, None)

        self.assertIsNone(metrics["latest_price"])
        self.assertEqual(metrics["market_value_krw"], 10_000.0)
        self.assertEqual(metrics["unrealized_pnl_krw"], 0.0)

    def test_latest_candle_prices_reads_latest_price_per_requested_market(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT,
                interval TEXT,
                candle_date_time_utc TEXT,
                trade_price REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO candles (market, interval, candle_date_time_utc, trade_price) VALUES (?, ?, ?, ?)",
            [
                ("KRW-BTC", "1m", "2026-01-01T00:00:00", 100.0),
                ("KRW-BTC", "1m", "2026-01-01T00:01:00", 101.0),
                ("KRW-BTC", "5m", "2026-01-01T00:02:00", 999.0),
                ("KRW-SOL", "1m", "2026-01-01T00:01:00", 20.0),
            ],
        )

        prices = latest_candle_prices(conn, ["KRW-BTC", "KRW-SOL", "KRW-MISSING"])

        self.assertEqual(prices, {"KRW-BTC": 101.0, "KRW-SOL": 20.0})

    def test_recent_trades_returns_last_ten_in_original_order(self) -> None:
        trades = recent_trades(sample_state())

        self.assertEqual(len(trades), 10)
        self.assertEqual(trades[0]["timestamp"], "2026-01-01T00:02:00")
        self.assertEqual(trades[-1]["timestamp"], "2026-01-01T00:11:00")

    def test_format_timestamp_map_is_stable(self) -> None:
        formatted = format_timestamp_map(sample_state()["last_processed_timestamp_by_market"])

        self.assertEqual(
            formatted,
            "KRW-BTC=2026-01-01T00:01:00, KRW-SOL=2026-01-01T00:02:00",
        )

    def test_format_timestamp_map_handles_empty_map(self) -> None:
        self.assertEqual(format_timestamp_map({}), "-")


if __name__ == "__main__":
    unittest.main()
