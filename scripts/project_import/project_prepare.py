"""Prepare and redact Project Architecture Profiles for AI Library Ingestion.

Scans core development repositories and academic portfolio projects,
extracts architecture, tech stack, directory layouts, configuration, entry points,
masks any sensitive credentials, and prepares atomic claims for MCP ingestion.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.prepare import split_text
from scripts.chat_import.redact import redact

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PREPARED_DIR = ROOT / '.agent-tmp' / 'prepared_projects'

PROJECTS_TO_SCAN = [
    {
        'key': 'pangu-novel',
        'title': '盘古小说生成与世界观叙事系统 (Pangu-novel)',
        'path': r'D:\02_Development_Tools\Pangu-novel',
        'category': '游戏与小说创作系统',
        'domain': 'ai-narrative-engine'
    },
    {
        'key': 'autocad-2d-to-3d-antigravity',
        'title': 'AutoCAD 2D图纸转3D建模与可视化系统 (Antigravity版)',
        'path': r'D:\02_Development_Tools\AutoCAD 2D to 3D - antigravity',
        'category': 'CAD与三维几何建模',
        'domain': 'engineering-cad-3d'
    },
    {
        'key': 'autocad-2d-to-3d-codex',
        'title': 'AutoCAD 2D转3D工业图纸提取与构件管线 (Codex版)',
        'path': r'D:\02_Development_Tools\AutoCAD 2D to 3D - codex',
        'category': 'CAD与三维几何建模',
        'domain': 'engineering-cad-3d'
    },
    {
        'key': 'autocad-2d-to-3d-raw',
        'title': 'AutoCAD 2D to 3D 原始工程图样与规格定义',
        'path': r'D:\02_Development_Tools\AutoCAD 2D to 3D',
        'category': 'CAD与三维几何建模',
        'domain': 'engineering-cad-3d'
    },
    {
        'key': 'ai-workdesk-local',
        'title': 'AI Workdesk 本地操作系统与伴侣视觉中枢',
        'path': r'D:\02_Development_Tools\ai-workdesk-local',
        'category': '桌面AI操作系统与调度',
        'domain': 'desktop-ai-os'
    },
    {
        'key': 'qwen3-5-studio',
        'title': 'Qwen 3.5 本地多Agent工作室与MCP集成网关',
        'path': r'D:\02_Development_Tools\qwen3.5-studio',
        'category': '多Agent协作与模型工作室',
        'domain': 'multi-agent-studio'
    },
    {
        'key': 'live2d-maker',
        'title': 'Live2D 动态模型与动作捕捉管线制作平台',
        'path': r'D:\02_Development_Tools\live2d-maker',
        'category': '动画与互动视觉',
        'domain': 'animation-live2d'
    },
    {
        'key': 'social-app',
        'title': 'Social App 现代社交网络与交互平台',
        'path': r'D:\02_Development_Tools\social-app',
        'category': '网络与社交应用',
        'domain': 'social-platform'
    },
    {
        'key': 'ui-builder',
        'title': 'UI Builder 可视化界面构建器与组件设计系统',
        'path': r'D:\02_Development_Tools\UI-builder',
        'category': '前端工具与组件设计',
        'domain': 'frontend-tooling'
    },
    {
        'key': 'anime-3d-model',
        'title': 'Anime 3D Model 实时渲染与三维交互工程',
        'path': r'D:\02_Development_Tools\anime 3d model',
        'category': '动画与互动视觉',
        'domain': '3d-rendering'
    },
    {
        'key': 'antigravity-harness',
        'title': 'Antigravity Agent 评测基准与插件测试套件',
        'path': r'D:\02_Development_Tools\Antigravity harness',
        'category': '开发测试与自动化工具',
        'domain': 'agent-eval-harness'
    },
    {
        'key': 'map-maker',
        'title': 'Map Maker 过程式与地牢瓦片地图生成引擎',
        'path': r'D:\02_Development_Tools\map-maker',
        'category': '游戏与小说创作系统',
        'domain': 'procedural-generation'
    },
    {
        'key': 'wallpaper',
        'title': 'Wallpaper 动态壁纸、桌宠与微循环视频生成工具',
        'path': r'D:\02_Development_Tools\wallpaper',
        'category': '桌面与多媒体视觉',
        'domain': 'desktop-visual-tools'
    },
    {
        'key': 'workbuddy',
        'title': 'WorkBuddyAI 本地原生桌面工作伙伴运行时',
        'path': r'D:\02_Development_Tools\WorkBuddy',
        'category': '桌面AI操作系统与调度',
        'domain': 'desktop-ai-os'
    },
    {
        'key': 'ai-library',
        'title': 'AI Library 跨Agent统一知识操作系统与双向量中枢',
        'path': r'D:\02_Development_Tools\AI library',
        'category': '知识管理与内存系统',
        'domain': 'knowledge-os'
    },
    {
        'key': 'portfolio-react',
        'title': 'Ling Jie Wei 个人全栈作品集展示站 (Portfolio Web)',
        'path': r'D:\03_Study_Projects\Project Assignment\Ling Jie Wei Portfolio',
        'category': '个人作品集与学术展示',
        'domain': 'personal-portfolio'
    },
    {
        'key': 'academic-portfolio',
        'title': '人工智能学士与文凭专业核心学术课题体系 (Degree & Diploma Portfolio)',
        'path': r'D:\03_Study_Projects\Project Assignment\Portfolio',
        'category': '个人作品集与学术展示',
        'domain': 'academic-curriculum'
    },
    {
        'key': 'doubao-portfolio',
        'title': '豆包协作个人作品集与 Flask 展示系统',
        'path': r'D:\03_Study_Projects\Project Assignment\豆包portfolio',
        'category': '个人作品集与学术展示',
        'domain': 'personal-portfolio'
    }
]

def scan_project_dir(base_path: Path):
    if not base_path.exists():
        return {'exists': False, 'files': 0, 'tree': [], 'docs': {}, 'configs': {}, 'entrypoints': []}

    ignore_dirs = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.next', 'dist', 'build', '.agent-tmp'}
    file_count = 0
    tree = []
    docs = {}
    configs = {}
    entrypoints = []

    for root, dirs, files in os.walk(base_path):
        rel = os.path.relpath(root, base_path)
        parts = rel.split(os.sep) if rel != '.' else []
        if any(p in ignore_dirs for p in parts):
            continue
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        level = len(parts)
        if level <= 2:
            rel_prefix = '' if rel == '.' else rel + '/'
            for d in dirs[:10]:
                tree.append(f"[DIR]  {rel_prefix}{d}")
            for f in files[:15]:
                tree.append(f"[FILE] {rel_prefix}{f}")

        for f in files:
            file_count += 1
            f_lower = f.lower()
            full = Path(root) / f

            # Detect entrypoints
            if f.endswith(('.bat', '.ps1', '.exe', '.spec')):
                entrypoints.append(str(full.relative_to(base_path)))
            elif f in ('main.py', 'app.py', 'server.py', 'bridge.py', 'index.html') and level <= 1:
                entrypoints.append(str(full.relative_to(base_path)))

            # Detect configs
            if f in ('package.json', 'requirements.txt', 'pyproject.toml', 'Cargo.toml', 'CMakeLists.txt', 'pytest.ini'):
                try:
                    configs[str(full.relative_to(base_path))] = full.read_text(encoding='utf-8', errors='replace')[:4000]
                except Exception:
                    pass

            # Detect documentation
            if ('readme' in f_lower or 'design' in f_lower or 'product' in f_lower or 'decisions' in f_lower or 'spec' in f_lower) and f.endswith('.md'):
                try:
                    docs[str(full.relative_to(base_path))] = full.read_text(encoding='utf-8', errors='replace')[:12000]
                except Exception:
                    pass
            elif 'docs' in parts and f.endswith('.md'):
                try:
                    docs[str(full.relative_to(base_path))] = full.read_text(encoding='utf-8', errors='replace')[:12000]
                except Exception:
                    pass

    return {
        'exists': True,
        'files': file_count,
        'tree': tree[:60],
        'docs': docs,
        'configs': configs,
        'entrypoints': entrypoints[:20]
    }


def generate_project_profile(pdef: dict, scan: dict) -> tuple:
    title = pdef['title']
    cat = pdef['category']
    domain = pdef['domain']
    path_str = pdef['path']
    file_count = scan['files']

    doc_snippets = []
    for doc_name, content in scan['docs'].items():
        doc_snippets.append(f"### 📄 文档: `{doc_name}`\n\n```markdown\n{content[:2500]}\n```\n")

    config_snippets = []
    for cfg_name, content in scan['configs'].items():
        config_snippets.append(f"### ⚙️ 配置: `{cfg_name}`\n\n```text\n{content[:2000]}\n```\n")

    tree_str = "\n".join(scan['tree'][:40])
    entry_str = "\n".join(f"- `{e}`" for e in scan['entrypoints']) if scan['entrypoints'] else "- 无显式启动脚本，作为库或模块引入"

    full_md = f"""# 工程档案：{title}

