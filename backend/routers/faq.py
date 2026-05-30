"""
FAQ router — Public read + Super-Admin CRUD with visibility toggle.

Phase-27 (2026-05-30) — Promotes the FAQ list from a hard-coded array
inside the mobile app to a server-managed collection. Adds:

  • Public endpoint        GET  /api/faq               → visible items only
  • Admin list             GET  /api/admin/faq         → every row, incl. hidden
  • Admin create           POST /api/admin/faq
  • Admin update           PATCH /api/admin/faq/{id}
  • Admin delete           DELETE /api/admin/faq/{id}
  • Admin reorder helper   POST /api/admin/faq/reorder

Storage layer: `faq_items` Mongo collection. Schema:

  {
    _id:        ObjectId,
    id:         "<slug-or-uuid4>",   # stable public-facing id
    category:   "Getting started",
    q:          "How do I … ?",
    a:          "<answer body>",
    sort_order: 100,                  # smaller = higher; default 100
    is_visible: True,                 # admin can flip to hide
    created_at: ISO 8601 UTC,
    updated_at: ISO 8601 UTC,
    created_by: "<admin user_id>",
    updated_by: "<admin user_id>",
  }

Idempotent seed runs once on boot via `seed_default_faqs()` so existing
clients get the 25 hand-curated entries without an admin having to
type them in. Re-seeding is safe (skips entries whose `id` already
exists), so re-deployments don't clobber admin edits.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

faq_router         = APIRouter(prefix="/api/faq",         tags=["faq"])
admin_faq_router   = APIRouter(prefix="/api/admin/faq",   tags=["admin-faq"])

_LOG = logging.getLogger("routers.faq")

# ─── Models ─────────────────────────────────────────────────────────
class FAQCreate(BaseModel):
    id:         Optional[str] = Field(None, max_length=64)
    category:   str           = Field(..., min_length=1, max_length=80)
    q:          str           = Field(..., min_length=3, max_length=300)
    a:          str           = Field(..., min_length=3, max_length=5000)
    sort_order: Optional[int] = Field(100, ge=0, le=99999)
    is_visible: Optional[bool] = True


class FAQUpdate(BaseModel):
    category:   Optional[str]  = Field(None, min_length=1, max_length=80)
    q:          Optional[str]  = Field(None, min_length=3, max_length=300)
    a:          Optional[str]  = Field(None, min_length=3, max_length=5000)
    sort_order: Optional[int]  = Field(None, ge=0, le=99999)
    is_visible: Optional[bool] = None


class FAQReorderItem(BaseModel):
    id:         str
    sort_order: int = Field(..., ge=0, le=99999)


class FAQReorderRequest(BaseModel):
    items: List[FAQReorderItem]


# ─── Default seed list ──────────────────────────────────────────────
# Mirror of the original hard-coded list from /support-center/faq.tsx,
# preserved so existing deployments continue to show the same content
# until admins make their own edits. Sort order increments by 10 so
# admins can slot new entries in between without re-saving everything.
_DEFAULT_FAQS: List[Dict[str, Any]] = [
    # ── Getting started ──
    {"id": "gs-signup",        "category": "Getting started",
     "q": "How do I create my Shippzo account?",
     "a": "Tap Sign up on the welcome screen and pick either email + password or WhatsApp OTP. WhatsApp OTP only needs your 10-digit mobile number — we'll send a 6-digit code on WhatsApp to verify it. Your shop name + business category is asked on the same screen so we can pre-tune the app for your business."},
    {"id": "gs-first-shipment", "category": "Getting started",
     "q": "How do I add my first shipment?",
     "a": "From the Shipments tab, tap the Add button (top-right) and either fill the form manually, paste a customer message into Smart Paste, or import an Excel/CSV. Smart Paste auto-extracts name, phone, address, pincode, product, COD amount and more from messy text in 9 Indian languages."},
    {"id": "gs-import",         "category": "Getting started",
     "q": "How do I import orders from Excel or Google Sheets?",
     "a": "Tap Add → Import. Drag in a .xlsx/.csv file or paste a Google Sheets link. The first row should contain headers (name, phone, address, pincode, etc.) — Shippzo auto-detects the mapping for the standard column names and lets you fix any unmapped ones before committing the import."},

    # ── Shipping labels & couriers ──
    {"id": "lbl-generate", "category": "Shipping labels & couriers",
     "q": "How do I generate a shipping label?",
     "a": "Open any shipment row and tap Generate label. Pick the courier from your configured list, confirm the tracking ID (auto-pulled if you added the courier's API key), and the PDF will be ready to download or print. Bulk-print up to 100 labels at once from the multi-select toolbar."},
    {"id": "lbl-courier-add", "category": "Shipping labels & couriers",
     "q": "How do I add a new courier partner?",
     "a": "Settings → Couriers → Add Courier. Pick from the built-in list (Delhivery, DTDC, India Post, etc.) or add a custom one. For tracking-ID auto-fetch, paste the courier's API key in the same screen. The same courier can be reused across all shipments."},
    {"id": "lbl-tracking", "category": "Shipping labels & couriers",
     "q": "Can Shippzo auto-fetch tracking IDs from courier APIs?",
     "a": "Yes — for couriers that publish a label-generation API (Delhivery, Shiprocket, Bluedart, etc.). Add your API key under Settings → Couriers and the tracking ID is auto-populated when you tap Generate Label. For couriers without an API, paste the tracking ID manually."},
    {"id": "lbl-bulk", "category": "Shipping labels & couriers",
     "q": "How do I print labels in bulk?",
     "a": "Switch the Shipments tab into Multi-Select mode (top-right toggle), pick the rows you want, then tap Bulk Download from the toolbar. The labels are stitched into a single multi-page PDF in the order you selected."},

    # ── Wallet, plans & payments ──
    {"id": "pay-recharge", "category": "Wallet, plans & payments",
     "q": "How do I recharge my wallet?",
     "a": "Profile → Wallet → Recharge. Pick an amount (or enter a custom amount) and pay via UPI / cards / netbanking through Razorpay. The credit reflects in your wallet within a few seconds. Your transaction history shows every recharge and deduction."},
    {"id": "pay-plan", "category": "Wallet, plans & payments",
     "q": "What's the difference between the Free, Starter, and Pro plans?",
     "a": "Free is for 1 user with up to 25 shipments per month and basic features. Starter unlocks WhatsApp templates, bulk import, and 500 shipments. Pro adds Smart Paste AI, multi-user accounts, advanced exports, and unlimited shipments. See Profile → Plans for the full feature matrix."},
    {"id": "pay-deduction", "category": "Wallet, plans & payments",
     "q": "When does Shippzo deduct credits from my wallet?",
     "a": "Credits are only deducted when you actually generate a shipping label or send a WhatsApp message — never for adding a shipment, importing data, or viewing reports. Every deduction is logged in Profile → Wallet → History so you can verify it line by line."},
    {"id": "pay-refund", "category": "Wallet, plans & payments",
     "q": "Can I get a refund for unused wallet credit?",
     "a": "Wallet credits are non-refundable once recharged, but they never expire and can be used for any chargeable action in the app. For special cases please contact support via Support Center → Create Request."},

    # ── WhatsApp & messaging ──
    {"id": "wa-connect", "category": "WhatsApp & messaging",
     "q": "How do I connect WhatsApp to send order updates?",
     "a": "Settings → WhatsApp Message Templates. WhatsApp messaging works out-of-the-box via our hosted provider (no separate WhatsApp Business API key needed). Pick your default language (Gujarati / Hindi / English) and customize the 4 standard templates: Booked, Dispatched, Delivered, Thanks."},
    {"id": "wa-otp", "category": "WhatsApp & messaging",
     "q": "Why am I getting login OTPs on WhatsApp?",
     "a": "WhatsApp is one of the supported login channels — you can opt in by signing in with your mobile number on the Login screen. OTPs expire in 10 minutes; up to 5 OTP resends are allowed per 30-minute window before a temporary lockout kicks in."},
    {"id": "wa-pack", "category": "WhatsApp & messaging",
     "q": "How do I send a packing list to my staff via WhatsApp?",
     "a": "Shipments tab → Multi-Select → pick rows → tap the green WhatsApp icon. Choose Gujarati / Hindi / English in the language popup; the packing summary opens with Copy / WhatsApp / Share actions. Default language is set in Settings → Packing Language."},

    # ── Smart Paste & AI ──
    {"id": "ai-paste", "category": "Smart Paste & AI",
     "q": "What is Smart Paste and how does it work?",
     "a": "Smart Paste turns a raw customer message (WhatsApp text, email body, screenshot OCR) into a fully-filled shipment form. It extracts name, phone, address, pincode, product, COD amount, even tokens — across 9 Indian scripts (Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Punjabi) plus English. Paste, review the highlighted fields, then save."},
    {"id": "ai-langs", "category": "Smart Paste & AI",
     "q": "Which Indian languages does Smart Paste understand?",
     "a": "Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam and Punjabi — plus mixed Hinglish/Gujlish text where addresses and city names are typed in Roman script. Currency and quantity keywords are recognised in every supported script."},
    {"id": "ai-pincode", "category": "Smart Paste & AI",
     "q": "Why does the city change after I paste an address?",
     "a": "Whenever a 6-digit pincode is detected, Shippzo cross-checks it with the official India Post database and overrides the city to the canonical district name. This avoids spelling typos (e.g. \"Ahemedabad\" → \"Ahmedabad\") that would otherwise reject the label at the courier's end."},

    # ── Reports & data ──
    {"id": "rep-csv", "category": "Reports & data",
     "q": "How do I export my shipments to Excel/CSV?",
     "a": "Shipments tab → tap the Bulk Download icon (top-right). The CSV honours any filters currently applied (status, date range, courier, etc.) and includes 44 columns including customer details, tracking, dispatch dates, COD amounts and payment status. Multi-select first if you only want specific rows."},
    {"id": "rep-sheet", "category": "Reports & data",
     "q": "How do I connect a Google Sheet for live sync?",
     "a": "Settings → Sheets → Connect. Share your sheet with the service-account email shown on screen and paste the sheet URL. Shipments are mirrored to your sheet in near-real-time — status changes flow back so your team can view dispatches without opening the app."},
    {"id": "rep-privacy", "category": "Reports & data",
     "q": "Can other Shippzo users see my shipments?",
     "a": "No. Every shop has a fully isolated dataset — your shipments, customers, couriers, wallet, settings and reports are scoped to your account. Even Shippzo admin staff need an explicit support-ticket trail before they can view your data."},

    # ── Account & troubleshooting ──
    {"id": "ac-team", "category": "Account & troubleshooting",
     "q": "How do I invite team members?",
     "a": "Available on Starter and Pro plans. Settings → Team → Invite. Send an email/phone invite; the invitee logs in via Email + Password or WhatsApp OTP. Roles are configurable: Owner (full access), Staff (no billing), or Read-only."},
    {"id": "ac-pwd", "category": "Account & troubleshooting",
     "q": "I forgot my password — what now?",
     "a": "Tap \"Forgot password?\" on the Login screen. We'll email a reset link valid for 30 minutes. Alternatively, log in with your registered mobile number via WhatsApp OTP and set a new password from Settings → Account."},
    {"id": "ac-logout", "category": "Account & troubleshooting",
     "q": "How do I log out of all devices?",
     "a": "Settings → Account → Log out everywhere. This invalidates every JWT we've issued for your account; the next login on any device starts a fresh session. Useful if you suspect a lost phone or shared device."},
    {"id": "ac-delete", "category": "Account & troubleshooting",
     "q": "Can I delete my account and data?",
     "a": "Yes — Support Center → Create Request and pick \"Account deletion\". We confirm the request with you, export any data you want, then erase your shop's records within 7 working days as required by our privacy policy."},
    {"id": "ac-bug", "category": "Account & troubleshooting",
     "q": "Something looks broken — how do I report a bug?",
     "a": "Support Center → Create Request → pick the \"Bug / Issue\" category. Attach a screenshot or a screen recording (the form supports both) — that gets us 90% of the way to a fix on the first reply. Most reports are triaged within 24 hours."},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_indexes(db: Any) -> None:
    """Idempotent: cheap to call on every cold start."""
    try:
        col = db["faq_items"]
        await col.create_index("id", unique=True)
        await col.create_index([("sort_order", 1), ("created_at", 1)])
        await col.create_index("is_visible")
    except Exception as e:
        _LOG.warning("faq index creation failed (non-fatal): %s", e)


async def seed_default_faqs(db: Any) -> None:
    """One-time seed of the 25 default Q&As.

    Skips any entry whose `id` already exists, so admin edits made
    earlier are never overwritten by a redeploy.
    """
    if db is None:
        return
    await _ensure_indexes(db)
    col = db["faq_items"]
    inserted = 0
    for i, raw in enumerate(_DEFAULT_FAQS):
        try:
            existing = await col.find_one({"id": raw["id"]})
            if existing:
                continue
            doc = {
                **raw,
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
            _LOG.warning("faq seed failed for %s: %s", raw.get("id"), e)
    if inserted:
        _LOG.info("Seeded %d default FAQ rows", inserted)


def _doc_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    """Strip Mongo internals and return a public-friendly dict."""
    return {
        "id":         d.get("id"),
        "category":   d.get("category") or "",
        "q":          d.get("q") or "",
        "a":          d.get("a") or "",
        "sort_order": int(d.get("sort_order") or 0),
        "is_visible": bool(d.get("is_visible")) if d.get("is_visible") is not None else True,
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


# ─── Init / route registration ──────────────────────────────────────
def init() -> None:
    """Late-bind so we can pull `db` / auth helpers from server.py
    without a circular import at module load time."""
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
    )
    import uuid as _uuid

    # ── PUBLIC ── GET /api/faq ───────────────────────────────────────
    @faq_router.get("")
    async def list_visible_faqs() -> Dict[str, Any]:
        """Return only visible FAQ rows, sorted for display.

        Unauthenticated — the FAQ is intentionally a marketing /
        education touchpoint. If you ever need this to be auth-gated,
        wrap it in `Depends(_get_current_user)`."""
        await _ensure_indexes(db)
        cursor = db["faq_items"].find({"is_visible": True}).sort(
            [("sort_order", 1), ("created_at", 1)]
        )
        rows = [_doc_to_dto(d) async for d in cursor]
        return {"items": rows, "count": len(rows)}

    # ── ADMIN ── GET /api/admin/faq ─────────────────────────────────
    @admin_faq_router.get("")
    async def list_all_faqs(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        await _ensure_indexes(db)
        cursor = db["faq_items"].find({}).sort(
            [("sort_order", 1), ("created_at", 1)]
        )
        rows = [_doc_to_dto(d) async for d in cursor]
        return {
            "items":     rows,
            "count":     len(rows),
            "visible":   sum(1 for r in rows if r["is_visible"]),
            "hidden":    sum(1 for r in rows if not r["is_visible"]),
        }

    # ── ADMIN ── POST /api/admin/faq ────────────────────────────────
    @admin_faq_router.post("")
    async def create_faq(
        payload: FAQCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        # Stable id — admin can override, otherwise we mint a slug.
        new_id = (payload.id or "").strip()
        if not new_id:
            new_id = f"admin-{_uuid.uuid4().hex[:10]}"
        # Don't allow accidental overwrite of an existing row.
        if await db["faq_items"].find_one({"id": new_id}):
            raise HTTPException(status_code=409, detail=f"FAQ id '{new_id}' already exists")
        doc = {
            "id":         new_id,
            "category":   payload.category.strip(),
            "q":          payload.q.strip(),
            "a":          payload.a.strip(),
            "sort_order": int(payload.sort_order or 100),
            "is_visible": True if payload.is_visible is None else bool(payload.is_visible),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "created_by": current_user.get("id") or "admin",
            "updated_by": current_user.get("id") or "admin",
        }
        await db["faq_items"].insert_one(doc)
        return {"ok": True, "item": _doc_to_dto(doc)}

    # ── ADMIN ── PATCH /api/admin/faq/{id} ──────────────────────────
    @admin_faq_router.patch("/{faq_id}")
    async def update_faq(
        faq_id: str,
        payload: FAQUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        existing = await db["faq_items"].find_one({"id": faq_id})
        if not existing:
            raise HTTPException(status_code=404, detail="FAQ not found")
        # Build $set selectively so we don't blank-out un-touched fields.
        patch: Dict[str, Any] = {}
        if payload.category   is not None: patch["category"]   = payload.category.strip()
        if payload.q          is not None: patch["q"]          = payload.q.strip()
        if payload.a          is not None: patch["a"]          = payload.a.strip()
        if payload.sort_order is not None: patch["sort_order"] = int(payload.sort_order)
        if payload.is_visible is not None: patch["is_visible"] = bool(payload.is_visible)
        if not patch:
            raise HTTPException(status_code=400, detail="Nothing to update")
        patch["updated_at"] = _now_iso()
        patch["updated_by"] = current_user.get("id") or "admin"
        await db["faq_items"].update_one({"id": faq_id}, {"$set": patch})
        updated = await db["faq_items"].find_one({"id": faq_id})
        return {"ok": True, "item": _doc_to_dto(updated)}

    # ── ADMIN ── DELETE /api/admin/faq/{id} ─────────────────────────
    @admin_faq_router.delete("/{faq_id}")
    async def delete_faq(
        faq_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        res = await db["faq_items"].delete_one({"id": faq_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="FAQ not found")
        return {"ok": True, "id": faq_id, "deleted": 1}

    # ── ADMIN ── POST /api/admin/faq/reorder ────────────────────────
    @admin_faq_router.post("/reorder")
    async def reorder_faqs(
        payload: FAQReorderRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        if not payload.items:
            return {"ok": True, "updated": 0}
        updated = 0
        for item in payload.items:
            res = await db["faq_items"].update_one(
                {"id": item.id},
                {"$set": {
                    "sort_order": int(item.sort_order),
                    "updated_at": _now_iso(),
                    "updated_by": current_user.get("id") or "admin",
                }},
            )
            updated += res.modified_count
        return {"ok": True, "updated": updated}
