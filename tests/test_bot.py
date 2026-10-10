from __future__ import annotations

import copy
import asyncio
import http.client
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import connect_to_telegram, ensure_polling, health_server, poll, run
from gift_offer.config import Config, load_dotenv
from gift_offer.domain import OfferError, parse_buy
from gift_offer.service import Service
from gift_offer.store import Store
from gift_offer.telegram import Telegram, TelegramError, TransportError
from gift_offer.views import TEXT, card_payload, help_text, keyboard, render_text
from gift_offer import admin


BUYER, SELLER, BOT = 111, 222, 999
CONNECTION = {
    "id": "business-1", "user": {"id": BUYER, "first_name": "Buyer", "username": "real_buyer"},
    "user_chat_id": BUYER, "date": 1800000000,
    "rights": {"can_reply": True}, "is_enabled": True,
}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.failures: dict[str, list[Exception]] = {}
        self.connection = copy.deepcopy(CONNECTION)
        self.counter = 100

    async def call(self, method: str, **params):
        self.calls.append((method, copy.deepcopy(params)))
        if self.failures.get(method):
            raise self.failures[method].pop(0)
        if method == "getBusinessConnection":
            return copy.deepcopy(self.connection)
        if method == "sendMessage":
            self.counter += 1
            return {"message_id": self.counter}
        return True


