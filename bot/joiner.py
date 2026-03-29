"""
Smart Joiner — intelligently joins Telegram links using multiple sessions.

How Telegram rate-limits joining:
  - You can join roughly 5-8 groups per session per ~2-3 minutes.
  - Exceeding this triggers a FloodWaitError (temporary ban on that account).

Our protection strategy:
  1. JOIN_SAFE_BURST  = max joins per session per burst (we use 5, conservative).
  2. JOIN_BURST_COOLDOWN = 200 seconds between bursts ON THE SAME SESSION.
  3. After each burst, rotate to the next available session.
  4. If all sessions are in cooldown, wait out the shortest remaining cooldown.
  5. Random delay (8-15s) between individual joins within a burst.
  6. FloodWaitError is caught and the exact wait time is respected.
  7. Joined links are recorded in bot_memory.json to avoid re-joining.
"""

import asyncio
import time
import random
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    UserAlreadyParticipantError,
    InviteRequestSentError,
    ChannelPrivateError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from config import (
    API_ID,
    API_HASH,
    JOIN_SAFE_BURST,
    JOIN_BURST_COOLDOWN,
    JOIN_DELAY_MIN,
    JOIN_DELAY_MAX,
)
from database import save_db


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
            remaining = JOIN_BURST_COOLDOWN - elapsed
            return max(0.0, remaining)
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
# Progress bar helper
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
    bar = _make_bar(done, total)
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
    """
    Returns (success: bool, message: str).
    """
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
# Smart multi-session joiner
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

    trackers: dict[str, SessionJoinTracker] = {
        s: SessionJoinTracker(s) for s in accounts
    }

    if "joined_links" not in db:
        db["joined_links"] = []

    already_joined = set(db["joined_links"])
    pending        = [l for l in links_to_join if l not in already_joined][:max_joins]
    total          = len(pending)

    if total == 0:
        await status_callback(
            f"🤝 **الانضمام الذكي**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ جميع الروابط المحددة تم الانضمام إليها مسبقاً."
        )
        return

    done    = 0
    success = 0
    failed  = 0
    acc_idx = 0

    await status_callback(_join_status_text(
        0, total, 0, 0,
        f"👤 حسابات: {len(accounts)} | 🛡 حد الدفعة: {JOIN_SAFE_BURST} روابط",
    ))

    for link in pending:
        attempts = 0
        session  = None

        while attempts < len(accounts) * 2:
            candidate = accounts[acc_idx % len(accounts)]
            tracker   = trackers[candidate]
            if not tracker.is_in_cooldown():
                session = candidate
                break
            acc_idx = (acc_idx + 1) % len(accounts)
            attempts += 1

        if session is None:
            min_wait  = min(t.cooldown_remaining() for t in trackers.values())
            wait_secs = int(min_wait) + 5
            await status_callback(_join_status_text(
                done, total, success, failed,
                f"📍 `{link.split('/')[-1]}`",
                waiting_msg=f"⏳ جميع الحسابات في تهدئة — انتظار **{wait_secs}** ثانية...",
            ))
            await asyncio.sleep(wait_secs)
            session = accounts[0]
            for s, t in trackers.items():
                if not t.is_in_cooldown():
                    session = s
                    break

        tracker = trackers[session]

        try:
            async with TelegramClient(session, API_ID, API_HASH) as client:
                ok, msg = await join_one_link(client, link)
        except FloodWaitError as e:
            tracker.record_flood(e.seconds)
            acc_idx = (acc_idx + 1) % len(accounts)
            failed += 1
            await status_callback(_join_status_text(
                done, total, success, failed,
                f"⚠️ FloodWait على الحساب — تبديل...",
                waiting_msg=f"⏳ انتظار {e.seconds}s",
            ))
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            msg = f"❌ {type(e).__name__}"
            ok  = False

        done += 1
        short = link.split("/")[-1] or link

        if ok:
            success += 1
            tracker.record_join()
            db["joined_links"].append(link)
            save_db(db)
            if tracker.burst_count >= JOIN_SAFE_BURST and len(accounts) > 1:
                acc_idx = (acc_idx + 1) % len(accounts)
        else:
            failed += 1
            if "FloodWait" in msg:
                try:
                    wait = int(msg.split(":")[1].strip().replace("s", ""))
                    tracker.record_flood(wait)
                    await asyncio.sleep(wait)
                    acc_idx = (acc_idx + 1) % len(accounts)
                except Exception:
                    pass

        icon = "✅" if ok else "❌"
        await status_callback(_join_status_text(
            done, total, success, failed,
            f"{icon} `{short}` — {msg}",
        ))

        if done < total:
            delay = random.uniform(JOIN_DELAY_MIN, JOIN_DELAY_MAX)
            await asyncio.sleep(delay)

    # Final summary
    bar = _make_bar(total, total)
    await status_callback(
        f"🤝 **الانضمام الذكي — اكتمل!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bar}\n"
        f"✅ نجح: **{success}**  |  ❌ فشل: **{failed}**\n"
        f"📊 الإجمالي: **{done}/{total}**"
    )
