// 3D adaptation of the Neural Vault visual reference (see vendor/neural-vault/NOTICE).
import * as THREE from '../vendor/three/three.module.js';

const colors = ['#c4b5fd', '#a78bfa', '#73c8ff', '#5ce0cc', '#f4c069', '#ef9fc9', '#96c3ee', '#c7d898', '#edaa8b'];
export function neuralColor(id) {
  let hash = 0;
  for (const ch of String(id)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}

// Reusable material cache to avoid creating thousands of identical LineBasicMaterials
const MATERIAL_CACHE = new Map();
function getLineMaterial(color, opacity = 0.35) {
  const key = `${color}_${Math.round(opacity * 100) / 100}`;
  if (!MATERIAL_CACHE.has(key)) {
    const mat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity,
      depthWrite: false
    });
    mat.userData.sharedUniverseResource = true;
    MATERIAL_CACHE.set(key, mat);
  }
  return MATERIAL_CACHE.get(key);
}

function line(group, points, color, opacity = 0.35) {
  const geom = new THREE.BufferGeometry().setFromPoints(points);
  const object = new THREE.Line(geom, getLineMaterial(color, opacity));
  group.add(object);
  return object;
}

/**
 * 把 group 的直接子 Line 按材质合并为单个 LineSegments。
 *
 * 动机：每个天体模型原本由 10~30 条独立 Line 拼成（环/肋/触须/磁力线），
 * 每条 Line 每帧各占一次 draw call。实测 373 个 body 合计 1736 条 Line、
 * 每帧 2862 次 draw call，是星图卡顿的主因。合并后视觉不变（顶点全部保留、
 * 只是打包成一批），draw call 降一个数量级。
 *
 * 安全约束：
 *  - 只处理 group 的直接子对象，且必须是 Line（非 LineSegments）、无子对象
 *  - 跳过虚线材质：LineDashedMaterial 依赖 lineDistance 属性，合并会破坏虚线效果
 *  - 不 dispose 标了 sharedUniverseResource 的共享几何体（如 UNIT_JELLY_RINGS）
 *  - 材质沿用原对象（getLineMaterial 返回缓存材质），不新建、不 dispose
 */
export function collapseLines(group) {
  // 先递归处理嵌套 Group（如脉冲星的 magnetic-field 组）：
  // 每个 Group 内部坐标系一致，可以安全地在本层合并，不会破坏层级变换。
  for (const child of [...group.children]) {
    if (child.isGroup) collapseLines(child);
  }

  const buckets = new Map();
  for (const child of [...group.children]) {
    if (!child.isLine || child.isLineSegments || child.children.length) continue;
    const geom = child.geometry;
    if (!geom?.attributes?.position) continue;
    if (child.material?.isLineDashedMaterial) continue;
    const key = child.material?.uuid || 'default';
    if (!buckets.has(key)) buckets.set(key, { material: child.material, lines: [] });
    buckets.get(key).lines.push(child);
  }

  for (const { material, lines } of buckets.values()) {
    if (lines.length < 2) continue;
    const positions = [];
    const v = new THREE.Vector3();
    for (const ln of lines) {
      ln.updateMatrix();
      const pos = ln.geometry.attributes.position;
      const arr = pos.array;
      const n = pos.count;
      const pts = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) {
        v.fromArray(arr, i * 3).applyMatrix4(ln.matrix);
        pts[i * 3] = v.x; pts[i * 3 + 1] = v.y; pts[i * 3 + 2] = v.z;
      }
      for (let i = 0; i < n - 1; i++) {
        positions.push(
          pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2],
          pts[(i + 1) * 3], pts[(i + 1) * 3 + 1], pts[(i + 1) * 3 + 2]
        );
      }
      group.remove(ln);
      if (!ln.geometry.userData?.sharedUniverseResource) ln.geometry.dispose();
    }
    const merged = new THREE.BufferGeometry();
    merged.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const obj = new THREE.LineSegments(merged, material);
    obj.userData.mergedLineCount = lines.length;
    group.add(obj);
  }
}

// 点状虚线：星图节点之间的彩色点状连线（参考视觉：虚线/点线而非实线）
function dashedLine(group, points, color, opacity = 0.4, dashSize = 0.32, gapSize = 0.5) {
  const geom = new THREE.BufferGeometry().setFromPoints(points);
  const mat = new THREE.LineDashedMaterial({
    color,
    transparent: true,
    opacity,
    depthWrite: false,
    dashSize,
    gapSize
  });
  mat.userData.sharedUniverseResource = true;
  const object = new THREE.Line(geom, mat);
  object.computeLineDistances();
  group.add(object);
  return object;
}

// 径向内阴影贴图：中心实心暗 → 中段半透 → 边缘完全透明。
// 纯黑圆盘看起来是一张平贴上去的纸；只有带衰减的暗斑才会被读成「凹进去的洞」。
let SOCKET_TEXTURE = null;
function socketTexture() {
  if (SOCKET_TEXTURE) return SOCKET_TEXTURE;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0.00, 'rgba(2,3,9,1)');
  g.addColorStop(0.50, 'rgba(2,3,9,1)');
  g.addColorStop(0.70, 'rgba(4,6,15,0.62)');
  g.addColorStop(0.87, 'rgba(6,9,22,0.24)');
  g.addColorStop(1.00, 'rgba(6,9,22,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  SOCKET_TEXTURE = new THREE.CanvasTexture(canvas);
  return SOCKET_TEXTURE;
}

// 把一圈点按 z 正负拆成「前半圈 / 后半圈」两条折线，正面用亮色、背面用暗色。
// 这是让线框球读起来「有体积」最有效的手段：单色线框无论怎么画都是一张网，
// 只有把背面压暗，大脑才会把它解释成一个球而不是一个圆。
function depthRing(group, points, frontColor, frontOpacity, backColor, backOpacity) {
  let run = [], runFront = null;
  const flush = () => {
    if (run.length > 1) {
      line(group, run, runFront ? frontColor : backColor, runFront ? frontOpacity : backOpacity);
    }
    run = [];
  };
  for (const p of points) {
    const isFront = p.z >= 0;
    if (runFront === null) runFront = isFront;
    if (isFront !== runFront) {
      if (run.length) { run.push(p); flush(); }   // 交界处补点，两段之间不留缺口
      runFront = isFront;
    }
    run.push(p);
  }
  flush();
}

function halo(group, texture, color, size, opacity) {
  // Purified: 移除无效 2D Billboard 光晕精灵贴图，杜绝模糊模板 AI 感，回归纯粹 3D 几何线框
  const anchor = new THREE.Group();
  group.add(anchor);
  return anchor;
}

// --------------------------------------------------------------------------
// Pre-calculated Unit Geometries (r = 1.0) for Wireframe Jellyfish & Stations
// --------------------------------------------------------------------------
const UNIT_JELLY_RINGS = [];
for (let ring = 1; ring <= 6; ring++) {
  const latitude = (ring / 6) * Math.PI / 2, pts = [];
  for (let j = 0; j <= 56; j++) {
    const a = (j / 56) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * Math.sin(latitude), Math.cos(latitude) * 0.8, Math.sin(a) * Math.sin(latitude)));
  }
  const geom = new THREE.BufferGeometry().setFromPoints(pts);
  geom.userData.sharedUniverseResource = true;
  UNIT_JELLY_RINGS.push(geom);
}

const UNIT_JELLY_RIBS = [];
for (let rib = 0; rib < 14; rib++) {
  const a = (rib / 14) * Math.PI * 2, pts = [];
  for (let j = 0; j <= 22; j++) {
    const v = (j / 22) * Math.PI / 2;
    pts.push(new THREE.Vector3(Math.cos(a) * Math.sin(v), Math.cos(v) * 0.8, Math.sin(a) * Math.sin(v)));
  }
  const geom = new THREE.BufferGeometry().setFromPoints(pts);
  geom.userData.sharedUniverseResource = true;
  UNIT_JELLY_RIBS.push(geom);
}

const UNIT_JELLY_TENTACLES = [];
for (let i = 0; i < 16; i++) {
  const a = (i / 16) * Math.PI * 2, length = 1.5 + 0.6 * Math.sin(i * 1.7), pts = [];
  for (let j = 0; j <= 24; j++) {
    const f = j / 24;
    pts.push(new THREE.Vector3(
      Math.cos(a) * (1 - f * 0.6) + Math.sin(f * 7 + i) * f * 0.19,
      -length * f,
      Math.sin(a) * (1 - f * 0.6) + Math.cos(f * 5 + i) * f * 0.14
    ));
  }
  const geom = new THREE.BufferGeometry().setFromPoints(pts);
  geom.userData.sharedUniverseResource = true;
  UNIT_JELLY_TENTACLES.push(geom);
}

const UNIT_HEART_GEOM = new THREE.SphereGeometry(0.12, 12, 8);
UNIT_HEART_GEOM.userData.sharedUniverseResource = true;
const UNIT_HEART_MAT = new THREE.MeshBasicMaterial({ color: '#f7f0ff' });
UNIT_HEART_MAT.userData.sharedUniverseResource = true;

const UNIT_BEACON_GEOM = new THREE.OctahedronGeometry(0.18, 1);
UNIT_BEACON_GEOM.userData.sharedUniverseResource = true;
const UNIT_BEACON_MAT = new THREE.MeshBasicMaterial({ color: '#ffeacc' });
UNIT_BEACON_MAT.userData.sharedUniverseResource = true;

const UNIT_SENSOR_GEOM = new THREE.TetrahedronGeometry(0.2);
UNIT_SENSOR_GEOM.userData.sharedUniverseResource = true;
const UNIT_SENSOR_MAT = new THREE.MeshBasicMaterial({ color: '#d4f0ff' });
UNIT_SENSOR_MAT.userData.sharedUniverseResource = true;

// 子主题预览节点：小型彩色线框球（比笔记小点更醒目，突出主题层级）
export function makeMiniSphere(group, radius, color, texture) {
  const r = radius;
  const solid = new THREE.Mesh(
    new THREE.SphereGeometry(r * 0.8, 12, 10),
    new THREE.MeshBasicMaterial({ color: 0x090e1c, transparent: true, opacity: 0.6, depthWrite: false })
  );
  solid.userData.sharedUniverseResource = true;
  group.add(solid);
  const wire = new THREE.WireframeGeometry(new THREE.SphereGeometry(r, 12, 10));
  const wireMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85, depthWrite: false });
  wireMat.userData.sharedUniverseResource = true;
  const lines = new THREE.LineSegments(wire, wireMat);
  lines.userData.sharedUniverseResource = true;
  group.add(lines);
  const ringPts = [];
  for (let i = 0; i <= 40; i++) {
    const a = (i / 40) * Math.PI * 2;
    ringPts.push(new THREE.Vector3(Math.cos(a) * r * 1.35, Math.sin(a) * r * 1.35, 0));
  }
  const ringObj = new THREE.Line(new THREE.BufferGeometry().setFromPoints(ringPts), getLineMaterial(color, 0.3));
  ringObj.userData.sharedUniverseResource = true;
  group.add(ringObj);
  halo(group, texture, color, r * 3.6, 0.35);
}

