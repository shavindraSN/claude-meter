#!/usr/bin/env python3
"""
GeeKmagic clock push — full-screen (Photo mode) variant.

Sibling of clock_push.py: same OAuth flow + fetch_usage, but renders a
larger 240x240 layout (with reset countdowns) and uploads to file1.jpg
so the clock shows it full-screen.

Device setup required (on http://192.168.1.125/):
  - main-page "theme"      -> anything (Photo mode overrides it)
  - /photo page:
      "photo-switch"       -> ON
      "file1-switch"       -> ON   (others OFF so nothing cycles in)
"""
import datetime
import io
import json
import sys
import time

import requests
from PIL import Image, ImageDraw

import clock_push as cp  # reuse OAuth + fetch_usage + colour palette + _font

CLOCK_FILENAME    = "file1.jpg"
DISPLAY_SIZE      = (240, 240)
PUSH_INTERVAL_SEC = cp.PUSH_INTERVAL_SEC
FORCE_PUSH_SEC    = cp.FORCE_PUSH_SEC


def render_jpeg(five_pct: float, five_reset: str,
                week_pct: float, week_reset: str) -> bytes:
    img = Image.new("RGB", DISPLAY_SIZE, cp.COLOR_BG)
    draw = ImageDraw.Draw(img)

    font_title = cp._font(20)
    font_pct   = cp._font(34)
    font_small = cp._font(14)

    draw.text((12, 8), "Claude usage", font=font_title, fill=cp.COLOR_TEXT)

    def draw_section(y: int, label: str, pct: float, reset: str):
        pct_clamped = max(0.0, min(pct, 999.0))
        bar_pct     = min(pct_clamped, 100.0)
        color       = cp._bar_color(pct_clamped)

        draw.text((12, y), label, font=font_small, fill=cp.COLOR_DIM)
        pct_text = f"{pct_clamped:.0f}%"
        draw.text((216 - int(font_pct.getlength(pct_text)), y - 4),
                  pct_text, font=font_pct, fill=color)

        bar_x, bar_y, bar_w, bar_h = 12, y + 38, 216, 14
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=cp.COLOR_TRACK)
        filled = int(bar_w * bar_pct / 100)
        if filled > 0:
            draw.rectangle([bar_x, bar_y, bar_x + filled, bar_y + bar_h], fill=color)

        draw.text((12, bar_y + bar_h + 4), f"resets {reset}",
                  font=font_small, fill=cp.COLOR_DIM)

    draw_section(40,  "5h session", five_pct, five_reset)
    draw_section(140, "7d weekly",  week_pct, week_reset)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


_logged_once = False
_last_pushed = None
_last_push_ts = 0.0


def push_once():
    global _logged_once, _last_pushed, _last_push_ts
    data = cp.fetch_usage()
    if not _logged_once:
        print("API response:", json.dumps(data, indent=2), flush=True)
        _logged_once = True

    five = data.get("five_hour") or {}
    week = data.get("seven_day") or {}
    five_pct = float(five.get("utilization") or 0)
    week_pct = float(week.get("utilization") or 0)
    key = (int(round(five_pct)), int(round(week_pct)))

    now = time.time()
    if _last_pushed == key and (now - _last_push_ts) < FORCE_PUSH_SEC:
        print(f"{datetime.datetime.now().strftime('%H:%M:%S')} "
              f"5h {five_pct:.0f}%  7d {week_pct:.0f}%  unchanged, skipped (photo)",
              flush=True)
        return

    jpg = render_jpeg(
        five_pct, cp._format_reset(five.get("resets_at", "")),
        week_pct, cp._format_reset(week.get("resets_at", "")),
    )

    resp = requests.post(
        cp.CLOCK_URL,
        files={cp.CLOCK_FIELD: (CLOCK_FILENAME, jpg, "image/jpeg")},
        timeout=5,
    )
    resp.raise_for_status()
    _last_pushed = key
    _last_push_ts = now
    print(f"{datetime.datetime.now().strftime('%H:%M:%S')} "
          f"5h {five_pct:.0f}%  7d {week_pct:.0f}%  pushed {len(jpg)}B (photo)",
          flush=True)


def main():
    while True:
        try:
            push_once()
        except KeyboardInterrupt:
            print("bye", flush=True)
            sys.exit(0)
        except Exception as e:
            print(f"[warn] {type(e).__name__}: {e}", flush=True)
        time.sleep(PUSH_INTERVAL_SEC)


if __name__ == "__main__":
    main()
