"""
bot.py — Telegram bot for delivering newsletters and collecting feedback.

Sends the newsletter in sections with inline reaction buttons.
Feedback is collected by fetching pending Telegram updates before each generation
(no persistent listener needed — Telegram stores updates for 24h).
"""

import asyncio
import html
import json
import os
import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from store import (
    add_interest,
    add_researcher,
    add_topic,
    list_config_summary,
    load_telegram_state,
    record_length_feedback,
    record_more_request,
    record_reaction,
    remove_interest,
    remove_researcher,
    remove_topic,
    save_telegram_state,
)

# Section labels for display
SECTION_NAMES = {
    "curiosity": "💡 Deep Curiosity",
    "research": "📄 Research Spotlight",
    "quick_bites": "⚡ Quick Bites",
    "thesis_corner": "🎯 Your Research Corner",
    "recap": "🔄 Weekly Recap",
}


def get_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN env variable (from @BotFather)"
        )
    return token


def get_chat_id() -> int | None:
    """Get stored chat ID, or None if not yet registered."""
    state = load_telegram_state()
    cid = state.get("chat_id")
    return int(cid) if cid else None


def _md_to_telegram_html(md_text: str) -> str:
    """Convert basic markdown to Telegram HTML.
    Handles bold, italic, inline code, links, and blockquotes.
    """
    text = md_text

    # Escape HTML special chars first (but preserve our markers)
    text = html.escape(text)

    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # Italic: *text* or _text_ (but not inside words with underscores)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'<i>\1</i>', text)

    # Inline code: `text`
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    # Links: [text](url) — need to unescape the URL
    def fix_link(m):
        label = m.group(1)
        url = html.unescape(m.group(2))
        return f'<a href="{url}">{label}</a>'
    text = re.sub(r'\[(.+?)\]\((.+?)\)', fix_link, text)

    # Blockquotes: > text
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('&gt;'):
            content = stripped[4:].strip()
            # Remove emoji at start of blockquote
            result.append(f"  {content}")
        else:
            result.append(line)
    text = '\n'.join(result)

    return text


def _section_reaction_keyboard(date: str, section: str) -> InlineKeyboardMarkup:
    """Inline keyboard for reacting to a section."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔥 Love it", callback_data=f"react:{date}:{section}:love"),
            InlineKeyboardButton("😐 Meh", callback_data=f"react:{date}:{section}:meh"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"react:{date}:{section}:skip"),
        ],
        [
            InlineKeyboardButton("🔁 More of this tomorrow", callback_data=f"more:{date}:{section}"),
        ],
    ])


def _length_keyboard(date: str) -> InlineKeyboardMarkup:
    """Inline keyboard for length feedback (sent after last section)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📏 Shorter", callback_data=f"length:{date}:shorter"),
            InlineKeyboardButton("👌 Perfect", callback_data=f"length:{date}:perfect"),
            InlineKeyboardButton("📐 Longer", callback_data=f"length:{date}:longer"),
        ],
    ])


async def send_newsletter(date: str, sections: dict[str, str], paper_topics: list[str]):
    """Send newsletter to Telegram in sections with feedback buttons.

    Args:
        date: Date string (YYYY-MM-DD)
        sections: Dict mapping section key to markdown content
        paper_topics: Short topic descriptions for 'more' requests
    """
    chat_id = get_chat_id()
    if not chat_id:
        print("⚠️  No chat ID registered. Send /start to the bot first.")
        return

    token = get_bot_token()
    app = Application.builder().token(token).build()

    async with app:
        # Header
        weekday = __import__("datetime").datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        header = f"<b>🔬 Daily Science — {weekday}, {date}</b>\n<i>~7 min to feed your curiosity</i>"
        await app.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)

        # Send each section
        section_order = ["curiosity", "research", "quick_bites", "thesis_corner"]
        topic_map = {}
        for i, key in enumerate(section_order):
            if key not in sections:
                continue

            name = SECTION_NAMES.get(key, key)
            content = sections[key]

            # Store topic for 'more' callback
            topic = paper_topics[i] if i < len(paper_topics) else key
            topic_map[key] = topic

            # Convert to Telegram HTML
            text_html = _md_to_telegram_html(content)
            message = f"<b>{name}</b>\n\n{text_html}"

            # Telegram has a 4096 char limit per message
            if len(message) > 4000:
                # Split at a natural break
                mid = len(message) // 2
                break_point = message.rfind('\n', 0, mid)
                if break_point == -1:
                    break_point = mid

                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message[:break_point],
                    parse_mode=ParseMode.HTML,
                )
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message[break_point:],
                    parse_mode=ParseMode.HTML,
                    reply_markup=_section_reaction_keyboard(date, key),
                )
            else:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_section_reaction_keyboard(date, key),
                )

            await asyncio.sleep(0.5)  # slight delay between sections

        # Length feedback after all sections
        await app.bot.send_message(
            chat_id=chat_id,
            text="<i>How was today's length?</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_length_keyboard(date),
        )

    # Store topic map for more-request resolution
    state = load_telegram_state()
    state["last_topics"] = topic_map
    state["last_date"] = date
    save_telegram_state(state)


