"""
╔══════════════════════════════════════════════╗
║         📧 FakeMail Telegram Bot             ║
║     إيميلات مؤقتة احترافية على تيليغرام      ║
╚══════════════════════════════════════════════╝

المتطلبات:
    pip install python-telegram-bot httpx aiofiles

الإعداد:
    - ضع التوكن في BOT_TOKEN
    - شغّل: python bot.py
"""

import asyncio
import json
import os
import random
import string
import time
import logging
from datetime import datetime, timedelta
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

# ─── الإعدادات ───────────────────────────────────────────────
BOT_TOKEN = "ضع_توكن_البوت_هنا"          # من @BotFather
ADMIN_ID   = 123456789                    # يوزر ID الأدمن (اختياري)

# نطاقات الإيميل المتاحة
DOMAINS = [
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "spam4.me",
    "grr.la",
    "guerrillamailblock.com",
]

DATA_FILE  = "emails_db.json"   # قاعدة بيانات المستخدمين
CHECK_INTERVAL = 30             # ثواني بين كل فحص للرسائل

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ─── قاعدة البيانات (JSON بسيطة) ────────────────────────────
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

# ─── Guerrilla Mail API ──────────────────────────────────────
GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"

async def create_email_api(user: str, domain: str) -> dict:
    """إنشاء أو استرجاع إيميل من Guerrilla Mail"""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GUERRILLA_API, params={
            "f": "set_email_user",
            "email_user": user,
            "lang": "en",
            "domain": domain,
        })
        return r.json()

async def get_inbox(sid_token: str, seq: int = 0) -> list:
    """جلب رسائل البريد الوارد"""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GUERRILLA_API, params={
            "f": "get_email_list",
            "offset": 0,
            "seq": seq,
            "sid_token": sid_token,
        })
        data = r.json()
        return data.get("list", [])

async def read_email(email_id: str, sid_token: str) -> dict:
    """قراءة رسالة معينة"""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GUERRILLA_API, params={
            "f": "fetch_email",
            "email_id": email_id,
            "sid_token": sid_token,
        })
        return r.json()

async def forget_email(sid_token: str):
    """حذف/نسيان الإيميل من السيرفر"""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.get(GUERRILLA_API, params={
            "f": "forget_me",
            "sid_token": sid_token,
        })

# ─── مساعدات ────────────────────────────────────────────────
def random_username(length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))

