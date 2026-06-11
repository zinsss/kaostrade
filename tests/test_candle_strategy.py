from __future__ import annotations

import argparse
import ast
import sqlite3
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.backtest.strategy_profiles import get_strategy_profile
from app.backtest.candle_strategy import (
    Candle,
    FEE_SWEEP_RATES,
    FIXED_UNIVERSE_MARKETS,
    HOLD_TP_BASELINE_LABEL,
    HOLD_TP_MARKETS,
    Position,
    apply_profile_defaults,
    aligned_mtf_trend,
    bollinger_rsi_parameter_grid,
    build_bollinger_rsi_sweep_report,
    build_compare_all_strategies_report,
    build_walk_forward_report,
    candle_price_at_or_before,
    configured_markets_with_candles,
    fixed_universe_args,
    fixed_universes,
    classify_single_backtest_verdict,
    classify_walk_forward_verdict,
    derive_five_minute_candles,
    main,
    merge_fixed_universe_window_input,
    parse_args,
    hold_tp_baseline_args,
    hold_tp_sweep_args,
    prior_market_return,
    rank_dynamic_universe_markets,
    market_subsets,
    risk_exit_for_position,
    sort_fixed_universe_rows,
    sort_hold_tp_rows,
    sort_market_subset_rows,
    summarize_fee_sensitivity,
    summarize_fixed_universe,
    summarize_hold_tp_result,
    summarize_market_subset,
    run_dynamic_universe_summary,
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
        "forced_exit_count": 0,
        "signal_exit_count": trade_count // 2,
        "take_profit_count": 1 if trade_count else 0,
    }


class StrategyProfileTests(unittest.TestCase):
    def test_candidate_profile_applies_defaults(self) -> None:
        with unittest.mock.patch("sys.argv", ["candle_strategy", "--profile", "candidate_v1"]):
            args = apply_profile_defaults(parse_args())

        self.assertEqual(args.strategy, "bollinger_rsi_and_mtf")
        self.assertEqual(args.markets, ["KRW-BTC", "KRW-SOL", "KRW-DOGE"])
        self.assertEqual(args.days, 180)
        self.assertEqual(args.walk_forward_window_days, 30)
        self.assertEqual(args.bollinger_period, 10)
        self.assertEqual(args.bollinger_stddev, 2.0)
        self.assertEqual(args.rsi_buy_threshold, 20.0)
        self.assertEqual(args.rsi_sell_threshold, 65.0)
        self.assertEqual(args.take_profit_pct, 0.5)
        self.assertEqual(args.stop_loss_pct, 0.0)
        self.assertEqual(args.min_signal_gap_minutes, 60)

    def test_explicit_cli_args_override_candidate_profile(self) -> None:
        argv = [
            "candle_strategy",
            "--profile",
            "candidate_v1",
            "--strategy",
            "ema",
            "--market",
            "KRW-ETH",
            "--days",
            "30",
            "--bollinger-period",
            "20",
            "--take-profit-pct",
            "1.0",
        ]
        with unittest.mock.patch("sys.argv", argv):
            args = apply_profile_defaults(parse_args())

        self.assertEqual(args.strategy, "ema")
        self.assertEqual(args.markets, ["KRW-ETH"])
        self.assertEqual(args.days, 30)
        self.assertEqual(args.bollinger_period, 20)
        self.assertEqual(args.take_profit_pct, 1.0)
        self.assertEqual(args.rsi_sell_threshold, 65.0)

    def test_no_profile_keeps_existing_defaults(self) -> None:
        with unittest.mock.patch("sys.argv", ["candle_strategy"]):
            args = apply_profile_defaults(parse_args())

        self.assertEqual(args.strategy, "ema")
        self.assertIsNone(args.markets)
        self.assertEqual(args.days, 30)
        self.assertEqual(args.walk_forward_window_days, 10)
        self.assertEqual(args.bollinger_period, 20)
        self.assertEqual(args.take_profit_pct, 0.0)


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


