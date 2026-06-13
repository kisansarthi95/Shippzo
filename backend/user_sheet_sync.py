"""
Phase H — User personal-sheet auto-sync
========================================
One-stop helper that wires shipment lifecycle (create / status update
/ delete) to the user's OWN Google Sheet (separate from the central
Master Sheet).

Mongo state we read/write per shipment:
    user_sheet_row_num     int      Row in the user's sheet
    user_sheet_sync_status "ok" | "pending" | "skipped" | "error"
    user_sheet_synced_at   ISO timestamp
    user_sheet_last_error  str

Failed writes get queued in a dedicated collection so a small worker
can drain on retry (similar pattern to the master backup retry worker).

Public entrypoints (all best-effort, never raise):
    sync_create(db, user, shipment) -> dict
    sync_status_change(db, user, shipment, new_status) -> dict
    sync_delete(db, user, shipment, reason) -> dict
    drain_pending_queue(db, batch=5)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import sheet_writer

log = logging.getLogger("user_sheet_sync")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_sheet_cfg(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return the user-sheet config dict, normalizing defaults."""
    raw = settings.get("sheet") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "sheet_id":        str(raw.get("sheet_id") or "").strip(),
        "tab":             str(raw.get("gid") or raw.get("tab_name") or "0").strip(),
        "auto_sync_create": bool(raw.get("auto_sync_create", True)),
        "auto_sync_status": bool(raw.get("auto_sync_status", True)),
        "auto_sync_delete": bool(raw.get("auto_sync_delete", True)),
    }


async def _load_user_sheet_cfg(db, user_id: str) -> Dict[str, Any]:
    s = await db.settings.find_one({"user_id": user_id}, {"_id": 0, "sheet": 1}) or {}
    return _user_sheet_cfg(s)


