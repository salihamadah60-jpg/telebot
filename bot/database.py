import json
import os
from config import DATA_FILE, SEEN_LINKS_FILE, RAW_LINKS_FILE


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
