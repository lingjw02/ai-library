import json
import sqlite3
from pathlib import Path

import pytest

from backend.auth.policy import AccessPolicy
from backend.candidates.candidate_bridge import CandidateBridge
from backend.capabilities.connections.agent_connections import AgentConnectionManager
from backend.capabilities.registry.capability_registry import CapabilityRegistry, SkillBundle
from backend.notes.notes_manager import NotesManager
from backend.schema.models import NodeType
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


def setup(tmp_path):
    root = tmp_path/'data'
    storage = CanonicalStorage(str(root/'knowledge_os.db'))
    notes = NotesManager(storage, RawSourceStore(str(root/'raw_sources')))
    note = notes.create_note('Original', '  原始内容\n', scope='a')
    notes.revise_note(note.id, 'Revised', 'Second revision', expected_revision=1)
    bridge = CandidateBridge(storage)
    _, candidate_id, _ = bridge.submit_candidate(note.id, 1, 'Source assertion', NodeType.FACT,
        {'content_hash': note.content_hash}, scope='a')
    memory = bridge.accept_candidate(candidate_id)
    memory.content = 'Reviewed assertion'
    storage.upsert_node(memory, expected_revision=1)
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('revoked-agent', 'a')
    grant = policy.get_grant('revoked-agent', 'a')
    policy.update_grant('revoked-agent', 'a', read=False, write=False, expected_revision=grant['revision'], actor='owner')
    registry = CapabilityRegistry(storage, str(root/'capabilities/artifacts'))
    for version in ['1', '2']:
        registry.register_skill(SkillBundle('example', 'inert fixture', 'not executable', '# Test version ' + version, version=version,
            files={'nested/resource.bin': b'\x00\x01\xff'}))
    profiles = AgentConnectionManager(storage, registry)
    profile = profiles.create_profile('codex')
    profiles.bind_skill(profile.id, 'example', '1', expected_revision=1)
    return root, storage, notes, note, memory, profile


