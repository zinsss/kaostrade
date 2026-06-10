from __future__ import annotations

from typing import Any, Callable

Classifier = Callable[[Any], tuple[str, str]]
CLASSIFIER_NAMES = ("basic", "momentum", "breadth")


class ClassifierNotApplicable(ValueError):
    pass


def classify_basic(features: Any) -> tuple[str, str]:
    btc_return_1h = features["btc_return_1h"]
    median_return_1h = features["median_return_1h"]
    positive_ratio = features["positive_ratio"]
    average_spread_pct = features["average_spread_pct"]

    spread_ok = average_spread_pct is None or _lte(average_spread_pct, 0.25)
    if (
        _gt(btc_return_1h, 0)
        and _gt(median_return_1h, 0)
        and _gte(positive_ratio, 0.6)
        and spread_ok
    ):
        if average_spread_pct is None:
            return (
                "RISK_ON",
                "BTC and median 1h returns are positive and positive_ratio is at least 0.60; average spread is unavailable.",
            )
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


def classify_momentum(features: Any) -> tuple[str, str]:
    btc_return_1h = features["btc_return_1h"]
    btc_return_4h = _feature_value(features, "btc_return_4h")
    median_return_1h = features["median_return_1h"]
    if btc_return_1h is None or btc_return_4h is None or median_return_1h is None:
        raise ClassifierNotApplicable("momentum classifier requires btc_return_1h, btc_return_4h, and median_return_1h")

    if _gt(btc_return_1h, 0) and _gt(btc_return_4h, 0) and _gt(median_return_1h, 0):
        return "RISK_ON", "BTC 1h, BTC 4h, and median 1h returns are positive."

    if _lt(btc_return_1h, 0) and _lt(btc_return_4h, 0):
        return "RISK_OFF", "BTC 1h and BTC 4h returns are negative."

    return "NEUTRAL", "Momentum risk-on and risk-off conditions were not met."


def classify_breadth(features: Any) -> tuple[str, str]:
    positive_ratio = features["positive_ratio"]

    if _gte(positive_ratio, 0.8):
        return "RISK_ON", "positive_ratio is at least 0.80."
    if _lte(positive_ratio, 0.2):
        return "RISK_OFF", "positive_ratio is at most 0.20."
    return "NEUTRAL", "Breadth thresholds were not triggered."


def get_classifier(name: str) -> Classifier:
    classifiers = {
        "basic": classify_basic,
        "momentum": classify_momentum,
        "breadth": classify_breadth,
    }
    try:
        return classifiers[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported regime classifier: {name}") from exc


def _feature_value(features: Any, key: str) -> Any:
    try:
        return features[key]
    except (KeyError, IndexError):
        return None


def _gt(value: Any, threshold: float) -> bool:
    return value is not None and float(value) > threshold


def _gte(value: Any, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _lt(value: Any, threshold: float) -> bool:
    return value is not None and float(value) < threshold


def _lte(value: Any, threshold: float) -> bool:
    return value is not None and float(value) <= threshold
