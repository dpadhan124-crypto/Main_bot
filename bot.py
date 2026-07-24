import os
import logging
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
        "force_subscribe_ids": [],
        "prices": {
            "1": "49",
            "2": "95",
            "3": "140"
        },
        "start_media_url": "https://ibb.co/ynTDh3tn",
        "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
        "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
        "maintenance_mode": False,
        "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
        "items_per_page": 10
    })

PENDING_ADMIN_ACTIONS = {}  
VERIFICATION_STATE = {}   
RATE_LIMIT_CACHE = {}     


def get_settings():
    """Fetches bot settings from MongoDB."""
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "more_channel_link": "https://t.me/your_more_channel/",
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "start_media_url": "https://ibb.co/ynTDh3tn",
            "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
            "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
            "maintenance_mode": False,
            "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
            "items_per_page": 10
        }
    return s


def update_settings(new_fields: dict):
    """Updates bot settings in MongoDB."""
    settings_collection.update_one({"_id": "bot_settings"}, {"$set": new_fields})


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing and converting Unicode variants to standard tokens."""
    if not text:
        return ""
    return text.strip().lower()


LOCALIZATION_STRINGS = {
    "en": {
        "welcome": "👋 Welcome!\nSend me any keyword or phrase to search our database.",
        "maintenance": "🛠️ Bot is currently under maintenance. Please check back later!",
        "unauthorized": "⛔ You are not authorized to use this command."
    },
    "hi": {
        "welcome": "👋 स्वागत है!\nहमारे डेटाबेस में खोजने के लिए कोई भी कीवर्ड या वाक्यांश भेजें।",
        "maintenance": "🛠️ बॉट वर्तमान में रखरखाव के अधीन है। कृपया बाद में जाँच करें!",
        "unauthorized": "⛔ आप इस कमांड का उपयोग करने के लिए अधिकृत नहीं हैं।"
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
        error_msg = f"🚨 <b>Bot Error Alert</b>:\n<pre>{str(context.error)}</pre>"
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

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n• Success: {success_count}\n• Blocked/Failed: {blocked_count}")


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
    if "editing_serial" in context.user_data:
        del context.user_data["editing_serial"]
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
        "Category:\n"
        "More info:"
    )
    await update.message.reply_text("📥 <b>Send channel details template:</b>\n\n" + f"<code>{template}</code>", parse_mode="HTML")


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
        "Category:\n"
        "More:\n"
        "Link:"
    )
    await update.message.reply_text("🔗 <b>Send distribution link template:</b>\n\n" + f"<code>{template}</code>", parse_mode="HTML")


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
        f"<b>Admins:</b> {admins_str}\n"
        f"<b>More channel link:</b> {bot_settings['more_channel_link']}\n"
        f"<b>Force subscribe ids:</b> {fs_ids}\n"
        f"<b>Prices:</b> {bot_settings['prices']}\n"
        f"<b>Start Media URL:</b> {bot_settings.get('start_media_url', 'Default')}\n"
        f"<b>QR/Pay Image URL:</b> {bot_settings.get('qr_image_url', 'Default')}\n"
        f"<b>Verify Banner URL:</b> {bot_settings.get('verify_banner_url', 'Default')}\n"
        f"<b>Maintenance Mode:</b> {bot_settings.get('maintenance_mode', False)}"
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
        f"• <b>Name:</b> {name}\n"
        f"• <b>ID:</b> <code>{user_id}</code>\n"
        f"• <b>Status:</b> {status_str}\n"
        f"• <b>Expiry:</b> {expiry_str}\n"
        f"• <b>Joined Channels:</b> <blockquote>{joined_text}</blockquote>"
    )
    await update.message.reply_text(stats_text, parse_mode="HTML")


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
        more_link_base = bot_settings["more_channel_link"]
        if not more_link_base.endswith("/"):
            more_link_base += "/"
        more_info_val = ch.get("more_info", "")
        more_info_hyperlink = f"{more_link_base}{more_info_val}" if more_info_val else more_link_base
        dist_link = ch.get("link", "#")

        lines.append(
            f"{idx}. <b>{ch['name']}</b>\n"
            f"<blockquote>   Type: {ch['type']} \n"
            f"Cat: {ch['category']} \n"
            f"Link: <a href='{dist_link}'>Open Link</a> \n"
            f"More Info: <a href='{more_info_hyperlink}'>Link</a></blockquote>"
        )
    
    if not page_items:
        lines.append("<i>No channels match your search filter.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    lines.append("<i>Send a serial number to edit/delete channel details.</i>")

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="ch_page_prev"))
    nav_row.append(InlineKeyboardButton("🔍 Search Channel", callback_data="ch_search_prompt"))
    if end_idx < len(items):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="ch_page_next"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    text_content = "\n".join(lines)

    PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_serial_select"

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
    await render_user_list_page(update, context, edit_message=False)


async def render_user_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    page = context.user_data.get("user_list_page", 0)
    search_filter = context.user_data.get("user_list_search", "")

    all_users = list(users_collection.find({}).sort("expiry", 1))
    if search_filter:
        sf = normalize_text(search_filter)
        all_users = [u for u in all_users if sf in str(u["user_id"]) or sf in normalize_text(u.get("name", ""))]

    context.user_data["current_rendered_users"] = all_users

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 3)
    max_pages = (len(all_users) - 1) // ITEMS_PER_PAGE if all_users else 0
    page = max(0, min(page, max_pages))
    context.user_data["user_list_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = all_users[start_idx:end_idx]

    lines = ["👥 <b>𝐔𝐒𝐄𝐑𝐒 𝐕𝐀𝐋𝐈𝐃𝐈𝐓𝐘 𝐋𝐈𝐒𝐓:</b>\n"]
    for idx, data in enumerate(page_items, start=start_idx + 1):
        uid = data["user_id"]
        name = data.get("name", "Unknown")
        expiry_val = data.get("expiry")
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val else "None"
        joined_channels = data.get("joined_channels", [])
        channel_count = len(joined_channels)
        
        lines.append(
            f"{idx}. <b>{name}</b> (<code>{uid}</code>)\n"
            f"   Expiry: <b>{expiry_str}</b>\n"
            f"   Channels Joined: <b>{channel_count}</b>\n"
        )

    if not page_items:
        lines.append("<i>No users found matching query.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    response_text = "\n".join(lines)

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="usr_page_prev"))
    nav_row.append(InlineKeyboardButton("🔍 Search User", callback_data="usr_search_prompt"))
    if end_idx < len(all_users):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="usr_page_next"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

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
                    ch_id, c_type, c_category, more_info = None, None, None, ""
                    for line in lines:
                        if line.lower().startswith("channel id:"):
                            ch_id = int(line.split(":", 1)[1].strip())
                        elif line.lower().startswith("type:"):
                            c_type = line.split(":", 1)[1].strip().lower()
                        elif line.lower().startswith("category:"):
                            c_category = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("more info:"):
                            more_info = line.split(":", 1)[1].strip()

                    chat_info = await context.bot.get_chat(ch_id)
                    ch_name = chat_info.title or f"Channel {ch_id}"
                    start_token = f"ch_{ch_id}"
                    start_link = f"https://t.me/{BOT_USERNAME}?start={start_token}"

                    channels_collection.insert_one({
                        "id": ch_id,
                        "name": ch_name,
                        "category": c_category,
                        "type": c_type,
                        "link": start_link,
                        "token": start_token,
                        "more_info": more_info
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text(f"✅ Channel <b>{ch_name}</b> added successfully!", parse_mode="HTML")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error parsing format: {e}")
                    return

            elif state == "awaiting_link_details":
                try:
                    lines = text.split("\n")
                    ch_name, c_type, c_category, more_info, dist_link = "", "free", "", "", ""
                    for line in lines:
                        if line.lower().startswith("channel name:"):
                            ch_name = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("types:"):
                            c_type = line.split(":", 1)[1].strip().lower()
                        elif line.lower().startswith("category:"):
                            c_category = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("more:"):
                            more_info = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("link:"):
                            dist_link = line.split(":", 1)[1].strip()

                    token = f"lnk_{abs(hash(ch_name))}"
                    channels_collection.insert_one({
                        "id": -999999,
                        "name": ch_name,
                        "category": c_category,
                        "type": c_type,
                        "link": dist_link,
                        "token": token,
                        "more_info": more_info
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text(f"✅ Distribution Link for <b>{ch_name}</b> added successfully!", parse_mode="HTML")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error saving link: {e}")
                    return

            elif state == "awaiting_serial_select":
                try:
                    serial = int(text)
                    rendered_items = context.user_data.get("current_rendered_channels", [])
                    if 1 <= serial <= len(rendered_items):
                        ch = rendered_items[serial - 1]
                        context.user_data["editing_serial"] = ch.get("id")
                        
                        keyboard = [
                            [InlineKeyboardButton("🗑️ Delete Channel", callback_data=f"confirm_del_ch_{ch.get('id')}")],
                            [InlineKeyboardButton("Cancel", callback_data="confirm_no")]
                        ]
                        await update.message.reply_text(f"⚠️ Selected Channel: <b>{ch['name']}</b>. Choose action:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
                    else:
                        await update.message.reply_text("❌ Invalid serial number range.")
                except ValueError:
                    await update.message.reply_text("❌ Please send a valid numeric serial index.")
                return

            elif state == "awaiting_settings_edit":
                try:
                    lines = text.split("\n")
                    new_more_link = bot_settings["more_channel_link"]
                    new_start_url = bot_settings.get("start_media_url", "")
                    new_qr_url = bot_settings.get("qr_image_url", "")
                    new_verify_url = bot_settings.get("verify_banner_url", "")
                    new_prices = bot_settings["prices"].copy()

                    for line in lines:
                        if ":" not in line:
                            continue
                        key, val = line.split(":", 1)
                        key_l = key.strip().lower()
                        val_s = val.strip()

                        if "more channel link" in key_l:
                            new_more_link = val_s
                        elif "start media url" in key_l:
                            new_start_url = val_s
                        elif "qr/pay image url" in key_l or "qr image url" in key_l:
                            new_qr_url = val_s
                        elif "verify banner url" in key_l:
                            new_verify_url = val_s
                        elif "1 month price" in key_l:
                            new_prices["1"] = val_s
                        elif "2 month price" in key_l:
                            new_prices["2"] = val_s
                        elif "3 month price" in key_l:
                            new_prices["3"] = val_s

                    update_settings({
                        "more_channel_link": new_more_link,
                        "start_media_url": new_start_url,
                        "qr_image_url": new_qr_url,
                        "verify_banner_url": new_verify_url,
                        "prices": new_prices
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text("✅ Settings and image URLs updated successfully!")
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

    # Track user info on message interaction
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
    qr_url = bot_settings.get("qr_image_url", QR_IMAGE_URL)

    plan_text = (
        "💎 <b>𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐒𝐔𝐁𝐒𝐂𝐑𝐈𝐏𝐓𝐈𝐎𝐍 𝐏𝐋𝐀𝐍𝐒</b>\n\n"
        f"• <b>₹{p1} INR</b> for 1 Month\n"
        f"• <b>₹{p2} INR</b> for 2 Months\n"
        f"• <b>₹{p3} INR</b> for 3 Months\n\n"
        "<b>UPI ID:</b> <code>fshhs@hshs</code>"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url="https://rb.gy/81kgkx")],
        [InlineKeyboardButton("📤 Send Screenshot", url="https://t.me/idffajnbot")]
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
        "• /start - Start bot\n"
        "• /plan - View subscription plans\n"
        "• /stats - Account status\n"
        "• /language - Toggle language\n"
        "• /help - Help guide"
    )

    if is_admin:
        admin_help = (
            "\n\n⚙️ <b>𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
            "• /add_channel - Add Telegram channel\n"
            "• /add_link - Add distribution link\n"
            "• /list_channel - Manage channels\n"
            "• /settings - Bot configuration (Images & Prices)\n"
            "• /backup - Database backup\n"
            "• /broadcast - Broadcast message\n"
            "• /add_user - Grant user validity\n"
            "• /list_user - List users with channel metrics"
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

            target_invite_link = matched_item["link"]
            
            # Record joined channel
            users_collection.update_one(
                {"user_id": user_id},
                {"$addToSet": {"joined_channels": matched_item["name"]}}
            )

            message_text = (
                "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
                f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
                f"<b>𝚃𝚢𝚙𝚎:</b> {c_type.capitalize()}\n"
                f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {matched_item['category']}\n"
            )
            keyboard = [[InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]]
            await update.message.reply_text(message_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            return

    start_url = bot_settings.get("start_media_url", START_MEDIA_URL)
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
    channel_names = [ch["name"] for ch in all_channels]
    fuzzy_results = process.extract(query, channel_names, limit=15, scorer=fuzz.token_sort_ratio)
    matched_names = {res[0] for res in fuzzy_results if res[1] >= 50}
    found_items = [ch for ch in all_channels if ch["name"] in matched_names or normalize_text(query) in normalize_text(f"{ch['name']} {ch['category']} {ch['type']}")]

    context.user_data["search_query"] = query
    context.user_data["found_items"] = found_items
    context.user_data["current_page"] = 0

    await send_search_results(update, context, edit_message=False)


async def send_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    query = context.user_data.get("search_query", "")
    all_channels = list(channels_collection.find({}))
    found_items = context.user_data.get("found_items", all_channels)
    page = context.user_data.get("current_page", 0)

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 10)
    max_pages = (len(found_items) - 1) // ITEMS_PER_PAGE if found_items else 0
    page = max(0, min(page, max_pages))
    context.user_data["current_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_page_items = found_items[start_idx:end_idx]

    text_lines = [
        "📊 <b>𝐒𝐄𝐀𝐑𝐂𝐇 𝐑𝐄𝐒𝐔𝐋𝐓𝐒</b>",
        f"▪ 𝐐𝐮𝐞𝐫𝐲: &quot;{query}&quot;",
        f"▪ 𝐏𝐚𝐠𝐞: {page + 1} of {max_pages + 1}\n",
        "📌 <b>𝐌𝐀𝐓𝐂𝐇𝐈𝐍𝐆 𝐑𝐄𝐂𝐎𝐑𝐃𝐒:</b>"
    ]

    for idx, item in enumerate(current_page_items, start=start_idx + 1):
        dist_link = item.get("link", "#")
        text_lines.append(f"<b>{idx}.</b> <a href='{dist_link}'>{item['name']}</a> [{item['type'].upper()}]")

    response_text = "\n".join(text_lines)
    keyboard = [
        [
            InlineKeyboardButton("🟢 Free", callback_data="filter_type_free"),
            InlineKeyboardButton("🟡 Verify", callback_data="filter_type_verify"),
            InlineKeyboardButton("🟣 Premium", callback_data="filter_type_premium"),
        ]
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="prev_page"))
    if end_idx < len(found_items):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
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

    if data == "edit_settings_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            return
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_settings_edit"
        edit_template = (
            f"More channel link: {bot_settings['more_channel_link']}\n"
            f"Start Media URL: {bot_settings.get('start_media_url', '')}\n"
            f"QR/Pay Image URL: {bot_settings.get('qr_image_url', '')}\n"
            f"Verify Banner URL: {bot_settings.get('verify_banner_url', '')}\n"
            f"1 month price: {bot_settings['prices']['1']}\n"
            f"2 month price: {bot_settings['prices']['2']}\n"
            f"3 month price: {bot_settings['prices']['3']}"
        )
        await query.message.reply_text("✏️ Edit settings and send back:\n\n" + f"<code>{edit_template}</code>", parse_mode="HTML")
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
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_ch_search_query"
        await query.message.reply_text("🔍 Send channel keyword filter:")
    elif data == "usr_search_prompt":
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_usr_search_query"
        await query.message.reply_text("🔍 Send user ID/Name filter:")


# --- RENDER WEB PORT BINDING SERVER ---
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

    # Start Flask Web Server for Render Port Binding
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

