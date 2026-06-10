from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.backtest.candle_strategy import (
    Candle,
    aligned_mtf_trend,
    derive_five_minute_candles,
    validate_strategy_interval,
)


def ts(minutes: int) -> str:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def candle(minutes: int, price: float | None = None) -> Candle:
    return Candle(market="KRW-BTC", ts=ts(minutes), price=float(price if price is not None else minutes + 1))


class MultiTimeframeTrendTests(unittest.TestCase):
    def test_derived_five_minute_candles_use_actual_last_one_minute_timestamp(self) -> None:
        candles = [candle(0), candle(1), candle(2), candle(3), candle(4), candle(5), candle(6)]

        derived = derive_five_minute_candles(candles)

        self.assertEqual([item.ts for item in derived], [ts(4), ts(6)])
        self.assertEqual([item.price for item in derived], [5.0, 7.0])

    def test_mtf_trend_is_not_available_before_completed_five_minute_candle(self) -> None:
        candles = [candle(index) for index in range(255)]

        trend = aligned_mtf_trend(candles)

        self.assertNotIn(ts(245), trend)
        self.assertNotIn(ts(248), trend)
        self.assertIn(ts(249), trend)

    def test_mtf_strategy_rejects_non_one_minute_interval(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --interval 1m"):
            validate_strategy_interval("bollinger_rsi_and_mtf", "5m")

        validate_strategy_interval("bollinger_rsi_and_mtf", "1m")
        validate_strategy_interval("bollinger_rsi_and", "5m")


if __name__ == "__main__":
    unittest.main()
