from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from html import escape

from .config import Config
from . import admin
from .domain import OfferError, parse_buy
from .store import Store
from .telegram import TelegramError, TransportError
from .views import card_payload, help_text, language_for, render_text, tr


log = logging.getLogger(__name__)
CALLBACK = re.compile(r"g:([a-f0-9]{16}):(accept|reject|cancel|sent|received|settled)")


def can_reply(connection: dict) -> bool:
    return bool(connection.get("is_enabled") and connection.get("rights", {}).get("can_reply"))


class Service:
    def __init__(self, config: Config, store: Store, api, bot_id: int, clock=time.time):
        self.config, self.store, self.api, self.bot_id = config, store, api, bot_id
        self.clock = clock
        self.lock = asyncio.Lock()
        # Refresh connections on first use after every restart, rather than trusting old rights.
        self.connections: dict[str, dict] = {}
        self.retry_at: dict[str, int] = {}

    async def connection(self, connection_id: str) -> dict:
        if connection_id not in self.connections:
            connection = await self.api.call("getBusinessConnection", business_connection_id=connection_id)
            self.connections[connection_id] = connection
            self.store.save_connection(connection)
            self.store.touch_user(connection["user"], int(self.clock()))
        return self.connections[connection_id]

    async def answer(self, query_id: str, text: str, alert: bool = False) -> None:
        try:
            await self.api.call("answerCallbackQuery", callback_query_id=query_id, text=text[:200], show_alert=alert)
        except (TelegramError, TransportError) as exc:
            log.warning("Callback answer failed: %s", exc)

    async def direct(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        extras = {"reply_markup": reply_markup} if reply_markup is not None else {}
        await self.api.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                            link_preview_options={"is_disabled": True}, **extras)

    async def notify_owner(self, connection: dict, text: str) -> None:
        try:
            await self.direct(connection["user_chat_id"], text)
        except (TelegramError, TransportError) as exc:
            log.warning("Owner notification unavailable: %s", exc)

    async def handle(self, update: dict) -> None:
        async with self.lock:
            now = int(self.clock())
            self.store.expire(now)
            if "business_connection" in update:
                connection = update["business_connection"]
                self.connections[connection["id"]] = connection
                self.store.save_connection(connection)
                self.store.touch_user(connection["user"], now)
            elif "callback_query" in update:
                await self.callback(update["callback_query"], now)
            elif "business_message" in update:
                await self.business_message(update["business_message"], now)
            elif "deleted_business_messages" in update:
                deleted = update["deleted_business_messages"]
                for message_id in deleted.get("message_ids", []):
                    self.store.hide_card(deleted["business_connection_id"], deleted["chat"]["id"], message_id, now)
            elif "message" in update:
                await self.private_message(update["message"], now)
            # Edited commands intentionally do not alter an existing contract.
            await self._flush(now)

    async def private_message(self, message: dict, now: int) -> None:
        if message.get("chat", {}).get("type") != "private" or message.get("from", {}).get("is_bot"):
            return
        user = message.get("from", {})
        user_id = user.get("id")
        chat_id = message["chat"]["id"]
        if not isinstance(user_id, int) or chat_id != user_id:
            return
        text = message.get("text", "").strip()
        if not text:
            return
        command = text.split()[0].split("@")[0].lower()
        language = language_for(user, self.config.default_language)
        self.store.touch_user(user, now)
        if command == "/start":
            self.store.record_visit(user, chat_id, message["message_id"], now)
        # No message body, token or promo is written to the audit log.
        if hmac.compare_digest(command.encode(), self.config.admin_promo.casefold().encode()):
            if message.get("forward_origin") or len(text.split()) != 1:
                return
            await self.activate_admin(user_id, chat_id, message["message_id"], now)
            return
        if command in {"/admin", "/logs", "/visits", "/transfers"}:
            if not self.store.is_admin(user_id):
                await self.direct(chat_id, "Этот раздел доступен только администратору.")
                return
            category = {"/admin": "home", "/logs": "all", "/visits": "visits", "/transfers": "transfers"}[command]
            await self.show_admin(chat_id, category)
            return
        if command in {"/start", "/help", ".buy"}:
            response = help_text(self.config.name, language)
        elif command in {"/terms", "/paysupport"}:
            response = tr(language, "terms", name=escape(self.config.name))
        else:
            return
        try:
            await self.direct(chat_id, response)
        except (TelegramError, TransportError) as exc:
            log.warning("Private reply failed: %s", exc)

    async def business_message(self, message: dict, now: int) -> None:
        text = message.get("text", "").strip()
        is_gift = message.get("unique_gift", {}).get("origin") == "transfer"
        if not is_gift and (not text or text.split()[0].lower() not in {".buy", ".help", ".status", ".cancel"}):
            return
        sender = message.get("from", {})
        if not isinstance(sender.get("id"), int) or sender.get("id", 0) <= 0 or sender.get("is_bot"):
            return
        if message.get("forward_origin") or (message.get("sender_business_bot") and not is_gift):
            return
        chat = message.get("chat", {})
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int) or chat["id"] <= 0:
            return
        if chat["id"] == sender["id"] and not is_gift:
            return
        connection_id = message.get("business_connection_id")
        if not connection_id:
            return
        try:
            connection = await self.connection(connection_id)
        except (TelegramError, TransportError) as exc:
            log.warning("Connection lookup failed: %s", exc)
            return
        owner_id = connection["user"]["id"]
        self.store.touch_user(connection["user"], now)
        self.store.touch_user(sender, now)
        self.store.touch_user(chat, now)
        if is_gift:
            if connection.get("is_enabled"):
                self.telegram_gift(message, connection, now)
            return
        if owner_id != sender["id"]:
            return
        language = self.config.default_language
        if not can_reply(connection):
            await self.notify_owner(connection, tr(language, "connection_disabled"))
            return
        command = text.split()[0].lower()
        if command == ".help":
            await self.notify_owner(connection, help_text(self.config.name, language))
            return
        if command in {".status", ".cancel"}:
            await self.owner_command(connection, message, command, now)
            return
        if self.store.from_source(connection_id, chat["id"], message["message_id"]):
            return
        try:
            spec = parse_buy(text, language)
        except OfferError as exc:
            await self.notify_owner(connection, tr(language, exc.code))
            return
        if now - self.store.latest_creation(sender["id"]) < self.config.cooldown:
            await self.notify_owner(connection, tr(language, "cooldown"))
            return
        offer = self.store.create(spec, connection, message, now, self.config.offer_ttl)
        try:
            result = await self.api.call("sendMessage", **card_payload(offer, self.config.name))
        except (TelegramError, TransportError) as exc:
            ambiguous = isinstance(exc, TransportError) or exc.code >= 500
            self.store.delivery_problem(offer["id"], ambiguous, now)
            log.warning("Offer %s could not be delivered: %s", offer["id"], exc)
            key = "check_chat" if ambiguous else "delivery_failed"
            await self.notify_owner(connection, tr(spec.language, key))
            return
        self.store.attach_card(offer["id"], result["message_id"])

    async def owner_command(self, connection: dict, message: dict, command: str, now: int) -> None:
        parts = message["text"].strip().split()
        language = self.config.default_language
        if len(parts) != 1:
            await self.notify_owner(connection, tr(language, "status_syntax"))
            return
        offer = self.store.latest_offer(connection["id"], message["chat"]["id"])
        if not offer:
            await self.notify_owner(connection, tr(language, "not_found"))
            return
        if command == ".cancel":
            try:
                offer = self.store.transition(offer["id"], "cancel", connection["user"]["id"], now)
            except OfferError as exc:
                await self.notify_owner(connection, tr(offer["language"], exc.code))
                return
        await self.notify_owner(connection, render_text(offer, self.config.name))

    async def callback(self, query: dict, now: int) -> None:
        user = query.get("from", {})
        language = language_for(user, self.config.default_language)
        query_id = query.get("id")
        if not query_id:
            return
        if query.get("data", "").startswith("a:"):
            await self.admin_callback(query)
            return
        match = CALLBACK.fullmatch(query.get("data", ""))
        if not match:
            await self.answer(query_id, tr(language, "unknown_action"), True)
            return
        offer_id, action = match.groups()
        offer = self.store.offer(offer_id)
        if not offer:
            await self.answer(query_id, tr(language, "not_found"), True)
            return
        language = offer["language"]
        if user.get("id") not in {offer["buyer_id"], offer["seller_id"]} or user.get("is_bot"):
            await self.answer(query_id, tr(language, "wrong_role"), True)
            return
        message = query.get("message", {})
        if (message.get("business_connection_id") != offer["connection_id"]
                or message.get("chat", {}).get("id") != offer["chat_id"]
                or not offer["display_enabled"] or not message.get("message_id")
                or message.get("date") == 0):
            await self.answer(query_id, tr(language, "wrong_card"), True)
            return
        try:
            connection = await self.connection(offer["connection_id"])
        except (TelegramError, TransportError):
            await self.answer(query_id, tr(language, "connection_disabled"), True)
            return
        if not can_reply(connection) or connection["user"]["id"] != offer["buyer_id"]:
            await self.answer(query_id, tr(language, "connection_disabled"), True)
            return
        if offer["card_message_id"] is None and offer["status"] == "delivery_unknown":
            # Recover only an actual message sent by this bot on this connection.
            if message.get("sender_business_bot", {}).get("id") != self.bot_id:
                await self.answer(query_id, tr(language, "wrong_card"), True)
                return
            offer = self.store.recover_card(offer_id, message["message_id"], now)
        if message["message_id"] != offer["card_message_id"]:
            await self.answer(query_id, tr(language, "wrong_card"), True)
            return
        self.store.touch_user(user, now)
        try:
            self.store.transition(offer_id, action, user["id"], now)
        except OfferError as exc:
            if exc.code == "expired":
                self.store.expire(now)
            await self.answer(query_id, tr(language, exc.code), True)
            return
        await self.answer(query_id, tr(language, "done"))

    async def tick(self) -> None:
        async with self.lock:
            now = int(self.clock())
            self.store.expire(now)
            await self._flush(now)

    async def _flush(self, now: int) -> None:
        for offer in self.store.dirty_offers():
            if self.retry_at.get(offer["id"], 0) > now:
                continue
            try:
                connection = await self.connection(offer["connection_id"])
                if not can_reply(connection):
                    continue
                await self.api.call("editMessageText", message_id=offer["card_message_id"],
                                    **card_payload(offer, self.config.name))
            except TelegramError as exc:
                if "message is not modified" in exc.description.lower():
                    self.store.mark_clean(offer["id"], offer["status"])
                elif "message to edit not found" in exc.description.lower():
                    self.store.hide_card(offer["connection_id"], offer["chat_id"], offer["card_message_id"], now)
                else:
                    log.warning("Card update for %s failed: %s", offer["id"], exc)
                    self.connections.pop(offer["connection_id"], None)
                    self.retry_at[offer["id"]] = now + 60
                continue
            except TransportError as exc:
                log.warning("Card update for %s failed: %s", offer["id"], exc)
                self.retry_at[offer["id"]] = now + 30
                continue
            self.store.mark_clean(offer["id"], offer["status"])
            self.retry_at.pop(offer["id"], None)

    def telegram_gift(self, message: dict, connection: dict, now: int) -> None:
        info = message["unique_gift"]
        gift = info.get("gift", {})
        slug = gift.get("name", "")
        if not isinstance(slug, str):
            return
        try:
            spec = parse_buy(f".buy https://t.me/nft/{slug} 1 stars")
        except OfferError:
            return
        owner_id, sender_id, peer_id = connection["user"]["id"], message["from"]["id"], message["chat"]["id"]
        # A private service message's sender must be one of the two participants.
        # Do not guess a recipient for ambiguous or forwarded messages.
        if sender_id == owner_id and not info.get("owned_gift_id"):
            recipient_id = peer_id
        elif sender_id == peer_id:
            recipient_id = owner_id
        else:
            return
        self.store.record_telegram_transfer(connection["id"], peer_id, message["message_id"],
                                            int(message.get("date") or now), sender_id, recipient_id,
                                            owner_id, spec.gift_url, spec.gift_name)

    async def activate_admin(self, user_id: int, chat_id: int, message_id: int, now: int) -> None:
        if self.store.is_admin(user_id):
            await self.show_admin(chat_id)
            return
        if self.store.get_setting("admin_user", ""):
            await self.direct(chat_id, "Админ-доступ уже закреплён за другим аккаунтом.")
            return
        connected = False
        for stored in self.store.known_connections(user_id):
            try:
                current = await self.connection(stored["id"])
            except (TelegramError, TransportError):
                continue
            if current.get("is_enabled") and current.get("user", {}).get("id") == user_id:
                connected = True
                break
        if not connected:
            await self.direct(chat_id, "Сначала подключи бота к своему аккаунту через настройки чат-ботов Telegram, затем повтори промокод.")
            return
        if self.store.claim_admin(user_id, now, f"admin:{chat_id}:{message_id}"):
            await self.show_admin(chat_id)
        else:
            await self.direct(chat_id, "Админ-доступ уже закреплён за другим аккаунтом.")

    async def show_admin(self, chat_id: int, category: str = "home", before: int = 0,
                         message_id: int | None = None) -> None:
        # Every entry point, including direct calls and callbacks, verifies the same binding.
        if not self.store.is_admin(chat_id):
            return
        if category == "home":
            text, markup = admin.home(self.store.audit_stats())
        else:
            text, markup = admin.log_page(category, self.store.audit_page(category, before, admin.PAGE_SIZE + 1))
        if message_id is None:
            await self.direct(chat_id, text, markup)
        else:
            try:
                await self.api.call("editMessageText", chat_id=chat_id, message_id=message_id,
                                    text=text, parse_mode="HTML", reply_markup=markup,
                                    link_preview_options={"is_disabled": True})
            except TelegramError as exc:
                if "message is not modified" not in exc.description.lower():
                    log.warning("Admin view could not be updated: %s", exc)
            except TransportError as exc:
                log.warning("Admin view could not be updated: %s", exc)

    async def admin_callback(self, query: dict) -> None:
        user, message = query.get("from", {}), query.get("message", {})
        user_id = user.get("id")
        if (not self.store.is_admin(user_id) or user.get("is_bot")
                or message.get("chat", {}).get("type") != "private"
                or message.get("chat", {}).get("id") != user_id
                or message.get("from", {}).get("id") != self.bot_id
                or message.get("business_connection_id") or message.get("forward_origin")
                or not message.get("message_id") or message.get("date") == 0):
            await self.answer(query["id"], "Админ-доступ недоступен для этой кнопки.", True)
            return
        data = query.get("data", "")
        if data == "a:home":
            category, before = "home", 0
        else:
            match = re.fullmatch(r"a:(all|visits|transfers|offers):([0-9]{1,19})", data)
            if not match:
                await self.answer(query["id"], "Неизвестная кнопка.", True)
                return
            category, cursor = match.groups()
            before = int(cursor)
            if before >= 2**63:
                await self.answer(query["id"], "Неизвестная страница.", True)
                return
        await self.answer(query["id"], "")
        await self.show_admin(user_id, category, before, message["message_id"])
