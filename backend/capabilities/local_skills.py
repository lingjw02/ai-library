"""Read-only discovery of local skill & MCP metadata enriched with AAS Core v19.8.0 philosophy.

Never imports or executes skill code. Performs:
1. Local multi-root skill discovery + installed AAS Catalog (`data/aas_catalog/package`) integration.
2. Exact-match deduplication ("一样的 skill 就跳过") across all roots and catalog entries.
3. High-similarity skill fusion ("高度相似的 skill 就融合") combining complementary playbooks.
4. 16-Domain Fleet Taxonomy classification & AAS Core Trust Boundary enrichment for Skills and MCPs.
"""
from collections import deque
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import time
import zipfile

import yaml

from backend.capabilities.aas_engine import (
    AAS_PHILOSOPHY_MOTTO,
    compose_aas_stack,
    deduplicate_and_fuse_skills,
    enrich_with_aas_philosophy,
    normalize_skill_id,
)


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def aas_package_root(workspace=None) -> Path:
    ws = Path(workspace or _workspace_root())
    return ws / "data" / "aas_catalog" / "package"


_aas_catalog_cache = None


def load_aas_catalog_map(workspace=None) -> dict:
    """Load the installed AAS v19.8.0 catalog.json / skills_index.json into a cached lookup map."""
    global _aas_catalog_cache
    if workspace is None and _aas_catalog_cache is not None:
        return _aas_catalog_cache

    pkg = aas_package_root(workspace)
    cat_file = pkg / "data" / "catalog.json"
    idx_file = pkg / "skills_index.json"
    entries = {}

    if cat_file.is_file():
        try:
            data = json.loads(cat_file.read_text(encoding="utf-8"))
            for item in data.get("skills", []):
                cid = (item.get("canonical_id") or item.get("id") or "").lower()
                if cid:
                    entries[cid] = item
        except Exception:
            pass

    if idx_file.is_file():
        try:
            idx_list = json.loads(idx_file.read_text(encoding="utf-8"))
            for item in idx_list:
                cid = (item.get("id") or "").lower()
                if cid:
                    if cid in entries:
                        entries[cid]["plugin"] = item.get("plugin", {})
                        entries[cid]["date_added"] = item.get("date_added")
                    else:
                        entries[cid] = item
        except Exception:
            pass

    if workspace is None and entries:
        _aas_catalog_cache = entries
    return entries


def skill_roots(home=None, workspace=None):
    home = Path(home or Path.home())
    workspace = Path(workspace or _workspace_root())
    roots = [
        (workspace / ".agents/skills", "当前项目"),
        (workspace / ".codex/skills", "当前项目 Codex"),
        (home / ".agents/skills", "共享技能"),
        (home / ".codex/skills", "Codex"),
        (home / ".gemini/skills", "Gemini"),
        (home / ".dsh/skills", "DSH"),
    ]
    # Only the latest local package version, not every historical cache copy.
    cache = home / ".codex/plugins/cache"
    if cache.is_dir():
        for vendor in sorted(cache.iterdir()):
            if not vendor.is_dir():
                continue
            for package in sorted(vendor.iterdir()):
                if not package.is_dir():
                    continue
                versions = [p for p in package.iterdir() if (p / "skills").is_dir()]
                if versions:
                    version = max(
                        versions,
                        key=lambda p: (tuple(int(n) for n in re.findall(r"\d+", p.name)), p.name),
                    )
                    roots.append((version / "skills", f"插件本地文件 · {package.name}"))
    return roots


