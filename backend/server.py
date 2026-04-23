from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

# Phase-1 auth (email+password, JWT, per-user data isolation)
from auth import (
    SignupRequest, LoginRequest, UserPublic,
    hash_password, verify_password, make_token, user_public,
    get_current_user_factory, utcnow_iso as auth_utcnow_iso,
    seed_demo_shipments, seed_default_courier, claim_legacy_data_for_admin,
)
# Phase-3a subscription plans + usage enforcement
from plans import (
    PLANS as PLAN_TABLE,
    public_plan_list,
    plan_start_payload,
    ensure_can_create_label,
    bump_label_usage,
    usage_summary,
    plan_room_status,
)
# Phase-4a credit wallet
from wallet import (
    ensure_wallet as wallet_ensure,
    get_balance as wallet_balance,
    list_history as wallet_list_history,
    require_balance as wallet_require,
    charge_for_label as wallet_charge,
    add_credits as wallet_add_credits,
    compute_label_cost,
    classify_and_cost as wallet_classify_and_cost,
)
from fastapi import Depends as _AuthDepends  # noqa: F401
import os
import io
import csv
import re
import logging
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone

# Google Sheets writer (Service Account)
try:
    from sheet_writer import append_order_row as sheet_append_order_row
    from sheet_writer import probe_connection as sheet_probe_connection
    from sheet_writer import mark_row_deleted as sheet_mark_row_deleted
    from sheet_writer import parse_row_from_updated_range as sheet_parse_row_from_updated_range
    from sheet_writer import update_row_status as sheet_update_row_status
