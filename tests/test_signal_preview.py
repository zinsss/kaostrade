from __future__ import annotations

import unittest

from app.backtest.candle_strategy import Candle
from app.paper.signal_preview import (
    POSITION_ASSUMPTION,
    current_signal_preview_row,
    signal_preview_row,
)


class SignalPreviewTests(unittest.TestCase):
    def test_current_signal_reports_latest_accepted_signal(self) -> None:
        latest = Candle(market="KRW-BTC", ts="2026-01-01T00:01:00", price=100.0)
        row = current_signal_preview_row(
            "KRW-BTC",
            latest,
            [
                {"market": "KRW-BTC", "ts": "2026-01-01T00:00:00", "price": 99.0, "signal": "SELL"},
                {"market": "KRW-BTC", "ts": "2026-01-01T00:01:00", "price": 100.0, "signal": "BUY"},
            ],
        )

        self.assertEqual(row["signal"], "BUY")
        self.assertEqual(row["latest_timestamp"], "2026-01-01T00:01:00")
        self.assertEqual(row["latest_price"], 100.0)
        self.assertEqual(row["position_assumption"], POSITION_ASSUMPTION)
        self.assertIn("accepted BUY", row["reason"])

    def test_current_signal_holds_when_latest_candle_has_no_signal(self) -> None:
        latest = Candle(market="KRW-BTC", ts="2026-01-01T00:01:00", price=100.0)
        row = current_signal_preview_row(
            "KRW-BTC",
            latest,
            [{"market": "KRW-BTC", "ts": "2026-01-01T00:00:00", "price": 99.0, "signal": "BUY"}],
        )

        self.assertEqual(row["signal"], "HOLD")
        self.assertEqual(row["reason"], "No accepted signal on latest candle")
        self.assertEqual(row["position_assumption"], "FLAT")

    def test_signal_preview_row_handles_missing_candles(self) -> None:
        row = signal_preview_row("KRW-SOL", None, None, "HOLD", "No candles available")

        self.assertEqual(row["market"], "KRW-SOL")
        self.assertEqual(row["latest_timestamp"], "-")
        self.assertIsNone(row["latest_price"])
        self.assertEqual(row["signal"], "HOLD")


if __name__ == "__main__":
    unittest.main()