class FeeSensitivitySweepTests(unittest.TestCase):
    def test_fee_sweep_rates_match_requested_grid(self) -> None:
        self.assertEqual(FEE_SWEEP_RATES, (0.0, 0.0002, 0.0004, 0.0006, 0.0008, 0.0010))

    def test_summarize_fee_sensitivity_uses_walk_forward_metrics(self) -> None:
        summary = summarize_fee_sensitivity(
            0.0004,
            [
                window_summary("a", "b", 0.8, 2, 0.2),
                window_summary("b", "c", -0.1, 4, 0.5),
                window_summary("c", "d", 0.3, 6, 0.4),
            ],
        )

        self.assertEqual(summary["fee_rate"], 0.0004)
        self.assertAlmostEqual(summary["average_return_pct"], (0.8 - 0.1 + 0.3) / 3)
        self.assertEqual(summary["median_return_pct"], 0.3)
        self.assertEqual(summary["positive_window_count"], 2)
        self.assertEqual(summary["negative_window_count"], 1)
        self.assertEqual(summary["max_drawdown_pct"], 0.5)


class HoldTakeProfitSweepTests(unittest.TestCase):
    def test_candidate_v1_baseline_args_match_existing_profile(self) -> None:
        args = hold_tp_baseline_args()
        profile = get_strategy_profile("candidate_v1")

        self.assertEqual(HOLD_TP_BASELINE_LABEL, "candidate_v1_baseline")
        self.assertEqual(HOLD_TP_MARKETS, tuple(profile["markets"]))
        self.assertEqual(args.strategy, profile["strategy"])
        self.assertEqual(args.days, profile["days"])
        self.assertEqual(args.walk_forward_window_days, profile["walk_forward_window_days"])
        self.assertEqual(args.bollinger_period, profile["bollinger_period"])
        self.assertEqual(args.bollinger_stddev, profile["bollinger_stddev"])
        self.assertEqual(args.rsi_buy_threshold, profile["rsi_buy_threshold"])
        self.assertEqual(args.rsi_sell_threshold, profile["rsi_sell_threshold"])
        self.assertEqual(args.take_profit_pct, profile["take_profit_pct"])
        self.assertEqual(args.stop_loss_pct, profile["stop_loss_pct"])
        self.assertEqual(args.min_signal_gap_minutes, profile["min_signal_gap_minutes"])
        self.assertIsNone(args.max_hold_hours)

    def test_hold_tp_sweep_args_use_candidate_v2_research_settings(self) -> None:
        args = hold_tp_sweep_args(max_hold_hours=12, take_profit_pct=2.0)

        self.assertEqual(args.strategy, "bollinger_rsi_and_mtf")
        self.assertEqual(HOLD_TP_MARKETS, ("KRW-BTC", "KRW-SOL", "KRW-DOGE"))
        self.assertEqual(args.days, 180)
        self.assertEqual(args.walk_forward_window_days, 30)
        self.assertEqual(args.bollinger_period, 10)
        self.assertEqual(args.bollinger_stddev, 2.0)
        self.assertEqual(args.rsi_buy_threshold, 20.0)
        self.assertEqual(args.rsi_sell_threshold, 65.0)
        self.assertEqual(args.take_profit_pct, 2.0)
        self.assertEqual(args.stop_loss_pct, 0.0)
        self.assertEqual(args.min_signal_gap_minutes, 60)
        self.assertEqual(args.max_hold_hours, 12)

    def test_max_hold_none_does_not_force_exit(self) -> None:
        position = Position(quantity=1.0, average_entry_price=100.0, entry_ts=ts(0))

        self.assertIsNone(
            risk_exit_for_position(
                position,
                100.0,
                ts=ts(10_000),
                take_profit_pct=0.0,
                stop_loss_pct=0.0,
                max_hold_hours=None,
            )
        )

    def test_max_hold_exit_requires_age_to_exceed_limit(self) -> None:
        position = Position(quantity=1.0, average_entry_price=100.0, entry_ts=ts(0))

        self.assertIsNone(
            risk_exit_for_position(
                position,
                100.0,
                ts=ts(360),
                take_profit_pct=0.0,
                stop_loss_pct=0.0,
                max_hold_hours=6,
            )
        )
        self.assertEqual(
            risk_exit_for_position(
                position,
                100.0,
                ts=ts(361),
                take_profit_pct=0.0,
                stop_loss_pct=0.0,
                max_hold_hours=6,
            ),
            "MAX_HOLD",
        )

    def test_summarize_hold_tp_result_uses_walk_forward_metrics(self) -> None:
        summary = summarize_hold_tp_result(
            "hold_24h_tp_1",
            24,
            1.0,
            [
                window_summary("a", "b", 0.8, 2, 0.2),
                window_summary("b", "c", -0.1, 4, 0.5),
                window_summary("c", "d", 0.3, 6, 0.4),
            ],
        )

        self.assertEqual(summary["label"], "hold_24h_tp_1")
        self.assertEqual(summary["max_hold_hours"], 24)
        self.assertEqual(summary["take_profit_pct"], 1.0)
        self.assertAlmostEqual(summary["average_return_pct"], (0.8 - 0.1 + 0.3) / 3)
        self.assertEqual(summary["median_return_pct"], 0.3)
        self.assertEqual(summary["positive_window_count"], 2)
        self.assertEqual(summary["negative_window_count"], 1)
        self.assertEqual(summary["worst_window_return_pct"], -0.1)
        self.assertEqual(summary["best_window_return_pct"], 0.8)
        self.assertEqual(summary["max_drawdown_pct"], 0.5)
        self.assertEqual(summary["trade_count"], 12)
        self.assertEqual(summary["forced_exit_count"], 0)
        self.assertEqual(summary["signal_exit_count"], 6)
        self.assertEqual(summary["take_profit_count"], 3)
        self.assertEqual(summary["average_hold_minutes"], 12.5)

    def test_hold_tp_rows_sort_by_return_positive_windows_then_drawdown(self) -> None:
        rows = [
            {
                "max_hold_hours": 6,
                "take_profit_pct": 0.5,
                "average_return_pct": 0.1,
                "positive_window_count": 3,
                "max_drawdown_pct": 0.1,
                "label": "low",
            },
            {
                "max_hold_hours": 12,
                "take_profit_pct": 1.0,
                "average_return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.5,
                "label": "better_positive",
            },
            {
                "max_hold_hours": 24,
                "take_profit_pct": 2.0,
                "average_return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.2,
                "label": "lower_drawdown",
            },
        ]

        sorted_rows = sort_hold_tp_rows(rows)

        self.assertEqual([row["max_hold_hours"] for row in sorted_rows], [24, 12, 6])

    def test_compare_hold_tp_does_not_import_paper_modules(self) -> None:
        tree = ast.parse(Path("app/backtest/candle_strategy.py").read_text())
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        self.assertFalse(any(module.startswith("app.paper") for module in imported_modules))


