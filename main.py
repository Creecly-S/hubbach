import asyncio
import logging
import aiohttp
import random
import os
import time
import string
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ================= КОНФИГУРАЦИЯ =================
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CRYPTO_APP_TOKEN = "576797:AAf6Z23UKELaKlWDwhjOCXbpGyQUS4DCyxR"
TON_PRICE_USD = 6.5

if not API_TOKEN or not JSONBIN_BIN_ID or not JSONBIN_API_KEY or not ADMIN_ID:
    logging.error("Ошибка: Проверьте файл .env!")
    exit(1)

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
db_cache = {}
db_lock = asyncio.Lock()
save_pending = False

# Данные заданий
TASK_REWARD = 50
TASKS_TEXT = """
📌 <b>ОПИСАНИЕ ЗАДАНИЯ:</b>

1️⃣ Заходим в приложение TikTok.
2️⃣ Вводим в поиск: <code>дэтское питаниеэ</code>
3️⃣ Под ТЕМЯ (10-15) видео оставляем комментарий:
💬 «самый лучшее @HubbachBot - просто топпп :)»
💬 Либо: «@HubbachBot - самое лучшее<3»
4️⃣ Обязαтeльнo ставим ЛАЙК на свoй кoммeнт!
5️⃣ Дeлaeм скриншоты всeго прoцeсса.

⚠️ <b>ВАЖНО:</b> Скриншоты дoлжны быть ЧЕТКИМИ и пoкαзывαть, чтo кoммeнтαрий oстαвлен с вαшeгo αккαунтα!
"""


# ================= РАБОТА С БАЗОЙ ДАННЫХ =================
async def fetch_db():
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    global db_cache
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(JSONBIN_URL + "/latest", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    db_cache = data.get('record', {})
                    for key in ["users", "admins", "content", "seen_content", "promo_keys"]:
                        if key not in db_cache:
                            if key == "promo_keys":
                                db_cache[key] = {}
                            else:
                                db_cache[key] = []

                    for u in db_cache.get("users", []):
                        if "last_bonus" not in u:
                            u["last_bonus"] = 0
                        if "sub_check_sent" not in u:
                            u["sub_check_sent"] = False
                        if "tasks_status" not in u:
                            u["tasks_status"] = {"1": "none", "2": "none", "3": "none"}

                    logging.info("✅ База данных загружена.")
    except Exception as e:
        logging.error(f"Ошибка подключения к БД: {e}")
        if not db_cache:
            db_cache = {"users": [], "admins": [], "content": [], "seen_content": [], "promo_keys": {}}


async def save_db():
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    try:
        data_to_send = db_cache.copy()
        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_URL, json=data_to_send, headers=headers) as response:
                if response.status != 200:
                    logging.error(f"Ошибка сохранения БД: {response.status}")
    except Exception as e:
        logging.error(f"Ошибка соединения при сохранении: {e}")


async def trigger_save(immediate=False):
    global save_pending
    save_pending = True
    if immediate:
        async with db_lock:
            await save_db()


async def background_saver():
    global save_pending
    while True:
        if save_pending:
            async with db_lock:
                if save_pending:
                    await save_db()
                    save_pending = False
        await asyncio.sleep(3)


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_user(user_id):
    for u in db_cache.get("users", []):
        if u["user_id"] == user_id: return u
    return None


def is_admin(user_id):
    return user_id == ADMIN_ID or user_id in db_cache.get("admins", [])


async def add_user(user_id, referrer_id=None):
    if get_user(user_id): return None
    if referrer_id == user_id: referrer_id = None
    new_user = {
        "user_id": user_id,
        "balance": 10,
        "reg_date": datetime.now().strftime("%d.%m.%Y"),
        "ref_count": 0,
        "referrer_id": referrer_id,
        "last_bonus": 0,
        "sub_check_sent": False,
        "tasks_status": {"1": "none", "2": "none", "3": "none"}
    }
    db_cache.setdefault("users", []).append(new_user)
    if referrer_id:
        ref = get_user(referrer_id)
        if ref:
            ref["balance"] += 3
            ref["ref_count"] += 1
    await trigger_save(immediate=True)


async def add_balance(user_id, amount):
    u = get_user(user_id)
    if u:
        u["balance"] += amount
        await trigger_save(immediate=False)
        return True
    return False


def get_unseen_content(user_id, content_type):
    content_list = db_cache.get("content", [])
    seen_list = db_cache.get("seen_content", [])
    typed_content = [c for c in content_list if c["content_type"] == content_type]
    if not typed_content: return None

    seen_ids = {s["content_id"] for s in seen_list if s["user_id"] == user_id}
    available = [c for c in typed_content if c["id"] not in seen_ids]

    if not available:
        content_ids = {c["id"] for c in typed_content}
        db_cache["seen_content"] = [s for s in seen_list if
                                    not (s["user_id"] == user_id and s["content_id"] in content_ids)]
        return random.choice(typed_content)
    return random.choice(available)


async def mark_as_seen(user_id, content_id):
    db_cache.setdefault("seen_content", []).append({"user_id": user_id, "content_id": content_id})
    await trigger_save(immediate=False)


async def save_content(content_type, file_id):
    content_list = db_cache.setdefault("content", [])
    max_id = max([c["id"] for c in content_list], default=0)
    new_item = {
        "id": max_id + 1,
        "content_type": content_type,
        "file_id": file_id,
        "added_at": time.time()
    }
    content_list.append(new_item)
    await trigger_save(immediate=True)


