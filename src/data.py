from __future__ import annotations

import threading

import ccxt
import pandas as pd

_CLIENT_CACHE: dict[str, "MarketDataClient"] = {}
_CLIENT_LOCK = threading.Lock()


def get_market_data_client(exchange_name: str = "binance") -> "MarketDataClient":
    """Return a cached MarketDataClient to avoid repeated load_markets()."""
    with _CLIENT_LOCK:
        if exchange_name not in _CLIENT_CACHE:
            _CLIENT_CACHE[exchange_name] = MarketDataClient(exchange_name)
        return _CLIENT_CACHE[exchange_name]


class MarketDataClient:
    def __init__(self, exchange_name: str = "binance"):
        if not hasattr(ccxt, exchange_name):
            raise ValueError(f"Exchange tidak didukung: {exchange_name}")
        exchange_class = getattr(ccxt, exchange_name)
        self.exchange = exchange_class({"enableRateLimit": True, "timeout": 30000})
        self.exchange.load_markets()

    def fetch_ticker_price(self, symbol: str) -> float:
        if symbol not in self.exchange.markets:
            raise ValueError(f"Symbol tidak tersedia di {self.exchange.id}: {symbol}")
        ticker = self.exchange.fetch_ticker(symbol)
        price = self._extract_price(ticker)
        if price is None:
            raise ValueError(f"Realtime price tidak tersedia untuk {symbol}")
        return price

    def fetch_ticker_prices(self, symbols: list[str]) -> dict[str, float]:
        """Bulk fetch realtime prices using fetch_tickers when supported."""
        if not symbols:
            return {}

        unique_symbols = sorted({symbol for symbol in symbols if symbol in self.exchange.markets})
        if not unique_symbols:
            return {}

        prices: dict[str, float] = {}
        if self.exchange.has.get("fetchTickers"):
            try:
                tickers = self.exchange.fetch_tickers(unique_symbols)
                for symbol, ticker in tickers.items():
                    price = self._extract_price(ticker)
                    if price is not None:
                        prices[symbol] = price
                if prices:
                    return prices
            except Exception:
                prices = {}

        for symbol in unique_symbols:
            try:
                prices[symbol] = self.fetch_ticker_price(symbol)
            except Exception:
                continue
        return prices

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
        if symbol not in self.exchange.markets:
            raise ValueError(f"Symbol tidak tersedia di {self.exchange.id}: {symbol}")

        if timeframe not in self.exchange.timeframes:
            valid_timeframes = ", ".join(self.exchange.timeframes.keys())
            raise ValueError(f"Timeframe tidak tersedia di {self.exchange.id}: {timeframe}. Pilihan: {valid_timeframes}")

        rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not rows:
            raise ValueError(f"Data OHLCV kosong untuk {symbol} {timeframe}")

        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

        numeric_columns = ["open", "high", "low", "close", "volume"]
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=numeric_columns).reset_index(drop=True)
        if df.empty:
            raise ValueError(f"Data OHLCV tidak valid untuk {symbol} {timeframe}")
        return df

    @staticmethod
    def _extract_price(ticker: dict) -> float | None:
        for key in ("last", "close", "bid", "ask"):
            value = ticker.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None
