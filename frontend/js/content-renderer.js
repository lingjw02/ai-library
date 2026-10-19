/**
 * AI Library - Rich Content, Chat History & Multimedia Renderer
 * Supports:
 * - Safe Markdown + GFM tables + Callouts + Code blocks with Copy button
 * - Clickable internal note/memory references (note-xxxx, mem_xxxx)
 * - Multimedia cards: Image (with Zoom/Pan/Rotate Lightbox), PDF (embedded viewer + page jump),
 *   Video (speed control, PiP, timestamp seeking), Audio
 * - Chat History Session Timeline: stitches multi-part chunks (1/N..N/N),
 *   filters turns (All / User / Assistant / Media), and renders inline chat multimedia.
 */

export function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function classifyUrlKind(url = '', alt = '') {
  const clean = String(url).split('?')[0].split('#')[0].toLowerCase();
  const hint = String(alt).toLowerCase();
  if (clean.endsWith('.pdf') || hint.includes('pdf')) return 'pdf';
  if (/\.(mp4|webm|mov|m4v|ogv)$/.test(clean) || hint.includes('视频') || hint.includes('video')) return 'video';
  if (/\.(mp3|wav|ogg|m4a|flac|aac)$/.test(clean) || hint.includes('音频') || hint.includes('audio')) return 'audio';
  if (/\.(png|jpe?g|jfif|gif|webp|svg|bmp|avif)$/.test(clean) || clean.startsWith('/api/media/') || clean.startsWith('data:image/')) {
    return 'image';
  }
  return 'link';
}

/**
 * Render a single media card HTML string (Image, PDF, Video, Audio).
 */
export function renderMediaCardHtml(item) {
  const url = escapeHtml(item.url || `/api/media/${item.id}`);
  const filename = escapeHtml(item.filename || item.title || '媒体附件');
  const caption = escapeHtml(item.caption || '');
  const sizeLabel = item.byte_length ? formatBytes(item.byte_length) : '';
  const kind = item.media_kind || classifyUrlKind(item.url || item.filename || '', item.caption || '');
  const downloadUrl = `${url}${url.includes('?') ? '&' : '?'}download=true`;

  if (kind === 'pdf') {
    return `
      <div class="media-card media-card-pdf" data-media-kind="pdf" data-media-url="${url}">
        <div class="media-card-bar">
          <div class="media-card-info">
            <span class="media-badge media-badge-pdf">📄 PDF</span>
            <strong class="media-filename" title="${filename}">${filename}</strong>
            ${sizeLabel ? `<span class="media-size">${sizeLabel}</span>` : ''}
          </div>
          <div class="media-card-actions">
            <label class="media-pdf-page-label" title="跳转到指定页码">
              页码 <input type="number" class="media-pdf-page-input" min="1" value="1" />
            </label>
            <button type="button" class="media-btn media-pdf-expand-btn" title="展开/收起 PDF 阅读高度">↕ 展开</button>
            <a href="${url}" target="_blank" rel="noopener" class="media-btn" title="在新标签页中打开 PDF">↗ 新窗口</a>
            <a href="${downloadUrl}" download="${filename}" class="media-btn" title="下载原始 PDF">⬇ 下载</a>
          </div>
        </div>
        <div class="media-pdf-Stage">
          <iframe src="${url}#toolbar=1&navpanes=0&view=FitH" class="media-pdf-frame" title="${filename}" loading="lazy"></iframe>
        </div>
        ${caption ? `<div class="media-caption">${caption}</div>` : ''}
      </div>
    `;
  }

  if (kind === 'video') {
    return `
      <div class="media-card media-card-video" data-media-kind="video" data-media-url="${url}">
        <div class="media-card-bar">
          <div class="media-card-info">
            <span class="media-badge media-badge-video">🎬 视频</span>
            <strong class="media-filename" title="${filename}">${filename}</strong>
            ${sizeLabel ? `<span class="media-size">${sizeLabel}</span>` : ''}
          </div>
          <div class="media-card-actions">
            <select class="media-video-speed" aria-label="播放倍速" title="切换播放倍速">
              <option value="0.5">0.5x</option>
              <option value="0.75">0.75x</option>
              <option value="1" selected>1.0x</option>
              <option value="1.25">1.25x</option>
              <option value="1.5">1.5x</option>
              <option value="2">2.0x</option>
            </select>
            <button type="button" class="media-btn media-video-pip-btn" title="画中画播放">⧉ 画中画</button>
            <a href="${downloadUrl}" download="${filename}" class="media-btn" title="下载视频">⬇ 下载</a>
          </div>
        </div>
        <div class="media-video-stage">
          <video controls preload="metadata" class="media-video-player" src="${url}"></video>
        </div>
        ${caption ? `<div class="media-caption">${caption}</div>` : ''}
      </div>
    `;
  }

  if (kind === 'audio') {
    return `
      <div class="media-card media-card-audio" data-media-kind="audio" data-media-url="${url}">
        <div class="media-card-bar">
          <div class="media-card-info">
            <span class="media-badge media-badge-audio">🎵 音频</span>
            <strong class="media-filename" title="${filename}">${filename}</strong>
            ${sizeLabel ? `<span class="media-size">${sizeLabel}</span>` : ''}
          </div>
          <div class="media-card-actions">
            <a href="${downloadUrl}" download="${filename}" class="media-btn" title="下载音频">⬇ 下载</a>
          </div>
        </div>
        <div class="media-audio-stage">
          <audio controls preload="metadata" class="media-audio-player" src="${url}"></audio>
        </div>
        ${caption ? `<div class="media-caption">${caption}</div>` : ''}
      </div>
    `;
  }

  // Default: Image Card with Lightbox trigger
  return `
    <figure class="media-card media-card-image" data-media-kind="image" data-media-url="${url}" data-media-name="${filename}">
      <div class="media-image-stage" role="button" tabindex="0" title="点击放大预览图片（支持滚轮缩放与旋转）">
        <img src="${url}" alt="${filename}" class="media-image-preview" loading="lazy" />
        <span class="media-image-zoom-hint">🔍 点击放大预览</span>
      </div>
      <figcaption class="media-card-bar media-image-footer">
        <div class="media-card-info">
          <span class="media-badge media-badge-image">🖼️ 图片</span>
          <span class="media-filename" title="${filename}">${filename}</span>
          ${sizeLabel ? `<span class="media-size">${sizeLabel}</span>` : ''}
        </div>
        <div class="media-card-actions">
          <button type="button" class="media-btn media-lightbox-trigger">🔍 放大</button>
          <a href="${downloadUrl}" download="${filename}" class="media-btn">⬇ 下载</a>
        </div>
      </figcaption>
      ${caption ? `<div class="media-caption">${caption}</div>` : ''}
    </figure>
  `;
}

