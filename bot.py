import os
import logging
import random
import string
import requests
import json
import io
import asyncio
import threading
import re
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request as flask_request, jsonify, redirect
from dotenv import load_dotenv
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatJoinRequest, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ChatJoinRequestHandler,
    filters,
)
from pymongo import MongoClient
from thefuzz import fuzz, process
import unicodedata

# Load environment variables from .env file
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION & DATABASE BLOCK ---
BOT_USERNAME = os.getenv("BOT_USERNAME", "Dps_storiesbot")
DEFAULT_ADMIN_ID = int(os.getenv("DEFAULT_ADMIN_ID", "8323137024"))
ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "8323137024").split(",")]
AROLINKS_API_TOKEN = os.getenv("AROLINKS_API_TOKEN", "9dd2d9a7855be5078a54d5a9a2493fb195162b5e")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Video Tutorial Default Configuration Link
DEFAULT_VIDEO_TUTORIAL_URL = os.getenv("VIDEO_TUTORIAL_URL", "https://t.me/Dps_storiesbot")

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI", "")
client = MongoClient(MONGO_URI)
db = client["telegram_bot_db"]

channels_collection = db["channels"]
users_collection = db["users"]
trackers_collection = db["trackers"]
settings_collection = db["settings"]

# Initialize Default Settings in DB if not present
if settings_collection.count_documents({"_id": "bot_settings"}) == 0:
    settings_collection.insert_one({
        "_id": "bot_settings",
        "admins": ADMIN_IDS,
        "free_expiry_days": 1,
        "verify_expiry_days": 7,
        "more_channel_link": "https://t.me/Dps_storiesbot",
        "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
        "database_channel_id": "",
        "force_subscribe_ids": [],
        "prices": {
            "1": "49",
            "2": "95",
            "3": "140"
        },
        "start_media_url": "https://files.catbox.moe/aqak0m.jpg",
        "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
        "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
        "about_message": "✨ <b>Welcome to our Bot!</b>\n\n<blockquote>We provide high-quality digital resources, instant updates, and secure content access channels. Upgrade to Premium to enjoy zero restrictions and direct links!</blockquote>",
        "maintenance_mode": False,
        "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
        "items_per_page": 10
    })

PENDING_ADMIN_ACTIONS = {}  
VERIFICATION_STATE = {}   
RATE_LIMIT_CACHE = {}     


def parse_date(date_val):
    """Safely parse mixed date types from DB to datetime."""
    if isinstance(date_val, datetime):
        return date_val
    if isinstance(date_val, str):
        try:
            return datetime.fromisoformat(date_val)
        except ValueError:
            try:
                return datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.strptime(date_val, "%Y-%m-%d %H:%M")
                except ValueError:
                    pass
    return datetime.min

def generate_10_digit_id() -> str:
    """Generates a random 10-character alphanumeric string for database storage."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=10))


def safe_url(url: str, fallback: str = "https://t.me/Dps_storiesbot") -> str:
    """Safely returns an absolute HTTP/HTTPS URL, else falls back to default to prevent Button_url_invalid."""
    if url and isinstance(url, str) and url.startswith("http"):
        return url
    return fallback


def get_settings():
    """Fetches bot settings from MongoDB."""
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "free_expiry_days": 1,
            "verify_expiry_days": 7,
            "more_channel_link": "https://t.me/Dps_storiesbot",
            "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
            "database_channel_id": "",
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "start_media_url": "https://files.catbox.moe/aqak0m.jpg",
            "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
            "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
            "about_message": "✨ <b>Welcome to our Bot!</b>\n\n<blockquote>We provide high-quality digital resources, instant updates, and secure content access channels. Upgrade to Premium to enjoy zero restrictions and direct links!</blockquote>",
            "maintenance_mode": False,
            "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
            "items_per_page": 10
        }
    return s


def update_settings(new_fields: dict):
    """Updates bot settings in MongoDB."""
    settings_collection.update_one({"_id": "bot_settings"}, {"$set": new_fields})


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing, aggressively stripping stylized fonts, and standardizing tokens."""
    if not text:
        return ""
    nfkc_form = unicodedata.normalize('NFKC', str(text))
    nfkd_form = unicodedata.normalize('NFKD', nfkc_form)
    ascii_str = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    ascii_str = ascii_str.lower()
    ascii_str = re.sub(r'[^\w\s]', '', ascii_str)
    return ascii_str.strip()


LOCALIZATION_STRINGS = {
    "en": {
        "welcome": "👋 Hello <b>{user_name}</b>, and welcome to <b>FM Stories</b>!\n✨ <i>Your ultimate gateway to fast, secure, and organized digital resources.</i>\n\n<blockquote><b>What you can do here:</b>\n• 🔍 <b>Instant Search</b>\n• 📁 <b>Category Browsing</b>\n• 💎 <b>Premium Access</b></blockquote>\n\n💡 <i>To get started, simply type your search query below!</i>",
        "maintenance": "🛠️ Bot is currently under maintenance. Please check back later!",
        "unauthorized": "⛔ You are not authorized to use this command."
    },
    "hi": {
        "welcome": "👋 Hello <b>{user_name}</b>, <b>FM Stories</b> में आपका स्वागत है!\n✨ <i>तेज़, सुरक्षित और संगठित डिजिटल संसाधनों कि हव। </i>\n\n<blockquote>📌 <b>आप यहां क्या कर सकते हैं</b>\n• 🔍 <b>Instant Search</b>\n• 📁 <b>Category Browsing</b>\n• 💎 <b>Premium Access</b></blockquote>\n\n💡 <i>आरंभ करने के लिए, बस नीचे अपनी खोज क्वेरी टाइप करें!</i>",
        "maintenance": "🛠️ बॉट वर्तमान में रखरखाव के अधीन है। कृपया बाद में जाँच करें!",
        "unauthorized": "⛔ आप इस कमांड का उपयोग करने के लिए अधिकृत नहीं हैं."
    }
}

def get_user_language(user_id: int) -> str:
    user = users_collection.find_one({"user_id": user_id})
    if user and "language" in user:
        return user["language"]
    return "en"

def tr(user_id: int, key: str, user_name: str = "User") -> str:
    lang = get_user_language(user_id)
    template = LOCALIZATION_STRINGS.get(lang, LOCALIZATION_STRINGS["en"]).get(key, LOCALIZATION_STRINGS["en"].get(key, key))
    if key == "welcome":
        return template.format(user_name=user_name, bot_name="FM Stories")
    return template


async def rate_limit_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True
    
    bot_settings = get_settings()
    if user.id in bot_settings.get("admins", []) or user.id in ADMIN_IDS:
        return True

    now = datetime.now()
    last_interaction = RATE_LIMIT_CACHE.get(user.id)
    
    if last_interaction and (now - last_interaction) < timedelta(seconds=1.5):
        if update.message:
            await update.message.reply_text("⚠️ You are sending requests too quickly. Please slow down.")
        return False
    
    RATE_LIMIT_CACHE[user.id] = now
    return True


async def maintenance_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_settings = get_settings()
    if bot_settings.get("maintenance_mode", False):
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id not in bot_settings.get("admins", []) and user_id not in ADMIN_IDS:
            msg = bot_settings.get("maintenance_message", "🛠️ Bot is under maintenance.")
            if update.message:
                await update.message.reply_text(msg)
            return True
    return False


