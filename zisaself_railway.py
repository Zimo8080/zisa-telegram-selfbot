import os
import re
import time
import random
import asyncio
import itertools
import shutil
import tempfile
import aiosqlite
import yt_dlp
from pyrogram import Client, filters, enums, compose
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from pyrogram.errors import FloodWait, UserNotParticipant, ChatAdminRequired

# ==========================================
# ⚙️ تنظیمات اصلی و متغیرها (حتماً پر شود)
# ==========================================
# تنظیمات را در Railway > Service > Variables وارد کنید.
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

# در Railway یک Volume بسازید و Mount Path آن را /data قرار دهید.
# سپس DATA_DIR را در Variables برابر /data تنظیم کنید.
DATA_DIR = os.environ.get("DATA_DIR", "/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.access(DATA_DIR, os.W_OK):
        raise PermissionError(f"{DATA_DIR} قابل نوشتن نیست")
except (OSError, PermissionError) as exc:
    raise RuntimeError(
        f"مسیر DATA_DIR={DATA_DIR!r} در دسترس/قابل نوشتن نیست. "
        "در Railway یک Volume با Mount Path مناسب وصل کنید و DATA_DIR را روی همان مسیر بگذارید."
    ) from exc

DB_NAME = os.path.join(DATA_DIR, "selfbot_data.db")
PHOTO_URL = os.environ.get("PHOTO_URL", "")

if not API_ID or not API_HASH or not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("Variables را در Railway تنظیم کنید: API_ID، API_HASH، BOT_TOKEN و OWNER_ID")

# فایل‌های session هم در فضای دائمی قرار می‌گیرند.
user_app = Client(os.path.join(DATA_DIR, "my_account"), api_id=API_ID, api_hash=API_HASH)
bot_app = Client(os.path.join(DATA_DIR, "master_bot"), api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# حافظه موقت (RAM)
target_cycles = {}       
secretary_cooldowns = {} 

# پیام‌های لیست دوست و دشمن
FRIEND_MESSAGES = ["پیام دوست 1", "پیام دوست 2"]
ENEMY_MESSAGES = ["پیام دشمن 1", "پیام دشمن 2"]

# ==========================================
# 🗄 دیتابیس هوشمند (SQLite)
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY AUTOINCREMENT, secretary_active INTEGER DEFAULT 0, secretary_text TEXT DEFAULT 'سلام فعلا افلاینم انلاین شدم پاسخ میدم✅', secretary_time INTEGER DEFAULT 3600)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS auto_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, target_user_id INTEGER, target_name TEXT, target_type TEXT, UNIQUE(target_user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS filtered_words (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS force_join_channels (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_username TEXT UNIQUE)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS muted_users (id INTEGER PRIMARY KEY AUTOINCREMENT, target_user_id INTEGER, target_name TEXT UNIQUE)""")
        await db.execute(
            "INSERT OR IGNORE INTO settings (id, secretary_active, secretary_text, secretary_time) VALUES (1, 0, ?, 3600)",
            ("سلام فعلا افلاینم انلاین شدم پاسخ میدم✅",)
        )
        await db.commit()

# ==========================================
# 💎 پنل شیشه‌ای مدیریت (Master Bot)
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ دوست و دشمن", callback_data="setting_targets"), InlineKeyboardButton("🎥 ویدیو مسیج", callback_data="setting_videonote")],
        [InlineKeyboardButton("🚀 اسپمر", callback_data="setting_spam"), InlineKeyboardButton("🚫 فیلتر کلمات", callback_data="setting_filterword")],
        [InlineKeyboardButton("👩🏻‍💻 منشی", callback_data="setting_secretary"), InlineKeyboardButton("🔐 عضویت اجباری", callback_data="setting_forcejoin")],
        [InlineKeyboardButton("📢 تگ دسته‌جمعی", callback_data="setting_tag"), InlineKeyboardButton("🗑 پاکسازی پیام", callback_data="setting_delete")],
        [InlineKeyboardButton("🔇 لیست سکوت", callback_data="setting_mute"), InlineKeyboardButton("👤 اطلاعات اکانت", callback_data="setting_userinfo")],
        [InlineKeyboardButton("🧮 ماشین حساب", callback_data="setting_calculator"), InlineKeyboardButton("📥 دانلودر", callback_data="setting_downloader")],
        [InlineKeyboardButton("👁 فضول‌گیر", callback_data="setting_snoopers"), InlineKeyboardButton("🎭 کپی پروفایل", callback_data="setting_clone")]
    ])

@bot_app.on_message(filters.private & filters.regex("^(پنل|/start)$"))
async def main_panel(client, message):
    # ⛔️ قفل امنیتی سخت‌افزاری: فقط OWNER_ID حق استفاده دارد
    if message.from_user.id != OWNER_ID:
        return 
        
    caption = "✦ **به پنل مدیریت سلف‌بات پیشرفته خوش آمدید** ✦\n\nبخش مورد نظر را انتخاب کنید:"
    if PHOTO_URL:
        await message.reply_photo(photo=PHOTO_URL, caption=caption, reply_markup=get_main_keyboard())
    else:
        await message.reply_text(caption, reply_markup=get_main_keyboard())

@bot_app.on_callback_query(filters.regex("^back_to_main$"))
async def back_to_main_callback(client, callback_query):
    if callback_query.from_user.id != OWNER_ID: return
    caption = "✦ **به پنل مدیریت سلف‌بات پیشرفته خوش آمدید** ✦\n\nبخش مورد نظر را انتخاب کنید:"
    if PHOTO_URL:
        await callback_query.edit_message_media(media=InputMediaPhoto(PHOTO_URL, caption=caption), reply_markup=get_main_keyboard())
    else:
        await callback_query.edit_message_text(caption, reply_markup=get_main_keyboard())

# ==========================================
# 🚀 هسته اصلی سلف‌بات
# ==========================================

# ۱. سیستم دوست و دشمن
@user_app.on_message(filters.me & filters.reply & filters.regex(r"^(تنظیم دوست|تنظیم دشمن)$"), group=1)
async def set_target_handler(client, message):
    target = message.reply_to_message.from_user
    if not target: return await message.edit_text("❌ خطا: کاربر یافت نشد.")
    t_type = "friend" if message.matches[0].group(1) == "تنظیم دوست" else "enemy"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO auto_targets (target_user_id, target_name, target_type) VALUES (?, ?, ?)", (target.id, target.first_name, t_type))
        await db.commit()
    await message.edit_text(f"✅ کاربر {target.first_name} با موفقیت در لیست قرار گرفت.")

@user_app.on_message(filters.me & filters.reply & filters.regex(r"^(حذف دوست|حذف دشمن)$"), group=2)
async def remove_target_handler(client, message):
    target = message.reply_to_message.from_user
    if not target: return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM auto_targets WHERE target_user_id = ?", (target.id,))
        await db.commit()
    await message.edit_text(f"🗑 کاربر از لیست هدف خارج شد.")

@user_app.on_message(~filters.me, group=3)
async def auto_target_guard(client, message):
    if not message.from_user: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT target_type FROM auto_targets WHERE target_user_id = ?", (message.from_user.id,))
        row = await cursor.fetchone()
    if row:
        cycle = target_cycles.setdefault(message.from_user.id, itertools.cycle(FRIEND_MESSAGES if row[0] == "friend" else ENEMY_MESSAGES))
        try: await message.reply_text(next(cycle))
        except FloodWait as e: await asyncio.sleep(e.value)

# ۲. مبدل ویدیو مسیج
@user_app.on_message(filters.me & filters.reply & filters.regex(r"^(ویدیو مسیج|تبدیل ویدیو)$"), group=4)
async def video_note_converter(client, message):
    if not message.reply_to_message.video: return await message.edit_text("❌ خطا: لطفاً روی یک ویدیو ریپلای کنید.")
    await message.edit_text("🔄 `در حال تبدیل...`")
    try:
        path = await message.reply_to_message.download()
        await client.send_video_note(message.chat.id, video_note=path)
        os.remove(path)
        await message.delete()
    except Exception as e: await message.edit_text("❌ خطا در تبدیل.")

# ۳. منشی هوشمند
@user_app.on_message(filters.me & filters.regex(r"^(تنظیم متن منشی|تنظیم تایم منشی)\s+(.+)$"), group=5)
async def configure_secretary(client, message):
    cmd, val = message.matches[0].group(1), message.matches[0].group(2).strip()
    async with aiosqlite.connect(DB_NAME) as db:
        if cmd == "تنظیم متن منشی":
            await db.execute("UPDATE settings SET secretary_text = ? WHERE id = 1", (val,))
            await message.edit_text(f"✅ متن تنظیم شد:\n`{val}`")
        elif val.isdigit():
            await db.execute("UPDATE settings SET secretary_time = ? WHERE id = 1", (int(val),))
            await message.edit_text(f"✅ تایمر روی **{val} ثانیه** تنظیم شد.")
        await db.commit()

@user_app.on_message(filters.private & ~filters.me, group=6)
async def secretary_guard(client, message):
    if not message.from_user: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT secretary_active, secretary_text, secretary_time FROM settings WHERE id = 1")
        row = await cursor.fetchone()
    if not row or row[0] == 0: return
    current_time = time.time()
    if current_time - secretary_cooldowns.get(message.from_user.id, 0) >= row[2]:
        try:
            await client.send_message(message.chat.id, row[1])
            secretary_cooldowns[message.from_user.id] = current_time
        except: pass

# ۴. اسپمر
@user_app.on_message(filters.me & filters.regex(r"^اسپم\s*(سریع|ارام)?\s*(\d+)\s+(.+)$"), group=7)
async def spam_handler(client, message):
    speed, count, text = message.matches[0].group(1), min(int(message.matches[0].group(2)), 50), message.matches[0].group(3)
    delay = 3.0 if speed == "ارام" else 0.2
    await message.delete()
    for _ in range(count):
        try:
            await client.send_message(message.chat.id, text)
            await asyncio.sleep(delay)
        except: break

# ۵. فیلتر کلمات
@user_app.on_message(filters.me & filters.regex(r"^(فیلتر کلمه|حذف فیلتر کلمه)\s+(.+)$"), group=8)
async def manage_filter_words(client, message):
    cmd, word = message.matches[0].group(1), message.matches[0].group(2).strip().lower()
    async with aiosqlite.connect(DB_NAME) as db:
        if cmd == "فیلتر کلمه":
            await db.execute("INSERT OR IGNORE INTO filtered_words (word) VALUES (?)", (word,))
            await message.edit_text(f"✅ کلمه `{word}` فیلتر شد.")
        else:
            await db.execute("DELETE FROM filtered_words WHERE word = ?", (word,))
            await message.edit_text(f"🗑 کلمه `{word}` حذف شد.")
        await db.commit()

@user_app.on_message(filters.private & ~filters.me, group=9)
async def filter_words_guard(client, message):
    text = (message.text or message.caption or "").lower()
    if not text: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT word FROM filtered_words")
        words = [row[0] for row in await cursor.fetchall()]
    if any(w in text for w in words):
        try: await message.delete()
        except: pass

# ۶. عضویت اجباری
@user_app.on_message(filters.me & filters.regex(r"^تنظیم عضویت اجباری\s+(@[a-zA-Z0-9_]+)$"), group=10)
async def set_forcejoin(client, message):
    channel = message.matches[0].group(1)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO force_join_channels (channel_username) VALUES (?)", (channel,))
        await db.commit()
    await message.edit_text(f"✅ کانال `{channel}` اضافه شد.")

@user_app.on_message(filters.private & ~filters.me, group=11)
async def forcejoin_guard(client, message):
    if not message.from_user: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT channel_username FROM force_join_channels")
        channels = [row[0] for row in await cursor.fetchall()]
    not_joined = []
    for ch in channels:
        try:
            member = await client.get_chat_member(ch, message.from_user.id)
            if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]: not_joined.append(ch)
        except: not_joined.append(ch)
    if not_joined:
        try:
            await message.delete()
            await client.send_message(message.chat.id, f"❌ لطفاً در کانال‌های زیر عضو شوید:\n" + "\n".join(not_joined))
        except: pass

# ۷. تگ اعضا
@user_app.on_message(filters.me & filters.group & filters.regex(r"^(تگ اعضا|تگ ادمین)$"), group=12)
async def tag_handler(client, message):
    cmd = message.matches[0].group(1)
    await message.delete()
    users, count = [], 0
    try:
        if cmd == "تگ اعضا":
            async for m in client.get_chat_members(message.chat.id):
                if count >= 300: break
                if not m.user.is_bot: users.append(f"[{m.user.first_name}](tg://user?id={m.user.id})"); count += 1
        else:
            async for m in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                if not m.user.is_bot: users.append(f"[{m.user.first_name}](tg://user?id={m.user.id})")
        for i in range(0, len(users), 5):
            await client.send_message(message.chat.id, " ".join(users[i:i+5]))
            await asyncio.sleep(1.5)
    except: pass

# ۸. پاکسازی پیام
@user_app.on_message(filters.me & filters.regex(r"^حذف\s+(\d+)$"), group=13)
async def bulk_delete(client, message):
    count = min(int(message.matches[0].group(1)), 100) + 1
    try:
        msg_ids = [msg.id async for msg in client.get_chat_history(message.chat.id, limit=count)]
        if msg_ids: await client.delete_messages(message.chat.id, msg_ids, revoke=True)
    except: pass

# ۹. سکوت
@user_app.on_message(filters.me & filters.reply & filters.regex(r"^سکوت$"), group=14)
async def set_mute(client, message):
    target = message.reply_to_message.from_user
    if not target: return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO muted_users (target_user_id, target_name) VALUES (?, ?)", (target.id, target.first_name))
        await db.commit()
    await message.edit_text(f"✅ کاربر {target.first_name} خفه شد.")

@user_app.on_message(~filters.me, group=15)
async def mute_guard(client, message):
    if not message.from_user: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT 1 FROM muted_users WHERE target_user_id = ?", (message.from_user.id,))
        if await cursor.fetchone():
            try: await message.delete()
            except: pass

# ۱۰. اطلاعات و کپی پروفایل
@user_app.on_message(filters.me & filters.reply & filters.regex(r"^(ایدی|آیدی|کپی|copy)$"), group=16)
async def clone_profile(client, message):
    cmd, target = message.matches[0].group(1).lower(), message.reply_to_message.from_user
    if not target: return await message.edit_text("❌ کاربر یافت نشد.")
    await message.edit_text("🔄 `پردازش...`")
    chat = await client.get_chat(target.id)
    if cmd in ["ایدی", "آیدی"]:
        await message.edit_text(f"👤 **نام:** `{target.first_name}`\n🔸 **آیدی:** `{target.id}`\n🔸 **بیو:** `{chat.bio or 'ندارد'}`")
    else:
        try:
            await client.update_profile(first_name=target.first_name or "", last_name=target.last_name or "", bio=chat.bio or "")
            if target.photo:
                path = await client.download_media(target.photo.big_file_id)
                await client.set_profile_photo(photo=path)
                os.remove(path)
            await message.edit_text("✅ **هویت با موفقیت کپی شد!** 🎭")
        except: await message.edit_text("❌ خطا در سرقت هویت.")

# ۱۱. ماشین حساب
@user_app.on_message(filters.me & filters.regex(r"^حساب\s+(.+)$"), group=17)
async def calculator(client, message):
    expr = message.matches[0].group(1).replace("×", "*").replace("÷", "/")
    if not re.match(r"^[\d\.\s\+\-\*\/\(\)]+$", expr): return await message.edit_text("❌ فقط اعداد مجاز است.")
    try: await message.edit_text(f"🧮 `{message.matches[0].group(1)} = {int(eval(expr)) if eval(expr) == int(eval(expr)) else eval(expr)}`")
    except: await message.edit_text("❌ خطا.")

# ۱۲. دانلودر هوشمند
@user_app.on_message(filters.me & filters.regex(r"^(تلگرام|اینستا|یوتیوب)\s+(https?://[^\s]+)$"), group=18)
async def downloader(client, message):
    plat, link = message.matches[0].group(1), message.matches[0].group(2)
    await message.edit_text(f"🔄 `دانلود از {plat}...`")
    try:
        if plat == "تلگرام":
            parts = link.split("/")
            msg = await client.get_messages(int("-100" + parts[-2]) if "c" in parts else parts[-2], int(parts[-1]))
            if msg.media:
                path = await msg.download()
                await client.send_document(message.chat.id, path, caption=msg.caption or "")
                os.remove(path)
        else:
            opts = {'outtmpl': os.path.join(DATA_DIR, 'downloads', '%(id)s.%(ext)s'), 'format': 'best', 'quiet': True}
            def dl():
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.prepare_filename(ydl.extract_info(link, download=True))
            path = await asyncio.to_thread(dl)
            await client.send_video(message.chat.id, path)
            os.remove(path)
        await message.delete()
    except: await message.edit_text("❌ خطا در دانلود.")

# ۱۳. فضول‌گیر (ترول)
@user_app.on_message(filters.me & filters.regex(r"^فضول ها$"), group=19)
async def snoopers_troll(client, message):
    await message.edit_text("🔄 `اسکن سرور...`")
    await asyncio.sleep(2)
    users = [f"👤 [{d.chat.first_name or 'کاربر'}](tg://user?id={d.chat.id})" async for d in client.get_dialogs(limit=20) if d.chat.type == enums.ChatType.PRIVATE and not d.chat.is_bot]
    if not users: return await message.edit_text("کسی پروفایل شما را ندیده است!")
    text = "👁 **لیست فضول‌ها (۲۴ ساعت گذشته):**\n\n"
    for i, s in enumerate(random.sample(users, random.randint(2, min(5, len(users)))), 1): text += f"{i}. {s} ⇦ (`{random.randint(1,8)} بازدید`)\n"
    await message.edit_text(text)

# ==========================================
# 💾 بکاپ و بازیابی دیتابیس (فقط مالک)
# ==========================================
REQUIRED_TABLES = {
    "settings", "auto_targets", "filtered_words",
    "force_join_channels", "muted_users"
}

@bot_app.on_message(filters.private & filters.command("backup"))
async def backup_database(client, message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    backup_path = os.path.join(DATA_DIR, f"selfbot_backup_{int(time.time())}.db")
    try:
        # SQLite backup API فایل سالم و سازگار حتی در زمان استفاده از دیتابیس می‌سازد.
        async with aiosqlite.connect(DB_NAME) as source:
            async with aiosqlite.connect(backup_path) as target:
                await source.backup(target)
        await message.reply_document(
            backup_path,
            caption="✅ بکاپ دیتابیس آماده است. برای بازیابی، روی همین فایل ریپلای کن و /restore بزن."
        )
    except Exception as exc:
        await message.reply_text(f"❌ ساخت بکاپ ناموفق بود: {type(exc).__name__}")
    finally:
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
        except OSError:
            pass

@bot_app.on_message(filters.private & filters.command("restore"))
async def restore_database(client, message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    reply = message.reply_to_message
    document = reply.document if reply else None
    if not document:
        return await message.reply_text(
            "برای بازیابی، روی فایل بکاپ دیتابیس ریپلای کن و دستور /restore را بفرست."
        )
    if document.file_size and document.file_size > 50 * 1024 * 1024:
        return await message.reply_text("❌ فایل بیش از حد بزرگ است (حداکثر ۵۰ مگابایت).")

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="restore_", suffix=".db", dir=DATA_DIR)
        os.close(fd)
        downloaded = await client.download_media(document, file_name=temp_path)
        if not downloaded or not os.path.isfile(downloaded):
            raise ValueError("فایل دریافت نشد")

        # قبل از جایگزینی، سلامت SQLite و جدول‌های مورد انتظار بررسی می‌شوند.
        async with aiosqlite.connect(downloaded) as check_db:
            cursor = await check_db.execute("PRAGMA integrity_check")
            integrity = await cursor.fetchone()
            cursor = await check_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
        if not integrity or integrity[0] != "ok":
            raise ValueError("ساختار دیتابیس سالم نیست")
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError("این فایل بکاپ دیتابیس همین ربات نیست یا ناقص است")

        # ساخت نسخه اضطراری و جایگزینی اتمیک
        emergency_path = os.path.join(DATA_DIR, f"before_restore_{int(time.time())}.db")
        async with aiosqlite.connect(DB_NAME) as current_db:
            async with aiosqlite.connect(emergency_path) as emergency_db:
                await current_db.backup(emergency_db)
        os.replace(downloaded, DB_NAME)
        await message.reply_text(
            "✅ دیتابیس با موفقیت بازیابی شد. یک نسخه اضطراری از دیتابیس قبلی نیز در فضای داده ذخیره شد."
        )
    except Exception as exc:
        await message.reply_text(f"❌ بازیابی انجام نشد؛ دیتابیس فعلی دست‌نخورده ماند. علت: {type(exc).__name__}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

# ==========================================
# 🚀 راه‌اندازی ربات
# ==========================================
async def main():
    print("⏳ در حال ساخت دیتابیس...")
    await init_db()
    os.makedirs(os.path.join(DATA_DIR, "downloads"), exist_ok=True)
    print("🚀 سلف‌بات و ربات مدیریت آماده کار هستند!")
    await compose([user_app, bot_app])

if __name__ == "__main__":
    asyncio.run(main())
