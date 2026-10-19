// API transport stays bounded; the user sees one continuous collection.
export async function collectUniverse(params, fetchPage, isCurrent, onProgress=()=>{}) {
  const query=new URLSearchParams(params);query.set('limit','500');
  const topics=[],items=[],seen=new Set();let offset=0,first=null;
  while(true){
    if(!isCurrent())return null;
    query.set('offset',String(offset));
    const page=await fetchPage(new URLSearchParams(query));
    if(!isCurrent())return null;
    first??=page;
    if(!Number.isSafeInteger(page.total)||page.total<0||page.total!==first.total)throw new Error('资料在载入期间发生变化，请重试。');
    const entries=[...page.topics.map(t=>({...t,kind:'topic'})),...page.items];
    for(const entry of entries){
      const key=`${entry.kind}:${entry.id}`;
      if(seen.has(key))throw new Error('资料顺序在载入期间发生变化，请重试。');
      seen.add(key);
    }
    topics.push(...page.topics);items.push(...page.items);offset+=entries.length;
    if(offset===first.total)return {...first,topics,items,offset:0,partial:false};
    if(!entries.length||offset>first.total)throw new Error('资料尚未完整载入，请重试。');
    onProgress(offset,first.total);
  }
}

// Preview one real level of membership, never inferred proximity or copied content.
export async function collectTopicPreviews(topics, scope, fetchPage, isCurrent) {
  let next=0;
  const result=new Map();
  // 并发从 3 提到 8：55 个主题逐个预取时，3 并发意味着 19 轮串行，
  // 每轮约 200~600ms，首屏要等十几秒。这些请求都是只读，后端可并发处理。
  await Promise.all(Array.from({length:Math.min(8,topics.length)},async()=>{
    while(next<topics.length && isCurrent()){
      const topic=topics[next++];
      try{
        const data=await collectUniverse(new URLSearchParams({scope,topic_id:topic.id,kind:'all'}),fetchPage,isCurrent);
        if(data)result.set(topic.id,{children:[...data.topics.map(t=>({...t,kind:'topic'})),...data.items]});
      }catch{
        if(isCurrent())result.set(topic.id,{children:[],previewError:true});
      }
    }
  }));
  return topics.map(t=>({...t,...result.get(t.id)}));
}

