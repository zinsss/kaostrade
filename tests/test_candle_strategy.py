from __future__ import annotations

import argparse
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from app.backtest.candle_strategy import (
    Candle,
    aligned_mtf_trend,
    bollinger_rsi_parameter_grid,
    build_bollinger_rsi_sweep_report,
    build_compare_all_strategies_report,
    build_walk_forward_report,
    classify_single_backtest_verdict,
    classify_walk_forward_verdict,
    derive_five_minute_candles,
    main,
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


def compare_args() -> argparse.Namespace:
    return argparse.Namespace(
        strategy="bollinger_rsi_and_mtf",
        days=180,
        interval="1m",
        trade_notional_krw=10000.0,
        fee_rate=0.0005,
        min_signal_gap_minutes=60,
        bollinger_period=20,
        bollinger_stddev=3.0,
        rsi_buy_threshold=25.0,
        rsi_sell_threshold=55.0,
        take_profit_pct=0.5,
        stop_loss_pct=0.0,
    )


def strategy_summary(
    strategy: str,
    return_pct: float,
    trade_count: int,
    max_drawdown_pct: float = 0.2,
) -> dict:
    return {
        "strategy": strategy,
        "return_pct": return_pct,
        "trade_count": trade_count,
        "buy_count": trade_count // 2,
        "sell_count": trade_count - (trade_count // 2),
        "total_fees_krw": float(trade_count) * 5.0,
        "max_drawdown_pct": max_drawdown_pct,
        "average_hold_minutes": 25.0 if trade_count else None,
        "take_profit_count": 1 if trade_count else 0,
        "stop_loss_count": 0,
        "signal_exit_count": max(0, trade_count // 2 - 1),
    }


class CompareAllStrategiesJsonReportTests(unittest.TestCase):
    def test_compare_all_json_report_shape(self) -> None:
        summaries = [
            strategy_summary("ema", 0.5, 12, 0.4),
            strategy_summary("rsi", -0.1, 14, 0.3),
        ]

        report = build_compare_all_strategies_report(compare_args(), ["KRW-BTC", "KRW-ETH"], summaries)

        self.assertEqual(report["mode"], "compare_all_strategies")
        self.assertEqual(report["markets"], ["KRW-BTC", "KRW-ETH"])
        self.assertEqual(report["days"], 180)
        self.assertEqual(report["interval"], "1m")
        self.assertEqual(
            report["parameters"],
            {
                "trade_notional_krw": 10000.0,
                "fee_rate": 0.0005,
                "min_signal_gap_minutes": 60,
                "bollinger_period": 20,
                "bollinger_stddev": 3.0,
                "rsi_buy_threshold": 25.0,
                "rsi_sell_threshold": 55.0,
                "take_profit_pct": 0.5,
                "stop_loss_pct": 0.0,
            },
        )
        self.assertEqual(len(report["strategies"]), 2)
        self.assertEqual(
            set(report["strategies"][0]),
            {
                "strategy",
                "return_pct",
                "trade_count",
                "buy_count",
                "sell_count",
                "total_fees_krw",
                "max_drawdown_pct",
                "average_hold_minutes",
                "take_profit_count",
                "stop_loss_count",
                "signal_exit_count",
                "verdict",
            },
        )
        self.assertEqual(report["best_by_return"]["strategy"], "ema")
        self.assertEqual(report["best_research_candidate"]["strategy"], "ema")
        self.assertEqual(report["research_candidate_count"], 1)

    def test_single_backtest_verdict_no_trades(self) -> None:
        self.assertEqual(classify_single_backtest_verdict(strategy_summary("ema", 1.0, 0)), "NO_TRADES")

    def test_single_backtest_verdict_too_few_trades(self) -> None:
        self.assertEqual(classify_single_backtest_verdict(strategy_summary("ema", 1.0, 9)), "TOO_FEW_TRADES")

    def test_single_backtest_verdict_weak_edge(self) -> None:
        self.assertEqual(classify_single_backtest_verdict(strategy_summary("ema", 0.0, 10)), "WEAK_EDGE")

    def test_single_backtest_verdict_high_drawdown(self) -> None:
        self.assertEqual(classify_single_backtest_verdict(strategy_summary("ema", 1.0, 10, 2.1)), "HIGH_DRAWDOWN")

    def test_single_backtest_verdict_research_candidate(self) -> None:
        self.assertEqual(classify_single_backtest_verdict(strategy_summary("ema", 1.0, 10, 2.0)), "RESEARCH_CANDIDATE")

    def test_best_by_return(self) -> None:
        report = build_compare_all_strategies_report(
            compare_args(),
            ["KRW-BTC"],
            [strategy_summary("ema", -0.1, 10), strategy_summary("rsi", 0.2, 10)],
        )

        self.assertEqual(report["best_by_return"]["strategy"], "rsi")

    def test_best_research_candidate_when_candidate_exists(self) -> None:
        report = build_compare_all_strategies_report(
            compare_args(),
            ["KRW-BTC"],
            [strategy_summary("ema", 0.1, 10), strategy_summary("rsi", 0.2, 10)],
        )

        self.assertEqual(report["best_research_candidate"]["strategy"], "rsi")
        self.assertEqual(report["research_candidate_count"], 2)

    def test_best_research_candidate_when_no_candidate_exists(self) -> None:
        report = build_compare_all_strategies_report(
            compare_args(),
            ["KRW-BTC"],
            [strategy_summary("ema", -0.1, 10), strategy_summary("rsi", 0.2, 9)],
        )

        self.assertIsNone(report["best_research_candidate"])
        self.assertEqual(report["research_candidate_count"], 0)

    def test_json_report_requires_supported_mode(self) -> None:
        with unittest.mock.patch("sys.argv", ["candle_strategy", "--json-report"]):
            with self.assertRaisesRegex(SystemExit, "--json-report requires --walk-forward, --compare-all-strategies, or --compare-bollinger-rsi"):
                main()


def sweep_result(
    strategy: str,
    return_pct: float,
    trade_count: int,
    verdict: str,
    max_drawdown_pct: float = 0.2,
    period: int = 20,
    stddev: float = 2.5,
    rsi_buy: float = 25.0,
    rsi_sell: float = 60.0,
) -> dict:
    return {
        "strategy": strategy,
        "bollinger_period": period,
        "bollinger_stddev": stddev,
        "rsi_buy_threshold": rsi_buy,
        "rsi_sell_threshold": rsi_sell,
        "take_profit_pct": 0.5,
        "stop_loss_pct": 0.0,
        "min_signal_gap_minutes": 60,
        "trade_notional_krw": 10000.0,
        "fee_rate": 0.0005,
        "return_pct": return_pct,
        "trade_count": trade_count,
        "buy_count": trade_count // 2,
        "sell_count": trade_count - (trade_count // 2),
        "total_fees_krw": float(trade_count) * 5.0,
        "max_drawdown_pct": max_drawdown_pct,
        "average_hold_minutes": 30.0 if trade_count else None,
        "take_profit_count": 1 if trade_count else 0,
        "stop_loss_count": 0,
        "signal_exit_count": max(0, trade_count // 2 - 1),
        "verdict": verdict,
    }


class BollingerRsiSweepJsonReportTests(unittest.TestCase):
    def test_parameter_grid_generation(self) -> None:
        grid = bollinger_rsi_parameter_grid()

        self.assertEqual(len(grid), 81)
        self.assertEqual(
            grid[0],
            {
                "bollinger_period": 10,
                "bollinger_stddev": 2.0,
                "rsi_buy_threshold": 20.0,
                "rsi_sell_threshold": 55.0,
            },
        )
        self.assertEqual(
            grid[-1],
            {
                "bollinger_period": 30,
                "bollinger_stddev": 3.0,
                "rsi_buy_threshold": 30.0,
                "rsi_sell_threshold": 65.0,
            },
        )

    def test_sweep_json_report_shape(self) -> None:
        results = [
            sweep_result("bollinger_rsi_and_mtf", 0.5, 12, "RESEARCH_CANDIDATE", period=10, stddev=2.0),
            sweep_result("bollinger_rsi_and_mtf", -0.1, 20, "WEAK_EDGE", period=30, stddev=3.0),
        ]

        report = build_bollinger_rsi_sweep_report(compare_args(), ["KRW-BTC"], results)

        self.assertEqual(report["mode"], "bollinger_rsi_parameter_sweep")
        self.assertEqual(report["markets"], ["KRW-BTC"])
        self.assertEqual(report["days"], 180)
        self.assertEqual(report["interval"], "1m")
        self.assertEqual(report["strategy"], "bollinger_rsi_and_mtf")
        self.assertEqual(
            report["parameter_grid"],
            {
                "bollinger_periods": [10, 20, 30],
                "bollinger_stddevs": [2.0, 2.5, 3.0],
                "rsi_buy_thresholds": [20.0, 25.0, 30.0],
                "rsi_sell_thresholds": [55.0, 60.0, 65.0],
                "total_runs": 81,
            },
        )
        self.assertEqual(report["result_count"], 2)
        self.assertEqual(len(report["results"]), 2)
        self.assertEqual(
            set(report["results"][0]),
            {
                "strategy",
                "bollinger_period",
                "bollinger_stddev",
                "rsi_buy_threshold",
                "rsi_sell_threshold",
                "take_profit_pct",
                "stop_loss_pct",
                "min_signal_gap_minutes",
                "trade_notional_krw",
                "fee_rate",
                "return_pct",
                "trade_count",
                "buy_count",
                "sell_count",
                "total_fees_krw",
                "max_drawdown_pct",
                "average_hold_minutes",
                "take_profit_count",
                "stop_loss_count",
                "signal_exit_count",
                "verdict",
            },
        )

    def test_sweep_results_are_sorted_by_return_descending(self) -> None:
        report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [
                sweep_result("a", -0.2, 10, "WEAK_EDGE"),
                sweep_result("b", 0.4, 10, "RESEARCH_CANDIDATE"),
                sweep_result("c", 0.1, 10, "RESEARCH_CANDIDATE"),
            ],
        )

        self.assertEqual([result["strategy"] for result in report["results"]], ["b", "c", "a"])

    def test_sweep_best_by_return_and_empty_results(self) -> None:
        report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [sweep_result("a", 0.1, 10, "RESEARCH_CANDIDATE"), sweep_result("b", 0.3, 10, "RESEARCH_CANDIDATE")],
        )
        empty_report = build_bollinger_rsi_sweep_report(compare_args(), ["KRW-BTC"], [])

        self.assertEqual(report["best_by_return"]["strategy"], "b")
        self.assertIsNone(empty_report["best_by_return"])

    def test_sweep_best_research_candidate_when_present_or_absent(self) -> None:
        report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [
                sweep_result("weak", 0.5, 10, "HIGH_DRAWDOWN", max_drawdown_pct=3.0),
                sweep_result("candidate", 0.2, 10, "RESEARCH_CANDIDATE"),
            ],
        )
        no_candidate_report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [sweep_result("weak", -0.1, 10, "WEAK_EDGE")],
        )

        self.assertEqual(report["best_research_candidate"]["strategy"], "candidate")
        self.assertIsNone(no_candidate_report["best_research_candidate"])

    def test_sweep_research_candidate_count(self) -> None:
        report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [
                sweep_result("a", 0.1, 10, "RESEARCH_CANDIDATE"),
                sweep_result("b", 0.2, 10, "RESEARCH_CANDIDATE"),
                sweep_result("c", -0.1, 10, "WEAK_EDGE"),
            ],
        )

        self.assertEqual(report["research_candidate_count"], 2)

    def test_sweep_verdicts_are_preserved(self) -> None:
        report = build_bollinger_rsi_sweep_report(
            compare_args(),
            ["KRW-BTC"],
            [sweep_result("a", 0.1, 10, "HIGH_DRAWDOWN", max_drawdown_pct=3.1)],
        )

        self.assertEqual(report["results"][0]["verdict"], "HIGH_DRAWDOWN")


if __name__ == "__main__":
    unittest.main()
