#!/usr/bin/env python3
"""Build a static OpenClaw cost dashboard from session JSONL logs."""

from __future__ import annotations

import glob
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SESSIONS_GLOB = os.path.expanduser("~/.openclaw/agents/main/sessions/*.jsonl")
LOGS_PATH = ROOT / "logs" / "costs.jsonl"
DIST_PATH = ROOT / "dist" / "index.html"


def ensure_dirs() -> None:
    LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIST_PATH.parent.mkdir(parents=True, exist_ok=True)


def parse_timestamp(value: Any) -> tuple[str, float]:
    """Return an ISO timestamp string plus epoch seconds."""
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z"), epoch

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "", 0.0
        try:
            if text.endswith("Z"):
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), dt.timestamp()
        except ValueError:
            return text, 0.0

    return "", 0.0


def flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            text = block
        elif isinstance(block, dict):
            block_type = block.get("type")
            if block_type in {"text", "input_text", "output_text"}:
                text = block.get("text", "")
            else:
                continue
        else:
            continue
        text = str(text).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def normalize_cost(cost_obj: Any) -> dict[str, float]:
    if not isinstance(cost_obj, dict):
        return {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0}
    return {
        "input": float(cost_obj.get("input", 0.0) or 0.0),
        "output": float(cost_obj.get("output", 0.0) or 0.0),
        "cacheRead": float(cost_obj.get("cacheRead", 0.0) or 0.0),
        "cacheWrite": float(cost_obj.get("cacheWrite", 0.0) or 0.0),
        "total": float(cost_obj.get("total", 0.0) or 0.0),
    }


def extract_entry(raw: dict[str, Any], session_path: str, latest_user_prompt: str) -> dict[str, Any] | None:
    if raw.get("type") != "message":
        return None

    msg = raw.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return None

    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None

    cost = normalize_cost(usage.get("cost"))
    if cost["total"] <= 0:
        return None

    timestamp_iso, timestamp_epoch = parse_timestamp(raw.get("timestamp") or msg.get("timestamp"))
    session_id = Path(session_path).stem
    model = str(msg.get("model", "unknown") or "unknown")
    input_tokens = int(usage.get("input", 0) or 0)
    output_tokens = int(usage.get("output", 0) or 0)
    cache_read_tokens = int(usage.get("cacheRead", 0) or 0)
    cache_write_tokens = int(usage.get("cacheWrite", 0) or 0)
    total_tokens = int(
        usage.get("totalTokens", input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) or 0
    )

    return {
        "timestamp": timestamp_iso,
        "timestampEpoch": timestamp_epoch,
        "sessionId": session_id,
        "responseId": msg.get("responseId", ""),
        "model": model,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total_tokens,
        "cost": cost["total"],
        "costBreakdown": cost,
        "prompt": latest_user_prompt,
    }


def parse_session_file(path: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    latest_user_prompt = ""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = raw.get("message")
                if isinstance(msg, dict) and msg.get("role") == "user":
                    text = flatten_text(msg.get("content"))
                    if text:
                        latest_user_prompt = text

                entry = extract_entry(raw, path, latest_user_prompt)
                if entry:
                    entries.append(entry)
    except OSError as exc:
        print(f"warning: failed to read {path}: {exc}", file=sys.stderr)

    return entries


def load_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    session_files = sorted(glob.glob(SESSIONS_GLOB))
    for path in session_files:
        entries.extend(parse_session_file(path))
    entries.sort(key=lambda item: item["timestampEpoch"], reverse=True)
    return entries


def write_costs_jsonl(entries: list[dict[str, Any]]) -> None:
    with open(LOGS_PATH, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=True))
            handle.write("\n")


