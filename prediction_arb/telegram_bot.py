from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request

from prediction_arb.capital import plan_capital
from prediction_arb.reporting import latest_opportunities, summarize_monitor_history


def run_telegram_bot(bot_token: str, monitor_file: Path, allowed_chat_id: str | None = None, poll_interval: float = 2.0) -> None:
    offset = None
    print("Telegram bot command loop started.")
    while True:
        updates = _telegram_api(bot_token, "getUpdates", {"timeout": 25, "offset": offset})
        for update in updates.get("result", []):
            offset = int(update.get("update_id", 0)) + 1
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
                send_telegram_message(bot_token, chat_id, response)
        time.sleep(poll_interval)


def handle_bot_command(text: str, monitor_file: Path) -> str | None:
    parts = text.split()
    command = parts[0].lower()
    if command in ("/start", "/help"):
        return (
            "Commands:\n"
            "/status [file]\n"
            "/report [file]\n"
            "/capital [limitless_cash] [polymarket_cash] [file]\n"
            "/files\n"
            "/help"
        )
    if command == "/files":
        files = sorted(Path("data").glob("monitor*.jsonl"))
        if not files:
            return "No monitor JSONL files found."
        return "Monitor files:\n" + "\n".join(f"- {path.name}" for path in files[:20])
    if command == "/status":
        summary = summarize_monitor_history(_command_file(parts, monitor_file), top=3)
        return (
            f"Monitor status\n"
            f"file: {summary['input']}\n"
            f"snapshots: {summary['snapshots']} ok={summary['successful_snapshots']} errors={summary['error_snapshots']}\n"
            f"active: {summary['latest_active_count']} routes_seen={summary['unique_routes_seen']}\n"
            f"last_success: {summary['last_success_detected_at']}"
        )
    if command == "/report":
        summary = summarize_monitor_history(_command_file(parts, monitor_file), top=5)
        lines = [
            "Best routes",
            f"active={summary['latest_active_count']} errors={summary['error_snapshots']}",
        ]
        for item in summary["best_routes"]:
            net_edge = item["net_edge"] or 0.0
            profit = item["estimated_profit"] or 0.0
            lines.append(f"- {item['outcome']} {item['route']} edge={net_edge:.4f} profit=${profit:.2f}")
        if summary["last_error"]:
            lines.append(f"last_error: {summary['last_error']}")
        return "\n".join(lines)
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
            "Capital plan",
            f"file: {path}",
            f"allocated={plan['allocated_count']} rejected={plan['rejected_count']}",
            f"cash_used=${plan['total_buy_cash_required']:.2f} est_profit=${plan['total_estimated_profit']:.2f}",
            f"left limitless=${plan['cash_remaining'].get('limitless', 0):.2f} polymarket=${plan['cash_remaining'].get('polymarket', 0):.2f}",
        ]
        for item in plan["allocated"][:5]:
            lines.append(f"- {item['outcome']} {item['route']} cash=${item['buy_cash_required']:.2f} profit=${item['estimated_profit']:.2f}")
        return "\n".join(lines)
    return None


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    _telegram_api(
        bot_token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


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
