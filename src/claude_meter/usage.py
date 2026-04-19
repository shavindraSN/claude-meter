"""Fetch + format Claude usage via Anthropic's OAuth usage endpoint."""
from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request

from claude_meter.auth import get_access_token

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA      = "oauth-2025-04-20"


def fetch_usage() -> dict:
    """GET /api/oauth/usage. Retries once on 401 after forcing a refresh."""
    token, _org = get_access_token()
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403):
            raise
        # Force a refresh by pretending the cached token was invalid.
        # We do this by reading it fresh (auth layer will refresh if < 60s).
        token, _org = get_access_token()
        req = urllib.request.Request(
            USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())


def format_reset(resets_at_str: str) -> str:
    """Human-readable countdown like 'in 2h 15m' or 'Mon 9:00 AM'."""
    if not resets_at_str:
        return "unknown"
    try:
        dt  = datetime.datetime.fromisoformat(resets_at_str)
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


def extract(data: dict) -> tuple[float, str, float, str]:
    """(five_pct, five_reset, week_pct, week_reset) from usage response."""
    five = data.get("five_hour") or {}
    week = data.get("seven_day") or {}
    return (
        float(five.get("utilization") or 0),
        format_reset(five.get("resets_at", "")),
        float(week.get("utilization") or 0),
        format_reset(week.get("resets_at", "")),
    )
