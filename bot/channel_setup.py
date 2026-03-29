import asyncio
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    InviteToChannelRequest,
    EditAdminRequest,
    GetChannelsRequest,
)
from telethon.tl.types import ChatAdminRights, PeerChannel, InputChannel
from config import API_ID, API_HASH, CHANNEL_KEYS, OWNER_ID

log = logging.getLogger(__name__)


async def _warm_up_cache(client: TelegramClient):
    """Fetch the account's dialogs so Telethon caches all channel entities."""
    try:
        await client.get_dialogs(limit=200)
    except Exception as e:
        log.warning("channel_setup: get_dialogs failed: %s", e)


async def _resolve_channel(client: TelegramClient, ch_id: int, access_hash: int | None = None):
    """
    Resolve a channel entity reliably.

    Strategy (in order):
    1. Use InputChannel(ch_id, access_hash) if we have the hash — no cache needed.
    2. Use GetChannelsRequest with PeerChannel — works if the account is a member.
    3. Fall back to get_entity(PeerChannel(ch_id)) from local cache.
    """
    if access_hash is not None:
        try:
            result = await client(GetChannelsRequest([InputChannel(ch_id, access_hash)]))
            if result.chats:
                return result.chats[0]
        except Exception:
            pass

    try:
        result = await client(GetChannelsRequest([PeerChannel(ch_id)]))
        if result.chats:
            return result.chats[0]
    except Exception:
        pass

    return await client.get_entity(PeerChannel(ch_id))


async def create_archive_channels(session: str, db: dict, save_db_fn) -> dict:
    """Create the 7 archive channels and add ALL connected accounts + owner as admins."""
    created = {}
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await _warm_up_cache(client)

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
                chat = result.chats[0]
                ch_id = chat.id
                access_hash = chat.access_hash
                db["channels"][key] = ch_id
                # Store access hash alongside the channel ID so we can resolve later
                if "channels_hashes" not in db:
                    db["channels_hashes"] = {}
                db["channels_hashes"][key] = access_hash
                created[key] = ch_id
                save_db_fn(db)
                await asyncio.sleep(2)
            except Exception as e:
                created[key] = f"فشل: {e}"

        channels = db.get("channels", {})
        hashes = db.get("channels_hashes", {})

        # Add all other connected sessions as admins
        other_sessions = [s for s in db.get("accounts", []) if s != session]
        if other_sessions and channels:
            await _add_sessions_to_channels(client, other_sessions, channels, hashes)

        # Add the owner's personal Telegram account as admin to all channels
        if channels and OWNER_ID:
            await _add_owner_to_channels(client, OWNER_ID, channels, hashes)

    return created


async def add_account_to_channels(new_session: str, db: dict):
    """
    Called when a new account is added after channels already exist.
    Uses the first existing account (creator) to invite + promote the new one.
    Tries every available account until one succeeds.
    """
    channels = db.get("channels", {})
    accounts = db.get("accounts", [])
    if not channels or not accounts:
        return

    hashes = db.get("channels_hashes", {})

    # Try each existing account as admin until one can resolve the channels
    for admin_session in accounts:
        if admin_session == new_session:
            continue
        try:
            async with TelegramClient(admin_session, API_ID, API_HASH) as admin_client:
                await _warm_up_cache(admin_client)
                # Verify we can resolve at least one channel before proceeding
                test_key = next(iter(channels))
                test_id = channels[test_key]
                if isinstance(test_id, int):
                    await _resolve_channel(
                        admin_client, test_id, hashes.get(test_key)
                    )
                await _add_sessions_to_channels(
                    admin_client, [new_session], channels, hashes
                )
            return  # success — stop trying
        except Exception as e:
            log.warning(
                "channel_setup: admin %s failed to add account, trying next: %s",
                admin_session, e,
            )
            continue


async def _add_sessions_to_channels(
    admin_client: TelegramClient,
    sessions: list[str],
    channels: dict,
    hashes: dict | None = None,
):
    """Invite each session's user into each channel and promote them to admin."""
    if hashes is None:
        hashes = {}

    admin_rights = ChatAdminRights(
        change_info=True,
        post_messages=True,
        edit_messages=True,
        delete_messages=True,
        ban_users=False,
        invite_users=True,
        pin_messages=True,
        add_admins=False,
        manage_call=False,
    )

    for sess in sessions:
        try:
            async with TelegramClient(sess, API_ID, API_HASH) as user_client:
                me = await user_client.get_me()
        except Exception:
            continue

        for key, ch_id in channels.items():
            if not isinstance(ch_id, int):
                continue
            try:
                channel_entity = await _resolve_channel(
                    admin_client, ch_id, hashes.get(key)
                )
            except Exception as e:
                log.warning(
                    "channel_setup: cannot resolve channel %s (%s): %s", key, ch_id, e
                )
                continue
            try:
                await admin_client(InviteToChannelRequest(
                    channel=channel_entity,
                    users=[me],
                ))
                await asyncio.sleep(1)
            except UserAlreadyParticipantError:
                pass
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception as e:
                log.warning(
                    "channel_setup: invite %s to %s failed: %s", me.id, key, e
                )
            try:
                await admin_client(EditAdminRequest(
                    channel=channel_entity,
                    user_id=me,
                    admin_rights=admin_rights,
                    rank="مساعد",
                ))
                await asyncio.sleep(1)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception as e:
                log.warning(
                    "channel_setup: promote %s in %s failed: %s", me.id, key, e
                )


async def add_owner_to_channels(db: dict):
    """Public entry point: add the owner to all existing channels using the first session."""
    channels = db.get("channels", {})
    accounts = db.get("accounts", [])
    if not channels or not accounts or not OWNER_ID:
        return
    hashes = db.get("channels_hashes", {})
    async with TelegramClient(accounts[0], API_ID, API_HASH) as client:
        await _warm_up_cache(client)
        await _add_owner_to_channels(client, OWNER_ID, channels, hashes)


async def _add_owner_to_channels(
    admin_client: TelegramClient,
    owner_id: int,
    channels: dict,
    hashes: dict | None = None,
):
    """Invite the owner's personal Telegram account as admin to all channels."""
    if hashes is None:
        hashes = {}

    admin_rights = ChatAdminRights(
        change_info=True,
        post_messages=True,
        edit_messages=True,
        delete_messages=True,
        ban_users=False,
        invite_users=True,
        pin_messages=True,
        add_admins=True,
        manage_call=False,
    )

    try:
        owner_entity = await admin_client.get_entity(owner_id)
    except Exception:
        return

    for key, ch_id in channels.items():
        if not isinstance(ch_id, int):
            continue
        try:
            channel_entity = await _resolve_channel(
                admin_client, ch_id, hashes.get(key)
            )
        except Exception as e:
            log.warning(
                "channel_setup: cannot resolve channel %s (%s) for owner: %s",
                key, ch_id, e,
            )
            continue
        try:
            await admin_client(InviteToChannelRequest(
                channel=channel_entity,
                users=[owner_entity],
            ))
            await asyncio.sleep(1)
        except UserAlreadyParticipantError:
            pass
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.warning("channel_setup: invite owner to %s failed: %s", key, e)
        try:
            await admin_client(EditAdminRequest(
                channel=channel_entity,
                user_id=owner_entity,
                admin_rights=admin_rights,
                rank="مالك",
            ))
            await asyncio.sleep(1)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.warning(
                "channel_setup: promote owner in %s failed: %s", key, e
            )
