from __future__ import annotations

from typing import Any

STRATEGY_PROFILES: dict[str, dict[str, Any]] = {
    "candidate_v1": {
        "strategy": "bollinger_rsi_and_mtf",
        "markets": ["KRW-BTC", "KRW-SOL", "KRW-DOGE"],
        "days": 180,
        "walk_forward_window_days": 30,
        "bollinger_period": 10,
        "bollinger_stddev": 2.0,
        "rsi_buy_threshold": 20.0,
        "rsi_sell_threshold": 65.0,
        "take_profit_pct": 0.5,
        "stop_loss_pct": 0.0,
        "min_signal_gap_minutes": 60,
    }
}


def profile_names() -> tuple[str, ...]:
    return tuple(sorted(STRATEGY_PROFILES))


def get_strategy_profile(name: str) -> dict[str, Any]:
    return dict(STRATEGY_PROFILES[name])
