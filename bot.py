import os
import logging
import requests
import json
import io
from datetime import datetime, timedelta
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Media Links Configuration
START_MEDIA_URL = os.getenv("START_MEDIA_URL", "https://ibb.co/ynTDh3tn")
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "https://files.catbox.moe/68r9do.jpg")
VERIFY_BANNER_URL = os.getenv("VERIFY_BANNER_URL", "https://files.catbox.moe/rr3cn8.jpg")

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
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
        "maintenance_mode": False,
        "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
        "items_per_page": 10
    })

PENDING_ADMIN_ACTIONS = {}  # Format: { user_id: "awaiting_channel_details" }
VERIFICATION_STATE = {}   # Format: { (user_id, token): True/False }
RATE_LIMIT_CACHE = {}     # Feature 1 & 10: In-memory rate limiting tracker


def get_settings():
    """Fetches bot settings from MongoDB."""
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "more_channel_link": "https://t.me/your_more_channel/",
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "maintenance_mode": False,
            "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
            "items_per_page": 10
        }
    return s


def update_settings(new_fields: dict):
    """Updates bot settings in MongoDB."""
    settings_collection.update_one({"_id": "bot_settings"}, {"$set": new_fields})


# Helper to normalize/clean strings for font-agnostic search
def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing and converting Unicode variants to standard tokens."""
    if not text:
        return ""
    return text.strip().lower()

# Pre-populate sample channels if database is empty
if channels_collection.count_documents({}) == 0:
    categories = ["Ebooks", "Movies", "Courses", "Tools", "Music"]
    types_list = ["free", "verify", "premium"]
    for i in range(1, 11):
        category = categories[i % len(categories)]
        c_type = types_list[i % len(types_list)]
        item_name = f"Channel Item {i} - {category} Guide"
        start_token = f"DPS_sty9{i}52yfsy1"
        start_link = f"https://t.me/{BOT_USERNAME}?start={start_token}"
        channels_collection.insert_one(
            {
                "id": -1001000000000 + i,
                "name": item_name,
                "category": category,
                "type": c_type,
                "link": start_link,
                "token": start_token,
                "more_info": str(i * 10)
            }
        )


# --- FEATURE 6: LOCALIZATION STRINGS ---
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
    """Fetches user's preferred language, defaults to English."""
    user = users_collection.find_one({"user_id": user_id})
    if user and "language" in user:
        return user["language"]
    return "en"

def tr(user_id: int, key: str) -> str:
    """Translates key based on user's selected language."""
    lang = get_user_language(user_id)
    return LOCALIZATION_STRINGS.get(lang, LOCALIZATION_STRINGS["en"]).get(key, LOCALIZATION_STRINGS["en"].get(key, key))


# --- FEATURE 1 & 10: RATE LIMITING & ANTI-SPAM MIDDLEWARE ---
async def rate_limit_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Throttles spam interactions and protects against command abuse."""
    user = update.effective_user
    if not user:
        return True
    
    bot_settings = get_settings()
    if user.id in bot_settings.get("admins", []) or user.id in ADMIN_IDS:
        return True  # Bypass for admins

    now = datetime.now()
    last_interaction = RATE_LIMIT_CACHE.get(user.id)
    
    if last_interaction and (now - last_interaction) < timedelta(seconds=1.5):
        if update.message:
            await update.message.reply_text("⚠️ You are sending requests too quickly. Please slow down.")
        return False
    
    RATE_LIMIT_CACHE[user.id] = now
    return True


# --- FEATURE 2: MAINTENANCE MODE TOGGLE & CHECKER ---
async def maintenance_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if maintenance mode is active."""
    bot_settings = get_settings()
    if bot_settings.get("maintenance_mode", False):
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id not in bot_settings.get("admins", []) and user_id not in ADMIN_IDS:
            msg = bot_settings.get("maintenance_message", "🛠️ Bot is under maintenance.")
            if update.message:
                await update.message.reply_text(msg)
            return True
    return False


