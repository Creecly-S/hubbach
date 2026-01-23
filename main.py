import asyncio
import logging
import aiohttp
import random
import os
import time
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
ADMIN_ID = int(os.getenv("ADMIN_ID"))

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
db_cache = {}
db_lock = asyncio.Lock()
save_pending = False


# ================= РАБОТА С JSONBIN =================

async def fetch_db():
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    global db_cache
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(JSONBIN_URL + "/latest", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    db_cache = data.get('record', {})
                    for key in ["users", "admins", "content", "seen_content"]:
                        if key not in db_cache: db_cache[key] = []
                    logging.info("База данных загружена.")
    except Exception as e:
        logging.error(f"Ошибка подключения: {e}")
        if not db_cache: db_cache = {"users": [], "admins": [], "content": [], "seen_content": []}


async def save_db():
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    try:
        data_to_send = db_cache.copy()
        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_URL, json=data_to_send, headers=headers) as response:
                if response.status != 200:
                    logging.error(f"Ошибка сохранения: {response.status}")
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


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user(user_id):
    for u in db_cache.get("users", []):
        if u["user_id"] == user_id: return u
    return None


def is_admin(user_id):
    return user_id == ADMIN_ID or user_id in db_cache.get("admins", [])


async def add_user(user_id, referrer_id=None):
    if get_user(user_id): return None
    if referrer_id == user_id: referrer_id = None
    new_user = {"user_id": user_id, "balance": 0, "reg_date": datetime.now().strftime("%d.%m.%Y"), "ref_count": 0,
                "referrer_id": referrer_id}
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
        reset_seen_content(user_id, content_type)
        return random.choice(typed_content)
    return random.choice(available)


def reset_seen_content(user_id, content_type):
    seen_list = db_cache.get("seen_content", [])
    content_ids = {c["id"] for c in db_cache.get("content", []) if c["content_type"] == content_type}
    db_cache["seen_content"] = [s for s in seen_list if
                                not (s["user_id"] == user_id and s["content_id"] in content_ids)]
    asyncio.create_task(trigger_save(immediate=False))


async def mark_as_seen(user_id, content_id):
    db_cache.setdefault("seen_content", []).append({"user_id": user_id, "content_id": content_id})
    await trigger_save(immediate=False)


async def save_content(content_type, file_id, caption=""):
    content_list = db_cache.setdefault("content", [])
    max_id = max([c["id"] for c in content_list], default=0)
    # Добавляем время добавления для функции удаления по времени
    new_item = {
        "id": max_id + 1,
        "content_type": content_type,
        "file_id": file_id,
        "caption": caption,
        "added_at": time.time()
    }
    content_list.append(new_item)
    await trigger_save(immediate=True)


async def delete_content_by_time(content_type, seconds_limit=None):
    content_list = db_cache.get("content", [])
    now = time.time()

    if seconds_limit:
        # Удаляем то, что было добавлено Х секунд назад (т.е. newer than now - limit)
        limit_time = now - seconds_limit
        # Сохраняем только то, что старее, либо другого типа
        db_cache["content"] = [
            c for c in content_list
            if not (c["content_type"] == content_type and c.get("added_at", 0) > limit_time)
        ]
    else:
        # Удаляем все
        db_cache["content"] = [c for c in content_list if c["content_type"] != content_type]

    await trigger_save(immediate=True)


def get_all_users():
    return [u["user_id"] for u in db_cache.get("users", [])]


async def add_admin_to_db(user_id):
    if user_id not in db_cache.get("admins", []):
        db_cache.setdefault("admins", []).append(user_id)
        await trigger_save(immediate=True)


async def remove_admin_from_db(user_id):
    admins = db_cache.get("admins", [])
    if user_id in admins:
        admins.remove(user_id)
        await trigger_save(immediate=True)


# ================= FSM =================
class PaymentStates(StatesGroup):
    waiting_amount = State()
    waiting_screenshot = State()


