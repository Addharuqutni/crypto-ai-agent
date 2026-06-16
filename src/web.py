from __future__ import annotations

import threading
from collections import Counter
from html import escape
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

from src.config import load_settings
from src.dataset import DEFAULT_JSONL_PATH
from src.data import MarketDataClient, get_market_data_client
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
        client = get_market_data_client(settings.exchange)
        prices = client.fetch_ticker_prices(symbols)
    except Exception:
        return rows

    enriched_rows = []
    for row in rows:
        item = dict(row)
        symbol = str(item.get("symbol") or "")
        if symbol in prices:
            item["realtime_price"] = prices[symbol]
            if item.get("label") not in {"WIN", "LOSS"}:
                item["pnl_percent"] = _calculate_realtime_pnl_percent(item, prices[symbol])
        enriched_rows.append(item)
    return enriched_rows


def _calculate_realtime_pnl_percent(row: dict[str, Any], realtime_price: float) -> float | None:
    try:
        entry_price = float(row.get("entry_price"))
        if entry_price <= 0:
            return None
        action = str(row.get("action") or "").upper()
        if action == "LONG":
            return round((realtime_price - entry_price) / entry_price * 100, 4)
        if action == "SHORT":
            return round((entry_price - realtime_price) / entry_price * 100, 4)
        return None
    except (TypeError, ValueError):
        return None


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
    call_cards = "".join(_render_call_card(row) for row in rows) or '<div class="empty">Belum ada action call.</div>'
    return f"""
<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto AI Agent Dashboard</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 24px; background: #0b1220; color: #e2e8f0; }}
    h1 {{ margin: 0 0 4px 0; font-size: 22px; }}
    .muted {{ color: #94a3b8; font-size: 13px; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 20px 0; }}
    .stat {{ background: #111c34; padding: 14px 16px; border-radius: 10px; border: 1px solid #1f2a44; }}
    .stat .label {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }}
    .stat .value {{ font-size: 24px; font-weight: bold; margin-top: 4px; }}
    .actions-bar {{ display: flex; gap: 8px; margin: 16px 0 24px; flex-wrap: wrap; }}
    button {{ background: #2563eb; color: white; border: 0; padding: 10px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }}
    button:hover {{ background: #1d4ed8; }}
    button.secondary {{ background: #334155; }}
    button.secondary:hover {{ background: #475569; }}
    .calls-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
    .call-card {{ background: #111c34; border: 1px solid #1f2a44; border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 10px; transition: transform .15s, border-color .15s; }}
    .call-card:hover {{ transform: translateY(-2px); border-color: #3b82f6; }}
    .call-header {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .symbol {{ font-size: 18px; font-weight: 700; letter-spacing: .3px; }}
    .badge {{ padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .5px; }}
    .badge.LONG {{ background: rgba(34, 197, 94, .15); color: #22c55e; border: 1px solid rgba(34, 197, 94, .35); }}
    .badge.SHORT {{ background: rgba(239, 68, 68, .15); color: #ef4444; border: 1px solid rgba(239, 68, 68, .35); }}
    .signal {{ color: #93c5fd; font-size: 12px; font-weight: 600; }}
    .pnl {{ font-size: 26px; font-weight: 800; }}
    .pnl.positive {{ color: #22c55e; }}
    .pnl.negative {{ color: #ef4444; }}
    .pnl.neutral {{ color: #94a3b8; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .field {{ background: #0b1220; padding: 8px 10px; border-radius: 8px; border: 1px solid #1f2a44; }}
    .field .k {{ color: #94a3b8; font-size: 10px; text-transform: uppercase; letter-spacing: .5px; }}
    .field .v {{ font-size: 13px; font-weight: 600; margin-top: 2px; word-break: break-all; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 6px; font-size: 11px; color: #94a3b8; }}
    .chip {{ background: #0b1220; border: 1px solid #1f2a44; padding: 3px 8px; border-radius: 6px; }}
    .label-WIN {{ color: #22c55e; }}
    .label-LOSS {{ color: #ef4444; }}
    .label-OPEN {{ color: #f59e0b; }}
    .label-PENDING {{ color: #94a3b8; }}
    .ai {{ font-size: 11px; color: #cbd5e1; line-height: 1.4; border-top: 1px dashed #1f2a44; padding-top: 8px; }}
    .empty {{ text-align: center; color: #94a3b8; padding: 40px; background: #111c34; border-radius: 12px; border: 1px dashed #334155; }}
  </style>
</head>
<body>
  <h1>Crypto AI Agent Dashboard</h1>
  <div class="muted">Screening top 100 Binance pairs, MTF action call, monitoring TP/SL, AI review.</div>

  <div class="stat-grid">
    {_stat_card('Total Calls', stats['total'])}
    {_stat_card('Winrate', str(stats['winrate']) + '%')}
    {_stat_card('WIN', stats['win'])}
    {_stat_card('LOSS', stats['loss'])}
    {_stat_card('OPEN', stats['open'])}
    {_stat_card('PENDING', stats['pending'])}
  </div>

  <div class="actions-bar">
    <button onclick="runJob('/api/scan')">Run Scan</button>
    <button onclick="runJob('/api/evaluate')">Evaluate TP/SL</button>
    <button class="secondary" onclick="location.reload()">Refresh</button>
  </div>

  <div class="calls-grid">{call_cards}</div>

<script>
async function runJob(url) {{
  const res = await fetch(url, {{ method: 'POST' }});
  const data = await res.json();
  alert(JSON.stringify(data, null, 2));
}}
setTimeout(() => location.reload(), 60000);
</script>
</body>
</html>
"""


