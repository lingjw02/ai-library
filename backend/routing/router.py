"""
Source Router & Query Router.
Ref: AI Knowledge OS Backend Specification §§19, 52, 83.1, 83.2.
Guarantees:
- Authority and scope boundaries are respected.
- Query intent routes to appropriate coarse-to-fine paths.
"""

from __future__ import annotations
import re
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class SearchTargetPlane(str, Enum):
    NOTES_PLANE = "NOTES"
    MEMORY_PLANE = "MEMORY"
    BOTH = "BOTH"


class QueryIntent(str, Enum):
    ENTITY_FAST_PATH = "ENTITY_FAST_PATH"      # Exact ID or unique attribute lookup
    OVERVIEW_TOPIC = "OVERVIEW_TOPIC"          # D0/D1 high-level summary
    SEMANTIC_QUESTION = "SEMANTIC_QUESTION"    # BM25 + Vector hybrid
    RELATION_MULTI_HOP = "RELATION_MULTI_HOP"  # Graph expansion
    HISTORICAL = "HISTORICAL"                  # As-of temporal query


class SourceRouteResult(BaseModel):
    target_planes: List[SearchTargetPlane]
    scope_filters: List[str]
    as_of: Optional[str] = None
    reason: str

    model_config = ConfigDict(extra="ignore")


class QueryRouteResult(BaseModel):
    intent: QueryIntent
    top_k: int = 10
    max_depth: int = 2
    use_graph_expansion: bool = False
    use_vector_hybrid: bool = True
    token_budget: int = 2048

    model_config = ConfigDict(extra="ignore")


class SourceRouter:
    """Routes query to Notes Plane, Memory Plane, or both (§83.1)."""

    @staticmethod
    def route(
        query: str,
        caller_scope: str = "default",
        user_specified_plane: Optional[str] = None,
    ) -> SourceRouteResult:
        q_lower = query.lower()

        if user_specified_plane is not None:
            try:
                plane = SearchTargetPlane(user_specified_plane.upper())
            except (ValueError, AttributeError) as exc:
                raise ValueError('plane must be MEMORY, NOTES or BOTH') from exc
            return SourceRouteResult(target_planes=[plane], scope_filters=[caller_scope],
                                     reason='Caller explicitly selected the search plane')

        if any(w in q_lower for w in ["note", "draft", "wrote", "raw notes"]):
            return SourceRouteResult(
                target_planes=[SearchTargetPlane.NOTES_PLANE],
                scope_filters=[caller_scope],
                reason="Query explicitly asks for personal notes",
            )
        if any(w in q_lower for w in ["decision", "official policy", "system memory", "canonical memory"]):
            return SourceRouteResult(
                target_planes=[SearchTargetPlane.MEMORY_PLANE],
                scope_filters=[caller_scope],
                reason="Query explicitly asks for canonical memory only",
            )

        # By default search both planes (§83.1)
        return SourceRouteResult(
            target_planes=[SearchTargetPlane.BOTH],
            scope_filters=[caller_scope],
            reason="Search across both Notes and Memory planes with distinct labeling",
        )


class QueryRouter:
    """Determines search execution path and budget (§83.2)."""

    @staticmethod
    def route(query: str) -> QueryRouteResult:
        q_lower = query.lower().strip()

        # Check for exact entity/ID patterns (e.g., entity-123, MB-001, node:abc)
        if re.search(r"\b(ent|dec|fact|node|mem|MB|ML)-[a-zA-Z0-9_-]+\b", query):
            return QueryRouteResult(
                intent=QueryIntent.ENTITY_FAST_PATH,
                top_k=5,
                max_depth=1,
                use_graph_expansion=False,
                use_vector_hybrid=False,
                token_budget=1024,
            )

        # Historical as-of query
        if any(w in q_lower for w in ["as of", "before", "history", "previous version", "originally"]):
            return QueryRouteResult(
                intent=QueryIntent.HISTORICAL,
                top_k=10,
                max_depth=1,
                use_graph_expansion=False,
                use_vector_hybrid=True,
                token_budget=2048,
            )

        # Relationship / multi-hop query
        if any(w in q_lower for w in ["connected to", "related to", "caused by", "depends on", "impact of", "how does"]):
            return QueryRouteResult(
                intent=QueryIntent.RELATION_MULTI_HOP,
                top_k=15,
                max_depth=3,
                use_graph_expansion=True,
                use_vector_hybrid=True,
                token_budget=3072,
            )

        # High level overview / domain summary
        if any(w in q_lower for w in ["overview", "summary", "architecture", "what is"]):
            return QueryRouteResult(
                intent=QueryIntent.OVERVIEW_TOPIC,
                top_k=8,
                max_depth=1,
                use_graph_expansion=False,
                use_vector_hybrid=True,
                token_budget=2048,
            )

        # Standard semantic hybrid
        return QueryRouteResult(
            intent=QueryIntent.SEMANTIC_QUESTION,
            top_k=10,
            max_depth=2,
            use_graph_expansion=False,
            use_vector_hybrid=True,
            token_budget=2048,
        )
