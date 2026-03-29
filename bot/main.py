import os
import re
import asyncio
import random
import threading
import functools

from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, AlreadyInConversationError
from telethon.tl.functions.updates import GetStateRequest

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    OWNER_ID,
    SESSIONS_DIR,
    CHANNEL_KEYS,
)
from database import (
    load_db,
    save_db,
    clear_seen,
    get_seen_count,
    get_raw_count,
    load_raw_links,
)
from account_manager import AccountManager
from channel_setup import (
    create_archive_channels,
    add_account_to_channels,
    add_owner_to_channels,
    join_accounts_via_invites,
)
from harvester import harvest_sources
from sorter import run_sorter
from joiner import run_smart_joiner
from searcher import run_smart_discovery
import state as sorter_ctrl

db = load_db()
bot = TelegramClient(
    "bot_controller", API_ID, API_HASH,
    connection_retries=-1,
    retry_delay=5,
    auto_reconnect=True,
    request_retries=5,
)


# ─────────────────────────────────────────────────────────────────────────────
# Auth guard
# ─────────────────────────────────────────────────────────────────────────────

def _is_authorized(sender_id: int) -> bool:
    """True if sender is the owner or a trusted user."""
    fresh = load_db()
    return sender_id == OWNER_ID or sender_id in fresh.get("trusted_users", [])


def owner_only(func):
    """Allows both the owner AND trusted users."""
    @functools.wraps(func)
    async def wrapper(event):
        if not _is_authorized(event.sender_id):
            msg = "🚫 غير مصرح لك. أرسل /start لطلب الوصول."
            try:
                await event.answer(msg, alert=True)
            except Exception:
                try:
                    await event.respond(msg, parse_mode="md")
                except Exception:
                    pass
            return
        try:
            await func(event)
        except AlreadyInConversationError:
            try:
                await event.answer("⚠️ هناك عملية جارية. أنهها أو انتظر.", alert=True)
            except Exception:
                pass
        except Exception as e:
            try:
                await event.answer("❌ حدث خطأ، حاول مجدداً.", alert=True)
            except Exception:
                pass
            try:
                await bot.send_message(OWNER_ID, f"❌ خطأ في {func.__name__}:\n{e}", parse_mode="md")
            except Exception:
                pass
    return wrapper


def admin_only(func):
    """Strict owner-only — trusted users cannot access."""
    @functools.wraps(func)
    async def wrapper(event):
        if event.sender_id != OWNER_ID:
            msg = "🔒 هذا الإجراء للمالك فقط."
            try:
                await event.answer(msg, alert=True)
            except Exception:
                try:
                    await event.respond(msg, parse_mode="md")
                except Exception:
                    pass
            return
        try:
            await func(event)
        except AlreadyInConversationError:
            try:
                await event.answer("⚠️ هناك عملية جارية. أنهها أو انتظر.", alert=True)
            except Exception:
                pass
        except Exception as e:
            try:
                await event.answer("❌ حدث خطأ، حاول مجدداً.", alert=True)
            except Exception:
                pass
            try:
                await bot.send_message(OWNER_ID, f"❌ خطأ في {func.__name__}:\n{e}", parse_mode="md")
            except Exception:
                pass
    return wrapper


async def status_msg(text: str):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="md")
    except Exception:
        pass


def make_edit_callback(msg_id: int, chat_id: int, fixed_buttons=None):
    """Returns an async callback that EDITS one persistent message instead of sending new ones."""
    async def _cb(text: str):
        try:
            await bot.edit_message(
                chat_id, msg_id, text,
                buttons=fixed_buttons,
                parse_mode="md",
            )
        except Exception:
            pass
    return _cb


