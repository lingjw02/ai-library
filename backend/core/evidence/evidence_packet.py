"""
Evidence Packet Builder.
Ref: AI Knowledge OS Backend Specification §§20, 22, 51.
Assembles verifiable evidence units with exact source bindings, provenance,
temporal validity, and explicit conflict annotations.
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from backend.schema.models import (
    GraphNode,
    MemoryAuthority,
    MemoryMaturity,
    utc_now,
)


class EvidenceItem(BaseModel):
    id: str
    node_type: str
    title: str
    content: str
    maturity: str
    depth: str
    authority: str
    confidence: float
    source_id: Optional[str] = None
    source_revision: Optional[int] = None
    content_hash: Optional[str] = None
    is_conflicted: bool = False
    conflict_note: Optional[str] = None
    lifecycle_status: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class EvidencePacket(BaseModel):
    query: str
    scope: str
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    coverage_status: str = "sufficient"  # "sufficient", "partial", "insufficient_evidence"
    stop_reason: str = "complete"
    items: List[EvidenceItem] = Field(default_factory=list)
    conflicts_detected: List[Dict[str, Any]] = Field(default_factory=list)
    estimated_tokens: int = 0

    model_config = ConfigDict(extra="ignore")


class EvidencePacketBuilder:
    """Constructs verifiable EvidencePackets from retrieved nodes and conflicts."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Heuristic linear token estimation: 1 token ≈ 4 characters."""
        return max(1, math.ceil(len(text) / 4))

    @classmethod
    def build(
        cls,
        query: str,
        nodes: List[GraphNode],
        conflicts: Optional[List[GraphNode]] = None,
        scope: str = "default",
        stop_reason: str = "complete",
    ) -> EvidencePacket:
        items: List[EvidenceItem] = []
        conflicts_list: List[Dict[str, Any]] = []
        total_text = query

        conflict_ids_by_node = {}
        retrieved_ids = {node.id for node in nodes}
        if conflicts:
            for c in conflicts:
                if (not c.is_active or c.scope != scope or
                        c.core_props.get('status') in {'RESOLVED', 'SUPERSEDED', 'IGNORED'}):
                    continue
                conflicted_ids = c.core_props.get("conflicting_nodes", [])
                relevant_ids = retrieved_ids.intersection(conflicted_ids)
                if not relevant_ids:
                    continue
                for identity in relevant_ids:
                    conflict_ids_by_node.setdefault(identity, set()).add(c.id)
                conflicts_list.append({
                    "conflict_id": c.id,
                    "title": c.title,
                    "content": c.content,
                    "conflicting_nodes": conflicted_ids,
                })
                total_text += f" {c.title} {c.content}"

        for node in nodes:
            src_id = None
            src_rev = None
            c_hash = None
            if node.provenance:
                src_id = node.provenance.source_id
                src_rev = node.provenance.source_revision
                c_hash = node.provenance.content_hash
            elif "source_id" in node.source_props:
                src_id = node.source_props.get("source_id")
                src_rev = node.source_props.get("source_revision", 1)
                c_hash = node.source_props.get("content_hash", "")

            conflict_ids = sorted(conflict_ids_by_node.get(node.id, set()))
            is_conf = bool(conflict_ids)
            items.append(
                EvidenceItem(
                    id=node.id,
                    node_type=node.node_type.value,
                    title=node.title,
                    content=node.content,
                    maturity=node.maturity.value,
                    depth=node.depth.value,
                    authority=node.authority.value,
                    confidence=node.confidence,
                    source_id=src_id,
                    source_revision=src_rev,
                    content_hash=c_hash,
                    is_conflicted=is_conf,
                    conflict_note=("Reported unresolved conflict(s): " + ', '.join(conflict_ids)
                                   + "; not independently verified") if is_conf else None,
                    lifecycle_status=(node.core_props.get('status', 'OPEN')
                                      if node.node_type.value == 'Conflict' else None),
                )
            )
            total_text += f" {node.title} {node.content}"

        coverage = "sufficient" if items else "insufficient_evidence"

        return EvidencePacket(
            query=query,
            scope=scope,
            coverage_status=coverage,
            stop_reason=stop_reason,
            items=items,
            conflicts_detected=conflicts_list,
            estimated_tokens=cls.estimate_tokens(total_text),
        )
