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


_VALID_CATEGORIES = {"general", "billing", "technical", "feature", "other"}
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
        doc = {
            "id":            tid,
            "user_id":       current_user["id"],
            "user_email":    (current_user.get("email") or ""),
            "title":         _norm_str(payload.title, 140),
            "category":      cat,
            "status":        "open",
            "priority":      "medium",
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
            db.support_tickets.find(q)
            .sort("updated_at", -1)
            .limit(limit)
        )
        items: List[Dict[str, Any]] = []
        async for t in cursor:
            # Strip the messages array on list view — the detail
            # endpoint returns full thread. Keep only the count + last
            # preview so the list UI can show "3 replies · last: …"
            # badges.
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
