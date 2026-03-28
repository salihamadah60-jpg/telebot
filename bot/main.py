import os
import re
import asyncio
import random
import threading

from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError

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
from channel_setup import create_archive_channels, add_account_to_channels, add_owner_to_channels
from harvester import harvest_sources
from sorter import run_sorter
from joiner import run_smart_joiner
from searcher import run_smart_discovery
import state as sorter_ctrl

db = load_db()
bot = TelegramClient("bot_controller", API_ID, API_HASH)


# ─────────────────────────────────────────────────────────────────────────────
# Auth guard
# ─────────────────────────────────────────────────────────────────────────────

def owner_only(func):
    async def wrapper(event):
        if event.sender_id != OWNER_ID:
            msg = f"🚫 غير مصرح لك.\n\nهويتك: `{event.sender_id}`\nالمسموح به: `{OWNER_ID}`"
            try:
                await event.answer(msg, alert=True)
            except Exception:
                try:
                    await event.respond(msg, parse_mode="md")
                except Exception:
                    pass
            return
        await func(event)
    return wrapper


async def status_msg(text: str):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="md")
    except Exception:
        pass


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
@owner_only
async def start_handler(event):
    db.update(load_db())
    text, buttons = build_dashboard(db)
    await event.respond(text, buttons=buttons, parse_mode="md")


