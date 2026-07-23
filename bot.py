import os
import logging
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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
import certifi

# Load environment variables from .env file
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- WEB SERVER BLOCK FOR RENDER PORT BINDING ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

def run_web_server():
    port = int(os.environ.get("PORT", 4000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Starting health check web server on port {port}...")
    server.serve_forever()

# --- CONFIGURATION & DATABASE BLOCK ---
BOT_USERNAME = os.getenv("BOT_USERNAME", "Dps_storiesbot")
DEFAULT_ADMIN_ID = int(os.getenv("DEFAULT_ADMIN_ID", "8323137024"))
ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "8323137024").split(",")]
AROLINKS_API_TOKEN = os.getenv("AROLINKS_API_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Media Links Configuration
START_MEDIA_URL = os.getenv("START_MEDIA_URL", "https://ibb.co/ynTDh3tn")
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "https://files.catbox.moe/68r9do.jpg")
VERIFY_BANNER_URL = os.getenv("VERIFY_BANNER_URL", "https://files.catbox.moe/rr3cn8.jpg")

# MongoDB Configuration with certifi TLS fix for cloud deployments
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
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
        }
    })

PENDING_ADMIN_ACTIONS = {}  # Format: { user_id: "awaiting_channel_details" }
VERIFICATION_STATE = {}   # Format: { (user_id, token): True/False }


def get_settings():
    """Fetches bot settings from MongoDB."""
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "more_channel_link": "https://t.me/your_more_channel/",
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"}
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
        f"<b>3 month price:</b> {bot_settings['prices']['3']}"
    )

    keyboard = [[InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings_prompt")]]
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

    ITEMS_PER_PAGE = 5
    max_pages = (len(items) - 1) // ITEMS_PER_PAGE if items else 0
    page = max(0, min(page, max_pages))
    context.user_data["channel_list_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = items[start_idx:end_idx]

    bot_settings = get_settings()
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

    ITEMS_PER_PAGE = 3
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
                        PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_edit_template"
                        
                        edit_template = (
                            f"Channel id:{ch['id']}\n"
                            f"Type:{ch['type']}\n"
                            f"Category:{ch['category']}\n"
                            f"More info:{ch.get('more_info', '')}"
                        )
                        await update.message.reply_text(
                            "✏️ Edit the details below and send back:\n\n" + f"<code>{edit_template}</code>",
                            parse_mode="HTML"
                        )
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

                    update_settings({
                        "admins": new_admins,
                        "more_channel_link": new_more_link,
                        "force_subscribe_ids": new_fs_ids,
                        "prices": new_prices
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
    bot_settings = get_settings()
    p1 = bot_settings["prices"]["1"]
    p2 = bot_settings["prices"]["2"]
    p3 = bot_settings["prices"]["3"]

    plan_text = (
        "💎 <b>𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐒𝐔𝐁𝐒𝐂𝐑𝐈𝐏𝐓𝐈𝐎𝐍 𝐏𝐋𝐀𝐍𝐒</b>\n\n"
        f"<blockquote>• <b>₹{p1} INR</b> for 1 Month</blockquote>\n"
        f"<blockquote>• <b>₹{p2} INR</b> for 2 Months</blockquote>\n"
        f"<blockquote>• <b>₹{p3} INR</b> for 3 Months</blockquote>\n\n"
        "<b>UPI ID:</b> <code>fshhs@hshs</code>\n\n"
        "Scan the QR code or click the button to pay:"
    )
    
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url="https://rb.gy/81kgkx")],
        [InlineKeyboardButton("📤 Send Screenshot", url="https://t.me/idffajnbot")]
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
    user_id = update.effective_user.id
    bot_settings = get_settings()
    is_admin = user_id in bot_settings["admins"] or user_id in ADMIN_IDS

    user_help = (
        "📖 <b>𝐔𝐒𝐄𝐑 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
        "• /start - Start the bot & access items\n"
        "• /plan - View premium subscription plans & payment options\n"
        "• /stats - View your account status, ID, expiry, and joined list\n"
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
            "• /add_user <code>{id} {validity}</code> - Grant user time (e.g. 7d, 30m)\n"
            "• /list_user - List premium users sorted by expiry"
        )
        await update.message.reply_text(user_help + admin_help, parse_mode="HTML")
    else:
        await update.message.reply_text(user_help, parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command, enforces force subscription & verification requirements with media type start response."""
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
        caption="👋 𝚆𝚎𝚕𝚌𝚘𝚖𝚎!\n𝚂𝚎𝚗𝚍 𝚖𝚎 𝚊𝚗𝚢 𝚔𝚎𝚢𝚠𝚘𝚛𝚍 𝚘𝚛 𝚙𝚑𝚛𝚊𝚜𝚎 𝚝𝚘 𝚜𝚎𝚊𝚛𝚌𝚑 𝚘𝚞𝚛 𝚍𝚊𝚝𝚊𝚋𝚊𝚜𝚎.",
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


async def handle_search_message_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes search queries with font-agnostic fallback logic from MongoDB."""
    query = update.message.text.strip()
    if not query:
        return

    query_lower = normalize_text(query)
    all_channels = list(channels_collection.find({}))
    found_items = []

    for item in all_channels:
        item_text = normalize_text(f"{item['name']} {item['category']} {item['type']}")
        if query_lower in item_text:
            found_items.append(item)

    if not found_items:
        keywords = query_lower.split()
        for item in all_channels:
            item_text = normalize_text(f"{item['name']} {item['category']} {item['type']}")
            if any(kw in item_text for kw in keywords):
                found_items.append(item)

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

    ITEMS_PER_PAGE = 10
    max_pages = (found_count - 1) // ITEMS_PER_PAGE if found_count > 0 else 0
    page = max(0, min(page, max_pages))
    context.user_data["current_page"] = page

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_page_items = found_items[start_idx:end_idx]

    bot_settings = get_settings()
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
            f"3 month price: {bot_settings['prices']['3']}"
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


async def background_expiry_checker(context: ContextTypes.DEFAULT_TYPE):
    """Background task checking 7-day join validity expirations for free/verify users and subscription expirations for premium users."""
    now = datetime.now()
    
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

    # Start lightweight web server in background thread to satisfy Render port requirements
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_channel", add_channel_command))
    app.add_handler(CommandHandler(opt := "list_channel", list_channel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("add_user", add_user_command))
    app.add_handler(CommandHandler("list_user", list_user_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))

    if app.job_queue:
        app.job_queue.run_repeating(background_expiry_checker, interval=3600, first=10)

    print("Bot is running with MongoDB backend, web server port 4000, and SSL fixes...")
    app.run_polling()


if __name__ == "__main__":
    main()