async def _keep_alive_http():
    """
    Minimal HTTP health-check server — keeps autoscale deployment alive.

    Replit sets REPLIT_DEPLOYMENT_ID only in the production deployment
    environment, not in the dev workspace. So we skip the server entirely
    in development — the dev workspace has the API server on the same port
    and the keep_alive.sh script already handles bot restarts.
    """
    if not os.environ.get("REPLIT_DEPLOYMENT_ID"):
        print("ℹ️ Dev workspace detected — HTTP health-check server not started.")
        return

    port = int(os.environ.get("PORT", 8080))

    async def _handle(reader, writer):
        try:
            await reader.read(1024)
        except Exception:
            pass
        body = b"Bot OK"
        response = (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n" + body
        )
        try:
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        writer.close()

    try:
        server = await asyncio.start_server(_handle, "0.0.0.0", port)
        print(f"🌐 HTTP health-check server started on port {port}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        print(f"⚠️ HTTP server error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Smart Flow Engine — determines current progress & next recommended step
# ─────────────────────────────────────────────────────────────────────────────

def get_flow_status(db: dict) -> dict:
    accounts   = db.get("accounts", [])
    channels   = db.get("channels", {})
    sources    = db.get("sources", [])
    raw_count  = get_raw_count()
    seen_count = get_seen_count()
    stats      = db.get("stats", {})
    sorted_ok  = stats.get("total_sorted", 0)
    last_idx   = db.get("progress", {}).get("last_sorted_index", 0)

    steps = [
        {
            "n": 1, "key": "accounts",
            "icon": "✅" if accounts else "🔴",
            "label": "الحسابات",
            "detail": f"{len(accounts)} حساب مرتبط" if accounts else "لا يوجد حساب",
            "done": bool(accounts),
        },
        {
            "n": 2, "key": "channels",
            "icon": "✅" if len(channels) >= 7 else ("⚠️" if channels else "🔴"),
            "label": "قنوات الأرشيف",
            "detail": f"{len(channels)}/7 قنوات" if channels else "لم تُنشأ بعد",
            "done": len(channels) >= 7,
            "locked": not accounts,
        },
        {
            "n": 3, "key": "sources",
            "icon": "✅" if sources else "🔴",
            "label": "المصادر",
            "detail": f"{len(sources)} مصدر مضاف" if sources else "لا مصادر",
            "done": bool(sources),
            "locked": not accounts,
        },
        {
            "n": 4, "key": "harvest",
            "icon": "✅" if raw_count > 0 else "🔴",
            "label": "حصاد الروابط",
            "detail": f"{raw_count:,} رابط تم جمعها" if raw_count else "لم يبدأ بعد",
            "done": raw_count > 0,
            "locked": not sources,
        },
        {
            "n": 5, "key": "sort",
            "icon": "✅" if (raw_count > 0 and last_idx >= raw_count) else ("🔄" if last_idx > 0 else "⏳"),
            "label": "الفرز الشامل",
            "detail": (
                f"{last_idx:,}/{raw_count:,} ✦ {int(last_idx/raw_count*100)}%"
                if raw_count > 0 and last_idx > 0
                else ("اكتمل ✅" if raw_count > 0 and sorted_ok > 0 and last_idx >= raw_count else "لم يبدأ بعد")
            ),
            "done": raw_count > 0 and last_idx >= raw_count,
            "in_progress": raw_count > 0 and 0 < last_idx < raw_count,
            "locked": raw_count == 0,
        },
        {
            "n": 6, "key": "discover",
            "icon": "✅" if stats.get("total_found", 0) > raw_count else "⏳",
            "label": "اكتشاف ذكي",
            "detail": "يجد روابط طبية تلقائياً" if not stats.get("total_found") else "تم التشغيل",
            "done": False,
            "locked": not accounts,
        },
        {
            "n": 7, "key": "join",
            "icon": "✅" if db.get("joined_links") else "⏳",
            "label": "انضمام ذكي",
            "detail": f"{len(db.get('joined_links', []))} رابط تم الانضمام" if db.get("joined_links") else "لم يبدأ بعد",
            "done": bool(db.get("joined_links")),
            "locked": not channels,
        },
    ]

    # Determine recommended next step
    next_step = None
    for s in steps:
        if not s.get("locked") and not s["done"]:
            next_step = s
            break
        if s.get("in_progress"):
            next_step = s
            break

    return {"steps": steps, "next": next_step, "last_idx": last_idx, "raw_count": raw_count}


def build_dashboard(db: dict) -> tuple[str, list]:
    flow   = get_flow_status(db)
    steps  = flow["steps"]
    nxt    = flow["next"]
    last   = flow["last_idx"]

    done_count = sum(1 for s in steps if s["done"])
    pct        = int(done_count / 7 * 100)
    bar        = "█" * done_count + "░" * (7 - done_count)

    # ── Compact step list ─────────────────────────────────────────────────────
    step_lines = []
    for s in steps:
        lock = "🔒" if s.get("locked") else ""
        step_lines.append(f"{s['icon']} {s['n']}. {lock}{s['label']} — {s['detail']}")

    text = (
        "🏥 **نظام الفلترة الطبية الذكي**\n"
        f"📊 {bar} {pct}%\n\n"
        + "\n".join(step_lines)
    )

    if nxt:
        tip = {
            "accounts": "ربط حساب تيليجرام",
            "channels": "إنشاء قنوات الأرشيف",
            "sources":  "إضافة مجموعات كمصادر",
            "harvest":  "جمع الروابط من المصادر",
            "sort":     f"استئناف من {last+1:,}" if last > 0 else "فرز وتصنيف الروابط",
            "discover": "اكتشاف روابط طبية جديدة",
            "join":     "الانضمام للقنوات الطبية",
        }.get(nxt["key"], "")
        text += f"\n\n💡 **التالي:** {nxt['label']} — {tip}"
    else:
        text += "\n\n🎉 **جميع الخطوات مكتملة!**"

    # ── Buttons ───────────────────────────────────────────────────────────────
    rows = []

    # Highlighted next step
    next_btn_map = {
        "accounts": ("➕ ربط حساب ◄",    b"add_acc"),
        "channels": ("📺 إنشاء القنوات ◄", b"make_ch"),
        "sources":  ("🔗 إضافة مصدر ◄",   b"add_src"),
        "harvest":  ("🌾 بدء الحصاد ◄",    b"harvest"),
        "sort":     ("▶️ استئناف الفرز ◄" if last > 0 else "⚡ بدء الفرز ◄", b"run_sort"),
        "discover": ("🧠 اكتشاف ذكي ◄",    b"smart_discover"),
        "join":     ("🤝 انضمام ذكي ◄",    b"smart_join"),
    }
    if nxt and nxt["key"] in next_btn_map:
        lbl, dat = next_btn_map[nxt["key"]]
        rows.append([Button.inline(lbl, dat)])

    rows += [
        [Button.inline("➕ حساب", b"add_acc"),   Button.inline("👤 حساباتي", b"list_acc"),  Button.inline("📊 إحصائيات", b"stats")],
        [Button.inline("📺 قنوات", b"make_ch"),  Button.inline("🔗 مصادر",   b"list_src"),  Button.inline("✏️ أضف مصدر", b"add_src")],
        [Button.inline("🌾 حصاد", b"harvest"),   Button.inline("⚡ فرز",     b"run_sort"),  Button.inline("🧠 اكتشاف",  b"smart_discover")],
        [Button.inline("🤝 انضمام", b"smart_join"), Button.inline("🧹 مسح الذاكرة", b"clear_mem")],
    ]

    return text, rows


# ─────────────────────────────────────────────────────────────────────────────
# Nav helpers — reusable nav row + post-step guidance
# ─────────────────────────────────────────────────────────────────────────────

def nav_row(prev_step_btn: bytes | None = None) -> list:
    """Bottom navigation: [◀ back step] [🏠 home]."""
    row = []
    if prev_step_btn:
        row.append(Button.inline("◀️ رجوع", prev_step_btn))
    row.append(Button.inline("🏠 القائمة الرئيسية", b"home"))
    return row


async def send_next_step_hint(next_key: str, db: dict):
    """After completing a step, tell user what to do next."""
    flow = get_flow_status(db)
    nxt  = flow["next"]
    if not nxt:
        await status_msg(
            "🎉 **جميع الخطوات مكتملة!**\n"
            "البوت جاهز بالكامل. استخدم /start للوحة التحكم."
        )
        return

    hints = {
        "channels":  ("📺 القنوات",      b"make_ch"),
        "sources":   ("🔗 إضافة مصدر",   b"add_src"),
        "harvest":   ("🌾 بدء الحصاد",   b"harvest"),
        "sort":      ("⚡ بدء الفرز",    b"run_sort"),
        "discover":  ("🧠 اكتشاف ذكي",   b"smart_discover"),
        "join":      ("🤝 انضمام ذكي",    b"smart_join"),
    }
    if nxt["key"] in hints:
        lbl, dat = hints[nxt["key"]]
        await bot.send_message(
            OWNER_ID,
            f"✅ **تم!** الخطوة التالية الموصى بها:\n\n"
            f"**{nxt['n']}. {nxt['label']}** — _{nxt['detail']}_",
            buttons=[[Button.inline(f"⏭️ {lbl}", dat)], nav_row()],
            parse_mode="md",
        )


# ─────────────────────────────────────────────────────────────────────────────
# /start & 🏠 Home
# ─────────────────────────────────────────────────────────────────────────────

async def show_dashboard(target):
    """Send or edit the dashboard. target can be a message event or a callback event."""
    db.update(load_db())
    text, buttons = build_dashboard(db)
    # Try to edit existing message first (cleaner UX), fall back to new message
    try:
        await target.edit(text, buttons=buttons, parse_mode="md")
    except Exception:
        try:
            await target.respond(text, buttons=buttons, parse_mode="md")
        except Exception:
            await bot.send_message(OWNER_ID, text, buttons=buttons, parse_mode="md")


@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    db.update(load_db())
    if _is_authorized(event.sender_id):
        text, buttons = build_dashboard(db)
        await event.respond(text, buttons=buttons, parse_mode="md")
        return

    user_id_str = str(event.sender_id)
    if user_id_str in db.get("pending_requests", {}):
        await event.respond(
            "⏳ **طلبك قيد المراجعة**\n\nسيتم إشعارك فور قبول أو رفض طلبك من المالك.",
            parse_mode="md",
        )
        return

    sender = await event.get_sender()
    name = getattr(sender, "first_name", "") or ""
    await event.respond(
        f"👋 مرحباً {name}!\n\n"
        "هذا البوت خاص ويتطلب موافقة المالك للوصول.\n\n"
        "هل تريد إرسال طلب وصول للمالك؟",
        buttons=[[Button.inline("📨 إرسال طلب الوصول", f"req_{event.sender_id}".encode())]],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"home"))
