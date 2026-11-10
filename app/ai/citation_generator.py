"""
Citation generator.

Converts numbered references in the LLM answer ([1], [2], etc.) to
Source objects that map back to real retrieved results.
"""
from __future__ import annotations

import re

from app.models.answer import Source
from app.models.result import SearchResult


class CitationGenerator:
    """
    Extracts and validates citations from a generated answer.

    Ensures that every [N] reference in the answer corresponds to a
    real source that was actually retrieved — no invented citations.
    """

    def generate_sources(
        self,
        answer: str,
        results: list[SearchResult],
    ) -> list[Source]:
        """
        Build a list of Source objects for citations found in the answer.

        Only sources whose citation number [N] appears in the answer text
        are included. This prevents citing sources that weren't relevant.

        Args:
            answer:  The LLM-generated answer text (may contain [1], [2]...).
            results: The ranked search results passed to the LLM.

        Returns:
            List of Source objects, ordered by citation_number.
        """
        # Normalize any unicode/dagger citations like 【1†L5-L9】 to standard [1]
        cleaned_answer = re.sub(r"[【\[](\d+)[†:][^】\]]*[】\]]", r"[\1]", answer)
        cleaned_answer = re.sub(r"【(\d+)】", r"[\1]", cleaned_answer)

        # Find all citation numbers referenced in the answer
        cited_numbers = set(
            int(m) for m in re.findall(r"\[(\d+)\]", cleaned_answer)
        )

        sources: list[Source] = []
        for num in sorted(cited_numbers):
            idx = num - 1  # 1-indexed to 0-indexed
            if 0 <= idx < len(results):
                r = results[idx]
                sources.append(
                    Source(
                        id=num,
                        title=r.title or r.domain or r.url,
                        url=r.url,
                        domain=r.domain,
                        citation_number=num,
                        relevance_score=r.final_score,
                        snippet=r.snippet[:200],
                        image_url=getattr(r, "image_url", ""),
                    )
                )

        # If the answer has no citations but has sources, include all sources
        # (LLM may have forgotten to cite in some edge cases)
        if not sources and results:
            for i, r in enumerate(results, start=1):
                sources.append(
                    Source(
                        id=i,
                        title=r.title or r.domain or r.url,
                        url=r.url,
                        domain=r.domain,
                        citation_number=i,
                        relevance_score=r.final_score,
                        snippet=r.snippet[:200],
                        image_url=getattr(r, "image_url", ""),
                    )
                )

        return sources

    def format_sources_block(self, sources: list[Source]) -> str:
        """Format sources for CLI display."""
        if not sources:
            return "No sources available."

        lines: list[str] = []
        for src in sources:
            lines.append(f"[{src.citation_number}] {src.title}")
            lines.append(f"    {src.url}")
            lines.append("")

        return "\n".join(lines)
