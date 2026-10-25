import os
import re
import time
import shutil
import sqlite3
import random
import asyncio
import itertools
import aiosqlite
import yt_dlp
from pyrogram import Client, filters, enums, compose
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from pyrogram.errors import SessionPasswordNeeded, FloodWait, UserNotParticipant

# ==========================================
# ⚙️ تنظیمات اصلی (حتماً پر شود)
# ==========================================
# Railway Variables: API_ID, API_HASH, BOT_TOKEN, OWNER_ID
# Attach a Railway Volume with mount path /data.
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
DB_NAME = os.path.join(DATA_DIR, "selfbot_data.db")
PHOTO_URL = os.environ.get("PHOTO_URL", "")
target_cycles = {}
secretary_cooldowns = {}
FRIEND_MESSAGES = ["پیام دوست 1", "پیام دوست 2"]
ENEMY_MESSAGES = ["پیام دشمن 1", "پیام دشمن 2"]
login_states = {}
active_selfbots = []

bot_app = Client("master_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=DATA_DIR)

# ==========================================
# 🗄 دیتابیس
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY AUTOINCREMENT, secretary_active INTEGER DEFAULT 0, secretary_text TEXT DEFAULT 'سلام فعلا افلاینم انلاین شدم پاسخ میدم✅', secretary_time INTEGER DEFAULT 3600)")
        await db.execute("CREATE TABLE IF NOT EXISTS auto_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, target_user_id INTEGER, target_name TEXT, target_type TEXT, UNIQUE(target_user_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS filtered_words (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE)")
        await db.execute("CREATE TABLE IF NOT EXISTS force_join_channels (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_username TEXT UNIQUE)")
        await db.execute("CREATE TABLE IF NOT EXISTS muted_users (id INTEGER PRIMARY KEY AUTOINCREMENT, target_user_id INTEGER, target_name TEXT UNIQUE)")
        await db.commit()

# ==========================================
# 🚀 هسته سلف‌بات (نصب روی اکانت‌های جدید)
# ==========================================
def register_selfbot_handlers(app: Client):
    
    @app.on_message(filters.me & filters.reply & filters.regex(r"^(تنظیم دوست|تنظیم دشمن)$"), group=1)
    async def set_target(client, message):
        t = message.reply_to_message.from_user
        if not t: return
        t_type = "friend" if message.matches[0].group(1) == "تنظیم دوست" else "enemy"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR REPLACE INTO auto_targets (target_user_id, target_name, target_type) VALUES (?, ?, ?)", (t.id, t.first_name, t_type))
            await db.commit()
        await message.edit_text(f"✅ کاربر در لیست قرار گرفت.")

    @app.on_message(~filters.me, group=3)
    async def target_guard(client, message):
        if not message.from_user: return
        async with aiosqlite.connect(DB_NAME) as db:
            c = await db.execute("SELECT target_type FROM auto_targets WHERE target_user_id = ?", (message.from_user.id,))
            r = await c.fetchone()
        if r:
            cyc = target_cycles.setdefault(message.from_user.id, itertools.cycle(FRIEND_MESSAGES if r[0] == "friend" else ENEMY_MESSAGES))
            try: await message.reply_text(next(cyc))
            except FloodWait as e: await asyncio.sleep(e.value)

    @app.on_message(filters.me & filters.reply & filters.regex(r"^(ویدیو مسیج|تبدیل ویدیو)$"), group=4)
    async def vid_note(client, message):
        if not message.reply_to_message.video: return
        try:
            path = await message.reply_to_message.download()
            await client.send_video_note(message.chat.id, video_note=path)
            os.remove(path); await message.delete()
        except: pass

    @app.on_message(filters.private & ~filters.me, group=6)
    async def sec_guard(client, message):
        if not message.from_user: return
        async with aiosqlite.connect(DB_NAME) as db:
            c = await db.execute("SELECT secretary_active, secretary_text, secretary_time FROM settings WHERE id = 1")
            r = await c.fetchone()
        if not r or r[0] == 0: return
        t = time.time()
        if t - secretary_cooldowns.get(message.from_user.id, 0) >= r[2]:
            try: await client.send_message(message.chat.id, r[1]); secretary_cooldowns[message.from_user.id] = t
            except: pass

    @app.on_message(filters.private & ~filters.me, group=9)
    async def filter_guard(client, message):
        txt = (message.text or message.caption or "").lower()
        if not txt: return
        async with aiosqlite.connect(DB_NAME) as db:
            c = await db.execute("SELECT word FROM filtered_words")
            wds = [row[0] for row in await c.fetchall()]
        if any(w in txt for w in wds):
            try: await message.delete()
            except: pass

    @app.on_message(filters.me & filters.regex(r"^حذف\s+(\d+)$"), group=13)
    async def bulk_del(client, message):
        try:
            msg_ids = [msg.id async for msg in client.get_chat_history(message.chat.id, limit=min(int(message.matches[0].group(1)), 100) + 1)]
            if msg_ids: await client.delete_messages(message.chat.id, msg_ids, revoke=True)
        except: pass

    @app.on_message(~filters.me, group=15)
    async def mute_guard(client, message):
        if not message.from_user: return
        async with aiosqlite.connect(DB_NAME) as db:
            c = await db.execute("SELECT 1 FROM muted_users WHERE target_user_id = ?", (message.from_user.id,))
            if await c.fetchone():
                try: await message.delete()
                except: pass

    @app.on_message(filters.me & filters.reply & filters.regex(r"^ایدی$"), group=16)
    async def show_id(client, message):
        target = message.reply_to_message.from_user
        if target:
            await message.edit_text(f"👤 **نام:** `{target.first_name}`\n🔸 **آیدی:** `{target.id}`")

    @app.on_message(filters.me & filters.regex(r"^حساب\s+(.+)$"), group=17)
    async def calc(client, message):
        expr = message.matches[0].group(1).replace("×", "*").replace("÷", "/")
        try: await message.edit_text(f"🧮 `{message.matches[0].group(1)} = {eval(expr)}`")
        except: await message.edit_text("❌ خطا.")

