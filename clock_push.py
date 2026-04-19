#!/usr/bin/env python3
"""
GeeKmagic clock push — Claude Code usage display.

Reuses the Mac widget's OAuth flow to fetch Anthropic's pre-computed usage
percentages, renders them to a 240x240 JPEG, and POSTs to the clock every
PUSH_INTERVAL_SEC seconds.

Numbers match Claude app's Settings -> Usage because they come from the
same /api/oauth/usage endpoint the desktop app uses.
"""
import base64
import hashlib
import io
import json
import pathlib
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants — OAuth block shared with /Users/shavindra/Documents/claude-widget
# ---------------------------------------------------------------------------

COOKIES_DB   = pathlib.Path.home() / "Library/Application Support/Claude/Cookies"
CACHE_DIR    = pathlib.Path.home() / ".claude-widget"   # shared with widget
KEY_CACHE    = CACHE_DIR / "aes.key"
TOKEN_CACHE  = CACHE_DIR / "token.json"
USAGE_URL    = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL    = "https://api.anthropic.com/v1/oauth/token"
CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPE        = "user:inference user:file_upload user:profile"

# Clock-specific. The stock firmware accepts POST /upload with multipart
# field "imageFile". Filename picks which slot to overwrite:
#   - "gif.jpg"   -> main-screen "customization" GIF slot (our target).
#     Format is reverse-engineered from the GeeKmagic converter tool's
#     output:  [frame0 JPEG][2400-byte index block][frame1 JPEG]...[frameN-1]
#     Each frame is an 80x80 baseline JPEG using the specific qtables +
#     APP0 density the firmware expects. The index block is 200 slots x
#     12 bytes each; only the first N are populated. Record layout:
#        <u16 0x01ff> <u16 id> <u32 offset> <u32 size>
#     where record 0's `id` holds the total frame count and records 1..N-1
#     hold the frame index. N must be >= a device-specific minimum — the
#     real converter emits 33 frames and we match that. Offsets account
#     for the 2400-byte index block.
#   - "file1.jpg".."file5.jpg" -> Photo-mode slots (plain 240x240 JPEG)
# Max 1 MB per the device's JS check.
CLOCK_URL         = "http://192.168.1.125/upload"
CLOCK_FIELD       = "imageFile"
CLOCK_FILENAME    = "gif.jpg"
PUSH_INTERVAL_SEC = 30
# Re-push even when numbers are unchanged after this many seconds, so the
# display recovers if the device reboots or the flash entry gets evicted.
FORCE_PUSH_SEC    = 600
DISPLAY_SIZE      = (80, 80)
GIF_FRAME_COUNT   = 33
GIF_INDEX_SIZE    = 2400

# JFIF APP0 segment from the converter's output (96x96 DPI density).
# The firmware silently rejects frames that use Pillow's default (0x00 01 01 00).
_APP0_BYTES = bytes.fromhex("ffe000104a46494600010101006000600000")

# Baseline JPEG quantization tables extracted from the converter's output.
# Hardware JPEG decoders on this device appear to only accept these values.
_LUMA_QTABLE = [
    3, 2, 2, 3, 2, 2, 3, 3, 3, 3, 4, 3, 3, 4, 5, 8,
    5, 5, 4, 4, 5, 10, 7, 7, 6, 8, 12, 10, 12, 12, 11, 10,
    11, 11, 13, 14, 18, 16, 13, 14, 17, 14, 11, 11, 16, 22, 16, 17,
    19, 20, 21, 21, 21, 12, 15, 23, 24, 22, 20, 24, 18, 20, 21, 20,
]
_CHROMA_QTABLE = [
    3, 4, 4, 5, 4, 5, 9, 5, 5, 9, 20, 13, 11, 13, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
    20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
]

# Colours
COLOR_BG     = (0, 0, 0)
COLOR_TEXT   = (235, 235, 235)
COLOR_DIM    = (140, 140, 140)
COLOR_TRACK  = (40, 40, 40)
COLOR_GREEN  = (26, 166, 75)
COLOR_YELLOW = (228, 184, 26)
COLOR_RED    = (217, 58, 58)


