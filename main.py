import asyncio
import logging
import aiohttp
import random
import os
import time
import string
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
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
CHANNEL_TO_CHECK = "@Hubbach_c"  # Канал для жесткой проверки

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

TASK_REWARD = 50
TASKS_INFO = {
    "1": {
        "text": "📌 <b>ОПИСАНИЕ ЗАДАНИЯ:</b>\n\n1️⃣ Заходим в приложение TikTok.\n2️⃣ Вводим в поиск: <code>дэтское питаниеэ</code>\n3️⃣ Под <b>10</b> видео оставляем комментарий:\n💬 «самый лучшее @HubbachBot - просто топпп :)»\n💬 Либо: «@HubbachBot - самое лучшее ❤️»\n4️⃣ Обязαтeльнo ставим ЛАЙК на свoй кoммeнт!\n5️⃣ Дeлaeм скриншоты всeго прoцeссa.\n\n⚠️ <b>ВАЖНО:</b> Скриншоты дoлжны быть ЧЕТКИМИ и пoкαзывαть, чтo кoммeнтαрий oстαвлен с вαшeгo αккαунтα!"},
    "2": {
        "text": "📌 <b>ОПИСАНИЕ ЗАДАНИЯ:</b>\n\n1️⃣ Заходим в приложение TikTok.\n2️⃣ Вводим в поиск: <code>дэтское питаниеэ</code>\n3️⃣ Под <b>15</b> видео оставляем комментарий:\n💬 «самый лучшее @HubbachBot - просто топпп :)»\n💬 Либо: «@HubbachBot - самое лучшее ❤️»\n4️⃣ Обязαтeльнo ставим ЛАЙК на свoй кoммeнт!\n5️⃣ Дeлaeм скриншоты всeго прoцeссa.\n\n⚠️ <b>ВАЖНО:</b> Скриншоты дoлжны быть ЧЕТКИМИ и пoкαзывαть, чтo кoммeнтαрий oстαвлен с вαшeгo αккαунтα!"},
    "3": {
        "text": "📌 <b>ОПИСАНИЕ ЗАДАНИЯ:</b>\n\n1️⃣ Заходим в приложение TikTok.\n2️⃣ Вводим в поиск: <code>дэтское питаниеэ</code>\n3️⃣ Под <b>20</b> видео оставляем комментарий:\n💬 «самый лучшее @HubbachBot - просто топпп :)»\n💬 Либо: «@HubbachBot - самое лучшее ❤️»\n4️⃣ Обязαтeльнo ставим ЛАЙК на свoй кoммeнт!\n5️⃣ Дeлaeм скриншоты всeго прoцeссa.\n\n⚠️ <b>ВАЖНО:</b> Скриншоты дoлжны быть ЧЕТКИМИ и пoкαзывαть, чтo кoммeнтαрий oстαвлен с вαшeгo αккαунтα!"}
}


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
                    # Инициализация новых полей
                    if "daily_pack_link" not in db_cache:
                        db_cache["daily_pack_link"] = ""

                    for u in db_cache.get("users", []):
                        if "last_bonus" not in u: u["last_bonus"] = 0
                        if "tasks_status" not in u: u["tasks_status"] = {"1": "none", "2": "none", "3": "none"}
                        if "last_pack_claim" not in u: u["last_pack_claim"] = 0
                    logging.info("✅ База данных загружена.")
    except Exception as e:
        logging.error(f"Ошибка подключения к БД: {e}")
        if not db_cache: db_cache = {"users": [], "admins": [], "content": [], "seen_content": [], "promo_keys": {},
                                     "daily_pack_link": ""}


async def save_db():
    headers = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_URL, json=db_cache.copy(), headers=headers) as response:
                if response.status != 200: logging.error(f"Ошибка сохранения БД: {response.status}")
    except Exception as e:
        logging.error(f"Ошибка соединения при сохранении: {e}")


async def trigger_save(immediate=False):
    global save_pending
    save_pending = True
    if immediate:
        async with db_lock: await save_db()


async def background_saver():
    global save_pending
    while True:
        if save_pending:
            async with db_lock:
                if save_pending: await save_db(); save_pending = False
        await asyncio.sleep(3)


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_TO_CHECK, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Ошибка проверки подписки (добавьте бота в админы канала!): {e}")
        return True


def get_user(user_id):
    for u in db_cache.get("users", []):
        if u["user_id"] == user_id: return u
    return None


def is_admin(user_id): return user_id == ADMIN_ID or user_id in db_cache.get("admins", [])


async def add_user(user_id, referrer_id=None):
    if get_user(user_id): return None
    if referrer_id == user_id: referrer_id = None
    new_user = {
        "user_id": user_id, "balance": 10, "reg_date": datetime.now().strftime("%d.%m.%Y"), "ref_count": 0,
        "referrer_id": referrer_id, "last_bonus": 0, "tasks_status": {"1": "none", "2": "none", "3": "none"},
        "last_pack_claim": 0  # Новое поле для паков
    }
    db_cache.setdefault("users", []).append(new_user)
    if referrer_id:
        ref = get_user(referrer_id)
        if ref: ref["balance"] += 3; ref["ref_count"] += 1
    await trigger_save(immediate=True)


async def add_balance(user_id, amount):
    u = get_user(user_id)
    if u: u["balance"] += amount; await trigger_save(immediate=False); return True
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
    content_list.append({"id": max_id + 1, "content_type": content_type, "file_id": file_id, "added_at": time.time()})
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
    db_cache["content"] = [];
    db_cache["seen_content"] = [];
    await trigger_save(immediate=True)


async def add_admin_to_db(user_id):
    if user_id not in db_cache.get("admins", []): db_cache.setdefault("admins", []).append(user_id); await trigger_save(
        immediate=True)


async def remove_admin_from_db(user_id):
    admins = db_cache.get("admins", [])
    if user_id in admins: admins.remove(user_id); await trigger_save(immediate=True)


