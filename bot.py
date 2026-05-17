"""
╔══════════════════════════════════════════════════════════╗
║        ULTRA ADVANCED TELEGRAM BUSINESS BOT              ║
║  Features: Unlimited msgs | Offline mode | Scheduling   ║
║           Per-user settings | Admin panel | Logs         ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import json
import logging
import asyncio
from datetime import datetime, time as dtime
import pytz
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    BusinessConnectionHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    CommandHandler,
)
import google.generativeai as genai
from dotenv import load_dotenv

# ─── Load env ────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
ADMIN_USER_ID      = int(os.getenv("ADMIN_USER_ID", "0"))  # apna Telegram user ID daalo .env me
TIMEZONE           = os.getenv("TIMEZONE", "Asia/Kolkata")

# ─── Logging ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Data persistence ─────────────────────────────────────
DATA_FILE = Path("bot_data.json")

def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_data = load_data()

# ─── Default user config ──────────────────────────────────
DEFAULT_USER_CONFIG = {
    "offline_mode": False,
    "away_message": "Yaar abhi main offline hoon 😴 Thodi der mein reply karunga!",
    "first_msg_greeting": True,
    "ai_enabled": True,
    "schedule_enabled": False,
    "offline_from": "22:00",   # 10 PM
    "offline_until": "08:00",  # 8 AM
    "language": "hinglish",    # hinglish / hindi / english
    "greeting_message": "Hey! 👋 Kya haal hai? Batao kya kaam hai!",
    "seen_users": [],          # list of user_ids jo pehle aa chuke hain
    "stats": {
        "total_messages": 0,
        "messages_today": 0,
        "last_reset_date": str(datetime.now().date()),
    }
}

def get_user_config(owner_id: int) -> dict:
    key = str(owner_id)
    if key not in bot_data:
        bot_data[key] = dict(DEFAULT_USER_CONFIG)
        save_data(bot_data)
    # Ensure all keys exist (for older configs)
    for k, v in DEFAULT_USER_CONFIG.items():
        if k not in bot_data[key]:
            bot_data[key][k] = v
    return bot_data[key]

def save_user_config(owner_id: int, config: dict):
    bot_data[str(owner_id)] = config
    save_data(bot_data)

# ─── Gemini AI ────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPTS = {
    "hinglish": (
        "Tu ek smart, friendly AI assistant hai jo kisi ke Telegram Business account ki taraf se reply karta hai. "
        "Hinglish (Hindi + English mix) mein baat kar. Short, helpful aur warm replies de. "
        "Kabhi mat batana ki tu AI hai jab tak seedha na poocha jaye. "
        "Natural laga, jaise koi dost reply kar raha ho."
    ),
    "hindi": (
        "तुम एक स्मार्ट AI असिस्टेंट हो जो Telegram Business account की तरफ से reply करते हो। "
        "हिंदी में बात करो। छोटे, helpful और friendly replies दो। "
        "कभी मत बताना कि तुम AI हो जब तक सीधे न पूछा जाए।"
    ),
    "english": (
        "You are a smart, friendly AI assistant replying on behalf of someone's Telegram Business account. "
        "Reply in English. Keep it short, helpful and warm. "
        "Never reveal you are an AI unless directly asked."
    ),
}

# Chat histories: { "owner_id:sender_id": [...] }
chat_histories: dict[str, list] = {}
MAX_HISTORY = 40  # Last 40 messages

def get_ai_model(language: str):
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS["hinglish"]),
    )

def get_ai_reply(owner_id: int, sender_id: int, user_message: str, language: str = "hinglish") -> str:
    history_key = f"{owner_id}:{sender_id}"
    if history_key not in chat_histories:
        chat_histories[history_key] = []

    history = chat_histories[history_key]
    model = get_ai_model(language)

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_message)
        reply = response.text.strip()

        history.append({"role": "user", "parts": [user_message]})
        history.append({"role": "model", "parts": [reply]})

        if len(history) > MAX_HISTORY:
            chat_histories[history_key] = history[-MAX_HISTORY:]

        return reply
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "Sorry yaar, AI se connect nahi ho pa raha abhi. Thodi der baad try karo! 🙏"

# ─── Schedule check ────────────────────────────────────────
def is_scheduled_offline(config: dict) -> bool:
    """Check karo ki current time offline schedule ke andar hai ya nahi."""
    if not config.get("schedule_enabled"):
        return False
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz).time()
        offline_from  = dtime(*map(int, config["offline_from"].split(":")))
        offline_until = dtime(*map(int, config["offline_until"].split(":")))

        # Overnight schedule handle (e.g., 22:00 - 08:00)
        if offline_from > offline_until:
            return now >= offline_from or now < offline_until
        else:
            return offline_from <= now < offline_until
    except Exception as e:
        logger.error(f"Schedule check error: {e}")
        return False

def should_send_away(config: dict) -> bool:
    return config.get("offline_mode", False) or is_scheduled_offline(config)

# ─── Stats updater ────────────────────────────────────────
def update_stats(config: dict) -> dict:
    today = str(datetime.now().date())
    stats = config.get("stats", {})
    if stats.get("last_reset_date") != today:
        stats["messages_today"] = 0
        stats["last_reset_date"] = today
    stats["total_messages"] = stats.get("total_messages", 0) + 1
    stats["messages_today"] = stats.get("messages_today", 0) + 1
    config["stats"] = stats
    return config

# ─── Keyboards ────────────────────────────────────────────
def main_settings_keyboard(config: dict) -> InlineKeyboardMarkup:
    offline_icon  = "🔴" if config["offline_mode"] else "🟢"
    ai_icon       = "🤖" if config["ai_enabled"] else "❌"
    schedule_icon = "⏰" if config["schedule_enabled"] else "⬜"
    greet_icon    = "👋" if config["first_msg_greeting"] else "❌"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{offline_icon} Offline Mode: {'ON' if config['offline_mode'] else 'OFF'}", callback_data="toggle_offline")],
        [InlineKeyboardButton(f"{ai_icon} AI Reply: {'ON' if config['ai_enabled'] else 'OFF'}", callback_data="toggle_ai")],
        [InlineKeyboardButton(f"{schedule_icon} Auto Schedule: {'ON' if config['schedule_enabled'] else 'OFF'}", callback_data="toggle_schedule")],
        [InlineKeyboardButton(f"{greet_icon} First-Time Greeting: {'ON' if config['first_msg_greeting'] else 'OFF'}", callback_data="toggle_greeting")],
        [InlineKeyboardButton("✏️ Away Message Set Karo", callback_data="set_away")],
        [InlineKeyboardButton("🕐 Schedule Time Set Karo", callback_data="set_schedule")],
        [InlineKeyboardButton("👋 Greeting Message Set Karo", callback_data="set_greet_msg")],
        [InlineKeyboardButton("🌐 Language Badlo", callback_data="set_language")],
        [InlineKeyboardButton("📊 Stats Dekho", callback_data="view_stats")],
        [InlineKeyboardButton("🔄 History Reset Karo", callback_data="reset_history")],
    ])

def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 Hinglish (Hindi+English)", callback_data="lang_hinglish")],
        [InlineKeyboardButton("🇮🇳 Pure Hindi", callback_data="lang_hindi")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_english")],
        [InlineKeyboardButton("« Wapas", callback_data="back_settings")],
    ])

# ─── Command Handlers ─────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    await update.message.reply_text(
        "🤖 *Ultra Advanced Business Bot*\n\n"
        "Main tumhare Telegram Business account ke liye ek fully customizable AI assistant hoon!\n\n"
        "*Kya kya kar sakta hoon:*\n"
        "• 🔴 Offline mode — auto away message bhejo\n"
        "• ⏰ Smart schedule — time ke hisaab se auto offline\n"
        "• 🤖 AI replies — Gemini se powered\n"
        "• 👋 First-time greeting — naye logon ko special welcome\n"
        "• 📊 Message stats — kitne log aaye track karo\n"
        "• 🌐 Language control — Hindi/English/Hinglish\n\n"
        "*Commands:*\n"
        "/settings — apni settings manage karo\n"
        "/status — current status dekho\n"
        "/stats — message statistics\n"
        "/offline — offline mode ON karo\n"
        "/online — offline mode OFF karo\n"
        "/reset — chat history clear karo\n"
        "/admin — admin panel (sirf owner ke liye)\n\n"
        "Shuru karo `/settings` se! 🚀",
        parse_mode="Markdown"
    )

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    lang = config.get("language", "hinglish")
    offline_status = "🔴 OFFLINE" if should_send_away(config) else "🟢 ONLINE"

    await update.message.reply_text(
        f"⚙️ *Settings Panel*\n\n"
        f"Status: {offline_status}\n"
        f"Language: {lang.title()}\n"
        f"AI: {'ON 🤖' if config['ai_enabled'] else 'OFF ❌'}\n"
        f"Schedule: {'ON ⏰' if config['schedule_enabled'] else 'OFF'}\n\n"
        f"Neeche se koi bhi setting badlo 👇",
        parse_mode="Markdown",
        reply_markup=main_settings_keyboard(config)
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz).strftime("%H:%M")

    status = "🔴 OFFLINE (Manual)" if config["offline_mode"] else \
             "🔴 OFFLINE (Scheduled)" if is_scheduled_offline(config) else \
             "🟢 ONLINE"

    await update.message.reply_text(
        f"📊 *Current Status*\n\n"
        f"Status: {status}\n"
        f"Current Time: {now} ({TIMEZONE})\n"
        f"AI Reply: {'✅' if config['ai_enabled'] else '❌'}\n"
        f"Away Message: _{config['away_message']}_\n"
        f"Schedule: {config['offline_from']} - {config['offline_until']} ({'Active' if config['schedule_enabled'] else 'Inactive'})\n"
        f"Language: {config['language'].title()}",
        parse_mode="Markdown"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    stats = config.get("stats", {})
    seen = len(config.get("seen_users", []))

    await update.message.reply_text(
        f"📈 *Message Statistics*\n\n"
        f"Total Messages: *{stats.get('total_messages', 0)}*\n"
        f"Messages Today: *{stats.get('messages_today', 0)}*\n"
        f"Unique Users: *{seen}*\n"
        f"Active Conversations: *{sum(1 for k in chat_histories if k.startswith(str(user_id)))}*",
        parse_mode="Markdown"
    )

async def cmd_offline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    config["offline_mode"] = True
    save_user_config(user_id, config)
    await update.message.reply_text("🔴 Offline mode ON! Ab main tumhari taraf se away message bhejunga.")

async def cmd_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = get_user_config(user_id)
    config["offline_mode"] = False
    save_user_config(user_id, config)
    await update.message.reply_text("🟢 Offline mode OFF! Ab main AI se reply karunga.")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Clear all histories for this owner
    keys_to_del = [k for k in chat_histories if k.startswith(str(user_id))]
    for k in keys_to_del:
        del chat_histories[k]
    await update.message.reply_text("✅ Saari chat history reset ho gayi!")

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Sirf admin is command ka use kar sakta hai.")
        return

    total_users = len(bot_data)
    total_msgs  = sum(
        bot_data[k].get("stats", {}).get("total_messages", 0)
        for k in bot_data
    )
    active_convos = len(chat_histories)

    user_list = ""
    for uid, cfg in list(bot_data.items())[:10]:  # first 10 users
        msgs = cfg.get("stats", {}).get("total_messages", 0)
        status = "🔴" if cfg.get("offline_mode") else "🟢"
        user_list += f"  {status} ID:{uid} | {msgs} msgs\n"

    await update.message.reply_text(
        f"👑 *Admin Panel*\n\n"
        f"Total Bot Users: *{total_users}*\n"
        f"Total Messages Handled: *{total_msgs}*\n"
        f"Active Conversations: *{active_convos}*\n\n"
        f"*Recent Users:*\n{user_list or 'None'}",
        parse_mode="Markdown"
    )

# ─── Callback Query Handler (Settings UI) ─────────────────
# Track users waiting for input
awaiting_input: dict[int, str] = {}  # user_id: what they're setting

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    config = get_user_config(user_id)
    data = query.data

    if data == "toggle_offline":
        config["offline_mode"] = not config["offline_mode"]
        save_user_config(user_id, config)
        status = "🔴 OFFLINE mode ON!" if config["offline_mode"] else "🟢 ONLINE mode ON!"
        await query.edit_message_text(
            f"✅ {status}\n\nSettings 👇",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "toggle_ai":
        config["ai_enabled"] = not config["ai_enabled"]
        save_user_config(user_id, config)
        await query.edit_message_text(
            f"✅ AI Reply {'ON 🤖' if config['ai_enabled'] else 'OFF ❌'}!\n\nSettings 👇",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "toggle_schedule":
        config["schedule_enabled"] = not config["schedule_enabled"]
        save_user_config(user_id, config)
        state = "ON ⏰" if config["schedule_enabled"] else "OFF"
        await query.edit_message_text(
            f"✅ Auto Schedule {state}!\n"
            f"Time: {config['offline_from']} → {config['offline_until']}\n\nSettings 👇",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "toggle_greeting":
        config["first_msg_greeting"] = not config["first_msg_greeting"]
        save_user_config(user_id, config)
        await query.edit_message_text(
            f"✅ First-Time Greeting {'ON 👋' if config['first_msg_greeting'] else 'OFF'}!\n\nSettings 👇",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "set_away":
        awaiting_input[user_id] = "away_message"
        await query.edit_message_text(
            "✏️ *Away Message Set Karo*\n\n"
            f"Current: _{config['away_message']}_\n\n"
            "Naya message type karo aur send karo 👇\n"
            "(Cancel karne ke liye /settings likho)",
            parse_mode="Markdown"
        )

    elif data == "set_greet_msg":
        awaiting_input[user_id] = "greeting_message"
        await query.edit_message_text(
            "👋 *Greeting Message Set Karo*\n\n"
            f"Current: _{config['greeting_message']}_\n\n"
            "Naya greeting message type karo 👇\n"
            "(Cancel karne ke liye /settings likho)",
            parse_mode="Markdown"
        )

    elif data == "set_schedule":
        awaiting_input[user_id] = "schedule_time"
        await query.edit_message_text(
            "⏰ *Schedule Time Set Karo*\n\n"
            f"Current: *{config['offline_from']}* se *{config['offline_until']}* tak offline\n\n"
            "Format mein likho: `22:00-08:00`\n"
            "(matlab 10 PM se 8 AM tak offline)\n\n"
            "(Cancel ke liye /settings)",
            parse_mode="Markdown"
        )

    elif data == "set_language":
        await query.edit_message_text(
            f"🌐 *Language Choose Karo*\n\nCurrent: *{config['language'].title()}*",
            parse_mode="Markdown",
            reply_markup=language_keyboard()
        )

    elif data.startswith("lang_"):
        lang = data.replace("lang_", "")
        config["language"] = lang
        save_user_config(user_id, config)
        await query.edit_message_text(
            f"✅ Language: *{lang.title()}* set ho gaya!\n\nSettings 👇",
            parse_mode="Markdown",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "view_stats":
        stats = config.get("stats", {})
        seen = len(config.get("seen_users", []))
        await query.edit_message_text(
            f"📊 *Your Stats*\n\n"
            f"Total Messages: *{stats.get('total_messages', 0)}*\n"
            f"Today: *{stats.get('messages_today', 0)}*\n"
            f"Unique Senders: *{seen}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Wapas", callback_data="back_settings")]])
        )

    elif data == "reset_history":
        keys_to_del = [k for k in chat_histories if k.startswith(str(user_id))]
        for k in keys_to_del:
            del chat_histories[k]
        await query.edit_message_text(
            "✅ Saari chat history clear!\n\nSettings 👇",
            reply_markup=main_settings_keyboard(config)
        )

    elif data == "back_settings":
        await query.edit_message_text(
            "⚙️ *Settings Panel*\n\nKoi bhi setting badlo 👇",
            parse_mode="Markdown",
            reply_markup=main_settings_keyboard(config)
        )

# ─── Handle "awaiting input" in DMs ───────────────────────
async def handle_regular_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    # If user is setting something
    if user_id in awaiting_input:
        setting = awaiting_input.pop(user_id)
        config = get_user_config(user_id)

        if setting == "away_message":
            config["away_message"] = user_text
            save_user_config(user_id, config)
            await message.reply_text(
                f"✅ Away message set!\n\n_{user_text}_\n\n/settings se wapas jao",
                parse_mode="Markdown"
            )
        elif setting == "greeting_message":
            config["greeting_message"] = user_text
            save_user_config(user_id, config)
            await message.reply_text(
                f"✅ Greeting message set!\n\n_{user_text}_\n\n/settings se wapas jao",
                parse_mode="Markdown"
            )
        elif setting == "schedule_time":
            try:
                parts = user_text.split("-")
                config["offline_from"]  = parts[0].strip()
                config["offline_until"] = parts[1].strip()
                save_user_config(user_id, config)
                await message.reply_text(
                    f"✅ Schedule set: *{config['offline_from']}* se *{config['offline_until']}* tak offline!\n\n/settings",
                    parse_mode="Markdown"
                )
            except Exception:
                await message.reply_text("❌ Format galat hai. Example: `22:00-08:00`\n\nDobara try karo.", parse_mode="Markdown")
        return

    # Normal command/message handling
    config = get_user_config(user_id)

    if user_text.startswith("/start"):
        await cmd_start(update, context)
    elif user_text.startswith("/settings"):
        await cmd_settings(update, context)
    elif user_text.startswith("/status"):
        await cmd_status(update, context)
    elif user_text.startswith("/stats"):
        await cmd_stats(update, context)
    elif user_text.startswith("/offline"):
        await cmd_offline(update, context)
    elif user_text.startswith("/online"):
        await cmd_online(update, context)
    elif user_text.startswith("/reset"):
        await cmd_reset(update, context)
    elif user_text.startswith("/admin"):
        await cmd_admin(update, context)
    else:
        # Testing in DM — AI reply
        if config.get("ai_enabled", True):
            await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
            reply = get_ai_reply(user_id, user_id, user_text, config.get("language", "hinglish"))
            await message.reply_text(reply)
        else:
            await message.reply_text("AI reply is OFF. /settings se ON karo.")

# ─── Business Connection ──────────────────────────────────
async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bc = update.business_connection
    if bc.is_enabled:
        logger.info(f"Business connected: {bc.user.first_name} ({bc.user.id})")
        # Auto-create config for this user
        get_user_config(bc.user.id)
    else:
        logger.info(f"Business disconnected: {bc.user.first_name} ({bc.user.id})")

# ─── Business Message (THE MAIN ONE) ──────────────────────
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if not message or not message.text:
        return

    owner_id  = message.business_connection_id  # connection ID (used as owner key alternative)
    sender_id = message.from_user.id
    sender_name = message.from_user.first_name or "User"
    user_text = message.text.strip()

    # Try to get owner from connection (fallback to ADMIN_USER_ID if set)
    # In business bots, we use ADMIN_USER_ID as the "owner" config
    effective_owner_id = ADMIN_USER_ID if ADMIN_USER_ID else sender_id
    config = get_user_config(effective_owner_id)

    logger.info(f"Business msg from {sender_name}({sender_id}): {user_text[:60]}")

    # Stats update
    config = update_stats(config)

    # Is this a new user?
    is_new_user = sender_id not in config.get("seen_users", [])
    if is_new_user and sender_id not in config.get("seen_users", []):
        config.setdefault("seen_users", []).append(sender_id)

    save_user_config(effective_owner_id, config)

    # Typing indicator
    try:
        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="typing",
            business_connection_id=message.business_connection_id,
        )
    except Exception:
        pass

    # ── Decide what to reply ──────────────────────────────

    # 1) OFFLINE / AWAY MODE
    if should_send_away(config):
        away_msg = config.get("away_message", DEFAULT_USER_CONFIG["away_message"])
        # Add schedule info if applicable
        if is_scheduled_offline(config):
            tz = pytz.timezone(TIMEZONE)
            away_msg += f"\n\n⏰ Main {config['offline_until']} baje online ho jaunga!"
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=away_msg,
            business_connection_id=message.business_connection_id,
            reply_to_message_id=message.message_id,
        )
        return

    # 2) FIRST-TIME GREETING
    if is_new_user and config.get("first_msg_greeting", True):
        greeting = config.get("greeting_message", DEFAULT_USER_CONFIG["greeting_message"])
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=greeting,
            business_connection_id=message.business_connection_id,
        )
        # Small delay then AI reply
        await asyncio.sleep(1)

    # 3) AI REPLY (if enabled)
    if config.get("ai_enabled", True):
        reply = get_ai_reply(
            effective_owner_id,
            sender_id,
            user_text,
            config.get("language", "hinglish")
        )
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=reply,
            business_connection_id=message.business_connection_id,
            reply_to_message_id=message.message_id,
        )
    # (If AI is off and not offline, bot stays silent — normal behavior)

# ─── Main ─────────────────────────────────────────────────
def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN .env mein set karo!")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY .env mein set karo!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Business handlers
    app.add_handler(BusinessConnectionHandler(handle_business_connection))
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))

    # Settings UI (inline buttons)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Regular DM handlers
    app.add_handler(MessageHandler(filters.ALL & ~filters.UpdateType.BUSINESS_MESSAGE, handle_regular_message))

    logger.info("🚀 Ultra Advanced Bot chal raha hai! Ctrl+C se band karo.")
    logger.info(f"Admin ID: {ADMIN_USER_ID or 'Not set'} | Timezone: {TIMEZONE}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()