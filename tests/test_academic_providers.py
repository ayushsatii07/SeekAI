"""
Tests for Phase 2: Academic & Research Providers.

Covers:
    - SemanticScholarProvider: API → SearchResult mapping, scoring, is_available
    - ArXivProvider: Atom XML parsing, recency scoring, PDF link
    - CrossrefProvider: JSON → SearchResult mapping, JATS stripping, date filter
    - SearchMode.academic enum value
    - SearchManager._execute_academic_search: parallel dispatch + failure isolation
"""
from __future__ import annotations

import asyncio
import textwrap
import xml.etree.ElementTree as ET
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.result import SearchResult
from app.models.search import SearchFreshness, SearchMode, SearchRequest
from app.search.providers.arxiv_provider import ArXivProvider, _entry_to_result, _NS
from app.search.providers.crossref_provider import CrossrefProvider, _item_to_result
from app.search.providers.semantic_scholar import SemanticScholarProvider, _BASE_URL


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_s2_paper(**overrides) -> dict:
    base = {
        "paperId": "abc123",
        "title": "Attention Is All You Need",
        "abstract": "We propose a new simple network architecture, the Transformer.",
        "year": 2017,
        "citationCount": 50000,
        "authors": [
            {"name": "Ashish Vaswani"},
            {"name": "Noam Shazeer"},
            {"name": "Niki Parmar"},
            {"name": "Jakob Uszkoreit"},  # >3 → et al.
        ],
        "externalIds": {"DOI": "10.48550/arXiv.1706.03762"},
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
        "url": "https://www.semanticscholar.org/paper/abc123",
    }
    base.update(overrides)
    return base


def make_arxiv_xml(
    paper_id: str = "2301.00001",
    title: str = "Test Paper",
    abstract: str = "An interesting abstract.",
    authors: list[str] | None = None,
    published: str = "2023-01-01T00:00:00Z",
) -> ET.Element:
    authors = authors or ["Alice Smith", "Bob Jones"]
    author_xml = "\n".join(
        f"""<author xmlns="http://www.w3.org/2005/Atom"><name>{a}</name></author>"""
        for a in authors
    )
    xml_str = f"""<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:arxiv="http://arxiv.org/schemas/atom">
  <id>https://arxiv.org/abs/{paper_id}</id>
  <title>{title}</title>
  <summary>{abstract}</summary>
  {author_xml}
  <published>{published}</published>
  <link rel="alternate" href="https://arxiv.org/abs/{paper_id}" type="text/html"/>
  <link rel="related" href="https://arxiv.org/pdf/{paper_id}" type="application/pdf"/>
  <arxiv:primary_category term="cs.LG"/>
</entry>"""
    return ET.fromstring(xml_str)


def make_crossref_item(**overrides) -> dict:
    base = {
        "DOI": "10.1145/1234.5678",
        "URL": "https://doi.org/10.1145/1234.5678",
        "title": ["Deep Learning for NLP"],
        "author": [
            {"given": "Jane", "family": "Doe"},
            {"given": "John", "family": "Smith"},
        ],
        "published-print": {"date-parts": [[2022, 6, 15]]},
        "container-title": ["Proceedings of ACL"],
        "publisher": "ACL Anthology",
        "abstract": "<jats:p>This paper presents a new approach.</jats:p>",
        "is-referenced-by-count": 200,
    }
    base.update(overrides)
    return base


# ── SearchMode enum ─────────────────────────────────────────────────────────────

def test_search_mode_academic_exists():
    assert SearchMode.academic == "academic"
    assert SearchMode.academic in list(SearchMode)


# ── SemanticScholarProvider unit tests ─────────────────────────────────────────

