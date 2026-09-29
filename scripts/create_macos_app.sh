#!/bin/zsh
set -euo pipefail

source_root="${0:A:h:h}"
data_root="${CHATGPT_EXPORT_DATA_ROOT:-$source_root/runtime}"
app="$source_root/dist/ChatGPT Export Viewer.app"
contents="$app/Contents"
resources="$contents/Resources"
launcher="$contents/MacOS/ChatGPT Export Viewer"

mkdir -p "$contents/MacOS" "$resources/source/scripts" "$resources/source/chat_viewer"
cp "$source_root"/scripts/*.py "$resources/source/scripts/"
cp "$source_root"/chat_viewer/* "$resources/source/chat_viewer/"

cat > "$contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ChatGPT Export Viewer</string>
  <key>CFBundleIdentifier</key><string>org.local.chatgpt-export-viewer</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleExecutable</key><string>ChatGPT Export Viewer</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
PLIST

if [[ -f "$source_root/app_icon/ChatGPTExportViewer.icns" ]]; then
  cp "$source_root/app_icon/ChatGPTExportViewer.icns" "$resources/ChatGPTExportViewer.icns"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string ChatGPTExportViewer' "$contents/Info.plist"
fi

{
  print '#!/bin/zsh'
  print 'set -euo pipefail'
  print "export CHATGPT_EXPORT_DATA_ROOT=${(q)data_root}"
  print 'export CHATGPT_EXPORT_SOURCE_ROOT="${0:A:h:h}/Resources/source"'
  print "exec ${(q)source_root}/.venv/bin/python \"\$CHATGPT_EXPORT_SOURCE_ROOT/scripts/chat_viewer_app.py\" \"\$@\""
} > "$launcher"
chmod +x "$launcher"
print "$app"