@owner_only
async def home_handler(event):
    await event.answer()
    await show_dashboard(event)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Add account
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"add_acc"))
@admin_only
async def add_acc_handler(event):
    await event.answer()
    count = len(db.get("accounts", []))

    async with bot.conversation(OWNER_ID, timeout=120) as conv:
        await conv.send_message(
            f"**①  ربط حساب تيليجرام**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"الحسابات المرتبطة حالياً: **{count}**\n\n"
            f"📱 أرسل رقم الهاتف بالصيغة الدولية:\n"
            f"`+9671234567890`",
            buttons=[nav_row()],
            parse_mode="md",
        )
        phone_msg = await conv.get_response()
        phone     = phone_msg.text.strip()

        await conv.send_message("⏳ جاري إرسال كود التحقق...")
        success, result = await AccountManager.add_account_interactive(conv, phone)

        if success:
            is_new = result not in db["accounts"]
            if is_new:
                db["accounts"].append(result)
                save_db(db)

            info = await AccountManager.get_account_info(result)
            await conv.send_message(
                f"✅ **تم ربط الحساب بنجاح!**\n\n"
                f"👤 {info['name']}  {info['username']}\n"
                f"☎️ {info['phone']}",
                parse_mode="md",
            )

            if is_new and db.get("channels"):
                await conv.send_message("⏳ جاري إضافة الحساب إلى قنوات الأرشيف...")
                invite_links = db.get("channels_invites", [])
                if invite_links:
                    # Use invite links — works even when entity cache is empty
                    try:
                        summary = await join_accounts_via_invites([result], invite_links, db, save_db)
                        s = list(summary.values())[0] if summary else {}
                        await conv.send_message(
                            f"✅ تم انضمام الحساب للقنوات عبر روابط الدعوة.\n"
                            f"انضم: {s.get('joined', 0)} | موجود مسبقاً: {s.get('already', 0)} | أخطاء: {s.get('errors', 0)}"
                        )
                    except Exception as e:
                        await conv.send_message(f"⚠️ فشل الانضمام عبر الروابط:\n{e}")
                else:
                    try:
                        await add_account_to_channels(result, db)
                        await conv.send_message("✅ تم إضافة الحساب لجميع القنوات كمسؤول.")
                    except Exception as e:
                        await conv.send_message(
                            f"⚠️ لم يتمكن من إضافته للقنوات تلقائياً.\n"
                            f"💡 استخدم زر **🔗 انضمام عبر روابط دعوة** من قائمة القنوات لإصلاح ذلك."
                        )

            await send_next_step_hint("channels", db)
        else:
            await conv.send_message(
                f"❌ **فشل ربط الحساب:**\n{result}",
                buttons=[[Button.inline("🔄 حاول مرة أخرى", b"add_acc")], nav_row()],
                parse_mode="md",
            )


# ─────────────────────────────────────────────────────────────────────────────
# List accounts
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"list_acc"))
@owner_only
async def list_acc_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond(
            "❌ لا توجد حسابات مرتبطة بعد.",
            buttons=[[Button.inline("➕ ربط حساب الآن", b"add_acc")], nav_row()],
        )
        return

    lines = [f"👤 **الحسابات المرتبطة ({len(db['accounts'])}):**\n"]
    for i, acc in enumerate(db["accounts"], 1):
        info = await AccountManager.get_account_info(acc)
        lines.append(f"{i}. **{info['name']}** {info['username']}\n   ☎️ {info['phone']}")

    await event.respond(
        "\n".join(lines),
        buttons=[[Button.inline("➕ إضافة حساب آخر", b"add_acc")], nav_row()],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Create archive channels
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"make_ch"))
@owner_only
async def make_ch_handler(event):
    await event.answer("⏳ جاري إنشاء القنوات...")

    if not db["accounts"]:
        await event.respond(
            "🔒 **الخطوة 2 مقفلة**\n\nيجب ربط حساب أولاً (الخطوة 1) قبل إنشاء القنوات.",
            buttons=[[Button.inline("➕ ربط حساب ◄", b"add_acc")], nav_row()],
            parse_mode="md",
        )
        return

    existing = len(db.get("channels", {}))
    if existing >= 7:
        has_invites = bool(db.get("channels_invites"))
        await event.respond(
            f"✅ **القنوات السبع موجودة بالفعل!**\n\n"
            + "\n".join(f"• {v}" for v in CHANNEL_KEYS.values())
            + "\n\n💡 إذا لم تستطع الحسابات الوصول للقنوات، استخدم روابط الدعوة للانضمام مباشرة.",
            buttons=[
                [Button.inline("🔗 انضمام عبر روابط دعوة", b"join_via_invites")],
                [Button.inline("➕ أضفني للقنوات كمسؤول", b"add_owner_to_ch")],
                [Button.inline("🔄 إعادة إنشاء القنوات", b"recreate_channels_confirm")],
                [Button.inline("🔁 إعادة الفرز من البداية", b"resort_from_scratch_confirm")],
                [Button.inline("⏭️ إضافة مصادر ◄", b"add_src")],
                nav_row(),
            ],
            parse_mode="md",
        )
        return

    await event.respond(
        "**②  إنشاء قنوات الأرشيف السبع**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ جاري الإنشاء، يرجى الانتظار...\n\n"
        + "\n".join(f"🔄 {v}" for v in CHANNEL_KEYS.values()),
        parse_mode="md",
    )

    created = await create_archive_channels(db["accounts"][0], db, save_db)

    lines = ["✅ **تم إنشاء قنوات الأرشيف السبع:**\n"]
    for key, ch_id in created.items():
        title  = CHANNEL_KEYS.get(key, key)
        status = f"✅ `{ch_id}`" if isinstance(ch_id, int) else f"⚠️ {ch_id}"
        lines.append(f"• {title}: {status}")

    await event.respond(
        "\n".join(lines),
        buttons=[[Button.inline("⏭️ إضافة مصادر ◄", b"add_src")], nav_row(b"add_acc")],
        parse_mode="md",
    )
    await send_next_step_hint("sources", db)


