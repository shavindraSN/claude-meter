"""Install / uninstall the push loop as a user-level background service.

macOS: launchd agent at ~/Library/LaunchAgents/com.claude-meter.plist
Linux: systemd user unit at ~/.config/systemd/user/claude-meter.service
"""
from __future__ import annotations

import pathlib
import platform
import shutil
import subprocess
import sys
from importlib.resources import files

SERVICE_LABEL = "com.claude-meter"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _executable() -> str:
    """Absolute path to the claude-meter entrypoint."""
    exe = shutil.which("claude-meter")
    if exe:
        return exe
    # Fall back to `python -m claude_meter` so service survives pipx reinstall.
    return f"{sys.executable} -m claude_meter"


def _launchd_plist_path() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def _systemd_unit_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "systemd" / "user" / "claude-meter.service"


def _log_dir() -> pathlib.Path:
    d = pathlib.Path.home() / "Library" / "Logs" / "claude-meter"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render(template_name: str) -> str:
    raw = files("claude_meter.services").joinpath(template_name).read_text()
    raw = raw.replace("__EXECUTABLE__", _executable())
    raw = raw.replace("__LOG_DIR__",    str(_log_dir()))
    return raw


def install() -> pathlib.Path:
    if _is_macos():
        path = _launchd_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render("launchd.plist.template"))
        # Reload: unload (best-effort) then load.
        subprocess.run(["launchctl", "unload", str(path)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "load", str(path)], check=True)
        return path

    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render("systemd.service.template"))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "claude-meter.service"],
                   check=True)
    return path


def uninstall() -> pathlib.Path | None:
    if _is_macos():
        path = _launchd_plist_path()
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            path.unlink()
            return path
        return None

    path = _systemd_unit_path()
    if path.exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", "claude-meter.service"],
                       check=False)
        path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return path
    return None


def status() -> str:
    if _is_macos():
        out = subprocess.run(["launchctl", "list", SERVICE_LABEL],
                             capture_output=True, text=True)
        return out.stdout + out.stderr
    out = subprocess.run(
        ["systemctl", "--user", "status", "claude-meter.service", "--no-pager"],
        capture_output=True, text=True,
    )
    return out.stdout + out.stderr
