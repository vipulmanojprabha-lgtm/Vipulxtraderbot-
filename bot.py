import csv
import io
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
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
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("VipulSmartBot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "6054005253").strip()
DB_PATH = os.getenv("DB_PATH", "vipul_members.db").strip()
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN missing hai! Render Environment Variables mein BOT_TOKEN add karein."
    )

try:
    ADMIN_ID: int = int(ADMIN_ID_RAW)
except ValueError as exc:
    raise RuntimeError("ADMIN_ID valid integer hona chahiye.") from exc

DEFAULT_WELCOME_MSG = (
    "APKA REQUEST AYA HAI DEAR ❤️\n\n"
    "APKA REQUEST JALD HI ACCEPT HO JAYEGA. TAB TAK AAP NICHE DIYE GAYE "
    "REGISTRATION LINK PAR CLICK KARKE REGISTRATION KAR LO.\n\n"
    "AUR JALD HI LIVE AAUNGA, TO LIVE MEIN ZAROOR AANA! 🔥❤️"
)

DEFAULT_LEAVE_MSG = (
    "Arey bhai, channel kyun leave kar diya? 😂❤️\n\n"
    "Ek baar phir se rejoin karo aur dekho, hum kaise loss recover karwane mein "
    "help karte hain! 🔥\n\n"
    "Aur bro, personal message karoge toh loss recovery mein bhi help kar dunga yaar. 🤝\n\n"
    "Ab bhai, dobara leave mat karna. Jaldi se channel rejoin karo! ❤️‍🔥"
)

DEFAULT_SETTINGS = {
    "channel_id": "",
    "channel_invite_link": "https://t.me/your_channel_invite_link",
    "registration_link": "https://example.com/register",
    "panel_link": "https://example.com/panel",
    "rejoin_link": "https://t.me/your_channel_invite_link",
    "welcome_message": DEFAULT_WELCOME_MSG,
    "leave_message": DEFAULT_LEAVE_MSG,
    "auto_approve": "off",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_admin(user_id: Optional[int]) -> bool:
    return user_id is not None and int(user_id) == ADMIN_ID


def is_valid_url(url: str) -> bool:
    u = (url or "").strip()
    return u.startswith("https://") or u.startswith("http://") or u.startswith("tg://")


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    user_chat_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    join_request_date TEXT,
                    request_status TEXT DEFAULT 'pending',
                    approval_date TEXT,
                    leave_date TEXT,
                    join_requests_count INTEGER DEFAULT 0,
                    times_left INTEGER DEFAULT 0,
                    last_message_status TEXT DEFAULT 'none',
                    last_left_processed_at TEXT,
                    dm_started INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (k, v),
                )
            conn.commit()

    def get_setting(self, key: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def get_all_settings(self) -> dict[str, str]:
        data = dict(DEFAULT_SETTINGS)
        with self._connect() as conn:
            for r in conn.execute("SELECT key, value FROM bot_settings").fetchall():
                data[str(r["key"])] = str(r["value"])
        return data

    def mark_dm_started(self, user_id: int, username: str, first_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, user_chat_id, username, first_name, dm_started)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    user_chat_id = COALESCE(excluded.user_chat_id, users.user_chat_id),
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    dm_started = 1
                """,
                (user_id, user_id, username, first_name),
            )
            conn.commit()

    def record_join_request(
        self, user_id: int, user_chat_id: int, username: str, first_name: str
    ) -> None:
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, user_chat_id, username, first_name,
                    join_request_date, request_status, join_requests_count, last_left_processed_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', 1, NULL)
                ON CONFLICT(user_id) DO UPDATE SET
                    user_chat_id = COALESCE(excluded.user_chat_id, users.user_chat_id),
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    join_request_date = excluded.join_request_date,
                    request_status = 'pending',
                    join_requests_count = users.join_requests_count + 1,
                    last_left_processed_at = NULL
                """,
                (user_id, user_chat_id, username, first_name, ts),
            )
            conn.commit()

    def mark_approved(self, user_id: int, username: str, first_name: str) -> None:
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, user_chat_id, username, first_name,
                    request_status, approval_date, join_requests_count
                )
                VALUES (?, ?, ?, ?, 'approved', ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    request_status = 'approved',
                    approval_date = excluded.approval_date,
                    last_left_processed_at = NULL
                """,
                (user_id, user_id, username, first_name, ts),
            )
            conn.commit()

    def record_genuine_leave(
        self, user_id: int, username: str, first_name: str
    ) -> Optional[sqlite3.Row]:
        ts = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

            if (
                row
                and row["request_status"] == "left"
                and row["last_left_processed_at"] is not None
            ):
                return None

            if row:
                conn.execute(
                    """
                    UPDATE users
                    SET username = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        request_status = 'left',
                        leave_date = ?,
                        times_left = times_left + 1,
                        last_left_processed_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        username or row["username"],
                        first_name or row["first_name"],
                        ts,
                        ts,
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id, user_chat_id, username, first_name,
                        request_status, leave_date, join_requests_count,
                        times_left, last_left_processed_at
                    )
                    VALUES (?, ?, ?, ?, 'left', ?, 0, 1, ?)
                    """,
                    (user_id, user_id, username, first_name, ts, ts),
                )
            conn.commit()
            return conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def update_message_status(self, user_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_message_status = ? WHERE user_id = ?",
                (status, user_id),
            )
            conn.commit()

    def get_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE request_status = 'pending'"
            ).fetchone()["c"]
            approved = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE request_status = 'approved'"
            ).fetchone()["c"]
            declined = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE request_status = 'declined'"
            ).fetchone()["c"]
            left = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE request_status = 'left'"
            ).fetchone()["c"]
            total_reqs = conn.execute(
                "SELECT COALESCE(SUM(join_requests_count), 0) AS s FROM users"
            ).fetchone()["s"]
            total_leaves = conn.execute(
                "SELECT COALESCE(SUM(times_left), 0) AS s FROM users"
            ).fetchone()["s"]
        return {
            "total": int(total),
            "pending": int(pending),
            "approved": int(approved),
            "declined": int(declined),
            "left": int(left),
            "total_requests": int(total_reqs),
            "total_leaves": int(total_leaves),
        }

    def list_users(
        self, status: Optional[str] = None, limit: int = 25
    ) -> list[sqlite3.Row]:
        with self._connect() as conn:
            if status:
                return conn.execute(
                    """
                    SELECT * FROM users
                    WHERE request_status = ?
                    ORDER BY COALESCE(join_request_date, leave_date, approval_date) DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            return conn.execute(
                """
                SELECT * FROM users
                ORDER BY COALESCE(join_request_date, leave_date, approval_date) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def get_all_users_for_export(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM users ORDER BY user_id ASC").fetchall()

    def get_broadcast_targets(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT user_id, user_chat_id
                FROM users
                WHERE user_chat_id IS NOT NULL
                  AND COALESCE(last_message_status, '') != 'blocked_or_no_dm'
                """
            ).fetchall()