export function makeJelly(group, radius, color, texture) {
  const r = radius * 1.1;

  // 1. Dark core & Wireframe dome
  const solid = new THREE.Mesh(
    new THREE.SphereGeometry(r * 0.82, 18, 14),
    new THREE.MeshBasicMaterial({ color: 0x090e1c, transparent: true, opacity: 0.45, depthWrite: false })
  );
  group.add(solid);

  const wire = new THREE.WireframeGeometry(new THREE.SphereGeometry(r, 18, 14));
  const wireMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35, depthWrite: false });
  wireMat.userData.sharedUniverseResource = true;
  group.add(new THREE.LineSegments(wire, wireMat));

  // 2. Pre-calculated rings (6 latitude rings)
  const lineMat = getLineMaterial(color, 0.30);
  for (const geom of UNIT_JELLY_RINGS) {
    const lineObj = new THREE.Line(geom, lineMat);
    lineObj.scale.setScalar(r);
    group.add(lineObj);
  }

  // 3. Longitude ribs (10 ribs)
  for (let rib = 0; rib < 10; rib++) {
    const a = (rib / 10) * Math.PI * 2, pts = [];
    for (let j = 0; j <= 18; j++) {
      const v = (j / 18) * Math.PI / 2;
      pts.push(new THREE.Vector3(Math.cos(a) * Math.sin(v) * r, Math.cos(v) * 0.8 * r, Math.sin(a) * Math.sin(v) * r));
    }
    line(group, pts, color, 0.25);
  }

  // 4. Undulating tentacles (12 tentacles)
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    const len = r * (1.4 + (i % 3) * 0.4);
    const pts = [];
    for (let j = 0; j <= 16; j++) {
      const f = j / 16;
      pts.push(new THREE.Vector3(
        Math.cos(a) * r * (1 - f * 0.5) + Math.sin(f * 6 + i) * r * 0.18,
        -f * len,
        Math.sin(a) * r * (1 - f * 0.5) + Math.cos(f * 5 + i) * r * 0.15
      ));
    }
    line(group, pts, color, 0.30);
  }

  // 5. Ambient ether particles
  const dots = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2, rr = r * 1.45;
    dots.push(Math.cos(a) * rr, Math.sin(a) * rr * 0.6, Math.sin(a) * rr * 0.45);
  }
  const dotGeom = new THREE.BufferGeometry();
  dotGeom.setAttribute('position', new THREE.Float32BufferAttribute(dots, 3));
  const dotMat = new THREE.PointsMaterial({ color, size: 0.12, transparent: true, opacity: 0.65, depthWrite: false, blending: THREE.AdditiveBlending });
  group.add(new THREE.Points(dotGeom, dotMat));
}

export function makePulsar(group, radius, color, texture, seed = 0) {
  const r = radius * 1.1;

  // 1. Soft radiant core & translucent corona
  const coreMat = new THREE.MeshBasicMaterial({ color: 0xfffbeb, transparent: true, opacity: 0.92 });
  const coreMesh = new THREE.Mesh(new THREE.SphereGeometry(r * 0.38, 16, 12), coreMat);
  group.add(coreMesh);

  const coronaMesh = new THREE.Mesh(
    new THREE.SphereGeometry(r * 0.68, 16, 12),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.20, depthWrite: false })
  );
  group.add(coronaMesh);

  // 2. Relativistic polar jet beams (softened pearl cyan)
  const jetLength = r * 3.2;
  const topJet = [new THREE.Vector3(0, r * 0.2, 0), new THREE.Vector3(0, jetLength, 0)];
  const bottomJet = [new THREE.Vector3(0, -r * 0.2, 0), new THREE.Vector3(0, -jetLength, 0)];
  line(group, topJet, '#e0f2fe', 0.65);
  line(group, bottomJet, '#e0f2fe', 0.65);

  // Collimation flare rings along jet (softened cyan)
  for (const dir of [1, -1]) {
    for (const h of [0.7, 1.4, 2.1, 2.8]) {
      const ringPts = [];
      const ringR = r * (0.16 + h * 0.07);
      for (let i = 0; i <= 24; i++) {
        const a = (i / 24) * Math.PI * 2;
        ringPts.push(new THREE.Vector3(Math.cos(a) * ringR, dir * r * h, Math.sin(a) * ringR));
      }
      line(group, ringPts, '#7dd3fc', 0.38);
    }
  }

  // 3. Curved dipolar magnetic field lines (attenuated helical loops)
  const magRings = new THREE.Group();
  magRings.name = 'magnetic-field';
  for (let m = 0; m < 6; m++) {
    const rotY = (m / 6) * Math.PI;
    const loopPts = [];
    for (let i = 0; i <= 36; i++) {
      const t = (i / 36) * Math.PI * 2;
      const rx = Math.sin(t) * r * 1.65;
      const ry = Math.cos(t) * r * 2.2;
      const rz = Math.sin(t * 2) * r * 0.45;
      loopPts.push(new THREE.Vector3(
        rx * Math.cos(rotY) - rz * Math.sin(rotY),
        ry,
        rx * Math.sin(rotY) + rz * Math.cos(rotY)
      ));
    }
    line(magRings, loopPts, color, 0.32);
  }
  group.add(magRings);

  // Equatorial magnetic containment arcs
  for (let k = 0; k < 12; k++) {
    const a = (k / 12) * Math.PI * 2;
    const pStart = new THREE.Vector3(Math.cos(a) * r * 0.72, 0, Math.sin(a) * r * 0.72);
    const pMid = new THREE.Vector3(Math.cos(a + 0.25) * r * 1.6, (k % 2 === 0 ? 0.35 : -0.35) * r, Math.sin(a + 0.25) * r * 1.6);
    const pEnd = new THREE.Vector3(Math.cos(a + 0.5) * r * 0.72, 0, Math.sin(a + 0.5) * r * 0.72);
    line(group, [pStart, pMid, pEnd], color, 0.28);
  }

  // 4. Equatorial synchrotron particle ring
  const synPts = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2, sr = r * 1.5;
    synPts.push(Math.cos(a) * sr, Math.sin(a * 3) * 0.1 * r, Math.sin(a) * sr);
  }
  const synGeom = new THREE.BufferGeometry();
  synGeom.setAttribute('position', new THREE.Float32BufferAttribute(synPts, 3));
  const synMat = new THREE.PointsMaterial({ color: '#c4b5fd', size: 0.12, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending });
  group.add(new THREE.Points(synGeom, synMat));

  group.userData.rotatingParts = [{ object: magRings, axis: 'y', speed: 0.85 }];
}

export function makeRingworld(group, radius, color, texture, seed = 0) {
  const r = radius * 1.1;

  // 1. Central warm star with soft photosphere
  const starMesh = new THREE.Mesh(
    new THREE.SphereGeometry(r * 0.55, 18, 14),
    new THREE.MeshBasicMaterial({ color: 0xfef3c7, transparent: true, opacity: 0.92 })
  );
  group.add(starMesh);

  const photosphere = new THREE.Mesh(
    new THREE.SphereGeometry(r * 0.76, 18, 14),
    new THREE.MeshBasicMaterial({ color: 0xfde047, transparent: true, opacity: 0.16, depthWrite: false })
  );
  group.add(photosphere);

  // 2. Inner mechanical engineering ring
  const innerRing = new THREE.Group();
  innerRing.name = 'inner-ring';
  const rInner = r * 1.55;
  const rInner2 = r * 1.75;
  const innerPts1 = [], innerPts2 = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    innerPts1.push(new THREE.Vector3(Math.cos(a) * rInner, 0, Math.sin(a) * rInner));
    innerPts2.push(new THREE.Vector3(Math.cos(a) * rInner2, 0, Math.sin(a) * rInner2));
  }
  line(innerRing, innerPts1, color, 0.48);
  line(innerRing, innerPts2, color, 0.48);

  // Cross-trusses & Solar arrays (attenuated)
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    const p1 = new THREE.Vector3(Math.cos(a) * rInner, 0, Math.sin(a) * rInner);
    const p2 = new THREE.Vector3(Math.cos(a) * rInner2, (i % 2 === 0 ? 0.12 : -0.12) * r, Math.sin(a) * rInner2);
    line(innerRing, [p1, p2], color, 0.28);
  }
  group.add(innerRing);

  // 3. Outer counter-rotating tilted ring
  const outerRing = new THREE.Group();
  outerRing.name = 'outer-ring';
  outerRing.rotation.x = 0.45;
  outerRing.rotation.z = 0.25;
  const rOuter = r * 2.25;
  const outerPts = [];
  for (let i = 0; i <= 56; i++) {
    const a = (i / 56) * Math.PI * 2;
    outerPts.push(new THREE.Vector3(Math.cos(a) * rOuter, Math.sin(a * 4) * 0.08 * r, Math.sin(a) * rOuter));
  }
  line(outerRing, outerPts, '#38bdf8', 0.42);

  // Outer orbital hab nodes
  const nodePts = [];
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    nodePts.push(Math.cos(a) * rOuter, 0, Math.sin(a) * rOuter);
  }
  const nodeGeom = new THREE.BufferGeometry();
  nodeGeom.setAttribute('position', new THREE.Float32BufferAttribute(nodePts, 3));
  outerRing.add(new THREE.Points(nodeGeom, new THREE.PointsMaterial({ color: '#fcd34d', size: 0.18, transparent: true, opacity: 0.70 })));
  group.add(outerRing);

  // 4. Structural support pylons (refined to 8 curved trusses to remove dense spoke moire)
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    const pStart = new THREE.Vector3(Math.cos(a) * r * 0.6, 0, Math.sin(a) * r * 0.6);
    const pEnd = new THREE.Vector3(Math.cos(a) * rInner, (i % 2 === 0 ? 0.15 : -0.15) * r, Math.sin(a) * rInner);
    line(group, [pStart, pEnd], color, 0.28);
  }

  // 4 orbital tether anchors
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + 0.2;
    const p1 = new THREE.Vector3(Math.cos(a) * rInner2, 0, Math.sin(a) * rInner2);
    const p2 = new THREE.Vector3(Math.cos(a) * rOuter, 0, Math.sin(a) * rOuter);
    line(group, [p1, p2], '#38bdf8', 0.25);
  }

  group.userData.rotatingParts = [
    { object: innerRing, axis: 'y', speed: 0.35 },
    { object: outerRing, axis: 'y', speed: -0.22 }
  ];
}

