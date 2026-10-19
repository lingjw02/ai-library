import * as THREE from '../vendor/three/three.module.js';
import { GLTFLoader } from '../vendor/three/GLTFLoader.js';

const names = ['personal-core', 'project-engineering', 'project-creative', 'project-knowledge',
  'note-crystal', 'memory-core', 'skill-station', 'mcp-ship', 'skill-module', 'mcp-vessel'];
const assets = new Map();
let pending = null;
let generation = 0;
const previews = new Map();

function release(root) {
  const geometries = new Set(), materials = new Set();
  root.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : object.material ? [object.material] : []) {
      materials.add(material);
    }
  });
  geometries.forEach(geometry => geometry.dispose());
  materials.forEach(material => material.dispose());
}

/** One atomic cached load; a failed attempt can be retried by calling again. */
export function loadUniverseModels() {
  if (pending) return pending;
  const currentGeneration = generation;
  const loader = new GLTFLoader();
  pending = Promise.allSettled(names.map(async name => {
    const gltf = await loader.loadAsync(new URL(`../assets/models/${name}.glb`, import.meta.url).href);
    return [name, gltf.scene];
  })).then(results => {
    const loaded = results.filter(result => result.status === 'fulfilled').map(result => result.value);
    const error = results.find(result => result.status === 'rejected');
    if (generation !== currentGeneration || error) {
      loaded.forEach(([, root]) => release(root));
      if (generation === currentGeneration) pending = null;
      throw error?.reason || new Error('Universe model loading was cancelled.');
    }
    for (const [name, root] of loaded) {
      root.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(root);
      const center = bounds.getCenter(new THREE.Vector3());
      let radius = 0;
      const vertex = new THREE.Vector3();
      root.traverse(object => {
        if (!object.isMesh) return;
        const position = object.geometry.attributes.position;
        for (let index = 0; index < position.count; index++) {
          vertex.fromBufferAttribute(position, index).applyMatrix4(object.matrixWorld);
          radius = Math.max(radius, vertex.distanceTo(center));
        }
        object.userData.sharedUniverseResource = true;
      });
      assets.set(name, {root, center, radius: radius || 1});
    }
    return {status: 'ready', count: assets.size};
  });
  return pending;
}

function projectAsset(data) {
  const words = `${data.currentTitle || ''} ${data.title || ''} ${data.name || ''} ${data.domain || ''}`.toLowerCase();
  if (/design|art|creative|wallpaper|anime|illustrat|角色|设计|创作|壁纸|绘|动画|美术/.test(words)) return 'project-creative';
  if (/code|software|ai|mcp|api|cad|3d|engineer|develop|tech|技术|开发|工程|编程|模型|智能/.test(words)) return 'project-engineering';
  return 'project-knowledge';
}

function selection(data) {
  const kind = data.kind || data.type || 'note';
  const role = String(data.modelRole || '').toLowerCase();
  const identity = data.id || data.collection_id || '';
  if (kind === 'notes-hub') return ['personal-core', 'personal'];
  if (kind === 'local-core') {
    if (/mcp|ship|vessel/.test(role) || identity === '__mcp_library') return ['mcp-ship', 'mcp'];
    if (/skill|station/.test(role) || identity === '__local_skills') return ['skill-station', 'skill'];
    if (/personal|notes-hub/.test(role)) return ['personal-core', 'personal'];
    if (/memory/.test(role)) return ['memory-core', 'memory'];
    if (/^note$|crystal/.test(role)) return ['note-crystal', 'note'];
    return [projectAsset(data), 'project'];
  }
  if (kind === 'topic') {
    const title = (data.title || data.name || '').trim();
    if (identity === '__local_skills' || /^skills?(?:\s*[\/|｜]\s*.*)?$/i.test(title)) return ['skill-station', 'skill'];
    if (identity === '__mcp_library' || /^mcp(?:\s*[\/|｜]\s*.*)?$/i.test(title)) return ['mcp-ship', 'mcp'];
    return [projectAsset(data), 'project'];
  }
  if (data.isFlagship) return ['skill-station', 'skill'];
  if (kind === 'skill') return ['skill-module', 'skill'];
  if (kind === 'mcp') return ['mcp-vessel', 'mcp'];
  if (kind === 'memory' || data.memory_id) return ['memory-core', 'memory'];
  return ['note-crystal', 'note'];
}

function previewResource(family) {
  if (previews.has(family)) return previews.get(family);
  let geometry;
  const color = {personal:0xd2a15e, project:0x719bad, note:0x6bc9d7, memory:0x8b9eda, skill:0xd5b17c, mcp:0x95bac8}[family];
  if (family === 'note' || family === 'memory') {
    geometry = new THREE.OctahedronGeometry(1, 0);
    geometry.scale(.65, 1, .65);
  } else if (family === 'mcp') {
    geometry = new THREE.ConeGeometry(.5, 1.9, 4);
    geometry.rotateX(Math.PI / 2);
  } else if (family === 'skill') {
    geometry = new THREE.BoxGeometry(1.2, .5, 1.2);
  } else {
    geometry = new THREE.IcosahedronGeometry(1, 0);
  }
  geometry.computeBoundingSphere();
  geometry.scale(1 / geometry.boundingSphere.radius, 1 / geometry.boundingSphere.radius, 1 / geometry.boundingSphere.radius);
  const material = new THREE.MeshStandardMaterial({color, roughness:.48, metalness:.3,
    emissive:color, emissiveIntensity:.12});
  const resource = {geometry, material};
  previews.set(family, resource);
  return resource;
}

/** Safe clone: transforms are private; immutable cached mesh buffers/materials are shared.
 * Callers must not dispose or mutate shared mesh geometry/materials. Dispose once at teardown.
 */
export function createUniverseModel(data, radius, {preview = false} = {}) {
  if (!(Number.isFinite(radius) && radius > 0)) throw new RangeError('A positive finite model radius is required.');
  const [assetName, modelFamily] = selection(data);
  const group = new THREE.Group();
  group.name = `${assetName}${preview ? '-preview' : ''}`;
  group.userData = {modelFamily, assetName, preview, nominalRadius:radius, sharedUniverseResource:true, movingParts:[]};
  if (preview) {
    const resource = previewResource(modelFamily);
    const mesh = new THREE.Mesh(resource.geometry, resource.material);
    mesh.userData.sharedUniverseResource = true;
    mesh.raycast = () => {};
    group.add(mesh);
    group.scale.setScalar(radius);
    return group;
  }
  const cached = assets.get(assetName);
  if (!cached) throw new Error('Universe models are not ready. Await loadUniverseModels() first.');
  const root = cached.root.clone(true);
  root.position.sub(cached.center);
  group.add(root);
  group.scale.setScalar(radius / cached.radius);
  root.traverse(object => {
    if (object.name.startsWith('idle_rotor_')) {
      group.userData.movingParts.push({object, axis:'y', speed:modelFamily === 'personal' ? .035 : .045});
    }
  });
  return group;
}

/** Release only after all scene instances have been removed. */
export function disposeUniverseModels() {
  generation++;
  assets.forEach(({root}) => release(root));
  assets.clear();
  previews.forEach(({geometry, material}) => { geometry.dispose(); material.dispose(); });
  previews.clear();
  pending = null;
}
