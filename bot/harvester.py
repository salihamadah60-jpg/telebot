import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from classifier import extract_links_from_text, is_addlist_link
from database import load_db, save_db, is_seen, mark_seen, save_raw_links, load_raw_links
from config import API_ID, API_HASH, DELAY_MIN, DELAY_MAX, BREAK_EVERY, BREAK_DURATION


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

    # entities covers both text entities and caption entities
    entities = msg.entities or []

    # reply_markup = inline keyboard (buttons with URLs)
    reply_markup = getattr(msg, "reply_markup", None)

    # forward source channel
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
    Reads every message and extracts links from:
      - message text + entities
      - inline keyboard buttons
      - forwarded source channels
      - addlist slugs (expanded to individual t.me links)

    Returns the full de-duplicated raw link list (existing + newly found).
    """
    existing_raw  = load_raw_links()
    existing_set  = set(existing_raw)
    newly_found: list[str] = []

    try:
        async with TelegramClient(session, API_ID, API_HASH) as client:

            for src_idx, source in enumerate(db.get("sources", []), 1):
                src_label = f"`{source}`"
                await status_callback(
                    f"🔍 [{src_idx}/{len(db['sources'])}] جاري سحب الروابط من: {src_label}"
                )

                # Verify we can access the source
                try:
                    await client.get_entity(source)
                except Exception as e:
                    await status_callback(f"⚠️ تعذر الوصول للمصدر {src_label}: {e}")
                    continue

                msg_count    = 0
                link_count   = 0
                addlist_count = 0

                async for msg in client.iter_messages(source, limit=None):
                    if not msg:
                        continue

                    # Extract links from all parts of the message
                    links = _extract_all_from_message(msg)

                    for link in links:
                        if link not in existing_set:
                            existing_set.add(link)
                            newly_found.append(link)
                            link_count += 1

                            # Expand addlist links immediately during harvest
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

                    # Periodic progress report and rate-limiting pause
                    if msg_count % 500 == 0:
                        await status_callback(
                            f"📦 {src_label}\n"
                            f"  رسائل مقروءة: {msg_count:,}\n"
                            f"  روابط جديدة:  {link_count:,}"
                        )
                        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

                    # Anti-flood break every BREAK_EVERY messages
                    if msg_count % BREAK_EVERY == 0 and msg_count > 0:
                        await status_callback(
                            f"😴 استراحة وقائية {BREAK_DURATION // 60} دقيقة..."
                        )
                        await asyncio.sleep(BREAK_DURATION)

                summary = (
                    f"✅ انتهى سحب {src_label}\n"
                    f"  رسائل: {msg_count:,} | "
                    f"روابط جديدة: {link_count:,}"
                )
                if addlist_count:
                    summary += f" | روابط مجلدات: {addlist_count:,}"
                await status_callback(summary)

        # Merge and save
        combined = existing_raw + newly_found
        save_raw_links(combined)

        await status_callback(
            f"🎉 **اكتمل الحصاد!**\n"
            f"📦 الإجمالي الكلي: {len(combined):,} رابط\n"
            f"🆕 تم إضافة: {len(newly_found):,} رابط جديد"
        )
        return combined

    except FloodWaitError as e:
        wait = e.seconds
        await status_callback(
            f"⚠️ حظر مؤقت أثناء الحصاد — انتظار {wait} ثانية...\n"
            f"تم حفظ {len(newly_found):,} رابط حتى الآن."
        )
        await asyncio.sleep(wait)
        combined = existing_raw + newly_found
        save_raw_links(combined)
        return combined

    except Exception as e:
        await status_callback(
            f"❌ خطأ في الحصاد: {e}\n"
            f"تم حفظ {len(newly_found):,} رابط حتى الآن."
        )
        if newly_found:
            combined = existing_raw + newly_found
            save_raw_links(combined)
            return combined
        return existing_raw
