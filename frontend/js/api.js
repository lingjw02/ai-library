/**
 * AI Library - Frontend REST API Client
 * Wraps AI Knowledge OS Backend v1.4 REST endpoints.
 * Local-first, revision-aware, strict error propagation.
 */

const BASE_URL = '';

async function request(endpoint, options = {}) {
  const url = `${BASE_URL}${endpoint}`;
  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  };

  try {
    const res = await fetch(url, config);
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errorJson = await res.json();
        if (errorJson.detail) {
          errorMsg = typeof errorJson.detail === 'string' ? errorJson.detail : JSON.stringify(errorJson.detail);
        }
      } catch (_) {}
      throw new Error(errorMsg);
    }
    return await res.json();
  } catch (err) {
    console.error(`[API Error] ${options.method || 'GET'} ${endpoint}:`, err);
    throw err;
  }
}

export const API = {
  /** 星图内容指纹：任何内容变更（含外部进程写入）都会使 fingerprint 变化。 */
  async getUniverseFingerprint(scope = 'default') {
    return request(`/api/universe/fingerprint?${new URLSearchParams({ scope })}`);
  },

  async fetchQuarantine({ scope = 'default', view = 'open', offset = 0 } = {}) {
    const query = new URLSearchParams({ scope, offset, limit: 20, resolved: view === 'resolved' });
    return request(`${view === 'legacy' ? '/api/quarantine-legacy' : '/api/quarantine'}?${query}`);
  },

  async fetchQuarantineRecord(id, scope) {
    return request(`/api/quarantine/${encodeURIComponent(id)}?${new URLSearchParams({ scope })}`);
  },

  async repairQuarantine(id, data) {
    return request(`/api/quarantine/${encodeURIComponent(id)}/repair`, { method: 'POST', body: JSON.stringify(data) });
  },

  async fetchConflicts({ scope = 'default', nodeId, status, offset = 0 } = {}) {
    const query = new URLSearchParams({ scope, offset, limit: 20 });
    if (nodeId) query.set('node_id', nodeId);
    if (status) query.set('status', status);
    return request(`/api/conflicts?${query}`);
  },

  async fetchConflict(id, scope) {
    return request(`/api/conflicts/${encodeURIComponent(id)}?${new URLSearchParams({ scope })}`);
  },

  async reviewConflict(id, review) {
    return request(`/api/conflicts/${encodeURIComponent(id)}/transition`, {
      method: 'POST', body: JSON.stringify(review),
    });
  },

  async searchDocuments(query, plane, scope = 'default', nodeType = null) {
    const params = new URLSearchParams({ query, plane, scope, limit: '100' });
    if (nodeType) params.set('node_type', nodeType);
    return request(`/api/retrieval/documents?${params}`);
  },
  // --- Health & Telemetry ---
  async checkHealth() {
    return request('/api/health');
  },

  // --- Notes Plane ---
  async fetchNotes(scope = null, limit = 100) {
    const query = new URLSearchParams();
    if (scope) query.set('scope', scope);
    if (limit) query.set('limit', limit);
    return request(`/api/notes?${query.toString()}`);
  },

  async fetchNote(noteId, revision = null) {
    const query = revision ? `?revision=${revision}` : '';
    return request(`/api/notes/${encodeURIComponent(noteId)}${query}`);
  },

  async createNote({ title, content, scope = 'default', author = 'user' }) {
    return request('/api/notes', {
      method: 'POST',
      body: JSON.stringify({ title, content, scope, author }),
    });
  },

  async reviseNote(noteId, { title, content, author = 'user', expectedRevision }) {
    return request(`/api/notes/${encodeURIComponent(noteId)}/revisions`, {
      method: 'POST',
      body: JSON.stringify({ title, content, author, expected_revision: expectedRevision }),
    });
  },

  // --- Memory Plane ---
  async fetchMemoryNodes({ nodeType = null, scope = null, maturity = null, depth = null, limit = 100 } = {}) {
    const query = new URLSearchParams();
    if (nodeType) query.set('node_type', nodeType);
    if (scope) query.set('scope', scope);
    if (maturity) query.set('maturity', maturity);
    if (depth) query.set('depth', depth);
    query.set('limit', limit);
    return request(`/api/memory/nodes?${query.toString()}`);
  },

  async fetchMemoryNode(nodeId) {
    return request(`/api/memory/nodes/${encodeURIComponent(nodeId)}`);
  },

  async fetchMemoryRevisions(nodeId, { scope = 'default', limit = 100 } = {}) {
    return request(`/api/memory/nodes/${encodeURIComponent(nodeId)}/revisions?${new URLSearchParams({ scope, limit })}`);
  },

  async saveMemoryNode(nodeData, expectedRevision = null) {
    const query = expectedRevision === null ? '' : `?expected_revision=${encodeURIComponent(expectedRevision)}`;
    return request(`/api/memory/nodes${query}`, {
      method: 'POST',
      body: JSON.stringify(nodeData),
    });
  },

  async fetchMemoryEdges({ scope = null, limit = 200 } = {}) {
    const query = new URLSearchParams();
    if (scope) query.set('scope', scope);
    query.set('limit', limit);
    return request(`/api/memory/edges?${query.toString()}`);
  },

  async fetchNodeEdges(nodeId) {
    return request(`/api/memory/nodes/${encodeURIComponent(nodeId)}/edges`);
  },

  // --- Candidate Bridge ---
  async fetchCandidates(scope = null, limit = 100, sourceId = null) {
    const query = new URLSearchParams();
    if (sourceId) query.set('source_id', sourceId);
    if (scope) query.set('scope', scope);
    query.set('limit', limit);
    return request(`/api/candidates?${query.toString()}`);
  },

  async submitCandidate({ sourceId, sourceRevision = 1, claim, suggestedNodeType = 'Fact', payload = {}, scope = 'default', authority = 'INFERRED' }) {
    return request('/api/candidates', {
      method: 'POST',
      body: JSON.stringify({
        source_id: sourceId,
        source_revision: sourceRevision,
        claim,
        suggested_node_type: suggestedNodeType,
        payload,
        scope,
        authority,
      }),
    });
  },

  async acceptCandidate(candidateId, reviewer = 'user') {
    return request(`/api/candidates/${encodeURIComponent(candidateId)}/accept?reviewer=${encodeURIComponent(reviewer)}`, {
      method: 'POST',
    });
  },

  async rejectCandidate(candidateId, reason = '') {
    return request(`/api/candidates/${encodeURIComponent(candidateId)}/reject?reason=${encodeURIComponent(reason)}`, {
      method: 'POST',
    });
  },

  // --- System Memory Backlog ---
  async fetchBacklog({ scope = null, limit = 100 } = {}) {
    const query = new URLSearchParams();
    if (scope) query.set('scope', scope);
    query.set('limit', limit);
    return request(`/api/maintenance/backlog?${query.toString()}`);
  },

  async createBacklogItem({ type, targets = [], reason, priority = 'normal', priorityReason = 'Standard maintenance', scope = 'default', blocker = null }) {
    return request('/api/maintenance/backlog', {
      method: 'POST',
      body: JSON.stringify({
        type,
        targets,
        reason,
        priority,
        priority_reason: priorityReason,
        scope,
        blocker,
      }),
    });
  },

  async transitionBacklog(itemId, { status, reason = '', blocker = null, resolution = null, expectedRevision }) {
    return request(`/api/maintenance/backlog/${encodeURIComponent(itemId)}/transition`, {
      method: 'POST',
      body: JSON.stringify({
        status,
        expected_revision: expectedRevision,
        reason,
        blocker,
        resolution,
      }),
    });
  },

  // --- System Memory Logsheet ---
  async fetchLogsheet({ operationId = null, backlogId = null, scope = null, limit = 100 } = {}) {
    const query = new URLSearchParams();
    if (operationId) query.set('operation_id', operationId);
    if (backlogId) query.set('backlog_id', backlogId);
    if (scope) query.set('scope', scope);
    query.set('limit', limit);
    return request(`/api/maintenance/logsheet?${query.toString()}`);
  },

  async appendLogsheet(entry) {
    return request('/api/maintenance/logsheet', {
      method: 'POST',
      body: JSON.stringify(entry),
    });
  },

  // --- Retrieval & Context Compiler ---
  async searchContext({ query, scope = 'default', userSpecifiedPlane = null, tokenBudget = 2048 }) {
    return request('/api/retrieval/search', {
      method: 'POST',
      body: JSON.stringify({
        query,
        scope,
        user_specified_plane: userSpecifiedPlane,
        token_budget: tokenBudget,
      }),
    });
  },

  // --- Settings (local-owner boundary) ---
  async fetchUsageRules() {
    return request('/api/settings/usage-rules', { signal: AbortSignal.timeout(15000) });
  },

  async publishUsageRules(rules, expected_revision) {
    return request('/api/settings/usage-rules', {
      method: 'POST', body: JSON.stringify({ rules, expected_revision }), signal: AbortSignal.timeout(15000),
    });
  },

  async fetchUsageRuleHistory() {
    return request('/api/settings/usage-rules/history', { signal: AbortSignal.timeout(15000) });
  },

  async fetchSettingsAccess(scope = null) {
    const query = scope ? `?${new URLSearchParams({ scope })}` : '';
    return request(`/api/settings/access${query}`);
  },

  async updateSettingsAccess(principal, scope, { access, expected_revision, expires_at = null }) {
    return request(`/api/settings/access/${encodeURIComponent(principal)}/${encodeURIComponent(scope)}`, {
      method: 'POST',
      body: JSON.stringify({ access, expected_revision, expires_at }),
    });
  },

  async fetchSettingsAudit({ principal = null, scope = null, limit = 50 } = {}) {
    const query = new URLSearchParams({ limit });
    if (principal) query.set('principal', principal);
    if (scope) query.set('scope', scope);
    return request(`/api/settings/access/audit?${query}`);
  },

  async fetchSettingsSystem() {
    return request('/api/settings/system');
  },

  async rebuildIndex(path) {
    return request(path, { method: 'POST' });
  },

  async fetchSettingsPreferences() {
    return request('/api/settings/preferences');
  },

  async updateSettingsPreferences(patch) {
    return request('/api/settings/preferences', { method: 'POST', body: JSON.stringify(patch) });
  },

  async fetchSettingsBackups() {
    return request('/api/settings/backups');
  },

  async createSettingsBackup(label = null) {
    return request('/api/settings/backups', { method: 'POST', body: JSON.stringify({ label }) });
  },

  async verifySettingsBackup(name) {
    return request(`/api/settings/backups/${encodeURIComponent(name)}/verify`, { method: 'POST' });
  },

  async restoreSettingsBackup(name) {
    return request(`/api/settings/backups/${encodeURIComponent(name)}/restore`, { method: 'POST' });
  },

  async vacuumSettingsDatabase() {
    return request('/api/settings/maintenance/vacuum', { method: 'POST' });
  },

  async fetchSettingsExport() {
    return request('/api/settings/export');
  },

  async fetchSettingsAbout() {
    return request('/api/settings/about');
  },

  async fetchCacheMetrics() {
    return request('/api/telemetry/cache-metrics');
  },

  // --- Multimedia & Chat History Session API ---
  async fetchNoteChatSession(noteId, { scope = 'default', fullSession = true } = {}) {
    const query = new URLSearchParams({ scope, full_session: String(fullSession) });
    return request(`/api/notes/${encodeURIComponent(noteId)}/chat-session?${query}`);
  },

  async uploadMedia(file, { scope = 'default', title = '', caption = '', noteId = null, createNote = false } = {}) {
    const query = new URLSearchParams({
      filename: file.name || 'upload.bin',
      scope,
      title,
      caption,
      create_note: String(Boolean(createNote)),
    });
    if (noteId) query.set('note_id', noteId);
    const res = await fetch(`/api/media/upload?${query}`, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file,
    });
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errJson = await res.json();
        if (errJson.detail) errorMsg = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      } catch (_) {}
      throw new Error(errorMsg);
    }
    return await res.json();
  },

  async fetchMediaList({ scope = 'default', noteId = null, sessionId = null, mediaKind = null, limit = 100 } = {}) {
    const query = new URLSearchParams({ limit: String(limit) });
    if (scope) query.set('scope', scope);
    if (noteId) query.set('note_id', noteId);
    if (sessionId) query.set('session_id', sessionId);
    if (mediaKind) query.set('media_kind', mediaKind);
    return request(`/api/media?${query}`);
  },

  // --- Source Intake (Web Capture & Text/HTML File Import) ---
  async prepareIntake({ sourceType, title = '', content = null, filename = null, url = null, scope = 'default' }) {
    return request('/api/intake/prepare', {
      method: 'POST',
      body: JSON.stringify({
        source_type: sourceType,
        title,
        content,
        filename,
        url,
        scope,
      }),
    });
  },

  async uploadIntakeFile(file, { scope = 'default', title = '' } = {}) {
    const query = new URLSearchParams({ filename: file.name || 'document.txt', scope, title });
    const res = await fetch(`/api/intake/files?${query}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errJson = await res.json();
        if (errJson.detail) errorMsg = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      } catch (_) {}
      throw new Error(errorMsg);
    }
    return await res.json();
  },

  async confirmIntake(previewId, { expectedRevision, expectedHash, idempotencyKey, selection, collections = [], userComment = '', scope = 'default' }) {
    return request(`/api/intake/${encodeURIComponent(previewId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        scope,
        expected_revision: expectedRevision,
        expected_hash: expectedHash,
        idempotency_key: idempotencyKey,
        selection,
        collections,
        user_comment: userComment,
      }),
    });
  },

  async cancelIntake(previewId, scope = 'default') {
    return request(`/api/intake/${encodeURIComponent(previewId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ scope }),
    });
  },

  async fetchIntakeItem(noteId, { scope = 'default', revision = null } = {}) {
    const query = new URLSearchParams({ scope });
    if (revision !== null && revision !== undefined) query.set('revision', String(revision));
    return request(`/api/intake/items/${encodeURIComponent(noteId)}?${query}`);
  },

  async addIntakeComment(noteId, { scope = 'default', content, expectedRevision }) {
    return request(`/api/intake/items/${encodeURIComponent(noteId)}/comments`, {
      method: 'POST',
      body: JSON.stringify({ scope, content, expected_revision: expectedRevision }),
    });
  },

  async fetchTopics(scope = 'default') {
    return request(`/api/topics?${new URLSearchParams({ scope, limit: '200' })}`);
  },

  // --- Saved Views (§97.3) ---
  async fetchSavedViews({ scope = 'default', workspace = null, includeArchived = false } = {}) {
    const query = new URLSearchParams({ scope, include_archived: String(includeArchived), limit: '50' });
    if (workspace) query.set('workspace', workspace);
    return request(`/api/saved-views?${query}`);
  },

  async createSavedView({ scope = 'default', name, query }) {
    return request('/api/saved-views', {
      method: 'POST',
      body: JSON.stringify({ scope, name, query }),
    });
  },

  async updateSavedView(viewId, { scope = 'default', expectedRevision, name = null, query = null, archived = null }) {
    const body = { scope, expected_revision: expectedRevision };
    if (name !== null) body.name = name;
    if (query !== null) body.query = query;
    if (archived !== null) body.archived = archived;
    return request(`/api/saved-views/${encodeURIComponent(viewId)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },

  async executeSavedView(viewId, { scope = 'default', expectedRevision, offset = 0, limit = 50 }) {
    return request(`/api/saved-views/${encodeURIComponent(viewId)}/execute`, {
      method: 'POST',
      body: JSON.stringify({ scope, expected_revision: expectedRevision, offset, limit }),
    });
  },

  // --- Knowledge Health, Idle Plan, Graph Projection & Token Budget (§32–48, §95) ---
  async fetchKnowledgeHealth({ scope = 'default', limit = 1000 } = {}) {
    return request(`/api/maintenance/health?${new URLSearchParams({ scope, limit: String(limit) })}`);
  },

  async queueKnowledgeHealthReview({ scope = 'default', reportHash, selectedIds }) {
    return request('/api/maintenance/health/review', {
      method: 'POST',
      body: JSON.stringify({ scope, report_hash: reportHash, selected_ids: selectedIds }),
    });
  },

  async fetchIdlePlan({ scope = 'default', maxJobs = 10, tokenBudget = 5000 } = {}) {
    return request(`/api/maintenance/idle-plan?${new URLSearchParams({ scope, max_jobs: String(maxJobs), token_budget: String(tokenBudget) })}`);
  },

  async fetchGraphProjectionStatus() {
    return request('/api/projections/knowledge/status');
  },

  async advanceGraphProjection(limit = 200) {
    return request(`/api/projections/knowledge/advance?limit=${encodeURIComponent(limit)}`, { method: 'POST' });
  },

  async rebuildGraphProjection() {
    return request('/api/projections/knowledge/rebuild', { method: 'POST' });
  },

  async fetchTokenBudget(scope = 'default') {
    return request(`/api/token-budget?${new URLSearchParams({ scope })}`);
  },

  async updateTokenBudgetPolicy({ scope = 'default', dailyQuota, expectedRevision }) {
    return request('/api/token-budget/policy', {
      method: 'PUT',
      body: JSON.stringify({ scope, daily_quota: dailyQuota, expected_revision: expectedRevision }),
    });
  },

  // --- Evolution Engine (§96) ---
  async fetchEvolutionRecords({ scope = 'default', limit = 50 } = {}) {
    return request(`/api/evolution/records?${new URLSearchParams({ scope, limit: String(limit) })}`);
  },

  async fetchEvolutionItems(kind, { scope = 'default', limit = 50 } = {}) {
    return request(`/api/evolution/items/${encodeURIComponent(kind)}?${new URLSearchParams({ scope, limit: String(limit) })}`);
  },

  async fetchEvolutionBudget(scope = 'default') {
    return request(`/api/evolution/budget?${new URLSearchParams({ scope })}`);
  },

  async fetchEvolutionActiveConfig(strategyKey, scope = 'default') {
    return request(`/api/evolution/active/${encodeURIComponent(strategyKey)}?${new URLSearchParams({ scope })}`);
  },

  async transitionEvolutionStrategy(id, action, { scope = 'default', expectedRevision, reason = null, approvalId = null }) {
    const body = { scope, expected_revision: expectedRevision };
    if (reason) body.reason = reason;
    if (approvalId) body.approval_id = approvalId;
    return request(`/api/evolution/strategies/${encodeURIComponent(id)}/${encodeURIComponent(action)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  // --- Lineage & Provenance (§94) ---
  async fetchLineageSource(sourceId, scope = 'default') {
    return request(`/api/lineage/sources/${encodeURIComponent(sourceId)}?${new URLSearchParams({ scope })}`);
  },

  async fetchLineageSession(sessionRef, scope = 'default') {
    return request(`/api/lineage/sessions/${encodeURIComponent(sessionRef)}?${new URLSearchParams({ scope })}`);
  },

  async revokeLineageSession(sessionRef, { scope = 'default', expectedRevision, reason }) {
    return request(`/api/lineage/sessions/${encodeURIComponent(sessionRef)}/revoke`, {
      method: 'POST',
      body: JSON.stringify({ scope, expected_revision: expectedRevision, reason }),
    });
  },

  // --- ViewState Persistence (§95.3) ---
  async fetchViewState({ workspaceId = 'universe-3d', ownerId = 'local-owner', scope = 'default' } = {}) {
    return request(`/api/view-state?${new URLSearchParams({ workspace_id: workspaceId, owner_id: ownerId, scope })}`);
  },

  async saveViewState({ workspaceId = 'universe-3d', ownerId = 'local-owner', scope = 'default', expectedRevision = 0, state }) {
    return request('/api/view-state', {
      method: 'PUT',
      body: JSON.stringify({
        owner_id: ownerId,
        workspace_id: workspaceId,
        scope,
        expected_revision: expectedRevision,
        state,
      }),
    });
  },

  async resetViewState({ workspaceId = 'universe-3d', ownerId = 'local-owner', scope = 'default', expectedRevision = 0 } = {}) {
    return request('/api/view-state/reset', {
      method: 'POST',
      body: JSON.stringify({
        owner_id: ownerId,
        workspace_id: workspaceId,
        scope,
        expected_revision: expectedRevision,
        state: {},
      }),
    });
  },

  // --- Single-Use Write Approvals (§55) ---
  async fetchWriteApprovals({ scope = 'default', principal = null } = {}) {
    const query = new URLSearchParams({ scope });
    if (principal) query.set('principal', principal);
    return request(`/api/approvals?${query}`);
  },

  async issueWriteApproval({ principal, scope = 'default', action, targetId, targetRevision, payloadHash, reason, expiresAt }) {
    return request('/api/approvals', {
      method: 'POST',
      body: JSON.stringify({
        principal,
        scope,
        action,
        target_id: targetId,
        target_revision: targetRevision,
        payload_hash: payloadHash,
        reason,
        expires_at: expiresAt,
      }),
    });
  },

  async revokeWriteApproval(ticketId, reason) {
    return request(`/api/approvals/${encodeURIComponent(ticketId)}/revoke`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },

  // --- Agent Profiles & Skill Bundle Download (§91) ---
  async fetchAgentProfiles() {
    return request('/api/agent-profiles');
  },

  async createAgentProfile({ clientKind, clientVersion = '1.0', deviceId = 'local' }) {
    return request('/api/agent-profiles', {
      method: 'POST',
      body: JSON.stringify({ client_kind: clientKind, client_version: clientVersion, device_id: deviceId }),
    });
  },

  async planAgentProfileClient(profileId, before = null) {
    return request(`/api/agent-profiles/${encodeURIComponent(profileId)}/client-plan`, {
      method: 'POST',
      body: JSON.stringify({ before }),
    });
  },

  async exportAgentProfileMcp(profileId) {
    return request(`/api/agent-profiles/${encodeURIComponent(profileId)}/export`);
  },
};