except Exception as _sheet_import_err:  # pragma: no cover
    sheet_append_order_row = None  # type: ignore
    sheet_probe_connection = None  # type: ignore
    sheet_mark_row_deleted = None  # type: ignore
    sheet_parse_row_from_updated_range = None  # type: ignore
    sheet_update_row_status = None  # type: ignore


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# FastAPI app + routers must be declared BEFORE endpoint decorators
# (auth_router is referenced by the @auth_router.post decorators below).
app = FastAPI()
api_router = APIRouter(prefix="/api")
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_q(user: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a Mongo filter scoped to this user's data. Prevents users
    from reading/writing each other's shipments/couriers/settings."""
    q = {"user_id": user["id"]}
    if extra:
        q.update(extra)
    return q


mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Bind the auth dependency now that `db` exists.
get_current_user = get_current_user_factory(db)


# --- Auth endpoints ---------------------------------------------------

@auth_router.post("/signup")
async def auth_signup(payload: SignupRequest):
    """Create a new account + seed per-user data.

    The very first signup becomes the `admin` and inherits any existing
    pre-multi-tenant data (shipments/couriers/settings that have no
    user_id yet). All subsequent signups get a fresh workspace with 15
    demo shipments + 1 starter courier.
    """
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    now = auth_utcnow_iso()
    is_first = (await db.users.count_documents({})) == 0
    uid = str(uuid.uuid4())
    # New users start on the 7-day Free Trial (10 labels one-time).
    trial_spec = plan_start_payload("free_trial")
    user_doc = {
        "id": uid,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name.strip(),
        "shop_name": payload.shop_name.strip(),
        "is_admin": is_first,
        "plan": trial_spec["plan"],
        "plan_started_at": trial_spec["plan_started_at"],
        "plan_expires_at": trial_spec["plan_expires_at"],
        "created_at": now,
    }
    await db.users.insert_one(user_doc)

    if is_first:
        # Developer/admin account — inherits the legacy rows so nothing is orphaned.
        claimed = await claim_legacy_data_for_admin(db, uid)
        logger.info(f"Admin {email} claimed legacy rows: {claimed}")
    else:
        # Fresh user — seed starter courier + 15 demo shipments.
        cid = await seed_default_courier(db, uid)
        await seed_demo_shipments(db, uid, cid)

    token = make_token(uid, email)
    out = user_public(user_doc)
    out["_token"] = token  # stashed for the /signup response
    return {**user_public(user_doc), **{"token": token}}  # type: ignore


@auth_router.post("/login")
async def auth_login(payload: LoginRequest):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = make_token(user["id"], email)
    return {**user_public(user), "token": token}


@auth_router.get("/me", response_model=UserPublic)
async def auth_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return user_public(current_user)


@auth_router.post("/logout")
async def auth_logout():
    # JWT is stateless; the client just drops the token. This endpoint
    # exists so the frontend has something consistent to call (e.g. for
    # analytics or future server-side revocation lists).
    return {"ok": True}


# --- Google OAuth (Emergent hosted) -----------------------------------
#
# Flow (web-only via Emergent Auth):
#   1. Frontend redirects to https://auth.emergentagent.com/?redirect=<origin>/
#   2. After Google consent user is sent back to <origin>/#session_id=XXXX
#   3. Frontend extracts session_id and POSTs it here.
#   4. We exchange it server-side against Emergent's /session-data endpoint
#      (NEVER call that from the browser — it leaks the session_token).
#   5. If the email is new → we create a user, seed demo data (or claim legacy
#      rows if they're the very first signup). If it exists → we log them in.
#   6. We respond with the same JWT shape as /auth/login so the client can
#      stash it identically.
#
# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS,
# THIS BREAKS THE AUTH — the redirect origin is derived from window.location
# on the client (see login.tsx).
class GoogleSessionRequest(BaseModel):
    session_id: str


@auth_router.post("/google/session")
async def auth_google_session(payload: GoogleSessionRequest):
    if not payload.session_id or len(payload.session_id) < 8:
        raise HTTPException(status_code=400, detail="Missing session_id")
    # 1. Exchange session_id → user profile via Emergent Auth.
    async with httpx.AsyncClient(timeout=15) as cli:
        try:
            r = await cli.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": payload.session_id},
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Emergent Auth unreachable: {e}")
    if r.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail=f"Google session rejected (status {r.status_code})",
        )
    try:
        prof = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid response from Emergent Auth")
    email = (prof.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google profile missing email")

    # 2. Find-or-create the user. Email is the unique key.
    user = await db.users.find_one({"email": email})
    now = auth_utcnow_iso()
    if user is None:
        is_first = (await db.users.count_documents({})) == 0
        uid = str(uuid.uuid4())
        trial_spec = plan_start_payload("free_trial")
        user_doc = {
            "id": uid,
            "email": email,
            # Social users have no password; the email/password login endpoint
            # will reject this account (empty hash → verify_password = False).
            "password_hash": "",
            "name": prof.get("name") or email.split("@")[0],
            "shop_name": "",
            "picture": prof.get("picture") or "",
            "auth_provider": "google",
            "is_admin": is_first,
            "plan": trial_spec["plan"],
            "plan_started_at": trial_spec["plan_started_at"],
            "plan_expires_at": trial_spec["plan_expires_at"],
            "created_at": now,
        }
        await db.users.insert_one(user_doc)
        if is_first:
            claimed = await claim_legacy_data_for_admin(db, uid)
            logger.info(f"Google-admin {email} claimed legacy rows: {claimed}")
        else:
            cid = await seed_default_courier(db, uid)
            await seed_demo_shipments(db, uid, cid)
        user = user_doc
    else:
        # Ensure the existing user is marked as Google-linked (useful later).
        update: Dict[str, Any] = {}
        if not user.get("auth_provider"):
            update["auth_provider"] = "google"
        if prof.get("picture") and prof.get("picture") != user.get("picture"):
            update["picture"] = prof["picture"]
        if prof.get("name") and not user.get("name"):
            update["name"] = prof["name"]
        if update:
            await db.users.update_one({"id": user["id"]}, {"$set": update})
            user.update(update)

    token = make_token(user["id"], email)
    return {**user_public(user), "token": token}


# --- Demo data clear (per-user) ---------------------------------------

@api_router.post("/demo/clear")
async def clear_demo_data(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Removes every row still flagged `is_demo: True` for this user.
    Non-demo (real) shipments are never touched, so running this after
    a user has added real orders is safe. Demo rows have no Sheet row,
    so no tombstone write is needed."""
    res = await db.shipments.delete_many({"user_id": current_user["id"], "is_demo": True})
    return {"ok": True, "deleted": res.deleted_count}


# --------------------------------------------------------------------
# (app + router setup moved to top of file for decorator availability)
# --------------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------- Models ----------------------

class Courier(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    series_prefix: str = ""
    next_number: int = 1
    number_padding: int = 4
    contact_phone: str = ""
    contact_email: str = ""
    website_url: str = ""
    tracking_url_template: str = ""   # e.g. "https://nandan.com/track?id={tracking_id}"
    customer_id: str = ""             # e.g. India Post customer ID printed under courier name on label
    notes: str = ""
    created_at: str = Field(default_factory=utcnow_iso)


class CourierCreate(BaseModel):
    name: str
    series_prefix: Optional[str] = ""
    next_number: Optional[int] = 1
    number_padding: Optional[int] = 4
    contact_phone: Optional[str] = ""
    contact_email: Optional[str] = ""
    website_url: Optional[str] = ""
    tracking_url_template: Optional[str] = ""
    customer_id: Optional[str] = ""
    notes: Optional[str] = ""


class CourierUpdate(BaseModel):
    name: Optional[str] = None
    series_prefix: Optional[str] = None
    next_number: Optional[int] = None
    number_padding: Optional[int] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    website_url: Optional[str] = None
    tracking_url_template: Optional[str] = None
    customer_id: Optional[str] = None
    notes: Optional[str] = None


class SenderAddress(BaseModel):
    name: str = ""
    phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    show_contact: bool = True


class SheetConfig(BaseModel):
    url: str = ""
    sheet_id: str = ""
    gid: str = ""
    tab_name: str = ""
    headers: List[str] = Field(default_factory=list)
    column_mapping: Dict[str, str] = Field(default_factory=dict)
    auto_refresh_minutes: int = 0   # 0 = disabled


class BrandConfig(BaseModel):
    name: str = ""          # e.g. "Mahek Creations"
    logo_base64: str = ""   # optional: data uri or base64 string for label top


class LabelFields(BaseModel):
    """Toggles for optional fields shown on the printed label."""
    oid: bool = True
    dispatch_date: bool = True
    weight: bool = True
    item: bool = True
    phone: bool = True
    customer_id: bool = True
    token_info: bool = False
    box_dimensions: bool = False
    shipment_notes: bool = False


# ---------------------------------------------------------------------------
# Custom user-defined fields printed on the label.
#
# Positions available on the label canvas:
#   "header_top"     → tiny row above the brand block (e.g. GST No, FSSAI)
#   "from_block"     → inside the sender (from) footer block
#   "to_block"       → inside the receiver (deliver-to) block, bottom
#   "meta_row"       → next to Wt / Box / Item line
#   "notes_area"     → below the deliver-to block, styled like shipment notes
#   "footer_bottom"  → last line, above the barcode strip
# ---------------------------------------------------------------------------
class CustomLabelField(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    label: str = ""             # e.g. "GST:"  (printed as bold prefix)
    value: str = ""             # static value to print (ignored if source="shipment")
    position: str = "meta_row"  # one of the positions above
    enabled: bool = True
    bold: bool = True           # value in bold?
    size: str = "sm"            # "xs" | "sm" | "md"
    # "static"  = same value for every label (e.g. GST No.)
    # "shipment"= per-order value; user types it in the New Shipment form
    #             (optionally auto-filled from a Google Sheet column).
    source: str = "static"
    # Optional: for "shipment"-sourced fields, map to a Google Sheet column
    # header. Smart Paste will populate shipment.custom_values[id] from it.
    sheet_column: str = ""
    # Placeholder shown in the Add Shipment input when source="shipment"
    placeholder: str = ""



class Settings(BaseModel):
    id: str = "default"
    sender: SenderAddress = Field(default_factory=SenderAddress)
    brand: BrandConfig = Field(default_factory=BrandConfig)
    whatsapp_template: str = (
        "નમસ્તે {customer_name}, તમારું પાર્સલ {courier} દ્વારા મોકલાયું છે. "
        "Tracking ID: {tracking_id}\nTrack here: {tracking_url}\n"
        "અપેક્ષિત ડિલિવરી: {eta_days} દિવસ."
    )
    copy_template: str = (
        "Hi {customer_name}, your order #{order_id} has been shipped via {courier}. "
        "Tracking ID: {tracking_id}. Track here: {tracking_url}"
    )
    default_eta_days: int = 7
    prefer_logo: bool = True  # true = show logo if uploaded; false = always show brand name
    logo_shape: str = "square"  # "square" | "wide"
    shipment_tagline: str = ""  # Default tagline/notice for all shipments (e.g. "Har Pal Prakruti ke Sang"). Used if per-order shipment_notes is empty.
    sheet: SheetConfig = Field(default_factory=SheetConfig)
    label_fields: LabelFields = Field(default_factory=LabelFields)
    custom_fields: List[CustomLabelField] = Field(default_factory=list)


class SettingsUpdate(BaseModel):
    sender: Optional[SenderAddress] = None
    brand: Optional[BrandConfig] = None
    whatsapp_template: Optional[str] = None
    copy_template: Optional[str] = None
    default_eta_days: Optional[int] = None
    prefer_logo: Optional[bool] = None
    logo_shape: Optional[str] = None
    shipment_tagline: Optional[str] = None
    sheet: Optional[SheetConfig] = None
    label_fields: Optional[LabelFields] = None
    custom_fields: Optional[List[CustomLabelField]] = None


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: str = ""
    order_id: str = ""
    customer_name: str
    customer_phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    payment_mode: str = "Prepaid"
    amount: float = 0.0         # NEW: always-on amount (prepaid OR COD)
    cod_amount: float = 0.0     # kept for backwards compat, equals amount for COD
    items: List[str] = Field(default_factory=list)
    item_description: str = ""  # fallback text
    weight: str = ""
    # Token / advance payment tracking (for COD split)
    token_amount: float = 0.0   # advance already collected (prepaid portion)
    box_dimensions: str = ""    # e.g. "30×20×10 cm"
    shipment_notes: str = ""    # free text, shown on label if toggled on
    # Per-shipment dynamic custom field values.
    # Key = CustomLabelField.id, Value = the text to print for this shipment.
    custom_values: Dict[str, str] = Field(default_factory=dict)
    status: str = "Pending"
    created_at: str = Field(default_factory=utcnow_iso)
    delivered_at: Optional[str] = None
    sheet_row_key: str = ""     # used to dedupe/reference imported rows
    # Soft-delete audit: if this shipment was appended to the Master Sheet
    # (via Smart Paste), we remember the exact row number so deletion can
    # mark it as "DELETED" instead of actually removing the row.
    sheet_row_num: Optional[int] = None


class ShipmentCreate(BaseModel):
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: Optional[str] = ""
    order_id: Optional[str] = ""
    customer_name: str
    customer_phone: Optional[str] = ""
    address_line1: Optional[str] = ""
    address_line2: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    pincode: Optional[str] = ""
    payment_mode: Optional[str] = "Prepaid"
    amount: Optional[float] = 0.0
    cod_amount: Optional[float] = 0.0
    items: Optional[List[str]] = None
    item_description: Optional[str] = ""
    weight: Optional[str] = ""
    token_amount: Optional[float] = 0.0
    box_dimensions: Optional[str] = ""
    shipment_notes: Optional[str] = ""
    custom_values: Optional[Dict[str, str]] = None
    sheet_row_key: Optional[str] = ""


class ShipmentUpdate(BaseModel):
    tracking_id: Optional[str] = None
    courier_id: Optional[str] = None
    courier_name: Optional[str] = None
    order_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    payment_mode: Optional[str] = None
    amount: Optional[float] = None
    cod_amount: Optional[float] = None
    items: Optional[List[str]] = None
    item_description: Optional[str] = None
    weight: Optional[str] = None
    token_amount: Optional[float] = None
    box_dimensions: Optional[str] = None
    shipment_notes: Optional[str] = None
    status: Optional[str] = None


# ---------------------- Helpers ----------------------

def strip_id(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc.pop("_id", None)
    return doc


async def seed_defaults():
    # Default tracking URL templates for common couriers
    default_tracking_urls = {
        "Nandan Courier": "https://nandancourier.com/track?id={tracking_id}",
        "DTDC": "https://www.dtdc.in/tracking/tracking_results.asp?strCnno={tracking_id}",
        "India Post": "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx?LocationId={tracking_id}",
        "ST Courier": "https://stcourier.com/track/shipment?trackingNumber={tracking_id}",
        "Trackon": "https://trackon.in/Tracking/MultiTracking?trackingNo={tracking_id}",
        "Anjani Courier": "https://anjanicourier.in/tracking?awb={tracking_id}",
        "Professional Courier": "https://www.tpcindia.com/Tracking2.aspx?id={tracking_id}",
        "Delhivery": "https://www.delhivery.com/track/package/{tracking_id}",
        "BlueDart": "https://www.bluedart.com/tracking?awb={tracking_id}",
        "Ekart": "https://ekartlogistics.com/shipmenttrack/{tracking_id}",
    }

    existing = await db.couriers.count_documents({})
    if existing == 0:
        defaults = [
            Courier(name="Nandan Courier", series_prefix="ND", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.nandancourier.com",
                    tracking_url_template=default_tracking_urls["Nandan Courier"]),
            Courier(name="DTDC", series_prefix="DT", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.dtdc.in",
                    tracking_url_template=default_tracking_urls["DTDC"]),
            Courier(name="India Post", series_prefix="IP", next_number=1, number_padding=5,
                    contact_phone="1800 266 6868", website_url="https://www.indiapost.gov.in",
                    tracking_url_template=default_tracking_urls["India Post"]),
            Courier(name="ST Courier", series_prefix="ST", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["ST Courier"]),
            Courier(name="Trackon", series_prefix="TR", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["Trackon"]),
            Courier(name="Anjani Courier", series_prefix="AJ", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["Anjani Courier"]),
        ]
        await db.couriers.insert_many([c.model_dump() for c in defaults])
    else:
        # Migration: fill in missing tracking_url_template for existing couriers by matching name
        cursor = db.couriers.find(
            {"$or": [
                {"tracking_url_template": {"$in": ["", None]}},
                {"tracking_url_template": {"$exists": False}},
            ]},
            {"_id": 0, "id": 1, "name": 1},
        )
        async for c in cursor:
            nm = (c.get("name") or "").strip()
            # try exact match first, then case-insensitive contains
            url = default_tracking_urls.get(nm)
            if not url:
                low = nm.lower()
                for k, v in default_tracking_urls.items():
                    if k.lower() in low or low in k.lower():
                        url = v
                        break
            if url:
                await db.couriers.update_one(
                    {"id": c["id"]},
                    {"$set": {"tracking_url_template": url}},
                )

    s = await db.settings.find_one({"id": "default"})
    if not s:
        await db.settings.insert_one(Settings().model_dump())
    else:
        # ensure new fields exist without wiping
        patch = {}
        if "sheet" not in s:
            patch["sheet"] = SheetConfig().model_dump()
        if "copy_template" not in s:
            patch["copy_template"] = Settings().copy_template
        if patch:
            await db.settings.update_one({"id": "default"}, {"$set": patch})


# ---------------------- Routes ----------------------

@api_router.get("/")
async def root():
    return {"message": "Courier Label Manager API"}


# -------- Couriers --------

@api_router.get("/couriers", response_model=List[Courier])
async def list_couriers(current_user: Dict[str, Any] = Depends(get_current_user)):
    docs = await db.couriers.find({"user_id": current_user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    return [Courier(**d) for d in docs]


@api_router.post("/couriers", response_model=Courier)
async def create_courier(
    payload: CourierCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    courier = Courier(**payload.model_dump())
    doc = courier.model_dump()
    doc["user_id"] = current_user["id"]
    await db.couriers.insert_one(doc)
    return courier


@api_router.put("/couriers/{courier_id}", response_model=Courier)
async def update_courier(
    courier_id: str, payload: CourierUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.couriers.find_one_and_update(
        {"id": courier_id, "user_id": current_user["id"]}, {"$set": update}, return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**strip_id(res))


@api_router.delete("/couriers/{courier_id}")
async def delete_courier(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    res = await db.couriers.delete_one({"id": courier_id, "user_id": current_user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Courier not found")
    return {"ok": True}


@api_router.get("/couriers/{courier_id}", response_model=Courier)
async def get_courier(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**doc)


@api_router.get("/couriers/{courier_id}/next-tracking")
async def peek_next_tracking(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    num = str(c.next_number).zfill(c.number_padding)
    return {"tracking_id": f"{c.series_prefix}{num}", "next_number": c.next_number}


@api_router.post("/couriers/{courier_id}/consume-tracking")
async def consume_tracking(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    tid = f"{c.series_prefix}{str(c.next_number).zfill(c.number_padding)}"
    await db.couriers.update_one({"id": courier_id, "user_id": current_user["id"]}, {"$inc": {"next_number": 1}})
    return {"tracking_id": tid}


# -------- Settings --------

@api_router.get("/settings", response_model=Settings)
async def get_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    # Each user has their own settings doc. If missing, create a fresh one
    # tagged with this user's id so future reads/writes find it.
    doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        s = Settings()
        d = s.model_dump()
        d["user_id"] = current_user["id"]
        d["id"] = f"settings_{current_user['id'][:8]}"
        await db.settings.insert_one(d)
        return s
    return Settings(**doc)


@api_router.put("/settings", response_model=Settings)
async def update_settings(
    payload: SettingsUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update: Dict[str, Any] = {}
    if payload.sender is not None:
        update["sender"] = payload.sender.model_dump()
    if payload.brand is not None:
        update["brand"] = payload.brand.model_dump()
    if payload.whatsapp_template is not None:
        update["whatsapp_template"] = payload.whatsapp_template
    if payload.copy_template is not None:
        update["copy_template"] = payload.copy_template
    if payload.default_eta_days is not None:
        update["default_eta_days"] = payload.default_eta_days
    if payload.sheet is not None:
        update["sheet"] = payload.sheet.model_dump()
    if payload.prefer_logo is not None:
        update["prefer_logo"] = payload.prefer_logo
    if payload.logo_shape is not None:
        update["logo_shape"] = payload.logo_shape
    if payload.shipment_tagline is not None:
        update["shipment_tagline"] = payload.shipment_tagline
    if payload.label_fields is not None:
        update["label_fields"] = payload.label_fields.model_dump()
    if payload.custom_fields is not None:
        # Replace entire list; cap at 6 to avoid label clutter / abuse.
        update["custom_fields"] = [
            f.model_dump() for f in payload.custom_fields[:6]
        ]
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Per-user settings doc. Ensures tenants don't overwrite each other.
    settings_filter = {"user_id": current_user["id"]}
    update["user_id"] = current_user["id"]
    res = await db.settings.find_one_and_update(
        settings_filter,
        {"$set": update, "$setOnInsert": {"id": f"settings_{current_user['id'][:8]}"}},
        upsert=True,
        return_document=True,
    )
    return Settings(**strip_id(res))


# -------- Google Sheet integration (public link) --------

def parse_sheet_url(url: str) -> Dict[str, str]:
    """Extract sheet_id and gid from a Google Sheets URL."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Invalid Google Sheet URL. Paste the full URL from your browser.",
        )
    sheet_id = m.group(1)
    gid_match = re.search(r"[?#&]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return {"sheet_id": sheet_id, "gid": gid}


async def fetch_sheet_csv(sheet_id: str, gid: str) -> str:
    export = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as cli:
        r = await cli.get(export)
    if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", "") + " ":
        # Some Google responses return text/html with login page when not public
        body_start = r.text[:200]
        if "<html" in body_start.lower() or "Sign in" in r.text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Sheet is not public. Open Share → General access → "
                    "'Anyone with the link → Viewer' and try again."
                ),
            )
        raise HTTPException(status_code=400, detail=f"Could not fetch sheet ({r.status_code}).")
    return r.text


