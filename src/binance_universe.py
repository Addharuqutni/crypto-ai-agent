from __future__ import annotations

from typing import Any

import ccxt

STABLE_BASES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDE", "USD1", "USDP", "PYUSD", "USDG", "USDD", "USDS"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


def fetch_binance_top_usdt_symbols(
    limit: int = 100,
    quote: str = "USDT",
    include_stablecoins: bool = False,
    market_type: str = "spot",
) -> list[str]:
    exchange = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    exchange.load_markets()
    tickers = exchange.fetch_tickers()
    candidates: list[tuple[str, float]] = []

    for symbol, ticker in tickers.items():
        market = exchange.markets.get(symbol)
        if not _is_valid_market(market, quote, include_stablecoins, market_type):
            continue
        volume = _quote_volume(ticker)
        if volume <= 0:
            continue
        candidates.append((symbol, volume))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in candidates[:limit]]


def _is_valid_market(market: dict[str, Any] | None, quote: str, include_stablecoins: bool, market_type: str) -> bool:
    if not market or not market.get("active", True):
        return False
    if market.get("quote") != quote:
        return False
    if market_type == "spot" and not market.get("spot"):
        return False
    if market_type in {"future", "swap"} and not (market.get("future") or market.get("swap")):
        return False

    base = str(market.get("base") or "").upper()
    if not include_stablecoins and base in STABLE_BASES:
        return False
    if base.endswith(LEVERAGED_SUFFIXES):
        return False
    return True


def _quote_volume(ticker: dict[str, Any]) -> float:
    for key in ("quoteVolume", "baseVolume"):
        value = ticker.get(key)
        if value is None:
            continue
        try:
            volume = float(value)
        except (TypeError, ValueError):
            continue
        if volume > 0:
            return volume
    return 0.0