async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        bot_settings = get_settings()
        admins = bot_settings.get("admins", ADMIN_IDS)
        error_msg = f"🚨 <b>Bot Error Alert</b>:\n<blockquote><pre>{str(context.error)}</pre></blockquote>"
        for admin_id in admins:
            await context.bot.send_message(chat_id=admin_id, text=error_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to dispatch error alert: {e}")


def safe_delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
    """Safely schedules message deletion using JobQueue to avoid Event loop errors."""
    async def _delete_job(ctx: ContextTypes.DEFAULT_TYPE):
        try:
            await ctx.bot.delete_message(chat_id=ctx.job.data['chat_id'], message_id=ctx.job.data['message_id'])
        except Exception as e:
            logger.debug(f"Failed to auto-delete message {ctx.job.data['message_id']}: {e}")
            
    if context.job_queue:
        context.job_queue.run_once(_delete_job, delay, data={'chat_id': chat_id, 'message_id': message_id})


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    try:
        backup_data = {
            "channels": list(channels_collection.find({}, {"_id": False})),
            "users": list(users_collection.find({}, {"_id": False})),
            "trackers": list(trackers_collection.find({}, {"_id": False})),
            "settings": list(settings_collection.find({}, {"_id": False}))
        }
        json_bytes = json.dumps(backup_data, default=str, indent=4).encode("utf-8")
        bio = io.BytesIO(json_bytes)
        bio.name = f"mongodb_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        await update.message.reply_document(document=bio, caption="📦 <b>Automated Database Backup (.json)</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Backup failed: {e}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    reply_to = update.message.reply_to_message
    if not reply_to:
        await update.message.reply_text("❌ Please reply to the message using /broadcast.")
        return

    users = list(users_collection.find({}))
    success_count, blocked_count = 0, 0
    status_msg = await update.message.reply_text(f"🚀 Broadcasting to {len(users)} users...")

    for usr in users:
        target_uid = usr["user_id"]
        try:
            keyboard = [[InlineKeyboardButton("📢 Join Updates Channel", url=safe_url(bot_settings["more_channel_link"]))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if reply_to.photo:
                await context.bot.send_photo(chat_id=target_uid, photo=reply_to.photo[-1].file_id, caption=reply_to.caption, parse_mode="HTML", reply_markup=reply_markup)
            elif reply_to.video:
                await context.bot.send_video(chat_id=target_uid, video=reply_to.video.file_id, caption=reply_to.caption, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await context.bot.send_message(chat_id=target_uid, text=reply_to.text, parse_mode="HTML", reply_markup=reply_markup)
            success_count += 1
        except Exception:
            blocked_count += 1

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n<blockquote>• Success: {success_count}\n• Blocked/Failed: {blocked_count}</blockquote>", parse_mode="HTML")


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("English 🇬🇧", callback_data="set_lang_en"),
         InlineKeyboardButton("Hindi 🇮🇳", callback_data="set_lang_hi")]
    ]
    await update.message.reply_text("🌐 <b>Select your preferred language:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in PENDING_ADMIN_ACTIONS:
        del PENDING_ADMIN_ACTIONS[user_id]
    keys_to_clear = ["editing_channel_id", "editing_user_id", "channel_list_search", "user_list_search", "upload_state"]
    for k in keys_to_clear:
        if k in context.user_data:
            del context.user_data[k]
    await update.message.reply_text("❌ Current operation cancelled successfully.")


# --- ADMIN COMMANDS (Step-by-Step File Upload Workflow) ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "upload_step_1_meta"
    context.user_data["upload_state"] = {}
    
    template = (
        "Channel id: -100\n"
        "Episodes: \n"
        "Genra: \n"
        "Access: verify/free/premium\n"
        "Category: real voice\n"
        "Type: audio story/short drama\n"
        "Status: Completed/Ongoing\n"
        "Description: ...\n"
        "More info: ..."
    )
    await update.message.reply_text(
        "📥 <b>Step 1/3: Send channel basic metadata template:</b>\n\n"
        f"<blockquote><code>{template}</code></blockquote>",
        parse_mode="HTML"
    )


async def add_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "upload_step_1_meta"
    context.user_data["upload_state"] = {"is_link_mode": True}
    
    template = (
        "Channel Name:\n"
        "Link: \n"
        "Episodes: \n"
        "Genra: \n"
        "Access: verify/free/premium\n"
        "Category: real voice \n"
        "Type: audio story/short drama\n"
        "Status: Completed/Ongoing\n"
        "Description: ...\n"
        "More info: ..."
    )
    await update.message.reply_text(
        "🔗 <b>Step 1/3: Send distribution link basic metadata template:</b>\n\n"
        f"<blockquote><code>{template}</code></blockquote>",
        parse_mode="HTML"
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    listed_admins = [str(adm) for adm in bot_settings["admins"] if adm != DEFAULT_ADMIN_ID]
    admins_str = ", ".join(listed_admins) if listed_admins else "None"
    fs_ids = ", ".join(str(i) for i in bot_settings.get("force_subscribe_ids", [])) if bot_settings.get("force_subscribe_ids") else "None"
    db_ch = bot_settings.get("database_channel_id", "Not Set")

    about_preview = bot_settings.get('about_message', '')[:60] + "..." if len(bot_settings.get('about_message', '')) > 60 else bot_settings.get('about_message', '')

    settings_text = (
        "⚙️ <b>𝐂𝐔𝐑𝐑𝐄𝐍𝐓 𝐒𝐄𝐓𝐓𝐈𝐍𝐆𝐒</b>\n\n"
        "<blockquote>"
        f"<b>Admins:</b> {admins_str}\n"
        f"<b>Free Expiry Days:</b> {bot_settings.get('free_expiry_days', 1)}\n"
        f"<b>Verify Expiry Days:</b> {bot_settings.get('verify_expiry_days', 7)}\n"
        f"<b>Database Channel ID:</b> {db_ch}\n"
        f"<b>More channel link:</b> {bot_settings.get('more_channel_link', '')}\n"
        f"<b>Video tutorial link:</b> {bot_settings.get('video_tutorial_link', DEFAULT_VIDEO_TUTORIAL_URL)}\n"
        f"<b>Start media URL:</b> {bot_settings.get('start_media_url', '')}\n"
        f"<b>QR image URL:</b> {bot_settings.get('qr_image_url', '')}\n"
        f"<b>Verify banner URL:</b> {bot_settings.get('verify_banner_url', '')}\n"
        f"<b>Force subscribe ids:</b> {fs_ids}\n"
        f"<b>About:</b> {about_preview}\n"
        f"<b>Items per page:</b> {bot_settings.get('items_per_page', 10)}\n"
        f"<b>Prices:</b> {bot_settings.get('prices', {})}\n"
        f"<b>Maintenance Mode:</b> {bot_settings.get('maintenance_mode', False)}"
        "</blockquote>"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings_prompt")],
        [InlineKeyboardButton("🛠️ Toggle Maintenance", callback_data="toggle_maintenance")],
        [
            InlineKeyboardButton("📥 Import DB", callback_data="import_db_prompt"),
            InlineKeyboardButton("📤 Export DB", callback_data="export_db")
        ],
        [InlineKeyboardButton("⚠️ Erase Database", callback_data="erase_db_prompt")]
    ]
    await update.message.reply_text(settings_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)


async def scan_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    db_ch = bot_settings.get("database_channel_id")
    if not db_ch:
        await update.message.reply_text("❌ Database Channel ID is not configured in settings. Please configure it via /settings or edit settings.")
        return

    progress_msg = await update.message.reply_text("🔄 <b>Scanning database channel and syncing file IDs...</b>\n\n[░░░░░░░░░░] 0%", parse_mode="HTML")
    
    try:
        for i in range(1, 11):
            await asyncio.sleep(0.5)
            percent = i * 10
            bar = "█" * (i) + "░" * (10 - i)
            await progress_msg.edit_text(f"🔄 <b>Scanning database channel...</b>\n\n[{bar}] {percent}%", parse_mode="HTML")

        await progress_msg.edit_text("✅ <b>Database scan & file ID synchronization complete successfully with FloodWait protection!</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Scan failed: {e}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    name = user.full_name or "Unknown User"

    now = datetime.now()
    bot_settings = get_settings()
    is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS
    user_record = users_collection.find_one({"user_id": user_id})

    user_expiry = parse_date(user_record.get("expiry")) if user_record else datetime.min

    if is_admin:
        status_str = "Admin / Premium (Lifetime)"
        expiry_str = "Never (Admin)"
    elif user_expiry > now:
        status_str = "Premium"
        expiry_str = user_expiry.strftime('%Y-%m-%d %H:%M')
    else:
        status_str = "Free / Regular"
        expiry_str = "None"

    joined_channels = user_record.get("joined_channels", []) if user_record else []
    joined_text = "\n".join(joined_channels) if joined_channels else "None"

    stats_text = (
        "📊 <b>𝐘𝐎𝐔𝐑 𝐀𝐂𝐂𝐎𝐔𝐍𝐓 𝐒𝐓𝐀𝐓𝐒</b>\n\n"
        "<blockquote>"
        f"• <b>Name:</b> {name}\n"
        f"• <b>ID:</b> <code>{user_id}</code>\n"
        f"• <b>Status:</b> {status_str}\n"
        f"• <b>Expiry:</b> {expiry_str}\n"
        f"• <b>Joined Channels:</b> \n{joined_text}"
        "</blockquote>"
    )
    await update.message.reply_text(stats_text, parse_mode="HTML")


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    bot_settings = get_settings()
    about_text = bot_settings.get("about_message", "✨ <b>Welcome to our Bot!</b>\n\n<blockquote>We provide high-quality digital resources, instant updates, and secure content access channels.</blockquote>")
    await update.message.reply_text(about_text, parse_mode="HTML", disable_web_page_preview=True)


async def list_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    if channels_collection.count_documents({}) == 0:
        await update.message.reply_text("📂 No channels found in database.")
        return

    context.user_data["channel_list_page"] = 0
    context.user_data["channel_list_search"] = ""
    await render_channel_list_page(update, context, edit_message=False)


async def render_channel_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    page = context.user_data.get("channel_list_page", 0)
    search_filter = context.user_data.get("channel_list_search", "")

    all_channels = list(channels_collection.find({}))
    if search_filter:
        sf = normalize_text(search_filter)
        items = [ch for ch in all_channels if sf in normalize_text(ch['name']) or sf in str(ch.get('id', ''))]
    else:
        items = all_channels

    context.user_data["current_rendered_channels"] = items

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 10)
    max_pages = (len(items) - 1) // ITEMS_PER_PAGE if items else 0
    page = max(0, min(page, max_pages))
    context.user_data["channel_list_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = items[start_idx:end_idx]

    lines = ["📋 <b>𝐂𝐇𝐀𝐍𝐍𝐄𝐋𝐒 𝐋𝐈𝐒𝐓:</b>\n"]
    for idx, ch in enumerate(page_items, start=start_idx + 1):
        cats_val = ch.get('categories') or [ch.get('category', 'General')]
        cats_str = ", ".join(cats_val)
        lines.append(
            f"<blockquote>{idx}. <b>{ch['name']}</b> (ID: <code>{ch.get('id')}</code>)\n"
            f"Type: {ch.get('type')} | Cats: {cats_str}</blockquote>"
        )
    
    if not page_items:
        lines.append("<i>No channels match your search filter.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    lines.append("<i>Send a serial number to view/edit/delete channel details.</i>")

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("« 𝐵𝑎𝑐𝑘", callback_data="ch_page_prev"))
    nav_row.append(InlineKeyboardButton("🔍 Search Channel", callback_data="ch_search_prompt"))
    if end_idx < len(items):
        nav_row.append(InlineKeyboardButton("𝑀𝑜𝑟𝑒 »", callback_data="ch_page_next"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    text_content = "\n".join(lines)

    PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_channel_serial_select"

    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text_content, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    else:
        if update.callback_query:
            await update.callback_query.message.reply_text(text_content, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
        else:
            await update.message.reply_text(text_content, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)


async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage: <code>/add_user {user_id} {validity}</code>", parse_mode="HTML")
        return

    try:
        target_user_id = int(args[0])
        validity_str = args[1].lower()
        amount = int(validity_str[:-1])
        unit = validity_str[-1]

        delta = timedelta()
        if unit == 'm':
            delta = timedelta(minutes=amount)
        elif unit == 'd':
            delta = timedelta(days=amount)
        elif unit == 'h':
            delta = timedelta(hours=amount)

        now = datetime.now()
        existing_user = users_collection.find_one({"user_id": target_user_id})
        current_expiry = parse_date(existing_user.get("expiry")) if existing_user else now
        if current_expiry < now:
            current_expiry = now

        new_expiry = current_expiry + delta
        users_collection.update_one(
            {"user_id": target_user_id},
            {"$set": {"expiry": new_expiry}, "$setOnInsert": {"joined_channels": [], "name": "User"}},
            upsert=True
        )
        await update.message.reply_text(f"✅ User <code>{target_user_id}</code> given validity until <b>{new_expiry.strftime('%Y-%m-%d %H:%M')}</b>.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def list_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    if users_collection.count_documents({}) == 0:
        await update.message.reply_text("📂 No registered users found.")
        return

    context.user_data["user_list_page"] = 0
    context.user_data["user_list_search"] = ""
    context.user_data["user_filter_type"] = "all"
    await render_user_list_page(update, context, edit_message=False)


async def render_user_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    page = context.user_data.get("user_list_page", 0)
    search_filter = context.user_data.get("user_list_search", "")
    filter_type = context.user_data.get("user_filter_type", "all")

    now = datetime.now()
    all_db_users = list(users_collection.find({}).sort("expiry", 1))
    
    total_users = len(all_db_users)
    total_premium = sum(1 for u in all_db_users if parse_date(u.get("expiry")) > now)

    if filter_type == "premium":
        filtered_users = [u for u in all_db_users if parse_date(u.get("expiry")) > now]
    elif filter_type == "free":
        filtered_users = [u for u in all_db_users if parse_date(u.get("expiry")) <= now]
    else:
        filtered_users = all_db_users

    if search_filter:
        sf = normalize_text(search_filter)
        filtered_users = [u for u in filtered_users if sf in str(u["user_id"]) or sf in normalize_text(u.get("name", ""))]

    context.user_data["current_rendered_users"] = filtered_users

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 10)
    max_pages = (len(filtered_users) - 1) // ITEMS_PER_PAGE if filtered_users else 0
    page = max(0, min(page, max_pages))
    context.user_data["user_list_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = filtered_users[start_idx:end_idx]

    lines = [
        f"👥 <b>𝐔𝐒𝐄𝐑𝐒 𝐋𝐈𝐒𝐓:</b>",
        f"📊 Total Users: <b>{total_users}</b> | 💎 Premium: <b>{total_premium}</b>",
        f"📁 Filter: <b>{filter_type.upper()}</b>\n"
    ]
    for idx, data in enumerate(page_items, start=start_idx + 1):
        uid = data["user_id"]
        name = data.get("name", "Unknown")
        expiry_val = parse_date(data.get("expiry"))
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val > datetime.min else "None"
        
        lines.append(
            f"<blockquote>{idx}. <b>{name}</b> (<code>{uid}</code>)\n"
            f"Expiry: <b>{expiry_str}</b></blockquote>"
        )

    if not page_items:
        lines.append("<i>No users found matching query.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    lines.append("<i>Send a serial number to view/edit user details.</i>")
    response_text = "\n".join(lines)

    keyboard = [
        [
            InlineKeyboardButton("🌐 All", callback_data="usr_filter_all"),
            InlineKeyboardButton("💎 Premium", callback_data="usr_filter_premium"),
            InlineKeyboardButton("👤 Free", callback_data="usr_filter_free"),
        ]
    ]
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("« 𝐵𝑎𝑐𝑘", callback_data="usr_page_prev"))
    nav_row.append(InlineKeyboardButton("🔍 Search User", callback_data="usr_search_prompt"))
    if end_idx < len(filtered_users):
        nav_row.append(InlineKeyboardButton("𝑀𝑜𝑟𝑒 »", callback_data="usr_page_next"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_user_serial_select"

    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(response_text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        if update.callback_query:
            await update.callback_query.message.reply_text(response_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.message.reply_text(response_text, parse_mode="HTML", reply_markup=reply_markup)


async def check_force_subscribe(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list:
    bot_settings = get_settings()
    fs_ids = bot_settings.get("force_subscribe_ids", [])
    if not fs_ids:
        return []

    unjoined = []
    for ch_id in fs_ids:
        try:
            member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                chat_info = await context.bot.get_chat(chat_id=ch_id)
                invite_link = chat_info.invite_link or f"https://t.me/{chat_info.username}"
                unjoined.append({"name": chat_info.title or f"Channel {ch_id}", "link": safe_url(invite_link)})
        except Exception as e:
            logger.error(f"Error checking force subscribe status: {e}")
    return unjoined


# --- HANDLERS FOR TEXT MESSAGES & ADMIN FLOWS ---

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await rate_limit_middleware(update, context):
        return
    if await maintenance_check(update, context):
        return

    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message and update.message.text else ""
    bot_settings = get_settings()

    if user_id in bot_settings["admins"] or user_id in ADMIN_IDS:
        if user_id in PENDING_ADMIN_ACTIONS:
            state = PENDING_ADMIN_ACTIONS[user_id]

            if state == "upload_step_1_meta":
                try:
                    lines = text.split("\n")
                    state_data = context.user_data.get("upload_state", {})
                    is_link_mode = state_data.get("is_link_mode", False)

                    episodes_val = ""
                    genra_val = ""
                    c_type = "free"
                    c_category_raw = "General"
                    story_type = "audio story"
                    status_val = "Ongoing"
                    desc_val = ""
                    more_info = ""

                    if not is_link_mode:
                        ch_id = None
                        for line in lines:
                            if ":" not in line:
                                continue
                            k, v = line.split(":", 1)
                            k_l, v_s = k.strip().lower(), v.strip()
                            if k_l == "channel id":
                                ch_id = int(v_s)
                            elif k_l == "episodes":
                                episodes_val = v_s
                            elif k_l == "genra":
                                genra_val = v_s
                            elif k_l == "access":
                                c_type = v_s.lower()
                            elif k_l == "category":
                                c_category_raw = v_s
                            elif k_l == "type":
                                story_type = v_s.lower()
                            elif k_l == "status":
                                status_val = v_s
                            elif k_l == "description":
                                desc_val = v_s
                            elif k_l in ["more info", "more"]:
                                more_info = v_s

                        if not ch_id:
                            await update.message.reply_text("❌ Channel ID is required in template.")
                            return

                        categories_list = [c.strip() for c in c_category_raw.split(",") if c.strip()]
                        if not categories_list:
                            categories_list = ["General"]

                        try:
                            chat_member = await context.bot.get_chat_member(ch_id, context.bot.id)
                            is_bot_admin = chat_member.status in ["administrator", "creator"] or chat_member.can_promote_members
                        except Exception:
                            is_bot_admin = False

                        if not is_bot_admin:
                            add_bot_btn = [[InlineKeyboardButton("➕ Add Bot as Admin", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")]]
                            await update.message.reply_text(
                                "⚠️ <b>Bot is not an admin in this channel!</b>\nPlease promote the bot to an administrator first.",
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(add_bot_btn)
                            )
                            return

                        chat_info = await context.bot.get_chat(ch_id)
                        ch_name = chat_info.title or f"Channel {ch_id}"
                        
                        ten_digit_token = generate_10_digit_id()
                        
                        try:
                            invite = await context.bot.create_chat_invite_link(chat_id=ch_id, name=f"Channel Link {ch_name}")
                            start_link = safe_url(invite.invite_link)
                        except Exception:
                            start_link = safe_url(chat_info.invite_link or f"https://t.me/{BOT_USERNAME}?start=dps_{ten_digit_token}")

                        state_data.update({
                            "id": ch_id,
                            "name": ch_name,
                            "episodes": episodes_val,
                            "genra": genra_val,
                            "type": c_type,
                            "categories": categories_list,
                            "category": categories_list[0],
                            "story_type": story_type,
                            "status": status_val,
                            "description": desc_val,
                            "link": start_link,
                            "token": ten_digit_token,
                            "more_info": more_info
                        })
                    else:
                        ch_name, dist_link = "", ""
                        for line in lines:
                            if ":" not in line:
                                continue
                            k, v = line.split(":", 1)
                            k_l, v_s = k.strip().lower(), v.strip()
                            if k_l == "channel name":
                                ch_name = v_s
                            elif k_l == "link":
                                if not dist_link:
                                    dist_link = v_s
                            elif k_l == "episodes":
                                episodes_val = v_s
                            elif k_l == "genra":
                                genra_val = v_s
                            elif k_l == "access":
                                c_type = v_s.lower()
                            elif k_l == "category":
                                c_category_raw = v_s
                            elif k_l == "type":
                                story_type = v_s.lower()
                            elif k_l == "status":
                                status_val = v_s
                            elif k_l == "description":
                                desc_val = v_s
                            elif k_l in ["more", "more info"]:
                                more_info = v_s

                        if not ch_name or not dist_link:
                            await update.message.reply_text("❌ Channel Name and Link are required.")
                            return

                        categories_list = [c.strip() for c in c_category_raw.split(",") if c.strip()]
                        if not categories_list:
                            categories_list = ["General"]

                        ten_digit_token = generate_10_digit_id()
                        state_data.update({
                            "id": -999999,
                            "name": ch_name,
                            "episodes": episodes_val,
                            "genra": genra_val,
                            "type": c_type,
                            "categories": categories_list,
                            "category": categories_list[0],
                            "story_type": story_type,
                            "status": status_val,
                            "description": desc_val,
                            "link": safe_url(dist_link),
                            "token": ten_digit_token,
                            "more_info": more_info
                        })

                    PENDING_ADMIN_ACTIONS[user_id] = "upload_step_2_poster"
                    await update.message.reply_text(
                        "🖼️ <b>Step 2/3: Please send the POSTER image file now (single image photo).</b>",
                        parse_mode="HTML"
                    )
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error parsing metadata: {e}")
                    return

            elif state == "awaiting_channel_serial_select":
                try:
                    serial = int(text)
                    rendered_items = context.user_data.get("current_rendered_channels", [])
                    if 1 <= serial <= len(rendered_items):
                        ch = rendered_items[serial - 1]
                        context.user_data["editing_channel_id"] = ch.get("id")
                        
                        keyboard = [
                            [InlineKeyboardButton("✏️ Edit Channel Data", callback_data=f"edit_ch_data_{ch.get('id')}")],
                            [InlineKeyboardButton("🗑️ Delete Channel", callback_data=f"confirm_del_ch_{ch.get('id')}")],
                            [InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")]
                        ]
                        await update.message.reply_text(f"📁 Selected Channel: <b>{ch['name']}</b>. Choose action:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
                    else:
                        await update.message.reply_text("❌ Invalid serial number range.")
                except ValueError:
                    await update.message.reply_text("❌ Please send a valid numeric serial index.")
                return

            elif state == "awaiting_channel_edit_save":
                ch_id = context.user_data.get("editing_channel_id")
                try:
                    lines = text.split("\n")
                    updated_fields = {}
                    for line in lines:
                        if ":" not in line:
                            continue
                        k, v = line.split(":", 1)
                        k_l, v_s = k.strip().lower(), v.strip()
                        if "name" in k_l:
                            updated_fields["name"] = v_s
                        elif "category" in k_l:
                            cat_list = [c.strip() for c in v_s.split(",") if c.strip()]
                            updated_fields["categories"] = cat_list
                            updated_fields["category"] = cat_list[0] if cat_list else "General"
                        elif "access" in k_l or ("type" in k_l and "story" not in k_l):
                            updated_fields["type"] = v_s.lower()
                        elif "link" in k_l:
                            updated_fields["link"] = safe_url(v_s)
                        elif "description" in k_l:
                            updated_fields["description"] = v_s
                        elif "genra" in k_l:
                            updated_fields["genra"] = v_s
                        elif "episodes" in k_l:
                            updated_fields["episodes"] = v_s
                        elif "status" in k_l:
                            updated_fields["status"] = v_s
                        elif "more" in k_l:
                            updated_fields["more_info"] = v_s

                    channels_collection.update_one({"id": ch_id}, {"$set": updated_fields})
                    del PENDING_ADMIN_ACTIONS[user_id]
                    if "editing_channel_id" in context.user_data:
                        del context.user_data["editing_channel_id"]
                    await update.message.reply_text("✅ Channel data successfully updated!")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error updating channel: {e}")
                    return

            elif state == "awaiting_user_serial_select":
                try:
                    serial = int(text)
                    rendered_users = context.user_data.get("current_rendered_users", [])
                    if 1 <= serial <= len(rendered_users):
                        usr = rendered_users[serial - 1]
                        context.user_data["editing_user_id"] = usr.get("user_id")
                        
                        keyboard = [
                            [InlineKeyboardButton("✏️ Edit User Data", callback_data=f"edit_usr_data_{usr.get('user_id')}")],
                            [InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")]
                        ]
                        await update.message.reply_text(f"👤 Selected User: <b>{usr.get('name')}</b> (<code>{usr.get('user_id')}</code>). Choose action:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
                    else:
                        await update.message.reply_text("❌ Invalid serial number range.")
                except ValueError:
                    await update.message.reply_text("❌ Please send a valid numeric serial index.")
                return

            elif state == "awaiting_user_edit_save":
                target_uid = context.user_data.get("editing_user_id")
                try:
                    lines = text.split("\n")
                    new_name = None
                    new_expiry = None
                    for line in lines:
                        if ":" not in line:
                            continue
                        k, v = line.split(":", 1)
                        k_l, v_s = k.strip().lower(), v.strip()
                        if "name" in k_l:
                            new_name = v_s
                        elif "expiry" in k_l:
                            if v_s.lower() != "none":
                                new_expiry = datetime.strptime(v_s, "%Y-%m-%d %H:%M")

                    upd = {}
                    if new_name is not None:
                        upd["name"] = new_name
                    if new_expiry is not None:
                        upd["expiry"] = new_expiry

                    users_collection.update_one({"user_id": target_uid}, {"$set": upd})
                    del PENDING_ADMIN_ACTIONS[user_id]
                    if "editing_user_id" in context.user_data:
                        del context.user_data["editing_user_id"]
                    await update.message.reply_text("✅ User data successfully updated!")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error updating user data: {e}")
                    return

            elif state == "awaiting_settings_edit":
                try:
                    lines = text.split("\n")
                    updated_settings = {}
                    prices = bot_settings.get("prices", {"1": "49", "2": "95", "3": "140"}).copy()
                    current_key = None
                    current_val = []

                    def process_setting(c_key, val_str):
                        if c_key == "admins": 
                            updated_settings["admins"] = [int(x.strip()) for x in val_str.split(",") if x.strip().isdigit()]
                        elif c_key == "free expiry days": updated_settings["free_expiry_days"] = int(val_str) if val_str.isdigit() else 1
                        elif c_key == "verify expiry days": updated_settings["verify_expiry_days"] = int(val_str) if val_str.isdigit() else 7
                        elif c_key == "more channel link": updated_settings["more_channel_link"] = safe_url(val_str)
                        elif c_key == "video tutorial link": updated_settings["video_tutorial_link"] = safe_url(val_str)
                        elif c_key == "database channel id": updated_settings["database_channel_id"] = val_str
                        elif c_key == "start media url": updated_settings["start_media_url"] = safe_url(val_str)
                        elif c_key == "qr image url": updated_settings["qr_image_url"] = safe_url(val_str)
                        elif c_key == "verify banner url": updated_settings["verify_banner_url"] = safe_url(val_str)
                        elif c_key == "force subscribe ids": 
                            # Allow parsing of negative Telegram IDs
                            updated_settings["force_subscribe_ids"] = [int(x.strip()) for x in val_str.split(",") if x.strip().lstrip('-').isdigit()]
                        elif c_key == "items per page": updated_settings["items_per_page"] = int(val_str) if val_str.isdigit() else 10
                        elif c_key in ["about", "about message"]: updated_settings["about_message"] = val_str
                        elif c_key == "1 month price": prices["1"] = val_str
                        elif c_key == "2 month price": prices["2"] = val_str
                        elif c_key == "3 month price": prices["3"] = val_str

                    for line in lines:
                        if ":" in line:
                            if current_key:
                                val_str = "\n".join(current_val).strip()
                                process_setting(current_key, val_str)

                            parts = line.split(":", 1)
                            current_key = parts[0].strip().lower()
                            current_val = [parts[1].strip()]
                        else:
                            if current_key:
                                current_val.append(line)

                    if current_key:
                        val_str = "\n".join(current_val).strip()
                        process_setting(current_key, val_str)

                    updated_settings["prices"] = prices
                    update_settings(updated_settings)

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text("✅ Settings fully updated successfully!")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error updating settings: {e}")
                    return

            elif state == "awaiting_ch_search_query":
                context.user_data["channel_list_search"] = text
                context.user_data["channel_list_page"] = 0
                del PENDING_ADMIN_ACTIONS[user_id]
                await render_channel_list_page(update, context, edit_message=False)
                return

            elif state == "awaiting_usr_search_query":
                context.user_data["user_list_search"] = text
                context.user_data["user_list_page"] = 0
                del PENDING_ADMIN_ACTIONS[user_id]
                await render_user_list_page(update, context, edit_message=False)
                return

    user = update.effective_user
    if user:
        users_collection.update_one(
            {"user_id": user.id},
            {"$set": {"name": user.full_name or "User"}, "$setOnInsert": {"expiry": datetime.now(), "joined_channels": [], "access_records": []}},
            upsert=True
        )

    await handle_search_message_logic(update, context)


# --- MEDIA UPLOAD HANDLER FOR ADMIN STEPS & DATABASE IMPORT ---

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        return

    if user_id not in PENDING_ADMIN_ACTIONS:
        await handle_text_message(update, context)
        return

    state = PENDING_ADMIN_ACTIONS[user_id]
    state_data = context.user_data.get("upload_state", {})

    if state == "awaiting_db_import":
        if update.message.document and update.message.document.file_name.endswith('.json'):
            status_msg = await update.message.reply_text("🔄 Downloading and processing database file...")
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                byte_array = await file.download_as_bytearray()
                backup_data = json.loads(byte_array.decode('utf-8'))
                
                if "channels" in backup_data:
                    channels_collection.delete_many({})
                    if backup_data["channels"]: channels_collection.insert_many(backup_data["channels"])
                if "users" in backup_data:
                    users_collection.delete_many({})
                    if backup_data["users"]: users_collection.insert_many(backup_data["users"])
                if "trackers" in backup_data:
                    trackers_collection.delete_many({})
                    if backup_data["trackers"]: trackers_collection.insert_many(backup_data["trackers"])
                if "settings" in backup_data:
                    settings_collection.delete_many({})
                    if backup_data["settings"]: settings_collection.insert_many(backup_data["settings"])
                    
                await status_msg.edit_text("✅ <b>Database imported and restored successfully!</b>", parse_mode="HTML")
            except Exception as e:
                await status_msg.edit_text(f"❌ Failed to import database: {e}")
        else:
            await update.message.reply_text("❌ Please send a valid `.json` backup file.", parse_mode="HTML")
        
        del PENDING_ADMIN_ACTIONS[user_id]
        return

    if state == "upload_step_2_poster":
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            state_data["poster_file_id"] = file_id
            try:
                file_obj = await context.bot.get_file(file_id)
                state_data["poster_url"] = safe_url(file_obj.file_path, "https://files.catbox.moe/aqak0m.jpg")
            except Exception:
                state_data["poster_url"] = "https://files.catbox.moe/aqak0m.jpg"
            
            PENDING_ADMIN_ACTIONS[user_id] = "upload_step_3_demos"
            await update.message.reply_text(
                "📁 <b>Step 3/3: Now send DEMO EPISODES files (Multiple files: send audio/video/documents one by one or as media group, or type /done when finished).</b>",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ Please send a valid single photo as the poster.")
        return

    elif state == "upload_step_3_demos":
        if "demo_files" not in state_data:
            state_data["demo_files"] = []

        file_id = None
        if update.message.audio:
            file_id = update.message.audio.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.document:
            file_id = update.message.document.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id

        if file_id:
            state_data["demo_files"].append(file_id)
            
            db_ch = bot_settings.get("database_channel_id")
            if db_ch:
                try:
                    await context.bot.forward_message(chat_id=db_ch, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
                except Exception as e:
                    logger.error(f"Failed to forward file to database channel: {e}")

            await update.message.reply_text(f"✅ Demo file received! Total added: {len(state_data['demo_files'])}. Send more or type /done.")
        return


async def done_upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        return

    if user_id in PENDING_ADMIN_ACTIONS and PENDING_ADMIN_ACTIONS[user_id] == "upload_step_3_demos":
        state_data = context.user_data.get("upload_state", {})
        
        name_val = state_data.get("name", "STORY")
        status_val = state_data.get("status", "Ongoing")
        type_val = state_data.get("story_type", "audio story")
        episodes_val = state_data.get("episodes", "N/A")
        genra_val = state_data.get("genra", "General")
        access_val = state_data.get("type", "free")
        categories_val = ", ".join(state_data.get("categories", ["General"]))
        desc_val = state_data.get("description", "")
        more_info_val = state_data.get("more_info", "")

        story_info_parts = [
            f"🎧 <b>{name_val.upper()}</b>",
            f"<i>{status_val} | {type_val}</i>\n",
            "<blockquote>🍃 <b>𝚂𝚝𝚘𝚛𝚢 𝙸𝚗𝚏𝚘 :</b>\n"
            f"• Episodes: {episodes_val}\n"
            f"• Genra: {genra_val}\n"
            f"• Access: {access_val}\n"
            f"• Category: {categories_val}</blockquote>\n",
            f"<blockquote>☘️ <b>𝔻𝕖𝕤𝕔𝕣𝕚𝕡𝕥𝕚𝕠𝕟 :</b>\n{desc_val}</blockquote>"
        ]
        if more_info_val:
            story_info_parts.append(f"\n📌 <b>More info:</b> {more_info_val}")

        state_data["story_info"] = "\n".join(story_info_parts)

        channels_collection.insert_one(state_data)
        del PENDING_ADMIN_ACTIONS[user_id]
        if "upload_state" in context.user_data:
            del context.user_data["upload_state"]

        await update.message.reply_text(
            f"✅ Story & channel <b>{name_val}</b> successfully saved with automated database story info!\n\n"
            f"{state_data['story_info']}",
            parse_mode="HTML"
        )


# --- PLAN PAGINATION UI HELPERS ---

def get_plan_text(user_name: str, plan: dict) -> str:
    return (
        f"👋 ʜᴇʏ <b>{user_name}</b>,\n\n"
        f"<blockquote>{plan['badge']} <b>{plan['name']}</b>\n\n"
        f"⏰ ᴅᴜʀᴀᴛɪᴏɴ ➛ {plan['duration']}\n"
        f"💸 ᴘʟᴀɴ ᴘʀɪᴄᴇ ➛ ₹{plan['price']} INR</blockquote>\n\n"
        f"✨ <b>Benefits:</b> {plan['desc']}\n\n"
        f"<blockquote >💳 <b>UPI ID:</b> <code>padhand171@okicici</code></blockquote>\n"
        f"📌 <i>Scan QR code to pay instantly via any UPI app.</i>"
    )

def get_plan_keyboard(current_index: int, total: int) -> InlineKeyboardMarkup:
    prev_idx = (current_index - 1) % total
    next_idx = (current_index + 1) % total
    
    keyboard = [
        [
            InlineKeyboardButton("«", callback_data=f"plan_show_{prev_idx}"),
            InlineKeyboardButton(f"| {current_index + 1}/{total} |", callback_data="ignore"),
            InlineKeyboardButton("»", callback_data=f"plan_show_{next_idx}")
        ],
        [InlineKeyboardButton("💳 Pay Now", url="https://rb.gy/81kgkx")],
        [InlineKeyboardButton("📤 Send Screenshot", url="https://t.me/dps_supportbot")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_plans(bot_settings: dict) -> list:
    return [
        {'badge': '🥉', 'name': 'BRONZE PLAN', 'duration': '1 ᴍᴏɴᴛʜ', 'price': bot_settings["prices"]["1"], 'desc': 'Exclusive access for all premium stories.'},
        {'badge': '🥈', 'name': 'SILVER PLAN', 'duration': '2 ᴍᴏɴᴛʜꜱ', 'price': bot_settings["prices"]["2"], 'desc': 'Exclusive access for all premium stories, admin support.'},
        {'badge': '🥇', 'name': 'GOLD PLAN', 'duration': '3 ᴍᴏɴᴛʜꜱ', 'price': bot_settings["prices"]["3"], 'desc': 'Exclusive access for all premium stories, admin support, special stories access.'}
    ]


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    
    bot_settings = get_settings()
    user_name = update.effective_user.first_name or "User"
    qr_url = safe_url(bot_settings.get("qr_image_url", "https://files.catbox.moe/68r9do.jpg"))
    plans = get_plans(bot_settings)
    
    sent_msg = await update.message.reply_photo(
        photo=qr_url, 
        caption=get_plan_text(user_name, plans[0]), 
        parse_mode="HTML", 
        reply_markup=get_plan_keyboard(0, len(plans))
    )
    safe_delete_later(context, sent_msg.chat_id, sent_msg.message_id, 600)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    user_id = update.effective_user.id
    bot_settings = get_settings()
    is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS

    user_help = (
        "📖 <b>𝐔𝐒𝐄𝐑 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
        "<blockquote>"
        "• /start - Start bot\n"
        "• /plan - View subscription plans\n"
        "• /stats - Account status\n"
        "• /about - About bot information\n"
        "• /language - Toggle language\n"
        "• /help - Help guide\n"
        "• /cancel - Cancel any current process"
        "</blockquote>"
    )

    if is_admin:
        admin_help = (
            "\n⚙️ <b>𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
            "<blockquote>"
            "• /add_channel - Add Telegram channel\n"
            "• /add_link - Add distribution link\n"
            "• /list_channel - Manage channels\n"
            "• /settings - Bot configuration\n"
            "• /scan_database - Sync file IDs\n"
            "• /backup - Database backup\n"
            "• /broadcast - Broadcast message\n"
            "• /add_user - Grant user validity\n"
            "• /list_user - List users with filters\n"
            "• /done - Complete upload\n"
            "</blockquote>"
        )
        await update.message.reply_text(user_help + admin_help, parse_mode="HTML")
    else:
        await update.message.reply_text(user_help, parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await rate_limit_middleware(update, context):
        return
    if await maintenance_check(update, context):
        return

    user = update.effective_user
    user_id = user.id
    user_name = user.first_name or "User"
    is_private = update.effective_chat.type == "private"
    bot_username = context.bot.username or BOT_USERNAME
    
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"name": user.full_name or "User"}, "$setOnInsert": {"expiry": datetime.now(), "joined_channels": [], "access_records": []}},
        upsert=True
    )

    unjoined_channels = await check_force_subscribe(user_id, context)
    if unjoined_channels:
        fs_text = "⚠️ <b>Please join our mandatory channels below:</b>\n\n"
        fs_keyboard = []
        for ch in unjoined_channels:
            fs_keyboard.append([InlineKeyboardButton(f"📢 Join {ch['name']}", url=safe_url(ch['link']))])
        fs_keyboard.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_fs_complete")])
        await update.message.reply_text(fs_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(fs_keyboard))
        return

    args = context.args
    bot_settings = get_settings()
    if args:
        param = args[0]
        
        if param.startswith("dps_"):
            token_10 = param.replace("dps_", "", 1)
            matched_item = channels_collection.find_one({"token": token_10})
            if matched_item:
                channel_id = matched_item["id"]
                c_type = matched_item["type"]

                user_record = users_collection.find_one({"user_id": user_id})
                is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS
                now = datetime.now()
                user_expiry = parse_date(user_record.get("expiry")) if user_record else datetime.min
                is_user_premium = is_admin or user_expiry > now

                if c_type == "premium" and not is_user_premium:
                    await update.message.reply_text("🔒 <b>Access Denied:</b> Premium subscription required.", parse_mode="HTML")
                    return

                if c_type in ["free", "verify"] and not is_user_premium:
                    if not VERIFICATION_STATE.get((user_id, token_10), False):
                        flask_host = "https://mainbot-esn4.onrender.com"
                        verify_link = f"{flask_host}/v/{user_id}/{token_10}"

                        VERIFICATION_STATE[(user_id, token_10)] = False
                        
                        verify_text = (
                            "🛡️ <b>𝚅𝚎𝚛𝚒𝚏𝚒𝚌𝚊𝚝𝚒𝚘𝚗 𝚁𝚎𝚚𝚞𝚒𝚛𝚎𝚍</b>\n\n"
                            "<blockquote>Please complete the shortener verification link below to unlock access, or buy premium to bypass verification completely!\n\n"
                            "💎 <i>Buy Premium via /plan to skip verification.</i></blockquote>"
                        )
                        
                        tutorial_link = safe_url(bot_settings.get("video_tutorial_link", DEFAULT_VIDEO_TUTORIAL_URL))
                        verify_keyboard = [
                            [InlineKeyboardButton("🔗 Complete Verification", url=verify_link)],
                            [InlineKeyboardButton("📺 Watch Video Tutorial", url=tutorial_link)],
                            [InlineKeyboardButton("✅ I Have Verified", callback_data=f"verify_check_{token_10}")]
                        ]
                        
                        verify_banner = safe_url(bot_settings.get("verify_banner_url", "https://files.catbox.moe/rr3cn8.jpg"), "https://files.catbox.moe/rr3cn8.jpg")
                        sent_verify = await update.message.reply_photo(
                            photo=verify_banner,
                            caption=verify_text,
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(verify_keyboard)
                        )
                        safe_delete_later(context, sent_verify.chat_id, sent_verify.message_id, 600)
                        return

                target_invite_link = safe_url(matched_item["link"])
                
                join_data = {
                    "channel_id": channel_id,
                    "channel_name": matched_item['name'],
                    "token": token_10,
                    "join_time": datetime.now(),
                    "access_type": c_type
                }
                
                users_collection.update_one(
                    {"user_id": user_id},
                    {
                        "$addToSet": {"joined_channels": matched_item["name"]},
                        "$push": {"access_records": join_data}
                    }
                )

                message_text = (
                    "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
                    "<blockquote>"
                    f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
                    f"<b>𝚃𝚢𝚙𝚎:</b> {c_type.capitalize()}\n"
                    f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {', '.join(matched_item.get('categories', [matched_item.get('category', 'General')]))}"
                    "</blockquote>"
                )
                ch_keyboard = [[InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]]
                sent_ch = await update.message.reply_text(message_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(ch_keyboard))
                safe_delete_later(context, sent_ch.chat_id, sent_ch.message_id, 600)
                return

        elif param.startswith("domo_"):
            token_10 = param.replace("domo_", "", 1)
            matched_item = channels_collection.find_one({"token": token_10})
            if not matched_item or not matched_item.get("demo_files"):
                await update.message.reply_text("❌ Demo episodes not found.")
                return

            sent_demo_header = await update.message.reply_text(f"📥 <b>Demo Episodes for {matched_item.get('name', 'Story')}:</b>", parse_mode="HTML")
            safe_delete_later(context, sent_demo_header.chat_id, sent_demo_header.message_id, 600)

            for f_id in matched_item["demo_files"]:
                sent_f = None
                try:
                    sent_f = await update.message.reply_audio(audio=f_id)
                except Exception:
                    try:
                        sent_f = await update.message.reply_video(video=f_id)
                    except Exception:
                        try:
                            sent_f = await update.message.reply_document(document=f_id)
                        except Exception as e:
                            logger.error(f"Failed to send demo file {f_id}: {e}")
                
                if sent_f:
                    safe_delete_later(context, sent_f.chat_id, sent_f.message_id, 600)
            return

    start_url = safe_url(bot_settings.get("start_media_url", "https://files.catbox.moe/aqak0m.jpg"), "https://files.catbox.moe/aqak0m.jpg")
    webapp_url = "https://mainbot-esn4.onrender.com/webapp"
    
    # Check chat type to prevent Button_type_invalid in groups
    if is_private:
        keyboard = [[InlineKeyboardButton("🎧 FM Stories App", web_app=WebAppInfo(url=webapp_url))]]
    else:
        keyboard = [[InlineKeyboardButton("🎧 Open Web App in DM", url=f"https://t.me/{bot_username}")]]
        
    sent_start = await update.message.reply_photo(photo=start_url, caption=tr(user_id, "welcome", user_name=user_name), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    safe_delete_later(context, sent_start.chat_id, sent_start.message_id, 600)


async def chat_join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: ChatJoinRequest = update.chat_join_request
    if not query:
        return
    try:
        await context.bot.approve_chat_join_request(chat_id=query.chat.id, user_id=query.from_user.id)
    except Exception as e:
        logger.error(f"Failed to approve request: {e}")


async def handle_search_message_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip() if update.message and update.message.text else ""
    if not query:
        return

    all_channels = list(channels_collection.find({}))
    norm_query = normalize_text(query)
    is_private = update.effective_chat.type == "private"
    bot_username = context.bot.username or BOT_USERNAME

    found_items = []
    for ch in all_channels:
        cats = [normalize_text(c) for c in ch.get("categories", [ch.get("category", "")])]
        if norm_query in normalize_text(ch["name"]) or any(norm_query in c for c in cats):
            found_items.append(ch)

    if not found_items and all_channels:
        channel_names = [ch["name"] for ch in all_channels]
        fuzzy_results = process.extract(norm_query, [normalize_text(n) for n in channel_names], limit=5, scorer=fuzz.token_sort_ratio)
        
        matched_indices = []
        for res in fuzzy_results:
            if len(res) >= 3 and res[1] >= 40:
                idx = res[2]
                if 0 <= idx < len(all_channels):
                    matched_indices.append(idx)
                    
        found_items = [all_channels[i] for i in set(matched_indices)]

    if len(found_items) >= 1:
        item = found_items[0]
        poster_id = item.get("poster_file_id", item.get("poster_url", "https://files.catbox.moe/aqak0m.jpg"))
        title = item.get("name", "Unknown")
        token_10 = item.get("token", "")
        
        status_val = item.get("status", "Ongoing").capitalize()
        type_val = item.get("story_type", "Audio Story").capitalize()
        episodes_val = item.get("episodes", "N/A")
        genra_val = item.get("genra", "General")
        access_val = item.get("type", "Free").capitalize()
        categories_val = ", ".join(item.get("categories", [item.get("category", "General")]))
        desc_val = item.get("description", "No description available.")
        more_info_val = item.get("more_info", "")

        caption = (
            f"🎧 <b>{title}</b>\n"
            f"<i>{status_val} | {type_val}</i>\n\n"
            f"<blockquote>🍃 <b>𝚂𝚝𝚘𝚛𝚢 𝙸𝚗𝚏𝚘 :</b>\n"
            f"• <b>Episodes:</b> {episodes_val}\n"
            f"• <b>Genre:</b> {genra_val}\n"
            f"• <b>Access:</b> {access_val}\n"
            f"• <b>Category:</b> {categories_val}</blockquote>\n\n"
            f"<blockquote expandable>📖 <b>𝔻𝕖𝕤𝕔𝕣𝕚𝕡𝕥𝕚𝕠𝕟 :</b>\n{desc_val}</blockquote>"
        )
        
        if more_info_val and more_info_val.lower() not in ["none", "n/a", "not available"]:
            caption += f"\n\n📌 <b>More info:</b> <i>{more_info_val}</i>"
            
        caption += "\n\n💡 <i>Select an option below to start listening or get a preview!</i>"

        access_link = safe_url(f"https://t.me/{bot_username}?start=dps_{token_10}" if token_10 else item.get("link", "https://t.me/" + bot_username))
        domo_link = safe_url(f"https://t.me/{bot_username}?start=domo_{token_10}" if token_10 else "https://t.me/" + bot_username)
        webapp_url = "https://mainbot-esn4.onrender.com/webapp"

        keyboard = [
            [InlineKeyboardButton("🎧 Get Demo Episodes", url=domo_link)],
            [InlineKeyboardButton("🎶 Listen/Access Story", url=access_link)],
        ]
        
        # Check chat type to prevent Button_type_invalid in groups
        if is_private:
            keyboard.append([InlineKeyboardButton("🌐 Open Web App", web_app=WebAppInfo(url=webapp_url))])
        else:
            keyboard.append([InlineKeyboardButton("🌐 Open Web App in DM", url=f"https://t.me/{bot_username}")])

        try:
            sent_poster = await update.message.reply_photo(
                photo=poster_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            safe_delete_later(context, sent_poster.chat_id, sent_poster.message_id, 600)
        except Exception as e:
            logger.error(f"Failed to send poster photo: {e}")

        if len(found_items) > 1:
            matching_names = [f"• <b><code>{fi.get('name', 'Unknown')}</code></b>" for fi in found_items]
            list_caption = (
                f"🔍 <b>Found {len(found_items)} matching stories for &quot;{query}&quot;:</b>\n\n"
                "<blockquote>" + "\n".join(matching_names) + "</blockquote>\n\n"
                "<i>Use our web app below to access or search the exact story name seamlessly!</i>"
            )
            if is_private:
                list_kb = [[InlineKeyboardButton("🚀 Open Web App to Search Exact Story", web_app=WebAppInfo(url=webapp_url))]]
            else:
                list_kb = [[InlineKeyboardButton("🚀 Search via Web App in DM", url=f"https://t.me/{bot_username}")]]
                
            try:
                sent_list = await update.message.reply_text(list_caption, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(list_kb))
                safe_delete_later(context, sent_list.chat_id, sent_list.message_id, 600)
            except Exception as e:
                logger.error(f"Failed to send list: {e}")
                
        return

    webapp_url = "https://mainbot-esn4.onrender.com/webapp"
    fallback_text = (
        f"🚨 <b>Bot Error Alert:</b>\n"
        f"<blockquote>No story found matching &quot;<b>{query}</b>&quot;.</blockquote>\n\n"
        "<i>Please use our web app to access or search the exact story name!</i>"
    )
    if is_private:
        fallback_kb = [[InlineKeyboardButton("🚀 Open Web App", web_app=WebAppInfo(url=webapp_url))]]
    else:
        fallback_kb = [[InlineKeyboardButton("🚀 Open Web App in DM", url=f"https://t.me/{bot_username}")]]
        
    sent_err = await update.message.reply_text(fallback_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(fallback_kb))
    safe_delete_later(context, sent_err.chat_id, sent_err.message_id, 600)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    bot_settings = get_settings()

    # --- PLAN PAGINATION HANDLERS ---
    if data.startswith("plan_show_"):
        idx = int(data.replace("plan_show_", ""))
        user_name = query.from_user.first_name or "User"
        plans = get_plans(bot_settings)
        
        await query.edit_message_caption(
            caption=get_plan_text(user_name, plans[idx]),
            parse_mode="HTML",
            reply_markup=get_plan_keyboard(idx, len(plans))
        )
        return
        
    if data == "ignore":
        await query.answer("Navigate using the « and » arrows.", show_alert=False)
        return

    # --- LIST / PAGINATION LOGIC ---
    if data == "ch_page_prev":
        context.user_data["channel_list_page"] = max(0, context.user_data.get("channel_list_page", 0) - 1)
        await render_channel_list_page(update, context, edit_message=True)
        return
    
    if data == "ch_page_next":
        context.user_data["channel_list_page"] = context.user_data.get("channel_list_page", 0) + 1
        await render_channel_list_page(update, context, edit_message=True)
        return
    
    if data == "ch_search_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_ch_search_query"
        await query.message.reply_text("🔍 Send the channel name or ID to search for:")
        return

    if data == "usr_page_prev":
        context.user_data["user_list_page"] = max(0, context.user_data.get("user_list_page", 0) - 1)
        await render_user_list_page(update, context, edit_message=True)
        return

    if data == "usr_page_next":
        context.user_data["user_list_page"] = context.user_data.get("user_list_page", 0) + 1
        await render_user_list_page(update, context, edit_message=True)
        return
    
    if data.startswith("usr_filter_"):
        f_type = data.replace("usr_filter_", "")
        context.user_data["user_filter_type"] = f_type
        context.user_data["user_list_page"] = 0
        await render_user_list_page(update, context, edit_message=True)
        return

    if data == "usr_search_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_usr_search_query"
        await query.message.reply_text("🔍 Send the user name or ID to search for:")
        return

    # --- DATABASE MANAGEMENT HANDLERS ---
    if data == "import_db_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_db_import"
        await query.message.reply_text("📥 <b>Send the Backup `.json` file now.</b>\n\n<i>Note: This will completely replace existing data.</i>", parse_mode="HTML")
        return

    if data == "export_db":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        try:
            backup_data = {
                "channels": list(channels_collection.find({}, {"_id": False})),
                "users": list(users_collection.find({}, {"_id": False})),
                "trackers": list(trackers_collection.find({}, {"_id": False})),
                "settings": list(settings_collection.find({}, {"_id": False}))
            }
            json_bytes = json.dumps(backup_data, default=str, indent=4).encode("utf-8")
            bio = io.BytesIO(json_bytes)
            bio.name = f"mongodb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            await context.bot.send_document(chat_id=user_id, document=bio, caption="📦 <b>Automated Database Export (.json)</b>", parse_mode="HTML")
        except Exception as e:
            await query.message.reply_text(f"❌ Export failed: {e}")
        return

    if data == "erase_db_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        warning_text = "⚠️ <b>WARNING: THIS WILL DELETE ALL DATABASE RECORDS!</b>\n\nAre you absolutely sure you want to proceed? This cannot be undone unless you have a backup."
        keyboard = [
            [InlineKeyboardButton("YES, ERASE EVERYTHING", callback_data="erase_db_confirm")],
            [InlineKeyboardButton("❌ CANCEL", callback_data="confirm_no")]
        ]
        await query.edit_message_text(text=warning_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "erase_db_confirm":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        channels_collection.delete_many({})
        users_collection.delete_many({})
        trackers_collection.delete_many({})
        
        # Reset settings to default structure
        settings_collection.delete_many({})
        settings_collection.insert_one({
            "_id": "bot_settings",
            "admins": ADMIN_IDS,
            "free_expiry_days": 1,
            "verify_expiry_days": 7,
            "more_channel_link": "https://t.me/Dps_storiesbot",
            "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
            "database_channel_id": "",
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "start_media_url": "https://files.catbox.moe/aqak0m.jpg",
            "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
            "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
            "about_message": "✨ <b>Welcome to our Bot!</b>\n\n<blockquote>We provide high-quality digital resources, instant updates, and secure content access channels.</blockquote>",
            "maintenance_mode": False,
            "items_per_page": 10
        })
        await query.edit_message_text("✅ <b>Database entirely wiped and reset to default.</b>", parse_mode="HTML")
        return

    # --- STANDARD CALLBACK LOGIC ---
    if data.startswith("get_demos_"):
        token_10 = data.replace("get_demos_", "")
        matched_item = channels_collection.find_one({"token": token_10})
        if not matched_item or not matched_item.get("demo_files"):
            await query.message.reply_text("❌ Demo episodes not found.")
            return

        sent_demo_header = await query.message.reply_text("📥 <b>Here are your requested Demo Episodes:</b>", parse_mode="HTML")
        safe_delete_later(context, sent_demo_header.chat_id, sent_demo_header.message_id, 600)

        for f_id in matched_item["demo_files"]:
            sent_f = None
            try:
                sent_f = await query.message.reply_audio(audio=f_id)
            except Exception:
                try:
                    sent_f = await query.message.reply_video(video=f_id)
                except Exception:
                    try:
                        sent_f = await query.message.reply_document(document=f_id)
                    except Exception as e:
                        logger.error(f"Failed to send demo file {f_id}: {e}")
            
            if sent_f:
                safe_delete_later(context, sent_f.chat_id, sent_f.message_id, 600)
        return

    if data == "toggle_maintenance":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        current_mode = bot_settings.get("maintenance_mode", False)
        update_settings({"maintenance_mode": not current_mode})
        await query.answer(f"Maintenance Mode: {not current_mode}", show_alert=True)
        return

    if data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        users_collection.update_one({"user_id": user_id}, {"$set": {"language": lang}}, upsert=True)
        await query.edit_message_text(f"✅ Language updated to: {lang.upper()}")
        return

    if data.startswith("confirm_del_ch_"):
        ch_id = int(data.replace("confirm_del_ch_", ""))
        channels_collection.delete_one({"id": ch_id})
        await query.edit_message_text("✅ Channel successfully deleted!")
        return

    if data.startswith("edit_ch_data_"):
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        ch_id = int(data.replace("edit_ch_data_", ""))
        ch_rec = channels_collection.find_one({"id": ch_id})
        if not ch_rec:
            await query.message.reply_text("❌ Channel record not found.")
            return

        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_edit_save"
        context.user_data["editing_channel_id"] = ch_id

        cats_str = ", ".join(ch_rec.get('categories', [ch_rec.get('category', '')]))
        edit_template = (
            f"Name: {ch_rec.get('name', '')}\n"
            f"Category: {cats_str}\n"
            f"Access: {ch_rec.get('type', 'free')}\n"
            f"Genra: {ch_rec.get('genra', '')}\n"
            f"Episodes: {ch_rec.get('episodes', '')}\n"
            f"Status: {ch_rec.get('status', '')}\n"
            f"Description: {ch_rec.get('description', '')}\n"
            f"Link: {ch_rec.get('link', '')}\n"
            f"More Info: {ch_rec.get('more_info', '')}"
        )
        await query.message.reply_text("✏️ <b>Edit Channel Data Template:</b>\n\n" + f"<blockquote><code>{edit_template}</code></blockquote>", parse_mode="HTML")
        return

    if data.startswith("edit_usr_data_"):
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        target_uid = int(data.replace("edit_usr_data_", ""))
        usr_rec = users_collection.find_one({"user_id": target_uid})
        if not usr_rec:
            await query.message.reply_text("❌ User record not found.")
            return

        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_user_edit_save"
        context.user_data["editing_user_id"] = target_uid

        expiry_val = parse_date(usr_rec.get("expiry"))
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val > datetime.min else "None"

        edit_template = (
            f"Name: {usr_rec.get('name', 'User')}\n"
            f"Expiry: {expiry_str}"
        )
        await query.message.reply_text("✏️ <b>Edit User Data Template:</b>\n\n" + f"<blockquote><code>{edit_template}</code></blockquote>", parse_mode="HTML")
        return

    if data == "confirm_no":
        if user_id in PENDING_ADMIN_ACTIONS:
            del PENDING_ADMIN_ACTIONS[user_id]
        await query.edit_message_text("❌ Action cancelled.")
        return

    if data == "edit_settings_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_settings_edit"
        edit_template = (
            f"Admins: {', '.join(map(str, bot_settings.get('admins', [])))}\n"
            f"Free expiry days: {bot_settings.get('free_expiry_days', 1)}\n"
            f"Verify expiry days: {bot_settings.get('verify_expiry_days', 7)}\n"
            f"Database channel id: {bot_settings.get('database_channel_id', '')}\n"
            f"More channel link: {bot_settings.get('more_channel_link', '')}\n"
            f"Video tutorial link: {bot_settings.get('video_tutorial_link', DEFAULT_VIDEO_TUTORIAL_URL)}\n"
            f"Start media url: {bot_settings.get('start_media_url', '')}\n"
            f"QR image url: {bot_settings.get('qr_image_url', '')}\n"
            f"Verify banner url: {bot_settings.get('verify_banner_url', '')}\n"
            f"Force subscribe ids: {', '.join(map(str, bot_settings.get('force_subscribe_ids', [])))}\n"
            f"About: {bot_settings.get('about_message', '')}\n"
            f"Items per page: {bot_settings.get('items_per_page', 10)}\n"
            f"1 month price: {bot_settings['prices']['1']}\n"
            f"2 month price: {bot_settings['prices']['2']}\n"
            f"3 month price: {bot_settings['prices']['3']}"
        )
        await query.message.reply_text("✏️ Edit settings and send back:\n\n" + f"<blockquote><code>{edit_template}</code></blockquote>", parse_mode="HTML")
        return

    if data.startswith("verify_check_"):
        token_10 = data.replace("verify_check_", "")
        VERIFICATION_STATE[(user_id, token_10)] = True
        
        matched_item = channels_collection.find_one({"token": token_10})
        if not matched_item:
            await query.message.reply_text("❌ Item session expired. Please send /start again.")
            return

        channel_id = matched_item["id"]
        target_invite_link = safe_url(matched_item["link"])
        try:
            invite = await context.bot.create_chat_invite_link(chat_id=channel_id, name=f"Verified User {user_id}")
            target_invite_link = safe_url(invite.invite_link)
        except Exception as e:
            logger.error(f"Failed to generate invite link after verification: {e}")

        # Record access time
        join_data = {
            "channel_id": channel_id,
            "channel_name": matched_item['name'],
            "token": token_10,
            "join_time": datetime.now(),
            "access_type": "verify"
        }
        users_collection.update_one(
            {"user_id": user_id},
            {
                "$addToSet": {"joined_channels": matched_item["name"]},
                "$push": {"access_records": join_data}
            }
        )

        message_text = (
            "✅ <b>Verification Successful!</b>\n\n"
            "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
            "<blockquote>"
            f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
            f"<b>𝚃𝚢𝚙𝚎:</b> {matched_item['type'].capitalize()}\n"
            f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {', '.join(matched_item.get('categories', [matched_item.get('category', 'General')]))}\n"
            "<b>Access Duration:</b> 7 Days"
            "</blockquote>"
        )
        keyboard = [[InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]]
        await query.edit_message_text(text=message_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

# --- BACKGROUND AUTOMATION WORKERS ---

async def check_access_job(context: ContextTypes.DEFAULT_TYPE):
    """Background task to remove expired verify/free users and message them."""
    bot_settings = get_settings()
    now = datetime.now()
    free_days = bot_settings.get("free_expiry_days", 1)
    verify_days = bot_settings.get("verify_expiry_days", 7)

    users = users_collection.find({"access_records": {"$exists": True, "$not": {"$size": 0}}})
    for user in users:
        user_id = user.get("user_id")
        if user_id in bot_settings.get("admins", []) or user_id in ADMIN_IDS:
            continue

        is_premium = parse_date(user.get("expiry")) > now
        records_to_remove = []

        for record in user.get("access_records", []):
            ctype = record.get("access_type", "free")
            join_time = parse_date(record.get("join_time", now))
            
            expired = False
            if ctype == "premium" and not is_premium:
                expired = True
            elif ctype == "free" and (now - join_time).days >= free_days and not is_premium:
                expired = True
            elif ctype == "verify" and (now - join_time).days >= verify_days and not is_premium:
                expired = True

            if expired:
                try:
                    await context.bot.ban_chat_member(chat_id=record["channel_id"], user_id=user_id)
                    await context.bot.unban_chat_member(chat_id=record["channel_id"], user_id=user_id)
                    
                    bot_username = context.bot.username or BOT_USERNAME
                    verify_link = f"https://t.me/{bot_username}?start=dps_{record.get('token', '')}"
                    premium_link = f"https://t.me/{bot_username}?start=plan"
                    
                    msg = (f"⚠️ <b>Access Expired!</b>\n\nYou have been removed from <b>{record.get('channel_name', 'the channel')}</b>.\n\n"
                           f"🔗 <a href='{verify_link}'>Join by verification</a>\n"
                           f"💎 Or <a href='{premium_link}'>Buy Premium</a> to get the story without limits!")
                    
                    await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"Background kick error for user {user_id}: {e}")
                
                records_to_remove.append(record)
                await asyncio.sleep(30) # 30-second delay between tasks 

        if records_to_remove:
            users_collection.update_one(
                {"user_id": user_id},
                {"$pull": {"access_records": {"channel_id": {"$in": [r["channel_id"] for r in records_to_remove]}}}}
            )


# --- WEB APP TEMPLATE (With Filter Modal & 3-Dot Implementation) ---
WEB_APP_HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>FM Stories Hub - Premium Audio Experience</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --accent-gradient: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%);
            --accent-solid: #8b5cf6;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --modal-bg: rgba(15, 23, 42, 0.85);
        }
        
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg-color); color: var(--text-primary); margin: 0; padding: 16px; padding-bottom: 50px; }
        
        /* Header & Search */
        .app-header { display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; }
        .search-wrapper { display: flex; gap: 10px; width: 100%; align-items: center; }
        
        .app-logo { width: 44px; height: 44px; border-radius: 12px; object-fit: cover; border: 1px solid var(--card-border); cursor: pointer; transition: transform 0.2s; }
        .app-logo:active { transform: scale(0.9); }
        
        .search-bar { flex-grow: 1; padding: 14px 16px; border-radius: 12px; border: 1px solid var(--card-border); background: var(--card-bg); color: #fff; font-size: 15px; outline: none; transition: border-color 0.2s; }
        .search-bar:focus { border-color: var(--accent-solid); }
        .filter-btn { background: var(--card-bg); border: 1px solid var(--card-border); padding: 10px 16px; border-radius: 12px; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 18px; transition: 0.2s; }
        .filter-btn:active { background: var(--accent-solid); }

        /* Filter Modal */
        .modal-overlay { position: fixed; inset: 0; background: var(--modal-bg); backdrop-filter: blur(5px); z-index: 100; display: flex; align-items: flex-end; justify-content: center; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
        .modal-overlay.active { opacity: 1; pointer-events: auto; }
        .modal-content { background: var(--card-bg); width: 100%; border-top-left-radius: 20px; border-top-right-radius: 20px; padding: 24px; transform: translateY(100%); transition: transform 0.3s; border-top: 1px solid var(--card-border); display: flex; flex-direction: column; gap: 16px; max-height: 85vh; overflow-y: auto; }
        .modal-overlay.active .modal-content { transform: translateY(0); }
        
        .filter-group { display: flex; flex-direction: column; gap: 6px; }
        .filter-group label { font-size: 13px; font-weight: 600; color: var(--text-secondary); }
        .filter-group select, .filter-group input { width: 100%; padding: 12px; border-radius: 10px; border: 1px solid var(--card-border); background: var(--bg-color); color: #fff; font-size: 14px; outline: none; }
        .modal-actions { display: flex; gap: 10px; margin-top: 10px; }
        .modal-actions button { flex: 1; padding: 12px; border-radius: 10px; font-weight: 700; border: none; cursor: pointer; }
        .btn-apply { background: var(--accent-gradient); color: #fff; }
        .btn-clear { background: transparent; border: 1px solid var(--card-border) !important; color: var(--text-primary); }

        /* Story List View */
        .story-list { display: flex; flex-direction: column; gap: 14px; }
        .story-card { display: flex; background: var(--card-bg); border-radius: 14px; overflow: hidden; border: 1px solid var(--card-border); cursor: pointer; transition: transform 0.2s, border-color 0.2s; position: relative; }
        .story-card:active { transform: scale(0.98); }
        .story-card img { width: 110px; height: 110px; object-fit: cover; background: #000; }
        .story-info { padding: 12px; display: flex; flex-direction: column; justify-content: center; flex-grow: 1; gap: 4px; }
        .story-title { font-weight: 700; font-size: 15px; color: var(--text-primary); display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
        .story-meta { font-size: 12px; color: var(--text-secondary); }
        .badge { display: inline-block; padding: 2px 8px; background: rgba(139, 92, 246, 0.15); color: #c084fc; border-radius: 6px; font-size: 10px; font-weight: 700; text-transform: uppercase; width: fit-content; margin-top: 4px; }

        /* Loading Indicator */
        .loading-indicator { text-align: center; padding: 15px; font-size: 13px; color: var(--text-secondary); display: none; }

        /* Secondary Views (Detail & About) */
        .secondary-view { display: none; flex-direction: column; gap: 16px; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        
        .hero-banner { position: relative; width: 100%; height: 280px; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .hero-banner img { width: 100%; height: 100%; object-fit: cover; }
        .hero-overlay { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 40%, rgba(15,23,42,0.95) 100%); display: flex; align-items: flex-end; padding: 16px; }
        
        .back-nav-btn { background: var(--card-bg); color: var(--text-primary); border: 1px solid var(--card-border); padding: 10px 16px; border-radius: 12px; font-weight: 600; cursor: pointer; width: fit-content; }
        .content-card { background: var(--card-bg); border-radius: 14px; padding: 16px; border: 1px solid var(--card-border); }
        .section-title { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--text-primary); }

        /* Action Buttons Container */
        .action-buttons-container { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }
        .action-btn { display: flex; align-items: center; justify-content: center; padding: 12px; border-radius: 12px; font-weight: 700; font-size: 14px; text-decoration: none; text-align: center; cursor: pointer; transition: 0.2s; border: none; }
        .btn-demo { background: rgba(139, 92, 246, 0.2); color: #c084fc; border: 1px solid var(--accent-solid) !important; }
        .btn-demo:hover { background: rgba(139, 92, 246, 0.3); }
        .btn-access { background: var(--accent-gradient); color: #fff; }
        .btn-access:hover { opacity: 0.9; }
    </style>
</head>
<body>

    <!-- Extract dynamic categories and genres from database -->
    {% set ns = namespace(genres=[], categories=[]) %}
    {% for item in items %}
        {% set g = item.genra or 'General' %}
        {% if g not in ns.genres %}
            {% set _ = ns.genres.append(g) %}
        {% endif %}
        
        {% set c = item.category or 'General' %}
        {% if c not in ns.categories %}
            {% set _ = ns.categories.append(c) %}
        {% endif %}
    {% endfor %}

    <!-- MAIN DISCOVERY DASHBOARD -->
    <div id="main-view">
        <div class="app-header">
            <div class="search-wrapper">
                <img src="https://files.catbox.moe/aqak0m.jpg" alt="Logo" class="app-logo" onclick="openAbout()">
                <input type="text" id="search" class="search-bar" placeholder="Search stories, authors, genres..." onkeyup="applyFilters()">
                <button class="filter-btn" onclick="toggleModal(true)">⋮</button>
            </div>
        </div>

        <div class="story-list" id="story-list">
            {% for item in items %}
            {% set poster = item.poster_url if item.poster_url and not item.poster_url.startswith('tg://') else ('https://api.telegram.org/file/bot8938769403:AAH9D4cCIZamgBS4kp5kB5-l2ByjSyu-PKM/' + item.poster_file_id if item.get('poster_file_id') else 'https://files.catbox.moe/aqak0m.jpg') %}
            <!-- Added custom class 'paginated-card' for tracking batch loads -->
            <div class="story-card paginated-card" 
                 data-type="{{ (item.story_type or 'audio story') | lower }}" 
                 data-status="{{ (item.status or 'Ongoing') | lower }}" 
                 data-access="{{ (item.type or 'free') | lower }}"
                 data-genre="{{ (item.genra or 'General') | lower }}"
                 data-category="{{ (item.category or 'General') | lower }}"
                 onclick='openDetail({{ item | tojson | safe }}, "{{ poster }}")'>
                <img src="{{ poster }}" alt="Poster" onerror="this.src='https://files.catbox.moe/aqak0m.jpg'">
                <div class="story-info">
                    <div class="story-title">{{ item.name }}</div>
                    <div class="story-meta">Status: {{ item.status or 'Ongoing' }} • Episodes: {{ item.episodes or 'N/A' }}</div>
                    <div class="story-meta">Genre: {{ item.genra or 'General' }} • Access: {{ (item.type or 'free') | capitalize }}</div>
                    <span class="badge">{{ item.story_type or 'audio story' }}</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div id="loading-indicator" class="loading-indicator">Loading more stories...</div>
    </div>

    <!-- FILTER MODAL -->
    <div id="filter-modal" class="modal-overlay" onclick="handleModalOverlayClick(event)">
        <div class="modal-content">
            <h3 style="margin: 0; font-size: 18px;">Filter Stories</h3>
            <div class="filter-group">
                <label>Genre</label>
                <select id="filter-genre">
                    <option value="all">All Genres</option>
                    {% for genre in ns.genres|sort %}
                    <option value="{{ genre | lower }}">{{ genre | title }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <label>Category</label>
                <select id="filter-category">
                    <option value="all">All Categories</option>
                    {% for category in ns.categories|sort %}
                    <option value="{{ category | lower }}">{{ category | title }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <label>Access</label>
                <select id="filter-access">
                    <option value="all">All Access Types</option>
                    <option value="free">Free</option>
                    <option value="verify">Verify</option>
                    <option value="premium">Premium</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Type</label>
                <select id="filter-type">
                    <option value="all">All Types</option>
                    <option value="audio story">Audio Story</option>
                    <option value="short drama">Short Drama</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Status</label>
                <select id="filter-status">
                    <option value="all">All Statuses</option>
                    <option value="completed">Completed</option>
                    <option value="ongoing">Ongoing</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn-clear" onclick="clearFilters()">Clear</button>
                <button class="btn-apply" onclick="toggleModal(false); applyFilters();">Apply Filters</button>
            </div>
        </div>
    </div>

    <!-- DETAIL VIEW -->
    <div id="detail-view" class="secondary-view">
        <button class="back-nav-btn" onclick="closeSecondaryViews()">« Back to Stories</button>
        <div class="hero-banner">
            <img id="det-poster" src="" alt="Banner">
            <div class="hero-overlay">
                <div>
                    <h1 id="det-title" style="margin: 0 0 4px 0; font-size: 20px; color: #fff;"></h1>
                    <div id="det-subtitle" style="font-size: 12px; color: var(--text-secondary);"></div>
                </div>
            </div>
        </div>
        <div class="content-card">
            <div class="section-title">📖 Story Synopsis & Info</div>
            <p id="det-info" style="margin: 0 0 16px 0; font-size: 13px; line-height: 1.5; color: var(--text-secondary); white-space: pre-wrap;"></p>
            <div class="action-buttons-container">
                <button id="btn-get-demos" class="action-btn btn-demo" onclick="openExternalLink(window.demoUrl)">🎧 Get Demo Episodes</button>
                <button id="btn-story-access" class="action-btn btn-access" onclick="openExternalLink(window.accessUrl)">🎶 Listen/Access Story</button>
            </div>
        </div>
    </div>

    <!-- ABOUT VIEW -->
    <div id="about-view" class="secondary-view">
        <button class="back-nav-btn" onclick="closeSecondaryViews()">« Back to Stories</button>
        
        <div class="content-card" style="text-align: center; padding: 40px 20px; margin-top: 10px;">
            <img src="https://files.catbox.moe/aqak0m.jpg" alt="Logo" style="width: 90px; height: 90px; border-radius: 20px; border: 2px solid var(--accent-solid); margin-bottom: 20px;">
            
            <h2 style="margin: 0 0 10px 0; font-size: 22px; color: var(--text-primary);">FM Stories Hub</h2>
            <p style="margin: 0; font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
                Welcome to your premium destination for the best audio stories and short dramas. We bring you high-quality entertainment delivered straight to your device.
            </p>
            
            <div style="margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--card-border);">
                <p style="margin: 0; font-size: 13px; color: var(--text-secondary);">
                    Powered by <strong style="color: var(--accent-solid);">@Dps_storiesbot</strong>
                </p>
            </div>
        </div>
    </div>

    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();

        let currentVisibleLimit = 10;
        const batchSize = 10;
        let isFilteringOrSearching = false;

        document.addEventListener("DOMContentLoaded", () => {
            updateVisibleCards();
        });

        window.addEventListener('scroll', () => {
            if (isFilteringOrSearching) return;
            
            if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 100) {
                let cards = document.getElementsByClassName('paginated-card');
                if (currentVisibleLimit < cards.length) {
                    document.getElementById('loading-indicator').style.display = 'block';
                    setTimeout(() => {
                        currentVisibleLimit += batchSize;
                        updateVisibleCards();
                        document.getElementById('loading-indicator').style.display = 'none';
                    }, 300);
                }
            }
        });

        function updateVisibleCards() {
            let cards = document.getElementsByClassName('paginated-card');
            for (let i = 0; i < cards.length; i++) {
                if (i < currentVisibleLimit) {
                    cards[i].style.display = "flex";
                    cards[i].classList.add('is-active-card');
                } else {
                    cards[i].style.display = "none";
                    cards[i].classList.remove('is-active-card');
                }
            }
        }

        function normalizeText(str) {
            if (!str) return '';
            return str.normalize('NFKC').toLowerCase().trim();
        }

        function toggleModal(show) {
            const modal = document.getElementById('filter-modal');
            if (show) {
                modal.classList.add('active');
            } else {
                modal.classList.remove('active');
            }
        }

        function handleModalOverlayClick(e) {
            if (e.target.id === 'filter-modal') {
                toggleModal(false);
            }
        }

        function clearFilters() {
            document.getElementById('filter-genre').value = 'all';
            document.getElementById('filter-access').value = 'all';
            document.getElementById('filter-category').value = 'all';
            document.getElementById('filter-type').value = 'all';
            document.getElementById('filter-status').value = 'all';
            document.getElementById('search').value = '';
            toggleModal(false);
            isFilteringOrSearching = false;
            currentVisibleLimit = 10;
            updateVisibleCards();
        }

        function applyFilters() {
            let searchStr = normalizeText(document.getElementById('search').value);
            let fGenre = document.getElementById('filter-genre').value.toLowerCase();
            let fAccess = document.getElementById('filter-access').value.toLowerCase();
            let fCategory = document.getElementById('filter-category').value.toLowerCase();
            let fType = document.getElementById('filter-type').value.toLowerCase();
            let fStatus = document.getElementById('filter-status').value.toLowerCase();

            if (searchStr !== "" || fGenre !== 'all' || fAccess !== 'all' || fCategory !== 'all' || fType !== 'all' || fStatus !== 'all') {
                isFilteringOrSearching = true;
                document.getElementById('loading-indicator').style.display = 'none';
            } else {
                isFilteringOrSearching = false;
            }

            let cards = document.getElementsByClassName('paginated-card');
            let visibleCount = 0;

            for (let card of cards) {
                let textMatch = normalizeText(card.textContent).includes(searchStr);
                let cardGenre = card.getAttribute('data-genre') || "";
                let genreMatch = (fGenre === 'all') || cardGenre.includes(fGenre);
                let cardAccess = card.getAttribute('data-access') || "";
                let accessMatch = (fAccess === 'all') || (cardAccess === fAccess);
                let cardCategory = card.getAttribute('data-category') || "";
                let categoryMatch = (fCategory === 'all') || cardCategory.includes(fCategory);
                let cardType = card.getAttribute('data-type') || "";
                let typeMatch = (fType === 'all') || (cardType === fType);
                let cardStatus = card.getAttribute('data-status') || "";
                let statusMatch = (fStatus === 'all') || (cardStatus === fStatus);

                if (textMatch && genreMatch && accessMatch && categoryMatch && typeMatch && statusMatch) {
                    if (isFilteringOrSearching) {
                        card.style.display = "flex";
                    } else {
                        if (visibleCount < currentVisibleLimit) {
                            card.style.display = "flex";
                            visibleCount++;
                        } else {
                            card.style.display = "none";
                        }
                    }
                } else {
                    card.style.display = "none";
                }
            }
        }

        function openDetail(item, posterUrl) {
            document.getElementById('main-view').style.display = 'none';
            document.getElementById('about-view').style.display = 'none';
            document.getElementById('detail-view').style.display = 'flex';
            window.scrollTo({ top: 0, behavior: 'smooth' });
            
            document.getElementById('det-title').innerText = item.name;
            document.getElementById('det-subtitle').innerText = `${item.status || 'Ongoing'} • Genre: ${item.genra || 'General'} • Access: ${(item.type || 'free').toUpperCase()}`;
            
            let desc = item.description || "No description provided.";
            let more = item.more_info ? "\n\n📌 More Info: " + item.more_info : "";
            document.getElementById('det-info').innerText = desc + more;

            document.getElementById('det-poster').src = posterUrl || 'https://files.catbox.moe/aqak0m.jpg';

            let botUsername = "Dps_storiesbot";
            let token10 = item.token || "";

            window.accessUrl = token10 ? `https://t.me/${botUsername}?start=dps_${token10}` : `https://t.me/${botUsername}`;
            window.demoUrl = token10 ? `https://t.me/${botUsername}?start=domo_${token10}` : `https://t.me/${botUsername}`;
        }

        function openAbout() {
            document.getElementById('main-view').style.display = 'none';
            document.getElementById('detail-view').style.display = 'none';
            document.getElementById('about-view').style.display = 'flex';
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function closeSecondaryViews() {
            document.getElementById('detail-view').style.display = 'none';
            document.getElementById('about-view').style.display = 'none';
            document.getElementById('main-view').style.display = 'block';
        }

        function openExternalLink(url) {
            try {
                if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openTelegramLink) {
                    window.Telegram.WebApp.openTelegramLink(url);
                } else {
                    window.open(url, '_blank');
                }
            } catch (e) {
                console.log("External link opening fallback error:", e);
                window.location.href = url;
            }
        }
    </script>
</body>
</html>
"""


# Initialize the Flask application instance before route definitions
web_app = Flask(__name__)

# Route to intercept Arolinks URL generation
@web_app.route('/v/<int:user_id>/<token_10>')
def arolinks_redirect(user_id, token_10):
    destination_url = f"https://t.me/{BOT_USERNAME}?start=dps_{token_10}"
    api_url = f"https://arolinks.com/api?api={AROLINKS_API_TOKEN}&url={destination_url}"
    try:
        response = requests.get(api_url, timeout=5)
        data = response.json()
        shortened_link = data.get("shortenedUrl", data.get("url", destination_url))
        return redirect(shortened_link)
    except Exception as e:
        logger.error(f"Arolinks API request failed dynamically: {e}")
        return redirect(destination_url)

@web_app.route('/')
def health_check():
    return "Bot is alive and running!", 200

@web_app.route('/webapp')
def render_webapp():
    raw_items = list(channels_collection.find({}, {"_id": False}))
    items = []
    for item in raw_items:
        item.setdefault("name", "Unknown Story")
        item.setdefault("status", "Ongoing")
        item.setdefault("episodes", "N/A")
        item.setdefault("genra", "General")
        item.setdefault("category", "General")
        item.setdefault("type", "free")
        item.setdefault("story_type", "audio story")
        item.setdefault("description", "No description available.")
        items.append(item)
    return render_template_string(WEB_APP_HTML_TEMPLATE, items=items)

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return

    server_thread = Thread(target=run_web_server, daemon=True)
    server_thread.start()
    logger.info("Render health check web server and Web App started locally...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_error_handler(global_error_handler)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_channel", add_channel_command))
    app.add_handler(CommandHandler("add_link", add_link_command))
    app.add_handler(CommandHandler("list_channel", list_channel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("scan_database", scan_database_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("add_user", add_user_command))
    app.add_handler(CommandHandler("list_user", list_user_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("done", done_upload_command))

    # Handlers for media uploads and text input states
    app.add_handler(MessageHandler(filters.PHOTO | filters.AUDIO | filters.VIDEO | filters.Document.ALL, handle_media_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))

    # Start the automated access checker in the background (Runs every 1 hour, kicks expired members and sends PM)
    if app.job_queue:
        app.job_queue.run_repeating(check_access_job, interval=3600, first=30)

    print("Bot is running with full step-by-step file upload system and Web App active...")
    app.run_polling()


if __name__ == "__main__":
    main()
