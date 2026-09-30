# macOS Release

The release build packages the local Python viewer, WebKit wrapper, importer, and search dependencies into one Apple Silicon app. It contains code and viewer assets only. Chat exports, indexes, and model downloads remain outside the app in the user's Application Support folder.

## Build

On an Apple Silicon Mac with Python 3.11 or newer:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[macos,build-macos]'
./scripts/build_macos_release.sh
```

The output is `release-build/ChatGPT-Export-Viewer-0.1.0-macos-arm64.zip` with a matching SHA-256 file. The build script validates the app metadata and code signature before packaging. `release-build/` is Git-ignored.

## Test with fictional data

```bash
./.venv/bin/python examples/make_demo_export.py runtime/release-qa-exports
CHATGPT_EXPORT_DATA_ROOT="$PWD/runtime/release-qa-data" \
  'release-build/dist/ChatGPT Export Viewer.app/Contents/MacOS/ChatGPT Export Viewer' \
  --import-export "$PWD/runtime/release-qa-exports/studio" \
  --email studio@example.invalid --skip-semantic
```

Then open the app with the same `CHATGPT_EXPORT_DATA_ROOT` setting, or import the fictional folder as a ZIP through the app. The command-line import path exists for verification and does not start the GUI. Before publishing a release, inspect the ZIP contents and test the extracted app on another Mac.

## Distribution limits

This ZIP is arm64-only. It is ad-hoc signed, not signed with an Apple Developer ID or notarized, so macOS Gatekeeper may require the recipient to approve opening it. A frictionless public download requires Developer ID signing and notarization. Do not bundle real exports, screenshots, model caches, account registries, or personal configuration.
