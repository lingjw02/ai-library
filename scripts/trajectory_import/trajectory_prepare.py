"""Prepare and aggregate all 76 Antigravity SQLite conversation trajectories (26,945 steps).

Scans ~/.gemini/antigravity-cli/conversations/*.db, clusters sessions by engineering workspace,
extracts user prompts, tool action patterns, associated artifacts, and generates
structured development trajectory profiles and atomic AI Memory claims.
"""
from collections import defaultdict
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.chat_import.prepare import split_text
from scripts.chat_import.redact import redact

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PREPARED_DIR = ROOT / '.agent-tmp' / 'prepared_trajectories'
CLI_CONVERSATIONS_DIR = Path(r'C:\Users\ling5\.gemini\antigravity-cli\conversations')
CLI_BRAIN_DIR = Path(r'C:\Users\ling5\.gemini\antigravity-cli\brain')
IDE_BRAIN_DIR = Path(r'C:\Users\ling5\.gemini\antigravity-ide\brain')
V1_BRAIN_DIR = Path(r'C:\Users\ling5\.gemini\antigravity\brain')

CLUSTER_DEFINITIONS = [
    {
        'key': 'traj-map-maker',
        'title': '过程式地图与地牢生成系统研发轨迹 (Map Maker)',
        'category': '游戏系统与过程式生成',
        'match': ['map-maker'],
        'summary': '涵盖 4,099 个执行步骤，重点解决了 Watabou 过程式地貌算法适配、地牢拓扑瓦片连接、地形层级生成与 Web 地图编辑器的交互开发。'
    },
    {
        'key': 'traj-autocad-2d-to-3d',
        'title': 'AutoCAD 2D图纸转3D建模管线研发轨迹 (AutoCAD 2D to 3D)',
        'category': '工业CAD与三维几何系统',
        'match': ['AutoCAD 2D to 3D', 'agy', 'Ori'],
        'summary': '涵盖 4,017 个执行步骤，重点攻克了工业仓储钢结构 DWG/DXF 几何图元语义提取、构件拓扑关联、三维网格自动生成与 WebGL 交互查看器。'
    },
    {
        'key': 'traj-fyp-degree-ai',
        'title': '大学毕业设计：计算机视觉与YOLO目标检测研发轨迹 (FYP AI)',
        'category': 'AI专业学术与毕业设计',
        'match': ['FYP', '0 FYP Project'],
        'summary': '涵盖 1,360 个执行步骤，重点开展了 YOLO 深度学习模型训练调优、数据集标注清洗、检测推理性能评估与学士毕业设计核心系统构建。'
    },
    {
        'key': 'traj-academic-portfolio',
        'title': '学术与专业作品集全栈站研发轨迹 (Portfolio)',
        'category': 'AI专业学术与毕业设计',
        'match': ['Project Assignment', 'Portfolio'],
        'summary': '涵盖 2,926 个执行步骤，包含了个人全栈交互作品集站（React / Vite / Python）、学士学位与文凭课程综合设计展示与 CV 交互平台。'
    },
    {
        'key': 'traj-ai-library',
        'title': 'AI Library 跨Agent知识操作系统研发轨迹 (Knowledge OS)',
        'category': '桌面Agent与知识系统',
        'match': ['AI library'],
        'summary': '涵盖 1,219 个执行步骤，完成了跨 Agent 统一知识架构、SQLite 权威存储、双 MCP 服务协议、EmbeddingGemma 2 本地向量检索与 C# Win64 启动器构建。'
    },
    {
        'key': 'traj-antigravity-harness',
        'title': 'Antigravity Agent 评测基准套件研发轨迹 (Harness)',
        'category': '桌面Agent与知识系统',
        'match': ['Antigravity harness'],
        'summary': '涵盖 1,111 个执行步骤，搭建了面向 Agent 的自动化基准评测管线、插件生命周期测试与高负载环境验证。'
    },
    {
        'key': 'traj-ui-builder',
        'title': 'UI Builder 可视化界面构建平台研发轨迹 (UI-builder)',
        'category': '前端与动捕视觉工程',
        'match': ['UI-builder'],
        'summary': '涵盖 1,123 个执行步骤，实现了组件拖拽画布、属性实时绑定面板、Tailwind / PostCSS 样式生成与页面工程化导出。'
    },
    {
        'key': 'traj-pet-wallpaper',
        'title': 'Wallpaper 动态壁纸与桌宠生成管线研发轨迹 (Wallpaper)',
        'category': '桌面Agent与知识系统',
        'match': ['wallpaper', 'pet'],
        'summary': '涵盖 1,265 个执行步骤，完成了基于 Python / MoviePy 的动态微循环视频壁纸合成、深度图渲染以及 Emilia 桌面宠物交互原型。'
    },
    {
        'key': 'traj-social-app',
        'title': 'Social App 现代社交网络平台研发轨迹 (Social Network)',
        'category': '前端与动捕视觉工程',
        'match': ['social-app', 'apu-social'],
        'summary': '涵盖 751 个执行步骤，构建了动态信息流、用户交互状态机、前端响应式界面与轻量化后端数据服务。'
    },
    {
        'key': 'traj-live2d-maker',
        'title': 'Live2D 动态模型与动捕制作管线研发轨迹 (Live2D)',
        'category': '前端与动捕视觉工程',
        'match': ['live2d-maker'],
        'summary': '涵盖 550 个执行步骤，实现了 Electron 宿主架构、Python 动作后端连接、Live2D Cubism 模型渲染与表情参数调试。'
    },
    {
        'key': 'traj-ai-workdesk',
        'title': 'AI Workdesk 本地调度工作台与伴侣中枢研发轨迹 (Workdesk)',
        'category': '桌面Agent与知识系统',
        'match': ['ai-workdesk-local', 'Workdesk 2'],
        'summary': '涵盖 323 个执行步骤，规划了本地 AI 工作台架构规范、屏幕视觉感知流、进程间网桥协议以及多 Agent 桌面调度。'
    },
    {
        'key': 'traj-desktop-skills',
        'title': '桌面自动化脚本与自定义技能体系研发轨迹 (Skills & Scripts)',
        'category': '桌面Agent与知识系统',
        'match': ['Skills', 'Scripts'],
        'summary': '涵盖 1,308 个执行步骤，沉淀了桌面端环境探测、自动化批处理脚本、自定义 Agent 技能开发与调试规范。'
    },
    {
        'key': 'traj-specialized-creatives',
        'title': '创意互动与专业课程实践轨迹 (Games, Media & Academics)',
        'category': '游戏系统与过程式生成',
        'match': ['LetterSea', 'AV Director Life', 'Imaging and Special Effect', 'Emergent Technology', 'Diploma', 'emilia', 'Stable Diffucsion', 'Ollama'],
        'summary': '涵盖 1,600+ 个执行步骤，囊括了 LetterSea 虚拟角色信件互动、游戏交互设计、数字图像与特效处理、新兴技术探索与本地模型部署实践。'
    },
    {
        'key': 'traj-system-workspace',
        'title': '系统环境维护与全局工程调度轨迹 (System & Root)',
        'category': '桌面Agent与知识系统',
        'match': ['D:/', 'C:/Users/ling5', 'C:/', '02_Development_Tools'],
        'summary': '涵盖 5,000+ 个执行步骤，记录了系统全局依赖排查、多项目路径重构、环境配置维护与跨目录自动化调度演进。'
    }
]


