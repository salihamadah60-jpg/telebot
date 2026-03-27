import os
import asyncio
import random

from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    OWNER_ID,
    SESSIONS_DIR,
    CHANNEL_KEYS,
)
from database import (
    load_db,
    save_db,
    clear_seen,
    get_seen_count,
    get_raw_count,
    load_raw_links,
)
from account_manager import AccountManager
from channel_setup import create_archive_channels
from harvester import harvest_sources
from sorter import run_sorter

db = load_db()

bot = TelegramClient("bot_controller", API_ID, API_HASH)


def owner_only(func):
    async def wrapper(event):
        if event.sender_id != OWNER_ID:
            await event.answer("🚫 غير مصرح لك.")
            return
        await func(event)
    return wrapper


async def status_msg(text: str):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="md")
    except Exception:
        pass


@bot.on(events.NewMessage(pattern="/start"))
@owner_only
async def start_handler(event):
    buttons = [
        [
            Button.inline("➕ ربط حساب جديد", b"add_acc"),
            Button.inline("📺 إنشاء قنوات الأرشيف", b"make_ch"),
        ],
        [
            Button.inline("🔗 إضافة مصادر", b"add_src"),
            Button.inline("📋 عرض المصادر", b"list_src"),
        ],
        [
            Button.inline("🌾 حصاد الروابط", b"harvest"),
            Button.inline("🚀 بدء الفرز الشامل", b"run_sort"),
        ],
        [
            Button.inline("📊 إحصائيات", b"stats"),
            Button.inline("🧹 مسح الذاكرة", b"clear_mem"),
        ],
        [
            Button.inline("👤 عرض الحسابات", b"list_acc"),
        ],
    ]
    await event.respond(
        "🏥 **نظام الفلترة الطبية الذكي**\n\n"
        "مرحباً بك! هذا البوت يقوم بحصاد وفرز وتصنيف روابط تيليجرام الطبية تلقائياً.\n\n"
        "**الترتيب المُوصى به للاستخدام:**\n"
        "1️⃣ ربط حساب جديد\n"
        "2️⃣ إنشاء قنوات الأرشيف\n"
        "3️⃣ إضافة المصادر (مجموعات الروابط)\n"
        "4️⃣ حصاد الروابط\n"
        "5️⃣ بدء الفرز الشامل\n\n"
        "اختر من القائمة أدناه:",
        buttons=buttons,
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"add_acc"))
@owner_only
async def add_acc_handler(event):
    await event.answer()
    async with bot.conversation(OWNER_ID, timeout=120) as conv:
        await conv.send_message(
            "📱 أرسل رقم الهاتف بالصيغة الدولية:\n"
            "مثال: `+9671234567890`"
        )
        phone_msg = await conv.get_response()
        phone = phone_msg.text.strip()

        await conv.send_message("⏳ جاري إرسال كود التحقق...")

        from account_manager import AccountManager
        success, result = await AccountManager.add_account_interactive(conv, phone)

        if success:
            if result not in db["accounts"]:
                db["accounts"].append(result)
                save_db(db)
            info = await AccountManager.get_account_info(result)
            await conv.send_message(
                f"✅ تم ربط الحساب بنجاح!\n\n"
                f"👤 **الاسم:** {info['name']}\n"
                f"📱 **المعرف:** {info['username']}\n"
                f"☎️ **الهاتف:** {info['phone']}"
            )
        else:
            await conv.send_message(f"❌ فشل ربط الحساب:\n{result}")


@bot.on(events.CallbackQuery(data=b"list_acc"))
@owner_only
async def list_acc_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond("❌ لا توجد حسابات مرتبطة بعد.")
        return

    lines = ["👤 **الحسابات المرتبطة:**\n"]
    for i, acc in enumerate(db["accounts"], 1):
        info = await AccountManager.get_account_info(acc)
        lines.append(
            f"{i}. **{info['name']}** {info['username']}\n"
            f"   ☎️ {info['phone']}"
        )
    await event.respond("\n".join(lines), parse_mode="md")


@bot.on(events.CallbackQuery(data=b"make_ch"))
@owner_only
async def make_ch_handler(event):
    await event.answer("⏳ جاري إنشاء القنوات...")
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً قبل إنشاء القنوات.")
        return

    await event.respond(
        "🔨 **جاري إنشاء قنوات الأرشيف الخمس...**\n\n"
        "📢 قناة القنوات\n"
        "👥 قناة المجموعات\n"
        "💀 قناة الروابط المنتهية\n"
        "🔐 قناة روابط الدعوة\n"
        "📂 قناة المجلدات (Addlist)",
        parse_mode="md",
    )
    created = await create_archive_channels(db["accounts"][0], db, save_db)

    lines = ["✅ **نتائج إنشاء القنوات الخمس:**\n"]
    for key, ch_id in created.items():
        title = CHANNEL_KEYS.get(key, key)
        status = f"✅ تم إنشاؤها (ID: `{ch_id}`)" if isinstance(ch_id, int) else f"⚠️ {ch_id}"
        lines.append(f"• **{title}**: {status}")

    await event.respond("\n".join(lines), parse_mode="md")