@bot.on(events.CallbackQuery(data=b"add_owner_to_ch"))
@owner_only
async def add_owner_to_ch_handler(event):
    await event.answer("⏳ جاري إضافتك للقنوات...")

    if not db.get("accounts"):
        await event.respond(
            "❌ لا يوجد حساب مرتبط. أضف حساباً أولاً.",
            buttons=[nav_row()],
        )
        return

    if not db.get("channels"):
        await event.respond(
            "❌ لا توجد قنوات مُنشأة بعد.",
            buttons=[nav_row()],
        )
        return

    await event.respond("⏳ جاري إضافتك كمسؤول في القنوات السبع، يرجى الانتظار...")

    try:
        await add_owner_to_channels(db)
        await event.respond(
            "✅ **تمت إضافتك كمسؤول في جميع القنوات السبع!**\n\n"
            "ستجد القنوات الآن في قائمة محادثاتك.",
            buttons=[[Button.inline("⏭️ إضافة مصادر ◄", b"add_src")], nav_row()],
            parse_mode="md",
        )
    except Exception as e:
        await event.respond(
            f"⚠️ حدث خطأ: {e}",
            buttons=[nav_row()],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Join via invite links
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"join_via_invites"))
@owner_only
async def join_via_invites_handler(event):
    await event.answer()

    if not db.get("accounts"):
        await event.respond(
            "❌ لا يوجد حساب مرتبط. أضف حساباً أولاً.",
            buttons=[nav_row()],
        )
        return

    if not db.get("channels"):
        await event.respond(
            "❌ لا توجد قنوات مُنشأة بعد.",
            buttons=[nav_row()],
        )
        return

    stored_invites = db.get("channels_invites", [])

    async with bot.conversation(OWNER_ID, timeout=180) as conv:
        if stored_invites:
            await conv.send_message(
                f"🔗 **روابط الدعوة المحفوظة ({len(stored_invites)}):**\n"
                + "\n".join(f"• `{l}`" for l in stored_invites)
                + "\n\nهل تريد استخدام هذه الروابط أم إدخال روابط جديدة؟\n"
                "أرسل **نعم** للاستخدام، أو أرسل الروابط الجديدة (كل رابط في سطر):",
                buttons=[nav_row()],
                parse_mode="md",
            )
        else:
            await conv.send_message(
                "🔗 **انضمام عبر روابط الدعوة**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "أرسل روابط دعوة قنوات الأرشيف السبع (كل رابط في سطر):\n"
                "`https://t.me/+XXXXXXXXXXXX`",
                buttons=[nav_row()],
                parse_mode="md",
            )

        reply = await conv.get_response()
        text  = reply.text.strip()

        if text.lower() in ("نعم", "yes", "y") and stored_invites:
            invite_links = stored_invites
        else:
            invite_links = [l.strip() for l in text.split("\n") if "t.me/" in l]
            if not invite_links:
                await conv.send_message("❌ لم يتم العثور على روابط صالحة. حاول مرة أخرى.")
                return
            # Save for future use
            db["channels_invites"] = invite_links
            save_db(db)

        accounts = db.get("accounts", [])
        await conv.send_message(
            f"⏳ **جاري الانضمام عبر {len(invite_links)} رابط دعوة...**\n"
            f"الحسابات: {len(accounts)}\n\n"
            "قد يستغرق هذا بضع دقائق...",
            parse_mode="md",
        )

        summary = await join_accounts_via_invites(accounts, invite_links, db, save_db)

        lines = ["✅ **اكتمل الانضمام عبر روابط الدعوة:**\n"]
        total_joined  = 0
        total_already = 0
        total_errors  = 0
        for sess, s in summary.items():
            acc_name = sess.split("/")[-1]
            total_joined  += s.get("joined",  0)
            total_already += s.get("already", 0)
            total_errors  += s.get("errors",  0)
            lines.append(
                f"• حساب `{acc_name}`: "
                f"✅ {s.get('joined',0)} انضم | "
                f"♻️ {s.get('already',0)} موجود | "
                f"❌ {s.get('errors',0)} خطأ"
            )

        hashes_found = len(db.get("channels_hashes", {}))
        lines.append(
            f"\n📌 هاش الوصول محفوظ لـ **{hashes_found}/7** قناة"
        )

        await conv.send_message(
            "\n".join(lines),
            buttons=[[Button.inline("⏭️ إضافة مصادر ◄", b"add_src")], nav_row()],
            parse_mode="md",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Recreate channels — confirmation + execution
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"recreate_channels_confirm"))
@admin_only
async def recreate_channels_confirm_handler(event):
    await event.answer()
    await event.respond(
        "⚠️ **تحذير: إعادة إنشاء القنوات**\n\n"
        "سيتم:\n"
        "• حذف معرفات القنوات الحالية من الذاكرة\n"
        "• إنشاء 7 قنوات أرشيف جديدة\n\n"
        "⚠️ القنوات القديمة على تيليجرام **لن تُحذف** — فقط تُفقد الصلة بها.\n\n"
        "هل أنت متأكد؟",
        buttons=[
            [Button.inline("✅ نعم، أعد الإنشاء", b"recreate_channels_do")],
            [Button.inline("❌ إلغاء", b"make_ch")],
            nav_row(),
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"recreate_channels_do"))
@admin_only
async def recreate_channels_do_handler(event):
    await event.answer("⏳ جاري إعادة الإنشاء...")

    if not db.get("accounts"):
        await event.respond("❌ لا يوجد حساب مرتبط. أضف حساباً أولاً.", buttons=[nav_row()])
        return

    # Clear old channel data
    db["channels"] = {}
    db.pop("channels_hashes", None)
    save_db(db)

    await event.respond(
        "🔄 **جاري إنشاء قنوات الأرشيف السبع من جديد...**\n"
        + "\n".join(f"🔄 {v}" for v in CHANNEL_KEYS.values()),
        parse_mode="md",
    )

    created = await create_archive_channels(db["accounts"][0], db, save_db)

    lines = ["✅ **تم إعادة إنشاء قنوات الأرشيف:**\n"]
    for key, ch_id in created.items():
        title  = CHANNEL_KEYS.get(key, key)
        status = f"✅ `{ch_id}`" if isinstance(ch_id, int) else f"⚠️ {ch_id}"
        lines.append(f"• {title}: {status}")

    lines.append(
        "\n💡 **التالي:** استخدم **🔗 انضمام عبر روابط دعوة** لإضافة باقي الحسابات."
    )

    await event.respond(
        "\n".join(lines),
        buttons=[
            [Button.inline("🔗 انضمام عبر روابط دعوة", b"join_via_invites")],
            [Button.inline("⏭️ إضافة مصادر ◄", b"add_src")],
            nav_row(),
        ],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Re-sort from scratch (clears seen-set + progress, re-processes all links)
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"resort_from_scratch_confirm"))
@owner_only
async def resort_from_scratch_confirm_handler(event):
    await event.answer()
    raw_count  = get_raw_count()
    seen_count = get_seen_count()
    await event.respond(
        "⚠️ **إعادة الفرز من البداية**\n\n"
        "سيتم:\n"
        f"• مسح سجل الروابط المرئية ({seen_count:,} رابط)\n"
        f"• إعادة ضبط مؤشر التقدم (من 0 / {raw_count:,})\n"
        "• إعادة فرز جميع الروابط وإرسالها للقنوات\n\n"
        "⚠️ هذا يفيد إذا كان الفرز السابق لم يُرسل الروابط للقنوات بسبب مشكلة في الوصول.\n\n"
        "هل أنت متأكد؟",
        buttons=[
            [Button.inline("✅ نعم، أعد الفرز من البداية", b"resort_from_scratch_do")],
            [Button.inline("❌ إلغاء", b"make_ch")],
            nav_row(),
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"resort_from_scratch_do"))
@owner_only
async def resort_from_scratch_do_handler(event):
    await event.answer("⏳ جاري المسح وإعادة الضبط...")

    # 1) Clear the seen-set file
    clear_seen()

    # 2) Reset progress index and stats
    db.setdefault("progress", {})["last_sorted_index"] = 0
    db.setdefault("stats", {}).update({
        "total_sorted": 0,
        "total_broken": 0,
        "total_invite": 0,
        "total_found":  0,
    })
    save_db(db)

    raw_count = get_raw_count()
    await event.respond(
        f"✅ **تم إعادة الضبط.**\n\n"
        f"• سجل الروابط المرئية: ممسوح\n"
        f"• مؤشر التقدم: 0 / {raw_count:,}\n\n"
        f"اضغط **⚡ فرز** لبدء الفرز من جديد وإرسال الروابط للقنوات.",
        buttons=[
            [Button.inline("⚡ بدء الفرز الآن ◄", b"run_sort")],
            nav_row(),
        ],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Add sources
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"add_src"))
@owner_only
async def add_src_handler(event):
    await event.answer()

    current = len(db.get("sources", []))

    async with bot.conversation(event.sender_id, timeout=180) as conv:
        await conv.send_message(
            f"**③  إضافة مصادر الروابط**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"المصادر الحالية: **{current}**\n\n"
            f"📋 أرسل روابط مجموعات تيليجرام (كل رابط في سطر):\n"
            f"`https://t.me/medical_links_group`\n"
            f"`https://t.me/+AbCdEfGh1234`",
            buttons=[nav_row(b"make_ch")],
            parse_mode="md",
        )
        links_msg   = await conv.get_response()
        new_sources = [l.strip() for l in links_msg.text.strip().split("\n") if l.strip()]

        added = 0
        for src in new_sources:
            if src not in db["sources"]:
                db["sources"].append(src)
                added += 1
        save_db(db)

        await conv.send_message(
            f"✅ **تمت إضافة {added} مصدر جديد.**\n"
            f"📋 إجمالي المصادر: **{len(db['sources'])}**",
            buttons=[
                [Button.inline("✏️ إضافة المزيد", b"add_src")],
                [Button.inline("⏭️ بدء الحصاد ◄",  b"harvest")],
                nav_row(b"make_ch"),
            ],
            parse_mode="md",
        )


