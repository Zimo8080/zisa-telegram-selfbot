import os
import re
import logging
import time
import random
import asyncio
import itertools
import aiosqlite
import yt_dlp
from pyrogram import Client, filters, enums, compose
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import SessionPasswordNeeded, FloodWait

# ==========================================
# ⚙️ تنظیمات اصلی
# ==========================================
# مقادیر را در Railway > Variables تنظیم کنید؛ اطلاعات محرمانه را داخل کد ننویسید.
def _env_int(name: str):
    value = os.getenv(name, "").strip()
    return int(value) if value else None

API_ID = _env_int("API_ID")
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = _env_int("OWNER_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("zisaself")

DB_NAME = "selfbot_data.db"
target_cycles = {}
secretary_cooldowns = {}
login_states = {}
active_selfbots = []

FRIEND_MESSAGES = ["پیام دوست 1", "پیام دوست 2"]
ENEMY_MESSAGES = ["پیام دشمن 1", "پیام دشمن 2"]

_missing_config = [
    name for name, value in (("API_ID", API_ID), ("API_HASH", API_HASH),
                             ("BOT_TOKEN", BOT_TOKEN), ("OWNER_ID", OWNER_ID))
    if value is None or value == ""
]
if _missing_config:
    raise RuntimeError("Railway Variables are missing: " + ", ".join(_missing_config))

bot_app = Client("master_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==========================================
# 🗄 دیتابیس
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY, 
                secretary_active INTEGER DEFAULT 0, 
                secretary_text TEXT DEFAULT 'سلام فعلا افلاینم انلاین شدم پاسخ میدم✅', 
                secretary_time INTEGER DEFAULT 3600
            )
        """)
        await db.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_targets (
                id INTEGER PRIMARY KEY, 
                target_user_id INTEGER, 
                target_name TEXT, 
                target_type TEXT, 
                UNIQUE(target_user_id)
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS filtered_words (
                id INTEGER PRIMARY KEY, 
                word TEXT UNIQUE
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS force_join_channels (
                id INTEGER PRIMARY KEY, 
                channel_username TEXT UNIQUE
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS muted_users (
                id INTEGER PRIMARY KEY, 
                target_user_id INTEGER, 
                target_name TEXT UNIQUE
            )
        """)
        await db.commit()

