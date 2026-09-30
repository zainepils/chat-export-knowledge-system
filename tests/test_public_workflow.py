from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
IMPORTER = REPO / "scripts" / "import_chatgpt_export.py"
VIEWER = REPO / "scripts" / "serve_chat_viewer.py"
DEMO = REPO / "examples" / "make_demo_export.py"


def request(url: str, payload: dict | None = None) -> tuple[int, object]:
    raw = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"} if raw else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if response.headers.get_content_type() == "application/json" else body
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.loads(exc.read())


class PublicWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exports = self.root / "exports"
        self.data = self.root / "runtime"
        subprocess.run([sys.executable, str(DEMO), str(self.exports)], check=True)
        self.env = {**os.environ, "CHATGPT_EXPORT_DATA_ROOT": str(self.data)}

    def import_account(self, name: str, *options: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(IMPORTER), str(self.exports / name), "--email", f"{name}@example.invalid", "--skip-semantic", *options],
            env=self.env, text=True, capture_output=True,
        )

    def test_imports_are_isolated_and_portable(self) -> None:
        for name, expected in (("studio", 3), ("workshop", 2)):
            result = self.import_account(name)
            self.assertEqual(result.returncode, 0, result.stderr)
            account = self.data / "accounts" / f"{name}_example_invalid"
            manifest = json.loads((account / "indexes/chat_manifest.json").read_text())
            self.assertEqual(len(manifest), expected)
            self.assertTrue(all(not Path(row["json_path"]).is_absolute() for row in manifest.values()))
            self.assertEqual(len(list((account / "conversations/markdown").glob("*.md"))), expected)
            assets = json.loads((account / "indexes/assets_index.json").read_text())
            self.assertEqual(len(assets), 2 if name == "studio" else 1)
            self.assertTrue(all(asset["linked"] for asset in assets))
            self.assertTrue(all(not Path(asset["clean_path"]).is_absolute() for asset in assets))

    def test_failed_reimport_preserves_previous_archive(self) -> None:
        self.assertEqual(self.import_account("studio").returncode, 0)
        account = self.data / "accounts/studio_example_invalid"
        before = (account / "indexes/chat_manifest.json").read_bytes()
        (self.exports / "studio/conversations-000.json").write_text("invalid JSON", encoding="utf-8")
        failed = self.import_account("studio", "--force")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((account / "indexes/chat_manifest.json").read_bytes(), before)

    def test_zip_traversal_is_rejected(self) -> None:
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../outside.txt", "unsafe")
            zf.writestr("conversations-000.json", "[]")
        result = subprocess.run(
            [sys.executable, str(IMPORTER), str(archive), "--email", "unsafe@example.invalid", "--skip-semantic"],
            env=self.env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "outside.txt").exists())

    def test_conversation_id_cannot_escape_archive(self) -> None:
        shard = self.exports / "studio/conversations-000.json"
        chats = json.loads(shard.read_text())
        chats[0]["id"] = "../../outside"
        chats[0]["conversation_id"] = "../../outside"
        shard.write_text(json.dumps(chats), encoding="utf-8")
        self.assertEqual(self.import_account("studio").returncode, 0)
        files = list((self.data / "accounts/studio_example_invalid/conversations/json").glob("*.json"))
        self.assertEqual(len(files), 3)
        self.assertFalse((self.root / "outside.json").exists())

    def test_shared_asset_stays_when_one_chat_is_binned(self) -> None:
        shard = self.exports / "studio/conversations-000.json"
        chats = json.loads(shard.read_text())
        user_node = chats[0]["mapping"]["demo-studio-plan-user"]
        user_node["message"]["content"]["parts"].append(
            {"content_type": "image_asset_pointer", "asset_pointer": "sediment://file_demo123"}
        )
        shard.write_text(json.dumps(chats), encoding="utf-8")
        self.assertEqual(self.import_account("studio").returncode, 0)
        account = self.data / "accounts/studio_example_invalid"
        asset = next((account / "assets").glob("file_demo123*.png"))
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
             "import serve_chat_viewer as viewer; "
             "viewer.delete_or_bin_chat('demo-dashboard', 'bin')", str(REPO / "scripts")],
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(asset.exists())
        self.assertTrue((account / "raw_export/file_demo123.dat").exists())

    def test_bundled_import_path_uses_in_process_builders(self) -> None:
        command = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import chat_viewer_app; sys.frozen = True; "
            "result = chat_viewer_app.AppAPI().import_export(sys.argv[2], 'studio@example.invalid', "
            "'Studio', False, True); print(result['counts']['conversation_json_files'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", command, str(REPO / "scripts"), str(self.exports / "studio")],
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "3")

    def test_viewer_search_accounts_and_files(self) -> None:
        self.assertEqual(self.import_account("studio").returncode, 0)
        self.assertEqual(self.import_account("workshop").returncode, 0)
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, str(VIEWER), "--port", str(port)], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        self.addCleanup(lambda: (process.terminate(), process.wait(timeout=5)) if process.poll() is None else None)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                status, accounts = request(base + "/api/accounts")
                if status == 200:
                    break
            except (OSError, TimeoutError):
                time.sleep(0.05)
        else:
            self.fail("viewer did not start")
        self.assertEqual(len(accounts["accounts"]), 2)
        self.assertFalse(accounts["capabilities"]["permanent_delete"])
        status, found = request(base + "/api/conversations?q=workshop&titles_only=1")
        self.assertEqual(status, 200)
        self.assertEqual(found["total"], 1)
        status, switched = request(base + "/api/account", {"slug": "studio_example_invalid"})
        self.assertEqual(switched["active"], "studio_example_invalid")
        _, found = request(base + "/api/conversations?q=dashboard&titles_only=1")
        self.assertEqual(found["total"], 1)
        _, chat = request(base + "/api/conversation/demo-dashboard")
        self.assertEqual(chat["assets"][0]["filename"], "dashboard-sketch.png")
        self.assertTrue(chat["messages"][0]["attachments"][0]["resolved"])
        status, image = request(base + "/api/asset/file_demo123")
        self.assertEqual(status, 200)
        self.assertTrue(image.startswith(b"\x89PNG"))
        status, _ = request(base + "/api/reveal", {"path": "/etc/hosts"})
        self.assertEqual(status, 400)
        status, _ = request(base + "/api/conversation/demo-dashboard/delete", {"mode": "delete"})
        self.assertEqual(status, 400)
        bad_host = urllib.request.Request(base + "/api/accounts", headers={"Host": "example.invalid"})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(bad_host, timeout=5)
        self.assertEqual(rejected.exception.code, 403)
        rejected.exception.close()


if __name__ == "__main__":
    unittest.main()
