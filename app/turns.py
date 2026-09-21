"""`/turns` — what Nikki's turns actually cost, and where.

Read-only. Nothing here writes, and nothing changes the agent loop. It queries two
tables that were already being written: `token_usage` (one row per completed turn)
and `traces` (per-thread user / assistant / tool_call / tool_result / approval /
error / budget_stop / repeat_blocked rows).

Why this page exists: until 2026-09-21 the per-turn token figures recorded only the
LAST model call of each turn, and the headless path (scheduled runs, /api/run)
never called log_token_usage at all. So any number older than that is wrong, and
any scheduled run older than that is missing entirely. `since_fix` in the summary
marks the boundary — treat everything before it as unreliable.

Design constraints that shaped this:

- The Cloud SQL pool is `pool_size=1, max_overflow=0`. Every query here runs on ONE
  connection, sequentially. Do not add a call that opens a second.
- `traces.payload` is a JSON string in a Text column, and this app runs on Postgres
  in production and SQLite locally. Rather than depend on a JSON operator that
  differs between them, rows are fetched under a hard cap and aggregated in Python.
  Turn counts are small (one row per turn); traces are capped by TRACE_CAP.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import persistence
from app.auth import require_admin

router = APIRouter()

# Before this, per-turn token figures counted one model call per turn and headless
# runs were not logged at all. See commits 93e6643 and 0f90e6b.
ACCOUNTING_FIXED = datetime(2026, 9, 21, tzinfo=timezone.utc)

TRACE_CAP = 20_000
TURN_CAP = 5_000


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


async def collect(days: int = 7) -> dict[str, Any]:
    """One connection, three sequential queries, aggregation in Python."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))

    async with persistence.engine().connect() as conn:
        turns = [dict(r._mapping) for r in await conn.execute(
            select(persistence.token_usage)
            .where(persistence.token_usage.c.ts >= since)
            .order_by(persistence.token_usage.c.ts.desc())
            .limit(TURN_CAP))]
        traces = [dict(r._mapping) for r in await conn.execute(
            select(persistence.traces.c.thread_id, persistence.traces.c.ts,
                   persistence.traces.c.kind, persistence.traces.c.payload)
            .where(persistence.traces.c.ts >= since)
            .order_by(persistence.traces.c.ts.desc())
            .limit(TRACE_CAP))]

    return rollup(turns, traces, days, since)


def rollup(turns: list[dict], traces: list[dict], days: int, since: datetime) -> dict[str, Any]:
    """Pure aggregation, split out so it can be tested without a database."""

    for t in traces:
        try:
            t["data"] = json.loads(t["payload"]) if t["payload"] else {}
        except (ValueError, TypeError):
            t["data"] = {}
        t.pop("payload", None)

    # ---- attribute each trace to the turn it belongs to -------------------
    # A turn's token_usage row is written at the end of the turn, so a trace
    # belongs to the first turn in its thread whose ts is >= the trace's ts.
    by_thread_turns: dict[str, list[dict]] = defaultdict(list)
    for t in turns:
        by_thread_turns[t["thread_id"]].append(t)
    for v in by_thread_turns.values():
        v.sort(key=lambda r: r["ts"])
    for t in turns:
        t["tools"] = []
        t["headless"] = False

    unattributed = 0
    for tr in traces:
        cands = by_thread_turns.get(tr["thread_id"]) or []
        owner = next((c for c in cands if c["ts"] >= tr["ts"]), None)
        if owner is None:
            unattributed += 1
            continue
        if tr["kind"] == "tool_call":
            name = (tr["data"] or {}).get("name")
            if name:
                owner["tools"].append(name)
        if (tr["data"] or {}).get("headless"):
            owner["headless"] = True

    # ---- rollups ----------------------------------------------------------
    totals = [int(t["total_tokens"] or 0) for t in turns]
    kinds = Counter(t["kind"] for t in traces)
    tool_counts = Counter(n for t in turns for n in t["tools"])

    # cost weight: tokens of the turns each tool appears in, not raw call count
    tool_tokens: Counter = Counter()
    for t in turns:
        for n in set(t["tools"]):
            tool_tokens[n] += int(t["total_tokens"] or 0)

    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "tokens": 0})
    for t in turns:
        d = t["ts"].date().isoformat()
        daily[d]["turns"] += 1
        daily[d]["tokens"] += int(t["total_tokens"] or 0)

    by_model: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "tokens": 0})
    for t in turns:
        m = t["model"] or "(unknown)"
        by_model[m]["turns"] += 1
        by_model[m]["tokens"] += int(t["total_tokens"] or 0)

    headless_turns = [t for t in turns if t["headless"]]
    stale = [t for t in turns if t["ts"] < ACCOUNTING_FIXED]

    top = sorted(turns, key=lambda t: int(t["total_tokens"] or 0), reverse=True)[:25]

    return {
        "window_days": days,
        "since": since.isoformat(),
        "summary": {
            "turns": len(turns),
            "tokens": sum(totals),
            "median_turn": _pct(totals, 0.50),
            "p90_turn": _pct(totals, 0.90),
            "max_turn": max(totals) if totals else 0,
            "traces": len(traces),
            "unattributed_traces": unattributed,
            "capped": len(traces) >= TRACE_CAP or len(turns) >= TURN_CAP,
        },
        # The guards added 2026-09-21. If these are firing, look at the threads.
        "incidents": {
            "budget_stop": kinds.get("budget_stop", 0),
            "repeat_blocked": kinds.get("repeat_blocked", 0),
            "error": kinds.get("error", 0),
            "fallback": kinds.get("fallback", 0),
            "model_routed": kinds.get("model_routed", 0),
        },
        # The split nobody could see before: scheduled/API runs were never logged.
        "headless": {
            "turns": len(headless_turns),
            "tokens": sum(int(t["total_tokens"] or 0) for t in headless_turns),
            "share_pct": round(100 * len(headless_turns) / len(turns)) if turns else 0,
        },
        "stale_accounting": {
            "turns_before_fix": len(stale),
            "fixed_at": ACCOUNTING_FIXED.isoformat(),
        },
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "by_model": [{"model": m, **v} for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["tokens"])],
        "tools_by_calls": [{"tool": n, "calls": c} for n, c in tool_counts.most_common(25)],
        "tools_by_turn_tokens": [{"tool": n, "tokens": c} for n, c in tool_tokens.most_common(25)],
        "top_turns": [{
            "thread_id": t["thread_id"],
            "ts": t["ts"].isoformat(),
            "model": t["model"],
            "input_tokens": t["input_tokens"],
            "output_tokens": t["output_tokens"],
            "total_tokens": t["total_tokens"],
            "headless": t["headless"],
            "tool_calls": len(t["tools"]),
            "tools": [f"{n}x{c}" if c > 1 else n for n, c in Counter(t["tools"]).most_common(8)],
        } for t in top],
    }


