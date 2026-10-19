/** Versioned rules editor. Publishing delivers policy; it does not claim Agent compliance. */
export class UsageRulesPanel {
  constructor({ API, onToast }) {
    this.API = API;
    this.onToast = onToast;
    this.form = document.getElementById('settings-rules-form');
    this.status = document.getElementById('settings-rules-status');
    this.save = document.getElementById('settings-rules-save');
    this.refresh = document.getElementById('settings-rules-refresh');
    this.defaults = document.getElementById('settings-rules-defaults');
    this.state = null;
    this.dirty = false;
    this.busy = false;
    this.publishing = false;
    this.form.addEventListener('input', () => this.updateDirty());
    this.form.addEventListener('change', () => this.updateDirty());
    this.form.addEventListener('submit', event => { event.preventDefault(); this.publish(); });
    this.refresh.onclick = () => {
      if (!this.dirty || window.confirm('重新读取会放弃未保存的修改，是否继续？')) this.load();
    };
    this.defaults.onclick = () => {
      if (!this.state || this.busy) return;
      this.fill(Object.fromEntries(this.state.fields.map(f => [f.key, f.default])));
      this.updateDirty();
    };
  }

  async open() {
    if (!this.dirty && !this.busy) await this.load();
  }

  setBusy(value) {
    this.busy = value;
    this.form.setAttribute('aria-busy', String(value));
    this.form.querySelectorAll('input, select').forEach(control => { control.disabled = value; });
    this.refresh.disabled = value;
    this.defaults.disabled = value || !this.state;
    this.save.disabled = value || !this.dirty;
  }

  async load() {
    if (this.busy) return;
    this.setBusy(true);
    this.status.textContent = '正在读取当前规则…';
    try {
      this.state = await this.API.fetchUsageRules();
      this.render();
      this.status.textContent = `当前发布版本 v${this.state.revision}。修改后点击“保存并更新”。`;
    } catch (error) {
      this.status.textContent = `读取失败：${error.message}。可点击“重新读取”重试。`;
    } finally {
      this.setBusy(false);
    }
  }

  render() {
    const fragment = document.createDocumentFragment();
    for (const group of this.state.groups) {
      const section = document.createElement('details');
      section.className = 'usage-rule-group';
      section.open = group.id === 'reading';
      const heading = document.createElement('summary');
      heading.textContent = group.title;
      section.append(heading);
      for (const field of this.state.fields.filter(f => f.group === group.id)) {
        const row = document.createElement('div');
        row.className = 'usage-rule-field';
        const copy = document.createElement('div');
        const label = document.createElement('label');
        label.htmlFor = `usage-rule-${field.key}`;
        label.textContent = field.label;
        copy.append(label);
        const input = document.createElement(field.type === 'enum' ? 'select' : 'input');
        input.id = label.htmlFor;
        input.name = field.key;
        if (field.type === 'enum') {
          input.className = 'settings-select';
          for (const option of field.options) {
            const entry = document.createElement('option');
            entry.value = option.value;
            entry.textContent = option.label;
            input.append(entry);
          }
        } else if (field.type === 'integer') {
          input.type = 'number';
          input.className = 'settings-input';
          input.min = field.minimum;
          input.max = field.maximum;
          input.step = 1;
          input.required = true;
        } else {
          input.type = 'checkbox';
        }
        if (field.hint) {
          const hint = document.createElement('p');
          hint.id = `${input.id}-hint`;
          hint.className = 'settings-hint';
          hint.textContent = field.hint;
          input.setAttribute('aria-describedby', hint.id);
          copy.append(hint);
        }
        row.append(copy, input);
        section.append(row);
      }
      fragment.append(section);
    }
    this.form.querySelector('.usage-rule-fields').replaceChildren(fragment);
    this.fill(this.state.rules);
    this.dirty = false;
    const fixed = document.getElementById('settings-rules-fixed');
    fixed.replaceChildren(...this.state.fixed_rules.map(text => {
      const item = document.createElement('li'); item.textContent = text; return item;
    }));
    const suggestions = document.getElementById('settings-rules-suggestions');
    suggestions.replaceChildren(...this.state.suggestions.map(suggestion => {
      const entry = document.createElement('div');
      const title = document.createElement('dt'); title.textContent = suggestion.title;
      const description = document.createElement('dd'); description.textContent = suggestion.description;
      entry.append(title, description); return entry;
    }));
    this.renderDeliveries();
    document.getElementById('settings-rules-version').textContent =
      `v${this.state.revision} · ${new Date(this.state.published_at).toLocaleString()}`;
  }