class ParsingTests(unittest.TestCase):
    def test_reference_commands(self):
        cases = [
            (".buy https://t.me/nft/DurovsCap-1234 500 stars", "500", "stars", "ru"),
            (".buy https://t.me/nft/DurovsCap-1234 10 gram en", "10", "gram", "en"),
            (".buy https://t.me/nft/DurovsCap-1234 250 stars zh", "250", "stars", "zh"),
            (".buy https://t.me/nft/CupidCharm-15484 0.000000001 gram ar", "0.000000001", "gram", "ar"),
        ]
        for command, amount, currency, language in cases:
            with self.subTest(command=command):
                spec = parse_buy(command)
                self.assertEqual((spec.amount, spec.currency, spec.language), (amount, currency, language))

    def test_decimal_values_normalize_without_float_rounding(self):
        self.assertEqual(parse_buy(".buy https://t.me/nft/Gift-1 10.5000 gram").amount, "10.5")
        self.assertEqual(parse_buy(".buy https://t.me/nft/Gift-1 8900.0 stars").amount, "8900")

    def test_rejects_unsafe_urls(self):
        for url in ["http://t.me/nft/Gift-1", "https://t.me.evil.test/nft/Gift-1", "https://t.me@evil.test/nft/Gift-1",
                    "https://evil.test/nft/Gift-1", "https://t.me:443/nft/Gift-1", "file:///nft/Gift-1",
                    "https://t.me/nft/Gift-1?to=somebody", "https://t.me/nft/Gift-1#payload", "https://t.me/nft/Gift-0"]:
            with self.subTest(url=url), self.assertRaises(OfferError):
                parse_buy(f".buy {url} 500 stars")

    def test_rejects_invalid_prices_and_options(self):
        for tail in ["0 stars", "-1 stars", "NaN gram", "Infinity gram", "1e3 gram", "1.1 stars",
                     "1.0000000001 gram", "100000001 stars", "1000001 gram", "5 rub", "5 stars xx"]:
            with self.subTest(tail=tail), self.assertRaises(OfferError):
                parse_buy(f".buy https://t.me/nft/Gift-1 {tail}")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1800000000
        self.store = Store(":memory:")
        self.api = FakeAPI()
        self.config = Config(token="123456:" + "X" * 30, cooldown=0)
        self.service = Service(self.config, self.store, self.api, BOT, clock=lambda: self.now)

    async def asyncTearDown(self):
        self.store.close()

    def message(self, text: str = ".buy https://t.me/nft/CupidCharm-15484 8900 stars ru", message_id: int = 10) -> dict:
        return {"business_message": {
            "business_connection_id": CONNECTION["id"], "message_id": message_id, "date": self.now,
            "from": {"id": BUYER, "first_name": "Buyer"},
            "chat": {"id": SELLER, "type": "private", "first_name": "Seller", "username": "seller_user"}, "text": text,
        }}

    async def create_offer(self, command: str | None = None, message_id: int = 10) -> dict:
        update = self.message(message_id=message_id)
        if command:
            update["business_message"]["text"] = command
        await self.service.handle(update)
        return self.store.from_source(CONNECTION["id"], SELLER, message_id)

    def callback(self, offer: dict, action: str, actor_id: int) -> dict:
        return {"callback_query": {
            "id": "query-" + action, "from": {"id": actor_id, "language_code": "ru"},
            "data": f'g:{offer["id"]}:{action}',
            "message": {"message_id": offer["card_message_id"] or 501, "date": self.now,
                        "business_connection_id": offer["connection_id"], "chat": {"id": offer["chat_id"], "type": "private"},
                        "sender_business_bot": {"id": BOT}},
        }}

    async def act(self, offer: dict, action: str, actor: int) -> dict:
        await self.service.handle(self.callback(offer, action, actor))
        return self.store.offer(offer["id"])

    def private(self, text: str, actor: int = BUYER, message_id: int = 50) -> dict:
        return {"message": {
            "text": text, "message_id": message_id, "date": self.now,
            "from": {"id": actor, "first_name": "Buyer" if actor == BUYER else "Visitor", "username": "real_buyer" if actor == BUYER else "visitor_user"},
            "chat": {"id": actor, "type": "private"},
        }}

    async def activate(self):
        await self.service.handle({"business_connection": copy.deepcopy(CONNECTION)})
        await self.service.handle(self.private("/ClezzyKryt"))

    async def test_full_flow_payment_before_gift(self):
        offer = await self.create_offer()
        self.assertEqual(offer["status"], "pending")
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "accepted")
        offer = await self.act(offer, "settled", SELLER)
        self.assertEqual(offer["status"], "payment_reported")
        offer = await self.act(offer, "sent", SELLER)
        self.assertEqual(offer["status"], "gift_reported")
        offer = await self.act(offer, "received", BUYER)
        self.assertEqual(offer["status"], "completed")
        self.assertIn("по их заявлениям", render_text(offer, self.config.name))
        actions = [r[0] for r in self.store.db.execute("SELECT action FROM events ORDER BY id")]
        self.assertEqual(actions, ["create", "accept", "settled", "sent", "received"])
        self.assertFalse(any("transfer" in method.lower() or "invoice" in method.lower() for method, _ in self.api.calls))

    async def test_full_flow_payment_after_gift(self):
        offer = await self.create_offer()
        for action, actor, status in [("accept", SELLER, "accepted"), ("sent", SELLER, "gift_reported"),
                                      ("received", BUYER, "gift_received"), ("settled", SELLER, "completed")]:
            offer = await self.act(offer, action, actor)
            self.assertEqual(offer["status"], status)

    async def test_payment_while_receipt_is_pending(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", SELLER)
        offer = await self.act(offer, "sent", SELLER)
        offer = await self.act(offer, "settled", SELLER)
        self.assertEqual(offer["status"], "gift_reported")
        self.assertEqual(offer["payment_claimed"], 1)
        self.assertIn("также сообщил", render_text(offer, self.config.name))
        offer = await self.act(offer, "received", BUYER)
        self.assertEqual(offer["status"], "completed")

    async def test_buyer_cannot_accept_own_offer_or_confirm_seller_payment(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", BUYER)
        self.assertEqual(offer["status"], "pending")
        offer = await self.act(offer, "accept", SELLER)
        offer = await self.act(offer, "settled", BUYER)
        self.assertEqual(offer["status"], "accepted")
        self.assertEqual(offer["payment_claimed"], 0)

    async def test_stranger_cannot_act(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", 333)
        self.assertEqual(offer["status"], "pending")
        self.assertTrue(self.api.calls[-1][1]["show_alert"])

    async def test_callback_is_bound_to_chat_connection_and_card(self):
        offer = await self.create_offer()
        for key, value in [("chat", {"id": 777}), ("business_connection_id", "another"), ("message_id", 888), ("date", 0)]:
            query = self.callback(offer, "accept", SELLER)
            query["callback_query"]["message"][key] = value
            await self.service.handle(query)
            self.assertEqual(self.store.offer(offer["id"])["status"], "pending")

    async def test_repeated_accept_and_stale_reject_do_not_change_offer(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", SELLER)
        for action in ("accept", "reject"):
            offer = await self.act(offer, action, SELLER)
            self.assertEqual(offer["status"], "accepted")
        count = self.store.db.execute("SELECT COUNT(*) FROM events WHERE action='accept'").fetchone()[0]
        self.assertEqual(count, 1)

    async def test_duplicate_source_message_never_sends_second_offer(self):
        await self.create_offer()
        await self.create_offer()
        cards = [params for method, params in self.api.calls if method == "sendMessage" and "business_connection_id" in params]
        self.assertEqual(len(cards), 1)

    async def test_incoming_or_forwarded_command_is_ignored(self):
        for changed in [{"from": {"id": SELLER}}, {"forward_origin": {"type": "user"}},
                        {"sender_business_bot": {"id": BOT}}, {"chat": {"id": -55, "type": "supergroup"}}]:
            update = self.message()
            update["business_message"].update(changed)
            await self.service.handle(update)
            self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 0)

    async def test_disabled_connection_blocks_new_offers_and_callbacks(self):
        offer = await self.create_offer()
        disabled = copy.deepcopy(CONNECTION)
        disabled["is_enabled"] = False
        await self.service.handle({"business_connection": disabled})
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "pending")
        await self.service.handle(self.message(message_id=20))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 1)

    async def test_expiry_at_exact_deadline(self):
        offer = await self.create_offer()
        self.now = offer["expires_at"]
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "expired")
        self.assertFalse(any("callback_data" in b for row in keyboard(offer)["inline_keyboard"] for b in row))

    async def test_acceptance_before_deadline_does_not_expire_mid_trade(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", SELLER)
        self.now = offer["expires_at"] + 100
        await self.service.tick()
        self.assertEqual(self.store.offer(offer["id"])["status"], "accepted")

    async def test_decline_and_cancel_are_terminal(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "reject", SELLER)
        self.assertEqual(offer["status"], "rejected")
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "rejected")
        offer2 = await self.create_offer(message_id=11)
        offer2 = await self.act(offer2, "cancel", BUYER)
        self.assertEqual(offer2["status"], "cancelled")

    async def test_cancellation_blocked_after_payment_or_transfer_report(self):
        for action in ("settled", "sent"):
            offer = await self.create_offer(message_id=100 if action == "settled" else 101)
            offer = await self.act(offer, "accept", SELLER)
            offer = await self.act(offer, action, SELLER)
            previous = offer["status"]
            offer = await self.act(offer, "cancel", BUYER)
            self.assertEqual(offer["status"], previous)

    async def test_ambiguous_send_is_not_repeated_and_can_recover_from_real_callback(self):
        self.api.failures["sendMessage"] = [TransportError("connection interrupted")]
        offer = await self.create_offer()
        self.assertEqual(offer["status"], "delivery_unknown")
        await self.create_offer()
        sends = [p for m, p in self.api.calls if m == "sendMessage" and "business_connection_id" in p]
        self.assertEqual(len(sends), 1)
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "accepted")
        self.assertEqual(offer["card_message_id"], 501)

    async def test_ambiguous_send_cannot_recover_from_another_bots_message(self):
        self.api.failures["sendMessage"] = [TransportError("connection interrupted")]
        offer = await self.create_offer()
        query = self.callback(offer, "accept", SELLER)
        query["callback_query"]["message"]["sender_business_bot"]["id"] = 555
        await self.service.handle(query)
        self.assertEqual(self.store.offer(offer["id"])["status"], "delivery_unknown")

    async def test_known_send_rejection_is_not_a_successful_offer(self):
        self.api.failures["sendMessage"] = [TelegramError(403, "Forbidden")]
        offer = await self.create_offer()
        self.assertEqual(offer["status"], "delivery_failed")
        self.assertIsNone(offer["card_message_id"])

    async def test_failed_card_edit_preserves_state_and_retries(self):
        offer = await self.create_offer()
        self.api.failures["editMessageText"] = [TransportError("offline")]
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "accepted")
        self.assertEqual(offer["dirty"], 1)
        self.now += 31
        await self.service.tick()
        self.assertEqual(self.store.offer(offer["id"])["dirty"], 0)

    async def test_deleted_card_cannot_be_used(self):
        offer = await self.create_offer()
        await self.service.handle({"deleted_business_messages": {
            "business_connection_id": offer["connection_id"], "chat": {"id": SELLER}, "message_ids": [offer["card_message_id"]],
        }})
        offer = await self.act(offer, "accept", SELLER)
        self.assertEqual(offer["status"], "cancelled")
        self.assertEqual(offer["display_enabled"], 0)

    async def test_status_is_private_to_owner_and_confined_to_original_chat(self):
        offer = await self.create_offer()
        await self.service.handle(self.message('.status', message_id=11))
        self.assertEqual(self.api.calls[-1][1]["chat_id"], BUYER)
        self.assertIn(offer["gift_name"], self.api.calls[-1][1]["text"])
        self.assertNotIn(offer["id"], self.api.calls[-1][1]["text"])

    async def test_all_language_and_state_cards_fit_telegram_limits(self):
        offer = await self.create_offer()
        keys = set(TEXT["ru"])
        for language in TEXT:
            self.assertEqual(set(TEXT[language]), keys)
            offer["language"] = language
            for status in ("pending", "accepted", "payment_reported", "gift_reported", "gift_received", "completed", "rejected", "expired"):
                offer["status"] = status
                payload = card_payload(offer, self.config.name)
                self.assertLessEqual(len(payload["text"]), 4096)
                self.assertTrue(payload["link_preview_options"]["show_above_text"])
                for row in payload["reply_markup"]["inline_keyboard"]:
                    for button in row:
                        self.assertLessEqual(len(button.get("callback_data", "").encode()), 64)
                self.assertIn(TEXT[language]["disclosure"], payload["text"])
            self.assertLessEqual(len(help_text(self.config.name, language)), 4096)

    async def test_html_names_cannot_inject_recipient_links(self):
        self.api.connection["user"]["first_name"] = '<a href="https://evil.test">Fake</a>'
        offer = await self.create_offer()
        text = render_text(offer, "<unsafe>")
        self.assertIn("&lt;unsafe&gt;", text)
        self.assertIn("&lt;a href=", text)
        self.assertIn(f'tg://user?id={BUYER}', text)
        self.assertNotIn('<a href="https://evil.test">', text)

    async def test_new_connected_owner_can_create_without_manual_configuration(self):
        self.api.connection["user"]["id"] = 333
        update = self.message()
        update["business_message"]["from"]["id"] = 333
        await self.service.handle(update)
        offer = self.store.from_source(CONNECTION["id"], SELLER, 10)
        self.assertEqual(offer["buyer_id"], 333)
        self.assertEqual(offer["status"], "pending")

    async def test_status_and_cancel_choose_latest_offer_without_ids(self):
        old = await self.create_offer(message_id=10)
        newest = await self.create_offer(".buy https://t.me/nft/DurovsCap-1234 500 stars", message_id=11)
        await self.service.handle(self.message(".status", message_id=12))
        self.assertIn(newest["gift_url"], self.api.calls[-1][1]["text"])
        await self.service.handle(self.message(".cancel", message_id=13))
        self.assertEqual(self.store.offer(newest["id"])["status"], "cancelled")
        self.assertEqual(self.store.offer(old["id"])["status"], "pending")

    async def test_visits_count_start_messages_and_deduplicate_redelivery(self):
        first = self.private("/start", actor=333, message_id=1)
        await self.service.handle(first)
        await self.service.handle(first)
        await self.service.handle(self.private("/start", actor=333, message_id=2))
        await self.service.handle(self.private("/start", actor=444, message_id=1))
        await self.service.handle(self.private("/help", actor=555, message_id=1))
        stats = self.store.audit_stats()
        self.assertEqual(stats["visit"], 3)
        self.assertEqual(stats["unique_visitors"], 2)

    async def test_promo_activates_admin_for_connected_account_without_id_setup(self):
        await self.activate()
        self.assertTrue(self.store.is_admin(BUYER))
        self.assertIn("Админ-панель", self.api.calls[-1][1]["text"])
        audit = self.store.audit_page()
        self.assertEqual(audit[0]["kind"], "admin_activation")
        self.assertNotIn("ClezzyKryt", str(audit))

    async def test_unconnected_user_cannot_claim_admin(self):
        await self.service.handle(self.private("/ClezzyKryt", actor=333))
        self.assertFalse(self.store.is_admin(333))
        self.assertEqual(self.store.get_setting("admin_user", ""), "")
        self.assertIn("подключи бота", self.api.calls[-1][1]["text"])

    async def test_admin_binding_cannot_be_taken_over_with_same_promo(self):
        await self.activate()
        connection = copy.deepcopy(CONNECTION)
        connection["id"] = "another-connection"
        connection["user"]["id"] = 333
        await self.service.handle({"business_connection": connection})
        await self.service.handle(self.private("/ClezzyKryt", actor=333))
        self.assertTrue(self.store.is_admin(BUYER))
        self.assertFalse(self.store.is_admin(333))
        self.assertIn("другим аккаунтом", self.api.calls[-1][1]["text"])

    async def test_repeated_promo_does_not_duplicate_activation(self):
        await self.activate()
        await self.service.handle(self.private("/clezzykryt", message_id=51))
        self.assertEqual(self.store.audit_stats()["admin_activation"], 1)

    async def test_forwarded_promo_never_grants_admin(self):
        await self.service.handle({"business_connection": copy.deepcopy(CONNECTION)})
        update = self.private("/ClezzyKryt")
        update["message"]["forward_origin"] = {"type": "user"}
        await self.service.handle(update)
        self.assertFalse(self.store.is_admin(BUYER))

    async def test_non_admin_cannot_read_log_commands(self):
        await self.activate()
        for command in ("/admin", "/logs", "/visits", "/transfers"):
            await self.service.handle(self.private(command, actor=333))
            self.assertEqual(self.api.calls[-1][1]["text"], "Этот раздел доступен только администратору.")

    async def test_transfer_statement_logs_real_sender_recipient_and_nft(self):
        offer = await self.create_offer()
        offer = await self.act(offer, "accept", SELLER)
        await self.act(offer, "sent", SELLER)
        rows = self.store.audit_page("transfers")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["actor_id"], rows[0]["recipient_id"]), (SELLER, BUYER))
        self.assertEqual(rows[0]["gift_url"], offer["gift_url"])
        self.assertEqual(rows[0]["evidence"], "user_statement")
        self.assertEqual(self.store.audit_stats().get("telegram_transfer", 0), 0)
        await self.activate()
        await self.service.handle(self.private("/transfers", message_id=51))
        text = self.api.calls[-1][1]["text"]
        self.assertIn("@seller_user", text)
        self.assertIn("@real_buyer", text)
        self.assertIn("заявление участника", text)
        self.assertNotIn(offer["id"], text)

    def gift_update(self, sender: int, message_id: int = 60) -> dict:
        update = self.message("", message_id=message_id)
        update["business_message"]["from"] = {"id": sender, "first_name": "Sender"}
        update["business_message"]["unique_gift"] = {
            "origin": "transfer", "gift": {"name": "CupidCharm-15484", "base_name": "Cupid Charm", "number": 15484},
        }
        return update

    async def test_telegram_transfer_incoming_is_separate_and_deduplicated(self):
        update = self.gift_update(SELLER)
        update["business_message"]["unique_gift"]["owned_gift_id"] = "gift-owned-by-business-account"
        await self.service.handle(update)
        await self.service.handle(update)
        rows = self.store.audit_page("transfers")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["actor_id"], rows[0]["recipient_id"]), (SELLER, BUYER))
        self.assertEqual(rows[0]["evidence"], "telegram_service")

    async def test_telegram_transfer_outgoing_logs_actual_peer(self):
        update = self.gift_update(BUYER)
        update["business_message"]["sender_business_bot"] = {"id": BOT}
        await self.service.handle(update)
        rows = self.store.audit_page("transfers")
        self.assertEqual((rows[0]["actor_id"], rows[0]["recipient_id"]), (BUYER, SELLER))

    async def test_upgrade_forward_or_unknown_sender_is_not_a_transfer_record(self):
        for change in ("upgrade", "forward", "unknown_sender"):
            update = self.gift_update(SELLER)
            if change == "upgrade":
                update["business_message"]["unique_gift"]["origin"] = "upgrade"
            elif change == "forward":
                update["business_message"]["forward_origin"] = {"type": "user"}
            else:
                update["business_message"]["from"]["id"] = 888
            await self.service.handle(update)
        self.assertEqual(self.store.audit_page("transfers"), [])

    async def test_log_filters_and_cursor_paging_do_not_skip_or_duplicate(self):
        for message_id in range(1, 13):
            await self.service.handle(self.private("/start", actor=333, message_id=message_id))
        await self.activate()
        latest = self.store.audit_page("visits", limit=5)
        older = self.store.audit_page("visits", before=latest[-1]["id"], limit=5)
        self.assertTrue(set(r["id"] for r in latest).isdisjoint(set(r["id"] for r in older)))
        self.assertTrue(all(r["kind"] == "visit" for r in latest + older))
        self.assertEqual(len(self.store.audit_page("transfers")), 0)

    async def test_admin_callback_cannot_be_used_by_stranger_or_in_business_chat(self):
        await self.activate()
        for changed in ("stranger", "business", "forward", "different_chat"):
            query = {"callback_query": {
                "id": "admin-query", "data": "a:visits:0", "from": {"id": BUYER},
                "message": {"message_id": 600, "date": self.now, "from": {"id": BOT}, "chat": {"id": BUYER, "type": "private"}},
            }}
            if changed == "stranger":
                query["callback_query"]["from"]["id"] = 333
            elif changed == "business":
                query["callback_query"]["message"]["business_connection_id"] = CONNECTION["id"]
            elif changed == "forward":
                query["callback_query"]["message"]["forward_origin"] = {"type": "user"}
            else:
                query["callback_query"]["message"]["chat"]["id"] = 333
            before = len([m for m, _ in self.api.calls if m == "editMessageText"])
            await self.service.handle(query)
            after = len([m for m, _ in self.api.calls if m == "editMessageText"])
            self.assertEqual(before, after)
            self.assertTrue(self.api.calls[-1][1]["show_alert"])

    async def test_authorized_admin_callback_opens_logs(self):
        await self.activate()
        await self.service.handle(self.private("/start", actor=333, message_id=1))
        await self.service.handle({"callback_query": {
            "id": "admin-query", "data": "a:visits:0", "from": {"id": BUYER},
            "message": {"message_id": 600, "date": self.now, "from": {"id": BOT}, "chat": {"id": BUYER, "type": "private"}},
        }})
        method, params = self.api.calls[-1]
        self.assertEqual(method, "editMessageText")
        self.assertIn("@visitor_user", params["text"])
        self.assertEqual(params["chat_id"], BUYER)

    async def test_admin_callback_overflow_does_not_query_sqlite(self):
        await self.activate()
        await self.service.handle({"callback_query": {
            "id": "admin-query", "data": "a:all:9999999999999999999", "from": {"id": BUYER},
            "message": {"message_id": 600, "date": self.now, "from": {"id": BOT}, "chat": {"id": BUYER, "type": "private"}},
        }})
        self.assertIn("Неизвестная страница", self.api.calls[-1][1]["text"])

    async def test_old_numeric_id_commands_are_not_needed_or_exposed(self):
        offer = await self.create_offer()
        self.assertNotIn(offer["id"], render_text(offer, self.config.name))
        for language in TEXT:
            text = help_text(self.config.name, language)
            self.assertNotIn("OWNER_IDS", text)
            self.assertNotIn(".status ID", text)
            self.assertNotIn(".cancel ID", text)