# ==========================================
# 🔐 موتور لاگین از راه دور (Master Bot)
# ==========================================
@bot_app.on_message(filters.private & filters.regex("^➕ افزودن اکانت$"))
async def start_login(client, message):
    if message.from_user.id != OWNER_ID: return
    login_states[OWNER_ID] = {"step": "phone"}
    await message.reply("📲 **مرحله ۱:** لطفاً شماره تلفن اکانت را با کد کشور بفرستید (مثال: +989123456789):")

@bot_app.on_message(filters.private & ~filters.regex("^(پنل|➕ افزودن اکانت|/start)$"))
async def process_login_steps(client, message):
    if message.from_user.id != OWNER_ID: return
    state = login_states.get(OWNER_ID)
    if not state: return

    if state["step"] == "phone":
        phone = message.text.replace(" ", "")
        temp_msg = await message.reply("🔄 `در حال ارتباط با سرور و ارسال کد...`")
        try:
            new_app = Client(f"session_{phone}", api_id=API_ID, api_hash=API_HASH, workdir=DATA_DIR)
            await new_app.connect()
            sent_code = await new_app.send_code(phone)
            login_states[OWNER_ID] = {"step": "code", "app": new_app, "phone": phone, "hash": sent_code.phone_code_hash}
            await temp_msg.edit_text("💬 **مرحله ۲:** کد ۵ رقمی تلگرام ارسال شد.\n\n⚠️ **کد را با فاصله بفرستید** (مثلاً `1 2 3 4 5`).")
        except Exception as e:
            await temp_msg.edit_text(f"❌ خطا در ارسال کد:\n`{e}`"); del login_states[OWNER_ID]

    elif state["step"] == "code":
        code = message.text.replace("-", "").replace(" ", "")
        temp_msg = await message.reply("🔄 `در حال تایید کد...`")
        try:
            app = state["app"]
            await app.sign_in(state["phone"], state["hash"], code)
            register_selfbot_handlers(app)
            await app.start()
            active_selfbots.append(app)
            await temp_msg.edit_text("✅ **اکانت با موفقیت متصل شد! سلف‌بات فعال است.**")
            del login_states[OWNER_ID]
        except SessionPasswordNeeded:
            login_states[OWNER_ID]["step"] = "password"
            await temp_msg.edit_text("🔐 **مرحله ۳:** پسورد تایید دو مرحله‌ای را بفرستید:")
        except Exception as e:
            await temp_msg.edit_text(f"❌ خطا در تایید کد:\n`{e}`"); del login_states[OWNER_ID]

    elif state["step"] == "password":
        temp_msg = await message.reply("🔄 `در حال بررسی پسورد...`")
        try:
            app = state["app"]
            await app.check_password(message.text)
            register_selfbot_handlers(app)
            await app.start()
            active_selfbots.append(app)
            await temp_msg.edit_text("✅ **اکانت با موفقیت متصل شد! سلف‌بات فعال است.**")
            del login_states[OWNER_ID]
        except: await temp_msg.edit_text("❌ پسورد اشتباه است."); del login_states[OWNER_ID]


