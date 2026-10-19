import * as THREE from '../vendor/three/three.module.js';
import { isSkillTopic } from './universe-scene.js';
import { store } from './state.js';

export class StationFlightController {
  constructor(universeController) {
    this.universe = universeController;
    this.active = false;
    this.viewMode = 'third'; // 'third' (第三人称追随) | 'first' (第一人称舰桥)
    this.freeView = false;   // 按下/按住 Shift 释放视角自由 360° 环视
    this.keys = new Set();

    this.velocity = new THREE.Vector3();
    this.yawVelocity = 0;
    this.heading = 0; // 航向弧度（初始指向 -Z 方向）
    this.stationBody = null;

    this.savedCameraPos = null;
    this.savedTarget = null;
    this.savedDrift = null;

    this.hudElement = null;
    this.speedLabel = null;
    this.viewLabel = null;
    this.shiftLabel = null;
    this.toggleBtn = null;
    this.warpStreaks = null;
    this.streakData = [];

    this.initHUD();
    this.bindEvents();
  }

  get scene() {
    return this.universe?.scene;
  }

  initHUD() {
    let hud = document.getElementById('station-flight-hud');
    if (!hud) {
      hud = document.createElement('div');
      hud.id = 'station-flight-hud';
      hud.className = 'station-flight-hud';
      hud.hidden = true;
      hud.innerHTML = `
        <div class="flight-hud-card">
          <div class="flight-hud-header">
            <div class="flight-hud-brand">
              <span class="flight-hud-beacon"></span>
              <strong class="flight-hud-title">技能空间站 · 战备巡航飞控</strong>
            </div>
            <span class="flight-hud-speed" id="flight-hud-speed">0.0 AU/s</span>
          </div>
          <div class="flight-hud-controls">
            <span class="flight-hud-pill"><strong>WASD</strong> 推进转向</span>
            <span class="flight-hud-pill"><strong>Space / C</strong> 升降机动</span>
            <button type="button" class="flight-hud-pill flight-hud-btn" id="flight-hud-view-toggle">
              🎥 视角: <strong id="flight-hud-view-label">第三人称 (V)</strong>
            </button>
            <button type="button" class="flight-hud-pill flight-hud-btn" id="flight-hud-shift-toggle">
              👁️ 观察: <strong id="flight-hud-shift-label">锁定 (Shift)</strong>
            </button>
            <button type="button" class="flight-hud-pill flight-hud-exit" id="flight-hud-exit-btn">泊车退出 (Esc / F)</button>
          </div>
        </div>
      `;
      document.getElementById('workspace-universe')?.append(hud);
    }
    this.hudElement = hud;
    this.speedLabel = hud.querySelector('#flight-hud-speed');
    this.viewLabel = hud.querySelector('#flight-hud-view-label');
    this.shiftLabel = hud.querySelector('#flight-hud-shift-label');

    hud.querySelector('#flight-hud-view-toggle')?.addEventListener('click', () => this.toggleViewMode());
    hud.querySelector('#flight-hud-shift-toggle')?.addEventListener('click', () => this.toggleFreeView());
    hud.querySelector('#flight-hud-exit-btn')?.addEventListener('click', () => this.stopFlight());

    this.toggleBtn = document.getElementById('universe-flight-toggle');
    if (this.toggleBtn && !this.toggleBtn._hasFlightListener) {
      this.toggleBtn._hasFlightListener = true;
      this.toggleBtn.addEventListener('click', () => this.toggleFlight());
    }
  }

  bindEvents() {
    window.addEventListener('keydown', e => {
      // 忽略在输入框、文本区或打开对话框时的按键
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName) || document.querySelector('dialog[open]')) return;
      if (this.universe?.app?.activeTab !== 'universe') return;

      const code = e.code;
      if (code === 'KeyF') {
        e.preventDefault();
        this.toggleFlight();
        return;
      }

      if (!this.active) return;

      if (code === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        this.stopFlight();
        return;
      }

      if (code === 'KeyV') {
        e.preventDefault();
        this.toggleViewMode();
        return;
      }

      if (code === 'ShiftLeft' || code === 'ShiftRight') {
        // 按住 Shift 释放自由观察视角
        this.setFreeView(true);
      }

      if (['KeyW', 'KeyS', 'KeyA', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space', 'KeyC'].includes(code)) {
        e.preventDefault();
        this.keys.add(code);
      }
    });

