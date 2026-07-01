from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request

from prediction_arb.reporting import summarize_monitor_history


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
    command = text.split()[0].lower()
    if command in ("/start", "/help"):
        return "Commands: /status, /report, /help"
    if command == "/status":
        summary = summarize_monitor_history(monitor_file, top=3)
        return (
            f"Monitor status\n"
            f"file: {summary['input']}\n"
            f"snapshots: {summary['snapshots']} ok={summary['successful_snapshots']} errors={summary['error_snapshots']}\n"
            f"active: {summary['latest_active_count']} routes_seen={summary['unique_routes_seen']}\n"
            f"last_success: {summary['last_success_detected_at']}"
        )
    if command == "/report":
        summary = summarize_monitor_history(monitor_file, top=5)
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
