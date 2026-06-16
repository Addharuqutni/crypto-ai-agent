from __future__ import annotations

import csv
import io
import json
from typing import Any

TRAINING_FEATURE_FIELDS = [
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
    "ai_decision",
    "ai_score",
]

TRAINING_TARGET_FIELDS = ["label", "pnl_percent"]
TRAINING_METADATA_FIELDS = ["created_at", "outcome_status", "outcome_price", "outcome_at", "ai_reason", "reasons"]
TRAINING_EXPORT_FIELDS = [*TRAINING_METADATA_FIELDS, *TRAINING_FEATURE_FIELDS, *TRAINING_TARGET_FIELDS]


def build_training_rows(rows: list[dict[str, Any]], labelled_only: bool = True) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        label = row.get("label")
        if labelled_only and label not in {"WIN", "LOSS"}:
            continue
        item = {field: row.get(field) for field in TRAINING_EXPORT_FIELDS}
        item["target"] = 1 if label == "WIN" else 0 if label == "LOSS" else None
        item["reasons"] = " | ".join(row.get("reasons") or []) if isinstance(row.get("reasons"), list) else row.get("reasons")
        output.append(item)
    return output


def rows_to_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    fieldnames = [*TRAINING_EXPORT_FIELDS, "target"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in fieldnames})
    return buffer.getvalue()
