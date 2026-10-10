from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit


LANGUAGES = ("ru", "en", "zh", "ar")
TERMINAL = frozenset({"completed", "rejected", "cancelled", "expired", "delivery_failed"})


class OfferError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OfferSpec:
    gift_url: str
    gift_slug: str
    gift_name: str
    amount: str
    currency: str
    language: str


def parse_buy(text: str, default_language: str = "ru") -> OfferSpec:
    parts = text.strip().split()
    if len(parts) not in (4, 5) or parts[0].lower() != ".buy":
        raise OfferError("syntax")
    try:
        url = urlsplit(parts[1])
    except ValueError as exc:
        raise OfferError("gift_url") from exc
    if url.scheme != "https" or url.netloc.lower() != "t.me" or url.query or url.fragment:
        raise OfferError("gift_url")
    match = re.fullmatch(r"/nft/([A-Za-z][A-Za-z0-9]{0,99})-([1-9][0-9]{0,11})/?", url.path)
    if not match:
        raise OfferError("gift_url")
    collection, number = match.groups()
    currency = parts[3].lower()
    if currency not in {"stars", "gram"}:
        raise OfferError("currency")
    amount_text = parts[2]
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,9})?", amount_text):
        raise OfferError("amount")
    amount = Decimal(amount_text)
    maximum = Decimal(100_000_000 if currency == "stars" else 1_000_000)
    if amount <= 0 or amount > maximum:
        raise OfferError("amount")
    if currency == "stars" and amount != amount.to_integral_value():
        raise OfferError("stars_integer")
    if currency == "stars":
        amount_text = str(int(amount))
    else:
        amount_text = format(amount, "f").rstrip("0").rstrip(".") if "." in format(amount, "f") else format(amount, "f")
    language = parts[4].lower() if len(parts) == 5 else default_language
    if language not in LANGUAGES:
        raise OfferError("language")
    slug = f"{collection}-{number}"
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", collection)
    return OfferSpec(f"https://t.me/nft/{slug}", slug, f"{name} #{number}", amount_text, currency, language)


# Every state is a participant's statement, never a payment-system result.
TRANSITIONS = {
    "accept": ("seller", {"pending"}, "accepted"),
    "reject": ("seller", {"pending"}, "rejected"),
    "cancel": ("buyer", {"pending", "accepted"}, "cancelled"),
    "sent": ("seller", {"accepted", "payment_reported"}, "gift_reported"),
    "received": ("buyer", {"gift_reported"}, "gift_received"),
    "settled": ("seller", {"accepted", "gift_reported", "gift_received"}, "payment_reported"),
}


def next_status(offer: dict, action: str, actor_id: int, now: int) -> str:
    if action not in TRANSITIONS:
        raise OfferError("unknown_action")
    role, allowed_states, target = TRANSITIONS[action]
    if actor_id != offer[f"{role}_id"]:
        raise OfferError("wrong_role")
    if action == "settled" and offer.get("payment_claimed"):
        raise OfferError("already_done")
    if offer["status"] == "expired" or (offer["status"] == "pending" and now >= offer["expires_at"]):
        raise OfferError("expired")
    if offer["status"] == target:
        raise OfferError("already_done")
    if offer["status"] not in allowed_states:
        raise OfferError("wrong_state")
    if action == "settled":
        if offer["status"] == "gift_received":
            return "completed"
        if offer["status"] == "gift_reported":
            return "gift_reported"
    if action == "received" and offer.get("payment_claimed"):
        return "completed"
    return target