# ─────────────────────────────────────────────────────────────────────────────
# List sources
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"list_src"))
@owner_only
async def list_src_handler(event):
    await event.answer()
    if not db["sources"]:
        await event.respond(
            "❌ لا توجد مصادر مضافة بعد.",
            buttons=[[Button.inline("✏️ إضافة مصدر ◄", b"add_src")], nav_row()],
        )
        return

    lines = [f"📋 **المصادر ({len(db['sources'])}):**\n"]
    for i, src in enumerate(db["sources"], 1):
        lines.append(f"{i}. `{src}`")

    await event.respond(
        "\n".join(lines),
        buttons=[[Button.inline("✏️ إضافة مصادر جديدة", b"add_src")], nav_row()],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Harvest
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"harvest"))
@owner_only
async def harvest_handler(event):
    await event.answer()

    if not db["accounts"]:
        await event.respond(
            "🔒 يجب ربط حساب أولاً.",
            buttons=[[Button.inline("➕ ربط حساب ◄", b"add_acc")], nav_row()],
        )
        return
    if not db["sources"]:
        await event.respond(
            "🔒 يجب إضافة مصادر أولاً.",
            buttons=[[Button.inline("✏️ إضافة مصدر ◄", b"add_src")], nav_row()],
        )
        return

    if sorter_ctrl.is_harvesting:
        await event.respond(
            "⚠️ **الحصاد يعمل بالفعل.**\n\nانتظر حتى ينتهي الحصاد الحالي، أو اضغط إيقاف.",
            buttons=[
                [Button.inline("⏹ إيقاف الحصاد", b"stop_harvest")],
                nav_row(),
            ],
            parse_mode="md",
        )
        return

    existing = get_raw_count()
    all_sessions = db["accounts"]
    _stop_btn = [[Button.inline("⏹ إيقاف الحصاد", b"stop_harvest")]]
    prog_msg = await event.respond(
        f"🌾 **الحصاد الموزع — جارٍ...**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 حسابات: **{len(all_sessions)}** | 📋 مصادر: **{len(db['sources'])}**\n"
        f"📦 روابط موجودة: **{existing:,}**\n\n"
        f"⏳ جاري الانضمام للمصادر وتوزيع العمل...",
        buttons=_stop_btn,
        parse_mode="md",
    )
    prog_cb = make_edit_callback(prog_msg.id, OWNER_ID, fixed_buttons=_stop_btn)

    sorter_ctrl.start_harvest()
    try:
        harvested = await harvest_sources(
            status_callback=prog_cb,
            db=db,
            sessions=all_sessions,
        )
    finally:
        sorter_ctrl.end_harvest()

    new_count = len(harvested) - existing
    try:
        await bot.edit_message(OWNER_ID, prog_msg.id, "🌾 **الحصاد — اكتمل ✅**", buttons=[nav_row(b"add_src")])
    except Exception:
        pass
    await bot.send_message(
        OWNER_ID,
        f"🎉 **اكتمل الحصاد!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 إجمالي الروابط: **{len(harvested):,}**\n"
        f"🆕 روابط جديدة: **{new_count:,}**\n\n"
        f"💾 محفوظة في `raw_links.json`\nهل تريد بدء الفرز الآن؟",
        buttons=[
            [Button.inline("⏭️ بدء الفرز الآن ◄", b"run_sort")],
            nav_row(b"add_src"),
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"stop_harvest"))
@owner_only
async def stop_harvest_handler(event):
    await event.answer("⏹ جاري إيقاف الحصاد بعد الرسالة الحالية...")
    sorter_ctrl.stop_harvest()
    await event.answer("⏹ تم إرسال إشارة الإيقاف — سيتوقف الحصاد وتُحفظ الروابط.", alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Sort
# ─────────────────────────────────────────────────────────────────────────────

def _sort_control_buttons(paused: bool = False) -> list:
    """Inline buttons shown while sorting is active."""
    if paused:
        return [
            [Button.inline("▶️ استئناف", b"sort_resume"),
             Button.inline("⏹ إيقاف وحفظ", b"sort_stop")],
        ]
    return [
        [Button.inline("⏸ إيقاف مؤقت", b"sort_pause"),
         Button.inline("⏹ إيقاف وحفظ", b"sort_stop")],
    ]


@bot.on(events.CallbackQuery(data=b"run_sort"))
@owner_only
async def run_sort_handler(event):
    await event.answer()

    if not db["accounts"]:
        await event.respond(
            "🔒 يجب ربط حساب أولاً.",
            buttons=[[Button.inline("➕ ربط حساب ◄", b"add_acc")], nav_row()],
        )
        return
    if not db.get("channels"):
        await event.respond(
            "🔒 يجب إنشاء القنوات أولاً.",
            buttons=[[Button.inline("📺 إنشاء القنوات ◄", b"make_ch")], nav_row()],
        )
        return

    if sorter_ctrl.is_harvesting:
        await event.respond(
            "⚠️ **الحصاد يعمل الآن.** لا يمكن تشغيل الفرز في نفس الوقت.\n\nانتظر حتى ينتهي الحصاد.",
            buttons=[nav_row()],
            parse_mode="md",
        )
        return

    raw = load_raw_links()
    if not raw:
        await event.respond(
            "🔒 لا توجد روابط. قم بتشغيل الحصاد أولاً.",
            buttons=[[Button.inline("🌾 بدء الحصاد ◄", b"harvest")], nav_row()],
        )
        return

    sorter_ctrl.reset()

    start_from = db.get("progress", {}).get("last_sorted_index", 0)
    remaining  = len(raw) - start_from
    pct        = int(start_from / len(raw) * 100) if raw else 0
    bar_f      = "▓" * (pct // 10)
    bar_e      = "░" * (10 - pct // 10)

    # Send ONE persistent progress message — the sorter will EDIT this message
    progress_msg = await bot.send_message(
        OWNER_ID,
        f"📊 **الفرز الشامل — جارٍ...**\n"
        f"[{bar_f}{bar_e}] {pct}%\n\n"
        f"تم: **{start_from:,}** / {len(raw):,} رابط\n"
        f"{'🔄 استئناف من حيث توقفنا...' if start_from > 0 else '⚡ بدء الفرز الشامل...'}\n\n"
        f"_الأرقام تُحدَّث تلقائياً — لا تُرسل رسائل جديدة._",
        buttons=_sort_control_buttons(),
        parse_mode="md",
    )
    sorter_ctrl.set_progress_msg(progress_msg.id, OWNER_ID)

    await run_sorter(
        status_callback=status_msg,
        db=db,
        accounts=db["accounts"],
        bot_client=bot,
        start_from=start_from,
    )
    sorter_ctrl.clear_progress_msg()

    if not sorter_ctrl.is_stopped():
        await bot.send_message(
            OWNER_ID,
            f"🎯 **اكتمل الفرز بنجاح!**\n\n"
            f"✅ مرتبة: {db['stats'].get('total_sorted', 0):,}\n"
            f"💀 تالفة/منتهية: {db['stats'].get('total_broken', 0):,}\n"
            f"🔐 دعوات خاصة: {db['stats'].get('total_invite', 0):,}\n\n"
            f"📝 الروابط \"التالفة\" = يوزرنيم محذوف فعلاً.\n"
            f"الدعوات الخاصة = رابط +invite لم ينضم إليه الحساب بعد.",
            buttons=[
                [Button.inline("🧠 اكتشاف ذكي ◄", b"smart_discover"),
                 Button.inline("🤝 انضمام ذكي ◄",  b"smart_join")],
                nav_row(),
            ],
            parse_mode="md",
        )


@bot.on(events.CallbackQuery(data=b"sort_pause"))
@owner_only
async def sort_pause_handler(event):
    await event.answer("⏸ سيتوقف بعد الدفعة الحالية...")
    sorter_ctrl.pause()
    # Update the progress message buttons to show Resume instead of Pause
    if sorter_ctrl.progress_msg_id and sorter_ctrl.progress_chat_id:
        try:
            await bot.edit_message(
                sorter_ctrl.progress_chat_id,
                sorter_ctrl.progress_msg_id,
                buttons=_sort_control_buttons(paused=True),
            )
        except Exception:
            pass
    await event.answer("⏸ الفرز سيتوقف بعد الدفعة الحالية.", alert=True)


@bot.on(events.CallbackQuery(data=b"sort_resume"))
@owner_only
async def sort_resume_handler(event):
    await event.answer("▶️ جاري الاستئناف...")
    sorter_ctrl.resume()
    if sorter_ctrl.progress_msg_id and sorter_ctrl.progress_chat_id:
        try:
            await bot.edit_message(
                sorter_ctrl.progress_chat_id,
                sorter_ctrl.progress_msg_id,
                buttons=_sort_control_buttons(paused=False),
            )
        except Exception:
            pass


@bot.on(events.CallbackQuery(data=b"sort_stop"))
@owner_only
async def sort_stop_handler(event):
    await event.answer("⏹ جاري الإيقاف وحفظ التقدم...")
    sorter_ctrl.stop()
    await event.answer("⏹ تم إيقاف الفرز. التقدم محفوظ — يمكن الاستئناف لاحقاً.", alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Smart Discovery
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"smart_discover"))
@owner_only
async def smart_discover_handler(event):
    await event.answer()

    if not db["accounts"]:
        await event.respond(
            "🔒 يجب ربط حساب أولاً.",
            buttons=[[Button.inline("➕ ربط حساب ◄", b"add_acc")], nav_row()],
        )
        return

    raw_count = get_raw_count()
    await event.respond(
        f"**⑥  الاكتشاف الذكي**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 الروابط الحالية: {raw_count:,}\n\n"
        f"🧠 يبحث بـ **8 طرق:**\n"
        f"  1️⃣ بحث بكلمات مفتاحية (100+ استعلام)\n"
        f"  2️⃣ قنوات مشابهة (Telegram AI)\n"
        f"  3️⃣ روابط من البيو والوصف\n"
        f"  4️⃣ روابط من الرسائل الأخيرة\n"
        f"  5️⃣ أنماط أسماء المستخدمين (GCC)\n"
        f"  6️⃣ مصفوفة الجهات × التخصصات 🆕\n"
        f"  7️⃣ بحث بالهاشتاقات الطبية 🆕\n"
        f"  8️⃣ Google Dorks (site:t.me) 🆕\n\n"
        f"هل تريد البدء؟",
        buttons=[
            [Button.inline("🚀 ابدأ الاكتشاف الآن", b"confirm_discover")],
            nav_row(b"run_sort"),
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"confirm_discover"))
@owner_only
async def confirm_discover_handler(event):
    await event.answer("🧠 جاري الاكتشاف...")

    archive_ids  = {k: v for k, v in db.get("channels", {}).items() if isinstance(v, int)}
    source_links = db.get("sources", [])

    prog_msg = await event.respond(
        "🧠 **الاكتشاف الذكي — جارٍ...**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ جاري التحضير...",
        parse_mode="md",
    )
    prog_cb = make_edit_callback(prog_msg.id, OWNER_ID)

    new_count = await run_smart_discovery(
        status_callback=prog_cb,
        db=db,
        accounts=db["accounts"],
        archive_channel_ids=archive_ids,
        source_links=source_links,
    )

    try:
        await bot.edit_message(OWNER_ID, prog_msg.id, "🧠 **الاكتشاف الذكي — اكتمل ✅**", buttons=[nav_row(b"run_sort")])
    except Exception:
        pass

    if new_count > 0:
        await bot.send_message(
            OWNER_ID,
            f"🎉 اكتُشف **{new_count:,}** رابط جديد!\nهل تريد الفرز الآن؟",
            buttons=[
                [Button.inline("⏭️ فرز الروابط الجديدة ◄", b"run_sort")],
                nav_row(b"run_sort"),
            ],
            parse_mode="md",
        )
    else:
        await bot.send_message(
            OWNER_ID,
            "ℹ️ لم يُكتشف روابط جديدة. المصادر الحالية قد تكون مستنفدة.",
            buttons=[
                [Button.inline("⏭️ انضمام ذكي ◄", b"smart_join")],
                nav_row(b"run_sort"),
            ],
            parse_mode="md",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Smart Join
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"smart_join"))
@owner_only
async def smart_join_handler(event):
    await event.answer()

    if not db["accounts"]:
        await event.respond(
            "🔒 يجب ربط حساب أولاً.",
            buttons=[[Button.inline("➕ ربط حساب ◄", b"add_acc")], nav_row()],
        )
        return
    if not db.get("channels"):
        await event.respond(
            "🔒 يجب إنشاء القنوات وتشغيل الفرز أولاً.",
            buttons=[[Button.inline("📺 إنشاء القنوات ◄", b"make_ch")], nav_row()],
        )
        return

    key_map = {
        "channels": (b"jch_channels", "📢 قناة القنوات"),
        "groups":   (b"jch_groups",   "👥 قناة المجموعات"),
        "invite":   (b"jch_invite",   "🔐 روابط الدعوة"),
        "addlist":  (b"jch_addlist",  "📂 المجلدات"),
        "bots":     (b"jch_bots",     "🤖 البوتات"),
        "other":    (b"jch_other",    "🌐 الروابط الأخرى"),
    }
    channel_buttons = []
    for key, (cb_data, label) in key_map.items():
        if key in db.get("channels", {}):
            channel_buttons.append([Button.inline(label, cb_data)])

    if not channel_buttons:
        await event.respond(
            "🔒 لا توجد قنوات أرشيف منشأة بعد.",
            buttons=[[Button.inline("📺 إنشاء القنوات ◄", b"make_ch")], nav_row()],
        )
        return

    joined_count = len(db.get("joined_links", []))
    await event.respond(
        f"**⑦  الانضمام الذكي**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤝 تم الانضمام حتى الآن: **{joined_count:,}**\n\n"
        f"اختر القناة التي تريد الانضمام إلى روابطها:",
        buttons=channel_buttons + [nav_row(b"smart_discover")],
        parse_mode="md",
    )


async def _ask_join_count_and_start(event, source_key: str):
    await event.answer()
    channel_label = CHANNEL_KEYS.get(source_key, source_key)

    async with bot.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message(
            f"✅ المصدر: **{channel_label}**\n\n"
            f"📊 كم رابطاً تريد الانضمام إليه؟ (مثال: `20`)",
            buttons=[nav_row(b"smart_join")],
            parse_mode="md",
        )
        count_msg = await conv.get_response()

        try:
            max_joins = int(count_msg.text.strip())
            if max_joins <= 0:
                raise ValueError
        except ValueError:
            await conv.send_message(
                "❌ رقم غير صحيح. تم الإلغاء.",
                buttons=[[Button.inline("🔄 حاول مرة أخرى", b"smart_join")], nav_row()],
            )
            return

        await conv.send_message(
            f"⏳ سيبدأ الانضمام إلى **{max_joins}** رابط من **{channel_label}**\n"
            f"🛡 نظام الحماية الذكي مفعّل (دفعات متقطعة).",
            parse_mode="md",
        )

    source_ch_id = db["channels"].get(source_key)
    links_to_join = []

    if source_ch_id:
        try:
            async with TelegramClient(db["accounts"][0], API_ID, API_HASH) as client:
                async for msg in client.iter_messages(int(source_ch_id), limit=500):
                    if msg.text:
                        found = re.findall(r"https?://t\.me/[\+a-zA-Z0-9_/]+", msg.text)
                        for lnk in found:
                            lnk = lnk.strip().rstrip("/")
                            if lnk not in links_to_join:
                                links_to_join.append(lnk)
                        if len(links_to_join) >= max_joins * 3:
                            break
        except Exception as e:
            await status_msg(f"❌ خطأ في قراءة روابط القناة: {e}")

    if not links_to_join:
        await bot.send_message(
            OWNER_ID,
            "❌ لا روابط في القناة.\nتأكد من تشغيل الفرز أولاً.",
            buttons=[[Button.inline("⚡ تشغيل الفرز أولاً ◄", b"run_sort")], nav_row()],
        )
        return

    join_prog = await bot.send_message(
        OWNER_ID,
        f"🤝 **الانضمام الذكي — جارٍ...**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"[░░░░░░░░░░] 0%\n"
        f"📋 روابط للانضمام: **{min(max_joins, len(links_to_join))}**\n\n"
        f"⏳ جاري التحضير...",
        parse_mode="md",
    )
    join_cb = make_edit_callback(join_prog.id, OWNER_ID)

    await run_smart_joiner(
        status_callback=join_cb,
        links_to_join=links_to_join,
        accounts=db["accounts"],
        db=db,
        max_joins=max_joins,
    )

    try:
        await bot.edit_message(OWNER_ID, join_prog.id, "🤝 **الانضمام الذكي — اكتمل ✅**", buttons=[nav_row(b"smart_join")])
    except Exception:
        pass


@bot.on(events.CallbackQuery(data=b"jch_channels"))
@owner_only
async def jch_channels(event):
    await _ask_join_count_and_start(event, "channels")

@bot.on(events.CallbackQuery(data=b"jch_groups"))
@owner_only
async def jch_groups(event):
    await _ask_join_count_and_start(event, "groups")

@bot.on(events.CallbackQuery(data=b"jch_invite"))
@owner_only
async def jch_invite(event):
    await _ask_join_count_and_start(event, "invite")

@bot.on(events.CallbackQuery(data=b"jch_addlist"))
@owner_only
async def jch_addlist(event):
    await _ask_join_count_and_start(event, "addlist")

@bot.on(events.CallbackQuery(data=b"jch_bots"))
@owner_only
async def jch_bots(event):
    await _ask_join_count_and_start(event, "bots")

@bot.on(events.CallbackQuery(data=b"jch_other"))
@owner_only
async def jch_other(event):
    await _ask_join_count_and_start(event, "other")


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"stats"))
@owner_only
async def stats_handler(event):
    await event.answer()
    stats = db.get("stats", {})
    seen  = get_seen_count()
    raw   = get_raw_count()
    last  = db.get("progress", {}).get("last_sorted_index", 0)
    pct   = int(last / raw * 100) if raw else 0

    bar_filled = "█" * (pct // 10)
    bar_empty  = "░" * (10 - pct // 10)

    text = (
        "📊 **إحصائيات النظام**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**الفرز:** {bar_filled}{bar_empty} {pct}%\n"
        f"  ↳ {last:,} / {raw:,} رابط\n\n"
        f"📦 **الروابط الخام:** {raw:,}\n"
        f"🔍 **المفحوصة:** {seen:,}\n"
        f"✅ **المرتبة بنجاح:** {stats.get('total_sorted', 0):,}\n"
        f"💀 **تالفة (منتهية فعلاً):** {stats.get('total_broken', 0):,}\n"
        f"🔐 **دعوات خاصة:** {stats.get('total_invite', 0):,}\n"
        f"⏭️ **المتخطاة (مكررة):** {stats.get('total_skipped_duplicate', 0):,}\n"
        f"🤝 **تم الانضمام إليها:** {len(db.get('joined_links', [])):,}\n\n"
        f"👤 **الحسابات:** {len(db.get('accounts', []))}\n"
        f"🔗 **المصادر:** {len(db.get('sources', []))}\n"
        f"📺 **القنوات:** {len(db.get('channels', {}))}/7\n\n"
        f"ℹ️ **ملاحظة عن التالفة:**\n"
        f"الروابط التالفة = يوزرنيم محذوف أو حساب غير موجود.\n"
        f"روابط +invite التي لم ينضم إليها الحساب → قناة 🔐 الدعوات."
    )

    await event.respond(text, buttons=[nav_row()], parse_mode="md")


# ─────────────────────────────────────────────────────────────────────────────
# Clear memory
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"clear_mem"))
@owner_only
async def clear_mem_handler(event):
    await event.answer()
    await event.respond(
        "⚠️ **تحذير — مسح الذاكرة**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "سيتم مسح:\n"
        "• ذاكرة الروابط المفحوصة\n"
        "• مؤشر التقدم في الفرز\n\n"
        "هذا يعني إعادة الفرز من البداية.\n"
        "**هل أنت متأكد؟**",
        buttons=[
            [Button.inline("✅ نعم، امسح الذاكرة", b"confirm_clear")],
            [Button.inline("❌ إلغاء", b"home")],
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"confirm_clear"))
@owner_only
async def confirm_clear_handler(event):
    await event.answer()
    clear_seen()
    db["stats"] = {
        "total_found": 0, "total_sorted": 0,
        "total_broken": 0, "total_skipped_duplicate": 0,
    }
    db["progress"]["last_sorted_index"] = 0
    save_db(db)
    await event.respond(
        "✅ **تم مسح الذاكرة.**\nيمكنك الآن بدء الفرز من جديد.",
        buttons=[[Button.inline("⚡ بدء الفرز ◄", b"run_sort")], nav_row()],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Access Request System
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(pattern=rb"req_(\d+)"))
async def request_access_handler(event):
    await event.answer()
    user_id = int(event.data.decode().split("_", 1)[1])

    if user_id != event.sender_id:
        await event.answer("❌ غير صالح.", alert=True)
        return

    db.update(load_db())
    if _is_authorized(user_id):
        await event.edit("✅ لديك وصول بالفعل. أرسل /start.", parse_mode="md")
        return

    sender = await event.get_sender()
    name     = getattr(sender, "first_name", "") or str(user_id)
    username = f"@{sender.username}" if getattr(sender, "username", None) else "بدون يوزرنيم"

    db.setdefault("pending_requests", {})[str(user_id)] = {
        "name": name,
        "username": username,
    }
    save_db(db)

    try:
        await bot.send_message(
            OWNER_ID,
            f"🔔 **طلب وصول جديد**\n\n"
            f"👤 الاسم: {name}\n"
            f"🆔 يوزرنيم: {username}\n"
            f"🔢 المعرف: `{user_id}`",
            buttons=[
                [Button.inline("✅ قبول", f"ap_{user_id}".encode()),
                 Button.inline("❌ رفض",  f"dn_{user_id}".encode())],
            ],
            parse_mode="md",
        )
    except Exception:
        pass

    await event.edit(
        "✅ **تم إرسال طلبك للمالك.**\n\nسيتم إشعارك فور البت في طلبك.",
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(pattern=rb"ap_(\d+)"))
@admin_only
async def approve_user_handler(event):
    user_id = int(event.data.decode().split("_", 1)[1])
    db.update(load_db())

    if user_id not in db.setdefault("trusted_users", []):
        db["trusted_users"].append(user_id)

    info = db.setdefault("pending_requests", {}).pop(str(user_id), {})
    save_db(db)

    try:
        await bot.send_message(
            user_id,
            "✅ **تم قبول طلبك!**\n\nأرسل /start لفتح لوحة التحكم.",
            parse_mode="md",
        )
    except Exception:
        pass

    name = info.get("name", str(user_id))
    await event.answer(f"✅ تم قبول {name}")
    await event.edit(
        f"✅ **تم قبول الوصول**\n👤 {name} | `{user_id}`",
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(pattern=rb"dn_(\d+)"))
@admin_only
async def deny_user_handler(event):
    user_id = int(event.data.decode().split("_", 1)[1])
    db.update(load_db())

    info = db.setdefault("pending_requests", {}).pop(str(user_id), {})
    save_db(db)

    try:
        await bot.send_message(
            user_id,
            "❌ **تم رفض طلبك.**\n\nللاستفسار تواصل مع المالك.",
            parse_mode="md",
        )
    except Exception:
        pass

    name = info.get("name", str(user_id))
    await event.answer(f"❌ تم رفض {name}")
    await event.edit(
        f"❌ **تم رفض الطلب**\n👤 {name} | `{user_id}`",
        parse_mode="md",
    )


