"""
Phase-24 (2026-05-17) — Field-config admin API.

Two routers (mounted under /api by ingress):
  * /api/field-configs/{module}            (any auth user; read-only)
  * /api/admin/field-configs/{module}      (admin: list + patch)

Follows the late-binding `init()` pattern used by routers/admin.py so
the module can be imported before server.py finishes wiring up `db`
and `get_current_user`.

The actual locked/configurable logic lives in
`services/field_config_service.py` so future modules can plug in
without touching the routing layer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.field_config_service import (
    LOCKED_FIELDS,
    MODULE_REGISTRY,
    get_module_config,
    is_locked,
    supported_modules,
    update_field,
)

log = logging.getLogger(__name__)

field_configs_router = APIRouter(
    prefix="/api/field-configs", tags=["field-configs"]
)
admin_field_configs_router = APIRouter(
    prefix="/api/admin/field-configs", tags=["field-configs"]
)


class UpdateFieldIn(BaseModel):
    enabled: Optional[bool] = None
    required: Optional[bool] = None


def init() -> None:
    """Late-bind the auth dep + db. Called exactly once from server.py
    *before* the routers are mounted."""
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
    )

    # ── Public-ish: any signed-in user can read the config ─────
    @field_configs_router.get("/modules")
    async def list_modules(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        return {"modules": supported_modules()}

    @field_configs_router.get("/{module}")
    async def get_my_module_config(
        module: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if module not in MODULE_REGISTRY:
            raise HTTPException(status_code=404, detail="unknown module")
        return await get_module_config(db, module)

    # ── Admin-only: list locked + edit configurable ────────────
    @admin_field_configs_router.get("/{module}")
    async def admin_get_module(
        module: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        if module not in MODULE_REGISTRY:
            raise HTTPException(status_code=404, detail="unknown module")
        cfg = await get_module_config(db, module)
        cfg["locked_keys"] = sorted(LOCKED_FIELDS.get(module, set()))
        return cfg

    @admin_field_configs_router.patch("/{module}/{field_key}")
    async def admin_patch_field(
        module: str,
        field_key: str,
        payload: UpdateFieldIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        if module not in MODULE_REGISTRY:
            raise HTTPException(status_code=404, detail="unknown module")
        if is_locked(module, field_key):
            raise HTTPException(
                status_code=400,
                detail=f"Field {field_key!r} is locked and cannot be modified.",
            )
        ok, msg = await update_field(
            db,
            module,
            field_key,
            enabled=payload.enabled,
            required=payload.required,
            actor_id=current_user.get("id", "?"),
        )
        if not ok:
            raise HTTPException(
                status_code=400 if msg in {"locked_field", "unknown_module"} else 422,
                detail=msg,
            )
        return await get_module_config(db, module)

    log.info("[field_configs] router endpoints registered")
