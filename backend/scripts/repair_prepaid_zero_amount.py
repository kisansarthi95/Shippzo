"""One-off repair script — restore `amount` on Prepaid shipments
that were silently zeroed by the Phase-34 `compute_order_amounts`
helper before the 2026-06-25 hotfix.

What the bug did
----------------
On POST /api/shipments and PUT /api/shipments, the helper was called
unconditionally. For a Prepaid row the frontend sends the entered
total in `amount`, but the helper treats its input as `cod_amount`
and returns `amount = cod + token`. For a Prepaid row both `cod_in`
and `token` were typically empty, so `amount` got set to 0 and the
order value was lost.

How this script repairs
-----------------------
1. Scan `shipments` for rows where:
     * payment_mode == "Prepaid"   (or != "COD" and amount == 0)
     * amount == 0
     * `pending_order_id` is set (Smart Paste / pending-flow rows)
2. Look up the source pending order by `pending_order_id`.
3. Restore the shipment's `amount` from the pending order's `amount`
   (Smart Paste writes the Total Order Value here).
4. If pending order has no usable amount, leave the shipment alone
   and log it for manual review.

Safe to re-run — idempotent (only touches rows where amount is still 0).

Usage
-----
    cd /app/backend && python -m scripts.repair_prepaid_zero_amount
    cd /app/backend && python -m scripts.repair_prepaid_zero_amount --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Configure logging BEFORE any imports that might use the root logger.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_log = logging.getLogger("repair_prepaid_zero_amount")


async def repair(dry_run: bool = False) -> Dict[str, Any]:
    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        _log.error("MONGO_URL / DB_NAME must be set in env.")
        return {"ok": False, "reason": "missing_env"}

    client = AsyncIOMotorClient(mongo_url)
    db     = client[db_name]

    # Scan: prepaid rows with amount=0. We attempt repair via:
    #   1. pending_order_id link (modern Smart-Paste path), OR
    #   2. order_id ↔ pending.order_id / pending.master_order_id
    #      (rows ingested before pending_order_id was wired up, OR
    #      where the link field was dropped on update).
    query = {
        "amount": {"$in": [0, 0.0, None]},
        "$or": [
            {"payment_mode": "Prepaid"},
            # Defensive: also pick rows where payment_mode is
            # empty/missing — those were Prepaid by default
            # in older code paths.
            {"payment_mode": {"$exists": False}},
            {"payment_mode": ""},
        ],
    }

    candidates: List[Dict[str, Any]] = await db.shipments.find(
        query, {"_id": 0},
    ).to_list(length=None)

    _log.info(
        "Found %d candidate Prepaid shipments with amount=0 (linked to pending).",
        len(candidates),
    )

    stats = {
        "scanned":          len(candidates),
        "repaired":         0,
        "no_pending":       0,
        "pending_no_value": 0,
        "errors":           0,
        "examples":         [],
    }

    repair_ts = datetime.now(timezone.utc).isoformat()

    for ship in candidates:
        poid          = (ship.get("pending_order_id") or "").strip()
        ship_order_id = (ship.get("order_id") or "").strip()
        ship_master   = (ship.get("master_order_id") or "").strip()
        ship_user_id  = ship.get("user_id") or ""

        pending = None
        match_source = ""

        # Path 1: explicit pending_order_id link (preferred).
        if poid:
            pending = await db.pending_orders.find_one(
                {"id": poid},
                {"_id": 0, "amount": 1, "cod_amount": 1,
                 "token_amount": 1, "id": 1, "user_id": 1,
                 "order_id": 1, "master_order_id": 1},
            )
            if pending:
                match_source = "pending_order_id"

        # Path 2: fallback via order_id / master_order_id within
        #         the same user's pending rows. Smart-Paste copies
        #         pending.order_id verbatim onto the shipment, so
        #         this is a high-confidence match.
        if pending is None and ship_user_id and (ship_order_id or ship_master):
            or_terms: List[Dict[str, Any]] = []
            if ship_order_id:
                or_terms.append({"order_id":        ship_order_id})
                or_terms.append({"master_order_id": ship_order_id})
            if ship_master and ship_master != ship_order_id:
                or_terms.append({"order_id":        ship_master})
                or_terms.append({"master_order_id": ship_master})
            pending = await db.pending_orders.find_one(
                {"user_id": ship_user_id, "$or": or_terms},
                {"_id": 0, "amount": 1, "cod_amount": 1,
                 "token_amount": 1, "id": 1, "user_id": 1,
                 "order_id": 1, "master_order_id": 1},
                sort=[("created_at", -1)],
            )
            if pending:
                match_source = "order_id_fallback"

        if not pending:
            stats["no_pending"] += 1
            _log.warning(
                "Shipment %s (order_id=%s): no matching pending order found; skipping.",
                ship.get("id", "?")[:8], ship_order_id or "?",
            )
            continue

        # Prefer pending.amount (the Smart-Paste Total Order Value).
        # Fall back to cod_amount only if amount was 0 (very old rows).
        restored_amount = float(pending.get("amount") or 0)
        if restored_amount <= 0:
            restored_amount = float(pending.get("cod_amount") or 0)
        if restored_amount <= 0:
            stats["pending_no_value"] += 1
            _log.warning(
                "Shipment %s: pending order has no usable amount (amount=%s cod=%s); skipping.",
                ship.get("id", "?")[:8],
                pending.get("amount"),
                pending.get("cod_amount"),
            )
            continue

        sample = {
            "shipment_id":     ship.get("id"),
            "tracking_id":     ship.get("tracking_id"),
            "order_id":        ship.get("order_id"),
            "customer_name":   ship.get("customer_name"),
            "old_amount":      ship.get("amount"),
            "restored_amount": restored_amount,
            "payment_mode":    ship.get("payment_mode") or "(empty)",
            "match_source":    match_source,
        }
        if len(stats["examples"]) < 10:
            stats["examples"].append(sample)

        if dry_run:
            stats["repaired"] += 1
            continue

        try:
            res = await db.shipments.update_one(
                {
                    "id":      ship["id"],
                    "user_id": ship.get("user_id"),
                    # Defensive re-check: only update if still zero.
                    "amount":  {"$in": [0, 0.0, None]},
                },
                {
                    "$set": {
                        "amount":             restored_amount,
                        "amount_repaired_at": repair_ts,
                        "amount_repair_note": (
                            f"Phase-34 Prepaid zero-amount hotfix; restored from "
                            f"pending.id={pending.get('id', '?')[:8]} "
                            f"via {match_source}"
                        ),
                    },
                },
            )
            if res.modified_count:
                stats["repaired"] += 1
            else:
                # Already repaired by an earlier run or race.
                pass
        except Exception:
            stats["errors"] += 1
            _log.exception(
                "Failed to repair shipment %s",
                ship.get("id", "?"),
            )

    _log.info("---- Repair summary ----")
    for k, v in stats.items():
        if k == "examples":
            continue
        _log.info("  %s: %s", k, v)
    if stats["examples"]:
        _log.info("Examples (max 10):")
        for ex in stats["examples"]:
            _log.info("  %s", ex)
    _log.info("Dry run: %s", dry_run)
    client.close()
    return {"ok": True, **stats}


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair Prepaid shipments with amount=0")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Scan + report only; do NOT write any changes.",
    )
    args = ap.parse_args()

    result = asyncio.run(repair(dry_run=args.dry_run))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
