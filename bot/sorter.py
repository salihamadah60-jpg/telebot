import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChannelPrivateError
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.chatlists import GetChatlistInviteRequest

from classifier import (
    classify,
    detect_link_type,
    is_addlist_link,
    is_invite_link,
)
from database import (
    load_db,
    save_db,
    is_seen,
    mark_seen,
    load_raw_links,
    save_raw_links,
)
from config import (
    API_ID,
    API_HASH,
    DELAY_MIN,
    DELAY_MAX,
    BREAK_EVERY,
    BREAK_DURATION,
    SWITCH_ACCOUNT_EVERY,
)


async def get_entity_info(client: TelegramClient, link: str) -> dict:
    try:
        entity = await client.get_entity(link)
    except Exception as e:
        return {
            "ok": False,
            "reason": str(e),
            "link": link,
            "is_private": is_invite_link(link),
        }

    title = getattr(entity, "title", "بدون اسم")
    username = getattr(entity, "username", "")
    bio = ""

    try:
        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            full = await client(GetFullChannelRequest(entity))
            bio = getattr(full.full_chat, "about", "") or ""
        else:
            full = await client(GetFullChatRequest(entity))
            bio = getattr(full.full_chat, "about", "") or ""
    except Exception:
        pass

    link_type = detect_link_type(entity)
    members = getattr(entity, "participants_count", None)

    joined = False
    try:
        if hasattr(entity, "left"):
            joined = not entity.left
    except Exception:
        pass

    return {
        "ok": True,
        "title": title,
        "username": username,
        "bio": bio,
        "link_type": link_type,
        "members": members,
        "joined": joined,
        "entity": entity,
    }


async def expand_addlist(client: TelegramClient, link: str) -> list:
    try:
        slug = link.split("addlist/")[-1].strip("/")
        invite = await client(GetChatlistInviteRequest(slug=slug))
        result = []
        peers = getattr(invite, "peers", []) + getattr(invite, "already_peer_chats", [])
        for peer in peers:
            uname = getattr(peer, "username", None)
            if uname:
                result.append(f"https://t.me/{uname}")
        return result
    except Exception:
        return []


def build_report(link: str, info: dict, category: str, account_name: str) -> str:
    if not info.get("ok"):
        reason = info.get("reason", "خطأ غير معروف")
        link_status = "🔐 خاص (رابط دعوة)" if info.get("is_private") else f"❌ تالف ({reason})"
        return (
            f"**الحالة:** {link_status}\n"
            f"**الرابط:** {link}\n"
            f"**بواسطة:** {account_name}\n"
            f"**التصنيف:** تالف وخاص"
        )

    link_type_map = {
        "channel": "📢 قناة",
        "supergroup": "👥 مجموعة كبيرة",
        "group": "👥 مجموعة",
        "bot": "🤖 بوت",
    }
    type_label = link_type_map.get(info.get("link_type", ""), "❓ غير محدد")
    joined_label = "نعم ✅" if info.get("joined") else "لا ❌"
    members = info.get("members")
    members_label = f"{members:,}" if members is not None else "غير متاح"

    return (
        f"📌 **الاسم:** {info.get('title', 'بدون اسم')}\n"
        f"🔗 **الرابط:** {link}\n"
        f"🏷 **النوع:** {type_label}\n"
        f"🧬 **التصنيف:** {category}\n"
        f"👥 **الأعضاء:** {members_label}\n"
        f"✅ **منضم؟:** {joined_label}\n"
        f"👤 **فحص بواسطة:** {account_name}\n"
        f"📝 **الوصف:** {info.get('bio', '')[:200] or '—'}"
    )


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

    total = len(raw_links)
    await status_callback(f"🚀 بدأ الفرز على {total} رابط...")

    acc_idx = 0
    op_count = 0

    for i, link in enumerate(raw_links[start_from:], start=start_from):
        if is_seen(link):
            db["stats"]["total_skipped_duplicate"] += 1
            continue

        if op_count > 0 and op_count % SWITCH_ACCOUNT_EVERY == 0 and len(accounts) > 1:
            acc_idx = (acc_idx + 1) % len(accounts)
            await status_callback(
                f"🔄 التبديل إلى الحساب: `{accounts[acc_idx]}`"
            )

        if op_count > 0 and op_count % BREAK_EVERY == 0:
            await status_callback(
                f"😴 استراحة وقائية {BREAK_DURATION // 60} دقيقة لتجنب الحظر..."
            )
            await asyncio.sleep(BREAK_DURATION)

        session = accounts[acc_idx % len(accounts)]

        try:
            async with TelegramClient(session, API_ID, API_HASH) as client:
                me = await client.get_me()
                account_name = (me.first_name or "") + (
                    f" (@{me.username})" if me.username else ""
                )

                extra_links = []
                if is_addlist_link(link):
                    extra_links = await expand_addlist(client, link)
                    if extra_links:
                        raw_links = raw_links + extra_links
                        save_raw_links(raw_links)
                        await status_callback(
                            f"📂 مجلد: استخرجنا {len(extra_links)} قناة من `{link}`"
                        )

                if is_invite_link(link):
                    info = {"ok": False, "reason": "رابط دعوة", "link": link, "is_private": True}
                    category = "تالف_وخاص"
                else:
                    info = await get_entity_info(client, link)
                    if info.get("ok"):
                        category = classify(
                            info.get("title", ""),
                            info.get("bio", ""),
                            info.get("username", ""),
                        )
                        db["stats"]["total_sorted"] += 1
                    else:
                        category = "تالف_وخاص"
                        db["stats"]["total_broken"] += 1

                report = build_report(link, info, category, account_name)
                target_ch_id = db["channels"].get(category) or db["channels"].get("تالف_وخاص")

                if target_ch_id:
                    await bot_client.send_message(int(target_ch_id), report, parse_mode="md")

                mark_seen(link)
                db["stats"]["total_found"] += 1
                db["progress"]["last_sorted_index"] = i
                save_db(db)

        except FloodWaitError as e:
            await status_callback(f"⚠️ حظر مؤقت، انتظار {e.seconds} ثانية...")
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            await status_callback(f"❌ خطأ في فحص `{link}`: {e}")
            mark_seen(link)

        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        await asyncio.sleep(delay)
        op_count += 1

        if op_count % 50 == 0:
            await status_callback(
                f"📊 تقدم: {i + 1}/{total} — مرتبة: {db['stats']['total_sorted']} | تالفة: {db['stats']['total_broken']}"
            )

    await status_callback("🎯 اكتمل الفرز بنجاح! جميع الروابط تمت معالجتها.")
