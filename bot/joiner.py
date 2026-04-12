"""
Smart Joiner — parallel multi-account Telegram link joining.

How Telegram rate-limits joining:
  - You can join roughly 5-8 groups per session per ~2-3 minutes.
  - Exceeding this triggers a FloodWaitError (temporary ban on that account).

Our protection strategy:
  1. JOIN_SAFE_BURST  = max joins per session per burst (we use 5, conservative).
  2. JOIN_BURST_COOLDOWN = 200 seconds between bursts ON THE SAME SESSION.
  3. Links are split across all accounts in round-robin — each account joins
     its own subset SIMULTANEOUSLY using asyncio.gather.
  4. Within each account, joins are sequential with random delays.
  5. FloodWaitError is caught and the exact wait time is respected per account.
  6. Joined links are recorded in bot_memory.json to avoid re-joining.
"""

import asyncio
import re
import time
import random
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    UserAlreadyParticipantError,
    InviteRequestSentError,
    ChannelPrivateError,
)
from telethon.tl.functions.channels import JoinChannelRequest, CreateChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from config import (
    API_ID,
    API_HASH,
    JOIN_SAFE_BURST,
    JOIN_BURST_COOLDOWN,
    JOIN_DELAY_MIN,
    JOIN_DELAY_MAX,
)
from database import save_db, load_raw_links, save_raw_links, load_all_known_links, save_whatsapp_links

# ─────────────────────────────────────────────────────────────────────────────
# Link-detection regexes for post-join scanning
# ─────────────────────────────────────────────────────────────────────────────

_TG_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_\-/]+",
    re.IGNORECASE,
)
_WA_LINK_RE = re.compile(
    r"(?:https?://)?(?:chat\.whatsapp\.com|wa\.me)/[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Post-join link scanning  ★ NEW (Task 5)
# ─────────────────────────────────────────────────────────────────────────────

# Discovery channel specs: db key → (title, description)
_DISCOVERY_CHANNELS: dict[str, tuple[str, str]] = {
    "tg_discovery": (
        "روابط تلجرام مكتشفة جديدة",
        "روابط مجموعات وقنوات تيليجرام الطبية المكتشفة تلقائياً",
    ),
    "wa_discovery": (
        "روابط واتساب مكتشفة",
        "روابط مجموعات واتساب الطبية المكتشفة تلقائياً",
    ),
}


async def _get_or_create_discovery_channel(
    client: TelegramClient,
    db: dict,
    channel_key: str,
) -> int | None:
    """
    Return the channel id for a discovery channel.
    Creates it on first call, stores the id in db["discovery_channels"],
    and invites ALL registered accounts as members.
    Returns None on failure.
    """
    db.setdefault("discovery_channels", {})
    if channel_key in db["discovery_channels"]:
        return db["discovery_channels"][channel_key]

    if channel_key not in _DISCOVERY_CHANNELS:
        return None

    title, about = _DISCOVERY_CHANNELS[channel_key]
    try:
        result = await client(
            CreateChannelRequest(
                title=title,
                about=about,
                megagroup=False,  # broadcast channel
            )
        )
        ch_id = result.chats[0].id
        db["discovery_channels"][channel_key] = ch_id
        save_db(db)

        # ── Invite all registered accounts to the new channel ──────────────
        all_accounts: list[str] = db.get("accounts", [])
        for sess in all_accounts:
            try:
                async with TelegramClient(sess, API_ID, API_HASH) as user_client:
                    me = await user_client.get_me()
                from telethon.tl.functions.channels import InviteToChannelRequest as _Invite
                from telethon.tl.types import PeerChannel as _PC
                try:
                    channel_entity = await client.get_entity(_PC(ch_id))
                    await client(_Invite(channel=channel_entity, users=[me]))
                    await asyncio.sleep(1)
                except Exception:
                    pass
            except Exception:
                continue

        return ch_id
    except Exception:
        return None


async def _scan_joined_for_new_links(
    client: TelegramClient,
    joined_entity,
    db: dict,
    status_callback,
    scan_limit: int = 50,
) -> None:
    """
    Scan the last `scan_limit` messages of a newly-joined chat for:
      • Telegram links  → added to raw_links.json for the normal pipeline
      • WhatsApp links  → saved to whatsapp_links.txt
    TG links are also appended raw_links.json for the sorter to classify later.
    """
    try:
        messages = await client.get_messages(joined_entity, limit=scan_limit)
    except Exception:
        return

    # Use the universal known set (raw + archived + joined) to avoid duplicates
    from database import normalize_link as _norm
    existing_known = load_all_known_links(joined_links=db.get("joined_links", []))
    new_tg: list[str] = []
    new_wa: list[str] = []

    for msg in messages:
        text = (msg.text or "") + " " + (msg.message or "")
        for m in _TG_LINK_RE.findall(text):
            link = m.strip().rstrip("/")
            if not link.startswith("http"):
                link = "https://" + link
            if _norm(link) not in existing_known:
                new_tg.append(link)
                existing_known.add(_norm(link))
        for m in _WA_LINK_RE.findall(text):
            link = m.strip().rstrip("/")
            if not link.startswith("http"):
                link = "https://" + link
            if _norm(link) not in existing_known:
                new_wa.append(link)
                existing_known.add(_norm(link))

    # ── Save newly found TG links to raw pipeline ─────────────────────────────
    if new_tg:
        combined = load_raw_links() + new_tg
        save_raw_links(combined)
        await status_callback(
            f"📡 **مسح ما بعد الانضمام:** {len(new_tg)} رابط تيليجرام جديد أُضيف للمعالجة"
        )

    if new_wa:
        saved_wa = save_whatsapp_links(new_wa)
        await status_callback(
            f"💬 **واتساب:** {saved_wa} رابط اكتُشف → حُفظ في ملف روابط الواتساب"
        )


async def _resolve_link_entity(client: TelegramClient, link: str):
    """
    Try to resolve a Telegram link to an entity for message scanning.
    Returns the entity or None for invite/joinchat links we cannot resolve.
    """
    # Only resolve public username-based links (t.me/username)
    # Skip invite hashes (t.me/joinchat/..., t.me/+...)
    m = re.match(
        r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})\s*$",
        link.strip(),
    )
    if not m:
        return None
    username = m.group(1)
    try:
        return await client.get_entity(username)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-session join tracker (in-memory, reset on bot restart)