/**
 * Format inline Markdown tokens safely (after HTML escaping).
 */
function renderInlineMarkdown(escapedLine, mediaMap = new Map()) {
  let out = escapedLine;

  // 1. Inline media syntax: ![alt](url)
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, rawUrl) => {
    const cleanUrl = rawUrl.replace(/&amp;/g, '&');
    const mediaIdMatch = cleanUrl.match(/\/api\/media\/(media-[a-zA-Z0-9_-]+)/);
    if (mediaIdMatch && mediaMap.has(mediaIdMatch[1])) {
      return renderMediaCardHtml(mediaMap.get(mediaIdMatch[1]));
    }
    const kind = classifyUrlKind(cleanUrl, alt);
    return renderMediaCardHtml({
      url: cleanUrl,
      filename: alt || cleanUrl.split('/').pop() || '媒体预览',
      media_kind: kind === 'link' ? 'image' : kind,
    });
  });

  // 2. Inline code `code`
  const codePlaceholders = [];
  out = out.replace(/`([^`]+)`/g, (_, code) => {
    const idx = codePlaceholders.length;
    codePlaceholders.push(`<code class="md-inline-code">${code}</code>`);
    return `\x00CODE_${idx}\x00`;
  });

  // 3. Markdown links [label](url)
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/api\/[^\s)]+)\)/g, (_, label, url) => {
    const cleanUrl = url.replace(/&amp;/g, '&');
    if (cleanUrl.startsWith('/api/media/')) {
      const mId = cleanUrl.split('/').pop().split('?')[0];
      if (mediaMap.has(mId)) {
        return renderMediaCardHtml(mediaMap.get(mId));
      }
    }
    return `<a href="${cleanUrl}" target="_blank" rel="noopener noreferrer" class="md-link">${label} ↗</a>`;
  });

  // 4. Bare URLs (not already in href/src)
  out = out.replace(/(^|[\s(])((https?:\/\/[^\s<)"']+))/g, (full, prefix, url) => {
    return `${prefix}<a href="${url}" target="_blank" rel="noopener noreferrer" class="md-link">${url} ↗</a>`;
  });

  // 5. Bold, Italic, Strikethrough
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  out = out.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // 6. Clickable internal Note / Memory IDs (e.g. note-0a100f35f9, mem_concept_a)
  out = out.replace(/\b(note-[0-9a-f]{8,32})\b/gi, '<button type="button" class="md-ref-chip" data-jump-note="$1" title="点击跳转到该笔记">📝 $1</button>');
  out = out.replace(/\b(mem_[a-zA-Z0-9_-]{4,64})\b/g, '<button type="button" class="md-ref-chip md-ref-memory" data-jump-memory="$1" title="点击跳转到该记忆">🧠 $1</button>');

  // 7. Clickable video/audio timestamps (e.g. 02:15 or 01:04:20)
  out = out.replace(/(^|[\s(（\[])(\d{1,2}:\d{2}(?::\d{2})?)(?=[\s)）\].,;，。；]|$)/g, (full, pre, ts) => {
    const parts = ts.split(':').map(Number);
    const secs = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
    return `${pre}<button type="button" class="md-timestamp-chip" data-seek-seconds="${secs}" title="跳转到媒体时间点 ${ts}">⏱ ${ts}</button>`;
  });

  // Restore inline code
  out = out.replace(/\x00CODE_(\d+)\x00/g, (_, idx) => codePlaceholders[Number(idx)] || '');
  return out;
}

/**
 * Render full Markdown text + Media assets into safe HTML.
 */
export function renderMarkdownToHtml(markdown = '', mediaAssets = []) {
  const text = String(markdown || '').replace(/\r\n/g, '\n');
  if (!text.trim() && (!mediaAssets || !mediaAssets.length)) {
    return '<div class="reader-empty">暂无正文内容</div>';
  }

  const mediaMap = new Map();
  const renderedMediaIds = new Set();
  for (const item of mediaAssets || []) {
    if (item && item.id) mediaMap.set(item.id, item);
  }

  // Strip HTML comments like <!-- media-asset: ... --> while recording referenced IDs
  const cleanedText = text.replace(/<!--\s*media-asset:\s*(media-[a-zA-Z0-9_-]+)[^>]*-->\n?/g, (_, id) => {
    return '';
  });

  // Also strip noisy `<image name=[Image #1] path="...">...</image>` wrappers in chat turns if media is rendered
  const lines = cleanedText
    .replace(/<image\b[^>]*\bpath="([^"]+)"[^>]*>\s*<\/image>/gi, (_, p) => `\`📎 引用路径：${p}\``)
    .split('\n');

  const htmlBlocks = [];
  let i = 0;

  while (i < lines.length) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    // Empty line
    if (!trimmed) {
      i++;
      continue;
    }

    // Fenced code block ```lang
    if (trimmed.startsWith('```')) {
      const lang = escapeHtml(trimmed.slice(3).trim() || 'text');
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing ```
      const escapedCode = escapeHtml(codeLines.join('\n'));
      htmlBlocks.push(`
        <div class="md-code-block">
          <div class="md-code-header">
            <span class="md-code-lang">${lang}</span>
            <button type="button" class="md-code-copy-btn" title="复制代码">📋 复制代码</button>
          </div>
          <pre class="md-code-pre"><code>${escapedCode}</code></pre>
        </div>
      `);
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      htmlBlocks.push('<hr class="md-hr" />');
      i++;
      continue;
    }

    // Headings # .. ######
    const headingMatch = rawLine.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const content = renderInlineMarkdown(escapeHtml(headingMatch[2].trim()), mediaMap);
      htmlBlocks.push(`<h${level} class="md-heading md-h${level}">${content}</h${level}>`);
      i++;
      continue;
    }

    // Markdown Table
    if (trimmed.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])) {
      const parseCells = rowStr =>
        rowStr
          .trim()
          .replace(/^\||\|$/g, '')
          .split('|')
          .map(c => renderInlineMarkdown(escapeHtml(c.trim()), mediaMap));
      const headers = parseCells(lines[i]);
      i += 2; // skip header and separator
      const bodyRows = [];
      while (i < lines.length && lines[i].trim().includes('|') && lines[i].trim() !== '') {
        bodyRows.push(parseCells(lines[i]));
        i++;
      }
      htmlBlocks.push(`
        <div class="md-table-wrap">
          <table class="md-table">
            <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
            <tbody>
              ${bodyRows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}
            </tbody>
          </table>
        </div>
      `);
      continue;
    }

    // Blockquote or GitHub Alert (> [!NOTE], > [!TIP], etc.)
    if (trimmed.startsWith('>')) {
      const quoteLines = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      const firstLine = quoteLines[0] || '';
      const alertMatch = firstLine.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
      if (alertMatch) {
        const alertType = alertMatch[1].toLowerCase();
        const alertTitle = alertMatch[2] || {
          note: '说明',
          tip: '提示',
          important: '重要',
          warning: '注意',
          caution: '警示',
        }[alertType] || alertType.toUpperCase();
        const rest = quoteLines.slice(1).map(l => renderInlineMarkdown(escapeHtml(l), mediaMap)).join('<br/>');
        htmlBlocks.push(`
          <div class="md-callout md-callout-${alertType}">
            <div class="md-callout-title">${escapeHtml(alertTitle)}</div>
            ${rest ? `<div class="md-callout-body">${rest}</div>` : ''}
          </div>
        `);
      } else {
        const body = quoteLines.map(l => renderInlineMarkdown(escapeHtml(l), mediaMap)).join('<br/>');
        htmlBlocks.push(`<blockquote class="md-blockquote">${body}</blockquote>`);
      }
      continue;
    }

    // Unordered or Task List
    if (/^\s*[-*+]\s+/.test(rawLine)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        const itemText = lines[i].replace(/^\s*[-*+]\s+/, '');
        const taskMatch = itemText.match(/^\[([ xX])\]\s+(.*)$/);
        if (taskMatch) {
          const checked = taskMatch[1].toLowerCase() === 'x';
          items.push(
            `<li class="md-task-item"><input type="checkbox" disabled ${checked ? 'checked' : ''} /> <span>${renderInlineMarkdown(escapeHtml(taskMatch[2]), mediaMap)}</span></li>`
          );
        } else {
          items.push(`<li>${renderInlineMarkdown(escapeHtml(itemText), mediaMap)}</li>`);
        }
        i++;
      }
      htmlBlocks.push(`<ul class="md-list">${items.join('')}</ul>`);
      continue;
    }

    // Ordered List
    if (/^\s*\d+\.\s+/.test(rawLine)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        const itemText = lines[i].replace(/^\s*\d+\.\s+/, '');
        items.push(`<li>${renderInlineMarkdown(escapeHtml(itemText), mediaMap)}</li>`);
        i++;
      }
      htmlBlocks.push(`<ol class="md-list md-list-ordered">${items.join('')}</ol>`);
      continue;
    }

    // Standalone Media line ![alt](url)
    const standaloneMedia = trimmed.match(/^!\[([^\]]*)\]\(([^)\s]+)\)$/);
    if (standaloneMedia) {
      const alt = standaloneMedia[1];
      const url = standaloneMedia[2];
      const mIdMatch = url.match(/\/api\/media\/(media-[a-zA-Z0-9_-]+)/);
      if (mIdMatch && mediaMap.has(mIdMatch[1])) {
        renderedMediaIds.add(mIdMatch[1]);
        htmlBlocks.push(renderMediaCardHtml(mediaMap.get(mIdMatch[1])));
      } else {
        const kind = classifyUrlKind(url, alt);
        htmlBlocks.push(
          renderMediaCardHtml({
            url,
            filename: alt || url.split('/').pop() || '媒体预览',
            media_kind: kind === 'link' ? 'image' : kind,
          })
        );
      }
      i++;
      continue;
    }

    // Regular paragraph
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('```') &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !lines[i].trim().startsWith('>') &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      paraLines.push(renderInlineMarkdown(escapeHtml(lines[i]), mediaMap));
      i++;
    }
    const paraHtml = paraLines.join('<br/>');
    for (const [mId] of mediaMap) {
      if (paraHtml.includes(`/api/media/${mId}`)) renderedMediaIds.add(mId);
    }
    htmlBlocks.push(`<p class="md-p">${paraHtml}</p>`);
  }

  // Append any resolved media assets that were not explicitly embedded via ![...](/api/media/...)
  const unrenderedMedia = (mediaAssets || []).filter(item => item && item.id && !renderedMediaIds.has(item.id));
  if (unrenderedMedia.length > 0) {
    htmlBlocks.push(`
      <div class="media-gallery-section">
        <div class="media-gallery-header">📎 关联多媒体内容（${unrenderedMedia.length} 项）</div>
        <div class="media-gallery-grid">
          ${unrenderedMedia.map(item => renderMediaCardHtml(item)).join('')}
        </div>
      </div>
    `);
  }

  return `<div class="md-rendered-root">${htmlBlocks.join('\n')}</div>`;
}