def parse_csv_rows(csv_text: str) -> Dict[str, Any]:
    buf = io.StringIO(csv_text)
    reader = csv.reader(buf)
    rows = list(reader)
    if not rows:
        return {"headers": [], "rows": []}
    headers = [h.strip() for h in rows[0]]
    data_rows: List[Dict[str, str]] = []
    for r in rows[1:]:
        # skip fully empty rows
        if not any((cell or "").strip() for cell in r):
            continue
        rec = {}
        for i, h in enumerate(headers):
            rec[h] = r[i].strip() if i < len(r) else ""
        data_rows.append(rec)
    return {"headers": headers, "rows": data_rows}


class SheetPreviewRequest(BaseModel):
    url: str


@api_router.post("/sheets/preview")
async def sheets_preview(payload: SheetPreviewRequest):
    parsed = parse_sheet_url(payload.url)
    csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
    data = parse_csv_rows(csv_text)
    # Auto-guess mapping
    guess = auto_guess_mapping(data["headers"])
    return {
        "sheet_id": parsed["sheet_id"],
        "gid": parsed["gid"],
        "headers": data["headers"],
        "sample_rows": data["rows"][:5],
        "total_rows": len(data["rows"]),
        "auto_mapping": guess,
    }


def auto_guess_mapping(headers: List[str]) -> Dict[str, str]:
    """Best-effort auto column mapping for common names (English+Gujarati+Hindi)."""
    lookups = {
        "order_id": ["order id", "orderid", "order no", "order number", "ઓર્ડર", "order"],
        "customer_name": ["name", "customer", "customer name", "full name", "નામ", "ग्राहक"],
        "phone": ["phone", "mobile", "contact", "whatsapp", "phone number", "mobile number",
                  "નંબર", "ફોન", "mob"],
        "address": ["address", "full address", "delivery address", "સરનામું", "पता"],
        "city": ["city", "શહેર", "शहर"],
        "state": ["state", "રાજ્ય", "राज्य"],
        "pincode": ["pincode", "pin code", "zip", "postal", "pin"],
        "item": ["item", "items", "product", "products", "order item", "product name",
                 "what you want", "વસ્તુ", "आइटम"],
        "amount": ["amount", "price", "total", "total amount", "cod amount", "order amount",
                   "રકમ", "राशि"],
        "timestamp": ["timestamp", "date", "created", "submitted"],
    }
    mapping: Dict[str, str] = {}
    lowered = {h.lower().strip(): h for h in headers}
    for key, options in lookups.items():
        for opt in options:
            # exact match first
            if opt in lowered:
                mapping[key] = lowered[opt]
                break
        if key in mapping:
            continue
        for opt in options:
            for lh, orig in lowered.items():
                if opt in lh:
                    mapping[key] = orig
                    break
            if key in mapping:
                break
    return mapping


