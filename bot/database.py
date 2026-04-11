import json
import os
from config import DATA_FILE, SEEN_LINKS_FILE, ARCHIVED_LINKS_FILE, RAW_LINKS_FILE, SORTED_DIR

SORTED_FILES = {
    "channels": f"{SORTED_DIR}/channels.txt",
    "groups":   f"{SORTED_DIR}/groups.txt",
    "bots":     f"{SORTED_DIR}/bots.txt",
    "invite":   f"{SORTED_DIR}/invite.txt",
    "addlist":  f"{SORTED_DIR}/addlist.txt",
    "other":    f"{SORTED_DIR}/other.txt",
    "broken":   f"{SORTED_DIR}/broken.txt",
}


def load_db() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "accounts": [],
        "sources": [],
        "channels": {},
        "joined_links": [],
        "trusted_users": [],
        "pending_requests": {},
        "progress": {
            "source_index": 0,
            "last_msg_id": 0,
            "last_sorted_index": 0,
        },
        "stats": {
            "total_found": 0,
            "total_sorted": 0,
            "total_broken": 0,
            "total_skipped_duplicate": 0,
        },
    }


def save_db(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_seen_set() -> set:
    """Load all seen links into memory as a set for O(1) lookups."""
    if not os.path.exists(SEEN_LINKS_FILE):
        return set()
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def is_seen(link: str) -> bool:
    clean = normalize_link(link)
    if not os.path.exists(SEEN_LINKS_FILE):
        return False
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        return clean in f.read()


def mark_seen(link: str) -> None:
    clean = normalize_link(link)
    with open(SEEN_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(clean + "\n")


def clear_seen() -> None:
    if os.path.exists(SEEN_LINKS_FILE):
        os.remove(SEEN_LINKS_FILE)


def normalize_link(link: str) -> str:
    link = link.strip().lower()
    link = link.replace("https://", "").replace("http://", "")
    if link.endswith("/"):
        link = link[:-1]
    return link


def load_raw_links() -> list:
    if os.path.exists(RAW_LINKS_FILE):
        with open(RAW_LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_raw_links(links: list) -> None:
    with open(RAW_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)


def save_seen_set(seen: set) -> None:
    """Bulk-write the full in-memory seen set to file (overwrites, atomic via temp)."""
    import tempfile, shutil
    tmp_path = SEEN_LINKS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)) + "\n")
    shutil.move(tmp_path, SEEN_LINKS_FILE)


def get_seen_count() -> int:
    if not os.path.exists(SEEN_LINKS_FILE):
        return 0
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def get_raw_count() -> int:
    raw = load_raw_links()
    return len(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Archived-links tracking (separate from seen — ONLY for links actually posted
# to an archive channel successfully).
# ─────────────────────────────────────────────────────────────────────────────

def mark_archived(link: str) -> None:
    """Record that this link was SUCCESSFULLY posted to an archive channel."""
    clean = normalize_link(link)
    with open(ARCHIVED_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(clean + "\n")


def load_archived_set() -> set:
    """Return a set of normalized links that were successfully archived."""
    if not os.path.exists(ARCHIVED_LINKS_FILE):
        return set()
    with open(ARCHIVED_LINKS_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def get_archived_count() -> int:
    if not os.path.exists(ARCHIVED_LINKS_FILE):
        return 0
    with open(ARCHIVED_LINKS_FILE, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def save_archived_set(archived: set) -> None:
    """Bulk-write the full in-memory archived set to file (atomic)."""
    import tempfile, shutil
    tmp_path = ARCHIVED_LINKS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(archived)) + "\n")
    shutil.move(tmp_path, ARCHIVED_LINKS_FILE)


def clear_archived() -> None:
    if os.path.exists(ARCHIVED_LINKS_FILE):
        os.remove(ARCHIVED_LINKS_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Locally-sorted link files (written during sorting, cleared after publishing)
# ─────────────────────────────────────────────────────────────────────────────

def save_sorted_link(channel_key: str, link: str) -> None:
    """Append a sorted link to the appropriate category file."""
    filepath = SORTED_FILES.get(channel_key)
    if not filepath:
        return
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(link.strip() + "\n")


def load_sorted_links(channel_key: str) -> list:
    """Load all locally-sorted links for a category (deduplicated, ordered)."""
    filepath = SORTED_FILES.get(channel_key)
    if not filepath or not os.path.exists(filepath):
        return []
    seen = set()
    result = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            lnk = line.strip()
            if lnk and lnk not in seen:
                seen.add(lnk)
                result.append(lnk)
    return result


def clear_sorted_links(channel_key: str | None = None) -> None:
    """Remove sorted files — all categories or a specific one."""
    if channel_key:
        filepath = SORTED_FILES.get(channel_key)
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    else:
        for filepath in SORTED_FILES.values():
            if os.path.exists(filepath):
                os.remove(filepath)


def get_sorted_counts() -> dict:
    """Return {channel_key: link_count} for all locally-sorted categories."""
    counts = {}
    for key, filepath in SORTED_FILES.items():
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                counts[key] = sum(1 for line in f if line.strip())
        else:
            counts[key] = 0
    return counts


def load_all_known_links(joined_links: list | None = None) -> set:
    """
    Return a normalized set of EVERY link the bot has ever seen,
    across all storage layers:
      • seen_links.txt  — links already posted to archive channels
      • raw_links.json  — links discovered but not yet archived
      • joined_links    — optional list of links the bot has already joined

    Use this as the universal 'known' set to eliminate duplicates bot-wide,
    from the very first /start command through every subsequent discovery run.
    """
    known: set = set()

    # Layer 1 — archived links (mark_seen was called on each)
    for link in load_seen_set():
        known.add(normalize_link(link))

    # Layer 2 — raw (discovered but not yet sorted)
    for link in load_raw_links():
        known.add(normalize_link(link))

    # Layer 3 — already-joined links (optional, passed from in-memory db)
    if joined_links:
        for link in joined_links:
            known.add(normalize_link(link))

    return known