export function makeAccretionDisk(group, radius, color, texture, seed = 0) {
  const r = radius * 1.1;

  // 1. Pitch-black Event Horizon (Absolute Singularity)
  const horizonMat = new THREE.MeshBasicMaterial({ color: 0x010204, depthWrite: true });
  const horizonMesh = new THREE.Mesh(new THREE.SphereGeometry(r * 0.58, 20, 16), horizonMat);
  group.add(horizonMesh);

  // 2. Relativistic Photon Ring (softened champagne aura)
  const photonRingPts = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    photonRingPts.push(new THREE.Vector3(Math.cos(a) * r * 0.64, 0, Math.sin(a) * r * 0.64));
  }
  line(group, photonRingPts, '#fef08a', 0.58);

  const photonOuterPts = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    photonOuterPts.push(new THREE.Vector3(Math.cos(a) * r * 0.68, 0, Math.sin(a) * r * 0.68));
  }
  line(group, photonOuterPts, '#fef08a', 0.22);

  // 3. Tilted broad swirling Accretion Disk
  const diskGroup = new THREE.Group();
  diskGroup.name = 'accretion-disk';
  diskGroup.rotation.x = 0.62;
  diskGroup.rotation.y = 0.25;

  for (let ring = 1; ring <= 4; ring++) {
    const rDisk = r * (0.85 + ring * 0.38);
    const ringPts = [];
    for (let i = 0; i <= 56; i++) {
      const a = (i / 56) * Math.PI * 2;
      ringPts.push(new THREE.Vector3(Math.cos(a) * rDisk, Math.sin(a * 2) * 0.05 * r, Math.sin(a) * rDisk));
    }
    line(diskGroup, ringPts, ring % 2 === 0 ? color : '#f43f5e', 0.48 - ring * 0.08);
  }

  // Accretion particle stream
  const diskParticles = [];
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * Math.PI * 2 + (i % 3) * 0.2;
    const pDist = r * (0.9 + (i % 7) * 0.22);
    diskParticles.push(Math.cos(a) * pDist, (Math.sin(a * 5) * 0.04) * r, Math.sin(a) * pDist);
  }
  const partGeom = new THREE.BufferGeometry();
  partGeom.setAttribute('position', new THREE.Float32BufferAttribute(diskParticles, 3));
  const partMat = new THREE.PointsMaterial({ color: '#f59e0b', size: 0.12, transparent: true, opacity: 0.65, blending: THREE.AdditiveBlending });
  diskGroup.add(new THREE.Points(partGeom, partMat));
  group.add(diskGroup);

  // 4. Infalling gravitational spiral streams (refined to 8 smooth spiral trails)
  for (let s = 0; s < 8; s++) {
    const startA = (s / 8) * Math.PI * 2;
    const streamPts = [];
    for (let step = 0; step <= 12; step++) {
      const t = step / 12;
      const curA = startA + t * 2.2;
      const curR = r * (2.2 - t * 1.5);
      streamPts.push(new THREE.Vector3(Math.cos(curA) * curR, (Math.sin(curA * 3) * 0.08 - t * 0.2) * r, Math.sin(curA) * curR));
    }
    line(group, streamPts, s % 2 === 0 ? color : '#f59e0b', 0.28);
  }

  // 5. Gravitational Lensing Curved Light Arcs
  for (const dir of [1, -1]) {
    const lensPts = [];
    for (let i = -16; i <= 16; i++) {
      const t = i / 16;
      const lx = t * r * 1.5;
      const ly = dir * Math.sqrt(Math.max(0, 1 - t * t)) * r * 1.25;
      lensPts.push(new THREE.Vector3(lx, ly, -r * 0.1));
    }
    line(group, lensPts, '#fbbf24', 0.48);
  }

  // 6. Polar frame boundary arcs
  for (const dir of [1, -1]) {
    const arcPts = [];
    for (let i = 0; i <= 24; i++) {
      const a = (i / 24) * Math.PI;
      arcPts.push(new THREE.Vector3(0, Math.cos(a) * r * 1.8, dir * Math.sin(a) * r * 1.8));
    }
    line(group, arcPts, '#6366f1', 0.20);
  }

  group.userData.rotatingParts = [
    { object: diskGroup, axis: 'z', speed: 0.65 }
  ];
}

export function makeCore(group, radius, texture) {
  const geometry = new THREE.IcosahedronGeometry(radius, 2);
  group.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x0c0c10, transparent: true, opacity: 0.75, depthWrite: false })));
  const wire = new THREE.WireframeGeometry(geometry);
  group.add(new THREE.LineSegments(wire, new THREE.LineBasicMaterial({ color: 0xd8b45a, transparent: true, opacity: 0.45 })));
  const positions = wire.getAttribute('position'), hot = [];
  for (let i = 0; i < positions.count; i += 2) {
    if ((i * 17) % 41 < 3) {
      for (let j = i; j < i + 2; j++) hot.push(positions.getX(j), positions.getY(j), positions.getZ(j));
    }
  }
  const geometryHot = new THREE.BufferGeometry();
  geometryHot.setAttribute('position', new THREE.Float32BufferAttribute(hot, 3));
  group.add(new THREE.LineSegments(geometryHot, new THREE.LineBasicMaterial({ color: 0xffd07a, transparent: true, opacity: 1, toneMapped: false, blending: THREE.AdditiveBlending })));
  const embers = [];
  for (let i = 0; i < 180; i++) {
    const a = i * 2.39996, r = radius * 0.55 * Math.sqrt((i + 0.5) / 180);
    embers.push(Math.cos(a) * r, Math.sin(a) * r, Math.sin(i * 7.3) * radius * 0.3 + radius * 0.6);
  }
  const particles = new THREE.BufferGeometry();
  particles.setAttribute('position', new THREE.Float32BufferAttribute(embers, 3));
  group.add(new THREE.Points(particles, new THREE.PointsMaterial({ color: 0xeaaa58, size: 0.06, transparent: true, opacity: 0.8, depthWrite: false, blending: THREE.AdditiveBlending })));
  halo(group, texture, 0xe5ab65, radius * 3, 0.32).position.z = radius * 0.7;
  halo(group, texture, 0xffa34f, radius * 1.3, 0.5).position.z = radius * 0.8;
  for (const r of [1.18, 1.5]) {
    const points = [];
    for (let i = 0; i <= 100; i++) {
      const a = (i / 100) * Math.PI * 2;
      points.push(new THREE.Vector3(Math.cos(a) * radius * r, Math.sin(a) * radius * r, 0));
    }
    line(group, points, 0x90909c, 0.14);
  }
}

/**
 * --------------------------------------------------------------------------
 * 科技之眼 (Holographic Tech Eye) —— 「我的空间」聚类视图的中心核心
 * --------------------------------------------------------------------------
 * 设计约束：不使用任何大体积不透明实体（旧版的纯白高光球 + 黑色瞳孔球会
 * 凸出成两个贴在一起的球）。全部由线框 / 粒子 / 光晕构成，任意机位可读。
 * 结构：全息经纬外壳 → 机械光圈虹膜（同心环 + 叶片 + 径向粒子）
 *       → 深空瞳孔（暗核 + 双环 + 发光数据核心）→ 刻度环 + 三段扫描弧
 */
