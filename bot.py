import os
import logging
import random
import string
import requests
import json
import io
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from dotenv import load_dotenv
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

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_USERNAME = os.getenv("BOT_USERNAME", "Dps_storiesbot")
DEFAULT_ADMIN_ID = int(os.getenv("DEFAULT_ADMIN_ID", "8323137024"))
ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.getenv("ADMIN_IDS", "8323137024").split(",")]
AROLINKS_API_TOKEN = os.getenv("AROLINKS_API_TOKEN", "9dd2d9a7855be5078a54d5a9a2493fb195162b5e")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_VIDEO_TUTORIAL_URL = os.getenv("VIDEO_TUTORIAL_URL", "https://t.me/your_video_tutorial_channel")
WEB_APP_BASE_URL = os.getenv("WEB_APP_BASE_URL", "https://your-render-app-url.onrender.com")

MONGO_URI = os.getenv("MONGO_URI", "")
client = MongoClient(MONGO_URI)
db = client["telegram_bot_db"]

channels_collection = db["channels"]
users_collection = db["users"]
trackers_collection = db["trackers"]
settings_collection = db["settings"]

if settings_collection.count_documents({"_id": "bot_settings"}) == 0:
    settings_collection.insert_one({
        "_id": "bot_settings",
        "admins": ADMIN_IDS,
        "more_channel_link": "https://t.me/your_more_channel/",
        "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
        "force_subscribe_ids": [],
        "prices": {"1": "49", "2": "95", "3": "140"},
        "start_media_url": "https://files.catbox.moe/aqak0m.jpg",
        "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
        "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
        "about_message": "✨ <b>Welcome to our Bot!</b>\n\nWe provide high-quality digital resources, instant updates, and secure content access channels.",
        "maintenance_mode": False,
        "maintenance_message": "🛠️ Bot is currently under maintenance. Please check back later!",
        "items_per_page": 10
    })

PENDING_ADMIN_ACTIONS = {}  
VERIFICATION_STATE = {}   
RATE_LIMIT_CACHE = {}     

def generate_dps_token() -> str:
    chars = string.ascii_letters + string.digits
    rand_part = ''.join(random.choices(chars, k=12))
    return f"dps_{rand_part}"

def get_settings():
    s = settings_collection.find_one({"_id": "bot_settings"})
    if not s:
        return {
            "admins": ADMIN_IDS,
            "more_channel_link": "https://t.me/your_more_channel/",
            "video_tutorial_link": DEFAULT_VIDEO_TUTORIAL_URL,
            "force_subscribe_ids": [],
            "prices": {"1": "49", "2": "95", "3": "140"},
            "start_media_url": "https://files.catbox.moe/aqak0m.jpg",
            "qr_image_url": "https://files.catbox.moe/68r9do.jpg",
            "verify_banner_url": "https://files.catbox.moe/rr3cn8.jpg",
            "about_message": "✨ <b>Welcome to our Bot!</b>",
            "maintenance_mode": False,
            "maintenance_message": "🛠️ Bot is under maintenance.",
            "items_per_page": 10
        }
    return s

def update_settings(new_fields: dict):
    settings_collection.update_one({"_id": "bot_settings"}, {"$set": new_fields})

def normalize_text(text: str) -> str:
    if not text:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', text)
    ascii_str = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    return ascii_str.strip().lower()

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
        return False
    RATE_LIMIT_CACHE[user.id] = now
    return True

async def maintenance_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_settings = get_settings()
    if bot_settings.get("maintenance_mode", False):
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id not in bot_settings.get("admins", []) and user_id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text(bot_settings.get("maintenance_message", "🛠️ Bot is under maintenance."))
            return True
    return False

# --- ENHANCED ADMIN CHANNEL MANAGEMENT COMMAND ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_settings = get_settings()
    if user_id not in bot_settings["admins"] and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    PENDING_ADMIN_ACTIONS[user_id] = "awaiting_channel_details"
    template = (
        "Story Name:\n"
        "Poster: [File ID or Image URL]\n"
        "Type: free/verify/premium\n"
        "Category: Action, Romance, AI\n"
        "Format: audio/video\n"
        "Demo Episodes: Ep 1, Ep 2\n"
        "Story Info: Detailed synopsis here...\n"
        "Direct Link: https://pocketfm.com/... or resource link"
    )
    await update.message.reply_text(
        "📥 <b>Send enhanced story/channel details template:</b>\n\n" + f"<blockquote><code>{template}</code></blockquote>",
        parse_mode="HTML"
    )

