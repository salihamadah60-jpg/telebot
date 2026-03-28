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
from joiner import run_smart_joiner
from searcher import run_smart_discovery

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


# ─────────────────────────────────────────────────────────────────────────────
# /start — Main control panel
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern="/start"))
@owner_only
async def start_handler(event):
    buttons = [
        [
            Button.inline("➕ ربط حساب جديد",       b"add_acc"),
            Button.inline("📺 إنشاء قنوات الأرشيف",  b"make_ch"),
        ],
        [
            Button.inline("🔗 إضافة مصادر",   b"add_src"),
            Button.inline("📋 عرض المصادر",   b"list_src"),
        ],
        [
            Button.inline("🌾 حصاد الروابط",        b"harvest"),
            Button.inline("⚡ بدء الفرز الشامل",    b"run_sort"),
        ],
        [
            Button.inline("🧠 اكتشاف ذكي",           b"smart_discover"),
            Button.inline("🤝 انضمام ذكي",           b"smart_join"),
        ],
        [
            Button.inline("📊 إحصائيات",             b"stats"),
            Button.inline("🧹 مسح الذاكرة",          b"clear_mem"),
        ],
        [
            Button.inline("👤 عرض الحسابات",         b"list_acc"),
        ],
    ]
    await event.respond(
        "🏥 **نظام الفلترة الطبية الذكي**\n\n"
        "**الترتيب المُوصى به للاستخدام:**\n"
        "1️⃣ ربط حساب جديد\n"
        "2️⃣ إنشاء قنوات الأرشيف الست\n"
        "3️⃣ إضافة المصادر (مجموعات الروابط)\n"
        "4️⃣ حصاد الروابط\n"
        "5️⃣ بدء الفرز الشامل\n"
        "6️⃣ 🧠 اكتشاف ذكي (يجد روابط مشابهة تلقائياً)\n"
        "7️⃣ 🤝 انضمام ذكي للروابط المرتبة\n\n"
        "اختر من القائمة أدناه:",
        buttons=buttons,
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account
# ─────────────────────────────────────────────────────────────────────────────

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
        phone     = phone_msg.text.strip()

        await conv.send_message("⏳ جاري إرسال كود التحقق...")

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


# ─────────────────────────────────────────────────────────────────────────────
# List accounts
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Create the 6 archive channels
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"make_ch"))
@owner_only
async def make_ch_handler(event):
    await event.answer("⏳ جاري إنشاء القنوات...")
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً قبل إنشاء القنوات.")
        return

    await event.respond(
        "🔨 **جاري إنشاء قنوات الأرشيف الست...**\n\n"
        "📢 أرشيف - القنوات\n"
        "👥 أرشيف - المجموعات\n"
        "💀 أرشيف - الروابط المنتهية\n"
        "🔐 أرشيف - روابط الدعوة\n"
        "📂 أرشيف - المجلدات (Addlist)\n"
        "🤖 أرشيف - البوتات",
        parse_mode="md",
    )
    created = await create_archive_channels(db["accounts"][0], db, save_db)

    lines = ["✅ **نتائج إنشاء القنوات الست:**\n"]
    for key, ch_id in created.items():
        title  = CHANNEL_KEYS.get(key, key)
        status = f"✅ تم إنشاؤها (ID: `{ch_id}`)" if isinstance(ch_id, int) else f"⚠️ {ch_id}"
        lines.append(f"• **{title}**: {status}")

    await event.respond("\n".join(lines), parse_mode="md")


# ─────────────────────────────────────────────────────────────────────────────
# Add sources
# ─────────────────────────────────────────────────────────────────────────────

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
        links_msg  = await conv.get_response()
        raw        = links_msg.text.strip().split("\n")
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


