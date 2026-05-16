"""
Phase-21 — Support Tickets backend.

Single-collection ticket store backing the Support Center screen in
the frontend. Goals:

  • One endpoint each for: create ticket, list mine, get detail,
    add reply, mark resolved/closed (user side).
  • Admin counterpart: list ALL tickets across users, set status,
    set priority, reply as admin.
  • Messages live inline on the ticket doc (`messages[]`) so we
    never need a separate `support_messages` collection — chat
    threads are short (< 50 turns typically) and inline storage
    keeps the read path single-document fast.

Collection: `support_tickets`
  {
    id:           uuid,
    user_id:      str,        # owner
    user_email:   str,        # denormalised for admin list view
    title:        str,
    category:     "general" | "billing" | "technical" | "feature" | "other",
    status:       "open" | "in_progress" | "resolved" | "closed",
    priority:     "low" | "medium" | "high",
    messages:     [{
      id, author_id, author_name, author_role ("user"|"admin"),
      body, created_at,
    }],
    created_at:   iso,
    updated_at:   iso,
    last_reply_at: iso,
    last_reply_by: "user" | "admin",
  }

Implementation note: routes are defined INSIDE `init()` so they can
bind to the real `get_current_user` async dependency (which itself
relies on sub-dependencies). The earlier `Depends(lambda: ...)`
shortcut produced un-awaited coroutines at request time because
FastAPI invoked the sync lambda directly instead of resolving the
inner async dep — same pattern used by routers/webhook.py.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field


_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Helpers (pure / stateless — safe at module scope)
# ────────────────────────────────────────────────────────────────────
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_VALID_CATEGORIES = {
    # Phase-21 — Expanded category set matching the Support Center
    # Category Selection screen. Each key is the canonical machine
    # value persisted on the ticket; the UI maps these to human
    # labels (e.g. "label_print" → "Label Generate & Print Related
    # Issue"). The legacy "general" / "billing" / "technical" /
    # "feature" / "other" set is folded into this list (and the
    # legacy values keep working — existing tickets stay valid).
    "account_login",     # Account Login & Forgot Account
    "plan_wallet",       # Plan & Wallet Issue
    "label_print",       # Label Generate & Print Related Issue
    "order_input",       # Order Input Related Issue
    "whatsapp",          # WhatsApp Integration Problem
    "app_bug",           # App Crash / Bug
    "feature_request",   # Required Feature
    # Legacy keys preserved so older tickets keep validating.
    "general", "billing", "technical", "feature", "other",
}
_VALID_STATUSES   = {"open", "in_progress", "resolved", "closed"}
_VALID_PRIORITIES = {"low", "medium", "high"}


def _norm_str(s: Any, max_len: int = 5000) -> str:
    """Trim + truncate user input so a malicious / accidental megabyte
    payload can't bloat a single ticket doc. 5 KB per field is a
    generous upper bound for support content."""
    if s is None:
        return ""
    return str(s).strip()[:max_len]


def _is_admin(user: Dict[str, Any]) -> bool:
    return bool(user and user.get("is_admin"))


def _serialise(t: Dict[str, Any]) -> Dict[str, Any]:
    """Strip Mongo `_id` before returning. We don't need it on the
    client — the `id` (uuid) field is the canonical identifier."""
    t.pop("_id", None)
    return t


# ────────────────────────────────────────────────────────────────────
# Pydantic models (stateless — safe at module scope)
# ────────────────────────────────────────────────────────────────────
class CreateTicketIn(BaseModel):
    title:       str = Field(..., min_length=2,  max_length=140)
    description: str = Field(..., min_length=2,  max_length=5000)
    category:    str = "general"
    # Phase-21 — Optional structured fields gathered by the Issue
    # Details screen. None are required so legacy clients that only
    # send {title, description, category} keep working.
    courier_name:  Optional[str] = ""
    order_id:      Optional[str] = ""
    issue_started: Optional[str] = ""   # "today" | "yesterday" | "this_week" | "older"
    screenshot_b64: Optional[str] = ""  # data: URL or base64 (PNG/JPG, <5 MB)
    recording_b64:  Optional[str] = ""  # base64 mp4 — optional, capped client-side
    device_info:   Optional[Dict[str, Any]] = None


class ReplyIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class StatusIn(BaseModel):
    status: str


class PriorityIn(BaseModel):
    priority: str


# ────────────────────────────────────────────────────────────────────
# Router instances — bare module-level so server.py can include them
# BEFORE init() wires up the actual handlers. include_router() before
# init() is a no-op (zero routes registered), so the order doesn't
# really matter; we just want a stable symbol.
# ────────────────────────────────────────────────────────────────────
support_router = APIRouter(prefix="/api/support", tags=["support"])
admin_support_router = APIRouter(
    prefix="/api/admin/support", tags=["admin-support"],
)


# ────────────────────────────────────────────────────────────────────
# init() — registers every route. Called from server.py once after
# the DB + auth dependency are ready.
# ────────────────────────────────────────────────────────────────────
def init() -> None:
    from server import (
        db,
        get_current_user as _get_current_user,
    )

    # Phase-21 — Atomic SHP-XXXX ticket-number allocator. Each
    # support ticket gets a human-readable `ticket_number` like
    # SHP-2487 (4+ zero-padded digits) so operators and admins can
    # quote/search by a short identifier instead of the UUID. The
    # counter lives on the `meta_counters` collection under
    # `support_ticket_seq`; findOneAndUpdate with upsert+inc is
    # crash-safe and concurrency-safe (Mongo guarantees atomicity).
    async def _next_ticket_number() -> str:
        seq_doc = await db.meta_counters.find_one_and_update(
            {"_id": "support_ticket_seq"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
        )
        n = int((seq_doc or {}).get("seq", 1))
        # Start at 2400 so the very first prod ticket is SHP-2400 —
        # matches the design mockup (SHP-2487 visible in the My
        # Requests screenshot) and avoids feeling like "user #1".
        n_padded = max(n + 2399, 1000)
        return f"SHP-{n_padded:04d}"

    # ───────── USER endpoints ──────────────────────────────────────
    @support_router.post("/tickets")
    async def create_ticket(
        payload: CreateTicketIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Create a new support ticket. The author's first message is
        derived from `description` and stored inline in `messages[0]`
        so the detail view doesn't need a second round-trip on first
        load."""
        cat = (payload.category or "general").lower()
        if cat not in _VALID_CATEGORIES:
            raise HTTPException(status_code=400, detail="invalid category")

        now = _utcnow_iso()
        tid = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        # Generate SHP-XXXX number. If the allocator fails for any
        # reason, fall back to "SHP-" + first 4 chars of the uuid so
        # the ticket can still be created — better than 500-ing the
        # whole request over a counter hiccup.
        try:
            ticket_number = await _next_ticket_number()
        except Exception:
            ticket_number = f"SHP-{tid[:4].upper()}"

        doc = {
            "id":            tid,
            "ticket_number": ticket_number,
            "user_id":       current_user["id"],
            "user_email":    (current_user.get("email") or ""),
            "title":         _norm_str(payload.title, 140),
            "category":      cat,
            "status":        "open",
            "priority":      "medium",
            # Phase-21 — structured supplementary fields from the
            # Issue Details screen. All are optional; stored as-is
            # for the detail view to render later. Screenshot +
            # recording capped on the client (5 MB / 8 MB) so the
            # whole ticket doc stays well under Mongo's 16 MB
            # document limit even with both attachments.
            "courier_name":   _norm_str(payload.courier_name, 80),
            "order_id_ref":   _norm_str(payload.order_id, 80),
            "issue_started":  _norm_str(payload.issue_started, 32),
            "screenshot_b64": _norm_str(payload.screenshot_b64, 8_000_000),
            "recording_b64":  _norm_str(payload.recording_b64, 12_000_000),
            "device_info":    (payload.device_info or {}),
            "messages": [
                {
                    "id":           msg_id,
                    "author_id":    current_user["id"],
                    "author_name":  (current_user.get("name")
                                     or current_user.get("email")
                                     or "User"),
                    "author_role":  "user",
                    "body":         _norm_str(payload.description, 5000),
                    "created_at":   now,
                },
            ],
            "created_at":     now,
            "updated_at":     now,
            "last_reply_at":  now,
            "last_reply_by":  "user",
        }
        await db.support_tickets.insert_one(doc)
        return _serialise(doc)

    @support_router.get("/tickets")
    async def list_my_tickets(
        status: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """List the current user's tickets newest-first. Optional
        `status` filter (open / in_progress / resolved / closed)."""
        q: Dict[str, Any] = {"user_id": current_user["id"]}
        if status:
            st = status.lower()
            if st not in _VALID_STATUSES:
                raise HTTPException(status_code=400, detail="invalid status")
            q["status"] = st
        cursor = (
            db.support_tickets.find(
                q,
                # Phase-21 — Strip heavy base64 attachments from the
                # list view; the detail endpoint fetches them when
                # actually needed. Mongo `find` with a projection that
                # omits these fields is dramatically cheaper over the
                # wire when a user has 50+ tickets each with a 5 MB
                # screenshot. The full doc is still available via
                # GET /tickets/{id}.
                {"recording_b64": 0, "screenshot_b64": 0},
            )
            .sort("updated_at", -1)
            .limit(limit)
        )
        items: List[Dict[str, Any]] = []
        async for t in cursor:
            # Strip the messages array on list view too — the detail
            # endpoint returns the full thread. Keep only count +
            # last-preview so the list UI can show "3 replies …"
            # badges without paying the bandwidth cost.
            msgs = t.pop("messages", []) or []
            last = msgs[-1] if msgs else None
            t["message_count"] = len(msgs)
            t["last_message_preview"] = (last or {}).get("body", "")[:140]
            items.append(_serialise(t))
        return {"items": items, "count": len(items)}

    @support_router.get("/tickets/{ticket_id}")
    async def get_ticket(
        ticket_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        t = await db.support_tickets.find_one({"id": ticket_id})
        if not t:
            raise HTTPException(status_code=404, detail="ticket not found")
        # Only the owner or an admin can read.
        if t["user_id"] != current_user["id"] and not _is_admin(current_user):
            raise HTTPException(status_code=403, detail="forbidden")
        return _serialise(t)

    @support_router.post("/tickets/{ticket_id}/reply")
    async def reply_to_ticket(
        ticket_id: str,
        payload: ReplyIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        t = await db.support_tickets.find_one({"id": ticket_id})
        if not t:
            raise HTTPException(status_code=404, detail="ticket not found")

        is_admin_user = _is_admin(current_user)
        if t["user_id"] != current_user["id"] and not is_admin_user:
            raise HTTPException(status_code=403, detail="forbidden")

        # Closed tickets are read-only — operator must re-open first.
        if t.get("status") == "closed":
            raise HTTPException(status_code=409, detail="ticket is closed")

        now = _utcnow_iso()
        role = "admin" if is_admin_user else "user"
        msg = {
            "id":          str(uuid.uuid4()),
            "author_id":   current_user["id"],
            "author_name": (current_user.get("name")
                            or current_user.get("email")
                            or ("Support" if is_admin_user else "User")),
            "author_role": role,
            "body":        _norm_str(payload.body, 5000),
            "created_at":  now,
        }
        update: Dict[str, Any] = {
            "$push": {"messages": msg},
            "$set": {
                "updated_at":    now,
                "last_reply_at": now,
                "last_reply_by": role,
            },
        }
        # When the admin replies, move the ticket into in_progress
        # (only if it was previously "open"). Operator-side replies
        # don't auto-change status.
        if is_admin_user and t.get("status") == "open":
            update["$set"]["status"] = "in_progress"
        await db.support_tickets.update_one({"id": ticket_id}, update)
        return {"ok": True, "message": msg}

    @support_router.post("/tickets/{ticket_id}/close")
    async def close_my_ticket(
        ticket_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """User-side close — marks the ticket resolved + closed. Used
        when the operator is satisfied with the support reply."""
        t = await db.support_tickets.find_one({"id": ticket_id})
        if not t:
            raise HTTPException(status_code=404, detail="ticket not found")
        if t["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="forbidden")
        await db.support_tickets.update_one(
            {"id": ticket_id},
            {"$set": {
                "status":     "closed",
                "updated_at": _utcnow_iso(),
            }},
        )
        return {"ok": True}

    # ───────── ADMIN endpoints ─────────────────────────────────────
    @admin_support_router.get("/tickets")
    async def admin_list_tickets(
        status: Optional[str] = Query(None),
        user_id: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if not _is_admin(current_user):
            raise HTTPException(status_code=403, detail="admin only")
        q: Dict[str, Any] = {}
        if status:
            if status not in _VALID_STATUSES:
                raise HTTPException(status_code=400, detail="invalid status")
            q["status"] = status
        if user_id:
            q["user_id"] = user_id
        cursor = (
            db.support_tickets.find(q)
            .sort("updated_at", -1)
            .limit(limit)
        )
        items: List[Dict[str, Any]] = []
        async for t in cursor:
            msgs = t.pop("messages", []) or []
            last = msgs[-1] if msgs else None
            t["message_count"] = len(msgs)
            t["last_message_preview"] = (last or {}).get("body", "")[:140]
            items.append(_serialise(t))
        return {"items": items, "count": len(items)}

    @admin_support_router.patch("/tickets/{ticket_id}/status")
    async def admin_set_status(
        ticket_id: str,
        payload: StatusIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if not _is_admin(current_user):
            raise HTTPException(status_code=403, detail="admin only")
        st = (payload.status or "").lower()
        if st not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status")
        res = await db.support_tickets.update_one(
            {"id": ticket_id},
            {"$set": {"status": st, "updated_at": _utcnow_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="ticket not found")
        return {"ok": True, "status": st}

    @admin_support_router.patch("/tickets/{ticket_id}/priority")
    async def admin_set_priority(
        ticket_id: str,
        payload: PriorityIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if not _is_admin(current_user):
            raise HTTPException(status_code=403, detail="admin only")
        pr = (payload.priority or "").lower()
        if pr not in _VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail="invalid priority")
        res = await db.support_tickets.update_one(
            {"id": ticket_id},
            {"$set": {"priority": pr, "updated_at": _utcnow_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="ticket not found")
        return {"ok": True, "priority": pr}
