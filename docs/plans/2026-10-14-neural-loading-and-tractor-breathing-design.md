# 优雅神经星核 Loading 与牵引线神经网络呼吸感设计规范

**日期**: 2026-10-14  
**状态**: Approved & Ready for Implementation  
**关联方案**: 知识宇宙 3D 星图 · 神经星图视觉规范 (DESIGN.md)

---

## 1. 需求与核心目标

1. **重塑 Loading 界面（告别陈旧 AI 感与机械军武科幻）**：
   - **痛点**：此前的战术陀螺仪（全息准星、机械虚线转环、EMP 脉冲指令与高饱和冷光）具有强烈的游戏机甲和模板化 AI 感，偏离了 AI Library「宁静、深邃、有机神经星图」的知识库初衷。
   - **目标**：打造沉浸、典雅、克制的「神经星核」加载形态。中心为温润呼吸的暖白星核与双层轻盈金青微光晕环，界面采用深石墨透明毛玻璃与极细金线，文案回归纯粹知识拓扑构建（如「正在编织神经星图…」）。

2. **牵引线（Tractor Lines）神经网络呼吸感**：
   - **痛点**：此前牵引线材质不透明度恒定不变，粒子以机械匀速在曲线上滑动，缺乏活体神经网络中突触兴奋与信息流动感。
   - **目标**：
     - **多相位自主呼吸**：每条连线基于端点拓扑哈希拥有独立相位（`phase = hash(fromId, toId)`），在 2.8s ~ 3.5s 周期内如神经网络般平滑起伏呼吸。
     - **突触动作电位脉冲**：粒子传导引入正弦强度调制（`sin(u * π)`），从发起节点点亮、在中段弧顶达到峰值强度微膨、在终点柔和融入，模拟突触传递物理全过程。
     - **语义悬停共振**：悬停节点时相连神经链路产生充能响应与频率共振。

---

## 2. 详细技术实现

### 2.1 DOM 与 CSS 结构重构 (`frontend/index.html` & `frontend/css/neural.css`)
- 移除：`.tactical-gyro-display`、`.gyro-reticle-ring`、`.gyro-crosshair`、游戏化文案；
- 新增：
  - `.neural-core-display`：包含柔和发光星核 `.neural-core-nucleus` 与有机双环 `.neural-orbit-ring.ring--gold`、`.neural-orbit-ring.ring--cyan`；
  - 极简通透卡片 `.warp-loading-card`：背景采用 `#141417` 配合 `rgba(244, 192, 105, 0.12)` 极细描边，提升空间感；
  - 进度条 `.warp-progress-bar`：渐变使用星光淡金到柔青（`#f4c069` 到 `#38bdf8`）。

### 2.2 交互与遥测清理 (`frontend/js/universe.js`)
- `setTelemetry(badge, message, progress)`：
  - 清理 EMP 脉冲绑定与准星提示；
  - 将默认日志文案由 `[NEURAL-SYNC]` 转化为平滑自然的知识库状态；
  - 保持平滑进度驱动与无缝渐隐退出。

### 2.3 3D 牵引线动力学升级 (`frontend/js/universe-scene.js`)
- **连线材质与呼吸循环**：
  - 在 `rebuildTractorLines()` 中为每个 `lineObj` 和 `tractorCurves` 记录确定性相位种子；
  - 在渲染循环 `render()` 中：
    ```javascript
    const breath = 0.35 + 0.35 * Math.sin(tSec * 2.0 + item.phase);
    item.lineObj.material.opacity = isHovered ? Math.max(0.85, breath * 1.5) : breath;
    ```
- **突触粒子动作电位动态调制**：
  - 粒子位置更新的同时，根据 `sin(u * π)` 动态调整顶点颜色亮度或通过动态点阵步长让能量流呈现由弱到盛再到隐的有机节律。

---

## 3. 验收标准

1. Loading 界面无机甲准星和战术游戏化文案，呈现深邃金青呼吸星核；
2. 牵引线呈现非同步自主呼吸波动，粒子呈现突触动作电位传导效果；
3. 现有自动化测试与端到端运行无报错，视觉回归正常。