class AdminCheckStates(StatesGroup):
    checking_payment = State()
    adding_balance = State()
    sending_link = State()


class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_issue_user_id = State()
    waiting_for_issue_amount = State()
    waiting_for_mailing = State()
    waiting_for_admin_id_add = State()
    waiting_for_admin_id_del = State()


class SupportStates(StatesGroup):
    waiting_message = State()


class SupportReplyStates(StatesGroup):
    waiting_text = State()


class SuggestionStates(StatesGroup):
    waiting_content = State()


# ================= ФУНКЦИЯ ШРИФТА =================
def convert_to_font(text: str) -> str:
    font_mapping = {
        'а': 'α', 'б': 'б', 'в': 'v', 'г': 'г', 'д': 'д', 'е': '℮', 'ё': 'ё', 'ж': 'ж', 'з': 'з', 'и': 'и',
        'й': 'й', 'к': 'k', 'л': 'л', 'м': 'м', 'н': 'н', 'о': 'o', 'п': 'п', 'р': 'ρ', 'с': 'c', 'т': 'т',
        'у': 'у', 'ф': 'φ', 'х': 'х', 'ц': 'ц', 'ч': 'ч', 'ш': 'ш', 'щ': 'щ', 'ъ': 'ъ', 'ы': 'ы', 'ь': 'ь',
        'э': 'э', 'ю': 'ю', 'я': 'я', 'А': 'Α', 'Б': 'Б', 'В': 'V', 'Г': 'Г', 'Д': 'Д', 'Е': 'Ε', 'Ё': 'Ё',
        'Ж': 'Ж', 'З': 'З', 'И': 'И', 'Й': 'Й', 'К': 'Κ', 'Л': 'Л', 'М': 'Μ', 'Н': 'Н', 'О': 'Ο', 'П': 'Π',
        'Р': 'Ρ', 'С': 'C', 'Т': 'Τ', 'У': 'Υ', 'Ф': 'Φ', 'Х': 'Χ', 'Ц': 'Ц', 'Ч': 'Ч', 'Ш': 'Ш', 'Щ': 'Щ',
        'Ъ': 'Ъ', 'Ы': 'Ы', 'Ь': 'Ь', 'Э': 'Э', 'Ю': 'Ю', 'Я': 'Я', 'a': 'α', 'b': 'b', 'c': 'c', 'd': 'd',
        'e': '℮', 'f': 'f', 'g': 'g', 'h': 'h', 'i': 'i', 'j': 'j', 'k': 'k', 'l': 'l', 'm': 'm', 'n': 'n',
        'o': 'o', 'p': 'p', 'q': 'q', 'r': 'r', 's': 's', 't': 't', 'u': 'u', 'v': 'v', 'w': 'w', 'x': 'x',
        'y': 'y', 'z': 'z', 'A': 'Α', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'Ε', 'F': 'F', 'G': 'G', 'H': 'Η',
        'I': 'Ι', 'J': 'J', 'K': 'Κ', 'L': 'L', 'M': 'Μ', 'N': 'Ν', 'O': 'Ο', 'P': 'Ρ', 'Q': 'Q', 'R': 'R',
        'S': 'S', 'T': 'Τ', 'U': 'U', 'V': 'V', 'W': 'W', 'X': 'X', 'Y': 'Y', 'Z': 'Z',
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
    }
    return ''.join(font_mapping.get(c, c) for c in text)


# ================= КЛАВИАТУРЫ =================
def get_main_keyboard(user_id):
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text=convert_to_font("📷 Фото")),
                types.KeyboardButton(text=convert_to_font("🎥 Видео")))
    builder.row(types.KeyboardButton(text=convert_to_font("🛍️ Каталог(скидки)")))
    builder.row(types.KeyboardButton(text=convert_to_font("💰 Пополнить баланс")),
                types.KeyboardButton(text=convert_to_font("👤 Мой профиль")))
    builder.row(types.KeyboardButton(text=convert_to_font("👥 Реферальная система")),
                types.KeyboardButton(text=convert_to_font("📝 Задания")))
    builder.row(types.KeyboardButton(text=convert_to_font("🆘 Поддержка")),
                types.KeyboardButton(text=convert_to_font("📤 Предложка")))
    if is_admin(user_id):
        builder.row(types.KeyboardButton(text="⚙️ Админка"))
    return builder.as_markup(resize_keyboard=True)


