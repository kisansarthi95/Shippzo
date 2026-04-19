from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
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


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")


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
    sheet: SheetConfig = Field(default_factory=SheetConfig)


class SettingsUpdate(BaseModel):
    sender: Optional[SenderAddress] = None
    brand: Optional[BrandConfig] = None
    whatsapp_template: Optional[str] = None
    copy_template: Optional[str] = None
    default_eta_days: Optional[int] = None
    prefer_logo: Optional[bool] = None
    logo_shape: Optional[str] = None
    sheet: Optional[SheetConfig] = None


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
    status: str = "Pending"
    created_at: str = Field(default_factory=utcnow_iso)
    delivered_at: Optional[str] = None
    sheet_row_key: str = ""     # used to dedupe/reference imported rows


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
async def list_couriers():
    docs = await db.couriers.find({}, {"_id": 0}).sort("created_at", 1).to_list(200)
    return [Courier(**d) for d in docs]


@api_router.post("/couriers", response_model=Courier)
async def create_courier(payload: CourierCreate):
    courier = Courier(**payload.model_dump())
    await db.couriers.insert_one(courier.model_dump())
    return courier


@api_router.put("/couriers/{courier_id}", response_model=Courier)
async def update_courier(courier_id: str, payload: CourierUpdate):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.couriers.find_one_and_update(
        {"id": courier_id}, {"$set": update}, return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**strip_id(res))


