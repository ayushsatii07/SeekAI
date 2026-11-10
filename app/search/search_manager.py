"""
Search Manager — orchestrates the full search pipeline.

Pipeline:
    User Query
    → Query Analysis (LLM)
    → Query Expansion (LLM)
    → Web/Local/Hybrid Search
    → Result Aggregation
    → Deduplication
    → Relevance Ranking
    → Content Extraction
    → Context Construction
    → LLM Answer Generation
    → Citation Generation
    → Persistence (SQLite)
    → Final Answer
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.ai.answer_generator import AnswerGenerator
from app.ai.citation_generator import CitationGenerator
from app.ai.client import AIClient
from app.ai.query_analyzer import QueryAnalyzer
from app.config import settings
from app.database.repository import SearchRepository
from app.models.answer import AnswerResponse, SearchMetadata, Source
from app.models.result import QueryAnalysis, SearchResult
from app.models.search import SearchFreshness, SearchMode, SearchRequest
from app.search.base import SearchProvider
from app.search.content_extractor import ContentExtractor
from app.search.deduplicator import Deduplicator
from app.search.query_expander import QueryExpander
from app.search.ranker import Ranker

from app.search.vector.hybrid_rrf import reciprocal_rank_fusion
from app.search.vector.store import ChromaVectorStore, VectorDocument
from app.search.providers import (
    ArXivProvider,
    CrossrefProvider,
    SemanticScholarProvider,
)

logger = logging.getLogger(__name__)


class SearchManager:
    """
    Coordinates all pipeline components.

    Dependencies are injected; every component is independently testable.
    """

    def __init__(
        self,
        search_provider: SearchProvider,
        ai_client: AIClient,
        repository: SearchRepository,
        query_analyzer: Optional[QueryAnalyzer] = None,
        query_expander: Optional[QueryExpander] = None,
        deduplicator: Optional[Deduplicator] = None,
        ranker: Optional[Ranker] = None,
        content_extractor: Optional[ContentExtractor] = None,
        answer_generator: Optional[AnswerGenerator] = None,
        citation_generator: Optional[CitationGenerator] = None,
        vector_store: Optional[ChromaVectorStore] = None,
        academic_providers: Optional[list[SearchProvider]] = None,
    ) -> None:
        self._provider = search_provider
        self._ai = ai_client
        self._repo = repository
        self._vector_store = vector_store

        # Academic providers — defaults to all three if not injected
        self._academic_providers: list[SearchProvider] = academic_providers or [
            SemanticScholarProvider(),
            ArXivProvider(),
            CrossrefProvider(),
        ]

        self._analyzer = query_analyzer or QueryAnalyzer(ai_client)
        self._expander = query_expander or QueryExpander(ai_client)
        self._dedup = deduplicator or Deduplicator()
        self._ranker = ranker or Ranker()
        self._extractor = content_extractor or ContentExtractor()
        self._answer_gen = answer_generator or AnswerGenerator(ai_client)
        self._citation_gen = citation_generator or CitationGenerator()

    @property
    def vector_store(self) -> Optional[ChromaVectorStore]:
        """Lazy access or initialized vector store."""
        if self._vector_store is None:
            try:
                self._vector_store = ChromaVectorStore()
            except Exception as exc:
                logger.debug("ChromaVectorStore not initialized: %s", exc)
        return self._vector_store

    # ─── Public API ─────────────────────────────────────────────────────────

    async def search(self, request: SearchRequest) -> AnswerResponse:
        """
        Execute the full search pipeline and return an AnswerResponse.

        Args:
            request: Validated search request.

        Returns:
            AnswerResponse with answer, citations, sources, and metadata.
        """
        t_total = time.monotonic()
        meta = SearchMetadata(mode=request.mode)

        # ── 1. Query Analysis ──────────────────────────────────────────────
        t0 = time.monotonic()
        analysis = await self._analyzer.analyze(request.query)
        meta.query_analysis_time = round(time.monotonic() - t0, 3)
        logger.info("Intent: %s | search_query: %r", analysis.intent, analysis.search_query)

        # ── 2. Query Expansion ─────────────────────────────────────────────
        expanded_queries = await self._expander.expand(request.query, analysis)
        meta.queries_generated = len(expanded_queries)
        logger.info("Expanded to %d queries", len(expanded_queries))

        # ── 3. Search (web / local / hybrid) ──────────────────────────────
        t0 = time.monotonic()
        all_results = await self._execute_search(
            request, analysis, expanded_queries
        )
        meta.search_api_time = round(time.monotonic() - t0, 3)
        meta.results_retrieved = len(all_results)
        logger.info("Retrieved %d raw results", len(all_results))

        if not all_results:
            return self._empty_response(request.query, analysis, meta)

        # ── 4. Deduplication ──────────────────────────────────────────────
        unique_results = self._dedup.deduplicate(all_results)
        meta.results_after_deduplication = len(unique_results)

        # ── 5. Ranking ────────────────────────────────────────────────────
        ranked = self._ranker.rank(unique_results, analysis.search_query)

        # Take top N for LLM (configurable)
        top_results = ranked[: settings.max_llm_sources]

        # ── 6. Content Extraction ─────────────────────────────────────────
        if request.use_ai:
            t0 = time.monotonic()
            top_results = await self._extractor.extract_many(top_results)
            meta.extraction_time = round(time.monotonic() - t0, 3)

        meta.sources_used = len(top_results)

        # ── 7. Answer Generation ──────────────────────────────────────────
        answer_text = ""
        sources: list[Source] = []

        if request.use_ai:
            t0 = time.monotonic()
            answer_text = await self._answer_gen.generate(
                request.query, top_results
            )
            meta.llm_time = round(time.monotonic() - t0, 3)
            sources = self._citation_gen.generate_sources(answer_text, top_results)

        # ── 8. Persist ────────────────────────────────────────────────────
        meta.response_time = round(time.monotonic() - t_total, 3)
        search_id = None
        try:
            search_id = self._repo.save_search(
                query=request.query,
                analysis=analysis,
                result_count=len(all_results),
                response_time=meta.response_time,
                mode=request.mode,
            )
            self._repo.save_results(search_id, ranked[:30])
            if self.vector_store:
                try:
                    import uuid
                    docs = [
                        VectorDocument(
                            id=str(uuid.uuid5(uuid.NAMESPACE_URL, r.url or f"search_{search_id}_{i}")),
                            content=f"{r.title}\n{r.snippet}\n{r.content[:1500] if r.content else ''}".strip(),
                            metadata={
                                "title": r.title,
                                "url": r.url,
                                "domain": r.domain,
                                "search_id": str(search_id or 0),
                            },
                        )
                        for i, r in enumerate(ranked[:20])
                        if (r.title or r.snippet or r.content)
                    ]
                    await self.vector_store.add_documents(docs)
                except Exception as exc:
                    logger.debug("Vector store document indexing failed: %s", exc)

            if request.use_ai and answer_text:
                self._repo.save_answer(
                    search_id, answer_text, self._ai.model, sources
                )
        except Exception as exc:
            logger.error("Persistence failed: %s", exc)

        return AnswerResponse(
            query=request.query,
            answer=answer_text,
            sources=sources,
            images=getattr(self._provider, "last_images", []),
            metadata=meta,
            search_id=search_id,
        )

    async def stream_search(self, request: SearchRequest):
        """
        Execute the search pipeline and yield SSE event dicts:
        - status: progress stages
        - sources: top retrieved sources
        - token: streamed answer tokens
        - done: final AnswerResponse
        """
        t_total = time.monotonic()
        meta = SearchMetadata(mode=request.mode)

        yield {"event": "status", "data": {"stage": "analyzing", "message": "Analyzing search intent…"}}
        t0 = time.monotonic()
        analysis = await self._analyzer.analyze(request.query)
        meta.query_analysis_time = round(time.monotonic() - t0, 3)

        yield {"event": "status", "data": {"stage": "expanding", "message": "Expanding search queries…", "queries": [analysis.search_query]}}
        expanded_queries = await self._expander.expand(request.query, analysis)
        meta.queries_generated = len(expanded_queries)

        yield {"event": "status", "data": {"stage": "searching", "message": f"Searching in {request.mode.value} mode…", "queries": expanded_queries}}
        t0 = time.monotonic()
        all_results = await self._execute_search(request, analysis, expanded_queries)
        meta.search_api_time = round(time.monotonic() - t0, 3)
        meta.results_retrieved = len(all_results)

        if not all_results:
            empty = self._empty_response(request.query, analysis, meta)
            yield {"event": "done", "data": empty.model_dump()}
            return

        yield {"event": "status", "data": {"stage": "ranking", "message": "Deduplicating & ranking sources…"}}
        unique_results = self._dedup.deduplicate(all_results)
        meta.results_after_deduplication = len(unique_results)
        ranked = self._ranker.rank(unique_results, analysis.search_query)
        top_results = ranked[: settings.max_llm_sources]

        if request.use_ai:
            yield {"event": "status", "data": {"stage": "extracting", "message": f"Extracting deep content from {len(top_results)} sources…"}}
            t0 = time.monotonic()
            top_results = await self._extractor.extract_many(top_results)
            meta.extraction_time = round(time.monotonic() - t0, 3)

        meta.sources_used = len(top_results)

        sources_payload = [
            {
                "id": i + 1,
                "title": r.title,
                "url": r.url,
                "domain": r.domain,
                "snippet": r.snippet[:250],
                "score": round(r.score, 3),
                "image_url": getattr(r, "image_url", ""),
            }
            for i, r in enumerate(top_results)
        ]
        yield {"event": "sources", "data": sources_payload}

        # Emit search visual images if available from search provider
        found_images = getattr(self._provider, "last_images", [])
        if found_images:
            yield {"event": "images", "data": found_images}

        answer_text = ""
        sources: list[Source] = []

        if request.use_ai:
            yield {"event": "status", "data": {"stage": "synthesizing", "message": "Synthesizing answer with citations…"}}
            t0 = time.monotonic()
            async for token in self._answer_gen.generate_stream(request.query, top_results):
                answer_text += token
                yield {"event": "token", "data": {"delta": token}}
            meta.llm_time = round(time.monotonic() - t0, 3)
            sources = self._citation_gen.generate_sources(answer_text, top_results)

        meta.response_time = round(time.monotonic() - t_total, 3)
        search_id = None
        try:
            search_id = self._repo.save_search(
                query=request.query,
                analysis=analysis,
                result_count=len(all_results),
                response_time=meta.response_time,
                mode=request.mode,
            )
            self._repo.save_results(search_id, ranked[:30])
            if request.use_ai and answer_text:
                self._repo.save_answer(search_id, answer_text, self._ai.model, sources)
        except Exception as exc:
            logger.error("Persistence failed: %s", exc)

        final_resp = AnswerResponse(
            query=request.query,
            answer=answer_text,
            sources=sources,
            images=getattr(self._provider, "last_images", []),
            metadata=meta,
            search_id=search_id,
        )
        yield {"event": "done", "data": final_resp.model_dump()}

    # ─── Internal helpers ────────────────────────────────────────────────────

    async def _execute_search(
        self,
        request: SearchRequest,
        analysis: QueryAnalysis,
        queries: list[str],
    ) -> list[SearchResult]:
        """Run web search, local FTS5, vector search, or hybrid RRF based on mode."""
        all_results: list[SearchResult] = []

        if request.mode in (SearchMode.web, SearchMode.hybrid):
            per_query = max(5, request.max_results // len(queries))
            freshness = request.freshness
            if analysis.is_time_sensitive and freshness is None:
                freshness = SearchFreshness.week

            tasks = [
                self._provider.search(
                    query=q,
                    max_results=per_query,
                    domains=request.domains,
                    freshness=freshness,
                )
                for q in queries
            ]
            results_per_query = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results_per_query:
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, Exception):
                    logger.warning("Search task failed: %s", result)

        if request.mode in (SearchMode.local, SearchMode.hybrid):
            sparse_results = self._local_search_to_results(request.query)
            dense_results = await self._vector_search_to_results(request.query)

            if sparse_results and dense_results:
                fused = reciprocal_rank_fusion(sparse_results, dense_results)
                all_results.extend(fused)
            elif sparse_results or dense_results:
                all_results.extend(sparse_results or dense_results)

        if request.mode == SearchMode.academic:
            await self._execute_academic_search(
                all_results,
                request.query,
                queries,
                max_results=request.max_results,
                freshness=request.freshness,
            )

        if request.mode == SearchMode.news:
            news_hits = self._news_search_to_results(request.query, limit=request.max_results)
            all_results.extend(news_hits)
            # Supplement with web search if few local results and provider is available
            if len(all_results) < 5 and self._provider:
                try:
                    web_news = await self._provider.search(
                        query=request.query,
                        max_results=request.max_results,
                        freshness=SearchFreshness.day,
                    )
                    all_results.extend(web_news)
                except Exception as exc:
                    logger.warning("Web fallback news search failed: %s", exc)

        return all_results

    async def _execute_academic_search(
        self,
        collector: list[SearchResult],
        primary_query: str,
        expanded_queries: list[str],
        max_results: int = 10,
        freshness: Optional[SearchFreshness] = None,
    ) -> None:
        """
        Run all available academic providers in parallel for each expanded query.

        Results are gathered with return_exceptions=True so a single provider
        failure does not abort the whole search.  Deduplication happens later
        in the main pipeline.
        """
        # Use first expanded query (usually closest to original intent) for academic
        query = expanded_queries[0] if expanded_queries else primary_query
        per_provider = max(5, max_results // max(len(self._academic_providers), 1))

        tasks = [
            provider.search(
                query=query,
                max_results=per_provider,
                freshness=freshness,
            )
            for provider in self._academic_providers
            if provider.is_available()
        ]

        if not tasks:
            logger.warning("No academic providers are available.")
            return

        batch = await asyncio.gather(*tasks, return_exceptions=True)
        for provider, outcome in zip(self._academic_providers, batch):
            if isinstance(outcome, list):
                logger.info("%s returned %d results", provider.name, len(outcome))
                collector.extend(outcome)
            else:
                logger.warning("%s search failed: %s", provider.name, outcome)

    def _local_search_to_results(self, query: str) -> list[SearchResult]:
        """Convert FTS5 local search rows to SearchResult objects."""
        from app.utils.url_utils import normalize_url, extract_domain
        rows = self._repo.local_search(query, limit=10)
        results: list[SearchResult] = []
        for row in rows:
            url = row.get("url", "")
            results.append(
                SearchResult(
                    title=row.get("title", ""),
                    url=url,
                    snippet=row.get("excerpt", ""),
                    content=row.get("excerpt", ""),
                    score=0.5,
                    domain=row.get("domain", "") or extract_domain(url),
                    source_type="local_fts",
                    normalized_url=normalize_url(url),
                )
            )
        return results

    async def _vector_search_to_results(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Convert Chroma vector search results to SearchResult objects."""
        vs = self.vector_store
        if not vs:
            return []
        try:
            from app.utils.url_utils import normalize_url, extract_domain
            vec_hits = await vs.search(query, limit=limit)
            results: list[SearchResult] = []
            for hit in vec_hits:
                meta = hit.metadata
                url = meta.get("url", "")
                title = meta.get("title", "") or (hit.content.split("\n")[0] if hit.content else "Document")
                snippet = hit.content[:300] if hit.content else ""
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        content=hit.content,
                        score=hit.score,
                        domain=meta.get("domain", "") or extract_domain(url),
                        source_type="local_vector",
                        normalized_url=normalize_url(url),
                    )
                )
            return results
        except Exception as exc:
            logger.debug("Vector search query failed: %s", exc)
            return []

    def _news_search_to_results(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Convert FTS5 crawled news articles to SearchResult objects."""
        from app.crawler.repository import NewsRepository
        from app.utils.url_utils import extract_domain, normalize_url
        try:
            repo = NewsRepository(self._repo._db)
            articles = repo.search_news(query, limit=limit)
            results: list[SearchResult] = []
            for art in articles:
                url = art.url
                results.append(
                    SearchResult(
                        title=art.title,
                        url=url,
                        snippet=art.summary or art.content[:300],
                        content=art.content or art.summary,
                        score=0.9,
                        domain=art.domain or extract_domain(url),
                        source_type="breaking_news",
                        normalized_url=normalize_url(url),
                        published_date=art.published_at,
                    )
                )
            return results
        except Exception as exc:
            logger.warning("News search failed: %s", exc)
            return []

    def _empty_response(
        self, query: str, analysis: QueryAnalysis, meta: SearchMetadata
    ) -> AnswerResponse:
        """Return a friendly response when no results are found."""
        answer = (
            "No reliable search results were found for this query.\n\n"
            "Try:\n"
            "  • using different keywords\n"
            "  • removing unnecessary words\n"
            "  • broadening the search"
        )
        return AnswerResponse(
            query=query,
            answer=answer,
            sources=[],
            metadata=meta,
        )
