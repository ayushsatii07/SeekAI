"""SeekAI API package."""
from app.api.routes_health import router as health_router
from app.api.routes_search import router as search_router
from app.api.routes_history import router as history_router

__all__ = ["health_router", "search_router", "history_router"]
