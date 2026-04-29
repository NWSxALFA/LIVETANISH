# ALFA TANISHUV CHATBOT v2
# Aiogram 3.x + SQLite + optional Redis cache
# Features: subscription gate, profiles, search filters, like/match, matched chat,
# random chat, stories, anonymous questions, reports, referral stats, balance,
# payments with admin approval, VIP, buy likes, TOP profile, daily coin bonus,
# admin statistics, broadcast, ban/unban.

import asyncio
import json
import math
import os
import sqlite3
from contextlib import suppress
from threading import Thread

from flask import Flask, jsonify
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

try:
    import redis.asyncio as redis
except Exception:
    redis = None

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "ALFA_TANISHUV_BOT").replace("@", "").strip()
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "5996676608").split(",") if x.strip()]
REDIS_URL = os.getenv("REDIS_URL", "").strip()

BOT_NAME = "ALFA TANISHUV CHATBOT"
REQUIRED_CHANNELS = ["@NWS_ALFA_007", "@ALFA_BONUS_NEWS"]
PAYMENT_LINK = "https://t.me/alfa_karta_id/13"
DB_PATH = os.getenv("DB_PATH", "alfa_tanishuv.db")
USERS_JSON_PATH = os.getenv("USERS_JSON_PATH", "users.json")
PORT = int(os.getenv("PORT", "10000"))

FREE_DAILY_LIKE_LIMIT = 10
VIP_DAILY_LIKE_LIMIT = 100
DAILY_BONUS_COINS = 300
COIN_TO_SOM_RATE = 1  # 1 coin = 1 so'm ichki qiymat