async def publish_to_channel(date: str, sections: dict[str, str], channel_username: str):
    """Post the newsletter to a public Telegram channel (no reaction buttons).

    Args:
        date: Date string (YYYY-MM-DD)
        sections: Dict mapping section key to markdown content
        channel_username: Channel username (with or without @)
    """
    token = get_bot_token()
    app = Application.builder().token(token).build()
    channel_id = f"@{channel_username.lstrip('@')}"

    async with app:
        weekday = __import__("datetime").datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        header = f"<b>🔬 Daily Science — {weekday}, {date}</b>\n<i>~7 min to feed your curiosity</i>"
        await app.bot.send_message(chat_id=channel_id, text=header, parse_mode=ParseMode.HTML)

        section_order = ["curiosity", "research", "quick_bites", "thesis_corner"]
        for key in section_order:
            if key not in sections:
                continue
            name = SECTION_NAMES.get(key, key)
            text_html = _md_to_telegram_html(sections[key])
            message = f"<b>{name}</b>\n\n{text_html}"

            if len(message) > 4000:
                mid = len(message) // 2
                bp = message.rfind('\n', 0, mid)
                if bp == -1:
                    bp = mid
                await app.bot.send_message(chat_id=channel_id, text=message[:bp], parse_mode=ParseMode.HTML)
                await app.bot.send_message(chat_id=channel_id, text=message[bp:], parse_mode=ParseMode.HTML)
            else:
                await app.bot.send_message(chat_id=channel_id, text=message, parse_mode=ParseMode.HTML)

            await asyncio.sleep(0.5)


# ── Callback handlers (run by the listener) ─────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("react:"):
        _, date, section, reaction = data.split(":")
        record_reaction(date, section, reaction)
        label = {"love": "🔥", "meh": "😐", "skip": "⏭"}.get(reaction, "✓")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"{label} Got it!", quote=True)

    elif data.startswith("more:"):
        _, date, section = data.split(":")
        state = load_telegram_state()
        topic = state.get("last_topics", {}).get(section, section)
        record_more_request(date, section, topic)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("🔁 I'll go deeper on this tomorrow!", quote=True)

    elif data.startswith("length:"):
        _, date, preference = data.split(":")
        record_length_feedback(date, preference)
        label = {"shorter": "📏", "perfect": "👌", "longer": "📐"}.get(preference, "✓")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"{label} Noted for tomorrow!", quote=True)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command — register chat ID."""
    chat_id = update.effective_chat.id
    state = load_telegram_state()
    state["chat_id"] = chat_id
    save_telegram_state(state)
    await update.message.reply_text(
        "🔬 Your Botletter is set up!\n\n"
        "You'll get a daily newsletter with:\n"
        "💡 A deep curiosity topic\n"
        "📄 A real research paper explained\n"
        "⚡ Quick fascinating bites\n"
        "🎯 Something for your research area\n\n"
        "<b>Manage your interests:</b>\n"
        "/add_interest quantum biology\n"
        "/add_topic KV cache eviction\n"
        "/add_researcher Ion Stoica @ UC Berkeley\n"
        "/remove_interest materials science\n"
        "/remove_topic serverless\n"
        "/remove_researcher Name\n"
        "/config — see all current settings\n"
        "/history — past issues",
        parse_mode=ParseMode.HTML,
    )
    print(f"✅ Telegram chat registered: {chat_id}")


async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /history — list past newsletters."""
    output_dir = __import__("pathlib").Path(__file__).parent / "output"
    if not output_dir.exists():
        await update.message.reply_text("No newsletters yet!")
        return

    files = sorted(output_dir.glob("*.md"), reverse=True)[:10]
    if not files:
        await update.message.reply_text("No newsletters yet!")
        return

    lines = ["<b>📚 Recent Issues</b>\n"]
    for f in files:
        date = f.stem
        lines.append(f"• {date}")
    lines.append(f"\n<i>{len(list(output_dir.glob('*.md')))} total issues</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def handle_add_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /add_interest quantum biology")
        return
    result = add_interest(text)
    await update.message.reply_text(result)


async def handle_add_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /add_topic KV cache eviction")
        return
    result = add_topic(text)
    await update.message.reply_text(result)


async def handle_add_researcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /add_researcher Ion Stoica @ UC Berkeley")
        return
    result = add_researcher(text)
    await update.message.reply_text(result)


async def handle_remove_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /remove_interest materials science")
        return
    result = remove_interest(text)
    await update.message.reply_text(result)


