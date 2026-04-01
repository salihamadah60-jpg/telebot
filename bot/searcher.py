"""
Smart Discovery Engine — finds new medical Telegram links using 8 methods:

  Method 1 — Keyword Search:
    Uses contacts.SearchRequest with hundreds of medical Arabic/English queries.

  Method 2 — Similar Channels:
    Telegram's own recommendation engine (GetChannelRecommendationsRequest).

  Method 3 — Bio/Description Crawling:
    Extracts t.me links from every known channel's about/bio section.

  Method 4 — Message Link Crawling:
    Reads recent messages from known sources and archive channels.

  Method 5 — Username Pattern Generation:
    Generates GCC-specific username variants from known channels.

  Method 6 — Compound Query Matrix  ★ NEW
    Cross-products GCC regulatory bodies × medical specialties to generate
    highly targeted compound queries (e.g. "SCFHS Cardiology", "OMSB Nursing").

  Method 7 — Hashtag Discovery  ★ NEW
    Searches Telegram using hashtag-style queries (#DHA_Exam, #SMLE_Recall …).

  Method 8 — Google Custom Search (optional)  ★ NEW
    Uses Google's Custom Search JSON API to run site:t.me dorks and extract
    group links from the open web. Requires GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX
    environment variables (skipped silently if not set).

All discovered links are:
  • Deduplicated against raw_links.json and global_seen.txt
  • Scored for engagement quality (activity_rate, unique_user_ratio)
  • Filtered for scam red-flags before saving
  • Appended to raw_links.json for sorting later
"""

import asyncio
import itertools
import os
import random
import re
import time
from typing import Callable

import urllib.request
import urllib.parse
import json

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
# Saudi Arabia — cities, regions, and national identifiers
# These are used both as standalone search terms AND as anchors in every
# combination phase (ثنائي / ثلاثي / رباعي / خماسي).
# ─────────────────────────────────────────────────────────────────────────────

SAUDI_KEYWORDS: list[str] = [
    # Country-level identifiers
    "Saudi Arabia", "Saudi", "KSA", "المملكة العربية السعودية",
    "المملكة", "السعودية", "سعودي", "طبي سعودي",
    "وزارة الصحة", "MOH KSA", "Ministry of Health Saudi",
    "SCFHS", "هيئة التخصصات الصحية", "ممارس بلس",
    "Saudi Board", "البورد السعودي",

    # Major cities — English
    "Riyadh", "Jeddah", "Dammam", "Khobar", "Dhahran",
    "Mecca", "Medina", "Taif", "Tabuk", "Abha",
    "Hail", "Najran", "Jazan", "Buraydah", "Onaizah",
    "Qassim", "Jubail", "Yanbu", "Khamis Mushait",
    "Al Khobar", "Hafar Al Batin", "Al Ahsa",
    "Al Qunfudah", "Al Baha", "Albaha",
    "Arar", "Sakaka", "Wajh", "Umluj",
    "Majmaah", "Zulfi", "Qatif", "Saihat",

    # Major cities — Arabic
    "الرياض", "جدة", "الدمام", "الخبر", "الظهران",
    "مكة", "مكة المكرمة", "المدينة", "المدينة المنورة",
    "الطائف", "تبوك", "أبها", "حائل", "نجران",
    "جازان", "بريدة", "عنيزة", "القصيم",
    "الجبيل", "ينبع", "خميس مشيط",
    "حفر الباطن", "الأحساء", "القنفذة",
    "الباحة", "عرعر", "سكاكا", "الوجه",
    "المجمعة", "الزلفي", "القطيف", "سيهات",

    # Regions
    "المنطقة الغربية", "المنطقة الشرقية", "المنطقة الوسطى",
    "Western Region", "Eastern Province", "Central Region",
    "منطقة الرياض", "منطقة مكة", "منطقة المدينة",
    "منطقة عسير", "منطقة جازان", "منطقة نجران",
    "منطقة الجوف", "منطقة حائل", "منطقة القصيم",
    "منطقة الحدود الشمالية", "منطقة تبوك",
]


# ─────────────────────────────────────────────────────────────────────────────
# Medical-only seeds — pure specialty / exam / training terms (no geography).
# Combined with SAUDI_KEYWORDS in every anchored combination phase.
# ─────────────────────────────────────────────────────────────────────────────

MEDICAL_SEED_KEYWORDS: list[str] = [
    # Exams
    "SMLE", "MRCP", "MRCGP", "MRCEM", "PLAB", "USMLE", "FCPS", "OSCE",
    "DHA", "HAAD", "DOH", "OMSB", "QCHP", "NHRA", "Prometric",
    # Specialties EN
    "Internal Medicine", "Surgery", "Pediatrics", "Obstetrics", "Gynecology",
    "Emergency Medicine", "ICU", "Radiology", "Psychiatry", "Nursing",
    "Family Medicine", "Orthopedic", "Cardiology", "Neurology", "Oncology",
    "Hematology", "ENT", "Ophthalmology", "Dermatology", "Anesthesia",
    # Specialties AR
    "باطنة", "جراحة", "أطفال", "نساء", "طوارئ", "تمريض",
    "أشعة", "نفسية", "عيون", "أسنان", "صيدلة",
    # Training & jobs
    "Residency", "Fellowship", "Rotation", "Board", "Internship",
    "بورد", "زمالة", "دورة", "امتياز",
    # Common descriptors
    "Group", "Channel", "Recall", "MCQ", "QBank",
    "قناة", "مجموعة", "تجميعات", "اختبار",
    # Year
    "2026",
]


# ─────────────────────────────────────────────────────────────────────────────
# GCC Regulatory bodies — used in compound query matrix
# ─────────────────────────────────────────────────────────────────────────────

GCC_REGULATORS: list[str] = [
    # Saudi Arabia
    "SCFHS", "scfhs", "هيئة التخصصات", "الهيئة السعودية للتخصصات",
    "SMLE", "SNLE", "SDLE", "SPLE", "mumaris", "ممارس بلس", "Mumaris Plus",
    "Saudi Board", "البورد السعودي",
    # UAE – Dubai
    "DHA", "dha dubai", "Sheryan",
    # UAE – Abu Dhabi
    "DOH", "HAAD", "Malafi", "Pearson VUE",
    # UAE – MOH
    "MOH UAE", "وزارة صحة الإمارات",
    # Qatar
    "QCHP", "DHP Qatar", "MOPH Qatar",
    # Oman
    "OMSB", "OMRS", "Oman Medical",
    # Bahrain
    "NHRA", "BLE", "Munshat", "QuadraBay",
    # Kuwait
    "MOH Kuwait", "KMLE", "KIMS",
    # Cross-GCC
    "DataFlow", "داتا فلو", "Prometric", "برومترك",
    "Prometric GCC", "DataFlow verification",
]

