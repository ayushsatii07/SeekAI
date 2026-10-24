"""
URL normalization utilities for SeekAI.

Normalizes URLs so that duplicates from different search queries
can be recognized and deduplicated correctly.
"""
from __future__ import annotations

import re
from urllib.parse import (
    ParseResult,
    parse_qs,
    urlencode,
    urlparse,
    urlunparse,
)

# Query parameters that are always tracking/session-specific and safe to remove
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "ref", "referrer",
        "_ga", "_gl", "mc_eid", "mc_cid",
        "WT.mc_id", "WT.srch",
    }
)


def extract_domain(url: str) -> str:
    """Return the netloc (domain) of a URL, stripped of 'www.'."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    """
    Normalize a URL for deduplication purposes.

    Transformations applied:
    - Lowercase scheme and host
    - Strip 'www.' prefix
    - Remove URL fragment (#section)
    - Remove trailing slash from path (unless path is just '/')
    - Remove known tracking query parameters
    - Sort remaining query parameters for stability
    """
    if not url:
        return ""

    try:
        parsed: ParseResult = urlparse(url.strip())

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # Normalize path: remove trailing slash (except root)
        path = parsed.path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Filter query parameters
        query_params = parse_qs(parsed.query, keep_blank_values=False)
        filtered_params = {
            k: v
            for k, v in query_params.items()
            if k.lower() not in _TRACKING_PARAMS
        }
        # Sort for stability
        normalized_query = urlencode(sorted(filtered_params.items()), doseq=True)

        # Drop fragment entirely
        normalized = urlunparse((scheme, netloc, path, parsed.params, normalized_query, ""))
        return normalized
    except Exception:
        return url  # Return original if parsing fails


def is_same_page(url1: str, url2: str) -> bool:
    """Return True if two URLs point to the same normalized page."""
    return normalize_url(url1) == normalize_url(url2)


def is_valid_url(url: str) -> bool:
    """Basic URL sanity check."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False