# Database backup/restore commands: owner only
@bot_app.on_message(filters.private & filters.command("backup"))
async def backup_database(client, message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    if not os.path.isfile(DB_NAME):
        await message.reply_text("❌ فایل دیتابیس هنوز ساخته نشده است.")
        return
    backup_path = os.path.join(DATA_DIR, "selfbot_data_backup.db")
    try:
        with sqlite3.connect(DB_NAME) as source_db, sqlite3.connect(backup_path) as backup_db:
            source_db.backup(backup_db)
        await message.reply_document(backup_path, caption="💾 بکاپ دیتابیس؛ محرمانه نگهش دارید.")
    except Exception as exc:
        await message.reply_text(f"❌ تهیه بکاپ ناموفق بود: {exc}")
    finally:
        try:
            if os.path.exists(backup_path): os.remove(backup_path)
        except OSError:
            pass

@bot_app.on_message(filters.private & filters.command("restore"))
async def restore_database(client, message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    if not message.document or not message.document.file_name or not message.document.file_name.lower().endswith((".db", ".sqlite", ".sqlite3")):
        await message.reply_text("فایل بکاپ دیتابیس با پسوند .db، .sqlite یا .sqlite3 را همراه دستور /restore ارسال کنید.")
        return
    incoming = os.path.join(DATA_DIR, "restore_upload.tmp")
    candidate = os.path.join(DATA_DIR, "restore_validated.db")
    try:
        await message.download(file_name=incoming)
        with sqlite3.connect(incoming) as check_db:
            integrity = check_db.execute("PRAGMA integrity_check").fetchone()
            tables = {r[0] for r in check_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"settings", "auto_targets", "filtered_words", "force_join_channels", "muted_users"}
            if not integrity or integrity[0] != "ok" or not required.issubset(tables):
                raise ValueError("فایل دیتابیس خراب یا ناسازگار است.")
            with sqlite3.connect(candidate) as candidate_db:
                check_db.backup(candidate_db)
        if os.path.exists(DB_NAME): shutil.copy2(DB_NAME, DB_NAME + ".before_restore")
        os.replace(candidate, DB_NAME)
        await message.reply_text("✅ دیتابیس بازیابی شد. برای بارگذاری کامل، سرویس را Redeploy کنید.")
    except Exception as exc:
        await message.reply_text(f"❌ بازیابی ناموفق بود: {exc}")
    finally:
        for path in (incoming, candidate):
            try:
                if os.path.exists(path): os.remove(path)
            except OSError:
                pass

# ==========================================
# 💎 پنل شیشه‌ای مدیریت
# ==========================================
def get_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن سلف‌بات جدید", callback_data="add_new_account")],
        [InlineKeyboardButton("❤️ دوست و دشمن", callback_data="setting_targets"), InlineKeyboardButton("🚀 اسپمر", callback_data="setting_spam")],
        [InlineKeyboardButton("⚙️ سایر تنظیمات", callback_data="other_settings")]
    ])

@bot_app.on_message(filters.private & filters.regex("^(پنل|/start)$"))
async def main_panel(client, message):
    if message.from_user.id != OWNER_ID: return 
    caption = "✦ **به پنل مدیریت سلف‌بات پیشرفته خوش آمدید** ✦"
    await message.reply_photo(photo=PHOTO_URL, caption=caption, reply_markup=get_panel_keyboard())

@bot_app.on_callback_query(filters.regex("^add_new_account$"))
async def add_account_btn(client, callback_query):
    if callback_query.from_user.id != OWNER_ID: return
    login_states[OWNER_ID] = {"step": "phone"}
    await callback_query.message.reply("📲 **مرحله ۱:** لطفاً شماره تلفن اکانت را با کد کشور بفرستید (مثال: +989123456789):")

# ==========================================
# 🚀 راه‌اندازی و اجرای سرور
# ==========================================
async def main():
    missing = [name for name, value in (("API_ID", API_ID), ("API_HASH", API_HASH), ("BOT_TOKEN", BOT_TOKEN), ("OWNER_ID", OWNER_ID)) if not value]
    if missing:
        raise RuntimeError("Railway Variables missing: " + ", ".join(missing))
    await init_db()
    os.makedirs(os.path.join(DATA_DIR, "downloads"), exist_ok=True)
    print("🚀 ربات مدیریت (Master Bot) روشن شد!")
    print(f"🔒 ربات کاملاً روی آیدی {OWNER_ID} قفل شده است و فقط در پی‌وی شما باز می‌شود.")
    await compose([bot_app])

if __name__ == "__main__":
    asyncio.run(main())
