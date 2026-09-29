#!/usr/bin/env python3
"""
Import a new ChatGPT data export into the clean multi-account archive.

This is the one-command wrapper around the older manual workflow:

1. Accept a ChatGPT export zip or an already-extracted export folder.
2. Extract/copy the raw export into the selected account folder.
3. Rebuild per-chat JSON/Markdown, assets, indexes, and reports.
4. Build semantic search unless disabled.
5. Register/update the account in accounts/accounts.json.
6. Optionally refresh the macOS app data copy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(os.environ.get("CHATGPT_EXPORT_DATA_ROOT", SOURCE_ROOT / "runtime"))
SCRIPTS_DIR = SOURCE_ROOT / "scripts"
REBUILD_SCRIPT = SCRIPTS_DIR / "rebuild_clean_archive.py"
SEMANTIC_SCRIPT = SCRIPTS_DIR / "build_semantic_search.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "chatgpt_account"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env)


def account_root_for(project_root: Path, slug: str) -> Path:
    return project_root / "accounts" / slug


def has_existing_clean_archive(account_root: Path) -> bool:
    expected = [
        account_root / "indexes" / "chat_manifest.json",
        account_root / "indexes" / "master_search.jsonl",
        account_root / "conversations" / "json",
    ]
    return any(path.exists() for path in expected)


def extract_zip(zip_path: Path, target: Path, *, force: bool) -> Path:
    raw_root = target / "raw_export"
    if raw_root.exists():
        if not force:
            raise SystemExit(f"Raw export already exists: {raw_root}\nUse --force to replace it.")
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)

    source_dir = target / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(zip_path, source_dir / zip_path.name)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                destination = (raw_root / info.filename).resolve()
                if destination != raw_root.resolve() and raw_root.resolve() not in destination.parents:
                    raise SystemExit("Export ZIP contains a path outside the extraction directory.")
                mode = info.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise SystemExit("Export ZIP contains a symlink, which is not supported.")
            zf.extractall(raw_root)
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"Could not read zip file: {zip_path}\n{exc}") from exc

    located = locate_raw_export_root(raw_root)
    if located != raw_root:
        normalized = target / "raw_export_normalized"
        if normalized.exists():
            shutil.rmtree(normalized)
        shutil.copytree(located, normalized)
        return normalized
    return raw_root


def copy_raw_folder(folder: Path, target: Path, *, force: bool) -> Path:
    raw_root = target / "raw_export"
    if raw_root.exists():
        if not force:
            raise SystemExit(f"Raw export already exists: {raw_root}\nUse --force to replace it.")
        shutil.rmtree(raw_root)
    shutil.copytree(folder, raw_root)
    return locate_raw_export_root(raw_root)


def locate_raw_export_root(root: Path) -> Path:
    if list(root.glob("conversations-*.json")):
        return root
    candidates = [path for path in root.rglob("conversations-*.json") if path.is_file()]
    if not candidates:
        raise SystemExit(f"No conversations-*.json files found under: {root}")
    counts: dict[Path, int] = {}
    for path in candidates:
        counts[path.parent] = counts.get(path.parent, 0) + 1
    return max(counts, key=counts.get)


def choose_python() -> str:
    for candidate in [SOURCE_ROOT / ".venv" / "bin" / "python"]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def rebuild_archive(raw_root: Path, account_root: Path, python: str) -> None:
    env = os.environ.copy()
    env["CHATGPT_RAW_EXPORT"] = str(raw_root)
    env["CHATGPT_CLEAN_ROOT"] = str(account_root)
    run([python, str(REBUILD_SCRIPT)], env=env)


def build_semantic(account_root: Path, python: str) -> None:
    env = os.environ.copy()
    env["CHATGPT_CLEAN_ROOT"] = str(account_root)
    run([python, str(SEMANTIC_SCRIPT)], env=env)


def update_account_registry(registry_path: Path, email: str, label: str, slug: str, root: Path, status: str) -> None:
    registry = load_json(registry_path, {"active": slug, "accounts": []})
    accounts = registry.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    entry = {
        "slug": slug,
        "email": email,
        "label": label,
        "root": str(root),
        "status": status,
    }
    replaced = False
    for index, existing in enumerate(accounts):
        if existing.get("slug") == slug or str(existing.get("email", "")).lower() == email.lower():
            accounts[index] = {**existing, **entry}
            replaced = True
            break
    if not replaced:
        accounts.append(entry)
    registry["accounts"] = accounts
    registry["active"] = slug
    write_json(registry_path, registry)


def read_build_counts(account_root: Path) -> dict[str, Any]:
    counts = {
        "conversation_json_files": len(list((account_root / "conversations" / "json").glob("*.json"))),
        "conversation_markdown_files": len(list((account_root / "conversations" / "markdown").glob("*.md"))),
        "asset_files": len(list((account_root / "assets").glob("*"))) if (account_root / "assets").exists() else 0,
        "semantic_records": 0,
    }
    semantic_records = account_root / "semantic_search" / "chat_records.jsonl"
    if semantic_records.exists():
        with semantic_records.open("r", encoding="utf-8") as f:
            counts["semantic_records"] = sum(1 for _ in f)
    return counts


def write_import_report(
    account_root: Path,
    *,
    email: str,
    slug: str,
    input_path: Path,
    raw_root: Path,
    semantic_built: bool,
) -> None:
    counts = read_build_counts(account_root)
    lines = [
        "# Import Report",
        "",
        f"- Imported at: `{utc_now()}`",
        f"- Account: `{email}`",
        f"- Slug: `{slug}`",
        f"- Input: `{input_path}`",
        f"- Raw export used: `{raw_root}`",
        f"- Clean root: `{account_root}`",
        f"- Semantic search built: `{semantic_built}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in counts.items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    (account_root / "reports").mkdir(parents=True, exist_ok=True)
    (account_root / "reports" / "import_report.md").write_text("\n".join(lines), encoding="utf-8")


def write_account_readme(account_root: Path, email: str, status: str) -> None:
    lines = [
        f"# {email}",
        "",
        "This is a cleaned ChatGPT export account root managed by `scripts/import_chatgpt_export.py`.",
        "",
        f"- Status: `{status}`",
        f"- Last updated: `{utc_now()}`",
        "",
        "Important folders:",
        "",
        "- `raw_export/` or `raw_export_normalized/` - copied raw export input.",
        "- `conversations/` - one JSON and one Markdown file per chat.",
        "- `assets/` - copied and renamed uploaded/exported assets.",
        "- `indexes/` - search indexes used by Codex and the viewer.",
        "- `semantic_search/` - local meaning-search vectors.",
        "- `reports/import_report.md` - latest import summary.",
        "",
    ]
    (account_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a ChatGPT export zip/folder into the clean archive.")
    parser.add_argument("input", type=Path, help="Path to a ChatGPT export .zip or extracted folder.")
    parser.add_argument("--email", required=True, help="Account email this export belongs to.")
    parser.add_argument("--label", help="Display label in the app. Defaults to the email.")
    parser.add_argument("--slug", help="Stable account slug. Defaults to a safe version of the email.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--force", action="store_true", help="Replace existing generated/raw files for this account.")
    parser.add_argument("--skip-semantic", action="store_true", help="Skip semantic search build.")
    parser.add_argument(
        "--root",
        type=Path,
        help="Override clean account root. Advanced use; normally the importer chooses this from the email/slug.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    project_root = args.project_root.expanduser().resolve()
    email = args.email.strip()
    label = args.label or email
    slug = slugify(args.slug or email)
    account_root = args.root.expanduser().resolve() if args.root else account_root_for(project_root, slug)

    if has_existing_clean_archive(account_root) and not args.force:
        raise SystemExit(
            f"Clean archive already exists for this account: {account_root}\n"
            "Use --force when you intentionally want to rebuild/replace it."
        )

    account_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{slug}-build-", dir=account_root.parent) as temporary:
        staged = Path(temporary) / "archive"
        staged.mkdir()
        if input_path.is_file():
            if input_path.suffix.lower() != ".zip":
                raise SystemExit("Input file must be a .zip export. For extracted exports, pass the folder.")
            raw_root = extract_zip(input_path, staged, force=False)
        else:
            raw_root = copy_raw_folder(input_path, staged, force=False)

        python = choose_python()
        rebuild_archive(raw_root, staged, python)

        semantic_built = False
        if not args.skip_semantic:
            # Reuse vectors for unchanged chats on a forced re-import.
            previous_semantic = account_root / "semantic_search"
            if previous_semantic.exists():
                shutil.copytree(previous_semantic, staged / "semantic_search")
            build_semantic(staged, python)
            semantic_built = True

        status = "ready" if semantic_built else "ready_without_semantic_search"
        write_account_readme(staged, email, status)
        write_import_report(
            staged, email=email, slug=slug, input_path=input_path,
            raw_root=account_root / raw_root.relative_to(staged), semantic_built=semantic_built,
        )
        backup = account_root.with_name(f".{account_root.name}-previous")
        if backup.exists():
            raise SystemExit(f"Previous import backup exists: {backup}")
        if account_root.exists():
            account_root.rename(backup)
        try:
            staged.rename(account_root)
        except Exception:
            if backup.exists():
                backup.rename(account_root)
            raise
        if backup.exists():
            shutil.rmtree(backup)

    update_account_registry(project_root / "accounts" / "accounts.json", email, label, slug, account_root, status)

    counts = read_build_counts(account_root)
    print(
        json.dumps(
            {
                "status": status,
                "email": email,
                "slug": slug,
                "account_root": str(account_root),
                "raw_export": str(account_root / "raw_export"),
                "counts": counts,
                "import_report": str(account_root / "reports" / "import_report.md"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