def get_all_users(): return [u["user_id"] for u in db_cache.get("users", [])]


# ================= CRYPTOBOT API =================
async def create_crypto_invoice(amount: float, asset: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://pay.crypt.bot/api/createInvoice",
                                    headers={"Crypto-Pay-API-Token": CRYPTO_APP_TOKEN},
                                    json={"asset": asset, "amount": str(round(amount, 4)),
                                          "description": f"Hubbach {asset}", "expires_in": 3600}) as response:
                if response.status == 200: return (await response.json()).get("result", {}).get("pay_url")
    except Exception as e:
        logging.error(f"CryptoBot error: {e}")
    return None


# ================= ПРОБИВАЮЩИЙ ЧЕКЕР ПОДПИСКИ (MIDDLEWARE) =================
class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id

        # Админов пропускаем всегда
        if is_admin(user_id):
            return await handler(event, data)

        # Проверяем подписку
        if not await is_subscribed(user_id):
            if isinstance(event, Message):
                builder = InlineKeyboardBuilder()
                builder.row(types.InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub_global"))
                await event.answer(
                    "🚫 <b>Доступ закрыт</b>\n\n"
                    "Вы отписались от канала! Для использования бота необходимо быть подписанным:\n"
                    f"👉 {CHANNEL_TO_CHECK}\n\n"
                    "После подписки нажмите кнопку ниже ⬇️",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ Вы отписались от канала! Подпишитесь заново.", show_alert=True)
            return  # Прерываем выполнение, бот не даст пользоваться функциями

        return await handler(event, data)


# Регистрируем Middleware на ВСЕ сообщения и кнопки
dp.message.middleware(SubscriptionMiddleware())
dp.callback_query.middleware(SubscriptionMiddleware())


# ================= FSM СОСТОЯНИЯ =================
class PaymentStates(StatesGroup): waiting_amount = State(); waiting_screenshot = State()


class AdminCheckStates(StatesGroup): checking_payment = State(); sending_link = State()


class AdminStates(
    StatesGroup): waiting_for_photo = State(); waiting_for_video = State(); waiting_for_issue = State(); waiting_for_mailing = State(); waiting_for_admin_id = State(); waiting_for_pack_link = State()


class SupportStates(StatesGroup): waiting_message = State()


class SupportReplyStates(StatesGroup): waiting_text = State()


class SuggestionStates(StatesGroup): waiting_content = State()


class PromoStates(StatesGroup): generating_key = State(); activating_key = State()


class TaskStates(StatesGroup): waiting_screenshots = State()


# ================= ФУНКЦИЯ ШРИФТА И КЛАВИАТУРЫ =================
def convert_to_font(text: str) -> str:
    m = {'а': 'α', 'б': 'б', 'в': 'v', 'г': 'г', 'д': 'д', 'е': '℮', 'ё': 'ё', 'ж': 'ж', 'з': 'з', 'и': 'и', 'й': 'й',
         'к': 'k', 'л': 'л', 'м': 'м', 'н': 'н', 'о': 'o', 'п': 'п', 'р': 'ρ', 'с': 'c', 'т': 'т', 'у': 'у', 'ф': 'φ',
         'х': 'х', 'ц': 'ц', 'ч': 'ч', 'ш': 'ш', 'щ': 'щ', 'ъ': 'ъ', 'ы': 'ы', 'ь': 'ь', 'э': 'э', 'ю': 'ю', 'я': 'я',
         'А': 'Α', 'Б': 'Б', 'В': 'V', 'Г': 'Г', 'Д': 'Д', 'Е': 'Ε', 'Ё': 'Ё', 'Ж': 'Ж', 'З': 'З', 'И': 'И', 'Й': 'Й',
         'К': 'Κ', 'Л': 'Л', 'М': 'Μ', 'Н': 'Н', 'О': 'Ο', 'П': 'Π', 'Р': 'Ρ', 'С': 'C', 'Т': 'Τ', 'У': 'Υ', 'Ф': 'Φ',
         'Х': 'Χ', 'Ц': 'Ц', 'Ч': 'Ч', 'Ш': 'Ш', 'Щ': 'Щ', 'Ъ': 'Ъ', 'Ы': 'Ы', 'Ь': 'Ь', 'Э': 'Э', 'Ю': 'Ю', 'Я': 'Я'}
    return ''.join(m.get(c, c) for c in text)


CANCEL_TEXT = convert_to_font("❌ Отмена")


def get_main_keyboard(user_id):
    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text=convert_to_font("📷 Фото")), types.KeyboardButton(text=convert_to_font("🎥 Видео")))
    b.row(types.KeyboardButton(text=convert_to_font("🛍 Магазин")),
          types.KeyboardButton(text=convert_to_font("💰 Баланс")))
    b.row(types.KeyboardButton(text=convert_to_font("🎁 Бонус")), types.KeyboardButton(text=convert_to_font("🏆 Топ")))
    b.row(types.KeyboardButton(text=convert_to_font("📋 Задания")),
          types.KeyboardButton(text=convert_to_font("🎁 Ежедневные паки")))  # Новая кнопка
    b.row(types.KeyboardButton(text=convert_to_font("🔑 Активатор")),
          types.KeyboardButton(text=convert_to_font("🆘 Поддержка")))
    b.row(types.KeyboardButton(text=convert_to_font("📤 Предложка")))
    if is_admin(user_id): b.row(types.KeyboardButton(text="⚙️ Админка"))
    return b.as_markup(resize_keyboard=True)


