import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from classifier import extract_links_from_text, is_addlist_link
from database import load_db, save_db, is_seen, mark_seen, save_raw_links, load_raw_links
from config import API_ID, API_HASH, DELAY_MIN, DELAY_MAX
import state as sorter_ctrl


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


async def harvest_sources(
    status_callback,
    db: dict,
    session: str,
) -> list:
    """
    Harvest ALL Telegram links from every source group / channel.

    Fixes applied:
    - flood_sleep_threshold=60: Telethon auto-waits short FloodWaits internally
    - FloodWait caught INSIDE the per-message loop so iteration resumes after wait
    - Periodic saves every 500 new links so partial progress is not lost
    - harvest_stop flag checked per message so the user can cancel mid-harvest
    """
    existing_raw  = load_raw_links()
    existing_set  = set(existing_raw)
    newly_found: list[str] = []

    # Save partial results to disk
    def _save_partial():
        combined = existing_raw + newly_found
        save_raw_links(combined)
        return combined

    try:
        # flood_sleep_threshold=60: Telethon will auto-sleep for FloodWaits ≤60s
        async with TelegramClient(
            session, API_ID, API_HASH,
            flood_sleep_threshold=60,
        ) as client:

            total_srcs = len(db.get("sources", []))
            for src_idx, source in enumerate(db.get("sources", []), 1):
                # Check stop flag before each source
                if sorter_ctrl.harvest_stop:
                    await status_callback(
                        f"⏹ **تم إيقاف الحصاد.**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 تم حفظ **{len(newly_found):,}** رابط جديد."
                    )
                    _save_partial()
                    return _save_partial()

                src_label = source.split("/")[-1] if "/" in source else source
                _pct = int((src_idx - 1) / total_srcs * 100)
                _bar = "▓" * (_pct // 10) + "░" * (10 - _pct // 10)
                await status_callback(
                    f"🌾 **الحصاد — جارٍ...**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"[{_bar}] {_pct}%\n"
                    f"📍 المصدر [{src_idx}/{total_srcs}]: `{src_label}`\n"
                    f"⏳ جاري الاتصال والقراءة...\n"
                    f"📦 إجمالي الرحلة: **{len(newly_found):,}**"
                )

                try:
                    await client.get_entity(source)
                except Exception as e:
                    await status_callback(f"⚠️ تعذر الوصول للمصدر {src_label}: {e}")
                    continue

                msg_count    = 0
                link_count   = 0
                addlist_count = 0
                last_save_at  = 0

                # Inner iteration — handles FloodWait inside the loop
                async for msg in client.iter_messages(source, limit=None):
                    # Check stop flag per message
                    if sorter_ctrl.harvest_stop:
                        break

                    if not msg:
                        continue

                    try:
                        links = _extract_all_from_message(msg)
                    except Exception:
                        msg_count += 1
                        continue

                    for link in links:
                        if link not in existing_set:
                            existing_set.add(link)
                            newly_found.append(link)
                            link_count += 1

                            if is_addlist_link(link):
                                try:
                                    children = await expand_addlist(client, link)
                                    for child in children:
                                        if child not in existing_set:
                                            existing_set.add(child)
                                            newly_found.append(child)
                                            addlist_count += 1
                                except Exception:
                                    pass

                    msg_count += 1

                    # Periodic progress report + rate-limit pause
                    if msg_count % 500 == 0:
                        _pct2 = int((src_idx - 1) / total_srcs * 100)
                        _bar2 = "▓" * (_pct2 // 10) + "░" * (10 - _pct2 // 10)
                        await status_callback(
                            f"🌾 **الحصاد — جارٍ...**\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"[{_bar2}] المصدر {src_idx}/{total_srcs}\n"
                            f"📍 `{src_label}`\n"
                            f"📨 رسائل مقروءة: **{msg_count:,}**\n"
                            f"🔗 روابط جديدة هنا: **{link_count:,}**\n"
                            f"📦 إجمالي الرحلة: **{len(newly_found):,}**"
                        )
                        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

                    # Periodic disk save every 500 newly found links
                    if len(newly_found) - last_save_at >= 500:
                        _save_partial()
                        last_save_at = len(newly_found)

                # Source done
                if sorter_ctrl.harvest_stop:
                    await status_callback(
                        f"⏹ **توقف الحصاد**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📍 `{src_label}` (المصدر {src_idx}/{total_srcs})\n"
                        f"📨 رسائل: **{msg_count:,}** | 🔗 روابط: **{link_count:,}**\n"
                        f"📦 الإجمالي المحفوظ: **{len(newly_found):,}**"
                    )
                    break

                _pct3 = int(src_idx / total_srcs * 100)
                _bar3 = "▓" * (_pct3 // 10) + "░" * (10 - _pct3 // 10)
                summary = (
                    f"🌾 **الحصاد — جارٍ...**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"[{_bar3}] {_pct3}%\n"
                    f"✅ انتهى [{src_idx}/{total_srcs}]: `{src_label}`\n"
                    f"📨 رسائل: **{msg_count:,}** | 🔗 روابط: **{link_count:,}**"
                )
                if addlist_count:
                    summary += f" | 📂 مجلدات: **{addlist_count:,}**"
                summary += f"\n📦 إجمالي الرحلة: **{len(newly_found):,}**"
                await status_callback(summary)

        combined = _save_partial()

        await status_callback(
            f"🎉 **اكتمل الحصاد!**\n"
            f"📦 الإجمالي الكلي: {len(combined):,} رابط\n"
            f"🆕 تم إضافة: {len(newly_found):,} رابط جديد\n\n"
            f"💾 جميع الروابط محفوظة في `raw_links.json` — "
            f"عمليات الحصاد القادمة ستُضاف إليها تلقائياً."
        )
        return combined

    except FloodWaitError as e:
        wait = e.seconds
        await status_callback(
            f"⏳ تجاوز حد Telegram — انتظار {wait} ثانية...\n"
            f"تم حفظ {len(newly_found):,} رابط حتى الآن."
        )
        combined = _save_partial()
        await asyncio.sleep(wait)
        return combined

    except Exception as e:
        await status_callback(
            f"❌ خطأ في الحصاد: {e}\n"
            f"تم حفظ {len(newly_found):,} رابط حتى الآن."
        )
        return _save_partial()
