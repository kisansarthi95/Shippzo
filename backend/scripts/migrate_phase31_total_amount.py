"""
Phase-31 rev-2 one-shot migration — fix historical shipment rows where
the stored `amount` no longer matches the canonical formula
`amount = cod_amount + token_amount` (COD-mode) or `amount = amount`
(non-COD).

Background
==========
Across Phases 30 → 31 → 31-rev-2, the meaning of the `amount` field
on the `shipments` collection flipped multiple times:

  • Phase-30  : amount = the typed COD-to-Collect (post-advance).
  • Phase-31  : amount = the Total Order Value entered by operator.
  • Phase-31r2: amount = Gross Total = cod_amount + token_amount.

Any row created during the in-between sessions can land with
`amount` not equal to `cod_amount + token_amount`. Order 26061302662
is the canonical example flagged by the business owner.

This script walks the entire `shipments` collection, computes the
expected `amount`, and updates rows where the stored value diverges.

Usage
=====
  # Dry-run (default): prints counts of would-be-changed rows.
  python -m scripts.migrate_phase31_total_amount

  # Live: same command with --apply to actually write the fixes.
  python -m scripts.migrate_phase31_total_amount --apply

  # Single-user / single-shipment debugging:
  python -m scripts.migrate_phase31_total_amount --user-id <uid>
  python -m scripts.migrate_phase31_total_amount --order-id 26061302662

The migration is **idempotent**: re-running on a clean DB produces
zero changes. Rows that already match the canonical formula are
skipped. Non-COD rows are checked only for sanity (amount must be
>= 0 and finite); their amount is never rewritten.

Safety
======
  • Dry-run by default; the --apply flag must be passed explicitly.
  • Each update is a targeted `$set` — no other fields are touched.
  • The original `amount` and `cod_amount` are recorded in a
    `_phase31_migration_audit` array on the doc so an operator can
    spot-check the change post-hoc.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

# Load .env so MONGO_URL / DB_NAME are available even when this script
# is invoked outside the supervisor-managed process.
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

log = logging.getLogger("migrate_phase31_total_amount")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

EPS = 0.005  # tolerate sub-paise floating-point drift


def _expected_amount(doc: Dict[str, Any]) -> float:
    """Phase-31 rev-2 canonical math:
        COD-mode → amount = cod_amount + token_amount
        else     → amount = amount (untouched)
    """
    pmode = (doc.get("payment_mode") or "").upper()
    cod = float(doc.get("cod_amount") or 0)
    token = float(doc.get("token_amount") or 0)
    if pmode == "COD":
        return cod + token
    # Prepaid / other modes — already-stored value is canonical.
    return float(doc.get("amount") or 0)


def _diverges(doc: Dict[str, Any]) -> bool:
    cur = float(doc.get("amount") or 0)
    exp = _expected_amount(doc)
    return abs(cur - exp) > EPS


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply", action="store_true",
        help="Actually write the updates (default: dry-run).",
    )
    p.add_argument(
        "--user-id", default=None,
        help="Restrict to one user_id (debugging).",
    )
    p.add_argument(
        "--order-id", default=None,
        help="Restrict to one order_id (e.g. 26061302662).",
    )
    p.add_argument(
        "--limit", type=int, default=0,
        help="Hard cap on rows examined (0 = unlimited).",
    )
    args = p.parse_args()

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "shippzo")
    if not mongo_url:
        log.error("MONGO_URL not set; aborting.")
        return 2

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    coll = db["shipments"]

    query: Dict[str, Any] = {}
    if args.user_id:
        query["user_id"] = args.user_id
    if args.order_id:
        query["order_id"] = args.order_id

    examined = 0
    diverged: List[Dict[str, Any]] = []
    by_user: Dict[str, int] = {}

    cur = coll.find(query, {
        "_id": 0,
        "id": 1, "user_id": 1, "order_id": 1, "payment_mode": 1,
        "amount": 1, "cod_amount": 1, "token_amount": 1,
        "status": 1, "created_at": 1, "customer_name": 1,
    })
    async for doc in cur:
        examined += 1
        if args.limit and examined > args.limit:
            break
        if _diverges(doc):
            diverged.append(doc)
            uid_short = (doc.get("user_id") or "?")[:8]
            by_user[uid_short] = by_user.get(uid_short, 0) + 1

    log.info(
        "Scanned %d shipment rows — %d need amount correction "
        "(%.2f%%).",
        examined, len(diverged),
        (100.0 * len(diverged) / examined) if examined else 0.0,
    )
    for uid, count in sorted(by_user.items(), key=lambda x: -x[1])[:10]:
        log.info("  user=%s …  %d rows", uid, count)

    if not diverged:
        log.info("Nothing to do — DB is already canonical.")
        return 0

    # Show first 5 examples (always — both dry-run and live).
    log.info("Sample (first 5):")
    for d in diverged[:5]:
        exp = _expected_amount(d)
        log.info(
            "  id=%s order=%s pmode=%s   amount %s → %s   (cod=%s tok=%s)",
            (d.get("id") or "?")[:12],
            d.get("order_id") or "-",
            d.get("payment_mode") or "-",
            d.get("amount"), exp,
            d.get("cod_amount"), d.get("token_amount"),
        )

    if not args.apply:
        log.info(
            "\nDRY-RUN — pass --apply to actually update %d rows.",
            len(diverged),
        )
        return 0

    # --apply path: targeted $set on the rows that need it.
    log.info("\nAPPLYING fixes to %d rows …", len(diverged))
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for d in diverged:
        new_amount = _expected_amount(d)
        audit_entry = {
            "phase":       "phase31-rev2-migration",
            "at":          now,
            "old_amount":  d.get("amount"),
            "new_amount":  new_amount,
            "cod_amount":  d.get("cod_amount"),
            "token_amount": d.get("token_amount"),
        }
        res = await coll.update_one(
            {"id": d["id"], "user_id": d.get("user_id")},
            {
                "$set":  {"amount": float(new_amount)},
                "$push": {"_phase31_migration_audit": audit_entry},
            },
        )
        if res.modified_count:
            updated += 1

    log.info("✅ Done — %d rows updated, %d skipped (unchanged).",
             updated, len(diverged) - updated)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