def test_snapshot_restores_independently_with_history_audit_grants_and_pinned_artifacts(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot
    root, storage, notes, note, memory, profile = setup(tmp_path)
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    assert verify_snapshot(snapshot)['format_version'] == 1
    with storage.session() as conn:
        event_ids = [r[0] for r in conn.execute('SELECT id FROM transactional_outbox ORDER BY id')]
        log_ids = [r[0] for r in conn.execute('SELECT entry_id FROM system_memory_logsheet ORDER BY entry_id')]
    notes.revise_note(note.id, 'Later', 'Not part of snapshot', expected_revision=2)
    hidden = tmp_path/'original-preserved'
    assert root.resolve().is_relative_to(tmp_path.resolve()) and hidden.resolve().is_relative_to(tmp_path.resolve())
    root.rename(hidden)  # Prove the restored app cannot accidentally read the original path.
    restored = tmp_path/'restored'
    report = restore_snapshot(snapshot, restored)
    assert report['external_installations_verified'] is False
    fresh = CanonicalStorage(str(restored/'knowledge_os.db'))
    raw = RawSourceStore(str(restored/'raw_sources'), canonical_storage=fresh)
    assert raw.get_source(note.id, 1)['content'] == '  原始内容\n'
    assert raw.get_source(note.id, 2)['content'] == 'Second revision'
    assert Path(raw.get_source(note.id, 1)['file_path']).is_relative_to(restored)
    assert fresh.get_node_revision(memory.id, 1, scope='a').content == 'Source assertion'
    assert fresh.get_node(memory.id).content == 'Reviewed assertion'
    with fresh.session() as conn:
        assert [r[0] for r in conn.execute('SELECT id FROM transactional_outbox ORDER BY id')] == event_ids
        assert [r[0] for r in conn.execute('SELECT entry_id FROM system_memory_logsheet ORDER BY entry_id')] == log_ids
    with pytest.raises(PermissionError):
        AccessPolicy(fresh).check('revoked-agent', 'a')
    registry = CapabilityRegistry(fresh, str(restored/'capabilities/artifacts'))
    for version in ['1', '2']:
        cap = registry.get_capability('example', version)
        registry.require_usable(cap)
        assert Path(cap.manifest['artifact_path']).is_relative_to(restored)
    assert AgentConnectionManager(fresh, registry).get_profile(profile.id).skill_bindings[0].pinned_version == '1'
    assert verify_snapshot(snapshot)['format_version'] == 1  # Restore never edits the snapshot.


def test_corrupt_or_traversing_snapshot_never_publishes_a_restore(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    root, *_ = setup(tmp_path)
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    blob = next((snapshot/'raw_sources').glob('*.raw'))
    original = blob.read_bytes()
    blob.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        restore_snapshot(snapshot, tmp_path/'restored')
    assert not (tmp_path/'restored').exists()
    blob.write_bytes(original)
    manifest_path = snapshot/'snapshot.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['../outside.raw'] = next(iter(manifest['files'].values()))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore_snapshot(snapshot, tmp_path/'restored')
    assert not (tmp_path/'outside.raw').exists()


def test_existing_destinations_and_nested_snapshot_paths_are_refused(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    root, *_ = setup(tmp_path)
    with pytest.raises(ValueError):
        create_snapshot(root, root/'backup')
    destination = tmp_path/'occupied'
    destination.mkdir()
    (destination/'keep.txt').write_text('Keep')
    with pytest.raises(FileExistsError):
        create_snapshot(root, destination)
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    with pytest.raises(FileExistsError):
        restore_snapshot(snapshot, destination)
    assert (destination/'keep.txt').read_text() == 'Keep'


def test_bad_source_and_interrupted_copy_leave_no_published_snapshot(tmp_path, monkeypatch):
    import backend.storage.recovery as recovery
    root, *_ = setup(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('injected copy interruption')
    monkeypatch.setattr(recovery, '_copy_verified', fail)
    with pytest.raises(OSError, match='interruption'):
        recovery.create_snapshot(root, tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()
    assert list(tmp_path.glob('.snapshot.incomplete-*'))
    assert (root/'knowledge_os.db').exists()


def test_relocation_map_must_match_canonical_source_hashes(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    root, storage, *_ = setup(tmp_path)
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    manifest_file = snapshot/'snapshot.json'
    manifest = json.loads(manifest_file.read_text())
    raw_paths = [key for key, value in manifest['path_map'].items() if value.endswith('.raw')]
    assert len(raw_paths) == 2
    manifest['path_map'][raw_paths[0]] = manifest['path_map'][raw_paths[1]]
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='integrity|binding'):
        restore_snapshot(snapshot, tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_snapshot_keeps_one_committed_view_while_later_notes_are_written(tmp_path, monkeypatch):
    import backend.storage.recovery as recovery
    root, storage, notes, note, *_ = setup(tmp_path)
    copy = recovery._copy_verified
    edited = False
    def edit_after_database_capture(*args, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            notes.revise_note(note.id, 'Later', 'Written during file copy', expected_revision=2)
        return copy(*args, **kwargs)
    monkeypatch.setattr(recovery, '_copy_verified', edit_after_database_capture)
    snapshot = tmp_path/'snapshot'
    recovery.create_snapshot(root, snapshot)
    assert notes.get_note(note.id).revision == 3
    recovered = tmp_path/'recovered'
    recovery.restore_snapshot(snapshot, recovered)
    fresh = CanonicalStorage(str(recovered/'knowledge_os.db'))
    manager = NotesManager(fresh, RawSourceStore(str(recovered/'raw_sources')))
    assert manager.get_note(note.id).revision == 2
    assert manager.raw_store.get_source(note.id)['content'] == 'Second revision'


def test_skill_relocation_cannot_substitute_another_registered_version(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    root, storage, *_ = setup(tmp_path)
    registry = CapabilityRegistry(storage, str(root/'capabilities/artifacts'))
    first = registry.get_capability('example', '1').manifest['artifact_path']
    second = registry.get_capability('example', '2').manifest['artifact_path']
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    manifest_file = snapshot/'snapshot.json'
    manifest = json.loads(manifest_file.read_text())
    manifest['path_map'][first] = manifest['path_map'][second]
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='binding'):
        restore_snapshot(snapshot, tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_standalone_legacy_sources_restore_without_the_original_directory(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot
    root, *_ = setup(tmp_path)
    raw = RawSourceStore(str(root/'raw_sources'))
    raw.store_source('legacy', 'Old standalone source', scope='legacy')
    raw.store_source('legacy', 'Second standalone revision', scope='legacy')
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    hidden = tmp_path/'original-preserved'
    assert root.resolve().is_relative_to(tmp_path.resolve()) and hidden.resolve().is_relative_to(tmp_path.resolve())
    root.rename(hidden)
    restored = tmp_path/'restored'
    restore_snapshot(snapshot, restored)
    restored_raw = RawSourceStore(str(restored/'raw_sources'))
    assert restored_raw.get_source('legacy', 1)['content'] == 'Old standalone source'
    assert restored_raw.get_source('legacy', 2)['content'] == 'Second standalone revision'
    assert Path(restored_raw.get_source('legacy', 1)['file_path']).is_relative_to(restored)
    verify_snapshot(snapshot)


def test_scoped_agent_cannot_create_verify_or_restore_owner_snapshots(tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot
    root, storage, *_ = setup(tmp_path)
    snapshot = tmp_path/'snapshot'
    create_snapshot(root, snapshot)
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'a')
    with policy.activate('agent', 'a', write=True):
        for action, args in [(create_snapshot, (root, tmp_path/'denied')),
                             (verify_snapshot, (snapshot,)),
                             (restore_snapshot, (snapshot, tmp_path/'restored'))]:
            with pytest.raises(PermissionError, match='local owner'):
                action(*args)
    assert not (tmp_path/'denied').exists()
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf')])
def test_invalid_time_budgets_do_not_touch_files(tmp_path, budget):
    from backend.storage.recovery import create_snapshot, restore_snapshot, verify_snapshot
    for action, args in [(create_snapshot, (tmp_path/'absent', tmp_path/'destination')),
                         (verify_snapshot, (tmp_path/'absent',)),
                         (restore_snapshot, (tmp_path/'absent', tmp_path/'destination'))]:
        with pytest.raises(ValueError, match='finite positive'):
            action(*args, timeout_seconds=budget)
    assert not list(tmp_path.iterdir())


def test_outside_root_source_is_refused_without_copying_external_bytes(tmp_path):
    from backend.storage.recovery import create_snapshot
    root, storage, *_ = setup(tmp_path)
    outside = tmp_path/'external.raw'
    outside.write_text('Outside directory')
    with storage.session() as conn:
        conn.execute('UPDATE raw_manifests SET file_path = ?', (str(outside),))
    with pytest.raises(ValueError, match='escapes'):
        create_snapshot(root, tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()
    for stage in tmp_path.glob('.snapshot.incomplete-*'):
        assert not list(stage.rglob('*.raw'))
    assert outside.read_text() == 'Outside directory'
