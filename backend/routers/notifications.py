"""
Notification Prefs + Push Token Registry — Phase-5d refactor.

Extracts 6 endpoints out of server.py into a dedicated, isolated
notifications domain. All endpoints are user-scoped (per-user prefs +
per-device push tokens) and have no cross-cutting business rules
beyond what `push_sender` already encapsulates.

Endpoints relocated:
  GET    /me/notification-prefs       get_notification_prefs
  PUT    /me/notification-prefs       put_notification_prefs
  POST   /me/push-token               register_push_token
  DELETE /me/push-token               remove_push_token
  POST   /me/push-token/test          push_token_self_test
  GET    /me/push-tokens              list_my_push_tokens

Pattern: late-binding `init()` — same as routers/wallet.py /
plans_billing.py / shipments_write.py.

Note: the `_push_event` helper that filters users by their
notification_prefs before pushing STAYS in server.py because it's
called by the SLA cron worker (also in server.py). Splitting that out
would require a circular import dance for one helper — not worth it.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


notifications_router = APIRouter(prefix="/api", tags=["notifications"])


# ============================== Models ==============================

class NotificationPrefsRequest(BaseModel):
    trial_ending:    Optional[bool] = None
    plan_expiring:   Optional[bool] = None
    low_credits:     Optional[bool] = None
    payment_success: Optional[bool] = None
    daily_summary:   Optional[bool] = None
    channel_push:    Optional[bool] = None
    channel_email:   Optional[bool] = None
    # Phase G6 — operational events
    sla_breach:        Optional[bool] = None
    daily_limit_warn:  Optional[bool] = None
    morning_reminder:  Optional[bool] = None
    new_order:         Optional[bool] = None
    low_wallet:        Optional[bool] = None


class PushTokenRequest(BaseModel):
    token: str
    platform: Optional[str] = "unknown"   # "ios" | "android" | "web"
    device_id: Optional[str] = ""


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.notifications")

    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _coerce_notif_prefs,
    )
    import push_sender as _push_sender

    # =================  Notification preferences  ======================

    @notifications_router.get("/me/notification-prefs")
    async def get_notification_prefs(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Per-user notification toggles. Surfaced from Settings →
        Notifications. Defaults are merged in via _coerce_notif_prefs
        so newly-added event keys auto-default to ON without a
        backfill migration."""
        fresh = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0},
        ) or {}
        return _coerce_notif_prefs(fresh.get("notification_prefs"))

    @notifications_router.put("/me/notification-prefs")
    async def put_notification_prefs(
        payload: NotificationPrefsRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Partial update — only fields explicitly set in the request
        body overwrite their current values; everything else is
        preserved. Returns the full merged prefs map so the UI can
        re-render without a follow-up GET."""
        fresh = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0},
        ) or {}
        current = _coerce_notif_prefs(fresh.get("notification_prefs"))
        incoming = payload.model_dump(exclude_none=True)
        merged = {**current, **{k: bool(v) for k, v in incoming.items()}}
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"notification_prefs": merged}},
        )
        return merged

    # =================  Push Token Registry (Phase G6)  =================

    @notifications_router.post("/me/push-token")
    async def register_push_token(
        payload: PushTokenRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Register or refresh an Expo push token for this device.
        push_sender deduplicates by (user_id, token) so re-registering
        the same token on app boot is idempotent."""
        try:
            res = await _push_sender.register_token(
                db, current_user["id"],
                payload.token,
                platform=payload.platform or "unknown",
                device_id=payload.device_id or "",
            )
            return res
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @notifications_router.delete("/me/push-token")
    async def remove_push_token(
        token: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Drop a token (e.g. user logged out / disabled notifications).
        Returns the count of tokens actually removed (0 or 1)."""
        n = await _push_sender.remove_token(db, current_user["id"], token)
        return {"ok": True, "removed": int(n)}

    @notifications_router.post("/me/push-token/test")
    async def push_token_self_test(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Fire a test push to all devices of the calling user. Useful
        from the Notification Prefs screen — confirms the device is
        correctly registered."""
        res = await _push_sender.send_push_to_users(
            db,
            user_ids=[current_user["id"]],
            title="🔔 Notifications working",
            body="If you can read this, push delivery is configured correctly.",
            data={"type": "self_test"},
            channel_id="default",
        )
        return res

    @notifications_router.get("/me/push-tokens")
    async def list_my_push_tokens(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Debug endpoint — lists registered tokens (truncated for log
        safety) for the current user. Used by the Settings screen to
        show "Push notifications: 2 devices registered"."""
        fresh = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0},
        ) or {}
        toks = fresh.get("push_tokens") or []
        return {
            "count":  len(toks),
            "tokens": [{
                "token":     (t or {}).get("token", "")[:40] + "…",
                "platform":  (t or {}).get("platform"),
                "device_id": (t or {}).get("device_id"),
                "updated_at": (t or {}).get("updated_at"),
            } for t in toks],
        }

    _logger.info("notifications router mounted (6 endpoints)")
