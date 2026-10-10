from __future__ import annotations

import asyncio
import json
import logging
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ui import has_custom_emoji, without_custom_emoji


log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        self.code = code
        self.retry_after = retry_after
        self.description = description
        super().__init__(f"Telegram API {code}: {description}")


class TransportError(RuntimeError):
    """A failed network request may still have reached Telegram."""


class Telegram:
    # This project only manages messages. No wallet, gift-transfer or payment APIs.
    METHODS = frozenset({
        "getMe", "getWebhookInfo", "deleteWebhook", "getUpdates", "getBusinessConnection",
        "sendMessage", "editMessageText", "answerCallbackQuery", "setMyCommands",
        "deleteBusinessMessages",
    })
    RETRY_SAFE = frozenset(METHODS - {"sendMessage"})

    def __init__(self, token: str):
        self._token = token
        self._plain_emoji_scopes: set[str] = set()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "[BOT_TOKEN]")[:500]

    def _decode_response(self, data: bytes, http_code: int = 0):
        try:
            result = json.loads(data)
        except ValueError:
            raise TransportError("Telegram returned invalid JSON") from None
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise TransportError("Telegram returned an invalid API response")
        if result["ok"]:
            if "result" not in result or http_code >= 400:
                raise TransportError("Telegram returned an incomplete API response")
            return result["result"]
        code = result.get("error_code", http_code or 500)
        description = result.get("description", "Unknown API error")
        if type(code) is not int or not isinstance(description, str):
            raise TransportError("Telegram returned an invalid error response")
        parameters = result.get("parameters")
        retry_after = parameters.get("retry_after", 0) if isinstance(parameters, dict) else 0
        if type(retry_after) is not int or retry_after < 0:
            retry_after = 0
        raise TelegramError(code, self._redact(description), retry_after)

    def _call(self, method: str, params: dict, timeout: int):
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read()
        except HTTPError as exc:
            try:
                data = exc.read()
            except OSError:
                raise TransportError("Telegram error response was interrupted") from None
            return self._decode_response(data, exc.code)
        except (URLError, OSError, socket.timeout):
            # Never include a URL containing the token in logs or exceptions.
            raise TransportError("Telegram network request failed") from None
        return self._decode_response(data)

    async def call(self, method: str, **params):
        if method not in self.METHODS:
            raise ValueError("This API method is not supported by Gift Offer.")
        message_method = method in {"sendMessage", "editMessageText"}
        emoji_scope = params.get("business_connection_id") or "bot"
        if message_method and emoji_scope in self._plain_emoji_scopes:
            params = without_custom_emoji(params)
        timeout = int(params.get("timeout", 0)) + 15 if method == "getUpdates" else 20
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self._call, method, params, timeout)
            except TelegramError as exc:
                description = exc.description.lower().replace("_", " ")
                if (message_method and exc.code in {400, 403} and has_custom_emoji(params)
                        and any(marker in description for marker in
                                ("custom emoji", "tg-emoji", "button icon", "emoji id", "document invalid"))):
                    # A definitive rejection cannot have created a message. Retry
                    # once with ordinary emoji; never retry an ambiguous send.
                    result = await self.call(method, **without_custom_emoji(params))
                    self._plain_emoji_scopes.add(emoji_scope)
                    log.info("Custom emoji unavailable in this message context; using standard icons.")
                    return result
                if exc.code == 429 and attempt < 2:
                    # A definitive rate-limit rejection is safe to retry.
                    await asyncio.sleep(max(exc.retry_after, 1))
                    continue
                if method in self.RETRY_SAFE and exc.code >= 500 and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise
            except TransportError:
                if method not in self.RETRY_SAFE or attempt == 2:
                    raise
                await asyncio.sleep(1 + attempt)
        raise AssertionError("Unreachable")
