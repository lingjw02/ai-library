/** Durable preference derived only from paired, successful human commands. */
export const projectKey = 'aiLibraryProject';
export const projectProtocol = 'ai-library-project/1';

function parseProject(value) {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join(',') !== 'id,revision,title'
    || typeof value.id !== 'string' || !value.id || value.id.length > 200
    || typeof value.title !== 'string' || value.title.length > 200
    || !Number.isSafeInteger(value.revision) || value.revision < 1) throw new TypeError('Invalid library project preference');
  return { id: value.id, title: value.title, revision: value.revision };
}

/** The native registry uses synchronous .parse() for checkpoint and wire validation. */
export function createProjectProjection(scope) {
  return {
    key: projectKey, stateVersion: 1,
    stateSchema: { parse(value) {
      if (!value || Object.keys(value).sort().join(',') !== 'pending,project'
        || !Array.isArray(value.pending) || value.pending.length > 32
        || value.pending.some(id => typeof id !== 'string' || id.length > 200)) throw new TypeError('Invalid project projection state');
      return { project: parseProject(value.project), pending: [...value.pending] };
    } },
    init: () => ({ project: null, pending: [] }),
    apply(state, event) {
      const data = event.data;
      if (event.type === 'command/run' && data.name === 'library' && data.source?.kind === 'user') {
        try {
          const request = JSON.parse(data.args);
          if (request.operation !== 'select_project') return state;
          return { ...state, pending: [...state.pending, data.commandId].slice(-32) };
        } catch { return state; }
      }
      if (event.type !== 'command/done' || !state.pending.includes(data.commandId)) return state;
      const pending = state.pending.filter(id => id !== data.commandId);
      if (data.kind !== 'success') return { ...state, pending };
      try {
        const response = JSON.parse(data.text);
        if (response.protocol !== projectProtocol || response.operation !== 'select_project' || response.scope !== scope) return { ...state, pending };
        return { project: parseProject(response.project), pending };
      } catch { return { ...state, pending }; }
    },
    wire: { viewSchema: { parse: parseProject }, view: state => state.project },
  };
}