def discover_mcp_servers(home=None, workspace=None):
    home = Path(home or Path.home())
    workspace = Path(workspace or _workspace_root())
    config_sources = [
        (home / ".gemini/config/mcp_config.json", "Gemini"),
        (home / ".codex/mcp.json", "Codex"),
        (home / ".workbuddy-ai/mcp.json", "WorkBuddy"),
        (workspace / "work/client-configs/doubao.mcp.json", "Doubao"),
        (workspace / "work/client-configs/workbuddy.mcp.json", "WorkBuddy Client"),
    ]
    schema_root = home / ".gemini/antigravity-cli/mcp"
    servers = {}

    # Register installed AAS Core local MCP control plane if present
    aas_mcp_bin = aas_package_root(workspace) / "tools" / "bin" / "aas-mcp.js"
    if aas_mcp_bin.is_file():
        servers["aas-core"] = {
            "id": "mcp-aas-core",
            "kind": "mcp",
            "title": "mcp · aas-core",
            "name": "aas-core",
            "server_name": "aas-core",
            "command": "node",
            "args": [str(aas_mcp_bin.resolve())],
            "tools": [
                {"name": "search_skills", "description": "Retrieve deterministic, paginated matches from the verified local AAS catalog without ranking bias."},
                {"name": "get_skill", "description": "Inspect one skill and optionally read its full SKILL.md content."},
                {"name": "list_skill_files", "description": "Page through the selected skill's catalog-bound file inventory (scripts, references)."},
                {"name": "read_skill_file", "description": "Read one inventoried file from a skill bundle."},
                {"name": "compose_stack", "description": "Validate chosen skill IDs and return a schema v2 aas-stack.json manifest in memory."},
                {"name": "inspect_stack", "description": "Inspect a composed stack manifest and check capability coverage."},
            ],
            "locations": [{"source": "AAS Core v19.8.0 Local MCP", "path": str(aas_mcp_bin.resolve())}],
        }

    for cfg_path, source_name in config_sources:
        if not cfg_path.is_file():
            continue
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg_servers = data.get("mcpServers", {})
            if isinstance(cfg_servers, dict):
                for name, srv in cfg_servers.items():
                    if not isinstance(srv, dict):
                        continue
                    entry = servers.setdefault(
                        name,
                        {
                            "id": f"mcp-{name}",
                            "kind": "mcp",
                            "title": f"mcp · {name}",
                            "name": name,
                            "server_name": name,
                            "command": srv.get("command", ""),
                            "args": srv.get("args", []),
                            "tools": [],
                            "locations": [],
                        },
                    )
                    loc = {"source": f"{source_name} MCP 配置", "path": str(cfg_path.resolve())}
                    if loc not in entry["locations"]:
                        entry["locations"].append(loc)
        except Exception:
            pass

    if schema_root.is_dir():
        for sdir in sorted(schema_root.iterdir()):
            if not sdir.is_dir():
                continue
            name = sdir.name
            tools = []
            for jf in sorted(sdir.glob("*.json")):
                try:
                    js = json.loads(jf.read_text(encoding="utf-8"))
                    desc = js.get("description", "")
                    tools.append({"name": jf.stem, "description": str(desc)[:200]})
                except Exception:
                    pass
            entry = servers.setdefault(
                name,
                {
                    "id": f"mcp-{name}",
                    "kind": "mcp",
                    "title": f"mcp · {name}",
                    "name": name,
                    "server_name": name,
                    "command": "",
                    "args": [],
                    "tools": [],
                    "locations": [],
                },
            )
            if tools:
                entry["tools"] = tools
            loc = {"source": "Antigravity CLI Schema", "path": str(sdir.resolve())}
            if loc not in entry["locations"]:
                entry["locations"].append(loc)

    results = []
    for name, s in sorted(servers.items()):
        cmd = s.get("command")
        args = s.get("args") or []
        tools = s.get("tools") or []
        lines = []
        if cmd:
            cmd_str = f"{cmd} {' '.join(str(a) for a in args)}".strip()
            lines.append(f"服务运行命令: {cmd_str}")
        if tools:
            lines.append(f"包含 {len(tools)} 个注册工具 (Tools)")
            sample = ", ".join(t["name"] for t in tools[:5])
            if len(tools) > 5:
                sample += f" 等共 {len(tools)} 项"
            lines.append(f"工具列表: {sample}")
        else:
            lines.append("本地标准 Model Context Protocol 服务能力节点。")
        s["description"] = "\n".join(lines)
        # Attach AAS Core Design Philosophy & accurate 16-Domain classification to every MCP
        enrich_with_aas_philosophy(s)
        results.append(s)

    return results


_skills_cache = None
_skills_cache_time = 0

# 全量扫描本机技能目录约需 27 秒（约 3100 项）。TTL 原来只有 30 秒，
# 等于每半分钟就有一个请求要阻塞近 27 秒——这是「用起来卡」的直接来源。
# 技能目录不会秒级变化，放宽到 5 分钟；需要立即生效时可在设置里重建。
_SKILLS_CACHE_TTL_SECONDS = 300


