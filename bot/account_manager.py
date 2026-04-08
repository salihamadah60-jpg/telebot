import os
import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from config import API_ID, API_HASH, SESSIONS_DIR


class AccountManager:
    def __init__(self, session_paths: list):
        self.session_paths = session_paths
        self._index = 0
        self._op_count = 0

    def current_session(self) -> str:
        return self.session_paths[self._index]

    def rotate(self) -> None:
        if len(self.session_paths) > 1:
            self._index = (self._index + 1) % len(self.session_paths)

    def count_op(self, switch_every: int = 100) -> bool:
        self._op_count += 1
        if self._op_count % switch_every == 0:
            self.rotate()
            return True
        return False

    def make_client(self, session: str = None) -> TelegramClient:
        s = session or self.current_session()
        return TelegramClient(s, API_ID, API_HASH)

    @staticmethod
    async def add_account_interactive(get_input_fn, phone: str, edit_fn=None):
        """
        get_input_fn() — async callable that returns the next Message from the user.
        edit_fn(text)  — called to update the single persistent UI message.
        """
        async def _ui(text: str):
            if edit_fn:
                try:
                    await edit_fn(text)
                except Exception:
                    pass

        session_name = os.path.join(
            SESSIONS_DIR, phone.strip().replace("+", "").replace(" ", "")
        )
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()

        try:
            sent = await client.send_code_request(phone)
        except FloodWaitError as e:
            await client.disconnect()
            return False, f"حظر مؤقت، انتظر {e.seconds} ثانية."
        except Exception as e:
            await client.disconnect()
            return False, f"فشل إرسال الكود: {e}"

        await _ui(
            "📩 **أرسل كود التحقق** الذي وصلك من تيليجرام:\n"
            "_(أرسل الأرقام فقط، مثال: 12345)_"
        )
        code_msg = await get_input_fn()
        code = code_msg.text.strip()
        try:
            await code_msg.delete()
        except Exception:
            pass

        await _ui("⏳ جاري التحقق من الكود...")

        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await _ui("🔐 الحساب محمي بكلمة مرور.\n\n**أرسل كلمة المرور:**")
            pw_msg = await get_input_fn()
            try:
                await pw_msg.delete()
            except Exception:
                pass
            await _ui("⏳ جاري التحقق من كلمة المرور...")
            try:
                await client.sign_in(password=pw_msg.text.strip())
            except Exception as e:
                await client.disconnect()
                return False, f"كلمة المرور غير صحيحة: {e}"
        except Exception as e:
            await client.disconnect()
            return False, f"فشل تسجيل الدخول: {e}"

        await client.disconnect()
        return True, session_name

    @staticmethod
    async def get_account_info(session: str) -> dict:
        from telethon.errors import AuthKeyUnregisteredError, UserDeactivatedError, UnauthorizedError
        client = TelegramClient(session, API_ID, API_HASH)
        phone_from_path = "+" + os.path.basename(session)
        try:
            await client.connect()
            # Use get_me() as the authoritative check — is_user_authorized() can return
            # False on transient connection issues even for valid sessions.
            me = None
            try:
                me = await client.get_me()
            except (AuthKeyUnregisteredError, UserDeactivatedError, UnauthorizedError):
                me = None
            except Exception:
                # Fallback: also try is_user_authorized
                try:
                    if not await client.is_user_authorized():
                        me = None
                    else:
                        me = await client.get_me()
                except Exception:
                    me = None

            if me is None:
                await client.disconnect()
                return {
                    "name": "⚠️ انتهت الجلسة",
                    "username": "—",
                    "phone": phone_from_path,
                    "id": 0,
                    "unauthorized": True,
                    "error": "Session expired — account signed out by Telegram",
                }
            name = (me.first_name or "") + (" " + me.last_name if me.last_name else "")
            username = f"@{me.username}" if me.username else "(بدون معرف)"
            phone = ("+" + me.phone) if me.phone and not str(me.phone).startswith("+") else (me.phone or phone_from_path)
            await client.disconnect()
            return {"name": name.strip(), "username": username, "phone": phone, "id": me.id, "unauthorized": False}
        except (AuthKeyUnregisteredError, UserDeactivatedError, UnauthorizedError) as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            return {
                "name": "🚫 محظور / مُسجَّل خروجه",
                "username": "—",
                "phone": phone_from_path,
                "id": 0,
                "unauthorized": True,
                "error": str(e),
            }
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            return {"name": "غير متاح", "username": "?", "phone": phone_from_path, "id": 0, "unauthorized": True, "error": str(e)}
