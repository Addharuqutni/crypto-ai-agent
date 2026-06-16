from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.dataset import DEFAULT_CSV_PATH, DEFAULT_JSONL_PATH, rewrite_action_call_csv
from src.config import load_settings
from src.data import MarketDataClient
from src.db import upsert_action_calls

EVALUATABLE_STATUSES = {None, "", "PENDING", "OPEN"}
WIN_LABEL = "WIN"
LOSS_LABEL = "LOSS"
OPEN_LABEL = "OPEN"
EXPIRED_LABEL = "EXPIRED"


def evaluate_pending_action_calls(
    exchange_name: str,
    timeframe: str,
    fetch_limit: int = 250,
    jsonl_path: str | Path = DEFAULT_JSONL_PATH,
    csv_path: str | Path = DEFAULT_CSV_PATH,
    max_rows: int | None = None,
) -> dict[str, int]:
    rows = load_action_call_rows(jsonl_path)
    if not rows:
        return {"total": 0, "pending": 0, "win": 0, "loss": 0, "open": 0, "expired": 0, "errors": 0}

    client = MarketDataClient(exchange_name)
    stats = {"total": len(rows), "pending": 0, "win": 0, "loss": 0, "open": 0, "expired": 0, "errors": 0}
    evaluated = 0

    for row in rows:
        if row.get("outcome_status") not in EVALUATABLE_STATUSES and row.get("label") != OPEN_LABEL:
            continue
        if max_rows is not None and evaluated >= max_rows:
            break

        stats["pending"] += 1
        evaluated += 1
        try:
            row_timeframe = str(row.get("timeframe") or timeframe)
            limit = max(fetch_limit, 50)
            candles = client.fetch_ohlcv(str(row["symbol"]), row_timeframe, limit=limit)
            outcome = evaluate_row_against_candles(row, candles)
            row.update(outcome)
            row.pop("evaluation_error", None)
            _increment_outcome_stats(stats, outcome["label"])
        except Exception as error:
            stats["errors"] += 1
            row["evaluation_error"] = str(error)

    changed_rows = [row for row in rows if row.get("outcome_status") in {"OPEN", "CLOSED"} or row.get("evaluation_error")]
    save_action_call_rows(rows, jsonl_path)
    rewrite_action_call_csv(rows, csv_path)
    _mirror_rows_to_postgres(changed_rows)
    return stats


def load_action_call_rows(jsonl_path: str | Path = DEFAULT_JSONL_PATH) -> list[dict[str, Any]]:
    path = Path(jsonl_path)
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def save_action_call_rows(rows: list[dict[str, Any]], jsonl_path: str | Path = DEFAULT_JSONL_PATH) -> None:
    path = Path(jsonl_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def evaluate_row_against_candles(row: dict[str, Any], candles: pd.DataFrame) -> dict[str, Any]:
    entry_time = _parse_datetime(row.get("created_at"))
    future_candles = candles[candles["timestamp"] > entry_time].copy()

    if future_candles.empty:
        return _open_outcome()

    action = str(row["action"]).upper()
    take_profit = float(row["take_profit"])
    stop_loss = float(row["stop_loss"])
    entry_price = float(row["entry_price"])

    for _, candle in future_candles.iterrows():
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        timestamp = candle["timestamp"].isoformat()

        if action == "LONG":
            hit_tp = high >= take_profit
            hit_sl = low <= stop_loss
        elif action == "SHORT":
            hit_tp = low <= take_profit
            hit_sl = high >= stop_loss
        else:
            raise ValueError(f"Action tidak valid: {action}")

        if hit_tp and hit_sl:
            return _closed_outcome(
                label=LOSS_LABEL,
                outcome_price=stop_loss,
                outcome_at=timestamp,
                pnl_percent=_calculate_pnl_percent(action, entry_price, stop_loss),
                note="TP dan SL tersentuh dalam candle yang sama; dilabeli LOSS konservatif",
            )
        if hit_tp:
            return _closed_outcome(
                label=WIN_LABEL,
                outcome_price=take_profit,
                outcome_at=timestamp,
                pnl_percent=_calculate_pnl_percent(action, entry_price, take_profit),
            )
        if hit_sl:
            return _closed_outcome(
                label=LOSS_LABEL,
                outcome_price=stop_loss,
                outcome_at=timestamp,
                pnl_percent=_calculate_pnl_percent(action, entry_price, stop_loss),
            )

    last_candle = future_candles.iloc[-1]
    return {
        "outcome_status": "OPEN",
        "outcome_price": float(last_candle["close"]),
        "outcome_at": last_candle["timestamp"].isoformat(),
        "pnl_percent": _calculate_pnl_percent(action, entry_price, float(last_candle["close"])),
        "label": OPEN_LABEL,
    }


def _parse_datetime(value: Any) -> pd.Timestamp:
    if not value:
        return pd.Timestamp(datetime.now(UTC))
    timestamp = pd.to_datetime(value, utc=True)
    if pd.isna(timestamp):
        return pd.Timestamp(datetime.now(UTC))
    return timestamp


def _open_outcome() -> dict[str, Any]:
    return {
        "outcome_status": "OPEN",
        "outcome_price": None,
        "outcome_at": None,
        "pnl_percent": None,
        "label": OPEN_LABEL,
    }


def _closed_outcome(label: str, outcome_price: float, outcome_at: str, pnl_percent: float, note: str | None = None) -> dict[str, Any]:
    outcome = {
        "outcome_status": "CLOSED",
        "outcome_price": outcome_price,
        "outcome_at": outcome_at,
        "pnl_percent": pnl_percent,
        "label": label,
    }
    if note:
        outcome["evaluation_note"] = note
    return outcome


def _calculate_pnl_percent(action: str, entry_price: float, outcome_price: float) -> float:
    if action == "LONG":
        pnl = (outcome_price - entry_price) / entry_price * 100
    else:
        pnl = (entry_price - outcome_price) / entry_price * 100
    return round(pnl, 4)


def _increment_outcome_stats(stats: dict[str, int], label: str) -> None:
    if label == WIN_LABEL:
        stats["win"] += 1
    elif label == LOSS_LABEL:
        stats["loss"] += 1
    elif label == OPEN_LABEL:
        stats["open"] += 1
    elif label == EXPIRED_LABEL:
        stats["expired"] += 1


def _mirror_rows_to_postgres(rows: list[dict[str, Any]]) -> None:
    settings = load_settings()
    if not settings.database_enabled or not settings.database_url:
        return
    upsert_action_calls(settings.database_url, rows)