def build_main_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    pause_text = "▶️ تفعيل الإيميل" if paused else "⏸ إيقاف مؤقت"
    buttons = [
        [
            InlineKeyboardButton("📬 صندوق الوارد", callback_data="inbox"),
            InlineKeyboardButton("🔄 تجديد الإيميل", callback_data="refresh"),
        ],
        [
            InlineKeyboardButton("📤 نقل إلى شخص", callback_data="transfer"),
            InlineKeyboardButton(pause_text, callback_data="toggle_pause"),
        ],
        [
            InlineKeyboardButton("🗑 حذف الإيميل", callback_data="delete"),
            InlineKeyboardButton("ℹ️ معلومات", callback_data="info"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

def email_card(address: str, created: str, paused: bool, msg_count: int) -> str:
    status = "⏸ متوقف مؤقتاً" if paused else "🟢 نشط"
    return (
        f"╔══════════════════════════╗\n"
        f"║      📧 إيميلك الحالي      ║\n"
        f"╚══════════════════════════╝\n\n"
        f"📮 **العنوان:**\n`{address}`\n\n"
        f"📊 **الحالة:** {status}\n"
        f"📨 **الرسائل:** {msg_count}\n"
        f"🕐 **أُنشئ:** {created}\n\n"
        f"_اضغط على الإيميل لنسخه تلقائياً_ 👆"
    )

# ─── الأوامر الرئيسية ────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)

    if rec and rec.get("address"):
        # مستخدم موجود
        kb  = build_main_keyboard(rec.get("paused", False))
        msg = email_card(
            rec["address"],
            rec.get("created", "—"),
            rec.get("paused", False),
            rec.get("msg_count", 0),
        )
        await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    # مستخدم جديد → إنشاء إيميل
    await update.message.reply_text("⏳ جاري إنشاء إيميلك...")
    username = random_username()
    domain   = random.choice(DOMAINS)

    try:
        data = await create_email_api(username, domain)
        address   = data.get("email_addr", f"{username}@{domain}")
        sid_token = data.get("sid_token", "")
        created   = datetime.now().strftime("%Y-%m-%d %H:%M")

        rec = {
            "address":   address,
            "username":  username,
            "domain":    domain,
            "sid_token": sid_token,
            "created":   created,
            "paused":    False,
            "msg_count": 0,
            "seen_ids":  [],
        }
        set_user(db, uid, rec)

        kb  = build_main_keyboard()
        msg = email_card(address, created, False, 0)
        await update.message.reply_text(
            f"✅ **تم إنشاء إيميلك بنجاح!**\n\n{msg}",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"create_email error: {e}")
        await update.message.reply_text("❌ خطأ أثناء إنشاء الإيميل، حاول مرة ثانية.")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 **FakeMail Bot — المساعدة**\n\n"
        "🔹 /start — بدء البوت وإنشاء إيميل\n"
        "🔹 /myemail — عرض إيميلك الحالي\n"
        "🔹 /inbox — فحص الرسائل\n"
        "🔹 /new — إنشاء إيميل جديد\n"
        "🔹 /pause — إيقاف/تفعيل الإيميل مؤقتاً\n"
        "🔹 /delete — حذف الإيميل\n"
        "🔹 /help — هذه الرسالة\n\n"
        "💡 **كيف يعمل البوت؟**\n"
        "يولّد لك إيميل مؤقت تستخدمه للتسجيل في المواقع. "
        "أي رسالة تصل إليه ستظهر هنا مباشرة."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_myemail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ ليس لديك إيميل، اضغط /start أولاً.")
        return
    kb  = build_main_keyboard(rec.get("paused", False))
    msg = email_card(rec["address"], rec.get("created","—"), rec.get("paused",False), rec.get("msg_count",0))
    await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")

async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)

    # حذف القديم إن وجد
    if rec and rec.get("sid_token"):
        try:
            await forget_email(rec["sid_token"])
        except Exception:
            pass

    await update.message.reply_text("⏳ جاري إنشاء إيميل جديد...")
    username = random_username()
    domain   = random.choice(DOMAINS)
    try:
        data      = await create_email_api(username, domain)
        address   = data.get("email_addr", f"{username}@{domain}")
        sid_token = data.get("sid_token", "")
        created   = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_rec   = {
            "address":   address,
            "username":  username,
            "domain":    domain,
            "sid_token": sid_token,
            "created":   created,
            "paused":    False,
            "msg_count": 0,
            "seen_ids":  [],
        }
        set_user(db, uid, new_rec)
        kb  = build_main_keyboard()
        msg = email_card(address, created, False, 0)
        await update.message.reply_text(f"✅ **إيميل جديد!**\n\n{msg}", reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        log.error(e)
        await update.message.reply_text("❌ فشل إنشاء الإيميل، حاول مرة ثانية.")

async def cmd_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ ليس لديك إيميل، اضغط /start.")
        return
    await fetch_and_show_inbox(update.message, rec, uid, db)

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ لا يوجد إيميل لحذفه.")
        return
    if rec.get("sid_token"):
        try:
            await forget_email(rec["sid_token"])
        except Exception:
            pass
    db.pop(str(uid), None)
    save_db(db)
    await update.message.reply_text("🗑 **تم حذف الإيميل بنجاح.**\n\nاضغط /start لإنشاء إيميل جديد.", parse_mode="Markdown")

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db  = load_db()
    rec = get_user(db, uid)
    if not rec:
        await update.message.reply_text("❌ ليس لديك إيميل.")
        return
    rec["paused"] = not rec.get("paused", False)
    set_user(db, uid, rec)
    status = "⏸ تم إيقاف الإيميل مؤقتاً" if rec["paused"] else "▶️ تم تفعيل الإيميل"
    await update.message.reply_text(f"{status}\n\n`{rec['address']}`", parse_mode="Markdown")

# ─── مساعد: عرض الصندوق ─────────────────────────────────────
async def fetch_and_show_inbox(msg_obj, rec, uid, db):
    if rec.get("paused"):
        await msg_obj.reply_text("⏸ الإيميل متوقف مؤقتاً. فعّله أولاً.")
        return
    try:
        emails = await get_inbox(rec.get("sid_token",""), 0)
        if not emails:
            await msg_obj.reply_text("📭 **لا توجد رسائل بعد.**\nسنُعلمك فور وصول أي رسالة!", parse_mode="Markdown")
            return
        for em in emails[:5]:  # أظهر آخر 5
            eid     = str(em.get("mail_id",""))
            subject = em.get("mail_subject","(بدون موضوع)")
            sender  = em.get("mail_from","مجهول")
            date    = em.get("mail_date","")
            excerpt = em.get("mail_excerpt","")[:200]
            text = (
                f"📨 **رسالة جديدة**\n\n"
                f"👤 **من:** `{sender}`\n"
                f"📌 **الموضوع:** {subject}\n"
                f"🕐 **التاريخ:** {date}\n\n"
                f"💬 **محتوى مختصر:**\n{excerpt}…"
            )
            await msg_obj.reply_text(text, parse_mode="Markdown")
        rec["msg_count"] = len(emails)
        set_user(db, uid, rec)
    except Exception as e:
        log.error(f"inbox error: {e}")
        await msg_obj.reply_text("❌ فشل جلب الرسائل. حاول مرة ثانية.")

# ─── Callback Buttons ────────────────────────────────────────
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data
    db   = load_db()
    rec  = get_user(db, uid)

    if data == "inbox":
        if not rec:
            await query.message.reply_text("❌ ليس لديك إيميل.")
            return
        await fetch_and_show_inbox(query.message, rec, uid, db)

    elif data == "refresh":
        # إنشاء إيميل جديد
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
            kb  = build_main_keyboard()
            msg = email_card(address, created, False, 0)
            await query.message.edit_text(f"🔄 **إيميل جديد!**\n\n{msg}", reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            log.error(e)
            await query.message.reply_text("❌ فشل التجديد.")

    elif data == "transfer":
        ctx.user_data["awaiting_transfer"] = True
        await query.message.reply_text(
            "📤 **نقل الإيميل**\n\nأرسل لي معرّف المستخدم (User ID) اللي تبي تنقل له الإيميل:",
            parse_mode="Markdown",
        )

    elif data == "toggle_pause":
        if not rec:
            await query.message.reply_text("❌ ليس لديك إيميل.")
            return
        rec["paused"] = not rec.get("paused", False)
        set_user(db, uid, rec)
        status = "⏸ تم الإيقاف المؤقت" if rec["paused"] else "▶️ تم التفعيل"
        kb  = build_main_keyboard(rec["paused"])
        msg = email_card(rec["address"], rec.get("created","—"), rec["paused"], rec.get("msg_count",0))
        await query.message.edit_text(f"{status}\n\n{msg}", reply_markup=kb, parse_mode="Markdown")

    elif data == "delete":
        if rec and rec.get("sid_token"):
            try: await forget_email(rec["sid_token"])
            except Exception: pass
        db.pop(str(uid), None)
        save_db(db)
        await query.message.edit_text(
            "🗑 **تم حذف الإيميل.**\n\nاضغط /start لإنشاء إيميل جديد.",
            parse_mode="Markdown",
        )

    elif data == "info":
        await query.message.reply_text(
            "ℹ️ **معلومات البوت**\n\n"
            "🔸 الإيميلات مؤقتة وتُستخدم لحماية خصوصيتك\n"
            "🔸 يمكنك نقل الإيميل لشخص آخر\n"
            "🔸 سنُعلمك فور وصول أي رسالة\n"
            "🔸 يدعم عدد غير محدود من المستخدمين\n\n"
            "📌 **الأوامر:**\n"
            "/start — بدء أو عرض إيميلك\n"
            "/new — إيميل جديد\n"
            "/inbox — فحص الرسائل\n"
            "/pause — إيقاف/تفعيل\n"
            "/delete — حذف الإيميل",
            parse_mode="Markdown",
        )

# ─── معالج الرسائل النصية (للنقل) ──────────────────────────
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if ctx.user_data.get("awaiting_transfer"):
        ctx.user_data["awaiting_transfer"] = False
        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ معرّف غير صحيح. أرسل رقم User ID فقط.")
            return

        db     = load_db()
        rec    = get_user(db, uid)
        if not rec:
            await update.message.reply_text("❌ ليس لديك إيميل.")
            return

        # نقل السجل للمستخدم الهدف
        target_rec = get_user(db, target_uid)
        if target_rec and target_rec.get("sid_token"):
            try: await forget_email(target_rec["sid_token"])
            except Exception: pass

        set_user(db, target_uid, dict(rec))
        db.pop(str(uid), None)
        save_db(db)

        await update.message.reply_text(
            f"✅ **تم النقل بنجاح!**\n\n"
            f"📧 الإيميل `{rec['address']}` نُقل للمستخدم `{target_uid}`.",
            parse_mode="Markdown",
        )
        # إعلام الشخص الآخر
        try:
            await ctx.bot.send_message(
                chat_id=target_uid,
                text=f"🎁 **تم إرسال إيميل إليك!**\n\n"
                     f"📧 `{rec['address']}`\n\n"
                     f"اضغط /start لإدارته.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return

    # رسالة عادية → إظهار الإيميل
    await cmd_myemail(update, ctx)

# ─── جلسة Auto-Check للرسائل ────────────────────────────────
async def auto_check_emails(app: Application):
    """يفحص كل المستخدمين كل CHECK_INTERVAL ثانية"""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        db = load_db()
        for uid_str, rec in list(db.items()):
            if rec.get("paused") or not rec.get("sid_token"):
                continue
            try:
                emails   = await get_inbox(rec["sid_token"], 0)
                seen_ids = rec.get("seen_ids", [])
                new_msgs = [e for e in emails if str(e.get("mail_id","")) not in seen_ids]

                for em in new_msgs:
                    eid     = str(em.get("mail_id",""))
                    subject = em.get("mail_subject","(بدون موضوع)")
                    sender  = em.get("mail_from","مجهول")
                    excerpt = em.get("mail_excerpt","")[:300]

                    text = (
                        f"🔔 **رسالة جديدة وصلت!**\n\n"
                        f"📮 **على:** `{rec['address']}`\n"
                        f"👤 **من:** `{sender}`\n"
                        f"📌 **الموضوع:** {subject}\n\n"
                        f"💬 {excerpt}"
                    )
                    await app.bot.send_message(chat_id=int(uid_str), text=text, parse_mode="Markdown")
                    seen_ids.append(eid)

                rec["seen_ids"] = seen_ids[-100:]  # احتفظ بآخر 100 فقط
                rec["msg_count"] = len(emails)
                db[uid_str] = rec

            except Exception as e:
                log.debug(f"auto_check uid={uid_str}: {e}")

        save_db(db)

# ─── التشغيل ─────────────────────────────────────────────────
async def post_init(app: Application):
    # ضع قائمة الأوامر في تيليغرام
    await app.bot.set_my_commands([
        BotCommand("start",  "بدء البوت وعرض إيميلك"),
        BotCommand("myemail","عرض إيميلك الحالي"),
        BotCommand("inbox",  "فحص الرسائل"),
        BotCommand("new",    "إنشاء إيميل جديد"),
        BotCommand("pause",  "إيقاف/تفعيل الإيميل"),
        BotCommand("delete", "حذف الإيميل"),
        BotCommand("help",   "المساعدة"),
    ])
    # شغّل المهمة الخلفية
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

    log.info("🤖 FakeMail Bot تشغيل...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
