/**
 * AI Library - Logsheet + Backlog + Health + Evolution Workspace (System Memory Operations)
 * Ref: AI Library Frontend README §4, §7 & Backend README §32–35, §89, §90, §96
 * Option B: Inline summary expansion + right-side full detail drawer.
 */

import { API } from './api.js';
import { QuarantineView } from './quarantine.js';

export class MaintenanceManager {
  constructor({ onToast, onNavigateToMemory, onNavigateToNote }) {
    this.onToast = onToast || console.log;
    this.onNavigateToMemory = onNavigateToMemory || (() => {});

    this.activeSub = 'backlog'; // 'backlog' | 'logsheet' | 'quarantine' | 'health' | 'evolution'
    this.backlogItems = [];
    this.logsheetEntries = [];
    this.activeItem = null;
    this.healthReport = null;
    this.idlePlan = null;
    this.evolutionData = null;

    // DOM Elements
    this.container = document.getElementById('maintenance-list');
    this.subBtnBacklog = document.getElementById('sub-btn-backlog');
    this.subBtnLogsheet = document.getElementById('sub-btn-logsheet');
    this.subBtnQuarantine = document.getElementById('sub-btn-quarantine');
    this.subBtnHealth = document.getElementById('sub-btn-health');
    this.subBtnEvolution = document.getElementById('sub-btn-evolution');
    this.searchInput = document.getElementById('maintenance-search-input');
    this.statusFilter = document.getElementById('maintenance-status-filter');
    this.btnSweep = document.getElementById('btn-sweep-stale');
    this.btnCreate = document.getElementById('btn-create-backlog');

    // Right-side Drawer Elements (Option B)
    this.drawer = document.getElementById('maintenance-detail-drawer');
    this.drawerCloseBtn = document.getElementById('maint-drawer-btn-close');
    this.drawerStatusPill = document.getElementById('maint-drawer-status-pill');
    this.drawerTitle = document.getElementById('maint-drawer-title');
    this.drawerType = document.getElementById('maint-drawer-type');
    this.drawerPriority = document.getElementById('maint-drawer-priority');
    this.drawerTargets = document.getElementById('maint-drawer-targets');
    this.drawerScope = document.getElementById('maint-drawer-scope');
    this.drawerDesc = document.getElementById('maint-drawer-desc');
    this.drawerJson = document.getElementById('maint-drawer-json');
    this.drawerActions = document.getElementById('maint-drawer-actions-container');
    this.btnResolve = document.getElementById('maint-act-resolve');
    this.btnBlock = document.getElementById('maint-act-block');
    this.quarantineEditor = document.createElement('section');
    this.quarantineEditor.id = 'quarantine-editor';
    this.drawer.querySelector('.drawer-body').appendChild(this.quarantineEditor);
    this.quarantine = new QuarantineView(this.container, this.quarantineEditor, {
      openShell: record => this.openQuarantineDrawer(record), closeShell: () => this.closeDrawer(),
      onNavigateToNote,
    });

    this.bindEvents();
  }

