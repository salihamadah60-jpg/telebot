import asyncio
from telethon import TelegramClient
from telethon.tl.functions.channels import CreateChannelRequest
from config import API_ID, API_HASH, CHANNEL_NAMES


async def create_archive_channels(session: str, db: dict, save_db_fn) -> dict:
    created = {}
    async with TelegramClient(session, API_ID, API_HASH) as client:
        for cat_key, channel_title in CHANNEL_NAMES.items():
            if cat_key in db.get("channels", {}):
                created[cat_key] = db["channels"][cat_key]
                continue
            try:
                result = await client(
                    CreateChannelRequest(
                        title=channel_title,
                        about=f"أرشيف آلي — تم الإنشاء بواسطة بوت الفلترة الذكي",
                        megagroup=False,
                    )
                )
                ch_id = result.chats[0].id
                db["channels"][cat_key] = ch_id
                created[cat_key] = ch_id
                save_db_fn(db)
                await asyncio.sleep(2)
            except Exception as e:
                created[cat_key] = f"فشل: {e}"
    return created
