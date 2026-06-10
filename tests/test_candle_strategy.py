from __future__ import annotations

import argparse
import unittest
from datetime import datetime, timedelta, timezone

from app.backtest.candle_strategy import (
    Candle,
    aligned_mtf_trend,
    build_walk_forward_report,
    classify_walk_forward_verdict,
    derive_five_minute_candles,
    summarize_walk_forward,
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


def walk_forward_args() -> argparse.Namespace:
    return argparse.Namespace(
        strategy="bollinger_rsi_and_mtf",
        days=180,
        interval="1m",
        walk_forward_window_days=30,
        bollinger_period=20,
        bollinger_stddev=3.0,
        rsi_buy_threshold=25.0,
        rsi_sell_threshold=55.0,
        take_profit_pct=0.5,
        stop_loss_pct=0.0,
        min_signal_gap_minutes=60,
    )


def window_summary(
    start: str,
    end: str,
    return_pct: float,
    trade_count: int,
    max_drawdown_pct: float = 0.1,
) -> dict:
    return {
        "window_start": start,
        "window_end": end,
        "return_pct": return_pct,
        "trade_count": trade_count,
        "buy_count": trade_count // 2,
        "sell_count": trade_count - (trade_count // 2),
        "total_fees_krw": float(trade_count) * 5.0,
        "max_drawdown_pct": max_drawdown_pct,
        "average_hold_minutes": 12.5 if trade_count else None,
    }


class WalkForwardJsonReportTests(unittest.TestCase):
    def test_walk_forward_json_report_shape(self) -> None:
        summaries = [
            window_summary("2026-01-01T00:00:00", "2026-01-31T00:00:00", 1.2, 8, 0.5),
            window_summary("2026-01-31T00:00:00", "2026-03-02T00:00:00", -0.2, 4, 0.7),
        ]

        report = build_walk_forward_report(walk_forward_args(), ["KRW-BTC", "KRW-ETH"], summaries)

        self.assertEqual(report["strategy"], "bollinger_rsi_and_mtf")
        self.assertEqual(report["markets"], ["KRW-BTC", "KRW-ETH"])
        self.assertEqual(report["days"], 180)
        self.assertEqual(report["interval"], "1m")
        self.assertEqual(report["walk_forward_window_days"], 30)
        self.assertEqual(
            report["parameters"],
            {
                "bollinger_period": 20,
                "bollinger_stddev": 3.0,
                "rsi_buy_threshold": 25.0,
                "rsi_sell_threshold": 55.0,
                "take_profit_pct": 0.5,
                "stop_loss_pct": 0.0,
                "min_signal_gap_minutes": 60,
            },
        )
        self.assertEqual(len(report["windows"]), 2)
        self.assertEqual(
            set(report["windows"][0]),
            {
                "window_start",
                "window_end",
                "return_pct",
                "trade_count",
                "buy_count",
                "sell_count",
                "total_fees_krw",
                "max_drawdown_pct",
                "average_hold_minutes",
            },
        )
        self.assertEqual(report["summary"]["total_trade_count"], 12)
        self.assertEqual(report["summary"]["positive_window_count"], 1)
        self.assertEqual(report["summary"]["negative_window_count"], 1)
        self.assertEqual(report["verdict"], "RESEARCH_CANDIDATE")

    def test_verdict_no_trades(self) -> None:
        summary = summarize_walk_forward([window_summary("a", "b", 1.0, 0)])

        self.assertEqual(classify_walk_forward_verdict(summary), "NO_TRADES")

    def test_verdict_too_few_trades(self) -> None:
        summary = summarize_walk_forward([window_summary("a", "b", 1.0, 9)])

        self.assertEqual(classify_walk_forward_verdict(summary), "TOO_FEW_TRADES")

    def test_verdict_unstable(self) -> None:
        summary = summarize_walk_forward([
            window_summary("a", "b", -0.1, 5),
            window_summary("b", "c", -0.2, 5),
            window_summary("c", "d", 0.3, 5),
        ])

        self.assertEqual(classify_walk_forward_verdict(summary), "UNSTABLE")

    def test_verdict_weak_edge(self) -> None:
        summary = summarize_walk_forward([
            window_summary("a", "b", -0.2, 5),
            window_summary("b", "c", 0.1, 5),
        ])

        self.assertEqual(classify_walk_forward_verdict(summary), "WEAK_EDGE")

    def test_verdict_research_candidate(self) -> None:
        summary = summarize_walk_forward([
            window_summary("a", "b", 0.2, 5),
            window_summary("b", "c", 0.1, 5),
        ])

        self.assertEqual(classify_walk_forward_verdict(summary), "RESEARCH_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
