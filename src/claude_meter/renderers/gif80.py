"""80x80 JPEG that lives in the GeeKmagic clock's Customization-GIF slot.

Fits alongside the device's stock clock + weather display. Percentages
shown as numbers with thin progress bars under each row.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from claude_meter.renderers import (
    COLOR_BG, COLOR_DIM, COLOR_TRACK, bar_color, load_font,
)

DISPLAY_SIZE = (80, 80)

# JFIF APP0 segment from the vendor converter's output (96x96 DPI density).
# Firmware silently rejects frames using Pillow's default (0x00 01 01 00).
APP0_BYTES = bytes.fromhex("ffe000104a46494600010101006000600000")

# Baseline JPEG quantization tables extracted from converter output.
# The hardware decoder on this device only accepts these values.
LUMA_QTABLE = [
    3, 2, 2, 3, 2, 2, 3, 3, 3, 3, 4, 3, 3, 4, 5, 8,
    5, 5, 4, 4, 5, 10, 7, 7, 6, 8, 12, 10, 12, 12, 11, 10,
    11, 11, 13, 14, 18, 16, 13, 14, 17, 14, 11, 11, 16, 22, 16, 17,
    19, 20, 21, 21, 21, 12, 15, 23, 24, 22, 20, 24, 18, 20, 21, 20,
]
CHROMA_QTABLE = [
    3, 4, 4, 5, 4, 5, 9, 5, 5, 9, 20, 13, 11, 13, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
]


class Gif80Renderer:
    """Renders an 80x80 JPEG frame (no container wrapping — transport does that)."""

    def render(self, five_pct: float, five_reset: str,
               week_pct: float, week_reset: str) -> bytes:
        img  = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)
        draw = ImageDraw.Draw(img)

        font_lbl = load_font(12)
        font_pct = load_font(20)

        def draw_row(y: int, label: str, pct: float):
            pct_clamped = max(0.0, min(pct, 999.0))
            bar_pct     = min(pct_clamped, 100.0)
            color       = bar_color(pct_clamped)
            pct_text    = f"{pct_clamped:.0f}%"

            draw.text((4, y), label, font=font_lbl, fill=COLOR_DIM)
            pct_w = int(font_pct.getlength(pct_text))
            draw.text((76 - pct_w, y - 2), pct_text, font=font_pct, fill=color)

            bar_y = y + 22
            draw.rectangle([4, bar_y, 76, bar_y + 6], fill=COLOR_TRACK)
            filled = int(72 * bar_pct / 100)
            if filled > 0:
                draw.rectangle([4, bar_y, 4 + filled, bar_y + 6], fill=color)

        draw_row(2,  "5h", five_pct)
        draw_row(42, "7d", week_pct)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", qtables=[LUMA_QTABLE, CHROMA_QTABLE], subsampling=2)
        frame = buf.getvalue()
        # Patch APP0 (bytes [2..20]) to the firmware-expected density.
        return frame[:2] + APP0_BYTES + frame[20:]
