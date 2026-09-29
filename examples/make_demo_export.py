#!/usr/bin/env python3
"""Create two entirely fictional ChatGPT-style export folders for demos/tests."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path


def dashboard_png() -> bytes:
    width, height = 640, 360
    pixels = bytearray((248, 250, 249) * (width * height))

    def rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * width + x0) * 3
            pixels[start:start + len(row)] = row

    rect(0, 0, width, 40, (29, 42, 44))
    rect(18, 15, 154, 21, (230, 244, 237))
    for x, color in ((24, (62, 160, 136)), (230, (233, 176, 78)), (436, (75, 120, 167))):
        rect(x, 62, x + 180, 142, (255, 255, 255))
        rect(x + 14, 78, x + 116, 85, (208, 218, 218))
        rect(x + 14, 101, x + 82, 119, color)
    rect(24, 164, 405, 336, (255, 255, 255))
    rect(43, 182, 209, 190, (68, 83, 84))
    for i, bar_height in enumerate((32, 67, 48, 92, 71, 111, 87)):
        x = 51 + i * 47
        rect(x, 315 - bar_height, x + 28, 315, (58, 158, 134))
    rect(425, 164, 616, 336, (255, 255, 255))
    rect(443, 183, 567, 191, (68, 83, 84))
    for i, size in enumerate((139, 114, 157, 92)):
        y = 215 + i * 27
        rect(443, y, 443 + size, y + 10, (219, 231, 227))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def simple_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length 78 >>\nstream\nBT /F1 22 Tf 72 700 Td (Fictional customer research notes) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)


def message(node_id: str, parent: str | None, role: str, text: str, timestamp: int, attachment: str = "") -> dict:
    parts: list[object] = [text]
    if attachment:
        parts.append({"content_type": "image_asset_pointer", "asset_pointer": f"sediment://{attachment}"})
    return {
        "id": node_id,
        "parent": parent,
        "children": [],
        "message": {
            "id": node_id,
            "author": {"role": role},
            "create_time": timestamp,
            "content": {"content_type": "multimodal_text", "parts": parts},
        },
    }


def conversation(chat_id: str, title: str, question: str, answer: str, timestamp: int, attachment: str = "") -> dict:
    first = f"{chat_id}-user"
    second = f"{chat_id}-assistant"
    user = message(first, None, "user", question, timestamp, attachment)
    assistant = message(second, first, "assistant", answer, timestamp + 20)
    user["children"] = [second]
    return {
        "id": chat_id,
        "conversation_id": chat_id,
        "title": title,
        "create_time": timestamp,
        "update_time": timestamp + 20,
        "current_node": second,
        "is_archived": False,
        "is_starred": False,
        "mapping": {first: user, second: assistant},
    }


def create(root: Path) -> None:
    samples = {
        "studio": [
            conversation(
                "demo-studio-plan", "Studio launch plan",
                "How should a small design studio track client enquiries and decisions?",
                "## Plan\n\n- Record each enquiry in a shared sheet.\n- Review weekly conversion and response time.\n- Keep a decision log.\n\n**Next step:** test this with three fictional clients.",
                1767225600,
            ),
            conversation(
                "demo-dashboard", "Operations dashboard sketch",
                "Can you help me plan an operations dashboard from this sketch?",
                "The dashboard can show enquiries, conversion, and open follow-ups. The uploaded sketch is a visual reference.",
                1767312000,
                "file_demo123",
            ),
            conversation(
                "demo-research", "Customer research notes",
                "Summarise these fictional customer interviews and make a reusable research template.",
                "Use sections for customer goal, current workaround, buying trigger, and unresolved questions.",
                1767398400,
                "file_report123",
            ),
        ],
        "workshop": [
            conversation(
                "demo-workshop", "Workshop booking workflow",
                "How can we reduce missed bookings for community workshops?",
                "Try a simple booking checklist, confirmation message, and attendance review after each event.",
                1767484800,
            ),
            conversation(
                "demo-budget", "Event budget review",
                "What should we include in a simple workshop event budget?",
                "Track room hire, supplies, promotion, ticket revenue, and contingency in a CSV.",
                1767571200,
                "file_budget123",
            ),
        ],
    }
    for account, chats in samples.items():
        destination = root / account
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "conversations-000.json").write_text(json.dumps(chats, indent=2) + "\n", encoding="utf-8")
        (destination / "shared_conversations.json").write_text(
            json.dumps([{"id": f"share-{account}", "conversation_id": chats[0]["id"], "title": chats[0]["title"]}], indent=2) + "\n",
            encoding="utf-8",
        )
    (root / "studio" / "file_demo123.dat").write_bytes(dashboard_png())
    (root / "studio" / "file_report123.dat").write_bytes(simple_pdf())
    (root / "workshop" / "file_budget123.dat").write_text("item,cost\nroom,50\nsupplies,20\n", encoding="utf-8")
    (root / "studio" / "conversation_asset_file_names.json").write_text(
        json.dumps({"file_demo123.dat": "dashboard-sketch.png", "file_report123.dat": "customer-research.pdf"}, indent=2) + "\n", encoding="utf-8"
    )
    (root / "workshop" / "conversation_asset_file_names.json").write_text(
        json.dumps({"file_budget123.dat": "event-budget.csv"}, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    create(parser.parse_args().output)
