"""AAS Core (Agentic Awesome Skills v19.8.0) Philosophy, Taxonomy, Deduplication & Fusion Engine.

Implements the AAS Core design philosophy for both Skills and MCP servers:
  "The agent inspects and chooses. AAS records and validates. You control."

Key capabilities:
1. Standardized AAS v1/v2 metadata enrichment (`category`, `risk`, `source`,
   `capability_dimension`, `trust_boundary`, `tags`, `triggers`, `plugin_targets`).
2. 16-Domain Fleet Taxonomy mapping all 113 AAS raw categories + semantic rules
   for both Skills and MCP servers.
3. Exact-match deduplication ("一样的 skill 就跳过") across local roots and the
   installed AAS Catalog (`data/aas_catalog/package`).
4. High-similarity skill fusion ("高度相似的 skill 就融合") combining complementary
   playbooks, descriptions, tags, and locations into unified entries.
5. AAS Core `compose_stack` validation & 10-dimension capability coverage check.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


AAS_PHILOSOPHY_MOTTO = "The agent inspects and chooses. AAS records and validates. You control."

AAS_CAPABILITY_DIMENSIONS = (
    "architecture/runtime",
    "languages/frameworks",
    "domain behavior",
    "data/storage",
    "external integrations",
    "testing/quality",
    "security/privacy",
    "ux/accessibility",
    "deployment/operations",
    "maintenance workflow",
)

# Canonical alias groups where different skill names represent the same or highly overlapping intent
CANONICAL_FUSION_GROUPS: Dict[str, str] = {
    # Exact/alias equivalents -> canonical ID
    "tdd": "test-driven-development",
    "test-driven-development": "test-driven-development",
    "review": "code-review",
    "code-review": "code-review",
    "deslop": "ai-slop-cleaner",
    "ai-slop-cleaner": "ai-slop-cleaner",
    "skill-development": "skill-creator",
    "skill-developer": "skill-creator",
    "skill-creator": "skill-creator",
    "implement_plan_micro": "implement_plan",
    "implement-plan-micro": "implement_plan",
    "implement_plan": "implement_plan",
    "accessibility-testing": "accessibility-patterns",
    "accessibility-patterns": "accessibility-patterns",
    "artifact-template-simple-light-mode": "artifact-template-simple-theme-mode",
    "artifact-template-simple-dark-mode": "artifact-template-simple-theme-mode",
    "artifact-template-operating-review": "artifact-template-business-review",
    "artifact-template-business-review": "artifact-template-business-review",
    "artifact-template-strategy-memorandum": "artifact-template-legal-memorandum",
    "artifact-template-legal-memorandum": "artifact-template-legal-memorandum",
    "ask-gemini": "ask-multi-agent-consult",
    "ask-claude": "ask-multi-agent-consult",
    "output-skill": "full-output-enforcement",
    "full-output-enforcement": "full-output-enforcement",
}

# 16-Domain Taxonomy mapping AAS categories & keywords to Domain Fleets
DOMAIN_TAXONOMY: List[Dict[str, Any]] = [
    {
        "id": "agents",
        "category": "agent-orchestration",
        "domain": "Agent / 多智能体编排舰队",
        "code": "SWARM-FLAGSHIP",
        "badge": "🤖",
        "color": "#f43f5e",
        "capability_dimension": "architecture/runtime",
        "aas_categories": {
            "agent-orchestration", "ai-agents", "agents", "multi-agent", "subagents",
            "autonomy", "swarm", "orchestration", "agentic", "antigravity",
        },
        "keywords": (
            "agent", "subagent", "orchestrat", "dispatch", "swarm", "autonomous",
            "tamagotchi", "workdesk", "antigravity", "claude", "codex", "cursor",
            "copilot", "kiro", "opencode", "handoff", "delegat",
        ),
    },
    {
        "id": "cognition",
        "category": "workflow",
        "domain": "探索思考 / 认知决策舰队",
        "code": "ORACLE-SCIENCE",
        "badge": "🧠",
        "color": "#c084fc",
        "capability_dimension": "domain behavior",
        "aas_categories": {
            "workflow", "planning", "research", "reasoning", "decision-making",
            "brainstorming", "methodology", "problem-solving", "andruia",
        },
        "keywords": (
            "brainstorm", "plan", "executing-plan", "reason", "think", "solve",
            "research", "strategy", "concept", "interview", "grill", "ultrawork",
            "deep-interview", "decision", "architecture-decision",
        ),
    },
    {
        "id": "security",
        "category": "security",
        "domain": "安全防御 / 威胁审计合规舰队",
        "code": "PROTOCOL-VANGUARD",
        "badge": "🔒",
        "color": "#2dd4bf",
        "capability_dimension": "security/privacy",
        "aas_categories": {
            "security", "cybersecurity", "privacy", "compliance", "cryptography",
            "auth", "authentication", "pentesting", "secops", "governance",
        },
        "keywords": (
            "security", "auth", "oauth", "jwt", "secret", "vault", "threat",
            "vulnerab", "pentest", "sast", "dast", "owasp", "compliance", "soc2",
            "gdpr", "encryption", "hardening", "firewall", "zero-trust", "audit",
        ),
    },
    {
        "id": "data",
        "category": "data",
        "domain": "数据科学 / 知识检索与记忆舰队",
        "code": "NEXUS-SURVEYOR",
        "badge": "🌐",
        "color": "#818cf8",
        "capability_dimension": "data/storage",
        "aas_categories": {
            "data", "database", "databases", "data-science", "data-engineering",
            "analytics", "rag", "search", "knowledge", "blockchain", "web3",
        },
        "keywords": (
            "data", "sql", "postgres", "supabase", "sqlite", "mongodb", "redis",
            "clickhouse", "vector", "embed", "retriev", "rag", "search", "index",
            "knowledge", "memory", "graph", "etl", "pandas", "spark", "analytics",
        ),
    },
    {
        "id": "ai_ml",
        "category": "ai-ml",
        "domain": "AI 模型 / 机器学习与评估舰队",
        "code": "NEURAL-CORE",
        "badge": "✨",
        "color": "#e879f9",
        "capability_dimension": "domain behavior",
        "aas_categories": {
            "ai-ml", "machine-learning", "deep-learning", "llm", "nlp", "evals",
            "huggingface", "computer-vision", "fine-tuning",
        },
        "keywords": (
            "llm", "model", "training", "fine-tun", "inference", "huggingface",
            "pytorch", "tensor", "eval", "benchmark", "token", "transformer",
            "openai", "gemini", "minimax", "dataset",
        ),
    },
    {
        "id": "ui",
        "category": "frontend",
        "domain": "UI / 前端与视觉体验舰队",
        "code": "UI-CARRIER",
        "badge": "🎨",
        "color": "#38bdf8",
        "capability_dimension": "ux/accessibility",
        "aas_categories": {
            "frontend", "web-development", "design", "design-it", "ui-ux",
            "accessibility", "css", "react", "vue", "nextjs",
        },
        "keywords": (
            "ui", "ux", "frontend", "react", "vue", "svelte", "angular", "nextjs",
            "nuxt", "tailwind", "css", "html", "design", "figma", "theme", "color",
            "typography", "font", "layout", "responsive", "accessibility", "wcag",
            "a11y", "component", "canvas", "svg",
        ),
    },
    {
        "id": "backend",
        "category": "backend",
        "domain": "后端服务 / 分布式与 API 架构舰队",
        "code": "CORE-DREADNOUGHT",
        "badge": "⚙️",
        "color": "#10b981",
        "capability_dimension": "languages/frameworks",
        "aas_categories": {
            "backend", "api-integration", "api", "development", "engineering",
            "microservices", "python", "typescript", "golang", "rust", "java",
            "dotnet", "architecture",
        },
        "keywords": (
            "backend", "api", "rest", "graphql", "grpc", "websocket", "fastapi",
            "django", "flask", "express", "nestjs", "springboot", "microservice",
            "kafka", "rabbitmq", "event-driven", "python", "golang", "rust",
            "typescript", "nodejs", "orm", "prisma",
        ),
    },
    {
        "id": "qa",
        "category": "testing",
        "domain": "质量保障 / 自动化测试与浏览器舰队",
        "code": "AEGIS-BASTION",
        "badge": "🛡️",
        "color": "#60a5fa",
        "capability_dimension": "testing/quality",
        "aas_categories": {
            "testing", "qa", "quality-assurance", "debugging", "browser-automation",
            "performance", "observability",
        },
        "keywords": (
            "test", "qa", "tdd", "pytest", "jest", "vitest", "playwright",
            "cypress", "selenium", "browser", "devtools", "lighthouse", "debug",
            "diagnos", "profil", "trace", "coverage", "e2e", "verify", "validat",
        ),
    },
    {
        "id": "refactoring",
        "category": "code-quality",
        "domain": "代码重构 / 架构整洁与审查舰队",
        "code": "TITAN-BATTLESHIP",
        "badge": "💻",
        "color": "#34d399",
        "capability_dimension": "maintenance workflow",
        "aas_categories": {
            "code-quality", "refactoring", "code-review", "clean-code", "linting",
        },
        "keywords": (
            "refactor", "clean", "modular", "code-review", "receiving-code-review",
            "ast-grep", "simplif", "deslop", "slop", "dead-code", "lint",
            "formatting", "technical-debt",
        ),
    },
    {
        "id": "cloud",
        "category": "cloud",
        "domain": "云原生 / 基础设施与 SRE 舰队",
        "code": "ORBITAL-BASTION",
        "badge": "☁️",
        "color": "#06b6d4",
        "capability_dimension": "deployment/operations",
        "aas_categories": {
            "cloud", "aws", "azure", "gcp", "kubernetes", "infrastructure", "sre",
        },
        "keywords": (
            "aws", "azure", "gcp", "cloud", "kubernetes", "k8s", "helm",
            "terraform", "pulumi", "iac", "infrastructure", "prometheus", "grafana",
            "sre", "incident", "chaos", "canary", "serverless", "cloudflare",
        ),
    },
    {
        "id": "devops",
        "category": "devops",
        "domain": "DevOps / 工具链与发布部署舰队",
        "code": "FORGE-HAULER",
        "badge": "🚀",
        "color": "#f97316",
        "capability_dimension": "deployment/operations",
        "aas_categories": {
            "devops", "ci-cd", "git", "containers", "docker", "release",
            "deployment", "automation", "cli", "system",
        },
        "keywords": (
            "git", "branch", "finishing-a-development-branch", "docker", "container",
            "deploy", "ci-cd", "github-actions", "release", "build", "cli",
            "terminal", "shell", "bash", "powershell", "window-mcp", "desktop",
            "npm", "pip", "uv", "package",
        ),
    },
    {
        "id": "vfx",
        "category": "media",
        "domain": "动效 / 多媒体视听与 3D 舰队",
        "code": "VFX-CRUISER",
        "badge": "🎬",
        "color": "#a78bfa",
        "capability_dimension": "ux/accessibility",
        "aas_categories": {
            "media", "video", "audio", "image", "3d", "animation", "gaming",
            "game-development",
        },
        "keywords": (
            "animat", "motion", "audio", "sound", "video", "music", "voice",
            "speech", "podcast", "media", "render", "3d", "threejs", "webgl",
            "shader", "image", "photo", "poster", "logo", "remotion", "cad",
        ),
    },
    {
        "id": "writing",
        "category": "content",
        "domain": "创意写作 / 文档与提示词工程舰队",
        "code": "MUSE-DREADNOUGHT",
        "badge": "✍️",
        "color": "#fbbf24",
        "capability_dimension": "domain behavior",
        "aas_categories": {
            "content", "writing", "documentation", "documents", "copywriting",
            "prompts", "communication", "education",
        },
        "keywords": (
            "prompt", "writing", "novel", "story", "narrative", "copywrit", "blog",
            "translat", "essay", "content", "author", "humaniz", "document",
            "docx", "pdf", "pptx", "presentation", "slides", "markdown", "output",
        ),
    },
    {
        "id": "business",
        "category": "business",
        "domain": "商业增长 / 营销与财务战略舰队",
        "code": "CHRONOS-TREASURY",
        "badge": "📊",
        "color": "#eab308",
        "capability_dimension": "external integrations",
        "aas_categories": {
            "business", "marketing", "seo", "sales", "finance", "fintech",
            "ecommerce", "legal", "hr", "operations", "project-management",
            "productivity", "crm", "email",
        },
        "keywords": (
            "financial", "finance", "budget", "investment", "legal", "market",
            "forecast", "spreadsheet", "xlsx", "payment", "stripe", "revenue",
            "saas", "business", "accounting", "seo", "marketing", "email", "brevo",
            "crm", "linkedin", "outreach", "sales", "product-management",
        ),
    },
    {
        "id": "mobile",
        "category": "mobile",
        "domain": "移动终端 / 跨端应用开发舰队",
        "code": "MOBILE-INTERCEPTOR",
        "badge": "📱",
        "color": "#ec4899",
        "capability_dimension": "languages/frameworks",
        "aas_categories": {
            "mobile", "ios", "android", "react-native", "flutter", "expo",
        },
        "keywords": (
            "mobile", "ios", "swift", "android", "kotlin", "flutter", "expo",
            "react-native",
        ),
    },
    {
        "id": "dx",
        "category": "meta",
        "domain": "开发者效能 / 扩展元技能舰队",
        "code": "FORGE-TOOLKIT",
        "badge": "🛠️",
        "color": "#f59e0b",
        "capability_dimension": "maintenance workflow",
        "aas_categories": {
            "meta", "developer-tools", "mcp", "plugins", "skills", "dx",
        },
        "keywords": (
            "skill-creator", "skill-developer", "skill-installer", "plugin",
            "hook", "setup", "statusline", "onboard", "codebase", "doctor",
            "mcp-builder", "agy-customizations", "antigravity-guide",
        ),
    },
]

_DOMAIN_BY_ID = {d["id"]: d for d in DOMAIN_TAXONOMY}
_AAS_CAT_TO_DOMAIN: Dict[str, Dict[str, Any]] = {}
for _d in DOMAIN_TAXONOMY:
    for _c in _d["aas_categories"]:
        _AAS_CAT_TO_DOMAIN[_c.lower()] = _d


def normalize_skill_id(raw_name: str) -> str:
    """Normalize a skill or MCP name into a canonical AAS kebab-case ID."""
    s = (raw_name or "").strip().lower()
    s = re.sub(r"^mcp\s*[·:_-]\s*", "mcp-", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s).strip("-")
    s = re.sub(r"[-_]?skill$", "", s)
    return CANONICAL_FUSION_GROUPS.get(s, s) or "unnamed-skill"


def infer_risk_level(title: str, description: str, kind: str = "skill", explicit_risk: Optional[str] = None) -> str:
    """Infer AAS risk level ('none', 'safe', 'low', 'medium', 'high')."""
    if explicit_risk and str(explicit_risk).lower() in {"none", "safe", "low", "medium", "high", "critical"}:
        return str(explicit_risk).lower()
    text = f"{title} {description}".lower()
    if any(k in text for k in ("exploit", "pentest", "nsfw", "outbound phone", "payment", "production deploy", "delete", "kill", "registry")):
        return "medium"
    if kind == "mcp":
        if any(k in text for k in ("read-only", "embedding", "search")):
            return "safe"
        return "low"
    if any(k in text for k in ("brainstorm", "plan", "review", "guide", "template", "pattern", "research", "analyze", "doc")):
        return "safe"
    if any(k in text for k in ("bash", "shell", "docker", "deploy", "execute", "migration", "browser")):
        return "low"
    return "safe"


def classify_capability(
    title: str,
    description: str = "",
    raw_category: Optional[str] = None,
    kind: str = "skill",
    locations: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Classify a Skill or MCP into one of the 16 AAS-aligned Domain Fleets."""
    cat_clean = (raw_category or "").strip().lower()
    # Ignore legacy generic MCP fallback category so MCPs get properly classified by function
    if cat_clean and "系统防御合规舰队" not in cat_clean:
        if cat_clean in _AAS_CAT_TO_DOMAIN:
            return _AAS_CAT_TO_DOMAIN[cat_clean]
        for d in DOMAIN_TAXONOMY:
            if cat_clean == d["id"] or cat_clean in d["domain"].lower():
                return d

    loc_str = " ".join(str(loc.get("path", "")) for loc in (locations or []))
    title_low = (title or "").lower()
    full_text = f"{title_low} {(description or '').lower()} {cat_clean} {loc_str.lower()}"

    # Special accurate routing for known MCP servers
    if kind == "mcp":
        if "aas-core" in title_low:
            return _DOMAIN_BY_ID["dx"]
        if any(k in title_low for k in ("memory", "embedding", "knowledge", "rag")):
            return _DOMAIN_BY_ID["data"]
        if any(k in title_low for k in ("browser", "chrome", "devtools", "playwright")):
            return _DOMAIN_BY_ID["qa"]
        if any(k in title_low for k in ("supabase", "postgres", "db", "sql")):
            return _DOMAIN_BY_ID["backend"]
        if any(k in title_low for k in ("brevo", "mail", "crm", "stripe")):
            return _DOMAIN_BY_ID["business"]
        if any(k in title_low for k in ("window", "desktop", "shell", "system")):
            return _DOMAIN_BY_ID["devops"]

    best_domain = None
    best_score = 0
    for d in DOMAIN_TAXONOMY:
        score = 0
        for kw in d["keywords"]:
            if kw in title_low:
                score += 6
            elif kw in full_text:
                score += 1
        if score > best_score:
            best_score = score
            best_domain = d

    return best_domain or _DOMAIN_BY_ID["dx"]


