#!/usr/bin/env python3
"""
Rebuild a clean, self-contained ChatGPT export archive.

This script copies/derives files into a clean archive and never modifies the
raw export folder.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_EXPORT = Path(os.environ.get("CHATGPT_RAW_EXPORT", PROJECT_ROOT / "runtime" / "raw_export"))
CLEAN_ROOT = Path(os.environ.get("CHATGPT_CLEAN_ROOT", PROJECT_ROOT / "runtime" / "accounts" / "default"))

CONV_JSON_DIR = CLEAN_ROOT / "conversations" / "json"
CONV_MD_DIR = CLEAN_ROOT / "conversations" / "markdown"
SHARED_JSON_DIR = CLEAN_ROOT / "shared_conversations" / "json"
SHARED_MD_DIR = CLEAN_ROOT / "shared_conversations" / "markdown"
ASSETS_DIR = CLEAN_ROOT / "assets"
INDEXES_DIR = CLEAN_ROOT / "indexes"
REPORTS_DIR = CLEAN_ROOT / "reports"

GENERATED_DIRS = [
    CONV_JSON_DIR,
    CONV_MD_DIR,
    SHARED_JSON_DIR,
    SHARED_MD_DIR,
    ASSETS_DIR,
    INDEXES_DIR,
]

REPORT_FILES = [
    REPORTS_DIR / "archive_map.md",
    REPORTS_DIR / "search_examples.md",
    REPORTS_DIR / "data_dictionary.md",
    REPORTS_DIR / "build_report.md",
]

ASSET_ID_RE = re.compile(r"\b(file[-_][A-Za-z0-9]+)\b")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def archive_path(path: Path) -> str:
    """Store portable paths when a file lives inside the clean archive."""
    try:
        return path.resolve().relative_to(CLEAN_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def safe_slug(value: str, max_len: int = 90) -> str:
    value = value or "Untitled"
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r'[\\/:*?"<>|]+', "-", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(". ")
    value = value or "Untitled"
    value = value[:max_len].rstrip(". -_")
    return value or "Untitled"


def short_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value or "unknown")[:12]


def iso_from_ts(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return ""


def date_from_ts(value: Any) -> str:
    iso = iso_from_ts(value)
    return iso[:10] if iso else "unknown-date"


def set_file_mtime(path: Path, timestamp: Any) -> None:
    if timestamp in (None, ""):
        return
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return
    os.utime(path, (ts, ts))


def clean_inline(value: Any, max_len: int | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if max_len and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def detect_asset_type(path: Path) -> tuple[str, str]:
    data = path.read_bytes()[:8192]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"%PDF"):
        return "application/pdf", ".pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/x-wav", ".wav"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip", ".zip"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif", ".gif"
    if b"\x00" not in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream", ".bin"
        comma_count = text.count(",")
        newline_count = text.count("\n")
        if comma_count >= 2 and newline_count >= 1:
            return "text/csv", ".csv"
        return "text/plain", ".txt"
    return "application/octet-stream", ".bin"


def prepare_output_dirs() -> None:
    for directory in GENERATED_DIRS:
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for report in REPORT_FILES:
        if report.exists():
            report.unlink()


def original_asset_name_map() -> dict[str, str]:
    mapping = load_json(RAW_EXPORT / "conversation_asset_file_names.json", default={}) or {}
    result: dict[str, str] = {}
    if isinstance(mapping, dict):
        for key, value in mapping.items():
            if isinstance(value, str):
                stem = Path(str(key)).stem
                result[stem] = value
                result[str(key)] = value
    return result


def library_file_maps() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records = load_json(RAW_EXPORT / "library_files.json", default=[]) or []
    by_file_id: dict[str, dict[str, Any]] = {}
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict):
                for key in ("file_id", "library_file_id"):
                    value = record.get(key)
                    if isinstance(value, str) and value:
                        by_file_id[value] = record
    return records if isinstance(records, list) else [], by_file_id


def extract_asset_ids(value: Any) -> set[str]:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, subvalue in item.items():
                if isinstance(subvalue, str):
                    found.update(ASSET_ID_RE.findall(subvalue))
                elif isinstance(subvalue, (dict, list)):
                    walk(subvalue)
                if isinstance(key, str):
                    found.update(ASSET_ID_RE.findall(key))
        elif isinstance(item, list):
            for subvalue in item:
                walk(subvalue)
        elif isinstance(item, str):
            found.update(ASSET_ID_RE.findall(item))

    walk(value)
    return found


def extract_fileish_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            keys = {str(k).lower() for k in item}
            if keys & {
                "name",
                "file_name",
                "mime_type",
                "library_file_id",
                "file_id",
                "asset_pointer",
                "file_token_size",
                "url",
            }:
                compact = {
                    key: val
                    for key, val in item.items()
                    if isinstance(val, (str, int, float, bool)) or val is None
                }
                if compact:
                    records.append(compact)
            for subvalue in item.values():
                walk(subvalue)
        elif isinstance(item, list):
            for subvalue in item:
                walk(subvalue)

    walk(value)
    return records


def message_text_from_content(content: Any) -> str:
    if not isinstance(content, dict):
        return clean_inline(content)
    parts = content.get("parts")
    if isinstance(parts, list):
        texts = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            else:
                texts.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(texts).strip()
    if "text" in content:
        return clean_inline(content.get("text"))
    return clean_inline(json.dumps(content, ensure_ascii=False))


def conversation_messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    messages = []
    mapping = conversation.get("mapping") or {}
    if not isinstance(mapping, dict):
        return messages
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = author.get("role") or author.get("name") or "unknown"
        content = message_text_from_content(message.get("content"))
        create_time = message.get("create_time")
        if not content and not extract_asset_ids(message):
            continue
        messages.append(
            {
                "node_id": node_id,
                "role": str(role),
                "create_time": create_time,
                "create_time_iso": iso_from_ts(create_time),
                "text": content,
                "asset_ids": sorted(extract_asset_ids(message)),
            }
        )
    return sorted(messages, key=lambda m: (m.get("create_time") is None, m.get("create_time") or 0, m.get("node_id") or ""))


def chat_filename(conversation: dict[str, Any], suffix: str) -> str:
    chat_id = conversation.get("id") or conversation.get("conversation_id") or "unknown"
    date = date_from_ts(conversation.get("create_time"))
    title = safe_slug(conversation.get("title") or "Untitled")
    return f"{date}__{title}__{short_id(str(chat_id))}.{suffix}"


def shared_filename(record: dict[str, Any], suffix: str) -> str:
    chat_id = record.get("conversation_id") or "unknown-conversation"
    share_id = record.get("id") or "unknown-share"
    title = safe_slug(record.get("title") or "Untitled")
    return f"shared__{title}__{short_id(str(chat_id))}__{short_id(str(share_id))}.{suffix}"


def markdown_for_conversation(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    source_shard: str,
    asset_ids: list[str],
    fileish: list[dict[str, Any]],
) -> str:
    title = clean_inline(conversation.get("title") or "Untitled") or "Untitled"
    chat_id = conversation.get("id") or conversation.get("conversation_id") or ""
    lines = [
        f"# {title}",
        "",
        "## Metadata",
        "",
        f"- Chat ID: `{chat_id}`",
        f"- Conversation ID: `{conversation.get('conversation_id') or ''}`",
        f"- Source shard: `{source_shard}`",
        f"- Created: `{iso_from_ts(conversation.get('create_time'))}`",
        f"- Updated: `{iso_from_ts(conversation.get('update_time'))}`",
        f"- Archived: `{conversation.get('is_archived')}`",
        f"- Starred: `{conversation.get('is_starred')}`",
        f"- Current node: `{conversation.get('current_node') or ''}`",
        "",
        "## Asset References",
        "",
    ]
    if asset_ids:
        lines.extend([f"- `{asset_id}`" for asset_id in asset_ids])
    elif fileish:
        for record in fileish[:30]:
            label = record.get("name") or record.get("file_name") or record.get("url") or record.get("library_file_id") or record.get("file_id")
            if label:
                lines.append(f"- `{clean_inline(label, 220)}`")
    else:
        lines.append("- None detected")
    lines.extend(["", "## Conversation", ""])
    if not messages:
        lines.append("_No transcript messages were present in this export record._")
    for idx, message in enumerate(messages, start=1):
        role = message.get("role") or "unknown"
        lines.append(f"### {idx}. {role}")
        if message.get("create_time_iso"):
            lines.append("")
            lines.append(f"`{message['create_time_iso']}`")
        if message.get("asset_ids"):
            lines.append("")
            lines.append("Assets: " + ", ".join(f"`{asset_id}`" for asset_id in message["asset_ids"]))
        lines.append("")
        text = message.get("text") or "_No text content._"
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_for_shared(record: dict[str, Any]) -> str:
    title = clean_inline(record.get("title") or "Untitled") or "Untitled"
    lines = [
        f"# {title}",
        "",
        "## Metadata",
        "",
        f"- Share ID: `{record.get('id') or ''}`",
        f"- Conversation ID: `{record.get('conversation_id') or ''}`",
        f"- Anonymous: `{record.get('is_anonymous')}`",
        "",
        "## Note",
        "",
        "This shared conversation export record is lightweight. The raw export contains share metadata here, not a full transcript.",
        "",
        "## Raw Record",
        "",
        "```json",
        json.dumps(record, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_assets(
    original_names: dict[str, str],
    chat_refs: dict[str, set[str]],
    chat_titles: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    asset_rows: list[dict[str, Any]] = []
    asset_by_id: dict[str, dict[str, Any]] = {}
    name_counts: Counter[str] = Counter()
    for source_path in sorted(RAW_EXPORT.glob("*.dat")):
        asset_id = source_path.stem
        detected_mime, extension = detect_asset_type(source_path)
        original_name = original_names.get(asset_id) or original_names.get(source_path.name) or ""
        original_stem = safe_slug(Path(original_name).stem, 80) if original_name else ""
        base_name = f"{asset_id}__{original_stem}" if original_stem else asset_id
        clean_name = safe_slug(base_name, 140) + extension
        name_counts[clean_name] += 1
        if name_counts[clean_name] > 1:
            clean_name = f"{safe_slug(base_name, 130)}__{name_counts[clean_name]}{extension}"
        target_path = ASSETS_DIR / clean_name
        shutil.copy2(source_path, target_path)
        referenced_by = sorted(chat_refs.get(asset_id, set()))
        row = {
            "asset_id": asset_id,
            "original_dat": archive_path(source_path),
            "clean_path": archive_path(target_path),
            "clean_filename": clean_name,
            "original_filename": original_name,
            "detected_mime": detected_mime,
            "extension": extension,
            "size_bytes": source_path.stat().st_size,
            "referenced_by_chat_ids": "|".join(referenced_by),
            "referenced_by_chat_titles": "|".join(clean_inline(chat_titles.get(chat_id, ""), 120) for chat_id in referenced_by),
            "linked": bool(referenced_by),
        }
        asset_rows.append(row)
        asset_by_id[asset_id] = row
    return asset_rows, asset_by_id


def write_reports(
    build_stats: dict[str, Any],
    unknown_types: Counter[str],
) -> None:
    (REPORTS_DIR / "archive_map.md").write_text(
        """# Archive Map

