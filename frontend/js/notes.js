/**
 * AI Library - My Notes Workspace & User-Friendly Multi-Media Reader
 * Supports:
 * - Read / Split / Edit modes with live Markdown + Code + Table + Callout rendering
 * - Automatic Chat History Session stitching (1/N .. N/N) & Turn-by-Turn Chat Bubble UI
 * - Inline Multimedia preview (PDF viewer, Image Lightbox, Video/Audio player)
 * - Drag & drop / Clipboard paste / Button upload of PDF, Image, Video, Audio into notes
 * - Resizable & Fullscreen Expandable Reader panel
 */

import { API } from './api.js';
import {
  renderMarkdownToHtml,
  renderChatSessionHtml,
  bindRenderedContentInteractions,
  escapeHtml,
} from './content-renderer.js';

export class NotesManager {
  constructor({ onToast, onClose, onNavigateToNote, onNavigateToMemory } = {}) {
    this.onToast = onToast || console.log;
    this.onClose = onClose || null;
    this.onNavigateToNote = onNavigateToNote || null;
    this.onNavigateToMemory = onNavigateToMemory || null;

    this.notes = [];
    this.activeNote = null;
    this.activeRevision = null;
    this.activeScope = 'default';
    this.savedDraft = { title: '', content: '' };
    this.isSaving = false;
    this.revisionRequest = 0;

    // Reader state
    this.viewMode = 'read'; // 'read' | 'split' | 'edit'
    this.chatBundle = null;
    this.chatFullSessionMode = true;
    this.chatRoleFilter = 'all';
    this.chatSearchQuery = '';
    this.resolvedMedia = [];

    // DOM Elements
    this.grid = document.getElementById('notes-grid');
    this.searchInput = document.getElementById('notes-search-input');
    this.scopeInput = document.getElementById('notes-scope-input');
    this.searchRequest = 0;
    this.countLabel = document.getElementById('notes-count-label');
    this.btnCreate = document.getElementById('btn-create-note');

    // Reader Elements (shared shell with the memory reader)
    this.modalBackdrop = document.getElementById('note-modal-backdrop');
    this.modalDialog = this.modalBackdrop?.querySelector('.note-modal-dialog');
    this.modalTitle = document.getElementById('note-edit-title');
    this.modalContent = document.getElementById('note-edit-content');
    this.renderedView = document.getElementById('note-rendered-view');
    this.modalCloseBtn = document.getElementById('note-modal-close');
    this.tabEditorBtn = document.getElementById('modal-tab-editor');
    this.tabSourceBtn = document.getElementById('modal-tab-source');
    this.tabAnalysisBtn = document.getElementById('modal-tab-analysis');
    this.tabButtons = [...document.querySelectorAll('#note-modal-backdrop [data-modaltab]')];
    this.modeButtons = [...document.querySelectorAll('#note-view-mode-group [data-note-mode]')];
    this.btnInsertMedia = document.getElementById('btn-insert-media');
    this.mediaFileInput = document.getElementById('note-media-file-input');
    this.bodyEditor = document.getElementById('modal-body-editor');
    this.bodySource = document.getElementById('modal-body-source');
    this.bodyAnalysis = document.getElementById('modal-body-analysis');
    this.claimsList = document.getElementById('analysis-claims-list');
    this.revisionSelect = document.getElementById('note-revision-select');
    this.hashLabel = document.getElementById('note-immutable-hash');
    this.readerMeta = document.getElementById('note-reader-meta');
    this.noteKind = document.getElementById('note-reader-kind');
    this.sourceId = document.getElementById('note-source-id');
    this.sourceAuthor = document.getElementById('note-source-author');
    this.sourceScope = document.getElementById('note-source-scope');
    this.sourceCreated = document.getElementById('note-source-created');
    this.sourceTopics = document.getElementById('note-source-topics');
    this.btnSave = document.getElementById('btn-save-note');
    this.btnExtract = document.getElementById('btn-trigger-ai-analysis');

    this.bindEvents();
    this.initReaderErgonomics();
  }

