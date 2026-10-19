import pytest
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.auth.policy import AccessPolicy


def env(tmp_path):
    from backend.telemetry.budgets import BudgetLedger
    storage = CanonicalStorage(str(tmp_path / 'db'))
    return storage, BudgetLedger(storage, default_quota=100)


def test_atomic_reservations_persist_replay_and_enforce_quota(tmp_path):
    from backend.telemetry.budgets import BudgetLedger
    storage, ledger = env(tmp_path)
    item = ledger.reserve('request', 70, scope='a', category='read')
    assert ledger.reserve('request', 70, scope='a', category='read')['id'] == item['id']
    with pytest.raises(ValueError):
        ledger.reserve('another', 31, scope='a')
    assert BudgetLedger(storage, default_quota=100).status(scope='a')['remaining'] == 30
    assert ledger.status(scope='b')['remaining'] == 100
    settled = ledger.settle(item['id'], scope='a', tokens=55, counting_mode='estimated')
    assert settled['state'] == 'SETTLED'
    assert ledger.status(scope='a')['remaining'] == 45
    assert ledger.settle(item['id'], scope='a', tokens=55, counting_mode='estimated') == settled
    with pytest.raises(ValueError):
        ledger.settle(item['id'], scope='a', tokens=54, counting_mode='estimated')


def test_reported_provider_usage_cannot_hide_overrun_or_fabricate_cache(tmp_path):
    _, ledger = env(tmp_path)
    item = ledger.reserve('request', 50, scope='a')
    with pytest.raises(ValueError):
        ledger.settle(item['id'], scope='a', tokens=-1, counting_mode='reported')
    with pytest.raises(ValueError):
        ledger.settle(item['id'], scope='a', tokens=40, counting_mode='reported', cached_tokens=41)
    ledger.settle(item['id'], scope='a', tokens=120, counting_mode='reported', cached_tokens=20)
    result = ledger.status(scope='a')
    assert result['used'] == 120 and result['overdrawn'] == 20 and result['remaining'] == 0
    assert result['reported_cached_tokens'] == 20
    assert result['provider_verification'] == 'external_report_not_billing_verification'


def test_cancellation_settings_cas_and_scoped_agent_boundary(tmp_path):
    storage, ledger = env(tmp_path)
    item = ledger.reserve('request', 99, scope='a')
    ledger.cancel(item['id'], scope='a')
    assert ledger.status(scope='a')['remaining'] == 100
    changed = ledger.configure(scope='a', daily_quota=200, expected_revision=0)
    assert changed['revision'] == 1 and ledger.status(scope='a')['quota'] == 200
    with pytest.raises(ValueError):
        ledger.configure(scope='a', daily_quota=300, expected_revision=0)
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        with pytest.raises(PermissionError):
            ledger.status(scope='b')
        with pytest.raises(PermissionError):
            ledger.configure(scope='a', daily_quota=100000, expected_revision=1)
