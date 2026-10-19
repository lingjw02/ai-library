/**
 * AI Library - AI Memory Workspace (Living Cellular Network)
 * Ref: AI Library Frontend README §4, §6
 * Visual: 2D Canvas living cell network with soft glowing radial halos and membranes.
 * Option B: Right-side detail drawer keeping the network visible.
 */

import { API } from './api.js';
import { ConflictReview } from './conflicts.js';
import { renderMarkdownToHtml, bindRenderedContentInteractions } from './content-renderer.js';

export class MemoryNetwork {
  constructor({ onToast, onClose, onNavigateToBacklog, onNavigateToNote, onNavigateToMemory, headless = false }) {
    this.headless = headless;
    this.onClose = onClose || null;
    this.onNavigateToMemory = onNavigateToMemory || (id => this.focusNodeById(id));
    this.onToast = onToast || console.log;
    this.onNavigateToBacklog = onNavigateToBacklog || (() => {});
    this.onNavigateToNote = onNavigateToNote || (() => {});

    // Canvas & Context
    this.canvas = document.getElementById('network-canvas');
    this.ctx = this.canvas.getContext('2d');
    this.container = document.getElementById('memory-canvas-container');

    // UI Elements
    this.searchInput = document.getElementById('memory-search-input');
    this.scopeInput = document.getElementById('memory-scope-input');
    this.searchStatus = document.getElementById('memory-search-status');
    this.projectFilter = document.getElementById('memory-project-filter');
    this.sidebar = document.getElementById('memory-sidebar');
    this.sidebarToggle = document.getElementById('btn-toggle-memory-sidebar');
    this.sidebar.hidden = matchMedia('(max-width: 760px)').matches;
    this.sidebarToggle.setAttribute('aria-expanded', String(!this.sidebar.hidden));
    this.sidebarToggle.addEventListener('click', () => {
      this.sidebar.hidden = !this.sidebar.hidden;
      this.sidebarToggle.setAttribute('aria-expanded', String(!this.sidebar.hidden));
      this.resizeCanvas();
    });
    this.projectFilter.addEventListener('change', () => {
      this.closeDrawer();
      this.scheduleSearch();
      this.fitView();
    });
    this.searchRequest = 0;
    this.typeFilter = document.getElementById('memory-type-filter');
    this.statsLabel = document.getElementById('memory-stats-label');
    this.btnFitView = document.getElementById('btn-reset-view');
    this.btnReload = document.getElementById('btn-reload-graph');
    this.ctrlZoomIn = document.getElementById('ctrl-zoom-in');
    this.ctrlZoomOut = document.getElementById('ctrl-zoom-out');
    this.ctrlFitView = document.getElementById('ctrl-fit-view');
    this.ctrlToggleSim = document.getElementById('ctrl-toggle-sim');
    this.legend = document.getElementById('network-legend');

    // Detail Drawer Elements
    this.drawer = document.getElementById('memory-detail-drawer');
    this.conflictReview = new ConflictReview(document.getElementById('memory-conflicts'), {
      onNavigateToNote: this.onNavigateToNote,
    });
    this.drawerCloseBtn = document.getElementById('drawer-btn-close');
    this.drawerNodeType = document.getElementById('drawer-node-type');
    this.drawerTitle = document.getElementById('drawer-node-title');
    this.drawerMaturity = document.getElementById('drawer-dim-maturity');
    this.drawerDepth = document.getElementById('drawer-dim-depth');
    this.drawerAuthority = document.getElementById('drawer-dim-authority');
    this.drawerScope = document.getElementById('drawer-dim-scope');
    this.drawerContent = document.getElementById('drawer-node-content');
    this.drawerProvNote = document.getElementById('drawer-prov-note');
    this.drawerProvRev = document.getElementById('drawer-prov-rev');
    this.drawerProvHash = document.getElementById('drawer-prov-hash');
    this.drawerRelations = document.getElementById('drawer-relations-list');
    this.drawerBtnExpand = document.getElementById('drawer-btn-expand');
    this.drawerBtnSource = document.createElement('button');
    this.drawerBtnSource.className = 'btn-secondary';
    this.drawerBtnSource.id = 'drawer-btn-source';
    this.drawerBtnSource.textContent = '打开原文版本';
    this.drawerProvHash.parentElement.parentElement.appendChild(this.drawerBtnSource);
    this.drawerBtnSource.addEventListener('click', () => {
      const provenance = this.selectedNode?.provenance;
      if (provenance) this.onNavigateToNote(provenance.source_id, provenance.source_revision);
    });
    this.drawerBtnBacklog = document.getElementById('drawer-btn-view-backlog');

    // Reader shell: meta line, view tabs and the source / evolution panes
    this.drawerMeta = document.getElementById('drawer-meta');
    this.drawerTimeCreated = document.getElementById('drawer-time-created');
    this.drawerTimeUpdated = document.getElementById('drawer-time-updated');
    this.drawerTimeObserved = document.getElementById('drawer-time-observed');
    this.drawerTimeValid = document.getElementById('drawer-time-valid');
    this.drawerRevisionList = document.getElementById('drawer-revision-list');
    this.drawerPanes = {
      content: document.getElementById('drawer-pane-content'),
      source: document.getElementById('drawer-pane-source'),
      evolution: document.getElementById('drawer-pane-evolution'),
    };
    this.drawerTabs = [...document.querySelectorAll('#memory-detail-drawer [data-drawertab]')];
    this.revisionRequest = 0;

    // Graph Data
    this.nodes = [];
    this.edges = [];
    this.nodeMap = new Map();

    // Camera Transform (Pan & Zoom)
    this.camera = { x: 0, y: 0, zoom: 1 };
    this.isDragging = false;
    this.draggedNode = null;
    this.dragStart = { x: 0, y: 0 };
    this.hoveredNode = null;
    this.selectedNode = null;

    // Simulation Physics State
    this.simRunning = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.settleFrames = 0;
    this.pulsePhase = 0;

    // Color definitions per NodeType
    this.typeColors = {
      CONCEPT: { main: '#6366f1', glow: 'rgba(99, 102, 241, 0.35)', badge: '#4338ca' },
      ENTITY: { main: '#818cf8', glow: 'rgba(129, 140, 248, 0.35)', badge: '#4338ca' },
      FACT: { main: '#10b981', glow: 'rgba(16, 185, 129, 0.35)', badge: '#065f46' },
      OBSERVATION: { main: '#f59e0b', glow: 'rgba(245, 158, 11, 0.35)', badge: '#92400e' },
      DECISION: { main: '#06b6d4', glow: 'rgba(6, 182, 212, 0.35)', badge: '#155e75' },
      POLICY: { main: '#0ea5e9', glow: 'rgba(14, 165, 233, 0.35)', badge: '#0369a1' },
      STRATEGY: { main: '#a855f7', glow: 'rgba(168, 85, 247, 0.35)', badge: '#6b21a8' },
      EVIDENCE: { main: '#3b82f6', glow: 'rgba(59, 130, 246, 0.35)', badge: '#1d4ed8' },
      CONFLICT: { main: '#ef4444', glow: 'rgba(239, 68, 68, 0.35)', badge: '#b91c1c' },
      TASK: { main: '#ec4899', glow: 'rgba(236, 72, 153, 0.35)', badge: '#9d174d' },
      DEFAULT: { main: '#94a3b8', glow: 'rgba(148, 163, 184, 0.25)', badge: '#334155' },
    };

    this.initCanvas();
    this.bindEvents();
    if (!headless) this.startRenderLoop();
  }