def build_html(entries: list[dict[str, Any]]) -> str:
    embedded = json.dumps(
        {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "entries": entries,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).replace("</script>", "<\\/script>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenClaw Cost Dashboard</title>
  <style>
    :root {{
      --bg: #eef3f8;
      --surface: #ffffff;
      --surface-alt: #f7fafe;
      --header: #132238;
      --header-accent: #2f7df6;
      --text: #182230;
      --muted: #5c6b7e;
      --border: #d7e2f0;
      --blue: #2f7df6;
      --blue-soft: #e8f1ff;
      --green: #0f9f6e;
      --shadow: 0 18px 48px rgba(19, 34, 56, 0.1);
      --radius: 18px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(47, 125, 246, 0.08), transparent 26%),
        linear-gradient(180deg, #f6f9fd 0%, var(--bg) 100%);
    }}
    .shell {{
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    .hero {{
      background: linear-gradient(135deg, #102033 0%, #17314f 58%, #21456f 100%);
      color: #fff;
      padding: 28px 20px 88px;
    }}
    .hero-inner, .content {{
      max-width: 1240px;
      margin: 0 auto;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.1);
      color: rgba(255, 255, 255, 0.88);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 16px 0 10px;
      font-size: clamp(32px, 5vw, 48px);
      line-height: 1.04;
      letter-spacing: -0.03em;
    }}
    .hero p {{
      margin: 0;
      max-width: 720px;
      font-size: 16px;
      line-height: 1.6;
      color: rgba(255, 255, 255, 0.78);
    }}
    .content {{
      margin-top: -52px;
      padding: 0 20px 32px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid rgba(215, 226, 240, 0.9);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .controls {{
      padding: 18px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }}
    .pill-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .pill {{
      border: 1px solid var(--border);
      background: #fff;
      color: var(--muted);
      border-radius: 999px;
      padding: 10px 16px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: 160ms ease;
    }}
    .pill:hover {{
      border-color: var(--blue);
      color: var(--blue);
      background: var(--blue-soft);
    }}
    .pill.active {{
      background: var(--blue);
      border-color: var(--blue);
      color: #fff;
      box-shadow: 0 8px 18px rgba(47, 125, 246, 0.24);
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
      margin: 18px 0;
    }}
    .card {{
      padding: 18px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: 0 10px 28px rgba(19, 34, 56, 0.06);
    }}
    .card-label {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }}
    .card-value {{
      margin-top: 10px;
      font-size: clamp(26px, 3vw, 36px);
      line-height: 1.08;
      letter-spacing: -0.03em;
    }}
    .card-sub {{
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
    }}
    .section {{
      margin-top: 18px;
      padding: 18px;
    }}
    .section-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .section h2 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: -0.02em;
    }}
    .section-note {{
      color: var(--muted);
      font-size: 13px;
    }}
    .table-wrap {{
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: #fff;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}
    th, td {{
      padding: 13px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      background: #f7fafe;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    th.sortable {{
      cursor: pointer;
      user-select: none;
    }}
    th.sortable::after {{
      content: " ⇅";
      color: #9cb0c8;
    }}
    th.sortable.asc::after {{
      content: " ↑";
      color: var(--blue);
    }}
    th.sortable.desc::after {{
      content: " ↓";
      color: var(--blue);
    }}
    tbody tr:hover td {{
      background: rgba(47, 125, 246, 0.025);
    }}
    tbody tr:last-child td {{
      border-bottom: 0;
    }}
    .model {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      background: var(--blue-soft);
      color: var(--blue);
      font-family: var(--mono);
      font-size: 12px;
    }}
    .cost {{
      font-weight: 700;
      color: #c43e32;
    }}
    .muted {{
      color: var(--muted);
    }}
    .prompt-button {{
      display: inline-flex;
      max-width: 340px;
      border: 0;
      padding: 0;
      background: transparent;
      color: var(--blue);
      font: inherit;
      cursor: pointer;
      text-align: left;
    }}
    .prompt-button:hover {{
      text-decoration: underline;
    }}
    .detail-row td {{
      background: var(--surface-alt);
      padding: 0;
    }}
    .detail-card {{
      padding: 16px 18px 18px;
    }}
    .detail-card h3 {{
      margin: 0 0 8px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
    }}
    .detail-card pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--sans);
      line-height: 1.55;
      font-size: 14px;
      color: var(--text);
    }}
    .empty {{
      padding: 34px 18px;
      text-align: center;
      color: var(--muted);
    }}
    @media (max-width: 980px) {{
      .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 720px) {{
      .hero {{ padding-bottom: 76px; }}
      .content {{ padding: 0 14px 24px; margin-top: -46px; }}
      .controls, .section {{ padding: 14px; }}
      .cards {{ grid-template-columns: 1fr; gap: 12px; }}
      .prompt-button {{ max-width: 220px; }}
      table {{ min-width: 640px; }}
      h1 {{ max-width: 10ch; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="hero">
      <div class="hero-inner">
        <div class="eyebrow">OpenClaw Usage</div>
        <h1>Cost Dashboard</h1>
        <p>Single-file static dashboard built from local OpenClaw session logs. Filter by time range, inspect model spend, and expand recent calls to view captured prompts.</p>
      </div>
    </header>

    <main class="content">
      <section class="panel controls">
        <div class="pill-row" id="rangePills"></div>
        <div class="meta" id="buildMeta"></div>
      </section>

      <section class="cards" id="summaryCards"></section>

      <section class="panel section">
        <div class="section-head">
          <h2>Cost by Model</h2>
          <div class="section-note" id="modelCountNote"></div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Calls</th>
                <th>Input</th>
                <th>Output</th>
                <th>Cache Read</th>
                <th>Cache Write</th>
                <th>Total Cost</th>
                <th>Avg Cost</th>
              </tr>
            </thead>
            <tbody id="modelRows"></tbody>
          </table>
        </div>
      </section>

      <section class="panel section">
        <div class="section-head">
          <h2>Recent API Calls</h2>
          <div class="section-note">Click any prompt to expand the captured user message.</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="sortable" data-sort="timestampEpoch">Time</th>
                <th class="sortable" data-sort="model">Model</th>
                <th class="sortable" data-sort="inputTokens">Input</th>
                <th class="sortable" data-sort="outputTokens">Output</th>
                <th class="sortable" data-sort="cacheReadTokens">Cache Read</th>
                <th class="sortable" data-sort="cacheWriteTokens">Cache Write</th>
                <th class="sortable" data-sort="totalTokens">Total Tokens</th>
                <th class="sortable" data-sort="cost">Cost</th>
                <th>Prompt</th>
              </tr>
            </thead>
            <tbody id="callRows"></tbody>
          </table>
        </div>
      </section>
    </main>
  </div>

  <script id="dashboard-data" type="application/json">{embedded}</script>
  <script>
    const DASHBOARD_DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
    const ALL_ENTRIES = Array.isArray(DASHBOARD_DATA.entries) ? DASHBOARD_DATA.entries : [];
    const RANGE_OPTIONS = [
      {{ key: "1h", label: "1h", ms: 60 * 60 * 1000 }},
      {{ key: "6h", label: "6h", ms: 6 * 60 * 60 * 1000 }},
      {{ key: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 }},
      {{ key: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 }},
      {{ key: "30d", label: "30d", ms: 30 * 24 * 60 * 60 * 1000 }},
      {{ key: "all", label: "All", ms: null }},
    ];

    let currentRange = "24h";
    let sortField = "timestampEpoch";
    let sortDirection = "desc";
    let expandedKey = null;

    function fmtCurrency(value) {{
      return "$" + Number(value || 0).toFixed(4);
    }}

    function fmtInt(value) {{
      return Number(value || 0).toLocaleString("en-US");
    }}

    function fmtPct(value) {{
      return Number(value || 0).toFixed(1) + "%";
    }}

    function fmtDate(iso) {{
      if (!iso) return "Unknown";
      const date = new Date(iso);
      return new Intl.DateTimeFormat("en-US", {{
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }}).format(date);
    }}

    function shortModel(model) {{
      return (model || "unknown").replace(/^anthropic\\//, "");
    }}

    function promptPreview(text) {{
      if (!text) return "(no prompt captured)";
      const normalized = text.replace(/\\s+/g, " ").trim();
      return normalized.length > 72 ? normalized.slice(0, 72) + "..." : normalized;
    }}

    function activeRange() {{
      return RANGE_OPTIONS.find((range) => range.key === currentRange) || RANGE_OPTIONS[2];
    }}

    function filteredEntries() {{
      const range = activeRange();
      if (!range.ms) return ALL_ENTRIES.slice();
      const cutoff = Date.now() - range.ms;
      return ALL_ENTRIES.filter((entry) => Number(entry.timestampEpoch || 0) * 1000 >= cutoff);
    }}

    function renderPills() {{
      const container = document.getElementById("rangePills");
      container.innerHTML = RANGE_OPTIONS.map((range) => `
        <button class="pill ${{range.key === currentRange ? "active" : ""}}" data-range="${{range.key}}">
          ${{range.label}}
        </button>
      `).join("");
      container.querySelectorAll(".pill").forEach((button) => {{
        button.addEventListener("click", () => {{
          currentRange = button.dataset.range;
          expandedKey = null;
          render();
        }});
      }});
    }}

    function renderMeta() {{
      const generatedAt = DASHBOARD_DATA.generatedAt ? fmtDate(DASHBOARD_DATA.generatedAt) : "Unknown";
      document.getElementById("buildMeta").textContent = `${{fmtInt(ALL_ENTRIES.length)}} billable calls loaded • Built ${{generatedAt}}`;
    }}

    function renderCards(entries) {{
      const totalCost = entries.reduce((sum, entry) => sum + Number(entry.cost || 0), 0);
      const totalCalls = entries.length;
      const totalTokens = entries.reduce((sum, entry) => sum + Number(entry.totalTokens || 0), 0);
      const cacheRead = entries.reduce((sum, entry) => sum + Number(entry.cacheReadTokens || 0), 0);
      const totalContext = entries.reduce(
        (sum, entry) => sum + Number(entry.inputTokens || 0) + Number(entry.cacheReadTokens || 0) + Number(entry.cacheWriteTokens || 0),
        0
      );
      const cacheHitRate = totalContext ? (cacheRead / totalContext) * 100 : 0;

      const cards = [
        {{ label: "Total Cost", value: fmtCurrency(totalCost), sub: "All billable assistant turns in range" }},
        {{ label: "Total Calls", value: fmtInt(totalCalls), sub: "Assistant messages with non-zero cost" }},
        {{ label: "Total Tokens", value: fmtInt(totalTokens), sub: "Input + output + cache activity" }},
        {{ label: "Cache Hit Rate", value: fmtPct(cacheHitRate), sub: fmtInt(cacheRead) + " cache-read tokens" }},
      ];

      document.getElementById("summaryCards").innerHTML = cards.map((card) => `
        <article class="card">
          <div class="card-label">${{card.label}}</div>
          <div class="card-value">${{card.value}}</div>
          <div class="card-sub">${{card.sub}}</div>
        </article>
      `).join("");
    }}

    function renderModels(entries) {{
      const grouped = new Map();
      entries.forEach((entry) => {{
        const key = entry.model || "unknown";
        if (!grouped.has(key)) {{
          grouped.set(key, {{
            model: key,
            count: 0,
            inputTokens: 0,
            outputTokens: 0,
            cacheReadTokens: 0,
            cacheWriteTokens: 0,
            cost: 0,
          }});
        }}
        const current = grouped.get(key);
        current.count += 1;
        current.inputTokens += Number(entry.inputTokens || 0);
        current.outputTokens += Number(entry.outputTokens || 0);
        current.cacheReadTokens += Number(entry.cacheReadTokens || 0);
        current.cacheWriteTokens += Number(entry.cacheWriteTokens || 0);
        current.cost += Number(entry.cost || 0);
      }});

      const rows = Array.from(grouped.values()).sort((a, b) => b.cost - a.cost);
      document.getElementById("modelCountNote").textContent = rows.length ? `${{rows.length}} model${{rows.length === 1 ? "" : "s"}} in range` : "No billable usage in range";

      document.getElementById("modelRows").innerHTML = rows.length ? rows.map((row) => `
        <tr>
          <td><span class="model">${{shortModel(row.model)}}</span></td>
          <td>${{fmtInt(row.count)}}</td>
          <td>${{fmtInt(row.inputTokens)}}</td>
          <td>${{fmtInt(row.outputTokens)}}</td>
          <td>${{fmtInt(row.cacheReadTokens)}}</td>
          <td>${{fmtInt(row.cacheWriteTokens)}}</td>
          <td><span class="cost">${{fmtCurrency(row.cost)}}</span></td>
          <td>${{fmtCurrency(row.count ? row.cost / row.count : 0)}}</td>
        </tr>
      `).join("") : `<tr><td colspan="8" class="empty">No data for the selected range.</td></tr>`;
    }}

    function sortedEntries(entries) {{
      const sorted = entries.slice().sort((a, b) => {{
        const left = a[sortField];
        const right = b[sortField];
        if (typeof left === "string" || typeof right === "string") {{
          const result = String(left || "").localeCompare(String(right || ""));
          return sortDirection === "asc" ? result : -result;
        }}
        const result = Number(left || 0) - Number(right || 0);
        return sortDirection === "asc" ? result : -result;
      }});
      return sorted;
    }}

    function renderCalls(entries) {{
      const rows = [];
      sortedEntries(entries).slice(0, 150).forEach((entry, index) => {{
        const rowKey = `${{entry.sessionId || "session"}}:${{entry.responseId || index}}`;
        rows.push(`
          <tr>
            <td>${{fmtDate(entry.timestamp)}}</td>
            <td><span class="model">${{shortModel(entry.model)}}</span></td>
            <td>${{fmtInt(entry.inputTokens)}}</td>
            <td>${{fmtInt(entry.outputTokens)}}</td>
            <td>${{fmtInt(entry.cacheReadTokens)}}</td>
            <td>${{fmtInt(entry.cacheWriteTokens)}}</td>
            <td>${{fmtInt(entry.totalTokens)}}</td>
            <td><span class="cost">${{fmtCurrency(entry.cost)}}</span></td>
            <td>
              <button class="prompt-button" type="button" data-row-key="${{rowKey}}">
                ${{promptPreview(entry.prompt)}}
              </button>
            </td>
          </tr>
        `);
        if (expandedKey === rowKey) {{
          rows.push(`
            <tr class="detail-row">
              <td colspan="9">
                <div class="detail-card">
                  <h3>Prompt</h3>
                  <pre>${{escapeHtml(entry.prompt || "(no prompt captured)")}}</pre>
                </div>
              </td>
            </tr>
          `);
        }}
      }});

      document.getElementById("callRows").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="9" class="empty">No data for the selected range.</td></tr>`;

      document.querySelectorAll(".prompt-button").forEach((button) => {{
        button.addEventListener("click", () => {{
          expandedKey = expandedKey === button.dataset.rowKey ? null : button.dataset.rowKey;
          renderCalls(entries);
        }});
      }});
    }}

    function escapeHtml(value) {{
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }}

    function syncSortHeaders() {{
      document.querySelectorAll("th.sortable").forEach((header) => {{
        header.classList.remove("asc", "desc");
        if (header.dataset.sort === sortField) {{
          header.classList.add(sortDirection);
        }}
      }});
    }}

    function bindSortHeaders() {{
      document.querySelectorAll("th.sortable").forEach((header) => {{
        header.addEventListener("click", () => {{
          const nextField = header.dataset.sort;
          if (sortField === nextField) {{
            sortDirection = sortDirection === "desc" ? "asc" : "desc";
          }} else {{
            sortField = nextField;
            sortDirection = nextField === "model" ? "asc" : "desc";
          }}
          expandedKey = null;
          render();
        }});
      }});
    }}

    function render() {{
      renderPills();
      renderMeta();
      const entries = filteredEntries();
      renderCards(entries);
      renderModels(entries);
      renderCalls(entries);
      syncSortHeaders();
    }}

    bindSortHeaders();
    render();
  </script>
</body>
</html>
"""


def write_html(entries: list[dict[str, Any]]) -> None:
    with open(DIST_PATH, "w", encoding="utf-8") as handle:
        handle.write(build_html(entries))


def main() -> int:
    ensure_dirs()
    entries = load_entries()
    write_costs_jsonl(entries)
    write_html(entries)

    total_cost = sum(entry["cost"] for entry in entries)
    print(f"Parsed {len(entries)} billable calls from {len(glob.glob(SESSIONS_GLOB))} session files")
    print(f"Wrote {LOGS_PATH}")
    print(f"Wrote {DIST_PATH}")
    print(f"Total cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