Start here when a future Codex chat needs to use this archive.

## Fast Search

- Use `indexes/master_search.jsonl` for the first broad search.
- Use `indexes/conversations_index.csv` for title/date/status filtering.
- Use `indexes/chat_manifest.json` to jump from a chat ID to its JSON, Markdown, and assets.

## Reading Chats

- Human-readable chats are in `conversations/markdown/`.
- Source-preserving per-chat JSON files are in `conversations/json/`.
- Shared conversation metadata is in `shared_conversations/`.

## Assets

- Normalized asset files are in `assets/`.
- Use `indexes/assets_index.csv` to map asset IDs and original names to clean filenames.
- Use `indexes/assets_by_chat.json` to find files associated with a chat.
- Use `indexes/chats_by_asset.json` to find which chats reference a file.
""",
        encoding="utf-8",
    )
    (REPORTS_DIR / "search_examples.md").write_text(
        """# Search Examples

```bash
rg -i "project planning" indexes/master_search.jsonl
rg -i "customer research" conversations/markdown
rg -i "meeting notes" indexes
```

Find a chat in the manifest:

```bash
jq '.["CHAT_ID_HERE"]' indexes/chat_manifest.json
```

Find assets for a chat:

```bash
jq '.["CHAT_ID_HERE"]' indexes/assets_by_chat.json
```
""",
        encoding="utf-8",
    )
    (REPORTS_DIR / "data_dictionary.md").write_text(
        """# Data Dictionary

