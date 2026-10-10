from __future__ import annotations

from datetime import datetime, timezone
from html import escape


TEXT = {
    "ru": {
        "offer": "Предложение", "proposes": "{buyer} предлагает <b>{price}</b> за подарок {gift}.",
        "stars": "⭐ Stars", "gram": "Gram", "expires": "Действует до {deadline}.",
        "disclosure": "Отдельный сервис офферов. Бот не принимает и не удерживает деньги. Запуски бота и действия с офферами сохраняются в журнале.",
        "accept": "✓ Принять", "reject": "✕ Отклонить", "gift": "Показать подарок",
        "buyer": "Профиль покупателя", "cancel": "Отменить оффер",
        "sent": "Я передал подарок", "received": "Я получил подарок", "settled": "Я получил оплату",
        "pending": "Ожидает решения продавца",
        "accepted": "Предложение принято. Согласуйте расчёт с покупателем. Оплата не подтверждена; передача подарка не запускает автоматическую выплату.",
        "payment_reported": "Продавец сообщил о получении оплаты. Бот не проверял платёж. Передача подарка ещё не подтверждена.",
        "payment_note": "Продавец также сообщил о получении оплаты; бот не проверял платёж.",
        "gift_reported": "Продавец сообщил о передаче подарка. Покупатель должен проверить получение в своём аккаунте и подтвердить его. Бот не проверил передачу.",
        "gift_received": "Покупатель сообщил о получении подарка. Продавец может подтвердить получение оплаты после проверки собственного счёта.",
        "completed": "Участники сообщили о получении подарка и оплаты. Оффер закрыт по их заявлениям; бот не проверял транзакции.",
        "rejected": "Продавец отклонил предложение.", "cancelled": "Оффер отменён.",
        "expired": "Срок предложения истёк. Создайте новый оффер, если хотите продолжить.",
        "delivery_failed": "Карточка не отправлена: Telegram отклонил запрос.",
        "delivery_unknown": "Статус отправки неизвестен. Если карточка появилась, нажмите её кнопку; не создавайте дубликат до проверки чата.",
        "help": "<b>{name} · Как создать оффер</b>\n\nОтправь команду со своего подключённого бизнес-аккаунта в личный чат с владельцем подарка:\n\n<code>.buy ССЫЛКА_НА_НФТ ЦЕНА ВАЛЮТА ЯЗЫК</code>\n\n<b>Примеры:</b>\n<code>.buy https://t.me/nft/DurovsCap-1234 500 stars</code>\n<code>.buy https://t.me/nft/DurovsCap-1234 10 gram en</code>\n<code>.buy https://t.me/nft/CupidCharm-15484 8900 stars ru</code>\n\nВалюты: <code>stars / gram</code>\nЯзыки: <code>ru / en / zh / ar</code>\nЯзык можно не указывать.\n\n<code>.status</code> — статус последнего оффера в текущем чате.\n<code>.cancel</code> — отмена последнего оффера до заявления о передаче или оплате.\n\n{disclosure}",
        "terms": "<b>{name} · Условия</b>\n\nБот хранит офферы и заявления участников. Stars и Gram — обозначения цены предложения. Касса, резервирование, выплаты, гарантия, автоматическая передача NFT и возвраты не подключены.\n\nПринятие означает согласие с ценой предложения. Кнопки передачи, получения и оплаты фиксируют только заявления пользователей. Проверяйте подарок в аккаунте, а оплату на собственном счёте.\n\nЦвет кнопок, фон чата и превью ссылок оформляет приложение Telegram.",
        "syntax": "Формат: .buy https://t.me/nft/Название-1234 500 stars ru",
        "gift_url": "Нужна ссылка https://t.me/nft/Название-1234 без параметров и сторонних доменов.",
        "currency": "Валюта: stars или gram.", "amount": "Цена должна быть положительной: до 100 000 000 Stars или 1 000 000 Gram. Для Gram — до 9 знаков после точки.",
        "stars_integer": "Цена в Stars должна быть целым числом.", "language": "Язык: ru, en, zh или ar.",
        "wrong_role": "Эта кнопка предназначена другому участнику сделки.",
        "wrong_state": "На этом этапе действие недоступно. Карточка будет обновлена.",
        "already_done": "Это действие уже выполнено.", "not_found": "Оффер не найден.",
        "unknown_action": "Неизвестная кнопка.", "wrong_card": "Эта кнопка не относится к исходной карточке оффера.",
        "connection_disabled": "Подключение бота отключено или у него нет права отвечать в этом чате.",
        
        "cooldown": "Подожди несколько секунд перед созданием следующего оффера.",
        "status_syntax": "Используй .status или .cancel без дополнительных аргументов.",
        "done": "Сохранено.", "check_chat": "Карточка могла быть отправлена. Проверь чат перед повтором команды.",
    },
    "en": {
        "offer": "Offer", "proposes": "{buyer} offers <b>{price}</b> for {gift}.",
        "stars": "⭐ Stars", "gram": "Gram", "expires": "Valid until {deadline}.",
        "disclosure": "Independent offer service. The bot does not collect or hold funds. Bot starts and offer actions are recorded in a log.",
        "accept": "✓ Accept", "reject": "✕ Decline", "gift": "Show gift", "buyer": "Buyer profile",
        "cancel": "Cancel offer", "sent": "I sent the gift", "received": "I received the gift", "settled": "I received payment",
        "pending": "Waiting for the seller",
        "accepted": "Offer accepted. Agree settlement with the buyer. Payment is unconfirmed; sending the gift does not trigger an automatic payout.",
        "payment_reported": "The seller reported receiving payment. The bot did not verify payment. The gift transfer has not been confirmed yet.",
        "payment_note": "The seller also reported receiving payment; the bot did not verify it.",
        "gift_reported": "The seller reported sending the gift. The buyer must check their account before confirming receipt. The bot has not verified the transfer.",
        "gift_received": "The buyer reported receiving the gift. The seller may confirm payment after checking their own account.",
        "completed": "The participants reported receiving the gift and payment. The offer is closed based on their statements; the bot did not verify transactions.",
        "rejected": "The seller declined the offer.", "cancelled": "Offer cancelled.",
        "expired": "This offer has expired. Create a new offer to continue.",
        "delivery_failed": "Telegram rejected the message request; the card was not sent.",
        "delivery_unknown": "Message delivery is uncertain. If the card appeared, use its button. Check the chat before creating a duplicate.",
        "help": "<b>{name} · Create an offer</b>\n\nSend this command from your connected business account in a private chat with the gift owner:\n\n<code>.buy NFT_LINK PRICE CURRENCY LANGUAGE</code>\n\n<code>.buy https://t.me/nft/DurovsCap-1234 500 stars</code>\n<code>.buy https://t.me/nft/DurovsCap-1234 10 gram en</code>\n\nCurrencies: <code>stars / gram</code>\nLanguages: <code>ru / en / zh / ar</code> (optional)\n\n<code>.status</code> — latest offer status in this chat.\n<code>.cancel</code> — cancel the latest offer before a transfer or payment report.\n\n{disclosure}",
        "terms": "<b>{name} · Terms</b>\n\nThe bot records offers and participant statements. Stars and Gram are price labels. Payment collection, escrow, payouts, guarantees, automatic NFT transfers and refunds are not connected.\n\nAcceptance agrees to the proposed price. Confirmation buttons record user statements only. Check gifts in your account and payments in your own balance.\n\nTelegram controls button colours, chat backgrounds and link previews.",
        "syntax": "Format: .buy https://t.me/nft/Name-1234 500 stars en",
        "gift_url": "Use https://t.me/nft/Name-1234 without query parameters or other domains.",
        "currency": "Currency must be stars or gram.", "amount": "Use a positive price up to 100,000,000 Stars or 1,000,000 Gram. Gram supports up to 9 decimals.",
        "stars_integer": "Stars must be a whole number.", "language": "Language must be ru, en, zh or ar.",
        "wrong_role": "This action belongs to the other participant.", "wrong_state": "This action is unavailable at this stage. The card will update.",
        "already_done": "This action is already recorded.", "not_found": "Offer not found.", "unknown_action": "Unknown button.",
        "wrong_card": "This is not the original offer card.",
        "connection_disabled": "The connection is disabled or the bot cannot reply in this chat.",
        
        "cooldown": "Wait a few seconds before creating another offer.",
        "status_syntax": "Use .status or .cancel without extra arguments.",
        "done": "Saved.", "check_chat": "The card may have been delivered. Check the chat before retrying.",
    },
    "zh": {
        "offer": "报价", "proposes": "{buyer} 愿以 <b>{price}</b> 购买 {gift}。",
        "stars": "⭐ Stars", "gram": "Gram", "expires": "有效期至 {deadline}。",
        "disclosure": "独立报价服务。机器人不收取或托管资金。启动机器人和报价操作将记录在日志中。",
        "accept": "✓ 接受", "reject": "✕ 拒绝", "gift": "查看礼物", "buyer": "买家资料",
        "cancel": "取消报价", "sent": "我已转移礼物", "received": "我已收到礼物", "settled": "我已收到付款",
        "pending": "等待卖家决定",
        "accepted": "报价已接受。请与买家商定结算方式。付款尚未确认；转移礼物不会触发自动付款。",
        "payment_reported": "卖家表示已收到付款。机器人未验证付款。礼物转移尚未确认。",
        "payment_note": "卖家也表示已收到付款；机器人未验证付款。",
        "gift_reported": "卖家表示已转移礼物。买家应先在自己的账户中核实，再确认收到。机器人未验证转移。",
        "gift_received": "买家表示已收到礼物。卖家核查自己的账户后，可确认收到付款。",
        "completed": "双方表示已收到礼物和付款。报价根据双方声明关闭；机器人未验证交易。",
        "rejected": "卖家拒绝了报价。", "cancelled": "报价已取消。", "expired": "报价已过期。如需继续，请创建新报价。",
        "delivery_failed": "Telegram 拒绝了发送请求，报价卡片未发送。",
        "delivery_unknown": "发送状态未知。如果卡片已出现，请使用卡片按钮。创建重复报价前请检查聊天。",
        "help": "<b>{name} · 创建报价</b>\n\n从已连接的商务账户，在与礼物持有者的私聊中发送：\n\n<code>.buy 礼物链接 价格 币种 语言</code>\n\n<code>.buy https://t.me/nft/DurovsCap-1234 500 stars zh</code>\n<code>.buy https://t.me/nft/DurovsCap-1234 10 gram en</code>\n\n币种：<code>stars / gram</code>\n语言：<code>ru / en / zh / ar</code>（可省略）\n\n<code>.status</code>：查看当前聊天的最新报价状态。\n<code>.cancel</code>：在声明转移或收款前取消最新报价。\n\n{disclosure}",
        "terms": "<b>{name} · 使用说明</b>\n\n机器人仅记录报价和用户声明。Stars 和 Gram 是价格标签。未连接收款、资金托管、付款、担保、自动礼物转移或退款功能。\n\n接受代表同意报价。确认按钮仅记录用户声明。请在自己的账户中核实礼物和付款。\n\n按钮颜色、聊天背景和链接预览由 Telegram 应用决定。",
        "syntax": "格式：.buy https://t.me/nft/Name-1234 500 stars zh",
        "gift_url": "请使用 https://t.me/nft/Name-1234，不含其他域名或查询参数。",
        "currency": "币种必须为 stars 或 gram。", "amount": "价格必须为正数，最高 100,000,000 Stars 或 1,000,000 Gram；Gram 最多 9 位小数。",
        "stars_integer": "Stars 必须为整数。", "language": "语言必须为 ru、en、zh 或 ar。",
        "wrong_role": "此操作应由另一位参与者执行。", "wrong_state": "当前阶段无法执行此操作，卡片将更新。",
        "already_done": "此操作已记录。", "not_found": "未找到报价。", "unknown_action": "未知按钮。",
        "wrong_card": "这不是原始报价卡片。", "connection_disabled": "连接已关闭，或机器人无法在此聊天中回复。",
        "cooldown": "创建下一个报价前请等待几秒。",
        "status_syntax": "使用 .status 或 .cancel，无需其他参数。", "done": "已保存。",
        "check_chat": "卡片可能已发送。重新发送命令前请检查聊天。",
    },
    "ar": {
        "offer": "عرض", "proposes": "يعرض {buyer} مبلغ <b>{price}</b> مقابل {gift}.",
        "stars": "⭐ Stars", "gram": "Gram", "expires": "صالح حتى {deadline}.",
        "disclosure": "خدمة عروض مستقلة. لا يستلم البوت الأموال أو يحتجزها. يتم تسجيل تشغيل البوت وإجراءات العروض في سجل.",
        "accept": "✓ قبول", "reject": "✕ رفض", "gift": "عرض الهدية", "buyer": "ملف المشتري",
        "cancel": "إلغاء العرض", "sent": "أرسلت الهدية", "received": "استلمت الهدية", "settled": "استلمت الدفع",
        "pending": "بانتظار قرار البائع",
        "accepted": "تم قبول العرض. اتفق على التسوية مع المشتري. الدفع غير مؤكد؛ نقل الهدية لا يؤدي إلى دفعة تلقائية.",
        "payment_reported": "أفاد البائع باستلام الدفع. لم يتحقق البوت من الدفع. لم يتم تأكيد نقل الهدية بعد.",
        "payment_note": "أفاد البائع أيضاً باستلام الدفع؛ لم يتحقق البوت منه.",
        "gift_reported": "أفاد البائع بإرسال الهدية. على المشتري التحقق منها في حسابه قبل تأكيد الاستلام. لم يتحقق البوت من النقل.",
        "gift_received": "أفاد المشتري باستلام الهدية. يمكن للبائع تأكيد استلام الدفع بعد التحقق من حسابه.",
        "completed": "أفاد الطرفان باستلام الهدية والدفع. أُغلق العرض بناءً على تصريحاتهما؛ لم يتحقق البوت من المعاملات.",
        "rejected": "رفض البائع العرض.", "cancelled": "تم إلغاء العرض.", "expired": "انتهت صلاحية العرض. أنشئ عرضاً جديداً للمتابعة.",
        "delivery_failed": "رفض Telegram طلب الإرسال؛ لم تُرسل البطاقة.",
        "delivery_unknown": "حالة الإرسال غير معروفة. إن ظهرت البطاقة، استخدم زرها. تحقق من المحادثة قبل إنشاء عرض مكرر.",
        "help": "<b>{name} · إنشاء عرض</b>\n\nأرسل الأمر من حساب الأعمال المتصل في محادثة خاصة مع مالك الهدية:\n\n<code>.buy NFT_LINK PRICE CURRENCY LANGUAGE</code>\n\n<code>.buy https://t.me/nft/DurovsCap-1234 500 stars ar</code>\n<code>.buy https://t.me/nft/DurovsCap-1234 10 gram en</code>\n\nالعملات: <code>stars / gram</code>\nاللغات: <code>ru / en / zh / ar</code> (اختياري)\n\n<code>.status</code> — حالة آخر عرض في هذه المحادثة.\n<code>.cancel</code> — إلغاء آخر عرض قبل الإبلاغ عن النقل أو الدفع.\n\n{disclosure}",
        "terms": "<b>{name} · الشروط</b>\n\nيسجل البوت العروض وتصريحات المشاركين فقط. Stars وGram تسميات للسعر. تحصيل الأموال وحجزها والدفع والضمان والنقل التلقائي للهدايا والاسترداد غير متصلة.\n\nالقبول يعني الموافقة على السعر. أزرار التأكيد تسجل تصريحات المستخدمين فقط. تحقق من الهدية والدفع في حسابك.\n\nيتحكم تطبيق Telegram بألوان الأزرار والخلفية ومعاينات الروابط.",
        "syntax": "الصيغة: .buy https://t.me/nft/Name-1234 500 stars ar",
        "gift_url": "استخدم https://t.me/nft/Name-1234 دون معاملات أو نطاقات أخرى.",
        "currency": "العملة يجب أن تكون stars أو gram.", "amount": "السعر موجب وبحد أقصى 100,000,000 Stars أو 1,000,000 Gram. يدعم Gram حتى 9 منازل عشرية.",
        "stars_integer": "يجب أن يكون سعر Stars عدداً صحيحاً.", "language": "اللغة: ru أو en أو zh أو ar.",
        "wrong_role": "هذا الإجراء مخصص للمشارك الآخر.", "wrong_state": "الإجراء غير متاح في هذه المرحلة. ستُحدّث البطاقة.",
        "already_done": "تم تسجيل هذا الإجراء بالفعل.", "not_found": "لم يُعثر على العرض.", "unknown_action": "زر غير معروف.",
        "wrong_card": "هذه ليست بطاقة العرض الأصلية.", "connection_disabled": "الاتصال معطل أو البوت لا يملك حق الرد هنا.",
        "cooldown": "انتظر بضع ثوانٍ قبل إنشاء عرض آخر.",
        "status_syntax": "استخدم .status أو .cancel دون معاملات إضافية.", "done": "تم الحفظ.",
        "check_chat": "ربما أُرسلت البطاقة. تحقق من المحادثة قبل إعادة المحاولة.",
    },
}


