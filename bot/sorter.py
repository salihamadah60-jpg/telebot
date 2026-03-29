"""
Sorter — parallel link inspection and dispatch to one of the 7 archive channels.

Channel routing:
  - addlist links  → "addlist"
  - bot entities   → "bots"
  - invite links   → "invite"  (both accessible AND inaccessible invite links)
  - non-medical    → "other"
  - channel type   → "channels"
  - group type     → "groups"
  - truly broken   → "broken"  (deleted/invalid usernames only)

Multi-account parallel mode:
  - Pending links are split evenly across all linked accounts.
  - Each account worker runs concurrently via asyncio.gather.
  - Seen-link set is loaded into memory once (O(1) lookups).
  - A shared asyncio.Lock protects file writes and stats updates.
  - Resume: reads last_sorted_index from db and skips already-seen links.

Persistent progress bar:
  - One message is sent at the start and EDITED on every batch update.
  - Stop and Pause/Resume buttons are embedded in that message.
"""

import asyncio
import random
from telethon import TelegramClient, Button
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
    load_seen_set,
    mark_seen,
    load_raw_links,
    save_raw_links,
    save_db,
    normalize_link,
)
from config import (
    API_ID,
    API_HASH,
    DELAY_MIN,
    DELAY_MAX,
    MAX_CONCURRENT,
    OWNER_ID,
)
import state as sorter_ctrl


# ─────────────────────────────────────────────────────────────────────────────
# Entity inspection
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

    if is_invite_link(link):
        return "invite"

    if not info.get("ok"):
        return "broken"

    entity = info.get("entity")
    if entity and is_bot_entity(entity):
        return "bots"

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
    "broken":   "💀 منتهي/غير صالح",
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
        is_priv = is_invite_link(link)
        status = "🔐 رابط دعوة (يحتاج انضمام)" if is_priv else f"❌ رابط منتهٍ ({info.get('reason', '')})"
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
# Progress bar
# ─────────────────────────────────────────────────────────────────────────────