def discover_skills(roots=None, *, include_aas_catalog: bool = True):
    global _skills_cache, _skills_cache_time
    custom_roots = roots is not None
    if not custom_roots and include_aas_catalog and _skills_cache is not None and (time.time() - _skills_cache_time < _SKILLS_CACHE_TTL_SECONDS):
        return _skills_cache

    roots = skill_roots() if roots is None else roots
    raw_by_hash = {}
    warnings = []
    visited_count = 0

    for root, source in roots:
        root = Path(root)
        if not root.exists():
            continue
        queue, visited = deque([(root, 0)]), set()
        while queue:
            directory, depth = queue.popleft()
            try:
                resolved = directory.resolve()
                if resolved in visited:
                    continue
                visited.add(resolved)
                visited_count += 1
                if visited_count > 10000:
                    warnings.append("扫描达到目录上限，清单不完整。")
                    break
                manifest = directory / "SKILL.md"
                if manifest.is_file():
                    if manifest.stat().st_size > 1048576:
                        warnings.append(f"{source} / {directory.name}：技能说明过大，未读取。")
                        continue
                    raw = manifest.read_bytes()
                    text = raw.decode("utf-8-sig")
                    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", text, re.S)
                    try:
                        header = yaml.safe_load(match.group(1)) if match else {}
                        header = header if isinstance(header, dict) else {}
                    except yaml.YAMLError:
                        header = {}
                        warnings.append(f"{source} / {directory.name}：名称元数据异常，显示目录名。")
                    name = header.get("name")
                    name = name.strip()[:300] if isinstance(name, str) and name.strip() else directory.name
                    description = header.get("description", "")
                    description = description[:6000] if isinstance(description, str) else ""
                    key = sha256(raw).hexdigest()
                    item = raw_by_hash.setdefault(
                        key,
                        {
                            "id": "local-skill-" + key[:24],
                            "kind": "skill",
                            "title": name,
                            "description": description,
                            "category": header.get("category"),
                            "risk": header.get("risk"),
                            "tags": header.get("tags") if isinstance(header.get("tags"), list) else None,
                            "locations": [],
                        },
                    )
                    location = {"source": source, "path": str(manifest.resolve())}
                    if location not in item["locations"]:
                        item["locations"].append(location)
                    continue
                for child in sorted(directory.iterdir()):
                    if child.name not in {".git", "node_modules", "__pycache__", ".venv"} and child.is_dir():
                        queue.append((child, depth + 1))
            except (OSError, UnicodeError, RuntimeError):
                warnings.append(f"{source} / {directory.name}：无法读取。")

    raw_skills = list(raw_by_hash.values())
    aas_map = {}

    if not custom_roots:
        aas_map = load_aas_catalog_map()
        if include_aas_catalog and aas_map:
            pkg_root_resolved = str(aas_package_root().resolve())
            sep = "\\" if "\\" in pkg_root_resolved else "/"
            for cid, aas_entry in aas_map.items():
                rel_path = aas_entry.get("path") or f"skills/{cid}/SKILL.md"
                if not rel_path.endswith("SKILL.md"):
                    rel_path = f"{rel_path.rstrip('/')}/SKILL.md"
                full_md_str = f"{pkg_root_resolved}{sep}{rel_path.replace('/', sep)}"
                raw_skills.append(
                    {
                        "id": f"skill-{cid}",
                        "kind": "skill",
                        "title": aas_entry.get("name") or cid,
                        "description": (aas_entry.get("description") or "")[:6000],
                        "category": aas_entry.get("category"),
                        "risk": aas_entry.get("risk", "safe"),
                        "tags": aas_entry.get("tags"),
                        "triggers": aas_entry.get("triggers"),
                        "locations": [
                            {
                                "source": "AAS Catalog v19.8.0",
                                "path": full_md_str,
                            }
                        ],
                    }
                )
        deduped_skills, stats = deduplicate_and_fuse_skills(raw_skills, aas_map)
        mcps = discover_mcp_servers()
        all_items = sorted([*deduped_skills, *mcps], key=lambda x: (x["title"].casefold(), x["id"]))
        stats["mcp_count"] = len(mcps)
        stats["total_items"] = len(all_items)
    else:
        # Preserve deterministic behavior for isolated unit test roots while still applying AAS philosophy
        for item in raw_skills:
            enrich_with_aas_philosophy(item)
        all_items = sorted(raw_skills, key=lambda x: (x["title"].casefold(), x["id"]))
        stats = {
            "skipped_duplicates": 0,
            "fused_skills": 0,
            "unique_skills": len(all_items),
            "mcp_count": 0,
            "total_items": len(all_items),
        }

    result = {
        "items": all_items,
        "warnings": warnings,
        "partial": bool(warnings),
        "stats": stats,
        "aas_core": {
            "version": "19.8.0",
            "philosophy": AAS_PHILOSOPHY_MOTTO,
            "catalog_installed": bool(aas_map),
            "catalog_size": len(aas_map),
        },
    }
    if not custom_roots and include_aas_catalog:
        _skills_cache = result
        _skills_cache_time = time.time()
    return result


