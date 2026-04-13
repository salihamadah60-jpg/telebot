import json
import os
import re
import time
from config import DATA_FILE, SEEN_LINKS_FILE, ARCHIVED_LINKS_FILE, RAW_LINKS_FILE, SORTED_DIR, WHATSAPP_LINKS_FILE, INSPECTION_CACHE_FILE

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
        return {normalize_link(line.strip()) for line in f if line.strip()}


def is_seen(link: str) -> bool:
    clean = normalize_link(link)
    if not os.path.exists(SEEN_LINKS_FILE):
        return False
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        return clean in f.read()


def mark_seen(link: str) -> None:
    clean = clean_telegram_link(link) or str(link or "").strip()
    if not clean:
        return
    with open(SEEN_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(clean + "\n")


def clear_seen() -> None:
    if os.path.exists(SEEN_LINKS_FILE):
        os.remove(SEEN_LINKS_FILE)


_TG_LINK_RE = re.compile(
    r"(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z0-9_+\-/]{3,})(?:[?][^\s\])}>'\"]*)?",
    re.IGNORECASE,
)
_TG_RESOLVE_RE = re.compile(r"tg://resolve\?domain=([A-Za-z][A-Za-z0-9_]{4,31})", re.IGNORECASE)


def clean_telegram_link(link: str) -> str:
    text = str(link or "").strip()
    if not text:
        return ""
    text = text.replace("telegram.me/", "t.me/").replace("telegram.dog/", "t.me/")
    resolve = _TG_RESOLVE_RE.search(text)
    if resolve:
        return f"https://t.me/{resolve.group(1)}"
    match = _TG_LINK_RE.search(text)
    if not match:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", text):
            return f"https://t.me/{text}"
        return ""
    path = match.group(1).strip("/.,;:!?\"')")
    if not path:
        return ""
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


def normalize_link(link: str) -> str:
    cleaned = clean_telegram_link(link)
    link = (cleaned or str(link or "")).strip().lower()
    link = link.replace("https://", "").replace("http://", "")
    if link.endswith("/"):
        link = link[:-1]
    return link


def load_inspection_cache() -> dict:
    if not os.path.exists(INSPECTION_CACHE_FILE):
        return {}
    try:
        with open(INSPECTION_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_inspection_cache(cache: dict) -> None:
    import tempfile, shutil
    tmp_path = INSPECTION_CACHE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    shutil.move(tmp_path, INSPECTION_CACHE_FILE)


def get_cached_inspection(cache: dict, link: str) -> dict | None:
    canonical = clean_telegram_link(link)
    item = cache.get(canonical) if canonical else None
    if item is None:
        item = cache.get(normalize_link(link))
    return item if isinstance(item, dict) else None


def remember_inspection(
    cache: dict,
    link: str,
    info: dict,
    channel_key: str,
    specialty: str,
    is_medical_link: bool,
    addlist_children: list[str] | None = None,
) -> None:
    clean_info = {
        "ok": bool(info.get("ok")),
        "title": info.get("title", "بدون اسم") if info.get("ok") else "—",
        "username": info.get("username", "") or "",
        "bio": info.get("bio", "") or "",
        "link_type": info.get("link_type", "") or "",
        "members": info.get("members"),
        "joined": bool(info.get("joined")),
        "reason": info.get("reason", "") or "",
        "is_private": bool(info.get("is_private")),
    }
    cache_key = clean_telegram_link(link) or normalize_link(link)
    cache[cache_key] = {
        "info": clean_info,
        "channel_key": channel_key,
        "specialty": specialty,
        "is_medical": bool(is_medical_link),
        "addlist_children": addlist_children or [],
        "cached_at": int(time.time()),
    }


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
    values = []
    for link in seen:
        clean = clean_telegram_link(link) or str(link or "").strip()
        if clean:
            values.append(clean)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(set(values))) + ("\n" if values else ""))
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
    clean = clean_telegram_link(link) or str(link or "").strip()
    if not clean:
        return
    with open(ARCHIVED_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(clean + "\n")


def load_archived_set() -> set:
    """Return a set of normalized links that were successfully archived."""
    if not os.path.exists(ARCHIVED_LINKS_FILE):
        return set()
    with open(ARCHIVED_LINKS_FILE, "r", encoding="utf-8") as f:
        return {normalize_link(line.strip()) for line in f if line.strip()}


def get_archived_count() -> int:
    if not os.path.exists(ARCHIVED_LINKS_FILE):
        return 0
    with open(ARCHIVED_LINKS_FILE, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def save_archived_set(archived: set) -> None:
    """Bulk-write the full in-memory archived set to file (atomic)."""
    import tempfile, shutil
    tmp_path = ARCHIVED_LINKS_FILE + ".tmp"
    values = []
    for link in archived:
        clean = clean_telegram_link(link) or str(link or "").strip()
        if clean:
            values.append(clean)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(set(values))) + ("\n" if values else ""))
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
    clean = normalize_link(link)
    entries = [existing for existing in entries if normalize_link(existing.get("link", "")) != clean]
    entries.append(entry)
    _write_sorted_entries(filepath, entries)


