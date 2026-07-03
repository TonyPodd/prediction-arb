from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request

from prediction_arb.capital import plan_capital
from prediction_arb.coverage import summarize_source_coverage
from prediction_arb.paper import paper_enter_from_monitor, paper_sync_from_monitor
from prediction_arb.portfolio import load_portfolio, portfolio_summary
from prediction_arb.reporting import latest_opportunities, summarize_monitor_history
from prediction_arb.review_analysis import summarize_review_quality
from prediction_arb.review_store import append_review_label, load_review_queue
from prediction_arb.sources import limitless, polymarket


def run_telegram_bot(bot_token: str, monitor_file: Path, allowed_chat_id: str | None = None, poll_interval: float = 2.0) -> None:
    offset = None
    print("Telegram bot command loop started.")
    while True:
        updates = _telegram_api(bot_token, "getUpdates", {"timeout": 25, "offset": offset})
        for update in updates.get("result", []):
            offset = int(update.get("update_id", 0)) + 1
            callback = update.get("callback_query") or {}
            if callback:
                _handle_callback(bot_token, callback, allowed_chat_id)
                continue
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            text = str(message.get("text") or "").strip()
            if not chat_id or not text:
                continue
            if allowed_chat_id and chat_id != str(allowed_chat_id):
                continue
            response = handle_bot_command(text, monitor_file)
            if response:
                send_telegram_message(bot_token, chat_id, response, reply_markup=command_reply_markup(text))
        time.sleep(poll_interval)


