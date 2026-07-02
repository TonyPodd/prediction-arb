from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from prediction_arb.capital import parse_balances, parse_inventory, plan_capital
from prediction_arb.coverage import summarize_source_coverage
from prediction_arb.depth import scan_depth_candidates
from prediction_arb.paper import paper_enter_from_monitor, paper_sync_from_monitor
from prediction_arb.portfolio import load_portfolio, portfolio_summary
from prediction_arb.reporting import latest_opportunities, read_monitor_history, summarize_monitor_history
from prediction_arb.review_store import append_review_label, load_review_queue
from prediction_arb.risk import assess_candidate_risk, candidate_to_dict
from prediction_arb.sources import limitless, polymarket


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
                if parsed.path == "/api/portfolio":
                    query = parse_qs(parsed.query)
                    path = _safe_monitor_path(query.get("portfolio", ["data/portfolio.json"])[0])
                    self._send_json(portfolio_summary(load_portfolio(path)))
                    return
                if parsed.path == "/api/coverage":
                    query = parse_qs(parsed.query)
                    limit = _int(query.get("limit", ["100"])[0], default=100)
                    category = query.get("category", [""])[0]
                    max_close_hours = _float(query.get("max_close_hours", [""])[0])
                    limitless_markets = limitless.fetch_markets(limit=limit)
                    polymarket_markets = polymarket.fetch_markets(limit=limit)
                    if category:
                        limitless_markets = _filter_by_category(limitless_markets, category)
                        polymarket_markets = _filter_by_category(polymarket_markets, category)
                    if max_close_hours is not None:
                        limitless_markets = _filter_by_max_close_hours(limitless_markets, max_close_hours)
                        polymarket_markets = _filter_by_max_close_hours(polymarket_markets, max_close_hours)
                    self._send_json(summarize_source_coverage(limitless_markets, polymarket_markets))
                    return
                if parsed.path == "/api/review":
                    query = parse_qs(parsed.query)
                    path = _safe_monitor_path(query.get("input", ["data/review-candidates.jsonl"])[0])
                    limit = _int(query.get("limit", ["100"])[0], default=100)
                    self._send_json(load_review_queue(path, limit=limit))
                    return
                if parsed.path == "/api/review-label":
                    query = parse_qs(parsed.query)
                    review_id = query.get("review_id", [""])[0]
                    label = query.get("label", [""])[0]
                    self._send_json(append_review_label(review_id, label))
                    return
                if parsed.path == "/api/near-misses":
                    query = parse_qs(parsed.query)
                    limit = _int(query.get("limit", ["200"])[0], default=200)
                    top = _int(query.get("top", ["20"])[0], default=20)
                    size = _float(query.get("size", ["100"])[0]) or 100.0
                    fee_bps = _float(query.get("fee_bps", ["10"])[0]) or 0.0
                    max_close_hours = _float(query.get("max_close_hours", ["24"])[0])
                    category = query.get("category", ["crypto"])[0]
                    limitless_markets = limitless.fetch_markets(limit=limit)
                    polymarket_markets = polymarket.fetch_markets(limit=limit)
                    if category:
                        limitless_markets = _filter_by_category(limitless_markets, category)
                        polymarket_markets = _filter_by_category(polymarket_markets, category)
                    if max_close_hours is not None:
                        limitless_markets = _filter_by_max_close_hours(limitless_markets, max_close_hours)
                        polymarket_markets = _filter_by_max_close_hours(polymarket_markets, max_close_hours)
                    rows = scan_depth_candidates(
                        limitless_markets,
                        polymarket_markets,
                        size=size,
                        min_net_edge=0.005,
                        safety_buffer=0.002,
                        min_match_score=0.25,
                        allow_partial=False,
                        fee_bps=fee_bps,
                        min_profit=0.0,
                        include_filtered=True,
                    )
                    rejected = [row for row in rows if row.rejection_reason]
                    rejected.sort(key=lambda row: (
                        _safe_num(getattr(row, "net_edge", None)),
                        _safe_num(getattr(row, "depth_edge", None)),
                        _safe_num(getattr(row, "top_of_book_edge", None)),
                    ), reverse=True)
                    self._send_json([
                        {"candidate": _serializable(candidate_to_dict(row)), "risk": assess_candidate_risk(row)}
                        for row in rejected[:top]
                    ])
                    return
                if parsed.path == "/api/paper-enter":
                    query = parse_qs(parsed.query)
                    monitor = _safe_monitor_path(query.get("input", [str(default_input)])[0])
                    portfolio = _safe_monitor_path(query.get("portfolio", ["data/portfolio.json"])[0])
                    limit = _int(query.get("limit", ["5"])[0], default=5)
                    require_inventory = _bool(query.get("require_sell_inventory", ["false"])[0])
                    self._send_json(paper_enter_from_monitor(monitor, portfolio, max_allocations=limit, require_sell_inventory=require_inventory))
                    return
                if parsed.path == "/api/paper-sync":
                    query = parse_qs(parsed.query)
                    monitor = _safe_monitor_path(query.get("input", [str(default_input)])[0])
                    portfolio = _safe_monitor_path(query.get("portfolio", ["data/portfolio.json"])[0])
                    self._send_json(paper_sync_from_monitor(monitor, portfolio))
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


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -999.0


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _filter_by_category(markets: list, category: str) -> list:
    tokens = _tokens(category)
    if not tokens:
        return markets
    return [market for market in markets if tokens <= _tokens(_market_text(market))]


