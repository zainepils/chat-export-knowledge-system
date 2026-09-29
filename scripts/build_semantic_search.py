#!/usr/bin/env python3
"""
Build a local semantic search index for the cleaned ChatGPT export.

This creates one embedding per conversation. It is designed for the first
retrieval step: finding the right chat by meaning, then opening the full chat in
the viewer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("CHATGPT_CLEAN_ROOT", PROJECT_ROOT / "runtime" / "accounts" / "default"))
INDEXES = ROOT / "indexes"
SEMANTIC = ROOT / "semantic_search"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_MAX_BODY_CHARS = 1500


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def compose_embedding_text(row: dict, max_body_chars: int) -> str:
    title = clean_text(row.get("title") or "Untitled")
    first_user = clean_text(row.get("first_user_message"))
    last_user = clean_text(row.get("last_user_message"))
    attachments = ", ".join(row.get("attachment_names") or [])
    body = clean_text(row.get("searchable_text"))[:max_body_chars]
    return "\n".join(
        part
        for part in [
            f"Title: {title}",
            f"Title again for importance: {title}",
            f"First user message: {first_user}",
            f"Last user message: {last_user}",
            f"Attachment names: {attachments}" if attachments else "",
            f"Conversation text: {body}",
        ]
        if part
    )


def load_rows() -> list[dict]:
    rows: list[dict] = []
    with (INDEXES / "master_search.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            rows.append(row)
    return rows


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def signature(record: dict) -> tuple:
    return (
        record.get("title") or "Untitled",
        record.get("updated_at") or "",
        int(record.get("message_count") or 0),
        tuple(record.get("asset_ids") or []),
        tuple(record.get("attachment_names") or []),
        record.get("first_user_message") or "",
        record.get("last_user_message") or "",
    )


def load_existing_index(model_name: str, max_body_chars: int) -> dict[str, tuple[dict, np.ndarray]]:
    manifest_path = SEMANTIC / "semantic_manifest.json"
    records_path = SEMANTIC / "chat_records.jsonl"
    embeddings_path = SEMANTIC / "chat_embeddings.npy"
    if not (manifest_path.exists() and records_path.exists() and embeddings_path.exists()):
        return {}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if manifest.get("model_name") != model_name or int(manifest.get("max_body_chars") or 0) != max_body_chars:
        return {}

    records = []
    with records_path.open("r", encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    vectors = np.load(embeddings_path)
    if len(records) != len(vectors):
        return {}

    existing: dict[str, tuple[dict, np.ndarray]] = {}
    for record, vector in zip(records, vectors, strict=True):
        chat_id = record.get("chat_id")
        if chat_id:
            existing[str(chat_id)] = (record, vector)
    return existing


def build(model_name: str, batch_size: int, max_body_chars: int, rebuild_all: bool = False) -> None:
    SEMANTIC.mkdir(exist_ok=True)
    rows = load_rows()
    records = []
    documents = []
    for index, row in enumerate(rows):
        document = compose_embedding_text(row, max_body_chars)
        digest = content_hash(document)
        records.append(
            {
                "index": index,
                "chat_id": row.get("chat_id"),
                "title": row.get("title") or "Untitled",
                "created_at": row.get("created_at") or "",
                "updated_at": row.get("updated_at") or "",
                "message_count": int(row.get("message_count") or 0),
                "asset_ids": row.get("asset_ids") or [],
                "attachment_names": row.get("attachment_names") or [],
                "first_user_message": row.get("first_user_message") or "",
                "last_user_message": row.get("last_user_message") or "",
                "content_hash": digest,
            }
        )
        documents.append(document)

    existing = {} if rebuild_all else load_existing_index(model_name, max_body_chars)
    vectors_list: list[np.ndarray | None] = [None] * len(records)
    docs_to_embed = []
    positions_to_embed = []
    reused = 0
    changed = 0
    for index, record in enumerate(records):
        chat_id = str(record.get("chat_id") or "")
        old = existing.get(chat_id)
        if old:
            old_record, old_vector = old
            old_hash = old_record.get("content_hash")
            reusable = old_hash == record["content_hash"] if old_hash else signature(old_record) == signature(record)
            if reusable:
                vectors_list[index] = old_vector
                reused += 1
                continue
            changed += 1
        docs_to_embed.append(documents[index])
        positions_to_embed.append(index)

    if docs_to_embed:
        model = TextEmbedding(model_name=model_name)
        for embedded_count, vector in enumerate(model.embed(docs_to_embed, batch_size=batch_size), start=1):
            vectors_list[positions_to_embed[embedded_count - 1]] = vector
            if embedded_count % 250 == 0 or embedded_count == len(docs_to_embed):
                print(
                    f"Embedded {embedded_count:,}/{len(docs_to_embed):,} new or changed conversations "
                    f"({reused:,} reused)",
                    flush=True,
                )
    else:
        print(f"Reused all {reused:,} existing conversation embeddings", flush=True)

    if any(vector is None for vector in vectors_list):
        raise RuntimeError("Internal error: some conversations did not receive an embedding")

    vectors = np.array(vectors_list, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.clip(norms, 1e-12, None)

    np.save(SEMANTIC / "chat_embeddings.npy", vectors)
    with (SEMANTIC / "chat_records.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model_name": model_name,
        "embedding_count": len(records),
        "dimensions": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
        "source": "indexes/master_search.jsonl",
        "max_body_chars": max_body_chars,
        "reused_embeddings": reused,
        "new_or_changed_embeddings": len(docs_to_embed),
        "changed_existing_embeddings": changed,
        "files": {
            "embeddings": "semantic_search/chat_embeddings.npy",
            "records": "semantic_search/chat_records.jsonl",
        },
        "notes": [
            "One vector per conversation.",
            "Titles and first/last user messages are intentionally weighted higher than full body text.",
            "Use this to find likely chats, then open the full conversation in the viewer.",
        ],
    }
    (SEMANTIC / "semantic_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (SEMANTIC / "README.md").write_text(
        "\n".join(
            [
                "# Semantic Search Index",
                "",
                "This folder contains a local embeddings index for chat-level semantic search.",
                "",
                f"- Model: `{model_name}`",
                f"- Conversations indexed: `{len(records)}`",
                f"- Dimensions: `{manifest['dimensions']}`",
                f"- Reused embeddings from previous build: `{reused}`",
                f"- New or changed embeddings built: `{len(docs_to_embed)}`",
                "",
                "Rebuild with:",
                "",
                "```bash",
                "./.venv/bin/python scripts/build_semantic_search.py",
                "```",
                "",
                "The viewer uses this index when Semantic search is enabled.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS)
    parser.add_argument("--rebuild-all", action="store_true", help="Ignore any existing semantic index and embed every chat.")
    args = parser.parse_args()
    build(args.model, args.batch_size, args.max_body_chars, args.rebuild_all)


if __name__ == "__main__":
    main()