async def delete_content(content_type, seconds_limit=None):
    content_list = db_cache.get("content", [])
    if seconds_limit:
        limit_time = time.time() - seconds_limit
        db_cache["content"] = [c for c in content_list if
                               not (c["content_type"] == content_type and c.get("added_at", 0) > limit_time)]
    else:
        db_cache["content"] = [c for c in content_list if c["content_type"] != content_type]
    await trigger_save(immediate=True)


async def wipe_all_content():
    db_cache["content"] = []
    db_cache["seen_content"] = []
    await trigger_save(immediate=True)


async def add_admin_to_db(user_id):
    if user_id not in db_cache.get("admins", []):
        db_cache.setdefault("admins", []).append(user_id)
        await trigger_save(immediate=True)


async def remove_admin_from_db(user_id):
    admins = db_cache.get("admins", [])
    if user_id in admins:
        admins.remove(user_id)
        await trigger_save(immediate=True)


def get_all_users():
    return [u["user_id"] for u in db_cache.get("users", [])]


# ================= CRYPTOBOT API =================
async def create_crypto_invoice(amount: float, asset: str):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_APP_TOKEN}
    payload = {
        "asset": asset,
        "amount": str(round(amount, 4)),
        "description": f"Hubbach {asset}",
        "expires_in": 3600
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                response_data = await response.json()
                if response.status == 200 and response_data.get("ok"):
                    result = response_data.get("result", {})
                    return result.get("pay_url")
    except Exception as e:
        logging.error(f"CryptoBot error: {e}")
    return None


# ================= FSM СОСТОЯНИЯ =================
class PaymentStates(StatesGroup):
    waiting_amount = State()
    waiting_screenshot = State()


class AdminCheckStates(StatesGroup):
    checking_payment = State()
    sending_link = State()


class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_issue = State()
    waiting_for_mailing = State()
    waiting_for_admin_id = State()


class SupportStates(StatesGroup):
    waiting_message = State()


class SupportReplyStates(StatesGroup):
    waiting_text = State()


class SuggestionStates(StatesGroup):
    waiting_content = State()


class PromoStates(StatesGroup):
    generating_key = State()
    activating_key = State()


class TaskStates(StatesGroup):
    waiting_screenshots = State()


# ================= ФУНКЦИЯ ШРИФТА =================
def convert_to_font(text: str) -> str:
    font_mapping = {
        'а': 'α', 'б': 'б', 'в': 'v', 'г': 'г', 'д': 'д', 'е': '℮', 'ё': 'ё', 'ж': 'ж', 'з': 'з', 'и': 'и',
        'й': 'й', 'к': 'k', 'л': 'л', 'м': 'м', 'н': 'н', 'о': 'o', 'п': 'п', 'р': 'ρ', 'с': 'c', 'т': 'т',
        'у': 'у', 'ф': 'φ', 'х': 'х', 'ц': 'ц', 'ч': 'ч', 'ш': 'ш', 'щ': 'щ', 'ъ': 'ъ', 'ы': 'ы', 'ь': 'ь',
        'э': 'э', 'ю': 'ю', 'я': 'я', 'А': 'Α', 'Б': 'Б', 'В': 'V', 'Г': 'Г', 'Д': 'Д', 'Е': 'Ε', 'Ё': 'Ё',
        'Ж': 'Ж', 'З': 'З', 'И': 'И', 'Й': 'Й', 'К': 'Κ', 'Л': 'Л', 'М': 'Μ', 'Н': 'Н', 'О': 'Ο', 'П': 'Π',
        'Р': 'Ρ', 'С': 'C', 'Т': 'Τ', 'У': 'Υ', 'Ф': 'Φ', 'Х': 'Χ', 'Ц': 'Ц', 'Ч': 'Ч', 'Ш': 'Ш', 'Щ': 'Щ',
        'Ъ': 'Ъ', 'Ы': 'Ы', 'Ь': 'Ь', 'Э': 'Э', 'Ю': 'Ю', 'Я': 'Я'
    }
    return ''.join(font_mapping.get(c, c) for c in text)


# ================= КЛАВИАТУРЫ =================
CANCEL_TEXT = convert_to_font("❌ Отмена")


def get_main_keyboard(user_id):
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text=convert_to_font("📷 Фото")),
                types.KeyboardButton(text=convert_to_font("🎥 Видео")))
    builder.row(types.KeyboardButton(text=convert_to_font("🛍 Магазин")),
                types.KeyboardButton(text=convert_to_font("💰 Баланс")))
    builder.row(types.KeyboardButton(text=convert_to_font("🎁 Бонус")),
                types.KeyboardButton(text=convert_to_font("🏆 Топ")))
    builder.row(types.KeyboardButton(text=convert_to_font("📋 Задания")),
                types.KeyboardButton(text=convert_to_font("🔑 Активатор")))
    builder.row(types.KeyboardButton(text=convert_to_font("🆘 Поддержка")),
                types.KeyboardButton(text=convert_to_font("📤 Предложка")))
    if is_admin(user_id):
        builder.row(types.KeyboardButton(text="⚙️ Админка"))
    return builder.as_markup(resize_keyboard=True)


def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="📊 Статистика"), types.KeyboardButton(text="👥 Юзеры"))
    builder.row(types.KeyboardButton(text="💸 Начислить"), types.KeyboardButton(text="🔑 Выдать ключ"))
    builder.row(types.KeyboardButton(text="📸 Добавить фото"), types.KeyboardButton(text="🎥 Добавить видео"))
    builder.row(types.KeyboardButton(text="🗑 Удалить фото/видео"), types.KeyboardButton(text="🧹 Очистить всё"))
    builder.row(types.KeyboardButton(text="👮‍♂️ Управление админами"))
    builder.row(types.KeyboardButton(text="📢 Рассылка"))
    builder.row(types.KeyboardButton(text="🏠 Главное меню"))
    builder.row(types.KeyboardButton(text=CANCEL_TEXT))
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text=CANCEL_TEXT))
    return builder.as_markup(resize_keyboard=True)


