"""Private admin views. Telegram identifiers stay inside links and callbacks."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape


PAGE_SIZE = 5
MSK = timezone(timedelta(hours=3))
CATEGORIES = {"all": "Все события", "visits": "Посещения", "transfers": "Передачи NFT", "offers": "Офферы"}
EVENT_NAMES = {
    "visit": "Запустил бота (/start)", "admin_activation": "Активировал админ-доступ",
    "offer_created": "Создал оффер", "offer_accepted": "Принял оффер",
    "offer_rejected": "Отклонил оффер", "offer_cancelled": "Отменил оффер",
    "transfer_claim": "Сообщил о передаче NFT", "receipt_claim": "Сообщил о получении NFT",
    "payment_claim": "Сообщил о получении оплаты", "telegram_transfer": "Передача NFT: событие Telegram",
}


def navigation() -> dict:
    return {"inline_keyboard": [
        [{"text": "Посещения", "callback_data": "a:visits:0"}, {"text": "Передачи NFT", "callback_data": "a:transfers:0"}],
        [{"text": "Офферы", "callback_data": "a:offers:0"}, {"text": "Все логи", "callback_data": "a:all:0"}],
        [{"text": "Обновить", "callback_data": "a:home"}],
    ]}


def home(stats: dict) -> tuple[str, dict]:
    text = (
        "<b>Админ-панель</b>\n\n"
        f'Посетителей: <b>{stats.get("unique_visitors", 0)}</b>\n'
        f'Запусков /start: <b>{stats.get("visit", 0)}</b>\n'
        f'Офферов: <b>{stats.get("offers", 0)}</b>\n'
        f'Заявлений о передаче NFT: <b>{stats.get("transfer_claim", 0)}</b>\n'
        f'Событий передачи от Telegram: <b>{stats.get("telegram_transfer", 0)}</b>\n\n'
        "Выбери журнал. Время показано по Москве.\n"
        "Посещения фиксируются по /start. Кнопки участников записывают их заявления; "
        "служебные события Telegram отмечаются отдельно."
    )
    return text, navigation()


def person(row: dict, prefix: str = "actor") -> str:
    user_id = row.get(f"{prefix}_id")
    if not user_id:
        return "Не установлен"
    full_name = " ".join(filter(None, [row.get(f"{prefix}_first"), row.get(f"{prefix}_last")]))
    label = escape((full_name or "Пользователь без имени")[:48])
    profile = f'<a href="tg://user?id={user_id}">{label}</a>'
    username = row.get(f"{prefix}_username") or ""
    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        profile += " @" + escape(username)
    return profile


def entry(row: dict) -> str:
    timestamp = datetime.fromtimestamp(row["at"], tz=MSK).strftime("%d.%m.%Y %H:%M:%S")
    kind = row["kind"]
    lines = [f'<b>{escape(EVENT_NAMES.get(kind, kind))}</b> · {timestamp}', f'Кто: {person(row)}']
    if row.get("recipient_id"):
        caption = "От кого" if kind == "receipt_claim" else "Кому"
        lines.append(f'{caption}: {person(row, "recipient")}')
    if row.get("gift_url"):
        lines.append(f'NFT: <a href="{escape(row["gift_url"], quote=True)}">{escape(row["gift_name"][:72])}</a>')
    if row.get("amount"):
        lines.append(f'Цена оффера: {escape(row["amount"])} {escape(row["currency"])}')
    if row["evidence"] == "telegram_service":
        lines.append("Источник: служебное событие Telegram.")
    elif kind in {"transfer_claim", "receipt_claim", "payment_claim"}:
        lines.append("Источник: заявление участника, без проверки транзакции.")
    return "\n".join(lines)


def log_page(category: str, rows: list[dict]) -> tuple[str, dict]:
    heading = f'<b>{CATEGORIES[category]}</b> · время МСК\n\n'
    blocks = []
    used = []
    for row in rows[:PAGE_SIZE]:
        block = entry(row)
        # Stay below Telegram's text limit even with heavily escaped profile names.
        if len(heading) + sum(len(item) + 2 for item in blocks) + len(block) > 3700:
            break
        blocks.append(block)
        used.append(row)
    text = heading + ("\n\n".join(blocks) if blocks else "Событий пока нет.")
    buttons = []
    if used and len(rows) > len(used):
        buttons.append({"text": "Старее", "callback_data": f'a:{category}:{used[-1]["id"]}'})
    buttons.append({"text": "Самые новые", "callback_data": f"a:{category}:0"})
    return text, {"inline_keyboard": [buttons, [{"text": "Админ-панель", "callback_data": "a:home"}]]}