# Medical specialties — short forms for matrix queries
_SPECIALTIES_SHORT: list[str] = [
    "Cardiology", "قلب",
    "Internal Medicine", "باطنة",
    "Surgery", "جراحة",
    "Orthopedic", "عظام",
    "Neurosurgery", "جراحة مخ",
    "Pediatrics", "أطفال",
    "Neonatology", "حديثي الولادة",
    "Obstetrics", "نساء وولادة",
    "Gynecology",
    "Dentistry", "أسنان",
    "ENT", "أنف وأذن",
    "Ophthalmology", "عيون",
    "Dermatology", "جلدية",
    "Psychiatry", "نفسية",
    "Radiology", "أشعة",
    "Pharmacy", "صيدلة",
    "Nursing", "تمريض",
    "Anesthesia", "تخدير",
    "Emergency", "طوارئ",
    "ICU", "عناية مركزة",
    "Oncology", "أورام",
    "Nephrology", "كلى",
    "Gastroenterology", "هضمية",
    "Endocrinology", "غدد صماء",
    "Rheumatology", "روماتيزم",
    "Pulmonology", "صدرية",
    "Neurology", "أعصاب",
    "Urology", "مسالك بولية",
    "Physical Therapy", "علاج طبيعي",
    "Laboratory", "مختبر",
    "Family Medicine", "طب أسرة",
    "Public Health", "صحة عامة",
]

# Academic & professional tiers
_TIERS: list[str] = [
    "GP", "General Practitioner", "طبيب عام",
    "Specialist", "أخصائي",
    "Consultant", "استشاري",
    "Resident", "مقيم", "Intern", "امتياز",
    "R1", "R2", "R3", "Fellow", "زمالة",
    "MBBS", "MD",
]