  fill(rules) {
    for (const field of this.state.fields) {
      const input = this.form.elements.namedItem(field.key);
      if (field.type === 'bool') input.checked = rules[field.key];
      else input.value = rules[field.key];
    }
  }

  read() {
    return Object.fromEntries(this.state.fields.map(field => {
      const input = this.form.elements.namedItem(field.key);
      const value = field.type === 'bool' ? input.checked : field.type === 'integer' ? Number(input.value) : input.value;
      return [field.key, value];
    }));
  }

  updateDirty() {
    if (!this.state || this.busy) return;
    const values = this.read();
    this.dirty = this.state.fields.some(field => values[field.key] !== this.state.rules[field.key]);
    this.save.disabled = !this.dirty;
    this.status.textContent = this.dirty
      ? '有未保存的修改，当前 Agent 仍使用已发布版本。'
      : `当前发布版本 v${this.state.revision}，没有未保存的修改。`;
  }

  discardDraft() {
    if (this.state) this.fill(this.state.rules);
    this.dirty = false;
    this.save.disabled = true;
  }

  async publish() {
    if (!this.state || this.busy || !this.dirty || !this.form.reportValidity()) return;
    const rules = this.read();
    const revision = this.state.revision;
    this.publishing = true;
    this.setBusy(true);
    this.status.textContent = '正在保存并发布新版本…';
    try {
      this.state = await this.API.publishUsageRules(rules, revision);
      this.render();
      this.status.textContent = `已保存并发布 v${this.state.revision}。Agent 下一次调用 MCP 时会收到更新。`;
      this.onToast(`使用规则已更新至 v${this.state.revision}`, 'success');
      await this.loadHistory();
    } catch (error) {
      this.status.textContent = `更新失败：${error.message}。草稿仍保留；保存结果尚未确认，可重新读取核对已发布版本。`;
    } finally {
      this.publishing = false;
      this.setBusy(false);
    }
  }

  renderDeliveries() {
    const target = document.getElementById('settings-rules-deliveries');
    const rows = this.state.deliveries || [];
    if (!rows.length) {
      target.textContent = '暂无连接记录。已升级的 MCP 服务会在 Agent 下次调用时附带当前规则。';
      return;
    }
    target.replaceChildren(...rows.map(row => {
      const entry = document.createElement('p');
      const state = row.revision == null ? '尚未附带新规则' : row.revision === this.state.revision
        ? `响应已附带 v${row.revision}` : `上次附带 v${row.revision}，待下次调用更新`;
      entry.textContent = `${row.principal} · ${row.scope}：${state}${row.can_read ? '' : '（已撤销）'}`;
      return entry;
    }));
  }

  async loadHistory() {
    const target = document.getElementById('settings-rules-history');
    try {
      const history = await this.API.fetchUsageRuleHistory();
      target.replaceChildren(...history.items.map(item => {
        const entry = document.createElement('p');
        entry.textContent = `v${item.revision} · ${new Date(item.published_at).toLocaleString()} · ${item.actor === 'local-owner' ? '用户发布' : '问卷默认'}`;
        return entry;
      }));
    } catch (error) {
      target.textContent = `版本记录读取失败：${error.message}`;
    }
  }
}
