import asyncio
import json
import os
import random
import string
import logging
from datetime import datetime
from typing import Optional

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- Settings ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))

DOMAINS = [
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "spam4.me",
    "grr.la",
]

DATA_FILE      = "emails_db.json"
CHECK_INTERVAL = 30

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# --- Database ---
def load_db() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db: dict, user_id: int) -> Optional[dict]:
    return db.get(str(user_id))

def set_user(db: dict, user_id: int, data: dict):
    db[str(user_id)] = data
    save_db(db)

# --- Guerrilla Mail API ---
GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"

async def create_email_api(user: str, domain: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GUERRILLA_API, params={
            "f": "set_email_user",
            "email_user": user,
            "lang": "en",
            "domain": domain,
        })
        return r.json()

async def get_inbox(sid_token: str) -> list:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GUERRILLA_API, params={
            "f": "get_email_list",
            "offset": 0,
            "seq": 0,
            "sid_token": sid_token,
        })
        data = r.json()
        return data.get("list", [])

async def forget_email(sid_token: str):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.get(GUERRILLA_API, params={
            "f": "forget_me",
            "sid_token": sid_token,
        })

# --- Helpers ---
def random_username(length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))

def build_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    pause_text = "▶️ Resume" if paused else "⏸ Pause"
    buttons = [
        [
            InlineKeyboardButton("📬 Inbox", callback_data="inbox"),
            InlineKeyboardButton("🔄 New Email", callback_data="refresh"),
        ],
        [
            InlineKeyboardButton("📤 Transfer", callback_data="transfer"),
            InlineKeyboardButton(pause_text, callback_data="toggle_pause"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data="delete"),
            InlineKeyboardButton("ℹ️ Info", callback_data="info"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

def email_card(address: str, created: str, paused: bool, msg_count: int) -> str:
    status = "⏸ Paused" if paused else "🟢 Active"
    return (
        f"📧 *Your Email Address*\n\n"
        f"📮 *Address:*\n`{address}`\n\n"
        f"📊 *Status:* {status}\n"
        f"📨 *Messages:* {msg_count}\n"
        f"🕐 *Created:* {created}\n\n"
        f"_Tap the address to copy it_ 👆"
    )

# --- Commands ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)

    if rec and rec.get("address"):
        kb  = build_keyboard(rec.get("paused", False))
        msg = email_card(rec["address"], rec.get("created","—"), rec.get("paused",False), rec.get("msg_count",0))
        await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    await update.message.reply_text("⏳ Creating your email...")
    username = random_username()
    domain   = random.choice(DOMAINS)

    try:
        data      = await create_email_api(username, domain)
        address   = data.get("email_addr", f"{username}@{domain}")
        sid_token = data.get("sid_token", "")
        created   = datetime.now().strftime("%Y-%m-%d %H:%M")

        rec = {
            "address": address, "username": username, "domain": domain,
            "sid_token": sid_token, "created": created,
            "paused": False, "msg_count": 0, "seen_ids": [],
        }
        set_user(db, uid, rec)
        kb  = build_keyboard()
        msg = email_card(address, created, False, 0)
        await update.message.reply_text(f"✅ *Email created!*\n\n{msg}", reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        log.error(f"create_email error: {e}")
        await update.message.reply_text("❌ Error creating email. Please try again.")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *FakeBoxBot - Help*\n\n"
        "🔹 /start - Start and get your email\n"
        "🔹 /myemail - Show your current email\n"
        "🔹 /inbox - Check messages\n"
        "🔹 /new - Create a new email\n"
        "🔹 /pause - Pause/resume email\n"
        "🔹 /delete - Delete your email\n"
        "🔹 /help - This message\n\n"
        "💡 Any message sent to your email will appear here instantly!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_myemail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ No email found. Use /start first.")
        return
    kb  = build_keyboard(rec.get("paused", False))
    msg = email_card(rec["address"], rec.get("created","—"), rec.get("paused",False), rec.get("msg_count",0))
    await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")

async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if rec and rec.get("sid_token"):
        try: await forget_email(rec["sid_token"])
        except Exception: pass
    await update.message.reply_text("⏳ Creating new email...")
    username = random_username()
    domain   = random.choice(DOMAINS)
    try:
        data      = await create_email_api(username, domain)
        address   = data.get("email_addr", f"{username}@{domain}")
        sid_token = data.get("sid_token", "")
        created   = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_rec   = {"address": address, "username": username, "domain": domain,
                     "sid_token": sid_token, "created": created,
                     "paused": False, "msg_count": 0, "seen_ids": []}
        set_user(db, uid, new_rec)
        kb  = build_keyboard()
        msg = email_card(address, created, False, 0)
        await update.message.reply_text(f"🔄 *New email created!*\n\n{msg}", reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        log.error(e)
        await update.message.reply_text("❌ Failed to create email.")

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ No email to delete.")
        return
    if rec.get("sid_token"):
        try: await forget_email(rec["sid_token"])
        except Exception: pass
    db.pop(str(uid), None)
    save_db(db)
    await update.message.reply_text("🗑 *Email deleted.*\n\nUse /start to create a new one.", parse_mode="Markdown")

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ No email found.")
        return
    rec["paused"] = not rec.get("paused", False)
    set_user(db, uid, rec)
    status = "⏸ Email paused" if rec["paused"] else "▶️ Email resumed"
    await update.message.reply_text(f"{status}\n\n`{rec['address']}`", parse_mode="Markdown")

async def fetch_and_show_inbox(msg_obj, rec, uid, db):
    if rec.get("paused"):
        await msg_obj.reply_text("⏸ Email is paused. Resume it first.")
        return
    try:
        emails = await get_inbox(rec.get("sid_token",""))
        if not emails:
            await msg_obj.reply_text("📭 *No messages yet.*\nWe'll notify you when mail arrives!", parse_mode="Markdown")
            return
        for em in emails[:5]:
            subject = em.get("mail_subject","(no subject)")
            sender  = em.get("mail_from","unknown")
            date    = em.get("mail_date","")
            excerpt = em.get("mail_excerpt","")[:200]
            text = (
                f"📨 *New Message*\n\n"
                f"👤 *From:* `{sender}`\n"
                f"📌 *Subject:* {subject}\n"
                f"🕐 *Date:* {date}\n\n"
                f"💬 *Preview:*\n{excerpt}..."
            )
            await msg_obj.reply_text(text, parse_mode="Markdown")
        rec["msg_count"] = len(emails)
        set_user(db, uid, rec)
    except Exception as e:
        log.error(f"inbox error: {e}")
        await msg_obj.reply_text("❌ Failed to fetch messages.")

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data
    db   = load_db()
    rec  = get_user(db, uid)

    if data == "inbox":
        if not rec:
            await query.message.reply_text("❌ No email found.")
            return
        await fetch_and_show_inbox(query.message, rec, uid, db)

    elif data == "refresh":
        if rec and rec.get("sid_token"):
            try: await forget_email(rec["sid_token"])
            except Exception: pass
        username = random_username()
        domain   = random.choice(DOMAINS)
        try:
            api_data  = await create_email_api(username, domain)
            address   = api_data.get("email_addr", f"{username}@{domain}")
            sid_token = api_data.get("sid_token","")
            created   = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_rec   = {"address":address,"username":username,"domain":domain,"sid_token":sid_token,"created":created,"paused":False,"msg_count":0,"seen_ids":[]}
            set_user(db, uid, new_rec)
            kb  = build_keyboard()
            msg = email_card(address, created, False, 0)
            await query.message.edit_text(f"🔄 *New email!*\n\n{msg}", reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            log.error(e)
            await query.message.reply_text("❌ Failed to refresh.")

    elif data == "transfer":
        ctx.user_data["awaiting_transfer"] = True
        await query.message.reply_text("📤 *Transfer Email*\n\nSend me the User ID of the person to transfer to:", parse_mode="Markdown")

    elif data == "toggle_pause":
        if not rec:
            await query.message.reply_text("❌ No email found.")
            return
        rec["paused"] = not rec.get("paused", False)
        set_user(db, uid, rec)
        status = "⏸ Paused" if rec["paused"] else "▶️ Resumed"
        kb  = build_keyboard(rec["paused"])
        msg = email_card(rec["address"], rec.get("created","—"), rec["paused"], rec.get("msg_count",0))
        await query.message.edit_text(f"{status}\n\n{msg}", reply_markup=kb, parse_mode="Markdown")

    elif data == "delete":
        if rec and rec.get("sid_token"):
            try: await forget_email(rec["sid_token"])
            except Exception: pass
        db.pop(str(uid), None)
        save_db(db)
        await query.message.edit_text("🗑 *Email deleted.*\n\nUse /start to create a new one.", parse_mode="Markdown")

    elif data == "info":
        await query.message.reply_text(
            "ℹ️ *FakeBoxBot Info*\n\n"
            "🔸 Temporary emails to protect your privacy\n"
            "🔸 Transfer email to another user\n"
            "🔸 Get notified instantly when mail arrives\n"
            "🔸 Supports unlimited users\n\n"
            "📌 *Commands:*\n"
            "/start - View your email\n"
            "/new - New email\n"
            "/inbox - Check messages\n"
            "/pause - Pause/resume\n"
            "/delete - Delete email",
            parse_mode="Markdown",
        )

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()
    if ctx.user_data.get("awaiting_transfer"):
        ctx.user_data["awaiting_transfer"] = False
        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid ID. Send a numeric User ID.")
            return
        db  = load_db()
        rec = get_user(db, uid)
        if not rec:
            await update.message.reply_text("❌ No email found.")
            return
        target_rec = get_user(db, target_uid)
        if target_rec and target_rec.get("sid_token"):
            try: await forget_email(target_rec["sid_token"])
            except Exception: pass
        set_user(db, target_uid, dict(rec))
        db.pop(str(uid), None)
        save_db(db)
        await update.message.reply_text(f"✅ *Transferred!*\n\n`{rec['address']}` sent to `{target_uid}`.", parse_mode="Markdown")
        try:
            await ctx.bot.send_message(chat_id=target_uid, text=f"🎁 *An email was sent to you!*\n\n`{rec['address']}`\n\nUse /start to manage it.", parse_mode="Markdown")
        except Exception: pass
        return
    await cmd_myemail(update, ctx)

async def auto_check_emails(app: Application):
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        db = load_db()
        for uid_str, rec in list(db.items()):
            if rec.get("paused") or not rec.get("sid_token"):
                continue
            try:
                emails   = await get_inbox(rec["sid_token"])
                seen_ids = rec.get("seen_ids", [])
                new_msgs = [e for e in emails if str(e.get("mail_id","")) not in seen_ids]
                for em in new_msgs:
                    eid     = str(em.get("mail_id",""))
                    subject = em.get("mail_subject","(no subject)")
                    sender  = em.get("mail_from","unknown")
                    excerpt = em.get("mail_excerpt","")[:300]
                    text = (
                        f"🔔 *New message arrived!*\n\n"
                        f"📮 *To:* `{rec['address']}`\n"
                        f"👤 *From:* `{sender}`\n"
                        f"📌 *Subject:* {subject}\n\n"
                        f"💬 {excerpt}"
                    )
                    await app.bot.send_message(chat_id=int(uid_str), text=text, parse_mode="Markdown")
                    seen_ids.append(eid)
                rec["seen_ids"]  = seen_ids[-100:]
                rec["msg_count"] = len(emails)
                db[uid_str] = rec
            except Exception as e:
                log.debug(f"auto_check uid={uid_str}: {e}")
        save_db(db)

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",   "Start and view your email"),
        BotCommand("myemail", "Show current email"),
        BotCommand("inbox",   "Check messages"),
        BotCommand("new",     "Create new email"),
        BotCommand("pause",   "Pause/resume email"),
        BotCommand("delete",  "Delete email"),
        BotCommand("help",    "Help"),
    ])
    asyncio.create_task(auto_check_emails(app))

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("myemail", cmd_myemail))
    app.add_handler(CommandHandler("new",     cmd_new))
    app.add_handler(CommandHandler("inbox",   cmd_inbox))
    app.add_handler(CommandHandler("delete",  cmd_delete))
    app.add_handler(CommandHandler("pause",   cmd_pause))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("🤖 FakeBoxBot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