@api_router.get("/sheets/orders")
async def sheets_orders(current_user: Dict[str, Any] = Depends(get_current_user)):
    doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="Settings not configured")
    s = Settings(**doc)
    cfg = s.sheet
    if not cfg.sheet_id:
        raise HTTPException(status_code=400, detail="Google Sheet not connected")
    csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
    data = parse_csv_rows(csv_text)

    # Detect header changes
    headers_changed = data["headers"] != cfg.headers

    mapping = cfg.column_mapping or {}

    # Find shipments that were imported from this sheet to mark them
    imported_keys = set()
    existing = await db.shipments.find(
        {"user_id": current_user["id"], "sheet_row_key": {"$ne": ""}},
        {"_id": 0, "sheet_row_key": 1},
    ).to_list(5000)
    for e in existing:
        if e.get("sheet_row_key"):
            imported_keys.add(e["sheet_row_key"])

    def mapped(row: Dict[str, str], key: str) -> str:
        col = mapping.get(key)
        if not col:
            return ""
        return row.get(col, "")

    orders = []
    for idx, row in enumerate(data["rows"]):
        row_key = _row_key(row, mapping, idx)
        orders.append({
            "row_key": row_key,
            "row_index": idx + 2,  # spreadsheet row (1-indexed + header)
            "order_id": mapped(row, "order_id"),
            "customer_name": mapped(row, "customer_name"),
            "phone": mapped(row, "phone"),
            "address": mapped(row, "address"),
            "city": mapped(row, "city"),
            "state": mapped(row, "state"),
            "pincode": mapped(row, "pincode"),
            "item": mapped(row, "item"),
            "amount": mapped(row, "amount"),
            "timestamp": mapped(row, "timestamp"),
            "already_shipped": row_key in imported_keys,
            "raw": row,
        })
    return {
        "headers": data["headers"],
        "headers_changed": headers_changed,
        "orders": orders,
        "total": len(orders),
    }


def _row_key(row: Dict[str, str], mapping: Dict[str, str], idx: int) -> str:
    order_col = mapping.get("order_id")
    phone_col = mapping.get("phone")
    name_col = mapping.get("customer_name")
    parts = []
    if order_col and row.get(order_col):
        parts.append(row[order_col])
    if phone_col and row.get(phone_col):
        parts.append(row[phone_col])
    if name_col and row.get(name_col):
        parts.append(row[name_col])
    if not parts:
        parts.append(str(idx))
    return "|".join(parts).strip()[:200]


# -------- Shipments --------

@api_router.get("/shipments", response_model=List[Shipment])
async def list_shipments(
    status: Optional[str] = None,
    courier_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Always scope to the logged-in user so one tenant never sees another's data.
    q: dict = {"user_id": current_user["id"]}
    if status:
        q["status"] = status
    if courier_id:
        q["courier_id"] = courier_id
    if search:
        q["$or"] = [
            {"tracking_id": {"$regex": search, "$options": "i"}},
            {"customer_name": {"$regex": search, "$options": "i"}},
            {"customer_phone": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}},
            {"order_id": {"$regex": search, "$options": "i"}},
        ]
    docs = await db.shipments.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return [Shipment(**d) for d in docs]


