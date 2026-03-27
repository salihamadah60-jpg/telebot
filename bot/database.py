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


def get_seen_count() -> int:
    if not os.path.exists(SEEN_LINKS_FILE):
        return 0
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def get_raw_count() -> int:
    raw = load_raw_links()
    return len(raw)
