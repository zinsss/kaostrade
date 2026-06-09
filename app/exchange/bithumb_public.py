from __future__ import annotations

from typing import Any

import httpx


class BithumbPublicClient:
    """Small client for Bithumb public market-data endpoints only."""

    def __init__(self, base_url: str = "https://api.bithumb.com", timeout: float = 10.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout, headers={"accept": "application/json"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BithumbPublicClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_markets(self) -> list[dict[str, Any]]:
        return self._get_list("/v1/market/all", params={"isDetails": "true"})

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        if not markets:
            return []
        return self._get_list("/v1/ticker", params={"markets": ",".join(markets)})

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, Any]]:
        if not markets:
            return []
        return self._get_list("/v1/orderbook", params={"markets": ",".join(markets)})

    def get_candles(self, market: str, interval: str, count: int = 200) -> list[dict[str, Any]]:
        unit = self._minute_unit(interval)
        return self._get_list(
            f"/v1/candles/minutes/{unit}",
            params={"market": market, "count": str(count)},
        )

    def _get_list(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Unexpected Bithumb response for {path}: {type(payload).__name__}")
        return payload

    def _minute_unit(self, interval: str) -> int:
        if not interval.endswith("m"):
            raise ValueError(f"Unsupported candle interval: {interval}")
        unit = int(interval[:-1])
        if unit <= 0:
            raise ValueError(f"Unsupported candle interval: {interval}")
        return unit
