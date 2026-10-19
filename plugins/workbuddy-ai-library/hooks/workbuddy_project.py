"""List or select the AI Library topic used by the prefetch hook.

Usage:
  python workbuddy_project.py --list [--limit 200]
  python workbuddy_project.py --set "AI library"     # by title (substring, unique match)
  python workbuddy_project.py --set topic-<id>       # by exact id
  python workbuddy_project.py --clear
  python workbuddy_project.py --status

Selection is stored in prefetch.config.json as `projectId`. The daemon re-reads it
per request, so no restart is needed. No new MCP server or DB connection is added
for normal operation; --list opens one short-lived read-only connection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = HOOK_DIR.parent
REPO_ROOT = PLUGIN_DIR.parent.parent
CONFIG_PATH = HOOK_DIR / "prefetch.config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_projects(limit: int = 200):
    """Fetch topics, paginating past the bridge's 200-item page cap."""
    sys.path.insert(0, str(REPO_ROOT))
    from backend.adapters.mcp.stdio_server import build_service
    from backend.adapters.harness.context_bridge import HarnessContextBridge

    service = build_service(REPO_ROOT / "data", "default", True, "workbuddy-project", "workbuddy")
    bridge = HarnessContextBridge(service)
    try:
        cap = max(1, min(limit, 200))
        page = bridge.handle({"operation": "list_projects", "offset": 0, "limit": cap})
        projects = list(page["projects"])
        while page.get("has_more") and len(projects) < limit:
            want = max(1, min(200, limit - len(projects)))
            page = bridge.handle({"operation": "list_projects", "offset": len(projects), "limit": want})
            if not page["projects"]:
                break
            projects.extend(page["projects"])
        return {**page, "projects": projects}
    finally:
        bridge.close()


def cmd_list(limit: int) -> int:
    page = list_projects(limit)
    print(f"scope={page['scope']}  total={page['total']}  has_more={page['has_more']}\n")
    by_parent: dict = {}
    for p in page["projects"]:
        by_parent.setdefault(p["parent_id"], []).append(p)

    def walk(parent, depth):
        for p in sorted(by_parent.get(parent, []), key=lambda x: -(x["count"] or 0)):
            print(f"{'  ' * depth}- {p['title']}  [{p['count']}]  {p['id']}")
            if depth < 2:
                walk(p["id"], depth + 1)

    walk(None, 0)
    return 0


def resolve(needle: str):
    page = list_projects(200)
    if needle.startswith("topic-"):
        for p in page["projects"]:
            if p["id"] == needle:
                return p
        return None
    hits = [p for p in page["projects"] if needle.casefold() in (p["title"] or "").casefold()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"没有主题匹配 {needle!r}", file=sys.stderr)
        return None
    exact = [p for p in hits if (p["title"] or "").casefold() == needle.casefold()]
    if len(exact) == 1:
        return exact[0]
    print(f"{needle!r} 匹配到 {len(hits)} 个主题，请用完整标题或 id：", file=sys.stderr)
    for p in hits:
        print(f"  {p['title']}  {p['id']}", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--set", metavar="TITLE_OR_ID")
    g.add_argument("--clear", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    cfg = load_config()

    if args.status:
        pid = cfg.get("projectId")
        print(f"projectId = {pid!r}  ({'全库检索' if not pid else '限定主题'})")
        return 0

    if args.list:
        return cmd_list(args.limit)

    if args.clear:
        cfg["projectId"] = None
        save_config(cfg)
        print("已清除主题限定 → 恢复全库检索")
        return 0

    project = resolve(args.set)
    if project is None:
        return 1
    cfg["projectId"] = project["id"]
    save_config(cfg)
    print(f"已限定主题：{project['title']}  [{project['count']} 条]  {project['id']}")
    print("（守护进程每条请求都会重读配置，无需重启）")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
