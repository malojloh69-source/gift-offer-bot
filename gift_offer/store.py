from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .domain import OfferError, OfferSpec, next_status


class Store:
    """Synchronous, short SQLite transactions used on one asyncio event loop."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS connections(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers(
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL, card_message_id INTEGER,
                buyer_id INTEGER NOT NULL, seller_id INTEGER NOT NULL,
                buyer_name TEXT NOT NULL, buyer_username TEXT NOT NULL,
                gift_url TEXT NOT NULL, gift_slug TEXT NOT NULL, gift_name TEXT NOT NULL,
                amount TEXT NOT NULL, currency TEXT NOT NULL, language TEXT NOT NULL,
                status TEXT NOT NULL, created_at INTEGER NOT NULL,
                payment_claimed INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                dirty INTEGER NOT NULL DEFAULT 0,
                display_enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(connection_id, chat_id, source_message_id)
            );
            CREATE INDEX IF NOT EXISTS offers_expiry ON offers(status, expires_at);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id TEXT NOT NULL REFERENCES offers(id),
                actor_id INTEGER, action TEXT NOT NULL, at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                last_name TEXT NOT NULL, username TEXT NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, at INTEGER NOT NULL,
                actor_id INTEGER, recipient_id INTEGER, owner_id INTEGER,
                gift_url TEXT NOT NULL DEFAULT '', gift_name TEXT NOT NULL DEFAULT '',
                amount TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_kind ON audit_log(kind, id);
        """)
        self._backfill_audit()

    def close(self) -> None:
        self.db.close()

    def get_setting(self, key: str, default: str = "0") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))

    def save_connection(self, connection: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO connections VALUES(?,?)", (
                connection["id"], json.dumps(connection, ensure_ascii=False),
            ))

    def touch_user(self, user: dict, now: int) -> None:
        user_id = user.get("id")
        if not isinstance(user_id, int) or user_id <= 0 or user.get("is_bot"):
            return
        previous = self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        profile = {key: str(user[key] if key in user else previous[key] if previous else "")
                   for key in ("first_name", "last_name", "username")}
        with self.db:
            self.db.execute("INSERT INTO users VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                            "first_name=excluded.first_name,last_name=excluded.last_name,"
                            "username=excluded.username,updated_at=excluded.updated_at", (
                user_id, profile["first_name"][:128], profile["last_name"][:128],
                profile["username"][:32], now,
            ))

    def known_connections(self, owner_id: int) -> list[dict]:
        return [connection for row in self.db.execute("SELECT payload FROM connections")
                if (connection := json.loads(row[0])).get("user", {}).get("id") == owner_id]

    def is_admin(self, user_id: int) -> bool:
        return self.get_setting("admin_user", "") == str(user_id)

    def claim_admin(self, user_id: int, now: int, source_key: str) -> bool:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.get_setting("admin_user", "")
            if current and current != str(user_id):
                return False
            if not current:
                self.db.execute("INSERT INTO settings VALUES('admin_user',?)", (str(user_id),))
                self._audit(source_key, "admin_activation", now, actor_id=user_id, evidence="bot_command")
        return True

    def _audit(self, source_key: str, kind: str, now: int, *, actor_id: int | None = None,
               recipient_id: int | None = None, owner_id: int | None = None,
               gift_url: str = "", gift_name: str = "", amount: str = "", currency: str = "",
               evidence: str = "user_statement") -> None:
        self.db.execute("INSERT OR IGNORE INTO audit_log(source_key,kind,at,actor_id,recipient_id,owner_id,"
                        "gift_url,gift_name,amount,currency,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            source_key, kind, now, actor_id, recipient_id, owner_id, gift_url, gift_name,
            amount, currency, evidence,
        ))

    def record_visit(self, user: dict, chat_id: int, message_id: int, now: int) -> None:
        self.touch_user(user, now)
        with self.db:
            self._audit(f"visit:{chat_id}:{message_id}", "visit", now,
                        actor_id=user["id"], evidence="bot_command")

    def record_telegram_transfer(self, connection_id: str, chat_id: int, message_id: int, now: int,
                                 actor_id: int, recipient_id: int, owner_id: int,
                                 gift_url: str, gift_name: str) -> None:
        with self.db:
            self._audit(f"telegram:{connection_id}:{chat_id}:{message_id}", "telegram_transfer", now,
                        actor_id=actor_id, recipient_id=recipient_id, owner_id=owner_id,
                        gift_url=gift_url, gift_name=gift_name, evidence="telegram_service")

    def _offer_audit(self, offer: dict, action: str, actor_id: int | None, now: int, event_id: int) -> None:
        kinds = {"create": "offer_created", "accept": "offer_accepted", "reject": "offer_rejected",
                 "cancel": "offer_cancelled", "sent": "transfer_claim", "received": "receipt_claim",
                 "settled": "payment_claim"}
        kind = kinds.get(action)
        if not kind:
            return
        recipient = offer["buyer_id"] if action == "sent" else offer["seller_id"] if action == "received" else None
        self._audit(f"offer-event:{event_id}", kind, now, actor_id=actor_id, recipient_id=recipient,
                    owner_id=offer["buyer_id"], gift_url=offer["gift_url"], gift_name=offer["gift_name"],
                    amount=offer["amount"], currency=offer["currency"],
                    evidence="bot_command" if action == "create" else "user_statement")

    def _backfill_audit(self) -> None:
        if self.get_setting("audit_backfill", "") == "1":
            return
        with self.db:
            for offer in self.db.execute("SELECT * FROM offers").fetchall():
                self.db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)", (
                    offer["buyer_id"], offer["buyer_name"], "", offer["buyer_username"], offer["created_at"],
                ))
            rows = self.db.execute("SELECT events.id AS event_id, events.action, events.actor_id, events.at,"
                                   "offers.* FROM events JOIN offers ON offers.id=events.offer_id").fetchall()
            for row in rows:
                self._offer_audit(dict(row), row["action"], row["actor_id"], row["at"], row["event_id"])
            self.db.execute("UPDATE offers SET dirty=1 WHERE card_message_id IS NOT NULL AND display_enabled=1 "
                            "AND status IN ('pending','accepted','payment_reported','gift_reported','gift_received')")
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('audit_backfill','1')")

    def audit_stats(self) -> dict:
        stats = {row[0]: row[1] for row in self.db.execute("SELECT kind,COUNT(*) FROM audit_log GROUP BY kind")}
        stats["unique_visitors"] = self.db.execute("SELECT COUNT(DISTINCT actor_id) FROM audit_log WHERE kind='visit'").fetchone()[0]
        stats["offers"] = self.db.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        return stats

    def audit_page(self, category: str = "all", before: int = 0, limit: int = 6) -> list[dict]:
        filters = {
            "all": (), "visits": ("visit",),
            "transfers": ("transfer_claim", "receipt_claim", "telegram_transfer"),
            "offers": ("offer_created", "offer_accepted", "offer_rejected", "offer_cancelled", "payment_claim"),
        }
        if category not in filters:
            raise ValueError("Unknown log category")
        where, args = [], []
        if before:
            where.append("a.id < ?")
            args.append(before)
        kinds = filters[category]
        if kinds:
            where.append("a.kind IN (" + ",".join("?" for _ in kinds) + ")")
            args.extend(kinds)
        query = "SELECT a.*,u.first_name AS actor_first,u.last_name AS actor_last,u.username AS actor_username," \
                "r.first_name AS recipient_first,r.last_name AS recipient_last,r.username AS recipient_username " \
                "FROM audit_log a LEFT JOIN users u ON u.user_id=a.actor_id LEFT JOIN users r ON r.user_id=a.recipient_id"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY a.id DESC LIMIT ?"
        args.append(min(max(limit, 1), 20))
        return [dict(row) for row in self.db.execute(query, args)]

    def connection(self, connection_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM connections WHERE id=?", (connection_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def offer(self, offer_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
        return dict(row) if row else None

    def from_source(self, connection_id: str, chat_id: int, message_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM offers WHERE connection_id=? AND chat_id=? AND source_message_id=?",
                              (connection_id, chat_id, message_id)).fetchone()
        return dict(row) if row else None

    def latest_offer(self, connection_id: str, chat_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM offers WHERE connection_id=? AND chat_id=? "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1", (connection_id, chat_id)).fetchone()
        return dict(row) if row else None

    def latest_creation(self, buyer_id: int) -> int:
        row = self.db.execute("SELECT MAX(created_at) FROM offers WHERE buyer_id=?", (buyer_id,)).fetchone()
        return int(row[0] or 0)

    def create(self, spec: OfferSpec, connection: dict, message: dict, now: int, ttl: int) -> dict:
        user = connection["user"]
        row = {
            "id": secrets.token_hex(8), "connection_id": connection["id"],
            "chat_id": message["chat"]["id"], "source_message_id": message["message_id"],
            "buyer_id": user["id"], "seller_id": message["chat"]["id"],
            "buyer_name": " ".join(filter(None, [user.get("first_name", ""), user.get("last_name", "")])) or "Покупатель",
            "buyer_username": user.get("username", ""),
            **asdict(spec), "status": "pending", "created_at": now,
            "expires_at": now + ttl, "updated_at": now,
        }
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        with self.db:
            self.db.execute(f"INSERT INTO offers({columns}) VALUES({placeholders})", tuple(row.values()))
            event = self.db.execute("INSERT INTO events(offer_id,actor_id,action,at) VALUES(?,?,?,?)",
                                    (row["id"], user["id"], "create", now))
            self._offer_audit(row, "create", user["id"], now, event.lastrowid)
        return self.offer(row["id"])

    def attach_card(self, offer_id: str, message_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE offers SET card_message_id=?, dirty=0 WHERE id=?", (message_id, offer_id))

    def delivery_problem(self, offer_id: str, ambiguous: bool, now: int) -> None:
        status = "delivery_unknown" if ambiguous else "delivery_failed"
        with self.db:
            self.db.execute("UPDATE offers SET status=?, updated_at=? WHERE id=?", (status, now, offer_id))
            self.db.execute("INSERT INTO events(offer_id,action,at) VALUES(?,?,?)", (offer_id, status, now))

    def recover_interrupted_sends(self, now: int) -> None:
        with self.db:
            ids = self.db.execute("SELECT id FROM offers WHERE card_message_id IS NULL AND status='pending'").fetchall()
            for row in ids:
                self.db.execute("UPDATE offers SET status='delivery_unknown',updated_at=? WHERE id=?", (now, row[0]))
                self.db.execute("INSERT INTO events(offer_id,action,at) VALUES(?,?,?)", (row[0], "delivery_unknown", now))

    def recover_card(self, offer_id: str, message_id: int, now: int) -> dict:
        with self.db:
            self.db.execute("UPDATE offers SET card_message_id=?,status='pending',dirty=1,updated_at=? "
                            "WHERE id=? AND card_message_id IS NULL AND status='delivery_unknown'",
                            (message_id, now, offer_id))
        return self.offer(offer_id)

    def transition(self, offer_id: str, action: str, actor_id: int, now: int) -> dict:
        # No await occurs in the transaction. Authorizations and changes are atomic.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.offer(offer_id)
            if row is None:
                raise OfferError("not_found")
            target = next_status(row, action, actor_id, now)
            self.db.execute("UPDATE offers SET status=?,updated_at=?,dirty=1, "
                            "payment_claimed=CASE WHEN ?='settled' THEN 1 ELSE payment_claimed END WHERE id=?",
                            (target, now, action, offer_id))
            event = self.db.execute("INSERT INTO events(offer_id,actor_id,action,at) VALUES(?,?,?,?)",
                                    (offer_id, actor_id, action, now))
            self._offer_audit(row, action, actor_id, now, event.lastrowid)
        return self.offer(offer_id)

    def expire(self, now: int) -> None:
        with self.db:
            rows = self.db.execute("SELECT id FROM offers WHERE status='pending' AND expires_at<=?", (now,)).fetchall()
            for row in rows:
                self.db.execute("UPDATE offers SET status='expired',updated_at=?,dirty=1 WHERE id=?", (now, row[0]))
                self.db.execute("INSERT INTO events(offer_id,action,at) VALUES(?,?,?)", (row[0], "expire", now))

    def dirty_offers(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM offers WHERE dirty=1 AND card_message_id IS NOT NULL AND display_enabled=1 LIMIT 30"
        )]

    def mark_clean(self, offer_id: str, displayed_status: str) -> None:
        with self.db:
            # Do not clear a newer state while an edit request was in flight.
            self.db.execute("UPDATE offers SET dirty=0 WHERE id=? AND status=?", (offer_id, displayed_status))

    def hide_card(self, connection_id: str, chat_id: int, message_id: int, now: int) -> None:
        with self.db:
            self.db.execute("UPDATE offers SET display_enabled=0,dirty=0,updated_at=?, "
                            "status=CASE WHEN status IN ('pending','accepted') THEN 'cancelled' ELSE status END "
                            "WHERE connection_id=? AND chat_id=? AND card_message_id=?",
                            (now, connection_id, chat_id, message_id))
