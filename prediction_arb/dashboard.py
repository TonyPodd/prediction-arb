from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from prediction_arb.reporting import read_monitor_history, summarize_monitor_history


DEFAULT_MONITOR_FILE = Path("data/monitor-taiwan.jsonl")


def serve_dashboard(host: str, port: int, default_input: Path = DEFAULT_MONITOR_FILE) -> None:
    handler_class = _handler(default_input)
    server = ThreadingHTTPServer((host, port), handler_class)
    print(f"Dashboard running at http://{host}:{port}/")
    server.serve_forever()


def _handler(default_input: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._send_html(_DASHBOARD_HTML)
                    return
                if parsed.path == "/api/files":
                    self._send_json(_monitor_files())
                    return
                if parsed.path == "/api/report":
                    query = parse_qs(parsed.query)
                    path = _safe_monitor_path(query.get("input", [str(default_input)])[0])
                    top = _int(query.get("top", ["10"])[0], default=10)
                    self._send_json(summarize_monitor_history(path, top=top))
                    return
                if parsed.path == "/api/snapshots":
                    query = parse_qs(parsed.query)
                    path = _safe_monitor_path(query.get("input", [str(default_input)])[0])
                    limit = _int(query.get("limit", ["200"])[0], default=200)
                    self._send_json(read_monitor_history(path)[-limit:])
                    return
                self.send_error(404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: object, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return DashboardHandler


def _monitor_files() -> list[dict[str, object]]:
    data_dir = Path("data")
    rows = []
    for path in sorted(data_dir.glob("monitor*.jsonl")):
        rows.append({"path": str(path), "name": path.name, "size": path.stat().st_size})
    return rows


def _safe_monitor_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Only relative monitor files are allowed.")
    return path


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prediction Arb Monitor</title>
  <style>
    :root {
      --bg: #f5f7f9;
      --panel: #ffffff;
      --ink: #18202a;
      --muted: #607080;
      --line: #d7dee6;
      --green: #138a63;
      --red: #c2413a;
      --blue: #1d6fb8;
      --amber: #b36a00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { padding: 20px 24px 32px; display: grid; gap: 18px; }
    .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    select, button {
      height: 34px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }
    button { cursor: pointer; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
    }
    .metric, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric { padding: 14px; min-height: 82px; }
    .label { color: var(--muted); font-size: 12px; }
    .value { font-size: 25px; font-weight: 700; margin-top: 6px; overflow-wrap: anywhere; }
    .grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 18px; }
    .panel { padding: 16px; min-width: 0; }
    .panel h2 { margin: 0 0 12px; font-size: 15px; }
    canvas { display: block; width: 100%; height: 260px; border: 1px solid var(--line); border-radius: 6px; background: #fbfcfd; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { font-size: 12px; color: var(--muted); font-weight: 600; }
    td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .route { font-weight: 650; }
    .muted { color: var(--muted); }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .warn { color: var(--amber); }
    .events { display: grid; gap: 8px; max-height: 300px; overflow: auto; }
    .event { border-left: 3px solid var(--blue); padding: 8px 10px; background: #fbfcfd; }
    .event.error { border-left-color: var(--red); }
    @media (max-width: 980px) {
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Prediction Arb Monitor</h1>
      <div class="muted" id="subtitle">Loading monitor history</div>
    </div>
    <div class="controls">
      <select id="fileSelect"></select>
      <button id="refreshBtn">Refresh</button>
    </div>
  </header>
  <main>
    <section class="metrics">
      <div class="metric"><div class="label">Snapshots</div><div class="value" id="snapshots">0</div></div>
      <div class="metric"><div class="label">Active</div><div class="value" id="active">0</div></div>
      <div class="metric"><div class="label">Unique Routes</div><div class="value" id="routes">0</div></div>
      <div class="metric"><div class="label">Best Edge</div><div class="value ok" id="bestEdge">0%</div></div>
      <div class="metric"><div class="label">Best Profit</div><div class="value ok" id="bestProfit">$0</div></div>
      <div class="metric"><div class="label">Errors</div><div class="value" id="errors">0</div></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Best Route Trend</h2>
        <canvas id="trend" width="900" height="260"></canvas>
      </div>
      <div class="panel">
        <h2>Latest Events</h2>
        <div class="events" id="events"></div>
      </div>
    </section>
    <section class="panel">
      <h2>Best Routes</h2>
      <table>
        <thead><tr><th>Route</th><th>Market</th><th>Edge</th><th>Size</th><th>Est. Profit</th><th>Detected</th></tr></thead>
        <tbody id="routesTable"></tbody>
      </table>
    </section>
  </main>
  <script>
    const state = { input: "data/monitor-taiwan.jsonl", report: null, snapshots: [] };
    const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
    const money = v => "$" + fmt.format(v || 0);
    const pct = v => ((v || 0) * 100).toFixed(2) + "%";

    async function json(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function loadFiles() {
      const files = await json("/api/files");
      const select = document.getElementById("fileSelect");
      select.innerHTML = "";
      for (const file of files) {
        const option = document.createElement("option");
        option.value = file.path;
        option.textContent = file.name;
        select.appendChild(option);
      }
      if (files.length) state.input = files[0].path;
      const taiwan = files.find(file => file.path.includes("monitor-taiwan"));
      if (taiwan) state.input = taiwan.path;
      select.value = state.input;
    }

    async function refresh() {
      state.input = document.getElementById("fileSelect").value || state.input;
      const q = encodeURIComponent(state.input);
      state.report = await json(`/api/report?input=${q}&top=20`);
      state.snapshots = await json(`/api/snapshots?input=${q}&limit=240`);
      render();
    }

    function render() {
      const r = state.report;
      const best = (r.best_routes || [])[0] || {};
      document.getElementById("subtitle").textContent = `${r.input} | last success ${r.last_success_detected_at || "n/a"}`;
      document.getElementById("snapshots").textContent = r.snapshots || 0;
      document.getElementById("active").textContent = r.latest_active_count || 0;
      document.getElementById("routes").textContent = r.unique_routes_seen || 0;
      document.getElementById("bestEdge").textContent = pct(best.net_edge);
      document.getElementById("bestProfit").textContent = money(best.estimated_profit);
      document.getElementById("errors").textContent = r.error_snapshots || 0;
      document.getElementById("errors").className = (r.error_snapshots || 0) ? "value warn" : "value";
      renderRoutes(r.best_routes || []);
      renderEvents(state.snapshots.slice(-20).reverse());
      drawTrend(state.snapshots);
    }

    function renderRoutes(rows) {
      const body = document.getElementById("routesTable");
      body.innerHTML = "";
      for (const row of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><div class="route">${row.outcome || ""} ${row.route || ""}</div><div class="muted">${row.key || ""}</div></td>
          <td>${escapeHtml(row.buy_title || "")}<br><span class="muted">${escapeHtml(row.sell_title || "")}</span></td>
          <td class="num ok">${pct(row.net_edge)}</td>
          <td class="num">${fmt.format(row.executable_size || 0)}</td>
          <td class="num ok">${money(row.estimated_profit)}</td>
          <td class="num">${row.detected_at || ""}</td>`;
        body.appendChild(tr);
      }
    }

    function renderEvents(rows) {
      const events = document.getElementById("events");
      events.innerHTML = "";
      for (const row of rows) {
        const div = document.createElement("div");
        div.className = "event" + (row.type === "error" ? " error" : "");
        div.innerHTML = row.type === "error"
          ? `<strong class="bad">Error</strong><br><span class="muted">${row.detected_at}</span><br>${escapeHtml(row.error || "")}`
          : `<strong>${row.opportunity_count || 0} active</strong> <span class="ok">+${row.new_count || 0}</span> <span class="bad">-${row.gone_count || 0}</span><br><span class="muted">${row.detected_at}</span>`;
        events.appendChild(div);
      }
    }

    function drawTrend(rows) {
      const canvas = document.getElementById("trend");
      const ctx = canvas.getContext("2d");
      const w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fbfcfd";
      ctx.fillRect(0, 0, w, h);
      const points = rows
        .filter(row => row.type !== "error")
        .map(row => {
          const best = [...(row.opportunities || [])].sort((a, b) => (b.net_edge || -999) - (a.net_edge || -999))[0];
          return best ? { t: row.detected_at, edge: best.net_edge || 0, profit: (best.net_edge || 0) * (best.executable_size || 0) } : null;
        })
        .filter(Boolean);
      const pad = 34;
      ctx.strokeStyle = "#d7dee6";
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = pad + (h - pad * 2) * i / 4;
        ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
      }
      if (points.length < 2) return;
      const maxEdge = Math.max(...points.map(p => p.edge), 0.01);
      ctx.strokeStyle = "#1d6fb8";
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((p, i) => {
        const x = pad + (w - pad * 2) * i / (points.length - 1);
        const y = h - pad - (h - pad * 2) * p.edge / maxEdge;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = "#18202a";
      ctx.fillText("best net edge", pad, 18);
      ctx.fillStyle = "#1d6fb8";
      ctx.fillText(pct(maxEdge), w - 86, 18);
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }

    document.getElementById("refreshBtn").addEventListener("click", refresh);
    document.getElementById("fileSelect").addEventListener("change", refresh);
    loadFiles().then(refresh).catch(err => {
      document.getElementById("subtitle").textContent = err.message;
    });
    setInterval(refresh, 30000);
  </script>
</body>
</html>"""