def load_sorted_entries(channel_key: str) -> list[dict]:
    filepath = SORTED_FILES.get(channel_key)
    if not filepath or not os.path.exists(filepath):
        return []
    return _read_sorted_entries(filepath)


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


def _canonical_or_original(link: str) -> str:
    clean = clean_telegram_link(link)
    return clean or str(link or "").strip()


def _dedupe_links(links: list) -> tuple[list[str], int, int]:
    result = []
    seen = set()
    changed = 0
    duplicates = 0
    for link in links:
        original = str(link or "").strip()
        if not original:
            continue
        clean = _canonical_or_original(original)
        norm = normalize_link(clean)
        if norm in seen:
            duplicates += 1
            continue
        seen.add(norm)
        if clean != original:
            changed += 1
        result.append(clean)
    return result, changed, duplicates


def _rewrite_link_file(filepath: str) -> tuple[int, int, int]:
    if not os.path.exists(filepath):
        return 0, 0, 0
    with open(filepath, "r", encoding="utf-8") as f:
        original_links = [line.strip() for line in f if line.strip()]
    cleaned, changed, duplicates = _dedupe_links(original_links)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(cleaned) + ("\n" if cleaned else ""))
    return len(cleaned), changed, duplicates


def _merge_cache_item(existing: dict | None, incoming: dict) -> dict:
    if not isinstance(existing, dict):
        return incoming
    existing_time = existing.get("cached_at", 0) or 0
    incoming_time = incoming.get("cached_at", 0) or 0
    return incoming if incoming_time >= existing_time else existing


