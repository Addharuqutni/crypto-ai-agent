from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ACTION_CALL_COLUMNS = [
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
UPSERT_COLUMNS = [
    "row_key",
    *ACTION_CALL_COLUMNS,
    "fibonacci",
    "reasons",
    "ai_raw_response",
    "evaluation_note",
    "evaluation_error",
    "raw",
]
_DB_INITIALIZED: set[str] = set()


@contextmanager
def db_connection(database_url: str) -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(database_url, row_factory=dict_row, prepare_threshold=None)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(database_url: str, force: bool = False) -> None:
    if not force and database_url in _DB_INITIALIZED:
        return

    with db_connection(database_url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_calls (
                id BIGSERIAL PRIMARY KEY,
                row_key TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ,
                symbol TEXT NOT NULL,
                timeframe TEXT,
                action TEXT,
                signal TEXT,
                status TEXT,
                entry_price DOUBLE PRECISION,
                realtime_price DOUBLE PRECISION,
                take_profit DOUBLE PRECISION,
                stop_loss DOUBLE PRECISION,
                risk_reward DOUBLE PRECISION,
                trend TEXT,
                regime TEXT,
                bias TEXT,
                rsi DOUBLE PRECISION,
                macd DOUBLE PRECISION,
                atr DOUBLE PRECISION,
                adx DOUBLE PRECISION,
                support DOUBLE PRECISION,
                resistance DOUBLE PRECISION,
                fibonacci JSONB,
                liquidity_sweep TEXT,
                order_block_kind TEXT,
                order_block_low DOUBLE PRECISION,
                order_block_high DOUBLE PRECISION,
                reasons JSONB,
                ai_provider TEXT,
                ai_model TEXT,
                ai_decision TEXT,
                ai_score DOUBLE PRECISION,
                ai_reason TEXT,
                ai_raw_response JSONB,
                outcome_status TEXT,
                outcome_price DOUBLE PRECISION,
                outcome_at TIMESTAMPTZ,
                pnl_percent DOUBLE PRECISION,
                label TEXT,
                evaluation_note TEXT,
                evaluation_error TEXT,
                raw JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute("ALTER TABLE action_calls ADD COLUMN IF NOT EXISTS realtime_price DOUBLE PRECISION")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_action_calls_created_at ON action_calls (created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_action_calls_label ON action_calls (label)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_action_calls_symbol ON action_calls (symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_action_calls_action ON action_calls (action)")
    _DB_INITIALIZED.add(database_url)


def upsert_action_call(database_url: str, row: dict[str, Any]) -> None:
    upsert_action_calls(database_url, [row])


def upsert_action_calls(database_url: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    init_db(database_url)
    placeholders = ", ".join(["%s"] * len(UPSERT_COLUMNS))
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in UPSERT_COLUMNS if column != "row_key")
    sql = f"""
        INSERT INTO action_calls ({', '.join(UPSERT_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT (row_key) DO UPDATE SET
            {updates},
            updated_at = NOW()
    """
    values = [_row_values(row) for row in rows]

    with db_connection(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, values)


def fetch_action_calls(database_url: str, limit: int = 500, labelled_only: bool = False) -> list[dict[str, Any]]:
    init_db(database_url)
    limit = max(1, min(limit, 10000))
    where = "WHERE label IN ('WIN', 'LOSS')" if labelled_only else ""
    with db_connection(database_url) as conn:
        result = conn.execute(
            f"""
            SELECT raw || jsonb_build_object(
                'outcome_status', outcome_status,
                'outcome_price', outcome_price,
                'outcome_at', outcome_at,
                'pnl_percent', pnl_percent,
                'label', label,
                'evaluation_note', evaluation_note,
                'evaluation_error', evaluation_error
            ) AS row
            FROM action_calls
            {where}
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(item["row"]) for item in result.fetchall()]


def make_row_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(key) or "")
        for key in ("created_at", "symbol", "timeframe", "action", "entry_price", "take_profit", "stop_loss")
    )


def _row_values(row: dict[str, Any]) -> list[Any]:
    payload = _db_payload(row)
    return [make_row_key(row), *[payload.get(column) for column in ACTION_CALL_COLUMNS], payload.get("fibonacci"), payload.get("reasons"), payload.get("ai_raw_response"), payload.get("evaluation_note"), payload.get("evaluation_error"), Jsonb(row)]


def _db_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {key: row.get(key) for key in ACTION_CALL_COLUMNS}
    payload["fibonacci"] = Jsonb(row.get("fibonacci")) if row.get("fibonacci") is not None else None
    payload["reasons"] = Jsonb(row.get("reasons") or [])
    payload["ai_raw_response"] = Jsonb(row.get("ai_raw_response")) if row.get("ai_raw_response") is not None else None
    payload["evaluation_note"] = row.get("evaluation_note")
    payload["evaluation_error"] = row.get("evaluation_error")
    return payload