@router.get("/api/turns")
async def api_turns(days: int = 7, _=Depends(require_admin)) -> dict:
    """Per-turn cost and tool rollup. Admin login cookie required."""
    return await collect(days)


@router.get("/turns", response_class=HTMLResponse)
async def turns_page() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Turns — Nikki</title>
<style>
  :root {
    --bg:#ffffff; --fg:#1a1a1a; --muted:#5d5d5d; --line:#e3e3e0;
    --panel:#faf9f7; --accent:#b8532f; --warn:#8a5a00; --bar:#d8d3cc;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:#1c1c1a; --fg:#eeece7; --muted:#a6a29a; --line:#333330;
      --panel:#232321; --accent:#e08b62; --warn:#d9a441; --bar:#3a3a36;
    }
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 ui-sans-serif,-apple-system,system-ui,sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 16px 80px }
  h1 { font-size:22px; margin:0 0 4px; font-weight:600 }
  .sub { color:var(--muted); font-size:13px; margin-bottom:24px }
  .ctl { display:flex; gap:8px; align-items:center; margin-bottom:24px; flex-wrap:wrap }
  .ctl button { background:var(--panel); color:var(--fg); border:1px solid var(--line);
    border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer }
  .ctl button[aria-pressed="true"] { border-color:var(--accent); color:var(--accent) }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:12px }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px }
  .card .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em }
  .card .v { font-size:24px; font-weight:600; margin-top:4px; font-variant-numeric:tabular-nums }
  .card .n { color:var(--muted); font-size:12px; margin-top:2px }
  .note { background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--warn);
    border-radius:6px; padding:12px 14px; font-size:13px; color:var(--muted); margin:16px 0 }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
    margin:32px 0 10px; font-weight:600 }
  table { width:100%; border-collapse:collapse; font-size:13px }
  th,td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top }
  th { color:var(--muted); font-weight:500; font-size:12px }
  td.n, th.n { text-align:right; font-variant-numeric:tabular-nums }
  .bar { height:4px; background:var(--bar); border-radius:2px; margin-top:4px }
  .bar > i { display:block; height:100%; background:var(--accent); border-radius:2px }
  .tag { display:inline-block; background:var(--panel); border:1px solid var(--line);
    border-radius:4px; padding:1px 6px; margin:1px 3px 1px 0; font-size:11px; color:var(--muted) }
  .hl { color:var(--accent); font-weight:600 }
  code { font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted) }
  .empty { color:var(--muted); font-size:13px; padding:12px 0 }
</style></head><body><div class="wrap">
<h1>Turns</h1>
<div class="sub" id="sub">loading…</div>
<div class="ctl">
  <span style="color:var(--muted);font-size:13px">window</span>
  <button data-d="1">24h</button><button data-d="7">7d</button>
  <button data-d="30">30d</button><button data-d="90">90d</button>
</div>
<div id="body"></div>
</div>
<script>
const $ = s => document.querySelector(s);
const n = v => (v ?? 0).toLocaleString();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let days = 7;

