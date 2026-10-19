"""
Context Compiler & Deterministic Serialization Engine.
Ref: AI Knowledge OS Backend Specification §§22, 23, 37, 85.
Assembles:
1. Stable Context (System boundaries, canonical principles, unchanging definitions)
2. Dynamic Context (Session, query, prioritized evidence packet items)
Under strict linear token budgeting and deterministic ordering.
"""

from __future__ import annotations
import hashlib
import math
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr
from backend.core.evidence.evidence_packet import EvidencePacket, EvidenceItem
from backend.schema.models import utc_now


class ContextArtifact(BaseModel):
    # Exact generator-selected items, not references parsed from untrusted text.
    # Private data is never serialized into the public context artifact.
    _included_evidence_items: List[EvidenceItem] = PrivateAttr(default_factory=list)
    id: str
    stable_context: str
    dynamic_context: str
    combined_prompt: str
    token_budget: int
    estimated_tokens: int
    is_truncated: bool = False
    token_count_method: str = 'ceil_characters_div_4'
    budget_unit: str = 'estimated_tokens'
    retrieval: Dict[str, Any] = Field(default_factory=dict)
    context_hash: str
    created_at: Optional[str] = Field(default_factory=lambda: utc_now().isoformat())
    lean: bool = False

    model_config = ConfigDict(extra="ignore")

    def to_lean_dict(self) -> Dict[str, Any]:
        """
        Ultra-compact serialization for MCP and LLM consumption.
        Strips duplicated text fields (saving ~50-60% tokens) and volatile timestamps
        to maximize Prompt Cache Hit rate across model invocations.
        """
        retrieval_lean = {
            k: v for k, v in self.retrieval.items()
            if k in ('evidence_refs', 'coverage', 'scope', 'plane', 'vector_search')
        }
        return {
            "id": self.id,
            "combined_prompt": self.combined_prompt,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "is_truncated": self.is_truncated,
            "context_hash": self.context_hash,
            "lean": True,
            "retrieval": retrieval_lean,
        }


class ContextCompiler:
    """
    Compiles deterministic ContextArtifacts for LLM consumption.
    Deterministic estimated-token budget; callers must use their model tokenizer
    for an actual model token limit.
    """

    DEFAULT_STABLE_PREFIX = (
        "SYSTEM DIRECTIVE: You are an agent operating on the AI Knowledge OS.\n"
        "RULES:\n"
        "1. Prioritize EXPLICIT decisions and verified L5 canonical knowledge.\n"
        "2. Treat INFERRED facts as hypotheses, not verified truths.\n"
        "3. Explicitly acknowledge any conflict nodes or contradictory evidence.\n"
    )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    @classmethod
    def compile(
        cls,
        evidence_packet: EvidencePacket,
        token_budget: int = 2048,
        custom_stable_prefix: Optional[str] = None,
        lean: bool = False,
        cache_prefix_layout: bool = True,
    ) -> ContextArtifact:
        if token_budget < 1:
            raise ValueError('token_budget must be positive')
        stable_text = (custom_stable_prefix or cls.DEFAULT_STABLE_PREFIX).strip()
        # Sort dynamic evidence deterministically:
        # Priority order: EXPLICIT > OBSERVED > INFERRED, then L5 down to L0, then id ASC
        authority_weight = {"EXPLICIT": 3, "OBSERVED": 2, "INFERRED": 1}
        maturity_weight = {"L5": 5, "L4": 4, "L3": 3, "L2": 2, "L1": 1, "L0": 0}

        def item_sort_key(item: EvidenceItem):
            return (
                -authority_weight.get(item.authority, 0),
                -maturity_weight.get(item.maturity, 0),
                item.id,
            )

        sorted_items = sorted(evidence_packet.items, key=item_sort_key)
        included_items = []
        is_truncated = False
        marker = '[CONTEXT_TRUNCATED]'

        if cache_prefix_layout:
            # Cache-friendly layout: Put deterministic EVIDENCE items BEFORE dynamic QUERY.
            # This freezes the token prefix so that different queries hitting the same knowledge
            # achieve high prompt cache hit rates across LLM invocations (Gemini / Claude / DeepSeek).
            query_line = f"QUERY: {evidence_packet.query}"
            evidence_lines = ["EVIDENCE:"]

            def assemble(ev_lines):
                return f"{stable_text}\n\n" + '\n'.join(ev_lines) + f"\n\n{query_line}"

            for item in sorted_items:
                conflict_tag = f" [CONFLICT: {item.conflict_note}]" if item.is_conflicted else ""
                lifecycle = f" [Conflict status: {item.lifecycle_status}]" if item.lifecycle_status else ""
                line = (
                    f"- [{item.authority}/{item.maturity}]{lifecycle} {item.title}: {item.content}"
                    f" (Source: {item.source_id or 'none'} rev {item.source_revision or 'unknown'}){conflict_tag}"
                )
                if cls.estimate_tokens(assemble([*evidence_lines, line])) > token_budget:
                    is_truncated = True
                    break

                evidence_lines.append(line)
                included_items.append(item)

            if is_truncated and cls.estimate_tokens(assemble([*evidence_lines, marker])) <= token_budget:
                evidence_lines.append(marker)

            dynamic_text = "\n".join(evidence_lines) + f"\n\n{query_line}"
            combined = f"{stable_text}\n\n{dynamic_text}"

            if cls.estimate_tokens(combined) > token_budget:
                is_truncated = True
                combined = marker if cls.estimate_tokens(marker) <= token_budget else ''
                stable_text, dynamic_text = combined, ''
        else:
            # Legacy layout: QUERY precedes EVIDENCE
            dynamic_lines = [f"QUERY: {evidence_packet.query}\nEVIDENCE:"]

            def assemble_legacy(lines):
                return stable_text + '\n\n' + '\n'.join(lines)

            for item in sorted_items:
                conflict_tag = f" [CONFLICT: {item.conflict_note}]" if item.is_conflicted else ""
                lifecycle = f" [Conflict status: {item.lifecycle_status}]" if item.lifecycle_status else ""
                line = (
                    f"- [{item.authority}/{item.maturity}]{lifecycle} {item.title}: {item.content}"
                    f" (Source: {item.source_id or 'none'} rev {item.source_revision or 'unknown'}){conflict_tag}"
                )
                if cls.estimate_tokens(assemble_legacy([*dynamic_lines, line])) > token_budget:
                    is_truncated = True
                    break

                dynamic_lines.append(line)
                included_items.append(item)

            if is_truncated and cls.estimate_tokens(assemble_legacy([*dynamic_lines, marker])) <= token_budget:
                dynamic_lines.append(marker)

            dynamic_text = "\n".join(dynamic_lines)
            combined = f"{stable_text}\n\n{dynamic_text}"

            if cls.estimate_tokens(combined) > token_budget:
                is_truncated = True
                combined = marker if cls.estimate_tokens(marker) <= token_budget else ''
                stable_text, dynamic_text = combined, ''

        context_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        artifact_id = f"ctx-{context_hash[:16]}"
        created_at_val = None if lean else utc_now().isoformat()

        artifact = ContextArtifact(
            id=artifact_id,
            stable_context="" if lean else stable_text,
            dynamic_context="" if lean else dynamic_text,
            combined_prompt=combined,
            token_budget=token_budget,
            estimated_tokens=cls.estimate_tokens(combined),
            is_truncated=is_truncated,
            context_hash=context_hash,
            created_at=created_at_val,
            lean=lean,
        )
        artifact._included_evidence_items = included_items
        return artifact
