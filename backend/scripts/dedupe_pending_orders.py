"""
One-time cleanup script — remove duplicate pending_orders.

Group key: (user_id, customer_phone, customer_name, items, amount, source)
Strategy:
  • Keep the earliest `created_at` row in each group.
  • Delete all later rows in that group.
  • Skip groups where customer_phone is empty (cannot dedup safely).

Usage:
  python scripts/dedupe_pending_orders.py --dry-run
  python scripts/dedupe_pending_orders.py --apply
"""

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

from dotenv import load_dotenv

# Allow running from project root.
sys.path.insert(0, "/app/backend")

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME",   "test_database")


async def run(apply: bool) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print(f"\n📊 Scanning collection `pending_orders` in DB `{DB_NAME}` …")
    total = await db.pending_orders.count_documents({})
    print(f"   Total rows: {total}\n")

    # Aggregation: group by the 6-tuple business key, sort each group's
    # docs by created_at ASC, capture the doc-ids list, then surface
    # groups with >= 2 docs. The first id in each group is the
    # "keeper"; the rest are the duplicates to remove.
    pipeline: List[Dict[str, Any]] = [
        # Skip rows without a phone — we can't safely dedup them.
        {"$match": {"customer_phone": {"$nin": [None, ""]}}},
        # Normalise nullable numeric / string fields so equality
        # behaves the same as the file-import duplicate guard
        # (case-insensitive name / items, float-coerced amount).
        {"$addFields": {
            "_grp": {
                "user_id":        "$user_id",
                "customer_phone": "$customer_phone",
                "customer_name":  {"$toLower": {"$ifNull": ["$customer_name", ""]}},
                "items":          {"$toLower": {"$ifNull": ["$items", ""]}},
                "amount":         {"$toDouble": {"$ifNull": ["$amount", 0]}},
                "source":         {"$ifNull": ["$source", ""]},
            },
        }},
        {"$sort": {"created_at": 1, "_id": 1}},
        {"$group": {
            "_id":  "$_grp",
            "ids":  {"$push": "$id"},
            "count": {"$sum": 1},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]

    groups = await db.pending_orders.aggregate(pipeline, allowDiskUse=True).to_list(None)
    dup_groups = len(groups)
    to_delete_ids: List[str] = []
    for g in groups:
        # Keep the first (earliest created_at); delete the rest.
        to_delete_ids.extend(g["ids"][1:])
    print(f"🔍 Duplicate groups found:  {dup_groups}")
    print(f"📉 Rows scheduled to delete: {len(to_delete_ids)}\n")

    # Show a sample of 5 groups so the operator can sanity-check.
    if dup_groups:
        print("🧪 Sample of duplicate groups (first 5):")
        for g in groups[:5]:
            k = g["_id"]
            print(
                f"   • phone={k.get('customer_phone'):>14}  "
                f"name={(k.get('customer_name') or '')[:18]:<18}  "
                f"amount={k.get('amount'):>8}  "
                f"source={k.get('source')!r:<12}  "
                f"count={g['count']}"
            )
        print()

    if not apply:
        print("✅ DRY RUN — no changes made. Re-run with --apply to delete.")
        client.close()
        return

    if not to_delete_ids:
        print("✅ Nothing to delete.")
        client.close()
        return

    print(f"🗑  Deleting {len(to_delete_ids)} duplicate rows in batches of 1000 …")
    deleted_total = 0
    BATCH = 1000
    for i in range(0, len(to_delete_ids), BATCH):
        chunk = to_delete_ids[i:i + BATCH]
        r = await db.pending_orders.delete_many({"id": {"$in": chunk}})
        deleted_total += r.deleted_count
        print(f"   batch {i // BATCH + 1}: deleted {r.deleted_count}")
    print(f"\n✅ DONE. Deleted {deleted_total} rows. New total = {total - deleted_total}.")
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply",   action="store_true", help="actually delete (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run flag (default behaviour)")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run
    asyncio.run(run(apply))
