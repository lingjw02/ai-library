import { API } from './api.js';
import { NotesManager } from './notes.js';
import { MemoryNetwork } from './network.js';
import { MaintenanceManager } from './maintenance.js';
import { UniverseController } from './universe.js';
import { SettingsPanel } from './settings.js';
import { store } from './state.js';

// Apply cached UI preferences before the first paint so theme and font scale
// never flash the defaults on reload.
SettingsPanel.applyCachedUi();

export class UniverseApp {
  get activeTab() {
    return this.store?.get('viewMode') === 'maintenance' ? 'maintenance' : 'universe';
  }
  constructor() {
    this.store = store;
    this.readRequest = 0;
    this.notesMgr = new NotesManager({
      onToast: (m, t) => this.showToast(m, t),
      onClose: () => this.syncReaderClosed(),
      onNavigateToNote: (id, r) => this.jumpToNote(id, r),
      onNavigateToMemory: id => this.jumpToMemory(id),
    });
    this.memoryNet = new MemoryNetwork({
      headless: true,
      onToast: (m, t) => this.showToast(m, t),
      onClose: () => this.syncReaderClosed(),
      onNavigateToNote: (id, r) => this.jumpToNote(id, r),
      onNavigateToMemory: id => this.jumpToMemory(id),
      onNavigateToBacklog: id => this.jumpToBacklog(id),
    });
    document.getElementById('workspace-universe').append(this.memoryNet.drawer);
    this.memoryNet.drawer.setAttribute('aria-label','AI 记忆详情');
    this.maintenanceMgr = new MaintenanceManager({onToast:(m,t)=>this.showToast(m,t),onNavigateToNote:(id,r)=>this.jumpToNote(id,r),onNavigateToMemory:id=>this.jumpToMemory(id)});
    this.universe = new UniverseController(this);
    this.settings = new SettingsPanel({ API, universe: this.universe, onToast: (m, t) => this.showToast(m, t) });
    document.getElementById('universe-settings').onclick = () => {
      if (!this.closeReaders()) return;
      this.settings.open();
    };
    const material = document.getElementById('material-dialog');
    document.getElementById('universe-add').onclick=()=>material.showModal();
    document.getElementById('material-close').onclick=()=>material.close();
    document.getElementById('material-new-note').onclick=()=>{
      material.close();
      if (!this.closeReaders()) return;
      this.notesMgr.scopeInput.value=this.universe.scope;
      this.notesMgr.openNewNote();
      this.store.set({ activeReader: { type: 'note', id: null } });
      this.notesMgr.returnFocus=document.getElementById('universe-add');
    };
    this.initMaterialIntake(material);
    material.addEventListener('close',()=>document.getElementById('universe-add').focus());
    document.getElementById('universe-maintenance').onclick=()=>this.toggleMaintenance();
    document.getElementById('universe-sync').onclick=()=>this.syncLibrary();
    document.getElementById('universe-home').onclick=()=>{
      if (!this.closeReaders()) return;
      this.showUniverse();
      this.universe.agentId = null;
      this.universe.perspective = 'topic';
      this.universe.navigate(null);
    };

    // 状态总线驱动的阅读器/全息星系联动
    this.store.subscribe('activeReader', reader => {
      const active = !!reader;
      document.body.classList.toggle('reader-active', active);
      this.universe.scene?.setReading(active);
    });

    for(const dialog of document.querySelectorAll('dialog')) {
      dialog.addEventListener('close', () => {
        const anyOpen = !!document.querySelector('dialog[open]');
        if (!anyOpen && !this.store.get('activeReader')) {
          document.body.classList.remove('reader-active');
          this.universe.scene?.setReading(false);
        }
      });
    }

    window.addEventListener('library-note-saved',()=>{ this.universe.invalidateCache(); this.universe.load(); this.syncFingerprintSoon(); });
    window.addEventListener('library-memory-changed',()=>{ this.universe.invalidateCache(); this.universe.load(); this.syncFingerprintSoon(); });
    this.universe.init();this.checkHealth();
    setInterval(()=>{if(!document.hidden)this.checkHealth();},15000);
    // 内容更新自动刷新：轮询后端内容指纹，检测到变化（含外部进程/其他会话写入）即自动重载星图数据，无需手动 F5。
    this.contentFingerprint = null;
    this.liveRefreshTimer = null;
    this.liveRefreshTick();
    // 轮询间隔从 4s 放宽到 8s：本地库内容变化不是秒级需求，而每次 tick 都是一次
    // 完整 HTTP 往返 + 后端指纹查询，4s 会在空闲时持续占用主线程与连接。
    setInterval(()=>this.liveRefreshTick(), 8000);
    this.initLegendTabs();
  }
  /** 添加资料弹窗：支持上传多媒体（PDF/图片/视频/音频）、两步式网页抓取与文本文件预览确认（§98.2–98.5）。 */
  initMaterialIntake(material) {
    const mediaBtn = document.getElementById('material-upload-media-btn');
    const mediaInput = document.getElementById('material-media-input');
    const urlInput = document.getElementById('material-url-input');
    const urlSubmit = document.getElementById('material-url-submit');
    const textBtn = document.getElementById('material-textfile-btn');
    const textInput = document.getElementById('material-textfile-input');
    const statusEl = document.getElementById('material-intake-status');

    const previewCard = document.getElementById('material-preview-card');
    const previewTitle = document.getElementById('material-preview-title');
    const previewText = document.getElementById('material-preview-text');
    const selectionRangeEl = document.getElementById('material-selection-range');
    const previewTopicsEl = document.getElementById('material-preview-topics');
    const previewCommentEl = document.getElementById('material-preview-comment');
    const previewCancelBtn = document.getElementById('material-preview-cancel');
    const previewConfirmBtn = document.getElementById('material-preview-confirm');

    this.activeIntakePreview = null;
    this.selectedPreviewTopics = new Set();

    const setStatus = (msg, isError = false) => {
      if (!statusEl) return;
      statusEl.hidden = !msg;
      statusEl.textContent = msg || '';
      statusEl.style.color = isError ? '#f87171' : '#f4c069';
    };

    const updateSelectionLabel = () => {
      if (!previewText || !selectionRangeEl || !this.activeIntakePreview) return;
      const start = previewText.selectionStart ?? 0;
      const end = previewText.selectionEnd ?? 0;
      if (end > start && (start > 0 || end < previewText.value.length)) {
        selectionRangeEl.textContent = `已选摘录区间 [${start}..${end}]（共 ${end - start} 字）`;
      } else {
        selectionRangeEl.textContent = `已选全文（共 ${previewText.value.length} 字）`;
      }
    };

    if (previewText) {
      previewText.addEventListener('select', updateSelectionLabel);
      previewText.addEventListener('keyup', updateSelectionLabel);
      previewText.addEventListener('mouseup', updateSelectionLabel);
    }

    const openPreviewCard = async (preview) => {
      this.activeIntakePreview = preview;
      this.selectedPreviewTopics.clear();
      if ( this.universe?.topicId && !['all', 'unclassified', '__local_skills'].includes(this.universe.topicId) ) {
        this.selectedPreviewTopics.add(this.universe.topicId);
      }
      if (previewTitle) {
        previewTitle.textContent = `预览就绪：${preview.title || '未命名资料'} (rev ${preview.preview_revision ?? 1})`;
      }
      if (previewText) {
        previewText.value = preview.content || '';
        previewText.setSelectionRange(0, 0);
      }
      if (previewCommentEl) previewCommentEl.value = '';
      updateSelectionLabel();

      if (previewTopicsEl) {
        previewTopicsEl.replaceChildren();
        try {
          const scope = this.universe?.scope || 'default';
          const res = await fetch(`/api/topics?scope=${encodeURIComponent(scope)}&limit=100`).then(r => r.json());
          const topics = res.topics || [];
          for (const t of topics) {
            const pill = document.createElement('button');
            pill.type = 'button';
            const isSel = this.selectedPreviewTopics.has(t.id);
            pill.className = `u-button ${isSel ? 'u-primary' : ''}`;
            pill.style.fontSize = '11px';
            pill.style.padding = '2px 8px';
            pill.textContent = t.title;
            pill.onclick = () => {
              if (this.selectedPreviewTopics.has(t.id)) {
                this.selectedPreviewTopics.delete(t.id);
                pill.classList.remove('u-primary');
              } else {
                this.selectedPreviewTopics.add(t.id);
                pill.classList.add('u-primary');
              }
            };
            previewTopicsEl.append(pill);
          }
        } catch {}
      }

      if (previewCard) previewCard.hidden = false;
    };

    if (previewCancelBtn) {
      previewCancelBtn.onclick = () => {
        this.activeIntakePreview = null;
        if (previewCard) previewCard.hidden = true;
        setStatus('');
      };
    }

    if (previewConfirmBtn) {
      previewConfirmBtn.onclick = async () => {
        const preview = this.activeIntakePreview;
        if (!preview) return;
        const scope = this.universe?.scope || 'default';
        const fullLen = (preview.content || '').length;
        const sStart = previewText ? (previewText.selectionStart ?? 0) : 0;
        const sEnd = previewText ? (previewText.selectionEnd ?? 0) : 0;
        const selection = (sEnd > sStart) ? { start: sStart, end: sEnd } : { start: 0, end: fullLen };
        const userComment = previewCommentEl?.value.trim() || null;
        const collections = [...this.selectedPreviewTopics];

        try {
          previewConfirmBtn.disabled = true;
          setStatus('正在执行哈希与摘录区间校验并写入不可变原文…');
          const saved = await API.confirmIntake(preview.preview_id, {
            expectedRevision: preview.preview_revision,
            expectedHash: preview.preview_hash,
            idempotencyKey: `intake-${preview.preview_id}-${Date.now()}`,
            selection,
            collections,
            userComment,
            scope,
          });
          this.activeIntakePreview = null;
          if (previewCard) previewCard.hidden = true;
          if (urlInput) urlInput.value = '';
          setStatus('');
          material.close();
          window.dispatchEvent(new CustomEvent('library-note-saved'));
          this.showToast('资料已通过两步预览确认并保存为笔记。', 'success');
          if (saved.note_id) await this.jumpToNote(saved.note_id);
        } catch (err) {
          setStatus(`确认入库失败：${err.message}`, true);
        } finally {
          previewConfirmBtn.disabled = false;
        }
      };
    }

    if (mediaBtn && mediaInput) {
      mediaBtn.onclick = () => mediaInput.click();
      mediaInput.onchange = async () => {
        const files = [...(mediaInput.files || [])];
        mediaInput.value = '';
        if (!files.length) return;
        const scope = this.universe?.scope || 'default';
        setStatus(`正在上传并建立多媒体资料（共 ${files.length} 项）…`);
        let lastNoteId = null;
        for (const file of files) {
          try {
            const res = await API.uploadMedia(file, { scope, title: file.name, createNote: true });
            if (res.note?.id) lastNoteId = res.note.id;
          } catch (err) {
            this.showToast(`上传「${file.name}」失败：${err.message}`, 'error');
          }
        }
        setStatus('');
        if (lastNoteId) {
          material.close();
          window.dispatchEvent(new CustomEvent('library-note-saved'));
          this.showToast('已导入多媒体资料并开启预览。', 'success');
          await this.jumpToNote(lastNoteId);
        }
      };
    }

    if (urlSubmit && urlInput) {
      urlSubmit.onclick = async () => {
        const url = urlInput.value.trim();
        if (!url) { setStatus('请输入有效的 https:// 或 http:// 网页地址', true); return; }
        const scope = this.universe?.scope || 'default';
        try {
          urlSubmit.disabled = true;
          setStatus('正在抓取网页并生成安全预览…');
          const preview = await API.prepareIntake({ sourceType: 'web_capture', url, scope });
          if (preview.state !== 'PREVIEW_READY' || !preview.content) {
            throw new Error(preview.error_message || '网页正文提取失败');
          }
          setStatus('网页正文已提取，请在下方预览框核对或划选段落后点击「确认入库」。');
          await openPreviewCard(preview);
        } catch (err) {
          setStatus(`抓取失败：${err.message}`, true);
        } finally {
          urlSubmit.disabled = false;
        }
      };
    }

    if (textBtn && textInput) {
      textBtn.onclick = () => textInput.click();
      textInput.onchange = async () => {
        const file = textInput.files?.[0];
        textInput.value = '';
        if (!file) return;
        const scope = this.universe?.scope || 'default';
        try {
          setStatus(`正在解析文件「${file.name}」并生成预览…`);
          const preview = await API.uploadIntakeFile(file, { scope, title: file.name });
          if (preview.state !== 'PREVIEW_READY' || !preview.content) {
            throw new Error(preview.error_message || '文件解析失败');
          }
          setStatus(`文件「${file.name}」已就绪，请核对或划选段落后点击「确认入库」。`);
          await openPreviewCard(preview);
        } catch (err) {
          setStatus(`导入失败：${err.message}`, true);
        }
      };
    }
  }
  /** 右侧面板：双标签页（主题 Collections / AI 机甲 Observers）与折叠状态联动。 */
  initLegendTabs() {
    const legend = document.getElementById('neural-legend');
    const tabTopics = document.getElementById('legend-tab-topics');
    const tabMecha = document.getElementById('legend-tab-mecha');
    const paneTopics = document.getElementById('legend-pane-topics');
    const paneMecha = document.getElementById('legend-pane-mecha');
    const collapseToggle = document.getElementById('neural-legend-toggle');
    if (!legend) return;

    const switchTab = (tab) => {
      const isTopics = tab === 'topics';
      if (tabTopics) {
        tabTopics.classList.toggle('active', isTopics);
        tabTopics.setAttribute('aria-selected', String(isTopics));
      }
      if (tabMecha) {
        tabMecha.classList.toggle('active', !isTopics);
        tabMecha.setAttribute('aria-selected', String(!isTopics));
      }
      if (paneTopics) {
        paneTopics.classList.toggle('active', isTopics);
        paneTopics.hidden = !isTopics;
      }
      if (paneMecha) {
        paneMecha.classList.toggle('active', !isTopics);
        paneMecha.hidden = isTopics;
      }
      // 如果面板之前处于折叠状态，点击标签页自动展开
      if (legend.getAttribute('data-collapsed') === 'true') {
        legend.removeAttribute('data-collapsed');
        if (collapseToggle) collapseToggle.setAttribute('aria-expanded', 'true');
        if (window.innerWidth < 1366) this.universe?.toggleDirectory(false);
      }
      try { localStorage.setItem('neural-legend-active-tab', tab); } catch {}
    };

    if (tabTopics) tabTopics.addEventListener('click', () => switchTab('topics'));
    if (tabMecha) tabMecha.addEventListener('click', () => switchTab('mecha'));

    if (collapseToggle) {
      collapseToggle.addEventListener('click', () => {
        const isCollapsed = legend.getAttribute('data-collapsed') === 'true';
        const next = !isCollapsed;
        if (next) {
          legend.setAttribute('data-collapsed', 'true');
          collapseToggle.setAttribute('aria-expanded', 'false');
        } else {
          legend.removeAttribute('data-collapsed');
          collapseToggle.setAttribute('aria-expanded', 'true');
          if (window.innerWidth < 1366) this.universe?.toggleDirectory(false);
        }
        try { localStorage.setItem('neural-legend-collapsed', next ? '1' : '0'); } catch {}
      });
    }

    try {
      if (localStorage.getItem('neural-legend-collapsed') === '1') {
        legend.setAttribute('data-collapsed', 'true');
        if (collapseToggle) collapseToggle.setAttribute('aria-expanded', 'false');
      }
      const savedTab = localStorage.getItem('neural-legend-active-tab');
      if (savedTab === 'mecha') switchTab('mecha');
    } catch {}
  }
  /** 立即把指纹基准同步为后端当前值（自己刚保存过内容时避免触发冗余自动刷新）。 */
  async syncFingerprintSoon() {
    try { this.contentFingerprint = (await API.getUniverseFingerprint(this.universe?.scope || 'default')).fingerprint; } catch {}
  }
  /** 4s 周期轮询：指纹变化 → 防抖 1.2s 合并 → 自动重载星图。 */
  async liveRefreshTick() {
    if (document.hidden) return;
    let fp;
    try { fp = (await API.getUniverseFingerprint(this.universe?.scope || 'default')).fingerprint; } catch { return; }
    if (this.contentFingerprint === null) { this.contentFingerprint = fp; return; }
    if (fp === this.contentFingerprint) return;
    if (this.universe?.scene?.interacting) return; // 用户正在拖拽 3D 场景：留待下一轮检测（基准未动，变化不会丢）
    this.contentFingerprint = fp;
    clearTimeout(this.liveRefreshTimer);
    this.liveRefreshTimer = setTimeout(() => this.refreshFromContentChange(), 1200);
  }
  refreshFromContentChange() {
    this.universe?.invalidateCache();
    this.universe?.load();
    this.showToast('↻ 检测到内容更新，星图已自动刷新', 'info');
  }
  /**
   * 顶部「同步」按钮：一键重建派生索引与图谱投影，然后刷新星图。
   * 全部端点都是幂等的派生数据重建，不会删除原文、笔记或记忆。
   */
  async syncLibrary() {
    const btn = document.getElementById('universe-sync');
    if (!btn || btn.dataset.busy === '1') return;
    const label = btn.textContent;
    btn.dataset.busy = '1';
    btn.disabled = true;
    const done = [];
    try {
      const steps = [
        ['关键词索引', '/api/retrieval/index/rebuild'],
        ['向量索引', '/api/retrieval/vector-index/rebuild'],
      ];
      for (const [name, path] of steps) {
        btn.textContent = `同步${name}…`;
        const res = await API.rebuildIndex(path);
        done.push(`${name} gen ${res?.generation ?? '—'}`);
      }
      btn.textContent = '同步图谱…';
      try {
        await API.rebuildGraphProjection();
        done.push('图谱投影');
      } catch (e) {
        this.showToast(`图谱投影重建失败（索引部分已完成）：${e.message}`, 'error');
      }
      this.universe?.invalidateCache();
      this.universe?.load();
      this.showToast(`✓ 同步完成：${done.join(' · ')}`, 'success');
    } catch (e) {
      const prefix = done.length ? `已完成 ${done.join(' · ')}；` : '';
      this.showToast(`同步中断：${prefix}${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = label;
      delete btn.dataset.busy;
    }
  }
  syncReaderClosed() {
    const noteOpen = this.notesMgr?.modalBackdrop?.classList.contains('open');
    const memOpen = this.memoryNet?.drawer?.classList.contains('open');
    if (!noteOpen && !memOpen && this.store.get('activeReader')) {
      this.store.set({ activeReader: null });
    }
  }
  closeReaders() {
    if (this.notesMgr.modalBackdrop.classList.contains('open')&&!this.notesMgr.closeModal()) return false;
    this.readRequest++;
    this.memoryNet.focusRequest=(this.memoryNet.focusRequest||0)+1;
    this.memoryNet.closeDrawer();
    this.store.set({ activeReader: null });
    return true;
  }
  showUniverse(){
    this.store.set({ viewMode: 'galaxy' });
    const maintenance=document.getElementById('workspace-maintenance');maintenance.hidden=true;maintenance.inert=true;maintenance.classList.remove('active');maintenance.style.display='none';
    document.getElementById('workspace-universe').hidden=false;
    document.getElementById('universe-maintenance').textContent='维护';
    this.universe?.scene?.setActive(true);
  }
  async toggleMaintenance(){
    if(this.store.get('viewMode')==='maintenance'){this.showUniverse();return;}
    if(!this.closeReaders())return;
    this.store.set({ viewMode: 'maintenance' });
    this.universe.scene?.setActive(false);
    document.getElementById('workspace-universe').hidden=true;
    const panel=document.getElementById('workspace-maintenance');panel.hidden=false;panel.inert=false;panel.classList.add('active');panel.style.display='flex';
    document.getElementById('universe-maintenance').textContent='返回宇宙';
    await this.maintenanceMgr.loadData();
  }
  async jumpToBacklog(id){await this.toggleMaintenance();this.maintenanceMgr.filterByTargetNode(id);}
  async jumpToNote(id,revision){
    if(!this.closeReaders())return;
    this.showUniverse();const request=++this.readRequest;
    try{
      const note=await API.fetchNote(id);
      if(request!==this.readRequest)return;
      if(note.scope!==this.universe.scope)await this.universe.setScope(note.scope,{closeReaders:false});
      if(request!==this.readRequest)return;
      await this.notesMgr.openNote(note);
      if(revision&&revision!==note.revision) {await this.notesMgr.loadRevision(revision);this.notesMgr.revisionSelect.value=revision;}
      this.store.set({ activeReader: { type: 'note', id, revision } });
    }catch(e){if(request===this.readRequest)this.showToast(`无法打开原文：${e.message}`,'error');}
  }
  async jumpToMemory(id){
    if(!this.closeReaders())return;
    this.showUniverse();const request=++this.readRequest;
    try{
      const [node,edges]=await Promise.all([API.fetchMemoryNode(id),API.fetchNodeEdges(id)]);
      if(request!==this.readRequest)return;
      if(!node.is_active)throw new Error('这条记忆当前不可用。');
      if(node.scope!==this.universe.scope)await this.universe.setScope(node.scope,{closeReaders:false});
      if(request!==this.readRequest)return;
      this.memoryNet.scopeInput.value=this.universe.scope;
      this.memoryNet.nodes=[];this.memoryNet.edges=[];this.memoryNet.nodeMap.clear();
      this.memoryNet.processData([node],edges.filter(e=>e.scope===this.universe.scope));
      this.memoryNet.selectedNode=this.memoryNet.nodeMap.get(id);
      this.memoryNet.returnFocus=document.activeElement;
      this.memoryNet.openDrawer(this.memoryNet.selectedNode);
      this.memoryNet.drawerCloseBtn.focus();
      this.store.set({ activeReader: { type: 'memory', id } });
    }catch(e){if(request===this.readRequest)this.showToast(`无法打开记忆：${e.message}`,'error');}
  }
  async checkHealth(){
    const el = document.getElementById('universe-health');
    if (!el) return;
    try {
      await API.checkHealth();
      el.innerHTML = '<span class="status-dot-pulse"></span> MCP 就绪';
      el.title = '外部 Agent 通过 MCP 协议操控 · 模型无关开放中枢';
    } catch {
      el.innerHTML = '<span class="status-dot-pulse" style="background:#ef4444;box-shadow:0 0 6px #ef4444;"></span> 连接已断开';
      el.title = '与本地知识库服务连接异常';
    }
  }
  showToast(message,type='info'){
    const el=document.createElement('div');el.className=`toast ${type}`;el.textContent=message;document.getElementById('toast-container').append(el);setTimeout(()=>el.remove(),6500);
  }
}