def _row_payload_from_shipment(s: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Build the keyword-arg dict expected by
    `append_order_row_to_user_sheet`.

    Phase-31: now passes courier/tracking/dimension/timestamp/misc
    columns so the user's own sheet mirrors the full Master Sheet
    schema (35 columns).
    """
    return dict(
        user_id=user.get("id", "") or "",
        user_name=user.get("name") or user.get("email", "") or "",
        master_order_id=s.get("master_order_id") or "",
        order_id=s.get("order_id") or s.get("master_order_id") or "",
        name=s.get("customer_name") or "",
        phone=s.get("customer_phone") or "",
        alt_phone=s.get("customer_alt_phone") or "",
        address=s.get("address") or "",
        city=s.get("city") or "",
        state=s.get("state") or "",
        pincode=s.get("pincode") or "",
        item_type=s.get("item_type") or s.get("item") or "",
        amount=s.get("amount") or "",
        token_amount=s.get("token_amount") or "",
        weight=str(s.get("weight") or ""),
        payment_mode=s.get("payment_mode") or "",
        status=s.get("status") or "Pending",
        notice="auto-sync",
        # ── Phase-31 extensions ──
        courier_name=str(s.get("courier_name") or ""),
        courier_id=str(s.get("courier_id") or ""),
        tracking_id=str(s.get("tracking_id") or ""),
        customer_email=str(s.get("customer_email") or ""),
        customer_gstin=str(s.get("customer_gstin") or ""),
        address_line2=str(s.get("address_line2") or ""),
        box_dimensions=str(s.get("box_dimensions") or ""),
        shipment_notes=str(s.get("shipment_notes") or ""),
        category=str(s.get("category") or ""),
        variant_name=str(s.get("variant_name") or ""),
        package_type=str(s.get("package_type") or ""),
        dispatched_at=str(s.get("dispatched_at") or ""),
        shipped_at=str(s.get("shipped_at") or ""),
        delivered_at=str(s.get("delivered_at") or ""),
        imported_status=str(s.get("imported_status") or ""),
        custom_values=s.get("custom_values") or {},
    )


async def _stamp_shipment(
    db, user_id: str, shipment_id: str, *,
    status: str, row_num: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    update: Dict[str, Any] = {
        "user_sheet_sync_status": status,
        "user_sheet_synced_at":    _utcnow(),
    }
    if row_num is not None:
        update["user_sheet_row_num"] = int(row_num)
    if error is not None:
        update["user_sheet_last_error"] = str(error)[:500]
    await db.shipments.update_one(
        {"id": shipment_id, "user_id": user_id},
        {"$set": update},
    )


async def _enqueue_pending(
    db, user_id: str, shipment_id: str, *,
    op: str, payload: Dict[str, Any],
) -> None:
    """Queue a deferred sync attempt. Idempotent on (user, shipment, op)."""
    await db.user_sheet_sync_pending.update_one(
        {"user_id": user_id, "shipment_id": shipment_id, "op": op},
        {"$set": {
            "payload":    payload,
            "queued_at":  _utcnow(),
            "attempts":   0,
        }},
        upsert=True,
    )


async def sync_create(
    db, user: Dict[str, Any], shipment: Dict[str, Any],
) -> Dict[str, Any]:
    """Append a row to the user's sheet for a freshly-created shipment."""
    cfg = await _load_user_sheet_cfg(db, user["id"])
    if not cfg["sheet_id"]:
        return {"ok": False, "skipped": True, "reason": "no_sheet_connected"}
    if not cfg["auto_sync_create"]:
        return {"ok": False, "skipped": True, "reason": "auto_sync_create_off"}

    kwargs = _row_payload_from_shipment(shipment, user)
    try:
        meta = sheet_writer.append_order_row_to_user_sheet(
            sheet_id=cfg["sheet_id"], tab_name=cfg["tab"], **kwargs,
        )
    except Exception as e:
        log.warning("user-sheet create sync failed: %s", e)
        await _stamp_shipment(db, user["id"], shipment["id"],
                              status="error", error=str(e))
        await _enqueue_pending(
            db, user["id"], shipment["id"], op="create", payload=kwargs,
        )
        return {"ok": False, "queued": True, "error": str(e)}

    row = None
    try:
        row = sheet_writer.parse_row_from_updated_range(meta.get("updated_range"))
    except Exception:
        pass

    await _stamp_shipment(
        db, user["id"], shipment["id"],
        status="ok", row_num=row,
    )
    return {"ok": True, "row": row, "meta": meta}


async def sync_status_change(
    db, user: Dict[str, Any], shipment: Dict[str, Any],
    new_status: str,
    extra_notice: Optional[str] = None,
) -> Dict[str, Any]:
    """Push the new status back into the user's sheet row."""
    cfg = await _load_user_sheet_cfg(db, user["id"])
    if not cfg["sheet_id"]:
        return {"ok": False, "skipped": True, "reason": "no_sheet_connected"}
    if not cfg["auto_sync_status"]:
        return {"ok": False, "skipped": True, "reason": "auto_sync_status_off"}

    row = shipment.get("user_sheet_row_num")
    if not row:
        # Never synced before — try a backfill create instead so the
        # row exists going forward.
        return await sync_create(db, user, shipment)

    try:
        meta = sheet_writer.update_user_sheet_row_status(
            sheet_id=cfg["sheet_id"], tab_name_or_gid=cfg["tab"],
            row_num=int(row), status=new_status,
            extra_notice=extra_notice,
        )
        if meta.get("skipped"):
            await _stamp_shipment(db, user["id"], shipment["id"],
                                  status="skipped", error=meta.get("reason"))
            return {"ok": False, "skipped": True, **meta}
        await _stamp_shipment(db, user["id"], shipment["id"], status="ok")
        return {"ok": True, **meta}
    except Exception as e:
        log.warning("user-sheet status sync failed: %s", e)
        await _stamp_shipment(db, user["id"], shipment["id"],
                              status="error", error=str(e))
        await _enqueue_pending(
            db, user["id"], shipment["id"], op="status",
            payload={"new_status": new_status, "extra_notice": extra_notice,
                     "row": int(row)},
        )
        return {"ok": False, "queued": True, "error": str(e)}


async def sync_delete(
    db, user: Dict[str, Any], shipment: Dict[str, Any],
    reason: str = "",
) -> Dict[str, Any]:
    """Tombstone the user-sheet row when a shipment is deleted."""
    cfg = await _load_user_sheet_cfg(db, user["id"])
    if not cfg["sheet_id"] or not cfg["auto_sync_delete"]:
        return {"ok": False, "skipped": True}
    row = shipment.get("user_sheet_row_num")
    if not row:
        return {"ok": False, "skipped": True, "reason": "no_row_linked"}
    try:
        meta = sheet_writer.mark_user_sheet_row_deleted(
            sheet_id=cfg["sheet_id"], tab_name_or_gid=cfg["tab"],
            row_num=int(row), reason=reason,
        )
        return {"ok": True, **meta}
    except Exception as e:
        log.warning("user-sheet delete sync failed: %s", e)
        await _enqueue_pending(
            db, user["id"], shipment["id"], op="delete",
            payload={"reason": reason, "row": int(row)},
        )
        return {"ok": False, "queued": True, "error": str(e)}


async def drain_pending_queue(db, batch: int = 5) -> Dict[str, Any]:
    """Periodic worker — retries up to `batch` queued sync ops per call."""
    drained = 0
    failed  = 0
    cursor = db.user_sheet_sync_pending.find().limit(batch)
    docs = await cursor.to_list(length=batch)
    for doc in docs:
        op = doc.get("op")
        attempts = int(doc.get("attempts") or 0)
        # Bail out if the shipment was deleted or user removed.
        ship = await db.shipments.find_one(
            {"id": doc["shipment_id"], "user_id": doc["user_id"]},
            {"_id": 0},
        )
        user = await db.users.find_one({"id": doc["user_id"]}, {"_id": 0}) if ship else None
        if not ship or not user:
            await db.user_sheet_sync_pending.delete_one({"_id": doc["_id"]})
            continue

        try:
            if op == "create":
                res = await sync_create(db, user, ship)
            elif op == "status":
                res = await sync_status_change(
                    db, user, ship,
                    new_status=doc["payload"].get("new_status") or ship.get("status", "Pending"),
                    extra_notice=doc["payload"].get("extra_notice"),
                )
            elif op == "delete":
                res = await sync_delete(
                    db, user, ship,
                    reason=doc["payload"].get("reason") or "queued retry",
                )
            else:
                res = {"ok": False, "skipped": True, "reason": f"unknown op {op}"}

            if res.get("ok") or res.get("skipped"):
                await db.user_sheet_sync_pending.delete_one({"_id": doc["_id"]})
                drained += 1
            else:
                failed += 1
                # Back off attempts; auto-give up after 10 tries.
                if attempts + 1 >= 10:
                    await db.user_sheet_sync_pending.delete_one({"_id": doc["_id"]})
                else:
                    await db.user_sheet_sync_pending.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"attempts": attempts + 1, "last_try_at": _utcnow()}},
                    )
        except Exception as e:
            failed += 1
            log.exception("queue drain iteration failed: %s", e)

    return {"drained": drained, "failed": failed, "examined": len(docs)}
