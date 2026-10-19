"""
BM25 Scorer for Scoped Keyword Matching.
Ref: AI Knowledge OS Backend Specification §§83.3, 83.4.
Deterministic, pure Python implementation for exact and keyword lexical relevance.
"""

from __future__ import annotations
import math
import re
from typing import Dict, List, Tuple


class BM25Scorer:
    """
    Standard Okapi BM25 implementation.
    Parameters:
      k1: term frequency saturation (default 1.5)
      b: length normalization (default 0.75)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: List[str] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_freqs: Dict[str, int] = {}
        self.inverted_index: Dict[str, Dict[str, int]] = {}
        self.num_docs: int = 0

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Latin words plus CJK bigrams, without an external tokenizer."""
        tokens = []
        for word in re.findall(r'[\u3400-\u9fff]+|[^\W\u3400-\u9fff]+', text.lower()):
            if '\u3400' <= word[0] <= '\u9fff':
                tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
                if len(word) == 1:
                    tokens.append(word)
            else:
                tokens.append(word)
        return tokens

    def fit(self, corpus: Dict[str, str]) -> None:
        """Index a dictionary of {doc_id: text_content}."""
        self.doc_ids = list(corpus.keys())
        self.num_docs = len(self.doc_ids)
        self.doc_lengths = []
        self.doc_freqs = {}
        self.inverted_index = {}

        total_length = 0
        for doc_id, text in corpus.items():
            tokens = self.tokenize(text)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_length += doc_len

            term_counts: Dict[str, int] = {}
            for t in tokens:
                term_counts[t] = term_counts.get(t, 0) + 1

            for term, count in term_counts.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = {}
                    self.doc_freqs[term] = 0
                self.inverted_index[term][doc_id] = count
                self.doc_freqs[term] += 1

        self.avg_doc_len = total_length / self.num_docs if self.num_docs > 0 else 0.0

    def score(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """Score documents against query, returning [(doc_id, score), ...] sorted descending."""
        if self.num_docs == 0:
            return []

        query_tokens = self.tokenize(query)
        scores: Dict[str, float] = {doc_id: 0.0 for doc_id in self.doc_ids}

        for term in query_tokens:
            if term not in self.doc_freqs:
                continue

            df = self.doc_freqs[term]
            # IDF with standard smoothing
            idf = math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0)

            term_posting = self.inverted_index[term]
            for doc_idx, doc_id in enumerate(self.doc_ids):
                tf = term_posting.get(doc_id, 0)
                if tf == 0:
                    continue
                doc_len = self.doc_lengths[doc_idx]
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len if self.avg_doc_len > 0 else 1.0))
                scores[doc_id] += idf * (tf * (self.k1 + 1.0)) / denom

        sorted_results = sorted(scores.items(), key=lambda x: (x[1], x[0]), reverse=True)
        # Filter zero scores if we have non-zero scores
        filtered = [item for item in sorted_results if item[1] > 0.0]
        return filtered[:top_k]
