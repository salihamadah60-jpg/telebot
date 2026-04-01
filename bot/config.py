import os
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID  = int(os.getenv("OWNER_ID", "0"))
BOT_ID    = int(BOT_TOKEN.split(":")[0]) if BOT_TOKEN and ":" in BOT_TOKEN else 0

DATA_FILE      = "bot_memory.json"
SEEN_LINKS_FILE = "global_seen.txt"
RAW_LINKS_FILE  = "raw_links.json"
SESSIONS_DIR    = "sessions"

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# ─────────────────────────────────────────────────────────────────────────────
# THE 6 ARCHIVE CHANNELS  (type-based, not specialty-based)
# ─────────────────────────────────────────────────────────────────────────────
CHANNEL_KEYS: dict[str, str] = {
    "channels": "📢 أرشيف - القنوات",
    "groups":   "👥 أرشيف - المجموعات",
    "broken":   "💀 أرشيف - الروابط المنتهية",
    "invite":   "🔐 أرشيف - روابط الدعوة",
    "addlist":  "📂 أرشيف - المجلدات (Addlist)",
    "bots":     "🤖 أرشيف - البوتات",
    "other":    "🌐 أرشيف - روابط أخرى (غير طبية)",
}

# ─────────────────────────────────────────────────────────────────────────────
# SPECIALTY CLASSIFICATION
# Used INSIDE each report message — not as separate channels.
# Comprehensive list covering every medical branch and sub-specialty.
# ─────────────────────────────────────────────────────────────────────────────
SPECIALTIES: dict[str, list[str]] = {

    # ── Licensing exams & certification ──────────────────────────────────────
    "اختبارات_الترخيص_والزمالات": [
        # Saudi
        "smle", "sle", "sdle", "sple", "snle", "sles",
        "scfhs", "هيئة التخصصات", "الهيئة السعودية",
        "mumaris", "ممارس بلس", "mumaris plus",
        "scfhs prometric", "pastel", "بستل",
        # GCC
        "dha", "haad", "doh", "moh uae", "omsb", "qchp", "nhra", "kmle",
        "دبي", "أبوظبي", "عمان", "قطر", "البحرين", "الكويت",
        # International
        "usmle", "plab", "ukmla", "mccqe", "amc exam",
        "mrcp", "mrcs", "mrcgp", "mrcpch", "mrcog",
        "frcsc", "frcpc", "facs", "facp",
        "mrcem", "emrcog", "eMRCOG",
        "fcps", "FCPS exam",
        "oet", "ielts", "neet pg", "pte", "PTE academic", "PTE exam",
        "goethe", "gothe", "goethe zertifikat", "german language exam",
        # System
        "prometric", "برومترك", "dataflow", "داتا فلو",
        "رخصة طبية", "medical license", "تصنيف مهني",
        "professional classification", "رخصة", "license",
        # Prep
        "تجميعات smle", "smle prep", "smle recall", "pass smle",
        "تجميعات", "recalls", "تسريبات", "leaks",
        "بنك أسئلة", "question bank", "qbank", "uworld",
        "mcq", "osce", "أوسكي", "اوسكي", "OSCE", "viva", "فيفا",
        "mock exam", "اختبار تجريبي", "practice test",
        "board exam", "board review", "board", "بورد",
        "diploma", "دبلوم", "دبلومة",
        "candidate", "مرشح", "مرشحين",
        "study", "مذاكرة", "note", "notes", "ملاحظات",
        # Months & years
        "2026", "2025",
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    ],

    # ── Residency / Fellowship / Scholarship ──────────────────────────────────
    "ابتعاث_وزمالات_وإقامة": [
        "ابتعاث", "منحة دراسية", "منح", "scholarship",
        "residency", "إقامة طبية", "resident physician",
        "fellowship", "زمالة طبية", "زمالة",
        "internship", "طبيب امتياز", "امتياز",
        "training program", "برنامج تدريبي",
        "دراسة في الخارج", "study abroad",
        "قبول برنامج", "program acceptance", "admission",
        "match", "nrmp", "البرنامج السعودي", "بورد سعودي",
        "saudi board", "arab board", "بورد عربي", "البورد العربي",
        "rotation", "روتيشن", "surgery rotation", "جراحة دورة تدريبية",
        "icu fellowship", "زمالة عناية مركزة", "icu زمالة",
        "cme", "cme credit", "cme student", "تعليم طبي مستمر",
        "interns", "طلاب امتياز", "intern medical student",
    ],

    # ── Internal Medicine & all sub-specialties ───────────────────────────────
    "باطنة_وتخصصاتها": [
        "باطنة", "باطنية", "internal medicine", "general medicine",
        "طب باطني", "internist",
        "imd", "imd gl", "قسم الباطنة", "internal medicine department",
        # Cardiology
        "قلب", "cardiology", "cardio", "cardiac",
        "electrophysiology", "ep", "كهرباء القلب",
        "interventional cardiology", "قلب تداخلي",
        "catheterization", "قسطرة قلبية", "cath lab",
        "echocardiography", "echo", "heart valves", "صمامات قلبية",
        "cabg", "coronary artery", "chf", "ami", "cad", "afib",
        "heart failure", "قصور قلب", "hypertension", "ضغط دم",
        "arrhythmia", "اضطراب نظم", "pace maker",
        # GI & Hepatology
        "جهاز هضمي", "gastroenterology", "gastro", "gi",
        "hepatology", "كبد", "liver",
        "endoscopy", "مناظير", "colonoscopy", "ercp",
        "gerd", "ileitis", "crohn", "colitis", "ibd",
        "cirrhosis", "تشمع", "fibrosis", "fatty liver",
        # Endocrine & Diabetes
        "غدد صماء", "endocrinology",
        "سكري", "diabetes", "dm", "هبة", "hypoglycemia",
        "هرمونات", "hormones",
        "غدة درقية", "thyroid", "hypothyroidism", "hyperthyroidism",
        "سمنة", "obesity", "bariatric medicine",
        "pituitary", "غدة نخامية",
        "adrenal", "غدة كظرية", "cushing", "addison",
        # Nephrology
        "كلى", "nephrology", "nephro",
        "غسيل كلى", "dialysis", "hemodialysis", "peritoneal dialysis",
        "زراعة كلى", "kidney transplant", "renal transplant",
        "esrd", "ckd", "رفت", "rft",
        "glomerulonephritis", "nephrotic syndrome",
        # Pulmonology / Respiratory
        "صدرية", "pulmonology", "chest medicine",
        "تنفسية", "respiratory medicine",
        "رئة", "lung", "pulmonary",
        "pft", "copd", "انسداد رئوي", "ربو", "asthma",
        "pneumonia", "التهاب رئة", "fibrosis رئوية",
        "sleep apnea", "انقطاع نفس", "pleural",
        # Infectious Diseases
        "معدية", "infectious diseases",
        "فيروسات", "virology",
        "بكتيريا", "bacteriology",
        "مناعة", "immunology",
        "مضادات حيوية", "antibiotics", "antimicrobial",
        "hiv", "tuberculosis", "tb", "سل",
        "hepatitis", "التهاب كبد",
        # Rheumatology
        "روماتيزم", "rheumatology", "rheum",
        "مفاصل", "joints", "arthritis",
        "مناعة ذاتية", "autoimmune",
        "osteoarthritis", "خشونة مفاصل",
        "lupus", "ذئبة", "sle",
        "fibromyalgia", "fibro",
        "gout", "نقرس",
        # Oncology & Hematology
        "أورام", "oncology", "onco",
        "سرطان", "cancer", "tumor", "malignancy",
        "كيماوي", "chemotherapy", "chemo", "targeted therapy",
        "immunotherapy", "radiation oncology",
        "دم", "hematology", "hema",
        "أمراض الدم", "blood disorders",
        "تخثر", "coagulation", "thrombosis", "cbc",
        "leukemia", "lymphoma", "سرطان دم", "myeloma",
        "bone marrow", "نخاع عظم", "stem cell transplant",
        # Neurology
        "مخ وأعصاب", "neurology", "neuro",
        "جلطة دماغية", "stroke", "cva", "tia",
        "صرع", "epilepsy", "seizure",
        "emg", "eeg", "nerve conduction",
        "parkinson", "باركنسون", "tremor", "رعشة",
        "multiple sclerosis", "ms", "تصلب لويحي",
        "alzheimer", "الزهايمر", "dementia", "خرف",
        "migraine", "شقيقة", "headache", "صداع",
        "myasthenia", "neuropathy", "اعتلال أعصاب",
        "guillain barre", "meningitis", "encephalitis",
        # Geriatrics
        "طب المسنين", "geriatrics", "aging", "elderly",
        "كبار السن", "gerontology",
        # Other internal
        "طب النوم", "sleep medicine",
        "طب الألم", "pain medicine", "pain management",
        "hospital medicine", "طب مستشفيات", "hospitalist",
        "طب المراهقين", "adolescent medicine",
    ],

    # ── Surgery & all sub-specialties ─────────────────────────────────────────
    "جراحة_وتخصصاتها": [
        "جراحة", "surgery", "surgical", "surgeon",
        "جراحة عامة", "general surgery",
        "عمليات", "غرفة عمليات", "operating room", "or",
        "laparoscopic", "منظار جراحي", "keyhole surgery",
        "robotic surgery", "روبوتية",
        "مرارة", "gallbladder", "cholecystectomy",
        "زائدة", "appendectomy", "hernia", "فتق",
        "colorectal", "قولون ومستقيم",
        # Orthopedics
        "عظام", "orthopedic", "ortho",
        "كسر", "fracture", "trauma",
        "عمود فقري", "spine", "ديسك", "disc herniation",
        "arthroplasty", "joint replacement", "تبديل مفصل",
        "arthroscopy", "sports medicine", "إصابات ملاعب",
        "scoliosis", "انحناء عمود",
        # Neurosurgery
        "جراحة مخ", "neurosurgery",
        "brain surgery", "دماغ",
        "spinal cord", "نخاع شوكي",
        "brain tumor", "أورام دماغية",
        "aneurysm", "arteriovenous malformation", "avm",
        # Cardiac Surgery
        "جراحة قلب", "cardiac surgery", "heart surgery",
        "جراحة صدر", "thoracic surgery",
        "valve replacement", "صمامات قلبية",
        "bypass", "cabg", "coronary bypass",
        # Urology
        "مسالك بولية", "urology",
        "بروستاتا", "prostate", "bph",
        "حصوات كلى", "kidney stones", "lithotripsy",
        "ureteroscopy", "cystoscopy",
        "prostatectomy", "nephrectomy",
        # Plastic Surgery
        "تجميل", "plastic surgery",
        "ترميم", "reconstructive surgery",
        "حروق", "burns",
        "microsurgery", "جراحة دقيقة",
        "liposuction", "شفط دهون",
        "rhinoplasty", "عملية أنف",
        "facelift", "blepharoplasty",
        # Pediatric Surgery
        "جراحة أطفال", "pediatric surgery",
        # Vascular Surgery
        "جراحة أوعية", "vascular surgery",
        "شرايين", "arteries", "veins",
        "دوالي", "varicose veins",
        "angiography", "قسطرة شرايين",
        "aortic", "carotid", "bypass vascular",
        # Maxillofacial / Cranio
        "وجه وفكين", "maxillofacial", "omfs", "maxfax",
        "craniofacial", "كرانيو", "قحف",
        "cleft palate", "شق حنك", "cleft lip",
        "orthognathic", "jaw surgery",
        # Endocrine Surgery
        "جراحة غدد", "endocrine surgery",
        "thyroid surgery", "parathyroid", "adrenal surgery",
        # Hand Surgery
        "جراحة يد", "hand surgery",
        # Transplant Surgery
        "زراعة أعضاء", "transplant surgery",
        "liver transplant", "kidney transplant", "heart transplant",
        # Surgical Oncology
        "جراحة أورام", "surgical oncology",
    ],

    # ── Pediatrics & ALL sub-specialties ──────────────────────────────────────
    "طب_أطفال": [
        "أطفال", "pediatrics", "peds", "pedia", "pediatric",
        "طب أطفال عام", "general pediatrics",
        "paediatrics", "paeds",
        "pem", "PEM", "pediatric emergency medicine", "طوارئ أطفال",
        # Neonatology
        "حديثي الولادة", "neonatology", "neonatal",
        "خدج", "premature", "preterm", "مبتسر",
        "nicu", "neonatal icu", "حضانة",
        # Intensive Care
        "picu", "pediatric icu", "عناية مركزة أطفال",
        "pediatric critical care",
        # Cardiology Pediatric
        "قلب أطفال", "pediatric cardiology",
        "congenital heart", "قلب خلقي",
        # Nephrology Pediatric
        "كلى أطفال", "pediatric nephrology",
        # Neurology Pediatric
        "أعصاب أطفال", "pediatric neurology",
        "neurodevelopment", "تطور عصبي",
        "autism", "توحد", "adhd", "فرط حركة",
        # Pulmonology Pediatric
        "صدرية أطفال", "pediatric pulmonology",
        "cystic fibrosis", "تليف كيسي",
        # GI Pediatric
        "جهاز هضمي أطفال", "pediatric gastroenterology",
        # Endocrinology Pediatric
        "غدد أطفال", "pediatric endocrinology",
        "growth hormone", "هرمون نمو", "dwarfism", "قصر قامة",
        # Oncology Pediatric
        "أورام أطفال", "pediatric oncology",
        "childhood cancer", "leukemia pediatric",
        # Hematology Pediatric
        "دم أطفال", "pediatric hematology",
        "sickle cell", "أنيميا منجلية", "thalassemia", "ثلاسيميا",
        # Rheumatology Pediatric
        "روماتيزم أطفال", "pediatric rheumatology", "jia",
        # Infectious Pediatric
        "معدية أطفال", "pediatric infectious", "vaccination child",
        # Surgery Pediatric
        "جراحة أطفال", "pediatric surgery",
        # Developmental / Behavioral
        "developmental pediatrics", "behavioral pediatrics",
        "تطور الطفل", "child development",
        "speech delay", "تأخر كلام",
        # Allergy / Immunology Pediatric
        "حساسية أطفال", "pediatric allergy",
        # General
        "رضيع", "infant", "kids", "child", "children",
        "طفولة", "childhood",
    ],

    # ── Obstetrics & Gynecology ───────────────────────────────────────────────
    "نساء_وولادة": [
        # Primary labels — all common forms and abbreviations
        "نساء وولادة", "نساء وتوليد", "نساء", "توليد",
        "obstetrics", "gynecology", "gynaecology",
        "obstetrics and gynecology", "obs and gyne",
        "obs & gyne", "ob/gyn", "obgyn", "ob gyn", "gynObs", "gyn obs",
        "gyne", "gyn", "obs",
        "طب النساء", "طب التوليد", "طب النساء والتوليد",
        "قسم النساء", "نسائية", "توليد ونساء",
        "قابلة", "midwifery", "midwife",
        # Fertility
        "عقم", "infertility", "fertility", "خصوبة",
        "أطفال أنابيب", "ivf", "in vitro fertilization", "ivf center",
        "icsi", "iui", "طفل أنبوب", "تلقيح اصطناعي",
        "reproductive endocrinology", "pcos", "متلازمة المبيض المتعدد الكيسات",
        # Maternal Fetal Medicine
        "طب الأجنة", "fetal medicine", "maternal fetal", "mfm",
        "high risk pregnancy", "حمل خطر", "حمل عالي الخطورة",
        "preeclampsia", "تسمم حمل",
        "amniocentesis", "بزل السلى",
        "prenatal", "postnatal", "antenatal", "رعاية ما قبل الولادة",
        # Gynecologic Oncology
        "أورام نسائية", "gynecologic oncology",
        "cervical cancer", "سرطان عنق الرحم",
        "ovarian cancer", "سرطان المبيض",
        "uterine cancer", "سرطان رحم",
        # Urogynecology
        "urogynecology", "مسالك بولية نسائية",
        "pelvic floor", "قاع الحوض",
        "incontinence", "prolapse", "هبوط رحم",
        # Minimally Invasive Gyne
        "hysteroscopy", "منظار رحم",
        "laparoscopy gyne", "laparoscopy", "منظار نساء", "تنظير نسائي",
        "myomectomy", "fibroid", "أورام ليفية", "ورم ليفي",
        "endometriosis", "بطانة رحم مهاجرة", "endometrium",
        # General anatomy terms
        "رحم", "مبيض", "ovary", "uterus", "cervix", "عنق الرحم",
        "حمل", "pregnancy", "ولادة", "delivery",
        "caesarean", "قيصرية", "c-section",
        "نفاس", "postpartum", "بعد الولادة",
        # Exams related
        "mrcog", "eMRCOG", "emrcog", "drcog",
        "arab board gyne", "بورد نساء",
    ],

    # ── Dentistry & Oral Health — all subspecialties ──────────────────────────
    "طب_أسنان_وفم": [
        "أسنان", "dentistry", "dental", "dentist",
        "طبيب أسنان", "طب أسنان عام", "general dentistry",
        # Orthodontics
        "تقويم أسنان", "orthodontics", "orthodontist",
        "braces", "braket", "aligners", "invisalign",
        "تقويم شفاف", "معدن أسنان",
        # Endodontics
        "علاج جذور", "endodontics", "root canal", "rct",
        "سحب عصب", "عصب", "nerve treatment",
        # Periodontics
        "جراحة لثة", "periodontics", "perio",
        "لثة", "gum disease", "gum surgery",
        "periodontist", "bone graft dental",
        # Prosthodontics
        "تعويضات أسنان", "prosthodontics",
        "تيجان", "crowns", "جسور", "bridges",
        "طقم أسنان", "dentures", "removable denture",
        # Implants
        "زراعة أسنان", "dental implants", "implant",
        "all on four", "all on 4", "all on six",
        # Cosmetic Dentistry
        "تجميل أسنان", "cosmetic dentistry", "esthetic dentistry",
        "تبييض أسنان", "teeth whitening", "bleaching",
        "فينير", "veneers", "lumineers",
        "hollywood smile", "هوليوود سمايل",
        "composite", "حشوات تجميلية",
        # Pediatric Dentistry
        "طب أسنان أطفال", "pediatric dentistry", "pedo", "pedodontist",
        # Oral Maxillofacial Surgery
        "جراحة فم وفكين", "oral maxillofacial", "omfs",
        "فك", "jaw", "wisdom tooth", "ضرس العقل",
        # Oral Radiology
        "أشعة أسنان", "oral radiology", "dental x-ray", "cbct dental",
        "panoramic", "بانوراما أسنان",
        # Oral Medicine
        "طب فم", "oral medicine", "oral pathology",
        # Cranio / Facial
        "craniofacial", "كرانيوفيشيال",
        "cleft lip", "cleft palate", "شق حنك", "شق شفة",
        "orthognathic surgery", "jaw correction",
        # Dental Lab / Tech
        "فني معمل أسنان", "dental lab", "dental technician",
        "dental technology", "تقنية أسنان",
        # General
        "حشوات", "fillings", "cleaning", "tarter", "tartar",
        "deep cleaning", "تنظيف أسنان",
        "tooth extraction", "خلع ضرس",
        "dental", "oral health", "صحة فم",
    ],

    # ── ENT – Ear Nose Throat – Otolaryngology ────────────────────────────────
    "أنف_وأذن_وحنجرة_ورأس_وعنق": [
        "أنف وأذن وحنجرة", "ent", "otolaryngology", "otorhinolaryngology",
        "ear nose throat",
        # Rhinology
        "أنف", "rhinology", "sinuses", "جيوب أنفية",
        "rhinitis", "التهاب أنف", "septum", "حاجز أنف",
        "septoplasty", "fess", "functional endoscopic sinus",
        "nasal polyps", "زوائد أنفية",
        # Otology
        "أذن", "otology", "ear", "hearing loss", "فقدان سمع",
        "otitis media", "التهاب أذن وسطى",
        "cholesteatoma", "cochlear implant", "زرع قوقعة",
        "tympanoplasty", "mastoidectomy",
        # Laryngology / Voice
        "حنجرة", "laryngology", "voice", "صوت",
        "dysphonia", "بحة صوت", "vocal cord",
        "laryngoscopy", "microlaryngoscopy",
        # Head & Neck Surgery
        "جراحة رأس وعنق", "head and neck surgery",
        "thyroidectomy", "استئصال درقية",
        "parotid", "salivary gland",
        "neck dissection",
        # Pediatric ENT
        "ent أطفال", "pediatric ent",
        "tonsils", "لوزتين", "tonsillectomy",
        "adenoids", "لحمية",
        # Allergy / Immunology ENT
        "حساسية أنف", "allergic rhinitis",
        "allergy ent",
        # Skull Base
        "قاعدة جمجمة", "skull base surgery",
    ],

    # ── Ophthalmology – Eye ───────────────────────────────────────────────────
    "طب_عيون": [
        "عيون", "ophthalmology", "eye medicine", "eye",
        "طبيب عيون", "ophthalmologist",
        "جراحة عيون", "eye surgery",
        # Retina
        "شبكية", "retina", "retinal detachment",
        "macular degeneration", "diabetic retinopathy",
        "vitreoretinal",
        # Cornea
        "قرنية", "cornea", "keratoconus",
        "lasik", "ليزك", "prk", "laser eye",
        "corneal transplant",
        # Glaucoma
        "جلوكوما", "glaucoma", "ضغط عين",
        # Cataract
        "كتاركت", "cataract", "ماء أبيض", "عتامة عدسة",
        "phacoemulsification",
        # Pediatric Ophthalmology
        "عيون أطفال", "pediatric ophthalmology",
        "strabismus", "حول", "amblyopia", "كسل عين",
        # Oculoplastics
        "جراحة تجميل عيون", "oculoplastics",
        "ptosis", "خفوت جفن",
        # General
        "بصر", "vision", "نظارات", "eyeglasses",
        "عدسات لاصقة", "contact lenses",
        "optometry", "بصريات", "optometrist",
    ],

    # ── Dermatology – Skin ────────────────────────────────────────────────────
    "جلدية": [
        # Primary labels — all common forms and abbreviations
        "جلدية", "dermatology", "skin", "dermatologist",
        "أمراض جلدية", "طب جلدية", "جلد وتناسلية",
        "derm", "derma", "أخصائي جلدية",
        # Sub-specialties
        "cosmetic dermatology", "تجميل جلد", "جلدية تجميلية",
        "dermatopathology", "علم أمراض جلدية",
        "pediatric dermatology", "جلدية أطفال",
        "wound care", "عناية جروح", "جروح مزمنة",
        "surgical dermatology", "جلدية جراحية",
        "immunodermatology", "جلدية مناعية",
        "mohs surgery", "موهز",
        "trichology", "شعر وفروة رأس",
        "phlebology", "أوردة",
        # Inflammatory Conditions
        "eczema", "أكزيما", "atopic dermatitis", "التهاب جلد تأتبي",
        "psoriasis", "صدفية", "psoriatic arthritis",
        "contact dermatitis", "التهاب جلد تماسي",
        "seborrheic dermatitis", "قشرة الرأس",
        "rosacea", "وردية الجلد", "احمرار وجه",
        "perioral dermatitis",
        # Acne & Hair
        "acne", "حب شباب", "pimples", "acne vulgaris", "cystic acne",
        "hair loss", "تساقط شعر", "alopecia", "areata alopecia",
        "androgenetic alopecia", "صلع وراثي", "hairfall",
        "hirsutism", "شعر زائد", "hypertrichosis",
        "folliculitis", "التهاب بصيلات",
        # Pigmentation
        "vitiligo", "بهاق",
        "melasma", "كلف", "hyperpigmentation", "تصبغات",
        "freckles", "نمش", "dark spots", "بقع داكنة",
        "laser pigmentation",
        # Urticaria & Allergy
        "urticaria", "شرى", "hives",
        "angioedema", "وذمة وعائية",
        "allergy skin", "حساسية جلد", "skin allergy test",
        "patch test",
        # Infections
        "fungal skin", "فطريات جلد", "tinea", "ringworm", "قوباء حلقية",
        "warts", "ثآليل", "molluscum", "contagiosum",
        "herpes zoster", "حزام ناري", "herpes simplex",
        "scabies", "جرب", "lice", "قمل",
        "impetigo", "القوباء",
        # Malignancies
        "melanoma", "ميلانوما", "سرطان جلد",
        "basal cell carcinoma", "bcc",
        "squamous cell carcinoma", "scc",
        "skin cancer", "سرطان الجلد",
        "mole", "شامة", "dermoscopy", "ديرماسكوبي",
        # Procedures & Lasers
        "botox", "بوتكس", "filler", "fillers", "حقن فيلر",
        "laser skin", "ليزر جلد", "laser resurfacing",
        "chemical peel", "تقشير", "تقشير كيميائي",
        "micro needling", "إبر دقيقة",
        "prp skin", "بلازما جلد",
        "phototherapy", "علاج ضوئي", "uv therapy", "nbuvb",
        "cryotherapy skin", "تجميد جلد",
        "electrocautery", "كيّ كهربائي",
        "biopsy skin", "خزعة جلد",
        "curettage", "كحت جلد",
        # Cosmetic Skin
        "skin care", "عناية بشرة", "routine care",
        "sunscreen", "واقي شمس", "spf",
        "retinol", "ريتينول", "vitamin c serum",
        "hyaluronic acid", "حمض هيالوروني",
        "anti aging", "مكافحة شيخوخة",
        # Exams
        "sdle", "sple", "dermatology board", "بورد جلدية",
        "fellowship dermatology",
    ],

    # ── Psychiatry & Mental Health ────────────────────────────────────────────
    "طب_النفسي_والصحة_النفسية": [
        # Primary labels — all common forms and abbreviations
        "نفسية", "psychiatry", "psychiatrist", "psych",
        "صحة نفسية", "mental health", "mental illness",
        "psychology", "سيكولوجي", "علم نفس", "نفسيات",
        "طب نفسي", "أخصائي نفسي", "معالج نفسي",
        # Sub-specialties
        "طب نفسي أطفال", "child psychiatry", "adolescent psychiatry",
        "geriatric psychiatry", "نفسية مسنين",
        "forensic psychiatry", "طب نفسي شرعي",
        "addiction psychiatry", "إدمان", "addiction medicine",
        "consultation liaison psychiatry", "نفسية استشارية",
        "neuropsychiatry", "عصبية نفسية",
        "community psychiatry", "نفسية مجتمع",
        "emergency psychiatry", "طوارئ نفسية",
        "sleep psychiatry", "نوم وصحة نفسية",
        "psychosomatic", "نفسجسدية",
        # Mood Disorders
        "depression", "اكتئاب", "major depressive", "اكتئاب حاد",
        "bipolar", "ثنائي القطب", "مانيا", "mania", "hypomania",
        "seasonal affective disorder", "sad",
        "dysthymia", "اكتئاب مزمن",
        # Anxiety Disorders
        "anxiety", "قلق", "anxiety disorder", "اضطراب قلق",
        "panic disorder", "نوبة هلع", "panic attack",
        "social anxiety", "رهاب اجتماعي", "phobia", "رهاب",
        "generalized anxiety", "قلق عام",
        "separation anxiety",
        # OCD & Related
        "ocd", "وسواس", "obsessive compulsive",
        "body dysmorphic", "اضطراب شكل الجسم",
        "trichotillomania", "excoriation disorder",
        # Trauma & Stress
        "ptsd", "صدمة نفسية", "trauma",
        "acute stress reaction", "adjustment disorder",
        "complex ptsd",
        # Psychotic Disorders
        "schizophrenia", "فصام", "psychosis", "ذهان",
        "schizoaffective", "brief psychotic disorder",
        "delusional disorder", "هذيان",
        "hallucination", "هلوسة",
        # Neurodevelopmental
        "autism", "توحد", "autism spectrum", "asd",
        "adhd", "فرط حركة", "attention deficit",
        "learning disability", "صعوبات تعلم",
        "intellectual disability", "إعاقة ذهنية",
        "developmental disorder", "اضطراب نمائي",
        # Eating & Personality
        "eating disorders", "اضطراب أكل",
        "anorexia", "فقدان شهية", "bulimia", "شره مرضي",
        "personality disorder", "اضطراب شخصية",
        "borderline personality", "bpd", "شخصية حدية",
        "narcissistic", "نرجسية",
        "antisocial personality",
        # Addiction
        "substance abuse", "تعاطي مخدرات", "addiction", "إدمان",
        "alcohol use disorder", "إدمان كحول",
        "drug rehabilitation", "تأهيل مدمنين",
        "opioid", "قصور أوبيويد",
        "naltrexone", "methadone",
        # Therapies
        "psychotherapy", "علاج نفسي",
        "cbt", "علاج سلوكي معرفي", "cognitive behavioral therapy",
        "dbt", "dialectical behavior therapy",
        "emdr", "eye movement desensitization",
        "act therapy", "acceptance commitment",
        "psychoanalysis", "تحليل نفسي",
        "group therapy", "علاج جماعي",
        "family therapy", "علاج أسري",
        "play therapy", "علاج باللعب",
        "counseling", "إرشاد نفسي",
        "mindfulness", "اليقظة الذهنية",
        # Medications
        "antidepressants", "مضادات اكتئاب",
        "antipsychotics", "مضادات ذهان",
        "mood stabilizers", "مثبتات مزاج",
        "ssri", "snri", "benzodiazepines",
        "lithium", "valproate", "clozapine",
        "electroconvulsive therapy", "ect", "صدمات كهربائية",
        # Exams
        "sple", "snle", "psychiatry board", "بورد نفسية",
        "arab board psychiatry", "fellowship psychiatry",
        "mrcp psych", "mrcpsych",
    ],

    # ── Neurosurgery / Brain ──────────────────────────────────────────────────
    "جراحة_مخ_وأعصاب": [
        "جراحة مخ", "neurosurgery", "neurosurgeon",
        "brain surgery", "دماغ",
        "spinal surgery", "جراحة عمود فقري",
        "spine neurosurgery",
        "brain tumor", "أورام دماغية",
        "glioma", "meningioma", "glioblastoma",
        "aneurysm", "أوعية دماغية",
        "avm", "arteriovenous malformation",
        "deep brain stimulation", "dbs",
        "hydrocephalus", "استسقاء دماغي",
        "pediatric neurosurgery",
        "stereotactic radiosurgery", "gamma knife",
        "craniotomy", "فتح جمجمة",
        "spinal cord injury", "إصابة نخاع",
    ],

    # ── Endocrinology (detailed) ──────────────────────────────────────────────
    "غدد_صماء_وسكري": [
        "غدد صماء", "endocrinology", "endocrinologist",
        "سكري", "diabetes mellitus", "dm", "insulin",
        "هرمونات", "hormones",
        "غدة درقية", "thyroid",
        "hypothyroidism", "hyperthyroidism", "hashimoto", "graves disease",
        "parathyroid", "غدة جار درقية", "calcium",
        "adrenal", "غدة كظرية",
        "cushing syndrome", "addison disease",
        "pheochromocytoma",
        "pituitary", "نخامية", "acromegaly",
        "prolactinoma",
        "metabolic bone", "osteoporosis", "هشاشة عظام",
        "pcos", "متلازمة مبيض متعدد الكيسات",
        "obesity endocrine", "سمنة",
        "thyroid cancer", "سرطان درقية",
        "thyroid nodule", "عقدة درقية",
        "insulin resistance", "مقاومة أنسولين",
        "hba1c", "سكر تراكمي",
    ],

    # ── Radiology & Imaging ───────────────────────────────────────────────────
    "أشعة_وتصوير_طبي": [
        "أشعة", "radiology", "radiology specialist",
        "أشعة تشخيصية", "diagnostic radiology",
        "أشعة مقطعية", "ct scan", "computed tomography",
        "رنين مغناطيسي", "mri", "magnetic resonance",
        "أشعة صوتية", "ultrasound", "sonar", "sonography",
        "طب نووي", "nuclear medicine", "pet scan", "pet ct",
        "أشعة علاجية", "radiation therapy", "radiotherapy",
        "radiation oncology", "linac",
        "أشعة تداخلية", "interventional radiology",
        "embolization", "ablation",
        "mammography", "ماموغرافي", "breast imaging",
        "bone densitometry", "dexa",
        "neuroradiology", "أشعة مخ",
        "cardiac imaging", "cardiac mri",
        "pediatric radiology", "أشعة أطفال",
        "musculoskeletal radiology", "msk",
        "فيزياء طبية", "medical physics",
        "biomedical engineering", "هندسة طبية",
        "فني أشعة", "radiology technician",
        "ct technologist", "mri technologist",
        "sonographer", "أخصائي أشعة",
        "أشعة سينية", "x-ray",
        "fluoroscopy",
    ],

    # ── Anesthesia & Critical Care ────────────────────────────────────────────
    "تخدير_وعناية_مركزة": [
        "تخدير", "anesthesia", "anesthesiology", "anesthesiologist",
        "تخدير عام", "general anesthesia",
        "تخدير نصفي", "spinal anesthesia", "epidural",
        "regional anesthesia", "تخدير موضعي",
        "pain management", "إدارة ألم",
        "critical care anesthesia",
        "عناية مركزة", "icu", "intensive care", "critical care",
        "إنعاش", "resuscitation", "cpr", "acls",
        "ventilator", "تنفس اصطناعي",
        "طب طوارئ", "emergency medicine", "er", "ed", "ER", "EMERGENCY",
        "emergency room", "trauma bay",
        "مسعف", "paramedic", "emt",
        "prehospital", "إسعاف ميداني",
        "pem", "PEM", "pediatric emergency medicine", "طوارئ أطفال",
        "icu fellowship", "زمالة عناية مركزة",
        "home care", "رعاية منزلية", "home care nursing",
        "pediatric anesthesia", "تخدير أطفال",
        "cardiac anesthesia", "تخدير قلب",
        "neuroanesthesia", "تخدير مخ",
        "obstetric anesthesia", "تخدير توليد",
    ],

    # ── Pharmacy ──────────────────────────────────────────────────────────────
    "صيدلة": [
        "صيدلة", "pharmacy", "pharmacist", "pharmacology",
        "صيدلة إكلينيكية", "clinical pharmacy",
        "صيدلة مجتمع", "community pharmacy", "retail pharmacy",
        "صيدلة مستشفيات", "hospital pharmacy",
        "صيدلة صناعية", "industrial pharmacy",
        "كيمياء صيدلية", "pharmaceutical chemistry",
        "رقابة دوائية", "drug control", "drug regulation",
        "جودة أدوية", "quality assurance", "qa", "qc",
        "pharmacovigilance", "تيقظ دوائي",
        "drug interactions", "تفاعلات دوائية",
        "antibiotic stewardship",
        "oncology pharmacy", "صيدلة أورام",
        "pediatric pharmacy", "صيدلة أطفال",
        "nuclear pharmacy", "صيدلة نووية",
        "pharm d", "pharmd",
        "dawa", "دواء", "أدوية", "drugs", "medication",
        "prescriptions", "وصفة طبية",
    ],

    # ── Medical Laboratory ────────────────────────────────────────────────────
    "مختبرات_طبية": [
        "مختبرات", "medical laboratory", "lab",
        "تحاليل طبية", "lab tests", "clinical laboratory",
        "كيمياء حيوية", "clinical biochemistry", "biochemistry",
        "أحياء دقيقة", "microbiology", "micro",
        "parasitology", "طفيليات",
        "علم أمراض الدم", "hematology lab",
        "علم الأنسجة", "histology", "histopathology",
        "علم الخلايا", "cytology", "cytopathology",
        "علم المناعة", "immunology lab", "serology",
        "وراثة طبية", "medical genetics", "genomics", "dna",
        "molecular biology", "بيولوجيا جزيئية",
        "pcr", "بي سي آر",
        "بنك دم", "blood bank", "transfusion medicine",
        "فصائل دم", "blood groups",
        "point of care", "poc testing",
        "cbc", "lft", "rft", "fbs",
        "culture sensitivity", "زرع وتحسس",
        "فني مختبر", "lab technician",
        "أخصائي مختبر", "lab specialist",
    ],

    # ── Nursing ───────────────────────────────────────────────────────────────
    "تمريض": [
        "تمريض", "nursing", "nurse", "ممرض", "ممرضة",
        "rn", "bsn", "msn", "nurse practitioner",
        "licensed practical nurse", "lpn",
        "تمريض قلب", "cardiac nursing",
        "تمريض أورام", "oncology nursing",
        "تمريض عناية مركزة", "critical care nursing", "icu nursing",
        "تمريض طوارئ", "emergency nursing",
        "تمريض أطفال", "pediatric nursing",
        "تمريض نساء وولادة", "obstetric nursing",
        "تمريض مجتمع", "community nursing",
        "تمريض جراحي", "surgical nursing",
        "nurse anesthetist", "crna",
        "infection control nurse",
        "nursing education", "تعليم تمريض",
        "home care nursing", "تمريض منزلي", "home care nurse",
        "رعاية منزلية", "nursing home care",
        "snle", "اختبار تمريض",
    ],

    # ── Physical Therapy & Rehabilitation ────────────────────────────────────
    "علاج_طبيعي_وتأهيل": [
        "علاج طبيعي", "physical therapy", "physiotherapy", "pt",
        "تأهيل طبي", "rehabilitation", "rehab",
        "علاج وظيفي", "occupational therapy", "ot",
        "علاج تنفسي", "respiratory therapy", "rt",
        "علاج كلام", "speech therapy",
        "نطق وتخاطب", "speech language pathology", "slp",
        "neurological physical therapy", "علاج طبيعي عصبي",
        "pediatric physical therapy", "علاج طبيعي أطفال",
        "sports physical therapy", "علاج طبيعي رياضي",
        "cardiac rehabilitation", "تأهيل قلبي",
        "pulmonary rehabilitation", "تأهيل رئوي",
        "prosthetics", "أطراف اصطناعية",
        "orthotics", "تقويم أطراف",
        "سمعيات", "audiology", "hearing",
        "audiologist", "أخصائي سمعيات",
        "cochlear implant rehab",
    ],

    # ── Nutrition & Dietetics ─────────────────────────────────────────────────
    "تغذية_علاجية": [
        "تغذية علاجية", "clinical nutrition", "dietitian",
        "تغذية", "nutrition", "nutritionist",
        "diabetes nutrition", "تغذية سكري",
        "pediatric nutrition", "تغذية أطفال",
        "bariatric nutrition", "تغذية بعد عمليات",
        "رضاعة طبيعية", "breastfeeding", "lactation",
        "enteral nutrition", "parenteral nutrition",
        "tpn", "تغذية وريدية",
        "obesity nutrition",
        "sports nutrition", "تغذية رياضية",
    ],

    # ── Family Medicine & Primary Care ────────────────────────────────────────
    "طب_أسرة_ومجتمع": [
        "طب أسرة", "family medicine", "fm", "FM",
        "family medicine hub", "طب أسرة جدة",
        "طب مجتمع", "community medicine",
        "رعاية أولية", "primary care", "primary healthcare",
        "ممارس عام", "general practitioner", "gp",
        "مركز صحي", "health center",
        "chronic disease management",
        "mrcgp", "family board", "MRCGP",
        # Saudi cities (frequently used for job/group search)
        "jeddah", "جدة", "khobar", "الخبر", "albaha", "الباحة",
        "riyadh", "الرياض", "dammam", "الدمام",
        "mecca", "مكة", "مكة المكرمة", "medina", "المدينة", "المدينة المنورة",
        "taif", "الطائف", "tabuk", "تبوك", "abha", "أبها",
        "hail", "حائل", "najran", "نجران", "jazan", "جازان",
        "buraydah", "بريدة", "hafar", "حفر الباطن",
        "qassim", "القصيم", "jubail", "الجبيل",
        "yanbu", "ينبع", "al qunfudah", "القنفذة",
    ],

    # ── Preventive Medicine & Public Health ───────────────────────────────────
    "طب_وقائي_وصحة_عامة": [
        # Primary labels — all common forms and abbreviations
        "طب وقائي", "preventive medicine",
        "صحة عامة", "public health", "ph", "صحة مجتمع",
        "community health", "global health", "صحة عالمية",
        "medical public health",
        # Epidemiology & Biostatistics
        "وبائيات", "epidemiology", "epidemiologist",
        "إحصاء حيوي", "biostatistics", "biostatistician",
        "spss", "sas statistics", "r software", "stata",
        "systematic review", "meta analysis", "مراجعة منهجية",
        "cross sectional", "cohort study", "case control",
        "randomized control trial", "rct", "clinical trial",
        "evidence based medicine", "ebm", "طب قائم على دليل",
        # Infectious Disease & Immunization
        "تطعيمات", "vaccinations", "immunization", "vaccine", "لقاح",
        "مكافحة العدوى", "infection control", "ipc", "مكافحة عدوى",
        "hospital epidemiology", "outbreak investigation",
        "antimicrobial resistance", "amr", "مقاومة مضادات",
        "covid", "كوفيد", "pandemic", "وباء",
        "quarantine", "حجر صحي", "surveillance", "ترصد وبائي",
        "notifiable disease", "أمراض إبلاغية",
        # Chronic Disease & Screening
        "health screening", "فحص مبكر", "cancer screening",
        "chronic disease management", "ncd", "أمراض مزمنة",
        "risk factor", "عوامل خطر", "primary prevention",
        "secondary prevention", "health promotion", "تعزيز الصحة",
        # Occupational & Environmental
        "طب مهني", "occupational medicine", "occupational health",
        "صحة مهنية", "work related disease", "مرض مهني",
        "ergonomics", "هندسة بشرية",
        "طب بيئة", "environmental medicine", "environmental health",
        "air quality", "water quality", "تلوث",
        # Special Branches
        "طب السفر", "travel medicine", "travel vaccination",
        "طب الطيران", "aviation medicine", "aircraft medicine",
        "طب الغوص", "diving medicine", "hyperbaric medicine",
        "طب الكوارث", "disaster medicine", "disaster response",
        "طب الحروب", "military medicine", "طب عسكري",
        "طب رياضي", "sports medicine", "رياضة وصحة",
        # Health Systems
        "نظام صحي", "health system", "health policy", "سياسات صحية",
        "health economics", "اقتصاديات صحة",
        "موارد صحية", "health resources",
        "مؤشرات صحية", "health indicators",
        "who", "منظمة الصحة العالمية",
        "sdh", "social determinants of health", "محددات اجتماعية",
        # Exams
        "sple", "fellowship preventive", "بورد وقائي",
        "mph", "master public health", "دكتوراه صحة عامة",
        "phd public health",
    ],

    # ── Forensic Medicine & Toxicology ───────────────────────────────────────
    "طب_شرعي_وسموم": [
        # Primary labels
        "طب شرعي", "forensic medicine", "forensics", "forensic",
        "جنائي", "criminal medicine", "legal medicine",
        "أخصائي طب شرعي", "forensic pathologist",
        # Pathology & Autopsy
        "مشرحة", "autopsy", "morgue", "post mortem",
        "forensic pathology", "علم أمراض شرعي",
        "cause of death", "سبب الوفاة",
        "death investigation", "تحقيق وفاة",
        "histology forensic", "أنسجة شرعية",
        "forensic radiology", "أشعة شرعية",
        # Crime & Investigation
        "تحقيق جنائي", "criminal investigation",
        "crime scene", "مسرح الجريمة",
        "evidence collection", "جمع أدلة",
        "wound assessment", "تقييم جروح",
        "blunt force trauma", "sharp force injury",
        "strangulation", "خنق", "hanging", "شنق",
        "drowning", "غرق", "burn forensic", "حروق شرعية",
        "sexual assault", "اعتداء جنسي", "rape kit",
        "child abuse", "إساءة أطفال",
        "elder abuse", "إساءة مسنين",
        # Toxicology
        "سموم", "toxicology", "forensic toxicology",
        "سموم إكلينيكية", "clinical toxicology",
        "جرعة زائدة", "overdose", "poisoning", "تسمم",
        "drug testing", "تحليل مخدرات",
        "alcohol testing", "تحليل كحول",
        "blood alcohol level", "مستوى كحول دم",
        "heavy metals", "معادن ثقيلة",
        "organophosphate", "مبيدات",
        "carbon monoxide", "أول أكسيد كربون",
        "cyanide", "سيانيد",
        "antidote", "ترياق",
        "poison control center", "مركز سموم",
        # Legal & Psychiatry
        "طب نفسي شرعي", "forensic psychiatry",
        "criminal responsibility", "مسؤولية جنائية",
        "insanity defense", "الدفع بالجنون",
        "criminal competency", "أهلية جنائية",
        "risk assessment forensic", "تقييم خطورة",
        # DNA & Identification
        "dna forensic", "bحمض نووي شرعي",
        "fingerprint", "بصمة إصبع",
        "facial reconstruction", "إعادة بناء وجه",
        "dental identification", "تعرف أسنان",
        "forensic anthropology", "أنثروبولوجيا شرعية",
        # Exams
        "fellowship forensic", "بورد شرعي",
        "forensic board exam",
    ],

    # ── Healthcare Admin, Quality & Informatics ───────────────────────────────
    "إدارة_صحية_وجودة": [
        "إدارة مستشفيات", "hospital management", "hospital administration",
        "جودة صحية", "healthcare quality",
        "cbahi", "jci", "سباهي",
        "accreditation", "اعتماد",
        "patient safety", "سلامة المرضى",
        "معلوماتية صحية", "health informatics",
        "سجل طبي", "medical record", "his",
        "emr", "electronic medical record",
        "تأمين طبي", "health insurance",
        "ترميز طبي", "medical coding", "icd-10", "cpt",
        "مطالبات", "claims", "موافقات", "approvals",
        "توظيف", "recruitment", "وظائف صحية", "healthcare jobs",
        "locum", "تشغيل ذاتي",
        "رواتب", "salary medical",
        "سكرتارية طبية", "medical secretary",
        "مدير مستشفى", "hospital director",
    ],

    # ── Books, Lectures & Study Resources ────────────────────────────────────
    "كتب_ومراجع_وملخصات": [
        "كتب", "كتاب", "مكتبة", "library",
        "مراجع طبية", "medical references",
        "ملخصات", "summaries", "notes", "نوتس",
        "pdf", "محاضرات", "lectures",
        "سلايد", "slides", "powerpoint", "بوربوينت",
        "ملفات", "files", "درايف", "drive", "google drive",
        "archive", "أرشيف مذاكرة",
        "قروب مذاكرة", "study group",
        "حالات سريرية", "clinical cases", "كيسات",
        "case study",
        "textbook", "كتاب طبي",
        "harrison", "davidson", "robbins",
        "oxford handbook",
    ],

    # ── Discussions & Q&A ────────────────────────────────────────────────────
    "استفسارات_ونقاشات": [
        "استفسار", "سؤال وجواب", "أسئلة",
        "مناقشة", "نقاش",
        "دردشة", "مجتمع طبي",
        "chat", "discussion", "q&a",
        "community", "forum", "منتدى",
        "medical q&a",
    ],

    # ── General Medicine / Catch-all ──────────────────────────────────────────
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
        "student", "طالب", "طلاب",
        "note", "notes", "ملاحظات", "نوتس",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Anti-ban timing
# ─────────────────────────────────────────────────────────────────────────────
SWITCH_ACCOUNT_EVERY = 100
DELAY_MIN            = 3.0
DELAY_MAX            = 7.0
BREAK_EVERY          = 500
BREAK_DURATION       = 300        # seconds

# Parallel processing
MAX_CONCURRENT = 5

# ─────────────────────────────────────────────────────────────────────────────
# Smart Joiner settings
# Telegram allows ~1-8 joins per session then needs a ~2-3 min cooldown.
# We stay conservative to avoid account bans.
# ─────────────────────────────────────────────────────────────────────────────
JOIN_SAFE_BURST      = 5          # max joins per session per burst
JOIN_BURST_COOLDOWN  = 200        # seconds to wait between bursts per session
JOIN_DELAY_MIN       = 8.0        # seconds between individual joins
JOIN_DELAY_MAX       = 15.0       # seconds between individual joins