export function makeEye(group, radius, texture) {
  const r = radius;
  const TAU = Math.PI * 2;

  // ── 0. 眼球曲面 ────────────────────────────────────────────────────
  // 这只眼睛永远正对镜头（billboard，见本函数末尾 faceCamera），
  // 于是**透视不再提供任何纵深线索** —— 立体感必须做进模型本身。
  // 做法：以半径 RS 的球面作为「角膜面」基准，把正面极点前推到 z0，
  // 虹膜 / 瞳孔 / 刻度环 / 扫描弧全部贴合这个曲面：
  // 从正前方看，越靠外圈的结构越往后收（中心 0.52r → 外圈 0.05r），
  // 再配合球壳的「正面亮 / 背面暗」，眼睛就立起来了。
  const RS = r * 1.18;
  const z0 = r * 0.52;
  const domeZ = rr => Math.sqrt(Math.max(0, RS * RS - rr * rr)) - (RS - z0);

  // ── 1. 全息外壳：经纬线框球 + 深度明暗（正面亮 / 背面暗）+ 壳层微尘 ──
  const shell = new THREE.Group();
  for (let i = 1; i <= 7; i++) {
    const phi = (i / 8) * Math.PI;
    const ringR = r * Math.sin(phi), y = r * Math.cos(phi) * 0.94, pts = [];
    for (let j = 0; j <= 72; j++) { const a = (j / 72) * TAU; pts.push(new THREE.Vector3(Math.cos(a) * ringR, y, Math.sin(a) * ringR)); }
    depthRing(shell, pts, 0x7fd8ff, 0.38, 0x3f6fa8, 0.10);
  }
  for (let i = 0; i < 18; i++) {
    const a = (i / 18) * TAU, pts = [];
    for (let j = 0; j <= 56; j++) {
      const b = (j / 56) * TAU;
      pts.push(new THREE.Vector3(Math.cos(a) * Math.sin(b) * r, Math.cos(b) * r * 0.94, Math.sin(a) * Math.sin(b) * r));
    }
    depthRing(shell, pts, 0x9aa2ff, 0.30, 0x4a4f8a, 0.08);
  }
  const dust = [];
  for (let i = 0; i < 360; i++) {
    const a = Math.acos(2 * ((i * 0.6180339887) % 1) - 1), b = i * 2.3999632;
    const rr = r * (0.95 + 0.05 * Math.sin(i * 5.1));
    dust.push(Math.cos(b) * Math.sin(a) * rr, Math.cos(a) * rr * 0.94, Math.sin(b) * Math.sin(a) * rr);
  }
  const dustGeom = new THREE.BufferGeometry();
  dustGeom.setAttribute('position', new THREE.Float32BufferAttribute(dust, 3));
  shell.add(new THREE.Points(dustGeom, new THREE.PointsMaterial({
    color: 0xa8dcff, size: r * 0.013, transparent: true, opacity: 0.32,
    depthWrite: false, blending: THREE.AdditiveBlending
  })));
  group.add(shell);

  // ── 2. 虹膜：贴合角膜曲面的径向粒子 + 同心环 + 机械光圈叶片 ──────────
  // 粒子是虹膜的“实体”，环与叶片只是结构线，故粒子密度/亮度要压过线框。
  // 全部按 domeZ(rr) 落到曲面上（而不是平铺在一个 z 上），这是立体感的主要来源。
  const iris = new THREE.Group();
  const irisPos = [], irisCol = [];
  const irisPalette = [
    new THREE.Color(0x22d3ee), new THREE.Color(0x38bdf8), new THREE.Color(0x818cf8),
    new THREE.Color(0xa78bfa), new THREE.Color(0x34d399), new THREE.Color(0xfbbf24)
  ];
  const IRIS_OUTER = r * 0.66;
  for (let i = 0; i < 1800; i++) {
    const band = (i % 45) / 45;                     // 径向分层
    // 半径 / 角度各自加一点确定性抖动：不加的话粒子会排成规则的
    // 「同心环 × 放射辐条」点阵，看起来像靶纸，反而更平。
    const jr = (((i * 2654435761) >>> 0) % 1000) / 1000 - 0.5;
    const ja = (((i * 40503) >>> 0) % 1000) / 1000 - 0.5;
    const rr = r * (0.24 + 0.42 * Math.sqrt(Math.max(0, band + jr * 0.055)));
    const a = (i / 1800) * TAU * 5.0 + band * 2.2 + ja * 0.42;
    irisPos.push(Math.cos(a) * rr, Math.sin(a) * rr, domeZ(rr));
    const c = irisPalette[(i * 7) % irisPalette.length];
    const shade = 1 - 0.5 * (rr / IRIS_OUTER);      // 外圈压暗，进一步强化球面
    irisCol.push(c.r * shade, c.g * shade, c.b * shade);
  }
  const irisGeom = new THREE.BufferGeometry();
  irisGeom.setAttribute('position', new THREE.Float32BufferAttribute(irisPos, 3));
  irisGeom.setAttribute('color', new THREE.Float32BufferAttribute(irisCol, 3));
  iris.add(new THREE.Points(irisGeom, new THREE.PointsMaterial({
    // 用径向渐变贴图把默认的方形点变成柔和圆点 —— 近距离特写时差别很明显。
    map: texture, size: r * 0.030, vertexColors: true, transparent: true, opacity: 0.95,
    depthWrite: false, blending: THREE.AdditiveBlending
  })));
  for (const [f, color, opacity] of [
    [0.26, 0x22d3ee, 0.75], [0.40, 0x60a5fa, 0.6], [0.52, 0x8b5cf6, 0.55],
    [0.60, 0xa78bfa, 0.45], [0.66, 0xffd07a, 0.5]
  ]) {
    const pts = [];
    for (let j = 0; j <= 96; j++) {
      const a = (j / 96) * TAU;
      pts.push(new THREE.Vector3(Math.cos(a) * r * f, Math.sin(a) * r * f, domeZ(r * f)));
    }
    line(iris, pts, color, opacity);
  }
  for (let i = 0; i < 28; i++) {           // 光圈叶片：长短/配色交替，像机械虹膜
    const a = (i / 28) * TAU, long = i % 2 === 0;
    const inner = r * (long ? 0.30 : 0.38), outer = r * (long ? 0.62 : 0.52);
    // 分段贴合角膜曲面（而不是拉一条直弦）—— 叶片外端会明显往后收，
    // 这是从正前方看也能察觉到的曲面线索。
    const pts = [];
    for (let k = 0; k <= 4; k++) {
      const rr = inner + (outer - inner) * (k / 4);
      pts.push(new THREE.Vector3(Math.cos(a) * rr, Math.sin(a) * rr, domeZ(rr)));
    }
    line(iris, pts, i % 3 === 0 ? 0xffd07a : 0x9fe8ff, i % 3 === 0 ? 0.6 : 0.28);
  }
  group.add(iris);

  // ── 3. 瞳孔：相对虹膜面**下沉**形成眼窝 + 悬浮数据核心 ──────────────
  // 瞳孔沉下去、核心浮上来，一前一后拉开 ~0.5r 的深度差，眼窝就有了纵深。
  const pupil = new THREE.Group();
  const pupilR = r * 0.20;                     // 真实瞳孔没这么大，缩小才像眼睛
  const socketZ = domeZ(pupilR);
  // 眼窝内阴影：中心实心暗 → 外圈渐隐。压在虹膜内侧，制造「往里凹」的过渡。
  const socket = new THREE.Mesh(
    new THREE.CircleGeometry(r * 0.38, 56),
    new THREE.MeshBasicMaterial({
      map: socketTexture(), transparent: true, depthWrite: false, toneMapped: false
    })
  );
  socket.position.z = socketZ - r * 0.10;      // 沉在角膜面之后
  pupil.add(socket);
  // 实心黑瞳：压在眼窝最深处
  const pupilVoid = new THREE.Mesh(new THREE.SphereGeometry(pupilR, 24, 16),
    new THREE.MeshBasicMaterial({ color: 0x02030a, toneMapped: false }));
  pupilVoid.scale.z = 0.72;
  pupilVoid.position.z = socketZ - r * 0.20;
  pupil.add(pupilVoid);
  // 眼窝口：贴着角膜面的一圈亮环，勾出「洞」的边
  for (const [f, color, opacity] of [[0.20, 0x8b5cf6, 0.9], [0.29, 0x22d3ee, 0.45]]) {
    const pts = [];
    for (let j = 0; j <= 80; j++) {
      const a = (j / 80) * TAU;
      pts.push(new THREE.Vector3(Math.cos(a) * r * f, Math.sin(a) * r * f, domeZ(r * f)));
    }
    line(pupil, pts, color, opacity);
  }
  const coreZ = z0 + r * 0.22;                 // 悬浮在角膜面之前
  const core = new THREE.Mesh(UNIT_BEACON_GEOM, new THREE.MeshBasicMaterial({ color: 0xd6f7ff, toneMapped: false }));
  core.scale.setScalar(r * 0.40);
  core.position.z = coreZ;
  pupil.add(core);
  halo(pupil, texture, 0xcdefff, r * 0.95, 0.5).position.z = coreZ;
  halo(pupil, texture, 0x8b8cf6, r * 1.5, 0.22).position.z = coreZ;
  group.add(pupil);

  // ── 4. 科技装饰：刻度环 + 三段扫描弧（同样贴合角膜曲面）─────────────
  const tickRing = new THREE.Group();
  for (let i = 0; i < 48; i++) {
    const a = (i / 48) * TAU, long = i % 4 === 0;
    const r0 = r * 0.70, r1 = r * (long ? 0.80 : 0.75);
    line(tickRing, [
      new THREE.Vector3(Math.cos(a) * r0, Math.sin(a) * r0, domeZ(r0)),
      new THREE.Vector3(Math.cos(a) * r1, Math.sin(a) * r1, domeZ(r1))
    ], long ? 0xffd07a : 0x5cc8ff, long ? 0.75 : 0.32);
  }
  {
    const pts = [];
    for (let j = 0; j <= 128; j++) {
      const a = (j / 128) * TAU;
      pts.push(new THREE.Vector3(Math.cos(a) * r * 0.84, Math.sin(a) * r * 0.84, domeZ(r * 0.84)));
    }
    line(tickRing, pts, 0x8b8cf6, 0.3);
  }
  group.add(tickRing);

  const scan = new THREE.Group();
  for (let k = 0; k < 3; k++) {
    const pts = [], start = k * (TAU / 3);
    for (let j = 0; j <= 26; j++) {
      const a = start + (j / 26) * 0.85, rr = r * 0.68;
      pts.push(new THREE.Vector3(Math.cos(a) * rr, Math.sin(a) * rr, domeZ(rr) + r * 0.03));
    }
    line(scan, pts, k === 0 ? 0x9ff5ff : 0xa78bfa, k === 0 ? 1 : 0.5);
  }
  group.add(scan);

  // ── 4b. 角膜高光：贴在曲面左上的一小块镜面反光 ─────────────────────
  // 眼睛「湿润 / 有体积」最直接的一条线索。挂在 group（不随虹膜自转），
  // 位置固定在左上，像有一盏固定的灯打在角膜上。
  {
    const glare = new THREE.Group();
    const ga = Math.PI * 0.78, gd = r * 0.42;      // 方位：左上
    const gx = Math.cos(ga) * gd, gy = Math.sin(ga) * gd;
    halo(glare, texture, 0xffffff, r * 0.52, 0.6).position.set(gx, gy, domeZ(gd) + r * 0.06);
    halo(glare, texture, 0xd8f4ff, r * 0.24, 0.5).position.set(gx * 0.40, gy * 0.40, domeZ(gd * 0.40) + r * 0.06);
    // 对侧一点冷色补光，避免高光孤零零地浮着
    halo(glare, texture, 0x7fd4ff, r * 0.34, 0.22).position.set(-gx * 0.85, -gy * 0.85, domeZ(gd * 0.85) + r * 0.04);
    group.add(glare);
  }

  // ── 5. 外环光晕（与星图氛围一致）────────────────────────────────────
  // 半径刻意收在 2.2r 以内：核心半径调大后，3.4r 的光晕会糊掉周围分区。
  halo(group, texture, 0x8b8cf6, r * 2.2, 0.28);
  halo(group, texture, 0xffd07a, r * 1.25, 0.3);
  halo(group, texture, 0x4cc3ff, r * 1.7, 0.32);

  // ── 6. 自转：虹膜缓转、扫描弧反向快扫、刻度环极慢、外壳微转 ─────────
  // 外壳必须绕 **z**（视轴）转，不能绕 y：球壳的「正面亮 / 背面暗」是烘焙在
  // 几何上的深度线索，绕 y 转会让亮面转到背面去，看起来像光源在跟着转。
  // 绕视轴转则正/背面不变，线索始终成立。
  group.userData.rotatingParts = [
    { object: iris, axis: 'z', speed: 0.22 },
    { object: scan, axis: 'z', speed: -0.85 },
    { object: tickRing, axis: 'z', speed: 0.12 },
    { object: shell, axis: 'z', speed: 0.035 }
  ];

  // ── 7. 注视行为：整只眼延迟追踪镜头 + 轻微摆动 ──────────────────────
  // 眼睛的所有结构（虹膜 / 瞳孔 / 刻度环 / 扫描弧）都建在 +Z 面，
  // 所以只要让 group 的 +Z 指向相机，眼睛就永远「看着你」。
  // 每帧的朝向由 universe-scene.js 的 frame() 处理（见 faceCamera 分支）：
  // 两级指数阻尼（注视点平滑 + 朝向 slerp）跟随相机，再叠加三轴低频摆动，
  // 像在注视中微微张望。**不是硬 lookAt** —— 那样拖镜头时眼睛会焊死在相机上。
  group.userData.faceCamera = true;
  // 摆动 / 追踪参数：
  //   px / py / pz —— 位置浮动幅度（世界单位）。billboard 下这是**主要可见动效**：
  //     整只眼正对镜头时，纯旋转只是把圆盘略微倾斜（6° 仅让圆环压缩 cos6°≈0.5%、
  //     瞳孔偏移 ~2px），几乎看不出来；位置浮动才有明显的「悬停摆动」感。
  //   x / y / z —— 三轴旋转幅度（弧度），频率与位置错开，让动作不机械。
  //   lagAim / lagTurn —— 延迟追踪速率（1/秒，越大越跟手）：
  //     时间常数 τ = 1/rate，所以 lagAim 5.0 ≈ 0.20s、lagTurn 9.0 ≈ 0.11s，
  //     合计约 0.3s 的滞后。太小会显得呆滞迟钝，太大会退回"焊死"的生硬感。
  //   maxLagDeg —— 滞后上限（度），约束的是**最终可见朝向**（不是中间量）。
  //     指数平滑的滞后量正比于镜头角速度：常速拖拽实测中位 ~14°，很自然；
  //     但快速甩镜头会冲到 50°+，眼睛像"跟丢了"。这里封顶 24°：
  //     常速拖拽够不到上限、手感不变，只有甩动时才限幅。
  group.userData.gazeSway = {
    px: 1.00, py: 0.70, pz: 0.50, x: 0.070, y: 0.105, z: 0.040,
    lagAim: 5.0, lagTurn: 9.0, maxLagDeg: 24
  };
}