class PersistenceTests(unittest.TestCase):
    def test_restart_retains_offer_events_and_poll_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "offers.sqlite3")
            store = Store(path)
            spec = parse_buy(".buy https://t.me/nft/Gift-1 500 stars")
            offer = store.create(spec, CONNECTION, {"chat": {"id": SELLER}, "message_id": 10}, 1000, 21600)
            store.attach_card(offer["id"], 99)
            store.transition(offer["id"], "accept", SELLER, 1001)
            store.set_setting("offset", "77")
            store.claim_admin(BUYER, 1002, "admin:first")
            store.close()
            store = Store(path)
            self.assertEqual(store.offer(offer["id"])["status"], "accepted")
            self.assertEqual(store.get_setting("offset"), "77")
            self.assertTrue(store.is_admin(BUYER))
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
            store.close()

    def test_interrupted_send_is_never_silently_resubmitted_after_restart(self):
        store = Store(":memory:")
        offer = store.create(parse_buy(".buy https://t.me/nft/Gift-1 500 stars"), CONNECTION,
                             {"chat": {"id": SELLER}, "message_id": 10}, 1000, 21600)
        store.recover_interrupted_sends(1001)
        self.assertEqual(store.offer(offer["id"])["status"], "delivery_unknown")
        store.close()

    def test_upgrade_imports_old_offer_events_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "offers.sqlite3")
            store = Store(path)
            offer = store.create(parse_buy(".buy https://t.me/nft/Gift-1 500 stars"), CONNECTION,
                                 {"chat": {"id": SELLER}, "message_id": 10}, 1000, 21600)
            store.transition(offer["id"], "accept", SELLER, 1001)
            store.transition(offer["id"], "sent", SELLER, 1002)
            store.attach_card(offer["id"], 900)
            # Simulate the 1.0 schema with no audit tables or backfill marker.
            store.db.executescript("DROP TABLE audit_log; DROP TABLE users; DELETE FROM settings WHERE key='audit_backfill';")
            store.close()
            store = Store(path)
            self.assertEqual(len(store.audit_page("transfers")), 1)
            self.assertEqual(store.offer(offer["id"])["status"], "gift_reported")
            self.assertEqual(store.offer(offer["id"])["dirty"], 1)
            count = len(store.audit_page())
            store.close()
            store = Store(path)
            self.assertEqual(len(store.audit_page()), count)
            store.close()


