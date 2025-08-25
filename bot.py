import logging
import aiohttp
import random
from datetime import datetime, timedelta
from telegram import Update, LabeledPrice
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)

# ------------------- НАСТРОЙКИ -------------------
TELEGRAM_TOKEN = "8239579140:AAHoAeeqTx04AO1GuioSPZetJk51k6P4J_g"
HF_TOKEN = "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"
HF_TEXT_MODEL = "tiiuae/falcon-7b-instruct"
HF_IMAGE_MODEL = "stabilityai/stable-diffusion-2"

PAYMENT_PROVIDER_TOKEN = "ВАШ_PAYMENT_PROVIDER_TOKEN"

VIP_BASIC_PRICE = 100  # ₽ разово
VIP_PRO_PRICE = 200    # ₽ в месяц
CURRENCY = "RUB"

BLACKLIST = ["эротика", "сексуальный", "голый", "интим", "нагота"]
SENSITIVE_TOPICS = ["политика", "религия", "национальность", "этнос", "государство", "война"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

users_data = {}  # user_id -> {name, history, mode, vip_level, subscription_expiry}  

# ------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -------------------

def detect_mood(message: str):
    msg = message.lower()
    if any(word in msg for word in ["грустно", "печаль", "уныло", "плохо"]):
        return "sad"
    if any(word in msg for word in ["счастлив", "рад", "круто", "здорово", "отлично"]):
        return "happy"
    if any(word in msg for word in ["злюсь", "сердит", "раздражен"]):
        return "angry"
    return "neutral"

def add_flair(text: str, mood: str, user_name: str):
    emodji_map = {
        "happy": ["😊", "😄", "🌸"],
        "sad": ["😔", "🌧️", "💛"],
        "angry": ["😠", "🔥", "💪"],
        "neutral": ["🙂", "🌿", "✨"]
    }
    compliments = [
        f"{user_name}, ты замечательный!",
        f"Мне нравится общаться с тобой, {user_name}!",
        f"{user_name}, сегодня ты прямо сияешь!"
    ]
    flair = ""
    if mood in emodji_map:
        flair += random.choice(emodji_map[mood]) + " "
    if random.random() < 0.5:
        flair += random.choice(compliments)
    return text + " " + flair

def maybe_add_question(history_length):
    questions = [
        "А можешь рассказать подробнее?",
        "Как ты к этому пришёл?",
        "Что ты об этом думаешь?",
        "Хочешь поделиться ещё чем-нибудь?",
        "Это звучит интересно! Продолжай…"
    ]
    if random.random() < 0.4 or history_length % 5 == 0:
        return " " + random.choice(questions)
    return ""

def maybe_suggest_vip(history_length, vip_level=0):
    if vip_level > 0:
        return None
    if history_length % 5 == 0 and random.random() < 0.2:
        suggestions = [
            "Хочешь, я могу рассказать немного откровеннее? 😏 Попробуй VIP: /buyvip_basic или /buyvip_pro",
            "VIP пользователи получают больше интерактива и картинок! 🌸 /buyvip_basic или /buyvip_pro",
            "Могу немного пошалить в чате с VIP доступом 😄 Попробуй VIP: /buyvip_basic или /buyvip_pro"
        ]
        return random.choice(suggestions)
    return None

def check_vip_status(user_id):
    user_info = users_data.get(user_id, None)
    if not user_info:
        return 0
    vip_level = user_info.get("vip_level", 0)
    if vip_level == 2:  # подписка
        expiry = user_info.get("subscription_expiry")
        if expiry and datetime.now() > expiry:
            user_info["vip_level"] = 0  # подписка закончилась
            return 0
    return user_info.get("vip_level", 0)

# ------------------- ОБРАБОТКА СООБЩЕНИЙ -------------------

async def get_hf_response(prompt: str, mood="neutral", mode="cute", history=None, user_name="Друг", vip_level=0):
    if vip_level == 0:
        for word in ["сексуальный", "интим", "горячо", "эротика"]:
            if word in prompt.lower():
                return "Эта функция доступна только VIP пользователям 😏\nЧтобы открыть доступ: /buyvip_basic или /buyvip_pro"

    if any(topic in prompt.lower() for topic in SENSITIVE_TOPICS):
        return f"Ой, {user_name}, давай оставим эти темы в стороне 😊 Лучше поговорим о чем-то приятном или забавном!"

    full_prompt = f"{user_name} сказал: {prompt}"
    if history:
        full_prompt = "История диалога: " + " | ".join(history) + f"\n{user_name}: {prompt}"

    if mode == "cute":
        full_prompt = f"Отвечай мило, дружелюбно, слегка флиртующе, используй имя пользователя ({user_name}), добавляй эмодзи и дружелюбные фразы: {full_prompt}"
    else:
        full_prompt = f"Отвечай дружелюбно, учитывай настроение ({mood}) пользователя {user_name}: {full_prompt}"

    url = f"https://api-inference.huggingface.co/models/{HF_TEXT_MODEL}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": full_prompt}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                output = await resp.json()
                if isinstance(output, list) and len(output) > 0 and 'generated_text' in output[0]:
                    text = output[0]['generated_text']
                    text = add_flair(text, mood, user_name)
                    text += maybe_add_question(len(history) if history else 0)
                    return text
    return add_flair("Извини, я не смог придумать ответ.", mood, user_name)

