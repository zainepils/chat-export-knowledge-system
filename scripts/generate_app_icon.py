#!/usr/bin/env python3
"""Generate a minimal macOS icon for the ChatGPT Export Viewer app."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "app_icon"
ICONSET = ICON_DIR / "ChatGPTExportViewer.iconset"
PNG = ICON_DIR / "ChatGPTExportViewer-1024.png"
ICNS = ICON_DIR / "ChatGPTExportViewer.icns"


def rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def make_base_icon() -> Image.Image:
    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = rounded_rect_mask(size, 224)

    background = Image.new("RGBA", (size, size), "#171a1f")
    draw = ImageDraw.Draw(background)
    for y in range(size):
        t = y / (size - 1)
        r = int(24 + 10 * t)
        g = int(27 + 18 * t)
        b = int(32 + 20 * t)
        draw.line((0, y, size, y), fill=(r, g, b, 255))

    image.paste(background, (0, 0), mask)
    draw = ImageDraw.Draw(image)

    # Subtle document stack.
    for offset, alpha in [(88, 52), (52, 78), (16, 255)]:
        x0, y0 = 236 - offset // 3, 230 + offset
        x1, y1 = 742 - offset // 3, 704 + offset
        fill = (248, 250, 247, alpha)
        outline = (255, 255, 255, min(alpha + 30, 255))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=56, fill=fill, outline=outline, width=5)

    # Chat lines.
    green = "#10a37f"
    muted = "#d9e7e1"
    draw.rounded_rectangle((304, 340, 646, 382), radius=21, fill=green)
    draw.rounded_rectangle((304, 432, 674, 474), radius=21, fill=muted)
    draw.rounded_rectangle((304, 524, 554, 566), radius=21, fill=muted)

    # Search lens.
    draw.ellipse((590, 574, 758, 742), outline=green, width=32)
    draw.line((722, 706, 812, 796), fill=green, width=34)

    # Tiny local base line.
    draw.rounded_rectangle((344, 816, 680, 844), radius=14, fill=(16, 163, 127, 210))

    # Soft highlight and shadow.
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    hdraw.ellipse((-180, -220, 800, 420), fill=(255, 255, 255, 28))
    image = Image.alpha_composite(image, highlight)

    shadow = Image.new("RGBA", (size + 80, size + 80), (0, 0, 0, 0))
    shadow_mask = rounded_rect_mask(size, 224).filter(ImageFilter.GaussianBlur(22))
    shadow.paste((0, 0, 0, 95), (40, 50), shadow_mask)
    shadow.paste(image, (40, 30), image)
    return shadow.crop((40, 30, 40 + size, 30 + size))


def save_iconset(base: Image.Image) -> None:
    ICON_DIR.mkdir(exist_ok=True)
    if ICONSET.exists():
        for item in ICONSET.iterdir():
            item.unlink()
    else:
        ICONSET.mkdir()
    base.save(PNG)
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for pixels, filename in sizes:
        resized = base.resize((pixels, pixels), Image.Resampling.LANCZOS)
        resized.save(ICONSET / filename)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)


def main() -> None:
    save_iconset(make_base_icon())
    print(ICNS)


if __name__ == "__main__":
    main()
