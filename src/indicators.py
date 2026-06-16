import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def _require_columns(df: pd.DataFrame) -> None:
    missing_columns = REQUIRED_COLUMNS - set(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Data OHLCV tidak lengkap. Kolom hilang: {missing}")


def _require_indicator(result: pd.DataFrame | pd.Series | None, name: str) -> pd.DataFrame | pd.Series:
    if result is None or result.empty:
        raise ValueError(f"Gagal menghitung indikator {name}. Tambahkan FETCH_LIMIT atau periksa data OHLCV.")
    return result


def add_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    indicators = config["indicators"]
    df = df.copy()
    _require_columns(df)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema_fast"] = _require_indicator(_ema(close, indicators["ema_fast"]), "EMA fast")
    df["ema_mid"] = _require_indicator(_ema(close, indicators["ema_mid"]), "EMA mid")
    df["ema_slow"] = _require_indicator(_ema(close, indicators["ema_slow"]), "EMA slow")
    df["rsi"] = _require_indicator(_rsi(close, indicators["rsi_length"]), "RSI")
    df["atr"] = _require_indicator(_atr(high, low, close, indicators["atr_length"]), "ATR")

    macd, macd_hist, macd_signal = _macd(
        close,
        fast=indicators["macd_fast"],
        slow=indicators["macd_slow"],
        signal=indicators["macd_signal"],
    )
    df["macd"] = _require_indicator(macd, "MACD")
    df["macd_hist"] = _require_indicator(macd_hist, "MACD histogram")
    df["macd_signal"] = _require_indicator(macd_signal, "MACD signal")

    adx, dmp, dmn = _adx(high, low, close, indicators["adx_length"])
    df["adx"] = _require_indicator(adx, "ADX")
    df["dmp"] = _require_indicator(dmp, "DMP")
    df["dmn"] = _require_indicator(dmn, "DMN")

    enriched_df = df.dropna().reset_index(drop=True)
    if len(enriched_df) < 2:
        raise ValueError("Data indikator kurang dari 2 candle setelah drop NA. Naikkan FETCH_LIMIT.")
    return enriched_df


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd = _ema(close, fast) - _ema(close, slow)
    macd_signal = _ema(macd, signal)
    macd_hist = macd - macd_signal
    return macd, macd_hist, macd_signal


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    atr = _atr(high, low, close, length)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    return adx, plus_di, minus_di