## Raw Export

- `conversations-*.json`: sharded arrays of full conversation records.
- `mapping`: graph of message nodes inside a conversation.
- `current_node`: node ID that represents the active/latest path through the chat.
- `conversation_id` / `id`: identifiers used by ChatGPT for the conversation.
- `shared_conversations.json`: lightweight records for shared chats.
- `.dat` files: raw asset blobs. The extension is generic; actual type is detected from file bytes.
- `conversation_asset_file_names.json`: maps some asset `.dat` filenames to original filenames.
- `library_files.json`: metadata about files in ChatGPT's library/file system.
- `export_manifest.json`: export inventory and bookkeeping.

## Clean Archive

- `conversations/json`: one pretty JSON file per chat.
- `conversations/markdown`: one readable Markdown transcript per chat.
- `assets`: copied asset files with detected real extensions.
- `indexes/master_search.jsonl`: compact search records for future Codex.
- `indexes/chat_manifest.json`: chat ID to clean file paths and metadata.
""",
        encoding="utf-8",
    )
    lines = [
        "# Build Report",
        "",
        "## Counts",
        "",
    ]
    for key, value in build_stats.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Asset Types", ""])
    for mime, count in unknown_types.most_common():
        lines.append(f"- `{mime}`: `{count}`")
    lines.append("")
    (REPORTS_DIR / "build_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    if not RAW_EXPORT.exists():
        raise SystemExit(f"Raw export not found: {RAW_EXPORT}")
    prepare_output_dirs()

    original_names = original_asset_name_map()
    library_records, library_by_id = library_file_maps()

    conversations_index: list[dict[str, Any]] = []
    master_search_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    title_alias_rows: list[dict[str, Any]] = []
    manifest: dict[str, dict[str, Any]] = {}
    assets_by_chat: dict[str, list[str]] = {}
    chats_by_asset: dict[str, set[str]] = defaultdict(set)
    chat_titles: dict[str, str] = {}
    chat_asset_refs: dict[str, set[str]] = defaultdict(set)
    generated_json_paths: set[str] = set()
    generated_md_paths: set[str] = set()
    message_counts: list[int] = []

    for shard in sorted(RAW_EXPORT.glob("conversations-*.json")):
        conversations = load_json(shard, default=[]) or []
        if not isinstance(conversations, list):
            continue
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            chat_id = str(conversation.get("id") or conversation.get("conversation_id") or "")
            if not chat_id:
                continue
            messages = conversation_messages(conversation)
            message_counts.append(len(messages))
            asset_ids = sorted(extract_asset_ids(conversation))
            fileish = extract_fileish_records(conversation)
            for record in fileish:
                for key in ("file_id", "library_file_id"):
                    value = record.get(key)
                    if isinstance(value, str) and value in library_by_id:
                        asset_ids.extend(sorted(extract_asset_ids(library_by_id[value])))
            asset_ids = sorted(set(asset_ids))
            chat_asset_refs[chat_id].update(asset_ids)
            for asset_id in asset_ids:
                chats_by_asset[asset_id].add(chat_id)
            assets_by_chat[chat_id] = asset_ids
            title = clean_inline(conversation.get("title") or "Untitled") or "Untitled"
            chat_titles[chat_id] = title
            json_name = chat_filename(conversation, "json")
            md_name = chat_filename(conversation, "md")
            json_path = CONV_JSON_DIR / json_name
            md_path = CONV_MD_DIR / md_name
            dump_json(json_path, conversation)
            md_path.write_text(
                markdown_for_conversation(conversation, messages, shard.name, asset_ids, fileish),
                encoding="utf-8",
            )
            set_file_mtime(json_path, conversation.get("update_time") or conversation.get("create_time"))
            set_file_mtime(md_path, conversation.get("update_time") or conversation.get("create_time"))
            generated_json_paths.add(str(json_path))
            generated_md_paths.add(str(md_path))
            first_user = next((m["text"] for m in messages if m.get("role") == "user" and m.get("text")), "")
            last_user = next((m["text"] for m in reversed(messages) if m.get("role") == "user" and m.get("text")), "")
            all_text = "\n".join(m["text"] for m in messages if m.get("text"))
            attachment_names = sorted(
                {
                    str(record.get("name") or record.get("file_name") or "")
                    for record in fileish
                    if record.get("name") or record.get("file_name")
                }
            )
            index_row = {
                "chat_id": chat_id,
                "conversation_id": conversation.get("conversation_id") or "",
                "title": title,
                "created_at": iso_from_ts(conversation.get("create_time")),
                "updated_at": iso_from_ts(conversation.get("update_time")),
                "source_shard": shard.name,
                "json_path": archive_path(json_path),
                "markdown_path": archive_path(md_path),
                "message_count": len(messages),
                "asset_count": len(asset_ids),
                "asset_ids": "|".join(asset_ids),
                "attachment_names": "|".join(attachment_names),
                "is_archived": conversation.get("is_archived"),
                "is_starred": conversation.get("is_starred"),
            }
            conversations_index.append(index_row)
            timeline_rows.append(
                {
                    "created_at": index_row["created_at"],
                    "updated_at": index_row["updated_at"],
                    "title": title,
                    "chat_id": chat_id,
                    "message_count": len(messages),
                    "asset_count": len(asset_ids),
                    "markdown_path": archive_path(md_path),
                }
            )
            title_alias_rows.append(
                {
                    "title": title,
                    "clean_json_filename": json_name,
                    "clean_markdown_filename": md_name,
                    "chat_id": chat_id,
                }
            )
            manifest[chat_id] = {
                "title": title,
                "created_at": index_row["created_at"],
                "updated_at": index_row["updated_at"],
                "source_shard": shard.name,
                "json_path": archive_path(json_path),
                "markdown_path": archive_path(md_path),
                "asset_ids": asset_ids,
                "message_count": len(messages),
            }
            master_search_rows.append(
                {
                    "chat_id": chat_id,
                    "title": title,
                    "created_at": index_row["created_at"],
                    "updated_at": index_row["updated_at"],
                    "source_shard": shard.name,
                    "message_count": len(messages),
                    "asset_ids": asset_ids,
                    "attachment_names": attachment_names,
                    "first_user_message": clean_inline(first_user, 2000),
                    "last_user_message": clean_inline(last_user, 2000),
                    "searchable_text": clean_inline(f"{title}\n{all_text}", 40000),
                }
            )

    asset_rows, asset_by_id = build_assets(original_names, chats_by_asset, chat_titles)
    chats_by_asset_json = {asset_id: sorted(chat_ids) for asset_id, chat_ids in sorted(chats_by_asset.items())}
    for chat_id, asset_ids in assets_by_chat.items():
        manifest[chat_id]["assets"] = [asset_by_id[asset_id] for asset_id in asset_ids if asset_id in asset_by_id]

    write_csv(
        INDEXES_DIR / "conversations_index.csv",
        conversations_index,
        [
            "chat_id",
            "conversation_id",
            "title",
            "created_at",
            "updated_at",
            "source_shard",
            "json_path",
            "markdown_path",
            "message_count",
            "asset_count",
            "asset_ids",
            "attachment_names",
            "is_archived",
            "is_starred",
        ],
    )
    dump_json(INDEXES_DIR / "conversations_index.json", conversations_index)
    dump_json(INDEXES_DIR / "chat_manifest.json", manifest)
    dump_json(INDEXES_DIR / "assets_by_chat.json", assets_by_chat)
    dump_json(INDEXES_DIR / "chats_by_asset.json", chats_by_asset_json)
    dump_json(INDEXES_DIR / "assets_index.json", asset_rows)
    write_csv(
        INDEXES_DIR / "assets_index.csv",
        asset_rows,
        [
            "asset_id",
            "original_dat",
            "clean_path",
            "clean_filename",
            "original_filename",
            "detected_mime",
            "extension",
            "size_bytes",
            "referenced_by_chat_ids",
            "referenced_by_chat_titles",
            "linked",
        ],
    )
    write_csv(
        INDEXES_DIR / "timeline.csv",
        sorted(timeline_rows, key=lambda row: (row["created_at"], row["chat_id"])),
        ["created_at", "updated_at", "title", "chat_id", "message_count", "asset_count", "markdown_path"],
    )
    write_csv(
        INDEXES_DIR / "title_aliases.csv",
        title_alias_rows,
        ["title", "clean_json_filename", "clean_markdown_filename", "chat_id"],
    )
    with (INDEXES_DIR / "master_search.jsonl").open("w", encoding="utf-8") as f:
        for row in master_search_rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    library_index_rows = []
    for record in library_records:
        if not isinstance(record, dict):
            continue
        library_index_rows.append(
            {
                "file_id": record.get("file_id") or "",
                "file_name": record.get("file_name") or "",
                "file_extension": record.get("file_extension") or "",
                "mime_type": record.get("mime_type") or "",
                "file_size_bytes": record.get("file_size_bytes") or "",
                "created_at": record.get("created_at") or "",
                "file_upload_time": record.get("file_upload_time") or "",
                "initiating_conversation_id": record.get("initiating_conversation_id") or "",
                "gizmo_id": record.get("gizmo_id") or "",
                "is_project": record.get("is_project") or "",
                "is_visible": record.get("is_visible") or "",
            }
        )
    write_csv(
        INDEXES_DIR / "library_files_index.csv",
        library_index_rows,
        [
            "file_id",
            "file_name",
            "file_extension",
            "mime_type",
            "file_size_bytes",
            "created_at",
            "file_upload_time",
            "initiating_conversation_id",
            "gizmo_id",
            "is_project",
            "is_visible",
        ],
    )

    shared_records = load_json(RAW_EXPORT / "shared_conversations.json", default=[]) or []
    shared_count = 0
    if isinstance(shared_records, list):
        for record in shared_records:
            if not isinstance(record, dict):
                continue
            shared_count += 1
            dump_json(SHARED_JSON_DIR / shared_filename(record, "json"), record)
            (SHARED_MD_DIR / shared_filename(record, "md")).write_text(markdown_for_shared(record), encoding="utf-8")

    asset_type_counts = Counter(row["detected_mime"] for row in asset_rows)
    build_stats = {
        "normal_conversation_json_files": len(list(CONV_JSON_DIR.glob("*.json"))),
        "normal_conversation_markdown_files": len(list(CONV_MD_DIR.glob("*.md"))),
        "shared_conversation_json_files": len(list(SHARED_JSON_DIR.glob("*.json"))),
        "shared_conversation_markdown_files": len(list(SHARED_MD_DIR.glob("*.md"))),
        "asset_files_copied": len(list(ASSETS_DIR.iterdir())),
        "asset_rows_indexed": len(asset_rows),
        "master_search_rows": len(master_search_rows),
        "conversation_index_rows": len(conversations_index),
        "library_file_rows": len(library_index_rows),
        "linked_assets": sum(1 for row in asset_rows if row["linked"]),
        "unlinked_assets": sum(1 for row in asset_rows if not row["linked"]),
        "min_message_count": min(message_counts) if message_counts else 0,
        "max_message_count": max(message_counts) if message_counts else 0,
        "shared_records_processed": shared_count,
    }
    write_reports(build_stats, asset_type_counts)
    print(json.dumps(build_stats, indent=2))


if __name__ == "__main__":
    main()