def extract_tags_and_triggers(title: str, description: str, category: str) -> Tuple[List[str], List[str]]:
    """Extract AAS-style tags and trigger tokens from title and description."""
    tokens = [t for t in re.split(r"[^a-z0-9\u4e00-\u9fff]+", title.lower()) if len(t) >= 2]
    tags = list(dict.fromkeys([*tokens[:5], category.lower()]))
    desc_words = [
        w for w in re.findall(r"[a-zA-Z]{4,}|[\u4e00-\u9fff]{2,}", (description or "").lower())
        if w not in {"this", "that", "with", "from", "when", "your", "into", "before", "after", "using"}
    ]
    triggers = list(dict.fromkeys([*tokens, *desc_words[:8]]))[:12]
    return tags, triggers


def enrich_with_aas_philosophy(item: Dict[str, Any], aas_index_entry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Attach AAS Core v19.8.0 design philosophy metadata to a Skill or MCP entry."""
    kind = item.get("kind", "skill")
    raw_title = item.get("title") or item.get("name") or ""
    canonical_id = (aas_index_entry or {}).get("canonical_id") or (aas_index_entry or {}).get("id") or normalize_skill_id(raw_title)
    raw_cat = (aas_index_entry or {}).get("category") or item.get("aas_category") or item.get("category")
    desc = item.get("description") or (aas_index_entry or {}).get("description") or ""

    domain_def = classify_capability(
        title=raw_title,
        description=desc,
        raw_category=raw_cat,
        kind=kind,
        locations=item.get("locations"),
    )

    explicit_risk = (aas_index_entry or {}).get("risk") or item.get("risk")
    if kind == "mcp" and "aas-core" in raw_title.lower():
        explicit_risk = "safe"
    risk = infer_risk_level(raw_title, desc, kind=kind, explicit_risk=explicit_risk)

    sources_set = {loc.get("source", "") for loc in item.get("locations", []) if loc.get("source")}
    if aas_index_entry and aas_index_entry.get("source"):
        source_label = aas_index_entry["source"]
    elif any("当前项目" in s for s in sources_set):
        source_label = "local-workspace"
    elif kind == "mcp":
        source_label = "mcp-control-plane"
    else:
        source_label = "local-curated"

    tags = (aas_index_entry or {}).get("tags") or item.get("tags")
    triggers = (aas_index_entry or {}).get("triggers") or item.get("triggers")
    if not tags or not triggers:
        gen_tags, gen_triggers = extract_tags_and_triggers(raw_title, desc, domain_def["category"])
        tags = tags or gen_tags
        triggers = triggers or gen_triggers

    if kind == "mcp":
        tools = item.get("tools") or []
        read_tools = [t["name"] for t in tools if re.match(r"^(get|list|search|read|query|inspect|fetch|view|take_snapshot|browser_snapshot|compose_stack|inspect_stack)", t.get("name", ""))]
        write_tools = [t["name"] for t in tools if t.get("name") not in read_tools]
        trust_boundary = (
            f"AAS MCP 控制平面契约：{AAS_PHILOSOPHY_MOTTO} "
            f"只读探查工具 ({len(read_tools)} 项) 在授权范围内确定性执行；"
            f"状态变更工具 ({len(write_tools)} 项) 须遵循显式写入审批与最小权限边界。"
        )
    else:
        trust_boundary = (
            f"AAS Core 技能契约：{AAS_PHILOSOPHY_MOTTO} "
            f"此剧本为只读能力规范（风险等级: {risk.upper()}），由 Agent 按需检索、组合入栈 (compose_stack) 并经人工确认后选择性激活，严禁全量盲目挂载污染上下文。"
        )

    item["canonical_id"] = canonical_id
    item["category"] = domain_def["domain"]
    item["domain"] = domain_def["domain"]
    item["domainId"] = domain_def["id"]
    item["aas_category"] = (aas_index_entry or {}).get("category") or domain_def["category"]
    item["risk"] = risk
    item["source"] = source_label
    item["capability_dimension"] = domain_def["capability_dimension"]
    item["trust_boundary"] = trust_boundary
    item["aas_motto"] = AAS_PHILOSOPHY_MOTTO
    item["tags"] = tags[:10]
    item["triggers"] = triggers[:12]
    item["plugin_targets"] = (aas_index_entry or {}).get("plugin", {}).get(
        "targets",
        {"codex": "supported", "claude": "supported", "gemini": "supported", "antigravity": "supported"},
    )
    return item


def merge_skill_records(primary: Dict[str, Any], secondary: Dict[str, Any], *, is_fusion: bool = False) -> Dict[str, Any]:
    """Merge an identical or highly similar skill into the primary record."""
    existing_locs = primary.setdefault("locations", [])
    for loc in secondary.get("locations", []):
        if loc not in existing_locs:
            existing_locs.append(loc)

    d1 = (primary.get("description") or "").strip()
    d2 = (secondary.get("description") or "").strip()
    if is_fusion:
        fused_list = primary.setdefault("fused_from", [primary.get("title", primary.get("id"))])
        sec_title = secondary.get("title") or secondary.get("id")
        if sec_title and sec_title not in fused_list:
            fused_list.append(sec_title)
        if d2 and d2.lower() not in d1.lower():
            if d1:
                primary["description"] = f"{d1}\n\n【融合互补能力 · {sec_title}】\n{d2}"[:6000]
            else:
                primary["description"] = d2[:6000]
        primary["source"] = "fused-composite"
    else:
        if len(d2) > len(d1) and (not d1 or "参见" in d1):
            primary["description"] = d2

    for field in ("tags", "triggers"):
        merged = list(dict.fromkeys([*(primary.get(field) or []), *(secondary.get(field) or [])]))
        primary[field] = merged[:15]

    return primary


def deduplicate_and_fuse_skills(
    raw_skills: List[Dict[str, Any]],
    aas_catalog_map: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Execute 2-stage Exact Deduplication + High-Similarity Fusion across all skills."""
    by_canonical: Dict[str, Dict[str, Any]] = {}
    skipped_exact = 0
    fused_count = 0

    for item in raw_skills:
        raw_title = (item.get("title") or "").strip()
        norm_id = normalize_skill_id(raw_title)
        aas_entry = aas_catalog_map.get(norm_id) or aas_catalog_map.get(raw_title.lower())
        enrich_with_aas_philosophy(item, aas_entry)
        cid = item["canonical_id"]

        if cid in by_canonical:
            existing = by_canonical[cid]
            if existing["title"].strip().lower() == raw_title.lower():
                skipped_exact += 1
                merge_skill_records(existing, item, is_fusion=False)
            else:
                fused_count += 1
                merge_skill_records(existing, item, is_fusion=True)
        else:
            item["id"] = f"skill-{cid}"
            by_canonical[cid] = item

    # Stage 2: High-similarity fusion within the same domain + shared prefix token bucket
    prefix_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    domain_local_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for cid, sk in list(by_canonical.items()):
        dom_id = sk.get("domainId", "dx")
        first_tok = re.split(r"[-_]+", cid)[0]
        if len(first_tok) >= 3:
            prefix_buckets.setdefault((dom_id, first_tok), []).append(sk)
        if any(loc.get("source") != "AAS Catalog v19.8.0" for loc in sk.get("locations", [])):
            domain_local_buckets.setdefault(dom_id, []).append(sk)

    removed_cids = set()

    def _fuse_bucket(bucket: List[Dict[str, Any]], max_size: int = 250) -> None:
        nonlocal fused_count
        if len(bucket) < 2 or len(bucket) > max_size:
            return
        bucket.sort(key=lambda x: (
            0 if any(loc.get("source") != "AAS Catalog v19.8.0" for loc in x.get("locations", [])) else 1,
            len(x.get("title", "")),
            x.get("title", "").lower(),
        ))
        for i in range(len(bucket)):
            s1 = bucket[i]
            c1 = s1["canonical_id"]
            if c1 in removed_cids:
                continue
            t1 = s1.get("title", "").lower()
            d1 = (s1.get("description") or "").lower()[:240]
            tokens1 = set(re.split(r"[-_]+", c1))
            for j in range(i + 1, len(bucket)):
                s2 = bucket[j]
                c2 = s2["canonical_id"]
                if c2 in removed_cids or c1 == c2:
                    continue
                t2 = s2.get("title", "").lower()
                if re.match(r"^\d+-", t1) or re.match(r"^\d+-", t2):
                    continue
                # Fast length & token pre-filter to avoid expensive SequenceMatcher on unrelated items
                if abs(len(t1) - len(t2)) > max(len(t1), len(t2)) * 0.45:
                    continue
                tokens2 = set(re.split(r"[-_]+", c2))
                if not (tokens1 & tokens2) and t1[:4] != t2[:4]:
                    continue
                d2 = (s2.get("description") or "").lower()[:240]
                if t1.startswith("artifact-template-") and t2.startswith("artifact-template-"):
                    name_sim = SequenceMatcher(None, t1, t2).ratio()
                    desc_sim = SequenceMatcher(None, d1, d2).ratio() if d1 and d2 else 0.0
                    should_fuse = name_sim >= 0.85 and desc_sim >= 0.80
                else:
                    jaccard = len(tokens1 & tokens2) / max(1, len(tokens1 | tokens2))
                    name_sim = SequenceMatcher(None, t1, t2).ratio()
                    desc_sim = SequenceMatcher(None, d1, d2).ratio() if d1 and d2 else 0.0
                    should_fuse = (name_sim >= 0.82 and desc_sim >= 0.65) or (jaccard >= 0.68 and name_sim >= 0.78 and desc_sim >= 0.50)

                if should_fuse:
                    merge_skill_records(s1, s2, is_fusion=True)
                    removed_cids.add(c2)
                    by_canonical.pop(c2, None)
                    fused_count += 1

    for b in domain_local_buckets.values():
        _fuse_bucket(b, max_size=300)
    for b in prefix_buckets.values():
        _fuse_bucket(b, max_size=120)

    final_skills = sorted(by_canonical.values(), key=lambda x: (x["title"].casefold(), x["id"]))
    return final_skills, {
        "skipped_duplicates": skipped_exact,
        "fused_skills": fused_count,
        "unique_skills": len(final_skills),
    }


def compose_aas_stack(
    selected_ids: List[str],
    all_items: List[Dict[str, Any]],
    project_name: str = "ai-library-knowledge-os",
) -> Dict[str, Any]:
    """Validate selected Skill/MCP IDs per AAS Core `compose_stack` contract (max 128)."""
    if not isinstance(selected_ids, list) or not selected_ids:
        raise ValueError("At least one skill or MCP ID is required to compose an AAS stack")
    if len(selected_ids) > 128:
        raise ValueError("AAS Core stack manifest exceeds technical maximum of 128 skills")

    by_id = {}
    for item in all_items:
        by_id[item["id"]] = item
        if item.get("canonical_id"):
            by_id[item["canonical_id"]] = item
        if item.get("title"):
            by_id[item["title"].lower()] = item

    resolved = []
    missing = []
    seen = set()
    for raw_id in selected_ids:
        clean = str(raw_id).strip()
        norm = normalize_skill_id(clean)
        match = by_id.get(clean) or by_id.get(norm) or by_id.get(clean.lower())
        if not match:
            missing.append(clean)
            continue
        cid = match.get("canonical_id") or match["id"]
        if cid in seen:
            continue
        seen.add(cid)
        resolved.append(match)

    if missing:
        raise ValueError(f"Unrecognized catalog IDs: {', '.join(missing)}")

    covered_dimensions: Dict[str, List[str]] = {dim: [] for dim in AAS_CAPABILITY_DIMENSIONS}
    for item in resolved:
        dim = item.get("capability_dimension", "maintenance workflow")
        covered_dimensions.setdefault(dim, []).append(item.get("canonical_id") or item["id"])

    gaps = [dim for dim, ids in covered_dimensions.items() if not ids]

    manifest = {
        "schemaVersion": 2,
        "aasCoreVersion": "19.8.0",
        "philosophy": AAS_PHILOSOPHY_MOTTO,
        "project": {
            "name": project_name,
            "target": "antigravity-codex-claude-gemini",
            "selectiveActivation": True,
        },
        "skills": [
            {
                "id": item.get("canonical_id") or item["id"],
                "kind": item.get("kind", "skill"),
                "category": item.get("aas_category", "meta"),
                "domain": item.get("domain", ""),
                "risk": item.get("risk", "safe"),
                "source": item.get("source", "local"),
                "capability_dimension": item.get("capability_dimension", ""),
                "fused_from": item.get("fused_from", []),
            }
            for item in resolved
        ],
        "coverage": {
            "covered_dimensions": {k: v for k, v in covered_dimensions.items() if v},
            "catalog_gaps": gaps,
            "coverage_ratio": round((len(AAS_CAPABILITY_DIMENSIONS) - len(gaps)) / len(AAS_CAPABILITY_DIMENSIONS), 2),
        },
    }
    return manifest
