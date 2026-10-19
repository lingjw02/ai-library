"""Tests for enhanced MCP tools: compact rules, candidate deduplication, conflict detection, quarantine health."""
import pytest
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.notes.notes_manager import NotesManager
from backend.storage.raw.raw_store import RawSourceStore
from backend.candidates.candidate_bridge import CandidateBridge, normalize_claim_text
from backend.conflicts.conflict_manager import ConflictManager
from backend.quarantine.quarantine_manager import QuarantineManager
from backend.maintenance.backlog import MemoryBacklog
from backend.maintenance.logsheet import MemoryLogsheet
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.adapters.mcp.knowledge_os_mcp import KnowledgeOsMcpServer
from backend.schema.models import NodeType, MemoryAuthority


@pytest.fixture
def mcp_service(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'knowledge_os.db'))
    notes = NotesManager(storage, RawSourceStore(str(tmp_path / 'raw_sources')))
    bridge = CandidateBridge(storage)
    backlog = MemoryBacklog(storage)
    logsheet = MemoryLogsheet(storage)
    coordinator = RetrievalCoordinator(storage, notes)
    return KnowledgeOsMcpServer(
        storage, notes, bridge, backlog, logsheet, coordinator,
        scope='default', read_only=False, principal='test-agent'
    )


def test_normalize_claim_text():
    raw1 = "  The database   uses SQLite WAL   mode.  "
    raw2 = "the database uses sqlite wal mode."
    assert normalize_claim_text(raw1) == normalize_claim_text(raw2)
    assert normalize_claim_text('“Quoted”') == normalize_claim_text('"Quoted"')


def test_candidate_claim_deduplication(mcp_service):
    # 1. Create a note
    note = mcp_service.execute_tool('create_user_note', {
        'title': 'System Architecture',
        'content': 'SQLite WAL mode is enabled for concurrent reads.'
    })
    note_id = note['structured_content']['id']
    rev = note['structured_content']['revision']
    chash = note['structured_content']['content_hash']

    # 2. Submit first claim
    res1 = mcp_service.execute_tool('submit_candidate_claim', {
        'source_id': note_id,
        'source_revision': rev,
        'source_content_hash': chash,
        'claim': 'SQLite WAL mode is enabled for concurrent reads.'
    })
    assert not res1.get('isError')
    cand_id_1 = res1['structured_content']['id']

    # 3. Submit identical claim with varied whitespace and casing
    res2 = mcp_service.execute_tool('submit_candidate_claim', {
        'source_id': note_id,
        'source_revision': rev,
        'source_content_hash': chash,
        'claim': '  sqlite  wal mode is enabled   for concurrent reads.  '
    })
    assert not res2.get('isError')
    cand_id_2 = res2['structured_content']['id']
    # Must be deduplicated to the same candidate ID
    assert cand_id_1 == cand_id_2

    # Verify only 1 candidate in candidate list
    candidates = mcp_service.execute_tool('list_candidates', {})['structured_content']
    assert len(candidates) == 1


def test_compact_usage_rules(mcp_service):
    # Standard full rules
    full = mcp_service.execute_tool('get_usage_rules', {})['structured_content']
    assert 'AI Library 使用规则 v' in full['instructions']

    # Compact summary rules
    compact = mcp_service.execute_tool('get_usage_rules', {'compact': True})['structured_content']
    assert 'AI Library 治理规则摘要 v' in compact['instructions']
    assert len(compact['instructions']) < len(full['instructions'])


def test_quarantine_audit_health(mcp_service):
    audit = mcp_service.execute_tool('audit_quarantine', {})['structured_content']
    assert audit['scope'] == 'default'
    assert audit['total'] == 0
    assert audit['healthy'] is True


def test_detect_candidate_conflicts(mcp_service):
    # For a fresh candidate, there are no conflicts
    res = mcp_service.execute_tool('detect_candidate_conflicts', {'candidate_id': 'cand-dummy'})
    assert not res.get('isError')
    assert res['structured_content'] == {'items': []}