def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="👥 Пользователи"), types.KeyboardButton(text="📢 Рассылка"))
    builder.row(types.KeyboardButton(text="📸 Добавить фото"), types.KeyboardButton(text="🎥 Добавить видео"))
    builder.row(types.KeyboardButton(text="🗑 Удалить фото"), types.KeyboardButton(text="🗑 Удалить видео"))
    builder.row(types.KeyboardButton(text="💸 Начислить монеты"))
    builder.row(types.KeyboardButton(text="👮‍♂️ Добавить админа"), types.KeyboardButton(text="🚫 Удалить админа"))
    builder.row(types.KeyboardButton(text="🔙 В главное меню"))
    return builder.as_markup(resize_keyboard=True)


# ================= ОБРАБОТЧИКИ =================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    referrer_id = None
    if message.text.startswith('/start '):
        try:
            referrer_id = int(message.text.split()[1])
        except:
            pass
    await add_user(user_id, referrer_id)
    await message.answer(convert_to_font("Hubbαch- тут вьı нαйдете тo сαмoe, и нe тoльkο"))
    await show_profile(message)


async def show_profile(message: Message):
    user_id = message.from_user.id
    u = get_user(user_id)
    bal = u["balance"] if u else 0
    reg = u["reg_date"] if u else "Сегодня"
    refs = u["ref_count"] if u else 0
    bot_info = await bot.get_me()

    header = convert_to_font("👤 Ваш профиль:")
    line1 = convert_to_font(f"🆔 ID: ") + f"{user_id}"
    line2 = convert_to_font(f"💰 Баланс: ") + f"{bal}"
    line3 = convert_to_font(f"📅 С нами с: ") + f"{reg}"
    line4 = convert_to_font(f"👥 Рефералов: ") + f"{refs}"
    line5_label = convert_to_font(f"🔗 Реферальная ссылка:\n")

    profile_text = f"{header}\n\n{line1}\n{line2}\n{line3}\n{line4}\n\n{line5_label}https://t.me/{bot_info.username}?start={user_id}"
    await message.answer(profile_text, reply_markup=get_main_keyboard(user_id))


@dp.message(F.text == convert_to_font("👤 Мой профиль"))
async def btn_profile(message: Message): await show_profile(message)


@dp.message(F.text == convert_to_font("👥 Реферальная система"))
async def panel_ref(message: Message):
    bot_info = await bot.get_me()
    await message.answer(
        f"{convert_to_font('Реферальная система')}\n{convert_to_font('Ссылка:')} https://t.me/{bot_info.username}?start={message.from_user.id}")


# --- ЛОГИКА КОНТЕНТА (С ЗАЩИТОЙ) ---
@dp.message(F.text == convert_to_font("📷 Фото"))
async def show_photo(message: Message):
    uid = message.from_user.id;
    u = get_user(uid)
    if not u: return
    if u["balance"] < 1: await message.answer(convert_to_font("❌ Недостаточно монет!")); return
    await add_balance(uid, -1);
    c = get_unseen_content(uid, 'photo')
    if c:
        await mark_as_seen(uid, c["id"])
        try:
            await message.answer_photo(photo=c["file_id"], caption=convert_to_font(
                f"💸 Списано 1 монету(ы). Ваш баланс: {u['balance'] - 1}"), protect_content=True)
        except:
            await add_balance(uid, 1); await message.answer(convert_to_font("Ошибка загрузки."))
    else:
        await add_balance(uid, 1); await message.answer(convert_to_font("Пока нет фото."))