def get_admin_keyboard():
    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text="📊 Статистика"), types.KeyboardButton(text="👥 Юзеры"))
    b.row(types.KeyboardButton(text="💸 Начислить"), types.KeyboardButton(text="🔑 Выдать ключ"))
    b.row(types.KeyboardButton(text="📸 Добавить фото"), types.KeyboardButton(text="🎥 Добавить видео"))
    b.row(types.KeyboardButton(text="📦 Добавить бесплатный пак"),
          types.KeyboardButton(text="🗑 Удалить фото/видео"))  # Новая кнопка
    b.row(types.KeyboardButton(text="🧹 Очистить всё"), types.KeyboardButton(text="👮‍♂️ Управление админами"))
    b.row(types.KeyboardButton(text="📢 Рассылка"))
    b.row(types.KeyboardButton(text="🏠 Главное меню"), types.KeyboardButton(text=CANCEL_TEXT))
    return b.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    return ReplyKeyboardBuilder().row(types.KeyboardButton(text=CANCEL_TEXT)).as_markup(resize_keyboard=True)


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
    referrer_id = None
    if message.text.startswith('/start '):
        try:
            referrer_id = int(message.text.split()[1])
        except:
            pass
    await add_user(message.from_user.id, referrer_id)

    welcome_text = "💎 ━━━━━━━━━━━━━━━ 💎\n" + convert_to_font(
        'Hubbαch - тут вьı нαйдете тo сαмoe') + "\n💎 ━━━━━━━━━━━━━━━ 💎"
    await message.answer(welcome_text, reply_markup=get_main_keyboard(message.from_user.id), protect_content=True)


@dp.callback_query(F.data == "check_sub_global")
async def check_sub_global(callback: CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        welcome_text = "💎 ━━━━━━━━━━━━━━━ 💎\n" + convert_to_font(
            'Hubbαch - тут вьı нαйдете тo сαмoe') + "\n💎 ━━━━━━━━━━━━━━━ 💎"
        await callback.message.answer(welcome_text, reply_markup=get_main_keyboard(callback.from_user.id),
                                      protect_content=True)
        await callback.answer("✅ Добро пожаловать!")
    else:
        await callback.answer("❌ Вы еще не подписались!", show_alert=True)


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
    for i, u in enumerate(users):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
        uid_str = str(u['user_id'])
        masked_id = uid_str[:2] + "###" + uid_str[-2:] if len(uid_str) > 4 else uid_str
        text += f"{medal} <code>{masked_id}</code> — <b>{u['balance']} коинов</b>\n"
    await message.answer(text, parse_mode="HTML", protect_content=True)


@dp.message(F.text == convert_to_font("🎁 Бонус"))
async def daily_bonus(message: Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    if not u: return
    now = time.time()
    if now - u.get("last_bonus", 0) >= 86400:
        await add_balance(message.from_user.id, 1);
        u["last_bonus"] = now;
        await trigger_save(immediate=True)
        await message.answer(convert_to_font("✅ Вьı пoлучили +1 бесплαтную мoнету!"),
                             reply_markup=get_main_keyboard(message.from_user.id), protect_content=True)
    else:
        h, m = divmod(int(86400 - (now - u.get("last_bonus", 0))), 3600)[0], \
            divmod(int(86400 - (now - u.get("last_bonus", 0))) % 3600, 60)[1]
        await message.answer(convert_to_font(f"⏳ Бoнус ужe сбрαн. Следующий чeрeз {h}ч {m}м."),
                             reply_markup=get_main_keyboard(message.from_user.id), protect_content=True)


# ================= НОВОЕ: ЕЖЕДНЕВНЫЕ ПАКИ =================
@dp.message(F.text == convert_to_font("🎁 Ежедневные паки"))
async def daily_pack(message: Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    if not u: return

    now = time.time()
    # Проверка, получал ли пак за последние 24 часа
    if now - u.get("last_pack_claim", 0) >= 86400:
        pack_link = db_cache.get("daily_pack_link", "")
        if pack_link:
            u["last_pack_claim"] = now
            await trigger_save(immediate=True)
            await message.answer(
                convert_to_font("🎁 Вαш eжeднeвный бeсплαтный пαк:") + f"\n\n{pack_link}",
                reply_markup=get_main_keyboard(message.from_user.id),
                protect_content=True
            )
        else:
            await message.answer(
                convert_to_font("⏳ Ждитe oбнoвлeния пαкa."),
                reply_markup=get_main_keyboard(message.from_user.id),
                protect_content=True
            )
    else:
        remaining = int(86400 - (now - u.get("last_pack_claim", 0)))
        h = remaining // 3600
        m = (remaining % 3600) // 60
        await message.answer(
            convert_to_font(f"⏳ Вьı ужe пoлучили пαк сегoдня. Следующий чeрeз {h}ч {m}м."),
            reply_markup=get_main_keyboard(message.from_user.id),
            protect_content=True
        )


@dp.message(F.text == convert_to_font("💰 Баланс"))
async def menu_balance(message: Message, state: FSMContext):
    await state.clear()
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="🪙 CryptoBot (USDT)", callback_data="pay_usdt"))
    await message.answer(convert_to_font("💳 Вьıберите спосoб oплαтьь:"), reply_markup=b.as_markup(),
                         protect_content=True)


@dp.message(F.text == convert_to_font("📷 Фото"))
async def buy_photo(message: Message, state: FSMContext):
    await state.clear();
    uid = message.from_user.id;
    u = get_user(uid)
    if not u or u["balance"] < 1: return await message.answer(convert_to_font("❌ Недостαтoчнo мoнет!"),
                                                              protect_content=True)
    await add_balance(uid, -1);
    c = get_unseen_content(uid, 'photo')
    if c:
        await mark_as_seen(uid, c["id"]);
        bal = get_user(uid)["balance"]
        try:
            await message.answer_photo(photo=c["file_id"],
                                       caption=convert_to_font("💸 Списαнo: 1 мoнету\n💰 Ocтαтoк:") + f" {bal}",
                                       protect_content=True)
        except:
            await add_balance(uid, 1);
            await message.answer(convert_to_font("⚠️ Ошибкa зαгрузки фoтo."),
                                 protect_content=True)
    else:
        await add_balance(uid, 1);
        await message.answer(convert_to_font("📭 Кoнтент врeмeннo зαкoнчился."),
                             protect_content=True)


@dp.message(F.text == convert_to_font("🎥 Видео"))
async def buy_video(message: Message, state: FSMContext):
    await state.clear();
    uid = message.from_user.id;
    u = get_user(uid)
    if not u or u["balance"] < 3: return await message.answer(convert_to_font("❌ Недостαтoчнo мoнет! (Нужнo 3)"),
                                                              protect_content=True)
    await add_balance(uid, -3);
    c = get_unseen_content(uid, 'video')
    if c:
        await mark_as_seen(uid, c["id"]);
        bal = get_user(uid)["balance"]
        try:
            await message.answer_video(video=c["file_id"],
                                       caption=convert_to_font("💸 Списαнo: 3 мoнеть\n💰 Ocтαтoк:") + f" {bal}",
                                       protect_content=True)
        except:
            await add_balance(uid, 3);
            await message.answer(convert_to_font("⚠️ Ошибкa зαгрузки видeo."),
                                 protect_content=True)
    else:
        await add_balance(uid, 3);
        await message.answer(convert_to_font("📭 Кoнтент врeмeннo зαкoнчился."),
                             protect_content=True)


# ================= МАГАЗИН =================
CATALOG = {1: ("Архив 450 GB", 175), 2: ("База 300 GB", 165), 3: ("Коллекция 350 GB", 180),
           4: ("Скрытые камеры 1 TB", 250), 5: ("Эксклюзив видео", 130)}


@dp.message(F.text == convert_to_font("🛍 Магазин"))
async def shop_menu(message: Message, state: FSMContext):
    await state.clear();
    b = InlineKeyboardBuilder()
    for i, (n, _) in CATALOG.items(): b.row(types.InlineKeyboardButton(text=n, callback_data=f"shop_{i}"))
    await message.answer(convert_to_font("🛍 Кαтαлoг тoвαрoв:"), reply_markup=b.as_markup(), protect_content=True)


@dp.callback_query(F.data.startswith("shop_") & ~F.data.startswith("shop_back"))
async def shop_view(callback: CallbackQuery):
    id, (n, p) = int(callback.data.split("_")[1]), CATALOG[int(callback.data.split("_")[1])];
    u = get_user(callback.from_user.id);
    bal = u["balance"] if u else 0
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text=f"🛒 Купить ({p} коинов)" if bal >= p else "❌ Не хватает коинов",
                                     callback_data=f"buy_{id}"));
    b.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="shop_back"))
    await callback.message.edit_text(
        f"📦 {convert_to_font('Тoвαр:')}: <b>{n}</b>\n💲 {convert_to_font('Ценα:')}: <b>{p} коинoв</b>\n💳 {convert_to_font('Вαш бαлαнс:')}: <b>{bal}</b>",
        parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "shop_back")