# ─────────────────────────────────────────────────────────────────────────────

class SessionJoinTracker:
    """Track join burst state for a single Telegram session."""

    def __init__(self, session_path: str):
        self.session_path    = session_path
        self.burst_count     = 0
        self.burst_start_ts  = 0.0
        self.flood_until_ts  = 0.0

    def is_in_cooldown(self) -> bool:
        now = time.time()
        if now < self.flood_until_ts:
            return True
        if self.burst_count >= JOIN_SAFE_BURST:
            elapsed = now - self.burst_start_ts
            if elapsed < JOIN_BURST_COOLDOWN:
                return True
            self.burst_count    = 0
            self.burst_start_ts = 0.0
        return False

    def cooldown_remaining(self) -> float:
        now = time.time()
        if now < self.flood_until_ts:
            return self.flood_until_ts - now
        if self.burst_count >= JOIN_SAFE_BURST:
            elapsed = now - self.burst_start_ts
            return max(0.0, JOIN_BURST_COOLDOWN - elapsed)
        return 0.0

    def record_join(self):
        now = time.time()
        if self.burst_count == 0:
            self.burst_start_ts = now
        self.burst_count += 1

    def record_flood(self, wait_seconds: int):
        self.flood_until_ts = time.time() + wait_seconds
        self.burst_count    = JOIN_SAFE_BURST