@dp.message(F.text == convert_to_font("🎥 Видео"))
async def show_video(message: Message):
    uid = message.from_user.id;
    u = get_user(uid)
    if not u: return
    if u["balance"] < 3: await message.answer(convert_to_font("❌ Недостаточно монет!")); return
    await add_balance(uid, -3);
    c = get_unseen_content(uid, 'video')
    if c:
        await mark_as_seen(uid, c["id"])
        try:
            await message.answer_video(video=c["file_id"],
                                       caption=convert_to_font(f"💸 Списано 3 монеты. Ваш баланс: {u['balance'] - 3}"),
                                       protect_content=True)
        except:
            await add_balance(uid, 3); await message.answer(convert_to_font("Ошибка загрузки."))
    else:
        await add_balance(uid, 3); await message.answer(convert_to_font("Пока нет видео."))


# --- ЗАДАНИЕ ---
@dp.message(F.text == convert_to_font("📝 Задания"))
async def panel_tasks(message: Message):
    text = (
        "🔥 ЗАДАНИЕ\n\n"
        "💰 +45💸 за задание:\n"
        "1. Пишем в поиске TikTok (тт): дэтское питаниеэ\n"
        "2. Под разными 10 видео оставляем по 1 коменту:\n"
        "самое лучшее\n"
        "፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰\n"
        "https://t.me/HubbachBot\n"
        "፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰፰\n"
        "4. ОБЯЗАТЕЛЬНО лайкаем свой комент!\n\n"
        "📸 После выполнения отправьте 15 скринов с вашим коментом в поддержку бота\n"
        "💸 45💸 поступят к вам на балик в течение 10 минут!"
    )
    await message.answer(text)


# ================= НОВЫЙ КАТАЛОГ =================
CATALOG = {
    1: ("Детskоe 450 GB", 175),
    2: ("С жиvotнымu 300 GB", 165),
    3: ("Gеu maльчuku 350 GB", 180),
    4: ("Сkрытaя каmera с шкоlьноgo tuалеta 1 TB", 250),
    5: ("LoliПоRN вsen виdeo", 130)
}


@dp.message(F.text == convert_to_font("🛍️ Каталог(скидки)"))
async def panel_catalog(message: Message):
    builder = InlineKeyboardBuilder()
    for item_id, (name, _) in CATALOG.items():
        builder.row(types.InlineKeyboardButton(text=convert_to_font(name), callback_data=f"cat_view_{item_id}"))
    await message.answer(convert_to_font("🛍️ Товары:"), reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("cat_view_"))
async def cat_view(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[2])
    name, price = CATALOG[item_id]
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text=convert_to_font(f"Купить ({price} монет)"), callback_data=f"cat_buy_{item_id}"))
    builder.row(types.InlineKeyboardButton(text=convert_to_font("🔙 Назад"), callback_data="cat_menu"))
    await callback.message.edit_text(
        f"{convert_to_font('Товар:')} {name}\n{convert_to_font('Цена:')} {price} {convert_to_font('монет')}",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "cat_menu")
async def cat_menu(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    for item_id, (name, _) in CATALOG.items():
        builder.row(types.InlineKeyboardButton(text=convert_to_font(name), callback_data=f"cat_view_{item_id}"))
    await callback.message.edit_text(convert_to_font("🛍️ Товары:"), reply_markup=builder.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("cat_buy_"))
async def cat_buy(callback: CallbackQuery):
    user_id = callback.from_user.id
    item_id = int(callback.data.split("_")[2])
    name, price = CATALOG[item_id]
    u = get_user(user_id)
    if not u or u["balance"] < price:
        await callback.answer(convert_to_font("Недостаточно монет!"), show_alert=True)
        return

    await add_balance(user_id, -price)
    await callback.message.edit_text(convert_to_font("✅ Заказ оформлен! Ожидайте проверки."))

    admin_builder = InlineKeyboardBuilder()
    admin_builder.row(types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"cat_conf_{user_id}_{price}"))
    admin_builder.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"cat_rej_{user_id}_{price}"))
    try:
        await bot.send_message(ADMIN_ID, f"📦 Заказ: {name}\n👤 ID: {user_id}\n💰 {price} монет",
                               reply_markup=admin_builder.as_markup())
    except:
        pass
    await callback.answer()


