import re
from config import CATEGORIES


def classify(title: str, bio: str, username: str) -> str:
    combined = f"{title} {bio} {username}".lower()

    hierarchy = [
        "أسنان",
        "جراحة",
        "صيدلة",
        "مختبرات",
        "أطفال",
        "تمريض",
        "ابتعاث_ومنح",
        "كتب_ومراجع",
        "استفسارات_ونقاشات",
        "طب_بشري_عام",
    ]

    for cat in hierarchy:
        keywords = CATEGORIES.get(cat, [])
        if any(kw in combined for kw in keywords):
            return cat

    return "طب_بشري_عام"


def detect_link_type(entity) -> str:
    if entity is None:
        return "unknown"
    if getattr(entity, "bot", False):
        return "bot"
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    return "group"


def extract_links_from_text(text: str) -> list:
    if not text:
        return []
    pattern = r"(?:https?://)?(?:t\.me|telegram\.me)/[\+a-zA-Z0-9_/]+"
    raw = re.findall(pattern, text)
    cleaned = []
    for link in raw:
        link = link.strip().rstrip("/")
        if not link.startswith("http"):
            link = "https://" + link
        cleaned.append(link)
    return cleaned


def is_addlist_link(link: str) -> bool:
    return "/addlist/" in link or "addlist/" in link


def is_invite_link(link: str) -> bool:
    return "/+" in link or "/joinchat/" in link
