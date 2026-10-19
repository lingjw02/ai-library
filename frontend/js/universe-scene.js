import * as THREE from '../vendor/three/three.module.js';
import { OrbitControls } from '../vendor/three/OrbitControls.js';
import { neuralColor, makeJelly, makeMiniSphere, makePulsar, makeRingworld, makeAccretionDisk, makeCore, makeEye, makeSkillStation, makeMcpWarship, makeAttackVessel, makeDefenseVessel, makeFlagshipVessel, makeMembershipLinks, collapseLines } from './neural-visuals.js';
import { loadUniverseModels, createUniverseModel } from './universe-models.js';
import { classifySkillDomain } from './universe-catalogue.js';

// Renderer pixel-ratio ceilings per quality tier; keeps the 3D universe usable on
// integrated GPUs and when the machine is running on battery.
const PIXEL_RATIO_CAP = { high: 1.75, medium: 1.25, low: 0.85 };

export const isSkillTopic = d => d && d.kind === 'topic' && (d.id === '__local_skills' || /^skills?(?:\s*[\/|｜]\s*.*)?$/i.test((d.title || d.name || '').trim()));
export const isMcpTopic = d => d && d.kind === 'topic' && (d.id === '__mcp_library' || /^mcp(?:\s*[\/|｜]\s*.*)?$/i.test((d.title || d.name || '').trim()));
export const isMcpItem = d => d && (
  d.kind === 'mcp' ||
  d.type === 'mcp' ||
  /\bmcp\b/i.test(d.title || d.name || '') ||
  /\bmcp\b/i.test(d.description || '') ||
  /\bmcp\b/i.test(d.id || '')
);
export const isSkillItem = d => d && (
  (d.kind === 'skill' || d.type === 'skill') && !isMcpItem(d)
);

const TAU = Math.PI * 2;
const hash = value => {
  let n = 2166136261;
  for (const c of String(value)) n = Math.imul(n ^ c.charCodeAt(0), 16777619);
  return n >>> 0;
};
const random = seed => () => {
  seed = Math.imul(1664525, seed) + 1013904223;
  return (seed >>> 0) / 4294967296;
};

// Arrangement is stable visual spacing with genuine 3D spherical depth and collision avoidance.

// 与后端 _normalize_agent 一致的归一化：operator/user → user（我的内容）；
// 各导入来源各自成区，其余（含 profile / trajectory 等批量导入）归「其他导入」。
export function normalizeAgent(author) {
  if (!author || author === 'user' || author === 'operator') return 'user';
  const lower = String(author).toLowerCase();
  if (lower.includes('antigravity')) return 'antigravity';
  if (lower.includes('codex')) return 'codex';
  if (lower.includes('deepseek')) return 'deepseek';
  if (lower.includes('qwen')) return 'qwen';
  if (lower.includes('doubao')) return 'doubao';
  if (lower.includes('pangu')) return 'pangu';
  return 'external';
}

/**
 * 战术护航编队分层排布算法 (Tactical Escort Squadron Layout):
 * 1. 1~15 艘 (s = 0~14): 近卫双翼锋矢突击阵 (Chevron Vanguard Spearhead)
 *    左右对称锋矢后掠展开，紧卫旗舰两舷，深度严格控制在 3.95 以内，绝不拖长。
 * 2. 16~35 艘 (s = 15~34): 巡洋中坚·两翼重装列阵 (Dual Flank Echelon Battlegroups)
 *    分立尖刀阵外侧两翼（左翼 10 艘，右翼 10 艘），成 5 排 × 2 列战术纵队方阵。
 * 3. 36 艘及以上 (s >= 35): 后卫远征·立体战术矩阵 (Rear Tactical Reserve Matrix)
 *    部署于后方支援走廊（|dx| <= 4.75, dz <= -4.4），对称展开，配合三层立体甲板高低起伏，整齐威武。
 */
export function getTacticalEscortOffset(squadronIndex, scale = 1.0) {
  const s = Math.max(0, squadronIndex || 0);
  let dx = 0, dy = 0, dz = 0;

  if (s < 15) {
    // 【第 1 阶：1 至 15 艘】—— 近卫双翼锋矢突击阵 (Chevron Vanguard)
    const wing = (s % 2 === 0 ? 1 : -1);
    const row = Math.floor(s / 2) + 1; // 1 to 7 级梯次
    dx = wing * (2.2 + row * 0.55);
    dz = - (0.8 + row * 0.45);
    dy = (row % 2 === 0 ? 0.22 : -0.22);
  } else if (s < 35) {
    // 【第 2 阶：16 至 35 艘】—— 巡洋中坚·两翼重装列阵 (Dual Flank Echelons)
    const arcIdx = s - 15; // 0 to 19 (共 20 艘，每翼 10 艘)
    const side = (arcIdx % 2 === 0 ? 1 : -1);
    const slot = Math.floor(arcIdx / 2); // 0 to 9
    const rank = Math.floor(slot / 2); // 0 to 4 (5 排)
    const col = slot % 2; // 0 内列, 1 外列
    dx = side * (6.2 + col * 1.1);
    dz = - (0.6 + rank * 0.75);
    dy = (col === 0 ? 0.3 : -0.3) + (rank % 2 ? 0.15 : -0.15);
  } else {
    // 【第 3 阶：36 艘及以上】—— 后卫远征·立体战术矩阵 (Rear Tactical Reserve Matrix)
    const k = s - 35; // 0, 1, 2...
    const COLS = 10;
    const rank = Math.floor(k / COLS);
    const col = k % COLS;
    const colOffset = col - 4.5; // 对称分布
    dx = colOffset * 1.05;
    dz = - (4.4 + rank * 0.65);
    const deck = (rank % 3) - 1; // -1, 0, 1 三层立体起伏
    dy = deck * 0.4 + (col % 2 ? 0.12 : -0.12);
  }

  return [dx * scale, dy * scale, dz * scale];
}

/**
 * 战区划分与泊位坐标规划表 (Functional Sector Clusters):
 * 1. 前沿交互战区 (West Flank, X ~ -36 to -39, heading: -0.35 rad)
 * 2. 核心系统战区 (Forward Battleline, Z ~ 26 to 30, heading: 0.0 rad 正北推进)
 * 3. 智能工程战区 (East Flank, X ~ 34 to 38, heading: 0.35 rad)
 * 4. 知识决策智库战区 (Rear Strategic Reserve, Z ~ -26 to -29, heading: PI 正南警戒)
 */
export const FLEET_SECTOR_BERTHS = {
  // Sector 0: 前沿交互战区 (West Flank)
  ui:          { sector: 'frontend',    pos: [-36.0, 1.5, 13.0],  heading: -0.35 },
  vfx:         { sector: 'frontend',    pos: [-39.0, 0.0, -1.0],  heading: -0.35 },
  mobile:      { sector: 'frontend',    pos: [-35.0, 2.0, -14.0], heading: -0.35 },
  // Sector 1: 核心系统战区 (Forward Battleline)
  refactoring: { sector: 'core',        pos: [-26.0, 2.0, 30.0],  heading: 0.0 },
  backend:     { sector: 'core',        pos: [-13.0, 0.5, 27.0],  heading: 0.0 },
  cloud:       { sector: 'core',        pos: [0.0, 2.5, 26.0],    heading: 0.0 },
  devops:      { sector: 'core',        pos: [13.0, 0.5, 27.0],   heading: 0.0 },
  security:    { sector: 'core',        pos: [26.0, 2.0, 30.0],   heading: 0.0 },
  // Sector 2: 智能工程战区 (East Flank)
  agents:      { sector: 'engineering', pos: [34.0, 2.0, 20.0],   heading: 0.35 },
  ai_ml:       { sector: 'engineering', pos: [39.0, 3.0, 13.0],   heading: 0.35 },
  dx:          { sector: 'engineering', pos: [38.0, 0.5, 0.0],    heading: 0.35 },
  qa:          { sector: 'engineering', pos: [34.0, 2.5, -12.0],  heading: 0.35 },
  // Sector 3: 知识决策战区 (Rear Strategic Reserve)
  data:        { sector: 'knowledge',   pos: [-26.0, 3.5, -29.0], heading: Math.PI },
  cognition:   { sector: 'knowledge',   pos: [-13.0, 2.0, -27.0], heading: Math.PI },
  writing:     { sector: 'knowledge',   pos: [0.0, 4.0, -26.0],   heading: Math.PI },
  business:    { sector: 'knowledge',   pos: [13.0, 2.0, -27.0],  heading: Math.PI },
  general:     { sector: 'knowledge',   pos: [26.0, 3.5, -29.0],  heading: Math.PI },
};

export function getFleetBerth(domainId, fallbackIndex = 0, total = 15) {
  if (domainId && FLEET_SECTOR_BERTHS[domainId]) {
    return FLEET_SECTOR_BERTHS[domainId];
  }
  const k = fallbackIndex % 5;
  const col = (k - 2) * 12.0;
  return {
    sector: 'reserve',
    pos: [col, 2.0, -32.0 - Math.floor(fallbackIndex / 5) * 8.0],
    heading: Math.PI
  };
}