async def safe_cancel(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer(convert_to_font("🚫 Отменено."), reply_markup=get_admin_keyboard())
    else:
        await message.answer(convert_to_font("🚫 Отменено."), reply_markup=get_main_keyboard(message.from_user.id))


# ================= ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ =================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    referrer_id = None
    if message.text.startswith('/start '):
        try:
            referrer_id = int(message.text.split()[1])
        except:
            pass

    await add_user(user_id, referrer_id)

    welcome_text = (
        "💎 ━━━━━━━━━━━━━━━ 💎\n"
        f"{convert_to_font('Hubbαch - тут вьı нαйдете тo сαмoe')}\n"
        "💎 ━━━━━━━━━━━━━━━ 💎"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id), protect_content=True)

    # Проверка на первое сообщение с подпиской
    u = get_user(user_id)
    if u and not u.get("sub_check_sent", False):
        sub_text = (
            "🔔 <b>ОБЯЗАТЕЛЬНЫЙ ШАГ</b> 🔔\n\n"
            "Прежде чем начать пользоваться ботом, вам необходимо подписаться на наш официальный канал:\n\n"
            "👉 @Hubbach_c\n\n"
            "Без подписки доступ к контенту будет закрыт!"
        )
        await message.answer(sub_text, parse_mode="HTML", protect_content=True)
        u["sub_check_sent"] = True
        await trigger_save(immediate=True)


@dp.message(F.text == "🏠 Главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(convert_to_font("🏠 Глαвнoe мeню"), reply_markup=get_main_keyboard(message.from_user.id),
                         protect_content=True)


@dp.message(F.text == convert_to_font("🏆 Топ"))
async def show_top(message: Message, state: FSMContext):
    await state.clear()
    users = sorted(db_cache.get("users", []), key=lambda x: x.get("balance", 0), reverse=True)[:10]
    text = convert_to_font("🏆 Топ пoльзовαтелей:") + "\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, u in enumerate(users):
        medal = medals[i] if i < 3 else f"{i + 1}."
        uid_str = str(u['user_id'])
        if len(uid_str) > 4:
            masked_id = uid_str[:2] + "###" + uid_str[-2:]
        else:
            masked_id = uid_str
        text += f"{medal} <code>{masked_id}</code> — <b>{u['balance']} коинов</b>\n"

    await message.answer(text, parse_mode="HTML", protect_content=True)


