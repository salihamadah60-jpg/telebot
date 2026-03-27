import re
from config import SPECIALTIES


def classify_specialty(title: str, bio: str, username: str = "") -> str:
    """
    Score every specialty by keyword hits in title+bio+username.
    Returns the highest-scoring specialty key, or 'طب_عام' as fallback.
    """
    combined = f"{title} {bio} {username}".lower()
    scores: dict[str, int] = {}

    for specialty, keywords in SPECIALTIES.items():
        hits = sum(1 for kw in keywords if kw.lower() in combined)
        if hits > 0:
            scores[specialty] = hits

    if not scores:
        return "طب_عام"
    return max(scores, key=lambda k: scores[k])


def detect_link_type(entity) -> str:
    """Return the Telegram entity type as a string key."""
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
    """Extract all t.me links from a block of text."""
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
    return "/addlist/" in link


def is_invite_link(link: str) -> bool:
    return "/+" in link or "/joinchat/" in link