def handle_bot_command(text: str, monitor_file: Path) -> str | None:
    parts = text.split()
    command = parts[0].lower()
    if command in ("/start", "/help"):
        return (
            "Бот prediction-arb\n"
            "Я показываю состояние монитора, план капитала, бумажный портфель и очередь ручной проверки.\n\n"
            "Команды:\n"
            "/status [file] - статус монитора\n"
            "/report [file] - лучшие активные маршруты\n"
            "/review - сделки для ручной проверки\n"
            "/review_report - качество ручной разметки\n"
            "/capital [limitless_cash] [polymarket_cash] [file] - план капитала\n"
            "/coverage [limit] [hours] [category] - покрытие источников\n"
            "/portfolio - бумажный портфель\n"
            "/paper_enter [file] [max] - бумажный вход\n"
            "/paper_sync [file] - обновить бумажные позиции\n"
            "/files - файлы монитора"
        )
    if command == "/files":
        files = sorted(Path("data").glob("monitor*.jsonl"))
        if not files:
            return "Файлы монитора JSONL не найдены."
        return "Файлы монитора:\n" + "\n".join(f"- {path.name}" for path in files[:20])
    if command == "/status":
        summary = summarize_monitor_history(_command_file(parts, monitor_file), top=3)
        return (
            f"Статус монитора\n"
            f"Файл: {summary['input']}\n"
            f"Снимки: {summary['snapshots']} ok={summary['successful_snapshots']} ошибки={summary['error_snapshots']}\n"
            f"Активно сейчас: {summary['latest_active_count']} маршрутов всего={summary['unique_routes_seen']}\n"
            f"Последний успешный снимок: {summary['last_success_detected_at']}"
        )
    if command == "/report":
        summary = summarize_monitor_history(_command_file(parts, monitor_file), top=5)
        lines = [
            "Лучшие маршруты сейчас",
            f"активно={summary['latest_active_count']} ошибки={summary['error_snapshots']}",
        ]
        for item in summary.get("latest_routes") or summary["best_routes"]:
            net_edge = item["net_edge"] or 0.0
            profit = item["estimated_profit"] or 0.0
            lines.append(f"- {item['outcome']} {item['route']} edge={net_edge:.4f} profit=${profit:.2f}")
        if summary["last_error"]:
            lines.append(f"последняя ошибка: {summary['last_error']}")
        return "\n".join(lines)
    if command == "/review":
        limit = int(_float(parts[1])) if len(parts) > 1 and _float(parts[1]) > 0 else 5
        return _format_review_queue(limit=limit)
    if command == "/review_report":
        return _format_review_report()
    if command == "/capital":
        limitless_cash = _float(parts[1]) if len(parts) > 1 else 250.0
        polymarket_cash = _float(parts[2]) if len(parts) > 2 else 250.0
        path = _file_from_name(parts[3]) if len(parts) > 3 else monitor_file
        plan = plan_capital(
            latest_opportunities(path),
            {"limitless": limitless_cash, "polymarket": polymarket_cash},
            assume_sell_inventory=True,
        )
        lines = [
            "План капитала",
            f"файл: {path}",
            f"выбрано={plan['allocated_count']} отклонено={plan['rejected_count']}",
            f"кэш на покупку=${plan['total_buy_cash_required']:.2f} оценка прибыли=${plan['total_estimated_profit']:.2f}",
            f"остаток limitless=${plan['cash_remaining'].get('limitless', 0):.2f} polymarket=${plan['cash_remaining'].get('polymarket', 0):.2f}",
        ]
        for item in plan["allocated"][:5]:
            lines.append(f"- {item['outcome']} {item['route']} cash=${item['buy_cash_required']:.2f} profit=${item['estimated_profit']:.2f}")
        return "\n".join(lines)
    if command == "/coverage":
        limit = int(_float(parts[1])) if len(parts) > 1 and _float(parts[1]) > 0 else 1000
        max_hours = _float(parts[2]) if len(parts) > 2 and _float(parts[2]) > 0 else 24.0
        category = parts[3] if len(parts) > 3 else ""
        limitless_markets = limitless.fetch_markets(limit=limit)
        polymarket_markets = polymarket.fetch_markets_expanded(limit=limit)
        if category:
            limitless_markets = _filter_by_category(limitless_markets, category)
            polymarket_markets = _filter_by_category(polymarket_markets, category)
        limitless_markets = _filter_by_max_close_hours(limitless_markets, max_hours)
        polymarket_markets = _filter_by_max_close_hours(polymarket_markets, max_hours)
        coverage = summarize_source_coverage(limitless_markets, polymarket_markets, example_limit=0)
        return _format_coverage(coverage, limit=limit, max_hours=max_hours, category=category)
    if command == "/portfolio":
        summary = portfolio_summary(load_portfolio(Path("data/portfolio.json")))
        return (
            "Бумажный портфель\n"
            f"кэш limitless=${summary['cash'].get('limitless', 0):.2f} polymarket=${summary['cash'].get('polymarket', 0):.2f}\n"
            f"открыто={summary['open_count']} закрыто={summary['closed_count']} отклонено={summary['rejected_count']}\n"
            f"открытый notional=${summary['open_notional']:.2f} прибыль входа=${summary['open_estimated_profit']:.2f} текущая прибыль=${summary['current_estimated_profit']:.2f}\n"
            f"realized pnl=${summary['realized_pnl']:.2f}"
        )
    if command == "/paper_enter":
        path = _file_from_name(parts[1]) if len(parts) > 1 else monitor_file
        limit = int(_float(parts[2])) if len(parts) > 2 else 5
        result = paper_enter_from_monitor(path, Path("data/portfolio.json"), max_allocations=limit)
        return (
            "Бумажный вход\n"
            f"файл: {path}\n"
            f"вошли={result['entered_count']}\n"
            f"открыто={result['portfolio']['open_count']} cash_limitless=${result['portfolio']['cash'].get('limitless', 0):.2f} "
            f"cash_polymarket=${result['portfolio']['cash'].get('polymarket', 0):.2f}"
        )
    if command == "/paper_sync":
        path = _file_from_name(parts[1]) if len(parts) > 1 else monitor_file
        result = paper_sync_from_monitor(path, Path("data/portfolio.json"))
        return (
            "Обновление бумажного портфеля\n"
            f"файл: {path}\n"
            f"обновлено={result['updated_count']} устарело={result['stale_count']}\n"
            f"текущая оценка прибыли=${result['portfolio']['current_estimated_profit']:.2f}"
        )
    return None


