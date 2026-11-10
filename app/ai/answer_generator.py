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

import re

_SYSTEM_PROMPT = """You are SeekAI, an elite AI search engine delivering concise, beautifully formatted, easy-to-read answers grounded strictly in retrieved sources.

STRICT FORMATTING & SYNTHESIS RULES:
1. Grounding: Answer ONLY using the provided sources. Do NOT use outside knowledge or hallucinate.
2. Structure & Readability:
   - Keep answers clear, structured, and easy to scan. NEVER output a giant wall of unbroken text.
   - Break your response into short paragraphs (2–3 sentences each) separated by blank lines.
   - Use bullet points (•) for key takeaways, events, or facts when summarizing complex topics.
3. Citations:
   - Cite every factual claim using clean numeric brackets [N] immediately after the claim, where N is the source number (e.g. "Nvidia launched the new architecture [1].").
   - STRICTLY PROHIBITED: NEVER use symbols like 【1†L1-L4】, 【1†source】, or dagger (†) symbols. Use ONLY standard square brackets like [1], [2].
   - If multiple sources support a claim, write [1] [2].
4. Conciseness:
   - Be direct, informative, and concise. Avoid repetitive filler phrases.
   - If sources lack enough information, clearly state: "The retrieved sources do not contain enough information to answer this question."
"""


def _clean_citations(text: str) -> str:
    """Normalize any unicode or dagger citation markers to clean [N]."""
    text = re.sub(r"[【\[](\d+)[†:][^】\]]*[】\]]", r"[\1]", text)
    text = re.sub(r"【(\d+)】", r"[\1]", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\]\s*\[", r"] [", text)
    return text


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
        answer = _clean_citations(answer.strip())
        logger.info("Answer generated in %.2fs | length=%d", elapsed, len(answer))
        return answer

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