/**
 * Render a user-friendly Chat History Conversation View (with multi-chunk stitching,
 * turn filtering, and inline multimedia cards).
 */
export function renderChatSessionHtml(bundle, { fullSessionMode = true, roleFilter = 'all', searchQuery = '' } = {}) {
  const chunks = bundle.chunks || [];
  const messages = bundle.messages || [];
  const sessionMedia = bundle.session_media || [];
  const q = searchQuery.trim().toLowerCase();

  const filteredMessages = messages.filter(msg => {
    if (roleFilter === 'user' && msg.role !== 'user') return false;
    if (roleFilter === 'assistant' && msg.role !== 'assistant') return false;
    if (roleFilter === 'media' && (!msg.media || msg.media.length === 0) && !msg.has_attachment_marker) return false;
    if (q && !String(msg.text || '').toLowerCase().includes(q)) return false;
    return true;
  });

  const activeIndex = chunks.findIndex(c => c.is_active);
  const prevChunk = activeIndex > 0 ? chunks[activeIndex - 1] : null;
  const nextChunk = activeIndex >= 0 && activeIndex + 1 < chunks.length ? chunks[activeIndex + 1] : null;

  const mediaCount = sessionMedia.length;

  return `
    <div class="chat-reader-container">
      <!-- Top Session Control Bar -->
      <div class="chat-session-toolbar">
        <div class="chat-session-meta">
          <span class="chat-category-pill">💬 ${escapeHtml(bundle.category || '历史对话')}</span>
          ${bundle.session_id ? `<span class="chat-session-id" title="会话 ID: ${escapeHtml(bundle.session_id)}">会话 ${escapeHtml(bundle.session_id.slice(0, 8))}…</span>` : ''}
          <span class="chat-count-pill">共 ${messages.length} 条对话${mediaCount ? ` · 📎 ${mediaCount} 项多媒体` : ''}</span>
        </div>

        <div class="chat-session-controls">
          ${
            chunks.length > 1
              ? `
            <div class="chat-chunk-nav">
              <button type="button" class="chat-nav-btn ${fullSessionMode ? 'active' : ''}" data-chat-mode="full" title="自动拼接所有分段，消除截断，连续阅读整场对话">
                🔗 完整会话连读 (${chunks.length} 段合并)
              </button>
              <button type="button" class="chat-nav-btn ${!fullSessionMode ? 'active' : ''}" data-chat-mode="single" title="仅查看当前分段原文">
                📄 当前分段 (${bundle.current_part}/${bundle.total_parts})
              </button>
              <button type="button" class="chat-nav-btn" data-jump-note="${prevChunk ? escapeHtml(prevChunk.id) : ''}" ${prevChunk ? '' : 'disabled'} title="切换到上一段">◀</button>
              <select class="chat-chunk-select" aria-label="选择对话分段">
                ${chunks
                  .map(
                    c =>
                      `<option value="${escapeHtml(c.id)}" ${c.is_active ? 'selected' : ''}>分段 ${c.part}/${bundle.total_parts}</option>`
                  )
                  .join('')}
              </select>
              <button type="button" class="chat-nav-btn" data-jump-note="${nextChunk ? escapeHtml(nextChunk.id) : ''}" ${nextChunk ? '' : 'disabled'} title="切换到下一段">▶</button>
            </div>
          `
              : ''
          }
          ${
            bundle.summary_note && bundle.summary_note.id !== bundle.note_id
              ? `<button type="button" class="chat-nav-btn chat-summary-jump" data-jump-note="${escapeHtml(bundle.summary_note.id)}">📑 查看本场对话摘要</button>`
              : ''
          }
        </div>

        <!-- Filter & Search Bar inside Conversation -->
        <div class="chat-filter-bar">
          <div class="chat-role-pills" role="group" aria-label="筛选对话角色">
            <button type="button" class="chat-filter-pill ${roleFilter === 'all' ? 'active' : ''}" data-role-filter="all">全部 (${messages.length})</button>
            <button type="button" class="chat-filter-pill ${roleFilter === 'user' ? 'active' : ''}" data-role-filter="user">👤 只看提问 (${messages.filter(m => m.role === 'user').length})</button>
            <button type="button" class="chat-filter-pill ${roleFilter === 'assistant' ? 'active' : ''}" data-role-filter="assistant">🤖 只看回复 (${messages.filter(m => m.role === 'assistant').length})</button>
            ${
              mediaCount > 0
                ? `<button type="button" class="chat-filter-pill ${roleFilter === 'media' ? 'active' : ''}" data-role-filter="media">🖼️ 含多媒体 (${messages.filter(m => (m.media && m.media.length > 0) || m.has_attachment_marker).length})</button>`
                : ''
            }
          </div>
          <input type="search" class="chat-search-input" placeholder="在对话中搜索关键词…" value="${escapeHtml(searchQuery)}" />
        </div>
      </div>

      <!-- Session Multimedia Overview Gallery (if any media in this chat session) -->
      ${
        sessionMedia.length > 0
          ? `
        <details class="chat-media-drawer" open>
          <summary class="chat-media-drawer-summary">
            <span>🖼️ 本场对话包含的多媒体附件与生成作品（共 ${sessionMedia.length} 项 — 点击图片可全屏放大）</span>
          </summary>
          <div class="chat-media-drawer-grid">
            ${sessionMedia.map(item => renderMediaCardHtml(item)).join('')}
          </div>
        </details>
      `
          : ''
      }

      <!-- Conversation Timeline Bubbles -->
      <div class="chat-timeline">
        ${
          filteredMessages.length === 0
            ? `<div class="reader-empty">没有匹配的对话消息。</div>`
            : filteredMessages
                .map(msg => {
                  const isUser = msg.role === 'user';
                  const roleLabel = isUser ? '👤 我 (User)' : '🤖 AI 助手 (Assistant)';
                  const timeStr = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : '';
                  const sourceFileShort = msg.source_ref ? msg.source_ref.split(/[\\/]/).pop() : '';
                  return `
                    <article class="chat-turn ${isUser ? 'chat-turn-user' : 'chat-turn-assistant'}" id="chat-msg-${msg.number}">
                      <header class="chat-turn-head">
                        <div class="chat-turn-actor">
                          <span class="chat-turn-badge">${roleLabel}</span>
                          <span class="chat-turn-num">#${msg.number}</span>
                          ${timeStr && timeStr !== 'Invalid Date' ? `<time class="chat-turn-time">${escapeHtml(timeStr)}</time>` : ''}
                        </div>
                        ${
                          sourceFileShort
                            ? `<span class="chat-turn-source" title="原始记录位置：${escapeHtml(msg.source_ref)}">${escapeHtml(sourceFileShort)}</span>`
                            : ''
                        }
                      </header>
                      <div class="chat-turn-body">
                        ${renderMarkdownToHtml(msg.text, msg.media || [])}
                      </div>
                    </article>
                  `;
                })
                .join('')
        }
      </div>
    </div>
  `;
}

