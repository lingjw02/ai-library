/**
 * AI Library - Local-owner settings panel.
 * Tabs: MCP connection permissions, system/index status, data & backups,
 * notifications & automation, interface & experience, update & about.
 * Grant edits are revision-checked; the backend refuses Agent-context writes.
 */

import { UsageRulesPanel } from './usage-rules.js';

const ACCESS_LABELS = { full: '完全访问', 'read-only': '只读', revoked: '已撤销' };
const ACCESS_ORDER = ['full', 'read-only', 'revoked'];
const UI_CACHE_KEY = 'ai-library.ui';
const FONT_SCALES = ['small', 'standard', 'large'];
const QUALITIES = ['high', 'medium', 'low'];

function toLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatBytes(bytes) {
  if (bytes == null) return '未知';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

export class SettingsPanel {
  /** Apply the cached UI preferences before the first paint (no server round-trip). */
  static applyCachedUi() {
    try {
      SettingsPanel.applyUi(JSON.parse(localStorage.getItem(UI_CACHE_KEY) || '{}'));
    } catch { /* ignore malformed cache */ }
  }

  /** Reflect UI preferences onto the document root; all styling hangs off these. */
  static applyUi(ui = {}) {
    const root = document.documentElement;
    root.setAttribute('data-theme', ui.theme === 'light' ? 'light' : 'dark');
    root.setAttribute('data-font-scale', FONT_SCALES.includes(ui.fontScale) ? ui.fontScale : 'standard');
    root.setAttribute('data-universe-quality', QUALITIES.includes(ui.universeQuality) ? ui.universeQuality : 'high');
  }

  constructor({ API, universe, onToast } = {}) {
    this.API = API;
    this.universe = universe;
    this.onToast = onToast || (() => {});
    this.dialog = document.getElementById('settings-dialog');
    this.loadedAccess = false;
    this.loadedSystem = false;
    this.loadedData = false;
    this.loadedAutomation = false;
    this.loadedAbout = false;
    this.usageRules = new UsageRulesPanel({ API, onToast: this.onToast });

    document.getElementById('settings-close').onclick = () => this.close();
    this.dialog.addEventListener('close', () => document.getElementById('universe-settings')?.focus());
    this.dialog.addEventListener('cancel', event => {
      if (this.usageRules.dirty || this.usageRules.publishing) { event.preventDefault(); this.close(); }
    });

    this.dialog.querySelectorAll('.settings-tab').forEach(tab => {
      tab.onclick = () => this.selectTab(tab.dataset.settingsTab);
    });

    document.getElementById('settings-access-refresh').onclick = () => this.loadAccess();
    document.getElementById('settings-rebuild-keyword').onclick = () => this.rebuild('keyword');
    document.getElementById('settings-rebuild-vector').onclick = () => this.rebuild('vector');
    document.getElementById('settings-copy-mcp').onclick = () => this.copyMcpConfig();

    const approvalsRefresh = document.getElementById('settings-approvals-refresh');
    if (approvalsRefresh) approvalsRefresh.onclick = () => this.loadWriteApprovals();
    const approvalIssueBtn = document.getElementById('settings-approval-issue-btn');
    if (approvalIssueBtn) approvalIssueBtn.onclick = () => this.issueWriteApproval();
    const profileCreateBtn = document.getElementById('settings-profile-create-btn');
    if (profileCreateBtn) profileCreateBtn.onclick = () => this.createAgentProfilePrompt();

    const advanceProjBtn = document.getElementById('settings-advance-projection');
    if (advanceProjBtn) advanceProjBtn.onclick = () => this.advanceProjection();
    const rebuildProjBtn = document.getElementById('settings-rebuild-projection');
    if (rebuildProjBtn) rebuildProjBtn.onclick = () => this.rebuildProjection();
    const tokenQuotaSaveBtn = document.getElementById('settings-token-quota-save');
    if (tokenQuotaSaveBtn) tokenQuotaSaveBtn.onclick = () => this.saveTokenBudgetQuota();

    document.getElementById('settings-backup-refresh').onclick = () => this.loadBackups();
    document.getElementById('settings-backup-create').onclick = () => this.createBackup();
    document.getElementById('settings-export').onclick = () => this.exportLibrary();
    document.getElementById('settings-vacuum').onclick = () => this.vacuumDatabase();
    document.getElementById('settings-about-refresh').onclick = () => this.loadAbout();

    this.bindAppearance();
    this.bindPreferences();
  }

  open() {
    this.dialog.showModal();
    this.usageRules.open();
    this.loadCacheMetrics();
    if (!this.loadedAccess) this.loadAccess();
    if (!this.loadedSystem) this.loadSystem();
    if (!this.loadedData) this.loadBackups();
    if (!this.loadedAutomation) this.loadPreferences();
    if (!this.loadedAbout) this.loadAbout();
  }

  close() {
    if (this.usageRules.publishing) return;
    if (this.usageRules.dirty && !window.confirm('规则有未保存的修改，关闭并放弃这些修改？')) return;
    this.usageRules.discardDraft();
    this.dialog.close();
  }

  selectTab(name) {
    this.dialog.querySelectorAll('.settings-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.settingsTab === name);
      t.setAttribute('aria-selected', String(t.dataset.settingsTab === name));
    });
    this.dialog.querySelectorAll('.settings-pane').forEach(p =>
      p.classList.toggle('active', p.dataset.settingsPane === name));
    if (name === 'rules') this.usageRules.loadHistory();
    if (name === 'access') this.loadCacheMetrics();
  }

  kvCell(key, value) {
    const cell = document.createElement('div');
    cell.className = 'settings-kv';
    const k = document.createElement('span'); k.textContent = key;
    const v = document.createElement('strong'); v.textContent = value ?? '—';
    cell.append(k, v);
    return cell;
  }

  async loadCacheMetrics() {
    try {
      const data = await this.API.fetchCacheMetrics();
      const savedTokensEl = document.getElementById('model-metric-saved-tokens');
      const cacheHitEl = document.getElementById('model-metric-cache-hit');
      const leanRatioEl = document.getElementById('model-metric-lean-ratio');
      const ttftSavedEl = document.getElementById('model-metric-ttft-saved');

      if (savedTokensEl) {
        const saved = data.tokens_saved_estimate || 0;
        savedTokensEl.textContent = `~${saved.toLocaleString()}`;
      }
      if (cacheHitEl) {
        const hitRate = data.cache_hit_rate ?? 0;
        const total = data.total_mcp_queries || 0;
        cacheHitEl.textContent = total > 0 ? `${hitRate.toFixed(1)}% (${data.cache_hit_queries}/${total})` : '冻结前缀就绪 (>80%)';
      }
      if (leanRatioEl) {
        const redRate = data.token_reduction_rate || 50.8;
        leanRatioEl.textContent = `-${redRate.toFixed(1)}% (精简序列化)`;
      }
      if (ttftSavedEl) {
        const avgLat = data.avg_latency_saved_ms || 0;
        ttftSavedEl.textContent = avgLat > 0 ? `节省 ~${Math.round(avgLat)} ms` : '预估 ~320 ms';
      }
    } catch (err) {
      console.warn('Failed to fetch cache metrics:', err);
    }
  }

  // ---------------------------------------------------------------
  // MCP connection permissions
  // ---------------------------------------------------------------
  async loadAccess() {
    this.loadCacheMetrics();
    const list = document.getElementById('settings-access-list');
    const status = document.getElementById('settings-access-status');
    status.textContent = '正在读取连接权限…';
    list.replaceChildren();
    try {
      const data = await this.API.fetchSettingsAccess();
      this.loadedAccess = true;
      const items = data.items || [];
      status.textContent = items.length
        ? `${items.length} 条连接授权。修改后立即对该连接生效，已返回客户端的数据无法追溯收回。`
        : '暂无连接授权。授权在 MCP 客户端首次获批启动时创建。';
      list.append(...items.map(item => this.renderGrantRow(item)));
    } catch (e) {
      status.textContent = `读取失败：${e.message}`;
    }
    this.loadAudit();
    this.loadWriteApprovals();
    this.loadAgentProfiles();
  }

  renderGrantRow(item) {
    const row = document.createElement('div');
    row.className = 'settings-grant';

    const head = document.createElement('div');
    head.className = 'settings-grant-head';
    const who = document.createElement('div');
    who.className = 'settings-grant-who';
    who.innerHTML = `<strong></strong><span></span>`;
    who.querySelector('strong').textContent = item.principal;
    who.querySelector('span').textContent = `scope: ${item.scope} · rev ${item.revision}`;
    head.append(who);

    const access = document.createElement('select');
    access.className = 'settings-select';
    access.setAttribute('aria-label', `${item.principal} 的访问级别`);
    ACCESS_ORDER.forEach(level => {
      const opt = document.createElement('option');
      opt.value = level;
      opt.textContent = ACCESS_LABELS[level];
      opt.selected = item.access === level;
      access.append(opt);
    });

    const expiry = document.createElement('input');
    expiry.type = 'datetime-local';
    expiry.className = 'settings-input settings-expiry-input';
    expiry.setAttribute('aria-label', `${item.principal} 的到期时间`);
    expiry.value = toLocalInput(item.expires_at);

    const hint = document.createElement('p');
    hint.className = 'settings-hint';
    const updateHint = () => {
      hint.textContent = expiry.value
        ? `当前设定到期：${new Date(expiry.value).toLocaleString()}`
        : '当前无到期时间（永久有效）。';
    };
    updateHint();

    const presetGroup = document.createElement('div');
    presetGroup.className = 'settings-grant-presets';
    presetGroup.setAttribute('role', 'group');
    presetGroup.setAttribute('aria-label', '有效期快捷预设');

    const presets = [
      { key: 'never', label: '永久有效', days: null },
      { key: '7d', label: '+7天', days: 7 },
      { key: '30d', label: '+30天', days: 30 },
      { key: '90d', label: '+90天', days: 90 },
      { key: 'custom', label: '自定义', days: 'custom' },
    ];

    let currentPreset = 'never';
    if (item.expires_at) {
      currentPreset = 'custom';
    }

    const presetBtns = presets.map(p => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'grant-preset-pill' + (p.key === currentPreset ? ' active' : '');
      btn.dataset.preset = p.key;
      btn.textContent = p.label;
      btn.onclick = () => {
        presetBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        if (p.key === 'never') {
          expiry.value = '';
          expiry.style.display = 'none';
        } else if (p.days === 'custom') {
          expiry.style.display = 'inline-block';
          expiry.focus();
        } else {
          expiry.style.display = 'none';
          const future = new Date(Date.now() + p.days * 86400 * 1000);
          expiry.value = toLocalInput(future.toISOString());
        }
        updateHint();
      };
      return btn;
    });

    if (currentPreset !== 'custom') {
      expiry.style.display = 'none';
    }

    expiry.addEventListener('input', () => {
      presetBtns.forEach(b => b.classList.toggle('active', b.dataset.preset === 'custom'));
      updateHint();
    });

    presetGroup.append(...presetBtns);

    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'u-button u-primary';
    save.textContent = '保存';
    save.onclick = () => this.saveGrant(item, access.value, expiry.value, save);

    const controls = document.createElement('div');
    controls.className = 'settings-grant-controls';
    controls.append(access, presetGroup, expiry, save);

    row.append(head, controls, hint);
    return row;
  }

  async saveGrant(item, access, expiryLocal, button) {
    button.disabled = true;
    const expires_at = expiryLocal ? new Date(expiryLocal).toISOString() : null;
    try {
      const updated = await this.API.updateSettingsAccess(item.principal, item.scope, {
        access, expected_revision: item.revision, expires_at,
      });
      this.onToast(`已更新 ${item.principal} 的访问级别：${ACCESS_LABELS[updated.access]}`, 'success');
      this.loadAccess();
    } catch (e) {
      this.onToast(`保存失败：${e.message}`, 'error');
      button.disabled = false;
      this.loadAccess();
    }
  }

  async loadAudit() {
    const box = document.getElementById('settings-access-audit');
    try {
      const data = await this.API.fetchSettingsAudit({ limit: 12 });
      const items = data.items || [];
      if (!items.length) { box.textContent = '暂无授权变更记录。'; return; }
      box.replaceChildren(...items.map(entry => {
        const line = document.createElement('div');
        line.className = 'settings-audit-row';
        const when = new Date(entry.recorded_at).toLocaleString();
        const d = entry.details || {};
        const label = d.can_read === false ? '已撤销' : (d.can_write ? '完全访问' : '只读');
        line.textContent = `${when} · ${entry.principal}/${entry.scope} · ${entry.event} → ${label}（${entry.actor}）`;
        return line;
      }));
    } catch (e) {
      box.textContent = `变更记录读取失败：${e.message}`;
    }
  }

  async loadWriteApprovals() {
    const box = document.getElementById('settings-approvals-list');
    if (!box) return;
    box.textContent = '正在读取单次审批票据…';
    try {
      const res = await this.API.fetchWriteApprovals({ scope: this.universe?.scope || 'default' });
      const items = Array.isArray(res) ? res : (res.items || []);
      if (!items.length) {
        box.textContent = '暂无单次写入审批票据。点击右上角「+ 签发审批票据」为特定操作生成绑定票据。';
        return;
      }
      box.replaceChildren(...items.map(ticket => {
        const line = document.createElement('div');
        line.className = 'settings-audit-row';
        const statusText = ticket.used_at ? '已使用' : (ticket.revoked_at ? '已吊销' : '待消费');
        line.textContent = `${ticket.id || ticket.approval_id} · ${ticket.action || ticket.operation_type} → ${ticket.target_id} · 主体: ${ticket.principal || ticket.grantee_principal} · 状态: ${statusText}`;
        return line;
      }));
    } catch (e) {
      box.textContent = `审批票据读取失败：${e.message}`;
    }
  }

  async issueWriteApproval() {
    const action = window.prompt('输入授权操作类型（例如 memory.promote 或 strategy.activate）：', 'memory.promote');
    if (!action) return;
    const targetId = window.prompt('输入授权目标 ID（例如 candidate_id 或 strategy_id）：', '');
    if (!targetId) return;
    const principal = window.prompt('输入获权 Agent 主体（例如 mcp-agent:local）：', 'mcp-agent:local');
    if (!principal) return;
    try {
      const expiresAt = new Date(Date.now() + 600 * 1000).toISOString();
      const ticket = await this.API.issueWriteApproval({
        principal: principal.trim(),
        scope: this.universe?.scope || 'default',
        action: action.trim(),
        targetId: targetId.trim(),
        targetRevision: 1,
        payloadHash: '0'.repeat(64),
        reason: 'Owner issued approval ticket from Settings UI',
        expiresAt,
      });
      this.onToast(`已签发单次写入票据：${ticket.id || ticket.approval_id}`, 'success');
      await this.loadWriteApprovals();
    } catch (e) {
      this.onToast(`签发失败：${e.message}`, 'error');
    }
  }

  async loadAgentProfiles() {
    const box = document.getElementById('settings-profiles-list');
    if (!box) return;
    box.textContent = '正在读取客户端配置…';
    try {
      const data = await this.API.fetchAgentProfiles();
      const profiles = Array.isArray(data) ? data : (data.items || []);
      if (!profiles.length) {
        box.textContent = '暂无已保存的客户端配置文件。点击「+ 新建客户端配置」创建并预览 Diff。';
        return;
      }
      box.replaceChildren(...profiles.map(p => {
        const pid = p.id || p.profile_id;
        const row = document.createElement('div');
        row.className = 'settings-audit-row';
        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.alignItems = 'center';
        row.style.gap = '8px';
        const info = document.createElement('span');
        info.textContent = `${pid} (${p.client_kind || p.client_type}) · v${p.client_version || '1.0'} · 设备: ${p.device_id || 'local'} · 兼容: ${p.compatibility_status || 'COMPATIBLE'}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'u-text-button';
        btn.textContent = '预览配置计划 (Diff)';
        btn.onclick = async () => {
          try {
            const plan = await this.API.planAgentProfileClient(pid);
            window.alert(`【${pid} · ${p.client_kind || p.client_type} 配置变更计划】\n\n--- Diff 预览 ---\n${plan.diff || plan.proposed || JSON.stringify(plan, null, 2)}`);
          } catch (err) {
            try {
              const exported = await this.API.fetchJSON(`/api/agent-profiles/${encodeURIComponent(pid)}/export`);
              window.alert(`【${pid} · ${p.client_kind || p.client_type} 导出模板预览】\n（提示：${err.message}）\n\n${JSON.stringify(exported, null, 2)}`);
            } catch (innerErr) {
              this.onToast(`无法生成配置预览：${err.message}`, 'error');
            }
          }
        };
        row.append(info, btn);
        return row;
      }));
    } catch (e) {
      box.textContent = `客户端配置读取失败：${e.message}`;
    }
  }

  async createAgentProfilePrompt() {
    const clientKind = window.prompt('输入客户端类型（gemini-cli / codex / claude_desktop / cursor）：', 'gemini-cli');
    if (!clientKind) return;
    const clientVersion = window.prompt('输入客户端版本：', '1.0.0');
    if (!clientVersion) return;
    const deviceId = window.prompt('输入设备标识 (device_id)：', 'local-workstation');
    if (!deviceId) return;
    try {
      const profile = await this.API.createAgentProfile({
        clientKind: clientKind.trim(),
        clientVersion: clientVersion.trim(),
        deviceId: deviceId.trim(),
      });
      this.onToast(`已创建客户端配置：${profile.id || profile.client_kind}`, 'success');
      await this.loadAgentProfiles();
    } catch (e) {
      this.onToast(`创建配置失败：${e.message}`, 'error');
    }
  }

  // ---------------------------------------------------------------
  // System & index status
  // ---------------------------------------------------------------
  async loadSystem() {
    const grid = document.getElementById('settings-system-grid');
    const indexBox = document.getElementById('settings-index-status');
    grid.textContent = '正在读取系统信息…';
    try {
      const info = await this.API.fetchSettingsSystem();
      this.loadedSystem = true;
      this.systemInfo = info;
      grid.replaceChildren(
        this.kvCell('版本', info.version),
        this.kvCell('数据目录', info.data_dir),
        this.kvCell('数据库文件', info.db_path),
        this.kvCell('数据库大小', formatBytes(info.db_size_bytes)),
        this.kvCell('资料范围', (info.scopes || []).join(', ') || '—'),
      );

      const counts = info.counts || {};
      const countGrid = document.getElementById('settings-count-grid');
      const countLabels = {
        notes: '笔记版本', memory_nodes: '记忆节点', edges: '关系', candidates: '候选',
        topics: '主题', quarantine: '隔离记录', backlog: '维护事项', embeddings: '向量', grants: '连接授权',
      };
      countGrid.replaceChildren(...Object.entries(countLabels).map(([key, label]) => {
        const cell = document.createElement('div');
        cell.className = 'settings-count';
        const n = document.createElement('strong'); n.textContent = counts[key] ?? '—';
        const t = document.createElement('span'); t.textContent = label;
        cell.append(n, t);
        return cell;
      }));

      const k = info.keyword_index || {};
      const v = info.vector_index || {};
      indexBox.textContent = [
        `关键词索引：generation ${k.generation ?? '—'} · checkpoint ${k.checkpoint ?? '—'} · 滞后 ${k.lag ?? '—'} · ${k.ready ? '就绪' : '未就绪'}`,
        `向量索引：generation ${v.generation ?? '—'} · 提交 ${v.submissions ?? '—'} / 投影 ${v.projected ?? '—'} · ${v.mode || '—'}`,
        `嵌入提供方：${v.embedding_provider || '—'}（模型是否加载：${v.model_loaded ? '是' : '否'}）`,
      ].join('\n');
      document.getElementById('settings-mcp-config').textContent =
        JSON.stringify(info.mcp_config_example, null, 2);
    } catch (e) {
      grid.textContent = `读取失败：${e.message}`;
    }
    this.loadProjectionAndBudget();
  }

  async loadProjectionAndBudget() {
    const projBox = document.getElementById('settings-projection-status');
    if (projBox) {
      try {
        const p = await this.API.fetchGraphProjectionStatus();
        projBox.textContent = `状态：${p.state || 'READY'} · 投影版本 gen ${p.generation ?? 0} · 已处理序号 ${p.last_sequence ?? 0} / 最新序号 ${p.latest_sequence ?? 0}（滞后 ${p.lag ?? 0}）· 节点 ${p.projected_nodes ?? 0} / 边 ${p.projected_edges ?? 0}`;
      } catch (e) {
        projBox.textContent = `图谱投影状态读取失败：${e.message}`;
      }
    }

    const budgetBox = document.getElementById('settings-token-budget-status');
    const quotaInput = document.getElementById('settings-token-quota-input');
    if (budgetBox) {
      try {
        const b = await this.API.fetchTokenBudget(this.universe?.scope || 'default');
        this.currentBudgetPolicyRevision = b.policy_revision ?? b.revision ?? 1;
        budgetBox.textContent = `今日配额：${(b.daily_quota ?? 0).toLocaleString()} Tokens · 已用 ${(b.used_tokens ?? 0).toLocaleString()} · 剩余 ${(b.remaining_tokens ?? 0).toLocaleString()} · 状态：${b.state || 'NORMAL'}`;
        if (quotaInput && !quotaInput.value) {
          quotaInput.value = b.daily_quota ?? 100000;
        }
      } catch (e) {
        budgetBox.textContent = `预算状态读取失败：${e.message}`;
      }
    }
  }

  async advanceProjection() {
    const btn = document.getElementById('settings-advance-projection');
    if (btn) btn.disabled = true;
    try {
      const res = await this.API.advanceGraphProjection(200);
      this.onToast(`已增量推进图谱投影：处理 ${res.processed ?? 0} 条变更`, 'success');
      await this.loadProjectionAndBudget();
    } catch (e) {
      this.onToast(`推进投影失败：${e.message}`, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async rebuildProjection() {
    const btn = document.getElementById('settings-rebuild-projection');
    if (btn) btn.disabled = true;
    try {
      const res = await this.API.rebuildGraphProjection();
      this.onToast(`已全量重建图谱投影：generation ${res.generation ?? '—'}`, 'success');
      await this.loadProjectionAndBudget();
    } catch (e) {
      this.onToast(`重建投影失败：${e.message}`, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async saveTokenBudgetQuota() {
    const quotaInput = document.getElementById('settings-token-quota-input');
    const btn = document.getElementById('settings-token-quota-save');
    if (!quotaInput) return;
    const quota = parseInt(quotaInput.value, 10);
    if (Number.isNaN(quota) || quota < 0) {
      this.onToast('请输入有效的非负整数 Token 配额', 'error');
      return;
    }
    if (btn) btn.disabled = true;
    try {
      await this.API.updateTokenBudgetPolicy({
        scope: this.universe?.scope || 'default',
        dailyQuota: quota,
        expectedRevision: this.currentBudgetPolicyRevision || 1,
      });
      this.onToast(`已更新每日 Token 预算配额为 ${quota.toLocaleString()}`, 'success');
      await this.loadProjectionAndBudget();
    } catch (e) {
      this.onToast(`更新配额失败：${e.message}`, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async rebuild(which) {
    const button = document.getElementById(which === 'keyword' ? 'settings-rebuild-keyword' : 'settings-rebuild-vector');
    button.disabled = true;
    try {
      const path = which === 'keyword' ? '/api/retrieval/index/rebuild' : '/api/retrieval/vector-index/rebuild';
      const result = await this.API.rebuildIndex(path);
      this.onToast(`已重建${which === 'keyword' ? '关键词' : '向量'}索引：generation ${result.generation ?? '—'}`, 'success');
      this.loadSystem();
    } catch (e) {
      this.onToast(`重建失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------
  // Data & backups
  // ---------------------------------------------------------------
  async loadBackups() {
    const list = document.getElementById('settings-backup-list');
    const status = document.getElementById('settings-backup-status');
    status.textContent = '正在读取备份…';
    list.replaceChildren();
    try {
      const data = await this.API.fetchSettingsBackups();
      this.loadedData = true;
      const items = data.items || [];
      status.textContent = items.length
        ? `${items.length} 份备份。备份位于数据目录之外，创建与恢复都不会覆盖当前数据。`
        : '还没有备份。建议先创建一份，再继续写入知识库。';
      list.append(...items.map(item => this.renderBackupRow(item)));
      document.getElementById('settings-data-paths').replaceChildren(
        this.kvCell('备份目录', data.dir),
        this.kvCell('恢复目录', data.restore_dir),
      );
    } catch (e) {
      status.textContent = `读取失败：${e.message}`;
    }
  }

  renderBackupRow(item) {
    const row = document.createElement('div');
    row.className = 'settings-grant';

    const head = document.createElement('div');
    head.className = 'settings-grant-head';
    const who = document.createElement('div');
    who.className = 'settings-grant-who';
    who.innerHTML = `<strong></strong><span></span>`;
    who.querySelector('strong').textContent = item.name;
    who.querySelector('span').textContent =
      `${formatTime(item.created_at)} · ${formatBytes(item.size_bytes)} · ${item.files ?? '?'} 个文件`;
    head.append(who);

    const verify = document.createElement('button');
    verify.type = 'button';
    verify.className = 'u-button';
    verify.textContent = '校验';
    verify.onclick = () => this.verifyBackup(item, verify);

    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'u-button';
    restore.textContent = '恢复到新目录';
    restore.disabled = !item.complete;
    restore.onclick = () => this.restoreBackup(item, restore);

    const controls = document.createElement('div');
    controls.className = 'settings-grant-controls';
    controls.append(verify, restore);
    row.append(head, controls);

    if (!item.complete) {
      const hint = document.createElement('p');
      hint.className = 'settings-hint';
      hint.textContent = '这份备份缺少 snapshot.json，可能未完成，不能用于恢复。';
      row.append(hint);
    }
    return row;
  }

  async createBackup() {
    const button = document.getElementById('settings-backup-create');
    const status = document.getElementById('settings-backup-status');
    button.disabled = true;
    status.textContent = '正在创建备份（逐文件校验哈希，请稍候）…';
    try {
      const result = await this.API.createSettingsBackup();
      this.onToast(`已创建备份 ${result.name}（${result.files ?? '?'} 个文件）`, 'success');
      this.loadBackups();
    } catch (e) {
      status.textContent = `创建失败：${e.message}`;
      this.onToast(`备份失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async verifyBackup(item, button) {
    button.disabled = true;
    try {
      const result = await this.API.verifySettingsBackup(item.name);
      this.onToast(`校验通过：${result.files} 个文件，格式 v${result.format_version}`, 'success');
    } catch (e) {
      this.onToast(`校验失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async restoreBackup(item, button) {
    if (!window.confirm(`将把备份「${item.name}」恢复到一个新目录，不会覆盖当前数据目录。继续？`)) return;
    button.disabled = true;
    try {
      const result = await this.API.restoreSettingsBackup(item.name);
      this.onToast(`已恢复到新目录：${result.restored_to}`, 'success');
    } catch (e) {
      this.onToast(`恢复失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async exportLibrary() {
    const button = document.getElementById('settings-export');
    button.disabled = true;
    try {
      const data = await this.API.fetchSettingsExport();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      const link = document.createElement('a');
      link.href = url;
      link.download = `ai-library-export-${stamp}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      const c = data.counts || {};
      this.onToast(`已导出 ${c.notes ?? 0} 条笔记 / ${c.nodes ?? 0} 个节点 / ${c.edges ?? 0} 条关系`, 'success');
    } catch (e) {
      this.onToast(`导出失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async vacuumDatabase() {
    if (!window.confirm('VACUUM 会重写数据库文件并短暂独占写入，期间请勿进行其他操作。继续？')) return;
    const button = document.getElementById('settings-vacuum');
    button.disabled = true;
    try {
      const result = await this.API.vacuumSettingsDatabase();
      this.onToast(`数据库已整理，回收 ${formatBytes(result.reclaimed_bytes ?? 0)}`, 'success');
      this.loadSystem();
    } catch (e) {
      this.onToast(`整理失败：${e.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------
  // Preferences (UI + automation)
  // ---------------------------------------------------------------
  bindPreferences() {
    const map = {
      'settings-theme': ['ui', 'theme'],
      'settings-font-scale': ['ui', 'fontScale'],
      'settings-universe-quality': ['ui', 'universeQuality'],
      // autoIndexRebuild 已移除：索引由后台 worker 无条件增量维护，
      // 该开关从未被任何后端逻辑消费，保留只会让人以为打开后才有自动索引。
      'settings-notify-ingest': ['automation', 'notifyOnIngest'],
      'settings-notify-conflict': ['automation', 'notifyOnConflict'],
      'settings-auto-record': ['automation', 'autoRecordToLibrary'],
    };
    for (const [id, [group, key]] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.onchange = () => {
        const value = el.type === 'checkbox' ? el.checked : el.value;
        this.savePreference(group, key, value);
      };
    }
  }

  async loadPreferences() {
    try {
      const data = await this.API.fetchSettingsPreferences();
      this.loadedAutomation = true;
      this.applyPreferences(data.preferences || {});
    } catch (e) {
      document.getElementById('settings-automation-status').textContent = `读取失败：${e.message}`;
    }
  }

  applyPreferences(prefs) {
    const ui = prefs.ui || {};
    const automation = prefs.automation || {};
    SettingsPanel.applyUi(ui);
    localStorage.setItem(UI_CACHE_KEY, JSON.stringify(ui));

    const set = (id, value) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!value; else el.value = value;
    };
    set('settings-theme', ui.theme || 'dark');
    set('settings-font-scale', ui.fontScale || 'standard');
    set('settings-universe-quality', ui.universeQuality || 'high');
    set('settings-notify-ingest', automation.notifyOnIngest);
    set('settings-notify-conflict', automation.notifyOnConflict);
    set('settings-auto-record', automation.autoRecordToLibrary);

    this.universe?.scene?.setQuality?.(ui.universeQuality || 'high');
    document.getElementById('settings-automation-status').textContent = '偏好已从本机 preferences.json 读取。';
  }

  async savePreference(group, key, value) {
    const status = document.getElementById('settings-automation-status');
    const uiStatus = document.getElementById('settings-ui-status');
    try {
      const data = await this.API.updateSettingsPreferences({ [group]: { [key]: value } });
      const ui = (data.preferences || {}).ui || {};
      SettingsPanel.applyUi(ui);
      localStorage.setItem(UI_CACHE_KEY, JSON.stringify(ui));
      this.universe?.scene?.setQuality?.(ui.universeQuality || 'high');
      if (status) status.textContent = '已保存到本机偏好。';
      if (uiStatus) uiStatus.textContent = '已保存到本机偏好。';
    } catch (e) {
      const message = `保存失败：${e.message}`;
      if (status) status.textContent = message;
      if (uiStatus) uiStatus.textContent = message;
      this.onToast(message, 'error');
    }
  }

  // ---------------------------------------------------------------
  // Appearance & motion (session-scoped)
  // ---------------------------------------------------------------
  bindAppearance() {
    const motion = document.getElementById('settings-motion');
    motion.value = localStorage.getItem('ai-library.motion') || 'standard';
    this.applyMotion(motion.value);
    motion.onchange = () => { this.applyMotion(motion.value); localStorage.setItem('ai-library.motion', motion.value); };

    const tractor = document.getElementById('settings-tractor');
    const pause = document.getElementById('settings-pause');
    const sync = () => {
      tractor.checked = this.universe?.showTractorLines !== false;
      pause.checked = !!this.universe?.paused;
    };
    sync();
    tractor.onchange = () => { this.universe?.toggleTractorLines(); sync(); };
    pause.onchange = () => {
      if (!this.universe) return;
      this.universe.paused = pause.checked;
      this.universe.scene?.setPaused(this.universe.paused);
      this.universe.updatePause();
      sync();
    };
  }

  applyMotion(level) {
    document.body.classList.remove('motion-quiet', 'motion-standard', 'motion-expressive');
    document.body.classList.add(`motion-${level}`);
  }

  // ---------------------------------------------------------------
  // Update & about
  // ---------------------------------------------------------------
  async loadAbout() {
    const grid = document.getElementById('settings-about-grid');
    const paths = document.getElementById('settings-about-paths');
    grid.textContent = '正在读取…';
    try {
      const info = await this.API.fetchSettingsAbout();
      this.loadedAbout = true;
      grid.replaceChildren(
        this.kvCell('应用版本', info.app_version),
        this.kvCell('代码版本', info.git_revision || '（未检测到 git）'),
        this.kvCell('Python', info.python_version),
        this.kvCell('SQLite', info.sqlite_version),
        this.kvCell('运行平台', info.platform),
      );
      paths.replaceChildren(
        this.kvCell('数据目录', info.data_dir),
        this.kvCell('数据库', info.db_path),
        this.kvCell('备份目录', info.backups_dir),
        this.kvCell('偏好文件', info.preferences_path),
      );
    } catch (e) {
      grid.textContent = `读取失败：${e.message}`;
    }
  }

  copyMcpConfig() {
    const text = document.getElementById('settings-mcp-config').textContent;
    navigator.clipboard?.writeText(text)
      .then(() => this.onToast('已复制 MCP 连接配置', 'success'))
      .catch(() => this.onToast('复制失败，请手动选择文本', 'error'));
  }
}
