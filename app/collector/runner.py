from __future__ import annotations

import signal
import threading
from datetime import datetime, timezone
from typing import Any

from app.collector.collect_once import CONFIG_PATH, DEFAULT_DB_PATH, collect_snapshots, load_config
from app.data.db import connect, init_schema
from app.exchange.bithumb_public import BithumbPublicClient
from app.features.market_features import generate_market_features
from app.regime.rule_based import classify_latest_regime

DEFAULT_INTERVAL_SEC = 30


def main() -> None:
    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
    interval_sec = get_interval_sec(config)

    with connect(db_path) as conn:
        init_schema(conn)
        with BithumbPublicClient() as bithumb:
            cycle = 1
            while not stop_event.is_set():
                started_at = datetime.now(timezone.utc).isoformat()
                try:
                    counts = collect_snapshots(conn, bithumb, config, started_at)
                except Exception as exc:
                    conn.rollback()
                    print(f"Collector cycle={cycle} failed: {exc}", flush=True)
                    cycle += 1
                    stop_event.wait(interval_sec)
                    continue

                feature_status = generate_features_safely(conn, cycle)
                regime_status = classify_regime_safely(conn, cycle)
                print(format_cycle_summary(cycle, counts, feature_status, regime_status), flush=True)

                cycle += 1
                stop_event.wait(interval_sec)

    print("Collector stopped.", flush=True)


def generate_features_safely(conn, cycle: int) -> str:
    try:
        generate_market_features(conn)
    except Exception as exc:
        conn.rollback()
        print(f"Feature generation cycle={cycle} failed: {exc}", flush=True)
        return "failed"
    return "generated"


def classify_regime_safely(conn, cycle: int) -> str:
    try:
        regime = classify_latest_regime(conn)
    except Exception as exc:
        conn.rollback()
        print(f"Regime classification cycle={cycle} failed: {exc}", flush=True)
        return "failed"
    return str(regime.get("regime", "unknown"))


def format_cycle_summary(
    cycle: int,
    counts: dict[str, int],
    feature_status: str,
    regime_status: str,
) -> str:
    return (
        f"cycle={cycle} "
        f"markets={counts.get('markets', 0)} "
        f"ticker={counts.get('ticker', 0)} "
        f"orderbook={counts.get('orderbook', 0)} "
        f"candles={counts.get('candles', 0)} "
        f"features={feature_status} "
        f"regime={regime_status}"
    )


def get_interval_sec(config: dict[str, Any]) -> int:
    value = config.get("collector", {}).get("interval_sec", DEFAULT_INTERVAL_SEC)
    interval_sec = int(value)
    if interval_sec <= 0:
        raise ValueError("collector.interval_sec must be greater than zero")
    return interval_sec


def install_signal_handlers(stop_event: threading.Event) -> None:
    def handle_stop(signum: int, _frame: object) -> None:
        print(f"Received signal {signum}; stopping collector...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)


if __name__ == "__main__":
    main()
