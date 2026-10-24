"""
Reciprocal Rank Fusion (RRF) algorithm for Hybrid Search.

Combines rankings from Sparse (SQLite FTS5 BM25/keyword) and
Dense (ChromaDB Vector embeddings) search pipelines.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.models.result import SearchResult
from app.utils.url_utils import normalize_url

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    sparse_results: list[SearchResult],
    dense_results: list[SearchResult],
    k: Optional[int] = None,
    sparse_weight: Optional[float] = None,
    dense_weight: Optional[float] = None,
    limit: Optional[int] = None,
) -> list[SearchResult]:
    """
    Fuse sparse (FTS5) and dense (vector) search results using RRF.

    Score formula for document d:
        RRF(d) = (w_sparse / (k + rank_sparse)) + (w_dense / (k + rank_dense))

    Args:
        sparse_results: Ordered search results from keyword / FTS5 retrieval.
        dense_results: Ordered search results from dense vector retrieval.
        k: Smoothing constant to dampen impact of high ranks (default: 60).
        sparse_weight: Weight multiplier for sparse rankings (default: 0.5).
        dense_weight: Weight multiplier for dense rankings (default: 0.5).
        limit: Max results to return.

    Returns:
        List of fused SearchResult objects ordered by descending RRF score.
    """
    k_val = k if k is not None else settings.rrf_k
    w_sparse = sparse_weight if sparse_weight is not None else settings.rrf_fts_weight
    w_dense = dense_weight if dense_weight is not None else settings.rrf_vector_weight

    # Map normalized_key -> (rrf_score, SearchResult, sparse_rank, dense_rank)
    doc_map: dict[str, SearchResult] = {}
    rrf_scores: dict[str, float] = {}
    sparse_ranks: dict[str, int] = {}
    dense_ranks: dict[str, int] = {}

    def _key_for(res: SearchResult) -> str:
        if res.normalized_url:
            return res.normalized_url
        if res.url:
            return normalize_url(res.url)
        return f"{res.title.strip().lower()}|{res.snippet[:40].strip().lower()}"

    # 1. Process sparse ranks (1-indexed)
    for rank, item in enumerate(sparse_results, start=1):
        key = _key_for(item)
        sparse_ranks[key] = rank
        doc_map[key] = item
        score = w_sparse / (k_val + rank)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + score

    # 2. Process dense ranks (1-indexed)
    for rank, item in enumerate(dense_results, start=1):
        key = _key_for(item)
        dense_ranks[key] = rank
        score = w_dense / (k_val + rank)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + score

        if key not in doc_map:
            doc_map[key] = item
        else:
            # Document exists in both: merge richer fields
            existing = doc_map[key]
            # Use longer snippet/content if dense has more detail
            if len(item.content) > len(existing.content):
                existing.content = item.content
            if len(item.snippet) > len(existing.snippet):
                existing.snippet = item.snippet
            existing.source_type = "hybrid"

    if not rrf_scores:
        return []

    # Sort keys by RRF score descending
    sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
    if limit is not None:
        sorted_keys = sorted_keys[:limit]

    # Calculate theoretical max score for normalization
    # max possible score is document at rank 1 in both systems:
    max_theoretical = (w_sparse / (k_val + 1)) + (w_dense / (k_val + 1))
    max_observed = max(rrf_scores.values()) if rrf_scores else 1.0
    normalizer = max_theoretical if max_theoretical > 0 else max_observed

    fused_results: list[SearchResult] = []
    for key in sorted_keys:
        item = doc_map[key]
        raw_score = rrf_scores[key]
        normalized_score = min(1.0, max(0.0, raw_score / normalizer))

        # Create updated SearchResult with fused score
        source_type = "hybrid"
        if key in sparse_ranks and key not in dense_ranks:
            source_type = "local_fts"
        elif key in dense_ranks and key not in sparse_ranks:
            source_type = "local_vector"

        updated = SearchResult(
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            content=item.content,
            score=round(normalized_score, 4),
            domain=item.domain,
            source_type=source_type,
            normalized_url=item.normalized_url or normalize_url(item.url),
            search_score=round(raw_score, 5),
            final_score=round(normalized_score, 4),
        )
        fused_results.append(updated)

    logger.debug(
        "RRF fused %d sparse + %d dense results into %d unique items",
        len(sparse_results),
        len(dense_results),
        len(fused_results),
    )
    return fused_results