@bot.on(events.NewMessage(pattern="/trusted"))
@admin_only
async def trusted_users_handler(event):
    db.update(load_db())
    trusted = db.get("trusted_users", [])

    if not trusted:
        await event.respond(
            "👥 **المستخدمون الموثوقون**\n\nلا يوجد مستخدمون موثوقون حتى الآن.\n\n"
            "ستصلك إشعارات عندما يطلب أحدهم الوصول.",
            parse_mode="md",
        )
        return

    lines = [f"👥 **المستخدمون الموثوقون ({len(trusted)}):**\n"]
    for uid in trusted:
        lines.append(f"• `{uid}`")

    lines.append("\n_لإزالة مستخدم: أرسل /revoke <ID>_")
    await event.respond("\n".join(lines), parse_mode="md")


@bot.on(events.NewMessage(pattern=r"/revoke (\d+)"))
@admin_only
async def revoke_user_handler(event):
    db.update(load_db())
    user_id = int(event.pattern_match.group(1))

    if user_id in db.get("trusted_users", []):
        db["trusted_users"].remove(user_id)
        save_db(db)
        await event.respond(f"✅ تم إلغاء وصول المستخدم `{user_id}`.", parse_mode="md")
        try:
            await bot.send_message(user_id, "🚫 تم إلغاء وصولك من قِبل المالك.", parse_mode="md")
        except Exception:
            pass
    else:
        await event.respond(f"❌ المعرف `{user_id}` غير موجود في قائمة الموثوقين.", parse_mode="md")


# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern="/whoami"))
async def whoami_handler(event):
    """No auth check — lets you find your real Telegram ID."""
    await event.respond(
        f"🪪 **معرفك في تيليجرام:**\n`{event.sender_id}`\n\n"
        f"📌 **OWNER_ID المحمّل:** `{OWNER_ID}`\n\n"
        + ("✅ أنت المالك." if event.sender_id == OWNER_ID else
           "❌ لا تتطابق! غيّر OWNER_ID في الأسرار ليساوي معرّفك."),
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Catch-all fallback — MUST be the last registered handler.
# Answers any callback that no specific handler above matched,
# so buttons never get stuck in a permanent loading state after restarts.
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery())
async def fallback_callback_handler(event):
    try:
        await event.answer("🔄 أعد تشغيل البوت بـ /start", alert=False)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    asyncio.create_task(_keep_alive_http())
    while True:
        try:
            await bot.start(bot_token=BOT_TOKEN)
            try:
                await bot(GetStateRequest())
            except Exception:
                pass
            print(f"🤖 البوت يعمل... OWNER_ID={OWNER_ID} | أرسل /start في تيليجرام للبدء.")
            await bot.run_until_disconnected()
        except Exception as e:
            print(f"⚠️ انقطع الاتصال: {e} — إعادة المحاولة خلال 10 ثوانٍ...")
            await asyncio.sleep(10)
        finally:
            try:
                await bot.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
