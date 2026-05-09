import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    BusinessConnectionHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai
from dotenv import load_dotenv

# ─── Load environment variables ───────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Gemini AI setup ──────────────────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    system_instruction=(
        "Tu ek helpful AI assistant hai. "
        "User ke messages ka short, friendly aur useful reply de. "
        "Hinglish (Hindi + English mix) mein baat kar jab tak user kisi aur language mein na likhe. "
        "Kabhi yeh mat batana ki tu AI hai jab tak poocha na jaye."
    ),
)

# ─── In-memory chat history per user ─────────────────────────────────────────
# { user_id: [ {"role": "user"/"model", "parts": ["text"]} ] }
chat_histories: dict[int, list] = {}

MAX_HISTORY = 20  # last 20 messages remember karega


def get_ai_reply(user_id: int, user_message: str) -> str:
    """Gemini se reply lao, chat history ke saath."""
    if user_id not in chat_histories:
        chat_histories[user_id] = []

    history = chat_histories[user_id]

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_message)
        reply = response.text.strip()

        # History update karo
        history.append({"role": "user", "parts": [user_message]})
        history.append({"role": "model", "parts": [reply]})

        # History trimming — sirf last MAX_HISTORY entries rakho
        if len(history) > MAX_HISTORY:
            chat_histories[user_id] = history[-MAX_HISTORY:]

        return reply

    except Exception as e:
        logger.error(f"Gemini error for user {user_id}: {e}")
        return "Sorry yaar, abhi kuch dikkat aa rahi hai. Thodi der baad try karo! 🙏"


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jab user pehli baar bot ko profile se connect kare."""
    bc = update.business_connection
    if bc.is_enabled:
        logger.info(f"Business connection established: user_id={bc.user.id}, name={bc.user.first_name}")
    else:
        logger.info(f"Business connection removed: user_id={bc.user.id}")


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Business chat se aane wale messages handle karo — AI se reply do."""
    message = update.business_message
    if not message or not message.text:
        return  # Photos/videos/stickers ignore karo (sirf text handle karo)

    user_id = message.from_user.id
    user_name = message.from_user.first_name or "User"
    user_text = message.text.strip()

    logger.info(f"Business message from {user_name} ({user_id}): {user_text[:60]}")

    # Typing indicator dikhao
    try:
        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="typing",
            business_connection_id=message.business_connection_id,
        )
    except Exception:
        pass

    # AI reply lo
    reply = get_ai_reply(user_id, user_text)

    # Reply bhejo business connection ke through
    await context.bot.send_message(
        chat_id=message.chat_id,
        text=reply,
        business_connection_id=message.business_connection_id,
        reply_to_message_id=message.message_id,
    )


async def handle_regular_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Normal DM mein bhi bot kaam kare (testing ke liye)."""
    message = update.message
    if not message or not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    # /start command
    if user_text.startswith("/start"):
        await message.reply_text(
            "👋 Hello! Main tumhara AI assistant hoon.\n\n"
            "Telegram Business ke through mujhe apne profile se connect karo:\n"
            "Settings → Chat Automation → Bot add karo\n\n"
            "Uske baad main tumhari taraf se automatically reply karunga! 🤖"
        )
        return

    # /reset command — history clear karo
    if user_text.startswith("/reset"):
        chat_histories.pop(user_id, None)
        await message.reply_text("✅ Chat history reset ho gayi! Fresh start karo.")
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")

    reply = get_ai_reply(user_id, user_text)
    await message.reply_text(reply)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable set nahi hai!")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable set nahi hai!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Business connection handler
    app.add_handler(BusinessConnectionHandler(handle_business_connection))

    # Business messages handler
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.BUSINESS_MESSAGE, handle_business_message))

    # Regular messages handler (DM / testing)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_regular_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_regular_message))

    logger.info("🤖 Bot chal raha hai... Ctrl+C se band karo.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