// ==========================================================================
// Dynamic Open-Ended Domain Fleet Classification & Battlegroup Builder
// ==========================================================================
export const DOMAIN_FLEET_RULES = [
  {
    id: 'ui',
    domain: 'UI / 视觉体验舰队',
    code: 'UI-CARRIER',
    badge: '🎨',
    color: '#38bdf8',
    patterns: [
      /\bui\b/i, /design/i, /css/i, /style/i, /color/i, /theme/i, /font/i, /typography/i,
      /apple-design/i, /art-director/i, /canvas/i, /svg/i, /layout/i, /responsive/i,
      /component/i, /accessibility/i, /tailwind/i, /figma/i, /icon/i, /frontend/i
    ]
  },
  {
    id: 'vfx',
    domain: '动效 / 多媒体视听舰队',
    code: 'VFX-CRUISER',
    badge: '🎬',
    color: '#a78bfa',
    patterns: [
      /animat/i, /motion/i, /audio/i, /sound/i, /video/i, /music/i, /wallpaper/i,
      /voice/i, /speech/i, /podcast/i, /media/i, /render/i, /\b3d\b/i, /threejs/i, /live2d/i,
      /autocad/i, /\bcad\b/i, /map-maker/i, /\bmap\b/i
    ]
  },
  {
    id: 'writing',
    domain: '创意写作 / 提示词工程舰队',
    code: 'MUSE-DREADNOUGHT',
    badge: '✍️',
    color: '#fbbf24',
    patterns: [
      /prompt/i, /writing/i, /novel/i, /story/i, /narrative/i, /copywrit/i, /blog/i,
      /text/i, /translat/i, /script/i, /essay/i, /content/i, /author/i, /humaniz/i,
      /talkmaker/i, /talk/i
    ]
  },
  {
    id: 'agents',
    domain: 'Agent / 多智能体编排舰队',
    code: 'SWARM-FLAGSHIP',
    badge: '🤖',
    color: '#f43f5e',
    patterns: [
      /agent/i, /orchestrat/i, /dispatch/i, /workflow/i, /subagent/i, /spawn/i,
      /tamagotchi/i, /proxy/i, /autonomous/i, /swarm/i, /\bbot\b/i,
      /pet/i, /desktop-pet/i, /social-app/i, /social/i, /workdesk/i, /ai-workdesk/i, /antigravity/i
    ]
  },
  {
    id: 'cloud',
    domain: '云原生 / 基础设施平台舰队',
    code: 'ORBITAL-BASTION',
    badge: '☁️',
    color: '#06b6d4',
    patterns: [
      /\baws\b/i, /\bazure\b/i, /\bgcp\b/i, /cloud/i, /kubernetes/i, /\bk8s\b/i,
      /terraform/i, /\biac\b/i, /infrastructure/i, /prometheus/i, /chaos/i, /canary/i,
      /resilience/i, /incident/i
    ]
  },
  {
    id: 'backend',
    domain: '后端服务 / 分布式架构舰队',
    code: 'CORE-DREADNOUGHT',
    badge: '⚙️',
    color: '#10b981',
    patterns: [
      /backend/i, /microservice/i, /kafka/i, /caching/i, /redis/i, /\bgrpc\b/i,
      /graphql/i, /websocket/i, /django/i, /springboot/i, /jpa/i, /golang/i,
      /python-patterns/i, /rust/i, /event-driven/i, /api-patterns/i, /api-versioning/i,
      /02-development/i, /development-tools/i
    ]
  },
  {
    id: 'refactoring',
    domain: '代码重构 / 架构整洁舰队',
    code: 'TITAN-BATTLESHIP',
    badge: '💻',
    color: '#34d399',
    patterns: [
      /refactor/i, /architect/i, /clean/i, /modular/i, /code-review/i, /ast-grep/i,
      /code-knowledge/i, /code-simplification/i, /deslop/i, /ai-slop/i, /dead-code/i,
      /dependency-analysis/i, /fullstack/i, /code/i
    ]
  },
  {
    id: 'dx',
    domain: '开发者效能 / 扩展元技能舰队',
    code: 'FORGE-TOOLKIT',
    badge: '🛠️',
    color: '#f59e0b',
    patterns: [
      /skill-creator/i, /skill-developer/i, /skill-installer/i, /plugin-management/i,
      /hook-developer/i, /omx-setup/i, /statusline/i, /\bhud\b/i, /onboard/i,
      /understand-codebase/i, /\btldr\b/i, /doctor/i, /external-skills/i, /worker/i,
      /session-analysis/i, /factcheck/i, /verification-before/i, /receiving-code/i, /requesting-code/i
    ]
  },
  {
    id: 'business',
    domain: '商业分析 / 财务战略规划舰队',
    code: 'CHRONOS-TREASURY',
    badge: '📊',
    color: '#eab308',
    patterns: [
      /financial/i, /budget/i, /investment/i, /legal/i, /market-trend/i, /forecast/i,
      /calendar/i, /spreadsheets/i, /presentations/i, /payment/i, /revenue/i,
      /saas-analytics/i, /business/i, /accounting/i, /finance/i
    ]
  },
  {
    id: 'qa',
    domain: '质量保障 / 测试诊断舰队',
    code: 'AEGIS-BASTION',
    badge: '🛡️',
    color: '#60a5fa',
    patterns: [
      /test/i, /\bqa\b/i, /lint/i, /benchmark/i, /diagnos/i, /profil/i, /audit/i,
      /review/i, /inspect/i, /verify/i, /validat/i, /debug/i, /error/i, /tdd/i,
      /harness/i
    ]
  },
  {
    id: 'security',
    domain: 'API 协议 / 系统防御合规舰队',
    code: 'PROTOCOL-VANGUARD',
    badge: '🔒',
    color: '#2dd4bf',
    patterns: [
      /\bapi\b/i, /\bmcp\b/i, /endpoint/i, /\brest\b/i, /protocol/i, /defense/i,
      /security/i, /auth/i, /token/i, /gateway/i, /insecure/i, /secret/i,
      /compliance/i, /\bsoc2\b/i, /\bkvkk\b/i, /sast/i, /oauth/i
    ]
  },
  {
    id: 'data',
    domain: '数据科学 / 知识检索舰队',
    code: 'NEXUS-SURVEYOR',
    badge: '🌐',
    color: '#818cf8',
    patterns: [
      /data/i, /sql/i, /database/i, /query/i, /vector/i, /embed/i, /retriev/i,
      /search/i, /index/i, /crawl/i, /scrap/i, /analytics/i, /knowledge/i, /graph/i,
      /memory/i, /clickhouse/i, /postgres/i, /mongodb/i, /elasticsearch/i,
      /ai library/i, /library/i
    ]
  },
  {
    id: 'devops',
    domain: 'DevOps / 工具链部署舰队',
    code: 'FORGE-HAULER',
    badge: '🚀',
    color: '#f97316',
    patterns: [
      /\bgit\b/i, /docker/i, /deploy/i, /build/i, /\bcli\b/i, /terminal/i, /shell/i,
      /bash/i, /powershell/i, /npm/i, /pip/i, /\buv\b/i, /package/i, /ci\/cd/i, /release/i
    ]
  },
  {
    id: 'cognition',
    domain: '探索思考 / 认知决策舰队',
    code: 'ORACLE-SCIENCE',
    badge: '🧠',
    color: '#c084fc',
    patterns: [
      /brainstorm/i, /reason/i, /plan/i, /think/i, /solve/i, /research/i, /strategy/i,
      /concept/i, /learn/i, /interview/i, /grill/i, /superpower/i, /ultrawork/i, /ultragoal/i
    ]
  },
  {
    id: 'ai_ml',
    domain: 'AI 模型 / 深度学习与微调舰队',
    code: 'NEURAL-TITAN',
    badge: '🧬',
    color: '#e879f9',
    patterns: [
      /\bllm\b/i, /fine-tun/i, /lora/i, /qlora/i, /pytorch/i, /tensorflow/i,
      /huggingface/i, /transformers/i, /diffusion/i, /vllm/i, /ollama/i,
      /inference/i, /quantiz/i, /gguf/i, /deeplearning/i, /machine-learning/i, /mlops/i
    ]
  },
  {
    id: 'mobile',
    domain: '移动终端 / 跨端应用开发舰队',
    code: 'MOBILE-INTERCEPTOR',
    badge: '📱',
    color: '#ec4899',
    patterns: [
      /mobile/i, /\bios\b/i, /swift/i, /android/i, /kotlin/i, /flutter/i,
      /expo/i, /react-native/i
    ]
  }
];