def send_telegram_message(bot_token: str, chat_id: str, text: str, reply_markup: dict[str, object] | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _telegram_api(
        bot_token,
        "sendMessage",
        payload,
    )


def command_reply_markup(text: str) -> dict[str, object]:
    command = (text.split() or [""])[0].lower()
    if command in {"/start", "/help"}:
        return {
            "keyboard": [
                [{"text": "/status"}, {"text": "/report"}],
                [{"text": "/review"}, {"text": "/capital"}],
                [{"text": "/review_report"}, {"text": "/portfolio"}],
                [{"text": "/coverage"}],
            ],
            "resize_keyboard": True,
        }
    if command == "/review":
        return _review_queue_keyboard()
    return {"keyboard": [[{"text": "/status"}, {"text": "/review"}, {"text": "/report"}]], "resize_keyboard": True}


def _handle_callback(bot_token: str, callback: dict[str, object], allowed_chat_id: str | None) -> None:
    message = callback.get("message", {}) if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat", {}) if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    if allowed_chat_id and chat_id != str(allowed_chat_id):
        return
    data = str(callback.get("data") or "")
    callback_id = str(callback.get("id") or "")
    if data.startswith("review:"):
        _, label, review_id = data.split(":", 2)
        append_review_label(review_id, label, actor=chat_id)
        _telegram_api(bot_token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Разметка сохранена"})
        send_telegram_message(bot_token, chat_id, f"Сохранил разметку #{review_id}: {_translate_label(label)}")


def _format_review_queue(limit: int = 5) -> str:
    rows = [row for row in load_review_queue(limit=50) if not row.get("label")][:limit]
    if not rows:
        return "Очередь ручной проверки пуста."
    lines = ["Очередь ручной проверки:"]
    for row in rows:
        candidate = row.get("candidate", {}) if isinstance(row.get("candidate"), dict) else {}
        risk = row.get("risk", {}) if isinstance(row.get("risk"), dict) else {}
        net_edge = _float(candidate.get("net_edge"))
        size = _float(candidate.get("executable_size"))
        lines.extend(
            [
                "",
                f"#{row.get('review_id')} риск={risk.get('risk_level')} score={risk.get('risk_score')}",
                f"{candidate.get('outcome')} {candidate.get('buy_source')} -> {candidate.get('sell_source')} edge={net_edge:.2%} profit=${net_edge * size:.2f}",
                f"Купить: {candidate.get('buy_title')}",
                f"Продать: {candidate.get('sell_title')}",
            ]
        )
    return "\n".join(lines)


def _format_review_report() -> str:
    report = summarize_review_quality()
    fp = report.get("false_positive_rate")
    same = report.get("same_event_rate")
    labels = report.get("label_counts", {}) if isinstance(report.get("label_counts"), dict) else {}
    reasons = report.get("different_event_reason_counts", {}) if isinstance(report.get("different_event_reason_counts"), dict) else {}
    lines = [
        "Качество ручной проверки",
        f"всего={report['total_candidates']} размечено={report['labeled_count']} pending={report['pending_count']}",
        f"то же={labels.get('same_event', 0)} не то={labels.get('different_event', 0)} сомнительно={labels.get('unsure', 0)}",
        f"same rate={_fmt_rate(same)} false positive={_fmt_rate(fp)}",
    ]
    if reasons:
        lines.append("Причины у 'не то': " + ", ".join(f"{key}:{value}" for key, value in list(reasons.items())[:5]))
    return "\n".join(lines)


def _review_queue_keyboard() -> dict[str, object]:
    rows = [row for row in load_review_queue(limit=20) if not row.get("label")][:3]
    keyboard = []
    for row in rows:
        review_id = str(row.get("review_id") or "")
        keyboard.append(
            [
                {"text": f"{review_id}: то же", "callback_data": f"review:same_event:{review_id}"},
                {"text": "не то", "callback_data": f"review:different_event:{review_id}"},
                {"text": "сомнительно", "callback_data": f"review:unsure:{review_id}"},
            ]
        )
    return {"inline_keyboard": keyboard} if keyboard else {"keyboard": [[{"text": "/status"}, {"text": "/report"}]], "resize_keyboard": True}


def _telegram_api(bot_token: str, method: str, payload: dict[str, object]) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url=f"https://api.telegram.org/bot{bot_token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=35) as response:
        return json.loads(response.read().decode("utf-8"))


def _command_file(parts: list[str], default: Path) -> Path:
    return _file_from_name(parts[1]) if len(parts) > 1 else default


def _file_from_name(value: str) -> Path:
    path = Path(value)
    if path.parent == Path("."):
        path = Path("data") / value
    if path.is_absolute() or ".." in path.parts:
        return Path("data/monitor-taiwan.jsonl")
    return path


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_coverage(coverage: dict[str, object], *, limit: int, max_hours: float, category: str) -> str:
    sources = coverage.get("sources", {}) if isinstance(coverage, dict) else {}
    lines = [
        "Покрытие источников",
        f"limit={limit} max_hours={max_hours:g}" + (f" category={category}" if category else ""),
    ]
    for name in ("limitless", "polymarket"):
        source = sources.get(name, {}) if isinstance(sources, dict) else {}
        lines.append(
            f"{name}: markets={source.get('count', 0)} short24h={source.get('short_term_24h_count', 0)} "
            f"kinds={_compact_counts(source.get('by_condition_kind', {}))}"
        )
        lines.append(
            f"  assets={_compact_counts(source.get('by_asset', {}), limit=5)} "
            f"intervals={_compact_counts(source.get('by_interval_minutes', {}), limit=5)}"
        )
    return "\n".join(lines)


def _translate_label(label: str) -> str:
    return {
        "same_event": "то же событие",
        "different_event": "не то событие",
        "unsure": "сомнительно",
    }.get(label, label)


def _fmt_rate(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _compact_counts(value: object, *, limit: int = 4) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    return ", ".join(f"{key}:{count}" for key, count in list(value.items())[:limit])


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
