from dataclasses import dataclass

from src.analyzer import AnalysisResult

ACTION_SIGNALS = {
    "BUY WATCH": "LONG",
    "BULLISH CONTINUATION": "LONG",
    "BULLISH TREND FOLLOW": "LONG",
    "MTF BULLISH ACTION CALL": "LONG",
    "SELL WATCH": "SHORT",
    "BEARISH CONTINUATION": "SHORT",
    "BEARISH TREND FOLLOW": "SHORT",
    "MTF BEARISH ACTION CALL": "SHORT",
}


@dataclass(frozen=True)
class ActionCall:
    symbol: str
    timeframe: str
    action: str
    signal: str
    entry_price: float
    realtime_price: float | None
    take_profit: float
    stop_loss: float
    risk_reward: float | None
    status: str


def build_action_call(result: AnalysisResult, realtime_price: float | None = None) -> ActionCall | None:
    action = ACTION_SIGNALS.get(result.signal)
    if not action:
        return None

    if result.price is None or result.take_profit is None or result.stop_loss is None:
        return None
    if result.risk_reward is not None and result.risk_reward < 1.0:
        return None

    return ActionCall(
        symbol=result.symbol,
        timeframe=result.timeframe,
        action=action,
        signal=result.signal,
        entry_price=result.price,
        realtime_price=realtime_price,
        take_profit=result.take_profit,
        stop_loss=result.stop_loss,
        risk_reward=result.risk_reward,
        status="WAIT_CONFIRMATION" if result.signal.endswith("WATCH") else "READY",
    )


def format_action_call(result: AnalysisResult, realtime_price: float | None = None) -> str:
    action_call = build_action_call(result, realtime_price)
    if action_call is None:
        return "Action Call: None"

    return f"""
Action Call:
Symbol: {action_call.symbol}
Timeframe: {action_call.timeframe}
Action: {action_call.action}
Signal: {action_call.signal}
Status: {action_call.status}
Entry Price: {action_call.entry_price}
Realtime Price: {action_call.realtime_price}
Take Profit: {action_call.take_profit}
Stop Loss: {action_call.stop_loss}
Risk/Reward: {action_call.risk_reward}
""".strip()


def action_call_to_dict(result: AnalysisResult, realtime_price: float | None = None) -> dict | None:
    action_call = build_action_call(result, realtime_price)
    if action_call is None:
        return None

    return {
        "symbol": action_call.symbol,
        "timeframe": action_call.timeframe,
        "action": action_call.action,
        "signal": action_call.signal,
        "status": action_call.status,
        "entry_price": action_call.entry_price,
        "realtime_price": action_call.realtime_price,
        "take_profit": action_call.take_profit,
        "stop_loss": action_call.stop_loss,
        "risk_reward": action_call.risk_reward,
    }
