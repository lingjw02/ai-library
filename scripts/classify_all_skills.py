import json
from collections import defaultdict
from pathlib import Path
import re

skills = json.load(open('scratch_skills.json', 'r', encoding='utf-8'))
out = []
out.append(f"Total discovered skills: {len(skills)}\n")

titles = [s['title'] for s in skills]
descriptions = [s.get('desc', '') for s in skills]

# Define classification rules based on domains:
rules = [
    ("UI & 视觉设计舰队 (UI / Visual Design)", [
        r'ui\b', r'design', r'css', r'style', r'color', r'theme', r'font', r'typography',
        r'apple-design', r'art-director', r'canvas', r'svg', r'layout', r'responsive',
        r'component', r'accessibility', r'tailwind', r'figma', r'sketch', r'icon'
    ]),
    ("动效与多媒体舰队 (Animation & Media)", [
        r'animat', r'motion', r'audio', r'sound', r'video', r'music', r'wallpaper',
        r'voice', r'speech', r'podcast', r'media', r'render', r'3d', r'threejs', r'live2d'
    ]),
    ("创意写作与提示词舰队 (Writing & Prompts)", [
        r'prompt', r'writing', r'novel', r'story', r'narrative', r'copywrit', r'blog',
        r'text', r'translat', r'script', r'essay', r'content', r'lyric', r'author', r'edit'
    ]),
    ("Agent 与多智能体编排舰队 (Agent & Orchestration)", [
        r'agent', r'orchestrat', r'dispatch', r'workflow', r'subagent', r'spawn',
        r'tamagotchi', r'proxy', r'autonomous', r'swarm', r'bot\b', r'assistant'
    ]),
    ("代码工程与重构舰队 (Software Engineering & Architecture)", [
        r'code', r'refactor', r'architect', r'pattern', r'pattern', r'clean',
        r'typescript', r'python', r'javascript', r'rust', r'golang', r'react', r'vue',
        r'svelte', r'backend', r'frontend', r'fullstack', r'framework', r'sdk'
    ]),
    ("质量保障与测试诊断舰队 (QA, Testing & Diagnostics)", [
        r'test', r'qa\b', r'lint', r'benchmark', r'diagnos', r'profil', r'audit',
        r'review', r'inspect', r'verify', r'validat', r'debug', r'error', r'check'
    ]),
    ("API 与系统协议防御舰队 (API & Protocol Defense)", [
        r'api\b', r'mcp\b', r'endpoint', r'rest\b', r'graphql', r'grpc', r'protocol',
        r'defense', r'security', r'auth', r'token', r'gateway', r'proxy', r'cors'
    ]),
    ("数据科学与知识检索舰队 (Data, Vector & Retrieval)", [
        r'data', r'sql', r'database', r'query', r'vector', r'embed', r'retriev',
        r'search', r'index', r'crawl', r'scrap', r'analytics', r'knowledge', r'graph', r'memory'
    ]),
    ("DevOps 与工具链舰队 (DevOps & Tooling)", [
        r'git\b', r'docker', r'deploy', r'build', r'cli\b', r'terminal', r'shell',
        r'bash', r'powershell', r'npm', r'pip', r'uv\b', r'package', r'ci/cd', r'release', r'tool'
    ]),
    ("探索与思考认知舰队 (Cognition, Reasoning & Brainstorming)", [
        r'brainstorm', r'reason', r'plan', r'think', r'solve', r'research', r'strategy',
        r'concept', r'learn', r'mentor', r'coach', r'interview', r'grill'
    ]),
]

def classify_skill(s):
    text = (s['title'] + " " + s.get('desc', '') + " " + s.get('path', '')).lower()
    scores = {}
    for domain, patterns in rules:
        score = 0
        for pat in patterns:
            matches = len(re.findall(pat, text))
            if matches:
                # title matches have much higher weight
                if re.search(pat, s['title'].lower()):
                    score += matches * 4
                else:
                    score += matches
        if score > 0:
            scores[domain] = score
    if scores:
        best_domain = max(scores.items(), key=lambda x: x[1])[0]
        return best_domain
    return "通用扩展与特遣舰队 (General Utility Fleet)"

fleet_groups = defaultdict(list)
for s in skills:
    cat = classify_skill(s)
    fleet_groups[cat].append(s)

out.append(f"--- Classified into {len(fleet_groups)} Capital Fleet Divisions ---\n")
for cat, items in sorted(fleet_groups.items(), key=lambda x: len(x[1]), reverse=True):
    out.append(f"\n==================================================")
    out.append(f"【{cat}】: 共 {len(items)} 艘护航舰")
    out.append(f"==================================================")
    for it in items[:15]:
        out.append(f"  - {it['title']}: {it.get('desc', '')[:60]}...")
    if len(items) > 15:
        out.append(f"  ... 以及其他 {len(items) - 15} 个技能")

open('scratch_analysis.txt', 'w', encoding='utf-8').write("\n".join(out))
print("Analysis written to scratch_analysis.txt successfully!")
