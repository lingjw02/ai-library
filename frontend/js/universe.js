import { collectUniverse, collectTopicPreviews, buildDomainFleets } from './universe-catalogue.js?h=20261012b';
import { neuralColor } from './neural-visuals.js?h=20261012b';
import { SpiderMechaController } from './spider-mecha.js';
import { StationFlightController } from './station-flight.js';
const $ = id => document.getElementById(id);
const typeName = kind => ({topic: '子主题', note: '笔记', memory: 'AI 记忆', skill: 'Skill', mcp: 'MCP 战备服务'})[kind] || '内容';
const skillTopic = t => /^skills?(?:\s*\/\s*工具配置)?$/i.test(t.title.trim());
const cleanTitle = s => (s || '未命名').replace(/^【[^】]+】\s*/, '').replace(/^聊天摘要[｜|]\s*/, '');
// 来源识别：把 author 归为可读的来源徽章（我 / 豆包 / Antigravity / 盘古 / Codex / 其他导入）
const SOURCE_RULES = [
  [/^(user|operator)$/i, '我', '#f4c069'],
  [/doubao/i, '豆包', '#34d399'],
  [/antigravity/i, 'Antigravity', '#a78bfa'],
  [/pangu/i, '盘古', '#f87171'],
  [/codex/i, 'Codex', '#38bdf8'],
  [/deepseek/i, 'DeepSeek', '#60a5fa'],
  [/qwen/i, 'Qwen', '#fbbf24'],
  [/.+/, '导入', '#94a3b8'],
];
function sourceBadge(author) {
  const b = document.createElement('span');
  b.className = 'universe-source-badge';
  const rule = SOURCE_RULES.find(([re]) => re.test(author || '')) || SOURCE_RULES[SOURCE_RULES.length - 1];
  const color = rule[2];
  b.textContent = rule[1];
  b.style.background = color + '22';
  b.style.color = color;
  b.style.borderColor = color + '55';
  b.title = `来源：${author || '（无）'}`;
  return b;
}
// 「我的空间」= 我写的笔记 + 关于我的记忆（kind 不限，混排）。
const isPersonalSpace = u => u.agentId === 'user';
async function call(path, method = 'GET', body) {
  const res = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
  const data = await res.json();
  if (!res.ok) throw new Error(res.status === 409 ? '内容已被更新，请关闭后重开此操作再试。' : typeof data.detail === 'string' ? data.detail : `操作失败（${res.status}），请检查输入后重试。`);
  return data;
}