# ─────────────────────────────────────────────────────────────────────────────
# Core keyword list (Method 1)
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_QUERIES: list[str] = (
    # ★ Saudi Arabia cities & identifiers — HIGHEST PRIORITY (searched first)
    SAUDI_KEYWORDS
    + [
    # ── General medical Arabic ───────────────────────────────────────────────
    "طب", "طبي", "طبيب", "أطباء", "دكتور", "دكاترة",
    "صحة", "مستشفى", "عيادة", "طلاب طب", "كلية طب",
    "طب بشري", "طب سعودي", "طب عربي", "طب خليجي",
    "روابط طبية", "قنوات طبية", "مجموعات طبية",
    "تخصصات طبية", "مجموعة أطباء",

    # ── Licensing exams ───────────────────────────────────────────────────────
    "SMLE", "smle سعودي", "تجميعات smle", "ملفات smle",
    "SNLE", "SDLE", "SPLE", "تجميعات SNLE",
    "USMLE", "PLAB", "UKMLA", "MCCQE", "AMC Exam",
    "MRCP", "MRCS", "MRCOG", "MRCPCH", "MRCGP",
    "FRCSC", "FRCPC", "FACS", "FACP",
    "DHA exam", "DHA prometric", "DHA recall",
    "HAAD exam", "DOH exam", "DOH Abu Dhabi",
    "QCHP exam", "Qatar prometric",
    "OMSB exam", "Oman prometric",
    "NHRA exam", "BLE Bahrain",
    "KMLE Kuwait",
    "Prometric", "برومترك", "Prometric recall",
    "هيئة التخصصات", "الهيئة السعودية للتخصصات",
    "scfhs", "mumaris", "ممارس بلس",
    "تجميعات طبية", "تجميعات 2025",
    "نقاط الهيئة", "ساعات الهيئة", "ساعات تعليمية",
    "تصنيف مهني", "professional classification",
    "DataFlow", "داتا فلو", "dataflow status",
    "Primary Source Verification", "PSV",
    "Mumaris Plus", "Sheryan", "Malafi", "OMRS", "KIMS", "Munshat",
    "OET", "IELTS medical",

    # ── Residency / Fellowship / Scholarship ──────────────────────────────────
    "residency", "fellowship", "ابتعاث", "زمالة", "منحة طبية",
    "إقامة طبية", "البورد السعودي", "بورد عربي",
    "Saudi Board", "Arab Board", "برنامج تدريبي", "تدريب طبي",
    "internship", "طبيب امتياز", "امتياز طب",
    "NRMP", "match day", "medical match",
    "scholarship medical", "study abroad medical",

    # ── Internal Medicine ─────────────────────────────────────────────────────
    "باطنة", "internal medicine", "internist",
    "قلب", "cardiology", "قلب وأوعية", "كهرباء قلب",
    "كلى", "nephrology", "dialysis", "غسيل كلى",
    "كبد", "hepatology", "gastroenterology", "جهاز هضمي",
    "غدد صماء", "endocrinology", "سكري", "diabetes",
    "رئة", "pulmonology", "صدرية", "COPD", "asthma ربو",
    "أورام", "oncology", "hematology", "دم",
    "أعصاب", "neurology", "مخ وأعصاب", "stroke جلطة",
    "روماتيزم", "rheumatology", "مفاصل",
    "معدية", "infectious diseases", "HIV", "TB سل",

    # ── Surgery ───────────────────────────────────────────────────────────────
    "جراحة", "surgery", "جراح",
    "عظام", "orthopedic", "عظام ومفاصل", "كسور",
    "جراحة عامة", "general surgery",
    "مسالك بولية", "urology", "بروستاتا",
    "جراحة مخ", "neurosurgery",
    "جراحة قلب", "cardiac surgery",
    "تجميل", "plastic surgery", "ترميم",
    "جراحة أطفال", "pediatric surgery",
    "أوعية دموية", "vascular surgery",
    "وجه وفكين", "maxillofacial", "omfs",
    "جراحة أورام", "surgical oncology",
    "laparoscopic", "منظار جراحي",

    # ── Pediatrics ────────────────────────────────────────────────────────────
    "أطفال", "pediatrics", "طب أطفال",
    "حديثي الولادة", "neonatology",
    "NICU", "PICU", "حضانة خدج",
    "توحد", "autism", "ADHD فرط حركة",

    # ── Gynecology / Obstetrics ───────────────────────────────────────────────
    "نساء وولادة", "obstetrics", "gynecology", "obgyn",
    "توليد", "عقم", "IVF", "أطفال أنابيب",
    "أمراض نسائية", "endometriosis",

    # ── Dentistry ─────────────────────────────────────────────────────────────
    "أسنان", "dentistry", "dental",
    "تقويم أسنان", "orthodontics", "invisalign",
    "زراعة أسنان", "dental implants",
    "هوليوود سمايل", "فينير veneers",
    "علاج جذور", "root canal",
    "SDLE dental",

    # ── ENT ───────────────────────────────────────────────────────────────────
    "أنف وأذن وحنجرة", "ENT", "otolaryngology",
    "سمعيات", "audiology", "زرع قوقعة cochlear",

    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "عيون", "ophthalmology", "eye care",
    "شبكية", "retina", "LASIK", "ليزك",
    "جلوكوما", "glaucoma", "كتاركت cataract",

    # ── Dermatology ───────────────────────────────────────────────────────────
    "جلدية", "dermatology", "skin",
    "تجميل جلد", "cosmetic dermatology",
    "بهاق vitiligo", "صدفية psoriasis",

    # ── Psychiatry ────────────────────────────────────────────────────────────
    "نفسية", "psychiatry", "mental health",
    "صحة نفسية", "psychology", "اكتئاب depression",
    "قلق anxiety", "وسواس OCD",

    # ── Pharmacy ─────────────────────────────────────────────────────────────
    "صيدلة", "pharmacy", "pharmacist",
    "صيدلة إكلينيكية", "clinical pharmacy",
    "pharmacology", "دوائية",

    # ── Laboratory ───────────────────────────────────────────────────────────
    "مختبرات طبية", "medical laboratory", "lab medical",
    "تحاليل طبية", "microbiology", "أحياء دقيقة",
    "بنك دم", "blood bank", "pathology",

    # ── Radiology ─────────────────────────────────────────────────────────────
    "أشعة", "radiology", "MRI رنين",
    "CT scan أشعة مقطعية",
    "أشعة تداخلية", "interventional radiology",
    "طب نووي", "nuclear medicine", "ultrasound سونار",

    # ── Anesthesia & Emergency ────────────────────────────────────────────────
    "تخدير", "anesthesia", "ICU",
    "عناية مركزة", "critical care",
    "طوارئ", "emergency medicine", "ATLS ACLS",

    # ── Nursing ───────────────────────────────────────────────────────────────
    "تمريض", "nursing", "nurse",
    "تمريض سعودي", "SNLE تمريض",
    "أخصائي تمريض", "Nursing Specialist",

    # ── Allied Health ─────────────────────────────────────────────────────────
    "علاج طبيعي", "physical therapy", "physiotherapy",
    "تغذية علاجية", "clinical nutrition", "nutritionist",
    "صحة مهنية", "occupational health",
    "تقنية مختبر", "medical technology",
    "أشعة تشخيصية", "diagnostic radiography",

    # ── Family Medicine / Preventive ──────────────────────────────────────────
    "طب أسرة", "family medicine",
    "طب وقائي", "preventive medicine",
    "صحة عامة", "public health", "وبائيات epidemiology",

    # ── Books / Study Resources ───────────────────────────────────────────────
    "كتب طبية", "medical books",
    "ملخصات طبية", "medical notes",
    "محاضرات طبية", "medical lectures",
    "مراجع طبية", "UWorld", "Qbank طبي",
    "First Aid USMLE", "Oxford Handbook",
    "Amboss", "Passmedicine",

    # ── Jobs / Recruitment ───────────────────────────────────────────────────
    "وظائف طبية", "medical jobs",
    "وظائف صحية", "healthcare jobs",
    "توظيف طبي", "medical recruitment",
    "وظائف سعودية طبية", "وظائف إماراتية طبية",

    # ── Country-specific compound ─────────────────────────────────────────────
    "أطباء السعودية", "أطباء الإمارات", "أطباء الكويت",
    "أطباء قطر", "أطباء البحرين", "أطباء عمان",
    "أطباء الرياض", "أطباء جدة", "أطباء دبي",
    "أطباء أبوظبي", "تجمع أطباء الخليج",
    "طب سعودي", "طب إماراتي", "طب كويتي",
    "طب قطري", "طب بحريني", "طب عُماني",
    "طب مصري", "طب أردني", "طب سوري",
    "تجمع أطباء عمان", "جراحي الإمارات",

    # ── Academic / Professional tiers ─────────────────────────────────────────
    "Resident physician", "مقيم طب",
    "Consultant specialist", "استشاري طب",
    "طبيب امتياز", "Intern physician",
    "Fellow medicine", "زميل أكاديمي",

    # ── Smart bots for medical ────────────────────────────────────────────────
    "medical bot", "بوت طبي",
    "smle bot", "pharmacy bot",
    "lab bot", "بوت تيليجرام طبي",
    "qbank bot", "mcq bot",

    # ── GynObs — all forms ────────────────────────────────────────────────────
    "gynObs", "gyn obs", "ob gyn", "obgyn", "ob/gyn",
    "gynecology", "gynaecology", "obstetrics",
    "obstetrics and gynecology", "obs and gyne", "obs gyne", "obs & gyne",
    "نساء وولادة", "نساء وتوليد", "طب النساء", "طب التوليد",
    "نسائية وولادة", "قسم النساء", "توليد ونساء",
    "MRCOG", "mrcog", "eMRCOG", "emrcog", "DRCOG",
    "بورد نساء", "arab board gyne",
    "IVF", "ivf center", "طفل أنبوب",
    "PCOS", "endometriosis", "بطانة رحم",
    "midwifery", "midwife", "قابلة",
    "caesarean", "c-section", "قيصرية",
    "postpartum", "نفاس", "antenatal",
    "fetal medicine", "طب الأجنة", "MFM",

    # ── New additions ─────────────────────────────────────────────────────────
    # Emergency & critical care
    "ER", "emergency room", "EMERGENCY", "طوارئ ER",
    "PEM", "pediatric emergency", "طوارئ أطفال",
    "ICU fellowship", "زمالة عناية مركزة", "icu زمالة",
    # Exams & certifications
    "MRCEM", "mrcem exam", "mrcem recall",
    "eMRCOG", "emrcog exam",
    "FCPS", "fcps exam", "fcps recall",
    "PASTEL", "pastel platform", "pastel saudi",
    "PTE", "PTE academic", "PTE medical",
    "Goethe", "goethe exam", "goethe zertifikat",
    "diploma", "دبلوم", "دبلومة طبية",
    "board", "بورد", "البورد العربي", "arab board",
    # Months & Year
    "2026 exam", "2026 board",
    "may 2026", "june 2026", "july 2026", "august 2026",
    "january 2026", "february 2026", "march 2026",
    "مايو 2026", "يونيو 2026", "يوليو 2026",
    # Rotations & training
    "rotation", "روتيشن", "surgery rotation", "جراحة دورة",
    "CME", "cme credit", "cme student", "تعليم مستمر",
    "intern rotation", "residency rotation",
    # IMD / Internal Medicine Dept
    "IMD", "imd gl", "قسم الباطنة", "internal medicine department",
    # Nursing & home care
    "home care nursing", "تمريض منزلي",
    "home care nurse", "رعاية منزلية",
    "ICU nursing", "تمريض عناية مركزة",
    # Imaging & radiology
    "imaging", "تصوير طبي", "medical imaging",
    # Orthopedic / endodontics
    "orthopedic", "عظام", "Orthopedic KSA",
    "endodontics", "علاج جذور", "root canal",
    # OBGYN & fetal
    "obgyn", "ob gyn", "نساء وولادة",
    "fetal medicine", "طب الأجنة",
    # Hematology & oncology
    "hematology", "أمراض الدم", "hema",
    # Family medicine
    "FM", "family medicine", "طب أسرة",
    "family medicine hub", "MRCGP",
    # Saudi cities
    "jeddah", "جدة", "khobar", "الخبر", "albaha", "الباحة",
    "riyadh", "الرياض", "dammam", "الدمام",
    "abha", "أبها", "taif", "الطائف", "tabuk", "تبوك",
    # Study & candidates
    "candidate", "مرشح", "مرشحين",
    "study group medical", "قروب مذاكرة",
    # Psychiatry & OSCE
    "psychiatry", "نفسية", "mental health",
    "OSCE", "أوسكي", "اوسكي", "osce prep",
    # SCFHS Prometric
    "SCFHS Prometric", "scfhs prometric", "برومترك الهيئة",
])