    window.addEventListener('keyup', e => {
      const code = e.code;
      if (code === 'ShiftLeft' || code === 'ShiftRight') {
        this.setFreeView(false);
      }
      this.keys.delete(code);
    });
  }

  findStationBody() {
    if (!this.scene?.bodies?.length) return null;
    // 优先寻找总览中的技能空间站或内部母港核心
    return this.scene.bodies.find(b => !b.decorative && (isSkillTopic(b.data) || b.data?.id === '__local_skills' || b.data?.isStationCore)) || null;
  }

  startFlight() {
    const station = this.findStationBody();
    if (!station) {
      this.universe?.app?.showToast('未检测到技能空间站，请回到宇宙总览或母港', 'warning');
      return false;
    }

    this.stationBody = station;
    station.isControlled = true;

    // 暂停自动微动漂移，由飞控全面接管
    if (station.drift) {
      this.savedDrift = station.drift;
      station.drift = null;
    }

    this.heading = station.group.rotation.y || 0;
    this.velocity.set(0, 0, 0);
    this.yawVelocity = 0;
    this.active = true;
    store.set({ flightMode: true });

    if (this.scene) {
      this.savedCameraPos = this.scene.camera.position.clone();
      this.savedTarget = this.scene.controls.target.clone();
      this.scene.controls.enablePan = false;
      this.scene.controls.minDistance = 2.0;
      this.scene.controls.maxDistance = 280.0;

      const fwdVec = new THREE.Vector3(Math.sin(this.heading), 0, Math.cos(this.heading));
      const stationPos = station.group.position;
      if (this.viewMode === 'first') {
        const bridgePos = stationPos.clone().add(new THREE.Vector3(0, 3.8, 0)).addScaledVector(fwdVec, 2.5);
        this.scene.camera.position.copy(bridgePos);
        this.scene.controls.target.copy(bridgePos.clone().addScaledVector(fwdVec, 60.0));
      } else {
        const chasePos = stationPos.clone().addScaledVector(fwdVec, -28.0).add(new THREE.Vector3(0, 10.5, 0));
        this.scene.camera.position.copy(chasePos);
        this.scene.controls.target.copy(stationPos.clone().addScaledVector(fwdVec, 10.0));
      }
      this.scene.controls.update();
    }

    if (this.hudElement) this.hudElement.hidden = false;
    this.toggleBtn = this.toggleBtn || document.getElementById('universe-flight-toggle');
    if (this.toggleBtn) {
      this.toggleBtn.classList.add('active');
      this.toggleBtn.setAttribute('aria-pressed', 'true');
    }

    this.updateHUD();
    this.universe?.app?.showToast('已接管技能空间站飞控系统 · [WASD] 推进转向 · [V] 切换视角 · [Shift] 自由观察', 'success');
    this.scene?.requestFrame();
    return true;
  }

  stopFlight() {
    if (!this.active) return;
    this.active = false;
    store.set({ flightMode: false });
    this.keys.clear();
    this.velocity.set(0, 0, 0);
    this.yawVelocity = 0;

    if (this.stationBody) {
      this.stationBody.isControlled = false;
      if (this.savedDrift) {
        this.stationBody.drift = {
          baseX: this.stationBody.group.position.x,
          baseY: this.stationBody.group.position.y,
          baseZ: this.stationBody.group.position.z
        };
      }
      this.stationBody = null;
    }

    if (this.scene) {
      this.scene.controls.enablePan = true;
      if (this.savedTarget) {
        this.scene.controls.target.copy(this.savedTarget);
      }
      this.scene.controls.update();
      this.scene.requestFrame();
    }

    if (this.hudElement) this.hudElement.hidden = true;
    const cockpitOverlay = document.getElementById('bridge-cockpit-overlay');
    if (cockpitOverlay) cockpitOverlay.hidden = true;
    if (this.warpStreaks) {
      this.warpStreaks.visible = false;
      this.warpStreaks.material.opacity = 0;
    }

    this.toggleBtn = this.toggleBtn || document.getElementById('universe-flight-toggle');
    if (this.toggleBtn) {
      this.toggleBtn.classList.remove('active');
      this.toggleBtn.setAttribute('aria-pressed', 'false');
    }

    this.universe?.app?.showToast('空间站已就地进入轨道泊车状态', 'info');
  }

  toggleFlight() {
    if (this.active) this.stopFlight();
    else this.startFlight();
  }

  toggleViewMode() {
    this.viewMode = this.viewMode === 'third' ? 'first' : 'third';
    this.updateHUD();
    if (this.stationBody && this.scene) {
      const fwdVec = new THREE.Vector3(Math.sin(this.heading), 0, Math.cos(this.heading));
      const stationPos = this.stationBody.group.position;
      if (this.viewMode === 'first') {
        const bridgePos = stationPos.clone().add(new THREE.Vector3(0, 3.8, 0)).addScaledVector(fwdVec, 2.5);
        this.scene.camera.position.copy(bridgePos);
        this.scene.controls.target.copy(bridgePos.clone().addScaledVector(fwdVec, 60.0));
      } else {
        const chasePos = stationPos.clone().addScaledVector(fwdVec, -28.0).add(new THREE.Vector3(0, 10.5, 0));
        this.scene.camera.position.copy(chasePos);
        this.scene.controls.target.copy(stationPos.clone().addScaledVector(fwdVec, 10.0));
      }
      this.scene.controls.update();
    }
    const modeName = this.viewMode === 'first' ? '第一人称 · 舰桥穹顶视角' : '第三人称 · 跟随全景视角';
    this.universe?.app?.showToast(`已切换至【${modeName}】`, 'info');
    this.scene?.requestFrame();
  }

  setFreeView(enabled) {
    if (this.freeView === enabled) return;
    this.freeView = enabled;
    this.updateHUD();
    this.scene?.requestFrame();
  }

  toggleFreeView() {
    this.setFreeView(!this.freeView);
  }

  updateHUD() {
    if (this.viewLabel) {
      this.viewLabel.textContent = this.viewMode === 'first' ? '第一人称舰桥 (V)' : '第三人称追随 (V)';
    }
    if (this.shiftLabel) {
      this.shiftLabel.textContent = this.freeView ? '自由观察中 (Shift)' : '锁定航向 (Shift)';
    }
    const card = this.hudElement?.querySelector('.flight-hud-card');
    if (card) {
      card.classList.toggle('is-first-person', this.viewMode === 'first');
      card.classList.toggle('is-free-view', this.freeView);
    }
    const cockpitOverlay = document.getElementById('bridge-cockpit-overlay');
    if (cockpitOverlay) {
      cockpitOverlay.hidden = !(this.active && this.viewMode === 'first');
    }
  }

  initWarpStreaks() {
    if (this.warpStreaks || !this.scene?.scene) return;
    const COUNT = 220;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(COUNT * 2 * 3);
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const material = new THREE.LineBasicMaterial({
      color: 0x7dd3fc,
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });

    this.warpStreaks = new THREE.LineSegments(geometry, material);
    this.warpStreaks.frustumCulled = false;
    this.warpStreaks.visible = false;
    this.scene.scene.add(this.warpStreaks);

    this.streakData = [];
    for (let i = 0; i < COUNT; i++) {
      const radius = 6 + Math.random() * 34;
      const angle = Math.random() * Math.PI * 2;
      const depth = Math.random() * 120 - 20;
      this.streakData.push({
        rx: Math.cos(angle) * radius,
        ry: Math.sin(angle) * radius + (Math.random() - 0.5) * 6,
        depth: depth,
        speedFactor: 0.8 + Math.random() * 0.4
      });
    }
  }

  updateWarpStreaks(speedAU, dt, fwdVec) {
    if (!this.warpStreaks) {
      this.initWarpStreaks();
      if (!this.warpStreaks) return;
    }

    if (speedAU < 0.25) {
      if (this.warpStreaks.material.opacity > 0.01) {
        this.warpStreaks.material.opacity = Math.max(0, this.warpStreaks.material.opacity - dt * 3.5);
      } else {
        this.warpStreaks.visible = false;
      }
      return;
    }

    this.warpStreaks.visible = true;
    const targetOpacity = THREE.MathUtils.clamp((speedAU - 0.3) / 1.5, 0.1, 0.85);
    this.warpStreaks.material.opacity = THREE.MathUtils.lerp(this.warpStreaks.material.opacity, targetOpacity, 0.15);

    const camPos = this.scene.camera.position;
    const rightVec = new THREE.Vector3(-fwdVec.z, 0, fwdVec.x).normalize();
    const upVec = new THREE.Vector3(0, 1, 0);

    const posAttr = this.warpStreaks.geometry.attributes.position;
    const arr = posAttr.array;
    const streakLength = Math.min(28.0, 4.0 + speedAU * 2.2);

    for (let i = 0; i < this.streakData.length; i++) {
      const s = this.streakData[i];
      s.depth -= speedAU * 18.0 * s.speedFactor * dt;
      if (s.depth < -25.0) {
        s.depth = 95.0 + Math.random() * 25.0;
      }

      const headX = camPos.x + rightVec.x * s.rx + upVec.x * s.ry + fwdVec.x * s.depth;
      const headY = camPos.y + rightVec.y * s.rx + upVec.y * s.ry + fwdVec.y * s.depth;
      const headZ = camPos.z + rightVec.z * s.rx + upVec.z * s.ry + fwdVec.z * s.depth;

      const tailX = headX - fwdVec.x * streakLength;
      const tailY = headY - fwdVec.y * streakLength;
      const tailZ = headZ - fwdVec.z * streakLength;

      const idx = i * 6;
      arr[idx] = headX;
      arr[idx + 1] = headY;
      arr[idx + 2] = headZ;
      arr[idx + 3] = tailX;
      arr[idx + 4] = tailY;
      arr[idx + 5] = tailZ;
    }

    posAttr.needsUpdate = true;
  }

  tick(time, delta) {
    if (!this.active || !this.stationBody || !this.scene?.ready) return;

    const station = this.stationBody;
    const dt = Math.min(0.08, Math.max(0.001, delta));

    let thrustForward = 0;
    let thrustYaw = 0;
    let thrustVertical = 0;

    if (this.keys.has('KeyW') || this.keys.has('ArrowUp')) thrustForward += 1;
    if (this.keys.has('KeyS') || this.keys.has('ArrowDown')) thrustForward -= 1;
    if (this.keys.has('KeyA') || this.keys.has('ArrowLeft')) thrustYaw += 1;
    if (this.keys.has('KeyD') || this.keys.has('ArrowRight')) thrustYaw -= 1;
    if (this.keys.has('Space')) thrustVertical += 1;
    if (this.keys.has('KeyC')) thrustVertical -= 1;

    // 动力学常数配置
    const ACCEL = 24.0;
    const TURN_ACCEL = 2.6;
    const DRAG = 0.94;
    const YAW_DRAG = 0.88;

    // 偏航角速度计算与阻尼
    this.yawVelocity += thrustYaw * TURN_ACCEL * dt;
    this.yawVelocity *= Math.pow(YAW_DRAG, dt * 60);
    this.heading += this.yawVelocity * dt;
    station.group.rotation.y = this.heading;

    // 水平朝向向量 (+Z 为战舰与空间站舰艏前进方向)
    const fwdX = Math.sin(this.heading);
    const fwdZ = Math.cos(this.heading);

    this.velocity.x += fwdX * thrustForward * ACCEL * dt;
    this.velocity.z += fwdZ * thrustForward * ACCEL * dt;
    this.velocity.y += thrustVertical * ACCEL * 0.7 * dt;

    this.velocity.multiplyScalar(Math.pow(DRAG, dt * 60));
    station.group.position.addScaledVector(this.velocity, dt);

    if (station.data) {
      station.data.position = [station.group.position.x, station.group.position.y, station.group.position.z];
    }

    // 速度显示仪表更新
    const speedAU = this.velocity.length();
    if (this.speedLabel) {
      this.speedLabel.textContent = `${speedAU.toFixed(1)} AU/s`;
    }

    // 伴飞护航舰队实时跟随机动（保持舰队阵型随空间站转向同步偏航）
    const previews = this.scene.bodies.filter(b => b.previewParent === station.data?.id);
    const cosH = Math.cos(this.heading);
    const sinH = Math.sin(this.heading);
    for (const b of previews) {
      if (b.previewVector) {
        if (!b.initialPreviewVector) {
          b.initialPreviewVector = b.previewVector.clone();
        }
        const lx = b.initialPreviewVector.x;
        const ly = b.initialPreviewVector.y;
        const lz = b.initialPreviewVector.z;
        const rx = lx * cosH + lz * sinH;
        const rz = -lx * sinH + lz * cosH;
        b.group.position.set(
          station.group.position.x + rx,
          station.group.position.y + ly,
          station.group.position.z + rz
        );
        b.group.rotation.y = (b.rotationY || 0) + this.heading;
      }
    }

    // 相机视口控制
    const stationPos = station.group.position;
    const fwdVec = new THREE.Vector3(fwdX, 0, fwdZ);

    if (this.freeView) {
      // 【Shift 自由视角】：释放驾驶相机锁定，由鼠标在 OrbitControls 中自由围绕空间站 360° 旋转
      this.scene.controls.target.copy(stationPos);
      this.scene.controls.update();
    } else if (this.viewMode === 'first') {
      // 【第一人称舰桥视角】：相机位于空间站顶部指挥穹顶，前视深空星云
      const bridgePos = stationPos.clone().add(new THREE.Vector3(0, 3.8, 0)).addScaledVector(fwdVec, 2.5);
      const lookAhead = bridgePos.clone().addScaledVector(fwdVec, 60.0);
      this.scene.camera.position.copy(bridgePos);
      this.scene.controls.target.copy(lookAhead);
      this.scene.controls.update();
    } else {
      // 【第三人称跟随视角】：相机位于空间站后上方，带有柔和惯性追随
      const chaseCamPos = stationPos.clone().addScaledVector(fwdVec, -28.0).add(new THREE.Vector3(0, 10.5, 0));
      const lookTarget = stationPos.clone().addScaledVector(fwdVec, 10.0);
      this.scene.camera.position.lerp(chaseCamPos, 0.12);
      this.scene.controls.target.lerp(lookTarget, 0.16);
      this.scene.controls.update();
    }

    // 3-1: 第一人称舰桥全息 HUD 航电与战术传感器实时更新
    if (this.viewMode === 'first' && this.active) {
      const deg = Math.round(((this.heading * 180 / Math.PI) % 360 + 360) % 360);
      const hdgEl = document.getElementById('cockpit-telemetry-hdg');
      if (hdgEl) hdgEl.textContent = `${deg.toString().padStart(3, '0')}°`;

      const spdEl = document.getElementById('cockpit-telemetry-spd');
      if (spdEl) spdEl.textContent = `${speedAU.toFixed(1)} AU/s`;

      const vrateEl = document.getElementById('cockpit-telemetry-vrate');
      if (vrateEl) vrateEl.textContent = `${this.velocity.y >= 0 ? '+' : ''}${this.velocity.y.toFixed(1)}`;

      const warpEl = document.getElementById('cockpit-telemetry-warp');
      if (warpEl) {
        if (speedAU > 1.6) {
          warpEl.textContent = 'HYPERDRIVE ACTIVE';
          warpEl.className = 'cockpit-val warp-active';
        } else if (speedAU > 0.4) {
          warpEl.textContent = 'WARP CRUISE';
          warpEl.className = 'cockpit-val warp-cruise';
        } else {
          warpEl.textContent = 'IMPULSE STANDBY';
          warpEl.className = 'cockpit-val highlight';
        }
      }
    }

    // 3-2: 高速航行推进粒子光效与折跃拉线系统更新
    this.updateWarpStreaks(speedAU, dt, fwdVec);

    this.scene.requestFrame();
  }
}
