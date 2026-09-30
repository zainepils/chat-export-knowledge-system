#!/bin/zsh
set -euo pipefail

source_root="${0:A:h:h}"
build_root="${CHATGPT_EXPORT_BUILD_ROOT:-$source_root/release-build}"
mkdir -p "$build_root"
export PYINSTALLER_CONFIG_DIR="$build_root/pyinstaller-cache"

"$source_root/.venv/bin/python" -m PyInstaller \
  --noconfirm \
  --distpath "$build_root/dist" \
  --workpath "$build_root/build" \
  "$source_root/scripts/mac_release.spec"

app="$build_root/dist/ChatGPT Export Viewer.app"
plutil -lint "$app/Contents/Info.plist"
codesign --verify --deep --strict "$app"

release="$build_root/ChatGPT-Export-Viewer-0.1.0-macos-arm64.zip"
ditto -c -k --sequesterRsrc --keepParent "$app" "$release"
shasum -a 256 "$release" > "$release.sha256"
print "$release"
