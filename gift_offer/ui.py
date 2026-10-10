"""Custom emoji shared by offer cards and the private admin panel."""
from __future__ import annotations

import copy
import re
from html import escape


STARS_EMOJI_ID = "5848259999763011021"
GRAM_EMOJI_ID = "5377620962390857342"
NEGATIVE_EMOJI_ID = "5893081007153746175"
POSITIVE_EMOJI_ID = "5409029658794537988"
CURRENCY_EMOJI = {
    "stars": (STARS_EMOJI_ID, "⭐", "Stars"),
    "gram": (GRAM_EMOJI_ID, "💎", "Gram"),
}


def price_text(amount: str, currency: str) -> str:
    emoji_id, fallback, label = CURRENCY_EMOJI[currency]
    return f'{escape(amount)} <tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji> {label}'


def button(text: str, *, callback_data: str | None = None,
           url: str | None = None, negative: bool = False) -> dict:
    if (callback_data is None) == (url is None):
        raise ValueError("An inline button needs exactly one action.")
    result = {"text": text, "icon_custom_emoji_id": NEGATIVE_EMOJI_ID if negative else POSITIVE_EMOJI_ID}
    result["callback_data" if callback_data is not None else "url"] = callback_data if callback_data is not None else url
    return result


def has_custom_emoji(params: dict) -> bool:
    if "<tg-emoji " in params.get("text", ""):
        return True
    return any("icon_custom_emoji_id" in item
               for row in params.get("reply_markup", {}).get("inline_keyboard", []) for item in row)


def without_custom_emoji(params: dict) -> dict:
    """Keep all actions and labels when Telegram refuses custom emoji."""
    result = copy.deepcopy(params)
    if "text" in result:
        result["text"] = re.sub(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", r"\1", result["text"], flags=re.S)
    for row in result.get("reply_markup", {}).get("inline_keyboard", []):
        for item in row:
            emoji_id = item.pop("icon_custom_emoji_id", None)
            if emoji_id in {NEGATIVE_EMOJI_ID, POSITIVE_EMOJI_ID}:
                icon = "❌" if emoji_id == NEGATIVE_EMOJI_ID else "✅"
                item["text"] = f'{icon} {item["text"]}'
    return result