# ─────────────────────────────────────────────────────────────────────────────
# Progress helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_bar(done: int, total: int) -> str:
    pct    = int(done / total * 100) if total else 0
    filled = "▓" * (pct // 10)
    empty  = "░" * (10 - pct // 10)
    return f"[{filled}{empty}] {pct}%  ({done}/{total})"


def _join_status_text(
    done: int,
    total: int,
    success: int,
    failed: int,
    last_line: str,
    waiting_msg: str = "",
) -> str:
    bar  = _make_bar(done, total)
    text = (
        f"🤝 **الانضمام الذكي — جارٍ...**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bar}\n"
        f"✅ نجح: **{success}**  |  ❌ فشل: **{failed}**  |  ⏳ متبقٍ: **{total - done}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{last_line}"
    )
    if waiting_msg:
        text += f"\n{waiting_msg}"
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Core joining function for a single link
# ─────────────────────────────────────────────────────────────────────────────

def _is_invite_hash(link: str) -> bool:
    return "/+" in link or "/joinchat/" in link


async def join_one_link(client: TelegramClient, link: str) -> tuple[bool, str]:
    """Returns (success: bool, message: str)."""
    try:
        if _is_invite_hash(link):
            if "/+" in link:
                invite_hash = link.split("/+")[-1].strip("/")
            else:
                invite_hash = link.split("/joinchat/")[-1].strip("/")
            await client(ImportChatInviteRequest(invite_hash))
        else:
            entity = await client.get_entity(link)
            await client(JoinChannelRequest(entity))

        return True, "✅ تم الانضمام"

    except UserAlreadyParticipantError:
        return True, "ℹ️ منضم مسبقاً"
    except InviteRequestSentError:
        return True, "⏳ طلب إرسال (يحتاج موافقة)"
    except FloodWaitError as e:
        return False, f"⏳ FloodWait: {e.seconds}s"
    except ChannelPrivateError:
        return False, "🔐 خاص"
    except Exception as e:
        return False, f"❌ {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Per-account parallel worker
# ─────────────────────────────────────────────────────────────────────────────

async def _account_join_worker(
    session: str,
    links_subset: list[str],
    db: dict,
    lock: asyncio.Lock,
    counters: dict,
    acc_index: int,
    total_accs: int,
    status_callback,
) -> None:
    """One account joins its assigned subset of links sequentially."""
    if not links_subset:
        return

    tracker = SessionJoinTracker(session)

    try:
        async with TelegramClient(session, API_ID, API_HASH) as client:
            for link in links_subset:
                # Wait out burst cooldown for this account
                while tracker.is_in_cooldown():
                    wait = tracker.cooldown_remaining()
                    await asyncio.sleep(min(wait, 10))

                try:
                    ok, msg = await join_one_link(client, link)
                except FloodWaitError as e:
                    tracker.record_flood(e.seconds)
                    await asyncio.sleep(e.seconds + 5)
                    # Retry once after flood wait
                    try:
                        ok, msg = await join_one_link(client, link)
                    except Exception:
                        ok, msg = False, "❌ فشل بعد FloodWait"
                except Exception as e:
                    ok, msg = False, f"❌ {type(e).__name__}"

                async with lock:
                    if ok:
                        tracker.record_join()
                        if "joined_links" not in db:
                            db["joined_links"] = []
                        if link not in db["joined_links"]:
                            db["joined_links"].append(link)
                        save_db(db)
                        counters["success"] += 1
                    else:
                        counters["failed"] += 1
                        if "FloodWait" in msg:
                            try:
                                wait = int(msg.split(":")[1].strip().replace("s", ""))
                                tracker.record_flood(wait)
                            except Exception:
                                pass

                    counters["done"] += 1

                if done_count := counters["done"]:
                    short = link.split("/")[-1] or link
                    icon  = "✅" if ok else "❌"
                    await status_callback(_join_status_text(
                        counters["done"],
                        counters["total"],
                        counters["success"],
                        counters["failed"],
                        f"[حساب {acc_index}/{total_accs}] {icon} `{short}` — {msg}",
                    ))

                if ok:
                    # ── Post-join scan for TG/WA links ★ NEW ─────────────────
                    try:
                        entity = await _resolve_link_entity(client, link)
                        if entity is not None:
                            await _scan_joined_for_new_links(
                                client, entity, db, status_callback
                            )
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(JOIN_DELAY_MIN, JOIN_DELAY_MAX))
                else:
                    await asyncio.sleep(3)

    except Exception as e:
        async with lock:
            counters["errors"] = counters.get("errors", 0) + 1


# ─────────────────────────────────────────────────────────────────────────────
# Main joiner entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_smart_joiner(
    status_callback,
    links_to_join: list[str],
    accounts: list[str],
    db: dict,
    max_joins: int,
) -> None:
    if not accounts:
        await status_callback("❌ لا توجد حسابات مرتبطة.")
        return
    if not links_to_join:
        await status_callback("❌ لا توجد روابط للانضمام.")
        return

    already_joined = set(db.get("joined_links", []))
    pending        = [l for l in links_to_join if l not in already_joined][:max_joins]
    total          = len(pending)

    if total == 0:
        await status_callback(
            f"🤝 **الانضمام الذكي**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ جميع الروابط المحددة تم الانضمام إليها مسبقاً."
        )
        return

    num_accounts = len(accounts)

    await status_callback(_join_status_text(
        0, total, 0, 0,
        f"👤 حسابات: **{num_accounts}** | 🔗 روابط: **{total}**\n"
        f"⚡ وضع موازي — كل حساب يعمل على مجموعته المستقلة",
    ))

    # Split links across accounts round-robin
    subsets = [pending[i::num_accounts] for i in range(num_accounts)]

    lock = asyncio.Lock()
    counters = {"done": 0, "success": 0, "failed": 0, "total": total}

    # Run all account workers in parallel
    workers = [
        _account_join_worker(
            session=accounts[i],
            links_subset=subsets[i],
            db=db,
            lock=lock,
            counters=counters,
            acc_index=i + 1,
            total_accs=num_accounts,
            status_callback=status_callback,
        )
        for i in range(num_accounts)
        if subsets[i]
    ]
    await asyncio.gather(*workers)

    # Final summary
    bar = _make_bar(total, total)
    await status_callback(
        f"🤝 **الانضمام الذكي — اكتمل!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bar}\n"
        f"✅ نجح: **{counters['success']}**  |  ❌ فشل: **{counters['failed']}**\n"
        f"📊 الإجمالي: **{counters['done']}/{total}**"
    )
