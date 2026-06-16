from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

import requests

from src.action_call import build_action_call
from src.analyzer import AnalysisResult

VALID_AI_DECISIONS = {"APPROVE", "REJECT", "WAIT"}


@dataclass(frozen=True)
class AIModelSettings:
    enabled: bool
    provider: str
    api_key: str | None
    model: str
    base_url: str | None
    timeout: int
    min_score: float


@dataclass(frozen=True)
class AIReview:
    provider: str
    model: str
    decision: str
    score: float
    reason: str
    raw_response: str | None = None


def review_action_call(result: AnalysisResult, settings: AIModelSettings) -> AIReview | None:
    action_call = build_action_call(result)
    if not settings.enabled or action_call is None:
        return None

    payload = build_ai_payload(result)
    try:
        if settings.provider == "gemini":
            return _review_with_gemini(payload, settings)
        if settings.provider == "openai_compatible":
            return _review_with_openai_compatible(payload, settings)
        if settings.provider == "custom":
            return _review_with_custom(payload, settings)
        raise ValueError(f"AI_MODEL_PROVIDER tidak didukung: {settings.provider}")
    except Exception as error:
        return AIReview(
            provider=settings.provider,
            model=settings.model,
            decision="WAIT",
            score=0.0,
            reason=f"AI review error: {error}",
            raw_response=None,
        )


def build_ai_payload(result: AnalysisResult) -> dict[str, Any]:
    action_call = build_action_call(result)
    if action_call is None:
        raise ValueError("Action call tidak tersedia")

    order_block = result.order_block
    return {
        "task": "Review crypto trading action call. Return APPROVE, REJECT, or WAIT with score 0-1.",
        "action_call": asdict(action_call),
        "market_state": {
            "trend": result.trend,
            "regime": result.regime,
            "bias": result.bias,
            "signal": result.signal,
        },
        "indicators": {
            "rsi": result.rsi,
            "macd": result.macd,
            "atr": result.atr,
            "adx": result.adx,
        },
        "structure": {
            "support": result.support,
            "resistance": result.resistance,
            "fibonacci": result.fibonacci,
            "liquidity_sweep": result.liquidity_sweep,
            "order_block": {
                "kind": order_block.kind,
                "low": order_block.low,
                "high": order_block.high,
            }
            if order_block
            else None,
        },
        "reasons": result.reasons,
        "required_json_schema": {
            "decision": "APPROVE|REJECT|WAIT",
            "score": "float 0.0-1.0",
            "reason": "short explanation",
        },
    }


def format_ai_review(review: AIReview | None) -> str:
    if review is None:
        return "AI Review: Disabled"

    return f"""
AI Review:
Provider: {review.provider}
Model: {review.model}
Decision: {review.decision}
Score: {review.score}
Reason: {review.reason}
""".strip()


def ai_review_to_dict(review: AIReview | None) -> dict[str, Any]:
    if review is None:
        return {
            "ai_provider": None,
            "ai_model": None,
            "ai_decision": None,
            "ai_score": None,
            "ai_reason": None,
            "ai_raw_response": None,
        }

    return {
        "ai_provider": review.provider,
        "ai_model": review.model,
        "ai_decision": review.decision,
        "ai_score": review.score,
        "ai_reason": review.reason,
        "ai_raw_response": review.raw_response,
    }


def _review_with_gemini(payload: dict[str, Any], settings: AIModelSettings) -> AIReview:
    if not settings.api_key:
        raise ValueError("GEMINI/API key kosong")

    model = settings.model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        url,
        params={"key": settings.api_key},
        json={
            "contents": [
                {
                    "parts": [
                        {
                            "text": _build_review_prompt(payload),
                        }
                    ]
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        },
        timeout=settings.timeout,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_review_json(text, settings.provider, model)


def _review_with_openai_compatible(payload: dict[str, Any], settings: AIModelSettings) -> AIReview:
    if not settings.api_key:
        raise ValueError("AI_MODEL_API_KEY kosong")
    if not settings.base_url:
        raise ValueError("AI_MODEL_BASE_URL kosong")

    url = settings.base_url.rstrip("/") + "/chat/completions"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.model,
            "messages": [
                {"role": "system", "content": "You are a strict crypto trade risk reviewer. Return JSON only."},
                {"role": "user", "content": _build_review_prompt(payload)},
            ],
            "temperature": 0.1,
        },
        timeout=settings.timeout,
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"]["content"]
    return _parse_review_json(text, settings.provider, settings.model)


def _review_with_custom(payload: dict[str, Any], settings: AIModelSettings) -> AIReview:
    if not settings.base_url:
        raise ValueError("AI_MODEL_BASE_URL kosong")

    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    response = requests.post(
        settings.base_url,
        headers=headers,
        json={"model": settings.model, "payload": payload},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    data = response.json()
    return _review_from_dict(data, settings.provider, settings.model, raw_response=json.dumps(data, ensure_ascii=False))


def _build_review_prompt(payload: dict[str, Any]) -> str:
    return (
        "Evaluate this crypto action call for risk quality. "
        "Return JSON only with keys: decision, score, reason. "
        "Decision must be APPROVE, REJECT, or WAIT. Score must be 0.0 to 1.0.\n\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def _parse_review_json(text: str, provider: str, model: str) -> AIReview:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    data = json.loads(cleaned)
    return _review_from_dict(data, provider, model, raw_response=text)


def _review_from_dict(data: dict[str, Any], provider: str, model: str, raw_response: str | None) -> AIReview:
    decision = str(data.get("decision", "WAIT")).upper()
    if decision not in VALID_AI_DECISIONS:
        decision = "WAIT"

    score = float(data.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    reason = str(data.get("reason", "No reason"))

    return AIReview(
        provider=provider,
        model=model,
        decision=decision,
        score=round(score, 4),
        reason=reason,
        raw_response=raw_response,
    )
