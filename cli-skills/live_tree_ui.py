#!/usr/bin/env python3
"""Small dependency-free status viewer for run-claw-pool.sh."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def worker_line(index: int, worker: dict) -> str:
    status = str(worker.get("status") or "RUNNING")
    icon = {
        "OK": "[ok]",
        "PARTIAL": "[partial]",
        "TIMEOUT": "[timeout]",
        "FAIL": "[fail]",
        "RUNNING": "[run]",
    }.get(status, "[run]" if worker.get("running") else "[done]")
    model = worker.get("model", "worker")
    msg = worker.get("msg", "Idle")
    elapsed = worker.get("elapsed", 0)
    return f"  |- subclaw {index}: {model} {icon} {msg} ({elapsed}s)"


def render(path: Path) -> str:
    data = read_json(path)
    orch = data.get("orchestrator", {})
    workers = data.get("workers", [])
    root_icon = "[run]" if orch.get("running", True) else "[done]"
    root = (
        f"Orchestrator: {orch.get('model', 'model')} "
        f"{root_icon} {orch.get('msg', 'Starting...')} ({orch.get('elapsed', 0)}s)"
    )
    lines = [root]
    for idx, worker in enumerate(workers, start=1):
        lines.append(worker_line(idx, worker))
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: live_tree_ui.py <pool_status.json> [refresh_hz]", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    refresh_hz = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    interval = 1.0 / max(refresh_hz, 0.2)
    tty = sys.stdout.isatty()
    last = ""

    while True:
        snapshot = render(path)
        if tty:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(snapshot + "\n")
            sys.stdout.flush()
        elif snapshot != last:
            print(f"\n[{time.strftime('%H:%M:%S')}]\n{snapshot}", flush=True)
            last = snapshot

        data = read_json(path)
        if data.get("orchestrator", {}).get("running") is False:
            break
        time.sleep(interval)

    if tty and os.name != "nt":
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
