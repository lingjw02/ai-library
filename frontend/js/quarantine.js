import { API } from './api.js';

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export class QuarantineView {
  constructor(container, editor, { openShell, closeShell, onNavigateToNote }) {
    Object.assign(this, { container, editor, openShell, closeShell, onNavigateToNote });
    this.generation = 0;
    this.editorGeneration = 0;
    this.active = false;
  }

  closeEditor() {
    this.editorGeneration++;
    this.editor.replaceChildren();
  }

  deactivate() {
    this.active = false;
    this.generation++;
    this.closeEditor();
  }

  activate() {
    if (this.active) return;
    this.active = true;
    const controls = el('div', undefined, 'quarantine-controls');
    const scopeLabel = el('label', 'Scope');
    this.scope = el('input');
    this.scope.id = 'quarantine-scope';
    this.scope.value = 'default';
    scopeLabel.append(this.scope);
    const viewLabel = el('label', 'Records');
    this.view = el('select');
    this.view.id = 'quarantine-view';
    for (const [value, label] of [['open', 'Needs repair'], ['resolved', 'Recovered'], ['legacy', 'Legacy — source unknown']]) {
      this.view.add(new Option(label, value));
    }
    viewLabel.append(this.view);
    const reload = el('button', 'Refresh records', 'btn-secondary');
    reload.addEventListener('click', () => this.load());
    this.scope.addEventListener('change', () => this.load());
    this.view.addEventListener('change', () => this.load());
    controls.append(scopeLabel, viewLabel, reload);
    this.help = el('p', undefined, 'conflict-feedback');
    this.rows = el('div');
    this.container.replaceChildren(controls, this.help, this.rows);
    this.load();
  }

  async load(offset = 0) {
    if (!this.active) return;
    if (offset === 0) {
      this.generation++;
      this.closeShell();
      this.rows.replaceChildren(el('p', 'Loading records…'));
    }
    const generation = this.generation;
    const current = () => this.active && generation === this.generation;
    const view = this.view.value;
    if (view === 'legacy' && !this.scope.disabled) {
      this.previousScope = this.scope.value;
      this.scope.value = 'Not assigned';
    } else if (view !== 'legacy' && this.scope.disabled) {
      this.scope.value = this.previousScope || 'default';
    }
    this.scope.disabled = view === 'legacy';
    const scope = this.scope.value.trim() || 'default';
    this.help.textContent = view === 'legacy'
      ? 'Legacy records have no verified scope or source revision. Inspect the original source in My Notes, verify its saved revision, then submit a new candidate. These records remain preserved; they cannot be restored automatically.'
      : 'Correcting a submission preserves its original data and returns a candidate for separate review. It does not verify or accept the claim.';
    try {
      const page = await API.fetchQuarantine({ scope, view, offset });
      if (!current()) return;
      if (offset === 0) this.rows.replaceChildren();
      if (!page.items.length && offset === 0) this.rows.append(el('p', 'No records in this scope and state.'));
      for (const record of page.items) {
        const card = el('div', undefined, 'maintenance-row-card quarantine-row');
        card.append(el('h3', record.submission?.claim || record.payload.title || record.id));
        const preview = el('details');
        preview.append(el('summary', 'Why this needs review'), el('p', record.reason));
        const open = el('button', record.resolved ? 'View recovered record' : 'Inspect and repair', 'btn-secondary');
        open.dataset.quarantineId = record.id;
        open.addEventListener('click', async () => {
          const ticket = ++this.editorGeneration;
          open.disabled = true;
          try {
            const fresh = view === 'legacy' ? record : await API.fetchQuarantineRecord(record.id, scope);
            if (current() && ticket === this.editorGeneration) this.openEditor(fresh);
          } catch (error) { if (current()) card.append(el('p', error.message)); }
          finally { open.disabled = false; }
        });
        card.append(preview, open);
        this.rows.append(card);
      }
      if (page.has_more) {
        const more = el('button', 'Load more records', 'btn-secondary');
        more.addEventListener('click', async () => {
          more.disabled = true;
          await this.load(page.next_offset);
          if (current()) more.remove();
        });
        this.rows.append(more);
      }
    } catch (error) {
      if (current()) this.rows.append(el('p', `Records unavailable: ${error.message}`));
    }
  }

  openEditor(record, message = '') {
    this.closeEditor();
    this.openShell(record);
    const ticket = this.editorGeneration;
    const current = () => this.active && ticket === this.editorGeneration;
    const feedback = el('p', message, 'conflict-feedback');
    feedback.setAttribute('role', 'status');
    this.editor.append(feedback);
    if (record.source_id && record.source_revision) {
      const source = el('button', 'Open source and candidate review', 'btn-secondary');
      source.id = 'quarantine-open-source';
      source.addEventListener('click', () => this.onNavigateToNote(record.source_id, record.source_revision));
      this.editor.append(source);
    }
    if (record.resolved) {
      this.editor.append(el('p', 'Recovered to candidate: ' + (record.result_candidate_id || 'Not recorded')));
      this.editor.append(el('p', record.resolution_notes || ''));
      return;
    }
    if (!record.scope || !record.source_revision || !record.submission) {
      this.editor.append(el('p', 'The original source revision is unknown. Verify the original source and submit a new candidate; automatic recovery is unavailable.'));
      return;
    }
    const form = el('form', undefined, 'conflict-review');
    const field = (label, tag, id, value) => {
      const wrapper = el('label', label);
      const input = el(tag);
      input.id = id;
      if (value !== undefined) input.value = value;
      wrapper.append(input);
      form.append(wrapper);
      return input;
    };
    const claim = field('Corrected claim', 'textarea', 'quarantine-claim', record.submission.claim);
    claim.required = true;
    claim.maxLength = 200000;
    const type = field('Memory type', 'input', 'quarantine-type', record.submission.suggested_node_type);
    type.required = true;
    type.maxLength = 100;
    const attributes = field('Additional attributes (JSON)', 'textarea', 'quarantine-attributes', JSON.stringify(record.submission.payload, null, 2));
    const reason = field('What did you correct?', 'textarea', 'quarantine-reason', '');
    reason.required = true;
    reason.maxLength = 8000;
    const save = el('button', 'Validate repair and create candidate', 'btn-primary');
    save.id = 'quarantine-save';
    save.type = 'submit';
    form.append(save);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      let payload;
      try {
        payload = JSON.parse(attributes.value);
        if (!payload || Array.isArray(payload) || typeof payload !== 'object') throw new Error();
      } catch (_) { feedback.textContent = 'Additional attributes must be a valid JSON object. Your draft is preserved.'; return; }
      save.disabled = true;
      try {
        const updated = await API.repairQuarantine(record.id, { scope: record.scope, expected_revision: record.revision,
          claim: claim.value, suggested_node_type: type.value, payload, reason: reason.value });
        if (current()) this.openEditor(updated, 'Repair saved to a candidate. This repair did not accept or verify it.');
      } catch (error) { if (current()) feedback.textContent = error.message; }
      finally { save.disabled = false; }
    });
    this.editor.append(form);
  }
}
