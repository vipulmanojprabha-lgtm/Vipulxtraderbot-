import logging
import os
import sqlite3
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# डिफ़ॉल्ट कॉन्फ़िगरेशन (ज़रूरत के अनुसार अपडेट करें)
DEFAULT_CONFIG = {
    "register_url": "https://www.khelv19.com/#/register?invitationCode=89414517627",
    "panel_url": "https://example.com/panel",
    "owner_username": "Myselfvipulkumar",
    "setup_video_url": "https://t.me/Myselfvipulkumar",
    "welcome_text": (
        "APKA REQUEST AYA HAI DEAR ❤️\n\n"
        "APKA REQUEST JALD HI ACCEPT HO JAYEGA. TAB TAK AAP NICHE DIYE GAYE "
        "REGISTRATION LINK PAR CLICK KARKE REGISTRATION KAR LO.\n\n"
        "PANEL KAISE USE KARNA HAI, ISKE LIYE SETUP VIDEO BHI NEECHE HAI.\n"
        "AUR JALD HI LIVE AAUNGA, TO LIVE MEIN ZAROOR AANA! 🔥❤️"
    ),
    "leave_text": (
        "Aapne channel leave kar diya hai! Agar kisi problem ya loss recovery ke liye "
        "wapas judna chahte hain to neeche diye gaye buttons se contact ya rejoin kar sakte hain."
    ),
}

DB_PATH = "bot_data.db"


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
        CREATE TABLE IF NOT EXISTS invite_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE
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

    for k, v in DEFAULT_CONFIG.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )

    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_CONFIG.get(key, "")


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


def add_invite_link(label: str, url: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO invite_links (label, url) VALUES (?, ?)",
            (label.strip(), url.strip()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_invite_links() -> list[tuple[int, str, str]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, label, url FROM invite_links ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_invite_link(link_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM invite_links WHERE id = ?", (link_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def build_action_keyboard(include_rejoin: bool = False) -> InlineKeyboardMarkup:
    register_url = get_setting("register_url")
    panel_url = get_setting("panel_url")
    owner_username = get_setting("owner_username").lstrip("@")
    setup_video_url = get_setting("setup_video_url")

    chat_url = f"https://t.me/{owner_username}" if owner_username else "https://t.me/Myselfvipulkumar"

    rows = [
        [InlineKeyboardButton("🔴 REGISTER NOW", url=register_url)],
        [InlineKeyboardButton("🔥 OPEN VIPUL PANEL", url=panel_url)],
        [InlineKeyboardButton(f"💬 CHAT WITH @{owner_username or 'Myselfvipulkumar'}", url=chat_url)],
        [InlineKeyboardButton("🎬 WATCH SETUP VIDEO", url=setup_video_url)],
    ]

    if include_rejoin:
        links = get_invite_links()
        for link_id, label, url in links[:3]:
            rows.append([InlineKeyboardButton(f"🔁 REJOIN: {label}", url=url)])

    return InlineKeyboardMarkup(rows)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        record_user(update.effective_user)

    text = get_setting("welcome_text")
    await update.message.reply_text(
        text=text,
        reply_markup=build_action_keyboard(include_rejoin=False),
    )


async def add_link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    # उपयोग: /addlink VIP_Ad https://t.me/+xxxx
    if len(context.args) < 2:
        await update.message.reply_text(
            "Istemaal:\n/addlink <Label> <Invite_URL>\n"
            "Udaharan:\n/addlink Reel_Promo https://t.me/+AbCdEfGh123"
        )
        return

    url = context.args[-1].strip()
    label = " ".join(context.args[:-1]).strip()

    if not (url.startswith("https://t.me/") or url.startswith("http://t.me/")):
        await update.message.reply_text("Kripya sahi Telegram invite link (https://t.me/...) dein.")
        return

    if add_invite_link(label, url):
        await update.message.reply_text(f"✅ Invite link add ho gaya:\n• {label}: {url}")
    else:
        await update.message.reply_text("⚠️ Ye invite link pehle se add hai.")


async def links_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    links = get_invite_links()
    if not links:
        await update.message.reply_text("Abhi koi invite link add nahi hai. /addlink se add karein.")
        return

    lines = ["📋 Saved Invite Links:\n"]
    for link_id, label, url in links:
        lines.append(f"{link_id}. {label} -> {url}")
    lines.append("\nDelete karne ke liye: /dellink <ID>")
    await update.message.reply_text("\n".join(lines))


async def del_link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Istemaal: /dellink <ID>")
        return

    link_id = int(context.args[0])
    if delete_invite_link(link_id):
        await update.message.reply_text(f"🗑️ Link ID {link_id} delete ho gaya.")
    else:
        await update.message.reply_text("❌ Is ID ka koi link nahi mila.")


async def set_video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Istemaal: /setvideo <Video_URL_or_t.me_Link>")
        return

    video_url = context.args[0].strip()
    set_setting("setup_video_url", video_url)
    await update.message.reply_text(f"✅ Setup video link update ho gaya:\n{video_url}")


async def set_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Istemaal: /setpanel <Panel_URL>")
        return

    panel_url = context.args[0].strip()
    set_setting("panel_url", panel_url)
    await update.message.reply_text(f"✅ Panel link update ho gaya:\n{panel_url}")


async def set_register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Istemaal: /setregister <Register_URL>")
        return

    reg_url = context.args[0].strip()
    set_setting("register_url", reg_url)
    await update.message.reply_text(f"✅ Register link update ho gaya:\n{reg_url}")


async def set_username_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Istemaal: /setusername Myselfvipulkumar")
        return

    uname = context.args[0].strip().lstrip("@")
    set_setting("owner_username", uname)
    await update.message.reply_text(f"✅ Chat username set ho gaya: @{uname}")


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    req = update.chat_join_request
    if not req or not req.from_user:
        return

    user = req.from_user
    record_user(user)

    invite_url = req.invite_link.invite_link if req.invite_link else "N/A"
    logger.info("Join request from %s (%s) via %s", user.id, user.full_name, invite_url)

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=get_setting("welcome_text"),
            reply_markup=build_action_keyboard(include_rejoin=False),
        )
    except Exception as exc:
        logger.warning("Could not send join DM to %s: %s", user.id, exc)


async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    if not cmu or not cmu.from_user:
        return

    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    target_user = cmu.new_chat_member.user

    # जब कोई member चैनल छोड़ता है या हटाया जाता है
    if old_status in ("member", "administrator", "restricted") and new_status in ("left", "kicked"):
        if not target_user or target_user.is_bot:
            return

        if not is_known_user(target_user.id):
            logger.info("User %s left channel without prior bot interaction; DM skipped.", target_user.id)
            return

        try:
            await context.bot.send_message(
                chat_id=target_user.id,
                text=get_setting("leave_text"),
                reply_markup=build_action_keyboard(include_rejoin=True),
            )
        except Exception as exc:
            logger.warning("Could not send leave DM to %s: %s", target_user.id, exc)


async def track_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        record_user(update.effective_user)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing in environment / .env file.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("addlink", add_link_cmd))
    app.add_handler(CommandHandler("links", links_cmd))
    app.add_handler(CommandHandler("dellink", del_link_cmd))
    app.add_handler(CommandHandler("setvideo", set_video_cmd))
    app.add_handler(CommandHandler("setpanel", set_panel_cmd))
    app.add_handler(CommandHandler("setregister", set_register_cmd))
    app.add_handler(CommandHandler("setusername", set_username_cmd))

    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_any_message))

    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "chat_join_request",
            "chat_member",
        ]
    )


if __name__ == "__main__":
    main()