# ─────────────────────────────────────────────────────────────────────────────
# List sources
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Harvest
# ─────────────────────────────────────────────────────────────────────────────

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
        [Button.inline("⚡ بدء الفرز الآن", b"run_sort")],
        [Button.inline("⏳ تأجيل الفرز",    b"start")],
    ]
    await bot.send_message(
        OWNER_ID,
        f"🎉 تم جمع **{len(harvested)}** رابط.\nهل تريد بدء الفرز الآن؟",
        buttons=buttons,
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sort
# ─────────────────────────────────────────────────────────────────────────────

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
        await event.respond("❌ لا توجد روابط في القائمة الخام. قم بتشغيل الحصاد أولاً.")
        return

    start_from = db.get("progress", {}).get("last_sorted_index", 0)
    if start_from > 0:
        await event.respond(
            f"🔁 استئناف الفرز من حيث توقفنا (الرابط رقم {start_from + 1})...",
            parse_mode="md",
        )
    else:
        await event.respond(
            f"⚡ **بدأ الفرز الشامل على {len(raw)} رابط...**\n"
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


# ─────────────────────────────────────────────────────────────────────────────
# Smart Join — Step 1: Choose source channel
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"smart_join"))
@owner_only
async def smart_join_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً.")
        return
    if not db.get("channels"):
        await event.respond("❌ يجب إنشاء القنوات وتشغيل الفرز أولاً حتى تكون هناك روابط للانضمام.")
        return

    # Build channel-selection buttons from the 6 archive channels
    channel_buttons = []
    key_map = {
        "channels": (b"jch_channels", "📢 من قناة القنوات"),
        "groups":   (b"jch_groups",   "👥 من قناة المجموعات"),
        "invite":   (b"jch_invite",   "🔐 من قناة روابط الدعوة"),
        "addlist":  (b"jch_addlist",  "📂 من قناة المجلدات"),
        "bots":     (b"jch_bots",     "🤖 من قناة البوتات"),
    }
    for key, (cb_data, label) in key_map.items():
        if key in db.get("channels", {}):
            channel_buttons.append([Button.inline(label, cb_data)])

    if not channel_buttons:
        await event.respond("❌ لا توجد قنوات أرشيف منشأة بعد.")
        return

    channel_buttons.append([Button.inline("❌ إلغاء", b"start")])

    await event.respond(
        "🤝 **الانضمام الذكي**\n\n"
        "اختر من أيّ قناة أرشيف تريد أن يجمع البوت الروابط للانضمام إليها:",
        buttons=channel_buttons,
        parse_mode="md",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Smart Join — Step 2: How many links (triggered by channel choice)
# ─────────────────────────────────────────────────────────────────────────────

async def _ask_join_count_and_start(event, source_key: str):
    """Ask how many links, then start the smart joiner."""
    await event.answer()
    channel_label = CHANNEL_KEYS.get(source_key, source_key)

    async with bot.conversation(OWNER_ID, timeout=120) as conv:
        await conv.send_message(
            f"✅ المصدر المختار: **{channel_label}**\n\n"
            "📊 كم رابطاً تريد الانضمام إليه؟\n"
            "أرسل رقماً (مثال: `20`):"
        )
        count_msg = await conv.get_response()

        try:
            max_joins = int(count_msg.text.strip())
            if max_joins <= 0:
                raise ValueError
        except ValueError:
            await conv.send_message("❌ رقم غير صحيح. تم الإلغاء.")
            return

        await conv.send_message(
            f"⏳ سيبدأ البوت الانضمام إلى **{max_joins}** رابط من **{channel_label}**\n"
            f"🔒 البوت يحمي الحسابات تلقائياً بنظام الدفعات الذكي.\n"
            f"سيتم إرسال تحديثات لكل رابط."
        )

    # Collect links from the chosen archive channel messages
    # We read raw_links.json filtered to links already sorted into that channel.
    # Alternatively, use the channel itself as source (read messages from it).
    source_ch_id = db["channels"].get(source_key)
    links_to_join = []

    if source_ch_id:
        try:
            async with TelegramClient(db["accounts"][0], API_ID, API_HASH) as client:
                async for msg in client.iter_messages(int(source_ch_id), limit=500):
                    if msg.text:
                        import re
                        found = re.findall(
                            r"https?://t\.me/[\+a-zA-Z0-9_/]+", msg.text
                        )
                        for lnk in found:
                            lnk = lnk.strip().rstrip("/")
                            if lnk not in links_to_join:
                                links_to_join.append(lnk)
                        if len(links_to_join) >= max_joins * 3:
                            break
        except Exception as e:
            await status_msg(f"❌ خطأ في قراءة روابط القناة: {e}")

    if not links_to_join:
        await status_msg(
            "❌ لم يتم العثور على روابط في القناة المختارة.\n"
            "تأكد من تشغيل الفرز أولاً."
        )
        return

    await run_smart_joiner(
        status_callback=status_msg,
        links_to_join=links_to_join,
        accounts=db["accounts"],
        db=db,
        max_joins=max_joins,
    )


@bot.on(events.CallbackQuery(data=b"jch_channels"))
@owner_only
async def jch_channels(event):
    await _ask_join_count_and_start(event, "channels")


@bot.on(events.CallbackQuery(data=b"jch_groups"))
@owner_only
async def jch_groups(event):
    await _ask_join_count_and_start(event, "groups")


@bot.on(events.CallbackQuery(data=b"jch_invite"))
@owner_only
async def jch_invite(event):
    await _ask_join_count_and_start(event, "invite")


@bot.on(events.CallbackQuery(data=b"jch_addlist"))
@owner_only
async def jch_addlist(event):
    await _ask_join_count_and_start(event, "addlist")


@bot.on(events.CallbackQuery(data=b"jch_bots"))
@owner_only
async def jch_bots(event):
    await _ask_join_count_and_start(event, "bots")


# ─────────────────────────────────────────────────────────────────────────────
# Smart Discovery — find new medical links automatically (5 methods)
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"smart_discover"))
@owner_only
async def smart_discover_handler(event):
    await event.answer()
    if not db["accounts"]:
        await event.respond("❌ يجب ربط حساب أولاً.")
        return

    raw_count = get_raw_count()
    buttons = [
        [Button.inline("🚀 ابدأ الاكتشاف الآن", b"confirm_discover")],
        [Button.inline("❌ إلغاء",               b"start")],
    ]
    await event.respond(
        "🧠 **الاكتشاف الذكي**\n\n"
        "سيبحث البوت عن روابط طبية جديدة باستخدام **5 طرق متزامنة:**\n\n"
        "1️⃣ **بحث بكلمات مفتاحية** — أكثر من 80 استعلام بحثي عربي وإنجليزي\n"
        "2️⃣ **قنوات مشابهة** — نظام التوصيات الداخلي في تيليجرام\n"
        "3️⃣ **روابط من البيو** — يقرأ وصف كل قناة ويستخرج الروابط المذكورة\n"
        "4️⃣ **روابط من الرسائل** — يقرأ آخر الرسائل في مجموعات المصادر\n"
        "5️⃣ **أنماط اسم المستخدم** — يولّد اسماء مشابهة للقنوات الموجودة\n\n"
        f"📦 روابط موجودة حالياً: **{raw_count}**\n"
        "⚠️ العملية قد تأخذ 10-30 دقيقة حسب عدد المصادر.",
        buttons=buttons,
        parse_mode="md",
    )