def reformat_malformed_links(db: dict | None = None) -> dict:
    report = {
        "raw_total": 0,
        "raw_changed": 0,
        "raw_duplicates": 0,
        "seen_total": 0,
        "seen_changed": 0,
        "seen_duplicates": 0,
        "archived_total": 0,
        "archived_changed": 0,
        "archived_duplicates": 0,
        "cache_total": 0,
        "cache_changed": 0,
        "cache_duplicates": 0,
        "sorted_total": 0,
        "sorted_changed": 0,
        "sorted_duplicates": 0,
        "db_changed": 0,
        "db_duplicates": 0,
    }

    raw_links = load_raw_links()
    cleaned_raw, changed, duplicates = _dedupe_links(raw_links)
    if cleaned_raw != raw_links:
        save_raw_links(cleaned_raw)
    report["raw_total"] = len(cleaned_raw)
    report["raw_changed"] = changed
    report["raw_duplicates"] = duplicates

    total, changed, duplicates = _rewrite_link_file(SEEN_LINKS_FILE)
    report["seen_total"] = total
    report["seen_changed"] = changed
    report["seen_duplicates"] = duplicates

    total, changed, duplicates = _rewrite_link_file(ARCHIVED_LINKS_FILE)
    report["archived_total"] = total
    report["archived_changed"] = changed
    report["archived_duplicates"] = duplicates

    cache = load_inspection_cache()
    cleaned_cache = {}
    for key, value in cache.items():
        clean_key = _canonical_or_original(key)
        if clean_key != key:
            report["cache_changed"] += 1
        if clean_key in cleaned_cache:
            report["cache_duplicates"] += 1
        cleaned_cache[clean_key] = _merge_cache_item(cleaned_cache.get(clean_key), value)
    if cleaned_cache != cache:
        save_inspection_cache(cleaned_cache)
    report["cache_total"] = len(cleaned_cache)

    for key, filepath in SORTED_FILES.items():
        if not os.path.exists(filepath):
            continue
        entries = _read_sorted_entries(filepath)
        cleaned_entries = []
        seen = set()
        for entry in entries:
            original = (entry.get("link") or "").strip()
            clean = _canonical_or_original(original)
            norm = normalize_link(clean)
            if norm in seen:
                report["sorted_duplicates"] += 1
                continue
            seen.add(norm)
            if clean != original:
                report["sorted_changed"] += 1
            updated = dict(entry)
            updated["link"] = clean
            cleaned_entries.append(updated)
        _write_sorted_entries(filepath, cleaned_entries)
        report["sorted_total"] += len(cleaned_entries)

    if isinstance(db, dict):
        for field in ("joined_links", "sources"):
            if isinstance(db.get(field), list):
                cleaned, changed, duplicates = _dedupe_links(db[field])
                if cleaned != db[field]:
                    db[field] = cleaned
                report["db_changed"] += changed
                report["db_duplicates"] += duplicates

    return report


def get_storage_stats(db: dict | None = None) -> dict:
    raw_links = load_raw_links()
    raw_norms = {normalize_link(_canonical_or_original(link)) for link in raw_links if str(link or "").strip()}
    sorted_counts = get_sorted_counts()
    useful_sorted_norms = set()
    broken_norms = set()
    for key in SORTED_FILES:
        entries = load_sorted_entries(key)
        target = broken_norms if key == "broken" else useful_sorted_norms
        for entry in entries:
            link = entry.get("link", "")
            if link:
                target.add(normalize_link(link))
    archived_norms = load_archived_set()
    source_norms = {normalize_link(link) for link in (db or {}).get("sources", [])}
    joined_norms = {normalize_link(link) for link in (db or {}).get("joined_links", [])}
    done_norms = archived_norms | useful_sorted_norms | joined_norms
    retry_pending_norms = raw_norms - done_norms - source_norms
    cache = load_inspection_cache()
    malformed = 0
    for link in raw_links:
        original = str(link or "").strip()
        clean = clean_telegram_link(original)
        if clean and clean != original:
            malformed += 1
    for filepath in [SEEN_LINKS_FILE, ARCHIVED_LINKS_FILE, WHATSAPP_LINKS_FILE]:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    original = line.strip()
                    clean = clean_telegram_link(original)
                    if clean and clean != original:
                        malformed += 1
    for key in cache.keys():
        clean = clean_telegram_link(key)
        if clean and clean != key:
            malformed += 1
    for key in SORTED_FILES:
        for entry in load_sorted_entries(key):
            original = (entry.get("link") or "").strip()
            clean = clean_telegram_link(original)
            if clean and clean != original:
                malformed += 1
    return {
        "raw_total": len(raw_links),
        "raw_unique": len(raw_norms),
        "sorted_counts": sorted_counts,
        "sorted_success_total": sum(count for key, count in sorted_counts.items() if key != "broken"),
        "broken_total": sorted_counts.get("broken", 0),
        "archived_total": len(archived_norms),
        "seen_total": len(load_seen_set()),
        "cache_total": len(cache),
        "retry_pending_total": len(retry_pending_norms),
        "joined_total": len(joined_norms),
        "malformed_total": malformed,
    }


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