export function makeSkillStation(group, radius, color, texture, modelInstance) {
  const r = radius * 1.15;
  if (modelInstance) group.add(modelInstance);

  // 1. 中央指挥主塔与通信桅杆 (Central Spire & Comms Hub)
  const keelGeom = new THREE.CylinderGeometry(r * 0.12, r * 0.18, r * 2.2, 16);
  const keelMat = new THREE.MeshBasicMaterial({ color: 0x111622, transparent: true, opacity: 0.85, depthWrite: false });
  group.add(new THREE.Mesh(keelGeom, keelMat));

  // 主塔加固环与观景穹顶环
  for (const y of [-0.8, -0.4, 0, 0.4, 0.8]) {
    const ringPts = [];
    const rKeel = r * (0.15 + Math.abs(y) * 0.04);
    for (let j = 0; j <= 24; j++) {
      const a = (j / 24) * Math.PI * 2;
      ringPts.push(new THREE.Vector3(Math.cos(a) * rKeel, y * r, Math.sin(a) * rKeel));
    }
    line(group, ringPts, '#f4c069', 0.55);
  }

  // 通信桅杆天线与微型信标
  const antennaPts = [new THREE.Vector3(0, r * 1.1, 0), new THREE.Vector3(0, r * 1.65, 0)];
  line(group, antennaPts, '#f4c069', 0.85);
  const beaconGeom = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, r * 1.65, 0)]);
  group.add(new THREE.Points(beaconGeom, new THREE.PointsMaterial({ color: '#f4c069', size: 0.22, transparent: true, opacity: 0.95 })));

  // 2. 自转双层栖居环与泊位舱段 (Counter-Rotating Habitat Ring & Berths)
  const habitatGroup = new THREE.Group();
  habitatGroup.name = 'station-habitat-ring';

  // 2.1 内侧工程轨道环 (Inner Engineering Torus)
  const rInner = r * 0.85;
  const innerRingPts = [];
  for (let j = 0; j <= 48; j++) {
    const a = (j / 48) * Math.PI * 2;
    innerRingPts.push(new THREE.Vector3(Math.cos(a) * rInner, 0, Math.sin(a) * rInner));
  }
  line(habitatGroup, innerRingPts, '#f4c069', 0.45);

  // 2.2 外侧主栖居环带 (Outer Habitation Ring) - 双同心环
  const rOuter = r * 1.55;
  const rOuter2 = r * 1.75;
  const outerRingPts1 = [], outerRingPts2 = [];
  for (let j = 0; j <= 56; j++) {
    const a = (j / 56) * Math.PI * 2;
    outerRingPts1.push(new THREE.Vector3(Math.cos(a) * rOuter, -r * 0.06, Math.sin(a) * rOuter));
    outerRingPts2.push(new THREE.Vector3(Math.cos(a) * rOuter2, r * 0.06, Math.sin(a) * rOuter2));
  }
  line(habitatGroup, outerRingPts1, color || '#f4c069', 0.65);
  line(habitatGroup, outerRingPts2, color || '#f4c069', 0.65);

  // 2.3 环形栖居模块与停泊节点 (6 处模块化对接舱)
  const podPts = [];
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2;
    // 径向支撑桁架 (6 Radial Trusses)
    line(habitatGroup, [
      new THREE.Vector3(Math.cos(a) * rInner, 0, Math.sin(a) * rInner),
      new THREE.Vector3(Math.cos(a) * rOuter, 0, Math.sin(a) * rOuter)
    ], '#94a3b8', 0.4);

    // 舱段外框
    for (const da of [-0.08, 0.08]) {
      line(habitatGroup, [
        new THREE.Vector3(Math.cos(a + da) * rOuter, -r * 0.06, Math.sin(a + da) * rOuter),
        new THREE.Vector3(Math.cos(a + da) * rOuter2, r * 0.06, Math.sin(a + da) * rOuter2)
      ], '#f4c069', 0.55);
    }
    // 停泊导航指示灯
    podPts.push(Math.cos(a) * (rOuter + rOuter2) * 0.5, 0, Math.sin(a) * (rOuter + rOuter2) * 0.5);
  }
  const podGeom = new THREE.BufferGeometry();
  podGeom.setAttribute('position', new THREE.Float32BufferAttribute(podPts, 3));
  habitatGroup.add(new THREE.Points(podGeom, new THREE.PointsMaterial({ color: '#fcd34d', size: 0.28, transparent: true, opacity: 0.9 })));

  group.add(habitatGroup);

  // 3. 轻薄能量与散热翼板 (4 轻量轻薄辐射翼板)
  for (let w = 0; w < 4; w++) {
    const wa = (w / 4) * Math.PI * 2 + Math.PI / 4;
    const cosW = Math.cos(wa), sinW = Math.sin(wa);
    const wStart = new THREE.Vector3(cosW * r * 0.2, 0, sinW * r * 0.2);
    const wEnd = new THREE.Vector3(cosW * r * 2.2, 0, sinW * r * 2.2);
    line(group, [wStart, wEnd], '#475569', 0.35);

    // 翼板小交叉横撑
    for (const dist of [0.8, 1.3, 1.8]) {
      const p1 = new THREE.Vector3(cosW * r * dist - sinW * r * 0.18, 0, sinW * r * dist + cosW * r * 0.18);
      const p2 = new THREE.Vector3(cosW * r * dist + sinW * r * 0.18, 0, sinW * r * dist - cosW * r * 0.18);
      line(group, [p1, p2], '#64748b', 0.25);
    }
  }

  // 4. 自转动画注册 (栖居环持续平缓旋转)
  group.userData.rotatingParts = [
    { object: habitatGroup, axis: 'y', speed: 0.18 }
  ];
}

/**
 * --------------------------------------------------------------------------
 * 达斯·维达座机模型 (Darth Vader's TIE Advanced x1) - 专用于 MCP 战舰与护卫舰
 * --------------------------------------------------------------------------
 * 特征：
 * 1. 中央深空钛灰球形座舱 + 帝国标志性八边形前视窗与激光火控雷达
 * 2. 下颌双联装重型激光炮 (Twin SFS L-s9.3 Laser Cannons)
 * 3. 加长型后部超空间/双重离子推进引擎舱 (Twin Ion Engines · 红/电青双核等离子尾焰)
 * 4. 标志性三段折角太阳能电池翼板 (中心直段 + 上段内折45° + 下段内折45°)
 */
export function makeTieAdvancedX1(group, radius, color, texture, isCapital = false, modelInstance = null) {
  const r = radius * (isCapital ? 1.05 : 0.95);
  if (modelInstance) group.add(modelInstance);

  // 1. 中央球形装甲座舱 (Cockpit Sphere)
  const sphereGeom = new THREE.SphereGeometry(r * 0.72, 16, 12);
  const cockpitMat = new THREE.MeshBasicMaterial({
    color: 0x090e1c,
    transparent: true,
    opacity: 0.82,
    depthWrite: false
  });
  group.add(new THREE.Mesh(sphereGeom, cockpitMat));

  // 赤道与子午线金属加固框架
  const sphereRings = [];
  for (let j = 0; j <= 32; j++) {
    const a = (j / 32) * Math.PI * 2;
    sphereRings.push(new THREE.Vector3(Math.cos(a) * r * 0.74, 0, Math.sin(a) * r * 0.74));
  }
  line(group, sphereRings, '#64748b', 0.65);

  const vertRing = [];
  for (let j = 0; j <= 32; j++) {
    const a = (j / 32) * Math.PI * 2;
    vertRing.push(new THREE.Vector3(0, Math.cos(a) * r * 0.74, Math.sin(a) * r * 0.74));
  }
  line(group, vertRing, '#64748b', 0.55);

  // 2. 标志性八边形前视窗 (Octagonal Viewport & Imperial Crimson Targeter)
  const viewportPts = [];
  const vpRadius = r * 0.38;
  const vpZ = r * 0.68;
  for (let i = 0; i <= 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    viewportPts.push(new THREE.Vector3(Math.cos(a) * vpRadius, Math.sin(a) * vpRadius, vpZ));
  }
  line(group, viewportPts, '#f43f5e', 0.95);
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    line(group, [
      new THREE.Vector3(0, 0, vpZ + r * 0.05),
      new THREE.Vector3(Math.cos(a) * vpRadius, Math.sin(a) * vpRadius, vpZ)
    ], '#f43f5e', 0.8);
  }
  const vpEye = halo(group, texture, '#f43f5e', r * 0.65, 0.9);
  vpEye.position.set(0, 0, vpZ);

  // 3. 下颌双联装激光炮 (Twin Chin Laser Cannons)
  for (const s of [-1, 1]) {
    const cannonStart = new THREE.Vector3(s * r * 0.22, -r * 0.42, r * 0.25);
    const cannonTip = new THREE.Vector3(s * r * 0.22, -r * 0.42, r * 0.95);
    line(group, [cannonStart, cannonTip], '#ef4444', 0.95);
    const muzzle = halo(group, texture, '#ef4444', r * 0.4, 0.9);
    muzzle.position.copy(cannonTip);
  }

  // 4. 加长型后部超空间/双重离子引擎舱 (Extended Aft Hyperdrive Pod)
  const aftTop = new THREE.Vector3(0, r * 0.32, -r * 1.05);
  const aftBot = new THREE.Vector3(0, -r * 0.32, -r * 1.05);
  const aftL = new THREE.Vector3(-r * 0.42, 0, -r * 1.05);
  const aftR = new THREE.Vector3(r * 0.42, 0, -r * 1.05);
  line(group, [aftTop, aftL, aftBot, aftR, aftTop], '#64748b', 0.7);
  line(group, [new THREE.Vector3(0, r * 0.6, -r * 0.2), aftTop], '#64748b', 0.6);
  line(group, [new THREE.Vector3(0, -r * 0.6, -r * 0.2), aftBot], '#64748b', 0.6);

  // 双重离子引擎喷口 (Twin Ion Engines: 帝国红+电青高能等离子尾焰)
  for (const s of [-1, 1]) {
    const engPos = [s * r * 0.24, 0, -r * 1.1];
    const engHalo = halo(group, texture, '#f43f5e', r * 1.15, 0.85);
    engHalo.position.set(...engPos);
    const engCore = halo(group, texture, '#38bdf8', r * 0.55, 0.95);
    engCore.position.set(engPos[0], engPos[1], engPos[2] + r * 0.05);
  }

  // 5. 左右连接翼梁短臂 (Wing Pylons)
  for (const s of [-1, 1]) {
    const root = new THREE.Vector3(s * r * 0.65, 0, -r * 0.05);
    const tip = new THREE.Vector3(s * r * 1.32, 0, -r * 0.05);
    line(group, [root, tip], '#94a3b8', 0.85);
    const cuff = [];
    for (let j = 0; j <= 16; j++) {
      const a = (j / 16) * Math.PI * 2;
      cuff.push(new THREE.Vector3(s * r * 0.98, Math.cos(a) * r * 0.18, -r * 0.05 + Math.sin(a) * r * 0.18));
    }
    line(group, cuff, '#64748b', 0.75);
  }

  // 6. 标志性三段折角太阳能电池翼板 (Bent Solar Array Wings: 上折角 + 中直段 + 下折角)
  for (const s of [-1, 1]) {
    const xBase = s * r * 1.35;
    const xBend = s * r * 0.92;
    const yCenter = r * 0.52;
    const yTip = r * 1.18;
    const zFront = r * 1.15;
    const zRear = -r * 1.35;

    const getProfile = z => [
      new THREE.Vector3(xBend, yTip, z),
      new THREE.Vector3(xBase, yCenter, z),
      new THREE.Vector3(xBase + s * r * 0.06, 0, z),
      new THREE.Vector3(xBase, -yCenter, z),
      new THREE.Vector3(xBend, -yTip, z),
    ];

    const frontProf = getProfile(zFront);
    const rearProf = getProfile(zRear);

    line(group, frontProf, color, 0.95);
    line(group, rearProf, color, 0.85);

    for (let p = 0; p < 5; p++) {
      line(group, [frontProf[p], rearProf[p]], color, p === 1 || p === 3 ? 0.9 : 0.75);
    }

    // 太阳能吸收板纵向与横向细密格栅
    for (const tz of [0.65, 0.25, -0.15, -0.55, -0.95]) {
      const midProf = getProfile(r * tz);
      line(group, midProf, '#38bdf8', 0.45);
    }
  }

  // 7. 防护力场与偏转光晕
  if (isCapital) {
    const shieldGroup = new THREE.Group();
    shieldGroup.name = 'tie-advanced-shield';
    const sRing = [];
    for (let j = 0; j <= 36; j++) {
      const a = (j / 36) * Math.PI * 2;
      sRing.push(new THREE.Vector3(Math.cos(a) * r * 1.85, Math.sin(a * 4) * 0.1 * r, Math.sin(a) * r * 1.85));
    }
    line(shieldGroup, sRing, '#f43f5e', 0.75);
    group.add(shieldGroup);
    group.userData.rotatingParts = [
      { object: shieldGroup, axis: 'y', speed: 0.35 }
    ];
    halo(group, texture, '#f43f5e', r * 3.8, 0.45);
  } else {
    halo(group, texture, color, r * 2.6, 0.4);
  }
}

