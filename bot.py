import asyncio
import html
import logging
import os
import re
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ───────────────────────── НАСТРОЙКИ ─────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "9215874877:AAFDcn-YA2ROA8oL8Oppagb_lkHsy9NlF0Y")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "8961537132").split(",") if x.strip()}
CHANNEL_ID = os.getenv("CHANNEL_ID", "@agromd_technic")
BANNER_PATH = os.getenv("BANNER_PATH", "banner.jpg")
DB_PATH = os.getenv("DB_PATH", "ads.db")

CATEGORIES = {
    "🚜 Продам технику": "#продамтехнику",
    "🔎 Куплю технику": "#куплютехнику",
    "🔧 Запчасти": "#запчасти",
    "🛠 Услуги": "#услуги",
    "📅 Аренда": "#аренда",
}
MAX_DESC = 600  # подпись к фото в Telegram ограничена 1024 символами

logging.basicConfig(level=logging.INFO)
router = Router()


# ───────────────────────── БАЗА ДАННЫХ ─────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, username TEXT,
                category TEXT, description TEXT, city TEXT,
                price TEXT, phone TEXT, photo_id TEXT,
                status TEXT DEFAULT 'pending'
            )"""
        )


def save_ad(data: dict) -> int:
    with db() as c:
        cur = c.execute(
            """INSERT INTO ads (user_id, username, category, description,
               city, price, phone, photo_id) VALUES (?,?,?,?,?,?,?,?)""",
            (
                data["user_id"], data.get("username"), data["category"],
                data["description"], data["city"], data["price"],
                data["phone"], data.get("photo_id"),
            ),
        )
        return cur.lastrowid


def get_ad(ad_id: int):
    with db() as c:
        return c.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()


def set_status(ad_id: int, status: str) -> bool:
    """Меняет статус только если объявление ещё на модерации."""
    with db() as c:
        cur = c.execute(
            "UPDATE ads SET status=? WHERE id=? AND status='pending'",
            (status, ad_id),
        )
        return cur.rowcount == 1


# ───────────────────────── ТЕЛЕФОН ─────────────────────────
def normalize_md_phone(raw: str):
    """Приводит номер к виду +373XXXXXXXX или возвращает None."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("373") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 9:   # 069123456
        return "+373" + digits[1:]
    if len(digits) == 8:                              # 69123456
        return "+373" + digits
    return None


# ───────────────────────── ТЕКСТ ОБЪЯВЛЕНИЯ ─────────────────────────
def build_text(ad, for_admin: bool = False) -> str:
    esc = html.escape
    phone = ad["phone"]
    digits = phone.lstrip("+")

    lines = []
    if for_admin:
        lines.append("🔔 <b>Новое объявление на модерации!</b>\n")
    lines += [
        CATEGORIES.get(ad["category"], ""),
        "",
        f"🚜 <b>Описание:</b> {esc(ad['description'])}",
        f"📍 <b>Город/Район:</b> {esc(ad['city'])}",
        f"💰 <b>Цена:</b> {esc(ad['price'])}",
        f"📞 <b>Контакты:</b> {esc(phone)}",
        f'💬 <a href="https://wa.me/{digits}">WhatsApp</a> · '
        f'<a href="viber://chat?number=%2B{digits}">Viber</a>',
    ]
    if ad["username"]:
        lines.append(f'✈️ <a href="https://t.me/{esc(ad["username"])}">Написать в Telegram</a>')
    if for_admin:
        lines.append(f"\n👤 Отправитель: ID {ad['user_id']}")
    return "\n".join(lines)


async def send_ad(bot: Bot, chat_id, ad, text: str, markup=None):
    """Отправляет объявление с фото автора, баннером или просто текстом."""
    if ad["photo_id"]:
        return await bot.send_photo(chat_id, ad["photo_id"], caption=text, reply_markup=markup)
    if Path(BANNER_PATH).exists():
        return await bot.send_photo(chat_id, FSInputFile(BANNER_PATH), caption=text, reply_markup=markup)
    return await bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)


# ───────────────────────── ДИАЛОГ С АВТОРОМ ─────────────────────────
class AdForm(StatesGroup):
    category = State()
    description = State()
    city = State()
    price = State()
    phone = State()
    photo = State()


def categories_kb():
    rows = [[KeyboardButton(text=t)] for t in CATEGORIES]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
    )


def skip_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True
    )


