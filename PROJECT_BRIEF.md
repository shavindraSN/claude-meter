# GeeKmagic Clock × Claude Code Usage Display

## Project goal

Display real-time Claude Code usage statistics (5-hour session % and weekly %) on a
GeeKmagic SmallTV desktop clock. The numbers should match what Claude app shows at
**Settings → Usage** as closely as possible.

We already have a working Python prototype. The remaining work is to port the
percentage-calculation logic from the existing Mac widget
(`/Users/shavindra/Documents/claude-widget`) so the clock numbers match the app
exactly, rather than using reverse-engineered approximations.

---

## Hardware & network

- **Device**: GeeKmagic SmallTV (stock firmware)
- **Static IP**: `192.168.1.125`
- **Display**: 240×240 pixel TFT
- **API**: Accepts JPEG uploads via `POST http://192.168.1.125/doUpload` (multipart form, field name `file`)
- No firmware flashing needed — stock firmware's `/doUpload` is enough.

---

## Current state

`clock_push.py` (in this project) is working. It:

1. Scans all `.jsonl` files under `~/.claude/projects/`
2. Parses each line for `timestamp`, `costUSD`, and `usage.{input,output,cache_creation,cache_read}_tokens`
3. Computes two aggregates:
   - **Session (5h)**: tokens within the last 5 hours, oldest message in window is approximate session start
   - **Weekly (7d)**: tokens within the last 7 days
4. Divides each by plan limits to get a percentage
5. Renders a 240×240 JPEG with two progress bars (green < 70%, yellow < 90%, red ≥ 90%)
6. POSTs to the clock, loops every 30 seconds

Plan is Max 5×. Current limits used (reverse-engineered, not official):

```python
PLAN_LIMITS = {
    "pro":   {"session": 44_000,   "weekly": 7_000_000},
    "max5":  {"session": 88_000,   "weekly": 35_000_000},
    "max20": {"session": 220_000,  "weekly": 140_000_000},
}
```

---

## Known gaps — this is what Claude Code should fix

### 1. Percentage accuracy

My current numbers are community reverse-engineered and will drift from the
Claude app. The existing Mac widget in `/Users/shavindra/Documents/claude-widget`
apparently gets this right. **Read that codebase first** and port its approach —
specifically:

- How does it determine the user's plan? (Config? API call? Auth?)
- How does it calculate the session percentage — what counts as the session start?
  Is it token-based, request-based, or time-based?
- How does it calculate the weekly percentage — rolling 7 days, or fixed anchor?
- Where does it get the quota limits from — hardcoded, fetched from an endpoint,
  or derived from observed usage?
- Does it only count certain token types (e.g. input + output, excluding cache reads)?

### 2. Session start detection

Our approximation uses "oldest message in the last 5 hours" as session start.
Claude Code's actual rule: **session starts with the first prompt after a reset,
and rolls forward 5h from that point**. If the user was idle for 3 hours then
started a session, our math under-reports the reset time.

### 3. Weekly reset anchor

Anthropic uses a **fixed** weekly anchor tied to the account — not a rolling 7-day
window. Our approximation uses rolling. The widget likely has the correct
anchor logic.

### 4. Opus-specific weekly limits

Claude app shows weekly limits split: **Opus-only** vs **all other models**. Our
current display only shows a single combined weekly percentage. Worth splitting
this out if the widget does.

---

## Reference: JSONL format

Each line in `~/.claude/projects/<project>/<session-id>.jsonl` looks roughly like:

```json
{
  "timestamp": "2026-04-15T10:30:00Z",
  "model": "claude-opus-4-6",
  "usage": {
    "input_tokens": 1245,
    "output_tokens": 28756,
    "cache_creation_input_tokens": 512,
    "cache_read_input_tokens": 256
  },
  "costUSD": 0.123
}
```

The files also contain non-usage lines (messages, tool calls, etc.) — we skip
anything without a `timestamp` and usage or cost.

---

## Reference: GeeKmagic `/doUpload` API

```python
import requests
from PIL import Image
import io

img = Image.new("RGB", (240, 240), (0, 0, 0))
# ... draw stuff ...
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=90)
buf.seek(0)

requests.post(
    "http://192.168.1.125/doUpload",
    files={"file": ("display.jpg", buf, "image/jpeg")},
    timeout=5,
)
```

The clock replaces whatever it was showing with the uploaded image. No
authentication. Image must be 240×240 JPEG.

---

## Suggested plan of attack

1. **Read the Mac widget first.**

   ```bash
   cd /Users/shavindra/Documents/claude-widget
   ls -la
   # Find the files that compute usage percentages
   ```

   Identify:
   - The data source (JSONL direct? Anthropic API? Some intermediate layer?)
   - The percentage calculation function(s)
   - How the plan / quota is determined
   - The session-start algorithm

2. **Port the logic to `clock_push.py`.** Replace the `compute_stats()` function
   with something that matches the widget's behaviour. Keep the rendering and
   push logic as-is — they work fine.

3. **Validate against Claude app.** Run the script and the Claude app Settings →
   Usage page side-by-side. Numbers should agree within a percentage point.

4. **Add Opus/non-Opus split** if the widget has it and there's room on the display.

5. **Package it.** Options:
   - Keep as a Python script + `nohup` (current approach)
   - Wrap it as a `launchd` agent so it auto-starts on login
   - Port to Swift and bundle it alongside the existing Mac widget, sharing the
     calculation module

6. **Optional niceties**:
   - Current model indicator (Opus vs Sonnet vs Haiku)
   - Cost today vs cost this week
   - A sparkline of the last hour's token consumption
   - Colour-blind-friendly palette option

---

## Files in this project

- `clock_push.py` — working prototype, reads JSONL + pushes to clock every 30s
- `PROJECT_BRIEF.md` — this file

## Dependencies

```bash
pip install pillow requests
```

## Running

```bash
python3 clock_push.py                              # foreground
nohup python3 clock_push.py > ~/clock.log 2>&1 &   # background
tail -f ~/clock.log                                # check it's working
```Pro