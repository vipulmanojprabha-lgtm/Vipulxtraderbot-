import asyncio
import logging
import os
import sqlite3
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "6054005253"))
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = "bot_data.db"

DEFAULT_SETTINGS = {
    "channel_id": "",
    "auto_approve": "off",  # "on" ya "off"
    "register_url": "https://www.khelv19.com/#/register?invitationCode=89414517627",
    "panel_url": "https://example.com/panel",
    "rejoin_url": "https://t.me/Myselfvipulkumar",
    "owner_username": "Myselfvipulkumar",
    "setup_video_url": "https://t.me/Myselfvipulkumar",
    "welcome_text": (
        "🔵 APKA REQUEST AYA HAI DEAR ❤️\n\n"
        "APKA REQUEST JALD HI ACCEPT HO JAYEGA. TAB TAK AAP NICHE DIYE GAYE "
        "REGISTRATION LINK PAR CLICK KARKE REGISTRATION KAR LO.\n\n"
        "AUR JALD HI LIVE AAUNGA, TO LIVE MEIN ZAROOR AANA! 🔥❤️"
    ),
    "leave_text": (
        "🔵 Aapne channel leave kar diya hai! Dobara join karne ke liye neeche diye gaye "
        "Rejoin ya Contact option par click karein."
    ),
}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_slots (
            slot_num INTEGER PRIMARY KEY,
            url TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS known_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
        """
    )

    for k, v in DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )

    cur.execute(
        "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
        (OWNER_ID,),
    )

    for s in (1, 2, 3):
        cur.execute(
            "INSERT OR IGNORE INTO invite_slots (slot_num, url) VALUES (?, ?)",
            (s, ""),
        )

    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_all_admin_ids() -> list[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    rows = [int(r[0]) for r in cur.fetchall()]
    conn.close()
    if OWNER_ID not in rows:
        rows.append(OWNER_ID)
    return rows


def record_user(user) -> None:
    if not user:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO known_users (user_id, username, first_name) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "username = excluded.username, first_name = excluded.first_name",
        (user.id, user.username or "", user.first_name or ""),
    )
    conn.commit()
    conn.close()


def is_known_user(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM known_users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_all_slots() -> list[tuple[int, str]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT slot_num, url FROM invite_slots ORDER BY slot_num ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_primary_invite_link() -> str:
    for _, url in get_all_slots():
        if url.strip():
            return url.strip()
    return get_setting("rejoin_url")


def set_slot_url(slot_num: int, url: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO invite_slots (slot_num, url) VALUES (?, ?) "
        "ON CONFLICT(slot_num) DO UPDATE SET url = excluded.url",
        (slot_num, url.strip()),
    )
    conn.commit()
    conn.close()


def add_new_slot() -> int:
    slots = get_all_slots()
    next_num = (slots[-1][0] + 1) if slots else 1
    set_slot_url(next_num, "")
    return next_num


def blue_bottom_keyboard(include_rejoin: bool = False) -> ReplyKeyboardMarkup:
    owner_username = get_setting("owner_username").lstrip("@") or "Myselfvipulkumar"
    rows = [
        [KeyboardButton("🟦 REGISTER NOW"), KeyboardButton("🟦 OPEN VIP PANEL")],
        [
            KeyboardButton(f"🟦 CHAT WITH @{owner_username}"),
            KeyboardButton("🟦 WATCH SETUP VIDEO"),
        ],
    ]
    if include_rejoin:
        rows.append([KeyboardButton("🟦 REJOIN CHANNEL")])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        input_field_placeholder="🔵 Blue Menu se option chunein...",
    )


def build_welcome_message() -> str:
    reg_url = get_setting("register_url")
    panel_url = get_setting("panel_url")
    owner_username = get_setting("owner_username").lstrip("@") or "Myselfvipulkumar"
    invite_url = get_primary_invite_link()
    header_text = get_setting("welcome_text")

    return (
        f"{header_text}\n\n"
        f"📢 Join Channel Link:\n{invite_url}\n\n"
        f"🔴 Register Link:\n{reg_url}\n\n"
        f"🔥 VIP Panel Link:\n{panel_url}\n\n"
        f"💬 Chat Link:\nhttps://t.me/{owner_username}"
    )


def build_leave_message() -> str:
    reg_url = get_setting("register_url")
    panel_url = get_setting("panel_url")
    owner_username = get_setting("owner_username").lstrip("@") or "Myselfvipulkumar"
    rejoin_url = get_setting("rejoin_url")
    header_text = get_setting("leave_text")

    return (
        f"{header_text}\n\n"
        f"🔁 Rejoin Link:\n{rejoin_url}\n\n"
        f"🔴 Register Link:\n{reg_url}\n\n"
        f"🔥 VIP Panel Link:\n{panel_url}\n\n"
        f"💬 Chat Link:\nhttps://t.me/{owner_username}"
    )


def settings_panel_keyboard() -> InlineKeyboardMarkup:
    auto_state = get_setting("auto_approve").upper()
    rows = [
        [InlineKeyboardButton("🔵 Set Channel ID", callback_data="ask_channel_id")],
        [
            InlineKeyboardButton(
                "🔵 Clear Channel Filter (All Channels)", callback_data="clear_channel_id"
            )
        ],
        [InlineKeyboardButton("🔵 Set Invite Link", callback_data="menu_invite_links")],
        [InlineKeyboardButton("🔵 Set Register Link", callback_data="ask_register_url")],
        [InlineKeyboardButton("🔵 Set Panel Link", callback_data="ask_panel_url")],
        [InlineKeyboardButton("🔵 Set Rejoin Link", callback_data="ask_rejoin_url")],
        [
            InlineKeyboardButton(
                f"🔵 Auto Approval: {auto_state}",
                callback_data="toggle_auto_approve",
            )
        ],
        [InlineKeyboardButton("🔵 Edit Welcome Message", callback_data="ask_welcome_text")],
        [InlineKeyboardButton("🔵 Edit Leave Message", callback_data="ask_leave_text")],
    ]
    return InlineKeyboardMarkup(rows)


def invite_slots_keyboard() -> InlineKeyboardMarkup:
    slots = get_all_slots()
    rows: list[list[InlineKeyboardButton]] = []

    for slot_num, url in slots:
        badge = "✅" if url else "🔵"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{badge} Set Invite Link {slot_num}",
                    callback_data=f"set_slot_{slot_num}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("🔵 Add More Invite Slot", callback_data="add_slot")])
    rows.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data="back_settings")])
    return InlineKeyboardMarkup(rows)


def format_settings_status() -> str:
    ch_id = get_setting("channel_id") or "All Channels (No Filter)"
    auto_app = get_setting("auto_approve").upper()
    reg_url = get_setting("register_url")
    panel_url = get_setting("panel_url")
    rejoin_url = get_setting("rejoin_url")
    slots = get_all_slots()
    active_slots = sum(1 for _, u in slots if u.strip())

    return (
        "🔵 **ADMIN SETTINGS CONTROL**\n\n"
        f"• **Channel ID:** `{ch_id}`\n"
        f"• **Auto Approval:** `{auto_app}`\n"
        f"• **Invite Slots Set:** `{active_slots}/{len(slots)}`\n"
        f"• **Register Link:** `{reg_url}`\n"
        f"• **Panel Link:** `{panel_url}`\n"
        f"• **Rejoin Link:** `{rejoin_url}`\n\n"
        "Kisi bhi button par click karke value badlein:"
    )


def format_admin_start_info(bot_username: str) -> str:
    return (
        "🔵 **VIPUL X TRADER BOT (ADMIN)**\n\n"
        f"• **Status:** Active\n"
        f"• **Auto Approval:** {get_setting('auto_approve').upper()}\n"
        f"• **User Funnel Link:** `https://t.me/{bot_username}?start=vip`\n\n"
        "Settings badalne ke liye `/settings` likh kar bhejein."
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    record_user(user)
    context.user_data.pop("awaiting", None)

    if is_admin(user.id):
        bot_username = context.bot.username or "YourBot"
        await update.message.reply_text(
            text=format_admin_start_info(bot_username),
            parse_mode="Markdown",
            reply_markup=blue_bottom_keyboard(include_rejoin=False),
        )
        return

    # Normal user jab /start ya ?start=vip se aaye to turant Welcome + Channel + Panel links bhejein
    await update.message.reply_text(
        text=build_welcome_message(),
        reply_markup=blue_bottom_keyboard(include_rejoin=False),
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    record_user(user)
    context.user_data.pop("awaiting", None)

    if not is_admin(user.id):
        await update.message.reply_text(
            "🔵 Yeh command sirf Admin ke liye hai.",
            reply_markup=blue_bottom_keyboard(include_rejoin=False),
        )
        return

    await update.message.reply_text(
        text=format_settings_status(),
        parse_mode="Markdown",
        reply_markup=settings_panel_keyboard(),
    )


async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("🔵 Yeh panel sirf Admin ke liye hai.")
        return

    data = query.data or ""

    if data == "back_settings":
        context.user_data.pop("awaiting", None)
        await query.edit_message_text(
            text=format_settings_status(),
            parse_mode="Markdown",
            reply_markup=settings_panel_keyboard(),
        )
        return

    if data == "clear_channel_id":
        set_setting("channel_id", "")
        context.user_data.pop("awaiting", None)
        await query.edit_message_text(
            text=format_settings_status(),
            parse_mode="Markdown",
            reply_markup=settings_panel_keyboard(),
        )
        return

    if data == "menu_invite_links":
        slots = get_all_slots()
        lines = ["🔵 **SET INVITE LINK MENU**\n"]
        for slot_num, url in slots:
            lines.append(f"• Slot {slot_num}: {url if url else '_Khali_'}")
        lines.append("\nKisi bhi button par click karke link bhejein:")
        await query.edit_message_text(
            text="\n".join(lines),
            parse_mode="Markdown",
            reply_markup=invite_slots_keyboard(),
        )
        return

    if data == "add_slot":
        new_num = add_new_slot()
        slots = get_all_slots()
        lines = [f"✅ Naya slot **Set Invite Link {new_num}** add ho gaya!\n"]
        for slot_num, url in slots:
            lines.append(f"• Slot {slot_num}: {url if url else '_Khali_'}")
        await query.edit_message_text(
            text="\n".join(lines),
            parse_mode="Markdown",
            reply_markup=invite_slots_keyboard(),
        )
        return

    if data.startswith("set_slot_"):
        slot_num = int(data.replace("set_slot_", ""))
        context.user_data["awaiting"] = f"invite_slot:{slot_num}"
        await query.edit_message_text(
            text=(
                f"🔵 **Set Invite Link {slot_num}** ke liye naya Telegram invite link bhejein "
                "(jaise `https://t.me/+AbCdEfGh123`):"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Cancel", callback_data="menu_invite_links")]]
            ),
        )
        return

    if data == "toggle_auto_approve":
        current = get_setting("auto_approve")
        new_val = "on" if current != "on" else "off"
        set_setting("auto_approve", new_val)
        await query.edit_message_text(
            text=format_settings_status(),
            parse_mode="Markdown",
            reply_markup=settings_panel_keyboard(),
        )
        return

    prompts = {
        "ask_channel_id": (
            "channel_id",
            "🔵 Apne main channel ki **Channel ID** bhejein (jaise `-1001234567890`):",
        ),
        "ask_register_url": ("register_url", "🔵 Naya **Register Link** bhejein:"),
        "ask_panel_url": ("panel_url", "🔵 Naya **Panel Link** bhejein:"),
        "ask_rejoin_url": ("rejoin_url", "🔵 Naya **Rejoin Link** bhejein:"),
        "ask_welcome_text": ("welcome_text", "🔵 Naya **Welcome Message** likh kar bhejein:"),
        "ask_leave_text": ("leave_text", "🔵 Naya **Leave Message** likh kar bhejein:"),
    }

    if data in prompts:
        key_name, prompt_msg = prompts[data]
        context.user_data["awaiting"] = f"setting:{key_name}"
        await query.edit_message_text(
            text=prompt_msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back to Settings", callback_data="back_settings")]]
            ),
        )


async def handle_bottom_or_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    record_user(user)
    text_val = (update.message.text or "").strip()
    if not text_val:
        return

    if is_admin(user.id):
        awaiting = context.user_data.get("awaiting")
        if awaiting:
            if awaiting.startswith("invite_slot:"):
                slot_num = int(awaiting.split(":", 1)[1])
                set_slot_url(slot_num, text_val)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text(
                    f"✅ **Set Invite Link {slot_num}** save ho gaya:\n`{text_val}`",
                    parse_mode="Markdown",
                    reply_markup=invite_slots_keyboard(),
                )
                return

            if awaiting.startswith("setting:"):
                key_name = awaiting.split(":", 1)[1]
                set_setting(key_name, text_val)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text(
                    f"✅ `{key_name}` update ho gaya!",
                    parse_mode="Markdown",
                    reply_markup=settings_panel_keyboard(),
                )
                return

    owner_username = get_setting("owner_username").lstrip("@") or "Myselfvipulkumar"

    if text_val == "🟦 REGISTER NOW":
        await update.message.reply_text(
            f"🔵 **REGISTER NOW**\n{get_setting('register_url')}",
            parse_mode="Markdown",
            reply_markup=blue_bottom_keyboard(),
        )
    elif text_val == "🟦 OPEN VIP PANEL":
        await update.message.reply_text(
            f"🔵 **OPEN VIP PANEL**\n{get_setting('panel_url')}",
            parse_mode="Markdown",
            reply_markup=blue_bottom_keyboard(),
        )
    elif text_val == f"🟦 CHAT WITH @{owner_username}":
        await update.message.reply_text(
            f"🔵 **CHAT WITH ME**\nhttps://t.me/{owner_username}",
            reply_markup=blue_bottom_keyboard(),
        )
    elif text_val == "🟦 WATCH SETUP VIDEO":
        await update.message.reply_text(
            f"🔵 **WATCH SETUP VIDEO**\n{get_setting('setup_video_url')}",
            parse_mode="Markdown",
            reply_markup=blue_bottom_keyboard(),
        )
    elif text_val == "🟦 REJOIN CHANNEL":
        await update.message.reply_text(
            f"🔵 **REJOIN CHANNEL**\n{get_setting('rejoin_url')}",
            reply_markup=blue_bottom_keyboard(include_rejoin=True),
        )


async def notify_admins_member_left(
    context: ContextTypes.DEFAULT_TYPE, user, chat_title: str, chat_id: int, dm_status: str
) -> None:
    username_str = f"@{user.username}" if getattr(user, "username", None) else "No Username"
    full_name = (
        getattr(user, "full_name", None)
        or getattr(user, "first_name", None)
        or "User"
    )
    alert_text = (
        "🔴 **MEMBER LEFT CHANNEL**\n\n"
        f"• **Name:** {full_name}\n"
        f"• **Username:** {username_str}\n"
        f"• **User ID:** `{user.id}`\n"
        f"• **Channel:** {chat_title} (`{chat_id}`)\n"
        f"• **User DM:** {dm_status}"
    )

    for admin_id in get_all_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=alert_text,
                parse_mode="Markdown",
            )
        except TelegramError as exc:
            logger.warning("Failed to send leave alert to admin %s: %s", admin_id, exc.message)


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    req = update.chat_join_request
    if not req or not req.from_user:
        return

    configured_ch = get_setting("channel_id").strip()
    if configured_ch and str(req.chat.id) != configured_ch:
        return

    user = req.from_user
    welcome_msg = build_welcome_message()
    dm_status = "Sent ✅"

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=welcome_msg,
            reply_markup=blue_bottom_keyboard(include_rejoin=False),
        )
        record_user(user)
    except Forbidden:
        dm_status = "Not Started Bot (User must tap Start first)"
    except TelegramError as exc:
        dm_status = f"Failed ({exc.message})"

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                "🟢 **NEW JOIN REQUEST**\n\n"
                f"• **User:** {user.full_name} (`{user.id}`)\n"
                f"• **Channel:** {req.chat.title or 'Channel'} (`{req.chat.id}`)\n"
                f"• **Welcome DM:** {dm_status}"
            ),
            parse_mode="Markdown",
        )
    except TelegramError:
        pass

    if get_setting("auto_approve") == "on":
        try:
            await context.bot.approve_chat_join_request(
                chat_id=req.chat.id,
                user_id=user.id,
            )
        except TelegramError as exc:
            logger.warning("Failed to auto-approve join request for %s: %s", user.id, exc.message)


async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    if not cmu:
        return

    configured_ch = get_setting("channel_id").strip()
    if configured_ch and str(cmu.chat.id) != configured_ch:
        return

    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    target_user = cmu.new_chat_member.user

    if not target_user or target_user.is_bot:
        return

    if old_status in ("member", "administrator", "restricted") and new_status in (
        "left",
        "kicked",
    ):
        chat_title = cmu.chat.title or "Channel"
        leave_msg = build_leave_message()

        try:
            await context.bot.send_message(
                chat_id=target_user.id,
                text=leave_msg,
                reply_markup=blue_bottom_keyboard(include_rejoin=True),
            )
            dm_status = "Sent ✅"
        except Forbidden:
            dm_status = "Not Started Bot (User must tap Start first)"
        except TelegramError as exc:
            dm_status = f"Failed ({exc.message})"

        await notify_admins_member_left(
            context, target_user, chat_title, cmu.chat.id, dm_status
        )


async def start_health_server() -> asyncio.base_events.Server:
    async def handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.read(1024)
            body = b"OK"
            headers = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            writer.write(headers)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "0.0.0.0", PORT)
    logger.info("Health server listening on 0.0.0.0:%s", PORT)
    return server


async def post_init(application: Application) -> None:
    application.bot_data["health_server"] = await start_health_server()
    try:
        await application.bot.delete_webhook(drop_pending_updates=False)
    except TelegramError:
        pass


async def post_shutdown(application: Application) -> None:
    server = application.bot_data.get("health_server")
    if server:
        server.close()
        await server.wait_closed()


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing in environment / .env file.")

    init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(ChatJoinRequestHandler(on_join_request), group=-1)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bottom_or_text))
    app.add_handler(
        ChatMemberHandler(on_chat_member_update, ChatMemberHandler.ANY_CHAT_MEMBER)
    )

    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "chat_join_request",
            "chat_member",
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