@bot.on(events.CallbackQuery(data=b"confirm_discover"))
@owner_only
async def confirm_discover_handler(event):
    await event.answer("⏳ جاري البدء...")
    if not db["accounts"]:
        await event.respond("❌ لا توجد حسابات.")
        return

    # Build archive channel IDs map (only integer IDs)
    archive_ids = {
        k: v for k, v in db.get("channels", {}).items()
        if isinstance(v, int)
    }

    source_links = db.get("sources", [])

    await event.respond(
        "🧠 **بدأ الاكتشاف الذكي...**\n"
        "ستصلك تحديثات مستمرة. لا تغلق البوت.",
        parse_mode="md",
    )

    new_count = await run_smart_discovery(
        status_callback=status_msg,
        db=db,
        accounts=db["accounts"],
        archive_channel_ids=archive_ids,
        source_links=source_links,
    )

    if new_count > 0:
        buttons = [
            [Button.inline("⚡ فرز الروابط الجديدة الآن", b"run_sort")],
            [Button.inline("⏳ تأجيل الفرز",              b"start")],
        ]
        await bot.send_message(
            OWNER_ID,
            f"🎉 اكتُشف **{new_count}** رابط جديد!\nهل تريد الفرز الآن؟",
            buttons=buttons,
            parse_mode="md",
        )
    else:
        await status_msg("ℹ️ لم يُكتشف روابط جديدة. المصادر الحالية قد تكون مستنفدة.")


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"stats"))
@owner_only
async def stats_handler(event):
    await event.answer()
    stats = db.get("stats", {})
    seen  = get_seen_count()
    raw   = get_raw_count()

    text = (
        "📊 **إحصائيات النظام:**\n\n"
        f"📦 **الروابط الخام المجمعة:** {raw}\n"
        f"🔍 **الروابط المفحوصة:** {seen}\n"
        f"✅ **المرتبة بنجاح:** {stats.get('total_sorted', 0)}\n"
        f"❌ **التالفة والخاصة:** {stats.get('total_broken', 0)}\n"
        f"⏭️ **المتخطاة (مكررة):** {stats.get('total_skipped_duplicate', 0)}\n"
        f"🤝 **تم الانضمام إليها:** {len(db.get('joined_links', []))}\n\n"
        f"👤 **الحسابات المرتبطة:** {len(db.get('accounts', []))}\n"
        f"🔗 **المصادر:** {len(db.get('sources', []))}\n"
        f"📺 **القنوات المنشأة:** {len(db.get('channels', {}))}"
    )
    await event.respond(text, parse_mode="md")


# ─────────────────────────────────────────────────────────────────────────────
# Clear memory
# ─────────────────────────────────────────────────────────────────────────────

@bot.on(events.CallbackQuery(data=b"clear_mem"))
@owner_only
async def clear_mem_handler(event):
    await event.answer()
    buttons = [
        [
            Button.inline("✅ نعم، امسح الذاكرة", b"confirm_clear"),
            Button.inline("❌ إلغاء",              b"start"),
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


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 البوت يعمل... أرسل /start في تيليجرام للبدء.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
