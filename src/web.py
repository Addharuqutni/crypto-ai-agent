from __future__ import annotations

import threading
from collections import Counter
from html import escape
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

from src.config import load_settings
from src.dataset import DEFAULT_JSONL_PATH
from src.data import MarketDataClient
from src.db import fetch_action_calls
from src.evaluator import evaluate_pending_action_calls, load_action_call_rows
from src.exporter import build_training_rows, rows_to_csv, rows_to_jsonl

app = FastAPI(title="Crypto AI Agent Dashboard")
_job_state: dict[str, Any] = {"scan_running": False, "evaluate_running": False, "last_scan": None, "last_evaluate": None}
_scheduler_started = False


def create_app() -> FastAPI:
    return app


@app.on_event("startup")
def startup_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return

    settings = load_settings()
    if not settings.dashboard_auto_scan and not settings.dashboard_auto_evaluate:
        return

    _scheduler_started = True
    threading.Thread(target=_dashboard_scheduler_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return dashboard()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    rows = _load_action_call_rows(limit=300)
    rows = _attach_realtime_prices(rows)
    stats = build_stats(rows)
    return _render_dashboard(rows, stats)


@app.get("/api/action-calls")
def api_action_calls(limit: int = 200) -> dict[str, Any]:
    limit = max(1, min(limit, 1000))
    rows = _load_action_call_rows(limit=limit)
    rows = _attach_realtime_prices(rows)
    return {"items": rows, "count": len(rows)}


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    rows = _load_action_call_rows(limit=10000)
    return build_stats(rows)


def _attach_realtime_prices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows

    settings = load_settings()
    symbols = sorted({str(row.get("symbol")) for row in rows if row.get("symbol") and row.get("action")})
    if not symbols:
        return rows

    prices: dict[str, float] = {}
    try:
        client = MarketDataClient(settings.exchange)
        for symbol in symbols:
            try:
                prices[symbol] = client.fetch_ticker_price(symbol)
            except Exception:
                continue
    except Exception:
        return rows

    enriched_rows = []
    for row in rows:
        item = dict(row)
        symbol = str(item.get("symbol") or "")
        if symbol in prices:
            item["realtime_price"] = prices[symbol]
        enriched_rows.append(item)
    return enriched_rows


@app.get("/api/jobs")
def api_jobs() -> dict[str, Any]:
    return _job_state


@app.get("/api/export/training.jsonl", response_class=PlainTextResponse)
def api_export_training_jsonl(limit: int = 10000, labelled_only: bool = True) -> str:
    rows = _load_rows_for_export(limit=limit, labelled_only=labelled_only)
    return rows_to_jsonl(build_training_rows(rows, labelled_only=labelled_only))


@app.get("/api/export/training.csv", response_class=PlainTextResponse)
def api_export_training_csv(limit: int = 10000, labelled_only: bool = True) -> str:
    rows = _load_rows_for_export(limit=limit, labelled_only=labelled_only)
    return rows_to_csv(build_training_rows(rows, labelled_only=labelled_only))


@app.post("/api/evaluate")
def api_evaluate() -> dict[str, Any]:
    if _job_state["evaluate_running"]:
        return {"started": False, "message": "evaluation already running", "job_state": _job_state}

    threading.Thread(target=_run_evaluate_job, daemon=True).start()
    return {"started": True, "job_state": _job_state}


@app.post("/api/scan")
def api_scan() -> dict[str, Any]:
    if _job_state["scan_running"]:
        return {"started": False, "message": "scan already running", "job_state": _job_state}

    threading.Thread(target=_run_scan_job, daemon=True).start()
    return {"started": True, "job_state": _job_state}


def _run_evaluate_job() -> None:
    _job_state["evaluate_running"] = True
    try:
        settings = load_settings()
        stats = evaluate_pending_action_calls(
            exchange_name=settings.exchange,
            timeframe=settings.timeframe,
            fetch_limit=settings.evaluation_fetch_limit,
            max_rows=settings.evaluation_max_rows,
        )
        _job_state["last_evaluate"] = stats
    except Exception as error:
        _job_state["last_evaluate"] = {"error": str(error)}
    finally:
        _job_state["evaluate_running"] = False


def _run_scan_job() -> None:
    _job_state["scan_running"] = True
    try:
        from main import scan_once

        scan_once()
        _job_state["last_scan"] = {"status": "completed"}
    except Exception as error:
        _job_state["last_scan"] = {"error": str(error)}
    finally:
        _job_state["scan_running"] = False


def _dashboard_scheduler_loop() -> None:
    import time

    last_scan = 0.0
    last_evaluate = 0.0
    while True:
        settings = load_settings()
        now = time.time()
        if settings.dashboard_auto_scan and not _job_state["scan_running"] and now - last_scan >= settings.dashboard_auto_scan_interval_seconds:
            last_scan = now
            threading.Thread(target=_run_scan_job, daemon=True).start()
        if settings.dashboard_auto_evaluate and not _job_state["evaluate_running"] and now - last_evaluate >= settings.dashboard_auto_evaluate_interval_seconds:
            last_evaluate = now
            threading.Thread(target=_run_evaluate_job, daemon=True).start()
        time.sleep(5)


def _load_action_call_rows(limit: int, labelled_only: bool = False) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 10000))
    settings = load_settings()
    if settings.database_enabled and settings.database_url:
        return fetch_action_calls(settings.database_url, limit=limit, labelled_only=labelled_only)

    rows = _latest_rows(load_action_call_rows(DEFAULT_JSONL_PATH), limit=limit)
    if labelled_only:
        rows = [row for row in rows if row.get("label") in {"WIN", "LOSS"}]
    return rows


