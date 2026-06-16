from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
STABLECOIN_SYMBOLS = {
    "USDT",
    "USDC",
    "DAI",
    "FDUSD",
    "TUSD",
    "USDE",
    "USDD",
    "PYUSD",
    "USDP",
    "GUSD",
    "LUSD",
    "FRAX",
}


@dataclass(frozen=True)
class MarketCapCoin:
    id: str
    symbol: str
    name: str
    market_cap_rank: int | None


def fetch_top_marketcap_coins(limit: int = 100, include_stablecoins: bool = False, timeout: int = 20) -> list[MarketCapCoin]:
    """Fetch top market-cap coins from CoinGecko."""
    if limit < 1:
        raise ValueError("TOP_MARKETCAP_LIMIT minimal 1")

    target_count = limit
    per_page = min(250, max(50, limit * 2))
    page = 1
    coins: list[MarketCapCoin] = []

    while len(coins) < target_count and page <= 4:
        response = requests.get(
            COINGECKO_MARKETS_URL,
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Response CoinGecko tidak valid")

        for item in payload:
            coin = _parse_coin(item)
            if coin is None:
                continue
            if not include_stablecoins and coin.symbol in STABLECOIN_SYMBOLS:
                continue
            coins.append(coin)
            if len(coins) >= target_count:
                break

        if len(payload) < per_page:
            break
        page += 1

    if not coins:
        raise ValueError("Tidak ada coin market cap yang berhasil diambil")
    return coins


def _parse_coin(item: Any) -> MarketCapCoin | None:
    if not isinstance(item, dict):
        return None

    coin_id = item.get("id")
    symbol = item.get("symbol")
    name = item.get("name")
    rank = item.get("market_cap_rank")
    if not coin_id or not symbol or not name:
        return None

    return MarketCapCoin(
        id=str(coin_id),
        symbol=str(symbol).upper(),
        name=str(name),
        market_cap_rank=int(rank) if rank is not None else None,
    )


def coins_to_quote_symbols(coins: list[MarketCapCoin], quote_asset: str = "USDT") -> list[str]:
    quote_asset = quote_asset.strip().upper()
    if not quote_asset:
        raise ValueError("TOP_MARKETCAP_QUOTE tidak boleh kosong")
    return [f"{coin.symbol}/{quote_asset}" for coin in coins]