  initCanvas() {
    this.resizeCanvas();
    window.addEventListener('resize', () => this.resizeCanvas());
    this.resizeObserver = new ResizeObserver(() => this.resizeCanvas());
    this.resizeObserver.observe(this.container);
  }

  resizeCanvas() {
    const rect = this.container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.scale(dpr, dpr);
    this.width = rect.width;
    this.height = rect.height;
  }

  bindEvents() {
    // Zoom and Pan with Mouse
    this.canvas.addEventListener('wheel', (e) => this.handleWheel(e), { passive: false });
    this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
    window.addEventListener('mousemove', (e) => this.handleMouseMove(e));
    window.addEventListener('mouseup', (e) => this.handleMouseUp(e));

    // Floating Controls
    this.ctrlZoomIn.addEventListener('click', () => this.zoom(1.25));
    this.ctrlZoomOut.addEventListener('click', () => this.zoom(0.8));
    this.ctrlFitView.addEventListener('click', () => this.fitView());
    this.btnFitView.addEventListener('click', () => this.fitView());
    this.btnReload.addEventListener('click', () => this.loadGraph());
    this.ctrlToggleSim.addEventListener('click', () => this.toggleSimulation());

    // Filter and Search
    this.searchInput.addEventListener('input', () => this.scheduleSearch());
    this.typeFilter.addEventListener('change', () => this.scheduleSearch());
    this.scopeInput.addEventListener('change', () => {
      this.focusRequest = (this.focusRequest || 0) + 1;
      this.closeDrawer();
      this.processData([], []);
      this.scheduleSearch();
      this.loadGraph();
    });

    // Detail Drawer
    this.drawerCloseBtn.addEventListener('click', () => this.closeDrawer());
    for (const button of this.drawerTabs) {
      button.addEventListener('click', () => this.switchDrawerTab(button.dataset.drawertab));
    }
    this.drawerBtnExpand.addEventListener('click', () => this.expandSelectedNeighbors());
    this.drawerBtnBacklog.addEventListener('click', () => {
      if (this.selectedNode) {
        this.onNavigateToBacklog(this.selectedNode.id);
      }
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.drawer.classList.contains('open')) {
        this.closeDrawer();
      }
    });
  }

  toggleSimulation() {
    this.simRunning = !this.simRunning;
    this.settleFrames = 0;
    this.ctrlToggleSim.textContent = this.simRunning ? '⏸' : '▶';
    this.ctrlToggleSim.title = this.simRunning ? 'Pause Dynamics' : 'Resume Dynamics';
  }

  async loadGraph() {
    const request = this.graphRequest = (this.graphRequest || 0) + 1;
    const scope = this.scopeInput.value.trim() || 'default';
    try {
      const [rawNodes, rawEdges] = await Promise.all([
        API.fetchMemoryNodes({ limit: 120, scope }),
        API.fetchMemoryEdges({ limit: 150, scope }),
      ]);

      if (request !== this.graphRequest) return;
      this.processData(rawNodes, rawEdges);
      this.applyFilters();
      if (this.searchInput.value.trim()) this.scheduleSearch();
      if (!this.hasLoaded) this.fitView();
      this.hasLoaded = true;
    } catch (err) {
      this.onToast(`Failed to load memory graph: ${err.message}`, 'error');
    }
  }

  processData(rawNodes, rawEdges) {
    const previous = new Map(this.nodeMap);
    const selectedId = this.selectedNode?.id;
    this.nodeMap.clear();
    if (rawNodes.some(n => !previous.has(n.id))) this.settleFrames = 0;

    const cx = this.width / 2;
    const cy = this.height / 2;

    this.nodes = rawNodes.map((n, i) => {
      const angle = i * 2.399963229728653;
      const radius = 105 * Math.sqrt(i + 1);
      const saved = previous.get(n.id);
      const node = {
        ...n,
        x: saved?.x ?? cx + Math.cos(angle) * radius,
        y: saved?.y ?? cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        radius: 26,
        visible: true,
        matchSearch: true,
      };
      this.nodeMap.set(node.id, node);
      return node;
    });

    this.edges = rawEdges
      .filter((e) => this.headless || (this.nodeMap.has(e.source_id) && this.nodeMap.has(e.target_id)))
      .map((e) => ({
        ...e,
        source: this.nodeMap.get(e.source_id),
        target: this.nodeMap.get(e.target_id),
        visible: true,
      }));

    if (selectedId && this.nodeMap.has(selectedId)) this.selectNode(this.nodeMap.get(selectedId));
    else if (selectedId) this.closeDrawer();
    const selectedProject = this.projectFilter.value;
    this.projectFilter.replaceChildren(new Option('全部项目', ''));
    [...new Set(this.nodes.map(n => this.projectOf(n)).filter(Boolean))].sort().forEach(project => {
      this.projectFilter.add(new Option(project, project));
    });
    this.projectFilter.value = [...this.projectFilter.options].some(o => o.value === selectedProject) ? selectedProject : '';
    this.updateStatsLabel();
  }

  projectOf(node) { return /^【([^】]+)】/.exec(node.title || '')?.[1] || ''; }

  captionOf(node) { return (node.title || node.id).replace(/^【[^】]+】\s*/, '').replace(/^聊天摘要｜/, ''); }

  updateStatsLabel() {
    const visibleNodes = this.nodes.filter((n) => n.visible).length;
    const visibleEdges = this.edges.filter((e) => e.visible).length;
    this.statsLabel.textContent = `${visibleNodes} 个节点 · ${visibleEdges} 条关系 · 当前载入范围`;
    this.statsLabel.title = '最多载入 120 个节点、150 条关系；搜索可查询当前 scope 的全部记忆。';
    document.getElementById('memory-list-count').textContent = visibleNodes;
  }

  applyFilters() {
    const query = (this.searchInput.value || '').trim().toLowerCase();
    const type = this.typeFilter.value;

    this.nodes.forEach((node) => {
      const matchType = !type || (node.node_type && node.node_type.toUpperCase() === type.toUpperCase());
      const matchQuery =
        !query ||
        (node.title && node.title.toLowerCase().includes(query)) ||
        (node.id && node.id.toLowerCase().includes(query));

      node.visible = matchType && matchQuery && (!this.projectFilter.value || this.projectOf(node) === this.projectFilter.value);
      node.matchSearch = Boolean(query && matchQuery);
    });

    this.edges.forEach((edge) => {
      edge.visible = edge.source.visible && edge.target.visible;
    });

    this.updateStatsLabel();
    this.renderNodeList();
  }

  renderNodeList() {
    if (this.searchInput.value.trim()) return;
    this.searchStatus.textContent = '';
    const list = document.getElementById('memory-node-list');
    list.replaceChildren();
    const visible = this.nodes.filter(n => n.visible);
    if (!visible.length) list.textContent = this.nodes.length ? 'No matching memory in the loaded window.' : 'No memory yet. Prepare and review a candidate in My Notes.';
    visible.forEach(node => {
      const button = document.createElement('button');
      button.className = 'memory-list-item';
      this.populateListButton(button, node);
      button.addEventListener('click', () => {
        this.returnFocus = button;
        this.selectNode(node);
        this.drawerCloseBtn.focus({ preventScroll: true });
      });
      list.appendChild(button);
    });
  }

  populateListButton(button, node) {
    const title = document.createElement('span');
    title.className = 'memory-list-title';
    title.textContent = this.captionOf(node);
    const meta = document.createElement('span');
    meta.className = 'memory-list-meta';
    meta.textContent = `${this.projectOf(node) || node.node_type} · ${node.maturity || '阶段未知'}`;
    button.append(title, meta);
    button.title = node.title || node.id;
    button.setAttribute('aria-pressed', String(this.selectedNode?.id === node.id));
  }

  async focusNodeById(nodeId) {
    const request = this.focusRequest = (this.focusRequest || 0) + 1;
    const scope = this.scopeInput.value.trim() || 'default';
    try {
      if (!this.nodeMap.has(nodeId)) {
        const node = await API.fetchMemoryNode(nodeId);
        if (request !== this.focusRequest || scope !== (this.scopeInput.value.trim() || 'default')) return;
        if (node.scope !== scope) {
          this.scopeInput.value = node.scope;
          this.closeDrawer();
          await this.loadGraph();
          if (request !== this.focusRequest || this.scopeInput.value !== node.scope) return;
        }
        this.processData([...this.nodes, node], this.edges);
      }
      ++this.searchRequest;
      clearTimeout(this.searchTimer);
      this.searchInput.value = '';
      this.typeFilter.value = '';
      this.projectFilter.value = '';
      this.applyFilters();
      this.selectNode(this.nodeMap.get(nodeId));
    } catch (error) { this.onToast(`Cannot open memory: ${error.message}`, 'error'); }
  }

  scheduleSearch() {
    const request = ++this.searchRequest;
    clearTimeout(this.searchTimer);
    this.applyFilters();
    const query = this.searchInput.value.trim();
    if (!query) return;
    const scope = this.scopeInput.value.trim() || 'default';
    const nodeType = this.typeFilter.value || null;
    const list = document.getElementById('memory-node-list');
    list.replaceChildren();
    this.searchStatus.textContent = 'Searching all memory in scope…';
    this.searchTimer = setTimeout(async () => {
      try {
        const result = await API.searchDocuments(query, 'MEMORY', scope, nodeType);
        if (request !== this.searchRequest) return;
        const matching = result.items.filter(node => !this.projectFilter.value || this.projectOf(node) === this.projectFilter.value);
        this.searchStatus.textContent = this.projectFilter.value ? `当前结果中有 ${matching.length} 条项目匹配` : `${result.items.length} of ${result.total} matches · ${result.mode === 'canonical_fallback' ? 'live fallback' : 'indexed'}`;
        matching.forEach(node => {
          const button = document.createElement('button');
          button.className = 'memory-list-item';
          this.populateListButton(button, node);
          button.addEventListener('click', async () => {
            await this.focusNodeById(node.id);
            this.drawerCloseBtn.focus({ preventScroll: true });
          });
          list.appendChild(button);
        });
      } catch (error) {
        if (request !== this.searchRequest) return;
        this.searchStatus.textContent = 'Search failed. Edit the query to retry.';
        this.onToast(error.message, 'error');
      }
    }, 250);
  }

  // --- Coordinate Transformations ---
  screenToWorld(sx, sy) {
    return {
      x: (sx - this.camera.x) / this.camera.zoom,
      y: (sy - this.camera.y) / this.camera.zoom,
    };
  }

  worldToScreen(wx, wy) {
    return {
      x: wx * this.camera.zoom + this.camera.x,
      y: wy * this.camera.zoom + this.camera.y,
    };
  }

  // --- Interaction Event Handlers ---
  handleWheel(e) {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.12 : 0.89;
    const newZoom = Math.min(Math.max(this.camera.zoom * zoomFactor, 0.2), 4.0);

    // Zoom centered on cursor
    this.camera.x = mx - (mx - this.camera.x) * (newZoom / this.camera.zoom);
    this.camera.y = my - (my - this.camera.y) * (newZoom / this.camera.zoom);
    this.camera.zoom = newZoom;
  }

  handleMouseDown(e) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const worldPos = this.screenToWorld(mx, my);

    const hitNode = this.getNodeAt(worldPos.x, worldPos.y);

    if (hitNode) {
      this.draggedNode = hitNode;
      this.dragStart = { x: mx, y: my };
      this.selectNode(hitNode);
    } else {
      this.isDragging = true;
      this.dragStart = { x: mx - this.camera.x, y: my - this.camera.y };
    }
  }

  handleMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const worldPos = this.screenToWorld(mx, my);

    if (this.draggedNode) {
      this.draggedNode.x = worldPos.x;
      this.draggedNode.y = worldPos.y;
      this.draggedNode.vx = 0;
      this.draggedNode.vy = 0;
    } else if (this.isDragging) {
      this.camera.x = mx - this.dragStart.x;
      this.camera.y = my - this.dragStart.y;
    } else {
      // Hover check
      const hit = this.getNodeAt(worldPos.x, worldPos.y);
      if (hit !== this.hoveredNode) {
        this.hoveredNode = hit;
        this.canvas.style.cursor = hit ? 'pointer' : 'grab';
      }
    }
  }

  handleMouseUp(e) {
    if (this.isDragging) {
      this.isDragging = false;
    }
    if (this.draggedNode) {
      this.draggedNode = null;
    }
  }

  getNodeAt(x, y) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const node = this.nodes[i];
      if (!node.visible) continue;
      const dx = node.x - x;
      const dy = node.y - y;
      if (dx * dx + dy * dy <= node.radius * node.radius) {
        return node;
      }
    }
    return null;
  }

  selectNode(node) {
    const alreadySelected = this.selectedNode?.id === node.id;
    this.selectedNode = node;
    this.openDrawer(node);
    if (!alreadySelected) {
      const availableWidth = Math.max(120, this.width - this.drawer.offsetWidth);
      this.camera.x = availableWidth / 2 - node.x * this.camera.zoom;
      this.camera.y = this.height / 2 - node.y * this.camera.zoom;
    }
  }

  zoom(factor) {
    const cx = this.width / 2;
    const cy = this.height / 2;
    const newZoom = Math.min(Math.max(this.camera.zoom * factor, 0.2), 4.0);
    this.camera.x = cx - (cx - this.camera.x) * (newZoom / this.camera.zoom);
    this.camera.y = cy - (cy - this.camera.y) * (newZoom / this.camera.zoom);
    this.camera.zoom = newZoom;
  }

  fitView() {
    // A user-requested overview must remain fitted, including reduced motion.
    this.settleFrames = 181;
    const visibleNodes = this.nodes.filter((n) => n.visible);
    if (visibleNodes.length === 0) {
      this.camera = { x: this.width / 2, y: this.height / 2, zoom: 1 };
      return;
    }

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    visibleNodes.forEach((n) => {
      minX = Math.min(minX, n.x - n.radius);
      maxX = Math.max(maxX, n.x + n.radius);
      minY = Math.min(minY, n.y - n.radius);
      maxY = Math.max(maxY, n.y + n.radius);
    });

    const graphWidth = maxX - minX || 200;
    const graphHeight = maxY - minY || 200;
    const padding = Math.min(100, this.width * 0.12, this.height * 0.15);

    const scaleX = (this.width - padding * 2) / graphWidth;
    const scaleY = (this.height - padding * 2) / graphHeight;
    const targetZoom = Math.min(Math.max(Math.min(scaleX, scaleY), 0.12), 1.6);

    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;

    this.camera.zoom = targetZoom;
    this.camera.x = this.width / 2 - centerX * targetZoom;
    this.camera.y = this.height / 2 - centerY * targetZoom;
  }

  getTypeColors(nodeType) {
    const key = (nodeType || '').toUpperCase();
    return this.typeColors[key] || this.typeColors.DEFAULT;
  }

  // --- Detail Drawer (Option B) ---
  openDrawer(node) {
    this.conflictReview.show(node);
    const colors = this.getTypeColors(node.node_type);

    this.drawerNodeType.textContent = node.node_type || 'Concept';
    this.drawerNodeType.style.background = colors.badge;
    this.drawerNodeType.style.color = '#ffffff';
    this.drawerNodeType.style.borderColor = `${colors.main}80`;

    this.drawerTitle.textContent = node.title || node.id;
    this.renderDrawerMeta(node);
    this.switchDrawerTab('content');
    this.renderDrawerTimes(node);
    this.loadRevisions(node);
    const stages = {L0:'原始记录', L1:'已索引', L2:'已整理', L3:'已核验', L4:'已整合', L5:'稳定知识'};
    const depths = {D0:'领域', D1:'主题', D2:'概念与对象', D3:'事实与关系', D4:'原始证据'};
    this.drawerMaturity.textContent = node.maturity ? `${node.maturity} · ${stages[node.maturity] || '未知'}` : '未知';
    this.drawerDepth.textContent = node.depth ? `${node.depth} · ${depths[node.depth] || '未知'}` : '未知';
    this.drawerAuthority.textContent = {EXPLICIT:'用户明确表达', OBSERVED:'直接观察', INFERRED:'AI 归纳'}[node.authority] || '未知';
    this.drawerScope.textContent = node.scope || 'default_scope';
    const verification = node.verification_props?.status;
    document.getElementById('drawer-verification-status').textContent = verification === 'UNVERIFIED'
      ? '待核验 · 已整理成记忆，不代表内容已被证实。'
      : verification ? `核验记录：${verification}` : '尚无独立核验记录；请结合原文判断。';

    const desc = node.content || '这里只是来源引用，请打开它的原文版本查看全文。';
    this.renderDrawerRichContent(node, desc, []);
    if (node.provenance?.source_id) {
      this.loadProvenanceMedia(node);
    }

    // Provenance
    this.drawerProvNote.textContent = node.provenance?.source_id || '未记录来源';
    this.drawerBtnSource.disabled = !node.provenance?.source_id;
    this.drawerProvRev.textContent = node.provenance ? `v${node.provenance.source_revision}` : '未知';
    this.drawerProvHash.textContent = node.provenance?.content_hash || '未记录';

    // Connected relations
    const connectedEdges = this.edges.filter(
      (e) => e.source_id === node.id || e.target_id === node.id
    );

    this.drawerRelations.innerHTML = '';
    if (connectedEdges.length === 0) {
      this.drawerRelations.innerHTML = '<span style="color:var(--text-dim);">当前视图没有载入关系</span>';
    } else {
      connectedEdges.forEach((e) => {
        const isOut = e.source_id === node.id;
        const otherNode = isOut ? e.target : e.source;
        const relRow = document.createElement('div');
        relRow.style.padding = '6px 10px';
        relRow.style.background = 'var(--bg-card)';
        relRow.style.borderRadius = '6px';
        relRow.style.border = '1px solid var(--border-subtle)';
        relRow.style.display = 'flex';
        relRow.style.justifyContent = 'space-between';
        relRow.style.alignItems = 'center';

        relRow.textContent = `${isOut ? '→' : '←'} ${e.relation} · ${otherNode?.title || (isOut ? e.target_id : e.source_id)}`;
        relRow.tabIndex = 0;
        relRow.setAttribute('role', 'button');
        relRow.addEventListener('keydown', event => { if (event.key === 'Enter') relRow.click(); });
        relRow.style.cursor = 'pointer';
        relRow.addEventListener('click', () => {
          if (this.headless) this.onNavigateToMemory(isOut ? e.target_id : e.source_id);
          else if (otherNode) this.selectNode(otherNode);
        });
        this.drawerRelations.appendChild(relRow);
      });
    }

    this.drawer.classList.add('open');
    this.legend.classList.add('drawer-open');
  }

  // --- Reader shell: shared grammar with the note reader ---
  switchDrawerTab(tabName) {
    for (const [name, pane] of Object.entries(this.drawerPanes)) {
      if (pane) pane.style.display = name === tabName ? 'block' : 'none';
    }
    for (const button of this.drawerTabs) {
      const active = button.dataset.drawertab === tabName;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  }

  escapeText(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  formatDate(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  renderDrawerMeta(node) {
    if (!this.drawerMeta) return;
    const parts = [];
    if (node.created_at) parts.push(this.formatDate(node.created_at));
    const authority = {EXPLICIT:'用户明确表达', OBSERVED:'直接观察', INFERRED:'AI 归纳'}[node.authority] || '来源未知';
    parts.push(authority);
    if (node.scope) parts.push(`范围 ${node.scope}`);
    this.drawerMeta.textContent = parts.join(' · ');
  }

  renderDrawerTimes(node) {
    const set = (element, value) => { if (element) element.textContent = value; };
    const temporal = node.temporal_props || {};
    set(this.drawerTimeCreated, this.formatDate(node.created_at));
    set(this.drawerTimeUpdated, this.formatDate(node.updated_at));
    set(this.drawerTimeObserved, this.formatDate(temporal.observed_at));
    const from = temporal.valid_from ? this.formatDate(temporal.valid_from) : null;
    const to = temporal.valid_to ? this.formatDate(temporal.valid_to) : null;
    set(this.drawerTimeValid, from || to ? `${from || '不限'} → ${to || '不限'}` : '未设定有效期');
  }

  async loadRevisions(node) {
    if (!this.drawerRevisionList) return;
    const requestId = ++this.revisionRequest;
    this.drawerRevisionList.innerHTML = '<div class="reader-empty">正在读取修订记录…</div>';
    try {
      const revisions = await API.fetchMemoryRevisions(node.id, { scope: node.scope || 'default' });
      if (requestId !== this.revisionRequest || this.selectedNode?.id !== node.id) return;
      this.renderRevisions(revisions, node);
    } catch (err) {
      if (requestId !== this.revisionRequest) return;
      this.drawerRevisionList.innerHTML = `<div class="reader-empty">修订记录读取失败：${this.escapeText(err.message)}</div>`;
    }
  }

  renderRevisions(revisions, node) {
    const list = Array.isArray(revisions) ? revisions : [];
    if (list.length === 0) {
      this.drawerRevisionList.innerHTML = '<div class="reader-empty">这条记忆还没有修订记录。</div>';
      return;
    }
    this.drawerRevisionList.innerHTML = '';
    for (const revision of list) {
      const row = document.createElement('div');
      row.className = `revision-row${revision.version === node.version ? ' is-current' : ''}`;
      row.innerHTML = `
        <span class="revision-version">v${this.escapeText(revision.version)}</span>
        <span class="revision-title">${this.escapeText(revision.title || '（无标题）')}</span>
        <span class="revision-time">${this.formatDate(revision.recorded_at)}</span>
      `;
      this.drawerRevisionList.appendChild(row);
    }
  }

  renderDrawerRichContent(node, desc, mediaList = []) {
    if (!this.drawerContent) return;
    this.drawerContent.innerHTML = renderMarkdownToHtml(desc, mediaList);
    bindRenderedContentInteractions(this.drawerContent, {
      onNavigateToNote: (id, r) => this.onNavigateToNote(id, r),
      onNavigateToMemory: id => this.onNavigateToMemory(id),
      onToast: this.onToast,
    });
  }

  async loadProvenanceMedia(node) {
    const sourceId = node?.provenance?.source_id;
    if (!sourceId) return;
    const reqId = this.revisionRequest;
    try {
      const bundle = await API.fetchNoteChatSession(sourceId, {
        scope: node.scope || 'default',
        fullSession: false,
      });
      if (reqId !== this.revisionRequest || this.selectedNode?.id !== node.id) return;
      const media = bundle.is_chat_session ? (bundle.session_media || []) : (bundle.media || []);
      if (media.length > 0) {
        const desc = node.content || '这里只是来源引用，请打开它的原文版本查看全文。';
        this.renderDrawerRichContent(node, desc, media);
      }
    } catch (_) {}
  }

  closeDrawer() {
    this.revisionRequest++;
    this.conflictReview.clear();
    this.drawer.classList.remove('open');
    this.legend.classList.remove('drawer-open');
    this.selectedNode = null;
    this.onClose?.();
    this.returnFocus?.focus();
  }

  async expandSelectedNeighbors() {
    const selected = this.selectedNode;
    if (!selected) return;
    const graphRequest = this.graphRequest;
    const scope = this.scopeInput.value.trim() || 'default';
    const current = () => this.selectedNode === selected && this.graphRequest === graphRequest && (this.scopeInput.value.trim() || 'default') === scope;
    try {
      this.drawerBtnExpand.disabled = true;
      this.drawerBtnExpand.textContent = 'Expanding...';
      const edges = await API.fetchNodeEdges(selected.id);
      if (!current()) return;
      const newNodes = [];
      for (const edge of edges.filter(e => e.scope === scope).slice(0, 20)) {
        const otherId = edge.source_id === selected.id ? edge.target_id : edge.source_id;
        if (!this.nodeMap.has(otherId) && !newNodes.some(n => n.id === otherId)) {
          const fetched = await API.fetchMemoryNode(otherId);
          if (!current()) return;
          if (fetched.scope === scope && fetched.is_active) newNodes.push(fetched);
        }
      }
      const rawEdges = await API.fetchMemoryEdges({ limit: 200, scope });
      if (!current()) return;
      this.processData([...this.nodes, ...newNodes], rawEdges);
      this.applyFilters();
      this.onToast(`Expanded ${newNodes.length} neighboring memory nodes.`, 'success');
    } catch (err) {
      if (current()) this.onToast(`Failed to expand: ${err.message}`, 'error');
    } finally {
      this.drawerBtnExpand.disabled = false;
      this.drawerBtnExpand.textContent = 'Expand Neighbors';
    }
  }

  // --- Physics & Force Simulation ---
  updatePhysics() {
    if (!this.simRunning || this.selectedNode || this.settleFrames++ > 180 ||
        matchMedia('(prefers-reduced-motion: reduce)').matches || document.body.classList.contains('motion-quiet')) return;

    this.pulsePhase += 0.03;
    const visibleNodes = this.nodes.filter((n) => n.visible);
    const visibleEdges = this.edges.filter((e) => e.visible);

    // Repulsion between all node pairs
    const repulsionStrength = 5000;
    for (let i = 0; i < visibleNodes.length; i++) {
      const a = visibleNodes[i];
      for (let j = i + 1; j < visibleNodes.length; j++) {
        const b = visibleNodes[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distSq = dx * dx + dy * dy + 40;
        const dist = Math.sqrt(distSq);
        const force = repulsionStrength / distSq + Math.max(0, a.radius + b.radius + 44 - dist) * 0.08;

        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;

        if (a !== this.draggedNode) { a.vx -= fx; a.vy -= fy; }
        if (b !== this.draggedNode) { b.vx += fx; b.vy += fy; }
      }
    }

    // Spring attraction along edges
    const linkLength = 170;
    const linkStrength = 0.04;
    visibleEdges.forEach((edge) => {
      const a = edge.source;
      const b = edge.target;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const displacement = dist - linkLength;
      const force = displacement * linkStrength;

      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      if (a !== this.draggedNode) { a.vx += fx; a.vy += fy; }
      if (b !== this.draggedNode) { b.vx -= fx; b.vy -= fy; }
    });

    // Center Gravity to prevent drifting
    const cx = this.width / 2;
    const cy = this.height / 2;
    const gravity = 0.0006;
    visibleNodes.forEach((node) => {
      if (node !== this.draggedNode) {
        node.vx += (cx - node.x) * gravity;
        node.vy += (cy - node.y) * gravity;

        // Velocity damping
        node.vx *= 0.85;
        node.vy *= 0.85;

        // Apply motion
        node.x += node.vx;
        node.y += node.vy;
      }
    });
  }

  // --- Rendering Loop ---
  startRenderLoop() {
    const render = () => {
      if (!document.hidden && document.getElementById('workspace-memory').classList.contains('active')) {
        this.updatePhysics();
        this.draw();
      }
      requestAnimationFrame(render);
    };
    requestAnimationFrame(render);
  }

  draw() {
    this.ctx.clearRect(0, 0, this.width, this.height);

    this.ctx.save();
    this.ctx.translate(this.camera.x, this.camera.y);
    this.ctx.scale(this.camera.zoom, this.camera.zoom);

    // Draw Edges
    this.drawEdges();

    // Draw Living Cell Nodes
    this.drawNodes();

    this.ctx.restore();
  }

  drawEdges() {
    const visibleEdges = this.edges.filter((e) => e.visible);

    visibleEdges.forEach((edge) => {
      const a = edge.source;
      const b = edge.target;

      const isConnectedToFocus =
        (this.hoveredNode && (a === this.hoveredNode || b === this.hoveredNode)) ||
        (this.selectedNode && (a === this.selectedNode || b === this.selectedNode));

      this.ctx.beginPath();
      this.ctx.moveTo(a.x, a.y);
      this.ctx.lineTo(b.x, b.y);

      if (isConnectedToFocus) {
        this.ctx.strokeStyle = '#818cf8';
        this.ctx.lineWidth = 2.5;
        this.ctx.shadowColor = 'rgba(99, 102, 241, 0.6)';
        this.ctx.shadowBlur = 8;
      } else {
        this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.12)';
        this.ctx.lineWidth = 1.2;
        this.ctx.shadowBlur = 0;
      }

      this.ctx.stroke();
      this.ctx.shadowBlur = 0;

      // Draw relation label if zoomed in or focused
      if (isConnectedToFocus) {
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        this.ctx.font = '10px -apple-system, sans-serif';
        this.ctx.fillStyle = isConnectedToFocus ? '#c7d2fe' : 'rgba(255, 255, 255, 0.35)';
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText(edge.relation, mx, my - 6);
      }
    });
  }

  drawNodes() {
    const visibleNodes = this.nodes.filter((n) => n.visible);

    visibleNodes.forEach((node) => {
      const isHovered = node === this.hoveredNode;
      const isSelected = node === this.selectedNode;
      const colors = this.getTypeColors(node.node_type);

      // Cellular Breathing Pulse
      const pulse = Math.sin(this.pulsePhase + node.x * 0.05) * 1.5;
      const baseRadius = node.radius + (isSelected ? 4 : isHovered ? 2 : 0) + pulse;

      // 1. Soft glowing outer radial halo (§6.1)
      const grad = this.ctx.createRadialGradient(
        node.x, node.y, baseRadius * 0.4,
        node.x, node.y, baseRadius * 2.2
      );
      grad.addColorStop(0, colors.glow);
      grad.addColorStop(1, 'rgba(0, 0, 0, 0)');

      this.ctx.beginPath();
      this.ctx.arc(node.x, node.y, baseRadius * 2.2, 0, Math.PI * 2);
      this.ctx.fillStyle = grad;
      this.ctx.fill();

      // 2. Cellular Cytoplasm Body
      this.ctx.beginPath();
      this.ctx.arc(node.x, node.y, baseRadius, 0, Math.PI * 2);
      this.ctx.fillStyle = isSelected ? '#1e1b4b' : '#141824';
      this.ctx.fill();

      // 3. Cellular Membrane Border
      this.ctx.strokeStyle = isSelected ? '#ffffff' : colors.main;
      this.ctx.lineWidth = isSelected ? 3 : 2;
      this.ctx.stroke();

      // 4. Center Nucleus Core Dot
      this.ctx.beginPath();
      this.ctx.arc(node.x, node.y, 4, 0, Math.PI * 2);
      this.ctx.fillStyle = colors.main;
      this.ctx.fill();

    });
    // Place labels in screen space. Suppressed labels remain available in the
    // sidebar and on focus; crowded geometry never makes text overlap.
    const zoom = this.camera.zoom;
    const occupied = visibleNodes.map(n => {
      const p = this.worldToScreen(n.x, n.y), r = n.radius * zoom + 3;
      return {x:p.x-r,y:p.y-r,w:r*2,h:r*2};
    });
    this.labelBoxes = [];
    const priority = n => n === this.selectedNode ? 0 : n === this.hoveredNode ? 1 : n.node_type === 'Decision' ? 2 : n.node_type === 'Source' ? 4 : 3;
    for (const node of [...visibleNodes].sort((a,b)=>priority(a)-priority(b))) {
      const p = this.worldToScreen(node.x,node.y);
      if (p.x < 0 || p.x > this.width || p.y < 0 || p.y > this.height) continue;
      if (node.node_type === 'Source' && zoom < 0.8 && priority(node) > 1) continue;
      this.ctx.font = `${12 / zoom}px "Segoe UI", "Microsoft YaHei", sans-serif`;
      let label = this.captionOf(node);
      while (label.length > 1 && this.ctx.measureText(label).width * zoom > 154) label = label.slice(0,-1);
      if (label !== this.captionOf(node)) label += '…';
      const width = this.ctx.measureText(label).width * zoom + 12;
      const box = {x:p.x-width/2,y:p.y+(node.radius+5)*zoom+5,w:width,h:22};
      if (box.x < 0 || box.x+box.w > this.width || box.y+box.h > this.height) continue;
      if (occupied.some(b=>box.x<b.x+b.w && box.x+box.w>b.x && box.y<b.y+b.h && box.y+box.h>b.y)) continue;
      occupied.push(box); this.labelBoxes.push(box);
      this.ctx.fillStyle = '#101620';
      const pos = this.screenToWorld(box.x,box.y);
      this.ctx.fillRect(pos.x,pos.y,box.w/zoom,box.h/zoom);
      this.ctx.fillStyle = priority(node)<2 ? '#ffffff' : '#cbd5e1';
      this.ctx.textAlign='center'; this.ctx.textBaseline='middle';
      this.ctx.fillText(label,node.x,(box.y+box.h/2-this.camera.y)/zoom);
    }
  }
}
