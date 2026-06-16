from dataclasses import dataclass
import pandas as pd


@dataclass
class Zone:
    low: float
    high: float
    kind: str


@dataclass
class AnalysisResult:
    symbol: str
    timeframe: str
    price: float
    trend: str
    regime: str
    signal: str
    bias: str
    rsi: float
    macd: float
    atr: float
    adx: float
    support: float
    resistance: float
    fibonacci: dict[str, float]
    order_block: Zone | None
    liquidity_sweep: str | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    reasons: list[str]


def round_float(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def detect_support_resistance(df: pd.DataFrame, lookback: int) -> tuple[float, float]:
    recent = df.tail(lookback)
    return float(recent["low"].min()), float(recent["high"].max())


def detect_fibonacci(df: pd.DataFrame, lookback: int) -> dict[str, float]:
    recent = df.tail(lookback)
    swing_low = float(recent["low"].min())
    swing_high = float(recent["high"].max())
    diff = swing_high - swing_low

    return {
        "0.0": swing_high,
        "0.236": swing_high - diff * 0.236,
        "0.382": swing_high - diff * 0.382,
        "0.5": swing_high - diff * 0.5,
        "0.618": swing_high - diff * 0.618,
        "0.786": swing_high - diff * 0.786,
        "1.0": swing_low,
    }


def detect_trend_and_regime(latest: pd.Series, rules: dict) -> tuple[str, str, str]:
    ema_bullish = latest["ema_fast"] > latest["ema_mid"] > latest["ema_slow"]
    ema_bearish = latest["ema_fast"] < latest["ema_mid"] < latest["ema_slow"]
    adx = latest["adx"]
    dmp = latest["dmp"]
    dmn = latest["dmn"]

    if ema_bullish and dmp > dmn:
        trend = "UPTREND"
        bias = "BULLISH"
    elif ema_bearish and dmn > dmp:
        trend = "DOWNTREND"
        bias = "BEARISH"
    else:
        trend = "SIDEWAYS"
        bias = "NEUTRAL"

    if adx >= rules["adx_trend_threshold"]:
        regime = "TRENDING"
    elif adx <= rules["adx_range_threshold"]:
        regime = "RANGING"
    else:
        regime = "TRANSITION"

    return trend, regime, bias


def detect_order_block(df: pd.DataFrame, lookback: int) -> Zone | None:
    recent = df.tail(lookback).reset_index(drop=True)
    avg_body = (recent["close"] - recent["open"]).abs().mean()

    for idx in range(len(recent) - 3, 2, -1):
        candle = recent.iloc[idx]
        next_candle = recent.iloc[idx + 1]
        body = abs(candle["close"] - candle["open"])
        displacement = abs(next_candle["close"] - next_candle["open"])

        if body == 0 or displacement < avg_body * 1.8:
            continue

        bearish_candle_before_bullish_impulse = candle["close"] < candle["open"] and next_candle["close"] > next_candle["open"]
        bullish_candle_before_bearish_impulse = candle["close"] > candle["open"] and next_candle["close"] < next_candle["open"]

        if bearish_candle_before_bullish_impulse:
            return Zone(low=float(candle["low"]), high=float(candle["high"]), kind="BULLISH_ORDER_BLOCK")
        if bullish_candle_before_bearish_impulse:
            return Zone(low=float(candle["low"]), high=float(candle["high"]), kind="BEARISH_ORDER_BLOCK")

    return None


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int) -> str | None:
    if len(df) < lookback + 2:
        return None

    previous = df.iloc[-lookback - 1:-1]
    latest = df.iloc[-1]
    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    swept_high_rejected = latest["high"] > previous_high and latest["close"] < previous_high
    swept_low_reclaimed = latest["low"] < previous_low and latest["close"] > previous_low

    if swept_high_rejected:
        return "BUY_SIDE_LIQUIDITY_SWEEP"
    if swept_low_reclaimed:
        return "SELL_SIDE_LIQUIDITY_SWEEP"
    return None


def calculate_risk_plan(price: float, support: float, resistance: float, bias: str, atr: float) -> tuple[float | None, float | None, float | None]:
    if bias == "BULLISH":
        stop_loss = min(support, price - atr * 1.5)
        take_profit = resistance
        risk = price - stop_loss
        reward = take_profit - price
    elif bias == "BEARISH":
        stop_loss = max(resistance, price + atr * 1.5)
        take_profit = support
        risk = stop_loss - price
        reward = price - take_profit
    else:
        return None, None, None

    if risk <= 0 or reward <= 0:
        return None, None, None

    return stop_loss, take_profit, round(reward / risk, 2)