# ─────────────────────────────────────────────────────────────────────────────
# SEED keywords — full pool for progressive combination search.
# = ALL Saudi cities/identifiers  +  ALL medical seeds
# This ensures every combination phase (ثنائي/ثلاثي/رباعي/خماسي) can produce
# queries that mix Saudi locations with medical specialties/exams/training.
# ─────────────────────────────────────────────────────────────────────────────

SEED_KEYWORDS: list[str] = SAUDI_KEYWORDS + MEDICAL_SEED_KEYWORDS

# ─────────────────────────────────────────────────────────────────────────────
# Hashtag queries (Method 7)
# ─────────────────────────────────────────────────────────────────────────────

HASHTAG_QUERIES: list[str] = [
    "#SMLE", "#SMLE2025", "#SMLE_Recall", "#SMLE_تجميعات",
    "#SNLE", "#SDLE", "#DHA_Exam", "#DHA_Recall",
    "#DOH_Exam", "#HAAD_Exam", "#QCHP_Exam",
    "#OMSB_Exam", "#NHRA_Exam", "#KMLE_Exam",
    "#Prometric_Nursing", "#Prometric_KSA",
    "#DataFlow", "#DataFlow_Status",
    "#SCFHS", "#Mumaris_Plus", "#نقاط_الهيئة",
    "#طب_سعودي", "#أطباء_السعودية", "#USMLE",
    "#MRCP", "#PLAB", "#زمالة_طبية",
    "#ابتعاث_طبي", "#وظائف_طبية",
    "#Saudi_Board", "#Arab_Board",
]

# ─────────────────────────────────────────────────────────────────────────────
# GCC-specific username suffixes (Method 5 — expanded)
# ─────────────────────────────────────────────────────────────────────────────

_USERNAME_SUFFIXES = [
    "2", "_sa", "_ksa", "_ar", "_arabic", "_med", "_medical",
    "_doc", "_dr", "_health", "_official", "_channel", "_group",
    "_bot", "_links", "_saudi", "_arab", "_gulf", "_uae",
    "_kw", "_ksa2", "_qatar", "_oman", "_bahrain",
    "_gcc", "_scfhs", "_dha", "_omsb",
    "_exam", "_prep", "_study", "_recall",
    "_nursing", "_pharmacy", "_dental",
    "2025", "_2025", "_new",
]

# ─────────────────────────────────────────────────────────────────────────────
# Scam / fraud keyword detection
# ─────────────────────────────────────────────────────────────────────────────

_SCAM_KEYWORDS: list[str] = [
    "guaranteed pass", "ضمان النجاح", "نجاح مضمون",
    "شهادات بلا حضور", "شهادات مزورة", "تزوير شهادة",
    "تسريب اختبار بمقابل", "أسئلة مسربة مدفوعة",
    "شراء درجة", "بيع نتيجة", "رشوة اختبار",
    "crypto payment", "bitcoin medical",
    "whatsapp payment", "send money exam",
    "fake certificate", "شهادة وهمية",
    "upgrade results", "تعديل نتيجة",
    "license without exam", "رخصة بدون اختبار",
    "leaked bank", "بنك مسرب بمقابل",
]

# Link extraction regex
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
    await asyncio.sleep(max(0, seconds))


def _has_scam_signals(text: str) -> bool:
    """Return True if text contains fraud/scam red-flag keywords."""
    tl = text.lower()
    return any(kw.lower() in tl for kw in _SCAM_KEYWORDS)


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

    queries = random.sample(SEARCH_QUERIES, min(len(SEARCH_QUERIES), 100))

    for i, query in enumerate(queries):
        # Telegram rejects queries shorter than 3 chars
        if len(query.strip()) < 3:
            continue
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

        if i % 25 == 24:
            await status_cb(
                f"🔍 بحث بالكلمات المفتاحية: {i + 1}/{len(queries)} — اكتُشف {len(found)} رابط جديد"
            )
            await _safe_sleep(random.uniform(2.0, 4.0))
        else:
            await _safe_sleep(random.uniform(0.7, 1.8))

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 2 — Similar Channels (Telegram recommendation engine)
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
# Method 4 — Message Link Crawling
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
            async for msg in client.iter_messages(link, limit=msgs_per_chat):
                text = (msg.text or "") + (
                    getattr(getattr(msg, "media", None), "caption", "") or ""
                )
                for raw in _LINK_RE.findall(text):
                    lnk = _normalise_link(raw)
                    if _is_new_link(lnk, known):
                        found.append(lnk)
                        known.add(lnk.lower())
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(1.0, 2.5))

    if found:
        await status_cb(f"💬 روابط من الرسائل: اكتُشف {len(found)} رابط")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 5 — Username Pattern Generation (GCC-specific suffixes)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_base_username(link: str) -> str | None:
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
    for base in base_usernames[:50]:
        root = base
        for suf in _USERNAME_SUFFIXES:
            if root.lower().endswith(suf.lower()):
                root = root[: -len(suf)]
                break
        for suf in _USERNAME_SUFFIXES:
            candidate = root + suf
            if candidate != base:
                candidates.append(candidate)

    random.shuffle(candidates)
    for candidate in candidates[:120]:
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
# Method 6 — Compound Query Matrix  ★ NEW
# Cross-products GCC regulatory bodies × medical specialties × tiers
# Generates targeted compound queries the simple keyword list can't cover.
# ─────────────────────────────────────────────────────────────────────────────