  bindEvents() {
    this.subBtnBacklog.addEventListener('click', () => this.switchSubTab('backlog'));
    this.subBtnLogsheet.addEventListener('click', () => this.switchSubTab('logsheet'));
    this.subBtnQuarantine.addEventListener('click', () => this.switchSubTab('quarantine'));
    if (this.subBtnHealth) this.subBtnHealth.addEventListener('click', () => this.switchSubTab('health'));
    if (this.subBtnEvolution) this.subBtnEvolution.addEventListener('click', () => this.switchSubTab('evolution'));

    this.searchInput.addEventListener('input', () => this.render());
    this.statusFilter.addEventListener('change', () => this.render());

    this.drawerCloseBtn.addEventListener('click', () => this.closeDrawer());
    this.btnResolve.addEventListener('click', () => this.resolveActiveItem());
    this.btnBlock.addEventListener('click', () => this.blockActiveItem());

    this.btnSweep.addEventListener('click', () => this.sweepStaleEvidence());
    this.btnCreate.addEventListener('click', () => this.createManualBacklogItem());

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.drawer.classList.contains('open')) {
        this.closeDrawer();
      }
    });
  }

  switchSubTab(sub) {
    if (sub !== 'quarantine') this.quarantine.deactivate();
    this.activeSub = sub;
    this.subBtnBacklog.classList.toggle('active', sub === 'backlog');
    this.subBtnLogsheet.classList.toggle('active', sub === 'logsheet');
    this.subBtnQuarantine.classList.toggle('active', sub === 'quarantine');
    if (this.subBtnHealth) this.subBtnHealth.classList.toggle('active', sub === 'health');
    if (this.subBtnEvolution) this.subBtnEvolution.classList.toggle('active', sub === 'evolution');

    this.statusFilter.style.display = sub === 'backlog' ? 'block' : 'none';
    const isListTab = sub === 'backlog' || sub === 'logsheet';
    this.searchInput.parentElement.style.display = isListTab ? '' : 'none';
    this.btnSweep.style.display = isListTab ? '' : 'none';
    this.btnCreate.style.display = isListTab ? '' : 'none';
    this.closeDrawer();
    this.render();
  }

  async loadData() {
    try {
      const [backlog, logsheet] = await Promise.all([
        API.fetchBacklog(),
        API.fetchLogsheet(),
      ]);
      this.backlogItems = backlog;
      this.logsheetEntries = logsheet;
      this.render();
    } catch (err) {
      this.onToast(`Failed to load maintenance data: ${err.message}`, 'error');
    }
  }

  render() {
    if (this.activeSub === 'quarantine') {
      this.quarantine.activate();
    } else if (this.activeSub === 'health') {
      this.renderHealth();
    } else if (this.activeSub === 'evolution') {
      this.renderEvolution();
    } else if (this.activeSub === 'backlog') {
      this.renderBacklog();
    } else {
      this.renderLogsheet();
    }
  }

  async renderHealth() {
    this.container.innerHTML = `<div class="empty-state"><div>正在诊断知识健康与空闲蒸馏计划 (§32–35)…</div></div>`;
    try {
      const [report, idlePlan] = await Promise.all([
        API.fetchKnowledgeHealth({ scope: 'default' }),
        API.fetchIdlePlan({ scope: 'default' }),
      ]);
      this.healthReport = report;
      this.idlePlan = idlePlan;
      const counts = report.counts || {};
      const findings = report.findings || [];
      const jobs = idlePlan.jobs || [];

      const wrap = document.createElement('div');
      wrap.style.display = 'flex';
      wrap.style.flexDirection = 'column';
      wrap.style.gap = '14px';

      const summaryCard = document.createElement('div');
      summaryCard.className = 'maintenance-row-card';
      summaryCard.style.padding = '16px';
      summaryCard.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <strong style="font-size:15px;">🩺 知识健康诊断 (§32–35)</strong>
          <span class="status-pill ${findings.length ? 'status-open' : 'status-resolved'}">${findings.length} 项待复核发现 · 覆盖: ${this.escapeHtml(report.coverage || 'complete')}</span>
        </div>
        <div class="dimensions-grid" style="margin-bottom:12px;">
          <div class="dim-card"><span class="dim-label">未核验记忆</span><span class="dim-value">${counts.unverified ?? 0}</span></div>
          <div class="dim-card"><span class="dim-label">缺来源证据</span><span class="dim-value">${counts.unsupported ?? 0}</span></div>
          <div class="dim-card"><span class="dim-label">过期/陈旧</span><span class="dim-value">${counts.stale ?? 0}</span></div>
          <div class="dim-card"><span class="dim-label">孤立/未决冲突</span><span class="dim-value">${(counts.isolated ?? 0) + (counts.conflicted ?? 0)}</span></div>
        </div>
        <div id="health-findings-box" style="display:flex;flex-direction:column;gap:8px;"></div>
      `;
      const findingsBox = summaryCard.querySelector('#health-findings-box');
      if (!findings.length) {
        findingsBox.innerHTML = `<div style="font-size:13px;color:var(--text-muted);">当前范围内所有记忆节点健康度良好，无异常发现。</div>`;
      } else {
        const queueAllBtn = document.createElement('button');
        queueAllBtn.className = 'btn-primary';
        queueAllBtn.style.alignSelf = 'flex-start';
        queueAllBtn.textContent = `一键将前 ${Math.min(20, findings.length)} 项异常加入复核事项`;
        queueAllBtn.onclick = async () => {
          queueAllBtn.disabled = true;
          try {
            const ids = findings.slice(0, 20).map(f => f.id);
            const res = await API.queueKnowledgeHealthReview({
              scope: report.scope || 'default',
              reportHash: report.report_hash,
              selectedIds: ids,
            });
            this.onToast(`已将 ${res.queued?.length || 0} 项知识异常转入维护事项。`, 'success');
            await this.loadData();
          } catch (e) {
            this.onToast(`加入复核失败：${e.message}`, 'error');
            queueAllBtn.disabled = false;
          }
        };
        findingsBox.appendChild(queueAllBtn);

        for (const f of findings.slice(0, 30)) {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border:1px solid rgba(255,255,255,0.08);border-radius:8px;font-size:12px;';
          row.innerHTML = `
            <div>
              <strong>[${this.escapeHtml(f.category)}]</strong>
              <span style="margin-left:6px;font-family:monospace;">${this.escapeHtml(f.node_id)}</span>
              <span style="margin-left:8px;color:var(--text-muted);">${this.escapeHtml(f.reason)}</span>
            </div>
            <div style="display:flex;gap:6px;">
              <button type="button" class="btn-secondary" data-view-node="${this.escapeHtml(f.node_id)}" style="font-size:11px;padding:3px 8px;">查看节点</button>
              <button type="button" class="btn-primary" data-queue-id="${this.escapeHtml(f.id)}" style="font-size:11px;padding:3px 8px;">加入复核</button>
            </div>
          `;
          row.querySelector('[data-view-node]').onclick = () => this.onNavigateToMemory(f.node_id);
          row.querySelector('[data-queue-id]').onclick = async (ev) => {
            ev.currentTarget.disabled = true;
            try {
              await API.queueKnowledgeHealthReview({
                scope: report.scope || 'default',
                reportHash: report.report_hash,
                selectedIds: [f.id],
              });
              this.onToast(`已加入维护复核：${f.node_id}`, 'success');
            } catch (e) {
              this.onToast(`操作失败：${e.message}`, 'error');
              ev.currentTarget.disabled = false;
            }
          };
          findingsBox.appendChild(row);
        }
      }

      const idleCard = document.createElement('div');
      idleCard.className = 'maintenance-row-card';
      idleCard.style.padding = '16px';
      idleCard.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <strong style="font-size:15px;">⏳ 空闲维护与预算受限计划 (Idle Plan)</strong>
          <span class="priority-badge priority-medium">预算上限: ${idlePlan.token_budget ?? 5000} tokens · 已规划: ${idlePlan.planned_tokens ?? 0} tokens</span>
        </div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">停止原因: ${this.escapeHtml(idlePlan.stop_reason || 'complete')} · 共规划 ${jobs.length} 项任务</div>
        <div id="idle-plan-jobs" style="display:flex;flex-direction:column;gap:6px;"></div>
      `;
      const jobsBox = idleCard.querySelector('#idle-plan-jobs');
      if (!jobs.length) {
        jobsBox.innerHTML = `<div style="font-size:12px;color:var(--text-muted);">当前无待执行的空闲维护任务。</div>`;
      } else {
        for (const j of jobs) {
          const jr = document.createElement('div');
          jr.style.cssText = 'padding:6px 10px;background:rgba(255,255,255,0.03);border-radius:6px;font-size:12px;display:flex;justify-content:space-between;';
          jr.innerHTML = `<span><strong>${this.escapeHtml(j.type)}</strong> · ${this.escapeHtml(j.reason || j.id)}</span><span>~${j.estimated_tokens ?? 0} tokens (${this.escapeHtml(j.priority || 'normal')})</span>`;
          jobsBox.appendChild(jr);
        }
      }

      wrap.append(summaryCard, idleCard);
      this.container.replaceChildren(wrap);
    } catch (err) {
      this.container.innerHTML = `<div class="empty-state"><div>读取知识体检失败：${this.escapeHtml(err.message)}</div></div>`;
    }
  }

  async renderEvolution() {
    this.container.innerHTML = `<div class="empty-state"><div>正在读取自演进引擎记录与激活策略 (§96)…</div></div>`;
    try {
      const [records, strategies, experiences, evaluations, budget, activeRank, activeCtx] = await Promise.all([
        API.fetchEvolutionRecords({ scope: 'default' }),
        API.fetchEvolutionItems('strategy', { scope: 'default' }),
        API.fetchEvolutionItems('experience', { scope: 'default' }),
        API.fetchEvolutionItems('evaluation', { scope: 'default' }),
        API.fetchEvolutionBudget('default'),
        API.fetchEvolutionActiveConfig('retrieval_ranking', 'default'),
        API.fetchEvolutionActiveConfig('context_config', 'default'),
      ]);

      const wrap = document.createElement('div');
      wrap.style.display = 'flex';
      wrap.style.flexDirection = 'column';
      wrap.style.gap = '14px';

      const overview = document.createElement('div');
      overview.className = 'maintenance-row-card';
      overview.style.padding = '16px';
      overview.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <strong style="font-size:15px;">🧬 自演进引擎与策略治理 (§96 · 已接入 RetrievalCoordinator)</strong>
          <span class="status-pill status-resolved">窗口剩余任务预算: ${budget.remaining ?? '—'} / ${budget.max_jobs ?? '—'}</span>
        </div>
        <div class="dimensions-grid" style="margin-bottom:10px;">
          <div class="dim-card"><span class="dim-label">执行记录 (Records)</span><span class="dim-value">${records.length}</span></div>
          <div class="dim-card"><span class="dim-label">评估报告 (Evaluations)</span><span class="dim-value">${evaluations.length}</span></div>
          <div class="dim-card"><span class="dim-label">蒸馏经验 (Experiences)</span><span class="dim-value">${experiences.length}</span></div>
          <div class="dim-card"><span class="dim-label">演进策略 (Strategies)</span><span class="dim-value">${strategies.length}</span></div>
        </div>
        <div style="font-size:12px;color:var(--text-muted);">
          <div>当前激活 <code>retrieval_ranking</code> 配置: <code>${this.escapeHtml(JSON.stringify(activeRank.configuration || null))}</code></div>
          <div>当前激活 <code>context_config</code> 配置: <code>${this.escapeHtml(JSON.stringify(activeCtx.configuration || null))}</code></div>
        </div>
      `;

      const stratBox = document.createElement('div');
      stratBox.className = 'maintenance-row-card';
      stratBox.style.padding = '16px';
      stratBox.innerHTML = `<strong style="font-size:14px;display:block;margin-bottom:8px;">候选与激活策略列表</strong>`;
      if (!strategies.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'font-size:12px;color:var(--text-muted);';
        empty.textContent = '暂无候选演进策略。当外部 Agent 导入执行记录并完成评估后，可提交候选策略并在审批后激活。';
        stratBox.appendChild(empty);
      } else {
        for (const s of strategies) {
          const itemRow = document.createElement('div');
          itemRow.style.cssText = 'padding:10px;border:1px solid rgba(255,255,255,0.08);border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;font-size:12px;';
          itemRow.innerHTML = `
            <div>
              <strong>${this.escapeHtml(s.strategy_key || s.id)}</strong>
              <span class="status-pill status-open" style="margin-left:6px;">${this.escapeHtml(s.state || 'CANDIDATE')}</span>
              <span style="margin-left:6px;color:var(--text-muted);">v${s.revision || 1} · 配置: <code>${this.escapeHtml(JSON.stringify(s.configuration || {}))}</code></span>
            </div>
            <div style="display:flex;gap:6px;">
              ${s.state === 'CANDIDATE' ? `<button type="button" class="btn-secondary" data-act="request-approval" style="font-size:11px;padding:3px 8px;">申请审批</button>` : ''}
              ${s.state === 'PENDING_APPROVAL' ? `<button type="button" class="btn-primary" data-act="approve" style="font-size:11px;padding:3px 8px;">批准策略</button>` : ''}
              ${s.state === 'ACTIVE' ? `<button type="button" class="btn-status-block" data-act="rollback" style="font-size:11px;padding:3px 8px;">回滚策略</button>` : ''}
            </div>
          `;
          itemRow.querySelectorAll('[data-act]').forEach(btn => {
            btn.onclick = async () => {
              const act = btn.dataset.act;
              const reason = act === 'approve' || act === 'rollback' ? prompt('请输入操作原因：', 'Local owner strategy governance') : null;
              if ((act === 'approve' || act === 'rollback') && !reason) return;
              try {
                await API.transitionEvolutionStrategy(s.id, act, {
                  scope: s.scope || 'default',
                  expectedRevision: s.revision || 1,
                  reason,
                });
                this.onToast(`策略 ${s.id} 已执行 ${act}`, 'success');
                this.renderEvolution();
              } catch (e) {
                this.onToast(`操作失败：${e.message}`, 'error');
              }
            };
          });
          stratBox.appendChild(itemRow);
        }
      }

      wrap.append(overview, stratBox);
      this.container.replaceChildren(wrap);
    } catch (err) {
      this.container.innerHTML = `<div class="empty-state"><div>读取演进引擎失败：${this.escapeHtml(err.message)}</div></div>`;
    }
  }

  renderBacklog() {
    const query = (this.searchInput.value || '').trim().toLowerCase();
    const statusVal = this.statusFilter.value;

    const filtered = this.backlogItems.filter((item) => {
      const matchStatus = !statusVal || item.status === statusVal;
      const matchQuery =
        !query ||
        item.reason.toLowerCase().includes(query) ||
        item.type.toLowerCase().includes(query) ||
        (item.id && item.id.toLowerCase().includes(query)) ||
        JSON.stringify(item.targets || []).toLowerCase().includes(query);
      return matchStatus && matchQuery;
    });

    this.container.innerHTML = '';

    if (filtered.length === 0) {
      this.container.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">📋</div>
          <div style="font-weight: 600; color: var(--text-muted);">No backlog items found</div>
          <div style="font-size: 13px;">System Memory is currently coherent and up to date.</div>
        </div>
      `;
      return;
    }

    filtered.forEach((item) => {
      const card = document.createElement('div');
      card.className = 'maintenance-row-card';
      card.id = `backlog-row-${item.id}`;

      const statusClass = item.status === 'RESOLVED' ? 'status-resolved' : item.status === 'BLOCKED' ? 'status-blocked' : 'status-open';
      const priorityClass = `priority-${(item.priority || 'medium').toLowerCase()}`;
      const targetsStr = (item.targets || []).map((t) => (typeof t === 'string' ? t : t.id || t.node_id || JSON.stringify(t))).join(', ') || 'Global';
      const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : '';

      card.innerHTML = `
        <div class="row-summary-header">
          <div class="row-left-group">
            <span class="row-expand-toggle">▶</span>
            <span class="status-pill ${statusClass}">${item.status}</span>
            <div class="row-title-area">
              <span class="row-title">${this.escapeHtml(item.reason || item.type)}</span>
              <div class="row-subtitle">
                <span>Type: <strong>${item.type}</strong></span>
                <span>•</span>
                <span>Targets: ${this.escapeHtml(targetsStr)}</span>
                <span>•</span>
                <span>${dateStr}</span>
              </div>
            </div>
          </div>
          <div class="row-right-group">
            <span class="priority-badge ${priorityClass}">${item.priority || 'MEDIUM'}</span>
            <button class="btn-secondary btn-inspect" style="font-size:11px; padding:4px 10px;">Details</button>
          </div>
        </div>

        <!-- Option B: Inline Accordion Summary Expansion (§4, §7.1) -->
        <div class="row-accordion-body">
          <div class="accordion-grid">
            <div class="accordion-field">
              <span class="accordion-field-label">Item ID</span>
              <span class="accordion-field-val" style="font-family:monospace;">${item.id}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Approval Status</span>
              <span class="accordion-field-val">${item.approval_status || 'NOT_REQUIRED'}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Priority Rationale</span>
              <span class="accordion-field-val">${this.escapeHtml(item.priority_reason || 'Standard')}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Blocker / Resolution</span>
              <span class="accordion-field-val">${this.escapeHtml(item.blocker || (item.resolution && item.resolution.note) || 'None')}</span>
            </div>
          </div>
          <div class="accordion-actions">
            ${
              item.status !== 'RESOLVED'
                ? `<button class="btn-status-resolve btn-row-resolve">Resolve Task</button>`
                : ''
            }
            <button class="btn-primary btn-open-drawer" style="font-size:11px; padding:4px 12px;">Full Record Drawer →</button>
          </div>
        </div>
      `;

      const header = card.querySelector('.row-summary-header');
      const inspectBtn = card.querySelector('.btn-inspect');
      const drawerBtn = card.querySelector('.btn-open-drawer');
      const rowResolveBtn = card.querySelector('.btn-row-resolve');

      header.addEventListener('click', (e) => {
        if (e.target.closest('.btn-inspect')) return;
        card.classList.toggle('expanded');
      });

      inspectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openBacklogDrawer(item);
      });

      drawerBtn.addEventListener('click', () => {
        this.openBacklogDrawer(item);
      });

      if (rowResolveBtn) {
        rowResolveBtn.addEventListener('click', () => {
          this.activeItem = item;
          this.resolveActiveItem();
        });
      }

      this.container.appendChild(card);
    });
  }

  renderLogsheet() {
    const query = (this.searchInput.value || '').trim().toLowerCase();

    const filtered = this.logsheetEntries.filter((entry) => {
      if (!query) return true;
      return (
        entry.reason.toLowerCase().includes(query) ||
        entry.event_type.toLowerCase().includes(query) ||
        entry.operation_id.toLowerCase().includes(query) || JSON.stringify(entry.target_refs || []).toLowerCase().includes(query)
      );
    });

    this.container.innerHTML = '';

    if (filtered.length === 0) {
      this.container.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">📜</div>
          <div style="font-weight: 600; color: var(--text-muted);">No logsheet records found</div>
          <div style="font-size: 13px;">Maintenance actions and automated sweeps will appear here.</div>
        </div>
      `;
      return;
    }

    filtered.forEach((entry) => {
      const card = document.createElement('div');
      card.className = 'maintenance-row-card';

      const statusClass = entry.result === 'SUCCESS' ? 'status-resolved' : ['FAILED','FAILURE','ERROR','BLOCKED'].includes(entry.result) ? 'status-blocked' : 'status-open';
      const actorName = entry.actor ? (entry.actor.name || entry.actor.id || JSON.stringify(entry.actor)) : 'system';
      const targetsStr = (entry.target_refs || []).map((t) => (typeof t === 'string' ? t : t.id || t.node_id || JSON.stringify(t))).join(', ') || 'N/A';
      const timestamp = entry.recorded_at || entry.created_at;
      const dateStr = timestamp ? new Date(timestamp).toLocaleString() : '';

      card.innerHTML = `
        <div class="row-summary-header">
          <div class="row-left-group">
            <span class="row-expand-toggle">▶</span>
            <span class="status-pill ${statusClass}">${entry.result}</span>
            <div class="row-title-area">
              <span class="row-title">${this.escapeHtml(entry.reason || entry.event_type)}</span>
              <div class="row-subtitle">
                <span>Op: <strong>${entry.event_type}</strong></span>
                <span>•</span>
                <span>Actor: ${this.escapeHtml(actorName)}</span>
                <span>•</span>
                <span>${dateStr}</span>
              </div>
            </div>
          </div>
          <div class="row-right-group">
            <button class="btn-secondary btn-inspect" style="font-size:11px; padding:4px 10px;">Details</button>
          </div>
        </div>

        <div class="row-accordion-body">
          <div class="accordion-grid">
            <div class="accordion-field">
              <span class="accordion-field-label">Operation ID</span>
              <span class="accordion-field-val" style="font-family:monospace;">${entry.operation_id}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Target References</span>
              <span class="accordion-field-val">${this.escapeHtml(targetsStr)}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Linked Backlog ID</span>
              <span class="accordion-field-val">${entry.backlog_id || 'Direct Operation'}</span>
            </div>
            <div class="accordion-field">
              <span class="accordion-field-label">Idempotency Key</span>
              <span class="accordion-field-val" style="font-family:monospace;">${entry.idempotency_key || 'None'}</span>
            </div>
          </div>
          <div class="accordion-actions">
            <button class="btn-primary btn-open-drawer" style="font-size:11px; padding:4px 12px;">Full Log Record Drawer →</button>
          </div>
        </div>
      `;

      const header = card.querySelector('.row-summary-header');
      const inspectBtn = card.querySelector('.btn-inspect');
      const drawerBtn = card.querySelector('.btn-open-drawer');

      header.addEventListener('click', (e) => {
        if (e.target.closest('.btn-inspect')) return;
        card.classList.toggle('expanded');
      });

      inspectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openLogsheetDrawer(entry);
      });

      drawerBtn.addEventListener('click', () => {
        this.openLogsheetDrawer(entry);
      });

      this.container.appendChild(card);
    });
  }

  // --- Right-side Detail Drawer (Option B) ---
  openBacklogDrawer(item) {
    this.quarantine.closeEditor();
    this.activeItem = item;
    const statusClass = item.status === 'RESOLVED' ? 'status-resolved' : item.status === 'BLOCKED' ? 'status-blocked' : 'status-open';

    this.drawerStatusPill.className = `status-pill ${statusClass}`;
    this.drawerStatusPill.textContent = item.status;
    this.drawerTitle.textContent = item.reason || item.type;
    this.drawerType.textContent = item.type;
    this.drawerPriority.textContent = item.priority || 'MEDIUM';

    this.renderTargetLinks(item.targets || []);
    this.drawerScope.textContent = item.scope || 'default';
    this.drawerDesc.textContent = item.reason;

    this.drawerJson.textContent = JSON.stringify(item, null, 2);
    this.drawerActions.style.display = 'block';

    this.drawer.classList.add('open');
  }

  openLogsheetDrawer(entry) {
    this.quarantine.closeEditor();
    this.activeItem = entry;
    const statusClass = entry.result === 'SUCCESS' ? 'status-resolved' : ['FAILED','FAILURE','ERROR','BLOCKED'].includes(entry.result) ? 'status-blocked' : 'status-open';

    this.drawerStatusPill.className = `status-pill ${statusClass}`;
    this.drawerStatusPill.textContent = entry.result;
    this.drawerTitle.textContent = entry.reason || entry.event_type;
    this.drawerType.textContent = entry.event_type;
    this.drawerPriority.textContent = entry.result;

    this.renderTargetLinks(entry.target_refs || []);
    this.drawerScope.textContent = entry.scope || 'default';
    this.drawerDesc.textContent = entry.reason;

    this.drawerJson.textContent = JSON.stringify(entry, null, 2);
    this.drawerActions.style.display = 'none';

    this.drawer.classList.add('open');
  }

  closeDrawer() {
    this.quarantine.closeEditor();
    this.drawer.classList.remove('open');
    this.activeItem = null;
  }

  openQuarantineDrawer(record) {
    this.activeItem = null;
    this.drawerStatusPill.className = 'status-pill ' + (record.resolved ? 'status-resolved' : 'status-blocked');
    this.drawerStatusPill.textContent = record.resolved ? 'RECOVERED' : 'NEEDS REPAIR';
    this.drawerTitle.textContent = record.submission?.claim || record.payload.title || record.id;
    this.drawerType.textContent = 'Invalid submission';
    this.drawerPriority.textContent = 'Original data preserved';
    this.drawerTargets.textContent = record.source_id || 'Source unknown';
    this.drawerScope.textContent = record.scope || 'Unknown — owner review required';
    this.drawerDesc.textContent = record.reason;
    this.drawerJson.textContent = JSON.stringify(record, null, 2);
    this.drawerActions.style.display = 'none';
    this.drawer.classList.add('open');
  }

  renderTargetLinks(targets) {
    this.drawerTargets.replaceChildren();
    for (const target of targets) {
      const id = typeof target === 'string' ? target : target.id || target.node_id;
      if (!id) continue;
      const element = document.createElement(['candidate', 'backlog', 'quarantine'].includes(target.type) ? 'span' : 'button');
      element.textContent = id;
      element.className = 'btn-secondary';
      if (element.tagName === 'BUTTON') element.addEventListener('click', () => this.onNavigateToMemory(id));
      this.drawerTargets.appendChild(element);
    }
    if (!this.drawerTargets.childNodes.length) this.drawerTargets.textContent = 'Scope-wide review';
  }

  async resolveActiveItem() {
    if (!this.activeItem) return;
    const resolutionNote = prompt('Describe the verification you actually performed and its evidence:');
    if (!resolutionNote) return;

    try {
      await API.transitionBacklog(this.activeItem.id, {
        expectedRevision: this.activeItem.revision,
        status: 'RESOLVED',
        reason: 'Resolved by operator',
        resolution: {
          resolved_by: 'user',
          note: resolutionNote,
          timestamp: new Date().toISOString(),
        },
      });

      this.onToast('Backlog item resolved and logged in Logsheet.', 'success');
      this.closeDrawer();
      await this.loadData();
    } catch (err) {
      this.onToast(`Resolution failed: ${err.message}`, 'error');
    }
  }

  async blockActiveItem() {
    if (!this.activeItem) return;
    const blockerReason = prompt('Enter reason for blocking:', 'Awaiting corroborating evidence from user notes.');
    if (!blockerReason) return;

    try {
      await API.transitionBacklog(this.activeItem.id, {
        expectedRevision: this.activeItem.revision,
        status: 'BLOCKED',
        reason: 'Marked blocked by operator',
        blocker: blockerReason,
      });

      this.onToast('Backlog item marked as BLOCKED.', 'warning');
      this.closeDrawer();
      await this.loadData();
    } catch (err) {
      this.onToast(`Failed to update status: ${err.message}`, 'error');
    }
  }

  async sweepStaleEvidence() {
    try {
      this.btnSweep.disabled = true;
      this.btnSweep.textContent = 'Sweeping...';

      await API.createBacklogItem({
        type: 'REFRESH_STALE',
        targets: [{ scope: 'default', query: 'stale_check' }],
        reason: 'Operator requested review of memory evidence freshness',
        priority: 'high',
        priorityReason: 'Memory freshness check',
        scope: 'default',
      });

      this.onToast('Evidence review queued. No automatic verification has run.', 'success');
      await this.loadData();
    } catch (err) {
      this.onToast(`Sweep failed: ${err.message}`, 'error');
    } finally {
      this.btnSweep.disabled = false;
      this.btnSweep.textContent = 'Queue evidence review';
    }
  }

  async createManualBacklogItem() {
    const reason = prompt('Enter maintenance task description:');
    if (!reason) return;

    try {
      await API.createBacklogItem({
        type: 'RESOLVE_ENTITY',
        targets: [{ scope: 'default' }],
        reason,
        priority: 'normal',
        scope: 'default',
      });
      this.onToast('New backlog item created.', 'success');
      await this.loadData();
    } catch (err) {
      this.onToast(`Creation failed: ${err.message}`, 'error');
    }
  }

  filterByTargetNode(nodeId) {
    this.switchSubTab('backlog');
    this.searchInput.value = nodeId;
    this.render();
  }

  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
}
