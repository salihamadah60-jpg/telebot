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


def _normalize_link(link: str) -> str:
    """Normalize a t.me link for deduplication."""
    link = _clean_telegram_link(link).strip().lower()
    link = re.sub(r"^https?://", "", link)
    link = re.sub(r"^telegram\.me", "t.me", link)
    link = re.sub(r"^telegram\.dog", "t.me", link)
    link = link.rstrip("/.,;:!?\"')")
    return link


def _clean_telegram_link(link: str) -> str:
    text = str(link or "").strip()
    if not text:
        return ""
    text = text.replace("telegram.me/", "t.me/").replace("telegram.dog/", "t.me/")
    resolve = re.search(r"tg://resolve\?domain=([A-Za-z][A-Za-z0-9_]{4,31})", text, re.IGNORECASE)
    if resolve:
        return f"https://t.me/{resolve.group(1)}"
    match = re.search(
        r"(?:t\.me)/([A-Za-z0-9_+\-/]{3,})(?:[?][^\s\])}>'\"]*)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return text
    path = match.group(1).strip("/.,;:!?\"')")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if parts[0] in {"c", "addlist", "joinchat"} and len(parts) >= 2:
        path = "/".join(parts[:2])
    elif parts[0].startswith("+"):
        path = parts[0]
    else:
        path = parts[0]
    return f"https://t.me/{path}"


def extract_links_from_text(text: str, entities=None, reply_markup=None, forward_chat=None) -> list:
    """
    Extract ALL Telegram links from a message — 100% comprehensive.

    Sources covered:
    - https://t.me/username
    - t.me/username           (no protocol)
    - t.me/+HASH              (invite links)
    - t.me/joinchat/HASH
    - t.me/addlist/SLUG
    - t.me/c/channelid/msgid  (private channel message links)
    - t.me/username/msgid     (post links — extracts the channel part)
    - telegram.me/...
    - tg://resolve?domain=username
    - @username references    (filtered to only valid-length public usernames)
    - Clickable message entities (MessageEntityTextUrl, MessageEntityUrl)
    - Inline keyboard button URLs (reply_markup)
    - Forwarded message source channel (forward_chat)
    """
    found: list[str] = []
    seen_normalized: set[str] = set()

    def add(link: str):
        link = _clean_telegram_link(link).strip().rstrip("/.,;:!?\"')")
        if not link:
            return
        if not link.startswith(("http://", "https://", "tg://")):
            link = "https://" + link
        norm = _normalize_link(link)
        # Skip overly short or generic links
        path = norm.replace("t.me/", "").replace("telegram.me/", "")
        if len(path) < 3:
            return
        if norm not in seen_normalized:
            seen_normalized.add(norm)
            found.append(link)

    # ── 1. Regex over raw text ────────────────────────────────────────────────
    if text:
        tme_pattern = (
            r"(?:https?://)?"
            r"(?:t\.me|telegram\.me|telegram\.dog)"
            r"/[\+a-zA-Z0-9_\-/]+"
        )
        for m in re.finditer(tme_pattern, text, re.IGNORECASE):
            raw = m.group(0)
            # For post links like t.me/channel/123, extract just the channel
            post_match = re.match(
                r"((?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/[a-zA-Z0-9_\-]+)/\d+$",
                raw.rstrip("/.,;:!?\"')"),
                re.IGNORECASE,
            )
            if post_match:
                add(post_match.group(1))
            else:
                add(raw)

        # ── 2. tg:// deep links ───────────────────────────────────────────────
        for m in re.finditer(r"tg://resolve\?domain=([\w]{3,32})", text, re.IGNORECASE):
            add(f"https://t.me/{m.group(1)}")

        # ── 3. @username mentions → filter to valid public usernames only ─────
        #   Rules: 5–32 chars, alphanumeric + underscore, not a bot command
        for m in re.finditer(r"(?<!\w)@([a-zA-Z][a-zA-Z0-9_]{4,31})(?!\w)", text):
            uname = m.group(1)
            # Skip all-digit-ending patterns (often message IDs), and _bot suffix
            if uname.lower().endswith("_bot") or uname.lower().endswith("bot"):
                continue
            add(f"https://t.me/{uname}")

    # ── 4. Message entities (hidden URLs behind hyperlink text) ───────────────
    if entities:
        try:
            from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
            for ent in entities:
                url = None
                if isinstance(ent, MessageEntityTextUrl):
                    url = ent.url
                elif isinstance(ent, MessageEntityUrl) and text:
                    url = text[ent.offset: ent.offset + ent.length]
                if not url:
                    continue
                url_l = url.lower()
                if any(d in url_l for d in ("t.me", "telegram.me", "telegram.dog", "tg://")):
                    add(url)
        except Exception:
            pass

    # ── 5. Inline keyboard button URLs ───────────────────────────────────────
    if reply_markup:
        try:
            rows = getattr(reply_markup, "rows", [])
            for row in rows:
                buttons = getattr(row, "buttons", [])
                for btn in buttons:
                    url = getattr(btn, "url", None)
                    if url:
                        url_l = url.lower()
                        if any(d in url_l for d in ("t.me", "telegram.me", "telegram.dog", "tg://")):
                            add(url)
        except Exception:
            pass

    # ── 6. Forwarded source channel ───────────────────────────────────────────
    if forward_chat:
        try:
            uname = getattr(forward_chat, "username", None)
            if uname:
                add(f"https://t.me/{uname}")
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
