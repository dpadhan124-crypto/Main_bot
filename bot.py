import os
import logging
import random
import string
import requests
import json
import io
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatJoinRequest
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
DEFAULT_VIDEO_TUTORIAL_URL = os.getenv("VIDEO_TUTORIAL_URL", "https://t.me/your_video_tutorial_channel")

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
        "more_channel_link": "https://t.me/your_more_channel/",
        "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
        "force_subscribe_ids": [],
        "prices": {
            "1": "49",
            "2": "95",
            "3": "140"
        },
        "start_media_url": "https://ibb.co/ynTDh3tn",
        "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
        "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
        "about_message": "✨ <b>Welcome to our Bot!</b>\n\nWe provide high-quality digital resources, instant updates, and secure content access channels. Upgrade to Premium to enjoy zero restrictions and direct links!",
        "maintenance_mode": False,
        "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
        "items_per_page": 10
    })

PENDING_ADMIN_ACTIONS = {}  
VERIFICATION_STATE = {}   
RATE_LIMIT_CACHE = {}     


def generate_dps_token() -> str:
    """Generates a random 15-character token starting with dps_."""
    chars = string.ascii_letters + string.digits
    rand_part = ''.join(random.choices(chars, k=12))
    return f"dps_{rand_part}"


def get_settings():
    """Fetches bot settings from MongoDB."""
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "more_channel_link": "https://t.me/your_more_channel/",
            "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "start_media_url": "https://ibb.co/ynTDh3tn",
            "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
            "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
            "about_message": "✨ <b>Welcome to our Bot!</b>\n\nWe provide high-quality digital resources, instant updates, and secure content access channels. Upgrade to Premium to enjoy zero restrictions and direct links!",
            "maintenance_mode": False,
            "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
            "items_per_page": 10
        }
    return s


