import json
import os
from config import DATA_FILE, SEEN_LINKS_FILE, ARCHIVED_LINKS_FILE, RAW_LINKS_FILE, SORTED_DIR, WHATSAPP_LINKS_FILE

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

def _clean_sorted_value(value) -> str:
    text = "—" if value is None or value == "" else str(value)
    return " ".join(text.split())


def _clean_member_value(value) -> str:
    text = _clean_sorted_value(value)
    return "غير متاح" if text in {"—", "none", "None"} else text


def _extract_link_from_sorted_entry(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("الرابط:"):
            return stripped.split(":", 1)[1].strip()
        if stripped.startswith("🔗 الرابط:"):
            return stripped.split(":", 1)[1].strip()
    if len(lines) == 1:
        stripped = lines[0].strip()
        if stripped.startswith(("http://", "https://", "t.me/")):
            return stripped
    return ""


def _extract_sorted_field(lines: list[str], field_name: str) -> str:
    for line in lines:
        stripped = line.strip()
        if field_name in stripped:
            return stripped.split(field_name, 1)[1].lstrip(":").strip()
    return ""


def _entry_from_lines(lines: list[str], section: str) -> dict:
    link = _extract_link_from_sorted_entry(lines)
    specialty = _extract_sorted_field(lines, "التخصص") or section or "طب_عام"
    name = _extract_sorted_field(lines, "الاسم")
    members = _extract_sorted_field(lines, "عدد الأعضاء")
    text = "\n".join(lines)
    return {
        "text": text,
        "link": link,
        "name": name,
        "specialty": specialty,
        "members": members,
    }


def _read_sorted_entries(filepath: str) -> list[dict]:
    if not os.path.exists(filepath):
        return []
    entries = []
    current = []
    current_section = "طب_عام"
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")
            if raw.strip() == "":
                if current:
                    entries.append(_entry_from_lines(current, current_section))
                    current = []
                continue
            stripped = raw.strip()
            if stripped.startswith("## "):
                if current:
                    entries.append(_entry_from_lines(current, current_section))
                    current = []
                current_section = stripped.lstrip("#").strip() or "طب_عام"
                continue
            if stripped.startswith(("http://", "https://", "t.me/")):
                if current:
                    entries.append(_entry_from_lines(current, current_section))
                    current = []
                entries.append({
                    "text": stripped,
                    "link": stripped,
                    "name": "",
                    "specialty": current_section,
                    "members": "",
                })
                continue
            current.append(raw)
    if current:
        entries.append(_entry_from_lines(current, current_section))
    return entries


def _format_sorted_entry(index: int, entry: dict) -> str:
    return "\n".join([
        f"{index}- الاسم: {_clean_sorted_value(entry.get('name'))}",
        f"   التخصص: {_clean_sorted_value(entry.get('specialty') or 'طب_عام')}",
        f"   عدد الأعضاء: {_clean_member_value(entry.get('members'))}",
        f"   الرابط: {(entry.get('link') or '').strip()}",
    ])


def _write_sorted_entries(filepath: str, entries: list[dict]) -> None:
    grouped: dict[str, list[dict]] = {}
    section_order: list[str] = []
    for entry in entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        section = _clean_sorted_value(entry.get("specialty") or "طب_عام")
        if section not in grouped:
            grouped[section] = []
            section_order.append(section)
        grouped[section].append(entry)

    with open(filepath, "w", encoding="utf-8") as f:
        for section in section_order:
            f.write(f"## {section}\n\n")
            for index, entry in enumerate(grouped[section], 1):
                f.write(_format_sorted_entry(index, entry) + "\n\n")


def save_sorted_link(
    channel_key: str,
    link: str,
    name: str | None = None,
    specialty: str | None = None,
    members=None,
) -> None:
    """Append a sorted link metadata entry to the appropriate category file."""
    filepath = SORTED_FILES.get(channel_key)
    if not filepath:
        return
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    entries = _read_sorted_entries(filepath)
    if name is None and specialty is None and members is None:
        entry = {
            "link": link.strip(),
            "name": "",
            "specialty": "طب_عام",
            "members": "",
        }
    else:
        entry = {
            "link": link.strip(),
            "name": name,
            "specialty": specialty or "طب_عام",
            "members": members,
        }
    entries.append(entry)
    _write_sorted_entries(filepath, entries)


def load_sorted_links(channel_key: str) -> list:
    """Load all locally-sorted links for a category (deduplicated, ordered)."""
    filepath = SORTED_FILES.get(channel_key)
    if not filepath or not os.path.exists(filepath):
        return []
    seen = set()
    result = []
    for entry in _read_sorted_entries(filepath):
        lnk = entry.get("link", "").strip()
        if lnk and lnk not in seen:
            seen.add(lnk)
            result.append(lnk)
    return result


def load_sorted_message(channel_key: str) -> str:
    filepath = SORTED_FILES.get(channel_key)
    if not filepath or not os.path.exists(filepath):
        return ""
    entries = _read_sorted_entries(filepath)
    grouped: dict[str, list[dict]] = {}
    section_order: list[str] = []
    for entry in entries:
        section = _clean_sorted_value(entry.get("specialty") or "طب_عام")
        if section not in grouped:
            grouped[section] = []
            section_order.append(section)
        grouped[section].append(entry)
    parts = []
    for section in section_order:
        parts.append(f"## {section}")
        for index, entry in enumerate(grouped[section], 1):
            parts.append(_format_sorted_entry(index, entry))
    return "\n\n".join(parts)


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
            counts[key] = len(_read_sorted_entries(filepath))
        else:
            counts[key] = 0
    return counts


def save_whatsapp_links(links: list[str]) -> int:
    existing = set(load_whatsapp_links(normalized=True))
    new_links = []
    for link in links:
        clean = link.strip()
        if not clean:
            continue
        norm = normalize_link(clean)
        if norm not in existing:
            existing.add(norm)
            new_links.append(clean)
    if not new_links:
        return 0
    with open(WHATSAPP_LINKS_FILE, "a", encoding="utf-8") as f:
        for link in new_links:
            f.write(link + "\n")
    return len(new_links)


def load_whatsapp_links(normalized: bool = False) -> list:
    if not os.path.exists(WHATSAPP_LINKS_FILE):
        return []
    result = []
    seen = set()
    with open(WHATSAPP_LINKS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            link = line.strip()
            if not link:
                continue
            norm = normalize_link(link)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(norm if normalized else link)
    return result


def get_whatsapp_count() -> int:
    return len(load_whatsapp_links())


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
