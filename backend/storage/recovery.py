"""Owner-operated consistent snapshots and relocation into new directories only.

SQLite backup captures committed canonical state. Only its referenced immutable
raw files and registered artifact versions are copied; unfinished/unreferenced
blobs and external installations are not part of the snapshot. Failed staging
directories remain available for inspection and are never published as complete.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from backend.auth.policy import current_access
from backend.capabilities.registry.capability_registry import _assert_no_links, _relative_name
from backend.schema.models import utc_now


def _owner():
    if current_access.get() is not None:
        raise PermissionError('Snapshots require the local owner, not a scoped Agent connection')


def _root(path):
    path = Path(os.path.abspath(path))
    _assert_no_links(path)
    return path


def _read_json(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON key in snapshot metadata')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _deadline(deadline):
    if time.monotonic() > deadline:
        raise TimeoutError('Snapshot operation exceeded its time budget; incomplete staging is preserved')


def _budget(seconds):
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('A finite positive time budget is required')
    return time.monotonic() + seconds


def _metadata(path, deadline):
    _assert_no_links(path)
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            _deadline(deadline)
            digest.update(block)
            size += len(block)
    return {'sha256': digest.hexdigest(), 'size': size}


def _copy_verified(source, target, expected, deadline):
    _assert_no_links(source)
    _assert_no_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with source.open('rb') as reader, target.open('xb') as writer:
        while block := reader.read(1024 * 1024):
            _deadline(deadline)
            writer.write(block)
            digest.update(block)
            size += len(block)
        writer.flush()
        os.fsync(writer.fileno())
    if {'sha256': digest.hexdigest(), 'size': size} != expected:
        raise ValueError('Snapshot file integrity check failed: ' + str(source))


def _reader(path, *, immutable=True):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA trusted_schema = OFF')
    return connection


def _check_database(conn):
    if [row[0] for row in conn.execute('PRAGMA quick_check')] != ['ok'] or conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Snapshot database integrity check failed')


def _staging(source, destination):
    _owner()
    source, destination = _root(source), _root(destination)
    if destination.exists():
        raise FileExistsError('Destination already exists; restore to a new directory')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Source and destination directories must not contain one another')
    if not source.is_dir():
        raise ValueError('Source directory is unavailable')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + '.incomplete-' + uuid.uuid4().hex)
    temporary.mkdir()
    return source, destination, temporary


def _referenced_path(root, stored_path):
    # Purely lexical: verification must also work after the original root is gone.
    path = Path(stored_path)
    if not path.is_absolute():
        path = (root.parent if path.parts and path.parts[0] == root.name else root) / path
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ValueError('Referenced file escapes the data directory')
    return path, _relative_name(path.relative_to(root).as_posix())


def _collect(conn, root, deadline):
    files, paths = {}, {}

    def referenced(stored_path):
        path, relative = _referenced_path(root, stored_path)
        _assert_no_links(path)
        paths[stored_path] = relative
        return path, relative

    def add(stored_path, digest=None, size=None):
        path, relative = referenced(stored_path)
        measured = _metadata(path, deadline)
        if (digest is not None and measured['sha256'] != digest) or (size is not None and measured['size'] != size):
            raise ValueError('Referenced source integrity check failed: ' + relative)
        if relative in files and files[relative] != measured:
            raise ValueError('Referenced file changed during snapshot')
        files[relative] = measured
        return relative

    legacy = {}
    catalog = root/'raw_sources/manifests.json'
    if catalog.exists():
        legacy = _read_json(catalog)
        add(str(catalog))
        for entry in legacy.values():
            add(entry['file_path'], entry['content_hash'], entry['byte_length'])
    raw = {}
    for row in conn.execute('SELECT * FROM raw_manifests'):
        add(row['file_path'], row['content_hash'], row['byte_length'])
        raw[(row['source_id'], row['revision'])] = row['content_hash']
    for row in conn.execute('SELECT id, revision, content, content_hash FROM notes'):
        expected = raw.get((row['id'], row['revision']))
        if expected is None:
            expected = legacy.get(f"{row['id']}:rev:{row['revision']}", {}).get('content_hash')
        if expected != row['content_hash'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest() != expected:
            raise ValueError('A note revision has no matching intact raw source')

    caps = []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'capability_versions' in tables:
        caps.extend(json.loads(row[0]) for row in conn.execute('SELECT payload_json FROM capability_versions'))
    caps.extend({'kind': row['kind'], 'content_hash': row['content_hash'], 'manifest': json.loads(row['manifest_json'])}
                for row in conn.execute('SELECT kind, content_hash, manifest_json FROM capabilities'))
    for cap in caps:
        if cap['kind'] != 'SKILL':
            continue
        manifest = cap['manifest']
        folder, _ = referenced(manifest['artifact_path'])
        declared = manifest.get('files')
        if not declared:
            raise ValueError('Skill version lacks a verifiable file manifest')
        actual = set()
        for item in folder.rglob('*'):
            _assert_no_links(item)
            if item.is_file():
                actual.add(item.relative_to(folder).as_posix())
        if actual != set(declared):
            raise ValueError('Skill version file manifest does not match stored resources')
        hashes = {}
        for name, digest in declared.items():
            name = _relative_name(name)
            relative = add(str(folder/name), digest)
            hashes[name] = {'size': files[relative]['size'], 'sha256': digest}
        combined = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if combined != cap['content_hash']:
            raise ValueError('Skill version integrity check failed')
    return files, paths


def _validate_bindings(conn, manifest, legacy, deadline):
    """Check locators against canonical hashes without consulting the old root."""
    files, paths = manifest['files'], manifest['path_map']

    def bound_file(stored_path, digest, size):
        relative = paths.get(stored_path)
        if relative is None or files.get(relative) != {'sha256': digest, 'size': size}:
            raise ValueError('Snapshot source binding integrity check failed')

    raw = {}
    for key, entry in legacy.items():
        _deadline(deadline)
        if key != f"{entry['source_id']}:rev:{entry['revision']}":
            raise ValueError('Snapshot legacy source binding identity mismatch')
        bound_file(entry['file_path'], entry['content_hash'], entry['byte_length'])
        raw[(entry['source_id'], entry['revision'])] = entry['content_hash']
    for row in conn.execute('SELECT * FROM raw_manifests'):
        _deadline(deadline)
        bound_file(row['file_path'], row['content_hash'], row['byte_length'])
        key = (row['source_id'], row['revision'])
        if key in raw and raw[key] != row['content_hash']:
            raise ValueError('Snapshot canonical and legacy source bindings disagree')
        raw[key] = row['content_hash']
    for row in conn.execute('SELECT id, revision, content, content_hash FROM notes'):
        _deadline(deadline)
        expected = raw.get((row['id'], row['revision']))
        if expected != row['content_hash'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest() != expected:
            raise ValueError('Snapshot note source binding integrity check failed')

    caps = []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'capability_versions' in tables:
        caps.extend(json.loads(row[0]) for row in conn.execute('SELECT payload_json FROM capability_versions'))
    caps.extend({'kind': row['kind'], 'content_hash': row['content_hash'], 'manifest': json.loads(row['manifest_json'])}
                for row in conn.execute('SELECT kind, content_hash, manifest_json FROM capabilities'))
    for cap in caps:
        _deadline(deadline)
        if cap['kind'] != 'SKILL':
            continue
        artifact = cap['manifest']
        relative = paths.get(artifact['artifact_path'])
        declared = artifact.get('files')
        if relative is None or not isinstance(declared, dict) or not declared:
            raise ValueError('Snapshot Skill binding is incomplete')
        prefix = relative + '/'
        actual = {name[len(prefix):]: metadata for name, metadata in files.items() if name.startswith(prefix)}
        if set(actual) != set(declared):
            raise ValueError('Snapshot Skill file binding mismatch')
        for name, digest in declared.items():
            _relative_name(name)
            if actual[name]['sha256'] != digest:
                raise ValueError('Snapshot Skill binding integrity check failed')
        combined = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if combined != cap['content_hash']:
            raise ValueError('Snapshot Skill version binding integrity check failed')


def create_snapshot(data_dir, destination, *, timeout_seconds=120):
    deadline = _budget(timeout_seconds)
    source, destination, staging = _staging(data_dir, destination)
    database = source/'knowledge_os.db'
    _assert_no_links(database)
    with closing(_reader(database, immutable=False)) as reader, closing(sqlite3.connect(staging/'knowledge_os.db')) as writer:
        reader.backup(writer, pages=256, progress=lambda *_: _deadline(deadline))
    with closing(_reader(staging/'knowledge_os.db')) as conn:
        _check_database(conn)
        files, paths = _collect(conn, source, deadline)
    for relative, metadata in files.items():
        _copy_verified(source/relative, staging/relative, metadata, deadline)
    files['knowledge_os.db'] = _metadata(staging/'knowledge_os.db', deadline)
    manifest = {'format_version': 1, 'created_at': utc_now().isoformat(), 'files': files, 'path_map': paths,
                'source_root': str(source), 'external_installations_included': False}
    _write_json(staging/'snapshot.json', manifest)
    verify_snapshot(staging, timeout_seconds=max(0.001, deadline - time.monotonic()))
    if destination.exists():
        raise FileExistsError('Destination appeared during snapshot; staging is preserved')
    staging.rename(destination)
    return manifest


def verify_snapshot(snapshot_dir, *, timeout_seconds=120):
    _owner()
    deadline = _budget(timeout_seconds)
    root = _root(snapshot_dir)
    _assert_no_links(root/'snapshot.json')
    manifest = _read_json(root/'snapshot.json')
    if manifest.get('format_version') != 1 or not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('path_map'), dict):
        raise ValueError('Unsupported snapshot manifest')
    files = manifest['files']
    if 'knowledge_os.db' not in files or 'snapshot.json' in files or len(files) > 100_000:
        raise ValueError('Invalid snapshot file manifest')
    names = set()
    for name, metadata in files.items():
        _relative_name(name)
        if name.casefold() in names:
            raise ValueError('Aliased snapshot file names are forbidden')
        names.add(name.casefold())
        if _metadata(root/name, deadline) != metadata:
            raise ValueError('Snapshot file integrity check failed: ' + name)
    actual = set()
    for path in root.rglob('*'):
        _assert_no_links(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files) | {'snapshot.json'}:
        raise ValueError('Snapshot contains unlisted files or missing resources')
    original_root = manifest.get('source_root')
    if not isinstance(original_root, str) or not Path(original_root).is_absolute():
        raise ValueError('Snapshot source root is invalid')
    for stored, relative in manifest['path_map'].items():
        _relative_name(relative)
        if _referenced_path(Path(original_root), stored)[1] != relative:
            raise ValueError('Snapshot relocation binding changes the resource layout')
        if relative not in files and not any(name.startswith(relative + '/') for name in files):
            raise ValueError('Snapshot relocation references an absent resource')
    with closing(_reader(root/'knowledge_os.db')) as conn:
        _check_database(conn)
        catalog = root/'raw_sources/manifests.json'
        legacy = _read_json(catalog) if catalog.exists() else {}
        _validate_bindings(conn, manifest, legacy, deadline)
    _deadline(deadline)
    return manifest


def restore_snapshot(snapshot_dir, destination, *, timeout_seconds=120):
    _owner()
    deadline = _budget(timeout_seconds)
    manifest = verify_snapshot(snapshot_dir, timeout_seconds=timeout_seconds)
    source, destination, staging = _staging(snapshot_dir, destination)
    for relative, metadata in manifest['files'].items():
        _copy_verified(source/relative, staging/relative, metadata, deadline)
    def relocate(path):
        if path not in manifest['path_map']:
            raise ValueError('Stored location is missing from the verified snapshot path map')
        return str(destination/manifest['path_map'][path])
    with closing(sqlite3.connect(staging/'knowledge_os.db')) as conn:
        conn.execute('PRAGMA trusted_schema = OFF')
        conn.execute('PRAGMA journal_mode = DELETE')
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute('SELECT source_id, revision, file_path FROM raw_manifests').fetchall():
            conn.execute('UPDATE raw_manifests SET file_path = ? WHERE source_id = ? AND revision = ?',
                         (relocate(row[2]), row[0], row[1]))
        for row in conn.execute("SELECT id, manifest_json FROM capabilities WHERE kind = 'SKILL'").fetchall():
            value = json.loads(row[1])
            value['artifact_path'] = relocate(value['artifact_path'])
            conn.execute('UPDATE capabilities SET manifest_json = ? WHERE id = ?', (json.dumps(value), row[0]))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'capability_versions' in tables:
            for row in conn.execute('SELECT capability_id, version, payload_json FROM capability_versions').fetchall():
                cap = json.loads(row[2])
                if cap['kind'] == 'SKILL':
                    cap['manifest']['artifact_path'] = relocate(cap['manifest']['artifact_path'])
                    conn.execute('UPDATE capability_versions SET payload_json = ? WHERE capability_id = ? AND version = ?',
                                 (json.dumps(cap), row[0], row[1]))
        conn.commit()
        _check_database(conn)
    catalog = staging/'raw_sources/manifests.json'
    if catalog.exists():
        legacy = _read_json(catalog)
        for entry in legacy.values():
            entry['file_path'] = relocate(entry['file_path'])
        # Only the newly created staging copy is rewritten; snapshot bytes stay intact.
        catalog.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding='utf-8')
    report = {'snapshot': str(source), 'restored_at': utc_now().isoformat(), 'destination': str(destination),
              'snapshot_database_sha256': manifest['files']['knowledge_os.db']['sha256'],
              'relocated_paths': len(manifest['path_map']), 'external_installations_verified': False,
              'client_configuration_applied': False}
    _write_json(staging/'restore-report.json', report)
    _deadline(deadline)
    if destination.exists():
        raise FileExistsError('Destination appeared during restore; staging is preserved')
    staging.rename(destination)
    return report