# ==========================================
# 🚀 هسته سلف‌بات (قابلیت‌های کامل)
# ==========================================
def register_selfbot_handlers(app: Client):
    
    # ۱. دوست و دشمن
    @app.on_message(filters.me & filters.reply & filters.regex(r"^(تنظیم دوست|تنظیم دشمن)$"), group=1)
    async def set_target_handler(client, message):
        target_user = message.reply_to_message.from_user
        if not target_user:
            return
            
        if "دوست" in message.text:
            target_type = "friend"
        else:
            target_type = "enemy"
            
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO auto_targets (target_user_id, target_name, target_type) VALUES (?, ?, ?)", 
                (target_user.id, target_user.first_name, target_type)
            )
            await db.commit()
            
        await message.edit_text(f"✅ کاربر {target_user.first_name} در لیست ثبت شد.")

    @app.on_message(~filters.me, group=3)
    async def target_guard_handler(client, message):
        if not message.from_user:
            return
            
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT target_type FROM auto_targets WHERE target_user_id = ?", (message.from_user.id,))
            row = await cursor.fetchone()
            
        if row:
            if row[0] == "friend":
                messages_list = FRIEND_MESSAGES
            else:
                messages_list = ENEMY_MESSAGES
                
            cycle = target_cycles.setdefault(message.from_user.id, itertools.cycle(messages_list))
            
            try:
                await message.reply_text(next(cycle))
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception:
                pass

    # ۲. تبدیل ویدیو مسیج
    @app.on_message(filters.me & filters.reply & filters.regex(r"^(ویدیو مسیج|تبدیل ویدیو)$"), group=4)
    async def video_note_converter(client, message):
        if not message.reply_to_message.video:
            return await message.edit_text("❌ خطا: روی یک ویدیو ریپلای کنید.")
            
        try:
            downloaded_path = await message.reply_to_message.download()
            await client.send_video_note(message.chat.id, video_note=downloaded_path)
            os.remove(downloaded_path)
            await message.delete()
        except Exception:
            await message.edit_text("❌ خطا در تبدیل ویدیو.")

    # ۳. منشی هوشمند
    @app.on_message(filters.private & ~filters.me, group=6)
    async def secretary_guard_handler(client, message):
        if not message.from_user:
            return
            
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT secretary_active, secretary_text, secretary_time FROM settings WHERE id = 1")
            row = await cursor.fetchone()
            
        if not row or row[0] == 0:
            return
            
        current_time = time.time()
        cooldown = row[2]
        secretary_text = row[1]
        
        if current_time - secretary_cooldowns.get(message.from_user.id, 0) >= cooldown:
            try:
                await client.send_message(message.chat.id, secretary_text)
                secretary_cooldowns[message.from_user.id] = current_time
            except Exception:
                pass

    # ۴. اسپمر
    @app.on_message(filters.me & filters.regex(r"^اسپم\s*(\d+)\s+(.+)$"), group=7)
    async def spam_handler(client, message):
        count = int(message.matches[0].group(1))
        if count > 50:
            count = 50
            
        spam_text = message.matches[0].group(2)
        await message.delete()
        
        for _ in range(count):
            try:
                await client.send_message(message.chat.id, spam_text)
                await asyncio.sleep(0.3)
            except Exception:
                break

    # ۵. فیلتر کلمات
    @app.on_message(filters.private & ~filters.me, group=9)
    async def filter_words_guard(client, message):
        message_text = (message.text or message.caption or "").lower()
        if not message_text:
            return
            
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT word FROM filtered_words")
            rows = await cursor.fetchall()
            bad_words = [row[0] for row in rows]
            
        for word in bad_words:
            if word in message_text:
                try:
                    await message.delete()
                    break
                except Exception:
                    pass

    # ۶. تگ دسته‌جمعی
    @app.on_message(filters.me & filters.group & filters.regex(r"^(تگ اعضا)$"), group=12)
    async def tag_members_handler(client, message):
        await message.delete()
        tagged_users = []
        
        try:
            async for member in client.get_chat_members(message.chat.id):
                if not member.user.is_bot:
                    tagged_users.append(f"[{member.user.first_name}](tg://user?id={member.user.id})")
                if len(tagged_users) >= 100:
                    break
                    
            for i in range(0, len(tagged_users), 5):
                batch = tagged_users[i:i+5]
                await client.send_message(message.chat.id, " ".join(batch))
                await asyncio.sleep(1.5)
        except Exception:
            pass

    # ۷. پاکسازی پیام‌ها
    @app.on_message(filters.me & filters.regex(r"^حذف\s+(\d+)$"), group=13)
    async def bulk_delete_handler(client, message):
        try:
            count = int(message.matches[0].group(1))
            if count > 100:
                count = 100
                
            messages_to_delete = []
            async for msg in client.get_chat_history(message.chat.id, limit=count + 1):
                messages_to_delete.append(msg.id)
                
            if messages_to_delete:
                await client.delete_messages(message.chat.id, messages_to_delete, revoke=True)
        except Exception:
            pass

    # ۸. لیست سکوت
    @app.on_message(filters.me & filters.reply & filters.regex(r"^سکوت$"), group=14)
    async def mute_user_handler(client, message):
        target_user = message.reply_to_message.from_user
        if not target_user:
            return
            
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO muted_users (target_user_id, target_name) VALUES (?, ?)", (target_user.id, target_user.first_name))
            await db.commit()
            
        await message.edit_text(f"✅ کاربر {target_user.first_name} سکوت شد.")
        
    @app.on_message(~filters.me, group=15)
    async def mute_guard_handler(client, message):
        if not message.from_user:
            return
            
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT 1 FROM muted_users WHERE target_user_id = ?", (message.from_user.id,))
            row = await cursor.fetchone()
            
        if row:
            try:
                await message.delete()
            except Exception:
                pass

    # ۹. کپی پروفایل
    @app.on_message(filters.me & filters.reply & filters.regex(r"^(ایدی|کپی)$"), group=16)
    async def clone_profile_handler(client, message):
        command = message.matches[0].group(1)
        target_user = message.reply_to_message.from_user
        
        if not target_user:
            return
            
        if command == "ایدی":
            return await message.edit_text(f"👤 **نام:** `{target_user.first_name}`\n🔸 **آیدی:** `{target_user.id}`")
            
        try:
            chat_info = await client.get_chat(target_user.id)
            
            first_name = target_user.first_name or ""
            last_name = target_user.last_name or ""
            bio = chat_info.bio or ""
            
            await client.update_profile(first_name=first_name, last_name=last_name, bio=bio)
            
            if target_user.photo:
                photo_path = await client.download_media(target_user.photo.big_file_id)
                await client.set_profile_photo(photo=photo_path)
                os.remove(photo_path)
                
            await message.edit_text("✅ **هویت با موفقیت کپی شد!**")
        except Exception:
            await message.edit_text("❌ خطا در کپی پروفایل.")

    # ۱۰. ماشین حساب
    @app.on_message(filters.me & filters.regex(r"^حساب\s+(.+)$"), group=17)
    async def calculator_handler(client, message):
        expression = message.matches[0].group(1)
        expression = expression.replace("×", "*").replace("÷", "/")
        
        try:
            result = eval(expression)
            await message.edit_text(f"🧮 `{message.matches[0].group(1)} = {result}`")
        except Exception:
            await message.edit_text("❌ خطا در محاسبه.")

    # ۱۱. دانلودر هوشمند (یوتیوب و اینستاگرام و ...)
    @app.on_message(filters.me & filters.regex(r"^(دانلود)\s+(https?://[^\s]+)$"), group=18)
    async def smart_downloader_handler(client, message):
        link = message.matches[0].group(2)
        await message.edit_text("🔄 `در حال دانلود...`")
        
        try:
            def run_ytdlp():
                ydl_opts = {
                    'outtmpl': 'downloads/%(id)s.%(ext)s', 
                    'quiet': True
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(link, download=True)
                    return ydl.prepare_filename(info_dict)
                    
            downloaded_file = await asyncio.to_thread(run_ytdlp)
            await client.send_document(message.chat.id, downloaded_file)
            os.remove(downloaded_file)
            await message.delete()
        except Exception:
            await message.edit_text("❌ خطا در دانلود فایل.")


# ==========================================
# 💎 پنل مدیریت شیشه‌ای + موتور لاگین
# ==========================================
def get_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن سلف‌بات جدید", callback_data="add_new_account")],
        [InlineKeyboardButton("🟢 سلف‌بات فعال است", callback_data="none")]
    ])

