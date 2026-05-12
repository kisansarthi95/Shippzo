"""
One-time backfill: source_meta.raw_payload for webhook-ingested pending orders
(Phase F3.7 Admin Card prerequisite).

Background
----------
Pending orders ingested from webhooks BEFORE the raw_payload capture was
added do not have `source_meta.raw_payload`. The Admin Card on the
edit-pending screen therefore renders nothing for them.

Recovery strategy
-----------------
For every pending_orders doc where:
    source == "webhook"
    source_meta.raw_payload missing
look up the parent webhook via source_meta.webhook_id, walk its
`recent_samples` ring buffer (capped at 10–20 entries server-side), and
match each sample by:
    payload.order.uuid == external_order_id    (Dukaan-style)
or  payload.order.id   == external_order_id    (Shopify-style)
or  payload.uuid       == external_order_id    (flat payloads)
or  payload.id         == external_order_id    (flat payloads)
or  payload.order.order_number == external_order_id (legacy)

When matched, write `source_meta.raw_payload = sample.payload`.

NOTE: Orders ingested too long ago to still be inside the ring buffer
cannot be recovered — that data is gone. They're counted as "aged out"
and listed once at the end so the operator knows the ceiling.

Usage:
    cd /app/backend && python -m scripts.backfill_admin_card_payload
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


def _id_match(payload: Dict[str, Any], target_id: str) -> bool:
    """Return True if any well-known id field inside `payload` equals
    the target external_order_id (case-insensitive string match)."""
    if not target_id:
        return False
    target = str(target_id).strip().lower()
    if not target:
        return False

    def _str(v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip().lower()

    order = payload.get("order") if isinstance(payload, dict) else None
    if isinstance(order, dict):
        for k in ("uuid", "id", "order_id", "order_number",
                  "display_order_id", "display_id", "name"):
            if _str(order.get(k)) == target:
                return True
    # Flat (Shopify-ish top-level)
    for k in ("uuid", "id", "order_id", "order_number",
              "display_order_id", "display_id", "name"):
        if _str(payload.get(k)) == target:
            return True
    return False


def _pick_payload(samples: List[Dict[str, Any]], target_id: str) -> Optional[Dict[str, Any]]:
    """Walk recent_samples (newest is last in our ring buffer) in
    REVERSE order so the most-recent matching payload wins for orders
    that re-ping (Dukaan re-pushes on every state change)."""
    if not samples:
        return None
    for sample in reversed(samples):
        payload = sample.get("payload") if isinstance(sample, dict) else None
        if not isinstance(payload, dict):
            continue
        if _id_match(payload, target_id):
            return payload
    return None


async def main() -> None:
    load_dotenv()
    mongo_url = os.environ["MONGO_URL"]
    db_name   = os.environ["DB_NAME"]
    client    = AsyncIOMotorClient(mongo_url)
    db        = client[db_name]

    print(f"Connecting to {db_name} …")

    # 1. Pull every webhook-source pending order that's missing the payload.
    query = {
        "source": "webhook",
        "$or": [
            {"source_meta.raw_payload": {"$exists": False}},
            {"source_meta.raw_payload": None},
            {"source_meta.raw_payload": {}},
        ],
    }
    cursor = db.pending_orders.find(query)
    candidates: List[Dict[str, Any]] = []
    async for doc in cursor:
        candidates.append(doc)
    total = len(candidates)
    print(f"Found {total} candidate pending_orders missing raw_payload.")

    if total == 0:
        print("Nothing to backfill — done.")
        return

    # 2. Group by source_meta.webhook_id so we hit user_webhooks once per
    # webhook. Some legacy docs might have webhook_id empty (pre-Phase F3)
    # — those are bucketed under "" and skipped (no parent to look up).
    by_wh: Dict[str, List[Dict[str, Any]]] = {}
    for d in candidates:
        wh_id = ((d.get("source_meta") or {}).get("webhook_id") or "").strip()
        by_wh.setdefault(wh_id, []).append(d)
    print(f"Spread across {len(by_wh)} distinct webhook(s).")

    backfilled       = 0
    aged_out         = 0
    no_webhook       = 0
    webhook_missing  = 0
    no_external_id   = 0
    aged_examples: List[str] = []

    for wh_id, docs in by_wh.items():
        if not wh_id:
            no_webhook += len(docs)
            print(f"  ⚠ {len(docs)} doc(s) have no source_meta.webhook_id — skipped.")
            continue

        wh = await db.user_webhooks.find_one({"id": wh_id})
        if not wh:
            webhook_missing += len(docs)
            print(f"  ⚠ Webhook {wh_id} no longer exists — {len(docs)} doc(s) skipped.")
            continue

        samples = wh.get("recent_samples") or []
        if not samples:
            aged_out += len(docs)
            print(f"  ⚠ Webhook {wh_id} has 0 recent_samples — {len(docs)} doc(s) aged out.")
            continue

        for d in docs:
            external_id = (
                (d.get("source_meta") or {}).get("external_order_id")
                or d.get("external_order_id")
                or d.get("order_id")
                or ""
            )
            if not external_id:
                no_external_id += 1
                continue

            payload = _pick_payload(samples, external_id)
            if payload is None:
                aged_out += 1
                if len(aged_examples) < 5:
                    aged_examples.append(
                        f"id={d.get('id') or '<?>'} ext_id={external_id} "
                        f"wh={wh_id[:8]}…"
                    )
                continue

            await db.pending_orders.update_one(
                {"id": d["id"]},
                {"$set": {"source_meta.raw_payload": payload}},
            )
            backfilled += 1

    # 3. Summary.
    print()
    print("=" * 60)
    print("BACKFILL SUMMARY")
    print("=" * 60)
    print(f"  Total candidates scanned : {total}")
    print(f"  ✅ Backfilled             : {backfilled}")
    print(f"  📜 Aged out (ring buffer): {aged_out}")
    print(f"  🚫 No source webhook_id  : {no_webhook}")
    print(f"  🚫 Parent webhook deleted: {webhook_missing}")
    print(f"  🚫 No external_order_id  : {no_external_id}")
    if aged_examples:
        print()
        print("  Examples of aged-out orders (sample of 5):")
        for ex in aged_examples:
            print(f"    • {ex}")
    print()
    print("Note: Aged-out orders cannot recover their payload — the original")
    print("ring buffer entries are gone. Future webhook orders will be saved")
    print("with raw_payload automatically (Phase F3.7 hook landed).")


if __name__ == "__main__":
    asyncio.run(main())