- **项目主目录**：`{path_str}`
- **技术领域 / 域**：`{domain}`
- **业务分类**：`{cat}`
- **代码文件规模**：约 {file_count} 个文件
- **核心入口与启动项**：
{entry_str}

---

## 一、目录结构概要 (Top Layout)
```text
{tree_str}
```

---

## 二、项目核心技术规范与文档
{"".join(doc_snippets) if doc_snippets else "（本项目无独立 README，结构规范由源码目录及配置定义）"}

---

## 三、环境配置与依赖项 (Dependencies)
{"".join(config_snippets) if config_snippets else "（无独立依赖配置文件或为纯原生脚本）"}

---

## 四、AI Library 知识图谱链接
- **归属系统**：Ling Jie Wei 本地全景工程资产库
- **来源路径**：`{path_str}`
- **安全审定**：凭证敏感信息已脱敏，SHA256 完整性记录。
"""
    redacted_md, _ = redact(full_md)
    md_hash = hashlib.sha256(redacted_md.encode('utf-8')).hexdigest()

    # Generate atomic memory claims
    claims = []
    claims.append(f"项目【{title}】是用户在本地开发的核心工程，位于目录 `{path_str}`，属于【{cat}】领域，包含约 {file_count} 个文件。")

    if scan['entrypoints']:
        claims.append(f"项目【{title}】的主要执行入口和启动脚本包括：{', '.join(scan['entrypoints'][:5])}。")

    if scan['configs']:
        cfg_keys = list(scan['configs'].keys())
        claims.append(f"项目【{title}】使用以下环境与依赖配置规范：{', '.join(cfg_keys)}。")

    # Add specific domain claims
    if 'pangu' in pdef['key']:
        claims.append("Pangu-novel 是结合 PySide6、OpenRouter 语言模型和 SQLite 的 AI 叙事驱动小说与游戏内核，具备卡牌、战斗、NPC 记忆与 Obsidian 双向联动系统。")
    elif 'autocad' in pdef['key']:
        claims.append("AutoCAD 2D to 3D 体系是针对工业钢结构与仓储图纸的自动化解析流水线，通过提取 DWG/DXF 几何语义，自动重构成 3D 几何模型并提供 WebGL 交互查看器。")
    elif 'workdesk' in pdef['key'] or 'workbuddy' in pdef['key']:
        claims.append("桌面 AI 工作台与 WorkBuddy 体系提供了本地伴侣视觉、进程网桥、屏幕上下文感知以及多 Agent MCP 协作中枢。")
    elif 'qwen' in pdef['key']:
        claims.append("qwen3.5-studio 是基于 Qwen 本地多 Agent 的工作平台，内置游戏系统支持与原生 MCP 客户端网关。")
    elif 'live2d' in pdef['key']:
        claims.append("live2d-maker 是基于 Electron 与 Python 后端的 Live2D 模型制作管线，支持角色绑定、动态表情与动作捕捉驱动。")
    elif 'library' in pdef['key']:
        claims.append("AI Library 是跨 Agent 统一知识操作系统，内置 SQLite 关系数据库、EmbeddingGemma 2 双向量库，并通过原生 C# Win64 启动器为 Antigravity、WorkBuddy 和豆包提供标准 MCP 接口。")
    elif 'portfolio' in pdef['key'] or 'academic' in pdef['key']:
        claims.append(f"项目【{title}】承载了用户的人工智能专业学术成果与全栈作品集，包含毕业设计 FYP（YOLO 目标检测系统）、高等 AI 算法、计算机视觉与模式识别等核心课题。")

    summary = {
        'key': pdef['key'],
        'title': title,
        'category': cat,
        'domain': domain,
        'path': path_str,
        'file_count': file_count,
        'claims': claims,
        'hash': md_hash
    }

    return redacted_md, summary, md_hash


def main():
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    print(f"Scanning and preparing {len(PROJECTS_TO_SCAN)} core development & academic projects...")

    for pdef in PROJECTS_TO_SCAN:
        p_path = Path(pdef['path'])
        scan = scan_project_dir(p_path)
        redacted_md, summary, md_hash = generate_project_profile(pdef, scan)

        p_dir = PREPARED_DIR / pdef['key']
        p_dir.mkdir(parents=True, exist_ok=True)

        (p_dir / 'profile.md').write_text(redacted_md, encoding='utf-8')
        (p_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

        manifest.append({
            'key': pdef['key'],
            'title': pdef['title'],
            'category': pdef['category'],
            'domain': pdef['domain'],
            'path': pdef['path'],
            'file_count': scan['files'],
            'claims_count': len(summary['claims']),
            'hash': md_hash
        })
        print(f"  ✓ Prepared: {pdef['key']} ({scan['files']} files, {len(summary['claims'])} claims)")

    (PREPARED_DIR / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nAll {len(manifest)} projects prepared in {PREPARED_DIR}!")

if __name__ == '__main__':
    main()
