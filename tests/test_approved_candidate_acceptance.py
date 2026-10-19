from datetime import timedelta
import pytest
from backend.auth.policy import AccessPolicy
from backend.auth.approvals import ApprovalTickets, hash_payload
from backend.candidates.candidate_bridge import CandidateBridge
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.schema.models import NodeType, utc_now


def test_agent_acceptance_requires_exact_owner_approval_and_atomic_replay(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    source = notes.create_note('Source', 'A claim', scope='a')
    bridge = CandidateBridge(storage)
    _, cid, _ = bridge.submit_candidate(source.id,1,'A claim',NodeType.FACT,{'content_hash':source.content_hash},scope='a')
    tickets = ApprovalTickets(storage)
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent','a')
    with policy.activate('agent','a',write=True):
        with pytest.raises(PermissionError):
            bridge.accept_candidate(cid,reviewer='user',actor_type='user')
    candidate = bridge.get_candidate(cid)
    ticket = tickets.issue(principal='agent',scope='a',action='accept_candidate',target_id=cid,
        target_revision=1,payload_hash=hash_payload(bridge.approval_payload(candidate)),reason='Owner reviewed this claim',
        expires_at=(utc_now()+timedelta(hours=1)).isoformat())
    with policy.activate('agent','a',write=True):
        first = bridge.accept_candidate(cid,reviewer='agent',actor_type='agent',approval_ticket=ticket['id'])
        repeated = bridge.accept_candidate(cid,reviewer='agent',actor_type='agent',approval_ticket=ticket['id'])
    assert first.id == repeated.id
    assert tickets.get(ticket['id'])['status'] == 'CONSUMED'
    assert first.maturity.value == 'L2' and first.verification_props['status'] == 'UNVERIFIED'


def test_candidate_conflict_rolls_back_ticket_consumption(tmp_path):
    storage = CanonicalStorage(str(tmp_path/'db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path/'raw')))
    source = notes.create_note('Source','A claim',scope='a')
    bridge = CandidateBridge(storage)
    _, cid, _ = bridge.submit_candidate(source.id,1,'A claim',NodeType.FACT,{},scope='a')
    tickets = ApprovalTickets(storage)
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent','a')
    ticket = tickets.issue(principal='agent',scope='a',action='accept_candidate',target_id=cid,
        target_revision=1,payload_hash=hash_payload(bridge.approval_payload(bridge.get_candidate(cid))),reason='Reviewed',
        expires_at=(utc_now()+timedelta(hours=1)).isoformat())
    with storage.session() as conn:
        conn.execute("CREATE TRIGGER reject_promotion BEFORE INSERT ON nodes BEGIN SELECT RAISE(ABORT,'promotion blocked'); END")
    import sqlite3
    with policy.activate('agent','a',write=True):
        with pytest.raises(sqlite3.IntegrityError):
            bridge.accept_candidate(cid,approval_ticket=ticket['id'])
    assert tickets.get(ticket['id'])['status'] == 'PENDING'
    assert bridge.get_candidate(cid).status.value == 'VALIDATED'