export function classifySkillDomain(skill) {
  if (skill.domainId) {
    const byId = DOMAIN_FLEET_RULES.find(r => r.id === skill.domainId);
    if (byId) return byId;
  }
  const explicitCategory = (skill.category || skill.domain || '').trim();
  if (explicitCategory) {
    const known = DOMAIN_FLEET_RULES.find(
      r => r.domain.toLowerCase().includes(explicitCategory.toLowerCase()) ||
           explicitCategory.toLowerCase().includes(r.id) ||
           r.id === explicitCategory.toLowerCase()
    );
    if (known) return known;
    let hash = 0;
    for (const ch of explicitCategory) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    const colors = ['#f43f5e', '#ec4899', '#d946ef', '#a855f7', '#6366f1', '#0ea5e9', '#06b6d4', '#14b8a6', '#10b981', '#84cc16', '#eab308', '#f97316'];
    return {
      id: `custom-${explicitCategory.toLowerCase().replace(/[^a-z0-9]/g, '-')}`,
      domain: `${explicitCategory.replace(/\s*舰队$/, '')} 舰队`,
      code: `${explicitCategory.toUpperCase().slice(0, 10)}-FLAGSHIP`,
      badge: '🛸',
      color: colors[hash % colors.length]
    };
  }

  const text = `${skill.title || ''} ${skill.description || ''} ${(skill.locations || []).map(l => l.path).join(' ')}`.toLowerCase();
  let bestRule = null, bestScore = 0;
  for (const rule of DOMAIN_FLEET_RULES) {
    let score = 0;
    for (const pat of rule.patterns) {
      if (pat.test(skill.title || '')) score += 5;
      else if (pat.test(text)) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      bestRule = rule;
    }
  }

  if (bestRule && bestScore > 0) return bestRule;

  return {
    id: 'general',
    domain: '通用扩展 / 特遣作战舰队',
    code: 'SHADOW-SKIFF',
    badge: '🛸',
    color: '#e2e8f0'
  };
}

const MAX_3D_ESCORTS_PER_FLAGSHIP = 36;

export function buildDomainFleets(skills = []) {
  const fleetMap = new Map();
  for (const skill of skills) {
    const def = classifySkillDomain(skill);
    if (!fleetMap.has(def.id)) {
      fleetMap.set(def.id, {
        def,
        skills: []
      });
    }
    fleetMap.get(def.id).skills.push(skill);
  }

  const fleets = [];
  for (const [id, entry] of fleetMap.entries()) {
    // Prioritize MCPs, fused composite skills, and local workspace skills for 3D escort rendering
    const sortedMembers = [...entry.skills].sort((a, b) => {
      const rank = s => {
        if (s.kind === 'mcp') return 0;
        if (s.fused_from?.length > 1) return 1;
        if (s.source && s.source !== 'community' && !String(s.locations?.[0]?.source || '').includes('AAS Catalog')) return 2;
        return 3;
      };
      return rank(a) - rank(b);
    });

    const flagship = {
      id: `flagship-${id}`,
      title: `${entry.def.badge} ${entry.def.domain}`,
      kind: 'skill',
      isFlagship: true,
      domainId: id,
      domainName: entry.def.domain,
      badge: entry.def.badge,
      code: entry.def.code,
      color: entry.def.color,
      fleetCount: entry.skills.length,
      memberSkills: sortedMembers
    };

    const allEscorts = sortedMembers.map((s, idx) => ({
      ...s,
      kind: s.kind || 'skill',
      isEscort: true,
      domainId: id,
      flagshipId: flagship.id,
      color: entry.def.color,
      squadronIndex: idx
    }));

    const escortShips = allEscorts.slice(0, MAX_3D_ESCORTS_PER_FLAGSHIP);
    fleets.push({ flagship, escortShips, allEscorts });
  }

  fleets.sort((a, b) => b.flagship.fleetCount - a.flagship.fleetCount);
  const flagships = fleets.map(f => f.flagship);
  const allItems = [];
  const directoryItems = [];
  for (const f of fleets) {
    allItems.push(f.flagship);
    allItems.push(...f.escortShips);
    directoryItems.push(f.flagship);
    directoryItems.push(...f.allEscorts);
  }
  return { fleets, flagships, allItems, directoryItems };
}