# ---------------------------------------------------------------------------
# AES key (from Keychain, cached to disk — prompts only once ever)
# ---------------------------------------------------------------------------

def _get_aes_key() -> bytes:
    if KEY_CACHE.exists():
        key = KEY_CACHE.read_bytes()
        if len(key) == 16:
            return key
    for acct in ["Claude", "Claude Key"]:
        try:
            pw = subprocess.check_output(
                ["security", "find-generic-password", "-w", "-s", "Claude Safe Storage", "-a", acct],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            key = hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, dklen=16)
            CACHE_DIR.mkdir(exist_ok=True)
            KEY_CACHE.write_bytes(key)
            KEY_CACHE.chmod(0o600)
            return key
        except Exception:
            continue
    raise RuntimeError("Could not read Claude Safe Storage from Keychain")


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def _decrypt_cookie(enc: bytes, key: bytes) -> str:
    dec = (
        Cipher(algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend())
        .decryptor()
        .update(enc[3:])
    )
    raw = dec[:-dec[-1]]
    m = re.search(rb"[\x20-\x7e]{8,}", raw)
    return m.group(0).decode("ascii", "ignore").strip().lstrip("`") if m else ""


def _get_cookies() -> dict:
    key = _get_aes_key()
    conn = sqlite3.connect(str(COOKIES_DB))
    cur = conn.cursor()
    cur.execute("SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%claude%'")
    rows = cur.fetchall()
    conn.close()
    result = {}
    for name, enc in rows:
        val = _decrypt_cookie(bytes(enc), key)
        if val:
            result[name] = val
    return result


# ---------------------------------------------------------------------------
# OAuth flow (mirrors Claude desktop app exactly)
# ---------------------------------------------------------------------------

def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    state_bytes = secrets.token_bytes(32)
    state = "".join(alphabet[b % len(alphabet)] for b in state_bytes)
    return verifier, challenge, state


def _post(url: str, body: dict, session_key: str = "") -> dict:
    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if session_key:
        headers["Authorization"] = f"Bearer {session_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def _fresh_oauth_token(session_key: str, org_id: str) -> dict:
    verifier, challenge, state = _pkce()
    resp = _post(
        f"https://api.anthropic.com/v1/oauth/{org_id}/authorize",
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "organization_uuid": org_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        session_key=session_key,
    )
    redirect = resp.get("redirect_uri", "")
    code = re.search(r"code=([^&]+)", redirect).group(1)
    returned_state = (re.search(r"state=([^&]+)", redirect) or re.search("", "")).group(0) or state

    return _post(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "state": returned_state,
            "code_verifier": verifier,
            "expires_in": 28800,
        },
        session_key=session_key,
    )


def _refresh_oauth_token(refresh_token: str, session_key: str) -> dict:
    return _post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": SCOPE,
            "expires_in": 28800,
        },
        session_key=session_key,
    )


def get_access_token() -> str:
    cookies = _get_cookies()
    session_key = cookies.get("sessionKey", "")
    org_id = cookies.get("lastActiveOrg", "")

    if TOKEN_CACHE.exists():
        try:
            cached = json.loads(TOKEN_CACHE.read_text())
            if time.time() < cached.get("expires_at", 0) - 60:
                return cached["access_token"]
            result = _refresh_oauth_token(cached["refresh_token"], session_key)
            _save_token(result)
            return result["access_token"]
        except Exception:
            TOKEN_CACHE.unlink(missing_ok=True)

    result = _fresh_oauth_token(session_key, org_id)
    _save_token(result)
    return result["access_token"]


def _save_token(result: dict):
    CACHE_DIR.mkdir(exist_ok=True)
    data = {
        "access_token":  result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "expires_at":    time.time() + result.get("expires_in", 28800),
    }
    TOKEN_CACHE.write_text(json.dumps(data))
    TOKEN_CACHE.chmod(0o600)


# ---------------------------------------------------------------------------
# Usage API
# ---------------------------------------------------------------------------

def fetch_usage() -> dict:
    token = get_access_token()
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            TOKEN_CACHE.unlink(missing_ok=True)
            token = get_access_token()
            req2 = urllib.request.Request(
                USAGE_URL,
                headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
            )
            return json.loads(urllib.request.urlopen(req2, timeout=10).read())
        raise


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

