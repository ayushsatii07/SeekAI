"""
Tests for ContentExtractor.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.models.result import SearchResult
from app.search.content_extractor import ContentExtractor


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Page</title><style>body { color: red; }</style></head>
<body>
    <header><nav><a href="/">Home</a></nav></header>
    <main>
        <h1>Main Article Title</h1>
        <p>This is the first paragraph with informative content about quantum computers.</p>
        <p>This is the second paragraph discussing superconducting qubits and coherence time.</p>
    </main>
    <footer><p>&copy; 2026 Example Corp. All rights reserved.</p></footer>
    <script>console.log("tracking");</script>
</body>
</html>
"""


class TestContentExtractor:
    def setup_method(self):
        self.extractor = ContentExtractor(timeout=5)

    def test_parse_html_removes_noise_tags(self):
        text = self.extractor._parse_html(SAMPLE_HTML)
        assert text is not None
        assert "Main Article Title" in text
        assert "quantum computers" in text
        assert "superconducting qubits" in text
        # Noise tags should be eliminated
        assert "console.log" not in text
        assert "color: red" not in text
        assert "&copy;" not in text

    def test_parse_html_minimal_content_returns_none(self):
        tiny_html = "<html><body><p>Too short</p></body></html>"
        text = self.extractor._parse_html(tiny_html)
        assert text is None

    @pytest.mark.asyncio
    async def test_extract_skips_when_content_already_populated(self):
        result = SearchResult(
            url="https://example.com/item",
            title="Item",
            content="A" * 300,
            snippet="short",
        )
        # Should not fetch
        enriched = await self.extractor.extract(result)
        assert enriched.content.startswith("A" * 100)

    @pytest.mark.asyncio
    async def test_extract_many_concurrency(self):
        results = [
            SearchResult(url=f"https://example.com/{i}", title=f"Title {i}", snippet=f"Snippet {i}", content="Existing content " * 20)
            for i in range(5)
        ]
        enriched = await self.extractor.extract_many(results, concurrency=2)
        assert len(enriched) == 5
