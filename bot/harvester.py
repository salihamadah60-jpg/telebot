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


async def harvest_sources(
    status_callback,
    db: dict,
    session: str,
) -> list:
    all_links = []
    existing_raw = load_raw_links()
    existing_set = set(existing_raw)

    try:
        async with TelegramClient(session, API_ID, API_HASH) as client:
            for source in db.get("sources", []):
                await status_callback(f"🔍 جاري سحب الروابط من: `{source}`")
                try:
                    await client.get_entity(source)
                except Exception as e:
                    await status_callback(f"⚠️ تعذر الوصول للمصدر `{source}`: {e}")
                    continue

                op_count = 0
                async for msg in client.iter_messages(source, limit=None):
                    if not msg.text:
                        continue
                    links = extract_links_from_text(msg.text, msg.entities)
                    for link in links:
                        if link not in existing_set:
                            existing_set.add(link)
                            all_links.append(link)

                    op_count += 1
                    if op_count % 200 == 0:
                        await status_callback(
                            f"📦 {op_count} رسالة تمت قراءتها من `{source}`"
                        )
                        delay = random.uniform(DELAY_MIN, DELAY_MAX)
                        await asyncio.sleep(delay)

                await status_callback(
                    f"✅ انتهى سحب `{source}` — وجدنا {len(all_links)} رابط جديد حتى الآن"
                )

        combined = existing_raw + all_links
        save_raw_links(combined)
        return combined

    except FloodWaitError as e:
        await status_callback(f"⚠️ حظر مؤقت أثناء الحصاد، انتظار {e.seconds} ثانية...")
        await asyncio.sleep(e.seconds)
        return all_links
    except Exception as e:
        await status_callback(f"❌ خطأ في الحصاد: {e}")
        return all_links
