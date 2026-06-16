from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.action_call import build_action_call
from src.ai_model import AIReview, ai_review_to_dict
from src.analyzer import AnalysisResult
from src.config import PROJECT_ROOT, load_settings

DEFAULT_DATASET_DIR = PROJECT_ROOT / "datasets"
DEFAULT_JSONL_PATH = DEFAULT_DATASET_DIR / "action_calls.jsonl"
DEFAULT_CSV_PATH = DEFAULT_DATASET_DIR / "action_calls.csv"

CSV_FIELDS = [
    "created_at",
    "symbol",
    "timeframe",
    "action",
    "signal",
    "status",
    "entry_price",
    "realtime_price",
    "take_profit",
    "stop_loss",
    "risk_reward",
    "trend",
    "regime",
    "bias",
    "rsi",
    "macd",
    "atr",
    "adx",
    "support",
    "resistance",
    "liquidity_sweep",
    "order_block_kind",
    "order_block_low",
    "order_block_high",
    "reasons",
    "ai_provider",
    "ai_model",
    "ai_decision",
    "ai_score",
    "ai_reason",
    "outcome_status",
    "outcome_price",
    "outcome_at",
    "pnl_percent",
    "label",
]


def build_action_call_dataset_row(
    result: AnalysisResult,
    ai_review: AIReview | None = None,
    realtime_price: float | None = None,
) -> dict[str, Any] | None:
    action_call = build_action_call(result, realtime_price)
    if action_call is None:
        return None

    order_block = result.order_block
    return {
        "created_at": datetime.now(UTC).isoformat(),
        **asdict(action_call),
        "trend": result.trend,
        "regime": result.regime,
        "bias": result.bias,
        "rsi": result.rsi,
        "macd": result.macd,
        "atr": result.atr,
        "adx": result.adx,
        "support": result.support,
        "resistance": result.resistance,
        "fibonacci": result.fibonacci,
        "liquidity_sweep": result.liquidity_sweep,
        "order_block_kind": order_block.kind if order_block else None,
        "order_block_low": order_block.low if order_block else None,
        "order_block_high": order_block.high if order_block else None,
        "reasons": result.reasons,
        **ai_review_to_dict(ai_review),
        "outcome_status": "PENDING",
        "outcome_price": None,
        "outcome_at": None,
        "pnl_percent": None,
        "label": None,
    }


def save_action_call_dataset(
    result: AnalysisResult,
    ai_review: AIReview | None = None,
    realtime_price: float | None = None,
    jsonl_path: str | Path = DEFAULT_JSONL_PATH,
    csv_path: str | Path = DEFAULT_CSV_PATH,
    mirror_postgres: bool = True,
) -> dict[str, Any] | None:
    row = build_action_call_dataset_row(result, ai_review, realtime_price)
    if row is None:
        return None

    jsonl_file = Path(jsonl_path)
    csv_file = Path(csv_path)
    jsonl_file.parent.mkdir(parents=True, exist_ok=True)
    csv_file.parent.mkdir(parents=True, exist_ok=True)

    with jsonl_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    csv_row = _to_csv_row(row)
    write_header = not csv_file.exists() or csv_file.stat().st_size == 0
    with csv_file.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(csv_row)

    if mirror_postgres:
        _mirror_row_to_postgres(row)
    return row


def rewrite_action_call_csv(rows: list[dict[str, Any]], csv_path: str | Path = DEFAULT_CSV_PATH) -> None:
    csv_file = Path(csv_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_to_csv_row(row))


def _to_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    csv_row = {field: row.get(field) for field in CSV_FIELDS}
    csv_row["reasons"] = " | ".join(row.get("reasons") or [])
    return csv_row


def save_action_call_rows_to_postgres(rows: list[dict[str, Any]]) -> None:
    settings = load_settings()
    if not rows or not settings.database_enabled or not settings.database_url:
        return
    from src.db import upsert_action_calls

    upsert_action_calls(settings.database_url, rows)


def _mirror_row_to_postgres(row: dict[str, Any]) -> None:
    try:
        save_action_call_rows_to_postgres([row])
    except Exception as error:
        row["database_error"] = str(error)