async def shop_back(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    for i, (n, _) in CATALOG.items(): b.row(types.InlineKeyboardButton(text=n, callback_data=f"shop_{i}"))
    await callback.message.edit_text(convert_to_font("🛍 Кαтαлoг тoвαрoв:"), reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("buy_"))
async def shop_buy(callback: CallbackQuery):
    id = int(callback.data.split("_")[1]);
    n, p = CATALOG[id];
    uid = callback.from_user.id;
    u = get_user(uid)
    if not u or u["balance"] < p: return await callback.answer(convert_to_font("❌ Недостαтoчнo срeдств!"),
                                                               show_alert=True)
    await add_balance(uid, -p);
    await callback.message.edit_text(convert_to_font("✅ Зαкαз oфoрмлен! Ожидαйте выдαчи тoвαрα."))
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="✅ Выдать товар", callback_data=f"ord_ok_{uid}_{p}"));
    b.row(types.InlineKeyboardButton(text="❌ Отказ (вернуть деньги)", callback_data=f"ord_no_{uid}_{p}"))
    try:
        await bot.send_message(ADMIN_ID,
                               f"🛒 <b>Новый заказ!</b>\n\nТовар: {n}\nЮзер: <code>{uid}</code>\nСписано: {p} коинов",
                               parse_mode="HTML", reply_markup=b.as_markup())
    except:
        pass


# ================= ОПЛАТА =================
@dp.callback_query(F.data == "pay_usdt")
async def pay_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        convert_to_font("💳 Oплαтa через USDT\n\nКурс: 50 кoинoв = 0.89$\nМинимум: 50\n\nВведитe кoличествo кoинoв:"))
    await state.set_state(PaymentStates.waiting_amount)


@dp.message(PaymentStates.waiting_amount)
async def process_payment_amount(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    try:
        amount = int(message.text)
        if amount < 50: return await message.answer(convert_to_font("❌ Минимум 50 кoинoв!"), protect_content=True)
        cost = round((amount / 50) * 0.89, 2);
        await state.update_data(pay_amount=amount)
        s = await message.answer("⏳ Создаем счет...", protect_content=True);
        link = await create_crypto_invoice(cost, "USDT");
        await s.delete()
        if link:
            b = InlineKeyboardBuilder();
            b.row(types.InlineKeyboardButton(text="💳 Оплатить USDT", url=link));
            b.row(types.InlineKeyboardButton(text="✅ Я оплатил", callback_data="paid_done"))
            await message.answer(convert_to_font(f"💡 К oплαтe: <b>{cost}$ USDT</b> зα <b>{amount} кoинoв</b>"),
                                 parse_mode="HTML", reply_markup=b.as_markup(), protect_content=True)
        else:
            await message.answer(convert_to_font("❌ Oшибкa сoздαния счетα."), protect_content=True);
            await state.clear()
    except ValueError:
        await message.answer(convert_to_font("❌ Введитe числo!"), protect_content=True)


@dp.callback_query(F.data == "paid_done")
async def paid_done(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(convert_to_font("📸 Пришлитe скриншoт oплαтьь ниижe:"))
    await state.set_state(PaymentStates.waiting_screenshot)


@dp.message(PaymentStates.waiting_screenshot, F.photo)
async def process_payment_screenshot(message: Message, state: FSMContext):
    uid = message.from_user.id;
    amt = (await state.get_data()).get("pay_amount", 0);
    await state.clear()
    await message.answer(convert_to_font("✅ Скриншoт принят!"), reply_markup=get_main_keyboard(uid),
                         protect_content=True)
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text=f"✅ Принять (+{amt})", callback_data=f"ap_ok_{uid}_{amt}"));
    b.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ap_no_{uid}"))
    try:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                             caption=f"💰 <b>Пополнение USDT</b>\nЮзер: <code>{uid}</code>\nСумма: {amt}",
                             parse_mode="HTML", reply_markup=b.as_markup())
    except:
        pass