def analyze(symbol: str, timeframe: str, df: pd.DataFrame, config: dict) -> AnalysisResult:
    if len(df) < 2:
        raise ValueError("Analisis membutuhkan minimal 2 candle dengan indikator lengkap")

    rules = config["rules"]
    structure = config["structure"]
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    price = float(latest["close"])
    reasons: list[str] = []

    trend, regime, bias = detect_trend_and_regime(latest, rules)
    support, resistance = detect_support_resistance(df, structure["sr_lookback"])
    fibonacci = detect_fibonacci(df, structure["fibonacci_lookback"])
    order_block = detect_order_block(df, structure["order_block_lookback"])
    liquidity_sweep = detect_liquidity_sweep(df, structure["liquidity_sweep_lookback"])

    macd_bullish_cross = previous["macd"] < previous["macd_signal"] and latest["macd"] > latest["macd_signal"]
    macd_bearish_cross = previous["macd"] > previous["macd_signal"] and latest["macd"] < latest["macd_signal"]

    if latest["rsi"] <= rules["rsi_oversold"]:
        reasons.append("RSI oversold")
    elif latest["rsi"] >= rules["rsi_overbought"]:
        reasons.append("RSI overbought")

    if macd_bullish_cross:
        reasons.append("MACD bullish crossover")
    elif macd_bearish_cross:
        reasons.append("MACD bearish crossover")

    if liquidity_sweep:
        reasons.append(liquidity_sweep)

    if order_block:
        in_ob = order_block.low <= price <= order_block.high
        reasons.append(f"Detected {order_block.kind}" + ("; price inside zone" if in_ob else ""))

    nearest_fib = min(fibonacci.items(), key=lambda item: abs(price - item[1]))
    reasons.append(f"Nearest Fibonacci: {nearest_fib[0]} at {round(nearest_fib[1], 4)}")

    signal = "HOLD"
    if bias == "BULLISH" and regime == "TRENDING" and macd_bullish_cross:
        signal = "BULLISH CONTINUATION"
    elif bias == "BEARISH" and regime == "TRENDING" and macd_bearish_cross:
        signal = "BEARISH CONTINUATION"
    elif liquidity_sweep == "SELL_SIDE_LIQUIDITY_SWEEP" and macd_bullish_cross:
        signal = "BUY WATCH"
    elif liquidity_sweep == "BUY_SIDE_LIQUIDITY_SWEEP" and macd_bearish_cross:
        signal = "SELL WATCH"
    elif rules.get("enable_trend_following_calls", True) and bias == "BULLISH" and regime in {"TRENDING", "TRANSITION"} and latest["macd"] > latest["macd_signal"] and latest["rsi"] < rules.get("rsi_overbought", 70):
        signal = "BULLISH TREND FOLLOW"
        reasons.append("Trend-following bullish setup")
    elif rules.get("enable_trend_following_calls", True) and bias == "BEARISH" and regime in {"TRENDING", "TRANSITION"} and latest["macd"] < latest["macd_signal"] and latest["rsi"] > rules.get("rsi_oversold", 30):
        signal = "BEARISH TREND FOLLOW"
        reasons.append("Trend-following bearish setup")

    stop_loss, take_profit, risk_reward = calculate_risk_plan(price, support, resistance, bias, float(latest["atr"]))
    if risk_reward and risk_reward < rules["min_risk_reward"]:
        reasons.append(f"Risk/reward kurang ideal: {risk_reward}")

    return AnalysisResult(
        symbol=symbol,
        timeframe=timeframe,
        price=round_float(price),
        trend=trend,
        regime=regime,
        signal=signal,
        bias=bias,
        rsi=round_float(latest["rsi"], 2),
        macd=round_float(latest["macd"], 4),
        atr=round_float(latest["atr"], 4),
        adx=round_float(latest["adx"], 2),
        support=round_float(support),
        resistance=round_float(resistance),
        fibonacci={level: round_float(value) for level, value in fibonacci.items()},
        order_block=Zone(round_float(order_block.low), round_float(order_block.high), order_block.kind) if order_block else None,
        liquidity_sweep=liquidity_sweep,
        stop_loss=round_float(stop_loss),
        take_profit=round_float(take_profit),
        risk_reward=risk_reward,
        reasons=reasons,
    )
