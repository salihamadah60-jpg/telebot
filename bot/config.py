import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DATA_FILE = "bot_memory.json"
SEEN_LINKS_FILE = "global_seen.txt"
RAW_LINKS_FILE = "raw_links.json"
SESSIONS_DIR = "sessions"

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

CATEGORIES = {
    "طب_بشري_عام": [
        "طب", "طبي", "طبيب", "دكتور", "medical", "medicine", "health",
        "dr", "clinic", "عيادة", "مستشفى", "hospital", "healthcare"
    ],
    "جراحة": [
        "جراحة", "جراح", "تجميل", "مشرط", "عمليات", "باطنة", "باطنية",
        "surgery", "surgeon", "surgical", "operating", "ortho", "عظام",
        "أعصاب", "neurology", "cardio", "قلب", "vascular"
    ],
    "أسنان": [
        "أسنان", "سن", "تقويم", "لثة", "حشو", "فم", "اسنان",
        "dental", "dentist", "dentistry", "teeth", "tooth",
        "orthodontic", "periodontics", "endodontics"
    ],
    "صيدلة": [
        "صيدلة", "صيدلاني", "دواء", "أدوية", "علاج", "عقاقير",
        "pharma", "pharmacy", "pharmacology", "pharmaceutical",
        "drugs", "medication", "drug"
    ],
    "مختبرات": [
        "مختبر", "تحاليل", "تحليل", "فحص", "مزرعة", "نتائج",
        "ميكروبيولوجي", "جراثيم", "فيروس",
        "lab", "laboratory", "analysis", "microbiology",
        "pathology", "histology", "biochemistry"
    ],
    "أطفال": [
        "أطفال", "طفل", "ولادة", "رضاعة", "نيوناتال", "حديثي الولادة",
        "pediatric", "pedia", "peds", "neonatal", "infant", "kids", "child"
    ],
    "تمريض": [
        "تمريض", "ممرض", "ممرضة", "إسعاف", "طوارئ",
        "nursing", "nurse", "emergency", "midwifery", "paramedic"
    ],
    "كتب_ومراجع": [
        "كتب", "كتاب", "مكتبة", "مراجع", "ملخصات", "ملخص",
        "محاضرات", "سلايد", "بوربوينت", "قناة علمية",
        "library", "pdf", "books", "archive", "summary",
        "lecture", "slides", "powerpoint", "reference"
    ],
    "استفسارات_ونقاشات": [
        "استفسار", "سؤال", "سؤل", "جواب", "مناقشة", "دردشة",
        "نقاش", "حوار", "مجتمع",
        "chat", "discussion", "q&a", "qa", "help", "community", "forum"
    ],
    "ابتعاث_ومنح": [
        "منحة", "منح", "ابتعاث", "قبول", "دراسة في الخارج",
        "scholarship", "fellowship", "residency", "training", "internship"
    ],
}

CHANNEL_NAMES = {
    "طب_بشري_عام":        "📋 أرشيف - طب بشري عام",
    "جراحة":              "🔪 أرشيف - جراحة",
    "أسنان":              "🦷 أرشيف - أسنان",
    "صيدلة":              "💊 أرشيف - صيدلة",
    "مختبرات":            "🔬 أرشيف - مختبرات",
    "أطفال":              "👶 أرشيف - طب أطفال",
    "تمريض":              "🩺 أرشيف - تمريض",
    "كتب_ومراجع":         "📚 أرشيف - كتب ومراجع",
    "استفسارات_ونقاشات":   "💬 أرشيف - استفسارات",
    "ابتعاث_ومنح":        "🎓 أرشيف - ابتعاث ومنح",
    "تالف_وخاص":          "⚠️ أرشيف - روابط تالفة وخاصة",
}

SWITCH_ACCOUNT_EVERY = 100
DELAY_MIN = 3.0
DELAY_MAX = 7.0
BREAK_EVERY = 500
BREAK_DURATION = 300