import datetime  # noqa: E402  — kept near its only user


def _format_reset(resets_at_str: str) -> str:
    if not resets_at_str:
        return "unknown"
    try:
        dt = datetime.datetime.fromisoformat(resets_at_str)
        now = datetime.datetime.now(tz=dt.tzinfo)
        secs = int((dt - now).total_seconds())
        if secs <= 0:
            return "soon"
        if secs < 3600:
            return f"in {secs // 60}m"
        if secs < 86400:
            h, m = divmod(secs // 60, 60)
            return f"in {h}h {m}m" if m else f"in {h}h"
        return dt.astimezone().strftime("%a %-I:%M %p")
    except Exception:
        return "unknown"


def _bar_color(pct: float):
    if pct >= 90:
        return COLOR_RED
    if pct >= 70:
        return COLOR_YELLOW
    return COLOR_GREEN


def _font(size: int) -> ImageFont.ImageFont:
    for path in [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _build_gif_container(frame: bytes, count: int = GIF_FRAME_COUNT) -> bytes:
    """
    Wrap N copies of a single 80x80 JPEG frame in the firmware's container
    format: frame0 | 2400-byte index | frame1 | ... | frameN-1.
    """
    import struct
    f_size = len(frame)
    idx = bytearray(GIF_INDEX_SIZE)
    # Record 0: id = total frame count; v1 = 0 (frame0 offset); v2 = frame0 size.
    struct.pack_into("<HHII", idx, 0, 0x01ff, count, 0, f_size)
    # Records 1..count-1: id = frame index; v1 = absolute offset; v2 = size.
    # All frames are identical here, so offset marches by f_size after the index.
    for k in range(1, count):
        offset = f_size + GIF_INDEX_SIZE + (k - 1) * f_size
        struct.pack_into("<HHII", idx, k * 12, 0x01ff, k, offset, f_size)
    return frame + bytes(idx) + frame * (count - 1)


def render_jpeg(five_pct: float, five_reset: str,
                week_pct: float, week_reset: str) -> bytes:
    """
    Render an 80x80 usage image and wrap it in the device's custom
    animated-GIF container (single frame repeated N times, static display).
    `_reset` args are accepted for API symmetry but not drawn — 80x80 has
    no room. The clock + weather shown around the GIF come from the
    device's Classic/Simple/Dial theme.
    """
    img = Image.new("RGB", DISPLAY_SIZE, COLOR_BG)
    draw = ImageDraw.Draw(img)

    font_lbl = _font(12)
    font_pct = _font(20)

    def draw_row(y: int, label: str, pct: float):
        pct_clamped = max(0.0, min(pct, 999.0))
        bar_pct     = min(pct_clamped, 100.0)
        color       = _bar_color(pct_clamped)
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
    img.save(buf, format="JPEG",
             qtables=[_LUMA_QTABLE, _CHROMA_QTABLE], subsampling=2)
    frame = buf.getvalue()
    # Patch APP0 (bytes [2..20]) to match the firmware-expected density.
    frame = frame[:2] + _APP0_BYTES + frame[20:]
    return _build_gif_container(frame)


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------

_logged_once = False
_last_pushed = None
_last_push_ts = 0.0


def push_once():
    global _logged_once, _last_pushed, _last_push_ts
    data = fetch_usage()
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
              f"5h {five_pct:.0f}%  7d {week_pct:.0f}%  unchanged, skipped",
              flush=True)
        return

    jpg = render_jpeg(
        five_pct, _format_reset(five.get("resets_at", "")),
        week_pct, _format_reset(week.get("resets_at", "")),
    )
    resp = requests.post(
        CLOCK_URL,
        files={CLOCK_FIELD: (CLOCK_FILENAME, jpg, "image/jpeg")},
        timeout=5,
    )
    resp.raise_for_status()
    _last_pushed = key
    _last_push_ts = now
    print(f"{datetime.datetime.now().strftime('%H:%M:%S')} "
          f"5h {five_pct:.0f}%  7d {week_pct:.0f}%  pushed {len(jpg)}B",
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
