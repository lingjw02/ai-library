"""
Typed Graph Node classes and helpers for all 20 Node Types.
Ref: AI Knowledge OS Backend Specification §6, §10, §13, §55.
"""

from __future__ import annotations
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from backend.schema.models import (
    GraphNode,
    NodeType,
    MemoryMaturity,
    MemoryDepth,
    MemoryAuthority,
    TemporalValidity,
    Provenance,
    utc_now,
)


class NodeFactory:
    """Helper factory for creating strictly typed nodes with proper defaults."""

    @staticmethod
    def create_entity(
        title: str,
        content: str,
        entity_type: str = "generic",
        aliases: Optional[List[str]] = None,
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.INFERRED,
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"entity-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.ENTITY,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L2_STRUCTURED,
            depth=MemoryDepth.D2_ENTITY_CONCEPT,
            authority=authority,
            core_props={"entity_type": entity_type, "aliases": aliases or []},
            custom_props=kwargs,
        )

    @staticmethod
    def create_fact(
        title: str,
        content: str,
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.OBSERVED,
        maturity: MemoryMaturity = MemoryMaturity.L3_VERIFIED,
        node_id: Optional[str] = None,
        provenance: Optional[Provenance] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"fact-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.FACT,
            scope=scope,
            title=title,
            content=content,
            maturity=maturity,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=authority,
            provenance=provenance,
            custom_props=kwargs,
        )

    @staticmethod
    def create_decision(
        title: str,
        content: str,
        rationale: str,
        status: str = "APPROVED",
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.EXPLICIT,
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"dec-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.DECISION,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L5_CANONICAL,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=authority,
            memory_props={"rationale": rationale, "decision_status": status},
            custom_props=kwargs,
        )

    @staticmethod
    def create_evidence(
        title: str,
        snippet: str,
        source_id: str,
        source_revision: int = 1,
        content_hash: str = "",
        scope: str = "default",
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"evid-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.EVIDENCE,
            scope=scope,
            title=title,
            content=snippet,
            maturity=MemoryMaturity.L3_VERIFIED,
            depth=MemoryDepth.D4_EVIDENCE_RAW,
            authority=MemoryAuthority.OBSERVED,
            source_props={
                "source_id": source_id,
                "source_revision": source_revision,
                "content_hash": content_hash,
            },
            custom_props=kwargs,
        )

    @staticmethod
    def create_conflict(
        title: str,
        content: str,
        conflicting_node_ids: List[str],
        conflict_type: str = "SEMANTIC_CONFLICT",
        scope: str = "default",
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        """Models explicit semantic contradiction as a first-class Data State (§3, §4, §29)."""
        return GraphNode(
            id=node_id or f"conf-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.CONFLICT,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L2_STRUCTURED,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=MemoryAuthority.OBSERVED,
            core_props={
                "conflict_type": conflict_type,
                "conflicting_nodes": conflicting_node_ids,
                "status": "UNRESOLVED",
            },
            custom_props=kwargs,
        )

    @staticmethod
    def create_experience(
        title: str,
        content: str,
        succeeded: bool,
        lessons_learned: Optional[str] = None,
        scope: str = "default",
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"exp-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.EXPERIENCE,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L3_VERIFIED,
            depth=MemoryDepth.D3_FACT_RELATION,
            authority=MemoryAuthority.OBSERVED,
            memory_props={"succeeded": succeeded, "lessons_learned": lessons_learned},
            custom_props=kwargs,
        )

    @staticmethod
    def create_heuristic(
        title: str,
        content: str,
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.INFERRED,
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"heur-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.HEURISTIC,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L4_CONSOLIDATED,
            depth=MemoryDepth.D2_ENTITY_CONCEPT,
            authority=authority,
            custom_props=kwargs,
        )

    @staticmethod
    def create_strategy(
        title: str,
        content: str,
        steps: Optional[List[str]] = None,
        scope: str = "default",
        authority: MemoryAuthority = MemoryAuthority.INFERRED,
        node_id: Optional[str] = None,
        **kwargs,
    ) -> GraphNode:
        return GraphNode(
            id=node_id or f"strat-{uuid.uuid4().hex[:10]}",
            node_type=NodeType.STRATEGY,
            scope=scope,
            title=title,
            content=content,
            maturity=MemoryMaturity.L4_CONSOLIDATED,
            depth=MemoryDepth.D1_TOPIC,
            authority=authority,
            memory_props={"steps": steps or []},
            custom_props=kwargs,
        )