class TestSemanticScholarProvider:
    def test_is_available_always_true(self):
        provider = SemanticScholarProvider()
        assert provider.is_available() is True

    def test_name(self):
        assert SemanticScholarProvider().name == "Semantic Scholar"

    def test_paper_to_result_full(self):
        paper = make_s2_paper()
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert result.title == "Attention Is All You Need"
        assert "semanticscholar.org" in result.url
        assert result.source_type == "academic:s2"
        assert 0.0 <= result.score <= 1.0
        assert result.domain != ""

    def test_paper_to_result_uses_doi_if_no_url(self):
        paper = make_s2_paper(url="", externalIds={"DOI": "10.1234/test"})
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert "doi.org/10.1234/test" in result.url

    def test_paper_to_result_uses_arxiv_if_no_url_or_doi(self):
        paper = make_s2_paper(url="", externalIds={"ArXiv": "2301.00001"})
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert "arxiv.org/abs/2301.00001" in result.url

    def test_paper_to_result_no_url_returns_none(self):
        paper = make_s2_paper(url="", externalIds={}, paperId="")
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is None

    def test_paper_to_result_no_title_returns_none(self):
        paper = make_s2_paper(title="")
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is None

    def test_et_al_in_snippet_for_many_authors(self):
        paper = make_s2_paper()
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert "et al." in result.snippet

    def test_zero_citations_gives_low_score(self):
        paper = make_s2_paper(citationCount=0)
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert result.score == 0.0

    def test_high_citations_gives_high_score(self):
        paper = make_s2_paper(citationCount=100_000)
        result = SemanticScholarProvider._paper_to_result(paper)
        assert result is not None
        assert result.score == 1.0

    def test_headers_include_api_key_when_set(self):
        provider = SemanticScholarProvider(api_key="test-key-123")
        headers = provider._build_headers()
        assert headers.get("x-api-key") == "test-key-123"

    def test_headers_no_key_when_missing(self):
        provider = SemanticScholarProvider(api_key="")
        headers = provider._build_headers()
        assert "x-api-key" not in headers

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        provider = SemanticScholarProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [make_s2_paper(), make_s2_paper(paperId="xyz", title="Another Paper")]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            results = await provider.search("transformers", max_results=5)

        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_search_filters_none_results(self):
        """Papers with no URL/title should be silently dropped."""
        provider = SemanticScholarProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                make_s2_paper(),          # valid
                {"paperId": "", "title": "", "url": ""},  # invalid → None
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            results = await provider.search("transformers")

        assert len(results) == 1


# ── ArXivProvider unit tests ───────────────────────────────────────────────────