# ================= НОВОЕ ПОПОЛНЕНИЕ =================
CRYPTO_PAY_LINK = "http://t.me/send?start=IVFzT8LRnugW"


@dp.message(F.text == convert_to_font("💰 Пополнить баланс"))
async def panel_topup(message: Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="CryptoBot", callback_data="topup_crypto"))
    builder.row(types.InlineKeyboardButton(text=convert_to_font("🔙 В главное меню"), callback_data="back_to_main"))
    await message.answer(convert_to_font("Выберите способ оплаты:"), reply_markup=builder.as_markup())


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_profile(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "topup_crypto")
async def topup_crypto_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "50 монет - 0.78$. Минимум 25\n\n" + convert_to_font("Введите нужное количество монет:"))
    await state.set_state(PaymentStates.waiting_amount)
    await callback.answer()


@dp.message(PaymentStates.waiting_amount)
async def topup_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount < 25: await message.answer(convert_to_font("Минимум 25 монет!")); return
        cost = (amount / 50) * 0.78
        await state.update_data(pay_amount=amount)
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text=convert_to_font("Оплатить"), url=CRYPTO_PAY_LINK))
        builder.row(types.InlineKeyboardButton(text=convert_to_font("Подтвердить"), callback_data="pay_confirm_step"))
        await message.answer(f"{convert_to_font('К оплате:')} {cost:.2f}$ ({amount} монет)",
                             reply_markup=builder.as_markup())
    except ValueError:
        await message.answer(convert_to_font("Введите число!"))


@dp.callback_query(F.data == "pay_confirm_step")
async def topup_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(convert_to_font("Отправьте скриншот оплаты."))
    await state.set_state(PaymentStates.waiting_screenshot)
    await callback.answer()


@dp.message(PaymentStates.waiting_screenshot, F.photo)
async def topup_screenshot(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = await state.get_data()
    amount = data.get("pay_amount", 0)
    await message.answer(convert_to_font("Скриншот получен. Проверка займет до 3 минут."))
    await state.clear()

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay_conf_{uid}_{amount}"))
    b.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay_rej_{uid}"))
    try:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=f"📝 Пополнение\n👤 {uid}\n💰 {amount}",
                             reply_markup=b.as_markup())
    except:
        pass


# ================= АДМИНСКИЕ ПРОВЕРКИ =================
@dp.callback_query(F.data.startswith("pay_conf_"))
async def adm_pay_conf(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    uid, amt = int(parts[2]), int(parts[3])
    await state.update_data(target_user_id=uid, pending_amount=amt)

    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text="Накрутить баланс"))
    b.row(types.KeyboardButton(text="Выдать ссылку"))
    b.row(types.KeyboardButton(text="Отмена"))
    await callback.message.answer(f"Платеж {amt} от {uid}. Выберите:", reply_markup=b.as_markup(resize_keyboard=True))
    await state.set_state(AdminCheckStates.checking_payment)
    await callback.message.edit_reply_markup(None)
    await callback.answer()


@dp.callback_query(F.data.startswith("pay_rej_"))
async def adm_pay_rej(callback: CallbackQuery):
    uid = int(callback.data.split("_")[2])
    try:
        await bot.send_message(uid, convert_to_font("❌ Платеж отклонен."))
    except:
        pass
    await callback.message.edit_reply_markup(None)
    await callback.answer()


@dp.message(AdminCheckStates.checking_payment, F.text == "Накрутить баланс")
async def adm_act_bal(message: Message, state: FSMContext):
    d = await state.get_data()
    uid, amt = d['target_user_id'], d['pending_amount']
    await add_balance(uid, amt)
    try:
        await bot.send_message(uid, convert_to_font(f"🎁 Начислено {amt} монет!"))
    except:
        pass
    await message.answer("Начислено.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(AdminCheckStates.checking_payment, F.text == "Выдать ссылку")
async def adm_act_link(message: Message, state: FSMContext):
    await message.answer("Введите ссылку:")
    await state.set_state(AdminCheckStates.sending_link)


@dp.message(AdminCheckStates.checking_payment, F.text == "Отмена")
async def adm_act_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_admin_keyboard())


