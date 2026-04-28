"""
Admin-only endpoints — incremental refactor scaffold.

This is the first router extracted from /app/backend/server.py. Right
now it carries a single proof-of-concept handler (`/admin/global-config`
GET). The bulk of admin handlers still live in server.py and will be
migrated phase-by-phase as we verify the pattern doesn't regress
the 36/36 backend test suite.

Why lazy imports?
  server.py is the module that imports us — therefore at the time this
  file is first evaluated, server's module object exists but isn't fully
  populated yet. Top-level `from server import db` would crash. By
  pulling helpers inside `init()` (called by server.py *after* it has
  finished defining everything), we avoid the circular-import trap
  while still binding to the canonical implementations.

Public API:
  - `admin_router` : the APIRouter instance
  - `init()`       : called once by server.py before include_router()
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends


admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def init() -> None:
    """Late-binding registration of admin endpoints.

    Called exactly once by server.py at the bottom of the file, after
    `db`, `get_current_user`, `_require_admin`, `_get_admin_config`
    and friends are defined.
    """
    from server import (  # noqa: WPS433 — intentional late import
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
        _get_admin_config as _get_admin_config_helper,
    )

    @admin_router.get("/global-config")
    async def get_global_config(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the admin's global configuration (AI rates, credit
        packages, plan pricing, feature flags). Admin-only.

        Logic identical to the original handler in server.py — only
        the file location has changed.
        """
        _require_admin_helper(current_user)
        return await _get_admin_config_helper()