def export_local_skill_zip(skill_id: str, roots=None):
    """Package a discovered local or AAS Catalog skill directory into a downloadable ZIP archive (B10)."""
    catalogue = discover_skills(roots=roots)
    target = None
    norm_query = normalize_skill_id(skill_id)
    for item in catalogue.get("items", []):
        if item.get("kind") == "skill" and (
            item.get("id") == skill_id
            or item.get("canonical_id") == skill_id
            or item.get("canonical_id") == norm_query
            or item.get("title") == skill_id
        ):
            target = item
            break
    if not target:
        raise LookupError(f"Local skill '{skill_id}' not found")

    locations = target.get("locations") or []
    skill_dirs = []
    for loc in locations:
        p = Path(loc.get("path", ""))
        if p.is_file() and p.name.lower() == "skill.md":
            resolved_dir = p.parent.resolve()
            if resolved_dir not in skill_dirs:
                skill_dirs.append(resolved_dir)
    if not skill_dirs:
        raise LookupError(f"Skill directory for '{skill_id}' is unavailable on disk")

    primary_dir = skill_dirs[0]
    buf = io.BytesIO()
    files_manifest = {}
    total_bytes = 0
    max_total_bytes = 25 * 1024 * 1024
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv"}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(primary_dir.rglob("*")):
            try:
                if fpath.is_symlink():
                    continue
                rel_parts = fpath.relative_to(primary_dir).parts
                if any(part in skip_dirs for part in rel_parts):
                    continue
                if fpath.is_file():
                    sz = fpath.stat().st_size
                    if sz > 5 * 1024 * 1024 or (total_bytes + sz) > max_total_bytes:
                        continue
                    raw = fpath.read_bytes()
                    rel_posix = fpath.relative_to(primary_dir).as_posix()
                    zf.writestr(rel_posix, raw)
                    files_manifest[rel_posix] = {"size": len(raw), "sha256": sha256(raw).hexdigest()}
                    total_bytes += len(raw)
            except OSError:
                continue

        # If this is a fused skill with multiple underlying directories, include fused variants under variants/
        if len(skill_dirs) > 1:
            for idx, extra_dir in enumerate(skill_dirs[1:], start=1):
                extra_md = extra_dir / "SKILL.md"
                if extra_md.is_file():
                    try:
                        raw = extra_md.read_bytes()
                        rel_posix = f"references/fused-{idx}-{extra_dir.name}-SKILL.md"
                        zf.writestr(rel_posix, raw)
                        files_manifest[rel_posix] = {"size": len(raw), "sha256": sha256(raw).hexdigest()}
                    except OSError:
                        pass

        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "id": target["id"],
                    "canonical_id": target.get("canonical_id"),
                    "title": target["title"],
                    "description": target.get("description", ""),
                    "category": target.get("category"),
                    "aas_category": target.get("aas_category"),
                    "risk": target.get("risk"),
                    "source": target.get("source"),
                    "capability_dimension": target.get("capability_dimension"),
                    "trust_boundary": target.get("trust_boundary"),
                    "fused_from": target.get("fused_from", []),
                    "locations": locations,
                    "files": files_manifest,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", target.get("canonical_id") or target["title"]).strip("-") or target["id"]
    return f"{safe_name}.zip", buf.getvalue()
