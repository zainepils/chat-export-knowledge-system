#!/usr/bin/env python3
"""
Launch the ChatGPT export viewer as a local macOS app window.

The app reuses the same HTML/CSS/JS and Python API server as the browser
version, but starts the local server automatically and embeds it in a native
WebKit window via pywebview.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen


SOURCE_ROOT = Path(os.environ.get(
    "CHATGPT_EXPORT_SOURCE_ROOT",
    Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1],
))
DEFAULT_DATA_ROOT = (
    Path.home() / "Library" / "Application Support" / "ChatGPT Export Viewer" / "runtime"
    if getattr(sys, "frozen", False)
    else SOURCE_ROOT / "runtime"
)
DATA_ROOT = Path(os.environ.get("CHATGPT_EXPORT_DATA_ROOT", DEFAULT_DATA_ROOT))
os.environ["CHATGPT_EXPORT_SOURCE_ROOT"] = str(SOURCE_ROOT)
os.environ["CHATGPT_EXPORT_DATA_ROOT"] = str(DATA_ROOT)
SCRIPTS = SOURCE_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from serve_chat_viewer import Handler, ReusableThreadingHTTPServer, find_port  # noqa: E402


class AppAPI:
    def __init__(self) -> None:
        self.window = None
        self.import_lock = threading.Lock()

    def pick_export(self) -> str:
        import webview

        chosen = self.window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("ChatGPT export (*.zip)",)
        )
        return str(chosen[0]) if chosen else ""

    def import_export(self, path: str, email: str, label: str, force: bool, skip_semantic: bool) -> dict:
        from import_chatgpt_export import run_import

        if not self.import_lock.acquire(blocking=False):
            raise RuntimeError("An import is already running.")
        try:
            try:
                return run_import(argparse.Namespace(
                    input=Path(path), email=email, label=label or email, slug=None,
                    project_root=DATA_ROOT, root=None, force=force, skip_semantic=skip_semantic,
                ))
            except SystemExit as exc:
                raise RuntimeError(str(exc)) from exc
        finally:
            self.import_lock.release()


def start_server(preferred_port: int) -> tuple[ReusableThreadingHTTPServer, str]:
    port = find_port(preferred_port)
    server = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="chat-viewer-server", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    return server, url


def wait_until_ready(url: str, timeout_seconds: float = 8.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - startup polling
            last_error = exc
        time.sleep(0.15)
    raise RuntimeError(f"Viewer did not become ready at {url}: {last_error}")


def run_app(preferred_port: int, smoke_test: bool = False) -> None:
    server, url = start_server(preferred_port)
    try:
        wait_until_ready(url)
        if smoke_test:
            print(url)
            return

        import webview

        api = AppAPI()
        window = webview.create_window(
            "ChatGPT Export Viewer",
            url,
            js_api=api,
            width=1280,
            height=860,
            min_size=(980, 640),
            text_select=True,
        )
        api.window = window
        webview.start(gui="cocoa", debug=False)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--import-export", type=Path, help="Import an export without opening the app window.")
    parser.add_argument("--email", help="Account email for --import-export.")
    parser.add_argument("--label", help="Account label for --import-export.")
    parser.add_argument("--force", action="store_true", help="Replace an existing account archive.")
    parser.add_argument("--skip-semantic", action="store_true", help="Skip local embedding generation.")
    args = parser.parse_args()
    if args.import_export:
        if not args.email:
            parser.error("--email is required with --import-export")
        import json

        result = AppAPI().import_export(
            str(args.import_export), args.email, args.label or args.email,
            args.force, args.skip_semantic,
        )
        print(json.dumps(result, indent=2))
        return
    run_app(args.port, args.smoke_test)


if __name__ == "__main__":
    main()