export function buildUniverseLayout({overview = false, mode = 'topic', topics = [], items = [], agents = []} = {}) {
  let entries = [];
  let clusterNotes = false;
  if (overview && mode === 'agent') {
    entries = agents.map(a => ({...a, id: a.id, kind: 'agent', title: a.name, count: a.total_items}));
  } else {
    entries = [
      ...topics.map(t => ({...t, id: t.id ?? t.collection_id, kind: 'topic'})),
      ...items.map(t => ({...t, id: t.id ?? t.note_id ?? t.memory_id,
        kind: t.kind === 'skill' ? 'skill' : t.kind === 'mcp' ? 'mcp' : t.kind === 'memory' || t.type === 'memory' || t.memory_id ? 'memory' : 'note'})),
    ];
  }

  // 「我的空间」视图：按来源分区聚类 —— 我的内容为核心，各导入分区节点环绕，
  // 笔记/记忆光点归入各自分区，而不是上千条散点。
  if (overview && mode === 'topic' && topics.length === 0 && items.length >= 30) {
    clusterNotes = true;
    // 归一化见模块顶层 normalizeAgent（与后端 _normalize_agent 一致）
    const AGENT_ORDER = ['user', 'antigravity', 'codex', 'doubao', 'pangu', 'deepseek', 'qwen', 'external'];
    const AGENT_NAMES = {
      user: '我的内容', antigravity: 'Antigravity', codex: 'Codex', doubao: '豆包导入',
      pangu: '盘古导入', deepseek: 'DeepSeek', qwen: 'Qwen', external: '其他导入'
    };
    const groups = new Map();
    for (const it of items) {
      const key = normalizeAgent(it.author);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    }
    const agentKeys = [...groups.keys()].sort((a, b) => {
      const ia = AGENT_ORDER.indexOf(a), ib = AGENT_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    const anchors = new Map();
    const ringCount = Math.max(1, agentKeys.length - (agentKeys.includes('user') ? 1 : 0));
    agentKeys.forEach((ag, i) => {
      if (ag === 'user') { anchors.set(ag, [0, 0, 0]); return; }
      const idx = i - (agentKeys.includes('user') ? 1 : 0);
      const yNorm = ringCount > 1 ? 1 - (2 * idx + 1) / ringCount : 0;
      const rho = Math.sqrt(Math.max(0.12, 1 - yNorm * yNorm));
      const theta = idx * 2.3999632 + 0.35;
      const dist = 12.5;
      anchors.set(ag, [Math.cos(theta) * rho * dist, yNorm * 0.8 * dist, Math.sin(theta) * rho * dist]);
    });
    entries = [];
    for (const ag of agentKeys) {
      const anchor = anchors.get(ag), list = groups.get(ag);
      entries.push({
        id: ag, kind: 'agent', title: AGENT_NAMES[ag] || ag,
        count: list.length, anchor: true, position: anchor,
        radius: ag === 'user' ? 3.0 : 2.5, orbit: null
      });
      list.forEach((it, k) => {
        const seed = hash(it.id), jitter = random(seed);
        const rad = ag === 'user' ? 10.0 : 5.2;
        const a1 = (k + jitter() * 0.6) * 2.3999632;
        const a2 = Math.acos(Math.max(-1, Math.min(1, 2 * ((k * 0.618 + jitter() * 0.4) % 1) - 1)));
        // 我的内容：排成眼球（半径 7.2）之外的一圈金色光环；
        // 导入分区：光点排成分区外壳，中心的天体模型不被淹没。
        const rr = ag === 'user' ? rad * (0.80 + 0.20 * jitter()) : rad * (0.56 + 0.44 * jitter());
        entries.push({
          ...it, id: it.id ?? it.note_id, kind: it.kind === 'memory' || it.type === 'memory' || it.memory_id ? 'memory' : 'note',
          position: [
            anchor[0] + Math.sin(a2) * Math.cos(a1) * rr,
            anchor[1] + Math.cos(a2) * rr * 0.8,
            anchor[2] + Math.sin(a2) * Math.sin(a1) * rr
          ],
          radius: 0.42 + (hash(it.id) % 5) * 0.05, orbit: null, agentGroup: ag
        });
      });
    }
  }

  const hasChildren = topics.some(t => t.children?.length);
  const goldenAngle = 2.3999632;
  const nonSkillEntries = entries.filter(e => !isSkillTopic(e));

  // 「我的空间」（mode === 'agent'）导入分区的「绕视轴旋转角」。
  //
  // 导入分区沿用「知识宇宙」那套斐波那契球面，以保留完整纵深（z 可达 ±10，
  // 近的分区因透视更大、远的小 —— 这是 3D 体积感的来源）。
  // 但球面本身会把某个分区丢到视轴附近：count=5 时 idx=2 的方向是
  // (cos 4.8, 0, sin 4.8) ≈ (-0.09, 0, -0.996)，几乎正对 -Z；
  // 默认机位在 +Z 看向原点，于是它被投影到画面正中，压在中心核心上。
  //
  // 解法不是压平布局（那会失去纵深），而是把整组绕视轴（Z 轴）旋转一个最优角度：
  // 绕视轴旋转不改变任何分区的 z，纵深 100% 保留，只改变它们在画面上的方位角。
  // 这里暴力搜 720 个候选角，取「各分区到视轴横向偏移的最小值」最大的那个。
  let agentTheta0 = 0;
  if (overview && mode === 'agent') {
    const n = Math.max(1, entries.filter(e => e.id !== 'user').length);
    const yN = i => (n > 1 ? 1 - (2 * i + 1) / n : 0);
    const rhoOf = i => Math.sqrt(Math.max(0.12, 1 - yN(i) ** 2));
    let bestMin = -1;
    for (let k = 0; k < 720; k++) {
      const t0 = (k / 720) * Math.PI * 2;
      let minLat = Infinity;
      for (let i = 0; i < n; i++) {
        const t = i * goldenAngle + t0;
        minLat = Math.min(minLat, Math.hypot(Math.cos(t) * rhoOf(i), yN(i) * 0.82));
      }
      if (minLat > bestMin) { bestMin = minLat; agentTheta0 = t0; }
    }
  }

  const layout = entries.map((data, index) => {
    let position, radius, orbit, drift = null, escortOffset = null, initialAngle = 0, rotationY = 0, tacticalBerth = null, heading = 0;
    const seed = hash(data.id), jitter = random(seed);
    if (data.position) {
      // 已由聚类逻辑预先定位（全部笔记 agent 聚类）
      position = data.position; radius = data.radius; orbit = data.orbit || null;
    } else if (overview) {
      if (isSkillTopic(data)) {
        // 技能空间站：部署于星盘外围极边缘轨道，完全脱离中心笔记恒星系，成为深空边境前哨站
        position = [62.0, 4.0, -42.0];
        radius = 3.6;
        orbit = null;
        drift = { baseX: 62.0, baseY: 4.0, baseZ: -42.0 };
      } else if (mode === 'agent' && data.id === 'user') {
        // 我的空间 = 核心：固定在中心（科技之眼），其余导入分区环绕它。
        // 半径 4.4 是为了和导入分区「视觉体量」对齐 —— 分区走的是 makePulsar /
        // makeRingworld / makeAccretionDisk / makeJelly，这些模型的线框笼实际画到
        // 自身 radius 的 ~2.7 倍；科技之眼的结构只到 0.84r。取 3.2 时核心会显得
        // 比周围 5 个分区小一圈，撑不起「中心」的分量。
        position = [0, 0, 0];
        radius = 4.4;
        orbit = null;
      } else {
        const pool = mode === 'agent' ? nonSkillEntries.filter(e => e.id !== 'user') : nonSkillEntries;
        const idx = Math.max(0, pool.indexOf(data));
        const count = Math.max(1, pool.length);

        // 360-degree Fibonacci spherical distribution around the center Notes Core
        const yNorm = count > 1 ? 1 - (2 * idx + 1) / count : 0;
        const rho = Math.sqrt(Math.max(0.12, 1 - yNorm * yNorm));
        // 「我的空间」额外叠加 agentTheta0：绕视轴旋转，纵深不变但方位角错开，
        // 保证没有分区落在中心核心正前方/正后方（见上方 agentTheta0 的推导）。
        const spin = mode === 'agent' ? agentTheta0 : 0;
        const theta = idx * goldenAngle + spin + (jitter() - 0.5) * (mode === 'agent' ? 0.30 : 0.45);
        const dirX = Math.cos(theta) * rho;
        const dirY = yNorm * 0.82; // gentle vertical relaxation
        const dirZ = Math.sin(theta) * rho;

        const baseDistance = mode === 'agent' ? 14 : (hasChildren ? 23 : 18.5);
        const distance = baseDistance + (hash(data.id) % 6) * 1.35 + (jitter() - 0.5) * 2.2;
        position = [dirX * distance, dirY * distance, dirZ * distance];
        radius = data.kind === 'topic' ? 1.65 + (hash(data.id) % 5) * 0.07 : data.kind === 'agent' ? 1.75 : 0.85;
        orbit = null;
      }
    } else if (entries.some(e => e.isFlagship)) {
      // 技能空间站母港：四大战区分区泊位阵列（告别环形公转，各舰队列阵就位）
      const flagships = entries.filter(e => e.isFlagship);
      const fCount = Math.max(1, flagships.length);
      if (data.isFlagship) {
        const k = Math.max(0, flagships.indexOf(data));
        const berth = getFleetBerth(data.domainId, k, fCount);
        position = [...berth.pos];
        radius = 1.0; // 尺寸减半（原 2.0 缩减至 1.0）
        orbit = null; // 告别圆环公转，采用战备泊位静态阵列与微动巡弋
        tacticalBerth = { baseX: berth.pos[0], baseY: berth.pos[1], baseZ: berth.pos[2] };
        heading = berth.heading;
        rotationY = berth.heading;
      } else {
        const k = flagships.findIndex(f => f.domainId === data.domainId);
        const fIdx = k >= 0 ? k : (index % fCount);
        const parentFlagship = k >= 0 ? flagships[k] : null;
        const berth = getFleetBerth(data.domainId || parentFlagship?.domainId, fIdx, fCount);
        const fCenter = berth.pos;
        const theta = berth.heading;
        const s = data.squadronIndex ?? index;
        const [dx, dy, dz] = getTacticalEscortOffset(s, 0.5); // 护航队形间距同步缩小 50%
        const cosA = Math.cos(theta), sinA = Math.sin(theta);
        position = [
          fCenter[0] + (dx * cosA + dz * sinA),
          fCenter[1] + dy,
          fCenter[2] + (-dx * sinA + dz * cosA)
        ];
        radius = 0.18; // 僚机尺寸减半（原 0.36 缩减至 0.18）
        orbit = null;
        escortOffset = [dx, dy, dz];
        heading = theta;
        rotationY = theta;
      }
    } else {
      const subTopics = entries.filter(e => e.kind === 'topic');
      if (data.kind === 'topic') {
        // 行星里的子行星：采用自然天体多阶椭圆/微随机轨道分布（告别机械等距环状排列）
        const subIndex = Math.max(0, subTopics.indexOf(data));
        const stCount = Math.max(1, subTopics.length);
        // 方位角：基准象限均分 + 确定性抖动（保证不同子行星分散在不同方向，但非机械对齐）
        const angleJitter = (jitter() - 0.5) * (0.85 / Math.max(1, Math.min(stCount, 4)));
        const angle = (subIndex / stCount) * TAU + angleJitter + ((hash(data.id) % 100) / 100) * 0.25;
        // 轨道半径：多层交错梯队 + 径向微扰（不同子行星拥有独立轨道高度，错落有致）
        const baseDist = 12.0 + (subIndex % 4) * 3.6;
        const distJitter = (jitter() - 0.5) * 2.2;
        const distance = baseDist + distJitter;
        // 轨道倾角与纵向偏角（真实天体轨道面倾角，赋予三维立体起伏）
        const tilt = ((subIndex % 2 ? -1 : 1) * (0.20 + (hash(data.id) % 4) * 0.04)) + (jitter() - 0.5) * 0.10;
        const yOffset = (jitter() - 0.5) * 2.8;
        position = [Math.cos(angle) * distance, Math.sin(angle) * distance * tilt + yOffset, Math.sin(angle) * distance];
        radius = 1.45 + (hash(data.id) % 5) * 0.09;
        // 开普勒公转角速度：半径越大速度越缓
        const speed = (0.0075 / Math.sqrt(distance / 12.0)) * (0.85 + 0.3 * jitter());
        orbit = {angle, distance, tilt, yOffset, speed};
      } else {
        // 笔记与记忆散点：在外围小行星带与散落环带中错落环绕
        const notePool = entries.filter(e => e.kind !== 'topic');
        const nIdx = Math.max(0, notePool.indexOf(data));
        let ring = 0, offset = nIdx, capacity = 8;
        while (offset >= capacity) { offset -= capacity; ring++; capacity = 8 + ring * 5; }
        const priorCount = ring * 8 + 5 * ring * (ring - 1) / 2;
        const count = Math.min(capacity, notePool.length - priorCount);
        const angle = (offset / Math.max(1, count)) * TAU + ring * 0.48 + (jitter() - 0.5) * 0.32;
        const minNoteDist = subTopics.length > 0 ? 14.5 + Math.min(subTopics.length, 4) * 2.6 : 11.0;
        const distance = minNoteDist + ring * 4.6 + (jitter() - 0.5) * 1.8;
        const tilt = (ring % 2 ? -1 : 1) * (0.24 + (ring % 3) * 0.06);
        const yOffset = (jitter() - 0.5) * 1.5;
        position = [Math.cos(angle) * distance, Math.sin(angle) * distance * tilt + yOffset, Math.sin(angle) * distance];
        radius = 0.62 + (hash(data.id) % 5) * 0.055;
        orbit = {angle, distance, tilt, yOffset, speed: 0.0055 / (ring + 1)};
      }
    }
    return {data, position, radius, orbit, drift, tacticalBerth, heading, escortOffset, initialAngle, rotationY};
  });

  // 3D Collision avoidance relaxation for overview topics
  // (skipped for the agent-clustered 「我的空间」 view — positions are pre-planned)
  if (overview && !clusterNotes) {
    for (let pass = 0; pass < 10; pass++) {
      for (let i = 0; i < layout.length; i++) {
        const a = layout[i];
        if (isSkillTopic(a.data) || (mode === 'agent' && a.data.id === 'user')) continue;
        for (let j = i + 1; j < layout.length; j++) {
          const b = layout[j];
          if (isSkillTopic(b.data) || (mode === 'agent' && b.data.id === 'user')) continue;
          const dx = b.position[0] - a.position[0];
          const dy = b.position[1] - a.position[1];
          const dz = b.position[2] - a.position[2];
          const dist = Math.hypot(dx, dy, dz);
          const minDist = 2.4 * (a.radius + b.radius);
          if (dist < minDist && dist > 0.001) {
            const push = (minDist - dist) * 0.5 / dist;
            a.position[0] -= dx * push;
            a.position[1] -= dy * push;
            a.position[2] -= dz * push;
            b.position[0] += dx * push;
            b.position[1] += dy * push;
            b.position[2] += dz * push;
          }
        }
        // Keep clear buffer from center core
        const cDist = Math.hypot(a.position[0], a.position[1], a.position[2]);
        if (cDist < 13.5 && cDist > 0.001) {
          const cPush = (13.5 - cDist) / cDist;
          a.position[0] += a.position[0] * cPush;
          a.position[1] += a.position[1] * cPush;
          a.position[2] += a.position[2] * cPush;
        }
      }
    }
  }
  const previews = [];
  for (const parent of layout) {
    const rawChildren = parent.data.children || [];
    const isStationParent = isSkillTopic(parent.data);
    const isMcpParent = isMcpTopic(parent.data);

    let children = rawChildren;
    if (overview && isStationParent) {
      const flagships = rawChildren.filter(c => c.isFlagship);
      if (flagships.length > 0) {
        const escorts = rawChildren.filter(c => !c.isFlagship);
        const vanguard = [...flagships];
        for (const f of flagships) {
          const fleetEscorts = escorts.filter(e => e.domainId === f.domainId).slice(0, 2);
          vanguard.push(...fleetEscorts);
        }
        children = vanguard;
      } else {
        const attackItems = rawChildren.filter(c => !isMcpItem(c));
        const defenseItems = rawChildren.filter(c => isMcpItem(c));
        const vanguard = [];
        const totalSquad = Math.min(16, rawChildren.length);
        let aIdx = 0, dIdx = 0;
        for (let i = 0; i < totalSquad; i++) {
          if (defenseItems.length > 0 && (i % 3 === 2 || aIdx >= attackItems.length) && dIdx < defenseItems.length) {
            vanguard.push(defenseItems[dIdx++]);
          } else if (aIdx < attackItems.length) {
            vanguard.push(attackItems[aIdx++]);
          } else if (dIdx < defenseItems.length) {
            vanguard.push(defenseItems[dIdx++]);
          }
        }
        children = vanguard;
      }
    } else if (overview && isMcpParent) {
      const attackItems = rawChildren.filter(c => !isMcpItem(c));
      const defenseItems = rawChildren.filter(c => isMcpItem(c));
      const vanguard = [];
      const totalSquad = Math.min(16, rawChildren.length);
      let aIdx = 0, dIdx = 0;
      for (let i = 0; i < totalSquad; i++) {
        if (defenseItems.length > 0 && (i % 3 === 2 || aIdx >= attackItems.length) && dIdx < defenseItems.length) {
          vanguard.push(defenseItems[dIdx++]);
        } else if (aIdx < attackItems.length) {
          vanguard.push(attackItems[aIdx++]);
        } else if (dIdx < defenseItems.length) {
          vanguard.push(defenseItems[dIdx++]);
        }
      }
      children = vanguard;
    } else if (overview) {
      // 主题总览：优先完整展示子主题（内部主题细节），笔记预览限量点缀，
      // 避免大量笔记小点糊成“星星点点”；进入主题后仍完整展示全部内容。
      const subTopics = rawChildren.filter(c => c.kind === 'topic');
      const rest = rawChildren.filter(c => c.kind !== 'topic');
      children = [...subTopics, ...rest.slice(0, Math.max(0, 14 - subTopics.length))];
    }

    children.forEach((data, index) => {
      let previewOffset, distance, previewAngle = 0;
      const capacity = Math.max(12, Math.ceil(children.length / 4));
      const ring = Math.floor(index / capacity), offset = index % capacity;
      const count = Math.min(capacity, children.length - ring * capacity);

      if (isStationParent) {
        // Multi-Domain Battlegroups: 四大战区泊位阵列（告别环形公转，各舰队规整列阵）
        const flagships = children.filter(c => c.isFlagship);
        const fCount = Math.max(1, flagships.length);
        const previewFormationScale = 0.38;
        if (data.isFlagship) {
          const k = Math.max(0, flagships.indexOf(data));
          const berth = getFleetBerth(data.domainId, k, fCount);
          previewAngle = berth.heading;
          previewOffset = [
            berth.pos[0] * previewFormationScale,
            berth.pos[1] * previewFormationScale,
            berth.pos[2] * previewFormationScale
          ];
        } else {
          // Escort ship: position relative to its domain flagship!
          const k = flagships.findIndex(f => f.domainId === data.domainId);
          const fIdx = k >= 0 ? k : (index % fCount);
          const parentFlagship = k >= 0 ? flagships[k] : null;
          const berth = getFleetBerth(data.domainId || parentFlagship?.domainId, fIdx, fCount);
          previewAngle = berth.heading;
          const fCenter = [
            berth.pos[0] * previewFormationScale,
            berth.pos[1] * previewFormationScale,
            berth.pos[2] * previewFormationScale
          ];

          const s = data.squadronIndex ?? index;
          const [dx, dy, dz] = getTacticalEscortOffset(s, 0.25);
          const cosA = Math.cos(berth.heading), sinA = Math.sin(berth.heading);
          previewOffset = [
            fCenter[0] + (dx * cosA + dz * sinA),
            fCenter[1] + dy,
            fCenter[2] + (-dx * sinA + dz * cosA)
          ];
        }
        distance = Math.hypot(...previewOffset);
      } else if (overview && isMcpParent) {
        // Point Defense Screen for MCP Warship
        const dSlots = [
          [-1.8, 4.8, 1.4],
          [-1.8, -4.8, 1.4],
          [1.6, 0.0, 4.4],
          [-4.5, 0.0, -2.6]
        ];
        previewOffset = [...dSlots[index % dSlots.length]];
        distance = Math.hypot(...previewOffset);
      } else if (data.kind === 'topic') {
        // 子行星在父星球周围的微缩预览：自然天体轨道分布（非同心圆呆板排列）
        const pSeed = hash(data.id), pJitter = random(pSeed);
        const subTopics = children.filter(c => c.kind === 'topic');
        const subIndex = Math.max(0, subTopics.indexOf(data));
        const subCount = Math.max(1, subTopics.length);
        const angle = (subIndex / subCount) * TAU + (pJitter() - 0.5) * 0.75 + ((pSeed % 100) / 100) * 0.35;
        distance = 3.3 + (subIndex % 3) * 0.85 + (pJitter() - 0.5) * 0.7;
        const tilt = 0.25 + (pJitter() - 0.5) * 0.15;
        const yPerturb = (pJitter() - 0.5) * 0.6;
        previewOffset = overview
          ? [Math.cos(angle) * distance, (Math.sin(angle) * distance * 0.65 - 0.4) + yPerturb, Math.sin(angle) * distance * 0.35 + (pJitter() - 0.5) * 0.4]
          : [Math.cos(angle) * distance, Math.sin(angle) * distance * tilt + yPerturb, Math.sin(angle) * distance];
      } else {
        const rSeed = hash(data.id), rJitter = random(rSeed);
        const rest = children.filter(c => c.kind !== 'topic');
        const rIndex = Math.max(0, rest.indexOf(data));
        const rCapacity = Math.max(10, Math.ceil(rest.length / 3));
        const ring = Math.floor(rIndex / rCapacity), offset = rIndex % rCapacity;
        const count = Math.min(rCapacity, rest.length - ring * rCapacity);
        const angle = (offset / Math.max(1, count)) * TAU + ring * 0.4 + (rJitter() - 0.5) * 0.2;
        distance = 2.8 + ring * 0.85 + (children.some(c => c.kind === 'topic') ? 1.2 : 0) + (rJitter() - 0.5) * 0.3;
        const tilt = 0.25;
        previewOffset = overview
          ? [Math.cos(angle) * distance, Math.sin(angle) * distance * 0.65 - 0.4, Math.sin(angle) * distance * 0.25]
          : [Math.cos(angle) * distance, Math.sin(angle) * distance * tilt, Math.sin(angle) * distance];
      }
      previews.push({
        data: {...data, preview: true, parentTitle: parent.data.title, color: data.isFlagship ? (data.color || '#38bdf8') : data.kind === 'topic' ? neuralColor(data.id) : neuralColor(parent.data.id)},
        radius: isStationParent
          ? (data.isFlagship ? 0.6 : (overview ? 0.14 : Math.min(0.18, TAU * distance / (count * 3))))
          : (overview && isMcpParent)
            ? 0.65
            : data.kind === 'topic'
              ? Math.min(0.55, TAU * distance / (count * 3))
              : Math.min(0.26, TAU * distance / (count * 3)),
        position: parent.position.map((v, i) => v + previewOffset[i]),
        orbit: null,
        previewParent: parent.data.id,
        previewOffset,
        previewDistance: distance,
        rotationY: isStationParent ? previewAngle : 0,
        escortOffset: (isStationParent && !data.isFlagship) ? getTacticalEscortOffset(data.squadronIndex ?? index, 0.25) : null
      });
    });
  }
  return [...layout, ...previews];
}

export class UniverseScene {
  constructor(container, {onSelect = () => {}, onError = () => {}, onHover = () => {}, onCanvasClick = () => {}} = {}) {
    this.container = container;
    this.onSelect = onSelect; this.onError = onError; this.onHover = onHover; this.onCanvasClick = onCanvasClick;
    this.bodies = []; this.pickMeshes = []; this.textures = new Map(); this.listeners = [];
    this.active = true; this.paused = false; this.reading = false; this.disposed = false;
    this.raf = 0; this.lastTime = 0; this.settleUntil = 0; this.hovered = null; this.selected = null;
    this.frames = 0; this.ready = false; this.interacting = false; this.visible = true;
    this.modelsReady = false;
    loadUniverseModels().then(() => {
      this.modelsReady = true;
      if (this.ready && this.data) this.updateCustomModels();
    }).catch(() => {});
    this.motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.reducedMotion = this.motionQuery.matches;
    try {
      this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'low-power'});
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, PIXEL_RATIO_CAP[this.quality] || PIXEL_RATIO_CAP.high));
      this.renderer.setClearColor(0x040912, 0);
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.3;
      const canvas = this.renderer.domElement;
      canvas.className = 'universe-canvas';
      canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;outline:none;';
      canvas.setAttribute('aria-label', '三维知识宇宙；拖动旋转，滚轮缩放，右键或双指平移。也可使用目录浏览。');
      canvas.setAttribute('role', 'img');
      this.container.append(canvas);
      this.labels = document.createElement('div');
      this.labels.className = 'universe-labels';
      this.labels.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden;';
      this.labels.setAttribute('aria-label', '主题与内容标签');
      this.container.append(this.labels);
      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(43, 1, 0.1, 650);
      this.camera.position.set(0, 24, 38);
      this.controls = new OrbitControls(this.camera, canvas);
      this.controls.enableDamping = true; this.controls.dampingFactor = 0.085;
      this.controls.minDistance = 3; this.controls.maxDistance = 160;
      this.controls.rotateSpeed = 0.65; this.controls.zoomSpeed = 0.8;
      this.controls.screenSpacePanning = true;
      this.controls.addEventListener('change', this.controlChange = () => this.requestFrame());
      this.controls.addEventListener('start', this.controlStart = () => {
        this.spiders?.stopFollowing();
        this.interacting = true; this.transition = null; this.settleUntil = performance.now() + 1200; this.requestFrame();
      });
      this.controls.addEventListener('end', this.controlEnd = () => {
        this.interacting = false; this.settleUntil = performance.now() + 1200; this.requestFrame();
      });
      this.scene.add(new THREE.HemisphereLight(0xb6daf5, 0x060b18, 1.2));
      const key = new THREE.DirectionalLight(0xd8efff, 2.4); key.position.set(-20, 35, 22); this.scene.add(key);
      this.content = new THREE.Group(); this.scene.add(this.content);
      this.tractorGroup = new THREE.Group(); this.scene.add(this.tractorGroup);
      this.showTractorLines = true; this.rawTractorLines = []; this.tractorMeshes = [];
      this.empRipples = [];
      this.warpReticle = null;
      this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2();
      this.makeStars();
      this.listen(canvas, 'pointerdown', event => {
        this.onCanvasClick?.();
        this.triggerEmpRipple(event);
        if (this.pointerDown) this.pointerDown.multiple = true;
        else this.pointerDown = {id: event.pointerId, x: event.clientX, y: event.clientY, time: performance.now(), multiple: false};
      });
      this.listen(canvas, 'pointermove', event => {
        if (this.pointerDown) {
          if (Math.hypot(event.clientX - this.pointerDown.x, event.clientY - this.pointerDown.y) > 5) this.pointerDown.dragged = true;
          return;
        }
        if (event.pointerType !== 'touch') this.setHover(this.pick(event));
      });
      this.listen(canvas, 'pointerup', event => {
        const down = this.pointerDown; this.pointerDown = null;
        if (down && down.id === event.pointerId && !down.dragged && !down.multiple && event.button === 0 && performance.now() - down.time < 650) {
          const body = this.pick(event);
          if (body) { this.selected = body; this.setHover(null); this.onSelect({...body.data}); this.requestFrame(); }
        }
      });
      this.listen(canvas, 'pointercancel', () => { this.pointerDown = null; this.setHover(null); });
      this.listen(canvas, 'pointerleave', () => this.setHover(null));
      this.listen(canvas, 'webglcontextlost', event => {
        event.preventDefault(); this.ready = false; this.stopFrame();
        this.labels.hidden = true; this.onError(new Error('3D 图形上下文已中断，请使用目录或刷新页面。'));
      });
      this.listen(document, 'visibilitychange', () => {
        if (document.hidden) this.stopFrame(); else this.requestFrame();
      });
      this.motionChange = event => { this.reducedMotion = event.matches; this.requestFrame(); };
      this.motionQuery.addEventListener('change', this.motionChange);
      this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(container);
      this.intersectionObserver = new IntersectionObserver(entries => {
        this.visible = entries[0].isIntersecting;
        if (this.visible) this.requestFrame(); else this.stopFrame();
      });
      this.intersectionObserver.observe(container);
      this.ready = true; this.resize();
    } catch (error) {
      this.ready = false;
      this.renderer?.dispose(); this.renderer?.domElement.remove(); this.labels?.remove();
      this.onError(error);
    }
  }

  listen(target, type, fn) { target.addEventListener(type, fn); this.listeners.push(() => target.removeEventListener(type, fn)); }

  makeStars() {
    const rand = random(7401), positions = [], colors = [];
    for (let i = 0; i < 1900; i++) {
      const theta = rand() * TAU, z = rand() * 2 - 1, radius = 150 + rand() * 120, latitude = Math.sqrt(1 - z * z);
      positions.push(Math.cos(theta) * latitude * radius, z * radius, Math.sin(theta) * latitude * radius);
      const shade = 0.23 + rand() * 0.5;
      colors.push(shade * (0.75 + rand() * 0.25), shade * (0.85 + rand() * 0.15), shade);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const material = new THREE.PointsMaterial({size: 0.37, vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false, sizeAttenuation: true});
    this.stars = new THREE.Points(geometry, material); this.scene.add(this.stars);
  }

  glowTexture() {
    if (this.textures.has('glow')) return this.textures.get('glow');
    const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 128;
    const ctx = canvas.getContext('2d'), gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    gradient.addColorStop(0, 'rgba(255,255,255,0.85)'); gradient.addColorStop(0.19, 'rgba(255,255,255,0.48)');
    gradient.addColorStop(0.4, 'rgba(255,255,255,0.12)'); gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, 128, 128);
    const texture = new THREE.CanvasTexture(canvas); this.textures.set('glow', texture); return texture;
  }

  createBody(layout, sun = false, decorative = false) {
    const {data, radius} = layout, group = new THREE.Group(); group.position.fromArray(layout.position);
    if (layout.rotationY) group.rotation.y = layout.rotationY;
    const agentColors = {
      user: '#f4c069',
      antigravity: '#a78bfa',
      codex: '#38bdf8',
      pangu: '#f87171',
      doubao: '#34d399',
      deepseek: '#60a5fa',
      qwen: '#fbbf24',
      external: '#fb923c'
    };
    const color = data.color || (data.kind === 'agent' ? (agentColors[data.id] || '#a78bfa')
      : isMcpItem(data) ? '#38bdf8'
      : (isSkillItem(data) || data.kind === 'skill') ? '#f43f5e'
      : data.kind === 'topic' ? neuralColor(data.id)
      : data.kind === 'memory' ? '#87aef2'
      : '#6fddca');
    const special = data.kind === 'notes-hub' || (data.kind === 'topic' && !data.preview) || data.kind === 'agent' || data.isFlagship;
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, data.preview ? 12 : 24, data.preview ? 8 : 16), new THREE.MeshBasicMaterial({color, transparent: true, opacity: 0, depthWrite: false}));
    group.add(mesh);
    let modelInstance = null;
    if (this.modelsReady) {
      if (isSkillTopic(data) || isMcpTopic(data) || isSkillItem(data) || isMcpItem(data) || data.isFlagship) {
        try {
          const scale = (isSkillTopic(data) || isMcpTopic(data)) ? radius * 1.35 : data.isFlagship ? radius * 0.95 : radius * 1.15;
          modelInstance = createUniverseModel(data, scale);
        } catch {}
      }
    }
    if (data.kind === 'notes-hub') {
      if (this.eyeHubMode) makeEye(group, radius, this.glowTexture());
      else makeCore(group, radius, this.glowTexture());
    }
    // 「我的内容」核心 = 科技之眼（全息线框外壳 + 机械光圈虹膜 + 深空瞳孔 + 刻度环/扫描弧）
    else if (data.kind === 'agent' && data.id === 'user') makeEye(group, radius, this.glowTexture());
    else if (isSkillTopic(data) && !data.preview) makeSkillStation(group, radius, color, this.glowTexture(), modelInstance);
    else if (isMcpTopic(data) && !data.preview) makeMcpWarship(group, radius, color, this.glowTexture(), modelInstance);
    else if (data.kind === 'agent') {
      // 导入分区（Antigravity / Codex / 豆包 / 其他导入）统一使用「知识宇宙」那套天体模型
      // （脉冲星 / 星环 / 吸积盘 / 水母），不再用空间站模型，保持两处视觉语言一致。
      const cType = hash(data.id) % 4;
      if (cType === 0) makePulsar(group, radius, color, this.glowTexture(), hash(data.id));
      else if (cType === 1) makeRingworld(group, radius, color, this.glowTexture(), hash(data.id));
      else if (cType === 2) makeAccretionDisk(group, radius, color, this.glowTexture(), hash(data.id));
      else makeJelly(group, radius, color, this.glowTexture());
    }
    else if (data.kind === 'topic' && !data.preview) {
      const cType = hash(data.id) % 4;
      if (cType === 0) makePulsar(group, radius, color, this.glowTexture(), hash(data.id));
      else if (cType === 1) makeRingworld(group, radius, color, this.glowTexture(), hash(data.id));
      else if (cType === 2) makeAccretionDisk(group, radius, color, this.glowTexture(), hash(data.id));
      else makeJelly(group, radius, color, this.glowTexture());
    }
    else if (data.isFlagship) {
      const fleetSector = FLEET_SECTOR_BERTHS[data.domainId]?.sector || 'core';
      makeFlagshipVessel(group, radius, color, this.glowTexture(), fleetSector, hash(data.id), modelInstance);
    }
    else if (isMcpItem(data) || data.kind === 'mcp') {
      makeDefenseVessel(group, radius, color, this.glowTexture(), hash(data.id), modelInstance);
    } else if (isSkillItem(data) || data.kind === 'skill') {
      const fleetSector = FLEET_SECTOR_BERTHS[data.domainId]?.sector || 'core';
      makeAttackVessel(group, radius, color, this.glowTexture(), fleetSector, hash(data.id), modelInstance);
    } else if (data.kind === 'topic' && data.preview) {
      // 子主题预览：小型彩色线框球（区别于笔记小点，突出主题层级）
      makeMiniSphere(group, radius, color, this.glowTexture());
    } else {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(radius * 0.42, 12, 10), new THREE.MeshBasicMaterial({color, toneMapped: false})); group.add(dot);
    }
    this.content.add(group);
    if (layout.rotationY) group.rotation.y = layout.rotationY;
    const body = {...layout, group, mesh, sun, decorative, label: null, modelInstance, drift: layout.drift, tacticalBerth: layout.tacticalBerth, heading: layout.heading, escortOffset: layout.escortOffset, initialAngle: layout.initialAngle}; mesh.userData.body = body;
    if (layout.previewParent) { body.previewAnchor = this.bodies.find(b => b.data.id === layout.previewParent && !b.data.preview); body.previewVector = new THREE.Vector3(...layout.previewOffset); }
    if (!decorative) this.pickMeshes.push(mesh);
    const label = document.createElement(special ? 'button' : 'div');
    if (special) {
      label.type = 'button';
      label.onclick = () => { this.selected = body; this.onSelect({...data}); };
      label.setAttribute('aria-label', `打开 ${data.title}`);
    }
    label.className = `universe-label universe-label--${data.kind}${sun ? ' universe-label--sun' : ''}${data.isFlagship ? ' universe-label--flagship' : ''}`;
    label.style.cssText = 'position:absolute;left:0;top:0;transform:translate(-50%,0);max-width:200px;'; label.style.setProperty('--node-color', color);
    const isStation = isSkillTopic(data);
    const isShip = isMcpTopic(data);
    const title = document.createElement('span'); title.className = 'universe-label-title';
    title.textContent = isStation ? (data.isStationCore ? '🛸 技能空间站 · 母港中枢' : '🛸 技能空间站') : data.isFlagship ? data.title : (data.title || data.name || '未命名');
    const meta = document.createElement('span'); meta.className = 'universe-label-kind';
    const count = data.count ?? data.member_count ?? data.item_count;
    const celestialNames = ['⚡ 脉冲星·磁星', '🪐 环世界·星环要塞', '🕳️ 吸积盘·引力奇点', '🌌 以太星云·水母'];
    const celestialLabel = (data.kind === 'topic' && !sun) ? '◉ 子主题' : celestialNames[hash(data.id) % 4];
    meta.textContent = data.kind === 'notes-hub' ? `${this.eyeHubMode ? '我的内容 · 笔记与记忆' : '我的空间'}${count == null ? '' : ` · ${count}`} · 点击打开`
      : data.kind === 'agent' ? `${data.id === 'user' ? '👤 我的内容' : '🛰️ 导入分区'}${count == null ? '' : ` · ${count}`} · 点击进入`
      : isStation ? (data.isStationCore ? `星际母港 · ${count || 492} 项技能编队总中枢` : `🛸 技能空间站${count == null ? '' : ` · ${count}`} · 点击进入`)
      : data.isFlagship ? `${data.code || 'FLAGSHIP'} · 附带 ${data.fleetCount || 0} 艘护航舰`
      : isShip ? `▲ MCP 战舰${count == null ? '' : ` · ${count}`}`
      : data.isAnchor ? '◂ 父星球 · 点击返回'
      : data.kind === 'topic' ? `${celestialLabel}${count == null ? '' : ` · ${count}`} · 点击进入`
      : data.kind === 'memory' ? '◇ AI 记忆'
      : isMcpItem(data) ? '🛡️ MCP 防御战舰'
      : (isSkillItem(data) || data.kind === 'skill') ? (data.isEscort ? `⚔️ 护航战舰 · ${data.title}` : '⚔️ 技能攻击舰')
      : '▤ 笔记';
    if (special) { const total = document.createElement('strong'); total.className = 'neural-node-count'; total.textContent = count ?? ''; label.append(total); }
    label.append(title, meta);
    label.hidden = true; // Hidden by default, only shown on hover or selection!
    this.labels.append(label); body.label = label;
    // 合并该天体的静态装饰线：draw call 从 10~30 降到 1~3，视觉不变
    collapseLines(group);
    this.bodies.push(body); return body;
  }

  updateCustomModels() {
    if (!this.content || !this.bodies) return;
    for (const body of this.bodies) {
      const data = body.data;
      if (!data) continue;
      const isStation = isSkillTopic(data) && !data.preview;
      const isShip = isMcpTopic(data) && !data.preview;
      const isMod = isSkillItem(data) && !data.preview;
      const isVessel = isMcpItem(data) && !data.preview;
      const isFlagship = Boolean(data.isFlagship);
      if ((isStation || isShip || isMod || isVessel || isFlagship) && !body.modelInstance) {
        try {
          const scale = (isStation || isShip) ? body.radius * 1.35 : isFlagship ? body.radius * 0.95 : body.radius * 1.15;
          const model = createUniverseModel(data, scale);
          body.group.add(model);
          body.modelInstance = model;
        } catch {}
      }
    }
    this.requestFrame();
  }

  setTractorLines(lines = []) {
    this.rawTractorLines = Array.isArray(lines) ? lines : [];
    if (this.ready) this.rebuildTractorLines();
  }

  setTractorLinesVisible(visible) {
    this.showTractorLines = Boolean(visible);
    if (this.tractorGroup) this.tractorGroup.visible = this.showTractorLines;
    this.requestFrame();
  }

  rebuildTractorLines() {
    if (!this.tractorGroup) return;
    if (this.tractorPulsePoints) {
      this.tractorGroup.remove(this.tractorPulsePoints);
      this.tractorPulsePoints.geometry?.dispose();
      this.tractorPulsePoints.material?.dispose();
      this.tractorPulsePoints = null;
    }
    for (const tm of this.tractorMeshes) {
      tm.geometry?.dispose();
    }
    this.tractorGroup.clear();
    this.tractorMeshes = [];
    this.tractorCurves = [];
    if (!this.showTractorLines || !this.rawTractorLines?.length || !this.bodies?.length) return;

    const colors = {
      SAME_TOPIC: 0x38bdf8,
      CITATION: 0x34d399,
      INHERITANCE: 0xa78bfa,
      CONFLICT: 0xf87171,
      AGENT: 0xedc78b
    };

    const bodyMap = new Map();
    for (const b of this.bodies) {
      if (b.data?.id) bodyMap.set(String(b.data.id), b);
      if (b.data?.parent_id) bodyMap.set(`parent_${b.data.id}`, b);
    }

    // 收集场景内所有作战战舰单元（四大战区主力战舰、护航僚机与 MCP 达斯·维达座机）
    const flagships = this.bodies.filter(b => b.data?.isFlagship);
    const escorts = this.bodies.filter(b => b.data?.isEscort);
    const mcpWarships = this.bodies.filter(b => isMcpTopic(b.data) || isMcpItem(b.data) || b.data?.domainId === 'security' || b.data?.id === '__mcp_library');
    const allWarships = [...flagships, ...escorts, ...mcpWarships];

    // 判断节点是否为空间站设施本身（总览中的远端母港空间站 或 内部中枢核心）
    const isStationNode = (id, data) => {
      const sid = String(id || '');
      if (sid === '__local_skills' || sid === 'topic-ac4951522739430aa647f83546395ecd') return true;
      if (data?.isStationCore) return true;
      if (isSkillTopic(data)) return true;
      return false;
    };

    // 战舰停靠分配器：将与技能相关的牵引线精准对接到所属领域的星战主力舰/护航战舰，坚决不对接空间站
    const domainUsage = new Map();
    const resolveWarshipForTarget = (targetTitle, targetId, preferredDomainId = null) => {
      if (!allWarships.length) return null;
      let targetDomain = preferredDomainId;
      if (!targetDomain) {
        if (/mcp|protocol|security/i.test(targetTitle || '') || /mcp/i.test(targetId || '')) {
          targetDomain = 'security';
        } else {
          targetDomain = classifySkillDomain({title: targetTitle, id: targetId})?.id || 'backend';
        }
      }

      const count = domainUsage.get(targetDomain) || 0;
      domainUsage.set(targetDomain, count + 1);

      // 若为 MCP 或安全协议，精准锁定达斯·维达座机 Darth Vader's TIE Fighter
      if (targetDomain === 'security' && mcpWarships.length > 0) {
        return mcpWarships[count % mcpWarships.length];
      }

      const domainFlagship = flagships.find(f => f.data?.domainId === targetDomain);
      const domainEscorts = escorts.filter(e => e.data?.domainId === targetDomain);

      // 同一领域多条光束时在旗舰与先锋护航舰之间智能编队分流，形成恢弘的舰队星际数据网络
      if (count > 0 && domainEscorts.length > 0) {
        return domainEscorts[(count - 1) % domainEscorts.length];
      }
      if (domainFlagship) return domainFlagship;
      if (domainEscorts.length > 0) return domainEscorts[0];

      // 回退策略：平摊至任意战备主力舰，坚决不回退到空间站
      return flagships[count % flagships.length] || allWarships[0];
    };

    const drawnPairs = new Set();
    const pulsePositions = [];
    const pulseColors = [];

    for (const line of this.rawTractorLines) {
      let fromBody = bodyMap.get(String(line.from_id)) || (line.from_topic_id ? bodyMap.get(String(line.from_topic_id)) : null);
      let toBody = bodyMap.get(String(line.to_id)) || (line.to_topic_id ? bodyMap.get(String(line.to_topic_id)) : null);

      const fromIsStation = isStationNode(line.from_id, fromBody?.data) || isStationNode(line.from_topic_id, fromBody?.data);
      const toIsStation = isStationNode(line.to_id, toBody?.data) || isStationNode(line.to_topic_id, toBody?.data);

      // 起点为空间站：重新导向至对应领域的战备战舰/旗舰
      if (fromIsStation && allWarships.length > 0) {
        const targetTitle = line.to_title || toBody?.data?.title || toBody?.data?.name || '';
        const warship = resolveWarshipForTarget(targetTitle, line.to_id, line.domain_id);
        if (warship) fromBody = warship;
      }

      // 终点为空间站：重新导向至对应领域的战备战舰/旗舰
      if (toIsStation && allWarships.length > 0) {
        const targetTitle = line.from_title || fromBody?.data?.title || fromBody?.data?.name || '';
        const warship = resolveWarshipForTarget(targetTitle, line.from_id, line.domain_id);
        if (warship) toBody = warship;
      }

      if (!fromBody || !toBody || fromBody === toBody) continue;

      const pairKey = [fromBody.data.id, toBody.data.id].sort().join('--') + line.type;
      if (drawnPairs.has(pairKey)) continue;
      drawnPairs.add(pairKey);

      const start = fromBody.group.position.clone();
      const end = toBody.group.position.clone();
      const dist = start.distanceTo(end);
      if (dist < 0.1) continue;

      const mid = start.clone().lerp(end, 0.5);
      mid.y += Math.min(dist * 0.22, 5.5);
      mid.z += (Math.sin(dist) * 0.15) * dist * 0.1;

      const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
      const points = curve.getPoints(24);
      const geom = new THREE.BufferGeometry().setFromPoints(points);
      const colorHex = colors[line.type] || 0x38bdf8;
      const opacity = line.type === 'CONFLICT' ? 0.65 : line.type === 'AGENT' ? 0.5 : 0.35;
      const mat = new THREE.LineDashedMaterial({
        color: colorHex,
        transparent: true,
        opacity,
        depthWrite: false,
        dashSize: line.type === 'AGENT' ? 0.45 : 0.32,
        gapSize: 0.5,
        blending: THREE.AdditiveBlending
      });
      const lineObj = new THREE.Line(geom, mat);
      lineObj.computeLineDistances();
      const fromStr = String(fromBody.data.id || '');
      const toStr = String(toBody.data.id || '');
      let hash = 0;
      for (let i = 0; i < fromStr.length; i++) hash = ((hash << 5) - hash + fromStr.charCodeAt(i)) | 0;
      for (let i = 0; i < toStr.length; i++) hash = ((hash << 5) - hash + toStr.charCodeAt(i)) | 0;
      const phase = Math.abs(hash % 628) / 100;
      const breathSpeed = 1.6 + (Math.abs(hash % 100) / 100) * 0.8;

      lineObj.userData = {
        tractorLine: line,
        fromId: fromBody.data.id,
        toId: toBody.data.id,
        baseOpacity: opacity,
        isConflict: line.type === 'CONFLICT',
        phase,
        breathSpeed
      };
      this.tractorGroup.add(lineObj);
      this.tractorMeshes.push(lineObj);

      // 流光粒子能量流 (Flowing energy pulse stream)
      const c = new THREE.Color(colorHex);
      const pulseCount = line.type === 'CONFLICT' ? 5 : 4;
      for (let p = 0; p < pulseCount; p++) {
        pulsePositions.push(start.x, start.y, start.z);
        pulseColors.push(c.r * 1.35, c.g * 1.35, c.b * 1.35);
      }
      this.tractorCurves.push({
        curve,
        lineObj,
        fromBody,
        toBody,
        pulseCount,
        speed: line.type === 'CONFLICT' ? 0.38 : line.type === 'CITATION' ? 0.26 : 0.2,
        fromId: fromBody.data.id,
        toId: toBody.data.id,
        isConflict: line.type === 'CONFLICT',
        baseColor: c,
        phase,
        breathSpeed
      });
    }

    if (pulsePositions.length > 0) {
      const pGeom = new THREE.BufferGeometry();
      pGeom.setAttribute('position', new THREE.Float32BufferAttribute(pulsePositions, 3));
      pGeom.setAttribute('color', new THREE.Float32BufferAttribute(pulseColors, 3));
      const pMat = new THREE.PointsMaterial({
        size: 0.38,
        vertexColors: true,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true
      });
      this.tractorPulsePoints = new THREE.Points(pGeom, pMat);
      this.tractorGroup.add(this.tractorPulsePoints);
    }

    this.requestFrame();
  }

  setData(data = {}, force = false) {
    if (!this.ready) return;
    if (!this.modelsReady) {
      loadUniverseModels().then(() => {
        this.modelsReady = true;
        this.updateCustomModels();
      }).catch(() => {});
    }
    const dataKey = data.overview
      ? `overview_${data.mode || 'topic'}_${data.notesCount}_${(data.topics || []).length}_${(data.agents || []).length}`
      : `detail_${data.id || data.title}_${(data.items || []).length}_${(data.topics || []).length}_${data.anchor?.id || ''}`;

    if (!force && this.currentDataKey === dataKey && this.bodies?.length > 0) {
      this.hovered = null;
      this.selected = null;
      this.transition = null;
      if (data.overview) this.resetView(false);
      this.requestFrame();
      return;
    }
    this.currentDataKey = dataKey;
    this.clearContent(); this.data = data; this.hovered = null; this.selected = null; this.transition = null;
    // 「我的空间」聚类视图判定：眼球核心只在此视图启用，其余视图（知识宇宙总览/详情）恢复金色核心
    const clusterNotesView = data.overview && (!data.mode || data.mode === 'topic')
      && !(data.topics || []).length && (data.items || []).length >= 30;
    this.eyeHubMode = clusterNotesView;
    const layout = buildUniverseLayout(data);
    if (!data.overview && (isSkillTopic(data.topic) || (data.title && /^skills?(?:\s*[\/|｜]\s*.*)?$/i.test(data.title.trim())))) {
      this.createBody({
        data: {
          id: '__local_skills',
          kind: 'topic',
          title: '🛸 技能空间站 · 母港中枢',
          count: data.total || data.items?.length || 492,
          isStationCore: true
        },
        radius: 6.0,
        position: [0, 0, 0]
      }, true, false);
    } else if (!data.mode || data.mode === 'topic' || !data.overview) {
      // 眼球核心代表「我的内容」（我写的笔记 + 关于我的记忆），只数我自己贡献的那部分；
      // 知识宇宙总览的金色核心是进入「我的空间」的入口，不显示个人计数。
      const myContentCount = (data.items || []).filter(i => normalizeAgent(i.author) === 'user').length;
      this.createBody({data: {id: '__notes_hub', kind: 'notes-hub', title: this.eyeHubMode ? '我的内容' : '我的空间', count: this.eyeHubMode ? myContentCount : null}, radius: 7.2, position: [0, 0, 0]}, true, false);
    }
    // 子主题详情：父星球作为「来路」锚点保留在场景边缘，点击可返回父星球
    if (!data.overview && data.anchor) {
      this.createBody({
        data: {id: data.anchor.id, kind: 'topic', title: data.anchor.title || '父星球', count: null, isAnchor: true},
        radius: 3.0,
        position: [0, 2.2, -30]
      }, false, false);
    }
    for (const entry of layout) this.createBody(entry, Boolean(data.overview && (entry.data.kind === 'topic' || entry.data.kind === 'agent') && !entry.data.preview));
    const flagships = this.bodies.filter(b => b.data?.isFlagship);
    for (const b of this.bodies) {
      if (b.data?.isEscort) {
        b.escortAnchor = flagships.find(f => f.data?.domainId === b.data?.domainId) || null;
      }
    }
    if (data.overview && (!data.mode || data.mode === 'topic')) makeMembershipLinks(this.content, this.bodies);
    if (!data.overview) {
      // 为子行星与外围笔记环带绘制真实轨道环线
      const topicOrbits = layout.filter(e => e.data.kind === 'topic' && e.orbit);
      const noteDistances = [...new Set(layout.filter(e => e.data.kind !== 'topic').map(e => Math.round((e.orbit?.distance || 0) * 2) / 2).filter(Boolean))];

      for (const tEntry of topicOrbits) {
        const dist = tEntry.orbit.distance;
        const tilt = tEntry.orbit.tilt || 0.22;
        const yOffset = tEntry.orbit.yOffset || 0;
        const points = [];
        for (let i = 0; i <= 160; i++) {
          const a = i / 160 * TAU;
          points.push(new THREE.Vector3(Math.cos(a) * dist, Math.sin(a) * dist * tilt + yOffset, Math.sin(a) * dist));
        }
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({color: 0x818cf8, transparent: true, opacity: 0.32, depthWrite: false})
        );
        this.content.add(line);
      }

      for (const nDist of noteDistances) {
        const sample = layout.find(e => (Math.round((e.orbit?.distance || 0) * 2) / 2) === nDist);
        if (!sample || !sample.orbit) continue;
        const dist = sample.orbit.distance;
        const tilt = sample.orbit.tilt || 0.24;
        const yOffset = sample.orbit.yOffset || 0;
        const points = [];
        for (let i = 0; i <= 160; i++) {
          const a = i / 160 * TAU;
          points.push(new THREE.Vector3(Math.cos(a) * dist, Math.sin(a) * dist * tilt + yOffset, Math.sin(a) * dist));
        }
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({color: 0x577384, transparent: true, opacity: 0.15, depthWrite: false})
        );
        this.content.add(line);
      }
    }
    this.rebuildTractorLines();
    // 「我的空间」聚类视图：牵引线 = 分区节点间的共享主题关联（AGENT 线）。
    // note 级引用线端点多为 memory/source 对象，该视图没有对应节点，故改画分区关联。
    if (this.eyeHubMode) {
      const agentTopics = new Map();
      for (const it of data.items || []) {
        const k = normalizeAgent(it.author);
        if (!agentTopics.has(k)) agentTopics.set(k, new Set());
        for (const t of (it.topics || [])) agentTopics.get(k).add(String(t.id || t));
      }
      const agentKeys = [...agentTopics.keys()];
      const agentLines = [];
      for (let i = 0; i < agentKeys.length; i++) {
        for (let j = i + 1; j < agentKeys.length; j++) {
          const a = agentKeys[i], b = agentKeys[j];
          const shared = [...agentTopics.get(a)].filter(t => agentTopics.get(b).has(t)).length;
          if (shared > 0) {
            agentLines.push({
              id: `ag_${a}_${b}`, from_id: a, to_id: b, type: 'AGENT',
              relation: 'SHARED_TOPIC', confidence: Math.min(1, shared / 4), shared
            });
          }
        }
      }
      this.rawTractorLines = agentLines;
      this.rebuildTractorLines();
    }
    const extentLayout = (data.overview && layout.length > 5)
      ? layout.filter(e => !isSkillTopic(e.data) && !e.data?.isFlagship && !e.data?.isEscort)
      : layout;
    // 「我的空间」只有 1 个核心 + 5 个分区，整体尺度远小于知识宇宙的 37 个主题；
    // 若沿用 30 的下限，相机会被推到 ~77 单位外，中心核心缩成一个点。
    const extentFloor = data.mode === 'agent' ? 17 : 30;
    this.extent = Math.max(extentFloor, ...extentLayout.map(e => Math.hypot(...e.position) + e.radius + 2));
    this.resetView(false); this.requestFrame();
  }

  clearContent() {
    if (this.tractorPulsePoints) {
      this.tractorGroup?.remove(this.tractorPulsePoints);
      this.tractorPulsePoints.geometry?.dispose();
      this.tractorPulsePoints.material?.dispose();
      this.tractorPulsePoints = null;
    }
    for (const tm of this.tractorMeshes) {
      tm.geometry?.dispose();
    }
    if (this.tractorGroup) this.tractorGroup.clear();
    this.tractorMeshes = [];
    this.tractorCurves = [];
    if (!this.content) return;
    this.content.traverse(object => {
      if (!object.userData?.sharedUniverseResource && !object.geometry?.userData?.sharedUniverseResource) {
        object.geometry?.dispose();
      }
      for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
        if (!material?.userData?.sharedUniverseResource) material?.dispose();
      }
    });
    this.content.clear(); this.labels.replaceChildren(); this.bodies = []; this.pickMeshes = [];
  }

  resize() {
    if (!this.ready) return;
    const {width, height} = this.container.getBoundingClientRect();
    if (width < 1 || height < 1) { this.stopFrame(); return; }
    if (this.width && Math.abs(width - this.width) < 1 && Math.abs(height - this.height) < 1) return;
    const wasNarrow = this.width && this.width < 600;
    this.width = width; this.height = height; this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
    if (this.data && wasNarrow !== (width < 600)) this.resetView(true);
    this.requestFrame();
  }

  resetView(immediate = false) {
    this.spiders?.stopFollowing();
    if (!this.ready) return;
    this.selected = null;
    const aspect = this.camera.aspect || 1;
    const distance = (this.extent || 17) / Math.sin(THREE.MathUtils.degToRad(21.5)) * Math.max(1, 1 / aspect) * 0.94;
    this.controls.maxDistance = Math.max(160, distance * 2);
    this.camera.far = Math.max(650, distance + this.extent * 3); this.camera.updateProjectionMatrix();
    const end = this.data?.overview ? new THREE.Vector3(0, distance * 0.12, distance * 0.993) : new THREE.Vector3(0, distance * 0.52, distance * 0.855);
    this.moveCamera(new THREE.Vector3(), end, immediate);
  }

  triggerEmpRipple(event) {
    if (!this.ready || !this.scene || !this.camera) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    const vec = new THREE.Vector3(x, y, 0.5).unproject(this.camera);
    const dir = vec.sub(this.camera.position).normalize();
    const origin = this.camera.position.clone().addScaledVector(dir, 18);

    const rippleGroup = new THREE.Group();
    rippleGroup.position.copy(origin);
    rippleGroup.quaternion.copy(this.camera.quaternion);

    const pts = [];
    for (let i = 0; i <= 36; i++) {
      const a = (i / 36) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a), Math.sin(a), 0));
    }
    const ringGeom = new THREE.BufferGeometry().setFromPoints(pts);
    const ringMat = new THREE.LineBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });
    rippleGroup.add(new THREE.Line(ringGeom, ringMat));

    const sparkCount = 16;
    const sparkPts = [], sparkDirs = [];
    for (let i = 0; i < sparkCount; i++) {
      sparkPts.push(0, 0, 0);
      const angle = (i / sparkCount) * Math.PI * 2 + (Math.random() - 0.5) * 0.4;
      const spd = 3.2 + Math.random() * 2.5;
      sparkDirs.push(Math.cos(angle) * spd, Math.sin(angle) * spd, (Math.random() - 0.5) * 0.8);
    }
    const sparkGeom = new THREE.BufferGeometry();
    sparkGeom.setAttribute('position', new THREE.Float32BufferAttribute(sparkPts, 3));
    const sparkMat = new THREE.PointsMaterial({
      color: 0xfcd34d,
      size: 0.28,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });
    rippleGroup.add(new THREE.Points(sparkGeom, sparkMat));

    this.scene.add(rippleGroup);
    this.empRipples.push({
      group: rippleGroup,
      ringMat,
      sparkMat,
      sparkGeom,
      sparkDirs,
      sparkPts,
      startTime: performance.now(),
      duration: 600
    });
    this.requestFrame();
  }

  triggerWarpReticle(target) {
    if (!this.ready || !this.scene) return;
    if (!this.warpReticle) {
      this.warpReticle = new THREE.Group();
      this.warpReticle.name = 'warp-reticle';
      for (const rad of [1.4, 2.2]) {
        const pts = [];
        for (let i = 0; i <= 36; i++) {
          const a = (i / 36) * Math.PI * 2;
          pts.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 0));
        }
        const g = new THREE.BufferGeometry().setFromPoints(pts);
        const m = new THREE.LineBasicMaterial({
          color: 0x38bdf8,
          transparent: true,
          opacity: 0.75,
          blending: THREE.AdditiveBlending,
          depthWrite: false
        });
        this.warpReticle.add(new THREE.Line(g, m));
      }
      for (let i = 0; i < 4; i++) {
        const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
        const p1 = new THREE.Vector3(Math.cos(a) * 2.5, Math.sin(a) * 2.5, 0);
        const p2 = new THREE.Vector3(Math.cos(a) * 3.1, Math.sin(a) * 3.1, 0);
        const g = new THREE.BufferGeometry().setFromPoints([p1, p2]);
        const m = new THREE.LineBasicMaterial({
          color: 0xfcd34d,
          transparent: true,
          opacity: 0.95,
          blending: THREE.AdditiveBlending,
          depthWrite: false
        });
        this.warpReticle.add(new THREE.Line(g, m));
      }
      this.scene.add(this.warpReticle);
    }
    this.warpReticle.position.copy(target);
    this.warpReticle.visible = true;
    this.warpReticle.scale.setScalar(3.5);
  }

  moveCamera(target, position, immediate = false) {
    const damping = this.controls.enableDamping; this.controls.enableDamping = false; this.controls.update(); this.controls.enableDamping = damping;
    if (immediate || this.reducedMotion) {
      this.transition = null; this.controls.target.copy(target); this.camera.position.copy(position); this.controls.update();
      if (this.warpReticle) this.warpReticle.visible = false;
    } else {
      this.transition = {
        start: performance.now(),
        duration: 450,
        from: this.camera.position.clone(),
        to: position.clone(),
        targetFrom: this.controls.target.clone(),
        targetTo: target.clone()
      };
      this.triggerWarpReticle(target);
    }
    this.settleUntil = performance.now() + 1000; this.requestFrame();
  }

  focus(id, kind) {
    if (!this.ready) return false;
    const body = this.bodies.find(b => !b.decorative && String(b.data.id) === String(id) && (!kind || b.data.kind === kind));
    if (!body) return false;
    this.selected = body;
    const target = body.group.position.clone(), direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this.moveCamera(target, target.clone().addScaledVector(direction, body.radius * 9)); return true;
  }

  // 父星球锚点：返回锚点在世界空间中的位置（供逐级退出做镜头过渡）
  getAnchorPosition() {
    const a = this.bodies.find(b => b.data?.isAnchor);
    if (!a) return null;
    const p = a.group.position;
    return [p.x, p.y, p.z];
  }

  // 逐级退出回父星球时：以父星球锚点为起点，平滑飞回父星球系统整体视角
  enterFromPosition(from) {
    if (!this.ready || !Array.isArray(from)) return;
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(from[0], from[1], from[2]);
    this.controls.update();
    this.resetView(false);
  }

  zoom(factor) {
    if (!this.ready || !Number.isFinite(factor) || factor <= 0) return;
    this.transition = null;
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() / factor, this.controls.minDistance, this.controls.maxDistance));
    this.moveCamera(this.controls.target, this.controls.target.clone().add(offset), true);
  }

  setPaused(value) { this.paused = Boolean(value); this.requestFrame(); }

  setQuality(level) {
    if (!PIXEL_RATIO_CAP[level]) return;
    this.quality = level;
    this.renderer?.setPixelRatio(Math.min(window.devicePixelRatio || 1, PIXEL_RATIO_CAP[level]));
    this.requestFrame?.();
  }
  setReading(value) { this.reading = Boolean(value); if (!value) this.selected = null; this.requestFrame(); }
  setActive(value) { this.active = Boolean(value); if (value) { this.resize(); this.requestFrame(); } else this.stopFrame(); }

  setHover(body) {
    if (this.hovered === body) return;
    this.hovered = body; this.renderer.domElement.style.cursor = body ? 'pointer' : 'grab';
    this.onHover(body ? {...body.data} : null); this.requestFrame();
  }

  pick(event) {
    if (!this.ready) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets = this.pickMeshes.length > 0 ? this.pickMeshes : this.bodies.map(b => b.mesh);
    const body = this.raycaster.intersectObjects(targets, false)[0]?.object.userData.body;
    return body && !body.decorative ? body : null;
  }

  // Topic & body names only display when hovered or selected! (Optimized O(1) targeting)
  updateLabels() {
    const target = this.hovered || this.selected;
    if (!target || !target.label) {
      if (this._lastLabeledBody && this._lastLabeledBody.label) {
        this._lastLabeledBody.label.hidden = true;
        this._lastLabeledBody = null;
      }
      return;
    }
    if (this._lastLabeledBody && this._lastLabeledBody !== target && this._lastLabeledBody.label) {
      this._lastLabeledBody.label.hidden = true;
    }
    this._lastLabeledBody = target;
    const center = target.group.position;
    const projection = this._projVec || (this._projVec = new THREE.Vector3());
    projection.copy(center).project(this.camera);
    if (projection.z < -1 || projection.z > 1 || Math.abs(projection.x) > 1.05 || Math.abs(projection.y) > 1.05) {
      target.label.hidden = true;
      return;
    }
    const distance = center.distanceTo(this.camera.position);
    const screenRadius = target.radius * this.height / (2 * distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    const x = (projection.x * 0.5 + 0.5) * this.width;
    const y = (-projection.y * 0.5 + 0.5) * this.height + screenRadius * (target.data.kind === 'topic' && !target.data.preview ? 1.6 : 1) + 12;
    target.label.hidden = false;
    target.label.style.left = `${x}px`;
    target.label.style.top = `${y}px`;
    target.label.classList.add('is-selected');
  }

  // 动画总开关：拖拽镜头(interacting)与悬停(hovered)时不阻断全场，确保公转、巡航与粒子流 60FPS 丝滑不卡顿
  canAnimate() {
    return Boolean(this.stationFlight?.active) || (
      !this.paused && !this.reading && !this.reducedMotion &&
      (this.bodies.some(b => b.orbit || b.data.kind === 'topic' || b.drift || b.group.userData?.faceCamera) || this.empRipples?.length > 0)
    );
  }

  requestFrame() {
    if (this.raf || !this.ready || this.disposed || !this.active || !this.visible || document.hidden || !this.width || !this.height) return;
    this.raf = requestAnimationFrame(time => this.frame(time));
  }

  stopFrame() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0; this.lastTime = 0; }

  frame(time) {
    this.raf = 0;
    if (!this.ready || this.disposed || !this.active || !this.visible || document.hidden) return;
    const delta = this.lastTime ? Math.min((time - this.lastTime) / 1000, 0.05) : 0; this.lastTime = time;
    if (this.transition) {
      const duration = this.transition.duration || 450;
      const t = Math.min(1, (time - this.transition.start) / duration);
      const ease = 1 - Math.pow(1 - t, 3);
      this.camera.position.lerpVectors(this.transition.from, this.transition.to, ease);
      this.controls.target.lerpVectors(this.transition.targetFrom, this.transition.targetTo, ease);
      if (this.warpReticle && this.warpReticle.visible) {
        this.warpReticle.quaternion.copy(this.camera.quaternion);
        this.warpReticle.scale.setScalar(3.5 - ease * 2.3);
        this.warpReticle.rotation.z += delta * 2.4;
      }
      if (t === 1) {
        this.transition = null;
        if (this.warpReticle) this.warpReticle.visible = false;
      }
    }
    if (this.empRipples?.length) {
      const now = performance.now();
      for (let i = this.empRipples.length - 1; i >= 0; i--) {
        const item = this.empRipples[i];
        const elapsed = now - item.startTime;
        const progress = elapsed / item.duration;
        if (progress >= 1) {
          this.scene.remove(item.group);
          item.group.traverse(obj => {
            obj.geometry?.dispose();
            obj.material?.dispose();
          });
          this.empRipples.splice(i, 1);
          continue;
        }
        const scale = progress * 7.5 + 0.3;
        item.group.scale.set(scale, scale, 1);
        item.ringMat.opacity = Math.max(0, (1 - progress) * 0.95);
        item.sparkMat.opacity = Math.max(0, (1 - progress) * 0.9);

        const posArr = item.sparkGeom.attributes.position.array;
        for (let s = 0; s < item.sparkDirs.length / 3; s++) {
          posArr[s * 3] = item.sparkDirs[s * 3] * progress * 1.6;
          posArr[s * 3 + 1] = item.sparkDirs[s * 3 + 1] * progress * 1.6;
          posArr[s * 3 + 2] = item.sparkDirs[s * 3 + 2] * progress * 1.6;
        }
        item.sparkGeom.attributes.position.needsUpdate = true;
      }
    }

    // ── 科技之眼：延迟追踪（damped billboard）──────────────────────────
    // 眼睛的所有结构（虹膜 / 瞳孔 / 刻度环 / 扫描弧）都建在 +Z 面，
    // 所以只要让 group 的 +Z 指向相机，眼睛就永远「看着你」。
    //
    // 但**不能每帧直接 lookAt**：那是硬切，拖镜头时眼睛像焊死在相机上，
    // 生硬且不自然。真实的眼睛有惯性 —— 先"看到"，转头再跟上。这里用
    // 两级指数阻尼模拟，两者都写成 k = 1 - e^(-rate·dt)，与帧率无关：
    //   ① 注视点 gazeAim 平滑跟随相机位置 → 眼睛瞄的是相机"刚才"的位置，
    //      相机静止时 gazeAim 收敛到相机，眼睛精确正对；相机一动就"慢半拍"。
    //   ② 朝向 gazeQuat 向"立刻对准 gazeAim"的四元数 slerp → 转头本身也有惯性。
    //   ③ 滞后限幅 maxLagDeg：①② 叠加后的滞后量正比于镜头角速度 —— 常速拖拽
    //      实测中位 ~14°（自然），但快速甩镜头能冲到 50°+，那时眼睛像"跟丢了"。
    //      所以最后再约束**看得见的朝向**：把 gazeQuat 的 +Z 相对「指向相机」
    //      的夹角夹到上限内。常速拖拽够不到上限、手感不变，只有甩动才封顶。
    // 速率见 neural-visuals.js 的 gazeSway.lagAim / lagTurn / maxLagDeg。
    //
    // 这段**刻意放在 canAnimate() 判断之外**，每帧无条件执行：
    // 拖动旋转（interacting）、悬停、选中、reducedMotion 时都要保持注视，
    // 否则一拖鼠标眼睛就"别过脸去"。摆动才属于动效，跟随 canAnimate()。
    {
      const swaying = this.canAnimate();
      const t = time * 0.001;
      // 复用暂存对象，避免每帧分配（Matrix4.lookAt 需要目标矩阵）
      const m4 = this._gazeM4 || (this._gazeM4 = new THREE.Matrix4());
      const dq = this._gazeQ || (this._gazeQ = new THREE.Quaternion());
      const cq = this._gazeClampQ || (this._gazeClampQ = new THREE.Quaternion());
      const up = this._gazeUp || (this._gazeUp = new THREE.Vector3(0, 1, 0));
      const dirCam = this._gazeA || (this._gazeA = new THREE.Vector3());
      const dirEye = this._gazeB || (this._gazeB = new THREE.Vector3());
      const axis = this._gazeC || (this._gazeC = new THREE.Vector3());
      for (const body of this.bodies) {
        const ud = body.group.userData;
        if (!ud?.faceCamera) continue;
        if (!ud.basePos) ud.basePos = body.group.position.clone();
        const g = ud.gazeSway || {};
        if (swaying) {
          // 位置摆动：像悬停在中心轻轻浮动。频率互质，避免走出明显周期。
          body.group.position.set(
            ud.basePos.x + Math.sin(t * 0.55) * (g.px ?? 1.0),
            ud.basePos.y + Math.sin(t * 0.79 + 1.2) * (g.py ?? 0.7),
            ud.basePos.z + Math.sin(t * 0.43 + 2.4) * (g.pz ?? 0.5)
          );
        } else {
          body.group.position.copy(ud.basePos);
        }
        const pos = body.group.position;

        // ① 注视点：向相机位置做指数平滑（这就是「延迟」的来源）
        if (!ud.gazeAim) ud.gazeAim = this.camera.position.clone();
        const kAim = this.reducedMotion ? 1 : 1 - Math.exp(-delta * (g.lagAim ?? 5.0));
        ud.gazeAim.lerp(this.camera.position, kAim);

        // ② 朝向：先求"立刻对准 gazeAim"的四元数，再 slerp 过去（转头惯性）
        m4.lookAt(ud.gazeAim, pos, up);          // +Z → gazeAim
        dq.setFromRotationMatrix(m4);
        if (!ud.gazeQuat) {
          ud.gazeQuat = dq.clone();              // 首帧直接就位，别从单位四元数甩过来
        } else {
          const kTurn = this.reducedMotion ? 1 : 1 - Math.exp(-delta * (g.lagTurn ?? 9.0));
          ud.gazeQuat.slerp(dq, kTurn);
        }

        // ③ 滞后限幅：把 gazeQuat 的 +Z 相对「指向相机」的夹角夹到 maxLagDeg。
        //     旋转量是 **ang - maxLag**（超出多少就转回多少），不是 maxLag ——
        //     写成固定 maxLag 会每帧硬转一个满额角度、直接穿过上限（实测曲线
        //     会先冲到 70° 再掉到 12° 再回弹）。轴取 dirEye×dirCam，绕它正向
        //     旋转即把 dirEye 朝 dirCam 靠；用轴角而不是 nlerp，因为 nlerp 在大
        //     夹角时给的实际角度偏小（90° 时 24° 会变成 20°），限幅就不准了。
        //     premultiply 作用在父空间（content 无变换，即世界空间）。
        dirCam.copy(this.camera.position).sub(pos);
        if (dirCam.lengthSq() > 1e-12) {
          const maxLag = (g.maxLagDeg ?? 24) * Math.PI / 180;
          dirCam.normalize();
          dirEye.set(0, 0, 1).applyQuaternion(ud.gazeQuat);   // 当前 +Z 指向
          const ang = Math.acos(Math.max(-1, Math.min(1, dirEye.dot(dirCam))));
          if (ang > maxLag) {
            axis.crossVectors(dirEye, dirCam);
            if (axis.lengthSq() > 1e-12) {
              ud.gazeQuat.premultiply(cq.setFromAxisAngle(axis.normalize(), ang - maxLag));
            }
          }
        }
        body.group.quaternion.copy(ud.gazeQuat); // 写基准朝向（不含摆动）

        if (!swaying) continue;
        // 摆动叠加在基准朝向之上，且**不写回 gazeQuat** ——
        // 否则摆动会被下一帧的 slerp 当成起点累积，几秒后眼睛就歪掉了。
        body.group.rotateX(Math.sin(t * 0.85) * (g.x ?? 0.07));        // 点头
        body.group.rotateY(Math.sin(t * 0.57 + 1.3) * (g.y ?? 0.105)); // 左右张望
        body.group.rotateZ(Math.sin(t * 0.43 + 2.1) * (g.z ?? 0.04));  // 歪头
      }
    }

    if (this.canAnimate()) {
      const _pulsePt = this._pulsePt || (this._pulsePt = new THREE.Vector3());
      for (const body of this.bodies) {
        if (body.orbit) {
          const orbit = body.orbit; orbit.angle += delta * orbit.speed;
          body.group.position.set(
            Math.cos(orbit.angle) * orbit.distance,
            Math.sin(orbit.angle) * orbit.distance * orbit.tilt + (orbit.yOffset || 0),
            Math.sin(orbit.angle) * orbit.distance
          );
          if (body.data?.isFlagship) {
            body.group.rotation.y = -orbit.angle;
          }
          body.mesh.rotation.y += delta * 0.013;
        }
        if (body.tacticalBerth) {
          const t = time * 0.0005;
          const seed = hash(body.data.id);
          const bobY = Math.sin(t + seed) * 0.25;
          const swayX = Math.cos(t * 0.7 + seed) * 0.15;
          body.group.position.set(
            body.tacticalBerth.baseX + swayX,
            body.tacticalBerth.baseY + bobY,
            body.tacticalBerth.baseZ
          );
          body.group.rotation.y = body.heading || 0;
        }
        if (body.escortAnchor) {
          const fBody = body.escortAnchor;
          const fAngle = (fBody.heading !== undefined && fBody.heading !== null)
            ? fBody.heading
            : (fBody.orbit ? fBody.orbit.angle : (fBody.initialAngle || 0));
          const cosA = Math.cos(fAngle), sinA = Math.sin(fAngle);
          const [dx, dy, dz] = body.escortOffset || [0, 0, 0];
          body.group.position.set(
            fBody.group.position.x + (dx * cosA + dz * sinA),
            fBody.group.position.y + dy,
            fBody.group.position.z + (-dx * sinA + dz * cosA)
          );
          body.group.rotation.y = fAngle;
        }
        if (body.drift) {
          const t = time * 0.0006;
          body.group.position.x = body.drift.baseX + Math.sin(t * 0.8) * 1.8;
          body.group.position.y = body.drift.baseY + Math.cos(t * 0.6) * 1.2;
          body.group.position.z = body.drift.baseZ + Math.sin(t * 0.5 + 1.2) * 1.5;
          body.group.rotation.y += delta * 0.08;
        }
        if (body.data?.kind === 'topic' && !body.data.preview) {
          if (this.hovered !== body && this.selected !== body) {
            body.group.scale.setScalar(1 + Math.sin(time * 0.0012 + hash(body.data.id)) * 0.025);
          }
        }
        if (body.group.userData?.rotatingParts) {
          for (const part of body.group.userData.rotatingParts) {
            part.object.rotation[part.axis] += delta * part.speed;
          }
        }
        if (body.modelInstance) {
          body.modelInstance.rotation.y += delta * 0.12;
          for (const part of body.modelInstance.userData.movingParts || []) {
            part.object.rotation[part.axis] += delta * part.speed;
          }
        }
        if (body.previewParent) {
          const parent = body.previewAnchor;
          if (parent && !parent.isControlled && (parent.orbit || parent.drift)) {
            body.group.position.copy(parent.group.position).add(body.previewVector);
            if (parent.drift) {
              const seed = hash(body.data.id);
              body.group.rotation.y = (body.rotationY || 0) + Math.sin(time * 0.0012 + seed) * 0.06;
              body.group.rotation.x = Math.sin(time * 0.0016 + seed) * 0.08;
              body.group.rotation.z = Math.cos(time * 0.0013 + seed) * 0.06;
            }
          }
        }
      }
      // 实时动态对跟战舰：当战备巡弋微动、泊位浮动或星球位移时，实时更新曲线与端点，光束分秒不离战舰舰体
      if (this.showTractorLines && this.tractorCurves?.length) {
        for (const item of this.tractorCurves) {
          if (!item.fromBody?.group || !item.toBody?.group || !item.lineObj?.geometry) continue;
          const fromPos = item.fromBody.group.position;
          const toPos = item.toBody.group.position;
          const curve = item.curve;
          curve.v0.copy(fromPos);
          curve.v2.copy(toPos);
          const dist = fromPos.distanceTo(toPos);
          curve.v1.copy(fromPos).lerp(toPos, 0.5);
          curve.v1.y += Math.min(dist * 0.22, 5.5);
          curve.v1.z += (Math.sin(dist) * 0.15) * dist * 0.1;

          const pts = curve.getPoints(24);
          const posAttr = item.lineObj.geometry.attributes.position;
          if (posAttr && posAttr.count === pts.length) {
            for (let i = 0; i < pts.length; i++) {
              posAttr.setXYZ(i, pts[i].x, pts[i].y, pts[i].z);
            }
            posAttr.needsUpdate = true;
            item.lineObj.computeLineDistances();
          }
        }
      }
      if (this.showTractorLines && this.tractorMeshes?.length) {
        const tSec = time * 0.001;
        for (const tm of this.tractorMeshes) {
          const isHovered = this.hovered && (this.hovered.data.id === tm.userData.fromId || this.hovered.data.id === tm.userData.toId);
          if (tm.userData.isConflict) {
            const conflictPulse = 0.45 + 0.4 * Math.sin(tSec * 5.0);
            tm.material.opacity = isHovered ? 0.95 : conflictPulse;
          } else {
            // 神经网络多相自主呼吸波：以独立相位和柔和正弦起伏，呈现有机突触生机
            const phase = tm.userData.phase || 0;
            const breathSpeed = tm.userData.breathSpeed || 2.0;
            const wave = 0.5 + 0.5 * Math.sin(tSec * breathSpeed + phase);
            const baseOp = tm.userData.baseOpacity || 0.35;
            const breathOpacity = baseOp * (0.45 + 0.75 * wave);
            tm.material.opacity = isHovered ? Math.max(0.85, breathOpacity * 1.8) : breathOpacity;
          }
        }
      }
      if (this.showTractorLines && this.tractorPulsePoints && this.tractorCurves?.length) {
        const posAttr = this.tractorPulsePoints.geometry.attributes.position;
        const colAttr = this.tractorPulsePoints.geometry.attributes.color;
        const posArray = posAttr.array;
        const colArray = colAttr ? colAttr.array : null;
        let pIdx = 0;
        const tSec = time * 0.001;
        for (const item of this.tractorCurves) {
          const isHovered = this.hovered && (this.hovered.data.id === item.fromId || this.hovered.data.id === item.toId);
          const speed = isHovered ? item.speed * 1.8 : item.speed;
          const baseC = item.baseColor;
          for (let p = 0; p < item.pulseCount; p++) {
            const u = ((tSec * speed + (p / item.pulseCount)) % 1 + 1) % 1;
            item.curve.getPoint(u, _pulsePt);
            posArray[pIdx * 3] = _pulsePt.x;
            posArray[pIdx * 3 + 1] = _pulsePt.y;
            posArray[pIdx * 3 + 2] = _pulsePt.z;

            // 突触动作电位动态调制 (Action Potential Intensity):
            // 粒子在发起端激发出微光，在中段弧顶达到峰值强度，在接近终点受体时柔和融入
            if (colArray && baseC) {
              const actionPotential = Math.sin(u * Math.PI); // 0 at ends, 1 in middle
              const intensity = (0.28 + 1.15 * Math.pow(actionPotential, 1.3)) * (isHovered ? 1.5 : 1.0);
              colArray[pIdx * 3] = Math.min(1.0, baseC.r * intensity);
              colArray[pIdx * 3 + 1] = Math.min(1.0, baseC.g * intensity);
              colArray[pIdx * 3 + 2] = Math.min(1.0, baseC.b * intensity);
            }
            pIdx++;
          }
        }
        posAttr.needsUpdate = true;
        if (colAttr) colAttr.needsUpdate = true;
      }
    }
    const spidersMoving = this.spiders?.tick(time, delta);
    this.stationFlight?.tick(time, delta);
    this.controls.update(); this.renderer.render(this.scene, this.camera); this.updateLabels(); this.frames++;
    if (this.canAnimate() || spidersMoving || this.stationFlight?.active || this.transition || this.interacting || time < this.settleUntil) this.requestFrame();
  }

  get debug() {
    return {ready: this.ready, assetReady: Boolean(this.modelsReady), threeVersion: THREE.REVISION, frames: this.frames, objects: this.pickMeshes.length,
      visibleLabels: this.bodies.filter(b => b.label && !b.label.hidden).length, paused: this.paused, reading: this.reading, reducedMotion: this.reducedMotion,
      active: this.active, animating: this.canAnimate(), camera: this.camera?.position.toArray(), target: this.controls?.target.toArray(),
      bodies: this.bodies.filter(b => !b.decorative).map(b => {
        const p = b.group.position.clone().project(this.camera);
        return {id: b.data.id, kind: b.data.kind, x: (p.x * 0.5 + 0.5) * this.width, y: (-0.5 * p.y + 0.5) * this.height, position: b.group.position.toArray()};
      })};
  }

  dispose() {
    this.spiders?.dispose();
    this.disposed = true; this.stopFrame(); this.listeners.forEach(remove => remove());
    this.resizeObserver?.disconnect(); this.intersectionObserver?.disconnect();
    this.motionQuery?.removeEventListener('change', this.motionChange);
    this.controls?.dispose(); this.clearContent();
    this.stars?.geometry.dispose(); this.stars?.material.dispose();
    this.textures.forEach(texture => texture.dispose()); this.textures.clear();
    this.renderer?.dispose(); this.renderer?.domElement.remove(); this.labels?.remove(); this.ready = false;
  }
}
