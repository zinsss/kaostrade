from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backtest.candle_strategy import connect_read_only
from app.backtest.strategy_profiles import get_strategy_profile, profile_names
from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, load_config
from app.paper.simulator import DEFAULT_STATE_PATH, load_state, run_simulator, save_state

DEFAULT_PROFILE = "candidate_v1"
DEFAULT_INTERVAL_SECONDS = 60


def main() -> None:
    args = parse_args()
    try:
        run_daemon(
            profile_name=args.profile,
            interval_seconds=args.interval_seconds,
            state_path=Path(args.state_path),
            once=args.once,
        )
    except KeyboardInterrupt:
        print("Paper simulator daemon stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local JSON paper simulator repeatedly.")
    parser.add_argument("--profile", choices=profile_names(), default=DEFAULT_PROFILE)
    parser.add_argument("--interval-seconds", type=positive_int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def run_daemon(profile_name: str, interval_seconds: int, state_path: Path, once: bool = False) -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    while True:
        result = run_once(profile_name=profile_name, db_path=db_path, state_path=state_path)
        print(summary_line(datetime.now(timezone.utc), profile_name, result), flush=True)
        if once:
            return
        time.sleep(interval_seconds)


def run_once(profile_name: str, db_path: str, state_path: Path) -> dict[str, Any]:
    profile = get_strategy_profile(profile_name)
    state = load_state(state_path)
    with connect_read_only(db_path) as conn:
        result = run_simulator(conn, profile, state)
    save_state(state_path, result["state"])
    return result


def summary_line(timestamp: datetime, profile_name: str, result: dict[str, Any]) -> str:
    summary = result["summary"]
    return " ".join(
        [
            f"timestamp={format_timestamp(timestamp)}",
            f"profile={profile_name}",
            f"cash={float(summary['cash']):.2f}",
            f"equity={float(summary['equity']):.2f}",
            f"realized_pnl={float(summary['realized_pnl']):.2f}",
            f"unrealized_pnl={float(summary['unrealized_pnl']):.2f}",
            f"open_positions={int(summary['open_positions'])}",
            f"trade_count={int(summary['trade_count'])}",
            f"new_action_count={len(result.get('actions', []))}",
        ]
    )


def format_timestamp(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
