#!/usr/bin/env python3
"""
Serve a local ChatGPT-style viewer for the cleaned export.

It reads generated indexes and per-chat JSON files, then exposes a small local
API for the browser UI. Moving chats to the viewer bin is enabled; permanent
deletion is opt-in through an environment variable.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


SOURCE_ROOT = Path(os.environ.get("CHATGPT_EXPORT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
DATA_ROOT = Path(os.environ.get("CHATGPT_EXPORT_DATA_ROOT", SOURCE_ROOT / "runtime"))
ROOT = DATA_ROOT
VIEWER = SOURCE_ROOT / "chat_viewer"
ACCOUNTS_DIR = DATA_ROOT / "accounts"
ACCOUNTS_CONFIG = ACCOUNTS_DIR / "accounts.json"
ALLOW_PERMANENT_DELETE = os.environ.get("CHATGPT_VIEWER_ALLOW_PERMANENT_DELETE", "0").casefold() in {
    "1", "true", "yes", "on"
}
ACTIVE_ACCOUNT_SLUG = ""
INDEXES = ROOT / "indexes"
SEMANTIC = ROOT / "semantic_search"
TRASH = ROOT / "_viewer_bin"


@dataclass
class ChatSummary:
    chat_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    asset_ids: list[str]
    attachment_names: list[str]
    first_user_message: str
    last_user_message: str
    searchable_text: str
    json_path: str
    markdown_path: str


def iso_from_timestamp(value: float | int | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def rebase_clean_path(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else ROOT / path)


def slugify_account(email: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", email.casefold()).strip("_")
    return slug or "account"


def ensure_accounts_config() -> list[dict]:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS_CONFIG.exists():
        ACCOUNTS_CONFIG.write_text(json.dumps({"active": "", "accounts": []}, indent=2), encoding="utf-8")
        return []
    data = json.loads(ACCOUNTS_CONFIG.read_text(encoding="utf-8"))
    return data.get("accounts") or []


def accounts_config() -> dict:
    ensure_accounts_config()
    data = json.loads(ACCOUNTS_CONFIG.read_text(encoding="utf-8"))
    accounts = [dict(account) for account in data.get("accounts") or []]
    return {"active": data.get("active") or (accounts[0]["slug"] if accounts else ""), "accounts": accounts}


def account_by_slug(slug: str) -> dict | None:
    for account in accounts_config()["accounts"]:
        if account.get("slug") == slug:
            return account
    return None


def configure_account(slug: str) -> None:
    global ACTIVE_ACCOUNT_SLUG, ROOT, INDEXES, SEMANTIC, TRASH
    account = account_by_slug(slug)
    if not account:
        raise RuntimeError("Account not found.")
    ACTIVE_ACCOUNT_SLUG = account["slug"]
    account_path = Path(account["root"])
    ROOT = account_path if account_path.is_absolute() else DATA_ROOT / account_path
    INDEXES = ROOT / "indexes"
    SEMANTIC = ROOT / "semantic_search"
    TRASH = ROOT / "_viewer_bin"


def set_active_account(slug: str) -> dict:
    data = accounts_config()
    if not any(account.get("slug") == slug for account in data["accounts"]):
        raise ViewerActionError("Account not found.")
    data["active"] = slug
    ACCOUNTS_CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    configure_account(slug)
    reload_account_data()
    return account_status()


def account_status() -> dict:
    data = accounts_config()
    rows = []
    for account in data["accounts"]:
        account_path = Path(account["root"])
        root = account_path if account_path.is_absolute() else DATA_ROOT / account_path
        ready = (root / "indexes" / "master_search.jsonl").exists() and (root / "indexes" / "chat_manifest.json").exists()
        rows.append({**account, "ready": ready, "is_active": account.get("slug") == ACTIVE_ACCOUNT_SLUG})
    return {
        "active": ACTIVE_ACCOUNT_SLUG,
        "accounts": rows,
        "capabilities": {"permanent_delete": ALLOW_PERMANENT_DELETE},
    }


def deleted_chat_ids() -> set[str]:
    log_path = TRASH / "deletion_log.jsonl"
    if not log_path.exists():
        return set()
    ids = set()
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("chat_id"):
                ids.add(row["chat_id"])
    return ids


def load_summaries() -> list[ChatSummary]:
    manifest_path = INDEXES / "chat_manifest.json"
    search_path = INDEXES / "master_search.jsonl"
    if not manifest_path.exists() or not search_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hidden_chat_ids = deleted_chat_ids()
    rows: list[ChatSummary] = []
    with search_path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = json.loads(line)
            if raw["chat_id"] in hidden_chat_ids:
                continue
            meta = manifest.get(raw["chat_id"], {})
            rows.append(
                ChatSummary(
                    chat_id=raw["chat_id"],
                    title=raw.get("title") or "Untitled",
                    created_at=raw.get("created_at") or "",
                    updated_at=raw.get("updated_at") or "",
                    message_count=int(raw.get("message_count") or 0),
                    asset_ids=raw.get("asset_ids") or [],
                    attachment_names=raw.get("attachment_names") or [],
                    first_user_message=raw.get("first_user_message") or "",
                    last_user_message=raw.get("last_user_message") or "",
                    searchable_text=raw.get("searchable_text") or "",
                    json_path=rebase_clean_path(meta.get("json_path")),
                    markdown_path=rebase_clean_path(meta.get("markdown_path")),
                )
            )
    rows.sort(key=lambda row: row.updated_at, reverse=True)
    return rows


SUMMARIES: list[ChatSummary] = []
SUMMARY_BY_ID: dict[str, ChatSummary] = {}
SEMANTIC_INDEX = None
SEMANTIC_LOCK = threading.Lock()


def load_json(path: Path, fallback: object) -> object:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


ASSETS_BY_CHAT = load_json(INDEXES / "assets_by_chat.json", {})
CHATS_BY_ASSET = load_json(INDEXES / "chats_by_asset.json", {})
ASSET_BY_ID = {row["asset_id"]: row for row in load_json(INDEXES / "assets_index.json", [])}


def reload_account_data() -> None:
    global SUMMARIES, SUMMARY_BY_ID, ASSETS_BY_CHAT, CHATS_BY_ASSET, ASSET_BY_ID, SEMANTIC_INDEX
    SUMMARIES = load_summaries()
    SUMMARY_BY_ID = {row.chat_id: row for row in SUMMARIES}
    ASSETS_BY_CHAT = load_json(INDEXES / "assets_by_chat.json", {})
    CHATS_BY_ASSET = load_json(INDEXES / "chats_by_asset.json", {})
    ASSET_BY_ID = {row["asset_id"]: row for row in load_json(INDEXES / "assets_index.json", [])}
    SEMANTIC_INDEX = None


class SemanticSearchError(RuntimeError):
    pass


class ViewerActionError(RuntimeError):
    pass


if accounts_config()["active"]:
    configure_account(accounts_config()["active"])
reload_account_data()


def clean_text(value: object, max_len: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if "asset_pointer" in text[:260] or "image_asset_pointer" in text[:260]:
        text = "[Attachment]"
    if max_len and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def clean_message_display(text: str) -> str:
    # ChatGPT exports sometimes contain private-use entity markers such as
    # \ue200entity\ue202["country","Italy","European country"]\ue201. Show the
    # human label instead of the raw marker.
    text = re.sub(
        r"\ue200entity\ue202\[[^\]]*?\"([^\"]+)\"[^\]]*?\]\ue201",
        r"\1",
        text,
    )
    text = text.replace("\ue200", "").replace("\ue201", "").replace("\ue202", "")
    return text.strip()


def asset_record(asset_id: str) -> dict | None:
    if not asset_id:
        return None
    if asset_id.startswith("sediment://"):
        asset_id = asset_id.removeprefix("sediment://")
    return ASSET_BY_ID.get(asset_id)


def public_asset(asset_id: str) -> dict | None:
    record = asset_record(asset_id)
    if not record:
        return None
    clean_path = rebase_clean_path(record.get("clean_path"))
    mime = record.get("detected_mime") or mimetypes.guess_type(clean_path)[0] or "application/octet-stream"
    return {
        "asset_id": asset_id,
        "filename": record.get("original_filename") or record.get("clean_filename") or asset_id,
        "clean_filename": record.get("clean_filename") or "",
        "content_type": mime,
        "extension": record.get("extension") or Path(clean_path).suffix,
        "size_bytes": int(record.get("size_bytes") or 0),
        "path": clean_path,
        "source_path": record.get("original_dat") or "",
        "is_image": mime.startswith("image/"),
        "is_pdf": mime == "application/pdf" or clean_path.casefold().endswith(".pdf"),
        "preview_url": f"/api/asset/{quote(asset_id)}" if mime.startswith("image/") else "",
    }


def message_text(content: dict | None) -> tuple[str, list[dict]]:
    if not content:
        return "", []
    parts = content.get("parts") or []
    text_parts: list[str] = []
    attachments: list[dict] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            text_parts.append(json.dumps(part, ensure_ascii=False, indent=2))
            continue
        content_type = part.get("content_type") or part.get("type") or "object"
        asset_pointer = part.get("asset_pointer") or part.get("file_id")
        if asset_pointer:
            lookup_id = asset_pointer.removeprefix("sediment://") if isinstance(asset_pointer, str) else asset_pointer
            asset = public_asset(lookup_id) or {}
            attachments.append(
                {
                    **asset,
                    "export_content_type": content_type,
                    "asset_pointer": asset_pointer,
                    "resolved": bool(asset),
                    "filename": asset.get("filename") or part.get("name") or part.get("filename") or "",
                }
            )
        fallback = part.get("text") or part.get("name") or part.get("filename")
        if fallback:
            text_parts.append(str(fallback))
        elif asset_pointer:
            continue
        else:
            text_parts.append(json.dumps(part, ensure_ascii=False, indent=2))
    return clean_message_display("\n\n".join(text_parts)), attachments


def conversation_path(data: dict) -> list[dict]:
    mapping = data.get("mapping") or {}
    current = data.get("current_node")
    chain: list[dict] = []
    seen: set[str] = set()
    while current and current in mapping and current not in seen:
        seen.add(current)
        node = mapping[current]
        chain.append(node)
        current = node.get("parent")
    chain.reverse()
    if chain:
        return chain
    return sorted(
        mapping.values(),
        key=lambda node: ((node.get("message") or {}).get("create_time") or 0, node.get("id") or ""),
    )


def parse_conversation(chat_id: str) -> dict:
    summary = SUMMARY_BY_ID.get(chat_id)
    if not summary or not summary.json_path:
        raise FileNotFoundError(chat_id)
    path = Path(summary.json_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    conversation_assets = [asset for asset in (public_asset(asset_id) for asset_id in summary.asset_ids) if asset]
    messages = []
    for node in conversation_path(data):
        msg = node.get("message")
        if not msg:
            continue
        role = ((msg.get("author") or {}).get("role") or "unknown").lower()
        content_text, attachments = message_text(msg.get("content"))
        if not content_text and not attachments:
            continue
        metadata = msg.get("metadata") or {}
        messages.append(
            {
                "id": msg.get("id") or node.get("id"),
                "node_id": node.get("id"),
                "role": role,
                "author_name": (msg.get("author") or {}).get("name"),
                "created_at": iso_from_timestamp(msg.get("create_time")),
                "content_type": (msg.get("content") or {}).get("content_type"),
                "text": content_text,
                "attachments": attachments,
                "model": metadata.get("model_slug") or metadata.get("model") or "",
                "metadata": metadata,
            }
        )
    return {
        "chat_id": chat_id,
        "title": data.get("title") or summary.title,
        "created_at": iso_from_timestamp(data.get("create_time")) or summary.created_at,
        "updated_at": iso_from_timestamp(data.get("update_time")) or summary.updated_at,
        "is_archived": bool(data.get("is_archived")),
        "is_starred": bool(data.get("is_starred")),
        "message_count": len(messages),
        "source_json": summary.json_path,
        "source_markdown": summary.markdown_path,
        "asset_ids": summary.asset_ids,
        "attachment_names": summary.attachment_names,
        "messages": messages,
        "assets": conversation_assets,
    }


def search_conversations(query: str, limit: int, offset: int, titles_only: bool = False) -> dict:
    query_norm = query.casefold().strip()
    if query_norm:
        terms = [term for term in re.split(r"\s+", query_norm) if term]
        matches = []
        for row in SUMMARIES:
            if titles_only:
                haystack = row.title.casefold()
            else:
                haystack = f"{row.title}\n{row.first_user_message}\n{row.last_user_message}\n{row.searchable_text}".casefold()
            if all(term in haystack for term in terms):
                matches.append(row)
    else:
        matches = SUMMARIES
    page = matches[offset : offset + limit]
    return {
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "chat_id": row.chat_id,
                "title": row.title,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "message_count": row.message_count,
                "asset_count": len(row.asset_ids),
                "attachment_names": row.attachment_names[:6],
                "first_user_message": clean_text(row.first_user_message, 220),
                "last_user_message": clean_text(row.last_user_message, 220),
            }
            for row in page
        ],
    }


def load_semantic_index() -> dict:
    global SEMANTIC_INDEX
    with SEMANTIC_LOCK:
        if SEMANTIC_INDEX is not None:
            return SEMANTIC_INDEX
        embeddings_path = SEMANTIC / "chat_embeddings.npy"
        records_path = SEMANTIC / "chat_records.jsonl"
        manifest_path = SEMANTIC / "semantic_manifest.json"
        if not embeddings_path.exists() or not records_path.exists() or not manifest_path.exists():
            raise SemanticSearchError("Semantic index has not been built yet.")
        try:
            import numpy as np
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise SemanticSearchError("Semantic search dependencies are not installed in this Python environment.") from exc
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        embeddings = np.load(embeddings_path)
        model = TextEmbedding(model_name=manifest["model_name"])
        SEMANTIC_INDEX = {
            "np": np,
            "model": model,
            "manifest": manifest,
            "records": records,
            "embeddings": embeddings,
        }
        return SEMANTIC_INDEX


def semantic_search_conversations(query: str, limit: int, offset: int) -> dict:
    query_norm = query.strip()
    if not query_norm:
        return search_conversations("", limit, offset)
    index = load_semantic_index()
    np = index["np"]
    query_vector = np.array(list(index["model"].embed([query_norm]))[0], dtype=np.float32)
    query_vector = query_vector / max(float(np.linalg.norm(query_vector)), 1e-12)
    scores = index["embeddings"] @ query_vector
    order = np.argsort(-scores)
    total = len(order)
    page_indices = order[offset : offset + limit]
    items = []
    for record_index in page_indices:
        record = index["records"][int(record_index)]
        summary = SUMMARY_BY_ID.get(record["chat_id"])
        if not summary:
            continue
        items.append(
            {
                "chat_id": summary.chat_id,
                "title": summary.title,
                "created_at": summary.created_at,
                "updated_at": summary.updated_at,
                "message_count": summary.message_count,
                "asset_count": len(summary.asset_ids),
                "attachment_names": summary.attachment_names[:6],
                "first_user_message": clean_text(summary.first_user_message, 220),
                "last_user_message": clean_text(summary.last_user_message, 220),
                "semantic_score": round(float(scores[int(record_index)]), 4),
            }
        )
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "semantic": True,
        "model_name": index["manifest"]["model_name"],
        "items": items,
    }


def safe_existing_path(value: str) -> Path:
    path = Path(rebase_clean_path(value)).resolve()
    if not path.exists():
        raise ViewerActionError(f"File does not exist: {value}")
    if path != ROOT.resolve() and ROOT.resolve() not in path.parents:
        raise ViewerActionError("The viewer can only reveal files inside the active archive.")
    return path


def reveal_path(value: str) -> None:
    path = safe_existing_path(value)
    subprocess.run(["open", "-R", str(path)], check=True)


def trash_destination(path: Path, chat_id: str, action_id: str) -> Path:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        relative = Path("_external_sources") / path.name
    return TRASH / f"{action_id}__{chat_id[:12]}" / relative


def move_or_delete(path: Path, mode: str, chat_id: str, action_id: str) -> dict:
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if mode == "bin":
        destination = trash_destination(path, chat_id, action_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        return {"path": str(path), "status": "moved_to_bin", "bin_path": str(destination)}
    path.unlink()
    return {"path": str(path), "status": "deleted"}


def exclusive_asset_ids(chat_id: str) -> list[str]:
    ids = ASSETS_BY_CHAT.get(chat_id) or []
    exclusive = []
    for asset_id in ids:
        refs = CHATS_BY_ASSET.get(asset_id) or []
        if refs == [chat_id] or set(refs) <= {chat_id}:
            exclusive.append(asset_id)
    return exclusive


def delete_or_bin_chat(chat_id: str, mode: str, include_raw_asset_sources: bool = False) -> dict:
    if mode not in {"bin", "delete"}:
        raise ViewerActionError("Mode must be bin or delete.")
    if mode == "delete" and not ALLOW_PERMANENT_DELETE:
        raise ViewerActionError(
            "Permanent deletion is disabled. Set CHATGPT_VIEWER_ALLOW_PERMANENT_DELETE=1 to enable it."
        )
    summary = SUMMARY_BY_ID.get(chat_id)
    if not summary:
        raise ViewerActionError("Conversation not found.")
    action_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    changed = []
    skipped = []
    for path_value in [summary.json_path, summary.markdown_path]:
        if path_value:
            path = Path(path_value).expanduser().resolve()
            if ROOT.resolve() not in path.parents:
                skipped.append({"path": str(path), "reason": "outside clean archive"})
                continue
            changed.append(move_or_delete(path, mode, chat_id, action_id))

    for asset_id in ASSETS_BY_CHAT.get(chat_id) or []:
        record = asset_record(asset_id)
        if not record:
            skipped.append({"asset_id": asset_id, "reason": "asset metadata missing"})
            continue
        refs = CHATS_BY_ASSET.get(asset_id) or []
        if asset_id not in exclusive_asset_ids(chat_id):
            skipped.append({"asset_id": asset_id, "reason": f"shared with {len(refs)} chats"})
            continue
        for key in ["clean_path", "original_dat"]:
            if key == "original_dat" and not include_raw_asset_sources:
                continue
            path_value = record.get(key)
            if not path_value:
                continue
            path = Path(rebase_clean_path(path_value)).resolve()
            if ROOT.resolve() not in path.parents:
                skipped.append({"asset_id": asset_id, "reason": "asset outside clean archive"})
                continue
            if not path.exists():
                changed.append({"path": str(path), "status": "missing"})
                continue
            changed.append(move_or_delete(path, mode, chat_id, action_id))

    SUMMARIES[:] = [row for row in SUMMARIES if row.chat_id != chat_id]
    SUMMARY_BY_ID.pop(chat_id, None)
    log = {
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "chat_id": chat_id,
        "mode": mode,
        "changed": changed,
        "skipped": skipped,
        "note": "Index files are not rewritten immediately; rebuild the clean archive for permanent index cleanup.",
    }
    TRASH.mkdir(exist_ok=True)
    with (TRASH / "deletion_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log, ensure_ascii=False) + "\n")
    return log


class Handler(BaseHTTPRequestHandler):
    server_version = "ChatExportViewer/1.0"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'",
        )
        super().end_headers()

    def is_local_host(self) -> bool:
        return urlparse("http://" + self.headers.get("Host", "")).hostname in {"127.0.0.1", "localhost"}

    def do_HEAD(self) -> None:
        if not self.is_local_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/asset/"):
            asset_id = unquote(parsed.path.removeprefix("/api/asset/"))
            record = asset_record(asset_id)
            if not record:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            path = Path(rebase_clean_path(record.get("clean_path")))
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = record.get("detected_mime") or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        path = VIEWER / "index.html" if parsed.path == "/" else (VIEWER / unquote(parsed.path.lstrip("/"))).resolve()
        if VIEWER.resolve() not in path.parents and path != VIEWER.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()

    def do_GET(self) -> None:
        if not self.is_local_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/accounts":
            self.send_json(account_status())
            return
        if parsed.path == "/api/conversations":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            limit = min(max(int(params.get("limit", ["80"])[0]), 1), 300)
            offset = max(int(params.get("offset", ["0"])[0]), 0)
            titles_only = params.get("titles_only", ["0"])[0].casefold() in {"1", "true", "yes", "on"}
            semantic = params.get("semantic", ["0"])[0].casefold() in {"1", "true", "yes", "on"}
            try:
                if semantic and query.strip():
                    self.send_json(semantic_search_conversations(query, limit, offset))
                else:
                    self.send_json(search_conversations(query, limit, offset, titles_only))
            except SemanticSearchError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if parsed.path.startswith("/api/conversation/"):
            chat_id = unquote(parsed.path.removeprefix("/api/conversation/"))
            try:
                self.send_json(parse_conversation(chat_id))
            except FileNotFoundError:
                self.send_json({"error": "Conversation not found"}, HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/asset/"):
            asset_id = unquote(parsed.path.removeprefix("/api/asset/"))
            record = asset_record(asset_id)
            if not record:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.serve_file(Path(rebase_clean_path(record.get("clean_path"))))
            return
        if parsed.path == "/":
            self.serve_file(VIEWER / "index.html")
            return
        requested = (VIEWER / unquote(parsed.path.lstrip("/"))).resolve()
        if VIEWER.resolve() not in requested.parents and requested != VIEWER.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.serve_file(requested)

    def do_POST(self) -> None:
        if not self.is_local_host():
            self.send_json({"error": "Only local requests are allowed"}, HTTPStatus.FORBIDDEN)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json({"error": "JSON requests are required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlparse(origin)
            if parsed_origin.hostname not in {"127.0.0.1", "localhost"} or parsed_origin.netloc != self.headers.get("Host"):
                self.send_json({"error": "Cross-origin requests are not allowed"}, HTTPStatus.FORBIDDEN)
                return
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            if parsed.path == "/api/reveal":
                reveal_path(str(body.get("path") or ""))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/account":
                self.send_json(set_active_account(str(body.get("slug") or "")))
                return
            if parsed.path.startswith("/api/conversation/") and parsed.path.endswith("/delete"):
                chat_id = unquote(parsed.path.removeprefix("/api/conversation/").removesuffix("/delete"))
                mode = str(body.get("mode") or "bin")
                include_raw = bool(body.get("include_raw_asset_sources", False))
                self.send_json(delete_or_bin_chat(chat_id, mode, include_raw))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ViewerActionError, subprocess.CalledProcessError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def find_port(start: int) -> int:
    port = start
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                port += 1
                continue
            return port


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    port = find_port(args.port)
    server = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Chat export viewer running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
