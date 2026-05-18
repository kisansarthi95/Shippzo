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
        # Phase-33 — Terminal-state lock. Cancelled / Cancel by buyer /
        # Returned shipments are PERMANENTLY dead. Any attempt to
        # mutate them (status change, edit, re-ship workflow, etc.)
        # is rejected at the API boundary with HTTP 423 LOCKED so the
        # frontend can pattern-match the response and show the right
        # banner.  Reads remain fully open.
        from lib.terminal_states import (
            is_terminal_shipment_status, TERMINAL_LOCK_DETAIL,
        )
        existing = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"_id": 0, "status": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Shipment not found")
        if is_terminal_shipment_status(existing.get("status")):
            raise HTTPException(
                status_code=423,
                detail=TERMINAL_LOCK_DETAIL,
            )

        update = {
            k: v for k, v in payload.model_dump().items() if v is not None
        }
        if "status" in update and update["status"] == "Delivered":
            update["delivered_at"] = utcnow_iso()
        # Phase-33 — Stamp terminal-state metadata when the target
        # status is one of the dead-states so reporting can group /
        # filter on cancelled_at (and we have an audit trail).
        if "status" in update and is_terminal_shipment_status(update["status"]):
            update.setdefault("cancelled_at", utcnow_iso())
            update.setdefault("cancel_reason", "user_action")
        if "amount" in update:
            update["cod_amount"] = (
                float(update["amount"])
                if update.get("payment_mode", "") == "COD" else 0.0
            )
        if not update:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Phase-19 — Auto-tag as "Modified" when the admin edits any
        # user-facing detail (everything that's NOT the status itself
        # or its auto-derived siblings). Pure status flips coming from
        # the new Next-Stage button on the Shipments card should NOT
        # flag the order as modified — that would defeat the filter.
        _auto_derived = {"status", "delivered_at", "cod_amount"}
        _content_keys = [k for k in update.keys() if k not in _auto_derived]
        if _content_keys:
            update.setdefault("is_modified", True)
            update.setdefault("modified_at", utcnow_iso())

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
        """Phase-33 — HARD DELETE REMOVED.

        The legacy delete endpoint has been re-purposed into a
        "Cancel Order" flip. The local record is NEVER removed from
        Mongo so that:
          * Reporting / history / customer contacts stay intact.
          * Sync sources (sheet, webhook, file-import) can detect a
            previously-cancelled order ID and refuse to re-insert it
            as a fresh active row.

        Behaviour:
          * If the shipment is already in a terminal state, the call
            is idempotent → 200 with `already_cancelled=True`.
          * Otherwise the status is flipped to `Cancelled`, with
            `cancelled_at` / `cancel_reason="user_action"` stamped.
          * Existing Master-Sheet tombstone / user-sheet status-sync
            integrations still run so the source-of-truth row stays
            in lock-step.
        """
        from lib.terminal_states import is_terminal_shipment_status

        doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Shipment not found")

        # Already terminal → idempotent ack so the frontend can show
        # the right toast without spamming the API.
        if is_terminal_shipment_status(doc.get("status")):
            return {
                "ok": True,
                "already_cancelled": True,
                "status": doc.get("status"),
            }

        sheet_result: Dict[str, Any] = {"attempted": False}
        row_num = doc.get("sheet_row_num")
        if (
            row_num
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            sheet_result["attempted"] = True
            try:
                tracking = doc.get("tracking_id") or ""
                sheet_update_row_status(
                    int(row_num),
                    "Cancelled",
                    extra_notice=(
                        f"Tracking: {tracking}" if tracking else None
                    ),
                )
                sheet_result["ok"] = True
            except Exception as e:
                _logger.exception("Sheet cancel sync failed (non-fatal)")
                sheet_result["ok"] = False
                sheet_result["error"] = str(e)

        # Flip to Cancelled (no removal) — write audit fields.
        await db.shipments.update_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": {
                "status": "Cancelled",
                "cancelled_at": utcnow_iso(),
                "cancel_reason": "user_action",
            }},
        )

        # Best-effort user-sheet status sync (same as the PUT path).
        try:
            import user_sheet_sync as _uss
            tracking = doc.get("tracking_id") or ""
            fresh = await db.shipments.find_one(
                {"id": shipment_id, "user_id": current_user["id"]},
                {"_id": 0},
            )
            await _uss.sync_status_change(
                db, current_user, fresh, "Cancelled",
                extra_notice=(
                    f"Tracking: {tracking}" if tracking else None
                ),
            )
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (cancel) failed (non-fatal)",
            )
        return {"ok": True, "sheet": sheet_result, "status": "Cancelled"}

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
        # Phase-33 — Cancelled pending orders cannot be promoted to a
        # shipment. This is the final back-stop after the PUT-update
        # lock: even a direct POST .../ship call gets rejected.
        from lib.terminal_states import (
            is_terminal_pending_status, TERMINAL_LOCK_DETAIL,
        )
        if is_terminal_pending_status(order.get("status")):
            raise HTTPException(
                status_code=423,
                detail=TERMINAL_LOCK_DETAIL,
            )
        if order.get("status") == "shipped":
            raise HTTPException(status_code=400, detail="Order already shipped")

        courier = await db.couriers.find_one(
            {"id": payload.courier_id, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not courier:
            raise HTTPException(status_code=404, detail="Courier not found")

        # ─── Allocate tracking ID ────────────────────────────────
        # Phase-23 — Manual-tracking couriers (India Post Speed Post
        # stickers, Anjani Courier physical AWB, etc.) skip the
        # sequential counter entirely. The frontend collects the AWB
        # typed/scanned from the printed sticker and passes it as
        # `manual_tracking_id`. For every other courier the original
        # series_prefix + zero-padded next_number path runs unchanged,
        # preserving 100% of historic behaviour.
        manual_mode = bool(courier.get("manual_tracking"))
        if manual_mode:
            manual_id = (payload.manual_tracking_id or "").strip()
            if not manual_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Courier '{courier.get('name')}' uses manual tracking. "
                        "Please enter the tracking number from the courier sticker."
                    ),
                )
            tracking_id = manual_id
            # No $inc on next_number — the counter is irrelevant for
            # manual couriers and must stay where the user left it.
        else:
            padding  = int(courier.get("number_padding") or 4)
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
            # Phase-21 — Order-ID priority chain on shipments:
            #   1. PendingOrder.order_id (already resolved at ingest:
            #      upstream order_id if the source sent one, else our
            #      master_order_id when auto-gen is ON, else the user's
            #      manual entry).
            #   2. order_id_hint (regex-parsed legacy hint — only set
            #      when the pasted text contained an explicit "Order
            #      #..." line; kept for backwards-compat with rows that
            #      were ingested BEFORE the resolved order_id was wired
            #      onto smart-paste).
            #   3. master_order_id — final safety net so the Shipment
            #      NEVER ships with a blank Order ID. The user
            #      previously reported missing order_ids on shipments
            #      coming from webhooks / files that had no upstream
            #      id; the master id is the right thing to surface
            #      since it's what we already wrote to the Master Sheet
            #      and what the Order-ID counter assigned to this row.
            "order_id":           (
                _get("order_id")
                or _get("order_id_hint")
                or _get("master_order_id")
            ),
            "master_order_id":    str(order.get("master_order_id") or ""),
            "notes":              _get("notes"),
            "status":             _ship_status,
            "created_at":         _ship_created_at,
            "updated_at":         utcnow_iso(),
            # Phase F2.4 — also persist the raw imported values on the
            # Shipment doc itself so analytics + the Detail page can
            # show "imported from Shopify on 29 Apr" without joining
            # back to the (now-deleted) PendingOrder. Empty strings
            # when the source row didn't carry these fields.
            "imported_status":    _imp_status,
            "imported_at":        _imp_at,
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