def _build_compound_queries() -> list[str]:
    """
    Build a list of compound search strings by combining:
      - GCC regulatory bodies with specialties  (e.g. "SCFHS Cardiology")
      - Regulatory bodies with professional tiers (e.g. "SCFHS Consultant")
      - Specialties with country names (e.g. "Cardiology Saudi")
    """
    gcc_countries = [
        "Saudi", "Saudi Arabia", "KSA", "السعودية",
        "UAE", "Emirates", "الإمارات", "Dubai", "دبي", "Abu Dhabi", "أبوظبي",
        "Kuwait", "الكويت",
        "Qatar", "قطر",
        "Oman", "عمان",
        "Bahrain", "البحرين",
        "GCC", "خليجي",
    ]
    compounds: list[str] = []
    # Regulator × Specialty (sample to avoid thousands of queries)
    for reg in GCC_REGULATORS[:15]:          # top 15 regulators
        for spec in _SPECIALTIES_SHORT[:20]: # top 20 specialties
            compounds.append(f"{reg} {spec}")
    # Regulator × Tier
    for reg in ["SCFHS", "DHA", "OMSB", "QCHP", "NHRA", "KMLE"]:
        for tier in _TIERS:
            compounds.append(f"{reg} {tier}")
    # Specialty × Country
    for spec in _SPECIALTIES_SHORT[:15]:
        for country in gcc_countries[:8]:
            compounds.append(f"{spec} {country}")
    # Deduplicate and shuffle
    unique = list(dict.fromkeys(compounds))
    random.shuffle(unique)
    return unique


async def search_by_compound_matrix(
    client: TelegramClient,
    known: set,
    status_cb: Callable,
    limit_per_query: int = 15,
    max_queries: int = 120,
) -> list[str]:
    """Method 6: Compound regulatory × specialty matrix search."""
    found: list[str] = []
    compounds = _build_compound_queries()[:max_queries]

    await status_cb(
        f"🧮 **الطريقة 6:** مصفوفة الاستعلامات المركبة\n"
        f"({len(compounds)} تركيبة من الجهات الرقابية × التخصصات)"
    )

    for i, query in enumerate(compounds):
        if len(query.strip()) < 3:
            continue
        try:
            result = await client(SearchRequest(q=query, limit=limit_per_query))
            for chat in result.chats:
                link = _entity_to_link(chat)
                if link and _is_new_link(link, known):
                    found.append(link)
                    known.add(link.lower())
        except FloodWaitError as e:
            await status_cb(f"⏳ مصفوفة: انتظار {e.seconds}s...")
            await _safe_sleep(e.seconds)
        except Exception:
            pass

        if i % 30 == 29:
            await status_cb(
                f"🧮 مصفوفة: {i + 1}/{len(compounds)} — اكتُشف {len(found)} رابط"
            )
            await _safe_sleep(random.uniform(2.0, 4.0))
        else:
            await _safe_sleep(random.uniform(0.8, 2.0))

    await status_cb(f"✅ الطريقة 6 اكتملت: {len(found)} رابط جديد")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 7 — Hashtag Discovery  ★ NEW
# ─────────────────────────────────────────────────────────────────────────────

async def search_by_hashtags(
    client: TelegramClient,
    known: set,
    status_cb: Callable,
    limit_per_tag: int = 20,
) -> list[str]:
    """Method 7: Search Telegram using medical GCC hashtag queries."""
    found: list[str] = []

    await status_cb(f"#️⃣ **الطريقة 7:** بحث بالهاشتاقات ({len(HASHTAG_QUERIES)} وسم)")

    for i, tag in enumerate(HASHTAG_QUERIES):
        try:
            result = await client(SearchRequest(q=tag, limit=limit_per_tag))
            for chat in result.chats:
                link = _entity_to_link(chat)
                if link and _is_new_link(link, known):
                    found.append(link)
                    known.add(link.lower())
        except FloodWaitError as e:
            await _safe_sleep(e.seconds)
        except Exception:
            pass
        await _safe_sleep(random.uniform(0.8, 2.0))

    await status_cb(f"✅ الطريقة 7 اكتملت: {len(found)} رابط جديد")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Method 8 — Google Custom Search (site:t.me dorks)  ★ NEW
# Requires env vars: GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX
# Silently skipped if not configured.
# ─────────────────────────────────────────────────────────────────────────────

_GOOGLE_DORKS: list[str] = [
    'site:t.me "SCFHS" OR "Mumaris"',
    'site:t.me "SMLE" "recall"',
    'site:t.me "Prometric" "recall" "2025"',
    'site:t.me "DHA" "exam" "medical"',
    'site:t.me "OMSB" "Oman"',
    'site:t.me "QCHP" "Qatar"',
    'site:t.me "NHRA" "Bahrain"',
    'site:t.me "KMLE" "Kuwait"',
    'site:t.me "DataFlow" "verification"',
    'site:t.me "الزمالة السعودية" OR "Saudi Board"',
    'site:t.me "نقاط الهيئة" "ساعات"',
    'site:t.me inurl:joinchat "medical" "DHA"',
    'site:t.me "تجميعات smle" OR "smle recalled"',
    'site:t.me "USMLE" "Arab doctors"',
    'site:t.me "أطباء الخليج" طبي',
]


async def search_by_google_dorks(
    known: set,
    status_cb: Callable,
) -> list[str]:
    """
    Method 8: Google Custom Search JSON API.
    Extracts t.me links from Google results using site:t.me dorks.
    Requires GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX to be set.
    """
    api_key = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    cx      = os.getenv("GOOGLE_CSE_CX", "").strip()

    if not api_key or not cx:
        return []   # silently skip — credentials not configured

    found: list[str] = []
    await status_cb(
        f"🌐 **الطريقة 8:** Google Dorks ({len(_GOOGLE_DORKS)} استعلام)"
    )

    for i, dork in enumerate(_GOOGLE_DORKS):
        try:
            params = urllib.parse.urlencode({
                "key": api_key,
                "cx":  cx,
                "q":   dork,
                "num": 10,
            })
            url = f"https://www.googleapis.com/customsearch/v1?{params}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for item in data.get("items", []):
                link_url = item.get("link", "")
                snippet  = item.get("snippet", "")
                # Extract any t.me URLs from the result URL or snippet
                for raw in _LINK_RE.findall(link_url + " " + snippet):
                    lnk = _normalise_link(raw)
                    if _is_new_link(lnk, known):
                        found.append(lnk)
                        known.add(lnk.lower())

        except Exception:
            pass

        await asyncio.sleep(1.2)   # stay within Google's rate limit

    if found:
        await status_cb(f"✅ الطريقة 8 اكتملت: {len(found)} رابط من Google")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Scoring  ★ NEW
# Analyzes recent messages of a discovered channel to produce a quality score.
# ─────────────────────────────────────────────────────────────────────────────

