import { API } from './api.js';

const terminal = new Set(['RESOLVED', 'SUPERSEDED', 'IGNORED']);
const labels = { OPEN: 'Open', UNDER_REVIEW: 'Under review', RESOLVED: 'Resolved',
  SUPERSEDED: 'Replaced by another report', IGNORED: 'Dismissed with evidence' };

function element(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

export class ConflictReview {
  constructor(container, { onNavigateToNote }) {
    this.container = container;
    this.onNavigateToNote = onNavigateToNote;
    this.generation = 0;
  }

  clear() {
    this.generation++;
    this.container.replaceChildren();
  }

  async show(node) {
    this.clear();
    const generation = this.generation;
    const current = () => generation === this.generation;
    this.container.append(element('div', 'Loading conflict reports…', 'conflict-feedback'));
    try {
      if (node.node_type === 'Conflict') {
        const report = await API.fetchConflict(node.id, node.scope);
        if (current()) this.renderReview(report);
        return;
      }
      const list = element('div');
      const heading = element('h3', 'Conflict reports', 'detail-section-title');
      const load = async offset => {
        const page = await API.fetchConflicts({ scope: node.scope, nodeId: node.id, offset });
        if (!current()) return;
        if (offset === 0) this.container.replaceChildren(heading, list);
        if (!page.items.length && offset === 0) list.append(element('p', 'No recorded conflict reports.'));
        for (const report of page.items) {
          const button = element('button', `${labels[report.core_props.status] || 'Open'} · ${report.title}`, 'btn-secondary conflict-report');
          button.dataset.conflictOpen = report.id;
          button.addEventListener('click', async () => {
            button.disabled = true;
            try {
              const fresh = await API.fetchConflict(report.id, node.scope);
              if (current()) this.renderReview(fresh);
            } catch (error) {
              if (current()) list.append(element('p', error.message, 'conflict-feedback'));
            } finally { button.disabled = false; }
          });
          list.append(button);
        }
        if (page.has_more) {
          const more = element('button', 'Load more reports', 'btn-secondary');
          more.addEventListener('click', async () => {
            more.disabled = true;
            try { await load(page.next_offset); more.remove(); }
            catch (error) { if (current()) list.append(element('p', error.message)); more.disabled = false; }
          });
          list.append(more);
        }
      };
      await load(0);
    } catch (error) {
      if (current()) this.container.replaceChildren(element('p', `Conflict reports unavailable: ${error.message}`));
    }
  }

  renderReview(report, savedMessage = '') {
    // All remote text is rendered as text, never interpreted as markup.
    this.clear();
    const generation = this.generation;
    const current = () => generation === this.generation;
    const form = element('form', undefined, 'conflict-review');
    const heading = element('h3', 'Review conflict', 'detail-section-title');
    const description = element('p', report.content);
    const boundary = element('p', 'This report is unverified. Recording a review preserves the existing claims and decisions.', 'conflict-feedback');
    const feedback = element('p', savedMessage, 'conflict-feedback');
    feedback.setAttribute('role', 'status');
    const participants = element('div', undefined, 'conflict-participants');
    participants.append(element('h4', 'Reported participants'));
    for (const participant of report.source_props.participants || []) {
      const detail = element('p', participant.type === 'memory'
        ? `Memory: ${participant.id} · revision ${participant.revision}`
        : `Candidate: ${participant.id} · source revision ${participant.source_revision}`);
      const source = participant.type === 'memory' ? participant.provenance : participant;
      if (source?.source_id) {
        const open = element('button', 'Read original source', 'btn-secondary');
        open.type = 'button';
        open.addEventListener('click', () => this.onNavigateToNote(source.source_id, source.source_revision));
        detail.append(open);
      }
      participants.append(detail);
    }
    const field = (title, tag, id) => {
      const label = element('label', title);
      const input = element(tag);
      input.id = id;
      label.append(input);
      form.append(label);
      return input;
    };
    const status = field('Review status', 'select', 'conflict-status');
    for (const [value, title] of Object.entries(labels)) status.add(new Option(title, value));
    status.value = report.core_props.status || 'OPEN';
    const reason = field('Reason for this review', 'textarea', 'conflict-reason');
    reason.required = true;
    reason.maxLength = 8000;
    const method = field('What evidence did you check?', 'textarea', 'conflict-method');
    method.maxLength = 8000;
    const search = field('Find a supporting note', 'input', 'conflict-evidence-search');
    search.type = 'search';
    search.maxLength = 2000;
    const evidence = field('Supporting note and saved revision', 'select', 'conflict-evidence');
    const sources = new Map();
    const sourceStatus = element('p', 'Loading notes…', 'conflict-feedback');
    form.append(sourceStatus);
    let sourceRequest = 0;
    const loadSources = async () => {
      const request = ++sourceRequest;
      const query = search.value.trim();
      try {
        const result = query ? await API.searchDocuments(query, 'NOTES', report.scope) : null;
        const notes = result ? result.items : await API.fetchNotes(report.scope, 100);
        if (!current() || request !== sourceRequest) return;
        const selected = evidence.value;
        const retained = sources.get(selected);
        sources.clear();
        evidence.replaceChildren(new Option('Choose a source note', ''));
        if (retained && !notes.some(note => note.id === retained.id)) notes.unshift(retained);
        for (const note of notes) {
          sources.set(note.id, note);
          evidence.add(new Option(`${note.title} · revision ${note.revision}`, note.id));
        }
        evidence.value = selected;
        sourceStatus.textContent = query ? 'Matching notes. Narrow your search if a note is missing.'
          : 'Latest 100 notes. Search by title or text to find older notes.';
      } catch (error) { if (current() && request === sourceRequest) sourceStatus.textContent = error.message; }
    };
    let timer;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { if (current()) loadSources(); }, 200); });
    const openEvidence = element('button', 'Read selected source', 'btn-secondary');
    openEvidence.type = 'button';
    openEvidence.addEventListener('click', () => {
      const source = sources.get(evidence.value);
      if (source) this.onNavigateToNote(source.id, source.revision);
    });
    form.append(openEvidence);
    const replacement = field('Replacement report (only when replacing this report)', 'select', 'conflict-replacement');
    replacement.add(new Option('Choose an open report', ''));
    const moreReports = element('button', 'Load replacement reports', 'btn-secondary');
    moreReports.type = 'button';
    let offset = 0;
    moreReports.addEventListener('click', async () => {
      moreReports.disabled = true;
      try {
        const page = await API.fetchConflicts({ scope: report.scope, offset });
        if (!current()) return;
        for (const other of page.items) {
          if (other.id !== report.id && !terminal.has(other.core_props.status)) replacement.add(new Option(other.title, other.id));
        }
        offset = page.next_offset;
        moreReports.hidden = !page.has_more;
      } catch (error) { if (current()) feedback.textContent = error.message; }
      finally { moreReports.disabled = false; }
    });
    const updateRequirements = () => {
      const replacing = status.value === 'SUPERSEDED';
      replacement.parentElement.hidden = !replacing;
      moreReports.hidden = !replacing || offset === null;
    };
    status.addEventListener('change', updateRequirements);
    form.append(moreReports);
    updateRequirements();
    const save = element('button', 'Save review', 'btn-primary');
    save.id = 'conflict-save';
    save.type = 'submit';
    form.append(save, feedback);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const source = sources.get(evidence.value);
      if (terminal.has(status.value) && !source) {
        feedback.textContent = 'Choose a source note before closing this conflict.';
        return;
      }
      if (terminal.has(status.value) && !method.value.trim()) {
        feedback.textContent = 'Describe the evidence check before closing this conflict.';
        return;
      }
      if (status.value === 'SUPERSEDED' && !replacement.value) {
        feedback.textContent = 'Choose the replacement report.';
        return;
      }
      save.disabled = true;
      try {
        const updated = await API.reviewConflict(report.id, { scope: report.scope, expected_revision: report.version,
          status: status.value, reason: reason.value, method: method.value,
          evidence_refs: source ? [{ source_id: source.id, source_revision: source.revision, content_hash: source.content_hash }] : [],
          superseded_by: status.value === 'SUPERSEDED' ? replacement.value : null });
        if (current()) this.renderReview(updated, 'Review saved. Existing claims were preserved.');
      } catch (error) { if (current()) feedback.textContent = error.message; }
      finally { save.disabled = false; }
    });
    const previous = element('details');
    previous.append(element('summary', 'Last recorded review'));
    previous.append(element('p', report.core_props.resolution_reason || 'No review has been recorded.'));
    if (report.core_props.resolution_method) previous.append(element('p', report.core_props.resolution_method));
    for (const reference of report.verification_props.resolution?.evidence_refs || []) {
      const link = element('button', `Read review evidence · revision ${reference.source_revision}`, 'btn-secondary');
      link.type = 'button';
      link.addEventListener('click', () => this.onNavigateToNote(reference.source_id, reference.source_revision));
      previous.append(link);
    }
    this.container.replaceChildren(heading, description, boundary, participants, previous, form);
    loadSources();
  }
}