def _build_progress_bar(done: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "░" * width + " 0%"
    pct    = done / total
    filled = round(pct * width)
    bar    = "▓" * filled + "░" * (width - filled)
    return f"{bar} {int(pct * 100)}%"


def _progress_buttons(paused: bool = False) -> list:
    if paused:
        return [
            [Button.inline("▶️ استئناف", b"sort_resume"),
             Button.inline("⏹ إيقاف وحفظ", b"sort_stop")],
        ]
    return [
        [Button.inline("⏸ إيقاف مؤقت", b"sort_pause"),
         Button.inline("⏹ إيقاف وحفظ", b"sort_stop")],
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Single-link processor (shared client per worker)
# ─────────────────────────────────────────────────────────────────────────────

async def _process_one(
    sem: asyncio.Semaphore,
    link: str,
    client: TelegramClient,
    account_name: str,
    db: dict,
    bot_client,
    file_lock: asyncio.Lock,
    seen_set: set,
    extra_links: list,
    counters: dict,
) -> None:
    """Process a single link: inspect → route → post to archive channel → mark seen."""
    async with sem:
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        try:
            _is_add = is_addlist_link(link)
            addlist_children: list[str] = []

            if _is_add:
                addlist_children = await expand_addlist(client, link)
                if addlist_children:
                    async with file_lock:
                        extra_links.extend(addlist_children)

            info = await get_entity_info(client, link)

            med = (
                is_medical(
                    info.get("title", ""),
                    info.get("bio", ""),
                    info.get("username", ""),
                )
                if info.get("ok") else True
            )

            specialty = (
                classify_specialty(
                    info.get("title", ""),
                    info.get("bio", ""),
                    info.get("username", ""),
                )
                if info.get("ok") else "—"
            )

            channel_key = route_to_channel(link, info, _is_add, med)

            report = build_report(
                link, info, specialty, channel_key,
                account_name, addlist_children or None,
            )

            # ── Post to the correct archive channel ───────────────────────────
            target_ch_id = db["channels"].get(channel_key)
            if target_ch_id and isinstance(target_ch_id, int):
                try:
                    await bot_client.send_message(
                        target_ch_id, report, parse_mode="md"
                    )
                except Exception:
                    pass

            # ── Mark seen + update stats (file_lock protects file writes) ─────
            async with file_lock:
                seen_set.add(normalize_link(link))
                mark_seen(link)

                if channel_key == "broken":
                    db["stats"]["total_broken"] = db["stats"].get("total_broken", 0) + 1
                elif channel_key == "invite":
                    db["stats"]["total_invite"] = db["stats"].get("total_invite", 0) + 1
                    db["stats"]["total_sorted"] = db["stats"].get("total_sorted", 0) + 1
                else:
                    db["stats"]["total_sorted"] = db["stats"].get("total_sorted", 0) + 1
                db["stats"]["total_found"] = db["stats"].get("total_found", 0) + 1

                counters["done"] += 1

        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            async with file_lock:
                counters["errors"] += 1
        except Exception:
            async with file_lock:
                mark_seen(link)
                seen_set.add(normalize_link(link))
                counters["done"] += 1
                counters["errors"] += 1


# ─────────────────────────────────────────────────────────────────────────────
# Per-account worker
# ─────────────────────────────────────────────────────────────────────────────

async def _account_worker(
    session: str,
    links_subset: list,
    db: dict,
    bot_client,
    file_lock: asyncio.Lock,
    seen_set: set,
    extra_links: list,
    counters: dict,
    acc_index: int,
    total_accs: int,
) -> None:
    if not links_subset:
        return

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    try:
        async with TelegramClient(session, API_ID, API_HASH, flood_sleep_threshold=30) as client:
            try:
                me = await client.get_me()
                acc_name = (me.first_name or "") + (f" (@{me.username})" if me.username else "")
            except Exception:
                acc_name = f"حساب {acc_index}"

            # Process in batches to periodically check stop/pause
            batch_size = MAX_CONCURRENT * 4
            batches = [links_subset[i: i + batch_size] for i in range(0, len(links_subset), batch_size)]

            for batch in batches:
                # Check stop
                if sorter_ctrl.is_stopped():
                    break

                # Wait out pause
                while sorter_ctrl.is_paused():
                    await asyncio.sleep(2)
                    if sorter_ctrl.is_stopped():
                        return

                tasks = [
                    _process_one(
                        sem, link, client, acc_name,
                        db, bot_client, file_lock, seen_set,
                        extra_links, counters,
                    )
                    for link in batch
                    if normalize_link(link) not in seen_set  # double-check before launching
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Save progress after each batch
                async with file_lock:
                    db["progress"]["last_sorted_index"] = counters.get("global_done", 0)
                    save_db(db)

    except Exception as e:
        async with file_lock:
            counters["errors"] = counters.get("errors", 0) + 1


# ─────────────────────────────────────────────────────────────────────────────
# Progress reporter (runs as a background task)
# ─────────────────────────────────────────────────────────────────────────────

async def _progress_reporter(
    bot_client,
    prog_msg_id: int,
    prog_chat_id: int,
    counters: dict,
    total: int,
    raw_total: int,
    start_from: int,
    db: dict,
):
    while not sorter_ctrl.is_stopped() and counters.get("done", 0) < total:
        await asyncio.sleep(5)
        done      = counters.get("done", 0)
        errors    = counters.get("errors", 0)
        global_done = start_from + done
        bar       = _build_progress_bar(global_done, raw_total)
        paused    = sorter_ctrl.is_paused()
        status    = "متوقف مؤقتاً ⏸" if paused else "جارٍ..."

        text = (
            f"📊 **الفرز الشامل — {status}**\n"
            f"[{bar}]\n\n"
            f"تم: **{global_done:,}** / {raw_total:,} رابط\n"
            f"✅ مرتبة: {db['stats'].get('total_sorted', 0):,} | "
            f"💀 تالفة: {db['stats'].get('total_broken', 0):,} | "
            f"❌ أخطاء: {errors:,}"
        )
        try:
            await bot_client.edit_message(
                prog_chat_id, prog_msg_id,
                text,
                buttons=_progress_buttons(paused),
                parse_mode="md",
            )
        except Exception:
            pass


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

    # ── Load seen set into memory (O(1) lookups instead of O(n) file reads) ───
    seen_set = load_seen_set()

    # ── Build pending list: from start_from onward, skip already-seen ─────────
    pending = [
        lnk for lnk in raw_links[start_from:]
        if normalize_link(lnk) not in seen_set
    ]
    total = len(pending)

    if total == 0:
        await status_callback(
            "✅ **جميع الروابط تمت معالجتها مسبقاً.**\n\n"
            "إذا أردت إعادة الفرز، امسح الذاكرة أولاً."
        )
        return

    num_accounts = len(accounts)
    await status_callback(
        f"⚡ **الفرز الموزع — بدء**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 حسابات: **{num_accounts}** | 🔗 روابط: **{total:,}**\n"
        f"{'🔄 استئناف من حيث توقفنا' if start_from > 0 else '⚡ بدء جديد'}\n"
        + "\n".join(
            f"  · حساب {i+1}: **{len(pending[i::num_accounts]):,}** رابط"
            for i in range(num_accounts)
        )
    )

    # ── Split links across accounts (round-robin) ─────────────────────────────
    subsets = [pending[i::num_accounts] for i in range(num_accounts)]

    file_lock   = asyncio.Lock()
    extra_links: list[str] = []
    counters    = {"done": 0, "errors": 0}

    # ── Progress reporter background task ─────────────────────────────────────
    prog_msg_id  = sorter_ctrl.progress_msg_id
    prog_chat_id = sorter_ctrl.progress_chat_id

    reporter = asyncio.create_task(
        _progress_reporter(
            bot_client, prog_msg_id, prog_chat_id,
            counters, total, len(raw_links), start_from, db,
        )
    ) if prog_msg_id and prog_chat_id else None

    # ── Run all account workers simultaneously ────────────────────────────────
    try:
        workers = [
            _account_worker(
                session=accounts[i],
                links_subset=subsets[i],
                db=db,
                bot_client=bot_client,
                file_lock=file_lock,
                seen_set=seen_set,
                extra_links=extra_links,
                counters=counters,
                acc_index=i + 1,
                total_accs=num_accounts,
            )
            for i in range(num_accounts)
            if subsets[i]
        ]
        await asyncio.gather(*workers)
    finally:
        if reporter:
            reporter.cancel()
            try:
                await reporter
            except asyncio.CancelledError:
                pass

    # ── Save final progress index ─────────────────────────────────────────────
    async with file_lock:
        db["progress"]["last_sorted_index"] = start_from + counters["done"]
        save_db(db)

    # ── Handle extra addlist-derived links ────────────────────────────────────
    if extra_links:
        unique_extra = list(dict.fromkeys(
            lnk for lnk in extra_links
            if normalize_link(lnk) not in seen_set
        ))
        if unique_extra:
            all_links = load_raw_links() + unique_extra
            save_raw_links(all_links)
            await status_callback(
                f"📂 تم استخراج **{len(unique_extra)}** رابط إضافي من المجلدات — أُضيفت للقائمة."
            )

    # ── Final progress message update ─────────────────────────────────────────
    if prog_msg_id and prog_chat_id:
        stopped = sorter_ctrl.is_stopped()
        final_text = (
            (
                f"⏹ **توقف الفرز — التقدم محفوظ**\n"
                f"[{_build_progress_bar(start_from + counters['done'], len(raw_links))}]\n\n"
                f"تم: **{start_from + counters['done']:,}** / {len(raw_links):,}\n"
                f"✅ مرتبة: {db['stats'].get('total_sorted', 0):,} | "
                f"💀 تالفة: {db['stats'].get('total_broken', 0):,}"
            ) if stopped else (
                f"🎯 **اكتمل الفرز!**\n"
                f"[{'▓' * 10}] 100%\n\n"
                f"تم: **{start_from + counters['done']:,}** / {len(raw_links):,} رابط\n"
                f"✅ مرتبة: {db['stats'].get('total_sorted', 0):,} | "
                f"💀 تالفة: {db['stats'].get('total_broken', 0):,} | "
                f"🔐 دعوات: {db['stats'].get('total_invite', 0):,}\n\n"
                f"📝 الروابط \"التالفة\" = يوزرنيم محذوف فعلاً.\n"
                f"الدعوات الخاصة → قناة 🔐 روابط الدعوة."
            )
        )
        try:
            await bot_client.edit_message(
                prog_chat_id, prog_msg_id,
                final_text,
                buttons=[[Button.inline("🏠 القائمة الرئيسية", b"home")]],
                parse_mode="md",
            )
        except Exception:
            pass
