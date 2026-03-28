"""
Sorter — parallel link inspection and dispatch to one of the 7 archive channels.

Channel routing:
  - addlist links  → "addlist"
  - bot entities   → "bots"
  - invite links   → "invite"
  - non-medical    → "other"
  - channel type   → "channels"
  - group type     → "groups"
  - broken/private → "broken"

Database-locking fix: one shared TelegramClient per batch (not one per task).
Parallel processing: up to MAX_CONCURRENT links inspected simultaneously.
"""

import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.chatlists import CheckChatlistInviteRequest

from classifier import (
    classify_specialty,
    is_medical,
    detect_link_type,
    is_addlist_link,
    is_invite_link,
    is_bot_entity,
)
from database import (
    is_seen,
    mark_seen,
    load_raw_links,
    save_raw_links,
    save_db,
)
from config import (
    API_ID,
    API_HASH,
    DELAY_MIN,
    DELAY_MAX,
    SWITCH_ACCOUNT_EVERY,
    MAX_CONCURRENT,
)
import state as sorter_ctrl


# ─────────────────────────────────────────────────────────────────────────────
# Entity inspection  (uses a shared client — no SQLite locking)
# ─────────────────────────────────────────────────────────────────────────────

async def get_entity_info(client: TelegramClient, link: str) -> dict:
    try:
        entity = await client.get_entity(link)
    except Exception as e:
        return {
            "ok": False,
            "reason": str(e),
            "link": link,
            "is_private": is_invite_link(link),
            "entity": None,
        }

    title    = getattr(entity, "title", "") or getattr(entity, "first_name", "بدون اسم")
    username = getattr(entity, "username", "") or ""
    bio      = ""

    try:
        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            full = await client(GetFullChannelRequest(entity))
            bio  = getattr(full.full_chat, "about", "") or ""
        elif not getattr(entity, "bot", False):
            full = await client(GetFullChatRequest(entity))
            bio  = getattr(full.full_chat, "about", "") or ""
    except Exception:
        pass

    link_type = detect_link_type(entity)
    members   = getattr(entity, "participants_count", None)

    joined = False
    try:
        if hasattr(entity, "left"):
            joined = not entity.left
    except Exception:
        pass

    return {
        "ok":        True,
        "title":     title,
        "username":  username,
        "bio":       bio,
        "link_type": link_type,
        "members":   members,
        "joined":    joined,
        "entity":    entity,
    }


async def expand_addlist(client: TelegramClient, link: str) -> list[str]:
    try:
        slug   = link.split("addlist/")[-1].strip("/")
        invite = await client(CheckChatlistInviteRequest(slug=slug))
        result = []
        peers  = getattr(invite, "peers", []) + getattr(invite, "already_peer_chats", [])
        for peer in peers:
            uname = getattr(peer, "username", None)
            if uname:
                result.append(f"https://t.me/{uname}")
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Channel routing logic
# ─────────────────────────────────────────────────────────────────────────────

def route_to_channel(link: str, info: dict, is_add: bool, is_med: bool = True) -> str:
    if is_add:
        return "addlist"
    if not info.get("ok"):
        return "broken"
    entity = info.get("entity")
    if entity and is_bot_entity(entity):
        return "bots"
    if is_invite_link(link):
        return "invite"
    if not is_med:
        return "other"
    link_type = info.get("link_type", "")
    if link_type == "channel":
        return "channels"
    return "groups"


# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

_CHANNEL_LABELS = {
    "channels": "📢 قناة",
    "groups":   "👥 مجموعة",
    "broken":   "💀 منتهي/خاص",
    "invite":   "🔐 رابط دعوة",
    "addlist":  "📂 مجلد",
    "bots":     "🤖 بوت",
    "other":    "🌐 غير طبي",
}

_TYPE_LABELS = {
    "channel":    "📢 قناة",
    "supergroup": "👥 مجموعة كبيرة",
    "group":      "👥 مجموعة عادية",
    "bot":        "🤖 بوت",
}


