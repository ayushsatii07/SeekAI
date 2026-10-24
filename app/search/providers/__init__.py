"""
Academic & Research search providers.

This package contains adapters for academic/scholarly databases:
    - SemanticScholarProvider  : Semantic Scholar Graph API (S2AG)
    - ArXivProvider            : arXiv preprints (Atom feed)
    - CrossrefProvider         : Crossref DOI / works registry
"""
from app.search.providers.arxiv_provider import ArXivProvider
from app.search.providers.crossref_provider import CrossrefProvider
from app.search.providers.semantic_scholar import SemanticScholarProvider

__all__ = [
    "ArXivProvider",
    "CrossrefProvider",
    "SemanticScholarProvider",
]
