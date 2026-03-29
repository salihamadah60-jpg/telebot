import asyncio
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    InviteToChannelRequest,
    EditAdminRequest,
)
from telethon.tl.types import ChatAdminRights, PeerChannel
from config import API_ID, API_HASH, CHANNEL_KEYS, OWNER_ID

log = logging.getLogger(__name__)


async def create_archive_channels(session: str, db: dict, save_db_fn) -> dict:
    """Create the 7 archive channels and add ALL connected accounts + owner as admins."""
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

        channels = db.get("channels", {})

        # Add all other connected sessions as admins
        other_sessions = [s for s in db.get("accounts", []) if s != session]
        if other_sessions and channels:
            await _add_sessions_to_channels(client, other_sessions, channels)

        # Add the owner's personal Telegram account as admin to all channels
        if channels and OWNER_ID:
            await _add_owner_to_channels(client, OWNER_ID, channels)

    return created


async def add_account_to_channels(new_session: str, db: dict):
    """
    Called when a new account is added after channels already exist.
    Uses the first existing account (creator) to invite + promote the new one.
    """
    channels = db.get("channels", {})
    accounts = db.get("accounts", [])
    if not channels or not accounts:
        return

    # Pick the first session as the admin doing the invite
    admin_session = accounts[0] if accounts[0] != new_session else (
        accounts[1] if len(accounts) > 1 else None
    )
    if not admin_session:
        return

    async with TelegramClient(admin_session, API_ID, API_HASH) as admin_client:
        await _add_sessions_to_channels(admin_client, [new_session], channels)


async def _add_sessions_to_channels(
    admin_client: TelegramClient,
    sessions: list[str],
    channels: dict,
):
    """Invite each session's user into each channel and promote them to admin."""
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
        # Get the user entity for this session
        try:
            async with TelegramClient(sess, API_ID, API_HASH) as user_client:
                me = await user_client.get_me()
        except Exception:
            continue

        for key, ch_id in channels.items():
            if not isinstance(ch_id, int):
                continue
            try:
                # Resolve channel entity explicitly (avoids raw-int resolution failures)
                channel_entity = await admin_client.get_entity(PeerChannel(ch_id))
            except Exception as e:
                log.warning("channel_setup: cannot resolve channel %s (%s): %s", key, ch_id, e)
                continue
            try:
                # Invite
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
                log.warning("channel_setup: invite %s to %s failed: %s", me.id, key, e)
            try:
                # Promote to admin
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
                log.warning("channel_setup: promote %s in %s failed: %s", me.id, key, e)


async def add_owner_to_channels(db: dict):
    """Public entry point: add the owner to all existing channels using the first session."""
    channels = db.get("channels", {})
    accounts = db.get("accounts", [])
    if not channels or not accounts or not OWNER_ID:
        return
    async with TelegramClient(accounts[0], API_ID, API_HASH) as client:
        await _add_owner_to_channels(client, OWNER_ID, channels)


async def _add_owner_to_channels(
    admin_client: TelegramClient,
    owner_id: int,
    channels: dict,
):
    """Invite the owner's personal Telegram account as admin to all channels."""
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
            channel_entity = await admin_client.get_entity(PeerChannel(ch_id))
        except Exception as e:
            log.warning("channel_setup: cannot resolve channel %s (%s) for owner: %s", key, ch_id, e)
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
            log.warning("channel_setup: promote owner in %s failed: %s", key, e)