def build_report(
    link: str,
    info: dict,
    specialty: str,
    channel_key: str,
    account_name: str,
    addlist_children: list[str] | None = None,
) -> str:
    channel_label = _CHANNEL_LABELS.get(channel_key, "❓")

    if not info.get("ok"):
        reason = info.get("reason", "خطأ غير معروف")
        status = "🔐 رابط دعوة" if info.get("is_private") else f"❌ تالف ({reason})"
        return (
            f"**الحالة:** {status}\n"
            f"**الرابط:** {link}\n"
            f"**النوع:** {channel_label}\n"
            f"**بواسطة:** `{account_name}`"
        )

    type_label    = _TYPE_LABELS.get(info.get("link_type", ""), "❓ غير محدد")
    joined_label  = "نعم ✅" if info.get("joined") else "لا ❌"
    members       = info.get("members")
    members_label = f"{members:,}" if members is not None else "غير متاح"
    bio_text      = (info.get("bio") or "—")[:200]

    lines = [
        f"📌 **الاسم:** {info.get('title', 'بدون اسم')}",
        f"🔗 **الرابط:** {link}",
        f"🏷 **النوع:** {type_label}",
        f"🗂 **الأرشيف:** {channel_label}",
        f"🧬 **التخصص:** {specialty}",
        f"👥 **الأعضاء:** {members_label}",
        f"✅ **منضم؟:** {joined_label}",
        f"👤 **فحص بواسطة:** `{account_name}`",
        f"📝 **الوصف:** {bio_text}",
    ]

    if addlist_children:
        lines.append(f"\n📋 **مجموعات مستخرجة ({len(addlist_children)}):**")
        for ch in addlist_children[:10]:
            lines.append(f"  • {ch}")
        if len(addlist_children) > 10:
            lines.append(f"  … و{len(addlist_children) - 10} رابط إضافي")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Single-link task  — uses a SHARED client (no per-task SQLite open)
# ─────────────────────────────────────────────────────────────────────────────

