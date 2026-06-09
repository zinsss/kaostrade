from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.data.db import connect, init_schema, insert_market_regime

CONFIG_PATH = Path("/app/config.yaml")
DEFAULT_DB_PATH = "/app/data/kaostrade.sqlite"


def main() -> None:
    config = load_config(CONFIG_PATH)
    db_path = config.get("database", {}).get("path", DEFAULT_DB_PATH)

    with connect(db_path) as conn:
        init_schema(conn)
        regime = classify_latest_regime(conn)

    print(json.dumps(regime, ensure_ascii=False, sort_keys=True))


def classify_latest_regime(conn: sqlite3.Connection) -> dict[str, Any]:
    features = latest_market_features(conn)
    if features is None:
        raise RuntimeError("No market_features rows available. Run python -m app.features.market_features first.")

    regime, reason = classify_features(features)
    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "reason": reason,
        "market_features_id": features["id"],
        "btc_return_1h": features["btc_return_1h"],
        "eth_return_1h": features["eth_return_1h"],
        "median_return_1h": features["median_return_1h"],
        "positive_ratio": features["positive_ratio"],
        "average_spread_pct": features["average_spread_pct"],
        "average_imbalance_5": features["average_imbalance_5"],
        "market_count": features["market_count"],
    }
    regime_id = insert_market_regime(conn, result)
    conn.commit()
    result["id"] = regime_id
    return result


def latest_market_features(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            id,
            ts,
            btc_return_1h,
            eth_return_1h,
            median_return_1h,
            positive_ratio,
            average_spread_pct,
            average_imbalance_5,
            market_count
        FROM market_features
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def classify_features(features: sqlite3.Row) -> tuple[str, str]:
    btc_return_1h = features["btc_return_1h"]
    median_return_1h = features["median_return_1h"]
    positive_ratio = features["positive_ratio"]
    average_spread_pct = features["average_spread_pct"]

    if (
        _gt(btc_return_1h, 0)
        and _gt(median_return_1h, 0)
        and _gte(positive_ratio, 0.6)
        and _lte(average_spread_pct, 0.25)
    ):
        return (
            "RISK_ON",
            "BTC and median 1h returns are positive, positive_ratio is at least 0.60, and average spread is at most 0.25%.",
        )

    risk_off_reasons = []
    if _lte(btc_return_1h, -0.01):
        risk_off_reasons.append("BTC 1h return is <= -1.00%")
    if _lte(median_return_1h, -0.005):
        risk_off_reasons.append("median 1h return is <= -0.50%")
    if _lte(positive_ratio, 0.3):
        risk_off_reasons.append("positive_ratio is <= 0.30")
    if risk_off_reasons:
        return "RISK_OFF", "; ".join(risk_off_reasons) + "."

    return "NEUTRAL", "Risk-on conditions were not met and risk-off thresholds were not triggered."


def _gt(value: Any, threshold: float) -> bool:
    return value is not None and float(value) > threshold


def _gte(value: Any, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _lte(value: Any, threshold: float) -> bool:
    return value is not None and float(value) <= threshold


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return config


if __name__ == "__main__":
    main()
