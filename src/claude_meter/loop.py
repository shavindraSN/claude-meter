"""Push loop: fetch usage, render, push, dedup, sleep."""
from __future__ import annotations

import datetime
import json
import sys
import time

from claude_meter import renderers, transports
from claude_meter.config import Config
from claude_meter.usage import extract, fetch_usage


def run(cfg: Config) -> None:
    renderer  = renderers.get(cfg.mode)
    transport = transports.get(cfg.transport, host=cfg.device_host, mode=cfg.mode)

    logged_once  = False
    last_key:   tuple | None = None
    last_push_ts = 0.0

    while True:
        try:
            data = fetch_usage()
            if not logged_once:
                print("API response:", json.dumps(data, indent=2), flush=True)
                logged_once = True

            five_pct, five_reset, week_pct, week_reset = extract(data)
            key = (int(round(five_pct)), int(round(week_pct)))
            now = time.time()

            if last_key == key and (now - last_push_ts) < cfg.force_push_sec:
                print(f"{_ts()} 5h {five_pct:.0f}%  7d {week_pct:.0f}%  "
                      f"unchanged, skipped", flush=True)
            else:
                payload = renderer.render(five_pct, five_reset, week_pct, week_reset)
                n = transport.push(payload)
                last_key     = key
                last_push_ts = now
                print(f"{_ts()} 5h {five_pct:.0f}%  7d {week_pct:.0f}%  "
                      f"pushed {n}B ({cfg.mode})", flush=True)
        except KeyboardInterrupt:
            print("bye", flush=True)
            sys.exit(0)
        except Exception as e:
            print(f"[warn] {type(e).__name__}: {e}", flush=True)

        time.sleep(cfg.push_interval_sec)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")
