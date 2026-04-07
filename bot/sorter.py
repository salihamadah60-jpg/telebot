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
import re
import time as _time
from telethon import TelegramClient, Button
from telethon.errors import FloodWaitError, InviteHashExpiredError, InviteHashInvalidError
from telethon.tl.functions.channels import GetFullChannelRequest, GetChannelsRequest
from telethon.tl.functions.messages import GetFullChatRequest, CheckChatInviteRequest
from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
from telethon.tl.types import (
    PeerChannel, InputChannel,
    User as TLUser, Chat as TLChat, Channel as TLChannel,
)

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
    save_seen_set,
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
    BOT_ID,
)
import state as sorter_ctrl

# ─────────────────────────────────────────────────────────────────────────────
# Account-rotation strategy constants
# ─────────────────────────────────────────────────────────────────────────────
FLOOD_MULTIPLIER     = 3     # wait 3× the FloodWait → "safety multiplier"
CRITICAL_FLOOD_SECS  = 600   # 10-minute FloodWait → triggers system-wide pause
CRITICAL_PAUSE_SECS  = 1200  # 20-minute pause applied to ALL accounts on critical


# ─────────────────────────────────────────────────────────────────────────────
# Entity inspection
# ─────────────────────────────────────────────────────────────────────────────

async def _get_invite_info(client: TelegramClient, link: str) -> dict | None:
    """
    Use CheckChatInviteRequest to pull metadata for a private invite link.
    Works whether the account is already a member or not, and does NOT join.
    Returns a partial info-dict on success, None on unrecoverable failure.
    """
    m = re.search(r't\.me/(?:\+|joinchat/)([A-Za-z0-9_\-]+)', link)
    if not m:
        return None
    inv_hash = m.group(1)
    try:
        result  = await client(CheckChatInviteRequest(inv_hash))
        entity  = getattr(result, "chat", None)
        chats   = list(getattr(result, "chats", []))
        if entity is None and chats:
            entity = chats[0]

        if entity is not None:
            title     = getattr(entity, "title", "") or "بدون اسم"
            username  = getattr(entity, "username", "") or ""
            members   = getattr(entity, "participants_count", None)
            is_ch     = getattr(entity, "broadcast", False)
            is_mg     = getattr(entity, "megagroup", False)
            link_type = "channel" if is_ch else ("supergroup" if is_mg else "group")
            joined    = hasattr(result, "chat") and result.chat is not None
            bio = ""
            try:
                if isinstance(entity, TLChannel):
                    full = await client(GetFullChannelRequest(entity))
                    bio  = getattr(full.full_chat, "about", "") or ""
            except Exception:
                pass
            return {
                "ok": True, "title": title, "username": username, "bio": bio,
                "link_type": link_type, "members": members,
                "joined": joined, "entity": entity,
            }
        else:
            title     = getattr(result, "title", "") or "بدون اسم"
            about     = getattr(result, "about", "") or ""
            members   = getattr(result, "participants_count", None)
            is_ch     = getattr(result, "broadcast", False)
            link_type = "channel" if is_ch else "group"
            return {
                "ok": True, "title": title, "username": "",
                "bio": about, "link_type": link_type,
                "members": members, "joined": False, "entity": None,
            }

    except (InviteHashExpiredError, InviteHashInvalidError):
        return {
            "ok": False, "reason": "رابط الدعوة منتهي أو غير صالح",
            "link": link, "is_private": True, "entity": None,
        }
    except Exception:
        return None


_MSG_LINK_RE = re.compile(r'^https?://t\.me/([A-Za-z0-9_]+)/(\d+)(?:\?.*)?$')


