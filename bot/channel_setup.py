import asyncio
from telethon import TelegramClient
from telethon.tl.functions.channels import CreateChannelRequest
from config import API_ID, API_HASH, CHANNEL_KEYS


async def create_archive_channels(session: str, db: dict, save_db_fn) -> dict:
    """
    Create the 5 archive channels if they don't already exist.
    Stores their IDs in db['channels'] keyed by CHANNEL_KEYS.
    """
    created = {}
    async with TelegramClient(session, API_ID, API_HASH) as client:
        for key, title in CHANNEL_KEYS.items():
            if key in db.get("channels", {}):
                created[key] = db["channels"][key]
                continue
            try:
                result = await client(
                    CreateChannelRequest(
                        title=title,
                        about="أرشيف آلي — تم الإنشاء بواسطة بوت الفلترة الطبي الذكي",
                        megagroup=False,
                    )
                )
                ch_id = result.chats[0].id
                db["channels"][key] = ch_id
                created[key] = ch_id
                save_db_fn(db)
                await asyncio.sleep(2)
            except Exception as e:
                created[key] = f"فشل: {e}"
    return created