@dp.callback_query(F.data.startswith("cat_conf_"))
async def adm_cat_conf(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    uid, price = int(parts[2]), int(parts[3])
    await state.update_data(target_user_id=uid)
    await callback.message.answer(f"Заказ {uid} ({price}). Отправьте ссылку:")
    await state.set_state(AdminCheckStates.sending_link)
    await callback.message.edit_reply_markup(None)
    await callback.answer()


@dp.callback_query(F.data.startswith("cat_rej_"))
async def adm_cat_rej(callback: CallbackQuery):
    parts = callback.data.split("_")
    uid, price = int(parts[2]), int(parts[3])
    await add_balance(uid, price)
    try:
        await bot.send_message(uid, convert_to_font("❌ Заказ отклонен. Монеты возвращены."))
    except:
        pass
    await callback.message.edit_reply_markup(None)
    await callback.answer()


@dp.message(AdminCheckStates.sending_link)
async def adm_send_link(message: Message, state: FSMContext):
    d = await state.get_data()
    uid = d['target_user_id']
    try:
        await bot.send_message(uid, convert_to_font("🔗 Ваша ссылка:\n\n") + message.text)
        await message.answer("Отправлено.", reply_markup=get_admin_keyboard())
    except:
        await message.answer("Ошибка отправки.", reply_markup=get_admin_keyboard())
    await state.clear()


# ================= ПОДДЕРЖКА И ПРЕДЛОЖКА =================
@dp.message(F.text == convert_to_font("🆘 Поддержка"))
async def support_start(message: Message, state: FSMContext):
    await message.answer(convert_to_font("Напишите ваш вопрос или отправьте скриншот/фото:"))
    await state.set_state(SupportStates.waiting_message)


@dp.message(SupportStates.waiting_message)
async def support_process(message: Message, state: FSMContext):
    uid = message.from_user.id
    text_content = message.text or message.caption or ""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="Ответить", callback_data=f"supp_reply_{uid}"))
    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"🆘 Поддержка от {uid}:\n{text_content}", reply_markup=builder.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id,
                                 caption=f"🆘 Поддержка от {uid}:\n{text_content}", reply_markup=builder.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"🆘 Поддержка от {uid}:\n{text_content}", reply_markup=builder.as_markup())
        await message.answer(convert_to_font("✅ Сообщение отправлено! Ожидайте ответа."))
        await state.clear()
    except Exception as e:
        await message.answer("Ошибка отправки.")
        logging.error(f"Support error: {e}")


@dp.callback_query(F.data.startswith("supp_reply_"))
async def supp_reply_callback(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split("_")[2])
    await state.clear()
    await state.update_data(supp_user_id=uid)
    await callback.message.answer(f"Введите ответ для пользователя {uid}:")
    await state.set_state(SupportReplyStates.waiting_text)
    await callback.answer()