@api_router.get("/shipments/stats")
async def shipments_stats(current_user: Dict[str, Any] = Depends(get_current_user)):
    base = {"user_id": current_user["id"]}
    total = await db.shipments.count_documents(base)
    delivered = await db.shipments.count_documents({**base, "status": "Delivered"})
    pending = await db.shipments.count_documents({**base, "status": "Pending"})
    cod_cursor = db.shipments.aggregate([
        {"$match": {**base, "payment_mode": "COD", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ])
    cod_sum = 0.0
    cod_count = 0
    async for row in cod_cursor:
        cod_sum = float(row.get("sum", 0.0))
        cod_count = int(row.get("count", 0))
    prepaid_cursor = db.shipments.aggregate([
        {"$match": {**base, "payment_mode": "Prepaid", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ])
    prepaid_sum = 0.0
    prepaid_count = 0
    async for row in prepaid_cursor:
        prepaid_sum = float(row.get("sum", 0.0))
        prepaid_count = int(row.get("count", 0))
    return {
        "total": total,
        "delivered": delivered,
        "pending": pending,
        "cod_total": cod_sum,
        "cod_count": cod_count,
        "prepaid_total": prepaid_sum,
        "prepaid_count": prepaid_count,
        "revenue_total": cod_sum + prepaid_sum,
    }


@api_router.get("/sheets/sample-template", response_class=PlainTextResponse)
async def sheets_sample_template():
    """Return a CSV with ideal column layout + example rows for users to import into Google Sheets."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Timestamp", "Order ID", "Name", "Phone", "Address",
        "City", "State", "Pincode", "Item", "Amount", "Payment Mode",
    ])
    samples = [
        ["2026-01-15 10:30:00", "ORD-1001", "Ramesh Patel", "9876543210",
         "12, Navrangpura Main Road, Ellisbridge",
         "Ahmedabad", "Gujarat", "380006",
         "Cotton Kurta Large - Blue", "850", "COD"],
        ["2026-01-15 11:12:45", "ORD-1002", "Priya Shah", "9823456710",
         "B-204, Sunrise Apts, Satellite Road",
         "Ahmedabad", "Gujarat", "380015",
         "Silk Saree Red; Matching Blouse", "2499", "Prepaid"],
        ["2026-01-15 14:02:10", "ORD-1003", "Rahul Mehta", "9812345678",
         "Shop 7, Main Bazaar, Near Bus Stand",
         "Rajkot", "Gujarat", "360001",
         "Men Jeans 32 - Dark Blue", "1299", "COD"],
        ["2026-01-15 16:47:22", "ORD-1004", "Anjali Desai", "9801234567",
         "45, Gulab Nagar, Adajan",
         "Surat", "Gujarat", "395009",
         "Kids T-shirt Small; Shorts", "699", "Prepaid"],
    ]
    for row in samples:
        w.writerow(row)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="courier_sheet_template.csv"'
        },
    )


@api_router.get("/shipments/export/csv", response_class=PlainTextResponse)
async def export_csv(current_user: Dict[str, Any] = Depends(get_current_user)):
    docs = await db.shipments.find({"user_id": current_user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(5000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tracking ID", "Courier", "Order ID", "Customer", "Phone",
        "Address Line 1", "Address Line 2", "City", "State", "Pincode",
        "Payment Mode", "Amount", "Items", "Weight",
        "Status", "Created At", "Delivered At",
    ])
    for d in docs:
        items = d.get("items") or []
        items_str = "; ".join(items) if items else d.get("item_description", "")
        writer.writerow([
            d.get("tracking_id", ""), d.get("courier_name", ""),
            d.get("order_id", ""),
            d.get("customer_name", ""), d.get("customer_phone", ""),
            d.get("address_line1", ""), d.get("address_line2", ""),
            d.get("city", ""), d.get("state", ""), d.get("pincode", ""),
            d.get("payment_mode", ""), d.get("amount", 0),
            items_str, d.get("weight", ""),
            d.get("status", ""), d.get("created_at", ""), d.get("delivered_at", ""),
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@api_router.get("/shipments/by-tracking/{tracking_id}")
async def get_shipment_by_tracking(
    tracking_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.shipments.find_one(
        {
            "user_id": current_user["id"],
            "tracking_id": {"$regex": f"^{tracking_id}$", "$options": "i"},
        },
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return Shipment(**doc)


@api_router.post("/shipments/bulk-fetch")
async def bulk_fetch(
    payload: Dict[str, List[str]],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    ids = payload.get("ids", [])
    if not ids:
        return []
    docs = await db.shipments.find(
        {"user_id": current_user["id"], "id": {"$in": ids}},
        {"_id": 0},
    ).to_list(500)
    by_id = {d["id"]: Shipment(**d) for d in docs}
    ordered = [by_id[i].model_dump() for i in ids if i in by_id]
    return ordered


@api_router.get("/shipments/{shipment_id}", response_model=Shipment)
async def get_shipment(
    shipment_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.shipments.find_one(
        {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return Shipment(**doc)


@api_router.post("/shipments", response_model=Shipment)
async def create_shipment(
    payload: ShipmentCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Phase-3a/4a combined gate:
    #   • If plan has room  → consume a plan slot + AI credit.
    #   • If plan exhausted → rely on wallet overage (paid plans only);
    #     free-trial users must upgrade (no overage).
    #   • Free-trial expired → refuse outright.
    room = await plan_room_status(db, current_user)
    if room["trial_expired"]:
        raise HTTPException(
            status_code=402,
            detail="Your 7-day free trial has expired. Upgrade to continue.",
        )
    if room["daily_blocked"]:
        raise HTTPException(
            status_code=402,
            detail="Daily limit reached (100/day on Platinum). Please try again tomorrow.",
        )
    plan_key = room["plan"]
    plan_has_room = bool(room["plan_has_room"])
    if (not plan_has_room) and plan_key == "free_trial":
        raise HTTPException(
            status_code=402,
            detail=(
                "Free trial limit reached (10 labels). Upgrade to "
                "Silver or higher to keep shipping."
            ),
        )

    data = payload.model_dump()
    addr_text = " ".join(filter(None, [
        data.get("address_line1", ""), data.get("address_line2", ""),
        data.get("city", ""), data.get("state", ""), str(data.get("pincode", "")),
    ])).strip()
    # Phase-4b: LLM-backed complexity classification with safe heuristic
    # fallback baked into wallet.classify_and_cost.
    breakdown, ai_reason = await wallet_classify_and_cost(current_user, addr_text, plan_has_room)
    # Re-use the classified complexity for the wallet pre-flight so we
    # don't double-classify.
    breakdown = await wallet_require(
        db, current_user, addr_text, plan_has_room,
        complexity_override=breakdown.ai_complexity,
    )

    if data.get("courier_id") and not data.get("courier_name"):
        c = await db.couriers.find_one(
            {"id": data["courier_id"], "user_id": current_user["id"]}, {"_id": 0}
        )
        if c:
            data["courier_name"] = c.get("name", "")
    # ensure amount is populated
    if data.get("payment_mode") == "COD":
        data["cod_amount"] = float(data.get("amount") or data.get("cod_amount") or 0)
    else:
        data["cod_amount"] = 0.0
    data["amount"] = float(data.get("amount") or data.get("cod_amount") or 0)
    if data.get("items") is None:
        data["items"] = []
    if data.get("custom_values") is None:
        data["custom_values"] = {}
    shipment = Shipment(**data)
    doc = shipment.model_dump()
    doc["user_id"] = current_user["id"]
    await db.shipments.insert_one(doc)
    # Only bump plan counter when the plan actually covered this label.
    if plan_has_room:
        await bump_label_usage(db, current_user)
    # Debit wallet (safe no-op for free-trial + trial-room combo).
    await wallet_charge(db, current_user, doc["id"], breakdown)
    return shipment


@api_router.put("/shipments/{shipment_id}", response_model=Shipment)
async def update_shipment(
    shipment_id: str,
    payload: ShipmentUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update and update["status"] == "Delivered":
        update["delivered_at"] = utcnow_iso()
    if "amount" in update:
        update["cod_amount"] = float(update["amount"]) if update.get("payment_mode", "") == "COD" else 0.0
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    # ---- Two-Way Status Sync: detect status transitions BEFORE mutation
    # so we can write the new value to the Master Sheet row if linked.
    new_status = update.get("status")
    prev_doc = None
    if new_status is not None:
        prev_doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
        )

    res = await db.shipments.find_one_and_update(
        {"id": shipment_id, "user_id": current_user["id"]},
        {"$set": update},
        return_document=True,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Shipment not found")

    # Best-effort write-back to Google Sheets. Never blocks the local
    # update — logs and moves on so the app stays fast/available.
    if (
        new_status is not None
        and prev_doc is not None
        and (prev_doc.get("status") or "") != new_status
        and prev_doc.get("sheet_row_num")
        and sheet_update_row_status is not None
    ):
        try:
            tracking = prev_doc.get("tracking_id") or res.get("tracking_id") or ""
            extra = f"Tracking: {tracking}" if tracking else None
            sheet_update_row_status(
                int(prev_doc["sheet_row_num"]),
                new_status,
                extra_notice=extra,
            )
            logger.info(
                f"Sheet status sync OK: row={prev_doc['sheet_row_num']} → {new_status}"
            )
        except Exception:
            logger.exception("Sheet status sync failed (non-fatal)")

    return Shipment(**strip_id(res))


@api_router.delete("/shipments/{shipment_id}")
async def delete_shipment(
    shipment_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Soft-delete: if the shipment is linked to a Master Sheet row, mark
    that row's Status="DELETED" before removing the local record. The
    Sheet row itself is preserved as an audit trail so that data never
    disappears from the source-of-truth even when the app-level record
    is removed. Sheet failures do NOT block the local delete — we log
    and proceed so users are never stuck.
    """
    doc = await db.shipments.find_one(
        {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")

    sheet_result: Dict[str, Any] = {"attempted": False}
    row_num = doc.get("sheet_row_num")
    if row_num and sheet_mark_row_deleted is not None:
        sheet_result["attempted"] = True
        try:
            reason = (
                f"shipment {doc.get('tracking_id') or doc.get('id')} "
                f"({doc.get('customer_name','')[:40]}) removed from app"
            )
            sheet_result.update(sheet_mark_row_deleted(int(row_num), reason=reason))
        except Exception as e:
            # Don't block local delete — but surface the error to the client
            # so they know the sheet was not marked. Local record still goes.
            logger.exception("Soft-delete sheet mark failed")
            sheet_result["ok"] = False
            sheet_result["error"] = str(e)

    res = await db.shipments.delete_one(
        {"id": shipment_id, "user_id": current_user["id"]}
    )
    if res.deleted_count == 0:
        # Race condition — someone else deleted. Still return 404.
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {"ok": True, "sheet": sheet_result}


# ---------------------- Pending Orders (Smart Paste + Sheet queue) ----------------------

class PendingOrder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "paste"  # "paste" | "sheet" | "manual"
    status: str = "pending"  # "pending" | "shipped" | "skipped"

    # Customer data
    customer_name: str = ""
    customer_phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    items: str = ""  # comma separated
    amount: float = 0
    payment_mode: str = "COD"  # "COD" | "PAID"

    # Hints from paste
    courier_hint: str = ""
    order_id_hint: str = ""
    weight: str = ""
    notes: str = ""

    # Source-specific
    sheet_row_num: Optional[int] = None
    raw_text: str = ""  # original pasted message (trimmed)

    # Link when shipped
    shipment_id: Optional[str] = None
    tracking_id: Optional[str] = None

    # Parse confidence (per field: "high" | "medium" | "low" | "missing")
    confidence: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    created_at: str = Field(default_factory=utcnow_iso)
    processed_at: Optional[str] = None


class SmartPasteRequest(BaseModel):
    text: str


class ShipOrderRequest(BaseModel):
    courier_id: str
    # optional overrides before creating the shipment
    overrides: Optional[Dict[str, Any]] = None


def _normalize_digits(s: str) -> str:
    """Convert Gujarati/Hindi digits to English."""
    if not s:
        return s
    gu = "૦૧૨૩૪૫૬૭૮૯"
    hi = "०१२३४५६७८९"
    out = []
    for ch in s:
        if ch in gu:
            out.append(str(gu.index(ch)))
        elif ch in hi:
            out.append(str(hi.index(ch)))
        else:
            out.append(ch)
    return "".join(out)


def parse_structured_paste(text: str) -> Dict[str, Any]:
    """Parse the fixed format the user pastes (from Custom GPT).

    Accepts BOTH multi-line AND single-line formats. Detects field
    keywords (NAME:, PHONE:, ADDRESS_1:, ...) regardless of newlines.
    Also accepts multi-word variants like "Order ID:" (space) or
    "Payment Mode:" — they are normalised to their canonical
    underscored form below.
    """
    text = _normalize_digits(text or "").strip()
    # Pre-normalise multi-word / hyphenated keys to their canonical
    # underscore form so the token regex below stays simple. The
    # lookahead ensures we only touch a key followed by ":".
    text = re.sub(r"(?i)\border[\s\-]+id(?=\s*:)", "ORDER_ID", text)
    text = re.sub(r"(?i)\bpayment[\s\-]+mode(?=\s*:)", "PAYMENT_MODE", text)
    text = re.sub(r"(?i)\bcustomer[\s\-]+name(?=\s*:)", "CUSTOMER_NAME", text)
    text = re.sub(r"(?i)\baddress[\s\-]+(\d)(?=\s*:)", r"ADDRESS_\1", text)
    result: Dict[str, str] = {}
    confidence: Dict[str, str] = {}
    warnings: List[str] = []

    # Canonical field keys (order matters: longer keys first where ambiguous)
    FIELD_KEYS = [
        ("ADDRESS_1", "address_line1"),
        ("ADDRESS1", "address_line1"),
        ("ADDRESS_2", "address_line2"),
        ("ADDRESS2", "address_line2"),
        ("ADDRESS", "address_line1"),
        ("CUSTOMER_NAME", "customer_name"),
        ("NAME", "customer_name"),
        ("MOBILE", "customer_phone"),
        ("CONTACT", "customer_phone"),
        ("PHONE", "customer_phone"),
        ("CITY", "city"),
        ("STATE", "state"),
        ("PINCODE", "pincode"),
        ("PIN", "pincode"),
        ("ITEMS", "items"),
        ("ITEM", "items"),
        ("AMOUNT", "amount"),
        ("PRICE", "amount"),
        ("TOTAL", "amount"),
        ("PAYMENT_MODE", "payment_mode"),
        ("PAYMENT", "payment_mode"),
        ("PAY", "payment_mode"),
        ("COURIER", "courier_hint"),
        ("ORDER_ID", "order_id_hint"),
        ("ORDER", "order_id_hint"),
        ("WEIGHT", "weight"),
        ("WT", "weight"),
        ("NOTES", "notes"),
        ("NOTE", "notes"),
    ]

    # Build a regex that matches "(KEY):" boundaries.
    keys_alt = "|".join(k for k, _ in FIELD_KEYS)
    pattern = re.compile(rf"\b({keys_alt})\s*:\s*", re.IGNORECASE)
    matches = list(pattern.finditer(text))

    for i, m in enumerate(matches):
        key_raw = m.group(1).upper()
        # find canonical mapping
        mapped = None
        for k, v in FIELD_KEYS:
            if k == key_raw:
                mapped = v
                break
        if not mapped:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[start:end].strip().strip(",;").strip()
        # clean trailing punctuation
        val = re.sub(r"[\s,;.]+$", "", val)
        if val in ("-", "—", "_", "") or val.lower() in ("none", "null", "empty", "n/a", "na"):
            continue
        # Don't overwrite if already set (first occurrence wins)
        if mapped not in result:
            result[mapped] = val

    # Clean & normalize
    if "customer_phone" in result:
        digits = re.sub(r"\D", "", result["customer_phone"])
        if len(digits) > 10:
            digits = digits[-10:]
        result["customer_phone"] = digits
        confidence["customer_phone"] = "high" if len(digits) == 10 else "low"
        if len(digits) != 10:
            warnings.append("Phone number doesn't look like 10 digits")

    if "pincode" in result:
        m = re.search(r"\b(\d{6})\b", result["pincode"])
        if m:
            result["pincode"] = m.group(1)
            confidence["pincode"] = "high"
        else:
            confidence["pincode"] = "low"
            warnings.append("Pincode should be 6 digits")

    if "amount" in result:
        m = re.search(r"(\d+(?:\.\d+)?)", result["amount"].replace(",", ""))
        if m:
            try:
                result["amount"] = float(m.group(1))
                confidence["amount"] = "high"
            except Exception:
                confidence["amount"] = "low"
        else:
            result.pop("amount", None)

    if "payment_mode" in result:
        v = result["payment_mode"].upper()
        if "COD" in v or "CASH" in v or "નકદ" in v or "ડિલિવરી" in v:
            result["payment_mode"] = "COD"
        elif "PAID" in v or "PREPAID" in v or "UPI" in v or "ONLINE" in v:
            result["payment_mode"] = "PAID"
        else:
            result.pop("payment_mode", None)

    for field in ["customer_name", "address_line1", "city", "state", "items"]:
        if result.get(field):
            confidence.setdefault(field, "high")
        else:
            confidence[field] = "missing"

    return {"fields": result, "confidence": confidence, "warnings": warnings}


@api_router.post("/smart-paste/parse")
async def smart_paste_parse(payload: SmartPasteRequest):
    """Parse pasted text only (no save) — for preview/dry-run."""
    return parse_structured_paste(payload.text or "")


# ----------------------------------------------------------------------
# Duplicate detection — Smart Paste MVP Phase 2
# ----------------------------------------------------------------------

def _clean_phone(p: str) -> str:
    """Normalise a phone string to last 10 digits for robust matching."""
    if not p:
        return ""
    digits = "".join(c for c in str(p) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


async def find_duplicate_matches(
    phone: Optional[str],
    order_id: Optional[str],
    user_id: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return a list of duplicate candidates across pending orders and
    shipments. Matches are keyed on (a) last-10-digits of phone, and
    (b) exact order_id (case-insensitive trimmed).

    Each returned dict has:
      {kind: "pending"|"shipment", id, tracking_id?, customer_name,
       customer_phone, order_id, match_on: ["phone"|"order_id"|both],
       status, created_at}

    Results are deduped by id + sorted newest-first, capped at `limit`.
    """
    phone_norm = _clean_phone(phone or "")
    oid_norm = (order_id or "").strip()

    if not phone_norm and not oid_norm:
        return []

    # Build OR query across both keys.
    or_clauses: List[Dict[str, Any]] = []
    if phone_norm:
        # Stored phone may have +91 or spaces; match on ending substring.
        or_clauses.append({"customer_phone": {"$regex": f"{phone_norm}$"}})
    if oid_norm:
        # Case-insensitive exact match on order_id / order_id_hint.
        safe = re.escape(oid_norm)
        or_clauses.append({"order_id": {"$regex": f"^{safe}$", "$options": "i"}})
        or_clauses.append({"order_id_hint": {"$regex": f"^{safe}$", "$options": "i"}})

    query: Dict[str, Any] = {"$or": or_clauses} if or_clauses else {}
    if user_id:
        query["user_id"] = user_id

    # Pending orders (not yet shipped).
    pending_q = {**query, "status": {"$ne": "shipped"}}
    pending_cursor = (
        db.pending_orders.find(pending_q, {"_id": 0}).sort("created_at", -1).limit(limit)
    )
    pending_docs = await pending_cursor.to_list(limit)

    # Recent shipments (any status; UI can decide what to show).
    shipments_cursor = (
        db.shipments.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    )
    shipment_docs = await shipments_cursor.to_list(limit)

    def _why(doc: Dict[str, Any]) -> List[str]:
        matched: List[str] = []
        if phone_norm:
            dp = _clean_phone(doc.get("customer_phone") or "")
            if dp and dp == phone_norm:
                matched.append("phone")
        if oid_norm:
            doid = (doc.get("order_id") or doc.get("order_id_hint") or "").strip().lower()
            if doid and doid == oid_norm.lower():
                matched.append("order_id")
        return matched

    results: List[Dict[str, Any]] = []
    for d in pending_docs:
        results.append({
            "kind": "pending",
            "id": d.get("id"),
            "customer_name": d.get("customer_name", ""),
            "customer_phone": d.get("customer_phone", ""),
            "order_id": d.get("order_id") or d.get("order_id_hint") or "",
            "status": d.get("status") or "pending",
            "created_at": d.get("created_at", ""),
            "match_on": _why(d),
        })
    for d in shipment_docs:
        results.append({
            "kind": "shipment",
            "id": d.get("id"),
            "tracking_id": d.get("tracking_id", ""),
            "customer_name": d.get("customer_name", ""),
            "customer_phone": d.get("customer_phone", ""),
            "order_id": d.get("order_id", ""),
            "status": d.get("status") or "",
            "created_at": d.get("created_at", ""),
            "match_on": _why(d),
        })
    # Sort newest first, cap at `limit` overall.
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results[:limit]


@api_router.post("/smart-paste/check-duplicate")
async def smart_paste_check_duplicate(
    payload: SmartPasteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Inspect pasted text for duplicates WITHOUT saving.

    Returns:
      {
        "fields": {...},              # parsed fields (so the frontend can
                                      # show a preview without a 2nd round-trip)
        "confidence": {...},
        "warnings": [...],
        "duplicates": [
          {kind, id, customer_name, customer_phone, order_id,
           status, created_at, match_on: ["phone","order_id"]},
          ...
        ]
      }
    """
    parsed = parse_structured_paste(payload.text or "")
    fields = parsed.get("fields", {}) or {}
    duplicates = await find_duplicate_matches(
        phone=fields.get("customer_phone", ""),
        order_id=fields.get("order_id", "") or fields.get("order_id_hint", ""),
        user_id=current_user["id"],
    )
    return {
        "fields": fields,
        "confidence": parsed.get("confidence", {}),
        "warnings": parsed.get("warnings", []),
        "duplicates": duplicates,
    }


@api_router.post("/smart-paste", response_model=PendingOrder)
async def smart_paste_create(
    payload: SmartPasteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Parse text → write to Google Sheet (Master) → save PendingOrder to Mongo.

    RULE: If the Google Sheet write fails, we DO NOT save to Mongo and
    return 502 so the client never sees a 'ghost' order that isn't in the
    source-of-truth sheet.
    """
    parsed = parse_structured_paste(payload.text or "")
    fields = parsed["fields"]

    # ---- 1) Write to Google Master Sheet first (atomic) ----
    sheet_meta: Dict[str, Any] = {"ok": False}
    if sheet_append_order_row is not None:
        try:
            addr = " ".join(
                [fields.get("address_line1", ""), fields.get("address_line2", "")]
            ).strip()
            items_val = fields.get("items") or []
            item_type_text = (
                ", ".join(items_val) if isinstance(items_val, list) else str(items_val)
            )
            sheet_meta = sheet_append_order_row(
                user_id=current_user["id"],
                order_id=fields.get("order_id", "") or "",
                name=fields.get("customer_name", "") or "",
                phone=fields.get("customer_phone", "") or "",
                address=addr,
                city=fields.get("city", "") or "",
                state=fields.get("state", "") or "",
                pincode=fields.get("pincode", "") or "",
                item_type=item_type_text,
                amount=fields.get("amount", "") or "",
                payment_mode=fields.get("payment_mode", "") or "",
                status="Pending",
                notice="via Smart Paste",
            )
            logger.info(f"Sheet append OK: {sheet_meta.get('updated_range')}")
        except Exception as e:
            logger.exception("Google Sheet write failed")
            raise HTTPException(
                status_code=502,
                detail=f"Google Sheet save failed — order not saved. Reason: {e}",
            )
    else:
        # Library missing — fail loudly so the user knows (Sheet is source of truth)
        raise HTTPException(
            status_code=503,
            detail="Google Sheets integration not configured on server.",
        )

    # ---- 2) Now save locally (Mongo) so the app can show the queue fast ----
    # Extract the row number from the append response so we can later
    # soft-delete that exact row if the user deletes from the app.
    sheet_row_num: Optional[int] = None
    if sheet_parse_row_from_updated_range is not None:
        try:
            sheet_row_num = sheet_parse_row_from_updated_range(
                sheet_meta.get("updated_range")
            )
        except Exception:
            sheet_row_num = None

    po = PendingOrder(
        source="paste",
        raw_text=(payload.text or "")[:2000],
        confidence=parsed["confidence"],
        warnings=parsed["warnings"],
        sheet_row_num=sheet_row_num,
        **{k: v for k, v in fields.items() if k in PendingOrder.model_fields
           and k not in ("sheet_row_num",)},
    )
    # Stash sheet-write metadata on the model's raw_text for debugging if needed
    doc = po.model_dump()
    doc["_sheet_meta"] = sheet_meta
    doc["user_id"] = current_user["id"]
    await db.pending_orders.insert_one(doc)
    return po


@api_router.get("/sheets/probe")
async def sheets_probe():
    """Quick debug endpoint — verifies Service Account can read the Master Sheet."""
    if sheet_probe_connection is None:
        return {"ok": False, "error": "gspread not installed"}
    return sheet_probe_connection()


@api_router.get("/orders/pending", response_model=List[PendingOrder])
async def list_pending_orders(
    source: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    q: Dict[str, Any] = {"user_id": current_user["id"]}
    if source:
        q["source"] = source
    if status:
        q["status"] = status
    else:
        q["status"] = "pending"
    cursor = db.pending_orders.find(q, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(length=500)


@api_router.get("/orders/pending/{order_id}", response_model=PendingOrder)
async def get_pending_order(
    order_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    return doc


@api_router.put("/orders/pending/{order_id}", response_model=PendingOrder)
async def update_pending_order(
    order_id: str,
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Allow partial field updates (user edits before shipping)
    allowed = {k for k in PendingOrder.model_fields if k not in ("id", "created_at", "source")}
    upd = {k: v for k, v in payload.items() if k in allowed}
    if not upd:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    res = await db.pending_orders.update_one(
        {"id": order_id, "user_id": current_user["id"]}, {"$set": upd}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    return doc


@api_router.delete("/orders/pending/{order_id}")
async def delete_pending_order(
    order_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Soft-delete pending (Smart-Paste) orders: tombstone the Master Sheet
    row if linked, then remove the local record. Sheet failures are logged
    but do not block local deletion."""
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")

    sheet_result: Dict[str, Any] = {"attempted": False}
    row_num = doc.get("sheet_row_num")
    if row_num and sheet_mark_row_deleted is not None:
        sheet_result["attempted"] = True
        try:
            reason = (
                f"pending order {doc.get('order_id_hint') or order_id[:8]} "
                f"({(doc.get('customer_name') or '')[:40]}) removed from app"
            )
            sheet_result.update(sheet_mark_row_deleted(int(row_num), reason=reason))
        except Exception as e:
            logger.exception("Soft-delete sheet mark failed (pending)")
            sheet_result["ok"] = False
            sheet_result["error"] = str(e)

    res = await db.pending_orders.delete_one(
        {"id": order_id, "user_id": current_user["id"]}
    )
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True, "sheet": sheet_result}


@api_router.post("/orders/pending/{order_id}/ship", response_model=Shipment)
async def ship_pending_order(
    order_id: str,
    payload: ShipOrderRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Promote a pending order to a real shipment — allocates tracking ID."""
    # Phase-3a/4a combined gate
    room = await plan_room_status(db, current_user)
    if room["trial_expired"]:
        raise HTTPException(status_code=402, detail="Your 7-day free trial has expired. Upgrade to continue.")
    if room["daily_blocked"]:
        raise HTTPException(status_code=402, detail="Daily limit reached. Please try again tomorrow.")
    plan_has_room = bool(room["plan_has_room"])
    if (not plan_has_room) and room["plan"] == "free_trial":
        raise HTTPException(status_code=402, detail="Free trial limit reached (10 labels). Upgrade to Silver or higher.")

    order = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.get("status") == "shipped":
        raise HTTPException(status_code=400, detail="Order already shipped")

    courier = await db.couriers.find_one(
        {"id": payload.courier_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not courier:
        raise HTTPException(status_code=404, detail="Courier not found")

    # Allocate tracking ID
    padding = int(courier.get("number_padding") or 4)
    next_num = int(courier.get("next_number") or 1)
    tracking_id = f"{courier.get('series_prefix','')}{str(next_num).zfill(padding)}"
    await db.couriers.update_one(
        {"id": courier["id"], "user_id": current_user["id"]},
        {"$inc": {"next_number": 1}},
    )

    # Build shipment from order + optional overrides
    overrides = payload.overrides or {}
    def get(k, default=""):
        return overrides.get(k, order.get(k, default))

    # items as list (stored as comma separated in pending_orders)
    items_str = get("items", "")
    items_list = [s.strip() for s in (items_str.split(",") if items_str else []) if s.strip()]

    ship_doc = {
        "id": str(uuid.uuid4()),
        "tracking_id": tracking_id,
        "courier_id": courier["id"],
        "courier_name": courier.get("name", ""),
        "customer_name": get("customer_name"),
        "customer_phone": get("customer_phone"),
        "address_line1": get("address_line1"),
        "address_line2": get("address_line2"),
        "city": get("city"),
        "state": get("state"),
        "pincode": get("pincode"),
        "items": items_list,
        "item_description": items_str,
        "amount": float(get("amount", 0) or 0),
        "cod_amount": float(get("amount", 0) or 0) if get("payment_mode") == "COD" else 0,
        "weight": get("weight"),
        "payment_mode": get("payment_mode", "COD"),
        "order_id": get("order_id_hint"),
        "notes": get("notes"),
        "status": "Pending",
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        # Carry the Master Sheet row number so a future delete can soft-delete
        # the exact tombstone row (preserves audit trail across app users).
        "sheet_row_num": order.get("sheet_row_num"),
        "user_id": current_user["id"],
    }
    await db.shipments.insert_one(ship_doc)

    # Mark order as shipped + link
    await db.pending_orders.update_one(
        {"id": order_id, "user_id": current_user["id"]},
        {"$set": {
            "status": "shipped",
            "processed_at": utcnow_iso(),
            "shipment_id": ship_doc["id"],
            "tracking_id": tracking_id,
        }},
    )
    # Charge wallet + bump plan counter.
    addr_text = " ".join(filter(None, [
        ship_doc.get("address_line1",""), ship_doc.get("address_line2",""),
        ship_doc.get("city",""), ship_doc.get("state",""), str(ship_doc.get("pincode","")),
    ]))
    # Phase-4b LLM-backed complexity detection (cached & heuristic-safe).
    breakdown, _reason = await wallet_classify_and_cost(current_user, addr_text, plan_has_room)
    # Wallet may not have been checked above (old-path) — make sure they can pay.
    bal = await wallet_balance(db, current_user["id"])
    if breakdown.total > bal + 1e-6:
        # Shouldn't happen (we already gated), but be safe.
        logger.warning(f"Ship path: wallet underfunded for user {current_user['id']}")
    if plan_has_room:
        await bump_label_usage(db, current_user)
    await wallet_charge(db, current_user, ship_doc["id"], breakdown)

    # ---- Two-Way Status Sync: bump the Master Sheet row from
    # "Pending" to "Dispatched" and stamp the tracking ID into Notice.
    # Best-effort: sheet failures are logged but never block the flow.
    sheet_row = order.get("sheet_row_num")
    if sheet_row and sheet_update_row_status is not None:
        try:
            sheet_update_row_status(
                int(sheet_row),
                "Dispatched",
                extra_notice=f"Tracking: {tracking_id} · {courier.get('name','')}",
            )
            logger.info(
                f"Sheet status sync OK: row={sheet_row} Pending → Dispatched ({tracking_id})"
            )
        except Exception:
            logger.exception("Sheet status sync failed on ship (non-fatal)")

    ship_doc.pop("_id", None)
    return ship_doc


@api_router.get("/orders/pending-count")
async def pending_orders_count(current_user: Dict[str, Any] = Depends(get_current_user)):
    n = await db.pending_orders.count_documents(
        {"user_id": current_user["id"], "status": "pending"}
    )
    return {"count": n}


# ---------------------- Phase-3a Plans & Usage ----------------------


@api_router.get("/plans")
async def list_plans(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Return the 4-tier plan catalogue plus a hint about which plan the
    caller is currently on (so the Plans screen can badge it)."""
    return {
        "plans": public_plan_list(),
        "current": current_user.get("plan") or "free_trial",
    }


@api_router.get("/me/usage")
async def my_usage(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Current plan + live usage counters. Safe to poll on screen focus."""
    return await usage_summary(db, current_user)


class UpgradePlanRequest(BaseModel):
    plan: str  # one of free_trial | silver | gold | platinum


@api_router.post("/plans/upgrade")
async def upgrade_plan(
    payload: UpgradePlanRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """MOCK upgrade flow for Phase-3a. Razorpay payment will be added in
    Phase-4. For now this simply switches the user's plan record and
    restarts the relevant validity window (trial_expires_at for
    free_trial, open-ended for paid tiers). No money changes hands.

    SECURITY: Downgrading to free_trial after it's been consumed does
    NOT reset the lifetime trial counter — the user will still hit the
    10-label cap immediately. This prevents "reset-abuse".
    """
    key = (payload.plan or "").strip().lower()
    if key not in PLAN_TABLE:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{payload.plan}'")
    set_payload = plan_start_payload(key)
    # Stamp a flag so the UI can display "Upgrade mocked — Razorpay in Phase 4".
    set_payload["plan_mocked"] = True
    await db.users.update_one({"id": current_user["id"]}, {"$set": set_payload})
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    return {
        "ok": True,
        "mocked": True,
        "plan": key,
        "plan_started_at": set_payload["plan_started_at"],
        "plan_expires_at": set_payload.get("plan_expires_at"),
        "user": user_public(fresh or {}),
    }


# ---------------------- Phase-4a Credit Wallet ----------------------


@api_router.get("/wallet")
async def get_wallet(current_user: Dict[str, Any] = Depends(get_current_user)):
    w = await wallet_ensure(db, current_user["id"])
    return {
        "total_credits": round(float(w.get("total_credits", 0.0)), 2),
        "used_credits": round(float(w.get("used_credits", 0.0)), 2),
        "remaining_credits": round(float(w.get("remaining_credits", 0.0)), 2),
        "updated_at": w.get("updated_at"),
    }


@api_router.get("/wallet/history")
async def get_wallet_history(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    entries = await wallet_list_history(db, current_user["id"], limit=max(1, min(500, limit)))
    return {"entries": entries, "count": len(entries)}


class PurchaseCreditsRequest(BaseModel):
    amount_inr: float  # 100 INR = 100 credits (1:1 per spec)


@api_router.post("/wallet/purchase")
async def purchase_credits(
    payload: PurchaseCreditsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """MOCK credit top-up for Phase-4a.

    Razorpay wiring arrives in Phase-4c. For now this endpoint simply
    credits the wallet at the ₹1 = 1 credit rate and stamps the history
    entry with `type=purchase`. Returned receipt doubles as an audit
    record until real payment webhooks start landing.
    """
    inr = float(payload.amount_inr or 0)
    if inr <= 0:
        raise HTTPException(status_code=400, detail="amount_inr must be > 0")
    if inr < 10 or inr > 100000:
        raise HTTPException(status_code=400, detail="Top-up must be between ₹10 and ₹1,00,000")
    credits = round(inr, 2)  # 1:1 for the spec
    res = await wallet_add_credits(
        db, current_user["id"], credits,
        ctype="purchase",
        description=f"Top-up ₹{int(inr)} → {credits} credits (mocked)",
    )
    wallet = res["wallet"]
    return {
        "ok": True,
        "mocked": True,
        "amount_inr": inr,
        "credits_added": credits,
        "balance": round(float(wallet.get("remaining_credits", 0.0)), 2),
        "history_id": res["history"]["id"],
    }


@api_router.get("/wallet/quote")
async def wallet_quote(
    address: str = "",
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Dry-run: show the user what ONE more label will cost right now.

    Phase-4b: complexity is classified by the LLM (cached + heuristic
    fallback); the reason string is surfaced so the UI can explain
    *why* an address was tagged simple/medium/complex.
    """
    room = await plan_room_status(db, current_user)
    plan_has_room = bool(room["plan_has_room"]) and not room["trial_expired"] and not room["daily_blocked"]
    bd, reason = await wallet_classify_and_cost(current_user, address, plan_has_room)
    bal = await wallet_balance(db, current_user["id"])
    return {
        "plan": room["plan"],
        "plan_has_room": plan_has_room,
        "trial_expired": room["trial_expired"],
        "daily_blocked": room["daily_blocked"],
        "ai_complexity": bd.ai_complexity,
        "ai_reason": reason,
        "ai_credits": bd.ai_credits,
        "ai_applies": bd.ai_applies,
        "shipment_credits": bd.shipment_credits,
        "total": bd.total,
        "wallet_balance": round(bal, 2),
        "can_afford": (bd.total <= bal + 1e-6),
    }


# ---------------------- App setup ----------------------

app.include_router(api_router)
app.include_router(auth_router)


# --------------------------------------------------------------------
# Auth middleware — requires a valid bearer token on every /api/*
# route except /api/auth/* (signup/login/me/logout are public/self-auth).
# This is the Phase-1a lock that prevents unauthenticated API access.
# Per-route user_id filtering (data isolation) comes in Phase-1b.
# --------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from auth import decode_token as _decode_token

# Endpoints that are intentionally reachable without a token.
_AUTH_EXEMPT_PREFIXES = ("/api/auth/",)
# Admin-only endpoints. For Phase-1a we keep this small; Phase-1b will
# expand as we harden multi-tenancy.
_ADMIN_ONLY_PATHS: set = set()


@app.middleware("http")
async def auth_gate(request, call_next):
    path = request.url.path or ""
    if not path.startswith("/api/"):
        return await call_next(request)
    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    auth_hdr = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if not auth_hdr.lower().startswith("bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_hdr.split(" ", 1)[1].strip()
    try:
        payload = _decode_token(token)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    # Stash for any handler that wants it (Phase-1b will filter queries here).
    request.state.user_id = payload.get("sub")
    request.state.user_email = payload.get("email")
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def on_startup():
    await seed_defaults()
    logger.info("Courier Label Manager API started; defaults seeded.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
