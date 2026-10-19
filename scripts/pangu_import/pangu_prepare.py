"""Prepare and redact Pangu Narrative & Game Systems for AI Library Ingestion.

Scans the 11 system design milestone plans, 16 developer reference contracts,
and the LetterSea character correspondence system, generating structured profiles
and atomic AI Memory claims.
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

PREPARED_DIR = ROOT / '.agent-tmp' / 'prepared_pangu'
PANGU_DIR = Path(r'D:\02_Development_Tools\Pangu-novel')
PLANS_DIR = PANGU_DIR / 'docs' / 'plans'
SKILLS_DIR = PANGU_DIR / 'skills' / 'pangu-novel-dev'
LETTERSEA_DIR = Path(r'D:\04_Games_Entertainment\correspondence-app\LetterSea')

PLAN_METADATA = {
    '2026-09-26-m1-playable-kernel.md': {
        'key': 'pangu-m1-kernel',
        'title': '盘古AI M1：可玩内核与主轮转循环状态机',
        'category': '游戏核心架构与轮转系统',
        'node_type': 'Concept',
        'summary': '定义了盘古系统的游戏主循环（Turn Loop）、状态机流转、玩家行动提交、确定性随机骰子算法与状态历史回溯验证。'
    },
    '2026-09-26-p2-memory-cards.md': {
        'key': 'pangu-p2-cards',
        'title': '盘古AI P2：记忆卡牌系统与动态上下文触发',
        'category': '卡牌与叙事记忆系统',
        'node_type': 'Concept',
        'summary': '实现了动态记忆卡牌生命周期管理、关键词检索触发、卡牌置顶激活以及向大模型叙事上下文的动态注入机制。'
    },
    '2026-10-03-p3-character-inventory.md': {
        'key': 'pangu-p3-inventory',
        'title': '盘古AI P3：角色属性与物品背包经济体系',
        'category': '角色养成与物品系统',
        'node_type': 'Concept',
        'summary': '规范了角色六维核心属性、装备槽位、负重计算、物品稀有度分级、耐久度磨损与交易估值算法。'
    },
    '2026-10-03-p4-combat.md': {
        'key': 'pangu-p4-combat',
        'title': '盘古AI P4：回合制战术对抗与状态数值系统',
        'category': '战术对抗与数值系统',
        'node_type': 'Concept',
        'summary': '构建了对抗判定公式、护甲免伤减免曲线、暴击/闪避判定、状态异常（Buff/Debuff）结算以及悬浮战斗界面规范。'
    },
    '2026-10-05-p5-npc-characters.md': {
        'key': 'pangu-p5-npc',
        'title': '盘古AI P5：自治NPC行为、好感度与对话系统',
        'category': 'NPC智能与社交系统',
        'node_type': 'Concept',
        'summary': '设计了基于好感度矩阵的 NPC 自治决策树、性格特质嵌入、动态情绪演算以及沉浸式自然语言对话生成引擎。'
    },
    '2026-10-06-p6-memory.md': {
        'key': 'pangu-p6-memory',
        'title': '盘古AI P6：分层记忆流转网络与SQLite FTS5检索',
        'category': '卡牌与叙事记忆系统',
        'node_type': 'Concept',
        'summary': '实现了多层级记忆流动网络（短期工作缓冲 -> 长期情景库）、SQLite FTS5 全文检索引擎与上下文预算截断器。'
    },
    '2026-10-07-p7-skills-classes.md': {
        'key': 'pangu-p7-skills',
        'title': '盘古AI P7：职业转职分支与主动被动技能树',
        'category': '角色养成与物品系统',
        'node_type': 'Concept',
        'summary': '定义了多元化职业进阶树、主动技能释放消耗、被动天赋强化节点以及技能等级熟练度计算契约。'
    },
    '2026-10-10-p8-extended-systems.md': {
        'key': 'pangu-p8-extended',
        'title': '盘古AI P8：信件邮件、成就奖励与派对协同体系',
        'category': '社交与系统扩展',
        'node_type': 'Concept',
        'summary': '扩展了游戏内异步信件通信、成就达标事件监听与自动发奖、全服/局域经济流转以及队友随从组队协作机制。'
    },
    '2026-10-10-p9-map-m5.md': {
        'key': 'pangu-p9-map',
        'title': '盘古AI P9：过程式地牢瓦片与大世界探索系统',
        'category': '世界探索与过程式生成',
        'node_type': 'Concept',
        'summary': '融合了过程式地貌生成、地牢瓦片拓扑连接、迷雾探索视距演算与 Map Panel 可视化交互面板。'
    },
    '2026-10-11-p10-ui-layout.md': {
        'key': 'pangu-p10-ui',
        'title': '盘古AI P10：PySide6 现代UI布局与Obsidian双向联动',
        'category': '界面工程与外部联动',
        'node_type': 'Concept',
        'summary': '实现了暗黑/亮色主题切换、可折叠多面板布局持久化、以及与 Obsidian 外部笔记库的双向 Markdown 实时同步屏幕。'
    },
    '2026-10-11-p11-engineering.md': {
        'key': 'pangu-p11-engineering',
        'title': '盘古AI P11：工程自动化测试、崩溃遥测与稳态基建',
        'category': '工程基建与质量保障',
        'node_type': 'Concept',
        'summary': '构建了 pytest 自动化单元与集成测试套件、崩溃栈自动抓取日志与本地遥测、以及 PyInstaller 单文件打包发布流水线。'
    }
}


def extract_key_sections(text: str) -> list:
    """Extract key headings and sub-clauses from markdown plan."""
    lines = text.splitlines()
    claims = []
    current_heading = ""
    for line in lines:
        line_s = line.strip()
        if line_s.startswith('## '):
            current_heading = line_s.lstrip('#').strip()
        elif line_s.startswith('- **') and current_heading and len(claims) < 6:
            claim_text = line_s.lstrip('- ').strip()
            # Clean formatting
            claim_clean = re.sub(r'[*_`]', '', claim_text)
            if len(claim_clean) > 20 and len(claim_clean) < 250:
                claims.append(f"【{current_heading}】{claim_clean}")
    return claims


def main():
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    print("=== Scanning and preparing Pangu Narrative & Game Systems ===")

    # 1. Prepare 11 Milestone Plans
    for fname, meta in PLAN_METADATA.items():
        fpath = PLANS_DIR / fname
        if not fpath.exists():
            continue
        raw_text = fpath.read_text(encoding='utf-8', errors='replace')
        redacted_text, _ = redact(raw_text)
        text_hash = hashlib.sha256(redacted_text.encode('utf-8')).hexdigest()

        extracted_claims = extract_key_sections(redacted_text)
        claims = [
            f"盘古AI子系统【{meta['title']}】是游戏与小说叙事系统的核心支柱，文档位于 `{fpath}`。",
            f"设计概要：{meta['summary']}"
        ]
        claims.extend(extracted_claims[:4])

        item_dir = PREPARED_DIR / meta['key']
        item_dir.mkdir(parents=True, exist_ok=True)

        summary_data = {
            'key': meta['key'],
            'title': meta['title'],
            'category': meta['category'],
            'node_type': meta['node_type'],
            'summary': meta['summary'],
            'source_path': str(fpath),
            'claims': claims,
            'hash': text_hash
        }

        (item_dir / 'document.md').write_text(redacted_text, encoding='utf-8')
        (item_dir / 'summary.json').write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding='utf-8')

        manifest.append({
            'key': meta['key'],
            'title': meta['title'],
            'category': meta['category'],
            'node_type': meta['node_type'],
            'size_bytes': len(redacted_text.encode('utf-8')),
            'claims_count': len(claims),
            'hash': text_hash
        })
        print(f"  ✓ Prepared Milestone: {meta['title']} ({len(claims)} claims)")

    # 2. Prepare Developer Skills & Protocol References
    if SKILLS_DIR.exists():
        refs_dir = SKILLS_DIR / 'references'
        if refs_dir.exists():
            ref_docs = []
            for rf in sorted(refs_dir.glob('*.md')):
                ref_docs.append(f"### 📑 协议参考：`{rf.name}`\n\n{rf.read_text(encoding='utf-8', errors='replace')[:2000]}\n")

            skill_main = (SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8', errors='replace') if (SKILLS_DIR / 'SKILL.md').exists() else ""
            combined_skills = f"# 盘古AI开发者规范与核心协议参考集 (Pangu-Novel Dev Skills)\n\n{skill_main}\n\n## 协议参考库\n\n" + "\n".join(ref_docs)
            redacted_skill, _ = redact(combined_skills)
            skill_hash = hashlib.sha256(redacted_skill.encode('utf-8')).hexdigest()

            skill_claims = [
                "盘古AI拥有专属开发规范与技能体系 (pangu-novel-dev skill)，涵盖16篇核心系统接口协议与契约约束。",
                "开发规范约束包括：卡牌生命周期契约、对抗计算公式、数据库版本迁移规范、NPC行为约束与自动化测试基准。",
                "所有子系统均通过严格的接口解耦设计，确保回合流转、AI提示词解析与UI渲染的高容错性。"
            ]

            s_dir = PREPARED_DIR / 'pangu-dev-contracts'
            s_dir.mkdir(parents=True, exist_ok=True)
            (s_dir / 'document.md').write_text(redacted_skill, encoding='utf-8')
            (s_dir / 'summary.json').write_text(json.dumps({
                'key': 'pangu-dev-contracts',
                'title': '盘古AI开发者规范与16项协议契约参考集',
                'category': '开发测试与协议标准',
                'node_type': 'Concept',
                'summary': '汇总了盘古系统所有接口规范、数据契约、卡牌生命周期与数据库迁移设计参考。',
                'source_path': str(SKILLS_DIR),
                'claims': skill_claims,
                'hash': skill_hash
            }, ensure_ascii=False, indent=2), encoding='utf-8')

            manifest.append({
                'key': 'pangu-dev-contracts',
                'title': '盘古AI开发者规范与16项协议契约参考集',
                'category': '开发测试与协议标准',
                'node_type': 'Concept',
                'size_bytes': len(redacted_skill.encode('utf-8')),
                'claims_count': len(skill_claims),
                'hash': skill_hash
            })
            print("  ✓ Prepared Pangu Developer Skills & Reference Protocols")

    # 3. Prepare LetterSea Correspondence System
    if LETTERSEA_DIR.exists():
        l_readme = (LETTERSEA_DIR / 'README.md').read_text(encoding='utf-8', errors='replace') if (LETTERSEA_DIR / 'README.md').exists() else ""
        l_text = f"# LetterSea：跨角色信件海叙事交互系统\n\n路径：`{LETTERSEA_DIR}`\n\n{l_readme}\n"
        redacted_l, _ = redact(l_text)
        l_hash = hashlib.sha256(redacted_l.encode('utf-8')).hexdigest()

        l_claims = [
            f"LetterSea 是位于 `{LETTERSEA_DIR}` 的信件互动与跨角色叙事通信系统，基于 Flask / Python 服务端构建。",
            "系统支持跨次元虚拟角色信件生成、邮戳信纸排版渲染以及情感互动记忆追踪。"
        ]

        l_dir = PREPARED_DIR / 'lettersea-system'
        l_dir.mkdir(parents=True, exist_ok=True)
        (l_dir / 'document.md').write_text(redacted_l, encoding='utf-8')
        (l_dir / 'summary.json').write_text(json.dumps({
            'key': 'lettersea-system',
            'title': 'LetterSea 虚拟角色信件互动与异步叙事系统',
            'category': '社交与系统扩展',
            'node_type': 'Document',
            'summary': '跨角色信件生成与互动叙事服务，提供沉浸式信纸视觉与情感纽带记忆。',
            'source_path': str(LETTERSEA_DIR),
            'claims': l_claims,
            'hash': l_hash
        }, ensure_ascii=False, indent=2), encoding='utf-8')

        manifest.append({
            'key': 'lettersea-system',
            'title': 'LetterSea 虚拟角色信件互动与异步叙事系统',
            'category': '社交与系统扩展',
            'node_type': 'Document',
            'size_bytes': len(redacted_l.encode('utf-8')),
            'claims_count': len(l_claims),
            'hash': l_hash
        })
        print("  ✓ Prepared LetterSea Correspondence System")

    (PREPARED_DIR / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nAll {len(manifest)} narrative and game design systems prepared in {PREPARED_DIR}!")

if __name__ == '__main__':
    main()
