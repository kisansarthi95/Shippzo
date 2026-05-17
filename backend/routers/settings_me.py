"""
Phase-5h (2026-05-17) — Settings / Me micro-endpoints rebound onto a
dedicated router.

Three endpoints handled (all under /api):
  GET /settings    — current user's saved settings
  PUT /settings    — save settings
  GET /me/usage    — usage / plan-limit telemetry

Same "function rebinding" pattern as routers/admin_rules.py — handler
bodies stay in server.py, we just re-decorate them on this router.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

log = logging.getLogger(__name__)

settings_me_router = APIRouter(prefix="/api", tags=["settings-me"])


def init() -> None:
    from server import (  # noqa: WPS433 — intentional late import
        get_settings,
        update_settings,
        my_usage,
        Settings,
    )

    settings_me_router.get("/settings", response_model=Settings)(get_settings)
    settings_me_router.put("/settings", response_model=Settings)(update_settings)
    settings_me_router.get("/me/usage")(my_usage)

    log.info("[settings_me] 3 endpoints rebound (Phase-5h)")
