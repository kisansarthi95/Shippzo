"""Phase-31 migration sanity check.

Reads db.shipments directly to verify the canonical invariant:
    amount == cod_amount + token_amount  (for COD rows)

Spot-checks:
  1) Order ID 26061302662 (historically wrong) — report values.
  2) 3 random COD shipments — verify invariant.
  3) Aggregate scan — count any remaining COD rows violating math.
"""
import asyncio
import os
import random
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

backend_env = Path(__file__).parent.parent / ".env"
load_dotenv(backend_env)

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


async def main() -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print("=" * 70)
    print("Phase-31 Migration Sanity — db.shipments canonical math")
    print(f"DB: {DB_NAME}")
    print("=" * 70)

    # 1) Target the specific historically-wrong order
    target = await db.shipments.find_one(
        {"order_id": "26061302662"}, {"_id": 0},
    )
    print("\n[1] Order ID 26061302662:")
    if target:
        amt = float(target.get("amount") or 0)
        cod = float(target.get("cod_amount") or 0)
        tok = float(target.get("token_amount") or 0)
        derived = cod + tok
        match = abs(amt - derived) < 1e-6
        print(f"    payment_mode = {target.get('payment_mode')!r}")
        print(f"    amount       = {amt}")
        print(f"    cod_amount   = {cod}")
        print(f"    token_amount = {tok}")
        print(f"    cod+token    = {derived}")
        print(f"    INVARIANT    = {'PASS' if match else 'FAIL'}")
    else:
        # try master_order_id
        target = await db.shipments.find_one(
            {"master_order_id": "26061302662"}, {"_id": 0},
        )
        if target:
            amt = float(target.get("amount") or 0)
            cod = float(target.get("cod_amount") or 0)
            tok = float(target.get("token_amount") or 0)
            derived = cod + tok
            match = abs(amt - derived) < 1e-6
            print("    (matched on master_order_id)")
            print(f"    payment_mode = {target.get('payment_mode')!r}")
            print(f"    amount       = {amt}")
            print(f"    cod_amount   = {cod}")
            print(f"    token_amount = {tok}")
            print(f"    cod+token    = {derived}")
            print(f"    INVARIANT    = {'PASS' if match else 'FAIL'}")
        else:
            print("    NOT FOUND — no shipment with that order_id/master_order_id")

    # 2) Random 3 COD shipments
    print("\n[2] Random 3 COD shipments:")
    cod_total = await db.shipments.count_documents({"payment_mode": "COD"})
    print(f"    Total COD shipments in db: {cod_total}")
    if cod_total == 0:
        print("    No COD rows to spot-check.")
    else:
        # Use random skip for sample
        sample_size = min(3, cod_total)
        cursor = db.shipments.aggregate([
            {"$match": {"payment_mode": "COD"}},
            {"$sample": {"size": sample_size}},
            {"$project": {
                "_id": 0,
                "id": 1,
                "order_id": 1,
                "amount": 1,
                "cod_amount": 1,
                "token_amount": 1,
            }},
        ])
        i = 0
        async for doc in cursor:
            i += 1
            amt = float(doc.get("amount") or 0)
            cod = float(doc.get("cod_amount") or 0)
            tok = float(doc.get("token_amount") or 0)
            derived = cod + tok
            match = abs(amt - derived) < 1e-6
            print(
                f"    [{i}] id={doc.get('id', '?')[:8]} "
                f"order_id={doc.get('order_id', '?')!r} "
                f"amount={amt} cod={cod} token={tok} "
                f"cod+tok={derived}  {'OK' if match else 'MISMATCH'}"
            )

    # 3) Aggregate scan — any remaining COD violators?
    print("\n[3] Aggregate scan of all COD rows:")
    bad = 0
    total = 0
    cursor = db.shipments.find(
        {"payment_mode": "COD"},
        {"_id": 0, "id": 1, "order_id": 1, "amount": 1,
         "cod_amount": 1, "token_amount": 1},
    )
    bad_samples = []
    async for d in cursor:
        total += 1
        amt = float(d.get("amount") or 0)
        cod = float(d.get("cod_amount") or 0)
        tok = float(d.get("token_amount") or 0)
        if abs(amt - (cod + tok)) >= 1e-6:
            bad += 1
            if len(bad_samples) < 5:
                bad_samples.append(d)
    print(f"    Scanned: {total}    Violators: {bad}")
    if bad_samples:
        for d in bad_samples:
            print(
                f"    Bad: order_id={d.get('order_id')!r} "
                f"amount={d.get('amount')} cod={d.get('cod_amount')} "
                f"token={d.get('token_amount')}"
            )

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