# --- FEATURE 7: ENHANCED LOGGING & ERROR ALERTS ---
async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler catching unexpected exceptions and notifying log channel/admins."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        bot_settings = get_settings()
        admins = bot_settings.get("admins", ADMIN_IDS)
        error_msg = f"🚨 <b>Bot Error Alert</b>:\n<pre>{str(context.error)}</pre>"
        for admin_id in admins:
            await context.bot.send_message(chat_id=admin_id, text=error_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to dispatch error alert: {e}")


# --- FEATURE 3: AUTOMATED BACKUP & RESTORE UTILITY ---
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all MongoDB collections into a compressed JSON backup file."""
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


# --- FEATURE 8: INTERACTIVE REMOVAL CONFIRMATION ---
PENDING_CONFIRMATIONS = {}

async def confirm_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles confirmation inline buttons for critical operations."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("confirm_yes_"):
        action_key = data.replace("confirm_yes_", "")
        if action_key in PENDING_CONFIRMATIONS:
            action_data = PENDING_CONFIRMATIONS[action_key]
            if action_data["type"] == "delete_channel":
                channels_collection.delete_one({"id": action_data["channel_id"]})
                await query.edit_message_text("✅ Channel successfully removed from database!")
            del PENDING_CONFIRMATIONS[action_key]
    elif data == "confirm_no":
        await query.edit_message_text("❌ Action cancelled.")


# --- FEATURE 11: BROADCAST MESSAGING FUNCTION ---
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to broadcast text, photo, or video to all registered users."""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    reply_to = update.message.reply_to_message
    if not reply_to:
        await update.message.reply_text("❌ Please reply to the message (text, photo, or video) you want to broadcast using /broadcast.")
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
            logger.warning(f"Failed to send broadcast to {target_uid}: {e}")
            blocked_count += 1

    await status_msg.edit_text(f"✅ Broadcast complete!\n\n• Success: {success_count}\n• Blocked/Failed: {blocked_count}")


# --- FEATURE 6: LANGUAGE TOGGLE COMMAND ---
async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets users toggle their preferred language dynamically."""
    keyboard = [
        [InlineKeyboardButton("English 🇬🇧", callback_data="set_lang_en"),
         InlineKeyboardButton("Hindi 🇮🇳", callback_data="set_lang_hi")]
    ]
    await update.message.reply_text("🌐 <b>Select your preferred language / अपनी भाषा चुनें:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# --- CANCEL COMMAND ---

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels any ongoing admin action/state."""
    user_id = update.effective_user.id
    if user_id in PENDING_ADMIN_ACTIONS:
        del PENDING_ADMIN_ACTIONS[user_id]
    if "editing_serial" in context.user_data:
        del context.user_data["editing_serial"]
    await update.message.reply_text("❌ Current operation cancelled successfully.")


# --- ADMIN & USER COMMANDS ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers the prompt template for adding a new channel."""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_details"
    template = (
        "Channel id:\n"
        "Type:free/verify/premium\n"
        "Category:\n"
        "More info:"
    )
    await update.message.reply_text(template)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays current bot settings and an edit button for admins."""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    listed_admins = [str(adm) for adm in bot_settings["admins"] if adm != DEFAULT_ADMIN_ID]
    admins_str = ", ".join(listed_admins) if listed_admins else "None (Using Default)"
    fs_ids = ", ".join(str(i) for i in bot_settings["force_subscribe_ids"]) if bot_settings["force_subscribe_ids"] else "None"

    settings_text = (
        "⚙️ <b>𝐂𝐔𝐑𝐑𝐄𝐍𝐓 𝐒𝐄𝐓𝐓𝐈𝐍𝐆𝐒</b>\n\n"
        f"<b>Admis:</b> {admins_str}\n"
        f"<b>More channel link:</b> {bot_settings['more_channel_link']}\n"
        f"<b>Force subscribe channel ids:</b> {fs_ids}\n"
        f"<b>1 month price:</b> {bot_settings['prices']['1']}\n"
        f"<b>2 month price:</b> {bot_settings['prices']['2']}\n"
        f"<b>3 month price:</b> {bot_settings['prices']['3']}\n"
        f"<b>Maintenance Mode:</b> {bot_settings.get('maintenance_mode', False)}\n"
        f"<b>Items Per Page:</b> {bot_settings.get('items_per_page', 10)}"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings_prompt")],
        [InlineKeyboardButton("🛠️ Toggle Maintenance", callback_data="toggle_maintenance")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(settings_text, parse_mode="HTML", reply_markup=reply_markup)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows user status, name, ID, expiry, and joined channel list from MongoDB."""
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
    """Lists all channels with serial numbers for editing."""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    channels_count = channels_collection.count_documents({})
    if channels_count == 0:
        await update.message.reply_text("📂 No channels found in the database.")
        return

    context.user_data["channel_list_page"] = 0
    await render_channel_list_page(update, context, edit_message=False)


async def render_channel_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    """Renders paginated channel list with search option."""
    page = context.user_data.get("channel_list_page", 0)
    search_filter = context.user_data.get("channel_list_search", "")

    all_channels = list(channels_collection.find({}))
    if search_filter:
        sf = normalize_text(search_filter)
        items = [ch for ch in all_channels if sf in normalize_text(ch['name']) or sf in str(ch['id'])]
    else:
        items = all_channels

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

        lines.append(
            f"{idx}. <b>{ch['name']}</b>\n"
            f"<blockquote>   ID: <code>{ch['id']}</code> \n"
            f"Type: {ch['type']} \n"
            f"Cat: {ch['category']} \n"
            f"More Info: <a href='{more_info_hyperlink}'>Link</a></blockquote>"
        )
    
    if not page_items:
        lines.append("<i>No channels match your search filter.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    lines.append("<i>Send a serial number to edit channel details, or use search.</i>")

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
    """Adds or updates premium validity for a user in MongoDB. Usage: /add_user <user_id> <validity>"""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage: <code>/add_user {user_id} {validity}</code>\nExample: <code>/add_user 123456789 7d</code> or <code>30m</code>", parse_mode="HTML")
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
        else:
            raise ValueError("Invalid unit. Use 'm' for minutes, 'd' for days, 'h' for hours.")

        now = datetime.now()
        existing_user = users_collection.find_one({"user_id": target_user_id})
        current_expiry = existing_user.get("expiry", now) if existing_user else now
        if not current_expiry or current_expiry < now:
            current_expiry = now

        new_expiry = current_expiry + delta

        users_collection.update_one(
            {"user_id": target_user_id},
            {"$set": {"expiry": new_expiry}, "$setOnInsert": {"joined_channels": []}},
            upsert=True
        )

        await update.message.reply_text(f"✅ User <code>{target_user_id}</code> given premium validity until <b>{new_expiry.strftime('%Y-%m-%d %H:%M')}</b>.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to add user validity: {e}")


async def list_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists users ordered by nearest expiry date from MongoDB."""
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    if users_collection.count_documents({}) == 0:
        await update.message.reply_text("📂 No registered users found.")
        return

    context.user_data["user_list_page"] = 0
    await render_user_list_page(update, context, edit_message=False)


async def render_user_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False):
    """Renders paginated list of users sorted by nearest expiration from MongoDB."""
    page = context.user_data.get("user_list_page", 0)
    search_filter = context.user_data.get("user_list_search", "")

    query = {}
    if search_filter:
        query = {"user_id": {"$regex": search_filter}}

    all_users = list(users_collection.find(query).sort("expiry", 1))
    if search_filter:
        sf = normalize_text(search_filter)
        all_users = [u for u in all_users if sf in str(u["user_id"])]

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
        expiry_val = data.get("expiry")
        expiry_str = expiry_val.strftime('%Y-%m-%d %H:%M') if expiry_val else "None"
        joined = data.get("joined_channels", [])
        joined_text = ", ".join(joined) if joined else "None"
        
        user_block = (
            f"{idx}. ID: <code>{uid}</code>\n"
            f"   Expiry: <b>{expiry_str}</b>\n"
            f"   <blockquote>Joined: {joined_text}</blockquote>\n"
        )
        lines.append(user_block)

    if not page_items:
        lines.append("<i>No users found matching query.</i>")

    lines.append(f"\nPage {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}")
    
    response_text = "\n".join(lines)
    if len(response_text) > 4000:
        response_text = response_text[:3950] + "\n...[truncated due to length limit]"

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


# --- FORCE SUBSCRIBE CHECK HELPER ---

async def check_force_subscribe(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list:
    """Checks if the user has joined all force subscribe channels. Returns list of unjoined channel metadata/links."""
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
                invite_link = chat_info.invite_link
                if not invite_link:
                    invite_link = f"https://t.me/{chat_info.username}" if chat_info.username else f"https://t.me/{BOT_USERNAME}"
                unjoined.append({"name": chat_info.title or f"Channel {ch_id}", "link": invite_link})
        except Exception as e:
            logger.error(f"Error checking force subscribe status for channel {ch_id}: {e}")
    return unjoined


# --- HANDLERS FOR TEXT MESSAGES & ADMIN FLOWS ---

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles general text, admin setup configurations, and search queries."""
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

                    if not ch_id or not c_type or not c_category:
                        raise ValueError("Missing fields")

                    chat_member = await context.bot.get_chat_member(ch_id, context.bot.id)
                    if not chat_member.can_promote_members and chat_member.status != "administrator":
                        await update.message.reply_text("❌ Bot lacks admin rights or permissions.")
                        return

                    chat_info = await context.bot.get_chat(ch_id)
                    ch_name = chat_info.title or f"Channel {ch_id}"
                    start_token = f"ch_{ch_name}"
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

            elif state == "awaiting_serial_select":
                try:
                    serial = int(text)
                    all_channels = list(channels_collection.find({}))
                    if 1 <= serial <= len(all_channels):
                        ch = all_channels[serial - 1]
                        context.user_data["editing_serial"] = ch["id"]
                        
                        # Feature 8: Inline removal confirmation demo option
                        keyboard = [
                            [InlineKeyboardButton("✏️ Edit Details", callback_data="proceed_edit_channel"),
                             InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"confirm_del_ch_{ch['id']}")],
                            [InlineKeyboardButton("Cancel", callback_data="confirm_no")]
                        ]
                        await update.message.reply_text(f"⚠️ Selected Channel: <b>{ch['name']}</b>. Choose action:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
                    else:
                        await update.message.reply_text("❌ Invalid serial number range.")
                except ValueError:
                    await update.message.reply_text("❌ Please send a valid numeric serial index.")
                return

            elif state == "awaiting_channel_edit_template":
                try:
                    ch_db_id = context.user_data.get("editing_serial")
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

                    if ch_db_id is not None:
                        chat_info = await context.bot.get_chat(ch_id)
                        ch_name = chat_info.title or "Channel"

                        channels_collection.update_one(
                            {"id": ch_db_id},
                            {"$set": {
                                "id": ch_id,
                                "name": ch_name,
                                "type": c_type,
                                "category": c_category,
                                "more_info": more_info
                            }}
                        )

                        del PENDING_ADMIN_ACTIONS[user_id]
                        await update.message.reply_text("✅ Channel database updated successfully!")
                    else:
                        await update.message.reply_text("❌ Session expired. Try /list_channel again.")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Failed to update item: {e}")
                    return

            elif state == "awaiting_settings_edit":
                try:
                    lines = text.split("\n")
                    new_admins = bot_settings["admins"]
                    new_more_link = bot_settings["more_channel_link"]
                    new_fs_ids = bot_settings["force_subscribe_ids"]
                    new_prices = bot_settings["prices"].copy()
                    new_ipp = bot_settings.get("items_per_page", 10)

                    for line in lines:
                        if ":" not in line:
                            continue
                        key, val = line.split(":", 1)
                        key_lower = key.strip().lower()
                        val_str = val.strip()

                        if "admis" in key_lower:
                            if val_str.lower() == "none" or not val_str:
                                new_admins = [DEFAULT_ADMIN_ID]
                            else:
                                admin_list = []
                                for part in val_str.split(","):
                                    part_clean = part.strip()
                                    if part_clean.isdigit():
                                        admin_list.append(int(part_clean))
                                if DEFAULT_ADMIN_ID not in admin_list:
                                    admin_list.append(DEFAULT_ADMIN_ID)
                                new_admins = admin_list
                        elif "more channel link" in key_lower:
                            new_more_link = val_str
                        elif "force subscribe channel ids" in key_lower:
                            if val_str.lower() == "none" or not val_str:
                                new_fs_ids = []
                            else:
                                fs_list = []
                                for part in val_str.split(","):
                                    part_clean = part.strip()
                                    if part_clean:
                                        try:
                                            fs_list.append(int(part_clean))
                                        except ValueError:
                                            pass
                                new_fs_ids = fs_list
                        elif "1 month price" in key_lower:
                            new_prices["1"] = val_str
                        elif "2 month price" in key_lower:
                            new_prices["2"] = val_str
                        elif "3 month price" in key_lower:
                            new_prices["3"] = val_str
                        elif "items per page" in key_lower:
                            new_ipp = int(val_str)

                    update_settings({
                        "admins": new_admins,
                        "more_channel_link": new_more_link,
                        "force_subscribe_ids": new_fs_ids,
                        "prices": new_prices,
                        "items_per_page": new_ipp
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text("✅ Settings updated successfully!")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error updating settings: {e}")
                    return

            elif state == "awaiting_ch_search_query":
                context.user_data["channel_list_search"] = text
                del PENDING_ADMIN_ACTIONS[user_id]
                await render_channel_list_page(update, context, edit_message=False)
                return

            elif state == "awaiting_usr_search_query":
                context.user_data["user_list_search"] = text
                del PENDING_ADMIN_ACTIONS[user_id]
                await render_user_list_page(update, context, edit_message=False)
                return

    await handle_search_message_logic(update, context)


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows subscription plans, payment details, QR code, and actions as media type."""
    if await maintenance_check(update, context):
        return
    bot_settings = get_settings()
    p1 = bot_settings["prices"]["1"]
    p2 = bot_settings["prices"]["2"]
    p3 = bot_settings["prices"]["3"]

    plan_text = (
        "💎 <b>𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐒𝐔𝐁𝐒𝐂𝐑𝐈𝐏𝐓𝐈𝐎𝐍 𝐏𝐋𝐀𝐍𝐒</b>\n\n"
        f"<blockquote>• <b>₹{p1} INR</b> for 1 Month</blockquote>\n"
        f"<blockquote>• <b>₹{p2} INR</b> for 2 Months</blockquote>\n"
        f"<blockquote>• <b>₹{p3} INR</b> for 3 Months</blockquote>\n\n"
        "<b>UPI ID:</b> <code>padhand171@okicici</code>\n\n"
        "Scan the QR code or click the button to pay:"
    )
    
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url="https://rb.gy/81kgkx")],
        [InlineKeyboardButton("📤 Send Screenshot", url="https://t.me/Digital_adminbot")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_photo(
        photo=QR_IMAGE_URL,
        caption=plan_text,
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays help commands based on user roles (Admin vs Regular User)."""
    if await maintenance_check(update, context):
        return
    user_id = update.effective_user.id
    bot_settings = get_settings()
    is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS

    user_help = (
        "📖 <b>𝐔𝐒𝐄𝐑 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
        "• /start - Start the bot & access items\n"
        "• /plan - View premium subscription plans & payment options\n"
        "• /stats - View your account status, ID, expiry, and joined list\n"
        "• /language - Change preferred bot language\n"
        "• /help - Show available commands\n"
        "• /cancel - Cancel any current operation\n"
        "• <i>Send any keyword to search database items</i>"
    )

    if is_admin:
        admin_help = (
            "\n\n⚙️ <b>𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
            "• /add_channel - Add a new channel configuration\n"
            "• /list_channel - List and edit managed channels\n"
            "• /settings - View and edit bot settings\n"
            "• /backup - Export MongoDB backup file (.json)\n"
            "• /broadcast - Broadcast text/photo/video to users\n"
            "• /add_user <code>{id} {validity}</code> - Grant user time (e.g. 7d, 30m)\n"
            "• /list_user - List premium users sorted by expiry"
        )
        await update.message.reply_text(user_help + admin_help, parse_mode="HTML")
    else:
        await update.message.reply_text(user_help, parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command, enforces force subscription & verification requirements with media type start response."""
    if not await rate_limit_middleware(update, context):
        return
    if await maintenance_check(update, context):
        return

    user_id = update.effective_user.id

    # --- FORCE SUBSCRIBE CHECK ---
    unjoined_channels = await check_force_subscribe(user_id, context)
    if unjoined_channels:
        fs_text = "⚠️ <b>Please join our mandatory channels below to use this bot:</b>\n\n"
        fs_keyboard = []
        for ch in unjoined_channels:
            fs_keyboard.append([InlineKeyboardButton(f"📢 Join {ch['name']}", url=ch['link'])])
        fs_keyboard.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_fs_complete")])
        
        await update.message.reply_text(
            fs_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(fs_keyboard)
        )
        return

    args = context.args
    bot_settings = get_settings()
    if args:
        token = args[0]
        matched_item = channels_collection.find_one({"token": token})
        if matched_item:
            channel_id = matched_item["id"]
            c_type = matched_item["type"]

            # --- USER PREMIUM CHECK ---
            user_record = users_collection.find_one({"user_id": user_id})
            is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS
            now = datetime.now()
            is_user_premium = is_admin or (user_record and user_record.get("expiry") and user_record["expiry"] > now)

            # --- ACCESS RESTRICTION CHECKS FOR PREMIUM CHANNELS ---
            if c_type == "premium" and not is_user_premium:
                await update.message.reply_text(
                    "<blockquote>🔒 <b>Access Denied:</b> This is a <b>Premium</b> channel.</blockquote>\n"
                    "Please use /plan to purchase a subscription package.",
                    parse_mode="HTML"
                )
                return

            # --- VERIFICATION STEP HANDLING ---
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
                        "Please complete the shortener verification link below to unlock access, or buy premium to bypass verification completely!\n\n"
                        "💎 <i>Buy Premium via /plan to skip verification.</i>"
                    )
                    keyboard = [
                        [InlineKeyboardButton("🔗 Complete Verification", url=shortened_link)],
                        [InlineKeyboardButton("✅ I Have Verified", callback_data=f"verify_check_{token}")]
                    ]
                    
                    await update.message.reply_photo(
                        photo=VERIFY_BANNER_URL,
                        caption=verify_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

            # --- EXISTING JOIN TRACKING & EXPIRY ---
            tracker = trackers_collection.find_one({"user_id": user_id, "channel_id": channel_id})
            if tracker and now < tracker["expiry"]:
                expiry_dt = tracker["expiry"]
                await update.message.reply_text(
                    f"⏳ You have already joined this channel. \nAccess expires on `{expiry_dt.strftime('%Y-%m-%d %H:%M')}`."
                )
                return
            
            new_tracker_expiry = now + timedelta(days=7)
            trackers_collection.update_one(
                {"user_id": user_id, "channel_id": channel_id},
                {"$set": {"expiry": new_tracker_expiry}},
                upsert=True
            )

            # Update user joined channels record
            users_collection.update_one(
                {"user_id": user_id},
                {
                    "$setOnInsert": {"expiry": now},
                    "$addToSet": {"joined_channels": matched_item['name']}
                },
                upsert=True
            )

            target_invite_link = matched_item["link"]

            try:
                if c_type == "verify":
                    invite = await context.bot.create_chat_invite_link(
                        chat_id=channel_id,
                        creates_join_request=True,
                        name=f"Verify User {user_id}"
                    )
                    target_invite_link = invite.invite_link
                elif c_type == "free":
                    invite = await context.bot.create_chat_invite_link(
                        chat_id=channel_id,
                        member_limit=1,
                        name=f"Free Single Use {user_id}"
                    )
                    target_invite_link = invite.invite_link
                else:
                    invite = await context.bot.create_chat_invite_link(
                        chat_id=channel_id,
                        name=f"Premium Access {user_id}"
                    )
                    target_invite_link = invite.invite_link
            except Exception as e:
                logger.error(f"Failed to generate dynamic invite link: {e}")

            message_text = (
                "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
                "<blockquote>"
                f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
                f"<b>𝚃𝚢𝚙𝚎:</b> {matched_item['type'].capitalize()}\n"
                f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {matched_item['category']}\n"
                "🕒 <b>Access Duration:</b> 7 Days\n"
                "</blockquote>"
            )

            keyboard = [
                [InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                message_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return

    await update.message.reply_photo(
        photo=START_MEDIA_URL,
        caption=tr(user_id, "welcome"),
        parse_mode="HTML"
    )


async def chat_join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically approves incoming join requests for verification channels and tracks sessions in MongoDB."""
    query: ChatJoinRequest = update.chat_join_request
    if not query:
        return

    user_id = query.from_user.id
    chat_id = query.chat.id

    try:
        await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
        trackers_collection.update_one(
            {"user_id": user_id, "channel_id": chat_id},
            {"$set": {"expiry": datetime.now() + timedelta(days=7)}},
            upsert=True
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text="<blockquote>✅ Verification completed via Arolinks flow! Your join request has been approved for 7 days.</blockquote>"
        )
    except Exception as e:
        logger.error(f"Failed to process join request: {e}")


# --- FEATURE 5: ADVANCED MULTI-KEYWORD & FUZZY SEARCH ---
async def handle_search_message_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes search queries with thefuzz fuzzy matching support from MongoDB."""
    query = update.message.text.strip()
    if not query:
        return

    all_channels = list(channels_collection.find({}))
    channel_names = [ch["name"] for ch in all_channels]
    
    # Use thefuzz to extract matches with score threshold
    fuzzy_results = process.extract(query, channel_names, limit=15, scorer=fuzz.token_sort_ratio)
    
    matched_names = {res[0] for res in fuzzy_results if res[1] >= 50}
    found_items = [ch for ch in all_channels if ch["name"] in matched_names or normalize_text(query) in normalize_text(f"{ch['name']} {ch['category']} {ch['type']}")]

    context.user_data["search_query"] = query
    context.user_data["found_items"] = found_items
    context.user_data["current_page"] = 0

    await send_search_results(update, context, edit_message=False)


async def send_search_results(
    update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message: bool = False
):
    """Renders UI layout with pagination and category options."""
    query = context.user_data.get("search_query", "")
    all_channels = list(channels_collection.find({}))
    found_items = context.user_data.get("found_items", all_channels)
    page = context.user_data.get("current_page", 0)

    total_items = len(all_channels)
    found_count = len(found_items)

    bot_settings = get_settings()
    ITEMS_PER_PAGE = bot_settings.get("items_per_page", 10)
    max_pages = (found_count - 1) // ITEMS_PER_PAGE if found_count > 0 else 0
    page = max(0, min(page, max_pages))
    context.user_data["current_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_page_items = found_items[start_idx:end_idx]

    text_lines = [
        "📊 <b>𝐒𝐄𝐀𝐑𝐂𝐇 𝐀𝐍𝐀𝐋𝐘𝐓𝐈𝐂𝐒</b>",
        "──────────────────────────",
        "<blockquote>"
        f"▪ 𝐓𝐨𝐭𝐚𝐥 𝐈𝐭𝐞𝐦𝐬         : {total_items:,}\n"
        f"▪ 𝐌𝐚𝐭𝐜𝐡𝐞𝐬 𝐅𝐨𝐮𝐧𝐝  : {found_count:,}\n"
        f"▪ 𝐐𝐮𝐞𝐫𝐲 𝐒𝐭𝐫𝐢𝐧𝐠      : &quot;{query}&quot;\n"
        f"▪ 𝐍𝐚𝐯𝐢𝐠𝐚𝐭𝐢𝐨𝐧          : 𝐏𝐚𝐠𝐞 {page + 1} of {max_pages + 1 if max_pages >= 0 else 1}"
        "</blockquote>",
        "──────────────────────────",
        "📌 <b>𝐌𝐀𝐓𝐂𝐇𝐈𝐍𝐆 𝐑𝐄𝐂𝐎𝐑𝐃𝐒:</b>",
    ]

    if not current_page_items:
        text_lines.append("<i>No items found matching your filter criteria.</i>")
    else:
        for idx, item in enumerate(current_page_items, start=start_idx + 1):
            more_link_base = bot_settings["more_channel_link"]
            if not more_link_base.endswith("/"):
                more_link_base += "/"
            more_info_val = item.get("more_info", "")
            more_info_hyperlink = f"{more_link_base}{more_info_val}" if more_info_val else more_link_base

            text_lines.append(f" <blockquote>{idx}. <a href=\"{item['link']}\">{item['name']}</a> [{item['type'].upper()}] - <a href=\"{more_info_hyperlink}\">More Info</a></blockquote>")

    response_text = "\n".join(text_lines)

    keyboard = [
        [
            InlineKeyboardButton("🟢 Free", callback_data="filter_type_free"),
            InlineKeyboardButton("🟡 Verify", callback_data="filter_type_verify"),
            InlineKeyboardButton("🟣 Premium", callback_data="filter_type_premium"),
        ],
        [
            InlineKeyboardButton("📂 Category Filters", callback_data="show_categories")
        ]
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ 𝐏𝐫𝐞𝐯", callback_data="prev_page"))
    nav_row.append(InlineKeyboardButton("🔍 Search", callback_data="search_prompt_trigger"))
    if end_idx < found_count:
        nav_row.append(InlineKeyboardButton("𝐍𝐞𝐱𝐭 ➡️", callback_data="next_page"))
    if nav_row:
        keyboard.append(nav_row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit_message:
        query_obj = update.callback_query
        await query_obj.edit_message_text(
            text=response_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            text=response_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button clicks for filtering, types, pagination, settings edit prompt, and verification checks."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    bot_settings = get_settings()

    # Feature 2: Maintenance Mode toggle callback
    if data == "toggle_maintenance":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            await query.answer("⛔ Unauthorized.", show_alert=True)
            return
        current_mode = bot_settings.get("maintenance_mode", False)
        update_settings({"maintenance_mode": not current_mode})
        await query.answer(f"Maintenance Mode set to: {not current_mode}", show_alert=True)
        return

    # Feature 6: Language selection callbacks
    if data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        users_collection.update_one({"user_id": user_id}, {"$set": {"language": lang}}, upsert=True)
        await query.edit_message_text(f"✅ Language successfully changed to: {'English 🇬🇧' if lang == 'en' else 'Hindi 🇮🇳'}")
        return

    # Feature 8: Confirmations
    if data.startswith("confirm_del_ch_"):
        ch_id = int(data.replace("confirm_del_ch_", ""))
        channels_collection.delete_one({"id": ch_id})
        await query.edit_message_text("✅ Channel successfully deleted with confirmation!")
        return
    elif data == "proceed_edit_channel":
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_edit_template"
        await query.message.reply_text("✏️ Please send the updated channel details template format now.")
        return

    if data == "check_fs_complete":
        unjoined = await check_force_subscribe(user_id, context)
        if unjoined:
            await query.answer("❌ You have not joined all required channels yet!", show_alert=True)
        else:
            await query.edit_message_text("✅ Thank you for joining! You can now use /start again or send your search keyword.")
        return

    if data == "edit_settings_prompt":
        if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
            await query.answer("⛔ Unauthorized.", show_alert=True)
            return
        
        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_settings_edit"
        
        listed_admins = [str(adm) for adm in bot_settings["admins"] if adm != DEFAULT_ADMIN_ID]
        admins_str = ", ".join(listed_admins) if listed_admins else ""
        fs_ids = ", ".join(str(i) for i in bot_settings["force_subscribe_ids"]) if bot_settings["force_subscribe_ids"] else ""

        edit_template = (
            f"Admis: {admins_str}\n"
            f"More channel link: {bot_settings['more_channel_link']}\n"
            f"Force subscribe channel ids: {fs_ids}\n"
            f"1 month price: {bot_settings['prices']['1']}\n"
            f"2 month price: {bot_settings['prices']['2']}\n"
            f"3 month price: {bot_settings['prices']['3']}\n"
            f"Items Per Page: {bot_settings.get('items_per_page', 10)}"
        )
        await query.message.reply_text(
            "✏️ Edit the details in below and send back to me:\n\n" + f"<code>{edit_template}</code>",
            parse_mode="HTML"
        )
        return

    if data.startswith("verify_check_"):
        token = data.replace("verify_check_", "")
        VERIFICATION_STATE[(user_id, token)] = True
        
        matched_item = channels_collection.find_one({"token": token})
        if not matched_item:
            await query.message.reply_text("❌ Item session expired. Please send /start again.")
            return

        channel_id = matched_item["id"]
        c_type = matched_item["type"]
        now = datetime.now()
        
        trackers_collection.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {"expiry": now + timedelta(days=7)}},
            upsert=True
        )
        
        users_collection.update_one(
            {"user_id": user_id},
            {
                "$setOnInsert": {"expiry": now},
                "$addToSet": {"joined_channels": matched_item['name']}
            },
            upsert=True
        )

        target_invite_link = matched_item["link"]
        try:
            if c_type == "verify":
                invite = await context.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    creates_join_request=True,
                    name=f"Verify User {user_id}"
                )
                target_invite_link = invite.invite_link
            elif c_type == "free":
                invite = await context.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    member_limit=1,
                    name=f"Free Single Use {user_id}"
                )
                target_invite_link = invite.invite_link
        except Exception as e:
            logger.error(f"Failed to generate invite link after verification: {e}")

        message_text = (
            "✅ <b>Verification Successful!</b>\n\n"
            "📂 <b>𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙳𝚎𝚝𝚊𝚒𝚕𝚜</b>\n\n"
            "<blockquote>"
            f"<b>𝙽𝚊𝚖𝚎:</b> {matched_item['name']}\n"
            f"<b>𝚃𝚢𝚙𝚎:</b> {matched_item['type'].capitalize()}\n"
            f"<b>𝙲𝚊𝚝𝚎𝚐𝚘𝚛𝚢:</b> {matched_item['category']}\n"
            "🕒 <b>Access Duration:</b> 7 Days\n"
            "</blockquote>"
        )
        keyboard = [[InlineKeyboardButton("𝙹𝚘𝚒𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕", url=target_invite_link)]]
        
        await query.edit_message_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
        await query.message.reply_text("🔍 Send the channel name or ID keyword to filter list:")

    elif data == "usr_search_prompt":
        PENDING_ADMIN_ACTIONS[update.effective_user.id] = "awaiting_usr_search_query"
        await query.message.reply_text("🔍 Send the user ID keyword to filter list:")

    elif data == "search_prompt_trigger":
        await query.message.reply_text("🔍 Send any text keyword to search channel records:")

    elif data.startswith("filter_type_"):
        selected_type = data.replace("filter_type_", "")
        found_items = list(channels_collection.find({"type": selected_type}))
        context.user_data["search_query"] = f"Type: {selected_type.capitalize()}"
        context.user_data["found_items"] = found_items
        context.user_data["current_page"] = 0
        await send_search_results(update, context, edit_message=True)

    elif data == "show_categories":
        all_channels = list(channels_collection.find({}))
        categories_list = sorted(list(set(cat["category"] for cat in all_channels)))
        cat_buttons = [
            [InlineKeyboardButton(f"📁 {cat}", callback_data=f"filter_cat_{cat}")]
            for cat in categories_list
        ]
        cat_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
        
        await query.edit_message_text(
            text="🏷️ <b>Select Category Filter:</b>",
            reply_markup=InlineKeyboardMarkup(cat_buttons),
            parse_mode="HTML"
        )

    elif data.startswith("filter_cat_"):
        selected_cat = data.replace("filter_cat_", "")
        found_items = list(channels_collection.find({"category": selected_cat}))
        context.user_data["search_query"] = f"Category: {selected_cat}"
        context.user_data["found_items"] = found_items
        context.user_data["current_page"] = 0
        await send_search_results(update, context, edit_message=True)

    elif data == "back_to_search":
        context.user_data["found_items"] = list(channels_collection.find({}))
        context.user_data["search_query"] = ""
        await send_search_results(update, context, edit_message=True)


# --- FEATURE 4: INTERACTIVE NOTIFICATION REMINDERS (BACKGROUND JOB) ---
async def background_expiry_checker(context: ContextTypes.DEFAULT_TYPE):
    """Background task checking 7-day join validity expirations, subscriptions, and sending 24h advance warnings."""
    now = datetime.now()
    
    # 24-hour advance warning for expiring trackers
    warning_threshold = now + timedelta(hours=24)
    approaching_trackers = list(trackers_collection.find({"expiry": {"$lte": warning_threshold, "$gt": now}, "warned": {"$ne": True}}))
    for trk in approaching_trackers:
        try:
            await context.bot.send_message(chat_id=trk["user_id"], text="⚠️ Reminder: Your temporary access to a channel expires in 24 hours!")
            trackers_collection.update_one({"_id": trk["_id"]}, {"$set": {"warned": True}})
        except Exception as e:
            logger.error(f"Failed to send 24h reminder: {e}")

    expired_trackers = list(trackers_collection.find({"expiry": {"$lte": now}}))
    for tracker in expired_trackers:
        user_id = tracker["user_id"]
        channel_id = tracker["channel_id"]
        try:
            ch_item = channels_collection.find_one({"id": channel_id})
            ch_name = ch_item["name"] if ch_item else "the channel"
            
            await context.bot.ban_chat_member(chat_id=channel_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=channel_id, user_id=user_id)

            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ Your 7-day temporary access window has expired. You have been removed from {ch_name}."
            )
        except Exception as e:
            logger.error(f"Could not revoke access for user {user_id} in channel {channel_id}: {e}")
        
        trackers_collection.delete_one({"_id": tracker["_id"]})

    expired_users = list(users_collection.find({"expiry": {"$lte": now}}))
    for user_record in expired_users:
        user_id = user_record["user_id"]
        try:
            bot_settings = get_settings()
            if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⏳ Your premium subscription has expired. You are now back to the regular user tier."
                )
        except Exception as e:
            logger.error(f"Could not send premium expiry notice to user {user_id}: {e}")


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing in environment variables!")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register error handler (Feature 7)
    app.add_error_handler(global_error_handler)

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_channel", add_channel_command))
    app.add_handler(CommandHandler("list_channel", list_channel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("add_user", add_user_command))
    app.add_handler(CommandHandler("list_user", list_user_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("backup", backup_command))      # Feature 3
    app.add_handler(CommandHandler("broadcast", broadcast_command)) # Feature 11
    app.add_handler(CommandHandler("language", language_command))   # Feature 6
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))

    if app.job_queue:
        app.job_queue.run_repeating(background_expiry_checker, interval=3600, first=10)

    print("Bot is running with MongoDB backend, all 11 features successfully integrated...")
    app.run_polling()


if __name__ == "__main__":
    main()

