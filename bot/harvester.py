import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError, ChannelPrivateError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from classifier import extract_links_from_text, is_addlist_link
from database import load_db, save_db, is_seen, mark_seen, save_raw_links, load_raw_links
from config import API_ID, API_HASH, DELAY_MIN, DELAY_MAX
import state as sorter_ctrl
from channel_setup import add_account_to_channels


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def expand_addlist(client: TelegramClient, link: str) -> list:
    try:
        from telethon.tl.functions.chatlists import GetChatlistInviteRequest
        slug_part = link.split("addlist/")[-1].strip("/")
        invite = await client(GetChatlistInviteRequest(slug=slug_part))
        extracted = []
        peers = getattr(invite, "peers", []) + getattr(invite, "already_peer_chats", [])
        for peer in peers:
            username = getattr(peer, "username", None)
            if username:
                extracted.append(f"https://t.me/{username}")
        return extracted
    except Exception:
        return []


def _extract_all_from_message(msg) -> list[str]:
    """
    Extract every Telegram link from ALL parts of a single message:
    - text / caption
    - message entities (formatted links)
    - inline keyboard button URLs
    - forwarded source channel
    """
    text = msg.text or msg.message or ""
    entities = msg.entities or []
    reply_markup = getattr(msg, "reply_markup", None)
    forward_chat = None
    if msg.forward:
        forward_chat = getattr(msg.forward, "chat", None) or getattr(msg.forward, "sender", None)

    return extract_links_from_text(
        text=text,
        entities=entities,
        reply_markup=reply_markup,
        forward_chat=forward_chat,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pre-harvest: join all accounts to sources + archive channels
# ─────────────────────────────────────────────────────────────────────────────

async def _join_single_account(session: str, targets: list, status_callback, label: str):
    """
    Join one account to a list of target groups/channels.
    Handles public usernames, public t.me/ links, and private invite links (t.me/+...).
    Returns (joined, skipped, failed) counts.
    """
    joined = skipped = failed = 0
    try:
        async with TelegramClient(session, API_ID, API_HASH, flood_sleep_threshold=60) as client:
            me = await client.get_me()
            acc_label = f"@{me.username}" if me.username else str(me.id)
            for target in targets:
                if sorter_ctrl.harvest_stop:
                    break
                try:
                    # Determine if private invite link (t.me/+ or t.me/joinchat/)
                    is_invite = (
                        "/+" in target
                        or "joinchat/" in target
                        or target.startswith("+")
                    )
                    if is_invite:
                        # Extract hash
                        if "/+" in target:
                            invite_hash = target.split("/+")[-1].strip("/")
                        elif "joinchat/" in target:
                            invite_hash = target.split("joinchat/")[-1].strip("/")
                        else:
                            invite_hash = target.strip("+").strip("/")
                        await client(ImportChatInviteRequest(invite_hash))
                    else:
                        # Public link or username
                        entity = await client.get_entity(target)
                        await client(JoinChannelRequest(entity))
                    joined += 1
                    await asyncio.sleep(random.uniform(2, 4))
                except UserAlreadyParticipantError:
                    skipped += 1
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 5)
                except ChannelPrivateError:
                    failed += 1
                except Exception:
                    failed += 1
    except Exception:
        pass
    return joined, skipped, failed