function bar(v, max) {
  const p = max > 0 ? Math.max(1, Math.round(100 * v / max)) : 0;
  return `<div class="bar"><i style="width:${p}%"></i></div>`;
}
function table(cols, rows, empty) {
  if (!rows.length) return `<div class="empty">${empty}</div>`;
  return `<table><thead><tr>${cols.map(c => `<th class="${c.n?'n':''}">${c.h}</th>`).join("")}</tr></thead>
  <tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.n?'n':''}">${c.f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

async function load() {
  $("#sub").textContent = "loading…";
  const res = await fetch(`/api/turns?days=${days}`, {credentials:"same-origin"});
  if (!res.ok) { $("#sub").textContent = res.status === 401 || res.status === 403
      ? "Admin sign-in required." : `Failed (${res.status}).`; $("#body").innerHTML=""; return; }
  const d = await res.json();
  const s = d.summary, inc = d.incidents;
  $("#sub").textContent = `${n(s.turns)} turns since ${d.since.slice(0,10)}`
    + (s.capped ? " — result capped, narrow the window" : "");

  const maxDay = Math.max(1, ...d.daily.map(x => x.tokens));
  const maxTool = Math.max(1, ...d.tools_by_turn_tokens.map(x => x.tokens));
  const guards = inc.budget_stop + inc.repeat_blocked;

  $("#body").innerHTML = `
  <div class="cards">
    <div class="card"><div class="k">Tokens</div><div class="v">${n(s.tokens)}</div>
      <div class="n">${n(s.turns)} turns</div></div>
    <div class="card"><div class="k">Median turn</div><div class="v">${n(s.median_turn)}</div>
      <div class="n">p90 ${n(s.p90_turn)}</div></div>
    <div class="card"><div class="k">Most expensive</div><div class="v">${n(s.max_turn)}</div>
      <div class="n">single turn</div></div>
    <div class="card"><div class="k">Unattended</div><div class="v">${d.headless.share_pct}%</div>
      <div class="n">${n(d.headless.turns)} turns, ${n(d.headless.tokens)} tokens</div></div>
    <div class="card"><div class="k">Guards fired</div>
      <div class="v" ${guards ? 'style="color:var(--accent)"' : ""}>${n(guards)}</div>
      <div class="n">${n(inc.budget_stop)} budget, ${n(inc.repeat_blocked)} repeat</div></div>
    <div class="card"><div class="k">Errors</div><div class="v">${n(inc.error)}</div>
      <div class="n">${n(inc.fallback)} model fallbacks</div></div>
  </div>

  ${d.stale_accounting.turns_before_fix ? `<div class="note">
    <b>${n(d.stale_accounting.turns_before_fix)} of these turns predate the accounting fix</b>
    (${d.stale_accounting.fixed_at.slice(0,10)}). Before it, a turn recorded only its last
    model call, and scheduled runs were not recorded at all — so those turns read far lower
    than they were, and unattended work is missing entirely. Compare like with like.
  </div>` : ""}

  <h2>Daily</h2>
  ${table([
    {h:"Date", f:r=>esc(r.date)},
    {h:"Turns", n:1, f:r=>n(r.turns)},
    {h:"Tokens", n:1, f:r=>n(r.tokens)},
    {h:"", f:r=>bar(r.tokens, maxDay)},
  ], d.daily, "No turns in this window.")}

  <h2>By model</h2>
  ${table([
    {h:"Model", f:r=>`<code>${esc(r.model)}</code>`},
    {h:"Turns", n:1, f:r=>n(r.turns)},
    {h:"Tokens", n:1, f:r=>n(r.tokens)},
  ], d.by_model, "—")}

  <h2>Tools, by tokens of the turns they appear in</h2>
  ${table([
    {h:"Tool", f:r=>`<code>${esc(r.tool)}</code>`},
    {h:"Turn tokens", n:1, f:r=>n(r.tokens)},
    {h:"", f:r=>bar(r.tokens, maxTool)},
  ], d.tools_by_turn_tokens, "No tool calls recorded in this window.")}

  <h2>Most expensive turns</h2>
  ${table([
    {h:"When", f:r=>esc(r.ts.slice(5,16).replace("T"," "))},
    {h:"Thread", f:r=>`<code>${esc(r.thread_id.slice(0,8))}</code>${r.headless?' <span class="tag">unattended</span>':""}`},
    {h:"Tokens", n:1, f:r=>`<span class="hl">${n(r.total_tokens)}</span>`},
    {h:"In/Out", n:1, f:r=>`${n(r.input_tokens)} / ${n(r.output_tokens)}`},
    {h:"Calls", n:1, f:r=>n(r.tool_calls)},
    {h:"Tools", f:r=>r.tools.map(t=>`<span class="tag">${esc(t)}</span>`).join("") || "—"},
  ], d.top_turns, "No turns in this window.")}

  ${s.unattributed_traces ? `<div class="note">${n(s.unattributed_traces)} trace rows could not be
    matched to a logged turn — usually a turn still in progress, or one that ended without
    writing a token_usage row.</div>` : ""}`;
}

document.querySelectorAll(".ctl button").forEach(b => {
  b.onclick = () => { days = +b.dataset.d;
    document.querySelectorAll(".ctl button").forEach(x => x.setAttribute("aria-pressed", x === b));
    load(); };
  b.setAttribute("aria-pressed", +b.dataset.d === days);
});
load();
</script></body></html>
"""
