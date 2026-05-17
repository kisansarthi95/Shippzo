"""
Phase-24 (2026-05-17) — Field-config admin API.
Phase-27 (2026-05-17) — Migrated from a global super-admin API to
                        per-user (per-tenant) endpoints. Each shop
                        owner manages THEIR OWN field config; access
                        to the editing path is gated by the
                        `field_controls` feature flag (decided by
                        plan, set by the super admin from the Plan
                        Features admin panel).

Endpoints (mounted under /api by ingress):
  • GET   /api/field-configs/modules                (auth)
  • GET   /api/field-configs/{module}               (auth, reads MY config)
  • GET   /api/me/field-configs/{module}            (alias for the above)
  • PATCH /api/me/field-configs/{module}/{field_key}
                                                    (auth + feature flag)

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
me_field_configs_router = APIRouter(
    prefix="/api/me/field-configs", tags=["field-configs"]
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
    )
    # Feature-flag helper lives in server.py — see _user_has_feature
    # below; we resolve it lazily so this module stays import-safe.

    async def _require_feature(current_user: Dict[str, Any], key: str) -> None:
        """Block the request unless the user's plan grants `key`. Admin
        always passes — same convention as the frontend useFeatureFlag."""
        if current_user.get("is_admin"):
            return
        try:
            from server import user_has_feature  # noqa: WPS433
            ok = await user_has_feature(current_user, key)
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your plan does not include '{key}'. "
                    "Ask your admin to upgrade, or enable the feature on your plan."
                ),
            )

    # ── Read paths (no feature-flag gate — every shop sees their config) ─
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
        return await get_module_config(db, module, current_user.get("id", ""))

    @me_field_configs_router.get("/{module}")
    async def get_me_module_config(
        module: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if module not in MODULE_REGISTRY:
            raise HTTPException(status_code=404, detail="unknown module")
        cfg = await get_module_config(db, module, current_user.get("id", ""))
        cfg["locked_keys"] = sorted(LOCKED_FIELDS.get(module, set()))
        return cfg

    # ── Write path (feature-flag gated) ────────────────────────
    @me_field_configs_router.patch("/{module}/{field_key}")
    async def patch_my_field(
        module: str,
        field_key: str,
        payload: UpdateFieldIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _require_feature(current_user, "field_controls")
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
            user_id=current_user.get("id", ""),
        )
        if not ok:
            raise HTTPException(
                status_code=400 if msg in {"locked_field", "unknown_module"} else 422,
                detail=msg,
            )
        cfg = await get_module_config(db, module, current_user.get("id", ""))
        cfg["locked_keys"] = sorted(LOCKED_FIELDS.get(module, set()))
        return cfg

    log.info("[field_configs] router endpoints registered (per-user)")