async def pre_harvest_setup(
    sessions: list,
    sources: list,
    channels: dict,
    status_callback,
    db: dict = None,
):
    """
    Before harvesting:
    1. Join ALL accounts to all source groups (skip already-joined).
    2. Ensure ALL accounts are members/admins of the 7 archive channels.
    Runs accounts in parallel for speed.
    """
    total_accounts = len(sessions)
    if total_accounts == 0:
        return

    await status_callback(
        f"🔗 **تجهيز الحسابات — انضمام للمصادر والقنوات**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 عدد الحسابات: **{total_accounts}**\n"
        f"📋 عدد المصادر: **{len(sources)}**\n"
        f"📺 قنوات الأرشيف: **{len(channels)}**\n"
        f"⏳ جاري الانضمام بالتوازي..."
    )

    # Step 1: Join all accounts to source groups simultaneously
    source_tasks = [
        _join_single_account(sess, list(sources), status_callback, f"حساب {i+1}")
        for i, sess in enumerate(sessions)
    ]
    results = await asyncio.gather(*source_tasks, return_exceptions=True)

    total_joined = sum(r[0] for r in results if isinstance(r, tuple))
    total_skipped = sum(r[1] for r in results if isinstance(r, tuple))

    await status_callback(
        f"✅ **انتهى الانضمام للمصادر**\n"
        f"✔️ انضم: **{total_joined}** | ⏭ موجود: **{total_skipped}**"
    )

    # Step 2: Ensure all accounts are in the 7 archive channels
    if channels and db is not None:
        await status_callback(
            f"📺 **إضافة الحسابات لقنوات الأرشيف السبع...**"
        )
        for sess in sessions:
            try:
                await add_account_to_channels(sess, db)
            except Exception:
                pass
        await status_callback(
            f"✅ **تم التحقق من عضوية جميع الحسابات في قنوات الأرشيف**\n\n"
            f"🌾 بدء الحصاد الموزع..."
        )
    else:
        await status_callback(f"🌾 بدء الحصاد الموزع...")


# ─────────────────────────────────────────────────────────────────────────────
# Per-account harvesting worker
# ─────────────────────────────────────────────────────────────────────────────

