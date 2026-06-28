from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from src.config import PROJECT_ROOT
from src.dataset import DEFAULT_JSONL_PATH
from src.evaluator import load_action_call_rows

DEFAULT_LEARNED_RULES_PATH = PROJECT_ROOT / "learned_action_rules.json"
WIN_LABEL = "WIN"
LOSS_LABEL = "LOSS"
MIN_CLOSED_TRADES = 20
MIN_SIGNAL_TRADES = 5
MIN_ACCEPTABLE_WINRATE = 0.45
DEFAULT_MIN_RISK_REWARD = 1.0


def load_learned_rules(path: str | Path = DEFAULT_LEARNED_RULES_PATH) -> dict[str, Any]:
    rules_path = Path(path)
    if not rules_path.exists():
        return _default_rules()
    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_rules()
    if not isinstance(data, dict):
        return _default_rules()
    defaults = _default_rules()
    defaults.update(data)
    defaults.setdefault("filters", {})
    defaults["filters"].setdefault("blocked_signals", [])
    return defaults


def update_learned_rules(
    jsonl_path: str | Path = DEFAULT_JSONL_PATH,
    output_path: str | Path = DEFAULT_LEARNED_RULES_PATH,
) -> dict[str, Any]:
    rows = load_action_call_rows(jsonl_path)
    closed_rows = [row for row in rows if row.get("label") in {WIN_LABEL, LOSS_LABEL}]
    rules = learn_rules_from_rows(closed_rows)
    Path(output_path).write_text(json.dumps(rules, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return rules


def learn_rules_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    wins = sum(1 for row in rows if row.get("label") == WIN_LABEL)
    losses = sum(1 for row in rows if row.get("label") == LOSS_LABEL)
    winrate = round(wins / total, 4) if total else None

    rules = _default_rules()
    rules.update(
        {
            "updated_at": datetime.now(UTC).isoformat(),
            "sample_size": total,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "status": "learning" if total >= MIN_CLOSED_TRADES else "insufficient_data",
        }
    )

    if total < MIN_CLOSED_TRADES:
        rules["notes"].append(f"Need at least {MIN_CLOSED_TRADES} closed action calls before strict self-learning filters activate.")
        return rules

    win_rows = [row for row in rows if row.get("label") == WIN_LABEL]
    signal_stats = _group_stats(rows, "signal")
    blocked_signals = [
        signal
        for signal, stats in signal_stats.items()
        if stats["total"] >= MIN_SIGNAL_TRADES and stats["winrate"] < MIN_ACCEPTABLE_WINRATE
    ]

    learned_min_rr = _learn_min_threshold(win_rows, "risk_reward", fallback=DEFAULT_MIN_RISK_REWARD)
    learned_min_adx = _learn_min_threshold(win_rows, "adx", fallback=None)
    learned_rsi_min, learned_rsi_max = _learn_range(win_rows, "rsi")

    filters = {
        "min_risk_reward": learned_min_rr,
        "min_adx": learned_min_adx,
        "rsi_min": learned_rsi_min,
        "rsi_max": learned_rsi_max,
        "blocked_signals": blocked_signals,
    }
    rules["filters"] = filters
    rules["signal_stats"] = signal_stats
    rules["notes"].append("Self-learning filters come from historical WIN/LOSS rows. They reduce low-quality action calls, not guarantee profit.")
    return rules


def should_allow_action_call(row: dict[str, Any], rules: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    learned = rules or load_learned_rules()
    if learned.get("status") != "learning":
        return True, []

    filters = learned.get("filters") or {}
    reasons: list[str] = []
    signal = row.get("signal")
    if signal in set(filters.get("blocked_signals") or []):
        reasons.append(f"Blocked by self-learning: signal {signal} has low historical winrate")

    min_rr = filters.get("min_risk_reward")
    risk_reward = _to_float(row.get("risk_reward"))
    if min_rr is not None and risk_reward is not None and risk_reward < float(min_rr):
        reasons.append(f"Blocked by self-learning: risk_reward {risk_reward} < learned minimum {min_rr}")

    min_adx = filters.get("min_adx")
    adx = _to_float(row.get("adx"))
    if min_adx is not None and adx is not None and adx < float(min_adx):
        reasons.append(f"Blocked by self-learning: adx {adx} < learned minimum {min_adx}")

    rsi = _to_float(row.get("rsi"))
    rsi_min = filters.get("rsi_min")
    rsi_max = filters.get("rsi_max")
    if rsi is not None and rsi_min is not None and rsi < float(rsi_min):
        reasons.append(f"Blocked by self-learning: rsi {rsi} < learned minimum {rsi_min}")
    if rsi is not None and rsi_max is not None and rsi > float(rsi_max):
        reasons.append(f"Blocked by self-learning: rsi {rsi} > learned maximum {rsi_max}")

    return not reasons, reasons


def _default_rules() -> dict[str, Any]:
    return {
        "updated_at": None,
        "status": "insufficient_data",
        "sample_size": 0,
        "wins": 0,
        "losses": 0,
        "winrate": None,
        "filters": {
            "min_risk_reward": DEFAULT_MIN_RISK_REWARD,
            "min_adx": None,
            "rsi_min": None,
            "rsi_max": None,
            "blocked_signals": [],
        },
        "signal_stats": {},
        "notes": [],
    }


def _group_stats(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    stats: dict[str, dict[str, Any]] = {}
    for name, items in grouped.items():
        total = len(items)
        wins = sum(1 for row in items if row.get("label") == WIN_LABEL)
        losses = sum(1 for row in items if row.get("label") == LOSS_LABEL)
        stats[name] = {"total": total, "wins": wins, "losses": losses, "winrate": round(wins / total, 4) if total else 0.0}
    return stats


def _learn_min_threshold(rows: list[dict[str, Any]], field: str, fallback: float | None) -> float | None:
    values = _numeric_values(rows, field)
    if not values:
        return fallback
    return round(float(median(values)), 4)


def _learn_range(rows: list[dict[str, Any]], field: str) -> tuple[float | None, float | None]:
    values = _numeric_values(rows, field)
    if len(values) < 5:
        return None, None
    low_index = max(0, int(len(values) * 0.1) - 1)
    high_index = min(len(values) - 1, int(len(values) * 0.9))
    return round(float(values[low_index]), 4), round(float(values[high_index]), 4)


def _numeric_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _to_float(row.get(field))
        if value is not None:
            values.append(value)
    return sorted(values)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