def _filter_by_max_close_hours(markets: list, max_close_hours: float) -> list:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    rows = []
    for market in markets:
        close_time = getattr(market, "close_time", None)
        if not close_time:
            continue
        try:
            close_at = datetime.fromisoformat(str(close_time).replace("Z", "+00:00"))
        except ValueError:
            continue
        if close_at.tzinfo is None:
            close_at = close_at.replace(tzinfo=timezone.utc)
        hours = (close_at.astimezone(timezone.utc) - now).total_seconds() / 3600.0
        if 0 <= hours <= max_close_hours:
            rows.append(market)
    return rows


def _market_text(market: object) -> str:
    raw = getattr(market, "raw", {}) or {}
    return " ".join(
        str(item)
        for item in [
            getattr(market, "title", ""),
            raw.get("description", ""),
            raw.get("slug", ""),
            " ".join(str(item) for item in raw.get("categories", []) if item),
            " ".join(str(item) for item in raw.get("tags", []) if item),
            str(raw.get("groupItemTitle") or ""),
        ]
        if item
    )


def _tokens(value: str) -> set[str]:
    import re

    aliases = {"bitcoin": "btc", "ethereum": "eth"}
    return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Панель арбитража прогнозных рынков</title>
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
    .coverage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
    .chip { border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; background: #fbfcfd; font-size: 12px; }
    .explain { display: grid; gap: 8px; margin-top: 12px; color: var(--muted); }
    .formula { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #fbfcfd; border: 1px solid var(--line); border-radius: 6px; padding: 8px; color: var(--ink); overflow-wrap: anywhere; }
    .switch { display: flex; align-items: center; gap: 7px; height: 34px; color: var(--muted); }
    .switch input { height: auto; min-width: auto; }
    @media (max-width: 980px) {
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .grid, .planner, .coverage-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topline">
      <div><h1>Панель арбитража прогнозных рынков</h1><div class="muted" id="subtitle">Загружаю данные монитора</div></div>
      <div class="controls"><select id="fileSelect"></select><button class="primary" id="refreshBtn">Обновить</button></div>
    </div>
    <div class="tabs">
      <button class="tab active" data-view="overview">Обзор</button>
      <button class="tab" data-view="capital">Капитал</button>
      <button class="tab" data-view="review">Проверка</button>
      <button class="tab" data-view="portfolio">Бумажный портфель</button>
      <button class="tab" data-view="coverage">Покрытие источников</button>
      <button class="tab" data-view="events">События</button>
    </div>
  </header>
  <main>
    <section class="metrics">
      <div class="metric"><div class="label">Снимки</div><div class="value" id="snapshots">0</div></div>
      <div class="metric"><div class="label">Активно сейчас</div><div class="value" id="active">0</div></div>
      <div class="metric"><div class="label">Маршрутов найдено</div><div class="value" id="routes">0</div></div>
      <div class="metric"><div class="label">Лучшая сейчас</div><div class="value ok" id="bestEdge">0%</div></div>
      <div class="metric"><div class="label">Прибыль сейчас</div><div class="value ok" id="bestProfit">$0</div></div>
      <div class="metric"><div class="label">Ошибки</div><div class="value" id="errors">0</div></div>
    </section>

    <section class="view active" id="overview">
      <div class="grid">
        <div class="panel"><h2>Динамика лучшей исполнимой доходности</h2><canvas id="trend" width="900" height="260"></canvas></div>
        <div class="panel"><h2>Последние события</h2><div class="events" id="eventsSmall"></div></div>
      </div>
      <div class="panel">
        <h2>Активные маршруты сейчас</h2>
        <table><thead><tr><th>Маршрут</th><th>Рынок</th><th>Net edge</th><th>Размер</th><th>Комиссии</th><th>Оценка прибыли</th><th>Обнаружено</th></tr></thead><tbody id="routesTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="capital">
      <div class="panel">
        <h2>Планировщик капитала</h2>
        <div class="planner">
          <label><div class="label">USDC на Limitless</div><input id="limitlessCash" type="number" value="250" min="0" step="10"></label>
          <label><div class="label">USDC на Polymarket</div><input id="polymarketCash" type="number" value="250" min="0" step="10"></label>
          <label><div class="label">Макс. позиций</div><input id="allocationLimit" type="number" value="10" min="1" step="1"></label>
          <label class="switch"><input id="assumeInventory" type="checkbox" checked> теоретический режим: не проверять sell-shares</label>
          <button class="primary" id="planBtn">Посчитать</button>
        </div>
        <div class="explain">
          <div><strong>Sell-shares</strong> - это YES/NO shares, которые уже лежат на площадке, где мы хотим продавать. Если их нет, мгновенно продать вторую ногу нельзя.</div>
          <div>Когда галочка включена, планировщик делает теоретическую оценку и считает, что нужные shares уже есть. Когда выключена, сделки без указанного инвентаря будут отклонены.</div>
        </div>
      </div>
      <div class="panel">
        <h2>Как считаются комиссии</h2>
        <div class="explain">
          <div>Сначала scanner считает среднюю цену исполнения по стакану на выбранный размер: <strong>avg buy</strong> и <strong>avg sell</strong>. Потом из разницы вычитает запас и комиссии.</div>
          <div class="formula">net edge = avg sell price - avg buy price - safety buffer - fee estimate</div>
          <div class="formula">estimated profit = net edge * executable size</div>
          <div><strong>Polymarket:</strong> если API говорит feesEnabled=false, комиссия считается 0. Иначе используется feeSchedule: rate * price * (1 - price). Если feeSchedule нет, применяется fallback.</div>
          <div><strong>Polymarket:</strong> комиссия округляется до 5 знаков, как в публичной документации. В расчете мы считаем taker-вход: покупка и продажа съедают стакан.</div>
          <div><strong>Limitless:</strong> если есть creatorFeePct, он учитывается. Если точная кривая комиссии не пришла из API, используется ручной запас <strong>fee-bps</strong>, а сделка получает повышенный risk score.</div>
          <div><strong>fee-bps 50</strong> в текущем мониторе означает 0.50%, не 50%. Например, при ценах 0.40 и 0.45 ручной запас равен (0.40 + 0.45) * 0.005 = 0.00425 на share.</div>
          <div>Не учитываются: gas, bridge/withdrawal, задержка перевода капитала между площадками и цена предварительного получения sell-shares.</div>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="label">Выбрано</div><div class="value" id="planAllocated">0</div></div>
        <div class="metric"><div class="label">Отклонено</div><div class="value" id="planRejected">0</div></div>
        <div class="metric"><div class="label">Кэш на покупки</div><div class="value" id="planCashUsed">$0</div></div>
        <div class="metric"><div class="label">Оценка прибыли</div><div class="value ok" id="planProfit">$0</div></div>
        <div class="metric"><div class="label">Остаток Limitless</div><div class="value" id="limitlessLeft">$0</div></div>
        <div class="metric"><div class="label">Остаток Polymarket</div><div class="value" id="polymarketLeft">$0</div></div>
      </div>
      <div class="panel">
        <h2>План распределения</h2>
        <table><thead><tr><th>Маршрут</th><th>Кэш покупки</th><th>Инвентарь продажи</th><th>Edge</th><th>Комиссии</th><th>Прибыль</th></tr></thead><tbody id="allocationTable"></tbody></table>
      </div>
      <div class="panel">
        <h2>Отклоненные сигналы</h2>
        <table><thead><tr><th>Маршрут</th><th>Причина</th><th>Нужно</th></tr></thead><tbody id="rejectedTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="events">
      <div class="panel"><h2>События монитора</h2><div class="events" id="eventsFull"></div></div>
    </section>

    <section class="view" id="review">
      <div class="panel">
        <h2>Ручная проверка совпадения рынков</h2>
        <div class="planner">
          <label><div class="label">Файл очереди</div><input id="reviewPath" value="data/review-candidates.jsonl"></label>
          <label><div class="label">Категория near-misses</div><input id="nearCategory" value="crypto"></label>
          <label><div class="label">Часов до закрытия</div><input id="nearHours" type="number" value="24" min="1"></label>
          <label><div class="label">Лимит загрузки</div><input id="nearLimit" type="number" value="200" min="10"></label>
          <button class="primary" id="reviewRefreshBtn">Обновить очередь</button>
          <button id="nearRefreshBtn">Найти near-misses</button>
        </div>
        <div class="explain">
          <div>Сюда попадают сделки с высоким risk score: слишком большая доходность, разные источники цены, слабый текстовый матч или другие признаки, что рынки могут быть не про одно и то же.</div>
          <div>Кнопки разметки сохраняют ответ в <strong>data/review-labels.jsonl</strong>. Потом этот файл можно использовать как датасет для обучения или настройки matcher.</div>
        </div>
      </div>
      <div class="panel">
        <h2>Очередь из монитора</h2>
        <table><thead><tr><th>Сигнал</th><th>Риск</th><th>Рынки</th><th>Разметка</th></tr></thead><tbody id="reviewTable"></tbody></table>
      </div>
      <div class="panel">
        <h2>Near-misses: сильные отклоненные кандидаты</h2>
        <table><thead><tr><th>Сигнал</th><th>Причина отказа</th><th>Риск</th><th>Рынки</th></tr></thead><tbody id="nearTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="portfolio">
      <div class="panel">
        <h2>Бумажный портфель</h2>
        <div class="planner">
          <label><div class="label">Файл портфеля</div><input id="portfolioPath" value="data/portfolio.json"></label>
          <label><div class="label">Макс. новых входов</div><input id="paperLimit" type="number" value="5" min="1"></label>
          <label class="switch"><input id="paperRequireInventory" type="checkbox"> требовать sell-инвентарь</label>
          <button class="primary" id="paperEnterBtn">Войти по последним</button>
          <button id="paperSyncBtn">Обновить оценки</button>
          <button id="portfolioRefreshBtn">Обновить портфель</button>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="label">Открытые позиции</div><div class="value" id="portfolioOpen">0</div></div>
        <div class="metric"><div class="label">Открытый notional</div><div class="value" id="portfolioNotional">$0</div></div>
        <div class="metric"><div class="label">Прибыль на входе</div><div class="value ok" id="portfolioProfit">$0</div></div>
        <div class="metric"><div class="label">Текущая прибыль</div><div class="value ok" id="portfolioCurrentProfit">$0</div></div>
        <div class="metric"><div class="label">Realized PnL</div><div class="value" id="portfolioPnl">$0</div></div>
        <div class="metric"><div class="label">Кэш Limitless</div><div class="value" id="portfolioLimitless">$0</div></div>
      </div>
      <div class="panel">
        <h2>Открытые бумажные позиции</h2>
        <table><thead><tr><th>Маршрут</th><th>Кэш</th><th>Инвентарь</th><th>Edge входа</th><th>Оценка прибыли</th><th>Открыта</th></tr></thead><tbody id="portfolioTable"></tbody></table>
      </div>
    </section>

    <section class="view" id="coverage">
      <div class="panel">
        <h2>Покрытие источников</h2>
        <div class="planner">
          <label><div class="label">Фильтр категории</div><input id="coverageCategory" value="crypto"></label>
          <label><div class="label">Макс. часов до закрытия</div><input id="coverageHours" type="number" value="24" min="1"></label>
          <label><div class="label">Лимит загрузки</div><input id="coverageLimit" type="number" value="200" min="10"></label>
          <button class="primary" id="coverageRefreshBtn">Анализировать</button>
        </div>
      </div>
      <div class="coverage-grid">
        <div class="panel"><h2>Limitless</h2><div id="coverageLimitless"></div></div>
        <div class="panel"><h2>Polymarket</h2><div id="coveragePolymarket"></div></div>
      </div>
    </section>
  </main>

  <script>
    const state = { input: "data/monitor-short-all.jsonl", report: null, snapshots: [], plan: null, portfolio: null, coverage: null, review: [], near: [] };
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
      await refreshPortfolio();
      await refreshReview();
      if (!state.coverage) await refreshCoverage();
      render();
    }

    async function refreshPlanner() {
      const cash = `limitless=${Number(el("limitlessCash").value || 0)},polymarket=${Number(el("polymarketCash").value || 0)}`;
      const assume = el("assumeInventory").checked ? "true" : "false";
      const limit = Number(el("allocationLimit").value || 10);
      state.plan = await json(`/api/capital?input=${encodeURIComponent(state.input)}&cash=${encodeURIComponent(cash)}&assume_sell_inventory=${assume}&limit=${limit}`);
    }

    async function refreshPortfolio() {
      state.portfolio = await json(`/api/portfolio?portfolio=${encodeURIComponent(el("portfolioPath").value || "data/portfolio.json")}`);
    }

    async function refreshCoverage() {
      const category = encodeURIComponent(el("coverageCategory").value || "");
      const hours = encodeURIComponent(el("coverageHours").value || "");
      const limit = Number(el("coverageLimit").value || 200);
      state.coverage = await json(`/api/coverage?category=${category}&max_close_hours=${hours}&limit=${limit}`);
    }

    async function refreshReview() {
      const path = encodeURIComponent(el("reviewPath").value || "data/review-candidates.jsonl");
      state.review = await json(`/api/review?input=${path}&limit=100`);
    }

    async function refreshNearMisses() {
      const category = encodeURIComponent(el("nearCategory").value || "");
      const hours = encodeURIComponent(el("nearHours").value || "24");
      const limit = Number(el("nearLimit").value || 200);
      state.near = await json(`/api/near-misses?category=${category}&max_close_hours=${hours}&limit=${limit}&top=30&size=100&fee_bps=10`);
      render();
    }

    async function labelReview(reviewId, label) {
      await json(`/api/review-label?review_id=${encodeURIComponent(reviewId)}&label=${encodeURIComponent(label)}`);
      await refreshReview();
      render();
    }

    async function paperEnter() {
      const portfolio = encodeURIComponent(el("portfolioPath").value || "data/portfolio.json");
      const limit = Number(el("paperLimit").value || 5);
      const requireInventory = el("paperRequireInventory").checked ? "true" : "false";
      state.portfolio = (await json(`/api/paper-enter?input=${encodeURIComponent(state.input)}&portfolio=${portfolio}&limit=${limit}&require_sell_inventory=${requireInventory}`)).portfolio;
      render();
    }

    async function paperSync() {
      const portfolio = encodeURIComponent(el("portfolioPath").value || "data/portfolio.json");
      state.portfolio = (await json(`/api/paper-sync?input=${encodeURIComponent(state.input)}&portfolio=${portfolio}`)).portfolio;
      render();
    }

    function render() {
      const r = state.report || {};
      const latestRoutes = r.latest_routes || [];
      const historicalRoutes = r.best_routes || [];
      const best = (latestRoutes || [])[0] || {};
      el("subtitle").textContent = `${r.input || state.input} | последний успешный снимок ${r.last_success_detected_at || "n/a"}`;
      el("snapshots").textContent = r.snapshots || 0;
      el("active").textContent = r.latest_active_count || 0;
      el("routes").textContent = r.unique_routes_seen || 0;
      el("bestEdge").textContent = pct(best.net_edge);
      el("bestProfit").textContent = money(best.estimated_profit);
      el("errors").textContent = r.error_snapshots || 0;
      el("errors").className = (r.error_snapshots || 0) ? "value warn" : "value";
      renderRoutes(latestRoutes, historicalRoutes);
      renderEvents(el("eventsSmall"), state.snapshots.slice(-12).reverse());
      renderEvents(el("eventsFull"), state.snapshots.slice(-80).reverse());
      renderPlan(state.plan || {});
      renderPortfolio(state.portfolio || {});
      renderCoverage(state.coverage || {});
      renderReview(state.review || []);
      renderNear(state.near || []);
      drawTrend(state.snapshots);
    }

    function renderRoutes(rows, historicalRows) {
      const activeHtml = rows.length ? rows.map(row => `
        <tr>
          <td><div class="route">${row.outcome || ""} ${row.route || ""}</div><div class="muted">${row.key || ""}</div></td>
          <td>${escapeHtml(row.buy_title || "")}<br><span class="muted">${escapeHtml(row.sell_title || "")}</span></td>
          <td class="num ok">${pct(row.net_edge)}</td>
          <td class="num">${fmt.format(row.executable_size || 0)}</td>
          <td class="num">${formatFee(row)}</td>
          <td class="num ok">${money(row.estimated_profit)}</td>
          <td class="num">${row.detected_at || ""}</td>
        </tr>`).join("") : `<tr><td colspan="7" class="muted">Сейчас активных исполнимых маршрутов нет.</td></tr>`;
      const historical = (historicalRows || [])[0];
      const historicalHtml = historical ? `<tr><td colspan="7" class="muted">Исторический максимум в файле: ${pct(historical.net_edge)} / ${money(historical.estimated_profit)}. Это не обязательно активная сейчас сделка.</td></tr>` : "";
      el("routesTable").innerHTML = activeHtml + historicalHtml;
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
        <td class="num ok">${pct(row.net_edge)}</td><td class="num">${formatFee(row)}</td><td class="num ok">${money(row.estimated_profit)}</td></tr>`).join("");
      el("rejectedTable").innerHTML = (plan.rejected || []).slice(0, 20).map(row => `
        <tr><td>${row.outcome || ""} ${row.buy_source || ""}->${row.sell_source || ""}</td><td class="bad">${translateReason(row.planner_rejection_reason || "")}</td>
        <td class="num">${money(row.buy_cash_required)} / ${fmt.format(row.sell_inventory_required || 0)} shares</td></tr>`).join("");
    }

    function renderPortfolio(portfolio) {
      const cash = portfolio.cash || {};
      el("portfolioOpen").textContent = portfolio.open_count || 0;
      el("portfolioNotional").textContent = money(portfolio.open_notional);
      el("portfolioProfit").textContent = money(portfolio.open_estimated_profit);
      el("portfolioCurrentProfit").textContent = money(portfolio.current_estimated_profit);
      el("portfolioPnl").textContent = money(portfolio.realized_pnl);
      el("portfolioLimitless").textContent = money(cash.limitless);
      el("portfolioTable").innerHTML = (portfolio.open_positions || []).map(row => `
        <tr><td><div class="route">${row.outcome || ""} ${row.route || ""}</div><div class="muted">${escapeHtml(row.buy_title || "")}</div><div class="muted">${row.key || ""}</div></td>
        <td class="num">${money(row.buy_cash_required)}</td><td class="num">${fmt.format(row.sell_inventory_required || 0)}<br><span class="muted">${row.sell_inventory_key || ""}</span></td>
        <td class="num ok">${pct(row.current_net_edge ?? row.entry_net_edge)}</td><td class="num ok">${money(row.current_estimated_profit ?? row.entry_estimated_profit)}</td><td class="num">${row.market_status || "entry"}<br><span class="muted">${row.opened_at || ""}</span></td></tr>`).join("");
    }

    function renderCoverage(coverage) {
      const sources = coverage.sources || {};
      renderCoverageSource("coverageLimitless", sources.limitless || {});
      renderCoverageSource("coveragePolymarket", sources.polymarket || {});
    }

    function renderCoverageSource(targetId, source) {
      const rows = source.examples || [];
      el(targetId).innerHTML = `
        <div class="metrics">
          <div class="metric"><div class="label">Рынки</div><div class="value">${source.count || 0}</div></div>
          <div class="metric"><div class="label">Краткосрочные 24ч</div><div class="value">${source.short_term_24h_count || 0}</div></div>
        </div>
        <div class="chips">${chips(source.by_condition_kind || {}, "kind")}</div>
        <div class="chips">${chips(source.by_asset || {}, "asset")}</div>
        <div class="chips">${chips(source.by_interval_minutes || {}, "interval")}</div>
        <table><thead><tr><th>Рынок</th><th>Тип</th><th>Актив</th><th>Интервал</th><th>Закрытие</th></tr></thead><tbody>
          ${rows.map(row => `<tr><td>${escapeHtml(row.title || "")}</td><td>${(row.condition || {}).kind || ""}</td><td>${(row.condition || {}).asset || ""}</td><td class="num">${(row.condition || {}).interval_minutes || ""}</td><td class="num">${row.close_time || ""}</td></tr>`).join("")}
        </tbody></table>`;
    }

    function renderReview(rows) {
      const pending = [...rows].reverse();
      el("reviewTable").innerHTML = pending.length ? pending.map(row => {
        const c = row.candidate || {}, r = row.risk || {}, label = row.label || null;
        return `<tr>
          <td><div class="route">#${row.review_id}</div><div>${c.outcome || ""} ${c.buy_source || ""}->${c.sell_source || ""}</div><div class="num ok">${pct(c.net_edge)} / ${money((c.net_edge || 0) * (c.executable_size || 0))}</div></td>
          <td><span class="${riskClass(r.risk_level)}">${translateRisk(r.risk_level)}</span><br><span class="muted">score=${r.risk_score || 0}</span><br>${riskComponents(r)}<span class="muted">${(r.reasons || []).map(translateRiskReason).join(", ")}</span></td>
          <td>${escapeHtml(c.buy_title || "")}<br><span class="muted">${escapeHtml(c.sell_title || "")}</span></td>
          <td>${label ? `<span class="ok">${translateLabel(label.label)}</span>` : reviewButtons(row.review_id)}</td>
        </tr>`;
      }).join("") : `<tr><td colspan="4" class="muted">Очередь пуста. Включи monitor с --save-suspicious, чтобы наполнять ее автоматически.</td></tr>`;
    }

    function renderNear(rows) {
      el("nearTable").innerHTML = rows.length ? rows.map(row => {
        const c = row.candidate || {}, r = row.risk || {};
        return `<tr>
          <td><div class="route">${c.outcome || ""} ${c.buy_source || ""}->${c.sell_source || ""}</div><div class="num">${pct(c.net_edge)} / depth ${pct(c.depth_edge)}</div></td>
          <td class="bad">${translateReason(c.rejection_reason || "")}</td>
          <td><span class="${riskClass(r.risk_level)}">${translateRisk(r.risk_level)}</span><br>${riskComponents(r)}<span class="muted">${(r.reasons || []).map(translateRiskReason).join(", ")}</span></td>
          <td>${escapeHtml(c.buy_title || "")}<br><span class="muted">${escapeHtml(c.sell_title || "")}</span></td>
        </tr>`;
      }).join("") : `<tr><td colspan="4" class="muted">Нажми “Найти near-misses”, чтобы загрузить текущие отклоненные кандидаты.</td></tr>`;
    }

    function reviewButtons(id) {
      return `<button onclick="labelReview('${id}', 'same_event')">То же событие</button> <button onclick="labelReview('${id}', 'different_event')">Не то</button> <button onclick="labelReview('${id}', 'unsure')">Сомнительно</button>`;
    }

    function riskComponents(risk) {
      const c = risk.components || {};
      return `<div class="chips">
        <span class="chip ${riskClass((c.matching || {}).level)}">матчинг: ${translateRisk((c.matching || {}).level)}</span>
        <span class="chip ${riskClass((c.depth || {}).level)}">стакан: ${translateRisk((c.depth || {}).level)}</span>
        <span class="chip ${riskClass((c.fees || {}).level)}">комиссии: ${translateRisk((c.fees || {}).level)}</span>
      </div>`;
    }

    function chips(obj, label) {
      return Object.entries(obj).map(([key, value]) => `<span class="chip">${label}: ${escapeHtml(key)} ${value}</span>`).join("");
    }

    function renderEvents(target, rows) {
      target.innerHTML = rows.map(row => {
        if (row.type === "error") return `<div class="event error"><strong class="bad">Ошибка</strong><br><span class="muted">${row.detected_at}</span><br>${escapeHtml(row.error || "")}</div>`;
        return `<div class="event"><strong>${row.opportunity_count || 0} активно</strong> <span class="ok">+${row.new_count || 0}</span> <span class="bad">-${row.gone_count || 0}</span><br><span class="muted">${row.detected_at}</span><br><span class="muted">${escapeHtml(row.query || "")}</span></div>`;
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
      ctx.stroke(); ctx.fillStyle = "#16202a"; ctx.fillText("лучший net edge", pad, 18); ctx.fillStyle = "#1f6fb2"; ctx.fillText(pct(maxEdge), w - 86, 18);
    }

    function translateReason(value) {
      const map = {
        insufficient_buy_cash: "недостаточно кэша для покупки",
        insufficient_sell_inventory: "недостаточно инвентаря для продажи",
        duplicate_market_exposure: "дублирующий риск на рынок",
        incomplete_buy_fill: "стакан покупки не заполняет размер",
        incomplete_sell_fill: "стакан продажи не заполняет размер",
        net_edge_below_min: "net edge ниже минимума",
        profit_below_min: "прибыль ниже минимума",
        orderbook_unavailable: "стакан недоступен",
      };
      return map[value] || value;
    }

    function translateRisk(value) {
      return { low: "низкий", medium: "средний", high: "высокий" }[value] || value || "";
    }

    function translateRiskReason(value) {
      const map = {
        hard_structural_warning: "жесткое структурное предупреждение",
        price_source_differs: "разный источник цены",
        price_pair_differs: "разная пара цены",
        low_match_score: "слабый матч текста",
        medium_match_score: "средний матч текста",
        high_net_edge: "высокая доходность",
        very_high_net_edge: "очень высокая доходность",
        extreme_net_edge: "аномальная доходность",
        large_top_depth_gap: "сильный разрыв top/depth",
        fee_estimate_missing: "нет оценки комиссий",
        fee_model_uncertain: "комиссии оценены неуверенно",
        limitless_fee_curve_unknown: "неизвестная кривая комиссии Limitless",
        low_manual_fee_buffer: "низкий ручной запас комиссии",
        filtered_candidate: "кандидат был отфильтрован",
      };
      return map[value] || value;
    }

    function translateLabel(value) {
      return { same_event: "то же событие", different_event: "не то событие", unsure: "сомнительно" }[value] || value;
    }

    function riskClass(value) {
      if (value === "high") return "bad";
      if (value === "medium") return "warn";
      return "ok";
    }

    function formatFee(row) {
      const notes = (row.fee_notes || []).map(translateFeeNote);
      const fee = row.fee_estimate == null ? "n/a" : pct(row.fee_estimate);
      return `${fee}<br><span class="muted">${notes.join(", ")}</span>`;
    }

    function translateFeeNote(value) {
      if (value === "polymarket_fees_disabled") return "Polymarket: 0";
      if (value === "polymarket_fee_rounded_5dp") return "Polymarket: округление 5 знаков";
      if (value === "limitless_fee_curve_unknown_use_manual_fee_bps") return "Limitless: ручной запас";
      if (value === "limitless_no_fee_field") return "Limitless: fee не найден";
      if (value === "manual_fee_buffer_missing") return "ручной запас не задан";
      if (value.startsWith("manual_fee_bps=")) return `ручной bps=${value.split("=")[1]}`;
      if (value.startsWith("polymarket_fee_rate=")) return `Polymarket rate=${value.split("=")[1]}`;
      if (value.startsWith("polymarket_fee_exponent=")) return `exp=${value.split("=")[1]}`;
      if (value.startsWith("limitless_creator_fee_pct=")) return `Limitless creator=${value.split("=")[1]}%`;
      return value;
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
    el("portfolioRefreshBtn").addEventListener("click", () => refreshPortfolio().then(render));
    el("coverageRefreshBtn").addEventListener("click", () => refreshCoverage().then(render));
    el("reviewRefreshBtn").addEventListener("click", () => refreshReview().then(render));
    el("nearRefreshBtn").addEventListener("click", refreshNearMisses);
    el("paperEnterBtn").addEventListener("click", paperEnter);
    el("paperSyncBtn").addEventListener("click", paperSync);
    loadFiles().then(refresh).catch(err => { el("subtitle").textContent = err.message; });
    setInterval(refresh, 30000);
  </script>
</body>
</html>"""
