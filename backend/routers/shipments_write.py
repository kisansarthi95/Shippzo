"""
Shipment write/mutation endpoints — Phase-5c-2 incremental refactor.

Extracts the 4 heaviest shipment-creation/modification endpoints out
of server.py monolith. These all share the same complex pipeline:
  plan-room gate → wallet pre-flight → master-sheet backup →
  Mongo insert → user-sheet auto-sync → wallet charge → counter bump.

Endpoints relocated (all under /api):

  POST   /shipments                          create_shipment       (heaviest)
  PUT    /shipments/{shipment_id}            update_shipment        (re-charge + sheet sync)
  DELETE /shipments/{shipment_id}            delete_shipment        (sheet tombstone)
  POST   /orders/pending/{order_id}/ship     ship_pending_order     (Pending → Shipment)

Pattern: late-binding `init()` — same as routers/wallet.py.

Heavy dependencies pulled in via late-bind:
  • Models: Shipment, ShipmentCreate, ShipmentUpdate, ShipOrderRequest
  • Plan/wallet:  plan_room_status, wallet_classify_and_cost,
                  wallet_require, wallet_charge, wallet_balance,
                  bump_label_usage
  • Master-sheet: _backup_shipment_to_master_sheet,
                  sheet_parse_row_from_updated_range,
                  sheet_update_row_status, sheet_mark_row_deleted
  • Custom-fields: _write_custom_values_to_user_sheet_bg
  • Order-id:    generate_master_order_id
  • Misc:        utcnow_iso, strip_id, user_has_feature
"""
from datetime import datetime
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException


