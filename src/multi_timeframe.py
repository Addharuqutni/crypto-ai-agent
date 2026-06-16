from __future__ import annotations

from dataclasses import replace

from src.analyzer import AnalysisResult, analyze
from src.data import MarketDataClient
from src.indicators import add_indicators

ENTRY_TIMEFRAME = "5m"
CONFIRMATION_TIMEFRAMES = ("15m", "30m")
TREND_TIMEFRAMES = ("1h", "4h")


def analyze_multi_timeframe(
    symbol: str,
    client: MarketDataClient,
    config: dict,
    fetch_limit: int,
    entry_timeframe: str = ENTRY_TIMEFRAME,
    confirmation_timeframes: tuple[str, ...] = CONFIRMATION_TIMEFRAMES,
    trend_timeframes: tuple[str, ...] = TREND_TIMEFRAMES,
) -> AnalysisResult:
    """Build action call from 5m entry, 15m/30m confirmations, 1h/4h trend direction."""
    timeframes = tuple(dict.fromkeys((entry_timeframe, *confirmation_timeframes, *trend_timeframes)))
    analyses: dict[str, AnalysisResult] = {}

    for timeframe in timeframes:
        raw_df = client.fetch_ohlcv(symbol, timeframe, fetch_limit)
        df = add_indicators(raw_df, config)
        analyses[timeframe] = analyze(symbol, timeframe, df, config)

    trend_results = [analyses[timeframe] for timeframe in trend_timeframes]
    trend_biases = {result.bias for result in trend_results}
    trend_aligned = len(trend_biases) == 1 and "NEUTRAL" not in trend_biases
    trend_bias = next(iter(trend_biases)) if trend_aligned else "NEUTRAL"

    confirmation_results = [analyses[timeframe] for timeframe in confirmation_timeframes]
    confirmation_aligned = (
        trend_aligned
        and all(result.bias == trend_bias for result in confirmation_results)
        and all(result.regime in {"TRENDING", "TRANSITION"} for result in confirmation_results)
    )

    entry = analyses[entry_timeframe]
    chosen_entry = _choose_entry(entry, trend_bias, confirmation_aligned)

    if chosen_entry is None:
        reasons = [
            *_mtf_reasons(trend_results, confirmation_results, trend_aligned, confirmation_aligned),
            "No aligned 5m entry setup",
            *entry.reasons,
        ]
        return replace(
            entry,
            signal="HOLD",
            bias=trend_bias,
            reasons=reasons,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
        )

    signal = "MTF BULLISH ACTION CALL" if trend_bias == "BULLISH" else "MTF BEARISH ACTION CALL"
    reasons = [
        *_mtf_reasons(trend_results, confirmation_results, trend_aligned, confirmation_aligned),
        "Entry selected from 5m",
        *chosen_entry.reasons,
    ]
    return replace(
        chosen_entry,
        signal=signal,
        bias=trend_bias,
        trend="UPTREND" if trend_bias == "BULLISH" else "DOWNTREND",
        reasons=reasons,
    )


def _choose_entry(entry: AnalysisResult, trend_bias: str, confirmation_aligned: bool) -> AnalysisResult | None:
    if not confirmation_aligned:
        return None
    if entry.bias != trend_bias or entry.risk_reward is None:
        return None
    if entry.signal == "HOLD":
        return None
    return entry


def _mtf_reasons(
    trend_results: list[AnalysisResult],
    confirmation_results: list[AnalysisResult],
    trend_aligned: bool,
    confirmation_aligned: bool,
) -> list[str]:
    trend_summary = ", ".join(f"{result.timeframe}:{result.bias}/{result.regime}" for result in trend_results)
    confirmation_summary = ", ".join(
        f"{result.timeframe}:{result.bias}/{result.regime}" for result in confirmation_results
    )
    return [
        f"Trend direction 1h/4h: {trend_summary}",
        f"Trend aligned: {trend_aligned}",
        f"Confirmation 15m/30m: {confirmation_summary}",
        f"15m/30m aligned with trend: {confirmation_aligned}",
    ]


def is_multi_timeframe_enabled(config: dict) -> bool:
    return bool(config.get("rules", {}).get("enable_multi_timeframe_action_calls", True))
