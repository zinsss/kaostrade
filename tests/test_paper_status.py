from __future__ import annotations

import unittest

from app.paper.status import recent_trades, status_summary, format_timestamp_map


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
    def test_status_summary_uses_local_json_state_only(self) -> None:
        summary = status_summary(sample_state())

        self.assertEqual(summary["cash"], 990_000.0)
        self.assertEqual(summary["equity"], 1_000_000.0)
        self.assertEqual(summary["realized_pnl"], 123.45)
        self.assertEqual(summary["unrealized_pnl"], 0.0)
        self.assertEqual(summary["open_positions"], 1)
        self.assertEqual(summary["trade_count"], 12)

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
