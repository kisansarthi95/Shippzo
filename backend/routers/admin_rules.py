"""
Phase-5g (2026-05-17) — Admin/Rules domain extracted from server.py.

Uses the "function rebinding" pattern: each handler function STAYS in
server.py (so we don't risk accidentally truncating large bodies); we
simply comment out the `@api_router.*` decorator there and re-decorate
the function with the new router from `init()`.

This loses nothing — FastAPI's path-op-builder runs at decorator-call
time which still happens *after* server.py finishes loading
(decorators just get applied from this router instead of api_router).

The endpoints below are 1:1 the same routes the old `api_router` was
serving — same paths, same models, same auth.

Endpoints handled (Phase-5g, all under /api):
  GET  /admin/plan-features
  PUT  /admin/plan-features
  GET  /admin/plan-limits
  PUT  /admin/plan-limits
  POST /admin/plan-limits/reset
  GET  /admin/whatsapp-pricing
  PUT  /admin/whatsapp-pricing
  GET  /me/whatsapp-pricing
  GET  /admin/stage-rules
  PUT  /admin/stage-rules
  GET  /me/stage-rules
  POST /admin/sla/run-now
  GET  /admin/sla/alerts
  POST /admin/sla/alerts/{alert_id}/dismiss
  POST /admin/sla/alerts/dismiss-bulk
  GET  /me/sla/alerts
  GET  /admin/sla/summary
  PUT  /admin/global-config
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

log = logging.getLogger(__name__)

admin_rules_router = APIRouter(prefix="/api", tags=["admin-rules"])


def init() -> None:
    """Late-bind: import the orphan handler functions from server.py and
    re-decorate them with the new router. Must be called exactly once,
    after server.py has finished defining the handlers."""
    from server import (  # noqa: WPS433 — intentional late import
        # Plan features
        admin_get_plan_features,
        admin_put_plan_features,
        # Plan limits
        admin_get_plan_limits,
        admin_put_plan_limits,
        admin_reset_plan_limits,
        # WhatsApp pricing
        admin_get_whatsapp_pricing,
        admin_put_whatsapp_pricing,
        me_get_whatsapp_pricing,
        # Stage rules
        admin_get_stage_rules,
        admin_put_stage_rules,
        me_get_stage_rules,
        # SLA
        admin_sla_run_now,
        admin_sla_alerts,
        admin_sla_dismiss,
        admin_sla_dismiss_bulk,
        me_sla_alerts,
        admin_sla_summary,
        # Global config
        admin_put_global_config,
    )

    admin_rules_router.get("/admin/plan-features")(admin_get_plan_features)
    admin_rules_router.put("/admin/plan-features")(admin_put_plan_features)

    admin_rules_router.get("/admin/plan-limits")(admin_get_plan_limits)
    admin_rules_router.put("/admin/plan-limits")(admin_put_plan_limits)
    admin_rules_router.post("/admin/plan-limits/reset")(admin_reset_plan_limits)

    admin_rules_router.get("/admin/whatsapp-pricing")(admin_get_whatsapp_pricing)
    admin_rules_router.put("/admin/whatsapp-pricing")(admin_put_whatsapp_pricing)
    admin_rules_router.get("/me/whatsapp-pricing")(me_get_whatsapp_pricing)

    admin_rules_router.get("/admin/stage-rules")(admin_get_stage_rules)
    admin_rules_router.put("/admin/stage-rules")(admin_put_stage_rules)
    admin_rules_router.get("/me/stage-rules")(me_get_stage_rules)

    admin_rules_router.post("/admin/sla/run-now")(admin_sla_run_now)
    admin_rules_router.get("/admin/sla/alerts")(admin_sla_alerts)
    admin_rules_router.post("/admin/sla/alerts/{alert_id}/dismiss")(admin_sla_dismiss)
    admin_rules_router.post("/admin/sla/alerts/dismiss-bulk")(admin_sla_dismiss_bulk)
    admin_rules_router.get("/me/sla/alerts")(me_sla_alerts)
    admin_rules_router.get("/admin/sla/summary")(admin_sla_summary)

    admin_rules_router.put("/admin/global-config")(admin_put_global_config)

    log.info("[admin_rules] 18 endpoints rebound (Phase-5g)")
