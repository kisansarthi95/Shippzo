from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import io
import csv
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
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
    series_prefix: str = ""   # e.g. "ND" or ""
    next_number: int = 1      # next numeric part for auto-increment
    number_padding: int = 4   # pad with zeros, e.g. 0001
    created_at: str = Field(default_factory=utcnow_iso)


class CourierCreate(BaseModel):
    name: str
    series_prefix: Optional[str] = ""
    next_number: Optional[int] = 1
    number_padding: Optional[int] = 4


class CourierUpdate(BaseModel):
    name: Optional[str] = None
    series_prefix: Optional[str] = None
    next_number: Optional[int] = None
    number_padding: Optional[int] = None


class SenderAddress(BaseModel):
    name: str = ""
    phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    show_contact: bool = True


class Settings(BaseModel):
    id: str = "default"
    sender: SenderAddress = Field(default_factory=SenderAddress)
    whatsapp_template: str = (
        "નમસ્તે {customer_name}, તમારું પાર્સલ {courier} દ્વારા મોકલાયું છે. "
        "Tracking ID: {tracking_id}. અપેક્ષિત ડિલિવરી: {eta_days} દિવસ."
    )
    default_eta_days: int = 7


class SettingsUpdate(BaseModel):
    sender: Optional[SenderAddress] = None
    whatsapp_template: Optional[str] = None
    default_eta_days: Optional[int] = None


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: str = ""
    customer_name: str
    customer_phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    payment_mode: str = "Prepaid"   # COD | Prepaid
    cod_amount: float = 0.0
    weight: str = ""                 # e.g. "0.5 kg"
    item_description: str = ""
    status: str = "Pending"          # Pending | Delivered | Cancelled
    created_at: str = Field(default_factory=utcnow_iso)
    delivered_at: Optional[str] = None


class ShipmentCreate(BaseModel):
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: Optional[str] = ""
    customer_name: str
    customer_phone: Optional[str] = ""
    address_line1: Optional[str] = ""
    address_line2: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    pincode: Optional[str] = ""
    payment_mode: Optional[str] = "Prepaid"
    cod_amount: Optional[float] = 0.0
    weight: Optional[str] = ""
    item_description: Optional[str] = ""


class ShipmentUpdate(BaseModel):
    tracking_id: Optional[str] = None
    courier_id: Optional[str] = None
    courier_name: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    payment_mode: Optional[str] = None
    cod_amount: Optional[float] = None
    weight: Optional[str] = None
    item_description: Optional[str] = None
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
            Courier(name="Nandan Courier", series_prefix="ND", next_number=1, number_padding=5),
            Courier(name="DTDC", series_prefix="DT", next_number=1, number_padding=5),
            Courier(name="ST Courier", series_prefix="ST", next_number=1, number_padding=5),
            Courier(name="Trackon", series_prefix="TR", next_number=1, number_padding=5),
            Courier(name="Other", series_prefix="", next_number=1, number_padding=4),
        ]
        await db.couriers.insert_many([c.model_dump() for c in defaults])

    s = await db.settings.find_one({"id": "default"})
    if not s:
        await db.settings.insert_one(Settings().model_dump())


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
    """Increments next_number and returns the tracking id that was consumed."""
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
    update = {}
    if payload.sender is not None:
        update["sender"] = payload.sender.model_dump()
    if payload.whatsapp_template is not None:
        update["whatsapp_template"] = payload.whatsapp_template
    if payload.default_eta_days is not None:
        update["default_eta_days"] = payload.default_eta_days
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.settings.find_one_and_update(
        {"id": "default"},
        {"$set": update},
        upsert=True,
        return_document=True,
    )
    return Settings(**strip_id(res))


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
        ]
    docs = await db.shipments.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return [Shipment(**d) for d in docs]


@api_router.get("/shipments/stats")
async def shipments_stats():
    total = await db.shipments.count_documents({})
    delivered = await db.shipments.count_documents({"status": "Delivered"})
    pending = await db.shipments.count_documents({"status": "Pending"})
    cod_total_cursor = db.shipments.aggregate([
        {"$match": {"payment_mode": "COD", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$cod_amount"}}},
    ])
    cod_sum = 0.0
    async for row in cod_total_cursor:
        cod_sum = float(row.get("sum", 0.0))
    return {
        "total": total,
        "delivered": delivered,
        "pending": pending,
        "cod_total": cod_sum,
    }


@api_router.get("/shipments/export/csv", response_class=PlainTextResponse)
async def export_csv():
    docs = await db.shipments.find({}, {"_id": 0}).sort("created_at", -1).to_list(5000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tracking ID", "Courier", "Customer", "Phone",
        "Address Line 1", "Address Line 2", "City", "State", "Pincode",
        "Payment Mode", "COD Amount", "Weight", "Item",
        "Status", "Created At", "Delivered At",
    ])
    for d in docs:
        writer.writerow([
            d.get("tracking_id", ""), d.get("courier_name", ""),
            d.get("customer_name", ""), d.get("customer_phone", ""),
            d.get("address_line1", ""), d.get("address_line2", ""),
            d.get("city", ""), d.get("state", ""), d.get("pincode", ""),
            d.get("payment_mode", ""), d.get("cod_amount", 0),
            d.get("weight", ""), d.get("item_description", ""),
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
    # Resolve courier name if courier_id provided
    if data.get("courier_id") and not data.get("courier_name"):
        c = await db.couriers.find_one({"id": data["courier_id"]}, {"_id": 0})
        if c:
            data["courier_name"] = c.get("name", "")
    shipment = Shipment(**data)
    await db.shipments.insert_one(shipment.model_dump())
    return shipment


@api_router.put("/shipments/{shipment_id}", response_model=Shipment)
async def update_shipment(shipment_id: str, payload: ShipmentUpdate):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update and update["status"] == "Delivered":
        update["delivered_at"] = utcnow_iso()
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