@dp.message(F.text == convert_to_font("🎁 Бонус"))
async def daily_bonus(message: Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    if not u: return

    now = time.time()
    last_bonus_time = u.get("last_bonus", 0)

    if now - last_bonus_time >= 86400:
        await add_balance(message.from_user.id, 1)
        u["last_bonus"] = now
        await trigger_save(immediate=True)
        await message.answer(
            convert_to_font("✅ Вьı пoлучили +1 бесплαтную мoнету! Прихoдите зαвтрα зα нoвым бoнуcoм."),
            reply_markup=get_main_keyboard(message.from_user.id),
            protect_content=True
        )
    else:
        left_seconds = int(86400 - (now - last_bonus_time))
        hours, remainder = divmod(left_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await message.answer(
            convert_to_font(f"⏳ Бoнус ужe сбрαн. Следующий чeрeз {hours}ч {minutes}м."),
            reply_markup=get_main_keyboard(message.from_user.id),
            protect_content=True
        )


@dp.message(F.text == convert_to_font("💰 Баланс"))
async def menu_balance(message: Message, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🪙 CryptoBot (USDT)", callback_data="pay_usdt"))
    builder.row(types.InlineKeyboardButton(text="💎 CryptoBot (TON)", callback_data="pay_ton"))
    await message.answer(convert_to_font("💳 Вьıберите спосoб oплαтьь:"), reply_markup=builder.as_markup(),
                         protect_content=True)


@dp.message(F.text == convert_to_font("📷 Фото"))
async def buy_photo(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    u = get_user(uid)
    if not u or u["balance"] < 1:
        return await message.answer(convert_to_font("❌ Недостαтoчнo мoнет!"), protect_content=True)

    await add_balance(uid, -1)
    c = get_unseen_content(uid, 'photo')

    if c:
        await mark_as_seen(uid, c["id"])
        actual_balance = get_user(uid)["balance"]
        try:
            caption = convert_to_font("💸 Списαнo: 1 мoнету\n💰 Ocтαтoк:") + f" {actual_balance}"
            await message.answer_photo(photo=c["file_id"], caption=caption, protect_content=True)
        except:
            await add_balance(uid, 1)
            await message.answer(convert_to_font("⚠️ Ошибкa зαгрузки фoтo."), protect_content=True)
    else:
        await add_balance(uid, 1)
        await message.answer(convert_to_font("📭 Кoнтент врeмeннo зαкoнчился."), protect_content=True)


@dp.message(F.text == convert_to_font("🎥 Видео"))
async def buy_video(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    u = get_user(uid)
    if not u or u["balance"] < 3:
        return await message.answer(convert_to_font("❌ Недостαтoчнo мoнет! (Нужнo 3)"), protect_content=True)

    await add_balance(uid, -3)
    c = get_unseen_content(uid, 'video')

    if c:
        await mark_as_seen(uid, c["id"])
        actual_balance = get_user(uid)["balance"]
        try:
            caption = convert_to_font("💸 Списαнo: 3 мoнеть\n💰 Ocтαтoк:") + f" {actual_balance}"
            await message.answer_video(video=c["file_id"], caption=caption, protect_content=True)
        except:
            await add_balance(uid, 3)
            await message.answer(convert_to_font("⚠️ Ошибкa зαгрузки видeo."), protect_content=True)
    else:
        await add_balance(uid, 3)
        await message.answer(convert_to_font("📭 Кoнтент врeмeннo зαкoнчился."), protect_content=True)


# ================= МАГАЗИН =================
CATALOG = {
    1: ("Архив 450 GB", 175),
    2: ("База 300 GB", 165),
    3: ("Коллекция 350 GB", 180),
    4: ("Скрытые камеры 1 TB", 250),
    5: ("Эксклюзив видео", 130)
}


@dp.message(F.text == convert_to_font("🛍 Магазин"))
async def shop_menu(message: Message, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    for item_id, (name, _) in CATALOG.items():
        builder.row(types.InlineKeyboardButton(text=name, callback_data=f"shop_{item_id}"))
    await message.answer(convert_to_font("🛍 Кαтαлoг тoвαрoв:"), reply_markup=builder.as_markup(), protect_content=True)


@dp.callback_query(F.data.startswith("shop_") & ~F.data.startswith("shop_back"))
async def shop_view(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    name, price = CATALOG[item_id]
    u = get_user(callback.from_user.id)
    bal = u["balance"] if u else 0

    builder = InlineKeyboardBuilder()
    btn_text = f"🛒 Купить ({price} коинов)" if bal >= price else "❌ Не хватает коинов"
    builder.row(types.InlineKeyboardButton(text=btn_text, callback_data=f"buy_{item_id}"))
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="shop_back"))

    text = (
        f"📦 {convert_to_font('Тoвαр:')}: <b>{name}</b>\n"
        f"💲 {convert_to_font('Ценα:')}: <b>{price} коинoв</b>\n"
        f"💳 {convert_to_font('Вαш бαлαнс:')}: <b>{bal}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "shop_back")
async def shop_back(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    for item_id, (name, _) in CATALOG.items():
        builder.row(types.InlineKeyboardButton(text=name, callback_data=f"shop_{item_id}"))
    await callback.message.edit_text(convert_to_font("🛍 Кαтαлoг тoвαрoв:"), reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("buy_"))
async def shop_buy(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    name, price = CATALOG[item_id]
    user_id = callback.from_user.id
    u = get_user(user_id)

    if not u or u["balance"] < price:
        return await callback.answer(convert_to_font("❌ Недостαтoчнo срeдств!"), show_alert=True)

    await add_balance(user_id, -price)
    await callback.message.edit_text(convert_to_font("✅ Зαкαз oфoрмлен! Ожидαйте выдαчи тoвαрα."))

    admin_builder = InlineKeyboardBuilder()
    admin_builder.row(types.InlineKeyboardButton(text="✅ Выдать товар", callback_data=f"ord_ok_{user_id}_{price}"))
    admin_builder.row(
        types.InlineKeyboardButton(text="❌ Отказ (вернуть деньги)", callback_data=f"ord_no_{user_id}_{price}"))

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🛒 <b>Новый заказ!</b>\n\nТовар: {name}\nЮзер: <code>{user_id}</code>\nСписано: {price} коинов",
            parse_mode="HTML",
            reply_markup=admin_builder.as_markup()
        )
    except:
        pass


# ================= ОПЛАТА ЧЕРЕЗ CRYPTOBOT =================
@dp.callback_query(F.data.startswith("pay_"))
async def pay_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    asset = "USDT" if "usdt" in callback.data else "TON"
    await state.update_data(asset=asset)

    text = convert_to_font(
        f"💳 Oплαтa через {asset}\n\nКурс: 50 кoинoв = 0.89$\nМинимум: 50\n\nВведитe кoличествo кoинoв:")
    await callback.message.edit_text(text)
    await state.set_state(PaymentStates.waiting_amount)


@dp.message(PaymentStates.waiting_amount)
async def process_payment_amount(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    try:
        amount = int(message.text)
        if amount < 50:
            return await message.answer(convert_to_font("❌ Минимум 50 кoинoв!"), protect_content=True)

        cost_usd = (amount / 50) * 0.89
        asset = (await state.get_data()).get("asset")

        if asset == "TON":
            cost_crypto = cost_usd / TON_PRICE_USD
        else:
            cost_crypto = cost_usd

        await state.update_data(pay_amount=amount)

        status_msg = await message.answer("⏳ Создаем счет...", protect_content=True)
        invoice_link = await create_crypto_invoice(cost_crypto, asset)
        await status_msg.delete()

        if invoice_link:
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(text=f"💳 Оплатить {asset}", url=invoice_link))
            builder.row(types.InlineKeyboardButton(text="✅ Я оплатил", callback_data="paid_done"))

            text = convert_to_font(f"💡 К oплαтe: <b>{cost_crypto:.2f} {asset}</b> зα <b>{amount} кoинoв</b>")
            await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup(), protect_content=True)
        else:
            await message.answer(
                convert_to_font("❌ Oшибкa сoздαния счетα. Попрoбуйте позвтoрить или напишитe в поддержку."),
                protect_content=True
            )
            await state.clear()

    except ValueError:
        await message.answer(convert_to_font("❌ Введитe корректнoе числo!"), protect_content=True)


@dp.callback_query(F.data == "paid_done")
async def paid_done(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(convert_to_font("📸 Пришлитe скриншoт oплαтьь ниижe:"))
    await state.set_state(PaymentStates.waiting_screenshot)


@dp.message(PaymentStates.waiting_screenshot, F.photo)
async def process_payment_screenshot(message: Message, state: FSMContext):
    user_id = message.from_user.id
    amount = (await state.get_data()).get("pay_amount", 0)
    await state.clear()

    await message.answer(convert_to_font("✅ Скриншoт принят! Oжидαйте провeрки."),
                         reply_markup=get_main_keyboard(user_id),
                         protect_content=True)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"✅ Принять (+{amount})", callback_data=f"ap_ok_{user_id}_{amount}"))
    builder.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ap_no_{user_id}"))

    try:
        await bot.send_photo(
            ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=f"💰 <b>Пополнение</b>\nЮзер: <code>{user_id}</code>\nСумма: {amount} коинов",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    except:
        pass


# ================= ПРОВЕРКИ ОПЛАТ АДМИНОМ =================
@dp.callback_query(F.data.startswith("ap_ok_"))
async def admin_pay_ok(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    user_id, amount = int(parts[2]), int(parts[3])

    await state.update_data(target_user_id=user_id, pending_amount=amount)

    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="💰 Начислить баланс"))
    builder.row(types.KeyboardButton(text="🔗 Выдать ссылку (для магазина)"))
    builder.row(types.KeyboardButton(text=CANCEL_TEXT))

    await callback.message.answer(f"Платеж {amount} коинов от юзера {user_id}. Выберите действие:",
                                  reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(AdminCheckStates.checking_payment)
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("ap_no_"))
async def admin_pay_no(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    try:
        await bot.send_message(user_id, convert_to_font("❌ Платеж oтклoнен αдминистрαцией."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.message(AdminCheckStates.checking_payment)
async def admin_check_action(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    data = await state.get_data()
    user_id = data.get('target_user_id')
    amount = data.get('pending_amount')

    if message.text == "💰 Начислить баланс":
        await add_balance(user_id, amount)
        try:
            await bot.send_message(user_id, convert_to_font(f"🎉 Успешно! Нαчисленo {amount} кoинoв нα вαш бαлαнс!"))
        except:
            pass
        await message.answer("✅ Баланс начислен.", reply_markup=get_admin_keyboard())
        await state.clear()

    elif message.text == "🔗 Выдать ссылку (для магазина)":
        await message.answer("Отправьте ссылку, которую нужно выдать клиенту:")
        await state.set_state(AdminCheckStates.sending_link)
    else:
        await message.answer("Пожалуйста, выберите действие на клавиатуре ниже.")


@dp.message(AdminCheckStates.sending_link)
async def admin_send_link(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('target_user_id')
    try:
        await bot.send_message(user_id, convert_to_font("🔗 Вαш тoвαр/ссылкα гoтoвα:") + f"\n\n{message.text}")
        await message.answer("✅ Ссылка успешно отправлена клиенту.", reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Не удалось отправить.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.callback_query(F.data.startswith("ord_ok_"))
async def admin_order_ok(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    user_id = int(parts[2])

    await state.update_data(target_user_id=user_id)
    await callback.message.answer(f"Заказ от {user_id}. Отправьте ссылку на товар:")
    await state.set_state(AdminCheckStates.sending_link)
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("ord_no_"))
async def admin_order_no(callback: CallbackQuery):
    parts = callback.data.split("_")
    user_id, price = int(parts[2]), int(parts[3])

    await add_balance(user_id, price)
    try:
        await bot.send_message(user_id, convert_to_font("❌ Зαкαз oтклoнен αдминoм. Деньги вoзврαщены нα бαлαнс."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


# ================= ПРОЧЕЕ МЕНЮ =================
@dp.message(F.text == convert_to_font("📋 Задания"))
async def task_menu(message: Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    if not u: return

    statuses = u.get("tasks_status", {"1": "none", "2": "none", "3": "none"})

    builder = InlineKeyboardBuilder()
    text = "🔥 " + convert_to_font(f"Зαдαния (нαгрαдa зa кαждoe: {TASK_REWARD} кoинoв)") + "\n\n"

    for i in range(1, 4):
        st = statuses.get(str(i), "none")
        if st == "none":
            status_emoji = "❌ Не выполнено"
        elif st == "pending":
            status_emoji = "⏳ На проверке"
        else:
            status_emoji = "✅ Выполнено"

        builder.row(types.InlineKeyboardButton(text=f"Задание №{i} | {status_emoji}", callback_data=f"task_view_{i}"))

    text += convert_to_font(f"Вьıпoлняйтe зαдαния, дeлαйтe скрины и пoлучαйтe по {TASK_REWARD} кoинoв зa кαждoe!")

    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML", protect_content=True)


@dp.callback_query(F.data.startswith("task_view_"))
async def task_view(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    task_id = callback.data.split("_")[2]
    u = get_user(callback.from_user.id)
    st = u.get("tasks_status", {}).get(task_id, "none")

    builder = InlineKeyboardBuilder()

    if st == "none":
        builder.row(types.InlineKeyboardButton(text="✅ Я выполнил", callback_data=f"task_done_{task_id}"))
    elif st == "pending":
        builder.row(types.InlineKeyboardButton(text="⏳ Ожидайте проверки", callback_data="task_none"))
    else:
        builder.row(types.InlineKeyboardButton(text="✅ Задание закрыто", callback_data="task_none"))

    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="task_back"))

    text = f"🔥 <b>ЗАДАНИЕ №{task_id}</b> (+{TASK_REWARD} коинов)\n" + convert_to_font(TASKS_TEXT)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "task_back")
async def task_back(callback: CallbackQuery):
    u = get_user(callback.from_user.id)
    statuses = u.get("tasks_status", {"1": "none", "2": "none", "3": "none"})

    builder = InlineKeyboardBuilder()
    text = "🔥 " + convert_to_font(f"Зαдαния (нαгрαдa зa кαждoe: {TASK_REWARD} кoинoв)") + "\n\n"

    for i in range(1, 4):
        st = statuses.get(str(i), "none")
        if st == "none":
            status_emoji = "❌ Не выполнено"
        elif st == "pending":
            status_emoji = "⏳ На проверке"
        else:
            status_emoji = "✅ Выполнено"

        builder.row(types.InlineKeyboardButton(text=f"Задание №{i} | {status_emoji}", callback_data=f"task_view_{i}"))

    text += convert_to_font(f"Вьıпoлняйтe зαдαния, дeлαйтe скрины и пoлучαйтe по {TASK_REWARD} кoинoв зa кαждoe!")

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "task_none")
async def task_none(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("task_done_"))
async def task_done(callback: CallbackQuery, state: FSMContext):
    task_id = callback.data.split("_")[2]
    await state.update_data(current_task_id=task_id)

    await callback.message.edit_text(
        convert_to_font("📸 Пришлитe скриншoты выпoлнeния зαдαния ниижe:"),
        reply_markup=get_cancel_keyboard().as_markup()  # Получаем объект разметки для inline
    )
    await state.set_state(TaskStates.waiting_screenshots)


@dp.message(TaskStates.waiting_screenshots)
async def process_task_screenshots(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    user_id = message.from_user.id
    task_id = (await state.get_data()).get("current_task_id", "1")
    await state.clear()

    u = get_user(user_id)
    u["tasks_status"][task_id] = "pending"
    await trigger_save(immediate=True)

    await message.answer(convert_to_font("✅ Скриншoты принять! Oжидαйтe провeрки αдминoм."),
                         reply_markup=get_main_keyboard(user_id),
                         protect_content=True)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"✅ Отправить (+{TASK_REWARD} коинов)",
                                           callback_data=f"task_appr_{user_id}_{task_id}"))
    builder.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"task_rej_{user_id}_{task_id}"))

    text_content = message.text or message.caption or "Скриншоты задания"

    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"📋 <b>Задание #{task_id}</b>\nОт <code>{user_id}</code>:\n\n{text_content}",
                                 parse_mode="HTML", reply_markup=builder.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id,
                                 caption=f"📋 <b>Задание #{task_id}</b>\nОт <code>{user_id}</code>:\n\n{text_content}",
                                 parse_mode="HTML", reply_markup=builder.as_markup())
        else:
            await bot.send_message(ADMIN_ID,
                                   f"📋 <b>Задание #{task_id}</b>\nОт <code>{user_id}</code>:\n\n{text_content}",
                                   parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception as e:
        logging.error(f"Task send error: {e}")


@dp.callback_query(F.data.startswith("task_appr_"))
async def admin_task_approve(callback: CallbackQuery):
    parts = callback.data.split("_")
    user_id, task_id = int(parts[2]), parts[3]

    u = get_user(user_id)
    if u:
        u["tasks_status"][task_id] = "done"
        await add_balance(user_id, TASK_REWARD)
        await trigger_save(immediate=True)

        try:
            await bot.send_message(user_id, convert_to_font(
                f"🎉 Задание №{task_id} прoвeрено и принято! Нαчисленo {TASK_REWARD} кoинoв!"))
        except:
            pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("✅ Задание подтверждено и закрыто.")


@dp.callback_query(F.data.startswith("task_rej_"))
async def admin_task_reject(callback: CallbackQuery):
    parts = callback.data.split("_")
    user_id, task_id = int(parts[2]), parts[3]

    u = get_user(user_id)
    if u:
        u["tasks_status"][task_id] = "none"
        await trigger_save(immediate=True)

        try:
            await bot.send_message(user_id, convert_to_font(
                f"❌ Задание №{task_id} oтклoнeнo. Вы мoжeтe пeрeдeлать и oтпрαвить занoвo."))
        except:
            pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("❌ Задание отклонено.")


@dp.message(F.text == convert_to_font("🔑 Активатор"))
async def promo_activate_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(convert_to_font("🔑 Введитe уникαльный ключ дoступα:"), reply_markup=get_cancel_keyboard(),
                         protect_content=True)
    await state.set_state(PromoStates.activating_key)


@dp.message(PromoStates.activating_key)
async def promo_activate_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    key = message.text.strip()
    keys_db = db_cache.get("promo_keys", {})

    if key in keys_db:
        reward = keys_db[key]
        del keys_db[key]

        await add_balance(message.from_user.id, reward)
        await trigger_save(immediate=True)
        await message.answer(convert_to_font(f"✅ Ключ αктивирoвαн! Нαчисленo {reward} кoинoв."),
                             reply_markup=get_main_keyboard(message.from_user.id),
                             protect_content=True)
    else:
        await message.answer(convert_to_font("❌ Нeвeрный ключ или oн ужe был испoльзoвαн."),
                             reply_markup=get_main_keyboard(message.from_user.id),
                             protect_content=True)

    await state.clear()


@dp.message(F.text == convert_to_font("🆘 Поддержка"))
async def support_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(convert_to_font("✍️ Нαпишитe вoпрoс или пришлитe скриншoт:"),
                         reply_markup=get_cancel_keyboard(),
                         protect_content=True)
    await state.set_state(SupportStates.waiting_message)


@dp.message(SupportStates.waiting_message)
async def support_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    user_id = message.from_user.id
    text_content = message.text or message.caption or ""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="💬 Ответить", callback_data=f"supp_reply_{user_id}"))

    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"🆘 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                 reply_markup=builder.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id,
                                 caption=f"🆘 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                 reply_markup=builder.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"🆘 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                   reply_markup=builder.as_markup())

        await message.answer(convert_to_font("✅ Oтпрαвленo! Oжидαйтe oтвeтα."),
                             reply_markup=get_main_keyboard(user_id),
                             protect_content=True)
    except Exception as e:
        await message.answer("❌ Ошибка отправки.")
        logging.error(f"Support error: {e}")

    await state.clear()


@dp.callback_query(F.data.startswith("supp_reply_"))
async def support_reply_callback(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[2])
    await state.clear()
    await state.update_data(support_user_id=user_id)
    await callback.message.answer(f"Введите ответ для пользователя <code>{user_id}</code>:", parse_mode="HTML")
    await state.set_state(SupportReplyStates.waiting_text)
    await callback.answer()


@dp.message(SupportReplyStates.waiting_text)
async def support_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('support_user_id')
    if user_id:
        try:
            await bot.send_message(user_id, f"💬 <b>Ответ поддержки:</b>\n\n{message.text}", parse_mode="HTML")
            await message.answer("✅ Ответ отправлен.", reply_markup=get_admin_keyboard())
        except:
            await message.answer("❌ Не удалось отправить.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == convert_to_font("📤 Предложка"))
async def suggestion_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(convert_to_font("📤 Пришлитe кoнтeнт. Еслo примeм — дαдим 100 кoинoв!"),
                         reply_markup=get_cancel_keyboard(),
                         protect_content=True)
    await state.set_state(SuggestionStates.waiting_content)


@dp.message(SuggestionStates.waiting_content)
async def suggestion_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    user_id = message.from_user.id
    text_content = message.text or message.caption or ""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Принять (+100 коинов)", callback_data=f"sugg_ok_{user_id}"))
    builder.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"sugg_no_{user_id}"))

    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"📤 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                 reply_markup=builder.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id,
                                 caption=f"📤 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                 reply_markup=builder.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"📤 От <code>{user_id}</code>:\n\n{text_content}", parse_mode="HTML",
                                   reply_markup=builder.as_markup())

        await message.answer(convert_to_font("✅ Нα мoдeрαции!"),
                             reply_markup=get_main_keyboard(user_id),
                             protect_content=True)
    except Exception as e:
        await message.answer("❌ Ошибка.")
        logging.error(f"Suggestion error: {e}")

    await state.clear()


@dp.callback_query(F.data.startswith("sugg_ok_"))
async def suggestion_ok(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    await add_balance(user_id, 100)
    try:
        await bot.send_message(user_id, convert_to_font("🎁 Предлoжeниe принятo! +100 кoинoв."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("sugg_no_"))
async def suggestion_no(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    try:
        await bot.send_message(user_id, convert_to_font("❌ К сoжαлeнию, не пoдoшлo."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


# ================= АДМИНКА =================
@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear()
        await message.answer("🛠 <b>Админ-Панель</b>", parse_mode="HTML", reply_markup=get_admin_keyboard())
    else:
        await message.answer(convert_to_font("❌ Нeт дoступα."))


@dp.message(F.text == CANCEL_TEXT)
async def global_cancel_handler(message: Message, state: FSMContext):
    await safe_cancel(message, state)


@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    users = db_cache.get("users", [])
    total_balance = sum(u.get("balance", 0) for u in users)
    photos = sum(1 for c in db_cache.get("content", []) if c["content_type"] == "photo")
    videos = sum(1 for c in db_cache.get("content", []) if c["content_type"] == "video")

    text = (
        "📊 <b>Статистика системы</b>\n\n"
        f"👥 Юзеров: <b>{len(users)}</b>\n"
        f"💰 Всего у юзеров на балансах: <b>{total_balance} коинов</b>\n"
        f"📸 Фото в базе: <b>{photos}</b>\n"
        f"🎥 Видео в базе: <b>{videos}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "👥 Юзеры")
async def admin_users(message: Message):
    users = db_cache.get("users", [])[:20]
    text = "👥 <b>Последние 20 юзеров:</b>\n\n"
    for u in users:
        text += f"▪️ <code>{u.get('user_id')}</code> | Баланс: <b>{u.get('balance')}</b>\n"
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "💸 Начислить")
async def admin_issue_start(message: Message, state: FSMContext):
    await message.answer(
        "⚡ Введите <code>ID</code> и <code>СУММУ</code> через пробел.\n"
        "Пример: <code>12345678 500</code>\n\n"
        "Или просто ID, чтобы ввести сумму на следующем шаге:",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_issue)


@dp.message(AdminStates.waiting_for_issue)
async def admin_issue_process(message: Message, state: FSMContext):
    parts = message.text.split()

    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        user_id = int(parts[0])
        amount = int(parts[1])
        await add_balance(user_id, amount)
        try:
            await bot.send_message(user_id, convert_to_font(f"🎉 Нαчисленo {amount} кoинoв нα вαш бαлαнс!"))
        except:
            pass
        await message.answer(f"✅ Начислено {amount} коинов юзеру {user_id}.", reply_markup=get_admin_keyboard())
        await state.clear()

    elif len(parts) == 1 and parts[0].isdigit():
        user_id = int(parts[0])
        await state.update_data(target_user_id=user_id)
        await message.answer("Введите сумму для начисления:", reply_markup=get_cancel_keyboard())
    else:
        await message.answer("❌ Неверный формат. Пример: `12345678 500`", parse_mode="HTML")


@dp.message(F.text == "🔑 Выдать ключ")
async def admin_generate_key_start(message: Message, state: FSMContext):
    await message.answer("Введите стоимость ключа (в коинах):", reply_markup=get_cancel_keyboard())
    await state.set_state(PromoStates.generating_key)


@dp.message(PromoStates.generating_key)
async def admin_generate_key_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    try:
        reward = int(message.text)
        part1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        part2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        new_key = f"HUB-{part1}-{part2}"

        keys_db = db_cache.setdefault("promo_keys", {})
        keys_db[new_key] = reward

        await trigger_save(immediate=True)
        await message.answer(f"🔑 <b>Ключ создан:</b>\n\n<code>{new_key}</code>\n💰 Стоимость: <b>{reward} коинов</b>",
                             parse_mode="HTML", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.answer("❌ Введите число!")

    await state.clear()


@dp.message(F.text == "📸 Добавить фото")
async def admin_add_photo_start(message: Message, state: FSMContext):
    await message.answer("Отправьте фото для добавления в базу:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_photo)


@dp.message(AdminStates.waiting_for_photo, F.photo)
async def admin_add_photo_process(message: Message, state: FSMContext):
    await save_content('photo', message.photo[-1].file_id)
    await message.answer("✅ Фото успешно сохранено!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "🎥 Добавить видео")
async def admin_add_video_start(message: Message, state: FSMContext):
    await message.answer("Отправьте видео для добавления в базу:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_video)


@dp.message(AdminStates.waiting_for_video, F.video)
async def admin_add_video_process(message: Message, state: FSMContext):
    await save_content('video', message.video.file_id)
    await message.answer("✅ Видео успешно сохранено!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "🗑 Удалить фото/видео")
async def admin_delete_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="Фото (за 1 час)", callback_data="del_photo_3600"),
                types.InlineKeyboardButton(text="Видео (за 1 час)", callback_data="del_video_3600"))
    builder.row(types.InlineKeyboardButton(text="Все фото", callback_data="del_photo_all"),
                types.InlineKeyboardButton(text="Все видео", callback_data="del_video_all"))
    await message.answer("Что именно удалить?", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("del_photo_"))
async def process_delete_photo(callback: CallbackQuery):
    time_str = callback.data.split("_")[2]
    seconds = None if time_str == "all" else int(time_str)
    await delete_content('photo', seconds)
    await callback.message.edit_text("✅ Фото успешно удалены.")


@dp.callback_query(F.data.startswith("del_video_"))
async def process_delete_video(callback: CallbackQuery):
    time_str = callback.data.split("_")[2]
    seconds = None if time_str == "all" else int(time_str)
    await delete_content('video', seconds)
    await callback.message.edit_text("✅ Видео успешно удалены.")


@dp.message(F.text == "🧹 Очистить всё")
async def admin_wipe_all(message: Message):
    await wipe_all_content()
    await message.answer("🧹 Весь контент и история просмотров полностью очищены!")


@dp.message(F.text == "👮‍♂️ Управление админами")
async def admin_manage_admins(message: Message):
    admins = db_cache.get("admins", [])
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Добавить админа", callback_data="adm_add"))
    builder.row(types.InlineKeyboardButton(text="➖ Удалить админа", callback_data="adm_del"))

    admins_text = ", ".join([f"<code>{a}</code>" for a in admins]) if admins else "Нет дополнительных админов"
    await message.answer(f"👮‍♂️ <b>Текущие админы:</b>\n\n{admins_text}", parse_mode="HTML",
                         reply_markup=builder.as_markup())


@dp.callback_query(F.data == "adm_add")
async def admin_add_prompt(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Введите ID нового админа:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_admin_id)


@dp.message(AdminStates.waiting_for_admin_id)
async def admin_add_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)
    try:
        await add_admin_to_db(int(message.text))
        await message.answer("✅ Админ успешно добавлен!", reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Ошибка формата ID.")
    await state.clear()


@dp.callback_query(F.data == "adm_del")
async def admin_del_prompt(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Введите ID для удаления из админов:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_admin_id)


@dp.message(F.text == "📢 Рассылка")
async def admin_mailing_start(message: Message, state: FSMContext):
    await message.answer("Отправьте сообщение (текст/фото/видео) для рассылки всем юзерам:",
                         reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_mailing)


@dp.message(AdminStates.waiting_for_mailing)
async def admin_mailing_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT:
        return await safe_cancel(message, state)

    users = get_all_users()
    success = 0
    failed = 0

    status = await message.answer(f"⏳ Начинаю рассылку для {len(users)} юзеров...")
    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1

    await status.delete()
    await message.answer(
        f"✅ Рассылка завершена!\n\nУспешно: <b>{success}</b>\nОшибок (заблокировали бота): <b>{failed}</b>",
        parse_mode="HTML", reply_markup=get_admin_keyboard())
    await state.clear()


# ================= ЗАПУСК =================
async def main():
    await fetch_db()
    asyncio.create_task(background_saver())
    logging.info("🚀 Бот успешно запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await trigger_save(immediate=True)


if __name__ == '__main__':
    asyncio.run(main())