export class UniverseController {
  constructor(app) {
    this.app = app; this.scope = 'default'; this.topicId = null; this.kind = 'all'; this.query = '';
    this.perspective = 'topic'; this.agentId = null; this.showTractorLines = true; this.clusterSuggestions = [];
    this.request = 0; this.data = null; this.scene = null;
    this.overviewCache = new Map(); this.tractorLineCache = new Map();
    this.paused = matchMedia('(prefers-reduced-motion: reduce)').matches; this.busy = false;
    this.pendingEntryFrom = null;

    $('universe-search-form').onsubmit = e => { e.preventDefault(); this.search(); };
    $('universe-search').addEventListener('input', () => {
      ++this.request; clearTimeout(this.timer); this.timer = setTimeout(() => this.search(), 300);
    });
    $('universe-exit').onclick = () => this.stepBack();
    $('neural-overview').onclick = () => { this.app.showUniverse(); this.toggleDirectory(false); this.agentId = null; this.perspective = 'topic'; this.updatePerspectiveUI(); this.navigate(null); };
    window.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('universe-search').focus(); }
    });
    // Esc 逐级退出：capture 阶段先判断，避免与笔记/记忆抽屉自身的 Esc 关闭逻辑冲突
    window.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      if (this.app.activeTab !== 'universe') return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (document.querySelector('dialog[open]')) return;
      if (this.app.notesMgr?.modalBackdrop.classList.contains('open') || this.app.memoryNet?.drawer.classList.contains('open')) return;
      const hasLevel = this.topicId || this.agentId || this.query || this.perspective === 'agent';
      if (!hasLevel) return;
      e.preventDefault();
      if (!$('universe-directory').hidden) { this.toggleDirectory(false); return; }
      this.stepBack();
    }, true);
    $('universe-tractor-toggle').onclick = () => this.toggleTractorLines();
    $('universe-cluster-open').onclick = () => this.openClusterDialog();
    $('universe-cluster-dismiss').onclick = () => { $('universe-cluster-banner').hidden = true; };
    $('universe-cluster-close').onclick = () => $('universe-cluster-dialog').close();
    $('universe-cluster-cancel').onclick = () => $('universe-cluster-dialog').close();
    $('universe-skill-close').onclick = () => $('universe-skill-dialog').close();
    $('universe-skill-related').onclick = () => { if (this.data?.relatedTopic) this.navigate(this.data.relatedTopic.id); };
    $('universe-directory-toggle').onclick = () => { this.app.showUniverse(); this.toggleDirectory(); };
    $('universe-directory-close').onclick = () => this.toggleDirectory(false);
    $('universe-pause').onclick = () => {
      this.paused = !this.paused; this.scene?.setPaused(this.paused); this.updatePause();
    };
    const saveLayoutBtn = $('universe-save-layout');
    if (saveLayoutBtn) saveLayoutBtn.onclick = () => this.saveCurrentViewState();
    const saveViewBtn = $('btn-save-current-view');
    if (saveViewBtn) saveViewBtn.onclick = () => this.saveCurrentSearchAsView();
    $('universe-reset').onclick = () => {
      this.scene?.resetView();
      // 后端没有 DELETE /api/view-state，重置走 POST /api/view-state/reset。
      call('/api/view-state/reset', 'POST', {
        owner_id: 'local-owner',
        workspace_id: 'universe-main',
        scope: this.scope,
        expected_revision: Number.isSafeInteger(this.viewStateRevision) ? this.viewStateRevision : 0,
        // reset 复用 SaveLayout 请求体，state 是必填字段（ViewState 有默认值，但字段本身不可省）
        state: { schema_version: 'viewstate-v1' },
      }).then(r => { if (r && Number.isSafeInteger(r.revision)) this.viewStateRevision = r.revision; }).catch(() => {});
      try { localStorage.removeItem('universe-view-filters'); } catch {}
    };
    $('universe-rename').onclick = () => this.openTopicForm(true);
    $('universe-split').onclick = () => this.splitCurrentTopic();
    const autoClusterBtn = $('universe-auto-cluster');
    if (autoClusterBtn) autoClusterBtn.onclick = () => this.autoClusterCurrentPage();
    const dirClusterBtn = $('directory-auto-cluster');
    if (dirClusterBtn) dirClusterBtn.onclick = () => this.autoClusterCurrentPage();
    $('universe-topic-form').onsubmit = e => { e.preventDefault(); this.saveTopic(); };
    $('universe-topic-cancel').onclick = () => $('universe-topic-dialog').close();
    $('universe-membership-close').onclick = () => $('universe-membership-dialog').close();
    $('universe-membership-dialog').addEventListener('cancel', e => { if (this.memberBusy) e.preventDefault(); });
    $('universe-topic-dialog').addEventListener('cancel', e => { if (this.busy) e.preventDefault(); });

    const scopeLabel = document.createElement('label'); scopeLabel.className = 'universe-scope'; scopeLabel.textContent = '资料范围';
    const scopeInput = document.createElement('input'); scopeInput.id = 'universe-scope'; scopeInput.value = this.scope; scopeInput.maxLength = 200; scopeInput.setAttribute('aria-label', '资料范围');
    scopeLabel.append(scopeInput); $('universe-list-status').before(scopeLabel);
    scopeInput.addEventListener('change', () => this.setScope(scopeInput.value));

    const layoutControls = () => {
      const top = document.querySelector('.universe-top'), mobile = matchMedia('(max-width:640px)').matches;
      $('universe-directory').style.top = `${top.offsetTop + top.offsetHeight + (mobile ? 16 : 26)}px`;
    };
    this.headingObserver = new ResizeObserver(layoutControls);
    this.headingObserver.observe(document.querySelector('.universe-top'));
    window.addEventListener('resize', layoutControls); layoutControls();
    this.updatePause();
    this.updatePerspectiveUI();
  }

  async init() {
    try {
      const {UniverseScene} = await import('./universe-scene.js');
      this.scene = new UniverseScene($('universe-stage'), {
        onSelect: obj => this.select(obj),
        onError: () => this.fallback(),
        onCanvasClick: () => {
          this.toggleDirectory(false);
          if (window.innerWidth < 1366) {
            this.collapseRightSidebar(true);
          }
        }
      });
      this.scene.setPaused(this.paused);
      this.stationFlight = new StationFlightController(this);
      this.scene.stationFlight = this.stationFlight;
    } catch (e) { console.error('Universe renderer unavailable', e); this.fallback(); }
    this.spiders = new SpiderMechaController(this);
    await this.restoreViewState();
    await this.loadSavedViews();
    await this.load();
  }

  collapseRightSidebar(collapsed = true) {
    const legend = $('neural-legend');
    if (!legend) return;
    if (collapsed) {
      legend.setAttribute('data-collapsed', 'true');
      $('neural-legend-toggle')?.setAttribute('aria-expanded', 'false');
    } else {
      legend.removeAttribute('data-collapsed');
      $('neural-legend-toggle')?.setAttribute('aria-expanded', 'true');
    }
  }

  fallback() {
    this.no3d = true; this.toggleDirectory(true);
    this.app.showToast('当前无法显示 3D。你仍可通过内容目录浏览和整理。', 'info');
  }

  updatePause() {
    $('universe-pause').textContent = this.paused ? '恢复公转' : '暂停公转';
    $('universe-pause').setAttribute('aria-pressed', String(this.paused));
  }

  toggleDirectory(force) {
    const open = force ?? $('universe-directory').hidden;
    $('universe-directory').hidden = !open;
    $('universe-directory-toggle').setAttribute('aria-expanded', String(open));
    if (open && window.innerWidth < 1366) {
      this.collapseRightSidebar(true);
    }
  }

  search() {
    clearTimeout(this.timer);
    if (!this.app.closeReaders()) return;
    this.app.showUniverse();
    this.query = $('universe-search').value.trim();
    if (this.query) this.toggleDirectory(true);
    this.load();
  }

  invalidateCache() {
    this.overviewCache.clear();
    this.tractorLineCache.clear();
  }

  // 「我的空间」= Agent 分区视图（两者已合并为同一视图）：
  // 中心「我的内容」（我写的笔记 + 关于我的记忆），各导入来源作为分区环绕在外圈。
  openMySpace() {
    this.app.showUniverse();
    this.toggleDirectory(false);
    this.perspective = 'agent'; this.updatePerspectiveUI();
    this.agentId = null; this.topicId = null; this.kind = 'all'; this.query = '';
    $('universe-search').value = ''; this.load();
  }

  async setScope(value, {closeReaders = true} = {}) {
    const scope = value.trim() || 'default';
    if (closeReaders && !this.app.closeReaders()) { $('universe-scope').value = this.scope; return false; }
    clearTimeout(this.timer); ++this.request; ++this.memberRequest;
    this.invalidateCache();
    this.scope = scope; $('universe-scope').value = scope; this.spiders?.setScope(scope);
    this.app.notesMgr.scopeInput.value = scope; this.app.memoryNet.scopeInput.value = scope;
    this.topicId = null; this.kind = 'all'; this.query = ''; this.data = null; this.currentTopic = null;
    $('universe-search').value = ''; $('universe-rename').hidden = true; $('universe-split').hidden = true; $('universe-list').replaceChildren();
    this.scene?.setData({overview: true, topics: [], items: []}, true);
    await this.load(); return true;
  }

  setPerspective(mode) {
    if (this.perspective === mode) return;
    this.perspective = mode;
    this.updatePerspectiveUI();
    this.agentId = null; this.topicId = null; this.query = '';
    this.toggleDirectory(false); this.load();
  }

  // 视角切换按钮（主题视界 / Agent 分区）已按需求移除；
  // perspective 仅作为内部状态保留（topic = 知识宇宙，agent = 我的空间）。
  updatePerspectiveUI() {}

  toggleTractorLines() {
    this.showTractorLines = !this.showTractorLines;
    $('universe-tractor-toggle').textContent = this.showTractorLines ? '牵引线: 开' : '牵引线: 关';
    $('universe-tractor-toggle').setAttribute('aria-pressed', String(this.showTractorLines));
    this.scene?.setTractorLinesVisible(this.showTractorLines);
  }

  navigateAgent(agentId, kind = 'all') {
    if (!this.app.closeReaders()) return;
    clearTimeout(this.timer); this.agentId = agentId; this.topicId = null; this.kind = kind; this.currentTopic = null;
    $('universe-rename').hidden = true; $('universe-split').hidden = true; this.query = ''; $('universe-search').value = ''; this.load();
  }

  openClusterDialog() {
    const list = $('universe-cluster-list');
    list.replaceChildren();
    if (!this.clusterSuggestions?.length) {
      list.textContent = '暂无符合条件的相似内容推荐（需 > 5 条）。';
      $('universe-cluster-dialog').showModal();
      return;
    }
    for (const s of this.clusterSuggestions) {
      const card = document.createElement('div');
      card.className = 'universe-cluster-card';
      const info = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = `【${s.title}】`;
      const badge = document.createElement('span');
      badge.textContent = `${s.count} 条相似内容`;
      info.append(title, badge);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'u-button u-primary';
      btn.textContent = '新建子主题归类';
      btn.onclick = async () => {
        btn.disabled = true;
        btn.textContent = '归类中…';
        try {
          await call(`/api/topics/${encodeURIComponent(this.topicId)}/cluster`, 'POST', {
            title: s.title,
            item_ids: s.item_ids,
            scope: this.scope
          });
          this.invalidateCache();
          this.app.showToast(`已成功创建子主题「${s.title}」并归类 ${s.count} 条内容！`, 'success');
          $('universe-cluster-dialog').close();
          $('universe-cluster-banner').hidden = true;
          this.load();
        } catch (e) {
          btn.disabled = false;
          btn.textContent = '新建子主题归类';
          this.app.showToast(e.message, 'error');
        }
      };
      card.append(info, btn);
      list.append(card);
    }
    $('universe-cluster-dialog').showModal();
  }

  async autoClusterCurrentPage() {
    if (this.autoClusterBusy) return;
    const topic = this.data?.topic;
    if (!this.topicId || !topic || topic.virtual || this.query) {
      this.app.showToast('一键子主题归类仅在具体主题页面且非搜索状态下生效。', 'info');
      return;
    }

    const items = this.data?.items || [];
    if (!items.length) {
      this.app.showToast('当前页面没有可归类的内容。', 'info');
      return;
    }

    // 仅对当前页面生效：扫描当前页面的 items，提取分类前缀
    const groups = new Map();
    for (const item of items) {
      const title = (item.title || '').trim();
      let key = null;
      const markerMatch = title.match(/【([^【】]{2,100})】/);
      if (markerMatch) {
        key = markerMatch[1].trim();
      } else {
        const sepMatch = title.match(/^([^｜|/—–-]{2,40})[｜|/—–-]/);
        if (sepMatch) {
          key = sepMatch[1].trim();
        }
      }
      if (key && key !== topic.title) {
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item.id);
      }
    }

    const candidates = [];
    for (const [key, ids] of groups.entries()) {
      if (ids.length >= 2) {
        candidates.push({ title: key, count: ids.length, item_ids: ids });
      }
    }
    candidates.sort((a, b) => b.count - a.count);

    if (!candidates.length) {
      this.app.showToast('当前页面未检测到可聚合的特征前缀（需至少 2 条包含【分类】或分类前缀的内容）。', 'info');
      return;
    }

    const totalCount = candidates.reduce((sum, c) => sum + c.count, 0);
    const summaryList = candidates.slice(0, 5).map(c => `• 「${c.title}」: ${c.count} 条`).join('\n');
    const moreText = candidates.length > 5 ? `\n… 等共 ${candidates.length} 组子主题` : '';
    const confirmMsg = `确定对当前主题「${topic.title}」进行一键子主题归类？\n\n检测到当前页面可新建归类：\n${summaryList}${moreText}\n\n共计 ${totalCount} 条内容将被移入对应子主题（仅对当前页面生效）。`;

    if (!window.confirm(confirmMsg)) return;

    this.autoClusterBusy = true;
    const autoBtn = $('universe-auto-cluster');
    const dirBtn = $('directory-auto-cluster');
    const origAutoText = autoBtn?.textContent;
    const origDirText = dirBtn?.textContent;
    if (autoBtn) { autoBtn.disabled = true; autoBtn.textContent = '正在归类…'; }
    if (dirBtn) { dirBtn.disabled = true; dirBtn.textContent = '归类中…'; }

    let createdCount = 0;
    let movedTotal = 0;
    const errors = [];

    try {
      for (const cand of candidates) {
        try {
          const res = await call(`/api/topics/${encodeURIComponent(this.topicId)}/cluster`, 'POST', {
            title: cand.title,
            item_ids: cand.item_ids,
            scope: this.scope,
          });
          createdCount++;
          movedTotal += res.moved_count ?? cand.count;
        } catch (err) {
          errors.push(`「${cand.title}」: ${err.message}`);
        }
      }

      this.invalidateCache();
      if (createdCount > 0) {
        this.app.showToast(`一键归类完成！已在当前主题下新建 ${createdCount} 个子主题，归纳 ${movedTotal} 条内容。${errors.length ? `(${errors.length} 组失败)` : ''}`, 'success');
        await this.load();
      } else {
        this.app.showToast(`归类未执行成功：${errors.join('；')}`, 'error');
      }
    } catch (e) {
      this.app.showToast(`归类异常：${e.message}`, 'error');
    } finally {
      this.autoClusterBusy = false;
      if (autoBtn) { autoBtn.disabled = false; autoBtn.textContent = origAutoText; }
      if (dirBtn) { dirBtn.disabled = false; dirBtn.textContent = origDirText; }
    }
  }

  navigate(id, kind = 'all') {
    if (!this.app.closeReaders()) return;
    clearTimeout(this.timer);
    if (!id) {
      this.agentId = null;
      this.perspective = 'topic';
    }
    this.topicId = id; this.kind = kind; this.currentTopic = null;
    $('universe-rename').hidden = true; $('universe-split').hidden = true;
    if ($('universe-auto-cluster')) $('universe-auto-cluster').hidden = true;
    if ($('directory-auto-cluster')) $('directory-auto-cluster').hidden = true;
    this.query = ''; $('universe-search').value = ''; this.load();
  }

  // 逐级退出：始终只退一步（子主题 → 父星球 → … → 宇宙总览；分区内一步回分区总览）。
  // 左上角品牌按钮与面包屑负责任意级跳转 / 一键回总览。
  stepBack() {
    if (!this.app.closeReaders()) return;
    this.toggleDirectory(false);
    if (this.query) {
      // 搜索态也算一层：先退出搜索，回到当前主题/总览
      this.query = ''; $('universe-search').value = '';
      this.load();
      return;
    }
    if (this.topicId) {
      if (this.agentId) { this.navigateAgent(this.agentId); return; }
      const parentId = this.data?.topic?.parent_id;
      if (parentId) {
        // 记录父星球锚点位置，供场景层做「飞回父星球轨道」的镜头过渡
        this.pendingEntryFrom = this.scene?.getAnchorPosition?.() || null;
        this.navigate(parentId);
      } else {
        this.navigate(null);
      }
      return;
    }
    if (this.agentId) { this.agentId = null; this.load(); return; }
    this.navigate(null);
  }

  setTelemetry(badge, message, progress = null) {
    const summary = $('universe-summary');
    if (summary) {
      summary.innerHTML = `
        <div class="tactical-telemetry-hud" role="status">
          <div class="tactical-gyro" title="知识星核状态">
            <div class="gyro-ring gyro-ring--outer"></div>
            <div class="gyro-ring gyro-ring--inner"></div>
            <div class="gyro-core"></div>
          </div>
          <div class="telemetry-stream">
            <span class="telemetry-badge">${badge}</span>
            <span class="telemetry-text">${message}</span>
          </div>
        </div>
      `;
      const gyro = summary.querySelector('.tactical-gyro');
      if (gyro) {
        gyro.onclick = (e) => {
          e.stopPropagation();
          this.scene?.triggerEmpRipple({ clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 });
        };
      }
    }

    const hud = $('universe-warp-loading');
    if (hud) {
      if (hud.hidden) {
        hud.hidden = false;
        hud.classList.remove('fade-out');
        if (!hud._boundEmp) {
          hud._boundEmp = true;
          hud.addEventListener('click', (e) => {
            this.scene?.triggerEmpRipple({ clientX: e.clientX, clientY: e.clientY });
          });
        }
      }
      const feed = $('warp-telemetry-feed');
      if (feed) {
        const line = document.createElement('div');
        line.className = 'telemetry-line';
        line.innerHTML = `<span class="msg">${message}</span>`;
        feed.appendChild(line);
        if (feed.children.length > 5) feed.removeChild(feed.children[0]);
      }
      if (progress !== null) {
        const bar = $('warp-progress-bar');
        if (bar) bar.style.width = `${Math.min(100, Math.max(5, progress))}%`;
      }
    }
  }

  dismissLoading() {
    const hud = $('universe-warp-loading');
    if (hud && !hud.hidden) {
      const bar = $('warp-progress-bar');
      if (bar) bar.style.width = '100%';
      hud.classList.add('fade-out');
      setTimeout(() => {
        hud.hidden = true;
        hud.classList.remove('fade-out');
      }, 350);
    }
  }

  async load() {
    this.spiders?.navigationChanged();
    $('universe-exit').hidden = this.perspective !== 'agent' && !this.topicId && !this.query && !this.agentId;
    const localSkills = this.topicId === '__local_skills';
    const request = ++this.request;
    this.setTelemetry('星图同步', '正在同步星系拓扑与知识节点…'); $('universe-notice').hidden = true;
    $('universe-cluster-banner').hidden = true;

    const isTopicOverview = !this.topicId && !this.query && !this.agentId && this.perspective === 'topic';
    const isAgentOverview = this.perspective === 'agent' && !this.agentId && !this.topicId && !this.query;
    const cacheKey = isTopicOverview ? `${this.scope}__topic` : isAgentOverview ? `${this.scope}__agent` : null;

    if (cacheKey && this.overviewCache.has(cacheKey)) {
      const cached = this.overviewCache.get(cacheKey);
      this.data = cached;
      const cachedLines = this.tractorLineCache.get(this.scope);
      if (cachedLines) this.scene?.setTractorLines(cachedLines);
      this.render();
      return;
    }

    try {
      if (!isAgentOverview) {
        // 内容级牵引线（note/topic 关联）只在主题/分区视图使用；
        // Agent 总览使用基于共享主题的 AGENT 牵引线（下方生成），不重复加载。
        // 我的空间（全部内容）聚类视图同理：scene 端按分区共享主题生成 AGENT 线。
        const isAllNotesCluster = this.topicId === 'all' && !this.query && !this.agentId;
        if (!isAllNotesCluster) {
          call(`/api/universe/tractor-lines?scope=${encodeURIComponent(this.scope)}&limit=300`).then(tRes => {
            if (tRes?.lines) {
              this.tractorLineCache.set(this.scope, tRes.lines);
              if (request === this.request) this.scene?.setTractorLines(tRes.lines);
            }
          }).catch(() => {});
        }
      }

      let data;
      if (isAgentOverview) {
        const agentsRes = await call(`/api/universe/agents?scope=${encodeURIComponent(this.scope)}`);
        if (request !== this.request) return;
        const agents = agentsRes.agents || [];
        const counts = {topics: 0, notes: 0, memories: 0};
        for (const a of agents) { counts.notes += a.notes_count; counts.memories += a.memories_count; counts.topics += a.topic_count; }
        data = {
          overview: true, mode: 'agent', agents, topics: [], items: [],
          total: agents.length, counts, breadcrumbs: []
        };
        // 牵引线：Agent 之间的相关内容关联 = 共享主题（两个分区都贡献过同一主题）
        const lines = [];
        for (let i = 0; i < agents.length; i++) {
          for (let j = i + 1; j < agents.length; j++) {
            const shared = (agents[i].topic_ids || []).filter(t => (agents[j].topic_ids || []).includes(t));
            if (shared.length) {
              lines.push({from_id: agents[i].id, to_id: agents[j].id, type: 'AGENT', shared: shared.length});
            }
          }
        }
        this.scene?.setTractorLines(lines);
      } else {
        const params = new URLSearchParams({scope: this.scope, kind: this.kind});
        if (this.topicId && !localSkills) params.set('topic_id', this.topicId);
        if (this.query && !localSkills) params.set('query', this.query);
        if (this.agentId) params.set('agent_id', this.agentId);

        data = await collectUniverse(params, q => call(`/api/universe?${q}`), () => request === this.request, (loaded, total) => {
          const pct = Math.round(20 + (loaded / Math.max(1, total)) * 50);
          this.setTelemetry('突触解析', `解析知识节点拓扑 [${loaded}/${total}]…`, pct);
        });
        if (!data || request !== this.request) return;
        if (!localSkills) data.topics = await collectTopicPreviews(data.topics, this.scope, q => call(`/api/universe?${q}`), () => request === this.request);
        if (localSkills || (!this.topicId && !this.query && !this.agentId)) {
          this.setTelemetry('节点校准', '空间站中继与旗舰节点拓扑校准就绪…', 85);
          try {
            const catalogue = await call('/api/local-skills');
            if (request !== this.request) return;
            const original = data.topics.find(skillTopic);
            const { flagships, allItems, directoryItems } = buildDomainFleets(catalogue.items);
            const fullList = directoryItems || allItems;
            const skillPlanet = {
              id: '__local_skills',
              kind: 'topic',
              title: 'Skills',
              count: catalogue.items.length,
              child_count: flagships.length,
              children: allItems,
              virtual: true
            };
            if (localSkills) {
              const query = this.query.toLocaleLowerCase();
              data.relatedTopic = original;
              data.topic = skillPlanet;
              data.topics = [];
              data.sceneItems = query
                ? fullList.filter(i => `${i.title} ${i.description} ${(i.tags || []).join(' ')}`.toLocaleLowerCase().includes(query)).slice(0, 300)
                : allItems;
              data.items = fullList.filter(i => !query || `${i.title} ${i.description} ${(i.tags || []).join(' ')} ${(i.fused_from || []).join(' ')}`.toLocaleLowerCase().includes(query));
              data.total = data.items.length;
              data.breadcrumbs = [skillPlanet];
              data.catalogueWarnings = catalogue.warnings;
              data.aasStats = catalogue.stats;
            } else {
              const index = data.topics.indexOf(original);
              if (index >= 0) data.topics[index] = skillPlanet;
              else { data.topics.push(skillPlanet); data.total++; data.counts.topics++; }
            }
          } catch (e) {
            console.error('SKILLS ERROR:', e);
            if (localSkills) throw e;
            data.catalogueWarnings = ['本机技能清单暂时无法读取，请刷新重试。'];
          }
        }
      }

      if (this.topicId && this.topicId !== 'all' && this.topicId !== 'unclassified' && !localSkills) {
        call(`/api/topics/${encodeURIComponent(this.topicId)}/cluster-suggestions?scope=${encodeURIComponent(this.scope)}&threshold=5`).then(cRes => {
          if (request === this.request) {
            this.clusterSuggestions = cRes?.suggestions || [];
            if (this.clusterSuggestions.length) {
              $('universe-cluster-msg').textContent = `检测到 ${this.clusterSuggestions.length} 组相似内容（均 > 5条，如「${this.clusterSuggestions[0].title}」等 ${this.clusterSuggestions[0].count} 条），建议归类为子主题。`;
              $('universe-cluster-banner').hidden = false;
            } else {
              $('universe-cluster-banner').hidden = true;
            }
          }
        }).catch(() => {});
      }

      if (request !== this.request) return;
      if (cacheKey) this.overviewCache.set(cacheKey, data);
      this.data = data; this.render();
    } catch (e) {
      this.dismissLoading();
      if (request !== this.request) return;
      this.data = null; $('universe-list').replaceChildren();
      this.scene?.setData({title: '暂时无法载入', overview: true, topics: [], items: []});
      $('universe-summary').textContent = '载入失败，已有资料仍保留。';
      const notice = $('universe-notice'); notice.replaceChildren(document.createTextNode(e.message));
      const retry = document.createElement('button'); retry.className = 'u-button'; retry.textContent = '重试';
      retry.onclick = () => this.load(); notice.append(retry); notice.hidden = false;
    }
  }

  render() {
    const d = this.data, topics = d.topics || [], items = d.items || [], agents = d.agents || [];
    this.currentTopic = d.topic;
    const isAgentOverview = d.mode === 'agent' && d.overview;
    const title = this.query ? `搜索：${this.query}`
      : isAgentOverview ? '我的空间'
      : d.topic?.title
      || (this.agentId ? (this.agentId === 'user' ? '我的空间 · 个人内容' : (d.agent_name || this.agentId))
      : (this.topicId === 'all' ? '我的空间' : this.topicId === 'unclassified' ? '未归类的想法' : '知识宇宙'));

    $('universe-title').textContent = title;
    $('universe-rename').hidden = !d.topic || d.topic.virtual || !!this.query;
    // 拆分散落：仅当当前是子主题（有父主题/所在行星）时可用
    $('universe-split').hidden = !d.topic || d.topic.virtual || !!this.query || !d.topic.parent_id;
    const canAutoCluster = Boolean(d.topic && !d.topic.virtual && !this.query && items.length >= 2);
    if ($('universe-auto-cluster')) $('universe-auto-cluster').hidden = !canAutoCluster;
    if ($('directory-auto-cluster')) $('directory-auto-cluster').hidden = !canAutoCluster;
    $('universe-skill-related').hidden = !d.relatedTopic;
    const warnings = $('universe-catalogue-warnings'); warnings.hidden = !d.catalogueWarnings?.length;
    $('universe-catalogue-warning-text').textContent = (d.catalogueWarnings || []).join('\n');
    const count = topics.length + items.length + agents.length;
    const c = d.counts || {};
    $('neural-overview').setAttribute('aria-current', !this.topicId && !this.query && !this.agentId && this.perspective === 'topic' ? 'page' : 'false');
    $('workspace-universe').classList.toggle('neural-branch', !!this.topicId || !!this.query || !!this.agentId);

    const legend = $('neural-legend-items'); legend.replaceChildren();
    const agentGrouped = this.topicId === 'all' && !this.query && !this.agentId && !topics.length && items.length >= 30;
    if (agentGrouped) {
      // 我的空间：按来源分区聚类 —— 中心眼球（个人内容）+ 各导入分区环绕
      // 图例面板现在只承载机甲观察，标题固定；这里不再改 h2，也不再控制面板显隐
      const lColors = {user: '#f4c069', antigravity: '#a78bfa', codex: '#38bdf8', doubao: '#34d399', pangu: '#f87171', deepseek: '#60a5fa', qwen: '#fbbf24', external: '#fb923c'};
      const lNames = {user: '我的内容', antigravity: 'Antigravity', codex: 'Codex', doubao: '豆包导入', pangu: '盘古导入', deepseek: 'DeepSeek Harness', qwen: 'Qwen', external: '其他导入'};
      const normalizeAgent = (author, title = '') => {
        if (!author) return 'external';
        const lower = String(author).toLowerCase();
        if (lower.includes('deepseek') || lower.includes('harness')) return 'deepseek';
        if (lower.includes('antigravity')) return 'antigravity';
        if (lower.includes('codex')) return 'codex';
        if (lower.includes('qwen')) return 'qwen';
        if (lower.includes('doubao')) return 'doubao';
        if (lower.includes('pangu')) return 'pangu';
        if (author === 'user' || author === 'operator') {
          if (title && String(title).startsWith('【')) return 'external';
          return 'user';
        }
        return 'external';
      };
      const lCounts = {};
      for (const it of items) { const k = normalizeAgent(it.author, it.title); lCounts[k] = (lCounts[k] || 0) + 1; }
      const lOrder = ['user', 'antigravity', 'codex', 'doubao', 'pangu', 'deepseek', 'qwen', 'external'];
      for (const k of lOrder) {
        if (!lCounts[k]) continue;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'neural-legend-entry';
        button.onclick = () => this.navigateAgent(k);
        const dot = document.createElement('i'); dot.style.background = lColors[k] || '#a78bfa'; dot.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span'); name.textContent = lNames[k] || k; name.title = lNames[k] || k;
        const countEl = document.createElement('b'); countEl.textContent = lCounts[k];
        button.append(dot, name, countEl); legend.append(button);
      }
    } else if (isAgentOverview) {
      const agentColors = {user: '#f4c069', antigravity: '#a78bfa', codex: '#38bdf8', pangu: '#f87171', doubao: '#34d399', deepseek: '#60a5fa', qwen: '#fbbf24', external: '#fb923c'};
      for (const agent of agents) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'neural-legend-entry';
        button.onclick = () => this.navigateAgent(agent.id);
        const dot = document.createElement('i');
        dot.style.background = agentColors[agent.id] || '#a78bfa';
        dot.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span'); name.textContent = agent.name; name.title = agent.name;
        const countEl = document.createElement('b'); countEl.textContent = agent.total_items ?? 0;
        button.append(dot, name, countEl); legend.append(button);
      }
    } else {
      for (const topic of topics) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'neural-legend-entry';
        button.onclick = () => this.select({...topic, kind: 'topic'});
        const dot = document.createElement('i'); dot.style.background = neuralColor(topic.id); dot.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span'); name.textContent = topic.title; name.title = topic.title;
        const countEl = document.createElement('b'); countEl.textContent = topic.count ?? 0;
        button.append(dot, name, countEl); legend.append(button);
      }
    }

    if (isAgentOverview) {
      $('universe-summary').textContent = `「我的空间」+ ${Math.max(0, agents.length - 1)} 个导入分区 · ${c.notes ?? 0} 条笔记 · ${c.memories ?? 0} 条 AI 记忆`;
      $('universe-list-status').textContent = '「我的空间」放你自己写的笔记与关于你的记忆；各导入分区收纳历次导入资料，点击进入浏览';
    } else if (this.topicId === '__local_skills' && d.aasStats) {
      $('universe-summary').textContent = `AAS Core v19.8.0 · 16 大领域舰队 · 共 ${d.total} 项战备节点（已跳过重复 ${d.aasStats.skipped_duplicates} 项 · 融合相似 ${d.aasStats.fused_skills} 项）`;
      $('universe-list-status').textContent = `AAS 哲学：Agent 检视并选择 · AAS 验证并记录 · 你掌控最终激活`;
    } else {
      $('universe-summary').textContent = this.topicId || this.query || this.agentId ? `${d.total} 个结果 · 全部显示${this.query ? ' · 搜索当前范围内全部主题和内容' : ''}` : `${c.topics ?? topics.length} 个主题 · ${c.notes ?? 0} 条笔记 · ${c.memories ?? 0} 条 AI 记忆`;
      $('universe-list-status').textContent = `共 ${d.total} 项 · ${this.query ? '关键词匹配' : '按当前层级浏览'}`;
    }

    const path = $('universe-path'); path.replaceChildren();
    const home = document.createElement('button'); home.textContent = '知识宇宙';
    home.onclick = () => { this.toggleDirectory(false); this.agentId = null; this.perspective = 'topic'; this.navigate(null); };
    path.append(home);
    const crumbs = d.breadcrumbs || [];
    for (let ci = 0; ci < crumbs.length; ci++) {
      const crumb = crumbs[ci];
      const sep = document.createElement('span'); sep.textContent = '/';
      const isCurrent = ci === crumbs.length - 1;
      const b = isCurrent
        ? (() => { const s = document.createElement('span'); s.textContent = crumb.title; s.className = 'universe-path-current'; s.setAttribute('aria-current', 'page'); return s; })()
        : (() => { const btn = document.createElement('button'); btn.textContent = crumb.title; btn.onclick = () => {
            this.toggleDirectory(false);
            if (crumb.agent_id) {
              this.navigateAgent(crumb.agent_id);
            } else {
              this.navigate(crumb.id);
            }
          }; return btn; })();
      path.append(sep, b);
    }

    const list = $('universe-list'); list.replaceChildren();
    const rawDisplayEntries = isAgentOverview ? agents.map(a => ({...a, kind: 'agent'})) : [...topics.map(t => ({...t, kind: 'topic'})), ...items];
    // Cap initial unsearched directory list at 450 rows in Skills view for instant DOM rendering
    const displayEntries = (this.topicId === '__local_skills' && !this.query && rawDisplayEntries.length > 450)
      ? rawDisplayEntries.slice(0, 450)
      : rawDisplayEntries;
    for (const obj of displayEntries) {
      const row = document.createElement('div'); row.className = 'universe-entry'; row.dataset.id = obj.id; row.dataset.kind = obj.kind;
      const open = document.createElement('button'); open.className = 'universe-entry-main';
      const name = document.createElement('strong'); name.textContent = cleanTitle(obj.title || obj.name); name.title = obj.title || obj.name;
      const meta = document.createElement('span');
      if (obj.kind === 'agent') {
        // 来源分区条目：彩色圆点 + 分区名 + 内容统计（我的空间金色核心样式）
        const dot = document.createElement('i');
        dot.className = 'universe-agent-dot';
        const agentDotColors = {user: '#f4c069', antigravity: '#a78bfa', codex: '#38bdf8', doubao: '#34d399', deepseek: '#60a5fa', qwen: '#fbbf24', external: '#fb923c', pangu: '#f87171'};
        dot.style.background = agentDotColors[obj.id] || '#a78bfa';
        dot.setAttribute('aria-hidden', 'true');
        open.append(dot);
        name.textContent = obj.name || obj.title;
        if (obj.id === 'user') {
          const core = document.createElement('em'); core.className = 'universe-agent-core'; core.textContent = '我的内容 · 我写的笔记 + 关于我的记忆';
          meta.textContent = `${obj.total_items ?? 0} 条内容`;
          meta.append(core);
        } else {
          meta.textContent = `${obj.total_items ?? 0} 条内容 · ${obj.topic_count ?? 0} 个主题`;
        }
      } else if (obj.kind === 'topic') {
        // 子主题数为 0 时不显示：当前 27 个主题几乎全为 0，写出来只是噪声。
        const childTxt = (obj.child_count ?? 0) > 0 ? ` · ${obj.child_count} 个子主题` : '';
        meta.textContent = `主题 · ${obj.count ?? 0} 条内容${childTxt}${obj.previewError ? ' · 内部预览未载入，可进入重试' : ''}`;
      } else if (obj.isFlagship) {
        meta.textContent = `🛸 大舰队旗舰 · 统辖 ${obj.fleetCount || 0} 艘护航舰`;
        open.classList.add('universe-entry-flagship');
      } else if (obj.kind === 'mcp') {
        const source = obj.locations?.[0]?.source || '本地 MCP';
        const toolCount = obj.tools?.length;
        const dim = obj.capability_dimension ? ` · [${obj.capability_dimension}]` : '';
        meta.textContent = `🛸 MCP 战备服务${toolCount ? ` · ${toolCount} 个工具` : ''}${dim} · ${source}`;
        open.classList.add('universe-entry-mcp');
      } else {
        const rawText = `${obj.title || ''} ${obj.excerpt || ''}`;
        let mediaTag = '';
        if (/聊天原文|聊天摘要|### 消息 /.test(rawText)) mediaTag = '💬 对话 · ';
        else if (/\.pdf\b|kind:\s*pdf|📄 PDF/i.test(rawText)) mediaTag = '📄 PDF · ';
        else if (/\.(mp4|webm|mov)\b|kind:\s*video|🎬 视频/i.test(rawText)) mediaTag = '🎬 视频 · ';
        else if (/\.(png|jpe?g|jfif|gif|webp|svg)\b|kind:\s*image|🖼️ 图片|附件引用/i.test(rawText)) mediaTag = '🖼️ 图片 · ';
        meta.textContent = `${mediaTag}${typeName(obj.kind)}${obj.kind === 'memory' && obj.node_type ? ` · ${obj.node_type}` : ''} · v${obj.revision ?? 1}`;
        if (obj.author) { meta.append(' · '); meta.append(sourceBadge(obj.author)); }
      }
      if (obj.kind === 'skill' && !obj.isFlagship) {
        const fuseBadge = obj.fused_from?.length > 1 ? `🔗 融合(${obj.fused_from.length}) · ` : '';
        const riskBadge = obj.risk ? `[${obj.risk.toUpperCase()}] · ` : '';
        const srcText = [...new Set((obj.locations || []).map(l => l.source))].join(' · ');
        meta.textContent = obj.isEscort ? `⚔️ ${fuseBadge}${riskBadge}${srcText}` : `${fuseBadge}${riskBadge}${srcText}`;
      }
      open.append(name, meta);
      if ((obj.kind === 'note' || obj.kind === 'memory') && obj.excerpt) {
        const cleanedExcerpt = String(obj.excerpt)
          .replace(/<!--[\s\S]*?-->/g, '')
          .replace(/^会话\s+[0-9a-f\-]+；类别[^\n]+\n(?:来源性质[^\n]+\n)?(?:正文为可见文字[^\n]+\n)?(?:副本全文[^\n]+\n)?/m, '')
          .replace(/#+ /g, '')
          .replace(/\s+/g, ' ')
          .trim();
        if (cleanedExcerpt) {
          const ex = document.createElement('small');
          ex.className = 'universe-entry-excerpt';
          ex.textContent = cleanedExcerpt.slice(0, 110);
          open.append(ex);
        }
      }
      open.onclick = () => this.select(obj, {fromDirectory: true});
      row.append(open);
      if (obj.kind === 'note' || obj.kind === 'memory') {
        const edit = document.createElement('button'); edit.className = 'universe-entry-manage'; edit.textContent = '整理'; edit.setAttribute('aria-label', `整理：${obj.title}`); edit.onclick = () => this.openMembership(obj); row.append(edit);
        if (this.query && obj.topics?.length) {
          const locate = document.createElement('button'); locate.className = 'universe-entry-manage'; locate.textContent = '定位'; locate.title = obj.topics.map(t => t.title).join('、'); locate.onclick = () => { this.toggleDirectory(false); this.navigate(obj.topics[0].id); }; row.append(locate);
        }
      }
      list.append(row);
    }
    if (!count) {
      const empty = document.createElement('p'); empty.className = 'universe-empty';
      empty.textContent = this.query ? '没有匹配内容。换个关键词再试。'
        : isPersonalSpace(this) ? '这里只放你自己创建的内容（点击右上角「添加资料」新建笔记）。历次导入的资料已收纳在「导入资料」中，不会混入这里。'
        : this.agentId ? '该导入分区暂无内容。'
        : d.topic ? '这个主题还没有内容。到“全部内容”点击“整理”，即可加入。' : '这里还没有主题或内容。可以新建主题，或从“全部内容”开始整理。';
      list.append(empty); this.toggleDirectory(true);
    }
    const sceneItems = d.sceneItems || items;
    this.scene?.setData({
      id: d.topic?.id, title, topic: d.topic, overview: !d.topic && !this.agentId, mode: isAgentOverview ? 'agent' : 'topic',
      notesCount: c.notes ?? 0, agents,
      anchor: (!d.topic?.virtual && d.topic?.parent_id) ? {id: d.topic.parent_id, title: (d.breadcrumbs || []).find(c => !c.agent_id && c.id === d.topic.parent_id)?.title || d.topic.parent_title || '父星球'} : null,
      topics: topics.map(t => ({...t, title: cleanTitle(t.title), children: t.children?.map(i => ({...i, title: cleanTitle(i.title)}))})),
      items: sceneItems.map(i => ({...i, title: cleanTitle(i.title)}))
    });
    this.scene?.setPaused(this.paused);
    if (this.pendingEntryFrom) {
      const from = this.pendingEntryFrom; this.pendingEntryFrom = null;
      this.scene?.enterFromPosition?.(from);
    }
    this.dismissLoading();
  }

  select(obj, {fromDirectory = false} = {}) {
    if (obj.kind === 'agent') {
      if (!fromDirectory) this.toggleDirectory(false);
      // 「我的空间」分区 = 我写的笔记 + 关于我的记忆（混排）；导入分区 = 该来源的资料。
      this.navigateAgent(obj.id);
      return;
    }
    if (obj.kind === 'skill') {
      if (obj.isFlagship) {
        if (this.scene) {
          this.scene.focus(obj.id, 'skill');
        }
        if (!fromDirectory) {
          this.app.showToast(`已锁定【${obj.title}】旗舰母舰（编队规模：${obj.fleetCount || 0} 艘护航舰）`, 'info');
          return;
        }
        $('universe-skill-title').textContent = obj.title;
        $('universe-skill-description').textContent = `领域大舰队旗舰母舰（AAS Core v19.8.0 编队标准）。\n统领该领域 ${obj.fleetCount || 0} 艘护航战舰（技能与 MCP 节点）。`;
        const locations = $('universe-skill-locations'); locations.replaceChildren();
        const item = document.createElement('p'); item.textContent = `战术代号: ${obj.code || 'FLAGSHIP'}\n编队成员: ${obj.fleetCount || 0} 艘护航战舰 · 规范文件: SKILL.md`; locations.append(item);
        const dlBtn = $('universe-skill-download');
        if (dlBtn) dlBtn.hidden = true;
        $('universe-skill-dialog').showModal();
        return;
      }
      if (!this.app.closeReaders()) return;
      $('universe-skill-title').textContent = obj.title;
      let descText = obj.description || '该技能没有提供简介。';
      const aasMetaLines = [];
      if (obj.domain) aasMetaLines.push(`• 所属舰队: ${obj.domain} (${obj.aas_category || 'general'})`);
      if (obj.capability_dimension) aasMetaLines.push(`• 能力维度: ${obj.capability_dimension}`);
      if (obj.risk) aasMetaLines.push(`• 风险等级: ${String(obj.risk).toUpperCase()} · 来源: ${obj.source || 'local'}`);
      if (obj.fused_from?.length > 1) aasMetaLines.push(`• 高度相似融合来源: ${obj.fused_from.join(' + ')}`);
      if (obj.tags?.length) aasMetaLines.push(`• 标签: ${obj.tags.join(', ')}`);
      if (obj.trust_boundary) aasMetaLines.push(`• 信任边界: ${obj.trust_boundary}`);
      if (aasMetaLines.length) {
        descText += `\n\n【AAS Core v19.8.0 设计哲学契约】\n${aasMetaLines.join('\n')}`;
      }
      $('universe-skill-description').textContent = descText;
      const locations = $('universe-skill-locations'); locations.replaceChildren();
      for (const location of obj.locations || []) { const item = document.createElement('p'); item.textContent = `${location.source}\n${location.path}`; locations.append(item); }
      const dlBtn = $('universe-skill-download');
      if (dlBtn) {
        const rawSkillId = String(obj.id || '').replace(/^skill:/, '') || obj.title;
        dlBtn.hidden = false;
        dlBtn.onclick = () => {
          const a = document.createElement('a');
          a.href = `/api/local-skills/${encodeURIComponent(rawSkillId)}/download`;
          a.download = `${rawSkillId}-skill-bundle.zip`;
          document.body.append(a);
          a.click();
          a.remove();
          this.app.showToast(`正在下载完整技能包「${rawSkillId}.zip」…`, 'info');
        };
      }
      $('universe-skill-dialog').showModal(); return;
    }
    if (obj.kind === 'mcp') {
      if (this.scene && !fromDirectory) {
        this.scene.focus(obj.id, 'mcp');
        this.app.showToast(`已锁定达斯·维达座机【${obj.title || obj.name}】（${obj.tools?.length || 0} 项工具）`, 'info');
        return;
      }
      if (!this.app.closeReaders()) return;
      $('universe-skill-title').textContent = obj.title || obj.name;
      let desc = obj.description || '本地 MCP 战备服务（Model Context Protocol）';
      if (obj.tools && obj.tools.length) {
        desc += '\n\n【已挂载 MCP 工具能力】\n' + obj.tools.map(t => `• ${t.name}: ${t.description || '无详细描述'}`).join('\n');
      }
      if (obj.trust_boundary) {
        desc += `\n\n【AAS Core v19.8.0 控制平面哲学】\n• 所属舰队: ${obj.domain || '开发者效能'}\n• 能力维度: ${obj.capability_dimension || 'external integrations'}\n• 风险等级: ${(obj.risk || 'low').toUpperCase()}\n• 信任边界: ${obj.trust_boundary}`;
      }
      $('universe-skill-description').textContent = desc;
      const locations = $('universe-skill-locations'); locations.replaceChildren();
      for (const location of obj.locations || []) {
        const item = document.createElement('p');
        item.textContent = `${location.source}\n${location.path || ''}`;
        locations.append(item);
      }
      const dlBtn = $('universe-skill-download');
      if (dlBtn) dlBtn.hidden = true;
      $('universe-skill-dialog').showModal();
      return;
    }
    if (obj.kind === 'notes-hub') {
      // 金色核心（知识宇宙中心）→ 进入「我的空间」（Agent 分区视图）。
      this.openMySpace();
      return;
    }
    if (obj.kind === 'topic') {
      if (!fromDirectory) this.toggleDirectory(false);
      if (this.topicId === obj.id) {
        this.scene?.resetView();
        this.app.showToast('已对齐母港中枢全景视角', 'info');
        return;
      }
      this.navigate(obj.id);
      return;
    }
    if (obj.kind === 'note') this.app.jumpToNote(obj.id); else this.app.jumpToMemory(obj.id);
  }

  openTopicForm(rename) {
    this.formTopic = rename ? {...this.currentTopic} : null;
    this.formParent = rename ? null : this.currentTopic?.id || null;
    $('universe-topic-form-title').textContent = rename ? '重命名主题' : this.formParent ? '新建子主题' : '新建主题';
    $('universe-topic-context').textContent = rename ? '名称改变不会修改其中的内容。' : this.formParent ? `将创建在「${this.currentTopic.title}」中。` : '在宇宙总览中创建一个新的太阳系。';
    $('universe-topic-name').value = rename ? this.currentTopic.title : ''; $('universe-topic-error').textContent = ''; $('universe-topic-dialog').showModal(); $('universe-topic-name').focus();
  }

  async saveTopic() {
    if (this.busy) return; const title = $('universe-topic-name').value.trim(); if (!title) return;
    this.busy = true; $('universe-topic-save').disabled = true; $('universe-topic-cancel').disabled = true;
    try {
      const topic = this.formTopic ? await call(`/api/topics/${encodeURIComponent(this.formTopic.id)}`, 'PATCH', {scope: this.scope, title, expected_revision: this.formTopic.revision}) : await call('/api/topics', 'POST', {scope: this.scope, title, parent_id: this.formParent});
      this.invalidateCache();
      $('universe-topic-dialog').close(); this.app.showToast('主题已保存。', 'success'); this.navigate(topic.id);
    } catch (e) { $('universe-topic-error').textContent = e.message; }
    finally { this.busy = false; $('universe-topic-save').disabled = false; $('universe-topic-cancel').disabled = false; }
  }

  async splitCurrentTopic() {
    const topic = this.currentTopic;
    if (!topic || !topic.parent_id) return;
    if (this.splitBusy) return;
    if (!window.confirm(`拆分散落「${topic.title}」？\n\n它的成员笔记/记忆会全部散落到当前所在的行星（「${topic.parent_title || '父主题'}」）内，空出来的子主题会被删除。\n\n笔记原文不会丢失，之后随时可以重新归类。`)) return;
    this.splitBusy = true;
    try {
      const res = await call(`/api/topics/${encodeURIComponent(topic.id)}/split`, 'POST', {scope: this.scope});
      this.invalidateCache();
      this.app.showToast(`已拆散「${res.split_title}」：${res.moved} 条内容散落进「${res.parent.title}」`, 'success');
      this.navigate(res.parent.id);
    } catch (e) {
      this.app.showToast(`拆分失败：${e.message}`, 'error');
    } finally {
      this.splitBusy = false;
    }
  }

  async openMembership(obj) {
    this.memberObject = obj; const token = this.memberRequest = (this.memberRequest || 0) + 1;
    const dialog = $('universe-membership-dialog'); $('universe-membership-item').textContent = obj.title; $('universe-membership-error').textContent = ''; $('universe-membership-list').textContent = '正在读取主题…'; dialog.showModal();
    try {
      const topics = []; let offset = 0; let result;
      do { result = await call(`/api/topics?${new URLSearchParams({scope: this.scope, offset, limit: 500})}`); topics.push(...result.topics); offset += result.topics.length; if (!result.topics.length) break; } while (offset < result.total);
      if (token !== this.memberRequest || !dialog.open) return;
      this.memberTopics = topics; this.memberIds = new Set(obj.topic_ids || obj.topics?.map(t => t.id) || []); this.renderMembership();
    } catch (e) { if (token === this.memberRequest) $('universe-membership-error').textContent = e.message; }
  }

  renderMembership() {
    const list = $('universe-membership-list'); list.replaceChildren();
    if (!this.memberTopics.length) { list.textContent = '还没有主题。关闭此面板后，先新建一个主题。'; return; }
    for (const t of this.memberTopics) {
      const row = document.createElement('div'); row.className = 'universe-membership-row'; const label = document.createElement('span'); const parent = this.memberTopics.find(p => p.id === t.parent_id); label.textContent = parent ? `${parent.title} / ${t.title}` : t.title;
      const button = document.createElement('button'); const belongs = this.memberIds.has(t.id); button.className = `u-button ${belongs ? 'is-member' : ''}`; button.textContent = belongs ? '移出散放' : '加入主题'; button.dataset.topicId = t.id; button.onclick = () => this.mutateMembership(t, belongs); row.append(label, button); list.append(row);
    }
  }

  async mutateMembership(topic, remove) {
    if (this.memberBusy) return; this.memberBusy = true; const obj = this.memberObject; $('universe-membership-error').textContent = ''; $('universe-membership-close').disabled = true;
    for (const b of $('universe-membership-list').querySelectorAll('button')) b.disabled = true;
    try {
      const updated = await call(`/api/topics/${encodeURIComponent(topic.id)}/members`, remove ? 'DELETE' : 'POST', {scope: this.scope, kind: obj.kind, object_id: obj.id, expected_revision: topic.revision});
      this.invalidateCache();
      Object.assign(topic, updated); if (remove) this.memberIds.delete(topic.id); else this.memberIds.add(topic.id);
      this.app.showToast(remove ? '已移出主题，原文保留。' : '已加入主题。', 'success'); await this.load();
    } catch (e) { $('universe-membership-error').textContent = e.message; }
    finally { this.memberBusy = false; $('universe-membership-close').disabled = false; this.renderMembership(); }
  }

  async restoreViewState() {
    try {
      // 后端契约是 workspace_id / owner_id（见 api.js 的 fetchViewState），
      // 原来这里传 view_id，FastAPI 因缺少必填参数直接返回 422。
      const res = await call(`/api/view-state?${new URLSearchParams({
        workspace_id: 'universe-main', owner_id: 'local-owner', scope: this.scope,
      })}`);
      if (!res) return;
      this.viewStateRevision = Number.isSafeInteger(res.revision) ? res.revision : 0;
      // 后端的 ViewState 只承载视角（viewport）与节点位置，没有筛选字段；
      // 筛选条件从前端本地存储恢复，避免读不到就静默丢失。
      let filters = {};
      try { filters = JSON.parse(localStorage.getItem('universe-view-filters') || '{}') || {}; } catch {}
      if (filters.topic_id) this.topicId = filters.topic_id;
      if (filters.agent_id) this.agentId = filters.agent_id;
      if (filters.perspective) this.perspective = filters.perspective;
      if (typeof filters.show_tractor_lines === 'boolean') {
        this.showTractorLines = filters.show_tractor_lines;
        const btn = $('universe-tractor-toggle');
        if (btn) {
          btn.textContent = this.showTractorLines ? '牵引线: 开' : '牵引线: 关';
          btn.setAttribute('aria-pressed', String(this.showTractorLines));
        }
      }
    } catch {}
  }

  async saveCurrentViewState() {
    try {
      const cam = this.scene?.camera?.position;
      const zoom = cam ? Number(cam.length().toFixed(2)) : 1.0;
      const pan = cam ? { x: Number(cam.x.toFixed(2)), y: Number(cam.y.toFixed(2)) } : { x: 0, y: 0 };
      const filters = {
        topic_id: this.topicId || null,
        agent_id: this.agentId || null,
        perspective: this.perspective || 'topic',
        show_tractor_lines: !!this.showTractorLines,
      };
      // 后端 ViewState 只接受 schema_version / viewport / node_positions /
      // pinned_nodes / collapsed_groups / selected_node_id（extra='forbid'），
      // 所以视角走后端，筛选条件存本机。
      try { localStorage.setItem('universe-view-filters', JSON.stringify(filters)); } catch {}
      const res = await call('/api/view-state', 'PUT', {
        owner_id: 'local-owner',
        workspace_id: 'universe-main',
        scope: this.scope,
        expected_revision: Number.isSafeInteger(this.viewStateRevision) ? this.viewStateRevision : 0,
        state: {
          schema_version: 'viewstate-v1',
          viewport: { x: pan.x, y: pan.y, zoom: Math.min(100, Math.max(0.01, zoom)) },
        },
      });
      if (res && Number.isSafeInteger(res.revision)) this.viewStateRevision = res.revision;
      this.app.showToast('已保存当前视角；筛选条件保存在本机。', 'success');
    } catch (e) {
      this.app.showToast(`保存布局失败：${e.message}`, 'error');
    }
  }

  async loadSavedViews() {
    const listEl = $('saved-views-list');
    if (!listEl) return;
    listEl.replaceChildren();
    try {
      const res = await call(`/api/saved-views?scope=${encodeURIComponent(this.scope)}&limit=50`);
      const items = res.items || [];
      if (!items.length) {
        const hint = document.createElement('span');
        hint.className = 'settings-hint';
        hint.style.fontSize = '11px';
        hint.textContent = '暂无保存视图';
        listEl.append(hint);
        return;
      }
      for (const view of items) {
        const pill = document.createElement('div');
        pill.style.display = 'inline-flex';
        pill.style.alignItems = 'center';
        pill.style.gap = '4px';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'u-button';
        btn.style.fontSize = '11px';
        btn.style.padding = '2px 8px';
        btn.textContent = `📌 ${view.name}`;
        btn.title = `执行保存视图：${view.query || '全部'}`;
        btn.onclick = async () => {
          try {
            $('universe-search').value = view.query || '';
            this.query = view.query || '';
            await call(`/api/saved-views/${encodeURIComponent(view.view_id)}/execute?scope=${encodeURIComponent(this.scope)}`, 'POST', {});
            await this.load();
            this.app.showToast(`已切换至保存视图「${view.name}」`, 'info');
          } catch (err) {
            this.app.showToast(`执行视图失败：${err.message}`, 'error');
          }
        };
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'u-text-button';
        del.style.fontSize = '11px';
        del.textContent = '×';
        del.title = `删除视图「${view.name}」`;
        del.onclick = async (e) => {
          e.stopPropagation();
          try {
            await call(`/api/saved-views/${encodeURIComponent(view.view_id)}?scope=${encodeURIComponent(this.scope)}`, 'DELETE');
            await this.loadSavedViews();
          } catch (err) {
            this.app.showToast(`删除视图失败：${err.message}`, 'error');
          }
        };
        pill.append(btn, del);
        listEl.append(pill);
      }
    } catch {}
  }

  async saveCurrentSearchAsView() {
    const defaultQuery = ($('universe-search')?.value || this.query || '').trim() || '*';
    const name = window.prompt('为当前查询或视角输入保存视图名称 (§97.3)：', this.query ? `搜索: ${this.query}` : (this.currentTopic?.title || '常用视图'));
    if (!name || !name.trim()) return;
    try {
      await call('/api/saved-views', 'POST', {
        name: name.trim(),
        query: defaultQuery,
        scope: this.scope,
        source_Surface: 'ALL',
        collections: this.topicId && !['all', 'unclassified', '__local_skills'].includes(this.topicId) ? [this.topicId] : [],
        limit: 50,
      });
      this.app.showToast(`已保存视图「${name.trim()}」`, 'success');
      await this.loadSavedViews();
    } catch (e) {
      this.app.showToast(`保存视图失败：${e.message}`, 'error');
    }
  }
}