export function makeMcpWarship(group, radius, color, texture, modelInstance) {
  makeTieAdvancedX1(group, radius, color, texture, true, modelInstance);
}

export function makeDefenseVessel(group, radius, color, texture, subtype = 0, modelInstance = null) {
  makeTieAdvancedX1(group, radius, color, texture, false, modelInstance);
}

/**
 * --------------------------------------------------------------------------
 * 星球大战经典星际战舰模型 1: 帝国歼星舰 (Imperial Star Destroyer - ISD)
 * 专用于：核心系统战区 (Forward Battleline: 后端、云原生、代码重构、DevOps、安全)
 * --------------------------------------------------------------------------
 */
function makeImperialStarDestroyer(group, r, color, texture, modelInstance) {
  if (modelInstance) group.add(modelInstance);

  // 1. 三角楔形厚重装甲舰身 (Arrowhead Wedge Dagger Hull)
  const bow = new THREE.Vector3(0, 0, r * 3.1);
  const sternL = new THREE.Vector3(-r * 1.65, 0, -r * 1.85);
  const sternR = new THREE.Vector3(r * 1.65, 0, -r * 1.85);
  const spineMid = new THREE.Vector3(0, r * 0.4, -r * 0.2);
  const keelMid = new THREE.Vector3(0, -r * 0.45, -r * 0.2);

  line(group, [bow, sternL, sternR, bow], color, 0.95);
  line(group, [bow, spineMid, new THREE.Vector3(0, r * 0.45, -r * 1.85)], color, 0.85);
  line(group, [bow, keelMid, new THREE.Vector3(0, -r * 0.45, -r * 1.85)], color, 0.8);
  line(group, [sternL, spineMid, sternR], color, 0.7);
  line(group, [sternL, keelMid, sternR], color, 0.7);

  // 2. 侧舷沟槽装甲带与重型涡轮激光炮塔 (Lateral Trenches & Turbolasers)
  for (const s of [-1, 1]) {
    const trFront = new THREE.Vector3(s * r * 0.35, 0, r * 1.6);
    const trRear = new THREE.Vector3(s * r * 1.5, 0, -r * 1.6);
    line(group, [trFront, trRear], '#38bdf8', 0.75);
    for (const zt of [0.8, 0.1, -0.6, -1.2]) {
      const bPos = new THREE.Vector3(s * (r * 1.1 + (1 - (zt + 1.2) / 2) * r * 0.4), r * 0.18, r * zt);
      line(group, [bPos, new THREE.Vector3(bPos.x + s * r * 0.2, bPos.y + r * 0.15, bPos.z)], '#22d3ee', 0.8);
    }
  }

  // 3. 高耸指挥塔与双联偏转护盾发生器圆球 (Command Tower & Twin Deflector Spheres)
  const towerBase = new THREE.Vector3(0, r * 0.55, -r * 0.9);
  const towerNeck = new THREE.Vector3(0, r * 1.05, -r * 0.95);
  line(group, [towerBase, towerNeck], color, 0.85);

  const brL = new THREE.Vector3(-r * 0.65, r * 1.2, -r * 1.0);
  const brR = new THREE.Vector3(r * 0.65, r * 1.2, -r * 1.0);
  line(group, [brL, towerNeck, brR, brL], color, 0.9);

  // 标志性双联护盾球 (Twin Geodesic Shield Domes)
  for (const s of [-1, 1]) {
    const globePos = new THREE.Vector3(s * r * 0.42, r * 1.5, -r * 0.95);
    line(group, [new THREE.Vector3(s * r * 0.42, r * 1.2, -r * 0.95), globePos], '#94a3b8', 0.8);
    const domeGeom = new THREE.SphereGeometry(r * 0.14, 10, 8);
    const domeMesh = new THREE.Mesh(domeGeom, new THREE.MeshBasicMaterial({color: '#38bdf8', wireframe: true}));
    domeMesh.position.copy(globePos);
    group.add(domeMesh);
    const domeGlow = halo(group, texture, '#38bdf8', r * 0.7, 0.9);
    domeGlow.position.copy(globePos);
  }
  line(group, [towerNeck, new THREE.Vector3(0, r * 1.62, -r * 0.95)], '#ffffff', 0.9);

  // 4. 倒三角三联重型主推进器 + 四联副喷口 (Triple Primary + Quad Secondary Engines)
  const primThrusters = [
    [0, -r * 0.1, -r * 1.9],
    [-r * 0.65, -r * 0.1, -r * 1.9],
    [r * 0.65, -r * 0.1, -r * 1.9]
  ];
  for (const pos of primThrusters) {
    const tGlow = halo(group, texture, '#0284c7', r * 1.5, 0.9);
    tGlow.position.set(...pos);
    const tCore = halo(group, texture, '#38bdf8', r * 0.75, 0.95);
    tCore.position.set(pos[0], pos[1], pos[2] + r * 0.08);
  }
  const secThrusters = [
    [-r * 1.1, r * 0.18, -r * 1.9],
    [-r * 0.35, r * 0.22, -r * 1.9],
    [r * 0.35, r * 0.22, -r * 1.9],
    [r * 1.1, r * 0.18, -r * 1.9]
  ];
  for (const pos of secThrusters) {
    const tGlow = halo(group, texture, '#0284c7', r * 0.7, 0.75);
    tGlow.position.set(...pos);
  }

  // 腹部主机库开口 (Ventral Hangar Aperture)
  line(group, [
    new THREE.Vector3(0, -r * 0.48, r * 0.5),
    new THREE.Vector3(-r * 0.25, -r * 0.48, r * 0.15),
    new THREE.Vector3(0, -r * 0.48, -r * 0.2),
    new THREE.Vector3(r * 0.25, -r * 0.48, r * 0.15),
    new THREE.Vector3(0, -r * 0.48, r * 0.5)
  ], '#06b6d4', 0.75);

  halo(group, texture, color, r * 4.2, 0.42);
}

/**
 * --------------------------------------------------------------------------
 * 星球大战经典星际战舰模型 2: 猎兵级攻击巡洋舰 (Venator-class Republic Cruiser)
 * 专用于：智能工程战区 (East Flank: Agent编排、开发者效能、质量QA)
 * --------------------------------------------------------------------------
 */
