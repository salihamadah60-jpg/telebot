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

# ─────────────────────────────────────────────────────────────────────────────
# THE 5 ARCHIVE CHANNELS  (type-based, not specialty-based)
# ─────────────────────────────────────────────────────────────────────────────
CHANNEL_KEYS = {
    "channels":      "📢 أرشيف - القنوات",
    "groups":        "👥 أرشيف - المجموعات",
    "broken":        "💀 أرشيف - الروابط المنتهية",
    "invite":        "🔐 أرشيف - روابط الدعوة",
    "addlist":       "📂 أرشيف - المجلدات (Addlist)",
}

# ─────────────────────────────────────────────────────────────────────────────
# SPECIALTY CLASSIFICATION  (used inside the report message, not as channels)
# All terms from the uploaded comprehensive list
# ─────────────────────────────────────────────────────────────────────────────
SPECIALTIES: dict[str, list[str]] = {

    # ── Licensing & Exam Preparation ──────────────────────────────────────────
    "اختبارات_الترخيص_والزمالات": [
        "smle", "sle", "sdle", "sple", "snle", "sles", "scfhs",
        "mumaris", "ممارس بلس", "mumaris plus",
        "dha", "haad", "doh", "moh", "omsb", "qchp", "nhra", "kmle",
        "usmle", "plab", "ukmla", "mccqe", "amc",
        "mrcp", "mrcs", "mrcgp", "mrcpch", "mrcog",
        "oet", "ielts", "neet",
        "prometric", "برومترك", "dataflow", "داتا فلو",
        "رخصة طبية", "medical license", "تصنيف مهني", "professional classification",
        "هيئة السعودية", "هيئة التخصصات",
        "تجميعات", "recalls", "تسريبات", "leaks",
        "بنك أسئلة", "question bank", "qbank", "uworld",
        "mcq", "osce", "أوسكي", "viva", "فيفا",
        "mock exam", "اختبار تجريبي", "practice test",
        "ملخصات smle", "smle prep", "pass smle",
        "زمالة", "fellowship", "board exam",
    ],

    # ── Residency / Scholarship / Abroad ──────────────────────────────────────
    "ابتعاث_وزمالات_وإقامة": [
        "ابتعاث", "منحة", "منح", "scholarship",
        "residency", "إقامة طبية", "resident",
        "training", "fellowship", "زمالة",
        "دراسة في الخارج", "study abroad",
        "internship", "امتياز",
        "قبول", "admission", "جامعة",
    ],

    # ── Internal Medicine & Sub-specialties ───────────────────────────────────
    "باطنة_وتخصصاتها": [
        "باطنة", "باطنية", "internal medicine", "general medicine",
        "طب باطني", "internist",
        # Cardiology
        "قلب", "cardiology", "cardio", "heart", "cardiac",
        "electrophysiology", "كهرباء القلب",
        "interventional cardiology", "قلب تداخلي",
        "cardiac catheterization", "قسطرة قلبية", "cath lab",
        "heart valves", "صمامات قلبية",
        "cabg", "coronary", "chf", "ami", "cad", "afib",
        # GI & Hepatology
        "جهاز هضمي", "gastroenterology", "gastro", "gi",
        "كبد", "hepatology", "liver",
        "endoscopy", "مناظير", "colonoscopy", "ercp",
        "gerd", "هضمي",
        # Endocrine & Diabetes
        "غدد صماء", "endocrinology", "endo",
        "سكري", "diabetes", "dm",
        "هرمونات", "hormones",
        "غدة درقية", "thyroid",
        "سمنة", "obesity", "bariatric",
        # Nephrology
        "كلى", "nephrology", "nephro",
        "غسيل كلى", "dialysis", "hemodialysis",
        "زراعة كلى", "renal transplant", "kidney transplant",
        "esrd", "rft",
        # Pulmonology
        "صدرية", "pulmonology", "chest medicine",
        "تنفسية", "respiratory",
        "رئة", "lung",
        "pft", "copd", "انسداد رئوي", "ربو", "asthma",
        # Infectious Diseases
        "معدية", "infectious diseases",
        "فيروسات", "virology",
        "بكتيريا", "bacteriology",
        "مناعة", "immunology",
        "مضادات حيوية", "antibiotics",
        # Rheumatology
        "روماتيزم", "rheumatology", "rheum",
        "مفاصل", "joints", "arthritis",
        "مناعة ذاتية", "autoimmune",
        "osteoarthritis", "خشونة",
        # Oncology & Hematology
        "أورام", "oncology", "onco",
        "سرطان", "cancer",
        "كيماوي", "chemotherapy", "chemo",
        "دم", "hematology", "hema",
        "أمراض الدم", "blood disorders",
        "تخثر", "coagulation", "cbc",
        # Neurology
        "مخ وأعصاب", "neurology", "neuro",
        "جلطة دماغية", "stroke", "cva",
        "صرع", "epilepsy", "seizure",
        "emg", "eeg",
        # Geriatrics
        "طب المسنين", "geriatrics", "aging", "elderly",
        # Others
        "طب النوم", "sleep medicine",
        "طب الألم", "pain medicine",
        "hospital medicine", "طب مستشفيات",
    ],

    # ── Surgery & Sub-specialties ──────────────────────────────────────────────
    "جراحة_وتخصصاتها": [
        "جراحة", "surgery", "surgical", "surgeon",
        "جراحة عامة", "general surgery",
        "مشرط", "عمليات", "غرفة عمليات", "operating room",
        "laparoscopic", "منظار جراحي", "endoscopic surgery",
        "مرارة", "gallbladder",
        "زائدة", "appendectomy",
        "فتق", "hernia",
        "قولون", "colorectal",
        # Orthopedics
        "عظام", "orthopedic", "ortho",
        "كسر", "fracture", "trauma",
        "عمود فقري", "spine", "ديسك", "disc",
        "مفاصل", "arthroplasty", "arthroscopy",
        "sports medicine", "إصابات ملاعب",
        # Neurosurgery
        "جراحة مخ", "neurosurgery",
        "دماغ", "brain surgery",
        "نخاع شوكي", "spinal cord",
        "أورام دماغية", "brain tumor",
        # Cardiac Surgery
        "جراحة قلب", "cardiac surgery", "heart surgery",
        "جراحة صدر", "thoracic surgery",
        "valve replacement", "صمامات",
        # Urology
        "مسالك بولية", "urology",
        "بروستاتا", "prostate", "bph",
        "حصوات", "kidney stones", "lithotripsy",
        "ureteroscopy",
        # Plastic Surgery
        "تجميل", "plastic surgery",
        "ترميم", "reconstructive",
        "حروق", "burns",
        "microsurgery", "جراحة دقيقة",
        # Pediatric Surgery
        "جراحة أطفال", "pediatric surgery",
        # Vascular Surgery
        "جراحة أوعية", "vascular surgery",
        "شرايين", "arteries",
        "دوالي", "varicose",
        "angiography", "قسطرة شرايين",
        # Maxillofacial
        "وجه وفكين", "maxillofacial", "omfs", "maxfax",
        # Endocrine surgery
        "جراحة غدد", "endocrine surgery",
        "parathyroid", "adrenal",
        # Hand surgery
        "جراحة يد", "hand surgery",
        # Oncologic surgery
        "جراحة أورام", "surgical oncology",
    ],

    # ── Pediatrics ────────────────────────────────────────────────────────────
    "طب_أطفال": [
        "أطفال", "pediatrics", "peds", "pedia",
        "طب أطفال عام", "general pediatrics",
        "حديثي الولادة", "neonatology",
        "خدج", "premature", "preterm", "nicu",
        "picu", "pediatric icu",
        "قلب أطفال", "pediatric cardiology",
        "كلى أطفال", "pediatric nephrology",
        "أعصاب أطفال", "pediatric neurology",
        "رضاعة", "infant", "kids", "child",
        "pediatric critical care",
    ],

    # ── Obstetrics & Gynecology ───────────────────────────────────────────────
    "نساء_وولادة": [
        "نساء وولادة", "obstetrics", "gynecology",
        "obgyn", "gyne", "توليد",
        "قابلة", "midwifery",
        "عقم", "infertility",
        "أطفال أنابيب", "ivf",
        "طب الأجنة", "fetal medicine",
        "أورام نسائية", "gynecologic oncology",
        "urogynecology",
    ],

    # ── Dentistry ─────────────────────────────────────────────────────────────
    "طب_أسنان": [
        "أسنان", "dentistry", "dental", "dentist",
        "تقويم أسنان", "orthodontics", "ortho",
        "علاج جذور", "endodontics",
        "جراحة لثة", "periodontics", "perio", "لثة",
        "تعويضات", "prosthodontics",
        "طب أسنان أطفال", "pediatric dentistry", "pedo",
        "أشعة أسنان", "oral radiology",
        "طب فم", "oral medicine",
        "تجميل أسنان", "esthetic dentistry", "cosmetic dentistry",
        "حشوات", "fillings",
        "سحب عصب", "root canal", "rct",
        "تيجان", "crowns", "جسور", "bridges",
        "طقم أسنان", "dentures",
        "زراعة أسنان", "dental implants", "implant",
        "تبييض أسنان", "teeth whitening",
        "فينير", "veneers",
        "hollywood smile",
        "فني معمل أسنان", "dental lab",
    ],

    # ── Pharmacy ──────────────────────────────────────────────────────────────
    "صيدلة": [
        "صيدلة", "pharmacy", "pharmacist",
        "صيدلة إكلينيكية", "clinical pharmacy",
        "علم الأدوية", "pharmacology",
        "صيدلة مجتمع", "community pharmacy", "retail pharmacy",
        "صيدلة مستشفيات", "hospital pharmacy",
        "رقابة دوائية", "drug control",
        "صيدلة صناعية", "industrial pharmacy",
        "كيمياء صيدلية", "pharmaceutical chemistry",
        "جودة أدوية", "qc", "quality assurance", "qa",
        "دواء", "أدوية", "drugs", "medication",
        "علاج", "عقاقير",
        "صيدلة أورام", "oncology pharmacy",
        "صيدلة أطفال", "pediatric pharmacy",
        "صيدلة نووية", "nuclear pharmacy",
    ],

    # ── Medical Laboratory ────────────────────────────────────────────────────
    "مختبرات_طبية": [
        "مختبرات", "medical laboratory", "lab", "مختبر",
        "تحاليل", "lab tests",
        "كيمياء حيوية", "clinical biochemistry",
        "أحياء دقيقة", "microbiology", "micro",
        "علم أمراض الدم", "hematology",
        "علم الأنسجة", "histology", "histopathology",
        "علم الخلايا", "cytology",
        "علم المناعة", "serology",
        "وراثة طبية", "medical genetics", "genomics", "dna",
        "بنك دم", "blood bank",
        "فصائل دم", "blood groups",
        "cbc", "lft", "rft", "fbs",
        "زراعة", "culture",
    ],

    # ── Anesthesia & ICU ──────────────────────────────────────────────────────
    "تخدير_وعناية_مركزة": [
        "تخدير", "anesthesia", "anesthesiologist",
        "تخدير عام", "general anesthesia",
        "تخدير نصفي", "spinal anesthesia",
        "عناية مركزة", "icu", "intensive care", "critical care",
        "إنعاش", "resuscitation", "cpr",
        "طب طوارئ", "emergency medicine", "er", "ed",
        "مسعف", "paramedic", "emt",
        "acls",
    ],

    # ── Radiology & Medical Imaging ───────────────────────────────────────────
    "أشعة_وتصوير_طبي": [
        "أشعة", "radiology",
        "أشعة تشخيصية", "diagnostic radiology",
        "أشعة مقطعية", "ct scan", "computed tomography",
        "رنين مغناطيسي", "mri",
        "أشعة صوتية", "ultrasound", "sonar", "sonography",
        "طب نووي", "nuclear medicine",
        "أشعة علاجية", "radiation therapy", "radiotherapy",
        "أشعة تداخلية", "interventional radiology",
        "فيزياء طبية", "medical physics",
        "biomedical", "هندسة طبية",
        "أشعة سينية", "x-ray",
        "ct technologist", "mri technologist", "sonographer",
    ],

    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "طب_عيون": [
        "عيون", "ophthalmology", "eye",
        "جراحة عيون", "eye surgery",
        "بصريات", "optometry",
        "نظارات", "eyeglasses",
        "عدسات لاصقة", "contact lenses",
    ],

    # ── ENT ───────────────────────────────────────────────────────────────────
    "أنف_وأذن_وحنجرة": [
        "أنف وأذن وحنجرة", "ent", "otolaryngology",
        "ear nose throat",
        "سمعيات", "audiology", "hearing",
    ],

    # ── Dermatology ───────────────────────────────────────────────────────────
    "جلدية": [
        "جلدية", "dermatology", "skin",
        "أمراض جلدية", "dermatologist",
    ],

    # ── Psychiatry & Mental Health ────────────────────────────────────────────
    "نفسية_وصحة_نفسية": [
        "نفسية", "psychiatry", "mental health",
        "صحة نفسية", "psychiatrist",
        "نفسي", "psychology",
    ],

    # ── Nursing ───────────────────────────────────────────────────────────────
    "تمريض": [
        "تمريض", "nursing", "nurse",
        "ممرض", "ممرضة", "rn", "bsn", "msn",
        "nurse practitioner", "lpn",
        "إسعاف", "طوارئ",
        "تمريض قلب", "cardiac nursing",
        "تمريض أورام", "oncology nursing",
        "تمريض عناية", "critical care nursing",
        "تمريض طوارئ", "emergency nursing",
        "ممرض تخدير", "nurse anesthetist",
    ],

    # ── Rehabilitation & Allied Health ───────────────────────────────────────
    "علاج_طبيعي_وتأهيل": [
        "علاج طبيعي", "physical therapy", "physiotherapy", "pt",
        "تأهيل طبي", "rehabilitation", "rehab",
        "علاج وظيفي", "occupational therapy", "ot",
        "علاج تنفسي", "respiratory therapy", "rt",
        "علاج كلام", "speech therapy",
        "نطق وتخاطب", "speech language pathology",
        "sports physical therapy", "رياضي",
    ],

    # ── Nutrition & Dietetics ─────────────────────────────────────────────────
    "تغذية": [
        "تغذية علاجية", "clinical nutrition", "dietitian",
        "تغذية", "nutrition", "nutritionist",
    ],

    # ── Family & Community Medicine ───────────────────────────────────────────
    "طب_أسرة_ومجتمع": [
        "طب أسرة", "family medicine", "fm",
        "طب مجتمع", "community medicine",
        "رعاية أولية", "primary care",
        "رعاية صحية", "primary healthcare",
        "ممارس عام", "general practitioner", "gp",
    ],

    # ── Preventive & Public Health ────────────────────────────────────────────
    "طب_وقائي_وصحة_عامة": [
        "طب وقائي", "preventive medicine",
        "صحة عامة", "public health",
        "وبائيات", "epidemiology",
        "إحصاء حيوي", "biostatistics",
        "تطعيمات", "vaccinations", "immunization",
        "مكافحة العدوى", "infection control",
        "طب مهني", "occupational medicine",
        "طب بيئة", "environmental medicine",
        "طب السفر", "travel medicine",
        "طب الطيران", "aviation medicine",
        "طب الغوص", "diving medicine",
    ],

    # ── Forensic & Toxicology ────────────────────────────────────────────────
    "طب_شرعي_وسموم": [
        "طب شرعي", "forensic medicine", "forensics",
        "جنائي", "criminal",
        "مشرحة", "autopsy",
        "سموم", "toxicology",
        "جرعة زائدة", "overdose",
        "تسمم", "poisoning",
    ],

    # ── Healthcare Admin & Coding ─────────────────────────────────────────────
    "إدارة_صحية_وترميز": [
        "إدارة مستشفيات", "hospital management",
        "جودة صحية", "healthcare quality",
        "cbahi", "jci", "سباهي",
        "معلوماتية صحية", "health informatics",
        "سجل طبي", "medical record",
        "تأمين طبي", "health insurance",
        "ترميز طبي", "medical coding",
        "icd-10", "cpt",
        "مطالبات", "claims", "موافقات", "approvals",
        "توظيف", "recruitment", "وظائف صحية", "healthcare jobs",
        "locum", "تشغيل ذاتي",
    ],

    # ── Books & Study Resources ───────────────────────────────────────────────
    "كتب_ومراجع_وملخصات": [
        "كتب", "كتاب", "مكتبة", "library",
        "مراجع", "references",
        "ملخصات", "summaries", "notes", "نوتس",
        "pdf", "محاضرات", "lectures",
        "سلايد", "slides", "powerpoint",
        "بوربوينت", "ملفات", "files",
        "درايف", "drive", "google drive",
        "archive", "أرشيف",
        "قروب مذاكرة", "study group",
        "كيسات", "clinical cases", "حالات سريرية",
        "مذاكرة",
    ],

    # ── Discussions & Q&A ────────────────────────────────────────────────────
    "استفسارات_ونقاشات": [
        "استفسار", "سؤال", "جواب",
        "مناقشة", "نقاش", "حوار",
        "دردشة", "مجتمع",
        "chat", "discussion", "q&a", "qa",
        "help", "community", "forum",
    ],

    # ── General Medicine (catch-all) ──────────────────────────────────────────
    "طب_عام": [
        "طب", "طبي", "طبيب", "دكتور",
        "medical", "medicine", "health", "dr",
        "clinic", "عيادة", "مستشفى", "hospital",
        "healthcare", "physician", "md",
        "intern", "امتياز",
        "resident", "مقيم",
        "specialist", "أخصائي",
        "consultant", "استشاري",
        "medical student", "طالب طب",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Anti-ban settings
# ─────────────────────────────────────────────────────────────────────────────
SWITCH_ACCOUNT_EVERY = 100
DELAY_MIN = 3.0
DELAY_MAX = 7.0
BREAK_EVERY = 500
BREAK_DURATION = 300

# Parallel processing: max simultaneous link inspections
MAX_CONCURRENT = 5