async def get_entity_info(client: TelegramClient, link: str) -> dict:
    # ── Detect message links (t.me/channel/12345) — resolve the channel only ──
    msg_match = _MSG_LINK_RE.match(link.strip())
    lookup = f"https://t.me/{msg_match.group(1)}" if msg_match else link

    try:
        entity = await client.get_entity(lookup)
    except FloodWaitError:
        raise
    except Exception as e:
        err_str = str(e)

        # ── Disconnected client — attempt one reconnect then retry ─────────────
        if "disconnected" in err_str.lower() or "Cannot send requests" in err_str:
            try:
                await client.connect()
                entity = await client.get_entity(lookup)
            except FloodWaitError:
                raise
            except Exception as e2:
                err_str = str(e2)
                # fall through to normal error handling below
                if is_invite_link(link):
                    invite_info = await _get_invite_info(client, link)
                    if invite_info is not None:
                        return invite_info
                return {
                    "ok": False,
                    "reason": err_str,
                    "link": link,
                    "is_private": is_invite_link(link),
                    "entity": None,
                }
        else:
            # For private invite links try CheckChatInviteRequest before giving up —
            # fetches title/member-count/type without joining.
            if is_invite_link(link):
                invite_info = await _get_invite_info(client, link)
                if invite_info is not None:
                    return invite_info
            return {
                "ok": False,
                "reason": err_str,
                "link": link,
                "is_private": is_invite_link(link),
                "entity": None,
            }

    is_user = isinstance(entity, TLUser)

    title    = getattr(entity, "title", "") or getattr(entity, "first_name", "بدون اسم")
    username = getattr(entity, "username", "") or ""
    bio      = ""

    # Explicit isinstance guards — prevents GetFullChatRequest being called on
    # a User entity if is_user is ever wrong due to a Telethon edge case.
    if not is_user:
        try:
            if isinstance(entity, TLChannel):
                full = await client(GetFullChannelRequest(entity))
                bio  = getattr(full.full_chat, "about", "") or ""
            elif isinstance(entity, TLChat):
                full = await client(GetFullChatRequest(entity.id))
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

    # Regular user accounts have no place in medical archives
    if entity and isinstance(entity, TLUser) and not getattr(entity, "bot", False):
        return "other"

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
# Resolve archive channel entities reliably (avoids entity-cache misses)
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_archive_channels(client: TelegramClient, db: dict) -> dict:
    """
    Pre-resolve all archive channel entities for this client session.
    Returns {channel_key: entity} for every channel we can reach.

    Resolution strategy (in order, stops at first success per channel):
      1. InputChannel(ch_id, stored_access_hash)  — no cache needed, fastest
      2. GetChannelsRequest([PeerChannel])          — works if dialogs populated
      3. CheckChatInviteRequest                    — works even at channel limit;
                                                     for already-joined channels
                                                     returns full entity with hash
      4. get_entity from local session cache        — last resort
    """
    channels     = db.get("channels", {})
    hashes       = db.get("channels_hashes", {})
    invite_links = db.get("channels_invites", [])
    resolved     = {}
    new_hashes   = {}     # hashes discovered in THIS call (to persist later)

    # ── Strategy 3 pre-pass: CheckChatInviteRequest for all invite links ─────
    # Fetch ONCE per invite link (not per channel) → build {ch_id: entity} map.
    # Works even when the account is at the 500-channel limit because
    # CheckChatInviteRequest never actually joins; for already-joined channels
    # it returns ChatInviteAlready with the full entity and access hash.
    invite_entity_map: dict[int, object] = {}   # bare channel_id → entity
    for link in invite_links:
        m = re.search(r't\.me/(?:\+|joinchat/)([A-Za-z0-9_\-]+)', link)
        if not m:
            continue
        inv_hash = m.group(1)
        try:
            result = await client(CheckChatInviteRequest(inv_hash))
            chat   = getattr(result, "chat", None)
            chats  = list(getattr(result, "chats", []))
            for c in ([chat] if chat else []) + chats:
                if c and hasattr(c, "id"):
                    invite_entity_map[c.id] = c
        except (InviteHashExpiredError, InviteHashInvalidError):
            continue
        except Exception:
            continue

    # ── Per-channel resolution ───────────────────────────────────────────────
    for key, ch_id in channels.items():
        if not isinstance(ch_id, int):
            continue
        entity = None

        # 1) Stored access hash — no session cache needed (fastest)
        if key in hashes:
            try:
                result = await client(GetChannelsRequest(
                    [InputChannel(ch_id, hashes[key])]
                ))
                if result.chats:
                    entity = result.chats[0]
                    if hasattr(entity, "access_hash"):
                        new_hashes[key] = entity.access_hash
            except Exception:
                pass

        # 2) PeerChannel via GetChannelsRequest (works if dialogs were cached)
        if entity is None:
            try:
                result = await client(GetChannelsRequest([PeerChannel(ch_id)]))
                if result.chats:
                    entity = result.chats[0]
                    if hasattr(entity, "access_hash"):
                        new_hashes[key] = entity.access_hash
            except Exception:
                pass

        # 3) CheckChatInviteRequest pre-pass result
        if entity is None and ch_id in invite_entity_map:
            entity = invite_entity_map[ch_id]
            if hasattr(entity, "access_hash"):
                new_hashes[key] = entity.access_hash

        # 4) Local session cache fallback
        if entity is None:
            try:
                entity = await client.get_entity(PeerChannel(ch_id))
                if hasattr(entity, "access_hash"):
                    new_hashes[key] = entity.access_hash
            except Exception:
                pass

        if entity is not None:
            resolved[key] = entity

    # Persist any newly discovered access hashes back into db (in-memory).
    # The caller (account worker) is responsible for calling save_db().
    if new_hashes:
        if "channels_hashes" not in db:
            db["channels_hashes"] = {}
        db["channels_hashes"].update(new_hashes)

    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Sleep helper — wakes every second to check for stop signal
