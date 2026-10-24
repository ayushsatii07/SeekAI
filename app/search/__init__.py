"""SeekAI search package."""
from app.search.base import SearchProvider
from app.search.tavily_provider import TavilySearchProvider
from app.search.deduplicator import Deduplicator
from app.search.ranker import Ranker
from app.search.content_extractor import ContentExtractor
from app.search.query_expander import QueryExpander
from app.search.search_manager import SearchManager
from app.search.vector.embeddings import (
    BaseEmbeddingService,
    OpenAIEmbeddingService,
    MockEmbeddingService,
    get_embedding_service,
)
from app.search.vector.store import (
    VectorDocument,
    VectorSearchResult,
    ChromaVectorStore,
)
from app.search.vector.hybrid_rrf import reciprocal_rank_fusion
from app.search.providers import (
    ArXivProvider,
    CrossrefProvider,
    SemanticScholarProvider,
)

__all__ = [
    "SearchProvider",
    "TavilySearchProvider",
    "Deduplicator",
    "Ranker",
    "ContentExtractor",
    "QueryExpander",
    "SearchManager",
    "BaseEmbeddingService",
    "OpenAIEmbeddingService",
    "MockEmbeddingService",
    "get_embedding_service",
    "VectorDocument",
    "VectorSearchResult",
    "ChromaVectorStore",
    "reciprocal_rank_fusion",
    # Academic providers
    "ArXivProvider",
    "CrossrefProvider",
    "SemanticScholarProvider",
]