def _load_rows_for_export(limit: int, labelled_only: bool) -> list[dict[str, Any]]:
    return _load_action_call_rows(limit=limit, labelled_only=labelled_only)


def build_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter((row.get("label") or "PENDING") for row in rows)
    actions = Counter((row.get("action") or "UNKNOWN") for row in rows)
    ai_decisions = Counter((row.get("ai_decision") or "NONE") for row in rows)
    closed = labels.get("WIN", 0) + labels.get("LOSS", 0)
    winrate = round(labels.get("WIN", 0) / closed * 100, 2) if closed else 0.0

    return {
        "total": len(rows),
        "win": labels.get("WIN", 0),
        "loss": labels.get("LOSS", 0),
        "open": labels.get("OPEN", 0),
        "pending": labels.get("PENDING", 0),
        "closed": closed,
        "winrate": winrate,
        "actions": dict(actions),
        "ai_decisions": dict(ai_decisions),
    }


def _latest_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)[:limit]


def _render_dashboard(rows: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    table_rows = "".join(_render_row(row) for row in rows)
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto AI Agent Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }}
    h1 {{ margin-bottom: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ background: #1e293b; padding: 16px; border-radius: 10px; border: 1px solid #334155; }}
    .card .label {{ color: #94a3b8; font-size: 12px; }}
    .card .value {{ font-size: 24px; font-weight: bold; margin-top: 6px; }}
    button {{ background: #2563eb; color: white; border: 0; padding: 10px 14px; border-radius: 8px; cursor: pointer; margin-right: 8px; }}
    button:hover {{ background: #1d4ed8; }}
    table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid #334155; padding: 10px; text-align: left; font-size: 13px; }}
    th {{ background: #0f172a; color: #93c5fd; position: sticky; top: 0; }}
    .WIN {{ color: #22c55e; font-weight: bold; }}
    .LOSS {{ color: #ef4444; font-weight: bold; }}
    .OPEN {{ color: #f59e0b; font-weight: bold; }}
    .PENDING {{ color: #94a3b8; font-weight: bold; }}
    .LONG {{ color: #22c55e; font-weight: bold; }}
    .SHORT {{ color: #ef4444; font-weight: bold; }}
    .muted {{ color: #94a3b8; }}
  </style>
</head>
<body>
  <h1>Crypto AI Agent Dashboard</h1>
  <div class="muted">Screening top 100, technical action call, monitoring TP/SL, AI review.</div>

  <div class="grid">
    {_stat_card('Total Calls', stats['total'])}
    {_stat_card('Winrate', str(stats['winrate']) + '%')}
    {_stat_card('WIN', stats['win'])}
    {_stat_card('LOSS', stats['loss'])}
    {_stat_card('OPEN', stats['open'])}
    {_stat_card('PENDING', stats['pending'])}
  </div>

  <p>
    <button onclick="runJob('/api/scan')">Run Scan</button>
    <button onclick="runJob('/api/evaluate')">Evaluate TP/SL</button>
    <button onclick="location.reload()">Refresh</button>
  </p>

  <table>
    <thead>
      <tr>
        <th>Created</th><th>Symbol</th><th>TF</th><th>Action</th><th>Signal</th>
        <th>Entry</th><th>Realtime</th><th>TP</th><th>SL</th><th>RR</th><th>Status</th><th>Label</th><th>PNL%</th>
        <th>AI</th><th>Score</th><th>Reason</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>

<script>
async function runJob(url) {{
  const res = await fetch(url, {{ method: 'POST' }});
  const data = await res.json();
  alert(JSON.stringify(data, null, 2));
}}
</script>
</body>
</html>
"""


def _stat_card(label: str, value: Any) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def _render_row(row: dict[str, Any]) -> str:
    label = escape(str(row.get("label") or "PENDING"))
    action = escape(str(row.get("action") or ""))
    return f"""
<tr>
  <td>{_short(row.get('created_at'))}</td>
  <td>{escape(str(row.get('symbol', '')))}</td>
  <td>{escape(str(row.get('timeframe', '')))}</td>
  <td class="{action}">{action}</td>
  <td>{escape(str(row.get('signal', '')))}</td>
  <td>{escape(str(row.get('entry_price', '')))}</td>
  <td>{escape(str(row.get('realtime_price', '')))}</td>
  <td>{escape(str(row.get('take_profit', '')))}</td>
  <td>{escape(str(row.get('stop_loss', '')))}</td>
  <td>{escape(str(row.get('risk_reward', '')))}</td>
  <td>{escape(str(row.get('outcome_status', '')))}</td>
  <td class="{label}">{label}</td>
  <td>{escape(str(row.get('pnl_percent', '')))}</td>
  <td>{escape(str(row.get('ai_decision') or 'NONE'))}</td>
  <td>{escape(str(row.get('ai_score') or ''))}</td>
  <td>{_short(row.get('ai_reason'), 80)}</td>
</tr>
"""


def _short(value: Any, limit: int = 24) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text if len(text) <= limit else text[: limit - 3] + "..."
    return escape(text)
