"""
Expo Push Notification helper.
==============================
Tiny wrapper around the Expo Push API (https://exp.host/--/api/v2/push/send).
No auth needed for unauthenticated apps; for higher rate limits you can pass
`EXPO_ACCESS_TOKEN` env var (we read it transparently).

Why Expo Push API instead of native FCM / APNs:
- One HTTP call covers both Android (via FCM) and iOS (via APNs).
- Works in Expo Go (dev) and standalone EAS builds.
- Free tier is plenty for our SLA + reminder volume.

Usage:
    from push_sender import send_push_to_users
    await send_push_to_users(
        db,
        user_ids=["<uuid>"],
        title="🚨 SLA breach",
        body="3 parcels overdue",
        data={"type": "sla_breach"},
    )

Token registry:
    Each user document gains a `push_tokens` list (array of dicts):
        [{"token": "ExponentPushToken[xxx]", "platform": "ios|android",
          "device_id": "...", "updated_at": "iso"}]
    Stale tokens (returned as DeviceNotRegistered by Expo) are auto-pruned.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger("push_sender")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_ACCESS_TOKEN = os.environ.get("EXPO_ACCESS_TOKEN", "").strip()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_expo_token(tok: str) -> bool:
    return isinstance(tok, str) and tok.startswith(("ExponentPushToken[", "ExpoPushToken["))


async def register_token(
    db, user_id: str, token: str,
    platform: str = "unknown",
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or update an Expo push token for the user. De-duplicates by
    token string; multiple devices per user are supported."""
    if not _is_valid_expo_token(token):
        raise ValueError(f"Invalid Expo push token: {token!r}")
    now = _utcnow()
    # Pull then push so we can overwrite stale device_id / updated_at.
    await db.users.update_one(
        {"id": user_id},
        {"$pull": {"push_tokens": {"token": token}}},
    )
    await db.users.update_one(
        {"id": user_id},
        {"$push": {"push_tokens": {
            "token":      token,
            "platform":   platform or "unknown",
            "device_id":  device_id or "",
            "updated_at": now,
        }}},
    )
    return {"ok": True, "token": token, "registered_at": now}


async def remove_token(db, user_id: str, token: str) -> int:
    res = await db.users.update_one(
        {"id": user_id},
        {"$pull": {"push_tokens": {"token": token}}},
    )
    return res.modified_count


async def _user_tokens(db, user_ids: Iterable[str]) -> List[Dict[str, str]]:
    """Return [{user_id, token, platform}] for every active token across
    the requested users."""
    out: List[Dict[str, str]] = []
    cursor = db.users.find(
        {"id": {"$in": list(user_ids)}},
        {"_id": 0, "id": 1, "push_tokens": 1},
    )
    async for u in cursor:
        for t in u.get("push_tokens") or []:
            tok = (t or {}).get("token")
            if _is_valid_expo_token(tok):
                out.append({
                    "user_id":  u["id"],
                    "token":    tok,
                    "platform": (t or {}).get("platform") or "unknown",
                })
    return out


async def _post_expo(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not messages:
        return []
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {EXPO_ACCESS_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(EXPO_PUSH_URL, json=messages, headers=headers)
            r.raise_for_status()
            data = r.json() or {}
            tickets = data.get("data") or []
            if not isinstance(tickets, list):
                return []
            return tickets
    except httpx.HTTPError:
        logger.exception("Expo push HTTP failure (count=%d)", len(messages))
        return []


async def _prune_invalid(db, results: List[Dict[str, Any]], messages: List[Dict[str, Any]]) -> int:
    """When Expo says a token is DeviceNotRegistered, drop it so we
    don't keep retrying."""
    pruned = 0
    for ticket, msg in zip(results, messages):
        if (ticket or {}).get("status") == "error":
            details = ((ticket.get("details") or {}).get("error") or "").lower()
            if "devicenotregistered" in details or details == "devicenotregistered":
                tok = msg.get("to")
                if tok:
                    res = await db.users.update_many(
                        {"push_tokens.token": tok},
                        {"$pull": {"push_tokens": {"token": tok}}},
                    )
                    pruned += res.modified_count
    return pruned


async def send_push_to_users(
    db,
    user_ids: Iterable[str],
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
    sound: str = "default",
    priority: str = "high",
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send the same message to every device of every listed user.

    Returns a summary dict {sent, errors, pruned}. Never raises — push
    is best-effort.
    """
    tokens = await _user_tokens(db, user_ids)
    if not tokens:
        return {"sent": 0, "errors": 0, "pruned": 0, "total": 0, "reason": "no_tokens"}

    messages: List[Dict[str, Any]] = []
    for t in tokens:
        msg: Dict[str, Any] = {
            "to":       t["token"],
            "title":    title[:120] if title else "Notification",
            "body":     body[:240] if body else "",
            "sound":    sound,
            "priority": priority,
            "data":     data or {},
        }
        if channel_id:
            msg["channelId"] = channel_id
        messages.append(msg)

    # Expo accepts up to 100 messages per call; chunk to be safe.
    sent, errors = 0, 0
    pruned = 0
    for i in range(0, len(messages), 90):
        chunk = messages[i:i + 90]
        tickets = await _post_expo(chunk)
        if not tickets:
            errors += len(chunk)
            continue
        for tk in tickets:
            if (tk or {}).get("status") == "ok":
                sent += 1
            else:
                errors += 1
        pruned += await _prune_invalid(db, tickets, chunk)

    return {
        "sent":   sent,
        "errors": errors,
        "pruned": pruned,
        "total":  len(messages),
    }