async def score_engagement(
    client: TelegramClient,
    link: str,
    sample_size: int = 100,
) -> dict:
    """
    Returns a dict with:
      - activity_rate: messages per day (last `sample_size` msgs)
      - unique_user_ratio: unique_authors / total_messages  (0-1)
      - is_broadcast: True if it's a channel (one-way)
      - scam_flagged: True if scam keywords detected
      - member_count: total members/subscribers
    """
    result = {
        "activity_rate": 0.0,
        "unique_user_ratio": 0.0,
        "is_broadcast": False,
        "scam_flagged": False,
        "member_count": 0,
    }
    try:
        entity = await client.get_entity(link)
        result["is_broadcast"] = getattr(entity, "broadcast", False)
        result["member_count"] = (
            getattr(entity, "participants_count", 0) or
            getattr(entity, "members_count", 0) or 0
        )

        messages       = []
        unique_authors = set()
        scam_text      = ""

        async for msg in client.iter_messages(link, limit=sample_size):
            messages.append(msg)
            if msg.sender_id:
                unique_authors.add(msg.sender_id)
            scam_text += (msg.text or "") + " "

        if messages:
            result["scam_flagged"] = _has_scam_signals(scam_text)
            total = len(messages)
            result["unique_user_ratio"] = round(len(unique_authors) / total, 3) if total else 0

            # Activity rate: msgs per day
            if len(messages) >= 2:
                newest = messages[0].date
                oldest = messages[-1].date
                span_days = max((newest - oldest).total_seconds() / 86400, 1)
                result["activity_rate"] = round(total / span_days, 2)

    except Exception:
        pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Scam filter — batch flag discovered links
# ─────────────────────────────────────────────────────────────────────────────

async def filter_scam_links(
    client: TelegramClient,
    links: list[str],
    status_cb: Callable,
    sample_size: int = 30,
) -> tuple[list[str], list[str]]:
    """
    Check a batch of new links for scam signals.
    Returns (clean_links, flagged_links).
    Only checks the first `sample_size` to save time.
    """
    clean: list[str]   = []
    flagged: list[str] = []

    check_links = links[:sample_size]
    skip_links  = links[sample_size:]

    for link in check_links:
        try:
            entity = await client.get_entity(link)
            # Check bio/description for scam keywords
            bio = ""
            if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
                full = await client(GetFullChannelRequest(entity))
                bio  = getattr(full.full_chat, "about", "") or ""
            title = getattr(entity, "title", "") or ""
            if _has_scam_signals(bio + " " + title):
                flagged.append(link)
            else:
                clean.append(link)
        except Exception:
            clean.append(link)   # assume clean if unreachable
        await _safe_sleep(random.uniform(0.3, 0.8))

    clean.extend(skip_links)
    if flagged:
        await status_cb(
            f"🚨 **فلتر الاحتيال:** تم تمييز {len(flagged)} رابط مشبوه\n"
            f"   الروابط المشبوهة لن تُضاف للأرشيف."
        )
    return clean, flagged


# ─────────────────────────────────────────────────────────────────────────────
# Progressive Keyword Search  ★ NEW (Task 4)
#
# Phase 1 — single keywords: searches every keyword in SEARCH_QUERIES one by one.
# Phase 2 — pairs:           every combination of 2 keywords from SEED_KEYWORDS.
# Phase 3 — triples:         every combination of 3 keywords from SEED_KEYWORDS.
# Phase 4 — quads:           every combination of 4 keywords from SEED_KEYWORDS.
# Phase 5 — quintuples:      every combination of 5 keywords from SEED_KEYWORDS.
#
# Each combination is joined with a space and searched as a single query.
# A _search_stopped flag allows any phase to be interrupted at any time.
# ─────────────────────────────────────────────────────────────────────────────

_search_stopped: bool = False


def stop_progressive_search():
    global _search_stopped
    _search_stopped = True


def reset_progressive_search():
    global _search_stopped
    _search_stopped = False


def _combo_generator(keywords: list[str], size: int):
    """Yield joined keyword combinations of the given size."""
    for combo in itertools.combinations(keywords, size):
        yield " ".join(combo)


def _saudi_anchored_combos(
    saudi_terms: list[str],
    med_seeds: list[str],
    total_size: int,
    max_queries: int = 1000,
) -> list[str]:
    """
    Generate queries where a Saudi term is the anchor + (total_size - 1)
    medical seeds form the rest.
      total_size=2 → ثنائي  (Saudi + 1 med)
      total_size=3 → ثلاثي  (Saudi + 2 med)
      total_size=4 → رباعي  (Saudi + 3 med)
      total_size=5 → خماسي  (Saudi + 4 med)
    Capped at max_queries, shuffled for variety.
    """
    combos: list[str] = []
    n_med = total_size - 1
    for sa in saudi_terms:
        for med_combo in itertools.combinations(med_seeds, n_med):
            combos.append(sa + " " + " ".join(med_combo))
            if len(combos) >= max_queries * 5:
                break
        if len(combos) >= max_queries * 5:
            break
    random.shuffle(combos)
    return combos[:max_queries]


async def _run_single_phase(
    client: TelegramClient,
    queries: list[str],
    known: set,
    status_cb: Callable,
    phase_label: str,
    limit_per_query: int = 20,
) -> list[str]:
    """Search a list of queries one-by-one. Returns list of new links found."""
    global _search_stopped
    found: list[str] = []
    total = len(queries)

    for i, query in enumerate(queries):
        if _search_stopped:
            break
        if len(query.strip()) < 3:
            continue
        try:
            result = await client(SearchRequest(q=query, limit=limit_per_query))
            for chat in result.chats:
                link = _entity_to_link(chat)
                if link and _is_new_link(link, known):
                    found.append(link)
                    known.add(link.lower())
        except FloodWaitError as e:
            await status_cb(f"⏳ {phase_label}: انتظار {e.seconds}s بسبب FloodWait...")
            await _safe_sleep(e.seconds)
        except Exception:
            pass

        if i % 30 == 29:
            await status_cb(
                f"🔍 {phase_label}: {i + 1}/{total} — اكتُشف {len(found)} رابط جديد"
            )
            await _safe_sleep(random.uniform(2.0, 4.0))
        else:
            await _safe_sleep(random.uniform(0.7, 1.8))

    return found


