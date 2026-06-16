import pandas as pd
import pandas_ta as ta


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

    df["ema_fast"] = _require_indicator(ta.ema(df["close"], length=indicators["ema_fast"]), "EMA fast")
    df["ema_mid"] = _require_indicator(ta.ema(df["close"], length=indicators["ema_mid"]), "EMA mid")
    df["ema_slow"] = _require_indicator(ta.ema(df["close"], length=indicators["ema_slow"]), "EMA slow")
    df["rsi"] = _require_indicator(ta.rsi(df["close"], length=indicators["rsi_length"]), "RSI")
    df["atr"] = _require_indicator(ta.atr(df["high"], df["low"], df["close"], length=indicators["atr_length"]), "ATR")

    macd = _require_indicator(
        ta.macd(
            df["close"],
            fast=indicators["macd_fast"],
            slow=indicators["macd_slow"],
            signal=indicators["macd_signal"],
        ),
        "MACD",
    )
    df["macd"] = macd.iloc[:, 0]
    df["macd_hist"] = macd.iloc[:, 1]
    df["macd_signal"] = macd.iloc[:, 2]

    adx = _require_indicator(
        ta.adx(
            df["high"],
            df["low"],
            df["close"],
            length=indicators["adx_length"],
        ),
        "ADX",
    )
    df["adx"] = adx.iloc[:, 0]
    df["dmp"] = adx.iloc[:, 1]
    df["dmn"] = adx.iloc[:, 2]

    enriched_df = df.dropna().reset_index(drop=True)
    if len(enriched_df) < 2:
        raise ValueError("Data indikator kurang dari 2 candle setelah drop NA. Naikkan FETCH_LIMIT.")
    return enriched_df
