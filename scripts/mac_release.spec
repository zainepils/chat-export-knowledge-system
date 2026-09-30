# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).resolve().parent
model_data, model_binaries, model_imports = collect_all("fastembed")

analysis = Analysis(
    [str(root / "scripts" / "chat_viewer_app.py")],
    pathex=[str(root / "scripts")],
    binaries=model_binaries,
    datas=[(str(root / "chat_viewer"), "chat_viewer"), *model_data],
    hiddenimports=model_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ChatGPT Export Viewer",
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ChatGPT Export Viewer",
)
app = BUNDLE(
    collection,
    name="ChatGPT Export Viewer.app",
    icon=str(root / "app_icon" / "ChatGPTExportViewer.icns"),
    bundle_identifier="org.local.chatgpt-export-viewer",
    version="0.1.0",
)
