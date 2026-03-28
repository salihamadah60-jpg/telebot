"""
Smart Discovery Engine — finds new medical Telegram links using 5 methods:

  Method 1 — Keyword Search:
    Uses contacts.SearchRequest with hundreds of medical Arabic/English queries
    to discover channels, groups and bots directly from Telegram's index.

  Method 2 — Similar Channels:
    For every channel already in the archive, calls GetSimilarChannelsRequest
    which is Telegram's own recommendation engine. Very high signal-to-noise.

  Method 3 — Bio/Description Crawling:
    Reads the bio/about of every known channel and extracts any t.me links
    mentioned there (channels often link to sister channels).

  Method 4 — Message Link Crawling:
    Reads the latest N messages from every known source group and every
    archive channel to extract t.me links posted there.

  Method 5 — Username Pattern Generation:
    Takes known medical channel usernames and generates plausible variants
    (suffixes like _sa, _ksa, _ar, _med, _doc, 2, _official …) then
    tries to resolve each one via Telegram.

All discovered links are deduplicated against raw_links.json and
global_seen.txt, then appended to raw_links.json for sorting later.
"""

import asyncio
import random
import re
import time
from typing import Callable

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.channels import GetChannelRecommendationsRequest
from telethon.tl.functions.channels import GetFullChannelRequest

from config import API_ID, API_HASH, DELAY_MIN, DELAY_MAX
from database import load_raw_links, save_raw_links, is_seen

# ─────────────────────────────────────────────────────────────────────────────
# Discovery search queries — optimised for Telegram's search index.
# These are SHORT, high-recall strings, not the full SPECIALTIES keywords.
# Arabic and English, covering every medical domain.
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_QUERIES: list[str] = [
    # ── General medical Arabic ───────────────────────────────────────────────
    "طب", "طبي", "طبيب", "دكتور", "صحة", "مستشفى", "عيادة",
    "أطباء", "طلاب طب", "كلية طب", "طب بشري",
    "طب سعودي", "طب عربي", "طب خليجي",
    "روابط طبية", "قنوات طبية", "مجموعات طبية",
    "تخصصات طبية", "التخصص الطبي",

    # ── Licensing exams ───────────────────────────────────────────────────────
    "SMLE", "smle سعودي", "USMLE", "PLAB", "MRCP", "MRCS",
    "MRCOG", "MRCPCH", "MRCGP", "DHA", "HAAD", "DOH", "OMSB",
    "QCHP", "NHRA", "KMLE", "prometric", "برومترك",
    "هيئة التخصصات", "الهيئة السعودية للتخصصات",
    "scfhs", "mumaris", "ممارس بلس",
    "تجميعات smle", "ملفات smle", "تجميعات طبية",

    # ── Residency / Fellowship ────────────────────────────────────────────────
    "residency", "fellowship", "ابتعاث", "زمالة", "منحة طبية",
    "إقامة طبية", "البورد السعودي", "بورد عربي",
    "برنامج تدريبي", "تدريب طبي",

    # ── Internal Medicine ─────────────────────────────────────────────────────
    "باطنة", "internal medicine", "internist",
    "قلب", "cardiology", "قلب وأوعية",
    "كلى", "nephrology",
    "كبد", "hepatology", "gastroenterology", "جهاز هضمي",
    "غدد صماء", "endocrinology", "سكري", "diabetes",
    "رئة", "pulmonology", "صدرية",
    "أورام", "oncology", "hematology", "دم",
    "أعصاب", "neurology", "مخ وأعصاب",
    "روماتيزم", "rheumatology",
    "معدية", "infectious diseases",

    # ── Surgery ───────────────────────────────────────────────────────────────
    "جراحة", "surgery", "جراح",
    "عظام", "orthopedic", "عظام ومفاصل",
    "جراحة عامة", "general surgery",
    "مسالك بولية", "urology",
    "جراحة مخ", "neurosurgery",
    "جراحة قلب", "cardiac surgery",
    "تجميل", "plastic surgery",
    "جراحة أطفال", "pediatric surgery",
    "أوعية دموية", "vascular surgery",
    "وجه وفكين", "maxillofacial", "omfs",

    # ── Pediatrics ────────────────────────────────────────────────────────────
    "أطفال", "pediatrics", "طب أطفال",
    "حديثي الولادة", "neonatology",
    "NICU", "PICU",
    "طفولة", "kids health",

    # ── Gynecology / Obstetrics ───────────────────────────────────────────────
    "نساء وولادة", "obstetrics", "gynecology", "obgyn",
    "توليد", "عقم", "IVF", "IVF arabic",
    "أمراض نسائية",

    # ── Dentistry ─────────────────────────────────────────────────────────────
    "أسنان", "dentistry", "dental",
    "تقويم أسنان", "orthodontics",
    "زراعة أسنان", "dental implants",
    "هوليوود سمايل",

    # ── ENT ───────────────────────────────────────────────────────────────────
    "أنف وأذن وحنجرة", "ENT", "otolaryngology",
    "سمعيات", "audiology",

    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "عيون", "ophthalmology", "eye care",
    "شبكية", "retina", "LASIK", "ليزك",

    # ── Dermatology ───────────────────────────────────────────────────────────
    "جلدية", "dermatology", "skin",
    "تجميل جلد", "cosmetic dermatology",

    # ── Psychiatry ────────────────────────────────────────────────────────────
    "نفسية", "psychiatry", "mental health",
    "صحة نفسية", "psychology",

    # ── Pharmacy ─────────────────────────────────────────────────────────────
    "صيدلة", "pharmacy", "pharmacist",
    "صيدلة إكلينيكية", "clinical pharmacy",

    # ── Laboratory ───────────────────────────────────────────────────────────
    "مختبرات طبية", "medical laboratory", "lab",
    "تحاليل طبية", "microbiology", "أحياء دقيقة",
    "بنك دم", "blood bank",

    # ── Radiology ─────────────────────────────────────────────────────────────
    "أشعة", "radiology", "MRI", "CT scan",
    "رنين مغناطيسي", "أشعة مقطعية",
    "أشعة تداخلية", "interventional radiology",
    "طب نووي", "nuclear medicine",

    # ── Anesthesia & Emergency ────────────────────────────────────────────────
    "تخدير", "anesthesia",
    "عناية مركزة", "ICU", "critical care",
    "طوارئ", "emergency medicine",

    # ── Nursing ───────────────────────────────────────────────────────────────
    "تمريض", "nursing", "nurse",
    "تمريض سعودي", "SNLE",

    # ── Allied Health ─────────────────────────────────────────────────────────
    "علاج طبيعي", "physical therapy",
    "تغذية علاجية", "clinical nutrition",
    "صحة مهنية", "occupational health",

    # ── Family Medicine / Preventive ──────────────────────────────────────────
    "طب أسرة", "family medicine",
    "طب وقائي", "preventive medicine",
    "صحة عامة", "public health",
    "وبائيات", "epidemiology",

    # ── Books / Study ─────────────────────────────────────────────────────────
    "كتب طبية", "medical books",
    "ملخصات طبية", "medical notes",
    "محاضرات طبية", "medical lectures",
    "مراجع طبية",

    # ── Jobs / Recruitment ───────────────────────────────────────────────────
    "وظائف طبية", "medical jobs",
    "وظائف صحية", "healthcare jobs",
    "توظيف طبي",

    # ── Arabic country-specific ───────────────────────────────────────────────
    "طب سعودي", "طب مصري", "طب أردني", "طب عراقي",
    "طب يمني", "طب ليبي", "طب سوري", "طب مغربي",
    "طب إماراتي", "طب كويتي", "طب قطري", "طب بحريني",
    "طب عُماني",

    # ── Smart bots for medical ────────────────────────────────────────────────
    "medical bot", "بوت طبي",
    "smle bot", "pharmacy bot",
    "lab bot", "بوت تيليجرام طبي",
]

