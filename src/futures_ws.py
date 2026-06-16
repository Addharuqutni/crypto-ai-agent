import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

import websocket


BINANCE_USDM_WS_BASE = "wss://fstream.binance.com/stream?streams="


@dataclass
class RealtimePrice:
    symbol: str
    price: float
    event_time: int
    source: str = "BINANCE_USDM_FUTURES_WS"


class BinanceUSDMFuturesWebSocket:
    def __init__(self, symbols: list[str], on_price: Callable[[RealtimePrice], None]):
        self.symbols = [self._normalize_symbol(symbol) for symbol in symbols]
        if not self.symbols:
            raise ValueError("Minimal satu symbol diperlukan untuk WebSocket")
        self.on_price = on_price
        self.ws: websocket.WebSocketApp | None = None
        self.should_run = True

    def _normalize_symbol(self, symbol: str) -> str:
        normalized = symbol.replace("/", "").replace(":USDT", "").strip().lower()
        if not normalized:
            raise ValueError(f"Symbol WebSocket tidak valid: {symbol!r}")
        return normalized

    def _build_url(self) -> str:
        streams = "/".join(f"{symbol}@markPrice@1s" for symbol in self.symbols)
        return BINANCE_USDM_WS_BASE + streams

    def _on_open(self, ws):
        print("Binance USDⓈ-M Futures WebSocket connected")

    def _on_message(self, ws, message: str):
        try:
            payload = json.loads(message)
            data = payload.get("data", {})

            symbol = data.get("s")
            mark_price = data.get("p")
            event_time = data.get("E")

            if not symbol or mark_price is None or event_time is None:
                return

            price = RealtimePrice(
                symbol=symbol,
                price=float(mark_price),
                event_time=int(event_time),
            )
            self.on_price(price)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"Invalid WebSocket message ignored: {error}")

    def _on_error(self, ws, error):
        print(f"Binance USDⓈ-M Futures WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"Binance USDⓈ-M Futures WebSocket closed: {close_status_code} {close_msg}")

    def run_forever(self):
        while self.should_run:
            self.ws = websocket.WebSocketApp(
                self._build_url(),
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self.ws.run_forever(ping_interval=20, ping_timeout=10)

            if self.should_run:
                print("Reconnect WebSocket in 5 seconds...")
                time.sleep(5)

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.should_run = False
        if self.ws:
            self.ws.close()
