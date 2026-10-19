"""WorkBuddy UserPromptSubmit hook: thin client for the prefetch daemon.

Contract (Claude Code Hooks compatible):
  stdin  : {"session_id", "transcript_path", "cwd", "hook_event_name", "prompt"}
  stdout : {"continue": true, "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": "<snapshot>"}}

Guarantees:
  * Always exits 0 and never blocks: if the daemon is cold, this message simply
    gets no snapshot and the daemon warms up for the next one.
  * stdout carries ONLY the JSON object.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = HOOK_DIR.parent
REPO_ROOT = PLUGIN_DIR.parent.parent
CONFIG_PATH = HOOK_DIR / "prefetch.config.json"
DAEMON = HOOK_DIR / "workbuddy_daemon.py"

DEFAULTS = {
    "enabled": True,
    "port": 47821,
    "minPromptChars": 4,
    "skipPrefixes": ["/"],
    "skipRegex": "^(?:(?:ok|okay|好的?|谢谢|感谢你?|多谢|收到|明白|嗯+|是的|对的|没问题|可以|行|辛苦了?|不错|棒|赞|厉害|thanks?|thank\\s*you|thx|got\\s*it|nice|cool|great|awesome)[\\s!！。.，,、~～？?…]*)+$",
    "probeTimeoutSeconds": 0.35,
    "requestTimeoutSeconds": 15,
    "spawnCooldownSeconds": 60,
    "coldWaitSeconds": 12,
    "spawnMarkerPath": str(HOOK_DIR / ".last-spawn-attempt"),
    "logPath": str(HOOK_DIR / "prefetch.log"),
    "logMaxBytes": 262144,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    with contextlib.suppress(Exception):
        if CONFIG_PATH.is_file():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def log(cfg: dict, message: str) -> None:
    path = cfg.get("logPath") or ""
    if not path:
        return
    with contextlib.suppress(Exception):
        p = Path(path)
        limit = int(cfg.get("logMaxBytes") or 0)
        if limit and p.is_file() and p.stat().st_size > limit:
            p.write_text("", encoding="utf-8")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")


def call_daemon(cfg: dict, payload: dict) -> dict | None:
    port = int(cfg["port"])
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=float(cfg["probeTimeoutSeconds"])) as s:
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            s.settimeout(float(cfg["requestTimeoutSeconds"]))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode("utf-8").strip()) if buf.strip() else None
    except Exception:
        return None


def spawn_daemon(cfg: dict) -> None:
    """Spawn the daemon detached, at most once per cooldown window.

    Without the cooldown, a daemon that cannot start (e.g. the library is mid-edit)
    would be respawned on every single message.
    """
    cooldown = float(cfg.get("spawnCooldownSeconds") or 60)
    marker = Path(cfg.get("spawnMarkerPath") or (HOOK_DIR / ".last-spawn-attempt"))
    with contextlib.suppress(Exception):
        if marker.is_file() and (time.time() - marker.stat().st_mtime) < cooldown:
            return
    with contextlib.suppress(Exception):
        marker.write_text(str(int(time.time())), encoding="utf-8")

    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with contextlib.suppress(Exception):
        subprocess.Popen(
            [sys.executable, str(DAEMON), "--serve"],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(REPO_ROOT),
        )


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def main() -> int:
    started = time.time()
    cfg = load_config()
    if not cfg.get("enabled", True):
        return 0

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = {}

    prompt = (event.get("prompt") or "").strip()
    session_id = (event.get("session_id") or "workbuddy-unknown")[:200]

    if "--end-session" in sys.argv:
        call_daemon(cfg, {"op": "end_session", "session_id": session_id})
        return 0

    if len(prompt) < int(cfg.get("minPromptChars") or 0):
        return 0
    for prefix in cfg.get("skipPrefixes") or []:
        if prefix and prompt.startswith(prefix):
            return 0
    # Pure pleasantries ("ok感谢你", "谢谢") carry no retrieval intent, but they pass
    # the length check and would otherwise cost a full 5-10s retrieval.
    pattern = cfg.get("skipRegex") or ""
    if pattern:
        with contextlib.suppress(Exception):
            if re.fullmatch(pattern, prompt, re.IGNORECASE):
                log(cfg, f"SKIP pleasantry ({time.time() - started:.2f}s)")
                return 0

    response = call_daemon(cfg, {"op": "prepare", "session_id": session_id, "prompt": prompt})
    if response is None:
        # Daemon cold: warm it up, then wait a bounded time so THIS message still
        # gets a snapshot instead of silently skipping.
        spawn_daemon(cfg)
        deadline = time.time() + float(cfg.get("coldWaitSeconds") or 0)
        while time.time() < deadline:
            time.sleep(0.5)
            response = call_daemon(cfg, {"op": "prepare", "session_id": session_id, "prompt": prompt})
            if response is not None:
                break
        if response is None:
            log(cfg, f"COLD gave up after {time.time() - started:.2f}s")
            return 0

    if not response.get("ok"):
        log(cfg, f"DAEMON ERROR {response.get('error')} ({time.time() - started:.2f}s)")
        return 0

    text = response.get("context_text")
    if not text:
        log(cfg, f"EMPTY ({time.time() - started:.2f}s)")
        return 0

    log(cfg, f"OK chars={len(text)} items={response.get('evidence_count')} "
             f"daemon={response.get('elapsed_ms')}ms total={int((time.time() - started) * 1000)}ms")
    emit(
        {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": text,
            },
        }
    )
    return 0


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
