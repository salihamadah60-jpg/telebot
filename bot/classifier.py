import re
from config import SPECIALTIES


def classify_specialty(title: str, bio: str, username: str = "") -> str:
    """
    Score every specialty by keyword hits in combined title+bio+username.
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


def is_medical(title: str, bio: str, username: str = "") -> bool:
    """
    Return True if the entity appears to be medical/health-related.
    Checks all specialty keyword lists (including طب_عام).
    Returns False only when zero keywords match — clearly non-medical.
    """
    combined = f"{title} {bio} {username}".lower()
    for keywords in SPECIALTIES.values():
        for kw in keywords:
            if kw.lower() in combined:
                return True
    return False


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


def extract_links_from_text(text: str, entities=None) -> list:
    """
    Extract ALL Telegram links from a message.

    Handles:
    - https://t.me/username
    - t.me/username          (no protocol)
    - t.me/+HASH             (invite links)
    - t.me/joinchat/HASH
    - t.me/addlist/SLUG
    - t.me/c/channelid/msgid (private message links)
    - t.me/username/msgid    (post links)
    - telegram.me/...
    - tg://resolve?domain=username
    - @username references   (converted to https://t.me/username)
    - Clickable message entities (MessageEntityTextUrl, MessageEntityUrl)
    """
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    # ── 1. Regex over raw text ────────────────────────────────────────────────
    pattern = (
        r"(?:https?://)?"
        r"(?:t\.me|telegram\.me|telegram\.dog)"
        r"/[\+a-zA-Z0-9_\-/]+"
    )
    for m in re.finditer(pattern, text, re.IGNORECASE):
        link = m.group(0).strip().rstrip("/.,;:!?\"')")
        if not link.startswith("http"):
            link = "https://" + link
        if link not in seen:
            seen.add(link)
            found.append(link)

    # ── 2. tg:// deep links ───────────────────────────────────────────────────
    for m in re.finditer(r"tg://resolve\?domain=([\w]+)", text, re.IGNORECASE):
        link = f"https://t.me/{m.group(1)}"
        if link not in seen:
            seen.add(link)
            found.append(link)

    # ── 3. @username mentions → convert to t.me links ────────────────────────
    for m in re.finditer(r"(?<!\w)@([a-zA-Z][a-zA-Z0-9_]{3,31})", text):
        username = m.group(1)
        link = f"https://t.me/{username}"
        if link not in seen:
            seen.add(link)
            found.append(link)

    # ── 4. Message entities (clickable links that may differ from visible text) ─
    if entities:
        try:
            from telethon.tl.types import (
                MessageEntityTextUrl,
                MessageEntityUrl,
            )
            for ent in entities:
                url = None
                if isinstance(ent, MessageEntityTextUrl):
                    url = ent.url
                elif isinstance(ent, MessageEntityUrl):
                    url = text[ent.offset: ent.offset + ent.length]
                if not url:
                    continue
                url = url.strip().rstrip("/.,;:!?\"')")
                url_lower = url.lower()
                if any(d in url_lower for d in ("t.me", "telegram.me", "telegram.dog")):
                    if not url.startswith("http"):
                        url = "https://" + url
                    if url not in seen:
                        seen.add(url)
                        found.append(url)
        except Exception:
            pass

    return found


def is_addlist_link(link: str) -> bool:
    return "/addlist/" in link


def is_invite_link(link: str) -> bool:
    return "/+" in link or "/joinchat/" in link


def is_bot_entity(entity) -> bool:
    """Return True if the entity is a Telegram bot."""
    return getattr(entity, "bot", False) is True
