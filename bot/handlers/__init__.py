from .user_order import router as user_router
from .info_pages import router as info_router
from .admin import router as admin_router
from .proxy_panel import router as proxy_router

__all__ = ["user_router", "info_router", "admin_router", "proxy_router"]