@bot.on(events.CallbackQuery(data=b"home"))
@owner_only
async def home_handler(event):
    await event.answer()
    await show_dashboard(event)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Add account
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"add_acc"))
@owner_only
async def add_acc_handler(event):
    await event.answer()
    count = len(db.get("accounts", []))

    await event.respond(
        f"**①  ربط حساب تيليجرام**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"الحسابات المرتبطة حالياً: **{count}**\n\n"
        f"📱 أرسل رقم الهاتف بالصيغة الدولية:\n"
        f"`+9671234567890`",
        buttons=[nav_row()],
        parse_mode="md",
    )

    async with bot.conversation(OWNER_ID, timeout=120) as conv:
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
                try:
                    await add_account_to_channels(result, db)
                    await conv.send_message("✅ تم إضافة الحساب لجميع القنوات كمسؤول.")
                except Exception as e:
                    await conv.send_message(f"⚠️ لم يتمكن من إضافته للقنوات تلقائياً:\n{e}")

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
        await event.respond(
            f"✅ **القنوات السبع موجودة بالفعل!**\n\n"
            + "\n".join(f"• {v}" for v in CHANNEL_KEYS.values())
            + "\n\n💡 إذا لم تظهر القنوات في حسابك، اضغط الزر أدناه لإضافتك إليها.",
            buttons=[
                [Button.inline("➕ أضفني للقنوات كمسؤول", b"add_owner_to_ch")],
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
# Step 3 — Add sources
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"add_src"))
@owner_only
async def add_src_handler(event):
    await event.answer()

    current = len(db.get("sources", []))
    await event.respond(
        f"**③  إضافة مصادر الروابط**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"المصادر الحالية: **{current}**\n\n"
        f"📋 أرسل روابط مجموعات تيليجرام (كل رابط في سطر):\n"
        f"`https://t.me/medical_links_group`\n"
        f"`https://t.me/+AbCdEfGh1234`",
        buttons=[nav_row(b"make_ch")],
        parse_mode="md",
    )

    async with bot.conversation(OWNER_ID, timeout=180) as conv:
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

    existing = get_raw_count()
    await event.respond(
        f"**④  حصاد الروابط**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 المصادر: {len(db['sources'])}\n"
        f"📦 الروابط الموجودة: {existing:,}\n\n"
        f"🌾 جاري السحب من جميع المصادر...\n"
        f"_ستصلك تحديثات دورية._",
        buttons=[nav_row(b"add_src")],
        parse_mode="md",
    )

    harvested = await harvest_sources(
        status_callback=status_msg,
        db=db,
        session=db["accounts"][0],
    )

    new_count = len(harvested) - existing
    await status_msg(
        f"✅ **اكتمل الحصاد!**\n\n"
        f"📦 إجمالي الروابط: **{len(harvested):,}**\n"
        f"🆕 روابط جديدة: **{new_count:,}**"
    )
    await bot.send_message(
        OWNER_ID,
        f"🎉 تم جمع **{len(harvested):,}** رابط!\nهل تريد بدء الفرز الآن؟",
        buttons=[
            [Button.inline("⏭️ بدء الفرز الآن ◄", b"run_sort")],
            nav_row(b"add_src"),
        ],
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Sort
# ─────────────────────────────────────────────────────────────────────────────

def _sort_control_buttons() -> list:
    """Inline buttons shown while sorting is active."""
    return [
        [Button.inline("⏸ إيقاف مؤقت", b"sort_pause"), Button.inline("⏹ إيقاف نهائي", b"sort_stop")],
        [Button.inline("🏠 القائمة الرئيسية", b"home")],
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

    await event.respond(
        f"**⑤ الفرز الشامل**\n"
        f"📊 الإجمالي: {len(raw):,} | ✅ تم: {start_from:,} ({pct}%) | ⏳ متبقي: {remaining:,}\n\n"
        + ("🔄 استئناف من حيث توقفنا..." if start_from > 0 else "⚡ بدء الفرز الشامل...")
        + "\n_ستصلك تحديثات دورية._",
        buttons=_sort_control_buttons(),
        parse_mode="md",
    )

    await run_sorter(
        status_callback=status_msg,
        db=db,
        accounts=db["accounts"],
        bot_client=bot,
        start_from=start_from,
    )

    if not sorter_ctrl.is_stopped():
        await bot.send_message(
            OWNER_ID,
            f"🎯 **اكتمل الفرز!**\n"
            f"✅ {db['stats'].get('total_sorted', 0):,} مرتبة | "
            f"💀 {db['stats'].get('total_broken', 0):,} تالفة",
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
    await event.answer("⏸ جاري الإيقاف المؤقت...")
    sorter_ctrl.pause()
    await event.respond(
        "⏸ **الفرز متوقف مؤقتاً.**\nسيتوقف بعد انتهاء الدفعة الحالية.",
        buttons=[
            [Button.inline("▶️ استئناف", b"sort_resume"), Button.inline("⏹ إيقاف نهائي", b"sort_stop")],
            nav_row(),
        ],
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"sort_resume"))
@owner_only
async def sort_resume_handler(event):
    await event.answer("▶️ جاري الاستئناف...")
    sorter_ctrl.resume()
    await event.respond(
        "▶️ **تم استئناف الفرز.**",
        buttons=_sort_control_buttons(),
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"sort_stop"))
@owner_only
async def sort_stop_handler(event):
    await event.answer("⏹ جاري الإيقاف...")
    sorter_ctrl.stop()
    await event.respond(
        "⏹ **تم إيقاف الفرز.**\nالتقدم محفوظ — يمكنك الاستئناف لاحقاً من حيث توقفت.",
        buttons=[
            [Button.inline("▶️ استئناف الفرز لاحقاً", b"run_sort")],
            nav_row(),
        ],
        parse_mode="md",
    )


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
        f"🧠 يبحث بـ **5 طرق متزامنة:**\n"
        f"  1️⃣ بحث بكلمات مفتاحية (80+ استعلام)\n"
        f"  2️⃣ استكشاف قنوات مشابهة\n"
        f"  3️⃣ فحص مجموعات المصادر الحالية\n"
        f"  4️⃣ توسيع المجلدات (Addlist)\n"
        f"  5️⃣ رصد الإشارات والمنشورات\n\n"
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

    await event.respond(
        "🧠 **بدأ الاكتشاف الذكي...**\n_ستصلك تحديثات مستمرة._",
        buttons=[nav_row(b"run_sort")],
        parse_mode="md",
    )

    new_count = await run_smart_discovery(
        status_callback=status_msg,
        db=db,
        accounts=db["accounts"],
        archive_channel_ids=archive_ids,
        source_links=source_links,
    )

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

    async with bot.conversation(OWNER_ID, timeout=120) as conv:
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
            f"🛡 نظام الحماية الذكي مفعّل (دفعات متقطعة).\n"
            f"_سيتم إرسال تحديث لكل رابط._",
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
        await status_msg(
            "❌ لم يتم العثور على روابط في القناة.\n"
            "تأكد من تشغيل الفرز أولاً.",
            # buttons arg not valid in status_msg - user sends a new message
        )
        await bot.send_message(
            OWNER_ID, "❌ لا روابط في القناة.",
            buttons=[[Button.inline("⚡ تشغيل الفرز أولاً ◄", b"run_sort")], nav_row()],
        )
        return

    await run_smart_joiner(
        status_callback=status_msg,
        links_to_join=links_to_join,
        accounts=db["accounts"],
        db=db,
        max_joins=max_joins,
    )


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
        f"❌ **التالفة/الخاصة:** {stats.get('total_broken', 0):,}\n"
        f"⏭️ **المتخطاة (مكررة):** {stats.get('total_skipped_duplicate', 0):,}\n"
        f"🤝 **تم الانضمام إليها:** {len(db.get('joined_links', [])):,}\n\n"
        f"👤 **الحسابات:** {len(db.get('accounts', []))}\n"
        f"🔗 **المصادر:** {len(db.get('sources', []))}\n"
        f"📺 **القنوات:** {len(db.get('channels', {}))}/7"
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


async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print(f"🤖 البوت يعمل... OWNER_ID={OWNER_ID} | أرسل /start في تيليجرام للبدء.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