async def generate_image(prompt: str, vip_level=0) -> str:
    if vip_level == 0:
        return None
    if any(word in prompt.lower() for word in BLACKLIST):
        return None
    url = f"https://api-inference.huggingface.co/models/{HF_IMAGE_MODEL}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": prompt}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.read()
                filename = "image.png"
                with open(filename, "wb") as f:
                    f.write(data)
                return filename
    return None

# ------------------- ОБРАБОТЧИКИ -------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    message_text = update.message.text

    if user_id not in users_data:
        users_data[user_id] = {"name": user_name, "history": [], "mode": "cute", "vip_level": 0, "subscription_expiry": None}

    user_info = users_data[user_id]
    user_info["history"].append(f"{user_name}: {message_text}")
    if len(user_info["history"]) > 15:
        user_info["history"] = user_info["history"][-15:]

    vip_level = check_vip_status(user_id)
    mood = detect_mood(message_text)
    reply = await get_hf_response(
        message_text,
        mood=mood,
        mode=user_info["mode"],
        history=user_info["history"],
        user_name=user_name,
        vip_level=vip_level
    )

    suggestion = maybe_suggest_vip(len(user_info["history"]), vip_level=vip_level)
    if suggestion:
        reply += f"\n\n{suggestion}"

    user_info["history"].append(f"Бот: {reply}")
    await update.message.reply_text(reply)

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_info = users_data.get(user_id, None)
    if user_info is None:
        await update.message.reply_text("Сначала напиши что-нибудь, чтобы бот запомнил тебя!")
        return

    if not context.args:
        await update.message.reply_text("Напиши /image [твой запрос]")
        return

    prompt = " ".join(context.args)
    vip_level = check_vip_status(user_id)
    if vip_level == 0:
        await update.message.reply_text("Эта функция доступна только VIP пользователям 😏\nЧтобы открыть доступ: /buyvip_basic или /buyvip_pro")
        return

    await update.message.reply_text("Генерирую изображение, подожди...")
    filename = await generate_image(prompt, vip_level=vip_level)
    if filename:
        await update.message.reply_photo(open(filename, "rb"))
    else:
        await update.message.reply_text("Не удалось сгенерировать изображение.")

# ------------------- ПЛАТЁЖНЫЕ ОБРАБОТЧИКИ -------------------

async def buyvip_basic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_invoice(
        title="Базовый VIP (разовый)",
        description="Доступ к лёгкому интимному тексту и генерации изображений",
        payload="vip_basic",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=[LabeledPrice("Базовый VIP", VIP_BASIC_PRICE * 100)],
        start_parameter="vip-basic"
    )

async def buyvip_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_invoice(
        title="Расширенный VIP (подписка на месяц)",
        description="Расширенный флирт, приоритет ответов и все функции базового VIP",
        payload="vip_pro",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=[LabeledPrice("Расширенный VIP", VIP_PRO_PRICE * 100)],
        start_parameter="vip-pro"
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload in ["vip_basic", "vip_pro"]:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Ошибка оплаты, попробуйте ещё раз.")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    payload = update.message.successful_payment.invoice_payload
    if user_id not in users_data:
        users_data[user_id] = {"name": update.message.from_user.first_name, "history": [], "mode": "cute", "vip_level": 0, "subscription_expiry": None}

    if payload == "vip_basic":
        users_data[user_id]["vip_level"] = 1
        await update.message.reply_text("Поздравляю! Вы стали Базовым VIP 😏 Теперь доступны дополнительные функции.")
    elif payload == "vip_pro":
        users_data[user_id]["vip_level"] = 2
        users_data[user_id]["subscription_expiry"] = datetime.now() + timedelta(days=30)
        await update.message.reply_text("Поздравляю! Вы стали Расширенным VIP 😏 Все функции доступны на месяц.")

# ------------------- ЗАПУСК БОТА -------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(CommandHandler("buyvip_basic", buyvip_basic))
    app.add_handler(CommandHandler("buyvip_pro", buyvip_pro))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    print("Бот запущен...")
    app.run_polling()
