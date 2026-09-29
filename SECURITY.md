# Security

This is a single-user local application. The HTTP viewer listens on loopback and is not designed for public hosting or multiple users. Do not expose its port through a reverse proxy or tunnel.

The viewer can move conversation files and exclusively referenced derived assets into a local bin. Permanent deletion is disabled by default. To opt in, set `CHATGPT_VIEWER_ALLOW_PERMANENT_DELETE=1` before starting the viewer. Raw export assets are excluded from deletion by the UI.

ZIP entries are checked for traversal paths and symlinks before extraction. Imports are built in a temporary account folder and swapped into place after processing succeeds. Keep backups of irreplaceable exports; this tool is not a backup system.

Do not open untrusted export content in a privileged browsing context. Report security issues privately to the repository maintainer rather than publishing exploit details in an issue.
