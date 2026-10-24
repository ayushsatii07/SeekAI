"""
Answer generator.

Uses the LLM to synthesize a grounded, cited answer from retrieved sources.
The LLM is strictly instructed to use only retrieved content — no hallucination.
"""
from __future__ import annotations

import logging
import time

from app.ai.client import AIClient
from app.config import settings
from app.models.result import SearchResult
from app.utils.text_utils import truncate_text

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an AI search assistant that synthesizes information \
from retrieved web sources.

STRICT RULES — FOLLOW EXACTLY:
1. Answer ONLY using the provided sources. Do NOT use prior knowledge.
2. If sources lack information, say "The retrieved sources do not contain \
enough information to answer this question." — do NOT invent an answer.
3. Cite every factual claim using [N] where N is the source number.
4. If sources contradict each other, explicitly note the disagreement: \
"Source [X] states A, while source [Y] states B."
5. Never fabricate URLs, titles, or citations.
6. Never cite a source number that is not listed in the provided sources.
7. Be clear about what is established fact vs. interpretation.
8. Write in plain, readable prose. Avoid bullet-point dumps.
9. End with a brief summary paragraph if the answer is long.
"""


def _build_sources_block(results: list[SearchResult]) -> str:
    """Format sources as a numbered block for the LLM prompt."""
    parts: list[str] = []
    for i, r in enumerate(results, start=1):
        content = truncate_text(
            r.content or r.snippet,
            settings.max_source_chars // len(results) + 200,
        )
        parts.append(
            f"[{i}] Title: {r.title}\n"
            f"    URL: {r.url}\n"
            f"    Content:\n{content}\n"
        )
    return "\n---\n".join(parts)


class AnswerGenerator:
    """Generates grounded, cited answers using the LLM."""

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def generate(
        self,
        query: str,
        sources: list[SearchResult],
    ) -> str:
        """
        Generate an answer grounded in the provided sources.

        Args:
            query:   The user's original question.
            sources: Top-ranked search results with extracted content.

        Returns:
            A grounded, cited answer string.
        """
        if not sources:
            return (
                "No reliable search results were found for this query.\n\n"
                "Try:\n"
                "  • using different keywords\n"
                "  • removing unnecessary words\n"
                "  • broadening the search"
            )

        t0 = time.monotonic()
        sources_block = _build_sources_block(sources)

        user_message = (
            f"Question: {query}\n\n"
            f"=== RETRIEVED SOURCES ===\n\n"
            f"{sources_block}\n\n"
            f"=========================\n\n"
            f"Based ONLY on the sources above, answer the question. "
            f"Cite each claim with [N] where N is the source number."
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        logger.info(
            "Generating answer | query=%r | sources=%d",
            query, len(sources),
        )

        try:
            answer = await self._client.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=1500,
            )
        except Exception as exc:
            logger.error("Answer generation failed: %s", exc)
            raise

        elapsed = time.monotonic() - t0
        logger.info("Answer generated in %.2fs | length=%d", elapsed, len(answer))
        return answer.strip()

    async def generate_stream(
        self,
        query: str,
        sources: list[SearchResult],
    ):
        """
        Stream an answer token-by-token.

        Yields:
            Token strings as they are produced by the LLM.
        """
        if not sources:
            yield (
                "No reliable search results were found for this query.\n\n"
                "Try:\n"
                "  • using different keywords\n"
                "  • removing unnecessary words\n"
                "  • broadening the search"
            )
            return

        sources_block = _build_sources_block(sources)
        user_message = (
            f"Question: {query}\n\n"
            f"=== RETRIEVED SOURCES ===\n\n"
            f"{sources_block}\n\n"
            f"=========================\n\n"
            f"Based ONLY on the sources above, answer the question. "
            f"Cite each claim with [N] where N is the source number."
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        logger.info(
            "Generating streaming answer | query=%r | sources=%d",
            query, len(sources),
        )

        # Check if client has chat_stream, else fallback to chat
        if hasattr(self._client, "chat_stream"):
            try:
                async for token in self._client.chat_stream(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=1500,
                ):
                    yield token
                return
            except Exception as exc:
                logger.warning("Streaming failed (%s), falling back to non-streaming chat", exc)

        # Fallback to chat if chat_stream not present or failed
        full_text = await self._client.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
        )
        # Yield in small realistic token chunks for streaming UI
        words = full_text.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
