"""
FastAPI REST API Boundary for AI Knowledge OS.
Ref: AI Knowledge OS Backend Specification §§59, 60.
Provides unified REST endpoints for:
- Notes Plane (create, read, revise, history)
- Memory Plane (query, save, retrieve)
- Candidate Bridge (submit, list, accept, reject)
- System Memory Backlog (capture, list, transition)
- System Memory Logsheet (append, list, query)
- Retrieval & Context Compiler (progressive search, deterministic context compilation)
- Capability Registry & Agent Connections (skills, MCPs, binding, exports)
"""

from __future__ import annotations
import os
import threading
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from backend.storage.canonical.sqlite_storage import CanonicalStorage, CanonicalRevisionConflict
from backend.storage.raw.raw_store import RawSourceStore
from backend.notes.notes_manager import NotesManager, RevisionConflict
from backend.collections.api import create_collection_router
from backend.views.api import create_saved_view_router
from backend.spiders.api import create_spider_router
from backend.settings.api import create_settings_router
from backend.lineage.api import create_lineage_router
from backend.evolution.api import create_evolution_router
from backend.evolution.manager import EvolutionManager
from backend.auth.approvals import create_approval_router
from backend.intake.api import create_intake_router
from backend.media.api import create_media_router
from backend.adapters.api.backend_operations import create_backend_operations_router
from backend.auth.policy import AccessPolicy
from backend.capabilities.local_skills import discover_skills, export_local_skill_zip
from backend.capabilities.aas_engine import compose_aas_stack
from backend.candidates.candidate_bridge import CandidateBridge
from backend.conflicts.conflict_manager import ConflictManager
from backend.quarantine.quarantine_manager import QuarantineManager
from backend.maintenance.backlog import MemoryBacklog, BacklogType, BacklogStatus, BacklogPriority, ApprovalStatus
from backend.maintenance.logsheet import MemoryLogsheet, LogsheetEventType
from backend.core.retrieval.retrieval_coordinator import RetrievalCoordinator
from backend.core.retrieval.keyword_index import KeywordIndexWorker
from backend.capabilities.registry.capability_registry import (
    CapabilityRegistry,
    SkillBundle,
    McpServerDefinition,
    CapabilityKind,
)
from backend.capabilities.connections.agent_connections import AgentConnectionManager, ProfileRevisionConflict
from backend.telemetry.token_accounting import TokenAccountant
from backend.telemetry.budgets import BudgetLedger
from backend.schema.registry import global_schema_registry, SchemaValidationError
from backend.schema.models import (
    GraphNode,
    NodeType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
)


# ---------------------------------------------------------
# Request/Response Models at Module Level
# ---------------------------------------------------------

class CreateNoteRequest(BaseModel):
    title: str
    content: str
    scope: str = "default"
    author: str = "user"


class ReviseNoteRequest(BaseModel):
    title: str
    content: str
    author: str = "user"
    expected_revision: int = Field(ge=1)


class SubmitCandidateRequest(BaseModel):
    source_id: str
    source_revision: int = 1
    claim: str
    suggested_node_type: NodeType = NodeType.FACT
    payload: Dict[str, Any]
    scope: str = "default"
    authority: MemoryAuthority = MemoryAuthority.INFERRED


class CaptureBacklogRequest(BaseModel):
    type: str
    scope: str = "default"
    targets: List[Dict[str, Any]]
    reason: str
    priority: str = "normal"
    priority_reason: str = "Standard maintenance"
    source_refs: Optional[List[Dict[str, Any]]] = None
    approval_status: str = "NOT_REQUIRED"
    blocker: Optional[str] = None


ConflictStatus = Literal['OPEN', 'UNDER_REVIEW', 'RESOLVED', 'SUPERSEDED', 'IGNORED']


class ReportConflictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)
    memory_targets: Dict[str, StrictInt] = Field(max_length=10)
    candidate_ids: List[str] = Field(max_length=10)
    kind: Literal['PROPERTY_CONFLICT', 'SEMANTIC_CONFLICT', 'TEMPORAL_CONFLICT']
    description: str = Field(min_length=1, max_length=8000)
    severity: Literal['low', 'normal', 'high'] = 'normal'


class ConflictEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(min_length=1, max_length=200)
    source_revision: StrictInt = Field(ge=1)
    content_hash: str = Field(pattern='^[0-9a-f]{64}$')


class TransitionConflictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)
    status: ConflictStatus
    reason: str = Field(min_length=1, max_length=8000)
    method: str = Field(default='', max_length=8000)
    evidence_refs: List[ConflictEvidenceRequest] = Field(default_factory=list, max_length=20)
    superseded_by: Optional[str] = Field(default=None, min_length=1, max_length=200)


class RepairQuarantineRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: str = Field(default='default', min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)
    claim: str = Field(min_length=1, max_length=200_000)
    suggested_node_type: NodeType
    payload: Dict[str, Any]
    reason: str = Field(min_length=1, max_length=8000)


class TransitionBacklogRequest(BaseModel):
    status: str
    expected_revision: int = Field(ge=1)
    reason: str = ""
    blocker: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None


class AppendLogsheetRequest(BaseModel):
    operation_id: str
    event_type: str
    actor: Dict[str, Any]
    scope: str
    target_refs: List[Dict[str, Any]]
    reason: str
    result: str
    evidence_refs: Optional[List[Dict[str, Any]]] = None
    backlog_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class EmbeddingSpaceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    dimension: StrictInt = Field(ge=1, le=4096)
    metric: Literal['cosine'] = 'cosine'
    normalization: Literal['l2'] = 'l2'


class EmbeddingQueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    space: EmbeddingSpaceRequest
    vector: List[StrictFloat | StrictInt] = Field(min_length=1, max_length=4096)


class EmbeddingSubmissionRequest(EmbeddingQueryRequest):
    plane: Literal['MEMORY', 'NOTES']
    object_id: str = Field(min_length=1, max_length=500)
    revision: StrictInt = Field(ge=1)
    content_hash: str = Field(pattern='^[0-9a-f]{64}$')
    scope: str = Field(default='default', min_length=1, max_length=200)


class SearchQueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=2000)
    scope: str = "default"
    user_specified_plane: Optional[str] = None
    token_budget: int = Field(default=2048, ge=1, le=65536)
    embedding_query: Optional[EmbeddingQueryRequest] = None
    read_mode: Literal['best_available', 'at_least_revision', 'historical_as_of'] = 'best_available'
    minimum_revision: StrictInt | None = Field(default=None, ge=0)
    as_of: str | None = Field(default=None, max_length=100)
    recorded_as_of: str | None = Field(default=None, max_length=100)
    deadline_ms: StrictInt = Field(default=500, ge=1, le=10_000)
    max_depth: StrictInt | None = Field(default=None, ge=0, le=3)


class RegisterSkillRequest(BaseModel):
    capability_id: str
    name: str
    description: str
    skill_md_content: str
    scripts: Optional[Dict[str, str]] = None
    references: Optional[Dict[str, str]] = None
    version: str = "1.0.0"


class RegisterMcpRequest(BaseModel):
    name: str
    description: str
    server_def: McpServerDefinition
    cap_id: Optional[str] = None
    version: str = "1.0.0"


class CreateAgentProfileRequest(BaseModel):
    client_kind: str
    client_version: str = "UNKNOWN"
    device_id: str = "local"


class SkillBindingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=1)
    pinned_version: str = Field(min_length=1, max_length=200)
    binding_method: Literal['read_only_path', 'symlink', 'copy'] = 'read_only_path'


class McpBindingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=1)
    pinned_version: str = Field(min_length=1, max_length=200)
    installation_or_endpoint_ref: str = Field(min_length=1, max_length=4000)
    credential_ref: Optional[str] = None


class ClientPlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    before: Optional[str] = Field(default=None, max_length=1_000_000)


