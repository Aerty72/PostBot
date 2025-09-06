import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
import os
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "bot.db"

# ---------------- DATABASE ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            channel_id TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            user_id INTEGER,
            text TEXT,
            media TEXT,
            button_text TEXT,
            button_url TEXT
        )
        """)
        await db.commit()

async def set_channel(user_id, channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO users (user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))
        await db.commit()

async def get_channel(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_id FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def save_post(user_id, text, media=None, button_text=None, button_url=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM posts WHERE user_id=?", (user_id,))
        await db.execute(
            "INSERT INTO posts (user_id, text, media, button_text, button_url) VALUES (?, ?, ?, ?, ?)",
            (user_id, text, media, button_text, button_url)
        )
        await db.commit()

async def get_post(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT text, media, button_text, button_url FROM posts WHERE user_id=?", (user_id,)) as cursor:
            return await cursor.fetchone()

# ---------------- STATES ----------------
class PostForm(StatesGroup):
    set_channel = State()
    text = State()
    media = State()
    button = State()

# ---------------- KEYBOARDS ----------------
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Привязать канал", callback_data="set_channel")],
        [InlineKeyboardButton(text="✏ Создать пост", callback_data="new_post")]
    ])

def confirm_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="confirm_yes")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_no")]
    ])

# ---------------- HANDLERS ----------------
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Привет! 👋\nВыбери действие:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "set_channel")
async def cb_set_channel(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправь @username или ID канала:")
    await state.set_state(PostForm.set_channel)
    await callback.answer()

@dp.message(PostForm.set_channel)
async def save_channel_id(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    await set_channel(message.from_user.id, channel_id)
    await message.answer(f"✅ Канал {channel_id} привязан.", reply_markup=main_menu())
    await state.clear()

@dp.callback_query(F.data == "new_post")
async def cb_new_post(callback: CallbackQuery, state: FSMContext):
    channel = await get_channel(callback.from_user.id)
    if not channel:
        await callback.message.answer("❌ Сначала привяжи канал через кнопку '📌 Привязать канал'")
        return
    await callback.message.answer("Отправь текст поста:")
    await state.set_state(PostForm.text)
    await callback.answer()

@dp.message(PostForm.text)
async def get_text(message: Message, state: FSMContext):
    await state.update_data(text=message.html_text)
    await message.answer("Отправь фото/видео или напиши 'нет':")
    await state.set_state(PostForm.media)

@dp.message(PostForm.media)
async def get_media(message: Message, state: FSMContext):
    media_id = None
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.video:
        media_id = message.video.file_id
    elif message.text.lower() == "нет":
        media_id = None
    await state.update_data(media=media_id)
    await message.answer("Хочешь кнопку? Напиши: Текст | https://ссылка или 'нет'")
    await state.set_state(PostForm.button)

@dp.message(PostForm.button)
async def get_button(message: Message, state: FSMContext):
    button_text, button_url = None, None
    if message.text.lower() != "нет":
        try:
            button_text, button_url = message.text.split("|", 1)
            button_text, button_url = button_text.strip(), button_url.strip()
        except ValueError:
            await message.answer("Неверный формат. Попробуй снова или напиши 'нет'")
            return
    await state.update_data(button_text=button_text, button_url=button_url)

    data = await state.get_data()
    await save_post(message.from_user.id, data["text"], data["media"], button_text, button_url)

    kb = None
    if button_text and button_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=button_url)]])

    if data["media"]:
        await bot.send_photo(message.chat.id, data["media"], caption=data["text"], reply_markup=kb)
    else:
        await message.answer(data["text"], reply_markup=kb)

    await message.answer("Опубликовать в канал?", reply_markup=confirm_menu())

@dp.callback_query(F.data == "confirm_yes")
async def cb_confirm_yes(callback: CallbackQuery, state: FSMContext):
    post = await get_post(callback.from_user.id)
    channel = await get_channel(callback.from_user.id)
    if post and channel:
        text, media, button_text, button_url = post
        kb = None
        if button_text and button_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=button_url)]])
        try:
            if media:
                await bot.send_photo(channel, media, caption=text, reply_markup=kb)
            else:
                await bot.send_message(channel, text, reply_markup=kb)
            await callback.message.answer("✅ Пост опубликован!", reply_markup=main_menu())
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка публикации: {e}", reply_markup=main_menu())
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "confirm_no")
async def cb_confirm_no(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("❌ Публикация отменена.", reply_markup=main_menu())
    await state.clear()
    await callback.answer()

# ---------------- MAIN ----------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())