@dp.message(SupportReplyStates.waiting_text)
async def supp_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get('supp_user_id')
    if uid:
        try:
            await bot.send_message(uid, f"📩 <b>Ответ поддержки:</b>\n\n{message.text}", parse_mode="HTML")
            await message.answer("Ответ отправлен.", reply_markup=get_admin_keyboard())
        except:
            await message.answer("Не удалось отправить ответ.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == convert_to_font("📤 Предложка"))
async def sugg_start(message: Message, state: FSMContext):
    await message.answer(convert_to_font("Кидайте фото/видео/ссылку с предложением. Вы можете получить до 100 монет!"))
    await state.set_state(SuggestionStates.waiting_content)


@dp.message(SuggestionStates.waiting_content)
async def sugg_process(message: Message, state: FSMContext):
    uid = message.from_user.id
    text_content = message.text or message.caption or ""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Принять (100 монет)", callback_data=f"sugg_acc_{uid}"))
    builder.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"sugg_rej_{uid}"))
    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"📤 Предложка от {uid}:\n{text_content}", reply_markup=builder.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id,
                                 caption=f"📤 Предложка от {uid}:\n{text_content}", reply_markup=builder.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"📤 Предложка от {uid} (Текст/Ссылка):\n{text_content}",
                                   reply_markup=builder.as_markup())
        await message.answer(convert_to_font("✅ Предложка отправлено на проверку!"))
        await state.clear()
    except Exception as e:
        await message.answer("Ошибка.")
        logging.error(f"Suggestion error: {e}")


