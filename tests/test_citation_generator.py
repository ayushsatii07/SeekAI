"""
Tests for CitationGenerator.
"""
import pytest

from app.ai.citation_generator import CitationGenerator
from app.models.result import SearchResult


def _make_result(url: str, title: str, score: float = 0.8) -> SearchResult:
    return SearchResult(
        url=url,
        title=title,
        snippet="Sample snippet text",
        score=score,
        domain="example.com",
    )


class TestCitationGenerator:
    def setup_method(self):
        self.generator = CitationGenerator()
        self.results = [
            _make_result("https://ibm.com/quantum", "IBM Quantum"),
            _make_result("https://microsoft.com/quantum", "Microsoft Quantum"),
            _make_result("https://google.com/quantum", "Google Quantum"),
        ]

    def test_extracts_cited_sources(self):
        answer = "Quantum computing uses qubits [1]. Topological qubits are researched elsewhere [2]."
        sources = self.generator.generate_sources(answer, self.results)
        assert len(sources) == 2
        assert sources[0].citation_number == 1
        assert sources[0].title == "IBM Quantum"
        assert sources[1].citation_number == 2
        assert sources[1].title == "Microsoft Quantum"

    def test_ignores_out_of_bounds_citation_numbers(self):
        answer = "Claim with invalid source [99] and valid source [1]."
        sources = self.generator.generate_sources(answer, self.results)
        assert len(sources) == 1
        assert sources[0].citation_number == 1

    def test_fallback_when_no_citations_present(self):
        answer = "Answer with no citation tags whatsoever."
        sources = self.generator.generate_sources(answer, self.results)
        # Fallback should return all passed results
        assert len(sources) == 3

    def test_format_sources_block(self):
        answer = "Fact from source [1]."
        sources = self.generator.generate_sources(answer, self.results)
        block = self.generator.format_sources_block(sources)
        assert "[1] IBM Quantum" in block
        assert "https://ibm.com/quantum" in block
