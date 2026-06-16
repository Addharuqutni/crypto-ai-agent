import ccxt
import pandas as pd


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
        price = ticker.get("last") or ticker.get("close") or ticker.get("bid") or ticker.get("ask")
        if price is None:
            raise ValueError(f"Realtime price tidak tersedia untuk {symbol}")
        return float(price)

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