async def process_single_link(
    sem: asyncio.Semaphore,
    link: str,
    client: TelegramClient,
    account_name: str,
    db: dict,
    bot_client,
    extra_links_collector: list,
) -> dict:
    async with sem:
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        result = {"link": link, "status": "ok", "channel_key": None, "error": None}

        try:
            _is_add = is_addlist_link(link)
            addlist_children: list[str] = []

            if _is_add:
                addlist_children = await expand_addlist(client, link)
                if addlist_children:
                    extra_links_collector.extend(addlist_children)

            info = await get_entity_info(client, link)

            med = (
                is_medical(
                    info.get("title", ""),
                    info.get("bio", ""),
                    info.get("username", ""),
                )
                if info.get("ok")
                else True
            )

            specialty = (
                classify_specialty(
                    info.get("title", ""),
                    info.get("bio", ""),
                    info.get("username", ""),
                )
                if info.get("ok")
                else "—"
            )

            channel_key = route_to_channel(link, info, _is_add, med)
            result["channel_key"] = channel_key

            report = build_report(
                link, info, specialty, channel_key,
                account_name, addlist_children or None,
            )

            target_ch_id = db["channels"].get(channel_key)
            if target_ch_id:
                try:
                    await bot_client.send_message(
                        int(target_ch_id), report, parse_mode="md"
                    )
                except Exception as send_err:
                    result["error"] = f"send error: {send_err}"

            mark_seen(link)

            if channel_key == "broken":
                db["stats"]["total_broken"] = db["stats"].get("total_broken", 0) + 1
            else:
                db["stats"]["total_sorted"] = db["stats"].get("total_sorted", 0) + 1
            db["stats"]["total_found"] = db["stats"].get("total_found", 0) + 1

        except FloodWaitError as e:
            result["status"] = "flood"
            result["error"]  = f"FloodWait {e.seconds}s"
            await asyncio.sleep(e.seconds)
        except Exception as e:
            result["status"] = "error"
            result["error"]  = str(e)
            mark_seen(link)

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Main sorter entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_sorter(
    status_callback,
    db: dict,
    accounts: list,
    bot_client,
    start_from: int = 0,
):
    raw_links = load_raw_links()
    if not raw_links:
        await status_callback("⚠️ لا توجد روابط في القائمة الخام. قم بتشغيل الحصاد أولاً.")
        return

    pending = [lnk for lnk in raw_links[start_from:] if not is_seen(lnk)]
    total   = len(pending)

    if total == 0:
        await status_callback("✅ جميع الروابط تمت معالجتها مسبقاً.")
        return

    await status_callback(
        f"⚡ بدأ الفرز المتوازي على **{total}** رابط "
        f"بـ **{MAX_CONCURRENT}** مسار متزامن..."
    )

    sem        = asyncio.Semaphore(MAX_CONCURRENT)
    acc_idx    = 0
    op_count   = 0
    batch_size = MAX_CONCURRENT * 4
    extra_links_collector: list[str] = []
    batches = [pending[i: i + batch_size] for i in range(0, len(pending), batch_size)]

    for batch_num, batch in enumerate(batches, 1):
        # ── Check stop ────────────────────────────────────────────────────────
        if sorter_ctrl.is_stopped():
            await status_callback("⏹ **توقف الفرز** — تم الحفظ.")
            return

        # ── Check pause — wait until resumed or stopped ───────────────────────
        if sorter_ctrl.is_paused():
            await status_callback("⏸ **الفرز متوقف مؤقتاً** — اضغط ▶️ استئناف للمتابعة.")
            while sorter_ctrl.is_paused():
                await asyncio.sleep(2)
            if sorter_ctrl.is_stopped():
                await status_callback("⏹ **توقف الفرز** — تم الحفظ.")
                return

        if op_count > 0 and op_count % SWITCH_ACCOUNT_EVERY < batch_size and len(accounts) > 1:
            acc_idx = (acc_idx + 1) % len(accounts)
            await status_callback(f"🔄 التبديل إلى الحساب: `{accounts[acc_idx]}`")


        session      = accounts[acc_idx % len(accounts)]
        account_name = session

        # ── Open ONE shared client per batch — eliminates "database is locked" ──
        try:
            async with TelegramClient(session, API_ID, API_HASH) as client:
                try:
                    me = await client.get_me()
                    account_name = (me.first_name or "") + (
                        f" (@{me.username})" if me.username else ""
                    )
                except Exception:
                    pass

                tasks = [
                    process_single_link(
                        sem, link, client, account_name,
                        db, bot_client, extra_links_collector,
                    )
                    for link in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as batch_err:
            await status_callback(f"⚠️ خطأ في الدُفعة {batch_num}: {batch_err}")
            results = []

        op_count += len(batch)
        errors    = sum(
            1 for r in results
            if isinstance(r, Exception)
            or (isinstance(r, dict) and r.get("status") != "ok")
        )

        db["progress"]["last_sorted_index"] = start_from + op_count
        save_db(db)

        await status_callback(
            f"📊 الدُفعة {batch_num}/{len(batches)} — "
            f"تم: {op_count}/{total} | "
            f"مرتبة: {db['stats'].get('total_sorted', 0)} | "
            f"تالفة: {db['stats'].get('total_broken', 0)} | "
            f"أخطاء في الدفعة: {errors}"
        )

    if extra_links_collector:
        all_links    = load_raw_links() + extra_links_collector
        unique_extra = list(dict.fromkeys(extra_links_collector))
        save_raw_links(all_links)
        await status_callback(
            f"📂 تم استخراج **{len(unique_extra)}** رابط إضافي من المجلدات."
        )

    await status_callback(
        f"🎯 اكتمل الفرز المتوازي!\n"
        f"✅ مرتبة: {db['stats'].get('total_sorted', 0)}\n"
        f"💀 تالفة: {db['stats'].get('total_broken', 0)}\n"
        f"📊 الإجمالي: {db['stats'].get('total_found', 0)}"
    )