@api_router.delete("/couriers/{courier_id}")
async def delete_courier(courier_id: str):
    res = await db.couriers.delete_one({"id": courier_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Courier not found")
    return {"ok": True}


@api_router.get("/couriers/{courier_id}", response_model=Courier)
async def get_courier(courier_id: str):
    doc = await db.couriers.find_one({"id": courier_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**doc)


@api_router.get("/couriers/{courier_id}/next-tracking")
async def peek_next_tracking(courier_id: str):
    doc = await db.couriers.find_one({"id": courier_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    num = str(c.next_number).zfill(c.number_padding)
    return {"tracking_id": f"{c.series_prefix}{num}", "next_number": c.next_number}


@api_router.post("/couriers/{courier_id}/consume-tracking")
async def consume_tracking(courier_id: str):
    doc = await db.couriers.find_one({"id": courier_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    tid = f"{c.series_prefix}{str(c.next_number).zfill(c.number_padding)}"
    await db.couriers.update_one({"id": courier_id}, {"$inc": {"next_number": 1}})
    return {"tracking_id": tid}


# -------- Settings --------

@api_router.get("/settings", response_model=Settings)
async def get_settings():
    doc = await db.settings.find_one({"id": "default"}, {"_id": 0})
    if not doc:
        s = Settings()
        await db.settings.insert_one(s.model_dump())
        return s
    return Settings(**doc)


@api_router.put("/settings", response_model=Settings)
async def update_settings(payload: SettingsUpdate):
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
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.settings.find_one_and_update(
        {"id": "default"},
        {"$set": update},
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
async def sheets_orders():
    doc = await db.settings.find_one({"id": "default"}, {"_id": 0})
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
        {"sheet_row_key": {"$ne": ""}}, {"_id": 0, "sheet_row_key": 1}
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
):
    q: dict = {}
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
async def shipments_stats():
    total = await db.shipments.count_documents({})
    delivered = await db.shipments.count_documents({"status": "Delivered"})
    pending = await db.shipments.count_documents({"status": "Pending"})
    cod_cursor = db.shipments.aggregate([
        {"$match": {"payment_mode": "COD", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ])
    cod_sum = 0.0
    cod_count = 0
    async for row in cod_cursor:
        cod_sum = float(row.get("sum", 0.0))
        cod_count = int(row.get("count", 0))
    prepaid_cursor = db.shipments.aggregate([
        {"$match": {"payment_mode": "Prepaid", "status": {"$ne": "Cancelled"}}},
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
async def export_csv():
    docs = await db.shipments.find({}, {"_id": 0}).sort("created_at", -1).to_list(5000)
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
async def get_shipment_by_tracking(tracking_id: str):
    doc = await db.shipments.find_one(
        {"tracking_id": {"$regex": f"^{tracking_id}$", "$options": "i"}},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return Shipment(**doc)


@api_router.post("/shipments/bulk-fetch")
async def bulk_fetch(payload: Dict[str, List[str]]):
    ids = payload.get("ids", [])
    if not ids:
        return []
    docs = await db.shipments.find({"id": {"$in": ids}}, {"_id": 0}).to_list(500)
    by_id = {d["id"]: Shipment(**d) for d in docs}
    ordered = [by_id[i].model_dump() for i in ids if i in by_id]
    return ordered


@api_router.get("/shipments/{shipment_id}", response_model=Shipment)
async def get_shipment(shipment_id: str):
    doc = await db.shipments.find_one({"id": shipment_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return Shipment(**doc)


@api_router.post("/shipments", response_model=Shipment)
async def create_shipment(payload: ShipmentCreate):
    data = payload.model_dump()
    if data.get("courier_id") and not data.get("courier_name"):
        c = await db.couriers.find_one({"id": data["courier_id"]}, {"_id": 0})
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
    shipment = Shipment(**data)
    await db.shipments.insert_one(shipment.model_dump())
    return shipment


@api_router.put("/shipments/{shipment_id}", response_model=Shipment)
async def update_shipment(shipment_id: str, payload: ShipmentUpdate):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update and update["status"] == "Delivered":
        update["delivered_at"] = utcnow_iso()
    if "amount" in update:
        update["cod_amount"] = float(update["amount"]) if update.get("payment_mode", "") == "COD" else 0.0
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.shipments.find_one_and_update(
        {"id": shipment_id}, {"$set": update}, return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return Shipment(**strip_id(res))


@api_router.delete("/shipments/{shipment_id}")
async def delete_shipment(shipment_id: str):
    res = await db.shipments.delete_one({"id": shipment_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {"ok": True}


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
    """
    text = _normalize_digits(text or "").strip()
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


@api_router.post("/smart-paste", response_model=PendingOrder)
async def smart_paste_create(payload: SmartPasteRequest):
    """Parse text and create a PendingOrder."""
    parsed = parse_structured_paste(payload.text or "")
    fields = parsed["fields"]
    po = PendingOrder(
        source="paste",
        raw_text=(payload.text or "")[:2000],
        confidence=parsed["confidence"],
        warnings=parsed["warnings"],
        **{k: v for k, v in fields.items() if k in PendingOrder.model_fields},
    )
    await db.pending_orders.insert_one(po.model_dump())
    return po


@api_router.get("/orders/pending", response_model=List[PendingOrder])
async def list_pending_orders(source: Optional[str] = None, status: Optional[str] = None):
    q: Dict[str, Any] = {}
    if source:
        q["source"] = source
    if status:
        q["status"] = status
    else:
        q["status"] = "pending"
    cursor = db.pending_orders.find(q, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(length=500)


@api_router.get("/orders/pending/{order_id}", response_model=PendingOrder)
async def get_pending_order(order_id: str):
    doc = await db.pending_orders.find_one({"id": order_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    return doc


@api_router.put("/orders/pending/{order_id}", response_model=PendingOrder)
async def update_pending_order(order_id: str, payload: Dict[str, Any]):
    # Allow partial field updates (user edits before shipping)
    allowed = {k for k in PendingOrder.model_fields if k not in ("id", "created_at", "source")}
    upd = {k: v for k, v in payload.items() if k in allowed}
    if not upd:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    res = await db.pending_orders.update_one({"id": order_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await db.pending_orders.find_one({"id": order_id}, {"_id": 0})
    return doc


@api_router.delete("/orders/pending/{order_id}")
async def delete_pending_order(order_id: str):
    res = await db.pending_orders.delete_one({"id": order_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True}


@api_router.post("/orders/pending/{order_id}/ship", response_model=Shipment)
async def ship_pending_order(order_id: str, payload: ShipOrderRequest):
    """Promote a pending order to a real shipment — allocates tracking ID."""
    order = await db.pending_orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.get("status") == "shipped":
        raise HTTPException(status_code=400, detail="Order already shipped")

    courier = await db.couriers.find_one({"id": payload.courier_id}, {"_id": 0})
    if not courier:
        raise HTTPException(status_code=404, detail="Courier not found")

    # Allocate tracking ID
    padding = int(courier.get("number_padding") or 4)
    next_num = int(courier.get("next_number") or 1)
    tracking_id = f"{courier.get('series_prefix','')}{str(next_num).zfill(padding)}"
    await db.couriers.update_one({"id": courier["id"]}, {"$inc": {"next_number": 1}})

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
    }
    await db.shipments.insert_one(ship_doc)

    # Mark order as shipped + link
    await db.pending_orders.update_one(
        {"id": order_id},
        {"$set": {
            "status": "shipped",
            "processed_at": utcnow_iso(),
            "shipment_id": ship_doc["id"],
            "tracking_id": tracking_id,
        }},
    )
    ship_doc.pop("_id", None)
    return ship_doc


@api_router.get("/orders/pending-count")
async def pending_orders_count():
    n = await db.pending_orders.count_documents({"status": "pending"})
    return {"count": n}


# ---------------------- App setup ----------------------

app.include_router(api_router)

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