class TestArXivProvider:
    def test_is_available_always_true(self):
        assert ArXivProvider().is_available() is True

    def test_name(self):
        assert ArXivProvider().name == "arXiv"

    def test_entry_to_result_basic(self):
        entry = make_arxiv_xml()
        result = _entry_to_result(entry)
        assert result is not None
        assert result.title == "Test Paper"
        assert "arxiv.org" in result.url
        assert result.source_type == "academic:arxiv"
        assert result.domain == "arxiv.org"

    def test_entry_to_result_pdf_in_content(self):
        entry = make_arxiv_xml(paper_id="2301.00001")
        result = _entry_to_result(entry)
        assert result is not None
        assert "[PDF]" in result.content

    def test_entry_to_result_et_al_for_many_authors(self):
        entry = make_arxiv_xml(authors=["A", "B", "C", "D"])
        result = _entry_to_result(entry)
        assert result is not None
        assert "et al." in result.snippet

    def test_entry_no_title_returns_none(self):
        xml_str = """<entry xmlns="http://www.w3.org/2005/Atom">
            <id>https://arxiv.org/abs/2301.00001</id>
            <title></title>
            <summary>abstract</summary>
            <published>2023-01-01T00:00:00Z</published>
        </entry>"""
        entry = ET.fromstring(xml_str)
        result = _entry_to_result(entry)
        assert result is None

    def test_score_bounded(self):
        entry = make_arxiv_xml(published="2024-01-01T00:00:00Z")
        result = _entry_to_result(entry)
        assert result is not None
        assert 0.0 <= result.score <= 1.0

    def test_recent_paper_higher_score_than_old(self):
        old_entry = make_arxiv_xml(published="2010-01-01T00:00:00Z")
        new_entry = make_arxiv_xml(published="2024-01-01T00:00:00Z")
        old_result = _entry_to_result(old_entry)
        new_result = _entry_to_result(new_entry)
        assert old_result is not None and new_result is not None
        assert new_result.score > old_result.score

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        provider = ArXivProvider()
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2301.00001</id>
    <title>Transformer Models</title>
    <summary>We propose transformers.</summary>
    <author><name>Alice</name></author>
    <published>2023-06-01T00:00:00Z</published>
    <link rel="alternate" href="https://arxiv.org/abs/2301.00001" type="text/html"/>
    <link rel="related" href="https://arxiv.org/pdf/2301.00001" type="application/pdf"/>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>"""
        mock_response = MagicMock()
        mock_response.text = sample_xml
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            results = await provider.search("transformers", max_results=5)

        assert len(results) == 1
        assert results[0].title == "Transformer Models"
        assert results[0].source_type == "academic:arxiv"

    @pytest.mark.asyncio
    async def test_search_handles_parse_error_gracefully(self):
        provider = ArXivProvider()
        mock_response = MagicMock()
        mock_response.text = "THIS IS NOT XML <<<>>>"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            results = await provider.search("bad xml")

        assert results == []


# ── CrossrefProvider unit tests ────────────────────────────────────────────────

class TestCrossrefProvider:
    def test_is_available_always_true(self):
        assert CrossrefProvider().is_available() is True

    def test_name(self):
        assert CrossrefProvider().name == "Crossref"

    def test_item_to_result_basic(self):
        item = make_crossref_item()
        result = _item_to_result(item)
        assert result is not None
        assert result.title == "Deep Learning for NLP"
        assert "doi.org" in result.url
        assert result.source_type == "academic:crossref"
        assert 0.0 <= result.score <= 1.0

    def test_item_jats_tags_stripped_from_abstract(self):
        item = make_crossref_item(abstract="<jats:p>Hello <jats:bold>world</jats:bold></jats:p>")
        result = _item_to_result(item)
        assert result is not None
        assert "<jats" not in result.content
        assert "Hello world" in result.content

    def test_item_no_doi_uses_url_field(self):
        item = make_crossref_item(DOI="", URL="https://example.com/paper")
        result = _item_to_result(item)
        assert result is not None
        assert result.url == "https://example.com/paper"

    def test_item_no_url_no_doi_returns_none(self):
        item = make_crossref_item(DOI="", URL="")
        result = _item_to_result(item)
        assert result is None

    def test_item_no_title_returns_none(self):
        item = make_crossref_item(title=[])
        result = _item_to_result(item)
        assert result is None

    def test_zero_citations_low_score(self):
        item = make_crossref_item(**{"is-referenced-by-count": 0})
        result = _item_to_result(item)
        assert result is not None
        assert result.score == 0.0

    def test_high_citations_high_score(self):
        item = make_crossref_item(**{"is-referenced-by-count": 1_000_000})
        result = _item_to_result(item)
        assert result is not None
        assert result.score == 1.0

    def test_headers_contain_mailto(self):
        provider = CrossrefProvider(mailto="test@example.com")
        headers = provider._build_headers()
        assert "test@example.com" in headers["User-Agent"]
        assert "SeekAI" in headers["User-Agent"]

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        provider = CrossrefProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "items": [make_crossref_item(), make_crossref_item(DOI="10.9999/other", title=["Other Paper"])]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            results = await provider.search("deep learning NLP")

        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_freshness_filter_added_to_params(self):
        """Verify that a freshness param is appended when provided."""
        provider = CrossrefProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"items": []}}
        mock_response.raise_for_status = MagicMock()

        captured_params: dict = {}

        async def fake_get(url, params=None, **kwargs):
            captured_params.update(params or {})
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = fake_get

            await provider.search("nlp", freshness=SearchFreshness.year)

        assert "filter" in captured_params
        assert "from-pub-date" in captured_params["filter"]


# ── SearchManager academic mode integration ────────────────────────────────────

class TestSearchManagerAcademicMode:
    """Tests that SearchManager routes academic mode to the right providers."""

    def _make_mock_provider(self, name: str, results: list[SearchResult]) -> MagicMock:
        provider = MagicMock()
        provider.name = name
        provider.is_available.return_value = True
        provider.search = AsyncMock(return_value=results)
        return provider

    def _make_failing_provider(self, name: str) -> MagicMock:
        provider = MagicMock()
        provider.name = name
        provider.is_available.return_value = True
        provider.search = AsyncMock(side_effect=RuntimeError("API down"))
        return provider

    def _make_search_result(self, title: str, source: str) -> SearchResult:
        return SearchResult(
            title=title,
            url=f"https://example.com/{title.replace(' ', '_')}",
            snippet=f"Snippet for {title}",
            source_type=source,
        )

    @pytest.mark.asyncio
    async def test_academic_mode_calls_all_providers(self):
        from unittest.mock import patch, AsyncMock
        from app.search.search_manager import SearchManager
        from app.models.search import SearchRequest, SearchMode

        s2_results = [self._make_search_result("S2 Paper", "academic:s2")]
        arxiv_results = [self._make_search_result("arXiv Paper", "academic:arxiv")]
        crossref_results = [self._make_search_result("Crossref Paper", "academic:crossref")]

        mock_s2 = self._make_mock_provider("Semantic Scholar", s2_results)
        mock_arxiv = self._make_mock_provider("arXiv", arxiv_results)
        mock_crossref = self._make_mock_provider("Crossref", crossref_results)

        ai_client = MagicMock()
        repo = MagicMock()
        repo.save_search.return_value = 1
        repo.save_results.return_value = None
        repo.save_answer.return_value = None
        repo.local_search.return_value = []

        web_provider = MagicMock()
        web_provider.search = AsyncMock(return_value=[])
        web_provider.is_available.return_value = False

        query_analyzer = MagicMock()
        from app.models.result import QueryAnalysis, IntentType
        analysis = QueryAnalysis(
            original_query="transformers",
            search_query="transformers attention mechanism",
            intent=IntentType.academic,
        )
        query_analyzer.analyze = AsyncMock(return_value=analysis)

        query_expander = MagicMock()
        query_expander.expand = AsyncMock(return_value=["transformers attention mechanism"])

        answer_gen = MagicMock()
        answer_gen.generate = AsyncMock(return_value="Transformers are powerful models.")

        citation_gen = MagicMock()
        citation_gen.generate_sources = MagicMock(return_value=[])

        mgr = SearchManager(
            search_provider=web_provider,
            ai_client=ai_client,
            repository=repo,
            query_analyzer=query_analyzer,
            query_expander=query_expander,
            answer_generator=answer_gen,
            citation_generator=citation_gen,
            academic_providers=[mock_s2, mock_arxiv, mock_crossref],
        )

        request = SearchRequest(
            query="transformers attention mechanism",
            mode=SearchMode.academic,
            use_ai=True,
        )

        response = await mgr.search(request)

        # All three providers were called
        mock_s2.search.assert_called_once()
        mock_arxiv.search.assert_called_once()
        mock_crossref.search.assert_called_once()

        # Response should have results from all providers
        assert response.metadata.results_retrieved == 3

    @pytest.mark.asyncio
    async def test_academic_mode_isolates_provider_failures(self):
        """A failing provider should not prevent results from healthy ones."""
        from app.search.search_manager import SearchManager
        from app.models.search import SearchRequest, SearchMode

        good_results = [self._make_search_result("Good Paper", "academic:s2")]
        mock_good = self._make_mock_provider("Semantic Scholar", good_results)
        mock_bad = self._make_failing_provider("arXiv")

        ai_client = MagicMock()
        repo = MagicMock()
        repo.save_search.return_value = 1
        repo.save_results.return_value = None
        repo.save_answer.return_value = None
        repo.local_search.return_value = []

        web_provider = MagicMock()
        web_provider.search = AsyncMock(return_value=[])

        from app.models.result import QueryAnalysis, IntentType
        analysis = QueryAnalysis(
            original_query="transformers",
            search_query="transformers",
            intent=IntentType.academic,
        )
        qa = MagicMock()
        qa.analyze = AsyncMock(return_value=analysis)
        qe = MagicMock()
        qe.expand = AsyncMock(return_value=["transformers"])
        ag = MagicMock()
        ag.generate = AsyncMock(return_value="")
        cg = MagicMock()
        cg.generate_sources = MagicMock(return_value=[])

        mgr = SearchManager(
            search_provider=web_provider,
            ai_client=ai_client,
            repository=repo,
            query_analyzer=qa,
            query_expander=qe,
            answer_generator=ag,
            citation_generator=cg,
            academic_providers=[mock_good, mock_bad],
        )

        request = SearchRequest(
            query="transformers",
            mode=SearchMode.academic,
            use_ai=False,
        )

        # Should not raise; bad provider failure is logged but swallowed
        response = await mgr.search(request)
        assert response.metadata.results_retrieved >= 1