# ─────────────────────────────────────────────────────────────────────────────

async def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for `seconds` but check sorter_ctrl every second.
    Returns True if the sorter was stopped before the full wait elapsed."""
    end = _time.time() + seconds
    while _time.time() < end:
        if sorter_ctrl.is_stopped():
            return True
        await asyncio.sleep(min(1.0, end - _time.time()))
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Sequential account-rotation inspector
# ─────────────────────────────────────────────────────────────────────────────

async def _sequential_inspector(
    clients_info: list,        # [(TelegramClient, name), ...] — all open, all alive
    pending: list,             # ordered list of links still to process
    file_lock: asyncio.Lock,
    seen_set: set,
    extra_links: list,
    result_queue: asyncio.Queue,
    counters: dict,
    skip_entity_ids: set,
    skip_normalized: set,
    db: dict,
    bot_client,
    prog_msg_id: int,
    prog_chat_id: int,
) -> None:
    """
    Sequential account rotation:
      1. Use accounts[0] until a FloodWait is raised.
      2. Mark that account unavailable for (flood_seconds × FLOOD_MULTIPLIER).
      3. Immediately hand the SAME link to the next available account.
      4. If ALL accounts are cooling down: idle-wait for the earliest one.
      5. Critical threshold (FloodWait > 10 min): pause ALL accounts for 20 min.
    """
    n = len(clients_info)
    cooldown_until = [0.0] * n   # epoch-time when each account becomes free

    acc_idx  = 0   # which account is currently active
    link_idx = 0   # index into pending[]

    # ── Initialise display ────────────────────────────────────────────────────
    async with file_lock:
        fa = counters.setdefault("flood_accounts", {})
        for i, (_, name) in enumerate(clients_info):
            fa[name] = "🟢" if i == 0 else "⏸"

    while link_idx < len(pending):

        # ── stop / pause ─────────────────────────────────────────────────────
        if sorter_ctrl.is_stopped():
            break
        while sorter_ctrl.is_paused():
            await asyncio.sleep(1)
            if sorter_ctrl.is_stopped():
                return

        # ── pick the next available account ──────────────────────────────────
        now     = _time.time()
        chosen  = None
        min_wait = float("inf")

        for offset in range(n):
            idx = (acc_idx + offset) % n
            if cooldown_until[idx] <= now:
                chosen = idx
                break
            remaining = cooldown_until[idx] - now
            if remaining < min_wait:
                min_wait = remaining

        if chosen is None:
            # All accounts cooling — save progress to disk before idle wait
            try:
                async with file_lock:
                    save_seen_set(seen_set)
                    save_db(db)
            except Exception:
                pass
            wait_secs = min_wait + 0.5
            m, s = divmod(int(wait_secs), 60)
            print(f"[Rotation] all accounts cooling — idle {m}m{s:02d}s")
            if prog_msg_id and prog_chat_id:
                try:
                    await bot_client.edit_message(
                        prog_chat_id, prog_msg_id,
                        f"⏳ **جميع الحسابات في فترة انتظار**\n"
                        f"أقرب حساب متاح بعد: **{m} دقيقة و {s:02d} ثانية**",
                        parse_mode="md",
                    )
                except Exception:
                    pass
            await _interruptible_sleep(wait_secs)
            continue

        acc_idx = chosen
        client, acc_name = clients_info[acc_idx]

        # ── refresh status icons ──────────────────────────────────────────────
        async with file_lock:
            fa = counters.setdefault("flood_accounts", {})
            for i, (_, n_) in enumerate(clients_info):
                if i == acc_idx:
                    fa[n_] = "🟢"
                elif cooldown_until[i] > _time.time():
                    rem = int(cooldown_until[i] - _time.time())
                    m2, s2 = divmod(rem, 60)
                    fa[n_] = f"❄️ {m2}م{s2:02d}ث"
                else:
                    fa[n_] = "⏸"

        # ── skip already-seen / excluded links ────────────────────────────────
        link = pending[link_idx]
        norm = normalize_link(link)
        if norm in skip_normalized or norm in seen_set:
            # Immediately add to seen_set so later duplicates in pending are skipped too
            async with file_lock:
                seen_set.add(norm)
            await result_queue.put({"link": link, "action": "skip"})
            link_idx += 1
            continue

        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        # ── inspect this link ─────────────────────────────────────────────────
        try:
            _is_add          = is_addlist_link(link)
            addlist_children: list[str] = []
            if _is_add:
                addlist_children = await expand_addlist(client, link)
                if addlist_children:
                    async with file_lock:
                        extra_links.extend(addlist_children)

            info = await get_entity_info(client, link)

            entity_id = getattr(info.get("entity"), "id", None)
            if entity_id and (entity_id in skip_entity_ids or entity_id == BOT_ID):
                await result_queue.put({"link": link, "action": "skip"})
                link_idx += 1
                continue

            med = (
                is_medical(info.get("title", ""), info.get("bio", ""), info.get("username", ""))
                if info.get("ok") else True
            )
            specialty = (
                classify_specialty(info.get("title", ""), info.get("bio", ""), info.get("username", ""))
                if info.get("ok") else "—"
            )

            channel_key = route_to_channel(link, info, _is_add, med)
            report      = build_report(
                link, info, specialty, channel_key,
                acc_name, addlist_children or None,
            )

            # Mark seen in-memory immediately so duplicates later in pending are skipped
            async with file_lock:
                seen_set.add(norm)

            await result_queue.put({
                "link": link, "action": "post",
                "channel_key": channel_key, "report": report,
            })
            link_idx += 1   # ✅ success — advance to next link

        except FloodWaitError as e:
            flood_secs = e.seconds
            wait_time  = flood_secs * FLOOD_MULTIPLIER

            if flood_secs > CRITICAL_FLOOD_SECS:
                # ── Critical flood: pause ALL accounts ───────────────────────
                pause_secs = max(CRITICAL_PAUSE_SECS, wait_time)
                for i in range(n):
                    cooldown_until[i] = _time.time() + pause_secs
                pm, ps = divmod(int(pause_secs), 60)
                async with file_lock:
                    fa = counters.setdefault("flood_accounts", {})
                    for _, n_ in clients_info:
                        fa[n_] = f"🔴 إيقاف {pm}م{ps:02d}ث"
                print(
                    f"[CRITICAL FLOOD] '{acc_name}': {flood_secs}s — "
                    f"system pause {pause_secs}s ({pm}m{ps:02d}s)"
                )
                # Save progress immediately on critical flood
                try:
                    async with file_lock:
                        save_seen_set(seen_set)
                        save_db(db)
                except Exception:
                    pass
                if prog_msg_id and prog_chat_id:
                    try:
                        await bot_client.edit_message(
                            prog_chat_id, prog_msg_id,
                            f"🚨 **حظر حرج — إيقاف النظام**\n\n"
                            f"الحساب: **{acc_name}**\n"
                            f"مدة الحظر من تيليجرام: {flood_secs // 60} دقيقة\n"
                            f"⛔ إيقاف جميع الحسابات لـ **{pm} دقيقة** حماية للسمعة",
                            parse_mode="md",
                        )
                    except Exception:
                        pass
                # Do NOT advance link_idx — retry same link after pause
                acc_idx = (acc_idx + 1) % n

            else:
                # ── Normal flood: rotate to next account immediately ──────────
                cooldown_until[acc_idx] = _time.time() + wait_time
                fm, fs = divmod(int(wait_time), 60)
                async with file_lock:
                    counters.setdefault("flood_accounts", {})[acc_name] = (
                        f"🔴 {fm}م{fs:02d}ث (×{FLOOD_MULTIPLIER})"
                    )
                print(
                    f"[FloodWait] '{acc_name}': {flood_secs}s → "
                    f"cooldown {wait_time}s (×{FLOOD_MULTIPLIER}) — rotating"
                )
                acc_idx = (acc_idx + 1) % n
                # Do NOT advance link_idx — retry same link with new account

        except Exception:
            await result_queue.put({"link": link, "action": "error"})
            link_idx += 1

    # ── mark all done ─────────────────────────────────────────────────────────
    async with file_lock:
        fa = counters.setdefault("flood_accounts", {})
        for _, name in clients_info:
            fa[name] = "✅"


# ─────────────────────────────────────────────────────────────────────────────
# Dedicated poster — one account, reads from queue, sends everything
# ─────────────────────────────────────────────────────────────────────────────

async def _poster_worker(
    client: TelegramClient,   # already-open client — do NOT open/close here
    db: dict,
    file_lock: asyncio.Lock,
    seen_set: set,
    result_queue: asyncio.Queue,
    counters: dict,
) -> None:
    """
    Single dedicated poster: uses accounts[0] to send all inspection results to
    archive channels. Runs until it receives the None sentinel.
    The client is opened and managed by run_sorter — do not disconnect here.
    """
    try:
        channel_entities = await _resolve_archive_channels(client, db)
        if channel_entities:
            try:
                save_db(db)
            except Exception:
                pass
        print(
            f"poster: resolved {len(channel_entities)}/{len(db.get('channels', {}))} archive channels"
        )

        while True:
            item = await result_queue.get()

            if item is None:  # sentinel — all inspectors finished
                break

            link   = item["link"]
            action = item["action"]

            if action in ("skip", "error"):
                async with file_lock:
                    seen_set.add(normalize_link(link))
                    mark_seen(link)
                    counters["done"] += 1
                    if action == "error":
                        counters["errors"] += 1
                continue

            # action == "post"
            channel_key = item["channel_key"]
            report      = item["report"]

            # Only post broken links to the archive; all other types are counted but not sent
            sent = False
            if channel_key == "broken":
                target_entity = channel_entities.get(channel_key)
                if target_entity is not None:
                    try:
                        await client.send_message(target_entity, report, parse_mode="md")
                        sent = True
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                        try:
                            await client.send_message(target_entity, report, parse_mode="md")
                            sent = True
                        except Exception as _fe:
                            print(f"[poster] send retry failed ({channel_key}): {_fe}")
                    except Exception as _pe:
                        print(f"[poster] send failed via entity ({channel_key}): {_pe}")

                if not sent:
                    raw_id   = db.get("channels", {}).get(channel_key)
                    raw_hash = db.get("channels_hashes", {}).get(channel_key)
                    if raw_id and isinstance(raw_id, int):
                        target = InputChannel(raw_id, raw_hash) if raw_hash else raw_id
                        try:
                            await client.send_message(target, report, parse_mode="md")
                            sent = True
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds)
                            try:
                                await client.send_message(target, report, parse_mode="md")
                                sent = True
                            except Exception as _fe2:
                                print(f"[poster] send retry (raw_id) failed ({channel_key}): {_fe2}")
                        except Exception as _pe2:
                            print(f"[poster] send failed via raw_id ({channel_key}): {_pe2}")
            else:
                # Non-broken links: classify and count but do not post to any archive channel
                sent = True  # treat as "handled" so it's not counted as an error

            async with file_lock:
                seen_set.add(normalize_link(link))
                mark_seen(link)
                ck = f"ch_{channel_key}"
                db["stats"][ck] = db["stats"].get(ck, 0) + 1
                if channel_key == "broken":
                    db["stats"]["total_broken"] = db["stats"].get("total_broken", 0) + 1
                elif channel_key == "invite":
                    db["stats"]["total_invite"] = db["stats"].get("total_invite", 0) + 1
                    db["stats"]["total_sorted"] = db["stats"].get("total_sorted", 0) + 1
                else:
                    db["stats"]["total_sorted"] = db["stats"].get("total_sorted", 0) + 1
                db["stats"]["total_found"] = db["stats"].get("total_found", 0) + 1
                if not sent:
                    counters["errors"] += 1
                counters["done"] += 1

    except Exception as _poster_crash:
        # Poster crashed — drain queue so inspector doesn't block forever
        print(f"[poster] CRASHED: {_poster_crash}")
        while True:
            try:
                item = result_queue.get_nowait()
                if item is None:
                    break
                async with file_lock:
                    counters["errors"] = counters.get("errors", 0) + 1
                    counters["done"]   = counters.get("done", 0) + 1
            except asyncio.QueueEmpty:
                break




# ─────────────────────────────────────────────────────────────────────────────
# Progress reporter (runs as a background task)
# ─────────────────────────────────────────────────────────────────────────────

def _format_channel_breakdown(stats: dict, errors: int) -> str:
    """Return a multi-line breakdown of results per archive channel."""
    s = stats
    return (
        f"📢 قنوات: **{s.get('ch_channels', 0):,}**  "
        f"👥 مجموعات: **{s.get('ch_groups', 0):,}**  "
        f"🤖 بوتات: **{s.get('ch_bots', 0):,}**\n"
        f"🔐 دعوات: **{s.get('ch_invite', 0):,}**  "
        f"📂 مجلدات: **{s.get('ch_addlist', 0):,}**  "
        f"🌐 غير طبي: **{s.get('ch_other', 0):,}**\n"
        f"💀 تالفة: **{s.get('ch_broken', 0):,}**  "
        f"❌ أخطاء: **{errors:,}**"
    )


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
        done        = counters.get("done", 0)
        errors      = counters.get("errors", 0)
        global_done = start_from + done
        bar         = _build_progress_bar(global_done, raw_total)
        paused      = sorter_ctrl.is_paused()
        status      = "متوقف مؤقتاً ⏸" if paused else "جارٍ..."
        flood_accs  = counters.get("flood_accounts", {})
        acc_line    = "  ".join(
            f"{name} {icon}" for name, icon in flood_accs.items()
        ) if flood_accs else "—"

        text = (
            f"📊 **الفرز الموزع — {status}**\n"
            f"[{bar}]\n\n"
            f"تم: **{global_done:,}** / {raw_total:,} رابط\n"
            f"👤 الحسابات: {acc_line}\n\n"
            + _format_channel_breakdown(db["stats"], errors)
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
    # IMPORTANT: Only load already-ARCHIVED links (global_seen.txt) + joined links.
    # Do NOT include raw_links.json here — those are the INPUT to the sorter.
    # Including raw_links in seen_set would cause every link to be skipped (0% bug).
    seen_set = load_seen_set()
    for lnk in db.get("joined_links", []):
        seen_set.add(normalize_link(lnk))

    # ── Build exclusion sets: source links, archive channel IDs ───────────────
    # Normalized source links (t.me/... as-is)
    source_norm: set = {normalize_link(s) for s in db.get("sources", [])}
    # Archive channel entity IDs (to skip by ID after resolving)
    skip_entity_ids: set = {v for v in db.get("channels", {}).values() if isinstance(v, int)}
    # Combined normalized skip set: sources + any t.me username variants stored
    skip_normalized: set = set(source_norm)

    # ── Build pending list: from start_from onward, skip already-seen & sources
    # Also deduplicate within pending itself (preserve first occurrence order)
    _seen_norms: set = set()
    pending: list = []
    for lnk in raw_links[start_from:]:
        n_ = normalize_link(lnk)
        if n_ in seen_set or n_ in skip_normalized or n_ in _seen_norms:
            continue
        _seen_norms.add(n_)
        pending.append(lnk)
    del _seen_norms
    total = len(pending)

    if total == 0:
        await status_callback(
            "✅ **جميع الروابط تمت معالجتها مسبقاً.**\n\n"
            "إذا أردت إعادة الفرز، امسح الذاكرة أولاً."
        )
        return

    num_accounts = len(accounts)

    # ── Open ALL clients once and keep them alive for the entire sort ──────────
    # This prevents the "database is locked" crash that happens when clients
    # sharing the same .session file are opened/closed concurrently.
    clients_info: list[tuple] = []   # [(TelegramClient, name), ...]
    dead_accounts: list[str]  = []   # human-readable names of expired sessions
    for i, session in enumerate(accounts):
        try:
            client = TelegramClient(
                session, API_ID, API_HASH,
                flood_sleep_threshold=0,   # we handle FloodWait ourselves
            )
            await client.connect()
            me = await client.get_me()
            if me is None:
                # Session file exists but token was invalidated by Telegram
                dead_label = f"حساب {i + 1}"
                dead_accounts.append(dead_label)
                print(f"skip (unauthorized): '{dead_label}' ({session})")
                await client.disconnect()
                continue
            name = (me.first_name or "") + (f" (@{me.username})" if me.username else "")
            if not name.strip():
                name = f"حساب {i + 1}"
            clients_info.append((client, name))
            print(f"connected: '{name}' ({session})")
        except Exception as e:
            print(f"failed to connect account {i + 1} ({session}): {e}")

    # Warn about dead sessions before continuing
    if dead_accounts:
        await status_callback(
            f"⚠️ **جلسات منتهية — تحتاج إعادة تسجيل دخول**\n"
            f"{'، '.join(dead_accounts)}\n\n"
            f"هذه الحسابات لن تُستخدم في الفرز. أضف هذه الحسابات مجدداً من إعدادات الحسابات."
        )

    if not clients_info:
        await status_callback("❌ تعذر الاتصال بأي حساب. تحقق من الجلسات.")
        return

    n = len(clients_info)

    await status_callback(
        f"🔄 **الفرز بالتناوب التسلسلي — بدء**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 حسابات: **{n}** (تناوب تسلسلي) | 🔗 روابط: **{total:,}**\n"
        f"{'🔄 استئناف من حيث توقفنا' if start_from > 0 else '⚡ بدء جديد'}\n"
        f"⚙️ مضاعف الانتظار: **×{FLOOD_MULTIPLIER}** | "
        f"حد الإيقاف الحرج: **{CRITICAL_FLOOD_SECS // 60} دقائق**\n\n"
        + "\n".join(
            f"  · **{name}** — في الانتظار"
            for _, name in clients_info
        )
    )

    file_lock    = asyncio.Lock()
    extra_links: list[str] = []
    counters     = {"done": 0, "errors": 0, "flood_accounts": {}}
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

    # ── Progress reporter background task ─────────────────────────────────────
    prog_msg_id  = sorter_ctrl.progress_msg_id
    prog_chat_id = sorter_ctrl.progress_chat_id

    reporter = asyncio.create_task(
        _progress_reporter(
            bot_client, prog_msg_id, prog_chat_id,
            counters, total, len(raw_links), start_from, db,
        )
    ) if prog_msg_id and prog_chat_id else None

    # ── Start dedicated poster (always clients_info[0]) ───────────────────────
    poster_client = clients_info[0][0]
    poster_task   = asyncio.create_task(
        _poster_worker(poster_client, db, file_lock, seen_set, result_queue, counters)
    )

    # ── Run sequential rotation inspector ─────────────────────────────────────
    try:
        await _sequential_inspector(
            clients_info    = clients_info,
            pending         = pending,
            file_lock       = file_lock,
            seen_set        = seen_set,
            extra_links     = extra_links,
            result_queue    = result_queue,
            counters        = counters,
            skip_entity_ids = skip_entity_ids,
            skip_normalized = skip_normalized,
            db              = db,
            bot_client      = bot_client,
            prog_msg_id     = prog_msg_id,
            prog_chat_id    = prog_chat_id,
        )
    finally:
        if reporter:
            reporter.cancel()
            try:
                await reporter
            except asyncio.CancelledError:
                pass

    # ── Signal poster that all inspections are done, then wait for it ─────────
    await result_queue.put(None)
    await poster_task

    # ── Disconnect all clients cleanly (sequentially to avoid lock conflicts) ─
    for client, cname in clients_info:
        try:
            await client.disconnect()
            print(f"disconnected: '{cname}'")
        except Exception:
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
        stopped     = sorter_ctrl.is_stopped()
        errors_now  = counters.get("errors", 0)
        breakdown   = _format_channel_breakdown(db["stats"], errors_now)
        final_text = (
            (
                f"⏹ **توقف الفرز — التقدم محفوظ**\n"
                f"[{_build_progress_bar(start_from + counters['done'], len(raw_links))}]\n\n"
                f"تم: **{start_from + counters['done']:,}** / {len(raw_links):,} رابط\n\n"
                + breakdown
            ) if stopped else (
                f"🎯 **اكتمل الفرز!**\n"
                f"[{'▓' * 10}] 100%\n\n"
                f"تم: **{start_from + counters['done']:,}** / {len(raw_links):,} رابط\n\n"
                + breakdown
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


# ─────────────────────────────────────────────────────────────────────────────
# Clear archive channels — delete all messages from every archive channel
# ─────────────────────────────────────────────────────────────────────────────

async def clear_archive_channels(accounts: list, db: dict, status_callback=None) -> dict:
    """
    Delete all messages from every archive channel using the first available account.
    Returns {channel_key: deleted_count}.
    """
    if not accounts:
        return {}

    channels = {k: v for k, v in db.get("channels", {}).items() if isinstance(v, int)}
    if not channels:
        return {}

    results = {}

    # Try each account until one connects successfully
    for session in accounts:
        try:
            async with TelegramClient(session, API_ID, API_HASH, flood_sleep_threshold=60) as client:
                # Pre-resolve channel entities
                channel_entities = await _resolve_archive_channels(client, db)

                for key, ch_id in channels.items():
                    entity = channel_entities.get(key)
                    if entity is None:
                        # Try raw ID as fallback
                        try:
                            entity = await client.get_entity(PeerChannel(ch_id))
                        except Exception:
                            results[key] = 0
                            continue

                    deleted = 0
                    try:
                        # Collect all message IDs in batches
                        msg_ids = []
                        async for msg in client.iter_messages(entity, limit=None):
                            msg_ids.append(msg.id)
                            if len(msg_ids) >= 100:
                                try:
                                    await client.delete_messages(entity, msg_ids)
                                    deleted += len(msg_ids)
                                except FloodWaitError as e:
                                    await asyncio.sleep(e.seconds)
                                    await client.delete_messages(entity, msg_ids)
                                    deleted += len(msg_ids)
                                except Exception:
                                    pass
                                msg_ids = []
                        # Delete remaining
                        if msg_ids:
                            try:
                                await client.delete_messages(entity, msg_ids)
                                deleted += len(msg_ids)
                            except FloodWaitError as e:
                                await asyncio.sleep(e.seconds)
                                await client.delete_messages(entity, msg_ids)
                                deleted += len(msg_ids)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    results[key] = deleted
                    if status_callback:
                        try:
                            done_count = sum(1 for v in results.values())
                            await status_callback(
                                f"🗑 جاري مسح القنوات... {done_count}/{len(channels)}\n"
                                f"  ✓ {key}: {deleted} رسالة محذوفة"
                            )
                        except Exception:
                            pass

            return results  # success — stop trying accounts
        except Exception:
            continue

    return results
