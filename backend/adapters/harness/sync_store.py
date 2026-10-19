"""Durable untrusted excerpts and per-task confirmation receipts.

Only confirm enters the existing guarded canonical managers. The sidecar is a
local trusted-host queue, never accepted Memory or original Notes by itself.
"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
import uuid

from backend.schema.models import MemoryAuthority, NodeType


MAX_REVIEW_BYTES = 900000  # Below the trusted Host's 1 MiB NDJSON reply ceiling.


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class HarnessSyncStore:
    def __init__(self, service):
        self.service = service
        self.path = service.storage.db_path.parent / '_runtime' / 'harness-sync.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS sync_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, binding TEXT NOT NULL,
                    event_id TEXT NOT NULL, event_hash TEXT NOT NULL, project_id TEXT,
                    turn_id TEXT, event_type TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(binding,event_id));
                CREATE TABLE IF NOT EXISTS sync_batches (
                    id TEXT PRIMARY KEY, binding TEXT NOT NULL, project_id TEXT NOT NULL,
                    status TEXT NOT NULL, body TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sync_event_binding ON sync_events(binding,seq);
                CREATE INDEX IF NOT EXISTS sync_batch_binding ON sync_batches(binding,project_id,status);
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA secure_delete=ON')
        try:
            with conn: yield conn
        finally: conn.close()

    def binding(self, command):
        return digest([self.service.scope, self.service.principal, command.session_key])

    def save(self, conn, body):
        conn.execute('UPDATE sync_batches SET status=?,body=?,updated_at=? WHERE id=?',
                     (body['status'], encoded(body), time.time(), body['id']))

    def event(self, command, project):
        binding = self.binding(command)
        event_body = {key:getattr(command,key) for key in
                      ('event_id','event_type','turn_id','text','text_truncated','tool_name','project_id')}
        event_digest = digest(event_body)
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT event_hash FROM sync_events WHERE binding=? AND event_id=?',
                               (binding,command.event_id)).fetchone()
            if old:
                if old['event_hash'] != event_digest: raise ValueError('Changed event replay rejected')
                return {'duplicate':True, **self._status(conn,command)}
            count = conn.execute('SELECT COUNT(*) FROM sync_events WHERE binding=?',(binding,)).fetchone()[0]
            if count >= 10000: raise ValueError('Session sync event limit reached; review or begin a new task')
            conn.execute('INSERT INTO sync_events(binding,event_id,event_hash,project_id,turn_id,event_type,body,created_at) VALUES (?,?,?,?,?,?,?,?)',
                         (binding,command.event_id,event_digest,command.project_id,command.turn_id,command.event_type,encoded(event_body),time.time()))
            if command.event_type == 'turn_completed' and project is not None:
                previous = conn.execute("SELECT MAX(seq) FROM sync_events WHERE binding=? AND project_id=? AND event_type='turn_completed' AND event_id!=?",
                                        (binding,command.project_id,command.event_id)).fetchone()[0] or 0
                rows = conn.execute("SELECT * FROM sync_events WHERE binding=? AND project_id=? AND seq>? AND turn_id IS ? AND event_type IN ('user_message','assistant_message') ORDER BY seq",
                                    (binding,command.project_id,previous,command.turn_id)).fetchall()
                messages = [json.loads(row['body']) for row in rows]
                final = command.text or next((m['text'] for m in reversed(messages) if m['event_type']=='assistant_message'), '')
                if final.strip():
                    row = conn.execute("SELECT body FROM sync_batches WHERE binding=? AND project_id=? AND status='pending' ORDER BY updated_at DESC LIMIT 1",
                                       (binding,command.project_id)).fetchone()
                    if row: batch=json.loads(row['body'])
                    else:
                        batch={'id':'batch-'+uuid.uuid4().hex,'binding':binding,'project_id':project['id'],
                               'status':'pending','items':[],'review_digest':None,'project_revision':None,
                               'selected_ids':None,'receipts':{},'result':None}
                        conn.execute('INSERT INTO sync_batches VALUES(?,?,?,?,?,?)',
                                     (batch['id'],binding,project['id'],'pending',encoded(batch),time.time()))
                    if len(batch['items'])>=50: raise ValueError('Task pending item limit reached; review this task first')
                    raw = '\n\n'.join(m['event_type']+':\n'+m['text'] for m in messages[-32:])
                    if command.text: raw += ('\n\n' if raw else '')+'assistant_message:\n'+command.text
                    raw_partial = len(raw)>24000 or len(messages)>32 or command.text_truncated or any(m['text_truncated'] for m in messages)
                    # Preserve the final response supporting the claim even when
                    # earlier user/assistant excerpts exhaust the raw-source cap.
                    raw=raw[-24000:]
                    item={'id':'draft-'+digest([binding,command.event_id])[:24], 'title':final.strip().splitlines()[0][:180],
                          'claim':final[:8000], 'source_text':raw, 'source_hash':text_hash(raw),
                          'source_partial':raw_partial,'verification':'UNVERIFIED','authority':'INFERRED',
                          'event_ids':[m['event_id'] for m in messages]+[command.event_id],
                          'event_hashes':[row['event_hash'] for row in rows]+[event_digest],
                          'turn_id':command.turn_id}
                    batch['items'].append(item)
                    batch['review_digest']=None
                    # Check the actual UTF-8 envelope, including a future digest,
                    # before committing the completion event. The trusted outbox
                    # can retry that event after the current batch is confirmed.
                    review_envelope={'batch':{'id':batch['id'],'digest':'0'*64,'project_id':batch['project_id'],
                                             'items':batch['items'],'status':'confirming','project_revision':project['revision']}}
                    if len(encoded(review_envelope).encode('utf-8'))>MAX_REVIEW_BYTES:
                        raise ValueError('Task review batch is full; review and confirm the current batch, then retry queued completion')
                    self.save(conn,batch)
            return {'duplicate':False, **self._status(conn,command)}

    def _status(self, conn, command):
        rows=conn.execute("SELECT id,body FROM sync_batches WHERE binding=? AND project_id IS ? AND status IN ('pending','confirming') ORDER BY updated_at DESC",
                          (self.binding(command),command.project_id)).fetchall()
        bodies=[json.loads(r['body']) for r in rows]
        last=conn.execute('SELECT MAX(created_at) FROM sync_events WHERE binding=? AND project_id IS ?',
                          (self.binding(command),command.project_id)).fetchone()[0]
        return {'enabled':True,'pending_count':sum(len(b['items']) for b in bodies),'last_synced_at':last,
                'batch_id':rows[0]['id'] if rows else None}

    def status(self, command):
        with self.db() as conn: return self._status(conn,command)

    def review(self, command, project):
        if project is None: return {'batch':None}
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT body FROM sync_batches WHERE binding=? AND project_id=? AND status IN ('pending','confirming') ORDER BY CASE status WHEN 'confirming' THEN 0 ELSE 1 END,updated_at DESC LIMIT 1",
                             (self.binding(command),project['id'])).fetchone()
            if row is None: return {'batch':None}
            batch=json.loads(row['body'])
            if batch['status']=='pending':
                batch['project_revision']=project['revision']
                batch['review_digest']=digest([batch['id'],batch['binding'],batch['project_id'],project['revision'],batch['items']])
                self.save(conn,batch)
            return {'batch':{'id':batch['id'],'digest':batch['review_digest'],'project_id':batch['project_id'],
                             'items':batch['items'],'status':batch['status'], 'project_revision':batch['project_revision']}}

    def _sources_unchanged(self, conn, batch):
        for item in batch['items']:
            if text_hash(item['source_text'])!=item['source_hash']: raise ValueError('Changed staged source; review required')
            for identity, expected in zip(item['event_ids'],item['event_hashes']):
                row=conn.execute('SELECT event_hash,body FROM sync_events WHERE binding=? AND event_id=?',
                                 (batch['binding'],identity)).fetchone()
                if row is None or row['event_hash']!=expected or digest(json.loads(row['body']))!=expected:
                    raise ValueError('Changed staged source event; review required')

    def _check_note(self, item, note_id):
        note=self.service.notes_mgr.get_note(note_id)
        if note and (note.scope!=self.service.scope or note.revision!=1 or note.content_hash!=item['source_hash'] or
                     text_hash(note.content)!=item['source_hash']):
            raise ValueError('Canonical source changed; review required')
        if note:
            raw=self.service.notes_mgr.raw_store.get_source(note.id,1)
            if raw is None or raw['content_hash']!=item['source_hash']:
                raise ValueError('Canonical raw source is missing or changed; review required')
        return note

    def _recover_memory(self, candidate_id):
        # CandidateBridge owns atomic promotion and its durable receipt.
        candidate=self.service.bridge.get_candidate(candidate_id)
        if candidate and candidate.status.value=='ACCEPTED':
            with self.service.storage.session() as conn:
                event=conn.execute("SELECT payload_json FROM transactional_outbox WHERE aggregate_id=? AND event_type='CANDIDATE_ACCEPTED' ORDER BY created_at LIMIT 1",(candidate_id,)).fetchone()
            if event: return json.loads(event['payload_json'])['promoted_node_id']
        return None

    def _check_memory(self, item, receipt):
        memory_id=receipt.get('memory_id') or (self._recover_memory(receipt['candidate_id']) if receipt.get('candidate_id') else None)
        if not memory_id: return
        node=self.service.storage.get_node(memory_id)
        if (node is None or node.provenance is None or node.scope!=self.service.scope or node.version!=receipt.get('memory_revision',1)
                or node.content!=item['claim'] or node.title!=item['title']
                or node.provenance.source_id!=receipt.get('note_id') or node.provenance.source_revision!=1
                or node.provenance.content_hash!=item['source_hash'] or node.authority!=MemoryAuthority.INFERRED):
            raise ValueError('Memory changed after confirmation; manual data preserved, review required')

    def _check_project_revision(self, batch):
        # Count only memberships already introduced by this exact confirmation.
        # A process crash after a membership commit but before its sidecar receipt
        # is recoverable without accepting an unrelated project edit.
        own_count=0
        with self.service.storage.session() as conn:
            project=self.service.collections._get(conn,batch['project_id'],self.service.scope)
            for identity in batch['selected_ids'] or []:
                item=next(i for i in batch['items'] if i['id']==identity)
                note_id='note-harness-'+digest([batch['id'],identity])[:24]
                receipt=batch['receipts'].get(identity,{})
                memory_id=receipt.get('memory_id') or (self._recover_memory(receipt['candidate_id']) if receipt.get('candidate_id') else None)
                for kind,obj_id in [('note',note_id),('memory',memory_id)]:
                    if obj_id and conn.execute('SELECT 1 FROM collection_members WHERE topic_id=? AND kind=? AND object_id=?',
                                               (batch['project_id'],kind,obj_id)).fetchone(): own_count+=1
        if project['revision'] != batch['project_revision']+own_count:
            raise ValueError('Project changed after review; refresh and resolve before confirmation')

    def confirm(self, command, project):
        if project is None: raise ValueError('Select a project before confirmation')
        if not command.batch_id or not command.digest: raise ValueError('Reviewed batch and digest required')
        if len(set(command.item_ids))!=len(command.item_ids) or any(not i.strip() or len(i)>200 for i in command.item_ids):
            raise ValueError('Invalid selected item IDs')
        # Sidecar transaction serializes confirmation retries, including across
        # processes. Canonical transactions independently recheck persisted grants.
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT body FROM sync_batches WHERE id=? AND binding=? AND project_id=?',
                             (command.batch_id,self.binding(command),project['id'])).fetchone()
            if row is None: raise ValueError('Batch belongs to another chat/project or is unavailable')
            batch=json.loads(row['body'])
            if batch['review_digest']!=command.digest: raise ValueError('Stale review digest; review again')
            recomputed=digest([batch['id'],batch['binding'],batch['project_id'],batch['project_revision'],batch['items']])
            if recomputed!=command.digest: raise ValueError('Changed review; review again')
            known={i['id'] for i in batch['items']}
            if not set(command.item_ids)<=known: raise ValueError('Selected item outside reviewed batch')
            selected=sorted(command.item_ids)
            if batch['selected_ids'] is not None and selected!=batch['selected_ids']:
                raise ValueError('Confirmation selection changed; original receipt preserved')
            self._sources_unchanged(conn,batch)
            with self.service.access_policy.activate(self.service.principal,self.service.scope,write=True):
                for item in batch['items']:
                    if item['id'] in selected:
                        receipt=batch['receipts'].get(item['id'],{})
                        note=self._check_note(item,'note-harness-'+digest([batch['id'],item['id']])[:24])
                        if receipt.get('note_id') and note is None: raise ValueError('Canonical source missing; review required')
                        self._check_memory(item,receipt)
                if batch['status']=='confirmed':
                    self.service.access_policy.check(self.service.principal,self.service.scope,write=True)
                    return batch['result']
                batch['selected_ids']=selected
                self._check_project_revision(batch)
                batch['status']='confirming'
                self.save(conn,batch)
            # Pin the exact approved selection before any canonical operation.
            conn.commit()
            try:
                with self.service.access_policy.activate(self.service.principal,self.service.scope,write=True):
                    for item in batch['items']:
                        if item['id'] not in selected: continue
                        receipt=batch['receipts'].setdefault(item['id'],{})
                        note_id='note-harness-'+digest([batch['id'],item['id']])[:24]
                        self._check_project_revision(batch)
                        self._check_memory(item,receipt)
                        note=self._check_note(item,note_id)
                        if note is None:
                            note=self.service.notes_mgr.create_note('DSH raw task excerpts · '+item['title'],item['source_text'],
                                scope=self.service.scope,author='harness:'+self.service.principal,note_id=note_id)
                        receipt['note_id']=note.id
                        self.save(conn,batch); conn.commit()
                        payload={'title':item['title'],'content':item['claim'],'content_hash':note.content_hash,
                                 'extractor_version':'harness-extractive-v1', 'core_props':{'fact_type':'harness_task_excerpt'},
                                 'custom_props':{'harness':{'batch_id':batch['id'],'draft_id':item['id'],'turn_id':item['turn_id'],
                                     'project_id':project['id'],'source_partial':item['source_partial'],
                                     'review_digest':command.digest,'confirmed_by':self.service.principal,
                                     'confirmation_is_truth_verification':False}}}
                        ok,candidate_id,error=self.service.bridge.submit_candidate(note.id,1,item['claim'],NodeType.FACT,payload,
                                scope=self.service.scope,authority=MemoryAuthority.INFERRED)
                        if not ok: raise ValueError('Candidate validation failed; preserved raw source: '+str(error))
                        receipt['candidate_id']=candidate_id
                        self.save(conn,batch); conn.commit()
                        # sync_confirm is the local owner's explicit, digest-pinned
                        # selection. Bind that consent to the exact candidate before
                        # promotion; the Harness agent itself cannot mint approvals.
                        from backend.auth.approvals import ApprovalTickets, hash_payload
                        from backend.auth.policy import current_access
                        from datetime import timedelta
                        from backend.schema.models import utc_now
                        candidate=self.service.bridge.get_candidate(candidate_id)
                        payload_hash=hash_payload(self.service.bridge.approval_payload(candidate))
                        tickets=ApprovalTickets(self.service.storage, initialize=False)
                        pending=next((ticket for ticket in tickets.list_pending(scope=self.service.scope,
                            principal=self.service.principal,limit=200) if ticket['action']=='accept_candidate'
                            and ticket['target_id']==candidate_id and ticket['target_revision']==candidate.source_revision
                            and ticket['payload_hash']==payload_hash),None)
                        if pending is None:
                            token=current_access.set(None)
                            try:
                                pending=tickets.issue(principal=self.service.principal,scope=self.service.scope,
                                    action='accept_candidate',target_id=candidate_id,
                                    target_revision=candidate.source_revision,payload_hash=payload_hash,
                                    reason='Explicit owner confirmation of reviewed Harness batch '+batch['id'],
                                    expires_at=(utc_now()+timedelta(minutes=15)).isoformat())
                            finally:
                                current_access.reset(token)
                        receipt['approval_ticket']=pending['id']
                        self.save(conn,batch); conn.commit()
                        node=self.service.bridge.accept_candidate(candidate_id,reviewer=self.service.principal,
                            actor_type='agent',approval_ticket=pending['id'])
                        if node.scope!=self.service.scope or node.provenance.source_id!=note.id or node.content!=item['claim']:
                            raise ValueError('Promotion receipt changed; review required')
                        receipt['memory_id']=node.id
                        receipt['memory_revision']=node.version
                        self.save(conn,batch); conn.commit()
                        for kind,obj_id in [('note',note.id),('memory',node.id)]:
                            self._check_project_revision(batch)
                            with self.service.storage.session() as canonical:
                                topic=self.service.collections._get(canonical,project['id'],self.service.scope)
                            self.service.collections.membership(project['id'],self.service.scope,kind,obj_id,topic['revision'])
                        receipt['status']='confirmed'
                        self.save(conn,batch); conn.commit()
                    self.service.access_policy.check(self.service.principal,self.service.scope,write=True)
                    batch['status']='confirmed'
                    batch['result']={'status':'confirmed','items':[{'id':i['id'],**batch['receipts'][i['id']]} if i['id'] in selected
                                      else {'id':i['id'],'status':'excluded'} for i in batch['items']],
                                     'note_ids':[batch['receipts'][i]['note_id'] for i in selected],
                                     'memory_ids':[batch['receipts'][i]['memory_id'] for i in selected]}
                    self.save(conn,batch)
                    return batch['result']
            except Exception:
                # Each completed canonical phase has a durable receipt. Preserve
                # the pinned selection and permit only the identical retry.
                self.save(conn,batch); conn.commit()
                raise