shipments_write_router = APIRouter(prefix="/api", tags=["shipments-write"])


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.shipments_write")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        # Models
        Shipment,
        ShipmentCreate,
        ShipmentUpdate,
        ShipOrderRequest,
        # Plan + wallet helpers
        plan_room_status,
        wallet_classify_and_cost,
        wallet_require,
        wallet_charge,
        wallet_balance,
        bump_label_usage,
        # Master sheet helpers
        _backup_shipment_to_master_sheet,
        sheet_parse_row_from_updated_range,
        sheet_update_row_status,
        sheet_mark_row_deleted,
        # Custom fields helper
        _write_custom_values_to_user_sheet_bg,
        # Master Order ID generator
        generate_master_order_id,
        # Misc helpers
        utcnow_iso,
        strip_id,
        user_has_feature,
    )

    # =================  POST /shipments  ============================

    @shipments_write_router.post("/shipments", response_model=Shipment)
    async def create_shipment(
        payload: ShipmentCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
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
        if room.get("plan_expired"):
            exp = room.get("plan_expires_at")
            try:
                exp_dt = datetime.fromisoformat(str(exp))
                exp_str = exp_dt.strftime("%d %b %Y")
            except Exception:
                exp_str = str(exp) or "the renewal date"
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Your {room.get('plan_name','plan')} subscription "
                    f"expired on {exp_str}. Renew from Plans to keep "
                    "creating labels."
                ),
            )
        if room["daily_blocked"]:
            raise HTTPException(
                status_code=402,
                detail=(
                    "Daily limit reached (100/day on Platinum). "
                    "Please try again tomorrow."
                ),
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
            data.get("city", ""), data.get("state", ""),
            str(data.get("pincode", "")),
        ])).strip()
        # Per-user AI rate card so Settings → AI Processing Charges
        # takes effect immediately. Defaults 0.5/1/2 used when not set.
        _s = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1,
             "ai_cost_complex": 1},
        ) or {}
        ai_costs = {
            "simple":  float(_s.get("ai_cost_simple", 0.5)),
            "medium":  float(_s.get("ai_cost_medium", 1.0)),
            "complex": float(_s.get("ai_cost_complex", 2.0)),
        }
        # LLM-backed complexity classification with safe heuristic fallback.
        breakdown, ai_reason = await wallet_classify_and_cost(
            current_user, addr_text, plan_has_room, ai_costs=ai_costs,
        )
        breakdown = await wallet_require(
            db, current_user, addr_text, plan_has_room,
            complexity_override=breakdown.ai_complexity,
            ai_costs=ai_costs,
        )

        if data.get("courier_id") and not data.get("courier_name"):
            c = await db.couriers.find_one(
                {"id": data["courier_id"], "user_id": current_user["id"]},
                {"_id": 0},
            )
            if c:
                data["courier_name"] = c.get("name", "")
        if data.get("payment_mode") == "COD":
            data["cod_amount"] = float(
                data.get("amount") or data.get("cod_amount") or 0,
            )
        else:
            data["cod_amount"] = 0.0
        data["amount"] = float(
            data.get("amount") or data.get("cod_amount") or 0,
        )
        if data.get("items") is None:
            data["items"] = []
        if data.get("custom_values") is None:
            data["custom_values"] = {}

        # ── Phase-7d/e Master Order ID + User Order ID for manual create ──
        settings_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "order_id_auto_generate": 1},
        ) or {}
        auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
        incoming_master = str(data.get("master_order_id") or "").strip()
        user_order_id = str(data.get("order_id") or "").strip()
        if auto_gen:
            if incoming_master and re.match(r"^\d{6}\d{5,}$", incoming_master):
                # Frontend sent a pre-allocated master ID — trust it
                # (atomic peek/consume already happened upstream).
                master_oid = incoming_master
            else:
                master_oid = await generate_master_order_id()
            retries = 0
            while await db.shipments.find_one(
                {"master_order_id": master_oid,
                 "user_id": current_user["id"]}, {"_id": 1},
            ):
                master_oid = await generate_master_order_id()
                retries += 1
                if retries > 5:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "Could not allocate a unique Master Order ID "
                            "— retry."
                        ),
                    )
            data["master_order_id"] = master_oid
            if not user_order_id:
                user_order_id = master_oid
            data["order_id"] = user_order_id
        else:
            if not user_order_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Order ID is required when Auto-Generate is OFF. "
                        "Enter your own Order ID or enable Auto-Generate "
                        "in Settings."
                    ),
                )
            data["master_order_id"] = ""
            data["order_id"] = user_order_id

        shipment = Shipment(**data)
        doc = shipment.model_dump()
        doc["user_id"] = current_user["id"]

        # ── Mandatory Master Sheet backup (all plans) ──
        sheet_meta = await _backup_shipment_to_master_sheet(
            current_user=current_user,
            data=doc,
            notice="via Add Shipment",
        )
        if sheet_meta.get("deferred"):
            doc["master_backup_status"] = "pending"
            doc["master_backup_payload"] = (
                sheet_meta.get("_pending_payload") or {}
            )
            doc["master_backup_last_error_at"] = utcnow_iso()
        elif (sheet_meta and sheet_meta.get("updated_range")
              and sheet_parse_row_from_updated_range):
            try:
                row = sheet_parse_row_from_updated_range(
                    sheet_meta["updated_range"],
                )
                if row:
                    doc["sheet_row_num"] = int(row)
                    if hasattr(shipment, "sheet_row_num"):
                        shipment.sheet_row_num = int(row)
                    doc["master_backup_status"] = "ok"
            except Exception:
                pass

        await db.shipments.insert_one(doc)

        # Phase H: User personal-sheet auto-sync (best-effort).
        try:
            import user_sheet_sync as _uss
            await _uss.sync_create(db, current_user, doc)
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (create) failed (non-fatal)",
            )

        # Best-effort: write custom-field values to user's personal sheet.
        await _write_custom_values_to_user_sheet_bg(
            current_user, doc.get("custom_values") or {},
        )
        # Only bump plan counter when the plan actually covered this label.
        if plan_has_room:
            await bump_label_usage(db, current_user)
        # Debit wallet (safe no-op for free-trial + trial-room combo).
        await wallet_charge(db, current_user, doc["id"], breakdown)
        return shipment

    # =================  PUT /shipments/{id}  ========================

    @shipments_write_router.put(
        "/shipments/{shipment_id}", response_model=Shipment,
    )
    async def update_shipment(
        shipment_id: str,
        payload: ShipmentUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        update = {
            k: v for k, v in payload.model_dump().items() if v is not None
        }
        if "status" in update and update["status"] == "Delivered":
            update["delivered_at"] = utcnow_iso()
        if "amount" in update:
            update["cod_amount"] = (
                float(update["amount"])
                if update.get("payment_mode", "") == "COD" else 0.0
            )
        if not update:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Two-Way Status Sync: detect status transitions BEFORE mutation
        # so we can write the new value to the Master Sheet row if linked.
        new_status = update.get("status")
        prev_doc = None
        if new_status is not None:
            prev_doc = await db.shipments.find_one(
                {"id": shipment_id, "user_id": current_user["id"]},
                {"_id": 0},
            )

        res = await db.shipments.find_one_and_update(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": update},
            return_document=True,
        )
        if not res:
            raise HTTPException(status_code=404, detail="Shipment not found")

        # Best-effort Master-Sheet write-back. Plan-gated:
        # `sheet_two_way_status_sync` must be enabled for the user's plan.
        if (
            new_status is not None
            and prev_doc is not None
            and (prev_doc.get("status") or "") != new_status
            and prev_doc.get("sheet_row_num")
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            try:
                tracking = (
                    prev_doc.get("tracking_id")
                    or res.get("tracking_id") or ""
                )
                extra = f"Tracking: {tracking}" if tracking else None
                sheet_update_row_status(
                    int(prev_doc["sheet_row_num"]),
                    new_status,
                    extra_notice=extra,
                )
                _logger.info(
                    f"Sheet status sync OK: row="
                    f"{prev_doc['sheet_row_num']} → {new_status}"
                )
            except Exception:
                _logger.exception("Sheet status sync failed (non-fatal)")

        # Phase H: User personal-sheet status auto-sync.
        if (
            new_status is not None
            and prev_doc is not None
            and (prev_doc.get("status") or "") != new_status
        ):
            try:
                import user_sheet_sync as _uss
                tracking = (
                    prev_doc.get("tracking_id")
                    or res.get("tracking_id") or ""
                )
                await _uss.sync_status_change(
                    db, current_user, res, new_status,
                    extra_notice=(
                        f"Tracking: {tracking}" if tracking else None
                    ),
                )
            except Exception:
                _logger.exception(
                    "user-sheet status auto-sync failed (non-fatal)",
                )

        return Shipment(**strip_id(res))

    # =================  DELETE /shipments/{id}  =====================

    @shipments_write_router.delete("/shipments/{shipment_id}")
    async def delete_shipment(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Soft-delete: if the shipment is linked to a Master Sheet row,
        mark that row's Status="DELETED" before removing the local
        record. The Sheet row itself is preserved as an audit trail so
        that data never disappears from the source-of-truth even when
        the app-level record is removed. Sheet failures do NOT block
        the local delete — we log and proceed."""
        doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Shipment not found")

        sheet_result: Dict[str, Any] = {"attempted": False}
        row_num = doc.get("sheet_row_num")
        # Plan-gated: `sheet_soft_delete_tombstone` controls whether
        # deletion leaves an audit-trail row in the Master Sheet.
        if (
            row_num
            and sheet_mark_row_deleted is not None
            and await user_has_feature(
                current_user, "sheet_soft_delete_tombstone",
            )
        ):
            sheet_result["attempted"] = True
            try:
                reason = (
                    f"shipment {doc.get('tracking_id') or doc.get('id')} "
                    f"({doc.get('customer_name','')[:40]}) "
                    "removed from app"
                )
                sheet_result.update(
                    sheet_mark_row_deleted(int(row_num), reason=reason),
                )
            except Exception as e:
                # Don't block local delete — but surface the error so the
                # client knows the sheet was not marked.
                _logger.exception("Soft-delete sheet mark failed")
                sheet_result["ok"] = False
                sheet_result["error"] = str(e)

        res = await db.shipments.delete_one(
            {"id": shipment_id, "user_id": current_user["id"]},
        )
        # Phase H: best-effort tombstone on the user's personal sheet too.
        try:
            import user_sheet_sync as _uss
            await _uss.sync_delete(
                db, current_user, doc,
                reason=(
                    f"shipment {doc.get('tracking_id') or doc.get('id')} "
                    "removed"
                ),
            )
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (delete) failed (non-fatal)",
            )
        if res.deleted_count == 0:
            # Race condition — someone else deleted. Still return 404.
            raise HTTPException(status_code=404, detail="Shipment not found")
        return {"ok": True, "sheet": sheet_result}

    # =================  POST /orders/pending/{id}/ship  =============

    @shipments_write_router.post(
        "/orders/pending/{order_id}/ship", response_model=Shipment,
    )
    async def ship_pending_order(
        order_id: str,
        payload: ShipOrderRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Promote a pending order to a real shipment — allocates a
        tracking ID, charges wallet, syncs sheets, and links the
        pending order to the new shipment."""
        # Phase-3a/4a combined gate
        room = await plan_room_status(db, current_user)
        if room["trial_expired"]:
            raise HTTPException(
                status_code=402,
                detail=(
                    "Your 7-day free trial has expired. "
                    "Upgrade to continue."
                ),
            )
        if room["daily_blocked"]:
            raise HTTPException(
                status_code=402,
                detail="Daily limit reached. Please try again tomorrow.",
            )
        plan_has_room = bool(room["plan_has_room"])
        if (not plan_has_room) and room["plan"] == "free_trial":
            raise HTTPException(
                status_code=402,
                detail=(
                    "Free trial limit reached (10 labels). "
                    "Upgrade to Silver or higher."
                ),
            )

        order = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.get("status") == "shipped":
            raise HTTPException(status_code=400, detail="Order already shipped")

        courier = await db.couriers.find_one(
            {"id": payload.courier_id, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not courier:
            raise HTTPException(status_code=404, detail="Courier not found")

        # Allocate tracking ID
        padding = int(courier.get("number_padding") or 4)
        next_num = int(courier.get("next_number") or 1)
        tracking_id = (
            f"{courier.get('series_prefix','')}"
            f"{str(next_num).zfill(padding)}"
        )
        await db.couriers.update_one(
            {"id": courier["id"], "user_id": current_user["id"]},
            {"$inc": {"next_number": 1}},
        )

        # Build shipment from order + optional overrides
        overrides = payload.overrides or {}

        def _get(k, default=""):
            return overrides.get(k, order.get(k, default))

        # items as list (stored as comma separated in pending_orders)
        items_str = _get("items", "")
        items_list = [
            s.strip()
            for s in (items_str.split(",") if items_str else [])
            if s.strip()
        ]

        # Phase F2.1 — Status + Timestamp from imported sources.
        # When the source row already carried a real-world status
        # (Shipped/Delivered/etc.) and/or timestamp, those land on the
        # PendingOrder as `imported_status` / `imported_at`. We copy
        # them onto the resulting Shipment so historical imports land
        # in the right pipeline bucket + carry the original timestamp.
        _imp_status = (order.get("imported_status") or "").strip()
        _imp_at     = (order.get("imported_at") or "").strip()
        _ship_status = _imp_status if _imp_status else "Pending"
        _ship_created_at = _imp_at if _imp_at else utcnow_iso()

        ship_doc = {
            "id":                 str(uuid.uuid4()),
            "tracking_id":        tracking_id,
            "courier_id":         courier["id"],
            "courier_name":       courier.get("name", ""),
            "customer_name":      _get("customer_name"),
            "customer_phone":     _get("customer_phone"),
            "customer_alt_phone": _get("customer_alt_phone"),
            "customer_email":     _get("customer_email"),
            "customer_gstin":     _get("customer_gstin"),
            "address_line1":      _get("address_line1"),
            "address_line2":      _get("address_line2"),
            "city":               _get("city"),
            "state":              _get("state"),
            "pincode":            _get("pincode"),
            "items":              items_list,
            "item_description":   items_str,
            "amount":             float(_get("amount", 0) or 0),
            "cod_amount": (
                float(_get("amount", 0) or 0)
                if _get("payment_mode") == "COD" else 0
            ),
            "weight":             _get("weight"),
            "payment_mode":       _get("payment_mode", "COD"),
            "order_id":           _get("order_id_hint"),
            "master_order_id":    str(order.get("master_order_id") or ""),
            "notes":              _get("notes"),
            "status":             _ship_status,
            "created_at":         _ship_created_at,
            "updated_at":         utcnow_iso(),
            # Phase F2.1 — carry per-shipment custom-field values from
            # the source PendingOrder so Smart Paste / CSV / Webhook
            # imports preserve dynamic fields end-to-end. Existing
            # `_write_custom_values_to_user_sheet_bg` (below) ALSO
            # writes to the user's Google sheet — both are needed:
            # the user-sheet write is best-effort, the doc embed is
            # mandatory (analytics + label rendering read it).
            "custom_values":      order.get("custom_values") or {},
            # Carry the Master Sheet row number so a future delete can
            # soft-delete the exact tombstone row.
            "sheet_row_num":      order.get("sheet_row_num"),
            "user_id":            current_user["id"],
        }
        # Phase F2.1 — when the import landed an already-Delivered row,
        # also stamp delivered_at so analytics + the Detail page show
        # the same timestamp the user saw in their source file.
        if _ship_status == "Delivered" and _imp_at:
            ship_doc["delivered_at"] = _imp_at

        # ── Mandatory Master Sheet backup (all plans) ──
        # PendingOrders that came in via Smart Paste already wrote a row
        # at creation time — `sheet_row_num` is set on the order. CSV /
        # sheet-sync sourced ones don't carry that row, so we MUST append.
        if not ship_doc.get("sheet_row_num"):
            sheet_meta = await _backup_shipment_to_master_sheet(
                current_user=current_user,
                data=ship_doc,
                notice=f"via Ship · Tracking: {tracking_id}",
            )
            if sheet_meta.get("deferred"):
                ship_doc["master_backup_status"] = "pending"
                ship_doc["master_backup_payload"] = (
                    sheet_meta.get("_pending_payload") or {}
                )
                ship_doc["master_backup_last_error_at"] = utcnow_iso()
            elif (sheet_meta and sheet_meta.get("updated_range")
                  and sheet_parse_row_from_updated_range):
                try:
                    row = sheet_parse_row_from_updated_range(
                        sheet_meta["updated_range"],
                    )
                    if row:
                        ship_doc["sheet_row_num"] = int(row)
                        ship_doc["master_backup_status"] = "ok"
                except Exception:
                    pass

        await db.shipments.insert_one(ship_doc)

        # Best-effort: custom-field values to user's personal sheet.
        custom_vals = order.get("custom_values") or {}
        await _write_custom_values_to_user_sheet_bg(current_user, custom_vals)

        # Mark order as shipped + link.
        await db.pending_orders.update_one(
            {"id": order_id, "user_id": current_user["id"]},
            {"$set": {
                "status":       "shipped",
                "processed_at": utcnow_iso(),
                "shipment_id":  ship_doc["id"],
                "tracking_id":  tracking_id,
            }},
        )
        # Charge wallet + bump plan counter.
        addr_text = " ".join(filter(None, [
            ship_doc.get("address_line1", ""),
            ship_doc.get("address_line2", ""),
            ship_doc.get("city", ""), ship_doc.get("state", ""),
            str(ship_doc.get("pincode", "")),
        ]))
        _s2 = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1,
             "ai_cost_complex": 1},
        ) or {}
        ai_costs2 = {
            "simple":  float(_s2.get("ai_cost_simple", 0.5)),
            "medium":  float(_s2.get("ai_cost_medium", 1.0)),
            "complex": float(_s2.get("ai_cost_complex", 2.0)),
        }
        breakdown, _reason = await wallet_classify_and_cost(
            current_user, addr_text, plan_has_room, ai_costs=ai_costs2,
        )
        # Wallet may not have been pre-checked above (old-path) — make
        # sure the user can pay.
        bal = await wallet_balance(db, current_user["id"])
        if breakdown.total > bal + 1e-6:
            _logger.warning(
                f"Ship path: wallet underfunded for user "
                f"{current_user['id']}",
            )
        if plan_has_room:
            await bump_label_usage(db, current_user)
        await wallet_charge(db, current_user, ship_doc["id"], breakdown)

        # Two-Way Status Sync: bump the Master Sheet row from "Pending"
        # to "Ready to Ship" and stamp the tracking ID into Notice.
        # Phase F2.2 (2026-05-09): formerly wrote literal "Dispatched";
        # rewritten to "Ready to Ship" so the Sheet matches the app's
        # canonical user-facing label everywhere.
        sheet_row = order.get("sheet_row_num")
        if (
            sheet_row
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            try:
                sheet_update_row_status(
                    int(sheet_row),
                    "Ready to Ship",
                    extra_notice=(
                        f"Tracking: {tracking_id} · "
                        f"{courier.get('name', '')}"
                    ),
                )
                _logger.info(
                    f"Sheet status sync OK: row={sheet_row} "
                    f"Pending → Ready to Ship ({tracking_id})"
                )
            except Exception:
                _logger.exception(
                    "Sheet status sync failed on ship (non-fatal)",
                )

        ship_doc.pop("_id", None)
        return ship_doc