/**
 * Bind interactive behaviors inside a rendered container:
 * - Copy code buttons
 * - Image Lightbox triggers
 * - PDF page jumper & height expander
 * - Video speed selector, PiP, & timestamp seeking
 * - Internal Note / Memory jump chips
 */
export function bindRenderedContentInteractions(container, { onNavigateToNote, onNavigateToMemory, onToast } = {}) {
  if (!container) return;

  // 1. Copy Code Buttons
  container.querySelectorAll('.md-code-copy-btn').forEach(btn => {
    btn.onclick = async () => {
      const codeEl = btn.closest('.md-code-block')?.querySelector('code');
      if (!codeEl) return;
      try {
        await navigator.clipboard.writeText(codeEl.textContent || '');
        const prev = btn.textContent;
        btn.textContent = '✓ 已复制';
        setTimeout(() => { btn.textContent = prev; }, 1600);
      } catch {
        onToast?.('复制失败，请手动选择复制', 'error');
      }
    };
  });

  // 2. Internal Note / Memory Jump Chips
  container.querySelectorAll('[data-jump-note]').forEach(btn => {
    btn.onclick = e => {
      e.preventDefault();
      const targetId = btn.dataset.jumpNote;
      if (targetId && onNavigateToNote) onNavigateToNote(targetId);
    };
  });

  container.querySelectorAll('[data-jump-memory]').forEach(btn => {
    btn.onclick = e => {
      e.preventDefault();
      const targetId = btn.dataset.jumpMemory;
      if (targetId && onNavigateToMemory) onNavigateToMemory(targetId);
    };
  });

  // 3. Image Lightbox Triggers
  container.querySelectorAll('.media-card-image').forEach(card => {
    const url = card.dataset.mediaUrl;
    const name = card.dataset.mediaName || '图片预览';
    const stage = card.querySelector('.media-image-stage');
    const triggerBtn = card.querySelector('.media-lightbox-trigger');
    const open = () => openMediaLightbox({ url, filename: name });
    if (stage) {
      stage.onclick = open;
      stage.onkeydown = e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        }
      };
    }
    if (triggerBtn) triggerBtn.onclick = open;
  });

  // 4. PDF Page Jump & Expand
  container.querySelectorAll('.media-card-pdf').forEach(card => {
    const baseUrl = card.dataset.mediaUrl;
    const iframe = card.querySelector('.media-pdf-frame');
    const pageInput = card.querySelector('.media-pdf-page-input');
    const expandBtn = card.querySelector('.media-pdf-expand-btn');

    if (pageInput && iframe && baseUrl) {
      pageInput.onchange = () => {
        const page = Math.max(1, parseInt(pageInput.value, 10) || 1);
        iframe.src = `${baseUrl}#page=${page}&toolbar=1&navpanes=0&view=FitH`;
      };
    }
    if (expandBtn) {
      expandBtn.onclick = () => {
        const expanded = card.classList.toggle('is-tall');
        expandBtn.textContent = expanded ? '↕ 收起高度' : '↕ 展开';
      };
    }
  });

  // 5. Video Playback Speed & PiP
  container.querySelectorAll('.media-card-video').forEach(card => {
    const video = card.querySelector('video');
    const speedSelect = card.querySelector('.media-video-speed');
    const pipBtn = card.querySelector('.media-video-pip-btn');
    if (video && speedSelect) {
      speedSelect.onchange = () => {
        video.playbackRate = parseFloat(speedSelect.value) || 1.0;
      };
    }
    if (video && pipBtn) {
      pipBtn.onclick = async () => {
        try {
          if (document.pictureInPictureElement) {
            await document.exitPictureInPicture();
          } else if (video.requestPictureInPicture) {
            await video.requestPictureInPicture();
          }
        } catch {
          onToast?.('当前浏览器或视频尚未就绪画中画模式', 'info');
        }
      };
    }
  });

  // 6. Timestamp Chips -> Seek First Video/Audio in Container
  container.querySelectorAll('.md-timestamp-chip').forEach(chip => {
    chip.onclick = () => {
      const secs = Number(chip.dataset.seekSeconds) || 0;
      const player = container.querySelector('video, audio') || document.querySelector('.reader-panel video, .reader-panel audio');
      if (player) {
        player.currentTime = secs;
        player.play?.().catch(() => {});
        player.scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else {
        onToast?.('当前资料中没有可跳转的音视频播放器', 'info');
      }
    };
  });
}