@dp.callback_query(F.data.startswith("sugg_acc_"))
async def sugg_acc(callback: CallbackQuery):
    uid = int(callback.data.split("_")[2])
    await add_balance(uid, 100)
    try:
        await bot.send_message(uid, convert_to_font("🎁 Ваше предложение принято! Вам начислено 100 монет."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Начислено 100 монет пользователю {uid}.")
    await callback.answer("Принято")


@dp.callback_query(F.data.startswith("sugg_rej_"))
async def sugg_rej(callback: CallbackQuery):
    uid = int(callback.data.split("_")[2])
    try:
        await bot.send_message(uid, convert_to_font("❌ К сожалению, ваше предложение не подошло."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отклонено")


# ================= АДМИНКА (НОВАЯ) =================

@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear()
        await message.answer("Админ-Панель.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Нет доступа.")


@dp.message(F.text == "🔙 В главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await show_profile(message)


def get_cancel_keyboard():
    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text="Отмена"))
    return b.as_markup(resize_keyboard=True)


# --- Просмотр пользователей ---
@dp.message(F.text == "👥 Пользователи")
async def admin_users(message: Message):
    users = db_cache.get("users", [])
    total = len(users)
    text = f"👥 Всего пользователей: {total}\n\n"

    # Берем первые 20, чтобы не превысить лимит сообщения
    for u in users[:20]:
        u_id = u.get("user_id", "?")
        u_bal = u.get("balance", 0)
        u_date = u.get("reg_date", "?")
        text += f"ID: {u_id} | Баланс: {u_bal} | Рег: {u_date}\n"

    if len(users) > 20:
        text += f"\n...и еще {len(users) - 20} пользователей."

    await message.answer(text)


# --- Удаление по времени (Инлайн меню) ---
@dp.message(F.text == "🗑 Удалить фото")
async def delete_photo_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="За 1 минуту", callback_data="del_photo_60"))
    builder.row(types.InlineKeyboardButton(text="За 1 час", callback_data="del_photo_3600"))
    builder.row(types.InlineKeyboardButton(text="За 1 день", callback_data="del_photo_86400"))
    builder.row(types.InlineKeyboardButton(text="За все время", callback_data="del_photo_all"))
    await message.answer("Выберите период удаления фото:", reply_markup=builder.as_markup())


@dp.message(F.text == "🗑 Удалить видео")
async def delete_video_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="За 1 минуту", callback_data="del_video_60"))
    builder.row(types.InlineKeyboardButton(text="За 1 час", callback_data="del_video_3600"))
    builder.row(types.InlineKeyboardButton(text="За 1 день", callback_data="del_video_86400"))
    builder.row(types.InlineKeyboardButton(text="За все время", callback_data="del_video_all"))
    await message.answer("Выберите период удаления видео:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("del_photo_"))
async def process_del_photo(callback: CallbackQuery):
    time_str = callback.data.split("_")[2]
    if time_str == "all":
        await delete_content_by_time('photo', None)
        await callback.answer("Все фото удалены.")
    else:
        seconds = int(time_str)
        await delete_content_by_time('photo', seconds)
        await callback.answer(f"Фото за указанный период удалены.")


@dp.callback_query(F.data.startswith("del_video_"))
async def process_del_video(callback: CallbackQuery):
    time_str = callback.data.split("_")[2]
    if time_str == "all":
        await delete_content_by_time('video', None)
        await callback.answer("Все видео удалены.")
    else:
        seconds = int(time_str)
        await delete_content_by_time('video', seconds)
        await callback.answer(f"Видео за указанный период удалены.")


# --- Остальная админка ---
@dp.message(F.text == "📸 Добавить фото")
async def add_ph_start(message: Message, state: FSMContext):
    await message.answer("Отправьте фото:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_photo)


@dp.message(AdminStates.waiting_for_photo, F.photo)
async def add_ph_proc(message: Message, state: FSMContext):
    await save_content('photo', message.photo[-1].file_id)
    await message.answer("✅ Сохранено!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "🎥 Добавить видео")
async def add_vid_start(message: Message, state: FSMContext):
    await message.answer("Отправьте видео:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_video)


@dp.message(AdminStates.waiting_for_video, F.video)
async def add_vid_proc(message: Message, state: FSMContext):
    await save_content('video', message.video.file_id)
    await message.answer("✅ Сохранено!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "📢 Рассылка")
async def mail_start(message: Message, state: FSMContext):
    await message.answer("Отправьте сообщение:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_mailing)


@dp.message(AdminStates.waiting_for_mailing)
async def mail_proc(message: Message, state: FSMContext):
    users = get_all_users();
    c = 0;
    f = 0
    await message.answer(f"Рассылка {len(users)}...")
    for uid in users:
        try:
            await message.copy_to(chat_id=uid);
            c += 1;
            await asyncio.sleep(0.05)
        except:
            f += 1
    await message.answer(f"Готово: {c}, Ошибок: {f}", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "💸 Начислить монеты")
async def issue_start(message: Message, state: FSMContext):
    await message.answer("Введите ID:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_issue_user_id)


@dp.message(AdminStates.waiting_for_issue_user_id)
async def issue_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text)
        await state.update_data(target_user_id=uid)
        await message.answer("Введите сумму:")
        await state.set_state(AdminStates.waiting_for_issue_amount)
    except:
        await message.answer("Неверный ID.")


@dp.message(AdminStates.waiting_for_issue_amount)
async def issue_amt(message: Message, state: FSMContext):
    try:
        amt = int(message.text)
        d = await state.get_data()
        await add_balance(d['target_user_id'], amt)
        await message.answer("Начислено.", reply_markup=get_admin_keyboard())
        await state.clear()
    except:
        await message.answer("Неверная сумма.")


@dp.message(F.text == "👮‍♂️ Добавить админа")
async def add_adm_start(message: Message, state: FSMContext):
    await message.answer("Введите ID:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_admin_id_add)


@dp.message(AdminStates.waiting_for_admin_id_add)
async def add_adm_proc(message: Message, state: FSMContext):
    try:
        await add_admin_to_db(int(message.text))
        await message.answer("Готово.", reply_markup=get_admin_keyboard())
        await state.clear()
    except:
        await message.answer("Ошибка.")


@dp.message(F.text == "🚫 Удалить админа")
async def del_adm_start(message: Message, state: FSMContext):
    await message.answer("Введите ID:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_admin_id_del)


@dp.message(AdminStates.waiting_for_admin_id_del)
async def del_adm_proc(message: Message, state: FSMContext):
    try:
        await remove_admin_from_db(int(message.text))
        await message.answer("Готово.", reply_markup=get_admin_keyboard())
        await state.clear()
    except:
        await message.answer("Ошибка.")


@dp.message(F.text == "Отмена")
async def cancel_action(message: Message, state: FSMContext):
    await message.answer("Отменено.", reply_markup=get_admin_keyboard())
    await state.clear()


# ================= ЗАПУСК =================
async def main():
    await fetch_db()
    asyncio.create_task(background_saver())
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await trigger_save(True)


if __name__ == '__main__':
    asyncio.run(main())