class WebhookAPI:
    def __init__(self, url="https://example.test/private-hook"):
        self.url = url
        self.pending = [{"update_id": 7, "message": {"text": "/start"}}]
        self.calls = []
        self.delete_error = None
        self.delete_result = True
        self.reactivate = False

    async def call(self, method, **params):
        self.calls.append((method, params))
        if method == "getWebhookInfo":
            return {"url": self.url}
        if method == "deleteWebhook":
            if self.delete_error:
                raise self.delete_error
            if self.delete_result:
                self.url = self.url if self.reactivate else ""
                if params.get("drop_pending_updates"):
                    self.pending.clear()
            return self.delete_result
        if method == "getUpdates":
            if self.url:
                raise TelegramError(409, "Cannot use getUpdates while webhook is active")
            return copy.deepcopy(self.pending)
        raise AssertionError(method)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_to_polling_preserves_pending_updates_and_hides_webhook_url(self):
        api = WebhookAPI()
        original_url, pending = api.url, copy.deepcopy(api.pending)
        with self.assertRaises(TelegramError):
            await api.call("getUpdates")
        with self.assertLogs("gift_offer", level="INFO") as logs:
            await ensure_polling(api)
        self.assertEqual(await api.call("getUpdates"), pending)
        self.assertNotIn(original_url, "\n".join(logs.output))

    async def test_already_polling_keeps_updates_without_deleting_webhook(self):
        api = WebhookAPI(url="")
        await ensure_polling(api)
        self.assertEqual([method for method, _ in api.calls], ["getWebhookInfo"])
        self.assertEqual(await api.call("getUpdates"), api.pending)

    async def test_api_rejection_keeps_webhook_and_pending_updates(self):
        api = WebhookAPI()
        original_url, pending = api.url, copy.deepcopy(api.pending)
        api.delete_error = TelegramError(401, "Unauthorized")
        with self.assertRaises(TelegramError):
            await ensure_polling(api)
        self.assertEqual(api.url, original_url)
        self.assertEqual(api.pending, pending)

    async def test_reactivated_webhook_stops_instead_of_repeatedly_deleting(self):
        api = WebhookAPI()
        api.reactivate = True
        with self.assertRaisesRegex(RuntimeError, "Остановите другой сервис"):
            await ensure_polling(api)
        self.assertEqual(sum(method == "deleteWebhook" for method, _ in api.calls), 1)
        with self.assertRaises(TelegramError):
            await api.call("getUpdates")

    async def test_unconfirmed_deletion_stops_startup(self):
        api = WebhookAPI()
        api.delete_result = False
        with self.assertRaisesRegex(RuntimeError, "не подтвердил отключение"):
            await ensure_polling(api)
        self.assertTrue(api.url)
        self.assertTrue(api.pending)


