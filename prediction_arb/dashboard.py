from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from prediction_arb.capital import parse_balances, parse_inventory, plan_capital
from prediction_arb.reporting import latest_opportunities, read_monitor_history, summarize_monitor_history


DEFAULT_MONITOR_FILE = Path("data/monitor-short-all.jsonl")


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
                if parsed.path == "/api/capital":
                    query = parse_qs(parsed.query)
                    path = _safe_monitor_path(query.get("input", [str(default_input)])[0])
                    cash = parse_balances(query.get("cash", ["limitless=250,polymarket=250"])[0])
                    inventory = parse_inventory(query.get("inventory", [""])[0])
                    assume = _bool(query.get("assume_sell_inventory", ["true"])[0])
                    limit = _int(query.get("limit", ["10"])[0], default=10)
                    self._send_json(plan_capital(latest_opportunities(path), cash, inventory, assume_sell_inventory=assume, max_allocations=limit))
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


def _bool(value: object) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prediction Arb Console</title>
  <style>
    :root {
      --bg: #f4f6f8; --panel: #fff; --ink: #16202a; --muted: #657383;
      --line: #d8e0e8; --blue: #1f6fb2; --green: #087b5b; --red: #b83b35; --amber: #a96500;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { position: sticky; top: 0; z-index: 2; background: var(--panel); border-bottom: 1px solid var(--line); padding: 16px 22px; display: grid; gap: 12px; }
    h1 { margin: 0; font-size: 20px; font-weight: 700; }
    h2 { margin: 0 0 12px; font-size: 15px; }
    main { padding: 18px 22px 34px; display: grid; gap: 16px; }
    .topline { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    select, button, input { height: 34px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--ink); padding: 0 10px; font: inherit; }
    input { min-width: 120px; }
    button { cursor: pointer; }
    button.primary { background: var(--blue); border-color: var(--blue); color: white; }
    .tabs { display: flex; gap: 6px; }
    .tab { border: 1px solid var(--line); background: #f9fbfc; height: 34px; border-radius: 6px; padding: 0 12px; }
    .tab.active { background: var(--ink); border-color: var(--ink); color: white; }
    .muted { color: var(--muted); }
    .ok { color: var(--green); } .bad { color: var(--red); } .warn { color: var(--amber); }
    .metrics { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 12px; }
    .metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
    .metric { padding: 13px; min-height: 82px; }
    .label { color: var(--muted); font-size: 12px; }
    .value { font-size: 24px; font-weight: 750; margin-top: 5px; overflow-wrap: anywhere; }
    .view { display: none; gap: 16px; }
    .view.active { display: grid; }
    .grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(320px, .8fr); gap: 16px; }
    .panel { padding: 15px; min-width: 0; }
    canvas { display: block; width: 100%; height: 260px; border: 1px solid var(--line); border-radius: 6px; background: #fbfcfd; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 650; }
    td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
    .route { font-weight: 700; }
    .events { display: grid; gap: 8px; max-height: 330px; overflow: auto; }
    .event { border-left: 3px solid var(--blue); padding: 8px 10px; background: #fbfcfd; }
    .event.error { border-left-color: var(--red); }
    .planner { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; align-items: end; }
    .switch { display: flex; align-items: center; gap: 7px; height: 34px; color: var(--muted); }
    .switch input { height: auto; min-width: auto; }
    @media (max-width: 980px) {
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .grid, .planner { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topline">
      <div><h1>Prediction Arb Console</h1><div class="muted" id="subtitle">Loading monitor data</div></div>
      <div class="controls"><select id="fileSelect"></select><button class="primary" id="refreshBtn">Refresh</button></div>
    </div>
    <div class="tabs">
      <button class="tab active" data-view="overview">Overview</button>
      <button class="tab" data-view="capital">Capital Planner</button>
      <button class="tab" data-view="events">Events</button>
    </div>
  </header>
  <main>
    <section class="metrics">
      <div class="metric"><div class="label">Snapshots</div><div class="value" id="snapshots">0</div></div>
      <div class="metric"><div class="label">Active</div><div class="value" id="active">0</div></div>
      <div class="metric"><div class="label">Routes Seen</div><div class="value" id="routes">0</div></div>
      <div class="metric"><div class="label">Best Edge</div><div class="value ok" id="bestEdge">0%</div></div>
      <div class="metric"><div class="label">Best Profit</div><div class="value ok" id="bestProfit">$0</div></div>
      <div class="metric"><div class="label">Errors</div><div class="value" id="errors">0</div></div>
    </section>

    <section class="view active" id="overview">
      <div class="grid">
        <div class="panel"><h2>Best Edge Trend</h2><canvas id="trend" width="900" height="260"></canvas></div>
        <div class="panel"><h2>Latest Events</h2><div class="events" id="eventsSmall"></div></div>
      </div>
      <div class="panel">
        <h2>Best Routes</h2>
        <table><thead><tr><th>Route</th><th>Market</th><th>Edge</th><th>Size</th><th>Est. Profit</th><th>Detected</th></tr></thead><tbody id="routesTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="capital">
      <div class="panel">
        <h2>Capital Planner</h2>
        <div class="planner">
          <label><div class="label">Limitless USDC</div><input id="limitlessCash" type="number" value="250" min="0" step="10"></label>
          <label><div class="label">Polymarket USDC</div><input id="polymarketCash" type="number" value="250" min="0" step="10"></label>
          <label><div class="label">Max allocations</div><input id="allocationLimit" type="number" value="10" min="1" step="1"></label>
          <label class="switch"><input id="assumeInventory" type="checkbox" checked> assume sell-side inventory</label>
          <button class="primary" id="planBtn">Calculate</button>
        </div>
        <p class="muted">Sell leg usually requires holding outcome shares on the sell platform. Disable the checkbox to see missing inventory rejections.</p>
      </div>
      <div class="metrics">
        <div class="metric"><div class="label">Allocated</div><div class="value" id="planAllocated">0</div></div>
        <div class="metric"><div class="label">Rejected</div><div class="value" id="planRejected">0</div></div>
        <div class="metric"><div class="label">Buy Cash Used</div><div class="value" id="planCashUsed">$0</div></div>
        <div class="metric"><div class="label">Est. Profit</div><div class="value ok" id="planProfit">$0</div></div>
        <div class="metric"><div class="label">Limitless Left</div><div class="value" id="limitlessLeft">$0</div></div>
        <div class="metric"><div class="label">Polymarket Left</div><div class="value" id="polymarketLeft">$0</div></div>
      </div>
      <div class="panel">
        <h2>Allocation Plan</h2>
        <table><thead><tr><th>Route</th><th>Buy Cash</th><th>Sell Inventory</th><th>Edge</th><th>Profit</th></tr></thead><tbody id="allocationTable"></tbody></table>
      </div>
      <div class="panel">
        <h2>Rejected Signals</h2>
        <table><thead><tr><th>Route</th><th>Reason</th><th>Needed</th></tr></thead><tbody id="rejectedTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="events">
      <div class="panel"><h2>Monitor Events</h2><div class="events" id="eventsFull"></div></div>
    </section>
  </main>

  <script>
    const state = { input: "data/monitor-short-all.jsonl", report: null, snapshots: [], plan: null };
    const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
    const money = v => "$" + fmt.format(v || 0);
    const pct = v => ((v || 0) * 100).toFixed(2) + "%";
    const el = id => document.getElementById(id);

    async function json(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function loadFiles() {
      const files = await json("/api/files");
      el("fileSelect").innerHTML = "";
      for (const file of files) {
        const option = document.createElement("option");
        option.value = file.path;
        option.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
        el("fileSelect").appendChild(option);
      }
      const shortAll = files.find(file => file.path.includes("monitor-short-all.jsonl"));
      const taiwan = files.find(file => file.path.includes("monitor-taiwan.jsonl"));
      state.input = (shortAll || taiwan || files[0] || {}).path || state.input;
      el("fileSelect").value = state.input;
    }

    async function refresh() {
      state.input = el("fileSelect").value || state.input;
      const q = encodeURIComponent(state.input);
      state.report = await json(`/api/report?input=${q}&top=30`);
      state.snapshots = await json(`/api/snapshots?input=${q}&limit=300`);
      await refreshPlanner();
      render();
    }

    async function refreshPlanner() {
      const cash = `limitless=${Number(el("limitlessCash").value || 0)},polymarket=${Number(el("polymarketCash").value || 0)}`;
      const assume = el("assumeInventory").checked ? "true" : "false";
      const limit = Number(el("allocationLimit").value || 10);
      state.plan = await json(`/api/capital?input=${encodeURIComponent(state.input)}&cash=${encodeURIComponent(cash)}&assume_sell_inventory=${assume}&limit=${limit}`);
    }

    function render() {
      const r = state.report || {};
      const best = (r.best_routes || [])[0] || {};
      el("subtitle").textContent = `${r.input || state.input} | last success ${r.last_success_detected_at || "n/a"}`;
      el("snapshots").textContent = r.snapshots || 0;
      el("active").textContent = r.latest_active_count || 0;
      el("routes").textContent = r.unique_routes_seen || 0;
      el("bestEdge").textContent = pct(best.net_edge);
      el("bestProfit").textContent = money(best.estimated_profit);
      el("errors").textContent = r.error_snapshots || 0;
      el("errors").className = (r.error_snapshots || 0) ? "value warn" : "value";
      renderRoutes(r.best_routes || []);
      renderEvents(el("eventsSmall"), state.snapshots.slice(-12).reverse());
      renderEvents(el("eventsFull"), state.snapshots.slice(-80).reverse());
      renderPlan(state.plan || {});
      drawTrend(state.snapshots);
    }

    function renderRoutes(rows) {
      el("routesTable").innerHTML = rows.map(row => `
        <tr>
          <td><div class="route">${row.outcome || ""} ${row.route || ""}</div><div class="muted">${row.key || ""}</div></td>
          <td>${escapeHtml(row.buy_title || "")}<br><span class="muted">${escapeHtml(row.sell_title || "")}</span></td>
          <td class="num ok">${pct(row.net_edge)}</td>
          <td class="num">${fmt.format(row.executable_size || 0)}</td>
          <td class="num ok">${money(row.estimated_profit)}</td>
          <td class="num">${row.detected_at || ""}</td>
        </tr>`).join("");
    }

    function renderPlan(plan) {
      el("planAllocated").textContent = plan.allocated_count || 0;
      el("planRejected").textContent = plan.rejected_count || 0;
      el("planCashUsed").textContent = money(plan.total_buy_cash_required);
      el("planProfit").textContent = money(plan.total_estimated_profit);
      el("limitlessLeft").textContent = money((plan.cash_remaining || {}).limitless);
      el("polymarketLeft").textContent = money((plan.cash_remaining || {}).polymarket);
      el("allocationTable").innerHTML = (plan.allocated || []).map(row => `
        <tr><td><div class="route">${row.outcome} ${row.route}</div><div class="muted">${escapeHtml(row.buy_title || "")}</div></td>
        <td class="num">${money(row.buy_cash_required)}</td><td class="num">${fmt.format(row.sell_inventory_required || 0)}<br><span class="muted">${row.sell_inventory_key}</span></td>
        <td class="num ok">${pct(row.net_edge)}</td><td class="num ok">${money(row.estimated_profit)}</td></tr>`).join("");
      el("rejectedTable").innerHTML = (plan.rejected || []).slice(0, 20).map(row => `
        <tr><td>${row.outcome || ""} ${row.buy_source || ""}->${row.sell_source || ""}</td><td class="bad">${row.planner_rejection_reason || ""}</td>
        <td class="num">${money(row.buy_cash_required)} / ${fmt.format(row.sell_inventory_required || 0)} shares</td></tr>`).join("");
    }

    function renderEvents(target, rows) {
      target.innerHTML = rows.map(row => {
        if (row.type === "error") return `<div class="event error"><strong class="bad">Error</strong><br><span class="muted">${row.detected_at}</span><br>${escapeHtml(row.error || "")}</div>`;
        return `<div class="event"><strong>${row.opportunity_count || 0} active</strong> <span class="ok">+${row.new_count || 0}</span> <span class="bad">-${row.gone_count || 0}</span><br><span class="muted">${row.detected_at}</span><br><span class="muted">${escapeHtml(row.query || "")}</span></div>`;
      }).join("");
    }

    function drawTrend(rows) {
      const canvas = el("trend"), ctx = canvas.getContext("2d"), w = canvas.width, h = canvas.height, pad = 34;
      ctx.clearRect(0, 0, w, h); ctx.fillStyle = "#fbfcfd"; ctx.fillRect(0, 0, w, h);
      const points = rows.filter(row => row.type !== "error").map(row => {
        const best = [...(row.opportunities || [])].sort((a, b) => (b.net_edge || -999) - (a.net_edge || -999))[0];
        return best ? { edge: best.net_edge || 0 } : null;
      }).filter(Boolean);
      ctx.strokeStyle = "#d8e0e8"; ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) { const y = pad + (h - pad * 2) * i / 4; ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke(); }
      if (points.length < 2) return;
      const maxEdge = Math.max(...points.map(p => p.edge), 0.01);
      ctx.strokeStyle = "#1f6fb2"; ctx.lineWidth = 2; ctx.beginPath();
      points.forEach((p, i) => { const x = pad + (w - pad * 2) * i / (points.length - 1); const y = h - pad - (h - pad * 2) * p.edge / maxEdge; if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
      ctx.stroke(); ctx.fillStyle = "#16202a"; ctx.fillText("best net edge", pad, 18); ctx.fillStyle = "#1f6fb2"; ctx.fillText(pct(maxEdge), w - 86, 18);
    }

    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }
    document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach(item => item.classList.remove("active"));
      btn.classList.add("active"); el(btn.dataset.view).classList.add("active");
    }));
    el("refreshBtn").addEventListener("click", refresh);
    el("fileSelect").addEventListener("change", refresh);
    el("planBtn").addEventListener("click", () => refreshPlanner().then(render));
    loadFiles().then(refresh).catch(err => { el("subtitle").textContent = err.message; });
    setInterval(refresh, 30000);
  </script>
</body>
</html>"""