def update_settings(new_fields: dict):
    """Updates bot settings in MongoDB."""
    settings_collection.update_one({"_id": "bot_settings"}, {"$set": new_fields})


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing, stripping fancy unicode variants, and standardizing tokens."""
    if not text:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', text)
    ascii_str = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    return ascii_str.strip().lower()


LOCALIZATION_STRINGS = {
    "en": {
        "welcome": "👋 Hello and Welcome!\n</b>, and welcome to <b>DPS Stories</b>!\n✨ <i>Your ultimate gateway to fast, secure, and organized digital resources.</i>\n\n<blockquote>───────────────────\n📌 <b>What you can do here:</b>\n• 🔍 <b>Instant Search:</b> Send any keyword or phrase to quickly find what you're looking for.\n• 📁 <b>Category Browsing:</b> Filter content effortlessly by your favorite categories.\n• 💎 <b>Premium Access:</b> Upgrade to enjoy zero restrictions and direct link unlocks.\n───────────────────</blockquote>\n\n💡 <i>To get started, simply type your search query below or explore our options using the buttons!</i>",
        "maintenance": "🛠️ Bot is currently under maintenance. Please check back later!",
        "unauthorized": "⛔ You are not authorized to use this command."
    },
    "hi": {
        "welcome": "👋 Hello and Welcome!\n<b>DPS Stories</b> में आपका स्वागत है!\n✨ <i>तेज़, सुरक्षित और संगठित डिजिटल संसाधनों के लिए आपका अंतिम प्रवेश द्वार।</i>\n\n<blockquote>───────────────────\n📌 <b>आप यहां क्या कर सकते हैं</b>\n• 🔍 <b>Instant Search:</b> आप जो खोज रहे हैं उसे तुरंत खोजने के लिए कोई भी कीवर्ड या वाक्यांश भेजें।\n• 📁 <b>Category Browsing:</b> अपनी पसंदीदा श्रेणियों द्वारा सामग्री को आसानी से फ़िल्टर करें।\n• 💎 <b>Premium Access:</b> शून्य प्रतिबंधों का आनंद लेने के लिए अपग्रेड करें और सीधा लिंक अनलॉक करें।\n───────────────────</blockquote>\n\n💡 <i>आरंभ करने के लिए, बस नीचे अपनी खोज क्वेरी टाइप करें या बटनों का उपयोग करके हमारे विकल्पों का अन्वेषण करें!</i>",
        "maintenance": "🛠️ बॉट वर्तमान में रखरखाव के अधीन है। कृपया बाद में जाँच करें!",
        "unauthorized": "⛔ आप इस कमांड का उपयोग करने के लिए अधिकृत नहीं हैं."
    }
}

def get_user_language(user_id: int) -> str:
    user = users_collection.find_one({"user_id": user_id})
    if user and "language" in user:
        return user["language"]
    return "en"

def tr(user_id: int, key: str) -> str:
    lang = get_user_language(user_id)
    return LOCALIZATION_STRINGS.get(lang, LOCALIZATION_STRINGS["en"]).get(key, LOCALIZATION_STRINGS["en"].get(key, key))


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
            keyboard = [[InlineKeyboardButton("📢 Join Updates Channel", url=bot_settings["more_channel_link"])]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if reply_to.photo:
                await context.bot.send_photo(chat_id=target_uid, photo=reply_to.photo[-1].file_id, caption=reply_to.caption, parse_mode="HTML", reply_markup=reply_markup)
            elif reply_to.video:
                await context.bot.send_video(chat_id=target_uid, video=reply_to.video.file_id, caption=reply_to.caption, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await context.bot.send_message(chat_id=target_uid, text=reply_to.text, parse_mode="HTML", reply_markup=reply_markup)
            success_count += 1
        except Exception as e:
            blocked_count += 1

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n<blockquote>• Success: {success_count}\n• Blocked/Failed: {blocked_count}</blockquote>")


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
    keys_to_clear = ["editing_channel_id", "editing_user_id", "channel_list_search", "user_list_search"]
    for k in keys_to_clear:
        if k in context.user_data:
            del context.user_data[k]
    await update.message.reply_text("❌ Current operation cancelled successfully.")


# --- ADMIN COMMANDS ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_details"
    template = (
        "Channel id:\n"
        "Type:free/verify/premium\n"
        "Category: Cat1, Cat2, Cat3\n"
        "More info:"
    )
    await update.message.reply_text("📥 <b>Send channel details template (Supports multiple categories separated by commas):</b>\n\n" + f"<blockquote><code>{template}</code></blockquote>", parse_mode="HTML")


async def add_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "awaiting_link_details"
    template = (
        "Channel Name:\n"
        "Types:\n"
        "Category: Cat1, Cat2\n"
        "More:\n"
        "Link:"
    )
    await update.message.reply_text("🔗 <b>Send distribution link template (Supports multiple categories separated by commas):</b>\n\n" + f"<blockquote><code>{template}</code></blockquote>", parse_mode="HTML")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    listed_admins = [str(adm) for adm in bot_settings["admins"] if adm != DEFAULT_ADMIN_ID]
    admins_str = ", ".join(listed_admins) if listed_admins else "None"
    fs_ids = ", ".join(str(i) for i in bot_settings["force_subscribe_ids"]) if bot_settings["force_subscribe_ids"] else "None"

    settings_text = (
        "⚙️ <b>𝐂𝐔𝐑𝐑𝐄𝐍𝐓 𝐒𝐄𝐓𝐓𝐈𝐍𝐆𝐒</b>\n\n"
        "<blockquote>"
        f"<b>Admins:</b> {admins_str}\n"
        f"<b>More channel link:</b> {bot_settings['more_channel_link']}\n"
        f"<b>Video tutorial link:</b> {bot_settings.get('video_tutorial_link', DEFAULT_VIDEO_TUTORIAL_URL)}\n"
        f"<b>Force subscribe ids:</b> {fs_ids}\n"
        f"<b>Prices:</b> {bot_settings['prices']}\n"
        f"<b>Start Media URL:</b> {bot_settings.get('start_media_url', 'Default')}\n"
        f"<b>QR/Pay Image URL:</b> {bot_settings.get('qr_image_url', 'Default')}\n"
        f"<b>Verify Banner URL:</b> {bot_settings.get('verify_banner_url', 'Default')}\n"
        f"<b>About Message:</b> Configured\n"
        f"<b>Maintenance Mode:</b> {bot_settings.get('maintenance_mode', False)}"
        "</blockquote>"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings_prompt")],
        [InlineKeyboardButton("🛠️ Toggle Maintenance", callback_data="toggle_maintenance")]
    ]
    await update.message.reply_text(settings_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    name = user.full_name or "Unknown User"

    now = datetime.now()
    bot_settings = get_settings()
    is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS
    user_record = users_collection.find_one({"user_id": user_id})

    if is_admin:
        status_str = "Admin / Premium (Lifetime)"
        expiry_str = "Never (Admin)"
    elif user_record and user_record.get("expiry") and user_record["expiry"] > now:
        status_str = "Premium"
        expiry_str = user_record["expiry"].strftime('%Y-%m-%d %H:%M')
    else:
        status_str = "Free / Regular"
        expiry_str = "None"

    joined_channels = user_record.get("joined_channels", []) if user_record else []
    joined_text = ", ".join(joined_channels) if joined_channels else "None"

    stats_text = (
        "📊 <b>𝐘𝐎𝐔𝐑 𝐀𝐂𝐂𝐎𝐔𝐍𝐓 𝐒𝐓𝐀𝐓𝐒</b>\n\n"
        "<blockquote>"
        f"• <b>Name:</b> {name}\n"
        f"• <b>ID:</b> <code>{user_id}</code>\n"
        f"• <b>Status:</b> {status_str}\n"
        f"• <b>Expiry:</b> {expiry_str}\n"
        f"• <b>Joined Channels:</b> {joined_text}"
        "</blockquote>"
    )
    await update.message.reply_text(stats_text, parse_mode="HTML")


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    bot_settings = get_settings()
    about_text = bot_settings.get("about_message", "✨ <b>Welcome to our Bot!</b>\n\nWe provide high-quality digital resources, instant updates, and secure content access channels.")
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
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 5)
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
            f"   Type: {ch.get('type')} | Cats: {cats_str}</blockquote>"
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
        current_expiry = existing_user.get("expiry", now) if existing_user else now
        if not current_expiry or current_expiry < now:
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
    all_users = list(users_collection.find({}).sort("expiry", 1))

    if filter_type == "premium":
        all_users = [u for u in all_users if u.get("expiry") and u["expiry"] > now]
    elif filter_type == "free":
        all_users = [u for u in all_users if not u.get("expiry") or u["expiry"] <= now]

    if search_filter:
        sf = normalize_text(search_filter)
        all_users = [u for u in all_users if sf in str(u["user_id"]) or sf in normalize_text(u.get("name", ""))]

    context.user_data["current_rendered_users"] = all_users

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 5)
    max_pages = (len(all_users) - 1) // ITEMS_PER_PAGE if all_users else 0
    page = max(0, min(page, max_pages))
    context.user_data["user_list_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = all_users[start_idx:end_idx]

    lines = [f"👥 <b>𝐔𝐒𝐄𝐑𝐒 𝐕𝐀𝐋𝐈𝐃𝐈𝐓𝐘 𝐋𝐈𝐒𝐓 ({filter_type.upper()}):</b>\n"]
    for idx, data in enumerate(page_items, start=start_idx + 1):
        uid = data["user_id"]
        name = data.get("name", "Unknown")
        expiry_val = data.get("expiry")
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val else "None"
        
        lines.append(
            f"<blockquote>{idx}. <b>{name}</b> (<code>{uid}</code>)\n"
            f"   Expiry: <b>{expiry_str}</b></blockquote>"
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
    if end_idx < len(all_users):
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
                unjoined.append({"name": chat_info.title or f"Channel {ch_id}", "link": invite_link})
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
    text = update.message.text.strip()
    bot_settings = get_settings()

    if user_id in bot_settings["admins"] or user_id in ADMIN_IDS:
        if user_id in PENDING_ADMIN_ACTIONS:
            state = PENDING_ADMIN_ACTIONS[user_id]

            if state == "awaiting_channel_details":
                try:
                    lines = text.split("\n")
                    ch_id, c_type, c_category_raw, more_info = None, None, "", ""
                    for line in lines:
                        if line.lower().startswith("channel id:"):
                            ch_id = int(line.split(":", 1)[1].strip())
                        elif line.lower().startswith("type:"):
                            c_type = line.split(":", 1)[1].strip().lower()
                        elif line.lower().startswith("category:"):
                            c_category_raw = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("more info:"):
                            more_info = line.split(":", 1)[1].strip()

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
                            "⚠️ <b>Bot is not an admin in this channel!</b>\nPlease promote the bot to an administrator in the channel first, then try adding it again.",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(add_bot_btn)
                        )
                        return

                    chat_info = await context.bot.get_chat(ch_id)
                    ch_name = chat_info.title or f"Channel {ch_id}"
                    start_token = generate_dps_token()
                    
                    try:
                        invite = await context.bot.create_chat_invite_link(chat_id=ch_id, name=f"Channel Link {ch_name}")
                        start_link = invite.invite_link
                    except Exception:
                        start_link = chat_info.invite_link or f"https://t.me/{BOT_USERNAME}?start={start_token}"

                    channels_collection.insert_one({
                        "id": ch_id,
                        "name": ch_name,
                        "categories": categories_list,
                        "category": categories_list[0],
                        "type": c_type,
                        "link": start_link,
                        "token": start_token,
                        "more_info": more_info
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text(f"✅ Channel <b>{ch_name}</b> added successfully with multiple categories indexing!", parse_mode="HTML")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error parsing format: {e}")
                    return

            elif state == "awaiting_link_details":
                try:
                    lines = text.split("\n")
                    ch_name, c_type, c_category_raw, more_info, dist_link = "", "free", "", "", ""
                    for line in lines:
                        if line.lower().startswith("channel name:"):
                            ch_name = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("types:"):
                            c_type = line.split(":", 1)[1].strip().lower()
                        elif line.lower().startswith("category:"):
                            c_category_raw = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("more:"):
                            more_info = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("link:"):
                            dist_link = line.split(":", 1)[1].strip()

                    categories_list = [c.strip() for c in c_category_raw.split(",") if c.strip()]
                    if not categories_list:
                        categories_list = ["General"]

                    token = generate_dps_token()
                    channels_collection.insert_one({
                        "id": -999999,
                        "name": ch_name,
                        "categories": categories_list,
                        "category": categories_list[0],
                        "type": c_type,
                        "link": dist_link,
                        "token": token,
                        "more_info": more_info
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text(f"✅ Distribution Link for <b>{ch_name}</b> added successfully with random start token and multi-categories!", parse_mode="HTML")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error saving link: {e}")
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
                        k_l = k.strip().lower()
                        v_s = v.strip()
                        if "name" in k_l:
                            updated_fields["name"] = v_s
                        elif "category" in k_l:
                            cat_list = [c.strip() for c in v_s.split(",") if c.strip()]
                            updated_fields["categories"] = cat_list
                            updated_fields["category"] = cat_list[0] if cat_list else "General"
                        elif "type" in k_l:
                            updated_fields["type"] = v_s.lower()
                        elif "link" in k_l:
                            updated_fields["link"] = v_s
                        elif "more info" in k_l:
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
                        k_l = k.strip().lower()
                        v_s = v.strip()
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
                    new_more_link = bot_settings["more_channel_link"]
                    new_video_link = bot_settings.get("video_tutorial_link", DEFAULT_VIDEO_TUTORIAL_URL)
                    new_start_url = bot_settings.get("start_media_url", "")
                    new_qr_url = bot_settings.get("qr_image_url", "")
                    new_verify_url = bot_settings.get("verify_banner_url", "")
                    new_about_msg = bot_settings.get("about_message", "")
                    new_prices = bot_settings["prices"].copy()

                    current_key = None
                    current_val_lines = []

                    for line in lines:
                        if ":" in line and any(line.lower().startswith(p) for p in ["more channel link", "video tutorial link", "start media url", "qr/pay image url", "qr image url", "verify banner url", "about message", "1 month price", "2 month price", "3 month price"]):
                            if current_key:
                                val_s = "\n".join(current_val_lines).strip()
                                if current_key == "more channel link": new_more_link = val_s
                                elif current_key == "video tutorial link": new_video_link = val_s
                                elif current_key == "start media url": new_start_url = val_s
                                elif current_key in ["qr/pay image url", "qr image url"]: new_qr_url = val_s
                                elif current_key == "verify banner url": new_verify_url = val_s
                                elif current_key == "about message": new_about_msg = val_s
                                elif current_key == "1 month price": new_prices["1"] = val_s
                                elif current_key == "2 month price": new_prices["2"] = val_s
                                elif current_key == "3 month price": new_prices["3"] = val_s
                            
                            parts = line.split(":", 1)
                            current_key = parts[0].strip().lower()
                            current_val_lines = [parts[1].strip()]
                        else:
                            current_val_lines.append(line)

                    if current_key:
                        val_s = "\n".join(current_val_lines).strip()
                        if current_key == "more channel link": new_more_link = val_s
                        elif current_key == "video tutorial link": new_video_link = val_s
                        elif current_key == "start media url": new_start_url = val_s
                        elif current_key in ["qr/pay image url", "qr image url"]: new_qr_url = val_s
                        elif current_key == "verify banner url": new_verify_url = val_s
                        elif current_key == "about message": new_about_msg = val_s
                        elif current_key == "1 month price": new_prices["1"] = val_s
                        elif current_key == "2 month price": new_prices["2"] = val_s
                        elif current_key == "3 month price": new_prices["3"] = val_s

                    update_settings({
                        "more_channel_link": new_more_link,
                        "video_tutorial_link": new_video_link,
                        "start_media_url": new_start_url,
                        "qr_image_url": new_qr_url,
                        "verify_banner_url": new_verify_url,
                        "about_message": new_about_msg,
                        "prices": new_prices
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text("✅ Settings and About message updated successfully!")
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
            {"$set": {"name": user.full_name or "User"}, "$setOnInsert": {"expiry": datetime.now(), "joined_channels": []}},
            upsert=True
        )

    await handle_search_message_logic(update, context)


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    bot_settings = get_settings()
    p1 = bot_settings["prices"]["1"]
    p2 = bot_settings["prices"]["2"]
    p3 = bot_settings["prices"]["3"]
    qr_url = bot_settings.get("qr_image_url", "https://files.catbox.moe/68r9do.jpg")

    plan_text = (
        "💎 <b>𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐒𝐔𝐁𝐒𝐂𝐑𝐈𝐏𝐓𝐈𝐎𝐍 𝐏𝐋𝐀𝐍𝐒</b>\n\n"
        "<i>Unlock exclusive access, skip all verifications, and get instant links to all premium content instantly!</i>\n\n"
        "<blockquote>"
        f"• <b>1 Month:</b> ₹{p1} INR\n"
        f"• <b>2 Months:</b> ₹{p2} INR\n"
        f"• <b>3 Months:</b> ₹{p3} INR\n\n"
        "💳 <b>UPI ID:</b> <code>padhand171@okicici</code>\n"
        "📌 <i>Scan QR code to pay instantly via any UPI app.</i>"
        "</blockquote>"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url="https://rb.gy/81kgkx")],
        [InlineKeyboardButton("📤 Send Screenshot", url="https://t.me/Digital_adminbot")]
    ]
    await update.message.reply_photo(photo=qr_url, caption=plan_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


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
            "\n\n⚙️ <b>𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
            "<blockquote>"
            "• /add_channel - Add Telegram channel\n"
            "• /add_link - Add distribution link\n"
            "• /list_channel - Manage channels\n"
            "• /settings - Bot configuration (Images & Prices)\n"
            "• /backup - Database backup\n"
            "• /broadcast - Broadcast message\n"
            "• /add_user - Grant user validity\n"
            "• /list_user - List users with filters\n"
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
    
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"name": user.full_name or "User"}, "$setOnInsert": {"expiry": datetime.now(), "joined_channels": []}},
        upsert=True
    )

    unjoined_channels = await check_force_subscribe(user_id, context)
    if unjoined_channels:
        fs_text = "⚠️ <b>Please join our mandatory channels below:</b>\n\n"
        fs_keyboard = []
        for ch in unjoined_channels:
            fs_keyboard.append([InlineKeyboardButton(f"📢 Join {ch['name']}", url=ch['link'])])
        fs_keyboard.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_fs_complete")])
        await update.message.reply_text(fs_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(fs_keyboard))
        return

    args = context.args
    bot_settings = get_settings()
    if args:
        token = args[0]
        matched_item = channels_collection.find_one({"token": token})
        if matched_item:
            channel_id = matched_item["id"]
            c_type = matched_item["type"]

            user_record = users_collection.find_one({"user_id": user_id})
            is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS
            now = datetime.now()
            is_user_premium = is_admin or (user_record and user_record.get("expiry") and user_record["expiry"] > now)

            if c_type == "premium" and not is_user_premium:
                await update.message.reply_text("🔒 <b>Access Denied:</b> Premium subscription required.", parse_mode="HTML")
                return

            if c_type in ["free", "verify"] and not is_user_premium:
                if not VERIFICATION_STATE.get((user_id, token), False):
                    destination_url = f"https://t.me/{BOT_USERNAME}?start={token}"
                    shortened_link = destination_url
                    try:
                        api_url = f"https://arolinks.com/api?api={AROLINKS_API_TOKEN}&url={destination_url}"
                        response = requests.get(api_url, timeout=5)
                        data = response.json()
                        if data.get("status") == "success" or "shortenedUrl" in data:
                            shortened_link = data.get("shortenedUrl", data.get("url", destination_url))
                    except Exception as e:
                        logger.error(f"Arolinks API request failed: {e}")

                    VERIFICATION_STATE[(user_id, token)] = False
                    
                    verify_text = (
                        "🛡️ <b>𝚅𝚎𝚛𝚒𝚏𝚒𝚌𝚊𝚝𝚒𝚘𝚗 𝚁𝚎𝚚𝚞𝚒𝚛𝚎𝚍</b>\n\n"
                        "<blockquote>Please complete the shortener verification link below to unlock access, or buy premium to bypass verification completely!\n\n"
                        "💎 <i>Buy Premium via /plan to skip verification.</i></blockquote>"
                    )
                    
                    tutorial_link = bot_settings.get("video_tutorial_link", DEFAULT_VIDEO_TUTORIAL_URL)
                    keyboard = [
                        [InlineKeyboardButton("🔗 Complete Verification", url=shortened_link)],
                        [InlineKeyboardButton("📺 Watch Video Tutorial", url=tutorial_link)],
                        [InlineKeyboardButton("✅ I Have Verified", callback_data=f"verify_check_{token}")]
                    ]
                    
                    verify_banner = bot_settings.get("verify_banner_url", "https://files.catbox.moe/rr3cn8.jpg")
                    await update.message.reply_photo(
                        photo=verify_banner,
                        caption=verify_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

            target_invite_link = matched_item["link"]
            
            users_collection.update_one(
                {"user_id": user_id},
                {"$addToSet": {"joined_channels": matched_item["name"]}}
            )

            message_text = (
                "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
                "<blockquote>"
                f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
                f"<b>𝚃𝚢𝚙𝚎:</b> {c_type.capitalize()}\n"
                f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {', '.join(matched_item.get('categories', [matched_item.get('category', 'General')]))}"
                "</blockquote>"
            )
            keyboard = [[InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]]
            await update.message.reply_text(message_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            return

    start_url = bot_settings.get("start_media_url", "https://files.catbox.moe/aqak0m.jpg")
    await update.message.reply_photo(photo=start_url, caption=tr(user_id, "welcome"), parse_mode="HTML")


async def chat_join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: ChatJoinRequest = update.chat_join_request
    if not query:
        return
    try:
        await context.bot.approve_chat_join_request(chat_id=query.chat.id, user_id=query.from_user.id)
    except Exception as e:
        logger.error(f"Failed to approve request: {e}")


async def handle_search_message_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    all_channels = list(channels_collection.find({}))
    norm_query = normalize_text(query)

    found_items = []
    for ch in all_channels:
        cats = [normalize_text(c) for c in ch.get("categories", [ch.get("category", "")])]
        if norm_query in normalize_text(ch["name"]) or any(norm_query in c for c in cats):
            found_items.append(ch)

    suggestions = []
    if not found_items:
        channel_names = [ch["name"] for ch in all_channels]
        fuzzy_results = process.extract(norm_query, [normalize_text(n) for n in channel_names], limit=15, scorer=fuzz.token_sort_ratio)
        matched_indices = {res[2] for res in fuzzy_results if res[1] >= 40}
        
        found_items = [all_channels[i] for i in matched_indices]
        if not found_items:
            suggestions = [channel_names[res[2]] for res in fuzzy_results[:3] if res[1] >= 30]

    context.user_data["search_query"] = query
    context.user_data["found_items"] = found_items
    context.user_data["search_suggestions"] = suggestions
    context.user_data["current_page"] = 0
    context.user_data["active_type_filter"] = None
    context.user_data["active_cat_filter"] = None
    context.user_data["category_browse_mode"] = False

    await send_search_results(update, context, edit_message=False)


async def send_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    query = context.user_data.get("search_query", "")
    all_channels = list(channels_collection.find({}))
    total_items = len(all_channels)
    
    category_browse_mode = context.user_data.get("category_browse_mode", False)
    cat_filter = context.user_data.get("active_cat_filter")
    type_filter = context.user_data.get("active_type_filter")

    if category_browse_mode and cat_filter:
        filtered_items = []
        for ch in all_channels:
            cats = [normalize_text(c) for c in ch.get("categories", [ch.get("category", "")])]
            if normalize_text(cat_filter) in cats:
                filtered_items.append(ch)
        found_count = len(filtered_items)
    else:
        found_items = context.user_data.get("found_items", all_channels)
        found_count = len(found_items)
        filtered_items = found_items
        if type_filter:
            filtered_items = [item for item in filtered_items if item.get("type", "").lower() == type_filter.lower()]
        if cat_filter:
            filtered_items = [item for item in filtered_items if normalize_text(cat_filter) in [normalize_text(c) for c in item.get("categories", [item.get("category", "")])]]

    page = context.user_data.get("current_page", 0)
    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 10)
    max_pages = (len(filtered_items) - 1) // ITEMS_PER_PAGE if filtered_items else 0
    page = max(0, min(page, max_pages))
    context.user_data["current_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_page_items = filtered_items[start_idx:end_idx]

    text_lines = [
        "📊 <b>𝐒𝐄𝐀𝐑𝐂𝐇 & 𝐁𝐑𝐎𝐖𝐒𝐄 𝐀𝐍𝐀𝐋𝐘𝐓𝐈𝐂𝐒</b>" if not category_browse_mode else "📁 <b>𝐂𝐀𝐓𝐄𝐆𝐎𝐑𝐘 𝐁𝐑𝐎𝐖𝐒𝐄𝐑</b>",
        "──────────────────────────",
        "<blockquote>"
        f"▪ 𝐓𝐨𝐭𝐚𝐥 𝐈𝐭𝐞𝐦𝐬         : {total_items:,}\n"
        f"▪ 𝐌𝐚𝐭𝐜𝐡𝐞𝐬 𝐅𝐨𝐮𝐧𝐝  : {found_count:,}\n"
        f"▪ 𝐐𝐮𝐞𝐫𝐲/𝐂𝐚𝐭        : &quot;{cat_filter if category_browse_mode and cat_filter else query}&quot;\n"
        f"▪ 𝐍𝐚𝐯𝐢𝐠𝐚𝐭𝐢𝐨𝐧          : 𝐏𝐚𝐠𝐞 {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}"
        "</blockquote>",
        "──────────────────────────",
        "📌 <b>𝐑𝐄𝐂𝐎𝐑𝐃𝐒:</b>",
    ]

    suggestions = context.user_data.get("search_suggestions", [])

    if not current_page_items:
        text_lines.append("<i>No items found matching your filter criteria.</i>")
        if suggestions and not category_browse_mode:
            text_lines.append("\n💡 <b>Did you mean:</b>")
            for sug in suggestions:
                text_lines.append(f"• <code>{sug}</code>")
    else:
        for idx, item in enumerate(current_page_items, start=start_idx + 1):
            token = item.get("token")
            access_link = f"https://t.me/{BOT_USERNAME}?start={token}" if token else item.get("link", "#")
            more_info_val = item.get("more_info", "")
            
            if more_info_val and more_info_val.isdigit():
                base_more = bot_settings.get("more_channel_link", "https://t.me/")
                clean_base = base_more.rstrip("/")
                more_link = f"{clean_base}/{more_info_val}"
            elif more_info_val and more_info_val.startswith("http"):
                more_link = more_info_val
            else:
                more_link = bot_settings.get("more_channel_link", "https://t.me/")

            cats_display = ", ".join(item.get("categories", [item.get("category", "General")]))
            text_lines.append(
                f"<blockquote>{idx}. <a href=\"{access_link}\">{item['name']}</a> [{item['type'].upper()}] - ({cats_display})\n"
                f"   /n<a href=\"{more_link}\">More Info</a></blockquote>"
            )

    response_text = "\n".join(text_lines)

    keyboard = [
        [
            InlineKeyboardButton("ⓕ Free", callback_data="filter_type_free"),
            InlineKeyboardButton("ⓥ Verify", callback_data="filter_type_verify"),
            InlineKeyboardButton("ⓟ Premium", callback_data="filter_type_premium"),
        ],
        [
            InlineKeyboardButton("🗂️ Category", callback_data="prompt_category_filter"),
            InlineKeyboardButton("🔁 Filters", callback_data="filter_reset")
        ]
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("« 𝐵𝑎𝑐𝑘", callback_data="prev_page"))
    if end_idx < len(filtered_items):
        nav_row.append(InlineKeyboardButton("𝑀𝑜𝑟𝑒 »", callback_data="next_page"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit_message:
        await update.callback_query.edit_message_text(text=response_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await update.message.reply_text(text=response_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    bot_settings = get_settings()

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
            f"Type: {ch_rec.get('type', 'free')}\n"
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

        expiry_val = usr_rec.get("expiry")
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val else "None"

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
            f"More channel link: {bot_settings['more_channel_link']}\n"
            f"Video tutorial link: {bot_settings.get('video_tutorial_link', DEFAULT_VIDEO_TUTORIAL_URL)}\n"
            f"Start Media URL: {bot_settings.get('start_media_url', '')}\n"
            f"QR/Pay Image URL: {bot_settings.get('qr_image_url', '')}\n"
            f"Verify Banner URL: {bot_settings.get('verify_banner_url', '')}\n"
            f"About Message: {bot_settings.get('about_message', '')}\n"
            f"1 month price: {bot_settings['prices']['1']}\n"
            f"2 month price: {bot_settings['prices']['2']}\n"
            f"3 month price: {bot_settings['prices']['3']}"
        )
        await query.message.reply_text("✏️ Edit settings and send back:\n\n" + f"<blockquote><code>{edit_template}</code></blockquote>", parse_mode="HTML")
        return

    if data.startswith("verify_check_"):
        token = data.replace("verify_check_", "")
        VERIFICATION_STATE[(user_id, token)] = True
        
        matched_item = channels_collection.find_one({"token": token})
        if not matched_item:
            await query.message.reply_text("❌ Item session expired. Please send /start again.")
            return

        channel_id = matched_item["id"]
        target_invite_link = matched_item["link"]
        try:
            invite = await context.bot.create_chat_invite_link(chat_id=channel_id, name=f"Verified User {user_id}")
            target_invite_link = invite.invite_link
        except Exception as e:
            logger.error(f"Failed to generate invite link after verification: {e}")

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

    if data.startswith("filter_type_"):
        context.user_data["active_type_filter"] = data.replace("filter_type_", "")
        context.user_data["current_page"] = 0
        await send_search_results(update, context, edit_message=True)
        return

    if data == "prompt_category_filter":
        all_channels = list(channels_collection.find({}))
        extracted_cats = set()
        
        category_browse_mode = context.user_data.get("category_browse_mode", False)
        
        target_source = all_channels
        if not category_browse_mode and context.user_data.get("found_items"):
            target_source = context.user_data.get("found_items")

        for ch in target_source:
            cats = ch.get("categories", [ch.get("category", "General")])
            for c in cats:
                if c:
                    extracted_cats.add(c.strip())
        categories = sorted(list(extracted_cats))
        
        cat_keyboard = [[InlineKeyboardButton("📁 All Categories", callback_data="set_cat_all")]]
        for cat in categories:
            cat_keyboard.append([InlineKeyboardButton(f"📁 {cat}", callback_data=f"set_cat_{cat}")])
        cat_keyboard.append([InlineKeyboardButton("🔙 Back to Results", callback_data="back_to_search_results")])
        
        await query.edit_message_text("📂 <b>Select a category to filter/browse by:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(cat_keyboard))
        return

    if data.startswith("set_cat_"):
        cat_val = data.replace("set_cat_", "")
        if cat_val == "all":
            context.user_data["active_cat_filter"] = None
            context.user_data["category_browse_mode"] = False
        else:
            context.user_data["active_cat_filter"] = cat_val
        context.user_data["current_page"] = 0
        await send_search_results(update, context, edit_message=True)
        return

    if data == "back_to_search_results":
        context.user_data["category_browse_mode"] = False
        await send_search_results(update, context, edit_message=True)
        return

    if data == "filter_reset":
        # Scoped exclusively to current search items list (no database reset)
        if "found_items" in context.user_data:
            context.user_data["found_items"] = context.user_data.get("found_items", [])
        context.user_data["current_page"] = 0
        await send_search_results(update, context, edit_message=True)
        return

    if data.startswith("usr_filter_"):
        context.user_data["user_filter_type"] = data.replace("usr_filter_", "")
        context.user_data["user_list_page"] = 0
        await render_user_list_page(update, context, edit_message=True)
        return

    if data == "next_page":
        context.user_data["current_page"] += 1
        await send_search_results(update, context, edit_message=True)
    elif data == "prev_page":
        context.user_data["current_page"] -= 1
        await send_search_results(update, context, edit_message=True)
    elif data == "ch_page_next":
        context.user_data["channel_list_page"] += 1
        await render_channel_list_page(update, context, edit_message=True)
    elif data == "ch_page_prev":
        context.user_data["channel_list_page"] -= 1
        await render_channel_list_page(update, context, edit_message=True)
    elif data == "usr_page_next":
        context.user_data["user_list_page"] += 1
        await render_user_list_page(update, context, edit_message=True)
    elif data == "usr_page_prev":
        context.user_data["user_list_page"] -= 1
        await render_user_list_page(update, context, edit_message=True)
    elif data == "ch_search_prompt":
        PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_ch_search_query"
        await query.message.reply_text("🔍 Send channel keyword filter:")
    elif data == "usr_search_prompt":
        PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_usr_search_query"
        await query.message.reply_text("🔍 Send user ID/Name filter:")


web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is alive and running!", 200

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return

    server_thread = Thread(target=run_web_server, daemon=True)
    server_thread.start()
    logger.info("Render health check web server started.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_error_handler(global_error_handler)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_channel", add_channel_command))
    app.add_handler(CommandHandler("add_link", add_link_command))
    app.add_handler(CommandHandler("list_channel", list_channel_command))
    app.add_handler(CommandHandler("settings", settings_command))
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))

    print("Bot is running with full features and Render health port active...")
    app.run_polling()


if __name__ == "__main__":
    main()