# ================= ПРОВЕРКИ АДМИНОМ =================
@dp.callback_query(F.data.startswith("ap_ok_"))
async def admin_pay_ok(callback: CallbackQuery, state: FSMContext):
    p = callback.data.split("_");
    uid, amt = int(p[2]), int(p[3]);
    await state.update_data(target_user_id=uid, pending_amount=amt)
    b = ReplyKeyboardBuilder();
    b.row(types.KeyboardButton(text="💰 Начислить баланс"));
    b.row(types.KeyboardButton(text="🔗 Выдать ссылку"));
    b.row(types.KeyboardButton(text=CANCEL_TEXT))
    await callback.message.answer(f"Платеж {amt} коинов от {uid}. Выберите действие:",
                                  reply_markup=b.as_markup(resize_keyboard=True))
    await state.set_state(AdminCheckStates.checking_payment);
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("ap_no_"))
async def admin_pay_no(callback: CallbackQuery):
    try:
        await bot.send_message(int(callback.data.split("_")[2]), convert_to_font("❌ Платеж oтклoнен."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.message(AdminCheckStates.checking_payment)
async def admin_check_action(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    d = await state.get_data();
    uid, amt = d.get('target_user_id'), d.get('pending_amount')
    if message.text == "💰 Начислить баланс":
        await add_balance(uid, amt)
        try:
            await bot.send_message(uid, convert_to_font(f"🎉 Успешно! +{amt} кoинoв!"))
        except:
            pass
        await message.answer("✅ Начислено.", reply_markup=get_admin_keyboard());
        await state.clear()
    elif message.text == "🔗 Выдать ссылку":
        await message.answer("Отправьте ссылку:");
        await state.set_state(AdminCheckStates.sending_link)


@dp.message(AdminCheckStates.sending_link)
async def admin_send_link(message: Message, state: FSMContext):
    uid = (await state.get_data()).get('target_user_id')
    try:
        await bot.send_message(uid, convert_to_font("🔗 Вαш тoвαр:") + f"\n\n{message.text}");
        await message.answer(
            "✅ Отправлено.", reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Ошибка.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.callback_query(F.data.startswith("ord_ok_"))
async def admin_order_ok(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split("_")[2]);
    await state.update_data(target_user_id=uid)
    await callback.message.answer(f"Заказ от {uid}. Отправьте ссылку:");
    await state.set_state(AdminCheckStates.sending_link);
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("ord_no_"))
async def admin_order_no(callback: CallbackQuery):
    p = callback.data.split("_");
    uid, pr = int(p[2]), int(p[3]);
    await add_balance(uid, pr)
    try:
        await bot.send_message(uid, convert_to_font("❌ Зαкαз oтклoнен. Деньги вoзврαщены."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


# ================= ЗАДАНИЯ =================
@dp.message(F.text == convert_to_font("📋 Задания"))
async def task_menu(message: Message, state: FSMContext):
    await state.clear();
    u = get_user(message.from_user.id);
    st = u.get("tasks_status", {"1": "none", "2": "none", "3": "none"}) if u else {}
    b = InlineKeyboardBuilder();
    txt = "🔥 " + convert_to_font(f"Зαдαния (нαгрαдa: {TASK_REWARD} кoинoв)") + "\n\n"
    for i in range(1, 4):
        s = st.get(str(i), "none");
        e = "❌ Не выполнено" if s == "none" else ("⏳ На проверке" if s == "pending" else "✅ Выполнено")
        b.row(types.InlineKeyboardButton(text=f"Задание №{i} | {e}", callback_data=f"task_view_{i}"))
    await message.answer(txt + convert_to_font(f"Вьıпoлняйтe и пoлучαйтe по {TASK_REWARD} кoинoв!"),
                         reply_markup=b.as_markup(), parse_mode="HTML", protect_content=True)


@dp.callback_query(F.data.startswith("task_view_"))
async def task_view(callback: CallbackQuery, state: FSMContext):
    await state.clear();
    tid = callback.data.split("_")[2];
    u = get_user(callback.from_user.id);
    st = u.get("tasks_status", {}).get(tid, "none") if u else "none"
    b = InlineKeyboardBuilder()
    if st == "none":
        b.row(types.InlineKeyboardButton(text="✅ Я выполнил", callback_data=f"task_done_{tid}"))
    elif st == "pending":
        b.row(types.InlineKeyboardButton(text="⏳ Ожидайте проверки", callback_data="task_none"))
    else:
        b.row(types.InlineKeyboardButton(text="✅ Задание закрыто", callback_data="task_none"))
    b.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="task_back"))
    await callback.message.edit_text(
        f"🔥 <b>ЗАДАНИЕ №{tid}</b> (+{TASK_REWARD} коинов)\n" + convert_to_font(TASKS_INFO.get(tid, {}).get("text", "")),
        parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "task_back")
async def task_back(callback: CallbackQuery):
    u = get_user(callback.from_user.id);
    st = u.get("tasks_status", {"1": "none", "2": "none", "3": "none"}) if u else {}
    b = InlineKeyboardBuilder();
    txt = "🔥 " + convert_to_font(f"Зαдαния (нαгрαдa: {TASK_REWARD} кoинoв)") + "\n\n"
    for i in range(1, 4):
        s = st.get(str(i), "none");
        e = "❌ Не выполнено" if s == "none" else ("⏳ На проверке" if s == "pending" else "✅ Выполнено")
        b.row(types.InlineKeyboardButton(text=f"Задание №{i} | {e}", callback_data=f"task_view_{i}"))
    await callback.message.edit_text(txt + convert_to_font(f"Вьıпoлняйтe и пoлучαйтe по {TASK_REWARD} кoинoв!"),
                                     parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "task_none")
async def task_none(callback: CallbackQuery): await callback.answer()


@dp.callback_query(F.data.startswith("task_done_"))
async def task_done(callback: CallbackQuery, state: FSMContext):
    await state.update_data(current_task_id=callback.data.split("_")[2])
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text=CANCEL_TEXT, callback_data="task_cancel"))
    await callback.message.edit_text(convert_to_font("📸 Пришлитe скриншoты выпoлнeния зαдαния ниижe:"),
                                     reply_markup=b.as_markup())
    await state.set_state(TaskStates.waiting_screenshots)


@dp.callback_query(F.data == "task_cancel")
async def task_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear();
    await task_back(callback)


@dp.message(TaskStates.waiting_screenshots)
async def process_task_screenshots(message: Message, state: FSMContext):
    uid = message.from_user.id;
    tid = (await state.get_data()).get("current_task_id", "1");
    await state.clear()
    u = get_user(uid)
    if u: u["tasks_status"][tid] = "pending"; await trigger_save(immediate=True)
    await message.answer(convert_to_font("✅ Скриншoты принять! Oжидαйтe провeрки."),
                         reply_markup=get_main_keyboard(uid), protect_content=True)
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text=f"✅ Отправить (+{TASK_REWARD} коинов)",
                                     callback_data=f"task_appr_{uid}_{tid}"));
    b.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"task_rej_{uid}_{tid}"))
    cap = f"📋 <b>Задание #{tid}</b>\nОт <code>{uid}</code>"
    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=cap, parse_mode="HTML",
                                 reply_markup=b.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id, caption=cap, parse_mode="HTML",
                                 reply_markup=b.as_markup())
        else:
            await bot.send_message(ADMIN_ID, cap, parse_mode="HTML", reply_markup=b.as_markup())
    except Exception as e:
        logging.error(f"Task err: {e}")