def _stat_card(label: str, value: Any) -> str:
    return f'<div class="stat"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def _render_call_card(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").upper()
    label = str(row.get("label") or "PENDING").upper()
    pnl = row.get("pnl_percent")
    pnl_class = "neutral"
    pnl_text = "—"
    if isinstance(pnl, (int, float)):
        pnl_class = "positive" if pnl > 0 else "negative" if pnl < 0 else "neutral"
        pnl_text = f"{pnl:+.2f}%"

    ai_decision = row.get("ai_decision") or "NONE"
    ai_score = row.get("ai_score")
    ai_reason = row.get("ai_reason")
    ai_block = ""
    if ai_decision and ai_decision != "NONE":
        score_text = f" · score {ai_score}" if ai_score is not None else ""
        reason_text = f" · {_short_text(ai_reason, 120)}" if ai_reason else ""
        ai_block = f'<div class="ai">AI {escape(str(ai_decision))}{escape(score_text)}{escape(reason_text)}</div>'

    return f"""
<div class="call-card">
  <div class="call-header">
    <div>
      <div class="symbol">{escape(str(row.get('symbol', '-')))}</div>
      <div class="signal">{escape(str(row.get('signal', '')))}</div>
    </div>
    <span class="badge {action}">{escape(action or '—')}</span>
  </div>

  <div class="pnl {pnl_class}">{escape(pnl_text)}</div>

  <div class="row">
    <div class="field"><div class="k">Entry</div><div class="v">{escape(_fmt(row.get('entry_price')))}</div></div>
    <div class="field"><div class="k">Realtime</div><div class="v">{escape(_fmt(row.get('realtime_price')))}</div></div>
    <div class="field"><div class="k">Take Profit</div><div class="v">{escape(_fmt(row.get('take_profit')))}</div></div>
    <div class="field"><div class="k">Stop Loss</div><div class="v">{escape(_fmt(row.get('stop_loss')))}</div></div>
  </div>

  <div class="meta">
    <span class="chip label-{label}">{escape(label)}</span>
    <span class="chip">TF {escape(str(row.get('timeframe') or '-'))}</span>
    <span class="chip">RR {escape(_fmt(row.get('risk_reward')))}</span>
    <span class="chip">{escape(_short_text(row.get('created_at'), 19))}</span>
  </div>

  {ai_block}
</div>
"""


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _short_text(value: Any, limit: int = 80) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."