def extract_session_details(db_path: Path):
    cid = db_path.stem
    c = sqlite3.connect(db_path)
    step_count = c.execute('SELECT count(*) FROM steps').fetchone()[0]
    meta_row = c.execute('SELECT * FROM trajectory_metadata_blob').fetchone()
    ws = 'Unknown'
    if meta_row and meta_row[1]:
        blob = meta_row[1]
        m = re.findall(rb'file:///[A-Za-z0-9%_/.:-]+', blob)
        if m:
            ws = urllib.parse.unquote(m[0].decode('utf-8', errors='ignore')).replace('file:///', '')

    # Check for brain logs and artifacts
    prompts = []
    artifacts = []

    b_dir = CLI_BRAIN_DIR / cid
    if b_dir.exists():
        tpath = b_dir / '.system_generated' / 'logs' / 'transcript.jsonl'
        if tpath.exists():
            with open(tpath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"USER_INPUT"' in line:
                        try:
                            obj = json.loads(line)
                            p = obj.get('content', '').strip()
                            if p and len(p) < 300 and not p.startswith('<'):
                                prompts.append(p)
                        except Exception:
                            pass
        for md in b_dir.glob('*.md'):
            artifacts.append(md.name)

    return {
        'id': cid,
        'steps': step_count,
        'workspace': ws,
        'prompts': prompts[:5],
        'artifacts': artifacts
    }


def main():
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    dbs = list(CLI_CONVERSATIONS_DIR.glob('*.db'))
    print(f"Scanning {len(dbs)} SQLite conversation databases...")

    sessions_by_cluster = defaultdict(list)
    unassigned = []

    for db in dbs:
        info = extract_session_details(db)
        ws = info['workspace']

        assigned = False
        for cdef in CLUSTER_DEFINITIONS:
            if any(m.lower() in ws.lower() for m in cdef['match']):
                sessions_by_cluster[cdef['key']].append(info)
                assigned = True
                break
        if not assigned:
            sessions_by_cluster['traj-system-workspace'].append(info)

    manifest = []
    print("\n=== Aggregating Trajectory Clusters ===")

    for cdef in CLUSTER_DEFINITIONS:
        ckey = cdef['key']
        cl_sessions = sessions_by_cluster.get(ckey, [])
        total_steps = sum(s['steps'] for s in cl_sessions)

        all_prompts = []
        all_artifacts = set()
        workspaces = set()

        for s in cl_sessions:
            workspaces.add(s['workspace'])
            all_prompts.extend(s['prompts'])
            all_artifacts.update(s['artifacts'])

        prompt_samples = "\n".join(f"- {p}" for p in all_prompts[:8]) if all_prompts else "- 终端指令驱动与自动化脚本流"
        artifact_samples = "\n".join(f"- `{a}`" for a in list(all_artifacts)[:8]) if all_artifacts else "- 无独立 Markdown 报告产出"
        ws_list = "\n".join(f"- `{w}`" for w in list(workspaces)[:6])

        profile_md = f"""# 研发实战轨迹画像：{cdef['title']}

- **聚合标识**：`{ckey}`
- **所属类别**：`{cdef['category']}`
- **涉及会话总数**：{len(cl_sessions)} 组深度对话库
- **累计研发步骤 (Steps)**：{total_steps} 个执行步骤
- **关联工作空间**：
{ws_list}

---

## 一、实战攻关与核心成就概述
{cdef['summary']}

---

## 二、代表性研发意图与交互任务
{prompt_samples}

---

## 三、关键交付件与架构文档
{artifact_samples}

---

## 四、AI Library 知识图谱与经验流转
- **数据来源**：本地 Antigravity 底层 SQLite 数据库（`~/.gemini/antigravity-cli/conversations/`）
- **经验价值**：收录了高强度的代码编写、依赖调试、参数排查与多工具链协同记录。
- **凭据脱敏**：包含的安全信息已通过脱敏过滤。
"""
        redacted_md, _ = redact(profile_md)
        text_hash = hashlib.sha256(redacted_md.encode('utf-8')).hexdigest()

        claims = [
            f"研发实战轨迹【{cdef['title']}】包含了 {len(cl_sessions)} 组会话共计 {total_steps} 个执行步骤，属于【{cdef['category']}】领域。",
            f"攻关亮点：{cdef['summary']}"
        ]
        if all_prompts:
            claims.append(f"典型研发任务与用户意图包括：{'; '.join(all_prompts[:3])}。")
        if all_artifacts:
            claims.append(f"该领域沉淀的代表性交付文档包括：{', '.join(list(all_artifacts)[:4])}。")

        c_dir = PREPARED_DIR / ckey
        c_dir.mkdir(parents=True, exist_ok=True)

        summary_data = {
            'key': ckey,
            'title': cdef['title'],
            'category': cdef['category'],
            'total_steps': total_steps,
            'sessions_count': len(cl_sessions),
            'summary': cdef['summary'],
            'claims': claims,
            'hash': text_hash
        }

        (c_dir / 'profile.md').write_text(redacted_md, encoding='utf-8')
        (c_dir / 'summary.json').write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding='utf-8')

        manifest.append({
            'key': ckey,
            'title': cdef['title'],
            'category': cdef['category'],
            'total_steps': total_steps,
            'sessions_count': len(cl_sessions),
            'claims_count': len(claims),
            'hash': text_hash
        })
        print(f"  ✓ Prepared Cluster: {cdef['title']} ({len(cl_sessions)} sessions, {total_steps} steps, {len(claims)} claims)")

    (PREPARED_DIR / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nAll {len(manifest)} trajectory clusters prepared in {PREPARED_DIR}!")

if __name__ == '__main__':
    main()
