# 多元宇宙天体体系、航母战斗群编队与全息战术折跃交互设计

**日期**：2026-10-12  
**状态**：已评审确认 (Validated)  
**目标**：提升三维知识宇宙星图的科幻沉浸感、丰富度与交互趣味性，解决星体模型单一、空间站队形单调以及等待加载枯燥的问题。

---

## 1. 核心需求与背景

1. **多元天体呈现（摒弃单一水母）**：星图 27 个主题不能全部使用同一种线框水母，需根据主题规模与类别演化出四种独特的硬核科幻宇宙天体体系。
2. **航母战斗群立体编队（Carrier Battlegroup Screen）**：参考《家园》(Homeworld) 与《EVE Online》，将技能空间站的护航舰从单调尾随长龙重构为具备内层防御护盾屏障（MCP 防御舰）与外层三叉戟突击锋刃（Skill 攻击舰）的三维战术立体战群。
3. **全息战术折跃雷达与交互等待控制台**：转场增加全息准星与锁定脉冲环，等待加载界面呈现多环战术罗盘与量子遥测日志，并支持鼠标悬停加速与点击释放电磁脉冲波纹（EMP Ripple）的解压互动。

---

## 2. 详细技术方案

### 2.1 多元天体分类演化体系 (Celestial Taxonomy)

在 `frontend/js/neural-visuals.js` 中扩充天体生成函数，在 `createBody` 中根据主题哈希分化：
1. **脉冲星 / 磁星 (Pulsar / Magnetar)** (`makePulsar`)：
   - 超密发光星核；
   - 两极双向相对论等离子喷流光柱（Relativistic Beams）；
   - 双道斜交高能磁力线旋转光晕与光斑。
2. **环世界星环要塞 (Ringworld Citadel)** (`makeRingworld`)：
   - 中心高能恒星核；
   - 双层倾斜机械工程星环，内环与外环以不同速率反向自转；
   - 环面搭载太阳能光伏微格栅与装甲锚点。
3. **引力透镜吸积盘黑洞 (Accretion Disk Singularity)** (`makeAccretionDisk`)：
   - 暗能量纯黑视界内核；
   - 环绕视界的倾斜高速粒子吸积盘环（双层同心 Additive 流光环）；
   - 伴随引力红移光弧。
4. **以太星云天体 (Nebula Entity)** (`makeJelly` 重构升级)：
   - 保留并美化呼吸感等离子发光体；
   - 优化触须曲率与半透明球壳，增强等离子体流动韵律。

### 2.2 三维航母战斗群编队 (Carrier Battlegroup Screen)

在 `frontend/js/universe-scene.js` 的 `buildUniverseLayout` 中实现三维航母战斗群算法：
1. **核心母港**：坐镇深空象限 $[25.0, 13.5, -18.0]$，保持六自由度简谐浮动。
2. **内层防御屏障环 (MCP 防御战舰群)**：
   - 部署在半径 $4.5 \sim 6.2$ 单位的近程空域；
   - 八角堡垒舰（Bastion）与菱形庇护舰（Aegis）呈交错偏转护盾网（Point Defense Screen），后卫由圣堂护盾舰（Sanctuary Carrier）坐镇。
3. **外层三叉戟突击锋刃 (Skill 攻击战舰群)**：
   - 部署在半径 $8.5 \sim 14.5$ 单位的前沿空域，分为三列：
     - 中军先锋锥（Vanguard Spearhead）：双联装 Interceptor 战机以阶梯楔形直指航向前沿；
     - 左翼破障刃（Port Flank Blade）：Raider 炮艇群呈折角外展阵；
     - 右翼巡洋线（Starboard Cruiser Line）：Cruiser 战列巡洋舰斜列护航。
4. **巡航自适应微动**：
   - 各舰随母港深空位移平移的同时，舰体叠加自身周期的俯仰（Pitch）、侧滚（Banking Roll）与推进尾焰脉动。

### 2.3 全息战术折跃雷达与交互遥测控制台

1. **全息镜头转场锁焦 (Holographic Matrix Focus Lock)**：
   - 视角切入目标天体时，在 `moveCamera` 过程中激活动态全息准星与测距标尺；
   - 镜头减速到位时，在目标位置激发射一道向外扩散的网格脉冲环。
2. **战术罗盘与动态量子遥测日志**：
   - 在 `frontend/css/neural.css` 和 `frontend/js/universe.js` 中构建 HUD 罗盘组件；
   - 实时根据数据加载状态推流硬核科幻遥测日志：
     - `⟡ [NAV] 测绘深空跃迁视界向量...`
     - `⟡ [SYNAPSE] 构建知识拓扑与神经突触图谱...`
     - `⟡ [FLEET] 航母战斗群与偏转护盾阵列校准就绪`
3. **点击解压交互微特效 (Click-to-EMP Pulse)**：
   - 鼠标悬停战术罗盘时转速激增 3 倍；
   - 在加载等待期间，点击星空画布在光标位置释放瞬时电磁光环（EMP Ring Ripple）与粒子星尘微粒。

---

## 3. 测试与验证策略

1. **自动化 Playwright 端到端测试**：
   - `tests/browser_skills_station_verify.cjs`：验证航母战斗群与空间站；
   - `tests/browser_neural_ui.cjs`：跨 1440/612/390px 视口验证天体模型渲染与交互；
   - `tests/browser_agent_perspective_and_clustering.cjs` & `tests/browser_universe_hub.cjs`：保证功能完全兼容无回归。
2. **后端测试**：全量 303 项 pytest 回归测试全绿。
3. **实景截图验收**：捕获四种天体、三维航母战斗群以及全息战术罗盘的高清视觉截图。
