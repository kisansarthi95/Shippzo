"""
Short-link service — Phase F3.9.8.

Customer-facing recovery URLs from Dukaan / Shopify are unwieldy:
    https://kisansarathi.com/kisansarthi/bag/cart-lead?uuid=a83da2ed-…

When the operator sends a WhatsApp recovery message we want a tidy
short alias so the message looks clean:
    https://<host>/api/s/Ab3xQ9

The /api prefix is unavoidable because Kubernetes ingress only
forwards `/api/*` to the backend (everything else goes to Expo on
port 3000). It still saves ~30 characters and the link looks
intentional rather than dumped raw.

Endpoints
---------
POST /api/short-links            (auth)   create a short link
GET  /api/s/{code}               (public) 302 redirect + analytics
GET  /api/short-links/{code}     (auth)   inspect a short link (debug)

Schema (`db.short_links`)
-------------------------
{
  code:           str (6-8 char base62, unique per user)
  user_id:        str
  target_url:     str
  cart_id:        Optional[str]   # abandoned-cart linkage for auto-recover
  hits:           int
  last_clicked_at: Optional[str]
  created_at:     str
}
"""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel


short_links_router = APIRouter(prefix="/api", tags=["short-links"])


# base62 alphabet — lowercase + uppercase + digits.
_ALPHABET = string.ascii_lowercase + string.ascii_uppercase + string.digits


def _gen_code(n: int = 6) -> str:
    return "".join(random.choice(_ALPHABET) for _ in range(n))


class _CreatePayload(BaseModel):
    target_url: str
    # Optional binding to an abandoned-cart row. When the public GET
    # is hit we stamp the cart with `clicked_at` so the auto-recover
    # cross-verifier (Phase F3.9.9) has another signal to lean on.
    cart_id: Optional[str] = None


def init() -> None:
    import logging
    _logger = logging.getLogger("routers.short_links")

    from server import db, get_current_user as _get_current_user  # noqa: WPS433

    @short_links_router.post("/short-links")
    async def create_short_link(
        payload: _CreatePayload,
        request: Request,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        url = (payload.target_url or "").strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="target_url must be http(s)")

        # Idempotency — if the same user already shortened this exact
        # URL, return the existing code so repeat WhatsApp sends don't
        # spawn duplicate short links.
        existing = await db.short_links.find_one(
            {"user_id": current_user["id"], "target_url": url},
            {"_id": 0},
        )
        if existing:
            code = existing["code"]
        else:
            # Pick a free code. With 6 chars × 62 = 56 billion namespace
            # collisions are negligible but we still retry a few times.
            code = _gen_code(6)
            for _ in range(5):
                clash = await db.short_links.find_one({"code": code}, {"_id": 1})
                if not clash:
                    break
                code = _gen_code(6)
            now = datetime.now(timezone.utc).isoformat()
            doc = {
                "code":            code,
                "user_id":         current_user["id"],
                "target_url":      url,
                "cart_id":         (payload.cart_id or None),
                "hits":            0,
                "last_clicked_at": None,
                "created_at":      now,
            }
            await db.short_links.insert_one(doc)

        # Derive short_url from the incoming request so it's always
        # the externally-reachable host, regardless of preview vs
        # production deployment.
        base = str(request.base_url).rstrip("/")
        return {
            "code":      code,
            "short_url": f"{base}/api/s/{code}",
            "target_url": url,
        }

    @short_links_router.get("/s/{code}")
    async def follow_short_link(
        code: str = Path(..., min_length=4, max_length=12),
    ):
        doc = await db.short_links.find_one({"code": code}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Link not found")
        now = datetime.now(timezone.utc).isoformat()
        # Best-effort analytics — don't block the redirect on failure.
        try:
            await db.short_links.update_one(
                {"code": code},
                {"$inc": {"hits": 1},
                 "$set": {"last_clicked_at": now}},
            )
            # When the link points to a specific abandoned cart,
            # also stamp the cart so Phase F3.9.9 auto-recover has
            # a confidence signal. We don't move the cart to
            # "recovered" yet — that happens only when an actual
            # order webhook arrives matching this cart.
            cart_id = doc.get("cart_id")
            if cart_id:
                await db.abandoned_carts.update_one(
                    {"id": cart_id, "user_id": doc["user_id"]},
                    {"$set": {
                        "link_clicked_at": now,
                        "updated_at":      now,
                    }},
                )
        except Exception as exc:        # noqa: BLE001
            _logger.warning("short-link analytics update failed: %s", exc)
        return RedirectResponse(url=doc["target_url"], status_code=302)

    @short_links_router.get("/short-links/{code}")
    async def inspect_short_link(
        code: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.short_links.find_one(
            {"code": code, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Not found")
        return doc