@router.message(CommandStart())
@router.message(Command("new"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdForm.category)
    await m.answer(
        "Здравствуйте! Подадим объявление для канала.\nВыберите категорию:",
        reply_markup=categories_kb(),
    )


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено. Чтобы начать заново, нажмите /new", reply_markup=ReplyKeyboardRemove())


@router.message(AdForm.category, F.text.in_(CATEGORIES.keys()))
async def got_category(m: Message, state: FSMContext):
    await state.update_data(category=m.text)
    await state.set_state(AdForm.description)
    await m.answer(
        "Опишите технику или товар: марка, модель, год, наработка, состояние.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdForm.category)
async def bad_category(m: Message):
    await m.answer("Пожалуйста, выберите категорию кнопкой ниже.")


@router.message(AdForm.description, F.text)
async def got_description(m: Message, state: FSMContext):
    if len(m.text) > MAX_DESC:
        return await m.answer(f"Слишком длинно ({len(m.text)}). Сократите до {MAX_DESC} символов.")
    await state.update_data(description=m.text.strip())
    await state.set_state(AdForm.city)
    await m.answer("Город или район (например: Кишинёв, Бельцы, Тирасполь):")


@router.message(AdForm.city, F.text)
async def got_city(m: Message, state: FSMContext):
    await state.update_data(city=m.text.strip()[:80])
    await state.set_state(AdForm.price)
    await m.answer("Цена (например: 550 EUR, 12 000 лей, договорная):")


@router.message(AdForm.price, F.text)
async def got_price(m: Message, state: FSMContext):
    await state.update_data(price=m.text.strip()[:60])
    await state.set_state(AdForm.phone)
    await m.answer(
        "📞 Укажите номер телефона для связи (+373XXXXXXXX) "
        "или нажмите кнопку, чтобы отправить свой:",
        reply_markup=phone_kb(),
    )


@router.message(AdForm.phone, F.contact)
async def got_phone_contact(m: Message, state: FSMContext):
    await _save_phone(m, state, m.contact.phone_number)


@router.message(AdForm.phone, F.text)
async def got_phone_text(m: Message, state: FSMContext):
    await _save_phone(m, state, m.text)


async def _save_phone(m: Message, state: FSMContext, raw: str):
    phone = normalize_md_phone(raw)
    if not phone:
        return await m.answer("Не похоже на номер Молдовы. Введите в формате +373XXXXXXXX (8 цифр после 373).")
    await state.update_data(phone=phone)
    await state.set_state(AdForm.photo)
    await m.answer(
        "Пришлите фото (одно) или нажмите «Пропустить», тогда будет стандартная заставка.",
        reply_markup=skip_kb(),
    )


@router.message(AdForm.photo, F.photo)
async def got_photo(m: Message, state: FSMContext, bot: Bot):
    await state.update_data(photo_id=m.photo[-1].file_id)
    await finish(m, state, bot)


@router.message(AdForm.photo, F.text == "Пропустить")
async def skip_photo(m: Message, state: FSMContext, bot: Bot):
    await finish(m, state, bot)


@router.message(AdForm.photo)
async def bad_photo(m: Message):
    await m.answer("Пришлите фото или нажмите «Пропустить».")


async def finish(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    data["user_id"] = m.from_user.id
    data["username"] = m.from_user.username
    ad_id = save_ad(data)
    ad = get_ad(ad_id)

    await m.answer(
        "⏳ Ваше объявление принято и отправлено на модерацию администратору.\n"
        "Как только оно пройдёт проверку, оно будет опубликовано в канале!",
        reply_markup=ReplyKeyboardRemove(),
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить и опубликовать", callback_data=f"ok:{ad_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{ad_id}"),
        ]]
    )
    for admin_id in ADMIN_IDS:
        try:
            await send_ad(bot, admin_id, ad, build_text(ad, for_admin=True), kb)
        except Exception as e:
            logging.warning("Не удалось отправить карточку админу %s: %s", admin_id, e)


# ───────────────────────── МОДЕРАЦИЯ ─────────────────────────
async def _mark_card(cb: CallbackQuery, note: str):
    """Убирает кнопки и дописывает результат в карточку."""
    try:
        base = cb.message.caption or cb.message.text or ""
        if cb.message.caption is not None:
            await cb.message.edit_caption(caption=f"{base}\n\n{note}", reply_markup=None, parse_mode=None)
        else:
            await cb.message.edit_text(f"{base}\n\n{note}", reply_markup=None, parse_mode=None)
    except Exception:
        await cb.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("ok:"))
async def approve(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет прав", show_alert=True)
    ad_id = int(cb.data.split(":")[1])
    ad = get_ad(ad_id)
    if not ad or not set_status(ad_id, "approved"):
        return await cb.answer("Уже обработано", show_alert=True)

    try:
        await send_ad(bot, CHANNEL_ID, ad, build_text(ad))
    except Exception as e:
        with db() as c:
            c.execute("UPDATE ads SET status='pending' WHERE id=?", (ad_id,))
        logging.error("Ошибка публикации: %s", e)
        return await cb.answer(f"Не удалось опубликовать: {e}", show_alert=True)

    await _mark_card(cb, "✅ Опубликовано")
    await cb.answer("Опубликовано")
    try:
        await bot.send_message(ad["user_id"], "✅ Ваше объявление опубликовано в канале!")
    except Exception:
        pass


@router.callback_query(F.data.startswith("no:"))
async def reject(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет прав", show_alert=True)
    ad_id = int(cb.data.split(":")[1])
    ad = get_ad(ad_id)
    if not ad or not set_status(ad_id, "rejected"):
        return await cb.answer("Уже обработано", show_alert=True)

    await _mark_card(cb, "❌ Отклонено")
    await cb.answer("Отклонено")
    try:
        await bot.send_message(
            ad["user_id"],
            "❌ Ваше объявление не прошло модерацию. Проверьте данные и подайте снова: /new",
        )
    except Exception:
        pass


# ───────────────────────── ЗАПУСК ─────────────────────────
async def main():
    if not BOT_TOKEN or not ADMIN_IDS or not CHANNEL_ID:
        raise SystemExit("Задайте переменные BOT_TOKEN, ADMIN_IDS и CHANNEL_ID")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())