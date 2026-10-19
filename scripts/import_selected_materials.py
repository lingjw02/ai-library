"""Batch import selected project materials into AI Library Canonical Store and Notes Plane.

Covers:
1. work/*.md (System architecture, implementation plans, and reports)
2. Root governance and system reports (DECISIONS.md, PRODUCT.md, PLAN_PHASES.md, verification reports)
3. Darwin Skill 2.0 reference specs (SKILL.md, skilllens-evidence.md, runtime-neutrality.md)

Ensures idempotent, content-addressed ingestion with raw blob manifests and canonical Document nodes.
"""
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager
from backend.schema.models import (
    GraphNode,
    NodeType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
    Provenance,
    utc_now,
)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def collect_target_files():
    targets = []
    
    # 1. work/*.md
    work_dir = ROOT / 'work'
    if work_dir.is_dir():
        for f in sorted(work_dir.glob('*.md')):
            targets.append((f, f"【Work 规划与报告】{f.name}", "work-document"))
            
    # 2. Root system documents
    root_docs = [
        'AI-Library-系统验证报告-20261013.md',
        'AI-Library-维修报告-20261013.md',
        'DECISIONS.md',
        'PRODUCT.md',
        'PLAN_PHASES.md',
    ]
    for name in root_docs:
        f = ROOT / name
        if f.is_file():
            targets.append((f, f"【核心系统规范】{name}", "root-governance"))
            
    # 3. Darwin Skill references
    darwin_dir = ROOT / '.agents' / 'skills' / 'darwin-skill'
    darwin_files = [
        darwin_dir / 'SKILL.md',
        darwin_dir / 'references' / 'skilllens-evidence.md',
        darwin_dir / 'references' / 'runtime-neutrality.md',
    ]
    for f in darwin_files:
        if f.is_file():
            targets.append((f, f"【达尔文规范】{f.parent.name}/{f.name}", "darwin-spec"))
            
    return targets


def run_import():
    data_dir = ROOT / 'data'
    db_path = str(data_dir / 'knowledge_os.db')
    raw_dir = str(data_dir / 'raw_sources')
    
    storage = CanonicalStorage(db_path)
    raw_store = RawSourceStore(raw_dir)
    notes_mgr = NotesManager(storage, raw_store)
    
    targets = collect_target_files()
    print(f"Total candidate files to inspect: {len(targets)}")
    
    # Pre-fetch existing notes by content_hash
    with storage.session() as conn:
        rows = conn.execute("SELECT id, revision, content_hash, title FROM notes WHERE is_current = 1").fetchall()
        existing_notes_by_hash = {r['content_hash']: r for r in rows}
        
        doc_rows = conn.execute("SELECT id, title FROM nodes WHERE node_type = 'Document'").fetchall()
        existing_doc_ids = {r['id'] for r in doc_rows}
        
    imported_notes = 0
    reused_notes = 0
    created_docs = 0
    reused_docs = 0
    
    for file_path, title, category in targets:
        try:
            content = file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            content = file_path.read_text(encoding='utf-8-sig', errors='replace')
            
        if not content.strip():
            continue
            
        content_bytes = content.encode('utf-8')
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        
        # 1. Notes Plane
        if content_hash in existing_notes_by_hash:
            note_row = existing_notes_by_hash[content_hash]
            note_id = note_row['id']
            note_rev = note_row['revision']
            reused_notes += 1
        else:
            note = notes_mgr.create_note(
                title=title,
                content=content,
                scope="default",
                author="user-import"
            )
            note_id = note.id
            note_rev = note.revision
            existing_notes_by_hash[content_hash] = {
                'id': note_id,
                'revision': note_rev,
                'content_hash': content_hash,
                'title': title
            }
            imported_notes += 1
            
        # 2. Canonical Document Node in Graph
        doc_id = f"mem-doc-{content_hash[:10]}"
        if doc_id in existing_doc_ids:
            reused_docs += 1
        else:
            # Summary excerpt (first 300 chars)
            excerpt = content[:400].replace('\r', '').strip()
            summary_text = f"{title}（大小 {len(content_bytes)} 字节）。\n\n摘要导读：\n{excerpt}"
            
            doc_node = GraphNode(
                id=doc_id,
                node_type=NodeType.DOCUMENT,
                schema_version=1,
                scope="default",
                title=title,
                content=summary_text,
                maturity=MemoryMaturity.L2_STRUCTURED,
                depth=MemoryDepth.D3_FACT_RELATION,
                authority=MemoryAuthority.EXPLICIT,
                confidence=1.0,
                importance=0.8,
                version=1,
                is_active=True,
                core_props={"filename": file_path.name, "category": category, "size_bytes": len(content_bytes)},
                provenance=Provenance(
                    source_id=note_id,
                    source_revision=note_rev,
                    content_hash=content_hash,
                    extractor_version="batch-import-v1",
                    author_or_actor="user-import"
                )
            )
            storage.upsert_node(doc_node)
            existing_doc_ids.add(doc_id)
            created_docs += 1
            
    print("=" * 50)
    print("Batch Import Complete:")
    print(f"  Notes Created : {imported_notes}")
    print(f"  Notes Reused  : {reused_notes}")
    print(f"  Docs Created  : {created_docs}")
    print(f"  Docs Reused   : {reused_docs}")
    print(f"  Total Active  : {len(targets)}")
    print("=" * 50)


if __name__ == '__main__':
    run_import()