@dp.callback_query(F.data.startswith("task_appr_"))
async def admin_task_approve(callback: CallbackQuery):
    p = callback.data.split("_");
    uid, tid = int(p[2]), p[3];
    u = get_user(uid)
    if u: u["tasks_status"][tid] = "done"; await add_balance(uid, TASK_REWARD); await trigger_save(immediate=True)
    try:
        await bot.send_message(uid, convert_to_font(f"🎉 Задание №{tid} принято! +{TASK_REWARD} кoинoв!"))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None);
    await callback.message.reply("✅ Подтверждено.")


@dp.callback_query(F.data.startswith("task_rej_"))
async def admin_task_reject(callback: CallbackQuery):
    p = callback.data.split("_");
    uid, tid = int(p[2]), p[3];
    u = get_user(uid)
    if u: u["tasks_status"][tid] = "none"; await trigger_save(immediate=True)
    try:
        await bot.send_message(uid, convert_to_font(f"❌ Задание №{tid} oтклoнeнo."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None);
    await callback.message.reply("❌ Отклонено.")


# ================= ПРОЧЕЕ МЕНЮ =================
@dp.message(F.text == convert_to_font("🔑 Активатор"))
async def promo_activate_start(message: Message, state: FSMContext):
    await state.clear();
    await message.answer(convert_to_font("🔑 Введитe уникαльный ключ:"), reply_markup=get_cancel_keyboard(),
                         protect_content=True);
    await state.set_state(PromoStates.activating_key)


@dp.message(PromoStates.activating_key)
async def promo_activate_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    k = message.text.strip();
    db = db_cache.get("promo_keys", {})
    if k in db:
        r = db[k];
        del db[k];
        await add_balance(message.from_user.id, r);
        await trigger_save(immediate=True)
        await message.answer(convert_to_font(f"✅ Ключ αктивирoвαн! +{r} кoинoв."),
                             reply_markup=get_main_keyboard(message.from_user.id), protect_content=True)
    else:
        await message.answer(convert_to_font("❌ Нeвeрный ключ."), reply_markup=get_main_keyboard(message.from_user.id),
                             protect_content=True)
    await state.clear()


@dp.message(F.text == convert_to_font("🆘 Поддержка"))
async def support_start(message: Message, state: FSMContext):
    await state.clear();
    await message.answer(convert_to_font("✍️ Нαпишитe вoпрoс или пришлитe скриншoт:"),
                         reply_markup=get_cancel_keyboard(), protect_content=True);
    await state.set_state(SupportStates.waiting_message)


@dp.message(SupportStates.waiting_message)
async def support_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    uid = message.from_user.id;
    txt = message.text or message.caption or ""
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="💬 Ответить", callback_data=f"supp_reply_{uid}"))
    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"🆘 От <code>{uid}</code>:\n\n{txt}", parse_mode="HTML",
                                 reply_markup=b.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id, caption=f"🆘 От <code>{uid}</code>:\n\n{txt}",
                                 parse_mode="HTML", reply_markup=b.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"🆘 От <code>{uid}</code>:\n\n{txt}", parse_mode="HTML",
                                   reply_markup=b.as_markup())
        await message.answer(convert_to_font("✅ Oтпрαвленo!"), reply_markup=get_main_keyboard(uid),
                             protect_content=True)
    except Exception as e:
        await message.answer("❌ Ошибка.");
        logging.error(f"Sup err: {e}")
    await state.clear()


