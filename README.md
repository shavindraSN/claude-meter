# claude-meter

At-a-glance Claude Code usage on a tiny screen. Pulls live 5-hour and
weekly percentages from Anthropic's OAuth API and pushes them to a
[GeeKmagic SmallTV](http://www.geekmagic.cc/) clock over Wi-Fi.

Numbers match the Claude app's **Settings → Usage** exactly — they
come from the same `/api/oauth/usage` endpoint the desktop app uses.

Supports macOS and Linux. Runs as a user-level background service.

---

## Requirements

- Python 3.9+
- `pip` 22.0+ (older pips can't install `pyproject.toml`-only packages in
  editable mode. Upgrade with `python3 -m pip install --upgrade pip`.)
- [Claude Code](https://claude.com/claude-code) CLI installed and signed
  in (we reuse its OAuth tokens — no separate login).
- A GeeKmagic SmallTV clock on your Wi-Fi (or any future supported display).

## Install

```bash
pipx install claude-meter
```

Or in a venv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install claude-meter
```

## Configure

```bash
claude-meter configure --device-host 192.168.1.50 --mode gif80
```

Modes:
- `gif80` — 80×80 image shown alongside the device's stock clock and
  weather. Uploads to the Customization-GIF slot.
- `photo240` — full-screen 240×240 usage card with reset countdowns.
  Requires Photo mode enabled on the device (`/photo` page,
  `photo-switch` ON, `file1-switch` ON, others OFF).

Other flags:

```bash
claude-meter configure --push-interval 30 --force-push 600
```

See current config:

```bash
claude-meter show
```

## Verify

```bash
claude-meter check
```

Confirms the Claude Code token works, the API responds, and your config
is loaded.

## Run

Foreground (for testing):

```bash
claude-meter run
```

As a background service (recommended):

```bash
claude-meter install-service     # launchd on macOS, systemd user unit on Linux
claude-meter service-status
claude-meter uninstall-service
```

Service logs (macOS): `~/Library/Logs/claude-meter/claude-meter.{out,err}.log`.
Linux: `journalctl --user -u claude-meter -f`.

## Docker

On the host, sign in with Claude Code once, then mount `~/.claude` into
the container read-write so token refresh persists:

```bash
docker run --rm \
  -v ~/.claude:/root/.claude:rw \
  -v ~/.config/claude-meter:/root/.config/claude-meter \
  python:3.12-slim bash -c "pip install claude-meter && claude-meter run"
```

Linux-only detail: Claude Code on Linux stores credentials at
`~/.claude/.credentials.json`. The mount above makes them visible to the
container. On macOS hosts Claude Code uses Keychain, so running
claude-meter **directly** on the host is simpler than Docker.

## How it works

```
       ┌────────────────────┐
       │  Claude Code CLI   │   (already signed in)
       │  tokens in Keychain│
       └─────────┬──────────┘
                 │ reused by
                 ▼
       ┌────────────────────┐           ┌──────────────────────┐
       │    claude-meter    │──GET──────▶ /api/oauth/usage     │
       │    push loop       │◀─────────── {five_hour, seven_day}│
       └─────────┬──────────┘           └──────────────────────┘
                 │ render to JPEG
                 ▼
       ┌────────────────────┐
       │   Renderer         │   gif80 (80×80)  or  photo240 (240×240)
       └─────────┬──────────┘
                 │ POST /upload
                 ▼
       ┌────────────────────┐
       │  GeeKmagic clock   │   on your Wi-Fi
       └────────────────────┘
```

Token refresh happens automatically. If the access token is within 60
seconds of expiry, claude-meter refreshes against
`/v1/oauth/token` using the stored refresh token and writes the new
pair back to the same store (Keychain on macOS, JSON on Linux). Your
`claude` CLI stays in sync.

## Supported displays

| Device | Mode(s) | Status |
| --- | --- | --- |
| GeeKmagic SmallTV (v2 firmware) | `gif80`, `photo240` | supported |

Adding another display is a single file under
[`src/claude_meter/transports/`](src/claude_meter/transports/) plus a
registration line in [`transports/__init__.py`](src/claude_meter/transports/__init__.py).
Renderers in [`src/claude_meter/renderers/`](src/claude_meter/renderers/)
are likewise pluggable.

## Privacy

claude-meter never sends your tokens anywhere except Anthropic. It does
not touch the network except to talk to `api.anthropic.com` and your
local display. No telemetry, no third parties.

## Troubleshooting

**`auth: FAIL — Claude Code credentials not found`**
Run `claude` and sign in, then retry `claude-meter check`.

**`usage: FAIL — HTTP Error 429`**
Rate-limited by the API. Increase `--push-interval`.

**Clock shows the old image.**
GeeKmagic silently rejects malformed uploads with HTTP 200 but keeps
the previous content. Check `claude-meter run` output for the byte
count — if it looks wrong, file an issue.

## License

MIT. See [LICENSE](LICENSE).
