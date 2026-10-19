"""Reorganize AI Library collections based on updated intake rules.

Rules (Usage Rules v3):
1. Topic Matching: Match materials into existing galaxy topics based on title markers and semantic patterns.
   Leave unassigned (in 'unclassified') if no similar topic exists.
2. AI Agent Dual Membership:
   Materials carry their authoring agent identity ('author' / 'author_or_actor'),
   placing them into 'My Space' agent orbits while simultaneously belonging to galaxy topical subjects.
3. Retroactive Execution:
   Process existing unclassified notes and graph nodes, associate DeepSeek specifications into 'deepseek harness'
   topic, and normalize their author to 'mcp-agent:deepseek-harness' so that DeepSeek appears in 'My Space'.
"""
import json
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'knowledge_os.db'

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_reorganization():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("=" * 60)
    print("AI Library Collections Reorganization (Usage Rules v3)")
    print("=" * 60)

    # 1. Load active collection topics
    topic_rows = cursor.execute("SELECT id, title, parent_id FROM collection_topics").fetchall()
    print(f"Total topics in library: {len(topic_rows)}")

    # Map of normalized topic title -> topic_id
    topic_by_title = {}
    for r in topic_rows:
        topic_by_title[r['title'].strip().lower()] = r['id']

    deepseek_topic_id = topic_by_title.get('deepseek harness')
    if not deepseek_topic_id:
        print("[!] Warning: 'deepseek harness' topic not found by title.")

    # 2. Reorganize DeepSeek Harness Documents
    print("\n--- 1. Reorganizing DeepSeek Harness Specifications ---")
    cursor.execute("""
        SELECT id, title, author FROM notes
        WHERE is_current = 1 AND (
            title LIKE '%DEEPSEEK-HARNESS%' OR
            title LIKE '%DeepSeek 插件灵动岛%'
        )
    """)
    dsh_notes = cursor.fetchall()
    dsh_updated = 0
    dsh_members_added = 0
    now = now_iso()

    for n in dsh_notes:
        nid = n['id']
        title = n['title']
        author = n['author']

        # Update note author to mcp-agent:deepseek-harness if it was user-import
        if author == 'user-import':
            cursor.execute("UPDATE notes SET author = 'mcp-agent:deepseek-harness' WHERE id = ?", (nid,))
            dsh_updated += 1

        # Add to deepseek harness topic
        if deepseek_topic_id:
            exists = cursor.execute(
                "SELECT 1 FROM collection_members WHERE topic_id = ? AND object_id = ?",
                (deepseek_topic_id, nid)
            ).fetchone()
            if not exists:
                cursor.execute(
                    "INSERT INTO collection_members (topic_id, kind, object_id, added_at) VALUES (?, 'note', ?, ?)",
                    (deepseek_topic_id, nid, now)
                )
                dsh_members_added += 1

        # Check corresponding Document node
        cursor.execute("SELECT id, data_json FROM nodes WHERE is_active = 1 AND json_extract(data_json, '$.provenance.source_id') = ?", (nid,))
        doc_nodes = cursor.fetchall()
        for dn in doc_nodes:
            doc_id = dn['id']
            try:
                data = json.loads(dn['data_json'])
                if data.get('provenance', {}).get('author_or_actor') == 'user-import':
                    data['provenance']['author_or_actor'] = 'deepseek-harness'
                    cursor.execute("UPDATE nodes SET data_json = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), doc_id))
            except Exception:
                pass

            if deepseek_topic_id:
                node_exists = cursor.execute(
                    "SELECT 1 FROM collection_members WHERE topic_id = ? AND object_id = ?",
                    (deepseek_topic_id, doc_id)
                ).fetchone()
                if not node_exists:
                    cursor.execute(
                        "INSERT INTO collection_members (topic_id, kind, object_id, added_at) VALUES (?, 'memory', ?, ?)",
                        (deepseek_topic_id, doc_id, now)
                    )
                    dsh_members_added += 1

    print(f"DeepSeek Notes Author Updated : {dsh_updated}")
    print(f"DeepSeek Members Added to Topic: {dsh_members_added}")

    # 3. Match unclassified Notes to existing topics
    print("\n--- 2. Matching Unclassified Notes to Existing Topics ---")
    cursor.execute("""
        SELECT n.id, n.title, n.author
        FROM notes n
        WHERE n.is_current = 1 AND NOT EXISTS (
            SELECT 1 FROM collection_members m WHERE m.object_id = n.id AND m.kind = 'note'
        )
    """)
    unclassified_notes = cursor.fetchall()
    print(f"Initial unclassified notes count: {len(unclassified_notes)}")

    notes_matched = 0
    notes_remained_unclassified = 0

    for n in unclassified_notes:
        nid = n['id']
        title = n['title'].strip()
        lower_title = title.lower()

        target_tid = None

        # A. Marker pattern: 【...】
        marker_match = re.search(r'【([^【】]+)】', title)
        if marker_match:
            marker = marker_match.group(1).strip().lower()
            if marker in topic_by_title:
                target_tid = topic_by_title[marker]
            else:
                for tname, tid in topic_by_title.items():
                    if marker in tname or tname in marker:
                        target_tid = tid
                        break

        # B. Prefix separator: ^(.*)[｜|/—–-]
        if not target_tid:
            sep_match = re.search(r'^([^｜|/—–-]{2,40})[｜|/—–-]', title)
            if sep_match:
                prefix = sep_match.group(1).strip().lower()
                if prefix in topic_by_title:
                    target_tid = topic_by_title[prefix]
                else:
                    for tname, tid in topic_by_title.items():
                        if prefix in tname or tname in prefix:
                            target_tid = tid
                            break

        # C. Substring matching for strong topic names (>= 4 chars)
        if not target_tid:
            for tname, tid in topic_by_title.items():
                if len(tname) >= 4 and tname in lower_title:
                    target_tid = tid
                    break

        if target_tid:
            cursor.execute(
                "INSERT INTO collection_members (topic_id, kind, object_id, added_at) VALUES (?, 'note', ?, ?)",
                (target_tid, nid, now)
            )
            notes_matched += 1
        else:
            notes_remained_unclassified += 1

    print(f"Notes Matched and Associated  : {notes_matched}")
    print(f"Notes Retained in Unclassified : {notes_remained_unclassified}")

    # 4. Match unclassified Graph Nodes to existing topics
    print("\n--- 3. Matching Unclassified Graph Nodes to Existing Topics ---")
    cursor.execute("""
        SELECT nd.id, nd.title, nd.node_type
        FROM nodes nd
        WHERE nd.is_active = 1 AND NOT EXISTS (
            SELECT 1 FROM collection_members m WHERE m.object_id = nd.id AND m.kind = 'memory'
        )
    """)
    unclassified_nodes = cursor.fetchall()
    print(f"Initial unclassified nodes count: {len(unclassified_nodes)}")

    nodes_matched = 0
    nodes_remained_unclassified = 0

    for nd in unclassified_nodes:
        node_id = nd['id']
        title = (nd['title'] or '').strip()
        lower_title = title.lower()

        target_tid = None
        marker_match = re.search(r'【([^【】]+)】', title)
        if marker_match:
            marker = marker_match.group(1).strip().lower()
            if marker in topic_by_title:
                target_tid = topic_by_title[marker]
            else:
                for tname, tid in topic_by_title.items():
                    if marker in tname or tname in marker:
                        target_tid = tid
                        break

        if not target_tid:
            sep_match = re.search(r'^([^｜|/—–-]{2,40})[｜|/—–-]', title)
            if sep_match:
                prefix = sep_match.group(1).strip().lower()
                if prefix in topic_by_title:
                    target_tid = topic_by_title[prefix]

        if not target_tid:
            for tname, tid in topic_by_title.items():
                if len(tname) >= 4 and tname in lower_title:
                    target_tid = tid
                    break

        if target_tid:
            cursor.execute(
                "INSERT INTO collection_members (topic_id, kind, object_id, added_at) VALUES (?, 'memory', ?, ?)",
                (target_tid, node_id, now)
            )
            nodes_matched += 1
        else:
            nodes_remained_unclassified += 1

    print(f"Nodes Matched and Associated  : {nodes_matched}")
    print(f"Nodes Retained in Unclassified : {nodes_remained_unclassified}")

    # 5. Touch updated topics to increment revisions
    cursor.execute("""
        UPDATE collection_topics
        SET revision = revision + 1, updated_at = ?
        WHERE id IN (SELECT DISTINCT topic_id FROM collection_members WHERE added_at = ?)
    """, (now, now))

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("Reorganization Complete and Committed!")
    print("=" * 60)


if __name__ == '__main__':
    run_reorganization()