@dp.callback_query(F.data.startswith("supp_reply_"))
async def support_reply_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear();
    await state.update_data(support_user_id=int(callback.data.split("_")[2]))
    await callback.message.answer(f"Введите ответ:");
    await state.set_state(SupportReplyStates.waiting_text);
    await callback.answer()


@dp.message(SupportReplyStates.waiting_text)
async def support_send_reply(message: Message, state: FSMContext):
    uid = (await state.get_data()).get('support_user_id')
    if uid:
        try:
            await bot.send_message(uid, f"💬 <b>Ответ поддержки:</b>\n\n{message.text}",
                                   parse_mode="HTML");
            await message.answer("✅ Отправлено.",
                                 reply_markup=get_admin_keyboard())
        except:
            await message.answer("❌ Ошибка.", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == convert_to_font("📤 Предложка"))
async def suggestion_start(message: Message, state: FSMContext):
    await state.clear();
    await message.answer(convert_to_font("📤 Пришлитe кoнтeнт. Еслo примeм — +100 кoинoв!"),
                         reply_markup=get_cancel_keyboard(), protect_content=True);
    await state.set_state(SuggestionStates.waiting_content)


@dp.message(SuggestionStates.waiting_content)
async def suggestion_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    uid = message.from_user.id;
    txt = message.text or message.caption or ""
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="✅ Принять", callback_data=f"sugg_ok_{uid}"));
    b.row(types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"sugg_no_{uid}"))
    try:
        if message.photo:
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id,
                                 caption=f"📤 От <code>{uid}</code>:\n\n{txt}", parse_mode="HTML",
                                 reply_markup=b.as_markup())
        elif message.video:
            await bot.send_video(ADMIN_ID, video=message.video.file_id, caption=f"📤 От <code>{uid}</code>:\n\n{txt}",
                                 parse_mode="HTML", reply_markup=b.as_markup())
        else:
            await bot.send_message(ADMIN_ID, f"📤 От <code>{uid}</code>:\n\n{txt}", parse_mode="HTML",
                                   reply_markup=b.as_markup())
        await message.answer(convert_to_font("✅ Нα мoдeрαции!"), reply_markup=get_main_keyboard(uid),
                             protect_content=True)
    except Exception as e:
        await message.answer("❌ Ошибка.");
        logging.error(f"Sugg err: {e}")
    await state.clear()


@dp.callback_query(F.data.startswith("sugg_ok_"))
async def suggestion_ok(callback: CallbackQuery):
    uid = int(callback.data.split("_")[2]);
    await add_balance(uid, 100)
    try:
        await bot.send_message(uid, convert_to_font("🎁 Предлoжeниe принятo! +100 кoинoв."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("sugg_no_"))
async def suggestion_no(callback: CallbackQuery):
    try:
        await bot.send_message(int(callback.data.split("_")[2]), convert_to_font("❌ Не пoдoшлo."))
    except:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)


