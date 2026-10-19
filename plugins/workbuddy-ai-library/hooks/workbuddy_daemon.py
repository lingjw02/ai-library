"""Persistent local bridge for the WorkBuddy prefetch hook.

Why a daemon: a one-shot process costs ~24s cold start (backend import + SQLite
+ keyword index) and contends with the running MCP servers for the WAL lock.
One long-lived process pays that cost once, then answers each message in ms.

Modes:
  --ensure   start the daemon if it is not already listening (used by SessionStart)
  --serve    run the daemon in the foreground (spawned detached by --ensure)
  --stop     ask a running daemon to shut down

Wire protocol: newline-delimited JSON over 127.0.0.1:<port>
  request  {"op": "prepare"|"end_session"|"ping"|"shutdown", "session_id": str, "prompt": str}
  response {"ok": true, ...} | {"ok": false, "error": str}
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = HOOK_DIR.parent
REPO_ROOT = PLUGIN_DIR.parent.parent
CONFIG_PATH = HOOK_DIR / "prefetch.config.json"

DEFAULTS = {
    "port": 47821,
    "dataDir": str(REPO_ROOT / "data"),
    "scope": "default",
    "principal": "workbuddy",
    "maxContextChars": 6000,
    "maxItems": 3,
    "excerptChars": 480,
    "projectId": None,
    "daemonIdleSeconds": 1800,
    "startTimeoutSeconds": 30,
    "buildAttempts": 8,
    "buildRetryDelaySeconds": 1.5,
    "requestAttempts": 3,
    "logPath": str(HOOK_DIR / "prefetch.log"),
    "logMaxBytes": 262144,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.is_file():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def log(cfg: dict, message: str) -> None:
    path = cfg.get("logPath") or ""
    if not path:
        return
    try:
        p = Path(path)
        limit = int(cfg.get("logMaxBytes") or 0)
        if limit and p.is_file() and p.stat().st_size > limit:
            p.write_text("", encoding="utf-8")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        pass


def probe(cfg: dict, timeout: float = 0.35) -> bool:
    """True when a daemon is already accepting connections on the port."""
    try:
        with socket.create_connection(("127.0.0.1", int(cfg["port"])), timeout=timeout):
            return True
    except Exception:
        return False


def request(cfg: dict, payload: dict, timeout: float = 20.0) -> dict | None:
    try:
        with socket.create_connection(("127.0.0.1", int(cfg["port"])), timeout=timeout) as s:
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode("utf-8").strip()) if buf.strip() else None
    except Exception:
        return None


def ensure_running(cfg: dict) -> bool:
    """Start the daemon detached if it is not listening yet."""
    if probe(cfg):
        return True
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve"],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(REPO_ROOT),
        )
    except Exception as exc:
        log(cfg, f"DAEMON SPAWN FAILED {type(exc).__name__}: {exc}")
        return False
    deadline = time.time() + float(cfg.get("startTimeoutSeconds") or 30)
    while time.time() < deadline:
        if probe(cfg):
            return True
        time.sleep(0.5)
    log(cfg, "DAEMON START TIMEOUT")
    return False


def serve(cfg: dict) -> int:
    # Cheap pre-check: never open the database just to lose a bind race. Opening
    # it would add a second concurrent writer for no reason.
    if probe(cfg, timeout=0.3):
        log(cfg, f"DAEMON ALREADY RUNNING (pre-check) port={cfg['port']}")
        return 0

    sys.path.insert(0, str(REPO_ROOT))
    from backend.adapters.mcp.stdio_server import build_service
    from backend.adapters.harness.context_bridge import HarnessContextBridge

    # The live MCP servers write heartbeats and run wal_checkpoint(TRUNCATE) on the
    # same database, which intermittently makes a fresh schema init fail with
    # "attempt to write a readonly database". Retry before giving up.
    service = None
    last_exc = None
    for attempt in range(int(cfg.get("buildAttempts") or 12)):
        try:
            service = build_service(
                Path(cfg["dataDir"]), cfg["scope"], True, cfg["principal"], "workbuddy"
            )
            if attempt:
                log(cfg, f"BUILD OK on attempt {attempt + 1}")
            break
        except Exception as exc:
            last_exc = exc
            log(cfg, f"BUILD RETRY {attempt + 1}: {type(exc).__name__}: {exc}")
            # Jitter: the competing writer runs a ~10s heartbeat/checkpoint cycle,
            # so fixed-interval retries can stay phase-locked to the contention.
            base = float(cfg.get("buildRetryDelaySeconds") or 1.5)
            time.sleep(base * (0.5 + random.random()))
    if service is None:
        raise RuntimeError(f"build_service failed after retries: {last_exc}")

    bridge = HarnessContextBridge(
        service,
        max_context_chars=int(cfg["maxContextChars"]),
        max_items=int(cfg["maxItems"]),
        excerpt_chars=int(cfg["excerptChars"]),
    )
    last_used = time.time()
    stopping = threading.Event()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows' SO_REUSEADDR lets a second process hijack the same port, so two
    # daemons can both "succeed" and double the SQLite contention. Use the
    # exclusive flag there, and let the loser exit quietly.
    if os.name == "nt":
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", int(cfg["port"])))
    except OSError as exc:
        log(cfg, f"DAEMON ALREADY RUNNING on port {cfg['port']} ({type(exc).__name__})")
        with contextlib.suppress(Exception):
            srv.close()
        with contextlib.suppress(Exception):
            bridge.close()
        return 0
    srv.listen(8)
    srv.settimeout(1.0)
    log(cfg, f"DAEMON READY port={cfg['port']} dataDir={cfg['dataDir']}")

    def handle(payload: dict) -> dict:
        op = payload.get("op") or "prepare"
        if op == "ping":
            return {"ok": True, "protocol": "ai-library-workbuddy/1", "scope": cfg["scope"]}
        if op == "shutdown":
            stopping.set()
            return {"ok": True, "stopping": True}
        session_id = (payload.get("session_id") or "workbuddy-unknown")[:200]
        if op == "end_session":
            with contextlib.suppress(Exception):
                bridge.handle({"operation": "end_session", "session_key": session_id})
            return {"ok": True, "ended": True}
        if op != "prepare":
            return {"ok": False, "error": "unknown op"}
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "empty prompt"}
        # Re-read the config per request so a project change takes effect without
        # restarting the daemon (and without opening another DB connection).
        request = {"operation": "prepare", "query": prompt[:2000], "session_key": session_id}
        project_id = load_config().get("projectId")
        if project_id:
            request["project_id"] = project_id
        last = None
        for attempt in range(int(cfg.get("requestAttempts") or 3)):
            try:
                result = bridge.handle(request)
                return {"ok": True, "context_text": result.get("context_text"),
                        "evidence_count": result.get("evidence_count"),
                        "partial": result.get("partial"),
                        "project": (result.get("project") or {}).get("title") if result.get("project") else None}
            except Exception as exc:
                last = exc
                log(cfg, f"PREPARE RETRY {attempt + 1}: {type(exc).__name__}: {exc}")
                time.sleep(0.8)
        return {"ok": False, "error": f"{type(last).__name__}: {last}"}

    try:
        while not stopping.is_set():
            if time.time() - last_used > float(cfg.get("daemonIdleSeconds") or 1800):
                log(cfg, "DAEMON IDLE EXIT")
                break
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(30.0)
                buf = b""
                try:
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    if not buf.strip():
                        continue
                    payload = json.loads(buf.decode("utf-8").strip())
                    last_used = time.time()
                    started = time.time()
                    try:
                        response = handle(payload)
                    except Exception as exc:
                        response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    response["elapsed_ms"] = int((time.time() - started) * 1000)
                    conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                except Exception as exc:
                    log(cfg, f"DAEMON REQUEST ERROR {type(exc).__name__}: {exc}")
    finally:
        with contextlib.suppress(Exception):
            srv.close()
        with contextlib.suppress(Exception):
            bridge.close()
        log(cfg, "DAEMON STOPPED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ensure", action="store_true")
    group.add_argument("--serve", action="store_true")
    group.add_argument("--stop", action="store_true")
    group.add_argument("--status", action="store_true")
    args = parser.parse_args()
    cfg = load_config()

    if args.ensure:
        ok = ensure_running(cfg)
        log(cfg, f"ENSURE {'ok' if ok else 'failed'}")
        return 0
    if args.status:
        print("running" if probe(cfg) else "stopped")
        return 0
    if args.stop:
        request(cfg, {"op": "shutdown"})
        return 0
    return serve(cfg)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.exit(main())
    except Exception as exc:  # never die silently: a dead daemon is invisible otherwise
        import traceback
        cfg_for_log = load_config()
        log(cfg_for_log, f"FATAL {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        traceback.print_exc()
        sys.exit(1)
