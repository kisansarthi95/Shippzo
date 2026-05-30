"""
Articles router — Public read + Super-Admin CRUD with visibility toggle.

Phase-29 (2026-05-30) — Replaces the previous "Popular Articles" rows in
the Support Center that linked out to https://shippzo.com/help with a
fully native, in-app article system.

Endpoints:
  • Public list           GET  /api/articles                  → visible only
  • Public detail         GET  /api/articles/{id}             → visible only
  • Admin list            GET  /api/admin/articles            → all rows
  • Admin detail          GET  /api/admin/articles/{id}       → any row
  • Admin create          POST /api/admin/articles
  • Admin update          PATCH /api/admin/articles/{id}
  • Admin delete          DELETE /api/admin/articles/{id}

Storage layer: `support_articles` Mongo collection. Schema:

  {
    _id:        ObjectId,
    id:         "<slug-or-uuid4>",   # stable public-facing id
    title:      "How to generate shipping label?",
    summary:    "<one-line intro>",
    body:       "<rich markdown-flavoured body>",
    icon:       "document-text-outline",  # Ionicon name
    category:   "Getting started" | "Shipping" | …,
    sort_order: 100,
    is_visible: True,
    created_at: ISO 8601 UTC,
    updated_at: ISO 8601 UTC,
    created_by: "<admin user_id>",
    updated_by: "<admin user_id>",
  }

The seed is idempotent: each default article has a stable `id`; if a
doc with that id already exists we leave it alone (admin edits never
clobbered by a redeploy). New defaults added later are appended without
touching anything else.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

articles_router       = APIRouter(prefix="/api/articles",       tags=["articles"])
admin_articles_router = APIRouter(prefix="/api/admin/articles", tags=["admin-articles"])

_LOG = logging.getLogger("routers.articles")

# ─── Pydantic ───────────────────────────────────────────────────────
class ArticleCreate(BaseModel):
    id:         Optional[str] = Field(None, max_length=64)
    title:      str           = Field(..., min_length=2, max_length=160)
    summary:    Optional[str] = Field("", max_length=400)
    body:       str           = Field(..., min_length=3, max_length=20000)
    icon:       Optional[str] = Field("document-text-outline", max_length=80)
    category:   Optional[str] = Field("General", max_length=80)
    sort_order: Optional[int] = Field(100, ge=0, le=99999)
    is_visible: Optional[bool] = True


class ArticleUpdate(BaseModel):
    title:      Optional[str]  = Field(None, min_length=2, max_length=160)
    summary:    Optional[str]  = Field(None, max_length=400)
    body:       Optional[str]  = Field(None, min_length=3, max_length=20000)
    icon:       Optional[str]  = Field(None, max_length=80)
    category:   Optional[str]  = Field(None, max_length=80)
    sort_order: Optional[int]  = Field(None, ge=0, le=99999)
    is_visible: Optional[bool] = None


# ─── Default seed list ──────────────────────────────────────────────
# Mirrors the 6 hardcoded titles previously shown in support-center.tsx
# but now ships with real Shippzo-specific bodies so users can actually
# read the answer in-app rather than be punted to an external website.
_DEFAULT_ARTICLES: List[Dict[str, Any]] = [
    {
        "id":       "label",
        "title":    "How to generate a shipping label?",
        "summary":  "Step-by-step: pick a shipment, choose your courier, and download the label as a printable PDF.",
        "icon":     "receipt-outline",
        "category": "Shipping labels & couriers",
        "body":
            "Generating a shipping label in Shippzo is a 3-tap operation.\n\n"
            "1. Open the Shipments tab and tap the order you want to ship.\n"
            "2. Tap Generate Label on the detail screen.\n"
            "3. Pick the courier from your saved list — the tracking ID is "
            "auto-fetched if you have the courier's API key configured "
            "(Settings → Couriers).\n"
            "4. The PDF preview opens; tap Download or Print.\n\n"
            "Bulk-print: Switch the Shipments tab into Multi-Select mode "
            "(top-right toggle), tick the rows you want, then tap Bulk Download "
            "from the toolbar. Up to 100 labels are stitched into a single "
            "multi-page PDF in the order you selected.\n\n"
            "Wallet credit is only deducted at the moment the label is "
            "actually generated — never when you add or edit a shipment.",
    },
    {
        "id":       "recharge",
        "title":    "How to recharge your wallet?",
        "summary":  "Top up your Shippzo wallet via UPI / cards / netbanking through Razorpay in a few seconds.",
        "icon":     "wallet-outline",
        "category": "Wallet, plans & payments",
        "body":
            "Your wallet pays for chargeable actions (label generation, "
            "WhatsApp messages, custom domains). Topping it up:\n\n"
            "1. Go to Profile → Wallet → Recharge.\n"
            "2. Pick a preset amount (₹100 / ₹500 / ₹2000) or enter a custom "
            "amount.\n"
            "3. Pay via UPI, credit/debit card, or netbanking through "
            "Razorpay's secure checkout.\n"
            "4. The credit reflects in your wallet within a few seconds — no "
            "manual reconciliation required.\n\n"
            "Every recharge and deduction is logged in Profile → Wallet → "
            "History so you can audit every paisa.\n\n"
            "Wallet credit never expires, but it is non-refundable once "
            "added. For special cases please raise a ticket from "
            "Support Center → Create Request.",
    },
    {
        "id":       "import",
        "title":    "How to import orders from Excel or CSV?",
        "summary":  "Drag-and-drop an Excel/CSV file or paste a Google Sheets link — Shippzo auto-maps the columns.",
        "icon":     "cloud-upload-outline",
        "category": "Getting started",
        "body":
            "Shippzo supports bulk-importing shipments from Excel, CSV, and "
            "Google Sheets in one screen:\n\n"
            "1. Open the Shipments tab and tap Add → Import.\n"
            "2. Pick \"Upload file\" and drag-drop or browse a .xlsx / .csv "
            "file. Alternatively, choose \"Google Sheets\" and paste the "
            "share URL.\n"
            "3. The first row of your file should contain column headers "
            "(name, phone, address, pincode, product, COD amount, etc.). "
            "Shippzo auto-detects the mapping for standard column names.\n"
            "4. The Preview screen highlights unmapped columns — drag-and-"
            "drop or tap to map them to the correct Shippzo fields.\n"
            "5. Tap Import. The rows arrive in your Shipments tab in seconds.\n\n"
            "Tip: Use Smart Paste from the Add screen if you only have a "
            "single customer message — the AI extracts every field for you "
            "in 9 Indian languages.",
    },
    {
        "id":       "whatsapp",
        "title":    "How to connect WhatsApp to send order updates?",
        "summary":  "Customise the 4 standard templates in Gujarati, Hindi, or English — no separate WhatsApp Business API key required.",
        "icon":     "logo-whatsapp",
        "category": "WhatsApp & messaging",
        "body":
            "WhatsApp messaging in Shippzo works out-of-the-box via our "
            "hosted provider — you don't need a separate WhatsApp Business "
            "API account. Here's how to switch it on:\n\n"
            "1. Go to Settings → WhatsApp Message Templates.\n"
            "2. Pick your default language: Gujarati / Hindi / English.\n"
            "3. Customise the 4 standard templates:\n"
            "   • Shipment Booked — sent when an order is created.\n"
            "   • Ready to Ship — sent when the parcel is packed.\n"
            "   • Delivery Confirmation — sent after the courier marks it "
            "delivered.\n"
            "   • Delivery Thanks — sent after the customer confirms "
            "receipt.\n"
            "4. Tap Save. The next status change you trigger will fire the "
            "matching message automatically.\n\n"
            "Use placeholders like {customer_name}, {order_id}, "
            "{tracking_id} inside any template — they're replaced at send "
            "time. Bulk-send a packing list to your staff from the "
            "Shipments tab's multi-select green WhatsApp icon.",
    },
    {
        "id":       "sheet",
        "title":    "How to connect a Google Sheet for live sync?",
        "summary":  "Mirror your shipments to a Google Sheet so your team can view dispatches without opening the app.",
        "icon":     "grid-outline",
        "category": "Reports & data",
        "body":
            "Live Google Sheet sync keeps your shop's master sheet in lock-"
            "step with Shippzo:\n\n"
            "1. Go to Settings → Sheets → Connect.\n"
            "2. Copy the service-account email shown on screen.\n"
            "3. Open your Google Sheet and share it (Editor access) with "
            "that email address.\n"
            "4. Paste the sheet's URL back into Shippzo and tap Connect.\n\n"
            "Every shipment you add, update, or cancel mirrors to the "
            "sheet in near-real-time. Status changes also flow back so "
            "your team can mark deliveries from the sheet without opening "
            "the app.\n\n"
            "Need help with the column layout? The first time you connect, "
            "Shippzo offers to create a template sheet with all 44 columns "
            "pre-filled — pick that and start adding rows immediately.",
    },
    {
        "id":       "courier",
        "title":    "How to add a courier partner?",
        "summary":  "Pick from the built-in list (Delhivery, DTDC, etc.) or add a custom courier with your own API key.",
        "icon":     "car-outline",
        "category": "Shipping labels & couriers",
        "body":
            "Adding a courier is a one-screen flow:\n\n"
            "1. Go to Settings → Couriers → Add Courier.\n"
            "2. Pick from the built-in list (Delhivery, DTDC, India Post, "
            "Bluedart, Shiprocket, etc.) or tap \"Add Custom\".\n"
            "3. For couriers with a label/tracking API, paste your API key "
            "in the same screen. Shippzo auto-fetches the tracking ID "
            "every time you tap Generate Label.\n"
            "4. Save. The new courier appears in the courier picker on "
            "every shipment.\n\n"
            "You can edit pricing, label dimensions, and the default "
            "service type on the same screen later. Couriers can be hidden "
            "(not deleted) so historical shipments still reference them.",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_indexes(db: Any) -> None:
    """Idempotent — safe to call on every cold start."""
    try:
        col = db["support_articles"]
        await col.create_index("id", unique=True)
        await col.create_index([("sort_order", 1), ("created_at", 1)])
        await col.create_index("is_visible")
    except Exception as e:
        _LOG.warning("articles index creation failed (non-fatal): %s", e)


async def seed_default_articles(db: Any) -> None:
    """One-time seed of the 6 default articles. Idempotent."""
    if db is None:
        return
    await _ensure_indexes(db)
    col = db["support_articles"]
    inserted = 0
    for i, raw in enumerate(_DEFAULT_ARTICLES):
        try:
            existing = await col.find_one({"id": raw["id"]})
            if existing:
                continue
            doc = {
                **raw,
                "summary":    raw.get("summary", ""),
                "sort_order": (i + 1) * 10,
                "is_visible": True,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "created_by": "system_seed",
                "updated_by": "system_seed",
            }
            await col.insert_one(doc)
            inserted += 1
        except Exception as e:
            _LOG.warning("articles seed failed for %s: %s", raw.get("id"), e)
    if inserted:
        _LOG.info("Seeded %d default support articles", inserted)


def _doc_to_dto(d: Dict[str, Any], *, include_body: bool = True) -> Dict[str, Any]:
    """Mongo doc → public-friendly dict. Lists can omit body to keep
    the wire payload small."""
    out: Dict[str, Any] = {
        "id":         d.get("id"),
        "title":      d.get("title") or "",
        "summary":    d.get("summary") or "",
        "icon":       d.get("icon") or "document-text-outline",
        "category":   d.get("category") or "General",
        "sort_order": int(d.get("sort_order") or 0),
        "is_visible": bool(d.get("is_visible")) if d.get("is_visible") is not None else True,
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }
    if include_body:
        out["body"] = d.get("body") or ""
    return out


# ─── Init / route registration ──────────────────────────────────────
def init() -> None:
    """Late-bind so we can pull db/auth helpers from server.py without
    a circular import at module load time."""
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
    )
    import uuid as _uuid

    # ── PUBLIC ── GET /api/articles ─────────────────────────────────
    @articles_router.get("")
    async def list_visible_articles() -> Dict[str, Any]:
        """Return all visible articles (summary only — body omitted to
        keep the list payload small). The detail endpoint returns the
        full body when the user actually opens an article."""
        await _ensure_indexes(db)
        cursor = db["support_articles"].find({"is_visible": True}).sort(
            [("sort_order", 1), ("created_at", 1)]
        )
        rows = [_doc_to_dto(d, include_body=False) async for d in cursor]
        return {"items": rows, "count": len(rows)}

    # ── PUBLIC ── GET /api/articles/{id} ────────────────────────────
    @articles_router.get("/{article_id}")
    async def get_visible_article(article_id: str) -> Dict[str, Any]:
        await _ensure_indexes(db)
        doc = await db["support_articles"].find_one({"id": article_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Article not found")
        if not doc.get("is_visible", True):
            # Hidden articles are not addressable publicly. We return
            # 404 (not 403) so a hidden id leaks no information about
            # whether it ever existed.
            raise HTTPException(status_code=404, detail="Article not found")
        return {"item": _doc_to_dto(doc, include_body=True)}

    # ── ADMIN ── GET /api/admin/articles ────────────────────────────
    @admin_articles_router.get("")
    async def list_all_articles(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        await _ensure_indexes(db)
        cursor = db["support_articles"].find({}).sort(
            [("sort_order", 1), ("created_at", 1)]
        )
        rows = [_doc_to_dto(d, include_body=False) async for d in cursor]
        return {
            "items":   rows,
            "count":   len(rows),
            "visible": sum(1 for r in rows if r["is_visible"]),
            "hidden":  sum(1 for r in rows if not r["is_visible"]),
        }

    # ── ADMIN ── GET /api/admin/articles/{id} ───────────────────────
    @admin_articles_router.get("/{article_id}")
    async def get_any_article(
        article_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        doc = await db["support_articles"].find_one({"id": article_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Article not found")
        return {"item": _doc_to_dto(doc, include_body=True)}

    # ── ADMIN ── POST /api/admin/articles ───────────────────────────
    @admin_articles_router.post("")
    async def create_article(
        payload: ArticleCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        new_id = (payload.id or "").strip()
        if not new_id:
            new_id = f"admin-{_uuid.uuid4().hex[:10]}"
        if await db["support_articles"].find_one({"id": new_id}):
            raise HTTPException(
                status_code=409,
                detail=f"Article id '{new_id}' already exists",
            )
        doc = {
            "id":         new_id,
            "title":      payload.title.strip(),
            "summary":    (payload.summary or "").strip(),
            "body":       payload.body.strip(),
            "icon":       (payload.icon or "document-text-outline").strip(),
            "category":   (payload.category or "General").strip(),
            "sort_order": int(payload.sort_order or 100),
            "is_visible": True if payload.is_visible is None else bool(payload.is_visible),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "created_by": current_user.get("id") or "admin",
            "updated_by": current_user.get("id") or "admin",
        }
        await db["support_articles"].insert_one(doc)
        return {"ok": True, "item": _doc_to_dto(doc, include_body=True)}

    # ── ADMIN ── PATCH /api/admin/articles/{id} ─────────────────────
    @admin_articles_router.patch("/{article_id}")
    async def update_article(
        article_id: str,
        payload: ArticleUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        existing = await db["support_articles"].find_one({"id": article_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Article not found")
        patch: Dict[str, Any] = {}
        if payload.title      is not None: patch["title"]      = payload.title.strip()
        if payload.summary    is not None: patch["summary"]    = payload.summary.strip()
        if payload.body       is not None: patch["body"]       = payload.body.strip()
        if payload.icon       is not None: patch["icon"]       = payload.icon.strip()
        if payload.category   is not None: patch["category"]   = payload.category.strip()
        if payload.sort_order is not None: patch["sort_order"] = int(payload.sort_order)
        if payload.is_visible is not None: patch["is_visible"] = bool(payload.is_visible)
        if not patch:
            raise HTTPException(status_code=400, detail="Nothing to update")
        patch["updated_at"] = _now_iso()
        patch["updated_by"] = current_user.get("id") or "admin"
        await db["support_articles"].update_one(
            {"id": article_id}, {"$set": patch},
        )
        updated = await db["support_articles"].find_one({"id": article_id})
        return {"ok": True, "item": _doc_to_dto(updated, include_body=True)}

    # ── ADMIN ── DELETE /api/admin/articles/{id} ────────────────────
    @admin_articles_router.delete("/{article_id}")
    async def delete_article(
        article_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        res = await db["support_articles"].delete_one({"id": article_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Article not found")
        return {"ok": True, "id": article_id, "deleted": 1}