async def run_progressive_keyword_search(
    client: TelegramClient,
    known: set,
    status_cb: Callable,
    max_combo_queries: int = 500,
    limit_per_query: int = 20,
) -> list[str]:
    """
    Progressive keyword search — 9 phases total:

    ★ Saudi-anchored phases (every combination anchored to a Saudi city/term):
      SA-2  (ثنائي):  Saudi term + 1 medical seed
      SA-3  (ثلاثي): Saudi term + 2 medical seeds
      SA-4  (رباعي): Saudi term + 3 medical seeds
      SA-5  (خماسي): Saudi term + 4 medical seeds

    General phases (all SEED_KEYWORDS mixed freely):
      Phase 1: Every single keyword in SEARCH_QUERIES
      Phase 2: All pairs from SEED_KEYWORDS (ثنائي عام)
      Phase 3: Triples (ثلاثي عام), capped
      Phase 4: Quads  (رباعي عام), capped
      Phase 5: Quintuples (خماسي عام), capped
    """
    global _search_stopped
    reset_progressive_search()
    all_found: list[str] = []

    saudi   = SAUDI_KEYWORDS
    med     = MEDICAL_SEED_KEYWORDS
    results: dict[str, list[str]] = {}

    # ══════════════════════════════════════════════════════════════════════════
    # SAUDI-ANCHORED PHASES — الكويت / المملكة anchor in every query
    # ══════════════════════════════════════════════════════════════════════════

    # ── SA-2: Saudi × Med (ثنائي سعودي) ─────────────────────────────────────
    sa2 = _saudi_anchored_combos(saudi, med, total_size=2, max_queries=max_combo_queries)
    await status_cb(
        f"🇸🇦 **المرحلة SA-2 — ثنائي سعودي:**\n"
        f"مدينة سعودية + تخصص طبي ({len(sa2)} تركيبة)"
    )
    results["sa2"] = await _run_single_phase(
        client, sa2, known, status_cb, "SA-2 ثنائي", limit_per_query=limit_per_query,
    )
    all_found.extend(results["sa2"])
    await status_cb(f"✅ SA-2 اكتمل: {len(results['sa2'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── SA-3: Saudi × Med × Med (ثلاثي سعودي) ───────────────────────────────
    sa3 = _saudi_anchored_combos(saudi, med, total_size=3, max_queries=max_combo_queries)
    await status_cb(
        f"🇸🇦 **المرحلة SA-3 — ثلاثي سعودي:**\n"
        f"مدينة + تخصص + اختبار ({len(sa3)} تركيبة)"
    )
    results["sa3"] = await _run_single_phase(
        client, sa3, known, status_cb, "SA-3 ثلاثي", limit_per_query=limit_per_query,
    )
    all_found.extend(results["sa3"])
    await status_cb(f"✅ SA-3 اكتمل: {len(results['sa3'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── SA-4: Saudi × Med × Med × Med (رباعي سعودي) ─────────────────────────
    sa4 = _saudi_anchored_combos(saudi, med, total_size=4, max_queries=max_combo_queries)
    await status_cb(
        f"🇸🇦 **المرحلة SA-4 — رباعي سعودي:**\n"
        f"مدينة + 3 مصطلحات طبية ({len(sa4)} تركيبة)"
    )
    results["sa4"] = await _run_single_phase(
        client, sa4, known, status_cb, "SA-4 رباعي", limit_per_query=limit_per_query,
    )
    all_found.extend(results["sa4"])
    await status_cb(f"✅ SA-4 اكتمل: {len(results['sa4'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── SA-5: Saudi × Med × Med × Med × Med (خماسي سعودي) ──────────────────
    sa5 = _saudi_anchored_combos(saudi, med, total_size=5, max_queries=max_combo_queries)
    await status_cb(
        f"🇸🇦 **المرحلة SA-5 — خماسي سعودي:**\n"
        f"مدينة + 4 مصطلحات طبية ({len(sa5)} تركيبة)"
    )
    results["sa5"] = await _run_single_phase(
        client, sa5, known, status_cb, "SA-5 خماسي", limit_per_query=limit_per_query,
    )
    all_found.extend(results["sa5"])
    await status_cb(f"✅ SA-5 اكتمل: {len(results['sa5'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ══════════════════════════════════════════════════════════════════════════
    # GENERAL PHASES — free combinations from the full SEED_KEYWORDS pool
    # ══════════════════════════════════════════════════════════════════════════

    # ── Phase 1: Single keywords (all SEARCH_QUERIES) ────────────────────────
    await status_cb(
        f"🔍 **المرحلة 1 — كلمات مفردة:**\n"
        f"({len(SEARCH_QUERIES)} كلمة بحثية شاملة)"
    )
    results["p1"] = await _run_single_phase(
        client, SEARCH_QUERIES, known, status_cb, "المرحلة 1",
        limit_per_query=limit_per_query,
    )
    all_found.extend(results["p1"])
    await status_cb(f"✅ المرحلة 1 اكتملت: {len(results['p1'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── Phase 2: All pairs (ثنائي عام) ───────────────────────────────────────
    pairs = list(_combo_generator(SEED_KEYWORDS, 2))
    await status_cb(
        f"🔍 **المرحلة 2 — ثنائي عام:**\n"
        f"({len(pairs)} تركيبة من {len(SEED_KEYWORDS)} كلمة محورية)"
    )
    results["p2"] = await _run_single_phase(
        client, pairs, known, status_cb, "المرحلة 2",
        limit_per_query=limit_per_query,
    )
    all_found.extend(results["p2"])
    await status_cb(f"✅ المرحلة 2 اكتملت: {len(results['p2'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── Phase 3: Triples (ثلاثي عام) ─────────────────────────────────────────
    triples = list(itertools.islice(_combo_generator(SEED_KEYWORDS, 3), max_combo_queries))
    random.shuffle(triples)
    await status_cb(
        f"🔍 **المرحلة 3 — ثلاثي عام:**\n"
        f"({len(triples)} تركيبة — محدودة بـ {max_combo_queries})"
    )
    results["p3"] = await _run_single_phase(
        client, triples, known, status_cb, "المرحلة 3",
        limit_per_query=limit_per_query,
    )
    all_found.extend(results["p3"])
    await status_cb(f"✅ المرحلة 3 اكتملت: {len(results['p3'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── Phase 4: Quads (رباعي عام) ────────────────────────────────────────────
    quads = list(itertools.islice(_combo_generator(SEED_KEYWORDS, 4), max_combo_queries))
    random.shuffle(quads)
    await status_cb(
        f"🔍 **المرحلة 4 — رباعي عام:**\n"
        f"({len(quads)} تركيبة — محدودة بـ {max_combo_queries})"
    )
    results["p4"] = await _run_single_phase(
        client, quads, known, status_cb, "المرحلة 4",
        limit_per_query=limit_per_query,
    )
    all_found.extend(results["p4"])
    await status_cb(f"✅ المرحلة 4 اكتملت: {len(results['p4'])} رابط جديد")
    if _search_stopped:
        return all_found

    # ── Phase 5: Quintuples (خماسي عام) ──────────────────────────────────────
    quints = list(itertools.islice(_combo_generator(SEED_KEYWORDS, 5), max_combo_queries))
    random.shuffle(quints)
    await status_cb(
        f"🔍 **المرحلة 5 — خماسي عام:**\n"
        f"({len(quints)} تركيبة — محدودة بـ {max_combo_queries})"
    )
    results["p5"] = await _run_single_phase(
        client, quints, known, status_cb, "المرحلة 5",
        limit_per_query=limit_per_query,
    )
    all_found.extend(results["p5"])

    # ── Final summary ─────────────────────────────────────────────────────────
    await status_cb(
        f"🎉 **البحث التصاعدي اكتمل بالكامل!**\n\n"
        f"🇸🇦 **مراحل سعودية مُثبَّتة:**\n"
        f"  ثنائي: {len(results['sa2'])} | ثلاثي: {len(results['sa3'])}\n"
        f"  رباعي: {len(results['sa4'])} | خماسي: {len(results['sa5'])}\n\n"
        f"🔍 **مراحل عامة:**\n"
        f"  مفردة: {len(results['p1'])} | ثنائي: {len(results['p2'])}\n"
        f"  ثلاثي: {len(results['p3'])} | رباعي: {len(results['p4'])}\n"
        f"  خماسي: {len(results['p5'])}\n\n"
        f"**✅ الإجمالي الجديد: {len(all_found)} رابط**"
    )
    return all_found


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
    Run all 8 discovery methods and append new links to raw_links.json.
    Returns the count of newly discovered links.
    """
    if not accounts:
        await status_callback("❌ لا توجد حسابات مرتبطة.")
        return 0

    existing_raw    = load_raw_links()
    # Seed `known` from ALL layers: raw + already-archived + already-joined
    # so no previously seen link is ever re-added regardless of pipeline stage.
    from database import load_all_known_links
    known: set[str] = load_all_known_links(joined_links=db.get("joined_links", []))

    await status_callback(
        "🧠 **بدأ الاكتشاف الذكي المحسّن!**\n\n"
        "🇸🇦 **SA-2** ثنائي سعودي: مدينة + تخصص\n"
        "🇸🇦 **SA-3** ثلاثي سعودي: مدينة + تخصص + اختبار\n"
        "🇸🇦 **SA-4** رباعي سعودي: مدينة + 3 مصطلحات\n"
        "🇸🇦 **SA-5** خماسي سعودي: مدينة + 4 مصطلحات\n"
        "1️⃣  كلمات مفردة (كل SEARCH_QUERIES)\n"
        "2️⃣  ثنائي عام | 3️⃣ ثلاثي | 4️⃣ رباعي | 5️⃣ خماسي\n"
        "6️⃣  قنوات مشابهة (Telegram AI)\n"
        "7️⃣  روابط من البيو والرسائل\n"
        "8️⃣  أنماط أسماء المستخدمين\n"
        "9️⃣  مصفوفة مركبة + هاشتاقات + Google Dorks\n"
        "🔒  فلتر احتيال تلقائي"
    )

    session = accounts[0]
    all_found: list[str] = []
    m1 = m2 = m3 = m4 = m5 = m6 = m7 = m8 = []

    archive_links = [
        f"https://t.me/c/{ch_id}" if isinstance(ch_id, int) else ch_id
        for ch_id in archive_channel_ids.values()
    ]
    seed_links = source_links + archive_links

    try:
      async with TelegramClient(session, API_ID, API_HASH) as client:

        # Guard: skip if session is not authorized (prevents EOFError in daemon env)
        try:
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False

        if not authorized:
            await status_callback(
                "❌ جلسة المستخدم غير مصرح بها أو منتهية الصلاحية.\n"
                "🔑 أعد ربط الحساب من قائمة الحسابات."
            )
            return 0

        # ── Method 1: Progressive Keyword Search (★ replaces old single-phase) ─
        await status_callback(
            "🔍 **الطريقة 1:** بحث تصاعدي تلقائي بجميع الكلمات المفتاحية..."
        )
        m1 = await run_progressive_keyword_search(
            client, known, status_callback,
            max_combo_queries=500, limit_per_query=20,
        )
        all_found.extend(m1)
        await status_callback(f"✅ الطريقة 1 اكتملت: {len(m1)} رابط جديد")

        # ── Method 2: Similar Channels ────────────────────────────────────────
        if seed_links:
            await status_callback("🔗 **الطريقة 2:** قنوات مشابهة من Telegram AI...")
            m2 = await get_similar_channels(client, seed_links[:30], known, status_callback)
            all_found.extend(m2)

        # ── Method 3: Bio Crawling ────────────────────────────────────────────
        if seed_links:
            await status_callback("📝 **الطريقة 3:** زحف البيو والوصف...")
            m3 = await crawl_bios(client, seed_links[:40], known, status_callback)
            all_found.extend(m3)

        # ── Method 4: Message Crawling ────────────────────────────────────────
        if seed_links:
            await status_callback("💬 **الطريقة 4:** زحف الرسائل...")
            m4 = await crawl_messages(client, seed_links[:20], known, status_callback)
            all_found.extend(m4)

        # ── Method 5: Username Patterns ───────────────────────────────────────
        if seed_links:
            await status_callback("🔤 **الطريقة 5:** أنماط أسماء المستخدمين...")
            m5 = await discover_by_username_patterns(client, seed_links, known, status_callback)
            all_found.extend(m5)

        # ── Method 6: Compound Query Matrix ★ ────────────────────────────────
        m6 = await search_by_compound_matrix(client, known, status_callback)
        all_found.extend(m6)

        # ── Method 7: Hashtag Discovery ★ ────────────────────────────────────
        m7 = await search_by_hashtags(client, known, status_callback)
        all_found.extend(m7)

        # ── Scam Filter ★ ────────────────────────────────────────────────────
        if all_found:
            await status_callback(
                f"🔒 **فلتر الاحتيال:** فحص {min(len(all_found), 30)} رابط..."
            )
            clean, flagged = await filter_scam_links(
                client, all_found, status_callback, sample_size=30
            )
            all_found = clean
            if flagged:
                await status_callback(
                    f"🚫 تم استبعاد {len(flagged)} رابط مشبوه:\n" +
                    "\n".join(f"  • {l}" for l in flagged[:5]) +
                    ("\n  ..." if len(flagged) > 5 else "")
                )

    except EOFError:
        await status_callback(
            "❌ خطأ EOF — جلسة المستخدم منتهية أو غير مصادقة.\n"
            "🔑 أعد ربط الحساب من قائمة الحسابات."
        )
        return 0
    except Exception as _disc_err:
        await status_callback(f"❌ خطأ في الاكتشاف: {_disc_err}")
        return 0

    # ── Method 8: Google Dorks (no client needed) ★ ──────────────────────────
    m8 = await search_by_google_dorks(known, status_callback)
    all_found.extend(m8)

    # ── Save ──────────────────────────────────────────────────────────────────
    if all_found:
        combined = existing_raw + all_found
        save_raw_links(combined)

    total_new = len(all_found)
    await status_callback(
        f"🎉 **اكتمل الاكتشاف الذكي!**\n\n"
        f"📊 الملخص:\n"
        f"  🔍 كلمات مفتاحية:  {len(m1)}\n"
        f"  🔗 قنوات مشابهة:  {len(m2) if seed_links else 0}\n"
        f"  📝 بيو:            {len(m3) if seed_links else 0}\n"
        f"  💬 رسائل:         {len(m4) if seed_links else 0}\n"
        f"  🔤 أنماط:         {len(m5) if seed_links else 0}\n"
        f"  🧮 مصفوفة مركبة: {len(m6)}\n"
        f"  #️⃣ هاشتاقات:      {len(m7)}\n"
        f"  🌐 Google Dorks:  {len(m8)}\n\n"
        f"✅ **الإجمالي الجديد: {total_new} رابط**\n"
        f"📦 المجموع الكلي: {len(existing_raw) + total_new:,} رابط"
    )

    db.setdefault("stats", {})
    db["stats"]["total_found"] = db["stats"].get("total_found", 0) + total_new

    return total_new
