"""
Feature-flag endpoints — Phase-3 incremental refactor.

Two read-only endpoints that the frontend uses to know which app
features the *current* user can access (plan-driven), plus the full
feature catalog for the Team-Members permission picker.

Public API surface is 100% unchanged from server.py — these were
simply moved out of the monolith to keep server.py shrinking.

Pattern: late-binding `init()` to avoid circular imports with server.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends


feature_flags_router = APIRouter(prefix="/api", tags=["feature-flags"])


def init() -> None:
    """Register routes after server.py finishes defining its helpers."""
    from server import (  # noqa: WPS433 — intentional late import
        get_current_user as _get_current_user,
        ALL_KEYS as _ALL_KEYS,
        _get_plan_features_doc as _get_plan_features_doc_helper,
        get_registry_payload as _get_registry_payload,
    )

    @feature_flags_router.get("/me/feature-flags")
    async def me_feature_flags(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Returns the list of feature keys the CURRENT user can use,
        based on their plan. Admin users get every key automatically so
        they never lock themselves out of the panel they administer."""
        plan = (current_user.get("plan") or "free_trial").lower()
        if current_user.get("is_admin"):
            return {"plan": plan, "features": _ALL_KEYS, "is_admin": True}
        plans = await _get_plan_features_doc_helper()
        allowed = plans.get(plan, plans.get("free_trial", []))
        return {"plan": plan, "features": list(allowed), "is_admin": False}

    @feature_flags_router.get("/me/feature-registry")
    async def me_feature_registry(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase A — Used by the Team Members screen so the shop-owner
        can pick which features each staff member can access. Returns
        the FULL feature catalog plus the subset the current user
        actually has — the UI then restricts the toggles so a user
        can't grant permissions they don't themselves have."""
        plan = (current_user.get("plan") or "free_trial").lower()
        if current_user.get("is_admin"):
            my = list(_ALL_KEYS)
        else:
            plans = await _get_plan_features_doc_helper()
            my = list(plans.get(plan, plans.get("free_trial", [])))
        return {
            "registry":    _get_registry_payload(),
            "my_features": my,
            "plan":        plan,
        }