# Username suffixes to try when generating variants of known usernames
_USERNAME_SUFFIXES = [
    "2", "_sa", "_ksa", "_ar", "_arabic", "_med", "_medical",
    "_doc", "_dr", "_health", "_official", "_channel", "_group",
    "_bot", "_links", "_saudi", "_arab", "_gulf", "_uae",
]

# Link regex
_LINK_RE = re.compile(r"https?://t\.me/[\+a-zA-Z0-9_/]+")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_link(raw: str) -> str:
    link = raw.strip().rstrip("/")
    if not link.startswith("http"):
        link = "https://" + link
    return link


def _is_new_link(link: str, known: set) -> bool:
    normalised = _normalise_link(link).lower()
    return normalised not in known and not is_seen(normalised)


def _entity_to_link(entity) -> str | None:
    uname = getattr(entity, "username", None)
    if uname:
        return f"https://t.me/{uname}"
    return None


async def _safe_sleep(seconds: float):
    await asyncio.sleep(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Method 1 — Keyword Search
# ─────────────────────────────────────────────────────────────────────────────

async def search_by_keywords(
    client: TelegramClient,
    known: set,
    status_cb: Callable,
    limit_per_query: int = 20,
) -> list[str]:
    found: list[str] = []

    queries = random.sample(SEARCH_QUERIES, min(len(SEARCH_QUERIES), 80))

    for i, query in enumerate(queries):
        try:
            result = await client(SearchRequest(q=query, limit=limit_per_query))
            for chat in result.chats:
                link = _entity_to_link(chat)
                if link and _is_new_link(link, known):
                    found.append(link)
                    known.add(link.lower())
        except FloodWaitError as e:
            await status_cb(f"⏳ بحث: انتظار {e.seconds}s بسبب FloodWait...")
            await _safe_sleep(e.seconds)
        except Exception:
            pass

        if i % 20 == 19:
            await status_cb(
                f"🔍 بحث بالكلمات المفتاحية: {i + 1}/{len(queries)} — اكتُشف {len(found)} رابط جديد"
            )
            await _safe_sleep(random.uniform(2.0, 4.0))
        else:
            await _safe_sleep(random.uniform(0.8, 2.0))

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 2 — Similar Channels (Telegram's own recommendation engine)
# ─────────────────────────────────────────────────────────────────────────────

async def get_similar_channels(
    client: TelegramClient,
    source_links: list[str],
    known: set,
    status_cb: Callable,
) -> list[str]:
    found: list[str] = []

    for link in source_links:
        try:
            entity = await client.get_entity(link)
            if not (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
                continue
            result = await client(GetChannelRecommendationsRequest(channel=entity))
            for chat in result.chats:
                lnk = _entity_to_link(chat)
                if lnk and _is_new_link(lnk, known):
                    found.append(lnk)
                    known.add(lnk.lower())
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(1.5, 3.0))

    if found:
        await status_cb(f"🔗 قنوات مشابهة: اكتُشف {len(found)} رابط")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 3 — Bio / Description Crawling
# ─────────────────────────────────────────────────────────────────────────────

async def crawl_bios(
    client: TelegramClient,
    source_links: list[str],
    known: set,
    status_cb: Callable,
) -> list[str]:
    found: list[str] = []

    for link in source_links:
        try:
            entity = await client.get_entity(link)
            bio = ""
            if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
                full = await client(GetFullChannelRequest(entity))
                bio  = getattr(full.full_chat, "about", "") or ""

            for raw in _LINK_RE.findall(bio):
                lnk = _normalise_link(raw)
                if _is_new_link(lnk, known):
                    found.append(lnk)
                    known.add(lnk.lower())
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(0.5, 1.5))

    if found:
        await status_cb(f"📝 روابط من البيو: اكتُشف {len(found)} رابط")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 4 — Message Link Crawling (from archive channels & source groups)
# ─────────────────────────────────────────────────────────────────────────────

async def crawl_messages(
    client: TelegramClient,
    source_links: list[str],
    known: set,
    status_cb: Callable,
    msgs_per_chat: int = 200,
) -> list[str]:
    found: list[str] = []

    for link in source_links:
        try:
            count = 0
            async for msg in client.iter_messages(link, limit=msgs_per_chat):
                text = (msg.text or "") + (
                    getattr(getattr(msg, "media", None), "caption", "") or ""
                )
                for raw in _LINK_RE.findall(text):
                    lnk = _normalise_link(raw)
                    if _is_new_link(lnk, known):
                        found.append(lnk)
                        known.add(lnk.lower())
                count += 1
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(1.0, 2.5))

    if found:
        await status_cb(f"💬 روابط من الرسائل: اكتُشف {len(found)} رابط")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 5 — Username Pattern Generation