def language_for(user: dict, default: str = "ru") -> str:
    language = str(user.get("language_code", "")).lower().split("-")[0]
    return language if language in TEXT else default


def tr(language: str, key: str, **kwargs) -> str:
    return TEXT.get(language, TEXT["ru"])[key].format(**kwargs)


def help_text(name: str, language: str) -> str:
    return tr(language, "help", name=escape(name), disclosure=tr(language, "disclosure"))


def render_text(offer: dict, name: str) -> str:
    language = offer["language"]
    gift = f'<a href="{escape(offer["gift_url"], quote=True)}">{escape(offer["gift_name"])}</a>'
    buyer = f'<a href="tg://user?id={offer["buyer_id"]}">{escape(offer["buyer_name"])}</a>'
    price = f'{escape(offer["amount"])} {tr(language, offer["currency"])}'
    lines = [
        f'<b>{escape(name)} · {tr(language, "offer")}</b>',
        "",
        tr(language, "proposes", buyer=buyer, price=price, gift=gift), "",
        tr(language, offer["status"]),
    ]
    if offer["status"] == "pending":
        deadline = datetime.fromtimestamp(offer["expires_at"], tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines.extend(["", tr(language, "expires", deadline=deadline)])
    if offer["status"] == "gift_reported" and offer.get("payment_claimed"):
        lines.extend(["", tr(language, "payment_note")])
    lines.extend(["", tr(language, "disclosure")])
    return "\n".join(lines)


def keyboard(offer: dict) -> dict:
    language = offer["language"]
    def action(key: str) -> dict:
        return {"text": tr(language, key), "callback_data": f'g:{offer["id"]}:{key}'}
    gift_button = {"text": tr(language, "gift"), "url": offer["gift_url"]}
    rows: list[list[dict]] = []
    state = offer["status"]
    if state == "pending":
        rows.append([action("reject"), action("accept")])
    if state in {"accepted", "payment_reported"}:
        rows.append([{"text": tr(language, "buyer"), "url": f'tg://user?id={offer["buyer_id"]}'}, gift_button])
        if not offer.get("payment_claimed"):
            rows.append([action("settled")])
        rows.append([action("sent")])
        if state == "accepted":
            rows.append([action("cancel")])
    elif state == "gift_reported":
        rows.append([action("received")])
        if not offer.get("payment_claimed"):
            rows.append([action("settled")])
    elif state == "gift_received":
        rows.append([action("settled")])
    if state not in {"accepted", "payment_reported"}:
        rows.append([gift_button])
    return {"inline_keyboard": rows}


def card_payload(offer: dict, name: str) -> dict:
    return {
        "business_connection_id": offer["connection_id"], "chat_id": offer["chat_id"],
        "text": render_text(offer, name), "parse_mode": "HTML",
        "link_preview_options": {"url": offer["gift_url"], "prefer_large_media": True, "show_above_text": True},
        "reply_markup": keyboard(offer),
    }