def create_app(
    db_path: str = "data/knowledge_os.db",
    raw_dir: str = "data/raw_sources",
    artifacts_dir: str = "data/capabilities/artifacts",
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        # 首次全量扫描本机技能目录约 27 秒。放到后台线程预热，避免第一个
        # /api/local-skills 请求把用户卡住。
        try:
            from backend.capabilities.local_skills import discover_skills as _warm_skills
            threading.Thread(target=_warm_skills, name='skills-warmup', daemon=True).start()
        except Exception:
            pass
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(
        title="AI Knowledge OS API",
        version="1.4.0",
        description="Model-Agnostic Knowledge, Memory & Shared Capability Infrastructure",
        lifespan=lifespan,
    )

    # 压缩前端静态资源：vendor/three 的两个未压缩 ESM 合计约 2 MB，是首屏加载的
    # 最大单项。gzip 后可降到约 400 KB，且对 JSON API 同样生效。
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # The frontend is served straight from frontend/ and is edited in place.
    # Without an explicit directive browsers fall back to heuristic caching, so
    # a refreshed index.html can be paired with a stale ES module: the new
    # "设置" button renders while its click handler never loads, and clicking it
    # does nothing. Force revalidation (etag -> cheap 304) on every static
    # response so the UI can never drift out of sync with its scripts.
    @app.middleware('http')
    async def _revalidate_frontend_assets(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith('/api/'):
            # vendor/ 与 assets/ 是不就地修改的第三方资源（three.js、GLB 模型），
            # 允许浏览器缓存一天：否则每次刷新都要重新下载约 2 MB 脚本。
            # 应用自身的 html/css/js 仍强制重验证，保持与 ES 模块版本一致。
            if request.url.path.startswith(('/vendor/', '/assets/')):
                response.headers['Cache-Control'] = 'public, max-age=86400'
            else:
                response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return response


    # Core engine singletons
    storage = CanonicalStorage(db_path=db_path)
    app.include_router(create_collection_router(storage))
    app.include_router(create_saved_view_router(storage))
    app.include_router(create_spider_router(storage))
    raw_store = RawSourceStore(base_dir=raw_dir)
    notes_mgr = NotesManager(storage=storage, raw_store=raw_store)
    intake_router = create_intake_router(storage, notes_mgr, raw_store)
    app.include_router(intake_router)
    media_router = create_media_router(storage, notes_mgr, raw_store)
    app.include_router(media_router)
    app.include_router(create_lineage_router(storage))
    evolution_mgr = EvolutionManager(storage)
    app.include_router(create_evolution_router(storage, manager=evolution_mgr))
    app.include_router(create_approval_router(storage))
    app.include_router(create_backend_operations_router(storage))
    bridge = CandidateBridge(storage=storage)
    conflicts = ConflictManager(storage)
    quarantine = QuarantineManager(storage)
    backlog = MemoryBacklog(storage=storage)
    logsheet = MemoryLogsheet(storage=storage)
    coordinator = RetrievalCoordinator(storage=storage, notes_mgr=notes_mgr, strategy_manager=evolution_mgr)
    worker = KeywordIndexWorker(coordinator.keyword_index)
    app.state.keyword_index = coordinator.keyword_index
    capability_registry = CapabilityRegistry(storage=storage, artifacts_dir=artifacts_dir)
    agent_conn_mgr = AgentConnectionManager(storage=storage, registry=capability_registry)
    token_accountant = TokenAccountant(storage=storage)
    durable_budget = BudgetLedger(storage)
    app.state.token_accountant = token_accountant

    # Local-owner settings surface (MCP grant administration, system status).
    # Deliberately not part of the scoped MCP tool list.
    access_policy = AccessPolicy(storage)
    app.include_router(create_settings_router(storage, access_policy, coordinator))

    # ---------------------------------------------------------
    # Health & System Status
    # ---------------------------------------------------------
    @app.get("/api/health")
    def health_check():
        return {
            "status": "healthy",
            "version": "1.4.0",
            "remaining_token_budget": durable_budget.status(scope="default")["remaining"],
            "keyword_index": coordinator.keyword_index.status(),
        }

    @app.get("/api/telemetry/cache-metrics")
    def get_cache_metrics():
        # Local retrieval/cache behavior is not a provider prompt-cache report.
        return coordinator.cache_mgr.stats()

    # ---------------------------------------------------------
    # Notes Plane (§5, §11)
    # ---------------------------------------------------------
    @app.post("/api/notes", status_code=status.HTTP_201_CREATED)
    def create_note(req: CreateNoteRequest):
        rev = notes_mgr.create_note(title=req.title, content=req.content, scope=req.scope, author=req.author)
        token_accountant.record_usage("create_note", tokens=len(req.content) // 4, category="write", scope=req.scope)
        return rev.to_dict()

    @app.get("/api/notes")
    def list_notes(scope: Optional[str] = None, limit: int = 50):
        notes = notes_mgr.list_notes(scope=scope, limit=limit)
        return [n.to_dict() for n in notes]

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: str, revision: Optional[int] = None):
        note = notes_mgr.get_note(note_id, revision=revision)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return note.to_dict()

    @app.post("/api/notes/{note_id}/revisions")
    def revise_note(note_id: str, req: ReviseNoteRequest):
        try:
            rev = notes_mgr.revise_note(note_id=note_id, title=req.title, content=req.content, author=req.author, expected_revision=req.expected_revision)
            token_accountant.record_usage("revise_note", tokens=len(req.content) // 4, category="write")
            return rev.to_dict()
        except RevisionConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ---------------------------------------------------------
    # Memory Plane (§6, §10, §11)
    # ---------------------------------------------------------
    @app.get("/api/memory/nodes")
    def list_memory_nodes(
        node_type: Optional[str] = None,
        scope: Optional[str] = None,
        maturity: Optional[str] = None,
        depth: Optional[str] = None,
        limit: int = 50,
        order_by: str = "updated_at DESC",
    ):
        nt = NodeType(node_type) if node_type else None
        mat = MemoryMaturity(maturity) if maturity else None
        dep = MemoryDepth(depth) if depth else None
        nodes = storage.list_nodes(node_type=nt, scope=scope, maturity=mat, depth=dep, limit=limit, order_by=order_by)
        return [n.model_dump() for n in nodes]

    @app.post("/api/memory/nodes", status_code=status.HTTP_201_CREATED)
    def save_memory_node(node_data: Dict[str, Any], expected_revision: Optional[int] = Query(default=None, ge=0)):
        try:
            node = global_schema_registry.require_node(node_data)
            if storage.get_node(node.id) is not None and expected_revision is None:
                raise HTTPException(status_code=428, detail='Updates require expected_revision; read current memory before editing')
            storage.upsert_node(node, expected_revision=expected_revision if expected_revision is not None else 0)
        except CanonicalRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SchemaValidationError as exc:
            record = exc.quarantine_record
            # No reliable source envelope accompanies a raw owner write. Keep
            # scope/source unknown, so it cannot enter scoped Agent repair flows.
            storage.save_quarantine(record)
            raise HTTPException(status_code=422, detail={'message': str(exc), 'quarantine_id': record.id}) from exc
        token_accountant.record_usage("save_memory_node", tokens=50, category="write", scope=node.scope)
        return {"status": "saved", "node_id": node.id, "version": node.version}

    @app.get("/api/memory/nodes/{node_id}")
    def get_memory_node(node_id: str, revision: Optional[int] = Query(default=None, ge=1), scope: str = 'default'):
        node = storage.get_node_revision(node_id, revision, scope=scope) if revision is not None else storage.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Memory node not found")
        return node.model_dump()

    @app.get('/api/memory/nodes/{node_id}/revisions')
    def get_memory_revisions(node_id: str, scope: str = 'default', limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        return storage.list_node_revisions(node_id, scope=scope, limit=limit, offset=offset)

    @app.get("/api/memory/edges")
    def list_memory_edges(scope: Optional[str] = None, limit: int = 100):
        with storage.session() as conn:
            query = "SELECT data_json FROM edges"
            params: List[Any] = []
            if scope:
                query += " WHERE scope = ?"
                params.append(scope)
            query += " LIMIT ?;"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [json.loads(r["data_json"]) for r in rows]

    @app.get("/api/memory/nodes/{node_id}/edges")
    def get_node_edges(node_id: str):
        edges = storage.get_edges_for_node(node_id)
        return [e.model_dump() for e in edges]

    # ---------------------------------------------------------
    # Candidate Bridge (§5)
    # ---------------------------------------------------------
    @app.post("/api/candidates")
    def submit_candidate(req: SubmitCandidateRequest):
        ok, cid, err = bridge.submit_candidate(
            source_id=req.source_id,
            source_revision=req.source_revision,
            claim=req.claim,
            suggested_node_type=NodeType(req.suggested_node_type),
            payload=req.payload,
            scope=req.scope,
            authority=MemoryAuthority(req.authority),
        )
        return {"success": ok, "id": cid, "error": err}

    @app.get("/api/candidates")
    def list_candidates(scope: Optional[str] = None, limit: int = 50, source_id: Optional[str] = None):
        cands = bridge.list_candidates(scope=scope, limit=limit, source_id=source_id)
        return [c.to_dict() for c in cands]

    @app.post("/api/candidates/{candidate_id}/accept")
    def accept_candidate(candidate_id: str, reviewer: str = "user"):
        try:
            node = bridge.accept_candidate(candidate_id, reviewer=reviewer)
            return {"status": "accepted", "promoted_node": node.model_dump()}
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/api/candidates/{candidate_id}/reject")
    def reject_candidate(candidate_id: str, reason: str = ""):
        try:
            bridge.reject_candidate(candidate_id, reason=reason)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"status": "rejected", "candidate_id": candidate_id}

    # ---------------------------------------------------------
    # Conflict reports and local-owner review (§4, §5)
    # REST is the existing trusted local-owner boundary, not remote multi-user auth.
    # MCP intentionally exposes reporting and reading only.
    # ---------------------------------------------------------
    def conflict_action(action):
        try:
            return action().model_dump(mode='json')
        except CanonicalRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post('/api/conflicts', status_code=201)
    def report_conflict(req: ReportConflictRequest):
        return conflict_action(lambda: conflicts.report(**req.model_dump(), actor='local-owner', actor_type='user'))

    @app.get('/api/conflicts')
    def list_conflicts(scope: str = 'default', status: Optional[ConflictStatus] = None,
                       node_id: Optional[str] = None, limit: int = Query(default=20, ge=1, le=100),
                       offset: int = Query(default=0, ge=0, le=1_000_000)):
        return conflicts.list(scope=scope, status=status, node_id=node_id, limit=limit, offset=offset)

    @app.get('/api/conflicts/{conflict_id}')
    def get_conflict(conflict_id: str, scope: str = 'default'):
        return conflict_action(lambda: conflicts.get(conflict_id, scope=scope))

    @app.post('/api/conflicts/{conflict_id}/transition')
    def transition_conflict(conflict_id: str, req: TransitionConflictRequest):
        return conflict_action(lambda: conflicts.transition(conflict_id=conflict_id,
            **req.model_dump(), actor='local-owner', actor_type='user'))

    @app.get('/api/quarantine')
    def list_quarantine(scope: str = 'default', resolved: bool = False,
                        limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1_000_000)):
        return quarantine.list(scope=scope, resolved=resolved, limit=limit, offset=offset)

    @app.get('/api/quarantine-legacy')
    def list_legacy_quarantine(limit: int = Query(default=20, ge=1, le=100),
                               offset: int = Query(default=0, ge=0, le=1_000_000)):
        return quarantine.list_legacy(limit=limit, offset=offset)

    @app.get('/api/quarantine/{quarantine_id}')
    def get_quarantine(quarantine_id: str, scope: str = 'default'):
        return conflict_action(lambda: quarantine.get(quarantine_id, scope=scope))

    @app.post('/api/quarantine/{quarantine_id}/repair')
    def repair_quarantine(quarantine_id: str, req: RepairQuarantineRequest):
        return conflict_action(lambda: quarantine.repair(quarantine_id, **req.model_dump(), actor='local-owner'))

    # ---------------------------------------------------------
    # System Memory Backlog (§89)
    # ---------------------------------------------------------
    @app.post("/api/maintenance/backlog")
    def capture_backlog_item(req: CaptureBacklogRequest):
        item = backlog.capture_item(
            item_type=BacklogType(req.type),
            scope=req.scope,
            targets=req.targets,
            reason=req.reason,
            priority=BacklogPriority(req.priority),
            priority_reason=req.priority_reason,
            source_refs=req.source_refs,
            approval_status=ApprovalStatus(req.approval_status),
            blocker=req.blocker,
        )
        return item.to_dict()

    @app.get("/api/maintenance/backlog")
    def list_backlog_items(scope: Optional[str] = None, limit: int = 50):
        items = backlog.list_items(scope=scope, limit=limit)
        return [i.to_dict() for i in items]

    @app.post("/api/maintenance/backlog/{item_id}/transition")
    def transition_backlog_item(item_id: str, req: TransitionBacklogRequest):
        try:
            item = backlog.transition_status(
                item_id=item_id,
                target_status=BacklogStatus(req.status),
                reason=req.reason,
                blocker=req.blocker,
                resolution=req.resolution,
                expected_revision=req.expected_revision,
            )
            return item.to_dict()
        except RevisionConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ---------------------------------------------------------
    # System Memory Logsheet (§90)
    # ---------------------------------------------------------
    @app.post("/api/maintenance/logsheet")
    def append_logsheet_entry(req: AppendLogsheetRequest):
        entry = logsheet.append_entry(
            operation_id=req.operation_id,
            event_type=LogsheetEventType(req.event_type),
            actor=req.actor,
            scope=req.scope,
            target_refs=req.target_refs,
            reason=req.reason,
            result=req.result,
            evidence_refs=req.evidence_refs,
            backlog_id=req.backlog_id,
            idempotency_key=req.idempotency_key,
        )
        return entry.to_dict()

    @app.get("/api/maintenance/logsheet")
    def query_logsheet(
        operation_id: Optional[str] = None,
        backlog_id: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 50,
    ):
        entries = logsheet.query_entries(
            operation_id=operation_id,
            backlog_id=backlog_id,
            scope=scope,
            limit=limit,
        )
        return [e.to_dict() for e in entries]

    # ---------------------------------------------------------
    # Retrieval & Context Compiler (§83, §85)
    # ---------------------------------------------------------
    @app.post("/api/retrieval/search")
    def search_and_compile_context(req: SearchQueryRequest):
        try:
            artifact = coordinator.retrieve(
                query=req.query,
                scope=req.scope,
                user_specified_plane=req.user_specified_plane,
                token_budget=req.token_budget,
                embedding_query=req.embedding_query.model_dump() if req.embedding_query else None,
                read_mode=req.read_mode,
                minimum_revision=req.minimum_revision,
                as_of=req.as_of,
                recorded_as_of=req.recorded_as_of,
                deadline_ms=req.deadline_ms,
                max_depth=req.max_depth,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        token_accountant.record_usage(
            "retrieve_context",
            tokens=artifact.estimated_tokens,
            category="read",
            scope=req.scope,
        )
        raw_dump = artifact.model_dump()
        return raw_dump

    @app.get('/api/retrieval/documents')
    def search_documents(query: str = Query(min_length=1, max_length=2000), scope: str = 'default',
                         plane: str = 'BOTH', limit: int = Query(default=30, ge=1, le=100), node_type: Optional[str] = None):
        try:
            return coordinator.search(query, scope, plane, limit, node_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/retrieval/index')
    def index_status():
        return coordinator.keyword_index.status()

    @app.post('/api/retrieval/index/rebuild')
    def rebuild_index():
        return coordinator.keyword_index.rebuild()

    @app.post('/api/retrieval/embeddings')
    def submit_embedding(req: EmbeddingSubmissionRequest):
        try:
            return coordinator.vector_index.submit_vector(**req.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/retrieval/vector-index')
    def vector_index_status():
        return coordinator.vector_index.status()

    @app.post('/api/retrieval/vector-index/rebuild')
    def rebuild_vector_index():
        return coordinator.vector_index.rebuild()

    # ---------------------------------------------------------
    # Capability Registry & Agent Connections (§91, §92)
    # ---------------------------------------------------------
    @app.post("/api/capabilities/skills")
    def register_skill(req: RegisterSkillRequest):
        bundle = SkillBundle(
            capability_id=req.capability_id,
            name=req.name,
            description=req.description,
            skill_md_content=req.skill_md_content,
            scripts=req.scripts,
            references=req.references,
            version=req.version,
        )
        cap = capability_registry.register_skill(bundle)
        return cap.model_dump()

    @app.post("/api/capabilities/mcp")
    def register_mcp(req: RegisterMcpRequest):
        cap = capability_registry.register_mcp_server(
            name=req.name,
            description=req.description,
            server_def=req.server_def,
            cap_id=req.cap_id,
            version=req.version,
        )
        return cap.model_dump()

    @app.get('/api/local-skills')
    def local_skills():
        return discover_skills()

    @app.post('/api/local-skills/compose-stack')
    def compose_local_aas_stack(payload: Dict[str, Any]):
        try:
            selected_ids = payload.get("skill_ids") or payload.get("selected_ids") or []
            project_name = payload.get("project_name") or "ai-library-knowledge-os"
            catalogue = discover_skills()
            return compose_aas_stack(
                selected_ids=selected_ids,
                all_items=catalogue.get("items", []),
                project_name=project_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/local-skills/{skill_id}/download')
    def download_local_skill(skill_id: str):
        try:
            filename, zip_bytes = export_local_skill_zip(skill_id)
            return Response(
                content=zip_bytes,
                media_type='application/zip',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/capabilities")
    def list_capabilities(kind: Optional[str] = None):
        ck = CapabilityKind(kind) if kind else None
        caps = capability_registry.list_capabilities(kind=ck)
        return [c.model_dump() for c in caps]

    @app.get("/api/capabilities/{capability_id}")
    def get_capability(capability_id: str):
        cap = capability_registry.get_capability(capability_id)
        if not cap:
            raise HTTPException(status_code=404, detail="Capability not found")
        return cap.model_dump()

    @app.get("/api/capabilities/skills/{capability_id}/download")
    def download_registered_skill(
        capability_id: str,
        version: Optional[str] = None,
        format: Literal["zip", "json"] = "zip",
    ):
        try:
            cap, zip_bytes, manifest_payload = capability_registry.export_skill_bundle(capability_id, version=version)
            if format == "json":
                return manifest_payload
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{cap.id}-{cap.version}.zip"'},
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/agent-profiles")
    def list_agent_profiles():
        return [p.model_dump() for p in agent_conn_mgr.list_profiles()]

    @app.post("/api/agent-profiles")
    def create_agent_profile(req: CreateAgentProfileRequest):
        profile = agent_conn_mgr.create_profile(
            client_kind=req.client_kind,
            client_version=req.client_version,
            device_id=req.device_id,
        )
        return profile.model_dump()

    @app.get("/api/agent-profiles/{profile_id}")
    def get_agent_profile(profile_id: str):
        prof = agent_conn_mgr.get_profile(profile_id)
        if not prof:
            raise HTTPException(status_code=404, detail="Profile not found")
        return prof.model_dump()

    @app.get("/api/agent-profiles/{profile_id}/export")
    def export_agent_mcp_config(profile_id: str):
        try:
            return agent_conn_mgr.export_mcp_config(profile_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    def require_profile(profile_id):
        if agent_conn_mgr.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail='Profile not found')

    def binding_result(operation):
        try:
            return operation().model_dump(mode='json')
        except ProfileRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put('/api/agent-profiles/{profile_id}/bindings/skills/{capability_id}')
    def bind_profile_skill(profile_id: str, capability_id: str, req: SkillBindingRequest):
        require_profile(profile_id)
        return binding_result(lambda: agent_conn_mgr.bind_skill(profile_id, capability_id,
            req.pinned_version, req.binding_method, expected_revision=req.expected_revision))

    @app.put('/api/agent-profiles/{profile_id}/bindings/mcp/{capability_id}')
    def bind_profile_mcp(profile_id: str, capability_id: str, req: McpBindingRequest):
        require_profile(profile_id)
        return binding_result(lambda: agent_conn_mgr.bind_mcp(profile_id, capability_id,
            req.installation_or_endpoint_ref, req.credential_ref, req.pinned_version,
            expected_revision=req.expected_revision))

    @app.delete('/api/agent-profiles/{profile_id}/bindings/{kind}/{capability_id}')
    def unbind_profile_capability(profile_id: str, kind: Literal['skills', 'mcp'], capability_id: str,
                                  expected_revision: int = Query(ge=1)):
        require_profile(profile_id)
        return binding_result(lambda: agent_conn_mgr.unbind_capability(profile_id, capability_id,
            'SKILL' if kind == 'skills' else 'MCP', expected_revision=expected_revision))

    @app.post('/api/agent-profiles/{profile_id}/client-plan')
    def plan_profile_client(profile_id: str, req: ClientPlanRequest):
        require_profile(profile_id)
        try:
            plan = agent_conn_mgr.plan_client_config(profile_id, req.before.encode('utf-8') if req.before is not None else None)
            return {'client': plan.client, 'before_hash': plan.before_hash, 'after_hash': plan.after_hash,
                    'proposed': plan.after.decode('utf-8-sig'), 'diff': plan.diff, 'applied': False,
                    'rollback': 'Keep the original bytes; restore only if the current file still matches after_hash.'}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/agent-profiles/{profile_id}/audit')
    def profile_audit(profile_id: str, limit: int = Query(default=100, ge=1, le=100)):
        require_profile(profile_id)
        return agent_conn_mgr.get_profile_audit(profile_id, limit)

    frontend_dir = str(Path(__file__).resolve().parents[3] / "frontend")
    if os.path.exists(frontend_dir):
        # Cache-Control for the frontend is applied by the middleware above.
        # StaticFiles does not accept a `headers` kwarg in this Starlette version.
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


_data_root = Path(os.environ.get('AI_LIBRARY_DATA_DIR', Path(__file__).resolve().parents[3] / 'data'))
app = create_app(str(_data_root / 'knowledge_os.db'), str(_data_root / 'raw_sources'), str(_data_root / 'capabilities/artifacts'))