class HealthTests(unittest.TestCase):
    def test_liveness_during_startup_and_readiness_after_polling(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        health = {"ready": False, "last_poll": time.time()}
        server = health_server(port, health)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address[0], "0.0.0.0")

        def request(path, method="GET"):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request(method, path)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        status, body = request("/health")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ready"])
        self.assertEqual(request("/readyz")[0], 503)
        self.assertEqual(request("/health", "HEAD"), (200, b""))
        health["ready"] = True
        self.assertEqual(request("/readyz")[0], 200)
        health["last_poll"] = 0
        self.assertEqual(request("/health")[0], 200)
        self.assertEqual(request("/healthz")[0], 503)
        self.assertEqual(request("/missing")[0], 404)

    def test_optional_http_server_can_be_disabled(self):
        self.assertIsNone(health_server(0, {"ready": False, "last_poll": 0}))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_hosting_starts_health_before_network_and_survives_temporary_failure(self):
        attempts = 0
        api = SimpleNamespace(call=AsyncMock())

        async def api_call(method, **params):
            nonlocal attempts
            self.assertTrue(http_server.called)
            if method == "getMe":
                attempts += 1
                if attempts == 1:
                    raise TransportError("temporary connection failure")
                return {"id": BOT, "username": "test_bot", "can_connect_to_business": True}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "setMyCommands":
                raise TelegramError(400, "Menu unavailable")
            raise AssertionError(method)

        async def fast_wait(stop, seconds):
            await asyncio.sleep(0)

        async def first_poll(service, stop, health):
            self.assertTrue(health["ready"])
            self.assertEqual(service.config.admin_promo, "/ClezzyKryt")
            stop.set()

        api.call.side_effect = api_call
        config = Config(token="123456:" + "X" * 30, db_path=":memory:", port=0)
        with patch("bot.health_server") as http_server, patch("bot.Telegram", return_value=api), \
                patch("bot.wait_or_stop", side_effect=fast_wait), \
                patch("bot.poll", side_effect=first_poll) as poller:
            await asyncio.wait_for(run(config), timeout=3)
            self.assertEqual(attempts, 2)
            poller.assert_awaited_once()
            http_server.return_value.shutdown.assert_called_once()
            http_server.return_value.server_close.assert_called_once()

    async def test_invalid_token_is_not_retried_forever(self):
        api = SimpleNamespace(call=AsyncMock(side_effect=TelegramError(401, "Unauthorized")))
        with patch("bot.wait_or_stop") as wait:
            with self.assertRaises(TelegramError):
                await connect_to_telegram(api, asyncio.Event())
            wait.assert_not_called()

    async def test_temporary_polling_conflict_preserves_offset_and_recovers(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.set_setting("offset", "66")
        stop = asyncio.Event()
        calls = []

        async def api_call(method, **params):
            calls.append(params)
            if len(calls) == 1:
                raise TelegramError(409, "terminated by other getUpdates request")
            return [{"update_id": 77}]

        async def handle(update):
            stop.set()

        async def fast_wait(event, seconds):
            self.assertEqual(store.get_setting("offset"), "66")

        service = SimpleNamespace(store=store, api=SimpleNamespace(call=api_call), handle=handle)
        with patch("bot.wait_or_stop", side_effect=fast_wait):
            await poll(service, stop, {"ready": True, "last_poll": 0})
        self.assertEqual([p["offset"] for p in calls], [66, 66])
        self.assertEqual(store.get_setting("offset"), "78")

    async def test_persistent_polling_conflict_stops_with_clear_error(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        api = SimpleNamespace(call=AsyncMock(side_effect=TelegramError(409, "other getUpdates request")))
        service = SimpleNamespace(store=store, api=api)
        with patch("bot.wait_or_stop", new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "Остановите вторую копию"):
                await poll(service, asyncio.Event(), {"ready": True, "last_poll": 0})
        self.assertEqual(api.call.await_count, 7)
        self.assertEqual(store.get_setting("offset"), "0")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_deletion_is_available_and_preserves_queue_parameter(self):
        api = Telegram("123456:" + "X" * 30)
        with patch.object(api, "_call", return_value=True) as request:
            self.assertTrue(await api.call("deleteWebhook", drop_pending_updates=False))
        request.assert_called_once_with("deleteWebhook", {"drop_pending_updates": False}, 20)

    async def test_send_is_not_retried_after_ambiguous_network_failure(self):
        api = Telegram("123456:" + "X" * 30)
        with patch.object(api, "_call", side_effect=TransportError("uncertain")) as request:
            with self.assertRaises(TransportError):
                await api.call("sendMessage", chat_id=BUYER, text="Offer")
            self.assertEqual(request.call_count, 1)

    async def test_token_redaction_and_payment_methods_unavailable(self):
        token = "123456:" + "X" * 30
        api = Telegram(token)
        self.assertNotIn(token, api._redact(f"https://api.telegram.org/bot{token}/getMe"))
        with self.assertRaises(ValueError):
            await api.call("transferGift", new_owner_chat_id=333)


class AdminViewTests(unittest.TestCase):
    def audit_row(self, event_id: int) -> dict:
        return {
            "id": event_id, "at": 1800000000, "kind": "transfer_claim", "evidence": "user_statement",
            "actor_id": SELLER, "actor_first": '<a href="https://evil.test">Fake</a>', "actor_last": "",
            "actor_username": "fake<script>", "recipient_id": BUYER,
            "recipient_first": "Buyer", "recipient_last": "", "recipient_username": "real_buyer",
            "gift_url": "https://t.me/nft/CupidCharm-15484", "gift_name": "Cupid Charm #15484",
            "amount": "8900", "currency": "stars",
        }

    def test_profile_names_cannot_inject_links_into_admin_log(self):
        text, markup = admin.log_page("transfers", [self.audit_row(1)])
        self.assertIn("&lt;a href=", text)
        self.assertNotIn('<a href="https://evil.test">', text)
        self.assertNotIn("fake<script>", text)
        self.assertIn(f'tg://user?id={SELLER}', text)
        self.assertIn("заявление участника", text)

    def test_long_escaped_entries_paginate_without_exceeding_text_limit(self):
        rows = [self.audit_row(event_id) for event_id in range(10, 4, -1)]
        for row in rows:
            row.update(actor_first="&" * 128, recipient_first="&" * 128, gift_name="&" * 200)
        text, markup = admin.log_page("transfers", rows)
        self.assertLessEqual(len(text), 4096)
        older = [b for line in markup["inline_keyboard"] for b in line if b["text"] == "Старее"]
        self.assertEqual(len(older), 1)
        self.assertTrue(older[0]["callback_data"].startswith("a:transfers:"))


class ConfigurationTests(unittest.TestCase):
    def test_bothost_token_aliases_and_primary_token_precedence(self):
        token = "123456:" + "X" * 30
        for key in ("API_TOKEN", "TELEGRAM_BOT_TOKEN"):
            with self.subTest(key=key), patch.dict(os.environ, {key: token}, clear=True):
                self.assertEqual(Config.from_env().token, token)
        with patch.dict(os.environ, {"BOT_TOKEN": token, "API_TOKEN": "654321:" + "Y" * 30}, clear=True):
            self.assertEqual(Config.from_env().token, token)

    def test_invalid_primary_token_does_not_silently_use_another_bot(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "invalid", "API_TOKEN": "123456:" + "X" * 30}, clear=True):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_platform_data_dir_and_explicit_database_path(self):
        env = {"BOT_TOKEN": "123456:" + "X" * 30, "DATA_DIR": "/app/data"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.from_env().db_path, "/app/data/offers.sqlite3")
            os.environ["DB_PATH"] = "/app/data/existing.sqlite3"
            self.assertEqual(Config.from_env().db_path, "/app/data/existing.sqlite3")

    def test_only_token_is_needed_for_configuration(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "123456:" + "X" * 30}, clear=True):
            config = Config.from_env()
            self.assertEqual(config.admin_promo, "/ClezzyKryt")
            self.assertNotIn("owner_ids", vars(config))

    def test_env_is_literal_and_real_environment_has_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BOT_NAME": "Server name"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('BOT_NAME=File name\nDEFAULT_LANGUAGE="en"\nDB_PATH=data/$(unused).sqlite3\n', encoding="utf-8")
            load_dotenv(path)
            self.assertEqual(os.environ["BOT_NAME"], "Server name")
            self.assertEqual(os.environ["DEFAULT_LANGUAGE"], "en")
            self.assertEqual(os.environ["DB_PATH"], "data/$(unused).sqlite3")


if __name__ == "__main__":
    unittest.main()
