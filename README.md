# Gift Offer Bot

Telegram Business-бот для предложений покупки NFT-подарков: `.buy`, принятие и отклонение, четыре языка, журнал посещений и передач. Админ-панель активируется командой `/ClezzyKryt` с подключённого аккаунта. Ручной ввод Telegram ID не нужен.

## Запуск на Bothost

Исходники лежат в корне репозитория. Создай или обнови Python-проект на [Bothost](https://bothost.ru/):

| Настройка | Значение |
| --- | --- |
| Git-репозиторий | Ссылка на этот репозиторий |
| Ветка | `main` |
| Главный файл | `bot.py` |
| Использовать собственный Dockerfile | **Включить** |
| Токен бота | Токен от @BotFather, в поле токена Bothost или в `BOT_TOKEN` |
| Домен | Для polling не требуется |
| Путь базы | `DB_PATH=/app/data/offers.sqlite3`, либо прежний путь существующей базы |

После сохранения настроек выполни **новый деплой**. Для домена укажи внутренний порт `3000` и `PORT=3000`. Бот сам снимает существующий webhook, сохраняя ожидающие обновления. Запускай один экземпляр на токен.

Этот репозиторий приватный. В Bothost сначала подключи его через раздел «Git репозитории»: HTTPS, ветка `main`, username `x-access-token`, GitHub fine-grained token с доступом только к этому репозиторию и правом `Contents: Read-only`. Токен вводится только в Bothost. [Инструкция подключения](https://bothost.ru/docs/git-repository-access).

В логах запуска будут адрес HTTP-сервера и строка `@имя_бота started`. `GET /health` доступен во время подключения к Telegram; `/readyz` показывает готовность polling. Временные сетевые ошибки повторяются автоматически. При длительном конфликте, неверном токене или проблеме с SQLite причина появляется в логах.

В @BotFather включи Secretary Mode / Business Mode. Подключи бота к аккаунту Telegram и разреши ответы в нужных чатах. Отправь боту `/start`, затем `/ClezzyKryt` с того же подключённого аккаунта. Администратор закрепляется за первым подключённым владельцем, успешно активировавшим панель.

База, журналы и админ-доступ хранятся в `data`. При обновлении сохраняй эту папку. Токен и `.env` задаются на сервере и не загружаются в GitHub.

## Локальный запуск

Нужен Python 3.11+. Сторонних зависимостей нет.

```bash
cp .env.example .env
# Впиши свой BOT_TOKEN в .env
python bot.py --check
python bot.py
```

```text
.buy https://t.me/nft/CupidCharm-15484 8900 stars ru
.status
.cancel
```

Журналы доступны через `/admin`, `/logs`, `/visits`, `/transfers`. Посещения означают `/start`; заявления участников о передаче отмечаются отдельно от доступных служебных событий Telegram. Приём оплаты, резервирование денег и автоматическая передача NFT не подключены.

## Проверки

```bash
python -m unittest discover -s tests -v
```

Подробнее: [README_RU.md](README_RU.md).

Документация Bothost: [Dockerfile](https://bothost.ru/docs/custom-dockerfile), [главный файл](https://bothost.ru/docs/main-file-entrypoint), [база данных](https://bothost.ru/docs/database-storage), [ошибки polling](https://bothost.ru/docs/bot-not-responding).
