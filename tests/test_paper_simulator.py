from __future__ import annotations

import unittest

from app.paper.simulator import (
    TRADE_NOTIONAL_KRW,
    apply_buy_signal,
    apply_sell_signal,
    initial_state,
    normalize_state,
    summarize_state,
    unprocessed_signals_for_market,
)
from app.backtest.candle_strategy import Candle


class PaperSimulatorTests(unittest.TestCase):
    def test_initial_state_shape(self) -> None:
        state = initial_state()

        self.assertEqual(state["cash_krw"], 1_000_000.0)
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["trade_log"], [])
        self.assertEqual(state["last_processed_timestamp_by_market"], {})
        self.assertEqual(state["realized_pnl_krw"], 0.0)

    def test_buy_signal_opens_flat_position_and_records_trade(self) -> None:
        state = initial_state()
        trade = apply_buy_signal(state, "KRW-BTC", "2026-01-01T00:00:00", 100.0, 0.001)

        self.assertIsNotNone(trade)
        self.assertAlmostEqual(state["cash_krw"], 1_000_000.0 - TRADE_NOTIONAL_KRW - 10.0)
        self.assertIn("KRW-BTC", state["positions"])
        self.assertEqual(state["positions"]["KRW-BTC"]["quantity"], 100.0)
        self.assertEqual(state["trade_log"][0]["side"], "BUY")
        self.assertEqual(state["trade_log"][0]["fee_krw"], 10.0)

    def test_buy_signal_is_ignored_when_position_exists(self) -> None:
        state = initial_state()
        apply_buy_signal(state, "KRW-BTC", "2026-01-01T00:00:00", 100.0, 0.0)
        trade = apply_buy_signal(state, "KRW-BTC", "2026-01-01T00:01:00", 101.0, 0.0)

        self.assertIsNone(trade)
        self.assertEqual(len(state["trade_log"]), 1)

    def test_sell_signal_closes_position_and_records_realized_pnl(self) -> None:
        state = initial_state()
        apply_buy_signal(state, "KRW-BTC", "2026-01-01T00:00:00", 100.0, 0.0)
        trade = apply_sell_signal(state, "KRW-BTC", "2026-01-01T01:00:00", 110.0, 0.001)

        self.assertIsNotNone(trade)
        self.assertNotIn("KRW-BTC", state["positions"])
        self.assertAlmostEqual(trade["realized_pnl_krw"], 989.0)
        self.assertAlmostEqual(state["realized_pnl_krw"], 989.0)
        self.assertEqual(state["trade_log"][-1]["side"], "SELL")

    def test_sell_signal_is_ignored_when_no_position_exists(self) -> None:
        state = initial_state()
        trade = apply_sell_signal(state, "KRW-BTC", "2026-01-01T01:00:00", 110.0, 0.001)

        self.assertIsNone(trade)
        self.assertEqual(state["trade_log"], [])

    def test_summary_estimates_equity_and_unrealized_pnl(self) -> None:
        state = initial_state()
        apply_buy_signal(state, "KRW-BTC", "2026-01-01T00:00:00", 100.0, 0.0)

        summary = summarize_state(state, {"KRW-BTC": 105.0})

        self.assertAlmostEqual(summary["cash"], 990_000.0)
        self.assertAlmostEqual(summary["equity"], 1_000_500.0)
        self.assertAlmostEqual(summary["unrealized_pnl"], 500.0)
        self.assertEqual(summary["open_positions"], 1)
        self.assertEqual(summary["trade_count"], 1)

    def test_normalize_state_fills_missing_keys(self) -> None:
        state = normalize_state({"cash_krw": "123"})

        self.assertEqual(state["cash_krw"], 123.0)
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["trade_log"], [])

    def test_unprocessed_signals_skip_processed_timestamps(self) -> None:
        profile = {
            "strategy": "ema",
            "bollinger_period": 20,
            "bollinger_stddev": 2.0,
            "rsi_buy_threshold": 30,
            "rsi_sell_threshold": 60,
            "min_signal_gap_minutes": 0,
        }
        # This price path creates an EMA crossover after the processed timestamp.
        candles = [
            Candle("KRW-BTC", f"2026-01-01T00:{index:02d}:00", float(price))
            for index, price in enumerate([10] * 50 + [20] * 10)
        ]
        state = initial_state()
        state["last_processed_timestamp_by_market"]["KRW-BTC"] = "2026-01-01T00:52:00"

        signals = unprocessed_signals_for_market(profile, "KRW-BTC", candles, state)

        self.assertTrue(all(signal["ts"] > "2026-01-01T00:52:00" for signal in signals))


if __name__ == "__main__":
    unittest.main()
