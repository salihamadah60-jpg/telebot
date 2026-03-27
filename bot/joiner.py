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
        self.burst_count     = 0          # joins done in current burst
        self.burst_start_ts  = 0.0        # when current burst started
        self.flood_until_ts  = 0.0        # if FloodWait, don't use until this time

    def is_in_cooldown(self) -> bool:
        now = time.time()
        if now < self.flood_until_ts:
            return True
        if self.burst_count >= JOIN_SAFE_BURST:
            elapsed = now - self.burst_start_ts
            if elapsed < JOIN_BURST_COOLDOWN:
                return True
            # Cooldown elapsed — reset burst
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
        self.burst_count    = JOIN_SAFE_BURST  # force cooldown after flood


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
            # Extract hash from t.me/+HASH or t.me/joinchat/HASH
            if "/+" in link:
                invite_hash = link.split("/+")[-1].strip("/")
            else:
                invite_hash = link.split("/joinchat/")[-1].strip("/")
            await client(ImportChatInviteRequest(invite_hash))
        else:
            # Public username
            entity = await client.get_entity(link)
            await client(JoinChannelRequest(entity))

        return True, "✅ تم الانضمام"

    except UserAlreadyParticipantError:
        return True, "ℹ️ منضم مسبقاً"

    except InviteRequestSentError:
        return True, "⏳ طلب انضمام أُرسل (يحتاج موافقة)"

    except FloodWaitError as e:
        return False, f"⏳ FloodWait: {e.seconds}s"

    except ChannelPrivateError:
        return False, "🔐 القناة/المجموعة خاصة"

    except Exception as e:
        return False, f"❌ خطأ: {e}"


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
    """
    Join up to `max_joins` links from `links_to_join` across all `accounts`.
    Rotates sessions automatically, respects cooldowns, catches FloodWait.
    """
    if not accounts:
        await status_callback("❌ لا توجد حسابات مرتبطة.")
        return
    if not links_to_join:
        await status_callback("❌ لا توجد روابط للانضمام.")
        return

    # Build tracker per session
    trackers: dict[str, SessionJoinTracker] = {
        s: SessionJoinTracker(s) for s in accounts
    }

    # Init join log in db if needed
    if "joined_links" not in db:
        db["joined_links"] = []

    already_joined = set(db["joined_links"])
    pending        = [l for l in links_to_join if l not in already_joined][:max_joins]
    total          = len(pending)

    if total == 0:
        await status_callback("✅ جميع الروابط المحددة تم الانضمام إليها مسبقاً.")
        return

    await status_callback(
        f"🤝 **بدأ الانضمام الذكي!**\n"
        f"📋 روابط للانضمام: **{total}**\n"
        f"👤 حسابات متاحة: **{len(accounts)}**\n"
        f"🔒 حد آمن لكل جلسة: {JOIN_SAFE_BURST} روابط ثم انتظار {JOIN_BURST_COOLDOWN // 60} دقيقة"
    )

    done     = 0
    success  = 0
    failed   = 0
    acc_idx  = 0

    for link in pending:
        # Find a non-cooldown session
        attempts       = 0
        session        = None

        while attempts < len(accounts) * 2:
            candidate = accounts[acc_idx % len(accounts)]
            tracker   = trackers[candidate]

            if not tracker.is_in_cooldown():
                session = candidate
                break

            # All sessions may be in cooldown — find the one with shortest wait
            acc_idx = (acc_idx + 1) % len(accounts)
            attempts += 1

        if session is None:
            # All sessions in cooldown — wait for the shortest one
            min_wait   = min(t.cooldown_remaining() for t in trackers.values())
            wait_secs  = int(min_wait) + 5
            await status_callback(
                f"⏳ جميع الحسابات في فترة تهدئة. انتظار {wait_secs} ثانية..."
            )
            await asyncio.sleep(wait_secs)
            # Reset to first non-cooldown session
            session = accounts[0]
            for s, t in trackers.items():
                if not t.is_in_cooldown():
                    session = s
                    break

        tracker = trackers[session]

        # Execute join
        try:
            async with TelegramClient(session, API_ID, API_HASH) as client:
                me = await client.get_me()
                acc_label = (me.first_name or "") + (
                    f" (@{me.username})" if me.username else ""
                )
                ok, msg = await join_one_link(client, link)

        except FloodWaitError as e:
            tracker.record_flood(e.seconds)
            await status_callback(
                f"⚠️ حظر مؤقت على `{session}` — انتظار {e.seconds}s\n"
                f"سيتم التبديل للحساب التالي."
            )
            acc_idx = (acc_idx + 1) % len(accounts)
            failed += 1
            continue
        except Exception as e:
            msg = f"❌ خطأ حاد: {e}"
            ok  = False

        done += 1

        if ok:
            success += 1
            tracker.record_join()
            db["joined_links"].append(link)
            save_db(db)
            # Rotate session after each burst
            if tracker.burst_count >= JOIN_SAFE_BURST and len(accounts) > 1:
                acc_idx = (acc_idx + 1) % len(accounts)
                await status_callback(
                    f"🔄 الجلسة وصلت لحد الدفعة — تبديل إلى الحساب التالي"
                )
        else:
            failed += 1
            # Check if it was a FloodWait message
            if "FloodWait" in msg:
                wait = int(msg.split(":")[1].strip().replace("s", ""))
                tracker.record_flood(wait)
                await asyncio.sleep(wait)
                acc_idx = (acc_idx + 1) % len(accounts)

        await status_callback(
            f"{'✅' if ok else '❌'} [{done}/{total}] `{link}`\n"
            f"   {msg}\n"
            f"   👤 الحساب: `{acc_label if ok else session}`"
        )

        # Random delay between joins to appear natural
        if done < total:
            delay = random.uniform(JOIN_DELAY_MIN, JOIN_DELAY_MAX)
            await asyncio.sleep(delay)

    await status_callback(
        f"\n🏁 **اكتمل الانضمام الذكي!**\n"
        f"✅ ناجح: {success}\n"
        f"❌ فشل: {failed}\n"
        f"📊 الإجمالي: {done}/{total}"
    )
