"""
Read Claude Code CLI credentials and refresh when needed.

Claude Code stores its OAuth tokens in:
  - macOS:  Keychain, service="Claude Code-credentials"
  - Linux:  ~/.claude/.credentials.json (0600)

Both stores hold the same JSON shape:
  {
    "claudeAiOauth": {
      "accessToken":  "...",
      "refreshToken": "...",
      "expiresAt":    <ms epoch>,
      "scopes":       [...]
    },
    "organizationUuid": "..."
  }

We reuse these tokens directly — no separate login for claude-meter.
On expiry we refresh against Anthropic's token endpoint and write the
new tokens back to the same store so the Claude CLI stays in sync.
"""
from __future__ import annotations

import json
import pathlib
import platform
import subprocess
import sys
import time
import urllib.request

CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URL    = "https://api.anthropic.com/v1/oauth/token"
SCOPE        = "user:inference user:file_upload user:profile"

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_LINUX_PATH       = pathlib.Path.home() / ".claude" / ".credentials.json"


class AuthError(RuntimeError):
    pass


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _read_creds() -> dict:
    """Return the full credentials dict, or raise AuthError."""
    if _is_macos():
        try:
            out = subprocess.check_output(
                ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            return json.loads(out)
        except subprocess.CalledProcessError as e:
            raise AuthError(
                "Claude Code credentials not found in Keychain. "
                "Run `claude` and sign in first."
            ) from e
    # Linux / anything else: file-based
    if not _LINUX_PATH.exists():
        raise AuthError(
            f"{_LINUX_PATH} not found. Install Claude Code and run `claude` to sign in, "
            "or mount your host's ~/.claude into this container."
        )
    return json.loads(_LINUX_PATH.read_text())


def _write_creds(creds: dict) -> None:
    """Persist updated creds back to whichever store we read from."""
    blob = json.dumps(creds)
    if _is_macos():
        # -U updates if the entry already exists, preserving ACLs.
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", _KEYCHAIN_SERVICE, "-a", _current_user(), "-w", blob],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    _LINUX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LINUX_PATH.write_text(blob)
    _LINUX_PATH.chmod(0o600)


def _current_user() -> str:
    import getpass
    return getpass.getuser()


def _refresh(refresh_token: str) -> dict:
    """Exchange a refresh token for new access + refresh tokens."""
    body = json.dumps({
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "refresh_token": refresh_token,
        "scope":         SCOPE,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        raise AuthError(f"refresh failed: {e}") from e
    return json.loads(resp.read())


def get_access_token() -> tuple[str, str]:
    """Return (access_token, organization_uuid). Refreshes if expired."""
    creds = _read_creds()
    oauth = creds.get("claudeAiOauth") or {}
    org   = creds.get("organizationUuid") or ""
    token = oauth.get("accessToken") or ""
    # expiresAt is milliseconds since epoch in Claude Code's format.
    expires_at_ms = int(oauth.get("expiresAt") or 0)
    now_ms        = int(time.time() * 1000)

    if token and now_ms < expires_at_ms - 60_000:
        return token, org

    refresh = oauth.get("refreshToken") or ""
    if not refresh:
        raise AuthError(
            "access token expired and no refresh token found. "
            "Run `claude` and sign in again."
        )

    result = _refresh(refresh)
    new_access  = result.get("access_token")  or ""
    new_refresh = result.get("refresh_token") or refresh
    expires_in  = int(result.get("expires_in") or 28800)
    if not new_access:
        raise AuthError(f"refresh response missing access_token: {result}")

    creds.setdefault("claudeAiOauth", {}).update({
        "accessToken":  new_access,
        "refreshToken": new_refresh,
        "expiresAt":    (int(time.time()) + expires_in) * 1000,
    })
    try:
        _write_creds(creds)
    except Exception as e:
        # Not fatal — we still have a working access token in hand.
        print(f"[warn] could not persist refreshed token: {e}", file=sys.stderr)

    return new_access, org