async def handle_remove_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /remove_topic serverless")
        return
    result = remove_topic(text)
    await update.message.reply_text(result)


async def handle_remove_researcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("✏️ Usage: /remove_researcher Name")
        return
    result = remove_researcher(text)
    await update.message.reply_text(result)


async def handle_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = list_config_summary()
    await update.message.reply_text(summary, parse_mode=ParseMode.HTML)


def run_listener():
    """Run the Telegram bot in polling mode to listen for feedback."""
    token = get_bot_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("add_interest", handle_add_interest))
    app.add_handler(CommandHandler("add_topic", handle_add_topic))
    app.add_handler(CommandHandler("add_researcher", handle_add_researcher))
    app.add_handler(CommandHandler("remove_interest", handle_remove_interest))
    app.add_handler(CommandHandler("remove_topic", handle_remove_topic))
    app.add_handler(CommandHandler("remove_researcher", handle_remove_researcher))
    app.add_handler(CommandHandler("config", handle_config))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🤖 Telegram bot listening for feedback... (Ctrl+C to stop)")
    app.run_polling()


def _process_callback_data(data: str):
    """Process a single callback data string and update the store."""
    if data.startswith("react:"):
        _, date, section, reaction = data.split(":")
        record_reaction(date, section, reaction)
    elif data.startswith("more:"):
        _, date, section = data.split(":")
        state = load_telegram_state()
        topic = state.get("last_topics", {}).get(section, section)
        record_more_request(date, section, topic)
    elif data.startswith("length:"):
        _, date, preference = data.split(":")
        record_length_feedback(date, preference)


_COMMAND_MAP = {
    "/add_interest": add_interest,
    "/add_topic": add_topic,
    "/add_researcher": add_researcher,
    "/remove_interest": remove_interest,
    "/remove_topic": remove_topic,
    "/remove_researcher": remove_researcher,
}


async def _process_text_command(bot: Bot, chat_id: int, text: str):
    """Process a text command from Telegram and send a reply."""
    text = text.strip()

    if text == "/config":
        await bot.send_message(chat_id=chat_id, text=list_config_summary(), parse_mode=ParseMode.HTML)
        return True

    if text.startswith("/start"):
        state = load_telegram_state()
        state["chat_id"] = chat_id
        save_telegram_state(state)
        return True

    for cmd, func in _COMMAND_MAP.items():
        if text.startswith(cmd):
            arg = text[len(cmd):].strip()
            if arg:
                result = func(arg)
            else:
                result = f"✏️ Usage: {cmd} <value>"
            await bot.send_message(chat_id=chat_id, text=result)
            return True

    return False


async def _fetch_feedback_async():
    """Fetch all pending Telegram updates, process feedback, clear the queue."""
    token = get_bot_token()
    bot = Bot(token=token)

    processed = 0
    offset = None

    while True:
        updates = await bot.get_updates(offset=offset, timeout=1)
        if not updates:
            break
        for update in updates:
            offset = update.update_id + 1
            # Handle callback queries (button presses)
            if update.callback_query:
                data = update.callback_query.data
                _process_callback_data(data)
                # Answer the callback so Telegram stops showing a spinner
                await update.callback_query.answer()
                # Remove inline keyboard from the message
                try:
                    await update.callback_query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass  # message might be too old to edit
                processed += 1
            # Handle text commands (/add_interest, /add_topic, /config etc.)
            elif update.message and update.message.text:
                text = update.message.text
                chat_id = update.effective_chat.id
                if text.startswith("/"):
                    await _process_text_command(bot, chat_id, text)
                    processed += 1

    await bot.shutdown()
    return processed


def fetch_pending_feedback() -> int:
    """Sync wrapper: fetch and process all pending Telegram feedback.
    Returns the number of updates processed."""
    return asyncio.run(_fetch_feedback_async())


async def _send_reminder_async(channel_username: str | None = None):
    """Send a short morning reminder to check the newsletter."""
    chat_id = get_chat_id()
    if not chat_id:
        return
    token = get_bot_token()
    bot = Bot(token=token)

    weekday = __import__("datetime").datetime.now().strftime("%A")
    text = (
        f"☀️ <b>Good morning!</b>\n\n"
        f"Your {weekday} science feed is waiting above ☝️\n"
        f"<i>~7 min to get smarter on the commute</i>"
    )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    if channel_username:
        channel_id = f"@{channel_username.lstrip('@')}"
        try:
            await bot.send_message(chat_id=channel_id, text=text, parse_mode=ParseMode.HTML)
        except Exception:
            pass  # don't fail the whole reminder if channel post fails

    await bot.shutdown()


def send_reminder(channel_username: str | None = None):
    """Sync wrapper: send morning reminder."""
    asyncio.run(_send_reminder_async(channel_username))
