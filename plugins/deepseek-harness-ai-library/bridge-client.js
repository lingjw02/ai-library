import { spawn } from 'node:child_process';

/** Persistent, bounded local bridge transport with auto-restart. Credentials are not inherited. */
export class BridgeClient {
  constructor(config) {
    this.config = config;
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;
    this.stopped = false;
    this.buffer = Buffer.alloc(0);
    this.recentErrors = [];
    this.restartAttempts = 0;
    this.reconnectTimer = null;

    this.allowedEnv = new Set(['SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
      'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'PROGRAMFILES', 'PROGRAMFILES(X86)']);

    this._spawnChild();
  }

  _spawnChild() {
    if (this.stopped) return;
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => this.allowedEnv.has(key.toUpperCase())));
    env.PYTHONUTF8 = '1';

    this.child = spawn(this.config.pythonExecutable, [
      this.config.bridgeScript,
      '--data-dir', this.config.dataDir,
      '--scope', this.config.scope,
      '--principal', this.config.principal,
      '--max-context-chars', String(this.config.maxContextChars),
      '--max-items', String(this.config.maxItems),
      '--excerpt-chars', String(this.config.excerptChars),
      ...(this.config.allowConfirmedSync ? ['--allow-confirmed-sync'] : [])
    ], { windowsHide: true, env, stdio: ['pipe', 'pipe', 'pipe'] });

    this.closed = false;
    this.buffer = Buffer.alloc(0);

    this.done = new Promise((resolve) => {
      this.child.once('close', (code, signal) => {
        this.closed = true;
        this.failAll(new Error(`AI Library bridge exited (code ${code}, signal ${signal ?? 'none'})`));
        if (!this.stopped) {
          this._scheduleRestart();
        }
        resolve();
      });
    });

    this.child.on('error', (error) => this.failAll(error));
    this.child.stdin.on('error', (error) => this.failAll(error));

    // Bounded stderr diagnostics cache (max 10 entries, safe summaries)
    this.child.stderr.on('data', (chunk) => {
      const msg = chunk.toString('utf8').trim();
      if (msg) {
        this.recentErrors.push({ time: Date.now(), msg: msg.slice(0, 300) });
        if (this.recentErrors.length > 10) this.recentErrors.shift();
      }
    });

    this.child.stdout.on('data', (chunk) => {
      this.buffer = Buffer.concat([this.buffer, chunk]);
      let newline;
      while ((newline = this.buffer.indexOf(10)) >= 0) {
        if (newline + 1 > this.config.maxReplyBytes) {
          this.failAll(new Error('AI Library bridge response exceeds maxReplyBytes'));
          this.child.kill();
          return;
        }
        const line = this.buffer.subarray(0, newline).toString('utf8');
        this.buffer = this.buffer.subarray(newline + 1);
        try {
          const frame = JSON.parse(line);
          if (typeof frame.request_id !== 'string' || typeof frame.ok !== 'boolean') throw new Error('Invalid bridge response');
          const request = this.pending.get(frame.request_id);
          if (!request) continue;
          if (frame.ok) {
            this.restartAttempts = 0; // Reset backoff on successful roundtrip
            request.finish(null, frame.result);
          } else {
            request.finish(new Error(`AI Library ${frame.error?.code ?? 'error'}: ${frame.error?.message ?? 'read failed'}`));
          }
        } catch (error) {
          this.failAll(error);
          this.child.kill();
          return;
        }
      }
      if (this.buffer.length > this.config.maxReplyBytes) {
        this.failAll(new Error('AI Library bridge response exceeds maxReplyBytes'));
        this.child.kill();
      }
    });
  }

  _scheduleRestart() {
    if (this.stopped || this.reconnectTimer) return;
    this.restartAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this.restartAttempts - 1), 30000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.stopped) {
        this._spawnChild();
      }
    }, delay);
  }

  failAll(error) {
    for (const pending of [...this.pending.values()]) pending.finish(error);
  }

  /** Return one correlated response; cancellation does not kill other agents' reads. */
  request(values, signal) {
    if (this.stopped && values?.operation !== 'shutdown') return Promise.reject(new Error('AI Library bridge is closed'));
    if (this.closed || !this.child || this.child.exitCode !== null) {
      if (this.stopped) return Promise.reject(new Error('AI Library bridge is closed'));
      if (!this.reconnectTimer) this._scheduleRestart();
      return Promise.reject(new Error('AI Library bridge is reconnecting'));
    }
    if (signal?.aborted) return Promise.reject(signal.reason ?? new Error('Request aborted'));
    if (this.pending.size >= 32) return Promise.reject(new Error('AI Library bridge has too many pending requests'));
    const request_id = String(++this.sequence);
    const wire = JSON.stringify({ ...values, request_id }) + '\n';
    if (Buffer.byteLength(wire) > 32768) return Promise.reject(new Error('Bridge request exceeds 32768 bytes'));
    return new Promise((resolve, reject) => {
      const abort = () => finish(signal.reason ?? new Error('Request aborted'));
      const timer = setTimeout(() => finish(new Error('AI Library bridge request timed out')), this.config.timeoutMs);
      const finish = (error, value) => {
        if (!this.pending.delete(request_id)) return;
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        if (error) reject(error); else resolve(value);
      };
      this.pending.set(request_id, { finish });
      signal?.addEventListener('abort', abort, { once: true });
      this.child.stdin.write(wire, (error) => { if (error) finish(error); });
    });
  }

  /** Request graceful cleanup, then await child exit; failed cleanup remains visible. */
  close() {
    if (this.closePromise) return this.closePromise;
    this.closePromise = this.stop();
    return this.closePromise;
  }

  async stop() {
    this.stopped = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.closed || !this.child || this.child.exitCode !== null) {
      this.closed = true;
      return;
    }
    let timeout;
    let graceful = false;
    try {
      await Promise.race([
        this.request({ operation: 'shutdown' }),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('Bridge shutdown timeout')), 5000); }),
      ]);
      graceful = true;
    } catch {
      // Graceful shutdown attempt failed or timed out; force kill fallback below
    } finally {
      clearTimeout(timeout);
      try { this.child.stdin.end(); } catch {}
      if (!graceful) {
        try { this.child.kill(); } catch {}
      }
      const killTimer = setTimeout(() => {
        try { this.child.kill(); } catch {}
      }, 5000);
      await this.done;
      clearTimeout(killTimer);
      this.closed = true;
    }
  }
}