db = Database(DB_PATH)


def build_welcome_keyboard(
    settings: dict[str, str], bot_username: str
) -> Optional[InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    reg_url = settings.get("registration_link", "").strip()
    panel_url = settings.get("panel_link", "").strip()

    if is_valid_url(reg_url):
        rows.append([InlineKeyboardButton("🟢 REGISTER NOW", url=reg_url)])
    if is_valid_url(panel_url):
        rows.append([InlineKeyboardButton("🔥 OPEN VIPUL PANEL", url=panel_url)])
    if bot_username:
        rows.append(
            [
                InlineKeyboardButton(
                    "🤖 OPEN BOT CHAT", url=f"https://t.me/{bot_username}?start=welcome"
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def build_rejoin_keyboard(
    settings: dict[str, str], bot_username: str
) -> Optional[InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    rejoin_url = (
        settings.get("rejoin_link") or settings.get("channel_invite_link", "")
    ).strip()

    if is_valid_url(rejoin_url):
        rows.append([InlineKeyboardButton("❤️ REJOIN CHANNEL", url=rejoin_url)])
    if bot_username:
        rows.append(
            [
                InlineKeyboardButton(
                    "💬 MESSAGE BOT FOR HELP",
                    url=f"https://t.me/{bot_username}?start=rejoin",
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


async def safe_send_dm(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_target_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> str:
    try:
        await context.bot.send_message(
            chat_id=chat_target_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        db.update_message_status(user_id, "sent_dm")
        return "sent_dm"
    except Forbidden:
        db.update_message_status(user_id, "blocked_or_no_dm")
        return "blocked_or_no_dm"
    except BadRequest:
        db.update_message_status(user_id, "bad_request")
        return "bad_request"
    except TelegramError:
        db.update_message_status(user_id, "error")
        return "error"


def matches_configured_channel(chat_id: int, settings: dict[str, str]) -> bool:
    configured = settings.get("channel_id", "").strip()
    if not configured:
        return True
    try:
        return int(configured) == int(chat_id)
    except ValueError:
        return True


async def handle_join_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    req = update.chat_join_request
    if not req:
        return

    settings = db.get_all_settings()
    if not matches_configured_channel(req.chat.id, settings):
        return

    user = req.from_user
    username = user.username or ""
    first_name = user.first_name or "Friend"
    target_chat_id = req.user_chat_id or user.id

    db.record_join_request(user.id, target_chat_id, username, first_name)

    welcome_text = settings.get("welcome_message") or DEFAULT_WELCOME_MSG
    keyboard = build_welcome_keyboard(settings, context.bot.username or "")
    await safe_send_dm(context, user.id, target_chat_id, welcome_text, keyboard)

    if settings.get("auto_approve", "off").lower() == "on":
        try:
            await context.bot.approve_chat_join_request(
                chat_id=req.chat.id, user_id=user.id
            )
            db.mark_approved(user.id, username, first_name)
        except TelegramError as exc:
            logger.error("Auto-approve failed for %s: %s", user.id, exc)


def _is_active_status(status: str) -> bool:
    return status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    }


async def handle_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    cmu = update.chat_member
    if not cmu:
        return

    settings = db.get_all_settings()
    if not matches_configured_channel(cmu.chat.id, settings):
        return

    old_status = cmu.old_chat_member.status
    new_status = cmu.new_chat_member.status
    user = cmu.new_chat_member.user or cmu.old_chat_member.user
    if not user or user.is_bot:
        return

    username = user.username or ""
    first_name = user.first_name or "Friend"

    if not _is_active_status(old_status) and _is_active_status(new_status):
        db.mark_approved(user.id, username, first_name)
        return

    if _is_active_status(old_status) and new_status == ChatMemberStatus.LEFT:
        processed_row = db.record_genuine_leave(user.id, username, first_name)
        if processed_row is None:
            return

        leave_text = settings.get("leave_message") or DEFAULT_LEAVE_MSG
        keyboard = build_rejoin_keyboard(settings, context.bot.username or "")
        chat_target = processed_row["user_chat_id"] or user.id

        dm_status = await safe_send_dm(
            context, user.id, int(chat_target), leave_text, keyboard
        )
        if dm_status != "sent_dm":
            bot_user = context.bot.username or "YourBot"
            deep_link = f"https://t.me/{bot_user}?start=rejoin"
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"ℹ️ Member Left (Direct DM blocked/unstarted)\n"
                        f"Name: {first_name} (@{username or 'N/A'})\n"
                        f"User ID: <code>{user.id}</code>\n"
                        f"Fallback Bot Link: {deep_link}"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass


def _format_user_rows(rows: list[sqlite3.Row], title: str) -> str:
    if not rows:
        return f"<b>{title}</b>\nNo records found."
    lines = [f"<b>{title}</b> (showing {len(rows)})\n"]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "no_username"
        lines.append(
            f"• <code>{r['user_id']}</code> | {r['first_name'] or 'User'} ({uname}) | "
            f"<b>{r['request_status']}</b> | Reqs: {r['join_requests_count']} | Left: {r['times_left']}"
        )
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    user = update.effective_user
    db.mark_dm_started(user.id, user.username or "", user.first_name or "Friend")

    if is_admin(user.id):
        await update.message.reply_text(
            "👋 <b>Vipul Smart Member Tracking & Auto DM Bot</b>\n\n"
            "Bot 24/7 Active hai! Commands ke liye /help ya links set karne ke liye /settings dabayein.",
            parse_mode=ParseMode.HTML,
        )
        return

    settings = db.get_all_settings()
    payload = context.args[0] if context.args else ""
    if payload == "rejoin":
        msg = settings.get("leave_message") or DEFAULT_LEAVE_MSG
        kb = build_rejoin_keyboard(settings, context.bot.username or "")
        await update.message.reply_text(
            msg, reply_markup=kb, disable_web_page_preview=True
        )
    else:
        msg = settings.get("welcome_message") or DEFAULT_WELCOME_MSG
        kb = build_welcome_keyboard(settings, context.bot.username or "")
        await update.message.reply_text(
            msg, reply_markup=kb, disable_web_page_preview=True
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    text = (
        "<b>🛠 Admin Control Commands</b>\n\n"
        "/start - Start bot\n"
        "/stats - Total users, requests & leaves\n"
        "/users - Saved user records\n"
        "/pending - Pending join requests\n"
        "/approved - Approved members\n"
        "/left - Users who left channel\n"
        "/broadcast &lt;message&gt; - Broadcast to users\n"
        "/settings - Links, Channel ID & Messages set karein\n"
        "/export - CSV file download karein\n"
        "/help - All commands"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    s = db.get_stats()
    settings = db.get_all_settings()
    text = (
        "<b>📊 Member & Request Statistics</b>\n\n"
        f"• Total Tracked Users: <b>{s['total']}</b>\n"
        f"• Pending Requests: <b>{s['pending']}</b>\n"
        f"• Approved Members: <b>{s['approved']}</b>\n"
        f"• Declined Requests: <b>{s['declined']}</b>\n"
        f"• Users Who Left: <b>{s['left']}</b>\n"
        f"• Total Join Requests: <b>{s['total_requests']}</b>\n"
        f"• Total Leave Events: <b>{s['total_leaves']}</b>\n"
        f"• Auto-Approve: <b>{settings.get('auto_approve', 'off').upper()}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    await update.message.reply_text(
        _format_user_rows(db.list_users(None, 25), "Saved User Records"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    await update.message.reply_text(
        _format_user_rows(db.list_users("pending", 25), "Pending Join Requests"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_approved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    await update.message.reply_text(
        _format_user_rows(db.list_users("approved", 25), "Approved Members"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_left(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    await update.message.reply_text(
        _format_user_rows(db.list_users("left", 25), "Users Who Left"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    message_text = " ".join(context.args).strip()
    if not message_text:
        await update.message.reply_text(
            "Usage: <code>/broadcast Aapka message</code>", parse_mode=ParseMode.HTML
        )
        return

    targets = db.get_broadcast_targets()
    sent_count = 0
    failed_count = 0
    for row in targets:
        uid = int(row["user_id"])
        cid = int(row["user_chat_id"] or uid)
        if await safe_send_dm(context, uid, cid, message_text, None) == "sent_dm":
            sent_count += 1
        else:
            failed_count += 1

    await update.message.reply_text(
        f"📣 <b>Broadcast Complete</b>\n• Delivered: <b>{sent_count}</b>\n• Failed/Blocked: <b>{failed_count}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    rows = db.get_all_users_for_export()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "user_id",
            "user_chat_id",
            "username",
            "first_name",
            "join_request_date",
            "request_status",
            "approval_date",
            "leave_date",
            "join_requests_count",
            "times_left",
            "last_message_status",
            "dm_started",
        ]
    )
    for r in rows:
        writer.writerow([r[col] for col in r.keys()])

    csv_bytes = io.BytesIO(buf.getvalue().encode("utf-8"))
    csv_bytes.name = "vipul_tracked_members.csv"
    await update.message.reply_document(
        document=csv_bytes, caption=f"Exported {len(rows)} user records."
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not is_admin(update.effective_user.id)
    ):
        return
    s = db.get_all_settings()
    summary = (
        "<b>⚙️ Bot Settings Panel</b>\n\n"
        f"1. Channel ID: <code>{s['channel_id'] or 'NOT SET'}</code>\n"
        f"2. Channel Invite Link: {s['channel_invite_link']}\n"
        f"3. Registration Link: {s['registration_link']}\n"
        f"4. Panel Link: {s['panel_link']}\n"
        f"5. Rejoin Button Link: {s['rejoin_link']}\n"
        f"6. Auto-Approve: <b>{s['auto_approve'].upper()}</b>\n\n"
        "Neeche button par click karke update karein:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Set Channel ID", callback_data="set_channel_id"),
                InlineKeyboardButton(
                    "Set Invite Link", callback_data="set_channel_invite_link"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Set Registration Link", callback_data="set_registration_link"
                ),
                InlineKeyboardButton("Set Panel Link", callback_data="set_panel_link"),
            ],
            [
                InlineKeyboardButton("Set Rejoin Link", callback_data="set_rejoin_link"),
                InlineKeyboardButton(
                    f"Auto-Approve: {s['auto_approve'].upper()}",
                    callback_data="toggle_auto_approve",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Edit Welcome Msg", callback_data="set_welcome_message"
                ),
                InlineKeyboardButton("Edit Leave Msg", callback_data="set_leave_message"),
            ],
        ]
    )
    await update.message.reply_text(
        summary,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def handle_settings_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.from_user or not is_admin(query.from_user.id):
        return
    await query.answer()
    data = query.data or ""

    if data == "toggle_auto_approve":
        nxt = "on" if db.get_setting("auto_approve").lower() != "on" else "off"
        db.set_setting("auto_approve", nxt)
        await query.edit_message_text(
            f"✅ Auto-Approve ab <b>{nxt.upper()}</b> hai.", parse_mode=ParseMode.HTML
        )
        return

    editable_keys = {
        "set_channel_id": (
            "channel_id",
            "Channel ID bhejein (jaise -1001234567890):",
        ),
        "set_channel_invite_link": (
            "channel_invite_link",
            "Channel Invite Link bhejein (https://...):",
        ),
        "set_registration_link": (
            "registration_link",
            "Registration Link bhejein (https://...):",
        ),
        "set_panel_link": ("panel_link", "Panel Link bhejein (https://...):"),
        "set_rejoin_link": (
            "rejoin_link",
            "Rejoin Button Link bhejein (https://...):",
        ),
        "set_welcome_message": ("welcome_message", "Naya Welcome Message bhejein:"),
        "set_leave_message": ("leave_message", "Naya Leave Message bhejein:"),
    }
    if data in editable_keys:
        setting_key, prompt_text = editable_keys[data]
        context.user_data["awaiting_setting_key"] = setting_key
        await query.message.reply_text(f"✏️ {prompt_text}")


async def handle_admin_text_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        not update.effective_user
        or not update.message
        or not update.message.text
        or not is_admin(update.effective_user.id)
    ):
        return
    key = context.user_data.get("awaiting_setting_key")
    if not key:
        return

    val = update.message.text.strip()
    if (
        key
        in {"channel_invite_link", "registration_link", "panel_link", "rejoin_link"}
        and not is_valid_url(val)
    ):
        await update.message.reply_text(
            "❌ Invalid URL! https:// se shuru hona chahiye. Dobara bhejein:"
        )
        return
    if key == "channel_id":
        try:
            val = str(int(val))
        except ValueError:
            await update.message.reply_text(
                "❌ Channel ID number hona chahiye (jaise -1001234567890). Dobara bhejein:"
            )
            return

    db.set_setting(key, val)
    context.user_data.pop("awaiting_setting_key", None)
    await update.message.reply_text(
        f"✅ <code>{key}</code> update ho gaya!", parse_mode=ParseMode.HTML
    )


async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK - Vipul Smart Bot is Alive 24/7!", status=200)


async def post_init(application: Application) -> None:
    app_web = web.Application()
    app_web.router.add_get("/", health_check)
    app_web.router.add_get("/health", health_check)

    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    application.bot_data["web_runner"] = runner
    logger.info("Anti-sleep HTTP server running on 0.0.0.0:%s", PORT)


async def post_shutdown(application: Application) -> None:
    runner = application.bot_data.get("web_runner")
    if runner:
        await runner.cleanup()


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approved", cmd_approved))
    app.add_handler(CommandHandler("left", cmd_left))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("export", cmd_export))

    app.add_handler(
        CallbackQueryHandler(
            handle_settings_callback, pattern=r"^(set_|toggle_auto_approve)"
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_ID),
            handle_admin_text_input,
        )
    )

    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(
        ChatMemberHandler(
            handle_chat_member_update, chat_member_types=ChatMemberHandler.CHAT_MEMBER
        )
    )

    logger.info("Starting Vipul Smart Member Tracking & Auto DM Bot...")
    app.run_polling(
        allowed_updates=[
            Update.MESSAGE,
            Update.CALLBACK_QUERY,
            Update.CHAT_JOIN_REQUEST,
            Update.CHAT_MEMBER,
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()