class DynamicUniverseHoldSweepTests(unittest.TestCase):
    def sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                interval TEXT NOT NULL,
                candle_date_time_utc TEXT NOT NULL,
                trade_price REAL NOT NULL
            )
            """
        )
        return conn

    def insert_candle(self, conn: sqlite3.Connection, market: str, minutes: int, price: float) -> None:
        conn.execute(
            """
            INSERT INTO candles (market, interval, candle_date_time_utc, trade_price)
            VALUES (?, '1m', ?, ?)
            """,
            (market, ts(minutes), price),
        )

    def test_rank_dynamic_universe_markets_uses_prior_seven_day_return(self) -> None:
        conn = self.sqlite_conn()
        window_start = datetime(2026, 1, 8, tzinfo=timezone.utc)
        start_minutes = 7 * 24 * 60
        for market, old_price, current_price in [
            ("KRW-BTC", 100.0, 110.0),
            ("KRW-SOL", 100.0, 140.0),
            ("KRW-DOGE", 100.0, 105.0),
            ("KRW-XRP", 100.0, 130.0),
        ]:
            self.insert_candle(conn, market, 0, old_price)
            self.insert_candle(conn, market, start_minutes, current_price)

        ranked = rank_dynamic_universe_markets(
            conn,
            ["KRW-BTC", "KRW-SOL", "KRW-DOGE", "KRW-XRP"],
            window_start,
        )

        self.assertEqual(ranked, ["KRW-SOL", "KRW-XRP", "KRW-BTC", "KRW-DOGE"])
        self.assertAlmostEqual(prior_market_return(conn, "KRW-SOL", window_start, 7), 0.4)

    def test_candle_price_at_or_before_and_available_market_filter(self) -> None:
        conn = self.sqlite_conn()
        self.insert_candle(conn, "KRW-BTC", 0, 100.0)
        self.insert_candle(conn, "KRW-BTC", 10, 125.0)

        self.assertEqual(
            candle_price_at_or_before(conn, "KRW-BTC", datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)),
            100.0,
        )
        self.assertEqual(
            configured_markets_with_candles(conn, ["KRW-BTC", "KRW-ETH"]),
            ["KRW-BTC"],
        )

    def test_dynamic_summary_records_selected_markets_by_window(self) -> None:
        window_inputs = [
            {
                "window_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "window_end": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "markets": ["KRW-SOL", "KRW-XRP", "KRW-BTC"],
                "events": [],
                "final_prices": {},
                "raw_signal_count": 0,
                "accepted_signal_count": 0,
            }
        ]

        summary = run_dynamic_universe_summary(window_inputs, None, 0.5, "dynamic_candidate_v1")

        self.assertEqual(summary["label"], "dynamic_candidate_v1")
        self.assertEqual(summary["universe_mode"], "dynamic_top3_prior_7d")
        self.assertIsNone(summary["max_hold_hours"])
        self.assertEqual(summary["take_profit_pct"], 0.5)
        self.assertEqual(summary["selected_markets_by_window"], [
            {"window_start": "2026-01-01T00:00:00", "markets": ["KRW-SOL", "KRW-XRP", "KRW-BTC"]}
        ])


class FixedUniverseComparisonTests(unittest.TestCase):
    def test_fixed_universes_include_requested_single_pair_and_triple_sets(self) -> None:
        universes = fixed_universes()

        self.assertEqual(FIXED_UNIVERSE_MARKETS, ("KRW-BTC", "KRW-SOL", "KRW-DOGE", "KRW-ETH", "KRW-XRP"))
        self.assertEqual(len(universes), 25)
        self.assertEqual(universes[:5], [
            ["KRW-BTC"],
            ["KRW-SOL"],
            ["KRW-DOGE"],
            ["KRW-ETH"],
            ["KRW-XRP"],
        ])
        self.assertIn(["KRW-BTC", "KRW-SOL"], universes)
        self.assertIn(["KRW-ETH", "KRW-XRP"], universes)
        self.assertIn(["KRW-BTC", "KRW-SOL", "KRW-DOGE"], universes)
        self.assertIn(["KRW-DOGE", "KRW-ETH", "KRW-XRP"], universes)

    def test_fixed_universe_args_use_candidate_v1_exits(self) -> None:
        args = fixed_universe_args()
        profile = get_strategy_profile("candidate_v1")

        self.assertEqual(args.strategy, "bollinger_rsi_and_mtf")
        self.assertEqual(args.days, 180)
        self.assertEqual(args.walk_forward_window_days, 30)
        self.assertEqual(args.bollinger_period, profile["bollinger_period"])
        self.assertEqual(args.bollinger_stddev, profile["bollinger_stddev"])
        self.assertEqual(args.rsi_buy_threshold, profile["rsi_buy_threshold"])
        self.assertEqual(args.rsi_sell_threshold, profile["rsi_sell_threshold"])
        self.assertEqual(args.take_profit_pct, 0.5)
        self.assertEqual(args.stop_loss_pct, 0)
        self.assertIsNone(args.max_hold_hours)

    def test_summarize_fixed_universe_uses_requested_output_fields(self) -> None:
        summary = summarize_fixed_universe(
            ["KRW-BTC", "KRW-SOL"],
            [
                window_summary("a", "b", 1.0, 2, 0.3),
                window_summary("b", "c", -0.5, 4, 0.8),
                window_summary("c", "d", 0.2, 6, 0.1),
            ],
        )

        self.assertEqual(summary["markets"], ["KRW-BTC", "KRW-SOL"])
        self.assertEqual(summary["market_count"], 2)
        self.assertEqual(summary["trade_count"], 12)
        self.assertAlmostEqual(summary["average_return_pct"], (1.0 - 0.5 + 0.2) / 3)
        self.assertEqual(summary["median_return_pct"], 0.2)
        self.assertEqual(summary["positive_window_count"], 2)
        self.assertEqual(summary["negative_window_count"], 1)
        self.assertEqual(summary["worst_window_return_pct"], -0.5)
        self.assertEqual(summary["best_window_return_pct"], 1.0)
        self.assertEqual(summary["max_drawdown_pct"], 0.8)

    def test_merge_fixed_universe_window_input_combines_cached_market_inputs(self) -> None:
        single_market_inputs = {
            "KRW-BTC": [
                {
                    "window_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "window_end": datetime(2026, 1, 2, tzinfo=timezone.utc),
                    "markets": ["KRW-BTC"],
                    "events": [{"ts": ts(2), "market": "KRW-BTC", "price": 100.0}],
                    "final_prices": {"KRW-BTC": 100.0},
                    "raw_signal_count": 2,
                    "accepted_signal_count": 1,
                }
            ],
            "KRW-SOL": [
                {
                    "window_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "window_end": datetime(2026, 1, 2, tzinfo=timezone.utc),
                    "markets": ["KRW-SOL"],
                    "events": [{"ts": ts(1), "market": "KRW-SOL", "price": 50.0}],
                    "final_prices": {"KRW-SOL": 50.0},
                    "raw_signal_count": 3,
                    "accepted_signal_count": 2,
                }
            ],
        }

        merged = merge_fixed_universe_window_input(["KRW-BTC", "KRW-SOL"], single_market_inputs, 0)

        self.assertEqual(merged["markets"], ["KRW-BTC", "KRW-SOL"])
        self.assertEqual([event["market"] for event in merged["events"]], ["KRW-SOL", "KRW-BTC"])
        self.assertEqual(merged["final_prices"], {"KRW-BTC": 100.0, "KRW-SOL": 50.0})
        self.assertEqual(merged["raw_signal_count"], 5)
        self.assertEqual(merged["accepted_signal_count"], 3)

    def test_fixed_universe_sort_order(self) -> None:
        rows = [
            {
                "markets": ["low"],
                "average_return_pct": 0.1,
                "positive_window_count": 3,
                "max_drawdown_pct": 0.1,
            },
            {
                "markets": ["better_positive"],
                "average_return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.5,
            },
            {
                "markets": ["lower_drawdown"],
                "average_return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.2,
            },
        ]

        sorted_rows = sort_fixed_universe_rows(rows)

        self.assertEqual([row["markets"][0] for row in sorted_rows], [
            "lower_drawdown",
            "better_positive",
            "low",
        ])


class MarketSubsetOptimizerTests(unittest.TestCase):
    def test_market_subsets_include_all_markets_and_one_to_four_market_combinations(self) -> None:
        subsets = market_subsets(["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"])

        self.assertEqual(subsets[0], ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"])
        self.assertEqual(len(subsets), 31)
        self.assertIn(["KRW-BTC"], subsets)
        self.assertIn(["KRW-BTC", "KRW-ETH"], subsets)
        self.assertIn(["KRW-BTC", "KRW-ETH", "KRW-XRP"], subsets)
        self.assertIn(["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"], subsets)

    def test_market_subsets_deduplicate_when_all_markets_is_at_most_four_symbols(self) -> None:
        subsets = market_subsets(["KRW-BTC", "KRW-ETH", "KRW-BTC"])

        self.assertEqual(subsets, [
            ["KRW-BTC", "KRW-ETH"],
            ["KRW-BTC"],
            ["KRW-ETH"],
        ])

    def test_summarize_market_subset_uses_walk_forward_metrics(self) -> None:
        summary = summarize_market_subset(
            ["KRW-BTC", "KRW-ETH"],
            [
                window_summary("a", "b", 1.0, 2, 0.3),
                window_summary("b", "c", -0.5, 4, 0.8),
                window_summary("c", "d", 0.2, 6, 0.1),
            ],
        )

        self.assertEqual(summary["markets"], ["KRW-BTC", "KRW-ETH"])
        self.assertEqual(summary["market_count"], 2)
        self.assertEqual(summary["trade_count"], 12)
        self.assertAlmostEqual(summary["return_pct"], (1.0 - 0.5 + 0.2) / 3)
        self.assertEqual(summary["median_window_return_pct"], 0.2)
        self.assertEqual(summary["positive_window_count"], 2)
        self.assertEqual(summary["negative_window_count"], 1)
        self.assertEqual(summary["max_drawdown_pct"], 0.8)

    def test_market_subset_sort_order(self) -> None:
        rows = [
            {
                "markets": ["low"],
                "return_pct": 0.1,
                "positive_window_count": 3,
                "max_drawdown_pct": 0.1,
            },
            {
                "markets": ["better_positive"],
                "return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.5,
            },
            {
                "markets": ["lower_drawdown"],
                "return_pct": 0.2,
                "positive_window_count": 4,
                "max_drawdown_pct": 0.2,
            },
        ]

        sorted_rows = sort_market_subset_rows(rows)

        self.assertEqual([row["markets"][0] for row in sorted_rows], [
            "lower_drawdown",
            "better_positive",
            "low",
        ])


if __name__ == "__main__":
    unittest.main()
