/**
 * AI Library - Unified Reactive State Store (Zero Dependencies)
 * Centralizes UI states across Galaxy Starfield, Directory Drawer,
 * Readers (Note & Memory), and Flight Avionics.
 */

class Store {
  constructor(initialState = {}) {
    this._state = {
      activeReader: null,     // { type: 'note' | 'memory', id: string, revision?: number } | null
      viewMode: 'galaxy',     // 'galaxy' | 'directory' | 'maintenance'
      selectedTopic: null,    // string | null
      flightMode: false,      // boolean
      scope: 'default',       // current active scope
      ...initialState,
    };
    this._listeners = new Map();
  }

  get state() {
    return this._state;
  }

  get(key) {
    return this._state[key];
  }

  set(partial) {
    const changes = [];
    for (const [key, nextVal] of Object.entries(partial)) {
      const prevVal = this._state[key];
      if (prevVal !== nextVal) {
        this._state[key] = nextVal;
        changes.push([key, nextVal, prevVal]);
      }
    }
    for (const [key, nextVal, prevVal] of changes) {
      const subs = this._listeners.get(key);
      if (subs) {
        subs.forEach(fn => {
          try { fn(nextVal, prevVal, this._state); } catch (e) { console.error(`[Store] Listener error on '${key}':`, e); }
        });
      }
    }
    if (changes.length > 0) {
      const allSubs = this._listeners.get('*');
      if (allSubs) {
        allSubs.forEach(fn => {
          try { fn(this._state, changes); } catch (e) { console.error('[Store] Wildcard listener error:', e); }
        });
      }
    }
  }

  subscribe(key, listener) {
    if (!this._listeners.has(key)) {
      this._listeners.set(key, new Set());
    }
    this._listeners.get(key).add(listener);
    return () => {
      this._listeners.get(key)?.delete(listener);
    };
  }
}

export const store = new Store();