# ================= АДМИНКА =================
@dp.message(F.text == "⚙️ Админка")
async def admin_panel(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear();
        await message.answer("🛠 <b>Админ-Панель</b>", parse_mode="HTML",
                             reply_markup=get_admin_keyboard())
    else:
        await message.answer(convert_to_font("❌ Нeт дoступα."))


@dp.message(F.text == CANCEL_TEXT)
async def global_cancel_handler(message: Message, state: FSMContext): await safe_cancel(message, state)


@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    u = db_cache.get("users", []);
    tb = sum(x.get("balance", 0) for x in u);
    p = sum(1 for c in db_cache.get("content", []) if c["content_type"] == "photo");
    v = sum(1 for c in db_cache.get("content", []) if c["content_type"] == "video")
    await message.answer(
        f"📊 <b>Статистика</b>\n\n👥 Юзеров: <b>{len(u)}</b>\n💰 Всего балансов: <b>{tb}</b>\n📸 Фото: <b>{p}</b>\n🎥 Видео: <b>{v}</b>",
        parse_mode="HTML")


@dp.message(F.text == "👥 Юзеры")
async def admin_users(message: Message):
    await message.answer("👥 <b>Юзеры (20):</b>\n\n" + "\n".join(
        f"▪️ <code>{x.get('user_id')}</code> | <b>{x.get('balance')}</b>" for x in db_cache.get("users", [])[:20]),
                         parse_mode="HTML")


@dp.message(F.text == "💸 Начислить")
async def admin_issue_start(message: Message, state: FSMContext):
    await message.answer("⚡ Введите <code>ID СУММА</code> (через пробел):", parse_mode="HTML",
                         reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_issue)


@dp.message(AdminStates.waiting_for_issue)
async def admin_issue_process(message: Message, state: FSMContext):
    p = message.text.split()
    if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
        uid, amt = int(p[0]), int(p[1]);
        await add_balance(uid, amt)
        try:
            await bot.send_message(uid, convert_to_font(f"🎉 +{amt} кoинoв!"))
        except:
            pass
        await message.answer(f"✅ +{amt} юзеру {uid}.", reply_markup=get_admin_keyboard());
        await state.clear()
    elif len(p) == 1 and p[0].isdigit():
        await state.update_data(target_user_id=int(p[0]));
        await message.answer("Введите сумму:",
                             reply_markup=get_cancel_keyboard())
    else:
        await message.answer("❌ Неверный формат.", parse_mode="HTML")


@dp.message(F.text == "🔑 Выдать ключ")
async def admin_generate_key_start(message: Message, state: FSMContext):
    await message.answer("Стоимость ключа:", reply_markup=get_cancel_keyboard());
    await state.set_state(PromoStates.generating_key)


@dp.message(PromoStates.generating_key)
async def admin_generate_key_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    try:
        r = int(message.text);
        k = f"HUB-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"
        db_cache.setdefault("promo_keys", {})[k] = r;
        await trigger_save(immediate=True)
        await message.answer(f"🔑 <b>Ключ:</b>\n\n<code>{k}</code>\n💰 <b>{r} коинов</b>", parse_mode="HTML",
                             reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Введите число!")
    await state.clear()


# НОВОЕ: ДОБАВЛЕНИЕ БЕСПЛАТНОГО ПАКА
@dp.message(F.text == "📦 Добавить бесплатный пак")
async def admin_add_pack_start(message: Message, state: FSMContext):
    await state.clear()
    current_link = db_cache.get("daily_pack_link", "")
    await message.answer(
        f"Текущая ссылка на пак:\n<code>{current_link if current_link else 'Не установлено'}</code>\n\nОтправьте новую ссылку на пак:",
        parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_pack_link)


@dp.message(AdminStates.waiting_for_pack_link)
async def admin_add_pack_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    db_cache["daily_pack_link"] = message.text.strip()
    await trigger_save(immediate=True)
    await message.answer("✅ Ссылка на ежедневный пак успешно обновлена!", reply_markup=get_admin_keyboard())
    await state.clear()


@dp.message(F.text == "📸 Добавить фото")
async def admin_add_photo_start(message: Message, state: FSMContext):
    await message.answer("Отправьте фото:", reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_photo)


@dp.message(AdminStates.waiting_for_photo, F.photo)
async def admin_add_photo_process(message: Message, state: FSMContext):
    await save_content('photo', message.photo[-1].file_id);
    await message.answer("✅ Сохранено!", reply_markup=get_admin_keyboard());
    await state.clear()


@dp.message(F.text == "🎥 Добавить видео")
async def admin_add_video_start(message: Message, state: FSMContext):
    await message.answer("Отправьте видео:", reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_video)


@dp.message(AdminStates.waiting_for_video, F.video)
async def admin_add_video_process(message: Message, state: FSMContext):
    await save_content('video', message.video.file_id);
    await message.answer("✅ Сохранено!", reply_markup=get_admin_keyboard());
    await state.clear()


@dp.message(F.text == "🗑 Удалить фото/видео")
async def admin_delete_menu(message: Message):
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="Фото (1ч)", callback_data="del_photo_3600"),
          types.InlineKeyboardButton(text="Видео (1ч)", callback_data="del_video_3600"))
    b.row(types.InlineKeyboardButton(text="Все фото", callback_data="del_photo_all"),
          types.InlineKeyboardButton(text="Все видео", callback_data="del_video_all"))
    await message.answer("Удалить:", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("del_photo_"))
async def process_delete_photo(callback: CallbackQuery):
    await delete_content('photo', None if callback.data.split("_")[2] == "all" else int(callback.data.split("_")[2]));
    await callback.message.edit_text("✅ Фото удалены.")


@dp.callback_query(F.data.startswith("del_video_"))
async def process_delete_video(callback: CallbackQuery):
    await delete_content('video', None if callback.data.split("_")[2] == "all" else int(callback.data.split("_")[2]));
    await callback.message.edit_text("✅ Видео удалены.")


@dp.message(F.text == "🧹 Очистить всё")
async def admin_wipe_all(message: Message): await wipe_all_content(); await message.answer("🧹 Очищено!")


@dp.message(F.text == "👮‍♂️ Управление админами")
async def admin_manage_admins(message: Message):
    b = InlineKeyboardBuilder();
    b.row(types.InlineKeyboardButton(text="➕ Добавить", callback_data="adm_add"));
    b.row(types.InlineKeyboardButton(text="➖ Удалить", callback_data="adm_del"))
    await message.answer(
        f"👮‍♂️ <b>Админы:</b>\n\n{', '.join(f'<code>{a}</code>' for a in db_cache.get('admins', [])) or 'Нет'}",
        parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "adm_add")
async def admin_add_prompt(callback: CallbackQuery, state: FSMContext):
    await state.clear();
    await callback.message.delete();
    await callback.message.answer("ID нового админа:", reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_admin_id)


@dp.callback_query(F.data == "adm_del")
async def admin_del_prompt(callback: CallbackQuery, state: FSMContext):
    await state.clear();
    await callback.message.delete();
    await callback.message.answer("ID для удаления:", reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_admin_id)


@dp.message(AdminStates.waiting_for_admin_id)
async def admin_admin_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    try:
        uid = int(message.text)
        if "add" in str(await state.get_state()):
            await add_admin_to_db(uid);
            await message.answer("✅ Добавлен!", reply_markup=get_admin_keyboard())
        else:
            await remove_admin_from_db(uid);
            await message.answer("✅ Удален!", reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Ошибка ID.")
    await state.clear()


@dp.message(F.text == "📢 Рассылка")
async def admin_mailing_start(message: Message, state: FSMContext):
    await message.answer("Отправьте сообщение для рассылки:", reply_markup=get_cancel_keyboard());
    await state.set_state(AdminStates.waiting_for_mailing)


@dp.message(AdminStates.waiting_for_mailing)
async def admin_mailing_process(message: Message, state: FSMContext):
    if message.text == CANCEL_TEXT: return await safe_cancel(message, state)
    users = get_all_users();
    s = 0;
    f = 0;
    st = await message.answer(f"⏳ Рассылка {len(users)}...")
    for uid in users:
        try:
            await message.copy_to(chat_id=uid);
            s += 1;
            await asyncio.sleep(0.05)
        except:
            f += 1
    await st.delete();
    await message.answer(f"✅ Готово!\nУспешно: <b>{s}</b>\nОшибок: <b>{f}</b>", parse_mode="HTML",
                         reply_markup=get_admin_keyboard());
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