  initReaderErgonomics() {
    // Expand / Fullscreen Zen toggle across all reader panels
    document.querySelectorAll('[data-toggle-reader-expand]').forEach(btn => {
      if (btn._boundExpand) return;
      btn._boundExpand = true;
      btn.addEventListener('click', () => {
        const expanded = document.body.classList.toggle('reader-expanded');
        document.querySelectorAll('[data-toggle-reader-expand]').forEach(b => {
          b.textContent = expanded ? '⤡' : '⤢';
          b.title = expanded ? '退出宽屏阅读' : '切换宽屏/全屏阅读';
        });
      });
    });

    // Left-edge drag resize across reader panels
    document.querySelectorAll('[data-resize-reader]').forEach(handle => {
      if (handle._boundResize) return;
      handle._boundResize = true;
      handle.addEventListener('mousedown', e => {
        e.preventDefault();
        document.body.classList.add('reader-resizing');
        const onMove = moveEvent => {
          const widthPx = Math.max(380, Math.min(window.innerWidth - 24, window.innerWidth - moveEvent.clientX - 12));
          document.documentElement.style.setProperty('--reader-width', `${widthPx}px`);
        };
        const onUp = () => {
          document.body.classList.remove('reader-resizing');
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      });
    });
  }

  bindEvents() {
    this.btnCreate.addEventListener('click', () => this.openNewNote());
    this.modalCloseBtn.addEventListener('click', () => this.closeModal());
    this.modalBackdrop.addEventListener('click', e => {
      if (e.target === this.modalBackdrop) this.closeModal();
    });

    // Reader view tabs (内容 / 来源 / 演变)
    for (const button of this.tabButtons) {
      button.addEventListener('click', () => this.switchModalTab(button.dataset.modaltab));
    }

    // Read / Split / Edit mode switcher
    for (const btn of this.modeButtons) {
      btn.addEventListener('click', () => this.setNoteViewMode(btn.dataset.noteMode));
    }

    this.modalTitle.addEventListener('input', () => {
      if (/[\r\n]/.test(this.modalTitle.value)) this.modalTitle.value = this.modalTitle.value.replace(/[\r\n]+/g, ' ');
      this.autoGrowTitle();
    });

    // Live update rendered preview when editing in Split mode
    this.modalContent.addEventListener('input', () => {
      if (this.viewMode === 'split') {
        clearTimeout(this.livePreviewTimer);
        this.livePreviewTimer = setTimeout(() => this.renderContentPreview(), 120);
      }
    });

    // Multimedia insert button, clipboard paste, and drag-and-drop
    if (this.btnInsertMedia && this.mediaFileInput) {
      this.btnInsertMedia.addEventListener('click', () => this.mediaFileInput.click());
      this.mediaFileInput.addEventListener('change', async () => {
        const files = [...(this.mediaFileInput.files || [])];
        this.mediaFileInput.value = '';
        if (files.length) await this.insertMediaFiles(files);
      });
    }

    this.modalContent.addEventListener('paste', async e => {
      const items = [...(e.clipboardData?.items || [])];
      const files = items
        .filter(it => it.kind === 'file')
        .map(it => it.getAsFile())
        .filter(Boolean);
      if (files.length > 0) {
        e.preventDefault();
        await this.insertMediaFiles(files);
      }
    });

    if (this.modalDialog) {
      this.modalDialog.addEventListener('dragover', e => {
        if (e.dataTransfer?.types?.includes('Files')) {
          e.preventDefault();
          this.modalDialog.classList.add('drag-over');
        }
      });
      this.modalDialog.addEventListener('dragleave', e => {
        if (!this.modalDialog.contains(e.relatedTarget)) {
          this.modalDialog.classList.remove('drag-over');
        }
      });
      this.modalDialog.addEventListener('drop', async e => {
        this.modalDialog.classList.remove('drag-over');
        const files = [...(e.dataTransfer?.files || [])];
        if (files.length > 0) {
          e.preventDefault();
          await this.insertMediaFiles(files);
        }
      });
    }

    // Save and AI actions
    this.btnSave.addEventListener('click', () => this.saveActiveNote());
    this.btnExtract.addEventListener('click', () => this.extractClaims());

    // Revision picker
    this.revisionSelect.addEventListener('change', e => {
      const revNum = parseInt(e.target.value, 10);
      this.loadRevision(revNum);
    });

    // Search filter
    this.searchInput.addEventListener('input', () => {
      ++this.searchRequest;
      clearTimeout(this.searchTimer);
      this.countLabel.textContent = '搜索中…';
      this.searchTimer = setTimeout(() => this.loadNotes(), 250);
    });
    this.scopeInput.addEventListener('change', () => this.loadNotes());

    window.addEventListener('beforeunload', e => {
      if (this.modalBackdrop.classList.contains('open') && this.hasUnsavedChanges()) {
        e.preventDefault();
        e.returnValue = '';
      }
    });

    // Keyboard navigation
    window.addEventListener('keydown', e => {
      if (e.key === 'Tab' && this.modalBackdrop.classList.contains('open')) {
        const items = [...this.modalBackdrop.querySelectorAll('button, input, textarea, select')]
          .filter(el => !el.disabled && el.getClientRects().length);
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
      if (e.key === 'Escape' && this.modalBackdrop.classList.contains('open')) {
        if (document.getElementById('media-lightbox-modal')?.classList.contains('open')) return;
        this.closeModal();
      }
    });
  }

  setNoteViewMode(mode) {
    this.viewMode = ['read', 'split', 'edit'].includes(mode) ? mode : 'read';
    if (this.bodyEditor) {
      this.bodyEditor.dataset.mode = this.viewMode;
    }
    for (const btn of this.modeButtons) {
      const active = btn.dataset.noteMode === this.viewMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    }
    if (this.viewMode === 'read' || this.viewMode === 'split') {
      this.renderContentPreview();
    }
    if (this.viewMode === 'edit') {
      this.modalContent.focus({ preventScroll: true });
    }
  }

  async insertMediaFiles(files) {
    const scope = this.activeNote?.scope || this.scopeInput.value.trim() || 'default';
    for (const file of files) {
      try {
        this.onToast(`正在上传多媒体「${file.name}」…`, 'info');
        const res = await API.uploadMedia(file, {
          scope,
          noteId: this.activeNote?.id || null,
          createNote: false,
        });
        const asset = res.media;
        if (!asset) continue;
        this.resolvedMedia.push(asset);
        const label = asset.media_kind === 'pdf'
          ? `PDF 预览：${asset.filename}`
          : asset.media_kind === 'video'
            ? `视频预览：${asset.filename}`
            : asset.media_kind === 'audio'
              ? `音频预览：${asset.filename}`
              : asset.filename;
        const snippet = `\n![${label}](${asset.url})\n`;
        const start = this.modalContent.selectionStart ?? this.modalContent.value.length;
        const end = this.modalContent.selectionEnd ?? this.modalContent.value.length;
        const val = this.modalContent.value;
        this.modalContent.value = val.slice(0, start) + snippet + val.slice(end);
        if (!this.modalTitle.value.trim()) {
          this.modalTitle.value = asset.filename;
          this.autoGrowTitle();
        }
        this.renderContentPreview();
        this.onToast(`已插入「${asset.filename}」，点击「保存版本」即可持久保存。`, 'success');
      } catch (err) {
        this.onToast(`上传「${file.name}」失败：${err.message}`, 'error');
      }
    }
  }

  renderContentPreview() {
    if (!this.renderedView) return;
    const currentText = this.modalContent.value || '';
    const isModifiedFromSaved = currentText !== this.savedDraft.content;

    // If viewing an unmodified Chat History transcript note and we have a loaded chatBundle
    if (!isModifiedFromSaved && this.chatBundle?.is_chat_session && !this.chatBundle.is_summary && (this.chatBundle.messages?.length > 0)) {
      this.renderedView.innerHTML = renderChatSessionHtml(this.chatBundle, {
        fullSessionMode: this.chatFullSessionMode,
        roleFilter: this.chatRoleFilter,
        searchQuery: this.chatSearchQuery,
      });
      this.bindChatSessionControls();
      bindRenderedContentInteractions(this.renderedView, {
        onNavigateToNote: (id, r) => this.handleJumpToNote(id, r),
        onNavigateToMemory: id => this.onNavigateToMemory?.(id),
        onToast: this.onToast,
      });
      return;
    }

    // If viewing a Chat Summary note that also has the full session messages/media available
    let html = renderMarkdownToHtml(currentText, this.resolvedMedia);
    if (!isModifiedFromSaved && this.chatBundle?.is_chat_session && this.chatBundle.is_summary && (this.chatBundle.messages?.length > 0)) {
      const mediaCount = (this.chatBundle.session_media || []).length;
      html += `
        <details class="chat-summary-full-session-box" ${mediaCount > 0 ? 'open' : ''}>
          <summary class="chat-summary-full-session-toggle">
            💬 展开本场完整对话原文与多媒体附件（共 ${this.chatBundle.messages.length} 条消息${mediaCount ? ` · 📎 ${mediaCount} 项多媒体` : ''}）
          </summary>
          <div class="chat-summary-embedded-timeline">
            ${renderChatSessionHtml(this.chatBundle, {
              fullSessionMode: true,
              roleFilter: this.chatRoleFilter,
              searchQuery: this.chatSearchQuery,
            })}
          </div>
        </details>
      `;
    }

    this.renderedView.innerHTML = html;
    this.bindChatSessionControls();
    bindRenderedContentInteractions(this.renderedView, {
      onNavigateToNote: (id, r) => this.handleJumpToNote(id, r),
      onNavigateToMemory: id => this.onNavigateToMemory?.(id),
      onToast: this.onToast,
    });
  }

  bindChatSessionControls() {
    if (!this.renderedView) return;

    this.renderedView.querySelectorAll('[data-chat-mode]').forEach(btn => {
      btn.onclick = async () => {
        const wantFull = btn.dataset.chatMode === 'full';
        if (this.chatFullSessionMode === wantFull) return;
        this.chatFullSessionMode = wantFull;
        if (this.activeNote) {
          await this.loadChatSessionAndMedia(this.activeNote);
        }
      };
    });

    const chunkSelect = this.renderedView.querySelector('.chat-chunk-select');
    if (chunkSelect) {
      chunkSelect.onchange = () => {
        const targetId = chunkSelect.value;
        if (targetId && targetId !== this.activeNote?.id) {
          this.handleJumpToNote(targetId);
        }
      };
    }

    this.renderedView.querySelectorAll('[data-role-filter]').forEach(btn => {
      btn.onclick = () => {
        this.chatRoleFilter = btn.dataset.roleFilter || 'all';
        this.renderContentPreview();
      };
    });

    const searchInput = this.renderedView.querySelector('.chat-search-input');
    if (searchInput) {
      searchInput.oninput = () => {
        this.chatSearchQuery = searchInput.value;
        const pos = searchInput.selectionStart;
        this.renderContentPreview();
        const nextInput = this.renderedView.querySelector('.chat-search-input');
        if (nextInput) {
          nextInput.focus();
          try { nextInput.setSelectionRange(pos, pos); } catch {}
        }
      };
    }
  }

  async handleJumpToNote(noteId, revision) {
    if (this.onNavigateToNote) {
      await this.onNavigateToNote(noteId, revision);
      return;
    }
    try {
      const note = await API.fetchNote(noteId, revision);
      await this.openNote(note);
    } catch (err) {
      this.onToast(`无法打开笔记：${err.message}`, 'error');
    }
  }

  async loadChatSessionAndMedia(note) {
    if (!note || !note.id) return;
    const reqId = this.revisionRequest;
    try {
      const bundle = await API.fetchNoteChatSession(note.id, {
        scope: note.scope || 'default',
        fullSession: this.chatFullSessionMode,
      });
      if (reqId !== this.revisionRequest || this.activeNote?.id !== note.id) return;
      this.chatBundle = bundle;
      this.resolvedMedia = bundle.is_chat_session ? (bundle.session_media || []) : (bundle.media || []);
      if (bundle.is_chat_session && this.noteKind) {
        this.noteKind.textContent = bundle.is_summary ? '💬 聊天摘要' : `💬 对话原文 (${bundle.current_part}/${bundle.total_parts})`;
      }
      this.renderContentPreview();
    } catch (_) {
      // Fallback gracefully if endpoint is unavailable in isolated unit tests
    }
  }

  async loadNotes() {
    const request = ++this.searchRequest;
    const query = this.searchInput.value.trim();
    const scope = this.scopeInput.value.trim() || 'default';
    try {
      const result = query ? await API.searchDocuments(query, 'NOTES', scope) : null;
      const notes = result ? result.items : await API.fetchNotes(scope);
      if (request !== this.searchRequest) return;
      this.notes = notes;
      this.searchResult = result;
      this.renderNotes();
    } catch (err) {
      if (request !== this.searchRequest) return;
      this.notes = [];
      this.renderNotes();
      this.countLabel.textContent = '搜索失败；修改关键词后重试。';
      this.onToast(`载入笔记失败：${err.message}`, 'error');
    }
  }

  renderNotes() {
    const filtered = this.notes;

    this.countLabel.textContent = this.searchResult
      ? `命中 ${filtered.length} / ${this.searchResult.total} · ${this.searchResult.mode === 'canonical_fallback' ? '实时回退' : '已建索引'}`
      : `${filtered.length} 条笔记 · 最近 100 条`;
    this.grid.innerHTML = '';

    if (filtered.length === 0) {
      this.grid.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 60px 20px; color: var(--text-dim);">
          <div style="font-size: 32px; margin-bottom: 12px; opacity: 0.5;">📝</div>
          <div style="font-size: 15px; font-weight: 600; color: var(--text-muted);">没有找到笔记</div>
          <div style="font-size: 13px; margin-top: 4px;">点击「新建笔记」记录你的想法。</div>
        </div>
      `;
      return;
    }

    const colors = ['color-amber', 'color-emerald', 'color-sky', 'color-purple', 'color-rose'];

    filtered.forEach(note => {
      const colorClass = colors[Math.abs(this.hashString(note.id)) % colors.length];
      const card = document.createElement('article');
      card.className = `sticky-note ${colorClass}`;
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', `Note: ${note.title}`);

      const dateStr = note.created_at ? new Date(note.created_at).toLocaleDateString() : '刚刚';

      card.innerHTML = `
        <div>
          <div class="note-origin">${note.author?.startsWith('mcp-agent') ? 'Agent 整理 · 保留来源' : '笔记 · 原文'}</div>
          <div class="note-header">
            <h3 class="note-title">${escapeHtml(note.title || '未命名笔记')}</h3>
            <span class="note-rev-badge">v${note.revision || 1}</span>
          </div>
          <p class="note-preview">${escapeHtml(note.content || '')}</p>
        </div>
        <div class="note-footer">
          <span class="note-scope">${escapeHtml(note.scope || 'default')}</span>
          <span>${dateStr}</span>
        </div>
      `;

      card.addEventListener('click', () => this.openNote(note));
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.openNote(note);
        }
      });

      this.grid.appendChild(card);
    });
  }

  hashString(str) {
    let hash = 0;
    for (let i = 0; i < (str || '').length; i++) {
      hash = (hash << 5) - hash + str.charCodeAt(i);
      hash |= 0;
    }
    return hash;
  }

  escapeHtml(str) {
    return escapeHtml(str);
  }

  openNewNote() {
    this.activeScope = this.scopeInput.value.trim() || 'default';
    this.revisionRequest++;
    this.returnFocus = document.activeElement;
    this.savedDraft = { title: '', content: '' };
    this.activeNote = null;
    this.activeRevision = null;
    this.chatBundle = null;
    this.resolvedMedia = [];
    this.modalTitle.value = '';
    this.modalContent.value = '';
    this.revisionSelect.innerHTML = '<option value="1">v1（草稿）</option>';
    this.hashLabel.textContent = '保存后生成';
    this.renderReaderMeta(null);
    this.renderSourcePane(null);
    this.switchModalTab('editor');
    this.setNoteViewMode('edit');
    this.autoGrowTitle();
    this.claimsList.innerHTML = `
      <div style="font-size: 13px; color: var(--text-dim); text-align: center; padding: 20px;">
        请先保存笔记，再生成待审核的记忆候选。
      </div>
    `;
    this.modalBackdrop.classList.add('open');
    this.autoGrowTitle();
    this.modalTitle.focus();
  }

  async openNote(note) {
    this.revisionRequest++;
    if (!this.modalBackdrop.classList.contains('open')) this.returnFocus = document.activeElement;
    this.savedDraft = { title: note.title || '', content: note.content || '' };

    this.activeNote = note;
    this.activeRevision = note.revision || 1;
    this.chatBundle = null;
    this.resolvedMedia = [];
    this.chatRoleFilter = 'all';
    this.chatSearchQuery = '';
    this.modalTitle.value = note.title || '';
    this.modalContent.value = note.content || '';
    this.hashLabel.textContent = note.content_hash || '未记录';

    // Populate revisions dropdown
    this.revisionSelect.innerHTML = '';
    const maxRev = note.revision || 1;
    for (let r = maxRev; r >= 1; r--) {
      const opt = document.createElement('option');
      opt.value = r;
      opt.textContent = `v${r}${r === maxRev ? '（当前）' : ''}`;
      if (r === maxRev) opt.selected = true;
      this.revisionSelect.appendChild(opt);
    }

    this.renderReaderMeta(note);
    this.renderSourcePane(note);
    this.switchModalTab('editor');
    this.setNoteViewMode('read');
    this.loadNoteClaims(note.id);
    this.loadChatSessionAndMedia(note);
    this.modalBackdrop.classList.add('open');
    this.autoGrowTitle();
    this.modalTitle.focus({ preventScroll: true });
  }

  async loadRevision(revisionNum) {
    if (!this.activeNote) return;
    if (this.isSaving || (this.hasUnsavedChanges() && !window.confirm('放弃未保存的修改并载入这个版本？'))) {
      this.revisionSelect.value = this.activeRevision;
      return;
    }
    const noteId = this.activeNote.id;
    const requestId = ++this.revisionRequest;
    const draft = { title: this.modalTitle.value, content: this.modalContent.value };
    try {
      const revNote = await API.fetchNote(noteId, revisionNum);
      if (requestId !== this.revisionRequest || this.activeNote?.id !== noteId) return;
      if (this.modalTitle.value !== draft.title || this.modalContent.value !== draft.content) {
        this.revisionSelect.value = this.activeRevision;
        this.onToast('已保留版本载入期间输入的修改。请先保存或放弃，再切换版本。', 'info');
        return;
      }
      this.modalTitle.value = revNote.title || '';
      this.modalContent.value = revNote.content || '';
      this.hashLabel.textContent = revNote.content_hash || '未记录';
      this.activeRevision = revisionNum;
      this.savedDraft = { title: revNote.title || '', content: revNote.content || '' };
      this.renderContentPreview();
    } catch (err) {
      if (requestId !== this.revisionRequest || this.activeNote?.id !== noteId) return;
      this.revisionSelect.value = this.activeRevision;
      this.onToast(`载入版本 ${revisionNum} 失败：${err.message}`, 'error');
    }
  }

  switchModalTab(tabName) {
    const panes = { editor: this.bodyEditor, source: this.bodySource, analysis: this.bodyAnalysis };
    for (const [name, pane] of Object.entries(panes)) {
      if (!pane) continue;
      pane.style.display = name === tabName ? (name === 'editor' ? 'flex' : 'block') : 'none';
    }
    for (const button of this.tabButtons) {
      const active = button.dataset.modaltab === tabName;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  }

  autoGrowTitle() {
    this.modalTitle.style.height = 'auto';
    this.modalTitle.style.height = `${this.modalTitle.scrollHeight}px`;
  }

  formatDate(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  renderReaderMeta(note) {
    if (!this.readerMeta) return;
    const isAgent = String(note?.author || '').startsWith('mcp-agent');
    const isChat = String(note?.title || '').includes('聊天') || String(note?.content || '').includes('### 消息 ');
    if (this.noteKind) {
      this.noteKind.textContent = !note ? '新笔记' : isChat ? '💬 历史对话' : isAgent ? '来源原文' : '我的笔记';
    }
    if (!note) {
      this.readerMeta.textContent = '尚未保存 · 支持粘贴/拖拽图片、PDF、视频';
      return;
    }
    const parts = [];
    if (note.created_at) parts.push(this.formatDate(note.created_at));
    parts.push(isAgent ? 'Agent 整理 · 保留来源' : '用户笔记 · 原文');
    if (note.scope) parts.push(`范围 ${note.scope}`);
    this.readerMeta.textContent = parts.join(' · ');
  }

  renderSourcePane(note) {
    const set = (element, value) => { if (element) element.textContent = value; };
    const intakeDetails = document.getElementById('note-intake-details');
    const commentsList = document.getElementById('note-comments-list');
    const commentInput = document.getElementById('note-comment-input');
    const commentSubmit = document.getElementById('note-comment-submit');
    const lineageDetails = document.getElementById('note-lineage-details');

    if (commentSubmit && !commentSubmit._boundComment) {
      commentSubmit._boundComment = true;
      commentSubmit.addEventListener('click', async () => {
        if (!this.activeNote?.id || !commentInput) return;
        const txt = commentInput.value.trim();
        if (!txt) return;
        try {
          commentSubmit.disabled = true;
          await API.addIntakeComment(this.activeNote.id, {
            scope: this.activeNote.scope || 'default',
            content: txt,
            expectedRevision: this.activeNote.revision || 1,
          });
          commentInput.value = '';
          this.onToast('已添加独立用户批注（原文保持不变）。', 'success');
          await this.loadIntakeAndLineageDetails(this.activeNote);
        } catch (err) {
          this.onToast(`添加批注失败：${err.message}`, 'error');
        } finally {
          commentSubmit.disabled = false;
        }
      });
    }

    if (!note) {
      set(this.sourceId, '保存后生成');
      set(this.sourceAuthor, '—');
      set(this.sourceScope, this.activeScope || 'default');
      set(this.sourceCreated, '—');
      set(this.sourceTopics, '—');
      if (intakeDetails) intakeDetails.textContent = '新笔记尚未保存，暂无入库溯源。';
      if (commentsList) commentsList.replaceChildren();
      if (lineageDetails) lineageDetails.textContent = '新笔记尚未保存，暂无对话血缘。';
      return;
    }
    set(this.sourceId, note.id || '—');
    set(this.sourceAuthor, note.author || '—');
    set(this.sourceScope, note.scope || 'default');
    set(this.sourceCreated, this.formatDate(note.created_at));
    const topicTitles = (note.topics || []).map(t => t.title || t.id).filter(Boolean);
    const topicIds = note.topic_ids || [];
    set(this.sourceTopics, topicTitles.length ? topicTitles.join('、')
      : topicIds.length ? topicIds.join('、') : '尚未归入任何主题');
    this.loadIntakeAndLineageDetails(note);
  }

  async loadIntakeAndLineageDetails(note) {
    if (!note?.id) return;
    const reqId = this.revisionRequest;
    const intakeDetails = document.getElementById('note-intake-details');
    const commentsList = document.getElementById('note-comments-list');
    const lineageDetails = document.getElementById('note-lineage-details');

    if (intakeDetails) {
      try {
        const item = await API.fetchIntakeItem(note.id);
        if (reqId !== this.revisionRequest || this.activeNote?.id !== note.id) return;
        const prov = item.provenance || {};
        const sel = prov.selection ? `[${prov.selection.start}..${prov.selection.end}]` : '全文';
        intakeDetails.textContent = `入库方式：${prov.source_type || '直接创建'} · 摘录区间：${sel} · 原始引用：${prov.source_ref || '本地'} · 不可变标记：${item.immutable ? '是' : '当前版本'}`;
        if (commentsList) {
          commentsList.replaceChildren();
          const comments = item.comments || [];
          for (const c of comments) {
            const row = document.createElement('div');
            row.className = 'reader-note';
            row.style.padding = '6px 8px';
            row.style.borderRadius = '6px';
            row.style.background = 'rgba(255,255,255,0.04)';
            row.textContent = `💬 ${c.comment} — ${c.author || 'user'} (${this.formatDate(c.created_at)})`;
            commentsList.append(row);
          }
        }
      } catch {
        if (intakeDetails) intakeDetails.textContent = '本地直接创建或标准笔记原文（支持下方独立添加用户批注）。';
      }
    }

    if (lineageDetails) {
      try {
        const lin = await API.fetchLineageSource(note.id, note.scope);
        if (reqId !== this.revisionRequest || this.activeNote?.id !== note.id) return;
        const cands = lin.candidates || [];
        const mems = lin.memories || [];
        const handoffs = lin.handoffs || [];
        if (!cands.length && !mems.length && !handoffs.length) {
          lineageDetails.textContent = '该笔记当前尚未派生出候选记忆、正式记忆节点或任务交接记录。';
        } else {
          lineageDetails.innerHTML = `
            <div>派生候选记忆：<strong>${cands.length}</strong> 条 · 正式记忆节点：<strong>${mems.length}</strong> 个 · 关联交接：<strong>${handoffs.length}</strong> 项</div>
            ${mems.map(m => `<div style="margin-top:4px;font-size:12px;">🧠 记忆节点 <code>${escapeHtml(m.id)}</code> (v${m.revision ?? 1}) — ${escapeHtml(m.node_type || 'Concept')}</div>`).join('')}
          `;
        }
      } catch {
        if (lineageDetails) lineageDetails.textContent = '暂无对话血缘或派生记忆记录。';
      }
    }
  }

  hasUnsavedChanges() {
    return this.modalTitle.value !== this.savedDraft.title || this.modalContent.value !== this.savedDraft.content;
  }

  closeModal() {
    if (this.isSaving) return false;
    if (this.hasUnsavedChanges() && !window.confirm('放弃未保存的修改？选“取消”可以继续保留草稿。')) return false;
    this.revisionRequest++;
    this.modalBackdrop.classList.remove('open');
    this.activeNote = null;
    this.activeRevision = null;
    this.onClose?.();
    this.returnFocus?.focus();
    return true;
  }

  async saveActiveNote() {
    if (this.isSaving) return;
    const title = this.modalTitle.value;
    const content = this.modalContent.value;

    if (!title.trim() || !content.trim()) {
      this.onToast('笔记标题和内容都不能为空。', 'error');
      return;
    }

    try {
      this.isSaving = true;
      this.btnSave.disabled = true;
      this.modalTitle.readOnly = true;
      this.modalContent.readOnly = true;
      this.btnSave.textContent = '保存中…';

      const isNew = !this.activeNote;
      if (!this.activeNote) {
        const created = await API.createNote({
          title,
          content,
          scope: this.activeScope,
          author: 'user',
        });
        this.onToast('已新建笔记。', 'success');
        await this.loadNotes();
        this.openNote(created);
      } else {
        const revised = await API.reviseNote(this.activeNote.id, {
          expectedRevision: this.activeNote.revision,
          title,
          content,
          author: 'user',
        });
        this.onToast(`已保存为版本 v${revised.revision}。`, 'success');
        await this.loadNotes();
        this.openNote(revised);
      }
      window.dispatchEvent(new CustomEvent('library-note-saved', { detail: { note: this.activeNote, isNew } }));
    } catch (err) {
      this.onToast(`保存失败：${err.message}`, 'error');
    } finally {
      this.isSaving = false;
      this.modalTitle.readOnly = false;
      this.modalContent.readOnly = false;
      this.btnSave.disabled = false;
      this.btnSave.textContent = '保存版本';
    }
  }

  async extractClaims() {
    if (!this.activeNote) {
      this.onToast('请先保存笔记，再提取候选。', 'error');
      return;
    }

    try {
      this.btnExtract.disabled = true;
      this.btnExtract.textContent = '提取中…';

      if (this.hasUnsavedChanges()) throw new Error('请先保存修改，再提取候选。');
      const requestId = this.revisionRequest;
      const sourceNote = await API.fetchNote(this.activeNote.id, this.activeRevision);
      if (requestId !== this.revisionRequest || this.activeNote?.id !== sourceNote.id || this.hasUnsavedChanges()) return;
      const claimText = sourceNote.content
        .split('\n')
        .map(s => s.trim())
        .find(s => s && !s.startsWith('<!--'));
      if (!claimText) throw new Error('这个版本没有可供审核的文字。');

      const intakeRes = await API.submitCandidate({
        sourceId: sourceNote.id,
        sourceRevision: sourceNote.revision,
        claim: claimText,
        suggestedNodeType: 'Concept',
        payload: {
          title: sourceNote.title,
          summary: claimText,
          tags: ['from_note'],
          extractor_version: 'local-first-line-v1',
          content_hash: sourceNote.content_hash,
        },
        scope: sourceNote.scope || 'default',
        authority: 'INFERRED',
      });

      if (requestId !== this.revisionRequest) return;
      if (intakeRes.success) {
        this.onToast('已生成本机候选，等待你审核。这不是 AI 分析，也不代表已验证。', 'success');
        this.switchModalTab('analysis');
        await this.loadNoteClaims(sourceNote.id);
      } else {
        this.onToast(`候选提交异常：${intakeRes.error || '已进入隔离区'}`, 'error');
      }
    } catch (err) {
      this.onToast(`提取失败：${err.message}`, 'error');
    } finally {
      this.btnExtract.disabled = false;
      this.btnExtract.textContent = '提取首行候选';
    }
  }

  async loadNoteClaims(noteId) {
    try {
      const candidates = await API.fetchCandidates(null, 100, noteId);
      if (this.activeNote?.id !== noteId) return;
      const noteCandidates = candidates.filter(c => c.source_id === noteId);

      if (noteCandidates.length === 0) {
        this.claimsList.innerHTML = `
          <div class="reader-empty">还没有候选。点击下方「提取首行候选」，从当前版本生成一条待审核记录。</div>
        `;
        return;
      }

      const statusLabels = {
        ACCEPTED: '已接受',
        REJECTED: '已拒绝',
        VALIDATED: '待审核',
        UNDER_REVIEW: '审核中',
        PENDING: '待审核',
        PENDING_OWNER_APPROVAL: '待所有者审批',
        QUARANTINED: '已隔离',
      };

      this.claimsList.innerHTML = '';
      noteCandidates.forEach(cand => {
        const item = document.createElement('div');
        item.className = `candidate-card ${cand.status === 'ACCEPTED' ? 'is-accepted' : cand.status === 'REJECTED' ? 'is-rejected' : 'is-pending'}`;

        item.innerHTML = `
          <div class="candidate-main">
            <div class="candidate-status">${cand.status} · ${statusLabels[cand.status] || '待处理'} · 建议类型 ${cand.suggested_node_type}</div>
            <div class="candidate-claim">${escapeHtml(cand.claim)}</div>
            <div class="candidate-meta">候选 ID ${cand.id} · 来源版本 v${cand.source_revision}</div>
          </div>
          <div class="candidate-actions">
            ${
              ['VALIDATED', 'UNDER_REVIEW', 'PENDING', 'PENDING_OWNER_APPROVAL'].includes(cand.status)
                ? `<button class="btn-primary" data-accept-id="${cand.id}">接受为记忆</button><button class="btn-secondary" data-reject-id="${cand.id}">拒绝</button>`
                : `<span class="candidate-done">${statusLabels[cand.status] || cand.status}</span>`
            }
          </div>
        `;

        const acceptBtn = item.querySelector('[data-accept-id]');
        if (acceptBtn) {
          acceptBtn.addEventListener('click', async () => {
            try {
              acceptBtn.disabled = true;
              acceptBtn.textContent = '提升中…';
              await API.acceptCandidate(cand.id, 'user');
              window.dispatchEvent(new CustomEvent('library-memory-changed'));
              this.onToast('已接受，并写入记忆。原文保持独立。', 'success');
              await this.loadNoteClaims(noteId);
            } catch (e) {
              this.onToast(`提升失败：${e.message}`, 'error');
              acceptBtn.disabled = false;
            }
          });
        }

        const rejectBtn = item.querySelector('[data-reject-id]');
        rejectBtn?.addEventListener('click', async () => {
          const reason = window.prompt('请填写拒绝这条候选的理由：');
          if (reason === null) return;
          rejectBtn.disabled = true;
          try {
            await API.rejectCandidate(cand.id, reason);
            await this.loadNoteClaims(noteId);
          } catch (e) {
            this.onToast(e.message, 'error');
            rejectBtn.disabled = false;
          }
        });
        this.claimsList.appendChild(item);
      });
    } catch (err) {
      this.onToast(`Could not load candidates: ${err.message}`, 'error');
    }
  }
}
