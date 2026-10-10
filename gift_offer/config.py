from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """A small, literal .env reader. Existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Неверная строка в .env: используйте KEY=value.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}: допустимо от {minimum} до {maximum}.")
    return value


@dataclass(frozen=True)
class Config:
    token: str
    name: str = "Gift Offer"
    admin_promo: str = "/ClezzyKryt"
    default_language: str = "ru"
    offer_ttl: int = 21600
    cooldown: int = 3
    db_path: str = "data/offers.sqlite3"
    port: int = 3000

    @classmethod
    def from_env(cls) -> "Config":
        token = next((os.environ.get(key, "").strip() for key in
                      ("BOT_TOKEN", "API_TOKEN", "TELEGRAM_BOT_TOKEN")
                      if os.environ.get(key, "").strip()), "")
        if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("Укажите настоящий BOT_TOKEN из @BotFather в .env или переменных сервера.")
        language = os.environ.get("DEFAULT_LANGUAGE", "ru").strip().lower()
        if language not in {"ru", "en", "zh", "ar"}:
            raise ValueError("DEFAULT_LANGUAGE: ru, en, zh или ar.")
        name = os.environ.get("BOT_NAME", "Gift Offer").strip()
        if not 1 <= len(name) <= 64:
            raise ValueError("BOT_NAME: от 1 до 64 символов.")
        admin_promo = os.environ.get("ADMIN_PROMO", "/ClezzyKryt").strip()
        if not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_]{4,63}", admin_promo):
            raise ValueError("ADMIN_PROMO должен быть командой вида /PromoCode.")
        data_dir = os.environ.get("DATA_DIR", "data").strip() or "data"
        db_path = os.environ.get("DB_PATH", "").strip() or str(Path(data_dir) / "offers.sqlite3")
        return cls(
            token=token,
            name=name,
            admin_promo=admin_promo,
            default_language=language,
            offer_ttl=integer_env("OFFER_TTL_SECONDS", 21600, 60, 86400),
            cooldown=integer_env("CREATE_COOLDOWN_SECONDS", 3, 0, 60),
            db_path=db_path,
            port=integer_env("PORT", 3000, 0, 65535),
        )