# --- GROUP STORY SEARCH & CAPTION POSTER HANDLER ---

async def handle_group_story_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        return

    query = update.message.text.strip()
    if query.startswith("/"):
        return

    all_channels = list(channels_collection.find({}))
    norm_query = normalize_text(query)
    
    matched_ch = None
    for ch in all_channels:
        if norm_query in normalize_text(ch.get("name", "")):
            matched_ch = ch
            break

    if not matched_ch:
        channel_names = [ch["name"] for ch in all_channels]
        fuzzy_results = process.extract(norm_query, [normalize_text(n) for n in channel_names], limit=1, scorer=fuzz.token_sort_ratio)
        if fuzzy_results and fuzzy_results[0][1] >= 60:
            matched_ch = all_channels[fuzzy_results[0][2]]

    if matched_ch:
        poster = matched_ch.get("poster", "https://files.catbox.moe/aqak0m.jpg")
        story_name = matched_ch.get("name", "Story")
        story_info = matched_ch.get("more_info", matched_ch.get("story_info", "No description available."))
        demo_eps = matched_ch.get("demo_episodes", "N/A")
        c_type = matched_ch.get("type", "free").capitalize()
        categories = ", ".join(matched_ch.get("categories", ["General"]))
        direct_link = matched_ch.get("link", "https://t.me/" + BOT_USERNAME)

        caption = (
            f"📖 <b>{story_name}</b>\n\n"
            f"<blockquote>"
            f"📌 <b>Category:</b> {categories}\n"
            f"🎧 <b>Format/Type:</b> {c_type}\n"
            f"🎬 <b>Demo Episodes:</b> {demo_eps}\n\n"
            f"📝 <b>Info:</b> {story_info}"
            f"</blockquote>"
        )

        web_app_url = f"{WEB_APP_BASE_URL}/webapp"
        keyboard = [
            [
                InlineKeyboardButton("🔗 Direct / Pocket FM Link", url=direct_link),
                InlineKeyboardButton("🌐 Open Web App", web_app=WebAppInfo(url=web_app_url))
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if poster.startswith("http"):
            await update.message.reply_photo(photo=poster, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        else:
            try:
                await update.message.reply_photo(photo=poster, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
            except Exception:
                await update.message.reply_text(text=caption, parse_mode="HTML", reply_markup=reply_markup)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await rate_limit_middleware(update, context):
        return
    if await maintenance_check(update, context):
        return

    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        await handle_group_story_search(update, context)
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
                    ch_name, poster, c_type, c_category_raw, format_type, demo_eps, story_info, dist_link = "", "https://files.catbox.moe/aqak0m.jpg", "free", "General", "audio", "N/A", "", ""
                    for line in lines:
                        if ":" not in line:
                            continue
                        k, v = line.split(":", 1)
                        k_l = k.strip().lower()
                        v_s = v.strip()
                        if "story name" in k_l or "channel name" in k_l: ch_name = v_s
                        elif "poster" in k_l: poster = v_s
                        elif "type" in k_l: c_type = v_s.lower()
                        elif "category" in k_l: c_category_raw = v_s
                        elif "format" in k_l: format_type = v_s.lower()
                        elif "demo episodes" in k_l: demo_eps = v_s
                        elif "story info" in k_l or "more info" in k_l: story_info = v_s
                        elif "link" in k_l: dist_link = v_s

                    categories_list = [c.strip() for c in c_category_raw.split(",") if c.strip()] or ["General"]
                    token = generate_dps_token()

                    channels_collection.insert_one({
                        "id": random.randint(100000, 999999),
                        "name": ch_name,
                        "poster": poster,
                        "categories": categories_list,
                        "category": categories_list[0],
                        "type": c_type,
                        "format": format_type,
                        "demo_episodes": demo_eps,
                        "story_info": story_info,
                        "more_info": story_info,
                        "link": dist_link or f"https://t.me/{BOT_USERNAME}?start={token}",
                        "token": token
                    })

                    del PENDING_ADMIN_ACTIONS[user_id]
                    await update.message.reply_text(f"✅ Story/Channel <b>{ch_name}</b> added successfully!", parse_mode="HTML")
                    return
                except Exception as e:
                    await update.message.reply_text(f"❌ Error parsing template: {e}")
                    return

    # Standard private chat search/browse logic
    await handle_search_message_logic(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_check(update, context):
        return
    user = update.effective_user
    bot_settings = get_settings()
    start_url = bot_settings.get("start_media_url", "https://files.catbox.moe/aqak0m.jpg")
    
    webapp_url = f"{WEB_APP_BASE_URL}/webapp"
    keyboard = [[InlineKeyboardButton("🚀 Launch Web App", web_app=WebAppInfo(url=webapp_url))]]
    await update.message.reply_photo(photo=start_url, caption="✨ <b>Welcome to DPS Stories Bot!</b>\n\nUse the Web App below to browse all stories with advanced filters.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_search_message_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return
    all_channels = list(channels_collection.find({}))
    found_items = [ch for ch in all_channels if normalize_text(query) in normalize_text(ch["name"])]
    await update.message.reply_text(f"🔍 Found {len(found_items)} matching stories. Open our Web App for advanced multi-criteria filtering!")


# --- ADVANCED WEB APP IMPLEMENTATION (FLASK) ---

web_app = Flask(__name__)

WEBAPP_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DPS Stories Hub</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-4">
    <div class="max-w-md mx-auto">
        <h1 class="text-2xl font-bold mb-4 text-center text-indigo-400">📚 DPS Stories Catalog</h1>
        
        <input type="text" id="search" placeholder="Search stories..." class="w-full p-3 rounded-lg bg-slate-900 border border-slate-800 mb-4 text-white focus:outline-none focus:border-indigo-500" oninput="filterStories()">

        <div class="flex gap-2 overflow-x-auto pb-2 mb-4 text-sm">
            <button onclick="setFilter('type', 'all')" class="filter-btn px-3 py-1 rounded bg-indigo-600 font-medium">All</button>
            <button onclick="setFilter('type', 'free')" class="filter-btn px-3 py-1 rounded bg-slate-800">Free</button>
            <button onclick="setFilter('type', 'verify')" class="filter-btn px-3 py-1 rounded bg-slate-800">Verify</button>
            <button onclick="setFilter('type', 'premium')" class="filter-btn px-3 py-1 rounded bg-slate-800">Premium</button>
            <button onclick="setFilter('format', 'audio')" class="filter-btn px-3 py-1 rounded bg-slate-800">Audio</button>
            <button onclick="setFilter('format', 'video')" class="filter-btn px-3 py-1 rounded bg-slate-800">Video</button>
        </div>

        <div id="story-list" class="space-y-3">
            <!-- Dynamic Content -->
        </div>
    </div>

    <script>
        let stories = [];
        let currentType = 'all';
        let currentFormat = 'all';

        async function fetchStories() {
            const res = await fetch('/api/stories');
            stories = await res.json();
            renderStories(stories);
        }

        function setFilter(category, val) {
            if(category === 'type') currentType = val;
            if(category === 'format') currentFormat = val;
            filterStories();
        }

        function filterStories() {
            const query = document.getElementById('search').value.toLowerCase();
            const filtered = stories.filter(s => {
                const matchQuery = s.name.toLowerCase().includes(query);
                const matchType = currentType === 'all' || s.type === currentType;
                const matchFormat = currentFormat === 'all' || s.format === currentFormat;
                return matchQuery && matchType && matchFormat;
            });
            renderStories(filtered);
        }

        function renderStories(items) {
            const list = document.getElementById('story-list');
            list.innerHTML = items.length ? '' : '<p class="text-center text-slate-500">No stories found.</p>';
            items.forEach(s => {
                list.innerHTML += `
                    <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 flex gap-3 items-center">
                        <img src="${s.poster || 'https://files.catbox.moe/aqak0m.jpg'}" class="w-16 h-16 object-cover rounded-md">
                        <div class="flex-1">
                            <h3 class="font-semibold text-white">${s.name}</h3>
                            <p class="text-xs text-slate-400 capitalize">${s.type} • ${s.format || 'audio'}</p>
                            <a href="${s.link}" target="_blank" class="text-xs text-indigo-400 hover:underline mt-1 inline-block">Access Resource →</a>
                        </div>
                    </div>
                `;
            });
        }

        fetchStories();
    </script>
</body>
</html>
"""

@web_app.route('/')
def health_check():
    return "Bot is alive and running!", 200

@web_app.route('/webapp')
def webapp_view():
    return render_template_string(WEBAPP_TEMPLATE)

@web_app.route('/api/stories')
def api_stories():
    items = list(channels_collection.find({}, {"_id": False}))
    return jsonify(items)

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return

    server_thread = Thread(target=run_web_server, daemon=True)
    server_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_channel", add_channel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()