function makeVenatorCruiser(group, r, color, texture, modelInstance) {
  if (modelInstance) group.add(modelInstance);

  // 1. 修长尖锐前矛舰首与侧翼收腰 (Venator Dagger Prow & Arrow Notches)
  const bow = new THREE.Vector3(0, 0, r * 3.3);
  const notchL = new THREE.Vector3(-r * 0.85, 0, r * 0.8);
  const notchR = new THREE.Vector3(r * 0.85, 0, r * 0.8);
  const waistL = new THREE.Vector3(-r * 0.65, 0, r * 0.4);
  const waistR = new THREE.Vector3(r * 0.65, 0, r * 0.4);
  const sternL = new THREE.Vector3(-r * 1.5, 0, -r * 2.0);
  const sternR = new THREE.Vector3(r * 1.5, 0, -r * 2.0);

  line(group, [bow, notchL, waistL, sternL, sternR, waistR, notchR, bow], color, 0.95);

  // 2. 中央红色长条机库走廊开合甲板 (Long Dorsal Venator Red Flight Deck Strip)
  const deckStart = new THREE.Vector3(0, r * 0.25, r * 2.4);
  const deckEnd = new THREE.Vector3(0, r * 0.4, -r * 0.4);
  const deckL = new THREE.Vector3(-r * 0.22, r * 0.25, r * 1.0);
  const deckR = new THREE.Vector3(r * 0.22, r * 0.25, r * 1.0);
  line(group, [deckStart, deckL, deckEnd, deckR, deckStart], '#f43f5e', 0.95);
  line(group, [deckStart, deckEnd], '#f43f5e', 0.8);

  // 3. 标志性双指挥舰桥塔 (Twin Command Bridge Towers)
  for (const s of [-1, 1]) {
    const tBase = new THREE.Vector3(s * r * 0.42, r * 0.45, -r * 0.85);
    const tTop = new THREE.Vector3(s * r * 0.42, r * 1.25, -r * 0.85);
    const tCapFront = new THREE.Vector3(s * r * 0.42, r * 1.35, -r * 0.65);
    const tCapRear = new THREE.Vector3(s * r * 0.42, r * 1.35, -r * 1.05);
    line(group, [tBase, tTop, tCapFront, tCapRear, tTop], color, 0.9);
    const bGlow = halo(group, texture, s === -1 ? '#38bdf8' : '#f43f5e', r * 0.5, 0.85);
    bGlow.position.copy(tCapFront);
  }
  // 双桥间连接走廊
  line(group, [
    new THREE.Vector3(-r * 0.42, r * 0.85, -r * 0.85),
    new THREE.Vector3(r * 0.42, r * 0.85, -r * 0.85)
  ], '#64748b', 0.7);

  // 4. 重型推进器组 (4 主引擎 + 2 侧向巡航引擎)
  const venatorEngines = [
    [-r * 0.65, r * 0.18, -r * 2.05],
    [-r * 0.65, -r * 0.18, -r * 2.05],
    [r * 0.65, r * 0.18, -r * 2.05],
    [r * 0.65, -r * 0.18, -r * 2.05],
    [-r * 1.15, 0, -r * 2.0],
    [r * 1.15, 0, -r * 2.0]
  ];
  for (const pos of venatorEngines) {
    const tGlow = halo(group, texture, '#f43f5e', r * 1.2, 0.85);
    tGlow.position.set(...pos);
    const tCore = halo(group, texture, '#38bdf8', r * 0.6, 0.95);
    tCore.position.set(pos[0], pos[1], pos[2] + r * 0.08);
  }

  halo(group, texture, color, r * 4.3, 0.45);
}

/**
 * --------------------------------------------------------------------------
 * 星球大战经典星际战舰模型 3: 蒙卡拉马里巡洋舰 (Mon Calamari MC80 Home One)
 * 专用于：前沿交互战区 (West Flank: UI交互设计、动效视听)
 * --------------------------------------------------------------------------
 */
function makeMonCalamariCruiser(group, r, color, texture, modelInstance) {
  if (modelInstance) group.add(modelInstance);

  // 1. 生物机械流线型圆润鱼雷舰体 (Organic Cylindrical Hull)
  const zSteps = [r * 2.5, r * 1.6, r * 0.6, -r * 0.4, -r * 1.3, -r * 1.8];
  const rxSteps = [r * 0.25, r * 0.65, r * 0.95, r * 1.15, r * 1.05, r * 0.85];
  const rySteps = [r * 0.22, r * 0.52, r * 0.72, r * 0.82, r * 0.75, r * 0.62];

  for (let s = 0; s < zSteps.length; s++) {
    const ringPts = [];
    const rx = rxSteps[s], ry = rySteps[s], z = zSteps[s];
    for (let j = 0; j <= 32; j++) {
      const a = (j / 32) * Math.PI * 2;
      ringPts.push(new THREE.Vector3(Math.cos(a) * rx, Math.sin(a) * ry, z));
    }
    line(group, ringPts, color, 0.7 + (s % 2) * 0.15);
  }

  for (let k = 0; k < 8; k++) {
    const a = (k / 8) * Math.PI * 2;
    const strand = [];
    for (let s = 0; s < zSteps.length; s++) {
      strand.push(new THREE.Vector3(Math.cos(a) * rxSteps[s], Math.sin(a) * rySteps[s], zSteps[s]));
    }
    line(group, strand, color, 0.65);
  }

  // 2. 多重半球形全景观测舱与感应泡 (Cascading Observation Blister Pods)
  const blisters = [
    [0, r * 0.78, r * 1.0, r * 0.22],
    [0, r * 0.95, -r * 0.6, r * 0.32],
    [-r * 0.95, r * 0.35, -r * 0.1, r * 0.25],
    [r * 0.95, r * 0.35, -r * 0.1, r * 0.25],
    [0, -r * 0.75, r * 0.3, r * 0.24]
  ];
  for (const [bx, by, bz, brad] of blisters) {
    const bGeom = new THREE.SphereGeometry(brad, 12, 8);
    const bMesh = new THREE.Mesh(bGeom, new THREE.MeshBasicMaterial({color: '#38bdf8', wireframe: true}));
    bMesh.position.set(bx, by, bz);
    group.add(bMesh);
    const bHalo = halo(group, texture, '#38bdf8', brad * 2.8, 0.85);
    bHalo.position.set(bx, by, bz);
  }

  // 3. 优雅流线型导流翼板 (Lateral Streamlined Fins)
  for (const s of [-1, 1]) {
    const fTip = new THREE.Vector3(s * r * 1.65, 0, -r * 0.7);
    const fFront = new THREE.Vector3(s * r * 1.15, 0, r * 0.2);
    const fRear = new THREE.Vector3(s * r * 1.05, 0, -r * 1.5);
    line(group, [fFront, fTip, fRear], '#38bdf8', 0.8);
  }

  // 4. 8联环形推进器喷口阵列 (Toroidal Thruster Cluster)
  for (let j = 0; j < 8; j++) {
    const a = (j / 8) * Math.PI * 2;
    const ep = [Math.cos(a) * r * 0.65, Math.sin(a) * r * 0.45, -r * 1.85];
    const tGlow = halo(group, texture, '#38bdf8', r * 0.9, 0.85);
    tGlow.position.set(...ep);
  }

  halo(group, texture, '#38bdf8', r * 4.6, 0.45);
  halo(group, texture, color, r * 2.4, 0.65);
}

/**
 * --------------------------------------------------------------------------
 * 星球大战经典星际战舰模型 4: 科雷利亚轻巡洋舰 (Corellian Corvette CR90 / Tantive IV)
 * 专用于：知识决策智库战区 (Rear Strategic Reserve: 数据、认知、写作、商业、通用)
 * --------------------------------------------------------------------------
 */
function makeCorellianCorvette(group, r, color, texture, modelInstance) {
  if (modelInstance) group.add(modelInstance);

  // 1. 标志性锤头横向指挥舱 (Hammerhead Bridge Head)
  const brW = r * 1.25, brH = r * 0.55, brD = r * 0.5;
  const brZ = r * 2.2;
  const bridgeBox = [
    new THREE.Vector3(-brW, brH * 0.5, brZ),
    new THREE.Vector3(brW, brH * 0.5, brZ),
    new THREE.Vector3(brW, -brH * 0.5, brZ),
    new THREE.Vector3(-brW, -brH * 0.5, brZ),
    new THREE.Vector3(-brW, brH * 0.5, brZ),
    new THREE.Vector3(-brW, brH * 0.5, brZ - brD),
    new THREE.Vector3(brW, brH * 0.5, brZ - brD),
    new THREE.Vector3(brW, brH * 0.5, brZ),
    new THREE.Vector3(brW, -brH * 0.5, brZ),
    new THREE.Vector3(brW, -brH * 0.5, brZ - brD),
    new THREE.Vector3(brW, brH * 0.5, brZ - brD),
    new THREE.Vector3(-brW, brH * 0.5, brZ - brD),
    new THREE.Vector3(-brW, -brH * 0.5, brZ - brD),
    new THREE.Vector3(-brW, -brH * 0.5, brZ)
  ];
  line(group, bridgeBox, color, 0.95);
  const brGlow = halo(group, texture, '#fbbf24', r * 1.3, 0.85);
  brGlow.position.set(0, 0, brZ);

  // 2. 修长中央工程脊梁管道与雷达通信天线 (Long Slender Spine & Radar Dish)
  line(group, [
    new THREE.Vector3(-r * 0.35, r * 0.25, brZ - brD), new THREE.Vector3(-r * 0.35, r * 0.25, 0),
    new THREE.Vector3(r * 0.35, r * 0.25, 0), new THREE.Vector3(r * 0.35, r * 0.25, brZ - brD)
  ], color, 0.8);
  line(group, [
    new THREE.Vector3(-r * 0.35, -r * 0.25, brZ - brD), new THREE.Vector3(-r * 0.35, -r * 0.25, 0),
    new THREE.Vector3(r * 0.35, -r * 0.25, 0), new THREE.Vector3(r * 0.35, -r * 0.25, brZ - brD)
  ], color, 0.7);

  // 背部雷达天线天盘
  const dishCenter = new THREE.Vector3(0, r * 0.75, r * 0.9);
  line(group, [new THREE.Vector3(0, r * 0.25, r * 0.9), dishCenter], '#ffffff', 0.9);
  const dishRim = [];
  for (let j = 0; j <= 24; j++) {
    const a = (j / 24) * Math.PI * 2;
    dishRim.push(new THREE.Vector3(Math.cos(a) * r * 0.35, r * 0.75 + Math.sin(a) * r * 0.15, r * 0.9 + Math.sin(a) * r * 0.25));
  }
  line(group, dishRim, '#fbbf24', 0.85);

  // 3. 坦提夫四号 11联密集火箭推进器阵列 (Tantive IV 11-Engine Cluster)
  const engBoxW = r * 1.35, engBoxH = r * 0.75, engBoxL = r * 1.6;
  const sternFaceZ = -engBoxL;
  line(group, [
    new THREE.Vector3(-engBoxW, engBoxH * 0.5, 0), new THREE.Vector3(engBoxW, engBoxH * 0.5, 0),
    new THREE.Vector3(engBoxW, engBoxH * 0.5, sternFaceZ), new THREE.Vector3(-engBoxW, engBoxH * 0.5, sternFaceZ),
    new THREE.Vector3(-engBoxW, engBoxH * 0.5, 0)
  ], color, 0.85);
  line(group, [
    new THREE.Vector3(-engBoxW, -engBoxH * 0.5, 0), new THREE.Vector3(engBoxW, -engBoxH * 0.5, 0),
    new THREE.Vector3(engBoxW, -engBoxH * 0.5, sternFaceZ), new THREE.Vector3(-engBoxW, -engBoxH * 0.5, sternFaceZ),
    new THREE.Vector3(-engBoxW, -engBoxH * 0.5, 0)
  ], color, 0.85);

  const engine11Coords = [
    [-r * 0.95, r * 0.24], [-r * 0.48, r * 0.24], [0, r * 0.24], [r * 0.48, r * 0.24], [r * 0.95, r * 0.24],
    [-r * 0.95, -r * 0.24], [-r * 0.48, -r * 0.24], [0, -r * 0.24], [r * 0.48, -r * 0.24], [r * 0.95, -r * 0.24],
    [0, 0]
  ];
  for (const [ex, ey] of engine11Coords) {
    const pos = [ex, ey, sternFaceZ - r * 0.05];
    const tGlow = halo(group, texture, '#f59e0b', r * 0.75, 0.85);
    tGlow.position.set(...pos);
    const tCore = halo(group, texture, '#ffffff', r * 0.35, 0.95);
    tCore.position.set(pos[0], pos[1], pos[2] + r * 0.05);
  }

  halo(group, texture, color, r * 4.0, 0.4);
}