@bot.on(events.CallbackQuery(data=b"add_src"))
@owner_only
async def add_src_handler(event):
    await event.answer()
    async with bot.conversation(OWNER_ID, timeout=180) as conv:
        await conv.send_message(
            "🔗 أرسل روابط مجموعات الروابط المصدر.\n"
            "يمكنك إرسال رابط واحد أو عدة روابط (كل رابط في سطر).\n\n"
            "**مثال:**\n"
            "`https://t.me/medical_links_group`\n"
            "`https://t.me/+AbCdEfGh1234`"
        )
        links_msg = await conv.get_response()
        raw = links_msg.text.strip().split("\n")
        new_sources = [l.strip() for l in raw if l.strip()]

        added = 0
        for src in new_sources:
            if src not in db["sources"]:
                db["sources"].append(src)
                added += 1

        save_db(db)
        await conv.send_message(
            f"✅ تمت إضافة {added} مصدر جديد.\n"
            f"📋 إجمالي المصادر: {len(db['sources'])}"
        )


@bot.on(events.CallbackQuery(data=b"list_src"))
@owner_only
async def list_src_handler(event):
    await event.answer()
    if not db["sources"]:
        await event.respond("❌ لا توجد مصادر مضافة بعد.")
        return
    lines = [f"📋 **المصادر ({len(db['sources'])}):**\n"]
    for i, src in enumerate(db["sources"], 1):
        lines.append(f"{i}. `{src}`")
    await event.respond("\n".join(lines), parse_mode="md")


@bot.on(events.CallbackQuery(data=b"harvest"))
@owner_only
async def harvest_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً.")
        return
    if not db["sources"]:
        await event.respond("❌ يجب إضافة مصادر أولاً.")
        return

    await event.respond(
        "🌾 **بدأ حصاد الروابط...**\n"
        "يتم سحب الروابط من جميع المصادر وحفظها في القائمة الخام.\n"
        "سيتم إرسال تحديثات دورية.",
        parse_mode="md",
    )

    harvested = await harvest_sources(
        status_callback=status_msg,
        db=db,
        session=db["accounts"][0],
    )

    await status_msg(
        f"✅ **اكتمل الحصاد!**\n"
        f"📦 إجمالي الروابط في القائمة الخام: {len(harvested)}"
    )

    buttons = [
        [Button.inline("🚀 بدء الفرز الآن", b"run_sort")],
        [Button.inline("⏳ تأجيل الفرز", b"start")],
    ]
    await bot.send_message(
        OWNER_ID,
        f"🎉 تم جمع **{len(harvested)}** رابط.\nهل تريد بدء الفرز الآن؟",
        buttons=buttons,
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"run_sort"))
@owner_only
async def run_sort_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً.")
        return
    if not db.get("channels"):
        await event.respond("❌ يجب إنشاء القنوات أولاً.")
        return

    raw = load_raw_links()
    if not raw:
        await event.respond(
            "❌ لا توجد روابط في القائمة الخام. قم بتشغيل الحصاد أولاً."
        )
        return

    start_from = db.get("progress", {}).get("last_sorted_index", 0)
    if start_from > 0:
        await event.respond(
            f"🔁 استئناف الفرز من حيث توقفنا (الرابط رقم {start_from + 1})...",
            parse_mode="md",
        )
    else:
        await event.respond(
            f"🚀 **بدأ الفرز الشامل على {len(raw)} رابط...**\n"
            "سيتم تحديثك دورياً بالتقدم.",
            parse_mode="md",
        )

    await run_sorter(
        status_callback=status_msg,
        db=db,
        accounts=db["accounts"],
        bot_client=bot,
        start_from=start_from,
    )


@bot.on(events.CallbackQuery(data=b"stats"))
@owner_only
async def stats_handler(event):
    await event.answer()
    stats = db.get("stats", {})
    seen = get_seen_count()
    raw = get_raw_count()

    text = (
        "📊 **إحصائيات النظام:**\n\n"
        f"📦 **الروابط الخام المجمعة:** {raw}\n"
        f"🔍 **الروابط المفحوصة:** {seen}\n"
        f"✅ **المرتبة بنجاح:** {stats.get('total_sorted', 0)}\n"
        f"❌ **التالفة والخاصة:** {stats.get('total_broken', 0)}\n"
        f"⏭️ **المتخطاة (مكررة):** {stats.get('total_skipped_duplicate', 0)}\n\n"
        f"👤 **الحسابات المرتبطة:** {len(db.get('accounts', []))}\n"
        f"🔗 **المصادر:** {len(db.get('sources', []))}\n"
        f"📺 **القنوات المنشأة:** {len(db.get('channels', {}))}"
    )
    await event.respond(text, parse_mode="md")


@bot.on(events.CallbackQuery(data=b"clear_mem"))
@owner_only
async def clear_mem_handler(event):
    await event.answer()
    buttons = [
        [
            Button.inline("✅ نعم، امسح الذاكرة", b"confirm_clear"),
            Button.inline("❌ إلغاء", b"start"),
        ]
    ]
    await event.respond(
        "⚠️ **تحذير:** سيتم مسح ذاكرة الروابط المفحوصة.\n"
        "هذا يعني أن البوت سيعيد فحص جميع الروابط من البداية.\n\n"
        "هل أنت متأكد؟",
        buttons=buttons,
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"confirm_clear"))
@owner_only
async def confirm_clear_handler(event):
    await event.answer()
    clear_seen()
    db["stats"] = {
        "total_found": 0,
        "total_sorted": 0,
        "total_broken": 0,
        "total_skipped_duplicate": 0,
    }
    db["progress"]["last_sorted_index"] = 0
    save_db(db)
    await event.respond("✅ تم مسح الذاكرة. يمكنك الآن بدء الفرز من جديد.")


async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 البوت يعمل... أرسل /start في تيليجرام للبدء.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
