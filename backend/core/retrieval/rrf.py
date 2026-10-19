"""
Reciprocal Rank Fusion (RRF) Implementation.
Ref: AI Knowledge OS Backend Specification §83.4.
RRF(d) = Σ [1 / (k + rank_i(d))]
Deterministic tie breaking: rank score DESC, canonical_id ASC, revision ASC.
"""

from __future__ import annotations
from typing import Dict, List, Tuple, Any, Optional


class ReciprocalRankFusion:
    """
    Combines ranked lists from multiple retrievers (e.g. BM25, Vector, Entity Fast Path)
    without normalizing or scaling disparate raw score distributions.
    """

    def __init__(self, k: int = 60):
        self.k = k

    def fuse(
        self,
        ranked_lists: List[List[str]],
        metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
        top_k: int = 20,
    ) -> List[Tuple[str, float]]:
        """
        Takes multiple lists of document/node IDs, each sorted by their respective retriever.
        Returns combined [(doc_id, rrf_score)] sorted by score DESC, then doc_id ASC.
        """
        scores: Dict[str, float] = {}

        for rank_list in ranked_lists:
            for rank_idx, doc_id in enumerate(rank_list):
                rank = rank_idx + 1  # 1-indexed rank
                scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (self.k + rank))

        # Sort: score DESC, doc_id ASC
        sorted_candidates = sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        return sorted_candidates[:top_k]