export function makeFlagshipVessel(group, radius, color, texture, sectorOrSubtype = 'core', subtype = 0, modelInstance = null) {
  const r = radius * 0.92;
  const sector = typeof sectorOrSubtype === 'string' ? sectorOrSubtype : ['frontend', 'core', 'engineering', 'knowledge'][Math.abs(sectorOrSubtype) % 4];
  const modelInst = typeof sectorOrSubtype === 'string' ? modelInstance : (typeof subtype === 'object' ? subtype : null);

  if (sector === 'core') {
    makeImperialStarDestroyer(group, r, color, texture, modelInst);
  } else if (sector === 'engineering') {
    makeVenatorCruiser(group, r, color, texture, modelInst);
  } else if (sector === 'frontend') {
    makeMonCalamariCruiser(group, r, color, texture, modelInst);
  } else {
    makeCorellianCorvette(group, r, color, texture, modelInst);
  }
}

/**
 * --------------------------------------------------------------------------
 * 护航战机群 (Escort Strike Fighters: X-Wing, A-Wing, TIE Interceptor, Y-Wing)
 * --------------------------------------------------------------------------
 */
function makeXWing(group, r, color, texture) {
  const nose = new THREE.Vector3(0, 0, r * 1.85);
  const stern = new THREE.Vector3(0, 0, -r * 0.85);
  const cockpit = new THREE.Vector3(0, r * 0.28, r * 0.2);
  line(group, [nose, cockpit, stern], color, 0.9);
  line(group, [new THREE.Vector3(-r * 0.18, 0, -r * 0.85), nose, new THREE.Vector3(r * 0.18, 0, -r * 0.85)], color, 0.8);

  // 4 X-split S-Foils (展开的 X 攻击机翼)
  const wings = [
    [-r * 1.55, r * 0.72],
    [r * 1.55, r * 0.72],
    [-r * 1.55, -r * 0.72],
    [r * 1.55, -r * 0.72]
  ];
  for (const [wx, wy] of wings) {
    const tip = new THREE.Vector3(wx, wy, -r * 0.45);
    const root = new THREE.Vector3(wx * 0.15, wy * 0.15, -r * 0.15);
    line(group, [root, tip, new THREE.Vector3(wx, wy, -r * 0.85), root], color, 0.85);
    const cannonFront = new THREE.Vector3(wx, wy, r * 1.25);
    line(group, [new THREE.Vector3(wx, wy, -r * 0.65), cannonFront], '#ff4d6d', 0.95);
    const muzzle = halo(group, texture, '#ff4d6d', r * 0.4, 0.9);
    muzzle.position.copy(cannonFront);
    const engHalo = halo(group, texture, '#f43f5e', r * 0.65, 0.85);
    engHalo.position.set(wx * 0.35, wy * 0.35, -r * 0.9);
  }
}

function makeAWing(group, r, color, texture) {
  const nose = new THREE.Vector3(0, 0, r * 1.75);
  const wingL = new THREE.Vector3(-r * 1.15, 0, -r * 0.65);
  const wingR = new THREE.Vector3(r * 1.15, 0, -r * 0.65);
  const stern = new THREE.Vector3(0, 0, -r * 1.15);
  line(group, [nose, wingL, stern, wingR, nose], color, 0.9);
  line(group, [nose, new THREE.Vector3(0, r * 0.35, 0), stern], color, 0.75);

  for (const s of [-1, 1]) {
    const finBase = new THREE.Vector3(s * r * 0.55, 0, -r * 0.75);
    const finTip = new THREE.Vector3(s * r * 0.75, r * 0.65, -r * 1.1);
    line(group, [finBase, finTip, new THREE.Vector3(s * r * 0.55, 0, -r * 1.15)], color, 0.85);
    line(group, [new THREE.Vector3(s * r * 1.15, 0, -r * 0.65), new THREE.Vector3(s * r * 1.15, 0, r * 0.8)], '#38bdf8', 0.95);
    const tHalo = halo(group, texture, '#38bdf8', r * 1.1, 0.9);
    tHalo.position.set(s * r * 0.55, 0, -r * 1.2);
  }
}

function makeTieInterceptor(group, r, color, texture) {
  const pod = new THREE.Mesh(new THREE.SphereGeometry(r * 0.45, 12, 10), new THREE.MeshBasicMaterial({color: 0x090e1c, wireframe: true}));
  group.add(pod);
  const vp = halo(group, texture, '#ef4444', r * 0.45, 0.9);
  vp.position.set(0, 0, r * 0.45);

  for (const s of [-1, 1]) {
    line(group, [new THREE.Vector3(s * r * 0.4, 0, 0), new THREE.Vector3(s * r * 0.85, 0, 0)], '#64748b', 0.8);
    for (const wy of [-1, 1]) {
      const daggerTip = new THREE.Vector3(s * r * 1.05, wy * r * 0.85, r * 1.25);
      const daggerMid = new THREE.Vector3(s * r * 0.85, wy * r * 0.35, 0);
      const daggerRear = new THREE.Vector3(s * r * 1.1, wy * r * 0.9, -r * 1.15);
      line(group, [daggerRear, daggerMid, daggerTip], color, 0.95);
      line(group, [daggerTip, new THREE.Vector3(s * r * 1.05, wy * r * 0.85, r * 1.55)], '#ef4444', 0.9);
    }
    const engGlow = halo(group, texture, '#ef4444', r * 0.7, 0.9);
    engGlow.position.set(s * r * 0.18, 0, -r * 0.55);
  }
}

function makeYWing(group, r, color, texture) {
  const nose = new THREE.Vector3(0, 0, r * 1.7);
  const cabL = new THREE.Vector3(-r * 0.38, 0, r * 0.8);
  const cabR = new THREE.Vector3(r * 0.38, 0, r * 0.8);
  line(group, [nose, cabL, cabR, nose], color, 0.85);

  line(group, [new THREE.Vector3(0, 0, r * 0.8), new THREE.Vector3(0, 0, -r * 0.95)], color, 0.8);

  for (const s of [-1, 1]) {
    const pylon = new THREE.Vector3(s * r * 0.85, 0, -r * 0.15);
    line(group, [new THREE.Vector3(0, 0, -r * 0.15), pylon], '#94a3b8', 0.75);
    const nacFront = new THREE.Vector3(s * r * 0.85, 0, r * 0.6);
    const nacRear = new THREE.Vector3(s * r * 0.85, 0, -r * 1.2);
    line(group, [nacFront, nacRear], color, 0.9);
    const ring = [];
    for (let j = 0; j <= 16; j++) {
      const a = (j / 16) * Math.PI * 2;
      ring.push(new THREE.Vector3(s * r * 0.85 + Math.cos(a) * r * 0.22, Math.sin(a) * r * 0.22, -r * 1.2));
    }
    line(group, ring, '#fbbf24', 0.85);
    const tGlow = halo(group, texture, '#fbbf24', r * 0.85, 0.9);
    tGlow.position.set(s * r * 0.85, 0, -r * 1.25);
  }
}

export function makeAttackVessel(group, radius, color, texture, sectorOrSubtype = 'core', subtype = 0, modelInstance = null) {
  const r = radius * 1.1;
  const modelInst = typeof sectorOrSubtype === 'string' ? modelInstance : (typeof subtype === 'object' ? subtype : null);
  if (modelInst) group.add(modelInst);

  const sector = typeof sectorOrSubtype === 'string' ? sectorOrSubtype : ['frontend', 'core', 'engineering', 'knowledge'][Math.abs(sectorOrSubtype) % 4];

  if (sector === 'core') {
    makeXWing(group, r, color, texture);
  } else if (sector === 'frontend') {
    makeAWing(group, r, color, texture);
  } else if (sector === 'engineering') {
    makeTieInterceptor(group, r, color, texture);
  } else {
    makeYWing(group, r, color, texture);
  }

  // 强化通用等离子微型尾焰
  const thrusterGlow = halo(group, texture, '#ff5983', r * 1.6, 0.85);
  thrusterGlow.position.set(0, 0, -r * 0.95);
  const thrusterCore = halo(group, texture, '#38bdf8', r * 0.75, 0.95);
  thrusterCore.position.set(0, 0, -r * 0.9);
  halo(group, texture, color, r * 2.2, 0.45);
}

const isSkillTopicData = d => d && (d.id === '__local_skills' || /^skills?(?:\s*[\/|｜]\s*.*)?$/i.test((d.title || d.name || '').trim()));
const isMcpTopicData = d => d && (d.id === '__mcp_library' || /^mcp(?:\s*[\/|｜]\s*.*)?$/i.test((d.title || d.name || '').trim()));

export function makeMembershipLinks(content, bodies) {
  for (const parent of bodies.filter(b => b.data.kind === 'topic' && !b.data.preview)) {
    // Skill space station and MCP warship drift independently and do NOT link to the center core!
    if (isSkillTopicData(parent.data) || isMcpTopicData(parent.data)) {
      continue;
    }

    const color = neuralColor(parent.data.id), points = [];
    const start = parent.group.position.clone().normalize().multiplyScalar(7.5);
    const mid = start.clone().lerp(parent.group.position, 0.5);
    mid.z -= 1;
    dashedLine(content, new THREE.QuadraticBezierCurve3(start, mid, parent.group.position).getPoints(36), color, 0.3);

    for (const child of bodies.filter(b => b.previewParent === parent.data.id)) {
      const cStart = parent.group.position, cEnd = child.group.position;
      const cMid = cStart.clone().lerp(cEnd, 0.5);
      cMid.y += 0.7;
      const curve = new THREE.QuadraticBezierCurve3(cStart, cMid, cEnd).getPoints(8);
      for (let i = 1; i < curve.length; i++) points.push(curve[i - 1], curve[i]);
    }
    if (points.length) {
      const segGeom = new THREE.BufferGeometry().setFromPoints(points);
      content.add(new THREE.LineSegments(segGeom, getLineMaterial(color, 0.14)));
    }
  }
}
