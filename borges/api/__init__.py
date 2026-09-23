"""API routers."""

from .agent import router as agent_router
from .collections import router as collections_router
from .documents import router as documents_router
from .pwa import router as pwa_router
from .search import router as search_router
from .status import router as status_router

ROUTERS = [status_router, collections_router, documents_router, search_router, pwa_router, agent_router]