VIP_PRICES = {
    3: 3000,
    7: 5000,
    30: 15000,
    90: 35000,
}
LIKE_PACKS = {
    10: 1000,
    50: 4000,
    100: 7000,
}
TOP_PRICES = {
    1: 1000,
    24: 5000,
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env ichida yo‘q!")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return f"{BOT_NAME} ishlayapti ✅"


@web_app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": BOT_NAME}), 200


def run_web_server() -> None:
    web_app.run(host="0.0.0.0", port=PORT)

_db: Optional[aiosqlite.Connection] = None
_cache: Any = None


# ================= TIME / FORMAT =================

def now_dt() -> datetime:
    return datetime.now()


def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def fmt_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so‘m"


def safe_int(text: str, default: int = 0) -> int:
    try:
        return int(str(text).replace(" ", "").strip())
    except Exception:
        return default


# ================= DB HELPERS =================

async def db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database init qilinmagan")
    return _db


async def fetchone(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    con = await db()
    async with con.execute(query, params) as cur:
        return await cur.fetchone()


async def fetchall(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    con = await db()
    async with con.execute(query, params) as cur:
        return await cur.fetchall()


async def execute(query: str, params: tuple = (), commit: bool = True) -> aiosqlite.Cursor:
    con = await db()
    cur = await con.execute(query, params)
    if commit:
        await con.commit()
    return cur


async def init_database() -> None:
    global _db, _cache
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = sqlite3.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA cache_size=-20000")
    await _db.execute("PRAGMA foreign_keys=ON")

    await _db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            name TEXT,
            age INTEGER,
            gender TEXT,
            city TEXT,
            interests TEXT,
            bio TEXT,
            photo_id TEXT,
            balance INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            bonus_likes INTEGER DEFAULT 0,
            vip_until TEXT,
            top_until TEXT,
            hidden INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            joined_at TEXT,
            ref_by INTEGER,
            last_bonus TEXT,
            last_active TEXT,
            latitude REAL,
            longitude REAL
        );

        CREATE TABLE IF NOT EXISTS likes (
            from_id INTEGER,
            to_id INTEGER,
            created_at TEXT,
            UNIQUE(from_id, to_id)
        );

        CREATE TABLE IF NOT EXISTS matches (
            user1 INTEGER,
            user2 INTEGER,
            created_at TEXT,
            UNIQUE(user1, user2)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            to_id INTEGER,
            text TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            target_id INTEGER,
            reason TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            photo_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            admin_id INTEGER,
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            kind TEXT,
            note TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            photo_id TEXT,
            caption TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS story_likes (
            story_id INTEGER,
            user_id INTEGER,
            created_at TEXT,
            UNIQUE(story_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS random_queue (
            user_id INTEGER PRIMARY KEY,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS random_pairs (
            user1 INTEGER,
            user2 INTEGER,
            created_at TEXT,
            UNIQUE(user1, user2)
        );

        CREATE TABLE IF NOT EXISTS blocks (
            user_id INTEGER,
            blocked_id INTEGER,
            UNIQUE(user_id, blocked_id)
        );

        CREATE INDEX IF NOT EXISTS idx_users_search ON users(hidden, banned, gender, city, age);
        CREATE INDEX IF NOT EXISTS idx_users_top ON users(top_until);
        CREATE INDEX IF NOT EXISTS idx_likes_from_date ON likes(from_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_id, from_id);
        CREATE INDEX IF NOT EXISTS idx_matches_user1 ON matches(user1);
        CREATE INDEX IF NOT EXISTS idx_matches_user2 ON matches(user2);
        CREATE INDEX IF NOT EXISTS idx_stories_created ON stories(created_at);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
        """
    )
    await _db.commit()

    if redis and REDIS_URL:
        with suppress(Exception):
            _cache = redis.from_url(REDIS_URL, decode_responses=True)
            await _cache.ping()


# ================= CACHE =================

async def cache_get(key: str) -> Optional[str]:
    if not _cache:
        return None
    with suppress(Exception):
        return await _cache.get(key)
    return None


async def cache_set(key: str, value: str, ttl: int = 300) -> None:
    if not _cache:
        return
    with suppress(Exception):
        await _cache.setex(key, ttl, value)


async def cache_delete(key: str) -> None:
    if not _cache:
        return
    with suppress(Exception):
        await _cache.delete(key)


async def get_user(user_id: int) -> Optional[dict[str, Any]]:
    cached = await cache_get(f"user:{user_id}")
    if cached:
        with suppress(Exception):
            return json.loads(cached)

    row = await fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not row:
        return None
    data = dict(row)
    await cache_set(f"user:{user_id}", json.dumps(data, ensure_ascii=False), 300)
    return data


async def invalidate_user(user_id: int) -> None:
    await cache_delete(f"user:{user_id}")


async def export_users_json() -> None:
    """Foydalanuvchilarni users.json faylga saqlaydi.
    Asosiy saqlash SQLite bazada, bu fayl esa backup/ko‘rish uchun.
    """
    rows = await fetchall(
        """
        SELECT user_id, username, full_name, name, age, gender, city,
               interests, bio, balance, coins, bonus_likes, vip_until,
               top_until, hidden, banned, joined_at, ref_by, last_active
        FROM users
        ORDER BY joined_at DESC
        """
    )
    data = [dict(r) for r in rows]
    with open(USERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ================= STATES =================

class ProfileState(StatesGroup):
    name = State()
    age = State()
    gender = State()
    city = State()
    interests = State()
    bio = State()
    photo = State()


class PaymentState(StatesGroup):
    amount = State()
    photo = State()


class BroadcastState(StatesGroup):
    text = State()


class StoryState(StatesGroup):
    photo = State()
    caption = State()


class ReportState(StatesGroup):
    reason = State()


class AnonState(StatesGroup):
    text = State()


class MatchChatState(StatesGroup):
    target = State()
    text = State()


class SearchFilterState(StatesGroup):
    gender = State()
    city = State()
    min_age = State()
    max_age = State()


class AdminBalanceState(StatesGroup):
    user_id = State()
    amount = State()


# ================= KEYBOARDS =================

def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="👤 Profil"), KeyboardButton(text="🔍 Tanishuv qidirish")],
        [KeyboardButton(text="❤️ Matchlar"), KeyboardButton(text="🎲 Random chat")],
        [KeyboardButton(text="📸 Story"), KeyboardButton(text="📍 Yaqinimda kim bor")],
        [KeyboardButton(text="💬 Anonim savol"), KeyboardButton(text="💎 VIP")],
        [KeyboardButton(text="💰 Balans"), KeyboardButton(text="🎁 Kunlik bonus")],
        [KeyboardButton(text="👥 Referal"), KeyboardButton(text="⚙️ Sozlamalar")],
        [KeyboardButton(text="🛡 Shikoyat")],
    ]
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="👑 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def back_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🏠 Bosh menyu")]], resize_keyboard=True)


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.replace('@', '')}")] for ch in REQUIRED_CHANNELS]
    rows.append([InlineKeyboardButton(text="✅ Obuna bo‘ldim", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ Like", callback_data=f"like:{target_id}"),
            InlineKeyboardButton(text="❌ Keyingi", callback_data="next_profile"),
        ],
        [
            InlineKeyboardButton(text="💬 Anonim savol", callback_data=f"anon:{target_id}"),
            InlineKeyboardButton(text="🚫 Shikoyat", callback_data=f"report:{target_id}"),
        ],
    ])


def admin_panel() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Broadcast")],
            [KeyboardButton(text="🚨 Shikoyatlar"), KeyboardButton(text="💰 To‘lovlar")],
            [KeyboardButton(text="➕ Balans qo‘shish"), KeyboardButton(text="👤 User qidirish")],
            [KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
    )


def payment_admin_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{payment_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{payment_id}"),
    ]])


# ================= AUTH / GUARD =================

async def ensure_user(message: Message) -> None:
    u = message.from_user
    await execute(
        """
        INSERT OR IGNORE INTO users(user_id, username, full_name, joined_at, last_active)
        VALUES (?, ?, ?, ?, ?)
        """,
        (u.id, u.username, u.full_name, now_str(), now_str()),
        commit=False,
    )
    await execute(
        "UPDATE users SET username=?, full_name=?, last_active=? WHERE user_id=?",
        (u.username, u.full_name, now_str(), u.id),
        commit=True,
    )
    await invalidate_user(u.id)
    await export_users_json()


async def is_subscribed(user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                return False
        except Exception:
            return False
    return True


async def is_registered(user_id: int) -> bool:
    u = await get_user(user_id)
    return bool(u and u.get("name") and u.get("age") and u.get("photo_id"))


async def is_vip(user_id: int) -> bool:
    u = await get_user(user_id)
    until = parse_dt(u.get("vip_until") if u else None)
    return bool(until and until > now_dt())


async def is_top(user_id: int) -> bool:
    u = await get_user(user_id)
    until = parse_dt(u.get("top_until") if u else None)
    return bool(until and until > now_dt())


async def guard(message: Message) -> bool:
    await ensure_user(message)
    u = await get_user(message.from_user.id)
    if u and u.get("banned"):
        await message.answer("🚫 Siz botdan bloklangansiz.")
        return False
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            f"👋 <b>{BOT_NAME}</b> ga xush kelibsiz!\n\n"
            "Botdan foydalanish uchun quyidagi kanallarga obuna bo‘ling 👇",
            reply_markup=subscription_keyboard(),
        )
        return False
    return True


# ================= PROFILE / SEARCH HELPERS =================

async def profile_text(user: dict[str, Any]) -> str:
    vip = "💎 VIP" if await is_vip(int(user["user_id"])) else "Oddiy"
    top = "🚀 TOP" if await is_top(int(user["user_id"])) else ""
    hidden = "Yashirin" if user.get("hidden") else "Ko‘rinadi"
    return (
        f"👤 <b>{user.get('name')}</b>, {user.get('age')}\n"
        f"🚻 {user.get('gender')}\n"
        f"📍 {user.get('city')}\n"
        f"🎯 {user.get('interests')}\n"
        f"📝 {user.get('bio')}\n\n"
        f"Status: {vip} {top}\n"
        f"Profil: {hidden}"
    )


async def show_profile(chat_id: int, target: dict[str, Any]) -> None:
    await bot.send_photo(
        chat_id,
        target["photo_id"],
        caption=await profile_text(target),
        reply_markup=profile_keyboard(int(target["user_id"])),
    )


async def show_random_profile(user_id: int, chat_id: int, use_filter: bool = True) -> None:
    if not await is_registered(user_id):
        await bot.send_message(chat_id, "Avval profil yarating: 👤 Profil")
        return

    me = await get_user(user_id)
    query = [
        "SELECT * FROM users WHERE user_id != ?",
        "AND photo_id IS NOT NULL AND hidden=0 AND banned=0",
        "AND user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id=?)",
    ]
    params: list[Any] = [user_id, user_id]

    if use_filter and me:
        # Oddiy qidiruv: qarama-qarshi jins va shaharni birinchi o‘ringa chiqaradi.
        pass

    query.append("ORDER BY CASE WHEN top_until IS NOT NULL AND top_until > datetime('now','localtime') THEN 0 ELSE 1 END, RANDOM() LIMIT 1")
    row = await fetchone(" ".join(query), tuple(params))

    if not row:
        await bot.send_message(chat_id, "Hozircha mos profil topilmadi.")
        return
    await show_profile(chat_id, dict(row))


async def add_transaction(user_id: int, amount: int, kind: str, note: str) -> None:
    await execute(
        "INSERT INTO transactions(user_id, amount, kind, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, kind, note, now_str()),
    )


async def change_balance(user_id: int, amount: int, kind: str, note: str) -> None:
    await execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id), commit=False)
    await add_transaction(user_id, amount, kind, note)
    con = await db()
    await con.commit()
    await invalidate_user(user_id)


# ================= START =================

@dp.message(CommandStart())
async def start(message: Message) -> None:
    await ensure_user(message)
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        ref_id = safe_int(args[1], 0)
        if ref_id and ref_id != message.from_user.id:
            await execute("UPDATE users SET ref_by=? WHERE user_id=? AND ref_by IS NULL", (ref_id, message.from_user.id))
            await invalidate_user(message.from_user.id)

    if not await is_subscribed(message.from_user.id):
        await message.answer(
            f"👋 Assalomu alaykum!\n\n<b>{BOT_NAME}</b> ga xush kelibsiz ❤️\n\n"
            "Botdan foydalanish uchun kanallarga obuna bo‘ling:",
            reply_markup=subscription_keyboard(),
        )
        return

    await message.answer(f"✅ Xush kelibsiz!\n\n<b>{BOT_NAME}</b> ishga tushdi.", reply_markup=main_menu(message.from_user.id))


@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery) -> None:
    if await is_subscribed(call.from_user.id):
        await call.message.answer("✅ Obuna tasdiqlandi!", reply_markup=main_menu(call.from_user.id))
    else:
        await call.answer("❌ Hali barcha kanallarga obuna bo‘lmadingiz!", show_alert=True)


# ================= PROFILE =================

@dp.message(F.text == "👤 Profil")
async def profile(message: Message, state: FSMContext) -> None:
    if not await guard(message):
        return
    u = await get_user(message.from_user.id)
    if await is_registered(message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Profilni qayta yaratish", callback_data="edit_profile")]])
        await message.answer_photo(u["photo_id"], caption=await profile_text(u), reply_markup=kb)
        return
    await message.answer("👤 Ismingizni kiriting:", reply_markup=back_menu())
    await state.set_state(ProfileState.name)


@dp.callback_query(F.data == "edit_profile")
async def edit_profile(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer("👤 Ismingizni kiriting:", reply_markup=back_menu())
    await state.set_state(ProfileState.name)


@dp.message(ProfileState.name)
async def p_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()[:40]
    if len(name) < 2:
        await message.answer("Ism kamida 2 ta harfdan iborat bo‘lsin.")
        return
    await state.update_data(name=name)
    await message.answer("🎂 Yoshingizni kiriting:\n\n⚠️ Bot faqat 18+ foydalanuvchilar uchun.")
    await state.set_state(ProfileState.age)


@dp.message(ProfileState.age)
async def p_age(message: Message, state: FSMContext) -> None:
    age = safe_int(message.text, -1)
    if age < 18:
        await message.answer("❌ Kechirasiz, bot faqat 18+ foydalanuvchilar uchun.")
        return
    if age > 80:
        await message.answer("❌ Yosh noto‘g‘ri.")
        return
    await state.update_data(age=age)
    await message.answer(
        "🚻 Jinsingizni tanlang:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Erkak"), KeyboardButton(text="Ayol")]], resize_keyboard=True),
    )
    await state.set_state(ProfileState.gender)


@dp.message(ProfileState.gender)
async def p_gender(message: Message, state: FSMContext) -> None:
    if message.text not in {"Erkak", "Ayol"}:
        await message.answer("Erkak yoki Ayol tanlang.")
        return
    await state.update_data(gender=message.text)
    await message.answer("📍 Shaharingizni kiriting:", reply_markup=back_menu())
    await state.set_state(ProfileState.city)


@dp.message(ProfileState.city)
async def p_city(message: Message, state: FSMContext) -> None:
    await state.update_data(city=message.text.strip()[:40])
    await message.answer("🎯 Qiziqishlaringizni yozing:")
    await state.set_state(ProfileState.interests)


@dp.message(ProfileState.interests)
async def p_interests(message: Message, state: FSMContext) -> None:
    await state.update_data(interests=message.text.strip()[:120])
    await message.answer("📝 O‘zingiz haqingizda qisqa bio yozing:")
    await state.set_state(ProfileState.bio)


@dp.message(ProfileState.bio)
async def p_bio(message: Message, state: FSMContext) -> None:
    await state.update_data(bio=message.text.strip()[:250])
    await message.answer("📸 Profil rasmingizni yuboring:")
    await state.set_state(ProfileState.photo)


@dp.message(ProfileState.photo)
async def p_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("❌ Rasm yuboring.")
        return
    data = await state.get_data()
    photo_id = message.photo[-1].file_id
    await execute(
        """
        UPDATE users SET name=?, age=?, gender=?, city=?, interests=?, bio=?, photo_id=?, hidden=0
        WHERE user_id=?
        """,
        (data["name"], data["age"], data["gender"], data["city"], data["interests"], data["bio"], photo_id, message.from_user.id),
    )
    await invalidate_user(message.from_user.id)
    await export_users_json()
    await state.clear()
    await message.answer("✅ Profil yaratildi!", reply_markup=main_menu(message.from_user.id))


# ================= SEARCH / LIKE / MATCH =================

@dp.message(F.text == "🔍 Tanishuv qidirish")
async def search_profiles(message: Message) -> None:
    if not await guard(message):
        return
    await show_random_profile(message.from_user.id, message.chat.id)


@dp.callback_query(F.data == "next_profile")
async def next_profile(call: CallbackQuery) -> None:
    await show_random_profile(call.from_user.id, call.message.chat.id)


@dp.callback_query(F.data.startswith("like:"))
async def like_profile(call: CallbackQuery) -> None:
    from_id = call.from_user.id
    to_id = safe_int(call.data.split(":", 1)[1], 0)
    if not to_id or from_id == to_id:
        await call.answer("Noto‘g‘ri amal.")
        return

    vip = await is_vip(from_id)
    u = await get_user(from_id)
    bonus_likes = int(u.get("bonus_likes") or 0)

    row = await fetchone("SELECT COUNT(*) c FROM likes WHERE from_id=? AND date(created_at)=date('now','localtime')", (from_id,))
    used = int(row["c"] if row else 0)
    limit = VIP_DAILY_LIKE_LIMIT if vip else FREE_DAILY_LIKE_LIMIT

    if used >= limit and bonus_likes <= 0:
        await call.answer("Kunlik like limiti tugadi. VIP yoki like paket oling 💎", show_alert=True)
        return

    await execute("INSERT OR IGNORE INTO likes(from_id, to_id, created_at) VALUES (?, ?, ?)", (from_id, to_id, now_str()))
    if used >= limit and bonus_likes > 0:
        await execute("UPDATE users SET bonus_likes=bonus_likes-1 WHERE user_id=?", (from_id,))
        await invalidate_user(from_id)

    reverse = await fetchone("SELECT 1 FROM likes WHERE from_id=? AND to_id=?", (to_id, from_id))
    if reverse:
        a, b = sorted([from_id, to_id])
        await execute("INSERT OR IGNORE INTO matches(user1, user2, created_at) VALUES (?, ?, ?)", (a, b, now_str()))
        await call.message.answer("💖 MATCH! Endi matchlar bo‘limidan yozishishingiz mumkin.")
        with suppress(Exception):
            await bot.send_message(to_id, "💖 Sizda yangi MATCH bor! Matchlar bo‘limini tekshiring.")
    else:
        with suppress(Exception):
            await bot.send_message(to_id, "❤️ Sizga kimdir like bosdi!\n👀 Kimligini ko‘rish uchun VIP oling 💎")
    await call.answer("❤️ Like yuborildi!")


@dp.message(F.text == "❤️ Matchlar")
async def my_matches(message: Message) -> None:
    if not await guard(message):
        return
    rows = await fetchall("SELECT * FROM matches WHERE user1=? OR user2=? ORDER BY created_at DESC LIMIT 20", (message.from_user.id, message.from_user.id))
    if not rows:
        await message.answer("Hozircha match yo‘q.")
        return
    text = "❤️ <b>Sizning matchlaringiz:</b>\n\n"
    kb_rows = []
    for r in rows:
        other = int(r["user2"] if r["user1"] == message.from_user.id else r["user1"])
        u = await get_user(other)
        if u:
            username = f"@{u['username']}" if u.get("username") else f"ID: {other}"
            text += f"💖 {u.get('name')} — {username}\n"
            kb_rows.append([InlineKeyboardButton(text=f"💌 {u.get('name')} ga yozish", callback_data=f"match_write:{other}")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None)


@dp.callback_query(F.data.startswith("match_write:"))
async def match_write_start(call: CallbackQuery, state: FSMContext) -> None:
    target = safe_int(call.data.split(":", 1)[1], 0)
    a, b = sorted([call.from_user.id, target])
    exists = await fetchone("SELECT 1 FROM matches WHERE user1=? AND user2=?", (a, b))
    if not exists:
        await call.answer("Bu user bilan match yo‘q.", show_alert=True)
        return
    await state.update_data(target=target)
    await call.message.answer("💌 Xabaringizni yozing:", reply_markup=back_menu())
    await state.set_state(MatchChatState.text)


@dp.message(MatchChatState.text)
async def match_send(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = int(data["target"])
    text = message.text.strip()[:1000]
    await execute("INSERT INTO messages(from_id, to_id, text, created_at) VALUES (?, ?, ?, ?)", (message.from_user.id, target, text, now_str()))
    with suppress(Exception):
        sender = await get_user(message.from_user.id)
        await bot.send_message(target, f"💌 <b>{sender.get('name', 'Match')}</b> dan xabar:\n\n{text}")
    await state.clear()
    await message.answer("✅ Xabar yuborildi.", reply_markup=main_menu(message.from_user.id))


# ================= BALANCE / VIP / BUY =================

@dp.message(F.text == "💎 VIP")
async def vip_menu(message: Message) -> None:
    if not await guard(message):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 1 kun trial — bepul", callback_data="vip_trial")],
        *[[InlineKeyboardButton(text=f"💎 {days} kun — {fmt_money(price)}", callback_data=f"buy_vip:{days}")] for days, price in VIP_PRICES.items()],
    ])
    await message.answer(
        "💎 <b>VIP imkoniyatlar:</b>\n\n"
        "👀 Kim like bosganini ko‘rish\n"
        "❤️ 100 ta kunlik like\n"
        "🚀 Profil TOPda chiqish imkoniyati\n"
        "🔍 Kengaytirilgan qidiruv\n"
        "🎲 VIP random chat\n\n"
        "Tarifni tanlang:",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "vip_trial")
async def vip_trial(call: CallbackQuery) -> None:
    u = await get_user(call.from_user.id)
    if u and u.get("vip_until"):
        await call.answer("Trial yoki VIP oldin ishlatilgan.", show_alert=True)
        return
    until = now_dt() + timedelta(days=1)
    await execute("UPDATE users SET vip_until=? WHERE user_id=?", (until.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id))
    await invalidate_user(call.from_user.id)
    await call.message.answer("🎁 1 kunlik VIP trial yoqildi!")


@dp.callback_query(F.data.startswith("buy_vip:"))
async def buy_vip(call: CallbackQuery) -> None:
    days = safe_int(call.data.split(":", 1)[1], 0)
    price = VIP_PRICES.get(days)
    if not price:
        return
    u = await get_user(call.from_user.id)
    if int(u.get("balance") or 0) < price:
        await call.answer("Balans yetarli emas. Hisob to‘ldiring.", show_alert=True)
        return
    base = parse_dt(u.get("vip_until")) if await is_vip(call.from_user.id) else now_dt()
    until = (base or now_dt()) + timedelta(days=days)
    await execute("UPDATE users SET balance=balance-?, vip_until=? WHERE user_id=?", (price, until.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id), commit=False)
    await add_transaction(call.from_user.id, -price, "buy_vip", f"VIP {days} kun")
    con = await db()
    await con.commit()
    await invalidate_user(call.from_user.id)
    await call.message.answer(f"✅ VIP {days} kunga faollashtirildi!")


@dp.message(F.text == "💰 Balans")
async def balance(message: Message) -> None:
    if not await guard(message):
        return
    u = await get_user(message.from_user.id)
    vip_until = u.get("vip_until") or "Yo‘q"
    top_until = u.get("top_until") or "Yo‘q"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Hisob to‘ldirish", callback_data="topup")],
        [InlineKeyboardButton(text="❤️ 10 like — 1 000 so‘m", callback_data="buy_likes:10")],
        [InlineKeyboardButton(text="❤️ 50 like — 4 000 so‘m", callback_data="buy_likes:50")],
        [InlineKeyboardButton(text="❤️ 100 like — 7 000 so‘m", callback_data="buy_likes:100")],
        [InlineKeyboardButton(text="🚀 TOP 1 soat — 1 000 so‘m", callback_data="top_profile:1")],
        [InlineKeyboardButton(text="🚀 TOP 24 soat — 5 000 so‘m", callback_data="top_profile:24")],
    ])
    await message.answer(
        f"💰 Balans: <b>{fmt_money(int(u.get('balance') or 0))}</b>\n"
        f"🪙 Coin: <b>{u.get('coins') or 0}</b>\n"
        f"❤️ Bonus like: <b>{u.get('bonus_likes') or 0}</b>\n"
        f"💎 VIP gacha: <code>{vip_until}</code>\n"
        f"🚀 TOP gacha: <code>{top_until}</code>",
        reply_markup=kb,
    )


@dp.callback_query(F.data.startswith("buy_likes:"))
async def buy_likes(call: CallbackQuery) -> None:
    count = safe_int(call.data.split(":", 1)[1], 0)
    price = LIKE_PACKS.get(count)
    if not price:
        return
    u = await get_user(call.from_user.id)
    if int(u.get("balance") or 0) < price:
        await call.answer("Balans yetarli emas.", show_alert=True)
        return
    await execute("UPDATE users SET balance=balance-?, bonus_likes=bonus_likes+? WHERE user_id=?", (price, count, call.from_user.id), commit=False)
    await add_transaction(call.from_user.id, -price, "buy_likes", f"{count} like paketi")
    con = await db()
    await con.commit()
    await invalidate_user(call.from_user.id)
    await call.message.answer(f"✅ {count} ta bonus like qo‘shildi.")


@dp.callback_query(F.data.startswith("top_profile:"))
async def top_profile(call: CallbackQuery) -> None:
    hours = safe_int(call.data.split(":", 1)[1], 0)
    price = TOP_PRICES.get(hours)
    if not price:
        return
    if not await is_registered(call.from_user.id):
        await call.answer("Avval profil yarating.", show_alert=True)
        return
    u = await get_user(call.from_user.id)
    if int(u.get("balance") or 0) < price:
        await call.answer("Balans yetarli emas.", show_alert=True)
        return
    base = parse_dt(u.get("top_until")) if await is_top(call.from_user.id) else now_dt()
    until = (base or now_dt()) + timedelta(hours=hours)
    await execute("UPDATE users SET balance=balance-?, top_until=? WHERE user_id=?", (price, until.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id), commit=False)
    await add_transaction(call.from_user.id, -price, "top_profile", f"TOP {hours} soat")
    con = await db()
    await con.commit()
    await invalidate_user(call.from_user.id)
    await call.message.answer(f"🚀 Profilingiz {hours} soat TOPga chiqarildi!")


@dp.message(F.text == "🎁 Kunlik bonus")
async def daily_bonus(message: Message) -> None:
    if not await guard(message):
        return
    u = await get_user(message.from_user.id)
    last = parse_dt(u.get("last_bonus"))
    if last and now_dt() - last < timedelta(hours=24):
        left = timedelta(hours=24) - (now_dt() - last)
        hours = int(left.total_seconds() // 3600)
        minutes = int((left.total_seconds() % 3600) // 60)
        await message.answer(f"⏳ Bonus hali tayyor emas. Qoldi: {hours} soat {minutes} daqiqa.")
        return
    await execute("UPDATE users SET coins=coins+?, last_bonus=? WHERE user_id=?", (DAILY_BONUS_COINS, now_str(), message.from_user.id))
    await invalidate_user(message.from_user.id)
    await message.answer(f"🎁 Kunlik bonus olindi: +{DAILY_BONUS_COINS} coin")


@dp.callback_query(F.data == "topup")
async def topup(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer(
        "💳 <b>Hisob to‘ldirish</b>\n\n"
        f"1. Shu linkka kiring: {PAYMENT_LINK}\n"
        "2. Hohlagan kartaga to‘lov qiling\n"
        "3. To‘lov miqdorini yozing\n\nMasalan: 15000"
    )
    await state.set_state(PaymentState.amount)


@dp.message(PaymentState.amount)
async def payment_amount(message: Message, state: FSMContext) -> None:
    amount = safe_int(message.text, 0)
    if amount < 1000:
        await message.answer("Minimal to‘lov 1000 so‘m. Faqat raqam kiriting.")
        return
    await state.update_data(amount=amount)
    await message.answer("📸 Endi to‘lov screenshotini yuboring:")
    await state.set_state(PaymentState.photo)


@dp.message(PaymentState.photo)
async def payment_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Screenshot rasm ko‘rinishida bo‘lishi kerak.")
        return
    data = await state.get_data()
    cur = await execute(
        "INSERT INTO payments(user_id, amount, photo_id, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, data["amount"], message.photo[-1].file_id, now_str()),
    )
    payment_id = cur.lastrowid
    for admin_id in ADMIN_IDS:
        with suppress(Exception):
            await bot.send_photo(
                admin_id,
                message.photo[-1].file_id,
                caption=f"💰 <b>Yangi to‘lov!</b>\n\nID: {payment_id}\nUser: <code>{message.from_user.id}</code>\nMiqdor: {fmt_money(data['amount'])}",
                reply_markup=payment_admin_kb(payment_id),
            )
    await state.clear()
    await message.answer("✅ To‘lov adminga yuborildi. Tasdiqlanishini kuting.", reply_markup=main_menu(message.from_user.id))


@dp.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        return
    pid = safe_int(call.data.split(":", 1)[1], 0)
    p = await fetchone("SELECT * FROM payments WHERE id=?", (pid,))
    if not p or p["status"] != "pending":
        await call.answer("Bu to‘lov allaqachon ko‘rilgan.", show_alert=True)
        return
    await execute("UPDATE payments SET status='approved', admin_id=?, reviewed_at=? WHERE id=?", (call.from_user.id, now_str(), pid), commit=False)
    await change_balance(int(p["user_id"]), int(p["amount"]), "payment", f"To‘lov #{pid} tasdiqlandi")
    await call.message.answer("✅ To‘lov tasdiqlandi.")
    with suppress(Exception):
        await bot.send_message(int(p["user_id"]), f"✅ To‘lov tasdiqlandi!\n💰 Balansingizga {fmt_money(int(p['amount']))} qo‘shildi.")


@dp.callback_query(F.data.startswith("pay_no:"))
async def pay_no(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        return
    pid = safe_int(call.data.split(":", 1)[1], 0)
    p = await fetchone("SELECT * FROM payments WHERE id=?", (pid,))
    if not p:
        return
    await execute("UPDATE payments SET status='rejected', admin_id=?, reviewed_at=? WHERE id=?", (call.from_user.id, now_str(), pid))
    await call.message.answer("❌ To‘lov rad etildi.")
    with suppress(Exception):
        await bot.send_message(int(p["user_id"]), "❌ To‘lov rad etildi. Chekni tekshirib qayta yuboring.")


# ================= RANDOM CHAT =================

async def find_pair(user_id: int) -> Optional[sqlite3.Row]:
    return await fetchone("SELECT * FROM random_pairs WHERE user1=? OR user2=?", (user_id, user_id))


async def partner_of(user_id: int) -> Optional[int]:
    p = await find_pair(user_id)
    if not p:
        return None
    return int(p["user2"] if p["user1"] == user_id else p["user1"])


@dp.message(F.text == "🎲 Random chat")
async def random_chat(message: Message) -> None:
    if not await guard(message):
        return
    if not await is_registered(message.from_user.id):
        await message.answer("Avval profil yarating.")
        return
    if await find_pair(message.from_user.id):
        await message.answer("Siz allaqachon random chatdasiz. Xabar yozing yoki ❌ Stop bosing.")
        return
    waiting = await fetchone("SELECT * FROM random_queue WHERE user_id!=? ORDER BY created_at LIMIT 1", (message.from_user.id,))
    if waiting:
        other = int(waiting["user_id"])
        a, b = sorted([message.from_user.id, other])
        await execute("DELETE FROM random_queue WHERE user_id IN (?, ?)", (message.from_user.id, other), commit=False)
        await execute("INSERT OR IGNORE INTO random_pairs(user1, user2, created_at) VALUES (?, ?, ?)", (a, b, now_str()), commit=True)
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Stop random chat")], [KeyboardButton(text="🏠 Bosh menyu")]], resize_keyboard=True)
        await message.answer("🎲 Suhbatdosh topildi! Anonim yozishni boshlang.", reply_markup=kb)
        with suppress(Exception):
            await bot.send_message(other, "🎲 Suhbatdosh topildi! Anonim yozishni boshlang.", reply_markup=kb)
    else:
        await execute("INSERT OR REPLACE INTO random_queue(user_id, created_at) VALUES (?, ?)", (message.from_user.id, now_str()))
        await message.answer("⏳ Suhbatdosh qidirilmoqda...")


@dp.message(F.text == "❌ Stop random chat")
async def stop_random(message: Message) -> None:
    p = await find_pair(message.from_user.id)
    await execute("DELETE FROM random_queue WHERE user_id=?", (message.from_user.id,), commit=False)
    if p:
        other = await partner_of(message.from_user.id)
        await execute("DELETE FROM random_pairs WHERE user1=? AND user2=?", (p["user1"], p["user2"]), commit=True)
        await message.answer("❌ Random chat tugatildi.", reply_markup=main_menu(message.from_user.id))
        if other:
            with suppress(Exception):
                await bot.send_message(other, "❌ Suhbatdoshingiz chatni tugatdi.", reply_markup=main_menu(other))
    else:
        con = await db()
        await con.commit()
        await message.answer("❌ Qidiruv to‘xtatildi.", reply_markup=main_menu(message.from_user.id))


# ================= STORY / ANON / REPORT =================

@dp.message(F.text == "📸 Story")
async def story_menu(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer("📸 Story bo‘limi:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Story qo‘shish", callback_data="add_story")],
        [InlineKeyboardButton(text="👀 Storylarni ko‘rish", callback_data="view_story")],
    ]))


@dp.callback_query(F.data == "add_story")
async def add_story(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer("📸 Story uchun rasm yuboring:")
    await state.set_state(StoryState.photo)


@dp.message(StoryState.photo)
async def story_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Rasm yuboring.")
        return
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("📝 Story caption yozing yoki '-' yuboring:")
    await state.set_state(StoryState.caption)


@dp.message(StoryState.caption)
async def story_caption(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    caption = "" if message.text == "-" else message.text.strip()[:200]
    await execute("INSERT INTO stories(user_id, photo_id, caption, created_at) VALUES (?, ?, ?, ?)", (message.from_user.id, data["photo_id"], caption, now_str()))
    await state.clear()
    await message.answer("✅ Story qo‘shildi. 24 soat ko‘rinadi.", reply_markup=main_menu(message.from_user.id))


@dp.callback_query(F.data == "view_story")
async def view_story(call: CallbackQuery) -> None:
    limit_time = (now_dt() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    s = await fetchone("SELECT * FROM stories WHERE created_at>? AND user_id!=? ORDER BY RANDOM() LIMIT 1", (limit_time, call.from_user.id))
    if not s:
        await call.message.answer("Hozircha story yo‘q.")
        return
    await call.message.answer_photo(s["photo_id"], caption=f"📸 Story\n\n{s['caption'] or ''}")


@dp.callback_query(F.data.startswith("anon:"))
async def anon_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(target=safe_int(call.data.split(":", 1)[1], 0))
    await call.message.answer("💬 Anonim savolingizni yozing:")
    await state.set_state(AnonState.text)


@dp.message(F.text == "💬 Anonim savol")
async def anon_info(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer("💬 Anonim savol yuborish uchun qidiruvdan profil tanlab, “Anonim savol” tugmasini bosing.")


@dp.message(AnonState.text)
async def anon_send(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = int(data.get("target") or 0)
    if target:
        with suppress(Exception):
            await bot.send_message(target, f"💌 Sizga anonim savol keldi:\n\n<i>{message.text.strip()[:800]}</i>")
    await state.clear()
    await message.answer("✅ Anonim savol yuborildi.")


@dp.callback_query(F.data.startswith("report:"))
async def report_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(target=safe_int(call.data.split(":", 1)[1], 0))
    await call.message.answer("🛡 Shikoyat sababini yozing:\nSpam / Haqorat / Nomaqbul kontent / Fake / Boshqa")
    await state.set_state(ReportState.reason)


@dp.message(F.text == "🛡 Shikoyat")
async def report_info(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer("Shikoyat qilish uchun qidiruvdagi profil ostidan 🚫 Shikoyat tugmasini bosing.")


@dp.message(ReportState.reason)
async def report_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = int(data.get("target") or 0)
    await execute("INSERT INTO reports(from_id, target_id, reason, created_at) VALUES (?, ?, ?, ?)", (message.from_user.id, target, message.text.strip()[:500], now_str()))
    for admin_id in ADMIN_IDS:
        with suppress(Exception):
            await bot.send_message(admin_id, f"🚨 <b>Yangi shikoyat</b>\n\nKimdan: <code>{message.from_user.id}</code>\nKimga: <code>{target}</code>\nSabab: {message.text}")
    await state.clear()
    await message.answer("✅ Shikoyat adminga yuborildi.")


# ================= REF / SETTINGS / NEAR =================

@dp.message(F.text == "👥 Referal")
async def referral(message: Message) -> None:
    if not await guard(message):
        return
    row = await fetchone("SELECT COUNT(*) c FROM users WHERE ref_by=?", (message.from_user.id,))
    link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"
    await message.answer(f"👥 <b>Referal tizimi</b>\n\n🔗 Link:\n{link}\n\n📊 Taklif qilganlar: {row['c'] if row else 0}\n\n❗ Referal uchun bonus berilmaydi.")


@dp.message(F.text == "⚙️ Sozlamalar")
async def settings(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer("⚙️ Sozlamalar:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Profilni yashirish/ko‘rsatish", callback_data="toggle_hidden")],
        [InlineKeyboardButton(text="🗑 Accountni o‘chirish", callback_data="delete_account")],
    ]))


@dp.callback_query(F.data == "toggle_hidden")
async def toggle_hidden(call: CallbackQuery) -> None:
    u = await get_user(call.from_user.id)
    new_val = 0 if int(u.get("hidden") or 0) else 1
    await execute("UPDATE users SET hidden=? WHERE user_id=?", (new_val, call.from_user.id))
    await invalidate_user(call.from_user.id)
    await call.message.answer("✅ Profil holati o‘zgartirildi.")


@dp.callback_query(F.data == "delete_account")
async def delete_account(call: CallbackQuery) -> None:
    uid = call.from_user.id
    await execute("DELETE FROM users WHERE user_id=?", (uid,), commit=False)
    await execute("DELETE FROM likes WHERE from_id=? OR to_id=?", (uid, uid), commit=False)
    await execute("DELETE FROM matches WHERE user1=? OR user2=?", (uid, uid), commit=True)
    await invalidate_user(uid)
    await export_users_json()
    await call.message.answer("🗑 Accountingiz o‘chirildi.")


@dp.message(F.text == "📍 Yaqinimda kim bor")
async def near(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer("📍 Lokatsiya yuborish funksiyasini keyingi bosqichda aniq GPS bilan ulaymiz. Hozir qidiruv TOP va random tartibda ishlaydi.")


# ================= ADMIN =================

@dp.message(F.text == "👑 Admin panel")
async def admin(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("👑 Admin panel:", reply_markup=admin_panel())


@dp.message(F.text == "📊 Statistika")
async def stats(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    data = {}
    for key, query in {
        "users": "SELECT COUNT(*) c FROM users",
        "today": "SELECT COUNT(*) c FROM users WHERE date(joined_at)=date('now','localtime')",
        "vip": "SELECT COUNT(*) c FROM users WHERE vip_until IS NOT NULL AND vip_until > datetime('now','localtime')",
        "likes": "SELECT COUNT(*) c FROM likes",
        "matches": "SELECT COUNT(*) c FROM matches",
        "reports": "SELECT COUNT(*) c FROM reports WHERE status='new'",
        "income": "SELECT COALESCE(SUM(amount),0) c FROM payments WHERE status='approved'",
    }.items():
        row = await fetchone(query)
        data[key] = row["c"] if row else 0
    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Userlar: {data['users']}\n"
        f"🆕 Bugun: {data['today']}\n"
        f"💎 Aktiv VIP: {data['vip']}\n"
        f"❤️ Like: {data['likes']}\n"
        f"💖 Match: {data['matches']}\n"
        f"🚨 Yangi shikoyatlar: {data['reports']}\n"
        f"💰 Daromad: {fmt_money(int(data['income']))}"
    )


@dp.message(F.text == "📢 Broadcast")
async def broadcast_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("📢 Hammaga yuboriladigan xabarni yozing:")
    await state.set_state(BroadcastState.text)


@dp.message(BroadcastState.text)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await fetchall("SELECT user_id FROM users WHERE banned=0")
    sent = 0
    failed = 0
    for r in rows:
        try:
            await bot.send_message(int(r["user_id"]), message.text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.035)
    await state.clear()
    await message.answer(f"✅ Broadcast tugadi. Yuborildi: {sent}, xato: {failed}")


@dp.message(F.text == "🚨 Shikoyatlar")
async def reports_admin(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await fetchall("SELECT * FROM reports WHERE status='new' ORDER BY id DESC LIMIT 10")
    if not rows:
        await message.answer("Yangi shikoyat yo‘q.")
        return
    text = "🚨 <b>Yangi shikoyatlar:</b>\n\n"
    for r in rows:
        text += f"ID: {r['id']} | {r['from_id']} → {r['target_id']}\nSabab: {r['reason']}\n\n"
    await message.answer(text)


@dp.message(F.text == "💰 To‘lovlar")
async def payments_admin(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await fetchall("SELECT * FROM payments ORDER BY id DESC LIMIT 10")
    if not rows:
        await message.answer("To‘lovlar yo‘q.")
        return
    text = "💰 <b>Oxirgi to‘lovlar:</b>\n\n"
    for p in rows:
        text += f"ID: {p['id']} | User: {p['user_id']} | {fmt_money(int(p['amount']))} | {p['status']}\n"
    await message.answer(text)


@dp.message(F.text == "➕ Balans qo‘shish")
async def admin_add_balance_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("User ID kiriting:")
    await state.set_state(AdminBalanceState.user_id)


@dp.message(AdminBalanceState.user_id)
async def admin_add_balance_user(message: Message, state: FSMContext) -> None:
    uid = safe_int(message.text, 0)
    if not uid:
        await message.answer("To‘g‘ri User ID kiriting.")
        return
    await state.update_data(user_id=uid)
    await message.answer("Qo‘shiladigan summa kiriting:")
    await state.set_state(AdminBalanceState.amount)


@dp.message(AdminBalanceState.amount)
async def admin_add_balance_amount(message: Message, state: FSMContext) -> None:
    amount = safe_int(message.text, 0)
    if amount == 0:
        await message.answer("Summa 0 bo‘lmasin.")
        return
    data = await state.get_data()
    uid = int(data["user_id"])
    await change_balance(uid, amount, "admin", f"Admin {message.from_user.id} tomonidan")
    await state.clear()
    await message.answer(f"✅ {uid} balansiga {fmt_money(amount)} qo‘shildi.")
    with suppress(Exception):
        await bot.send_message(uid, f"💰 Balansingizga admin tomonidan {fmt_money(amount)} qo‘shildi.")


@dp.message(Command("ban"))
async def ban_user(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    uid = safe_int(message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else "0", 0)
    if not uid:
        await message.answer("Ishlatish: /ban USER_ID")
        return
    await execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
    await invalidate_user(uid)
    await message.answer("🚫 User ban qilindi.")


@dp.message(Command("unban"))
async def unban_user(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    uid = safe_int(message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else "0", 0)
    if not uid:
        await message.answer("Ishlatish: /unban USER_ID")
        return
    await execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    await invalidate_user(uid)
    await message.answer("✅ User unban qilindi.")


# ================= ROUTER =================

@dp.message(F.text == "🏠 Bosh menyu")
async def home(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🏠 Bosh menyu", reply_markup=main_menu(message.from_user.id))


@dp.message()
async def all_messages(message: Message) -> None:
    if message.from_user:
        p = await find_pair(message.from_user.id)
        if p and message.text not in {"🏠 Bosh menyu"}:
            other = await partner_of(message.from_user.id)
            if other:
                with suppress(Exception):
                    if message.text:
                        await bot.send_message(other, f"💬 Anonim suhbatdosh:\n\n{message.text}")
                    elif message.photo:
                        await bot.send_photo(other, message.photo[-1].file_id, caption="📸 Anonim suhbatdosh rasm yubordi")
            return
    await message.answer("Menyudan tanlang 👇", reply_markup=main_menu(message.from_user.id))


# ================= RUN =================

async def main() -> None:
    await init_database()
    await export_users_json()
    Thread(target=run_web_server, daemon=True).start()
    print(f"{BOT_NAME} ishga tushdi...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