/**
 * Singleton Fullscreen Image Lightbox with Zoom, Pan, Rotate, and Download.
 */
let lightboxEl = null;
let lbState = { scale: 1, rotate: 0, tx: 0, ty: 0, dragging: false, startX: 0, startY: 0 };

export function openMediaLightbox({ url, filename = '图片预览' }) {
  if (!lightboxEl) {
    lightboxEl = document.createElement('div');
    lightboxEl.id = 'media-lightbox-modal';
    lightboxEl.className = 'media-lightbox';
    lightboxEl.setAttribute('role', 'dialog');
    lightboxEl.setAttribute('aria-modal', 'true');
    lightboxEl.setAttribute('aria-label', '图片全屏预览');
    lightboxEl.innerHTML = `
      <div class="media-lightbox-topbar">
        <strong class="media-lightbox-title"></strong>
        <div class="media-lightbox-controls">
          <button type="button" data-lb-action="zoom-out" title="缩小">➖ 缩小</button>
          <span class="media-lightbox-scale">100%</span>
          <button type="button" data-lb-action="zoom-in" title="放大">➕ 放大</button>
          <button type="button" data-lb-action="rotate" title="顺时针旋转 90°">↻ 旋转</button>
          <button type="button" data-lb-action="reset" title="重置视图">⟲ 重置</button>
          <a class="media-lightbox-download" download title="下载原图">⬇ 下载原图</a>
          <button type="button" data-lb-action="close" class="media-lightbox-close" title="关闭预览 (Esc)">✕</button>
        </div>
      </div>
      <div class="media-lightbox-stage">
        <img class="media-lightbox-img" alt="" draggable="false" />
      </div>
    `;
    document.body.appendChild(lightboxEl);

    const stage = lightboxEl.querySelector('.media-lightbox-stage');
    const img = lightboxEl.querySelector('.media-lightbox-img');
    const scaleLabel = lightboxEl.querySelector('.media-lightbox-scale');

    const applyTransform = () => {
      img.style.transform = `translate(${lbState.tx}px, ${lbState.ty}px) scale(${lbState.scale}) rotate(${lbState.rotate}deg)`;
      scaleLabel.textContent = `${Math.round(lbState.scale * 100)}%`;
    };

    lightboxEl.querySelectorAll('[data-lb-action]').forEach(btn => {
      btn.onclick = e => {
        e.stopPropagation();
        const act = btn.dataset.lbAction;
        if (act === 'zoom-in') lbState.scale = Math.min(5, lbState.scale * 1.25);
        else if (act === 'zoom-out') lbState.scale = Math.max(0.2, lbState.scale / 1.25);
        else if (act === 'rotate') lbState.rotate = (lbState.rotate + 90) % 360;
        else if (act === 'reset') lbState = { ...lbState, scale: 1, rotate: 0, tx: 0, ty: 0 };
        else if (act === 'close') {
          lightboxEl.classList.remove('open');
          return;
        }
        applyTransform();
      };
    });

    stage.addEventListener('wheel', e => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 0.87;
      lbState.scale = Math.min(5, Math.max(0.2, lbState.scale * factor));
      applyTransform();
    }, { passive: false });

    stage.addEventListener('mousedown', e => {
      if (e.target === stage) {
        lightboxEl.classList.remove('open');
        return;
      }
      lbState.dragging = true;
      lbState.startX = e.clientX - lbState.tx;
      lbState.startY = e.clientY - lbState.ty;
      img.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', e => {
      if (!lbState.dragging) return;
      lbState.tx = e.clientX - lbState.startX;
      lbState.ty = e.clientY - lbState.startY;
      applyTransform();
    });

    window.addEventListener('mouseup', () => {
      if (!lbState.dragging) return;
      lbState.dragging = false;
      img.style.cursor = 'grab';
    });

    window.addEventListener('keydown', e => {
      if (e.key === 'Escape' && lightboxEl.classList.contains('open')) {
        e.stopPropagation();
        lightboxEl.classList.remove('open');
      }
    }, true);
  }

  lbState = { scale: 1, rotate: 0, tx: 0, ty: 0, dragging: false, startX: 0, startY: 0 };
  const img = lightboxEl.querySelector('.media-lightbox-img');
  const titleEl = lightboxEl.querySelector('.media-lightbox-title');
  const dlLink = lightboxEl.querySelector('.media-lightbox-download');
  const scaleLabel = lightboxEl.querySelector('.media-lightbox-scale');

  img.src = url;
  img.alt = filename;
  img.style.transform = 'translate(0px, 0px) scale(1) rotate(0deg)';
  scaleLabel.textContent = '100%';
  titleEl.textContent = filename;
  dlLink.href = `${url}${url.includes('?') ? '&' : '?'}download=true`;
  dlLink.setAttribute('download', filename);
  lightboxEl.classList.add('open');
}
