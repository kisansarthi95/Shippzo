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
    # mapping keys: order_id, customer_name, phone, address, city, state, pincode, item, amount, timestamp


class Settings(BaseModel):
    id: str = "default"
    sender: SenderAddress = Field(default_factory=SenderAddress)
    whatsapp_template: str = (
        "નમસ્તે {customer_name}, તમારું પાર્સલ {courier} દ્વારા મોકલાયું છે. "
        "Tracking ID: {tracking_id}. અપેક્ષિત ડિલિવરી: {eta_days} દિવસ."
    )
    copy_template: str = (
        "Hi {customer_name}, your order #{order_id} has been shipped via {courier}. "
        "Tracking ID: {tracking_id}. Track here: {tracking_url}"
    )
    default_eta_days: int = 7
    sheet: SheetConfig = Field(default_factory=SheetConfig)


class SettingsUpdate(BaseModel):
    sender: Optional[SenderAddress] = None
    whatsapp_template: Optional[str] = None
    copy_template: Optional[str] = None
    default_eta_days: Optional[int] = None
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
    existing = await db.couriers.count_documents({})
    if existing == 0:
        defaults = [
            Courier(name="Nandan Courier", series_prefix="ND", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.nandancourier.com"),
            Courier(name="DTDC", series_prefix="DT", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.dtdc.in"),
            Courier(name="India Post", series_prefix="IP", next_number=1, number_padding=5,
                    contact_phone="1800 266 6868", website_url="https://www.indiapost.gov.in"),
            Courier(name="ST Courier", series_prefix="ST", next_number=1, number_padding=5),
            Courier(name="Trackon", series_prefix="TR", next_number=1, number_padding=5),
        ]
        await db.couriers.insert_many([c.model_dump() for c in defaults])

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
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}}},
    ])
    cod_sum = 0.0
    async for row in cod_cursor:
        cod_sum = float(row.get("sum", 0.0))
    revenue_cursor = db.shipments.aggregate([
        {"$match": {"status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}}},
    ])
    revenue = 0.0
    async for row in revenue_cursor:
        revenue = float(row.get("sum", 0.0))
    return {
        "total": total,
        "delivered": delivered,
        "pending": pending,
        "cod_total": cod_sum,
        "revenue_total": revenue,
    }


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
