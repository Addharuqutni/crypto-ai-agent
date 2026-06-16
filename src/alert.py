import requests
from src.action_call import format_action_call
from src.ai_model import AIReview, format_ai_review
from src.analyzer import AnalysisResult


def format_report(result: AnalysisResult, ai_review: AIReview | None = None, realtime_price: float | None = None) -> str:
    reasons = "\n".join(f"- {reason}" for reason in result.reasons) or "- Tidak ada sinyal kuat"
    fib_lines = "\n".join(f"- {level}: {value}" for level, value in result.fibonacci.items())
    action_call = format_action_call(result, realtime_price)
    ai_review_text = format_ai_review(ai_review)

    if result.order_block:
        order_block = f"{result.order_block.kind} [{result.order_block.low} - {result.order_block.high}]"
    else:
        order_block = "None"

    return f"""
Crypto Technical Analysis

Pair: {result.symbol}
Timeframe: {result.timeframe}
Price: {result.price}

Market State:
Trend: {result.trend}
Regime: {result.regime}
Bias: {result.bias}
Signal: {result.signal}

Indicators:
RSI: {result.rsi}
MACD: {result.macd}
ATR: {result.atr}
ADX: {result.adx}

Structure:
Support: {result.support}
Resistance: {result.resistance}
Order Block: {order_block}
Liquidity Sweep: {result.liquidity_sweep}

Fibonacci:
{fib_lines}

Risk Plan:
Stop Loss: {result.stop_loss}
Take Profit: {result.take_profit}
Risk/Reward: {result.risk_reward}

{action_call}

{ai_review_text}

Reasons:
{reasons}
""".strip()


def send_telegram(token: str | None, chat_id: str | None, message: str) -> None:
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
    response.raise_for_status()
