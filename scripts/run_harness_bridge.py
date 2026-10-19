"""Persistent local NDJSON bridge with fixed authority and reviewed task sync."""
from pathlib import Path
import argparse
import json
import logging
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.adapters.harness.context_bridge import HarnessContextBridge
from backend.adapters.mcp.stdio_server import build_service
from backend.core.retrieval.keyword_index import KeywordIndexWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--principal', default='deepseek-harness')
    parser.add_argument('--max-context-chars', type=int, default=6000)
    parser.add_argument('--max-items', type=int, default=3)
    parser.add_argument('--excerpt-chars', type=int, default=480)
    parser.add_argument('--allow-confirmed-sync', action='store_true', help='Enable only native reviewed task confirmation; ordinary MCP tools remain read-only')
    options = parser.parse_args()
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    service = build_service(options.data_dir.resolve(), options.scope, True, options.principal, 'deepseek')
    bridge = HarnessContextBridge(service, max_context_chars=options.max_context_chars,
        max_items=options.max_items, excerpt_chars=options.excerpt_chars, allow_confirmed_sync=options.allow_confirmed_sync)
    worker = KeywordIndexWorker(service.coordinator.keyword_index)
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(10):
            try: service.spider_activity.heartbeat(service.spider_connection)
            except Exception: logging.exception('Harness mecha heartbeat unavailable')

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    worker.start()
    try:
        while not bridge.closed:
            line = sys.stdin.buffer.readline(32769)
            if not line: break
            invalid_framing = len(line) > 32768 or not line.endswith(b'\n')
            request_id = None
            try:
                if invalid_framing:
                    raise ValueError('Bridge request exceeds 32768 bytes or lacks a newline')
                raw = json.loads(line)
                if not isinstance(raw, dict): raise ValueError('Bridge request must be an object')
                request_id = raw.pop('request_id', None)
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
                    raise ValueError('A bounded string request_id is required')
                result = bridge.handle(raw)
                response = {'request_id': request_id, 'ok': True, 'result': result}
            except Exception as exc:
                response = {'request_id': request_id, 'ok': False,
                            'error': {'code': type(exc).__name__, 'message': str(exc)[:600]}}
            print(json.dumps(response, ensure_ascii=False, separators=(',', ':')), flush=True)
            if invalid_framing: break  # Never interpret a rejected frame's tail as a new request.
    finally:
        stop.set()
        thread.join(timeout=11)
        worker.stop()
        bridge.close()


if __name__ == '__main__': main()
