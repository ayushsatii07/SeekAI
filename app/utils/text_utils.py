"""
Text processing utilities for SeekAI.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_text(text: str, max_chars: int, suffix: str = "…") -> str:
    """Truncate text to max_chars, appending suffix if truncated."""
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars - len(suffix)]
    # Try to break at a sentence boundary
    last_period = truncated.rfind(".")
    if last_period > max_chars * 0.7:
        truncated = truncated[: last_period + 1]
    return truncated + suffix


def clean_text(text: str) -> str:
    """Remove control characters and normalize unicode."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cc" or ch in "\n\t")
    return normalize_whitespace(text)


def extract_keywords(query: str) -> list[str]:
    """
    Extract meaningful keywords from a query string.
    Removes common English stop words.
    """
    STOP_WORDS = {
        "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
        "of", "and", "or", "but", "not", "with", "this", "that", "are",
        "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may",
        "might", "shall", "can", "what", "which", "who", "how", "when",
        "where", "why", "i", "you", "he", "she", "we", "they",
    }
    words = re.findall(r"\b[a-z0-9]+\b", query.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


def keyword_overlap_score(keywords: list[str], text: str) -> float:
    """
    Return the fraction of keywords present in text (0.0–1.0).
    Simple bag-of-words presence check.
    """
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return hits / len(keywords)