@bot_app.on_message(filters.private & (filters.command("start") | filters.regex("^پنل$")))
async def main_panel_handler(client, message):
    sender_id = message.from_user.id if message.from_user else None
    logger.info("Received /start or پنل from user_id=%s; configured OWNER_ID=%s", sender_id, OWNER_ID)
    if sender_id != OWNER_ID:
        if message.from_user:
            await message.reply_text("⛔ این پنل فقط برای مالک ربات فعال است. OWNER_ID را در Railway بررسی کنید.")
        return

    caption = "✦ **به پنل مدیریت سلف‌بات پیشرفته خوش آمدید** ✦\n\nبرای راه‌اندازی سلف‌بات روی خط جدید کلیک کنید:"
    await message.reply_text(text=caption, reply_markup=get_panel_keyboard())

@bot_app.on_callback_query(filters.regex("^add_new_account$"))
async def add_account_callback(client, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        return
        
    login_states[OWNER_ID] = {"step": "phone"}
    await callback_query.message.reply("📲 **مرحله ۱:** لطفاً شماره تلفن اکانت را با کد کشور بفرستید (مثال: +989123456789):")

@bot_app.on_message(filters.private & ~filters.command("start") & ~filters.regex("^پنل$"))
async def process_login_steps_handler(client, message):
    if message.from_user.id != OWNER_ID:
        return
        
    state = login_states.get(OWNER_ID)
    if not state:
        return

    if state["step"] == "phone":
        phone_number = message.text.replace(" ", "")
        status_message = await message.reply("🔄 `در حال ارسال کد به تلگرام...`")
        
        try:
            new_app = Client(f"session_{phone_number}", api_id=API_ID, api_hash=API_HASH)
            await new_app.connect()
            sent_code_info = await new_app.send_code(phone_number)
            
            login_states[OWNER_ID] = {
                "step": "code", 
                "app": new_app, 
                "phone": phone_number, 
                "hash": sent_code_info.phone_code_hash
            }
            
            await status_message.edit_text("💬 **مرحله ۲:** کد ۵ رقمی را با فاصله بفرستید (مثلاً `1 2 3 4 5`).")
            
        except Exception as error:
            await status_message.edit_text(f"❌ خطا در ارسال کد:\n`{error}`")
            del login_states[OWNER_ID]

    elif state["step"] == "code":
        login_code = message.text.replace("-", "").replace(" ", "")
        status_message = await message.reply("🔄 `در حال تایید کد...`")
        
        try:
            app = state["app"]
            await app.sign_in(state["phone"], state["hash"], login_code)
            
            register_selfbot_handlers(app)
            await app.start()
            active_selfbots.append(app)
            
            await status_message.edit_text("✅ **سلف‌بات با موفقیت روی این خط فعال شد!**")
            del login_states[OWNER_ID]
            
        except SessionPasswordNeeded:
            login_states[OWNER_ID]["step"] = "password"
            await status_message.edit_text("🔐 **مرحله ۳:** پسورد تایید دو مرحله‌ای (Two-Step Verification) را بفرستید:")
            
        except Exception as error:
            await status_message.edit_text(f"❌ خطا در تایید کد:\n`{error}`")
            del login_states[OWNER_ID]

    elif state["step"] == "password":
        status_message = await message.reply("🔄 `در حال بررسی پسورد...`")
        
        try:
            app = state["app"]
            await app.check_password(message.text)
            
            register_selfbot_handlers(app)
            await app.start()
            active_selfbots.append(app)
            
            await status_message.edit_text("✅ **سلف‌بات با موفقیت روی این خط فعال شد!**")
            del login_states[OWNER_ID]
            
        except Exception:
            await status_message.edit_text("❌ پسورد اشتباه است.")
            del login_states[OWNER_ID]

# ==========================================
# 🚀 راه‌اندازی و اجرای سرور
# ==========================================
async def main():
    await init_db()
    os.makedirs("downloads", exist_ok=True)
    logger.info("Starting Telegram bot; OWNER_ID=%s", OWNER_ID)
    try:
        await bot_app.start()
        me = await bot_app.get_me()
        logger.info("Bot started successfully as @%s (id=%s)", me.username, me.id)
        await asyncio.Event().wait()
    finally:
        if bot_app.is_connected:
            await bot_app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("Application stopped with an error")
        raise
