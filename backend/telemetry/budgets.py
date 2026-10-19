"""Durable local token reservations; reported usage is never a verified invoice."""
from datetime import datetime, timedelta, timezone
import uuid
from backend.auth.policy import current_access
from backend.lineage.manager import guard, stable
from backend.schema.models import utc_now


class BudgetLedger:
    CATEGORIES = {'read', 'write', 'distill', 'overhead'}

    def __init__(self, storage, default_quota=100_000):
        self.storage, self.default_quota = storage, default_quota
        self._tokens(default_quota)
        with storage.session() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS token_budget_policies (
                    scope TEXT PRIMARY KEY, daily_quota INTEGER NOT NULL, revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS token_reservations (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, day TEXT NOT NULL, request_key TEXT NOT NULL,
                    category TEXT NOT NULL, reserved INTEGER NOT NULL, used INTEGER,
                    state TEXT NOT NULL, counting_mode TEXT, cached_tokens INTEGER,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    settled_at TEXT, UNIQUE(scope,day,request_key));
                CREATE INDEX IF NOT EXISTS token_reservation_budget ON token_reservations(scope, day, state);
                CREATE TABLE IF NOT EXISTS token_policy_history (
                    scope TEXT NOT NULL, revision INTEGER NOT NULL, daily_quota INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(scope,revision));
            ''')

    @staticmethod
    def _tokens(value):
        if type(value) is not int or not 0 <= value <= 1_000_000_000:
            raise ValueError('Token count must be a bounded nonnegative integer')

    @staticmethod
    def _day():
        return utc_now().astimezone(timezone(timedelta(hours=8))).date().isoformat()

    def _status(self, conn, scope, day):
        policy = conn.execute('SELECT * FROM token_budget_policies WHERE scope=?', (scope,)).fetchone()
        rows = conn.execute('SELECT * FROM token_reservations WHERE scope=? AND day=?', (scope, day)).fetchall()
        now = utc_now()
        quota = policy['daily_quota'] if policy else self.default_quota
        used = sum(r['used'] for r in rows if r['state'] == 'SETTLED')
        reserved = sum(r['reserved'] for r in rows if r['state'] == 'RESERVED' and datetime.fromisoformat(r['expires_at']) > now)
        modes = {mode: sum(r['used'] for r in rows if r['state'] == 'SETTLED' and r['counting_mode'] == mode) for mode in ('estimated', 'reported')}
        return {'scope': scope, 'day': day, 'timezone': 'Asia/Kuala_Lumpur', 'quota': quota,
            'policy_revision': policy['revision'] if policy else 0, 'used': used, 'reserved': reserved,
            'remaining': max(0, quota - used - reserved), 'overdrawn': max(0, used - quota),
            'tokens_by_counting_mode': modes,
            'reported_cached_tokens': sum(r['cached_tokens'] or 0 for r in rows if r['state'] == 'SETTLED' and r['counting_mode'] == 'reported'),
            'provider_verification': 'external_report_not_billing_verification',
            'enforcement': 'preflight_reservation; external_runtime_must_obey_reservation'}

    def status(self, *, scope='default', day=None):
        guard(scope)
        if day is not None:
            try:
                if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise ValueError('Exact ISO budget day required') from exc
        with self.storage.session() as conn:
            guard(scope, conn)
            return self._status(conn, scope, day or self._day())

    def configure(self, *, scope, daily_quota, expected_revision):
        guard(scope, write=True)
        if current_access.get() is not None:
            raise PermissionError('Agent cannot raise or change the budget policy')
        self._tokens(daily_quota)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Nonnegative expected_revision required')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT revision FROM token_budget_policies WHERE scope=?', (scope,)).fetchone()
            if (current['revision'] if current else 0) != expected_revision:
                raise ValueError('Budget policy revision conflict')
            revision, now = expected_revision + 1, utc_now().isoformat()
            conn.execute('INSERT INTO token_budget_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET daily_quota=excluded.daily_quota,revision=excluded.revision,updated_at=excluded.updated_at', (scope, daily_quota, revision, now))
            conn.execute('INSERT INTO token_policy_history VALUES(?,?,?,?)', (scope, revision, daily_quota, now))
            conn.execute('COMMIT')
            return {'scope': scope, 'daily_quota': daily_quota, 'revision': revision}

    def reserve(self, request_key, tokens, *, scope='default', category='read', ttl_seconds=300):
        guard(scope, write=True)
        self._tokens(tokens)
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 200:
            raise ValueError('Bounded idempotency key required')
        if category not in self.CATEGORIES or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError('Invalid reservation category or lifetime')
        day, now = self._day(), utc_now()
        context = current_access.get()
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            guard(scope, conn, write=True)
            old = conn.execute('SELECT * FROM token_reservations WHERE scope=? AND day=? AND request_key=?', (scope, day, request_key)).fetchone()
            if old:
                if old['reserved'] != tokens or old['category'] != category or (context and old['actor'] != context.principal):
                    raise ValueError('Idempotency key bound to different reservation')
                conn.execute('COMMIT')
                return dict(old) | {'expired': old['state'] == 'RESERVED' and datetime.fromisoformat(old['expires_at']) <= now}
            if self._status(conn, scope, day)['remaining'] < tokens:
                raise ValueError('Token quota insufficient; defer or explicitly change policy')
            rid = 'budget-' + uuid.uuid4().hex
            conn.execute('INSERT INTO token_reservations VALUES(?,?,?,?,?,?,NULL,?,NULL,NULL,?,?,?,NULL)',
                (rid, scope, day, request_key, category, tokens, 'RESERVED', context.principal if context else 'local-owner', now.isoformat(), (now + timedelta(seconds=ttl_seconds)).isoformat()))
            result = dict(conn.execute('SELECT * FROM token_reservations WHERE id=?', (rid,)).fetchone()) | {'expired': False}
            conn.execute('COMMIT')
            return result

    @staticmethod
    def _get(conn, reservation_id, scope):
        row = conn.execute('SELECT * FROM token_reservations WHERE id=? AND scope=?', (reservation_id, scope)).fetchone()
        context = current_access.get()
        if not row or (context and row['actor'] != context.principal):
            raise LookupError('Reservation unavailable to caller')
        return row

    def settle(self, reservation_id, *, scope, tokens, counting_mode, cached_tokens=None):
        guard(scope, write=True)
        self._tokens(tokens)
        if counting_mode not in ('estimated', 'reported'):
            raise ValueError('Counting mode must be estimated or reported')
        if cached_tokens is not None:
            self._tokens(cached_tokens)
            if counting_mode != 'reported' or cached_tokens > tokens:
                raise ValueError('Provider cached tokens require bounded reported usage')
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            guard(scope, conn, write=True)
            row = self._get(conn, reservation_id, scope)
            if row['state'] == 'SETTLED':
                if (row['used'], row['counting_mode'], row['cached_tokens']) != (tokens, counting_mode, cached_tokens):
                    raise ValueError('Settlement replay conflicts with recorded measurement')
            elif row['state'] != 'RESERVED':
                raise ValueError('Cancelled reservation cannot be settled')
            else:
                # A real overrun already happened: record it truthfully rather than
                # rejecting the report and erasing consumption from the ledger.
                conn.execute("UPDATE token_reservations SET state='SETTLED',used=?,counting_mode=?,cached_tokens=?,settled_at=? WHERE id=?",
                    (tokens, counting_mode, cached_tokens, utc_now().isoformat(), reservation_id))
            result = dict(conn.execute('SELECT * FROM token_reservations WHERE id=?', (reservation_id,)).fetchone())
            conn.execute('COMMIT')
            return result

    def cancel(self, reservation_id, *, scope):
        guard(scope, write=True)
        with self.storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            guard(scope, conn, write=True)
            row = self._get(conn, reservation_id, scope)
            if row['state'] == 'SETTLED':
                raise ValueError('Settled usage cannot be erased by cancellation')
            conn.execute("UPDATE token_reservations SET state='CANCELLED' WHERE id=?", (reservation_id,))
            conn.execute('COMMIT')
            return {'id': reservation_id, 'state': 'CANCELLED'}
