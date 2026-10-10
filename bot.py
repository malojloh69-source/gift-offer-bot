#!/usr/bin/env python3
"""Run with Python 3.11+, no third-party packages required."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gift_offer.config import Config, load_dotenv
from gift_offer.service import Service
from gift_offer.store import Store
from gift_offer.telegram import Telegram, TelegramError, TransportError


log = logging.getLogger("gift_offer")
ALLOWED_UPDATES = ["message", "business_connection", "business_message", "callback_query", "deleted_business_messages"]


def health_server(port: int, health: dict):
    if port == 0:
        return None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/", "/health", "/healthz", "/readyz"}:
                self.send_response(404)
                self.end_headers()
                return
            ok = bool(health["ready"] and time.time() - health["last_poll"] < 90)
            payload = json.dumps({"status": "ok" if ok else "starting_or_disconnected", "ready": ok}).encode()
            readiness_check = self.path in {"/healthz", "/readyz"}
            self.send_response(503 if readiness_check and not ok else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_HEAD(self):
            self.do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("HTTP: 0.0.0.0:%s, /health — процесс, /readyz — Telegram.", port)
    return server


async def wait_or_stop(stop: asyncio.Event, seconds: float):
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass


async def poll(service: Service, stop: asyncio.Event, health: dict):
    conflicts = 0
    while not stop.is_set():
        offset = int(service.store.get_setting("offset"))
        try:
            updates = await service.api.call("getUpdates", offset=offset, timeout=25,
                                             allowed_updates=ALLOWED_UPDATES, limit=50)
            health["last_poll"] = time.time()
            conflicts = 0
        except TelegramError as exc:
            if exc.code == 409 and "webhook" not in exc.description.lower():
                conflicts += 1
                if conflicts <= 6:
                    log.warning("Конфликт polling (%s/6). Повтор через 5 секунд; оставьте один процесс бота.", conflicts)
                    await wait_or_stop(stop, 5)
                    continue
            if exc.code in {401, 409}:
                if exc.code == 401:
                    raise RuntimeError("Telegram не принимает токен. Обновите BOT_TOKEN в панели Bothost.") from None
                raise RuntimeError("Telegram отклонил polling. Остановите вторую копию бота или сервис, настраивающий webhook.") from None
            log.warning("Polling error: %s", exc)
            await wait_or_stop(stop, 5)
            continue
        except TransportError as exc:
            log.warning("Polling error: %s", exc)
            await wait_or_stop(stop, 5)
            continue
        for update in updates:
            if stop.is_set():
                break
            try:
                await service.handle(update)
            except Exception:
                # Preserve the offset on an unexpected processing failure.
                # Offer source uniqueness prevents duplicate creation on redelivery.
                log.exception("Update %s failed; it will be retried", update.get("update_id"))
                await wait_or_stop(stop, 5)
                break
            service.store.set_setting("offset", str(update["update_id"] + 1))


async def maintenance(service: Service, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await service.tick()
        except Exception:
            log.exception("Offer maintenance failed")
        await wait_or_stop(stop, 5)


async def ensure_polling(api: Telegram):
    """Remove an existing webhook without discarding queued updates."""
    webhook = await api.call("getWebhookInfo")
    if not webhook.get("url"):
        return
    log.info("Отключаем webhook для запуска polling; очередь обновлений сохраняется.")
    deleted = await api.call("deleteWebhook", drop_pending_updates=False)
    if deleted is not True:
        raise RuntimeError("Telegram не подтвердил отключение webhook. Перезапустите бота.")
    webhook = await api.call("getWebhookInfo")
    if webhook.get("url"):
        raise RuntimeError("Webhook остаётся активным. Остановите другой сервис этого бота и перезапустите.")
    log.info("Webhook отключён. Ожидающие обновления будут обработаны через polling.")


async def connect_to_telegram(api: Telegram, stop: asyncio.Event):
    while not stop.is_set():
        try:
            me = await api.call("getMe")
            await ensure_polling(api)
            return me
        except TelegramError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
            log.warning("Telegram временно недоступен: %s. Повтор подключения через 5 секунд.", exc)
        except TransportError as exc:
            log.warning("%s. Повтор подключения через 5 секунд.", exc)
        await wait_or_stop(stop, 5)
    return None


async def run(config: Config):
    api = Telegram(config.token)
    health = {"ready": False, "last_poll": time.time()}
    server = None
    store = None
    tasks: list[asyncio.Task] = []
    stop = asyncio.Event()
    try:
        # Open the platform's health endpoint before any network requests.
        server = health_server(config.port, health)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            store = Store(config.db_path)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError("Не удалось открыть SQLite. Проверьте DB_PATH и право записи в папку data; "
                               "на Bothost включите собственный Dockerfile. " + str(exc)) from None

        async def serve():
            me = await connect_to_telegram(api, stop)
            if me is None:
                return
            service = Service(config, store, api, me["id"])
            store.recover_interrupted_sends(int(time.time()))
            try:
                await api.call("setMyCommands", commands=[
                    {"command": "start", "description": "Как создать оффер / Create an offer"},
                    {"command": "admin", "description": "Админ-панель"},
                    {"command": "terms", "description": "Как работает бот / How the bot works"},
                ])
            except (TelegramError, TransportError) as exc:
                # Menu registration is optional; messages and commands still work.
                log.warning("Не удалось обновить меню команд: %s. Запуск продолжается.", exc)
            if not me.get("can_connect_to_business"):
                log.warning("Включите Secretary Mode (Business Mode) у @BotFather для .buy в бизнес-чатах.")
            health["ready"] = True
            log.info("@%s started. Business offers expire after %s seconds.", me.get("username", "bot"), config.offer_ttl)
            workers = [asyncio.create_task(poll(service, stop, health)),
                       asyncio.create_task(maintenance(service, stop))]
            tasks.extend(workers)
            await asyncio.gather(*workers)

        worker = asyncio.create_task(serve())
        shutdown = asyncio.create_task(stop.wait())
        tasks.extend([worker, shutdown])
        done, _ = await asyncio.wait([worker, shutdown], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        health["ready"] = False
        stop.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if server:
            server.shutdown()
            server.server_close()
        if store:
            store.close()


def main():
    parser = argparse.ArgumentParser(description="Gift Offer Telegram Business bot")
    parser.add_argument("--check", action="store_true", help="Validate configuration without Telegram requests")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        root = Path(__file__).resolve().parent
        os.chdir(root)
        load_dotenv(root / ".env")
        config = Config.from_env()
        if args.check:
            print(f"Конфигурация корректна. Владельцы определяются автоматически. Язык: {config.default_language}.")
            return 0
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