async def _harvest_worker(
    session: str,
    sources_subset: list,
    existing_set: set,
    shared_lock: asyncio.Lock,
    shared_results: list,
    status_callback,
    acc_index: int,
    total_accs: int,
):
    """
    Harvests a subset of sources using a single account.
    Thread-safe via shared_lock when touching shared_results / existing_set.
    """
    total_srcs = len(sources_subset)
    if total_srcs == 0:
        return

    try:
        async with TelegramClient(session, API_ID, API_HASH, flood_sleep_threshold=60) as client:
            for src_idx, source in enumerate(sources_subset, 1):
                if sorter_ctrl.harvest_stop:
                    break

                src_label = source.split("/")[-1] if "/" in source else source
                msg_count = 0
                link_count = 0
                addlist_count = 0
                local_new: list[str] = []

                try:
                    await client.get_entity(source)
                except Exception as e:
                    await status_callback(
                        f"⚠️ [حساب {acc_index}] تعذر الوصول: `{src_label}` — {e}"
                    )
                    continue

                async for msg in client.iter_messages(source, limit=None, reverse=True):
                    if sorter_ctrl.harvest_stop:
                        break
                    if not msg:
                        continue

                    try:
                        links = _extract_all_from_message(msg)
                    except Exception:
                        msg_count += 1
                        continue

                    new_for_msg: list[str] = []
                    async with shared_lock:
                        for link in links:
                            if link not in existing_set:
                                existing_set.add(link)
                                new_for_msg.append(link)

                    if new_for_msg:
                        for link in new_for_msg:
                            local_new.append(link)
                            link_count += 1
                            if is_addlist_link(link):
                                try:
                                    children = await expand_addlist(client, link)
                                    async with shared_lock:
                                        for child in children:
                                            if child not in existing_set:
                                                existing_set.add(child)
                                                local_new.append(child)
                                                addlist_count += 1
                                except Exception:
                                    pass

                    msg_count += 1

                    if msg_count % 500 == 0:
                        await status_callback(
                            f"🌾 [حساب {acc_index}/{total_accs}] `{src_label}` ({src_idx}/{total_srcs})\n"
                            f"📨 رسائل: **{msg_count:,}** | 🔗 جديدة: **{link_count:,}**"
                        )
                        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

                    # Periodic save every 500 new links
                    if len(local_new) % 500 == 0 and local_new:
                        async with shared_lock:
                            shared_results.extend(local_new)
                            local_new.clear()

                # Flush remaining local_new
                if local_new:
                    async with shared_lock:
                        shared_results.extend(local_new)
                        local_new.clear()

                await status_callback(
                    f"✅ [حساب {acc_index}] انتهى: `{src_label}` — "
                    f"📨 {msg_count:,} رسالة | 🔗 {link_count:,} رابط جديد"
                )

    except FloodWaitError as e:
        await status_callback(
            f"⏳ [حساب {acc_index}] FloodWait {e.seconds}s — سيُستأنف تلقائياً."
        )
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        await status_callback(f"❌ [حساب {acc_index}] خطأ: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def harvest_sources(
    status_callback,
    db: dict,
    sessions: list = None,
    # Legacy single-session param kept for backward compat
    session: str = None,
) -> list:
    """
    Harvest ALL Telegram links from every source group / channel.

    Multi-account parallel mode:
    - If multiple sessions provided, sources are split evenly across all accounts.
    - Before harvesting, all accounts are auto-joined to every source group.
    - Each account harvests its assigned subset concurrently.

    Single-account fallback:
    - If only one session or legacy `session` param is passed, works as before.
    """
    # Resolve sessions list
    if sessions is None or len(sessions) == 0:
        if session:
            sessions = [session]
        else:
            sessions = db.get("accounts", [])[:1]

    if not sessions:
        await status_callback("❌ لا توجد حسابات مرتبطة.")
        return load_raw_links()

    sources = db.get("sources", [])
    channels = db.get("channels", {})

    existing_raw = load_raw_links()
    existing_set = set(existing_raw)

    # ── Step 1: Pre-harvest — join all accounts to sources & channels ─────────
    await pre_harvest_setup(sessions, sources, channels, status_callback, db=db)

    if sorter_ctrl.harvest_stop:
        save_raw_links(existing_raw)
        return existing_raw

    # ── Step 2: Split sources across accounts ─────────────────────────────────
    num_accounts = len(sessions)
    # Round-robin distribution
    subsets: list[list] = [[] for _ in range(num_accounts)]
    for i, src in enumerate(sources):
        subsets[i % num_accounts].append(src)

    await status_callback(
        f"🌾 **الحصاد الموزع — بدء**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 حسابات: **{num_accounts}** | 📋 مصادر: **{len(sources)}**\n"
        + "\n".join(
            f"  · حساب {i+1}: **{len(subsets[i])}** مصدر"
            for i in range(num_accounts)
        )
    )

    shared_lock = asyncio.Lock()
    shared_results: list[str] = []

    # Save partial results periodically
    async def _periodic_save():
        while not sorter_ctrl.harvest_stop:
            await asyncio.sleep(30)
            async with shared_lock:
                combined = existing_raw + shared_results
            save_raw_links(combined)

    save_task = asyncio.create_task(_periodic_save())

    try:
        workers = [
            _harvest_worker(
                session=sessions[i],
                sources_subset=subsets[i],
                existing_set=existing_set,
                shared_lock=shared_lock,
                shared_results=shared_results,
                status_callback=status_callback,
                acc_index=i + 1,
                total_accs=num_accounts,
            )
            for i in range(num_accounts)
            if subsets[i]  # skip accounts with no assigned sources
        ]
        await asyncio.gather(*workers)
    finally:
        save_task.cancel()
        try:
            await save_task
        except asyncio.CancelledError:
            pass

    combined = existing_raw + shared_results
    save_raw_links(combined)

    new_count = len(shared_results)
    await status_callback(
        f"🌾 **الحصاد — اكتمل ✅**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"[▓▓▓▓▓▓▓▓▓▓] 100%\n"
        f"📦 الإجمالي: **{len(combined):,}** رابط\n"
        f"🆕 تم إضافة: **{new_count:,}** رابط جديد\n"
        f"💾 محفوظة في `raw_links.json`"
    )
    return combined