# ─────────────────────────────────────────────────────────────────────────────

def _extract_base_username(link: str) -> str | None:
    """Extract bare username from a t.me link (skip invite/addlist links)."""
    if "/+" in link or "/joinchat/" in link or "/addlist/" in link:
        return None
    parts = link.rstrip("/").split("/")
    uname = parts[-1].strip("@")
    if uname and re.match(r"^[a-zA-Z][a-zA-Z0-9_]{3,}$", uname):
        return uname
    return None


async def discover_by_username_patterns(
    client: TelegramClient,
    source_links: list[str],
    known: set,
    status_cb: Callable,
) -> list[str]:
    found: list[str] = []
    base_usernames: list[str] = []

    for link in source_links:
        base = _extract_base_username(link)
        if base and base not in base_usernames:
            base_usernames.append(base)

    candidates: list[str] = []
    for base in base_usernames[:40]:  # cap to avoid too many requests
        # Strip known suffixes first to get clean root
        root = base
        for suf in _USERNAME_SUFFIXES:
            if root.endswith(suf):
                root = root[: -len(suf)]
                break
        for suf in _USERNAME_SUFFIXES:
            candidate = root + suf
            if candidate != base:
                candidates.append(candidate)

    random.shuffle(candidates)
    for candidate in candidates[:100]:  # cap total attempts
        try:
            entity = await client.get_entity(candidate)
            lnk = f"https://t.me/{candidate}"
            if _is_new_link(lnk, known):
                found.append(lnk)
                known.add(lnk.lower())
        except (UsernameInvalidError, UsernameNotOccupiedError):
            pass
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(0.5, 1.5))

    if found:
        await status_cb(f"🔤 أنماط اسم المستخدم: اكتُشف {len(found)} رابط")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Main discovery runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_smart_discovery(
    status_callback: Callable,
    db: dict,
    accounts: list[str],
    archive_channel_ids: dict[str, int],
    source_links: list[str],
) -> int:
    """
    Run all 5 discovery methods and append new links to raw_links.json.
    Returns the count of newly discovered links.
    """
    if not accounts:
        await status_callback("❌ لا توجد حسابات مرتبطة.")
        return 0

    # Build the set of already-known links for fast dedup
    existing_raw    = load_raw_links()
    known: set[str] = {_normalise_link(l).lower() for l in existing_raw}

    # Collect links from the archive channels to feed method 2, 3, 4
    await status_callback(
        "🧠 **بدأ الاكتشاف الذكي!**\n\n"
        "الطريقة 1️⃣: بحث بكلمات مفتاحية\n"
        "الطريقة 2️⃣: قنوات مشابهة (Telegram AI)\n"
        "الطريقة 3️⃣: روابط من البيو\n"
        "الطريقة 4️⃣: روابط من الرسائل\n"
        "الطريقة 5️⃣: أنماط اسم المستخدم\n"
    )

    session = accounts[0]
    all_found: list[str] = []

    # Sources: mix of archive channel links + known source groups
    archive_links = [
        f"https://t.me/c/{ch_id}" if isinstance(ch_id, int) else ch_id
        for ch_id in archive_channel_ids.values()
    ]
    seed_links = source_links + archive_links

    async with TelegramClient(session, API_ID, API_HASH) as client:

        # ── Method 1: Keyword Search ──────────────────────────────────────────
        await status_callback("🔍 **الطريقة 1:** بحث بأكثر من 80 كلمة مفتاحية...")
        m1 = await search_by_keywords(client, known, status_callback)
        all_found.extend(m1)
        await status_callback(f"✅ الطريقة 1 اكتملت: {len(m1)} رابط جديد")

        await _safe_sleep(3)

        # ── Method 2: Similar Channels ────────────────────────────────────────
        await status_callback("🔗 **الطريقة 2:** قنوات مشابهة لما في الأرشيف...")
        m2 = await get_similar_channels(client, seed_links[:50], known, status_callback)
        all_found.extend(m2)
        await status_callback(f"✅ الطريقة 2 اكتملت: {len(m2)} رابط جديد")

        await _safe_sleep(3)

        # ── Method 3: Bio Crawling ────────────────────────────────────────────
        await status_callback("📝 **الطريقة 3:** قراءة بيو القنوات المعروفة...")
        m3 = await crawl_bios(client, seed_links[:80], known, status_callback)
        all_found.extend(m3)
        await status_callback(f"✅ الطريقة 3 اكتملت: {len(m3)} رابط جديد")

        await _safe_sleep(3)

        # ── Method 4: Message Crawling ────────────────────────────────────────
        await status_callback("💬 **الطريقة 4:** قراءة آخر الرسائل في المصادر...")
        m4 = await crawl_messages(client, source_links[:20], known, status_callback)
        all_found.extend(m4)
        await status_callback(f"✅ الطريقة 4 اكتملت: {len(m4)} رابط جديد")

        await _safe_sleep(3)

        # ── Method 5: Username Patterns ───────────────────────────────────────
        await status_callback("🔤 **الطريقة 5:** توليد أسماء مستخدمين مشابهة...")
        m5 = await discover_by_username_patterns(client, seed_links, known, status_callback)
        all_found.extend(m5)
        await status_callback(f"✅ الطريقة 5 اكتملت: {len(m5)} رابط جديد")

    # Deduplicate and save
    unique_new = list(dict.fromkeys(all_found))
    if unique_new:
        updated = existing_raw + unique_new
        save_raw_links(updated)

    await status_callback(
        f"\n🎯 **اكتمل الاكتشاف الذكي!**\n\n"
        f"🔍 بحث كلمات مفتاحية: {len(m1)}\n"
        f"🔗 قنوات مشابهة:      {len(m2)}\n"
        f"📝 روابط من البيو:     {len(m3)}\n"
        f"💬 روابط من الرسائل:  {len(m4)}\n"
        f"🔤 أنماط المستخدمين:  {len(m5)}\n"
        f"──────────────────────\n"
        f"📦 **الإجمالي الجديد: {len(unique_new)} رابط**\n\n"
        f"الروابط أُضيفت لقائمة الحصاد الخام.\n"
        f"شغّل الفرز الشامل لتصنيفها."
    )
    return len(unique_new)
