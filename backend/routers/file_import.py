"""
File Import — Phase F1 (CSV + XLSX bulk-import to pending_orders).

Mirrors the Smart Paste / Google Sheet ingestion path, but the source
is a user-uploaded `.csv` or `.xlsx` file. Once a row is parsed and
validated, it lands in the same `pending_orders` Mongo collection
(source="file") so the Orders tab + Ship-this-order pipeline reuse
the existing UI + business rules unchanged.

Endpoints:
  POST   /api/orders/import/preview     parse-only, return columns + first N sample rows
  POST   /api/orders/import/commit      parse + map + insert pending_orders
  GET    /api/me/file-import-mapping    saved default column → schema mapping
  PUT    /api/me/file-import-mapping    save / update the default mapping

Pattern: late-binding `init()` — same as routers/notifications.py.

Phase F1.1 (2026-05-08): Address Auto-Merge, Token field exposed.
Phase F1.2 (2026-05-08): All 22 Add-Shipment fields mappable.
Phase F2.0 (2026-05-08): Schema/aliases/coercions extracted to
    /app/backend/import_schema.py for sharing with webhook router.
Phase F2.1 (2026-05-09): Status + Timestamp mapping. When the source
    file carries Shipped / Delivered / etc. plus a real-world date,
    the resulting PendingOrder records `imported_status` and
    `imported_at`; ship_pending_order copies them to the Shipment so
    historical imports land in the right pipeline bucket. Per-user
    custom fields can be mapped via "custom:<id>" mapping values.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from import_schema import (
    SCHEMA_FIELDS,
    NUMERIC_FIELDS,
    PAYMENT_MODE_NORMALISE,
    HEADER_ALIASES,
    suggest_mapping,
    normalise_value,
    is_custom_field_mapping,
    custom_field_id,
    validate_mapping_field,
)


file_import_router = APIRouter(prefix="/api", tags=["file-import"])


MAX_SAMPLE_ROWS = 10
MAX_FILE_BYTES  = 10 * 1024 * 1024     # 10 MB hard limit
MAX_IMPORT_ROWS = 5000                 # one upload at a time


class FileImportMappingPayload(BaseModel):
    mapping: Dict[str, str]            # { csv_column_header: schema_field }


# ════════════════════════════════════════════════════════════════════
#                              Parsers
# ════════════════════════════════════════════════════════════════════

def _parse_csv(blob: bytes) -> tuple[List[str], List[Dict[str, str]]]:
    # CSV may use BOM (Excel-saved csv) — decode-detect.
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = blob.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail="Could not decode file")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="File is empty")
    rows: List[Dict[str, str]] = []
    for line in reader:
        # pad / trim to header length
        if len(line) < len(header):
            line = list(line) + [""] * (len(header) - len(line))
        elif len(line) > len(header):
            line = line[: len(header)]
        rows.append({h.strip(): (line[i] or "").strip() for i, h in enumerate(header)})
    return [h.strip() for h in header], rows


def _parse_xlsx(blob: bytes) -> tuple[List[str], List[Dict[str, Any]]]:
    """Parse .xlsx into header + list of dict rows.

    NOTE: We KEEP native Python types (datetime, int, float) when openpyxl
    yields them so the timestamp/status normalisers downstream can do
    their job (e.g. datetime.isoformat()) without lossy str() conversion.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="XLSX parser (openpyxl) not installed on the server",
        )
    try:
        wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read XLSX: {e}")
    ws = wb.active
    if ws is None:
        raise HTTPException(status_code=400, detail="Workbook has no sheets")
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_raw = next(rows_iter)
    except StopIteration:
        raise HTTPException(status_code=400, detail="Sheet is empty")
    header = [str(c).strip() if c is not None else "" for c in header_raw]
    rows: List[Dict[str, Any]] = []
    for line in rows_iter:
        line = list(line) + [None] * (len(header) - len(line))
        line = line[: len(header)]
        if all(c is None or (isinstance(c, str) and c.strip() == "") for c in line):
            continue   # skip blank rows
        rows.append({h: line[i] for i, h in enumerate(header)})
    return header, rows


def _parse_upload(file: UploadFile, blob: bytes) -> tuple[str, List[str], List[Dict[str, Any]]]:
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        cols, rows = _parse_xlsx(blob)
        return "xlsx", cols, rows
    if name.endswith(".csv") or (file.content_type or "").startswith("text/csv"):
        cols, rows = _parse_csv(blob)
        return "csv", cols, rows
    raise HTTPException(
        status_code=400,
        detail="Only .csv and .xlsx files are supported (got "
               f"{file.filename or 'unknown'})",
    )


def _row_sample_for_preview(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Stringify sample rows for the JSON preview response so datetime
    objects don't blow up json.dumps."""
    out: List[Dict[str, str]] = []
    for r in rows[:MAX_SAMPLE_ROWS]:
        out.append({
            k: ("" if v is None else (
                v.isoformat() if hasattr(v, "isoformat") else str(v).strip()
            ))
            for k, v in r.items()
        })
    return out


# ════════════════════════════════════════════════════════════════════
#                              Routes
# ════════════════════════════════════════════════════════════════════

def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.file_import")
    from server import (  # noqa: WPS433 — late-binding to avoid circular import
        db,
        get_current_user as _get_current_user,
        generate_master_order_id,
    )

    async def _list_user_custom_fields(user_id: str) -> List[Dict[str, Any]]:
        cur = db.user_custom_fields.find(
            {"user_id": user_id, "active": {"$ne": False}}, {"_id": 0},
        )
        return [doc async for doc in cur]

    # ── Saved default mapping (per user) ──────────────────────────

    @file_import_router.get("/me/file-import-mapping")
    async def get_file_import_mapping(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0, "file_import_mapping": 1},
        ) or {}
        custom_fields = await _list_user_custom_fields(current_user["id"])
        return {
            "mapping":        doc.get("file_import_mapping") or {},
            "schema_fields":  SCHEMA_FIELDS,
            "custom_fields":  [
                # `name` is the canonical label key on user_custom_fields
                # docs (CustomFieldCreate.name in routers/custom_fields.py).
                {"id": cf.get("id"), "label": cf.get("name") or cf.get("label") or ""}
                for cf in custom_fields
            ],
        }

    @file_import_router.put("/me/file-import-mapping")
    async def put_file_import_mapping(
        payload: FileImportMappingPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Sanity-check the mapping values are known schema fields or
        # one of the user's custom-field ids ("custom:<id>").
        custom_fields = await _list_user_custom_fields(current_user["id"])
        cf_ids = {cf.get("id") for cf in custom_fields if cf.get("id")}
        bad = [
            v for v in payload.mapping.values()
            if not validate_mapping_field(v, cf_ids)
        ]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown schema fields: {sorted(set(bad))}",
            )
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"file_import_mapping": payload.mapping}},
        )
        return {"ok": True, "mapping": payload.mapping}

    # ── Preview (no DB write) ─────────────────────────────────────

    @file_import_router.post("/orders/import/preview")
    async def import_preview(
        file: UploadFile = File(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        blob = await file.read()
        if len(blob) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (limit { MAX_FILE_BYTES // (1024 * 1024) } MB)",
            )
        fmt, columns, rows = _parse_upload(file, blob)
        # Auto-suggest mapping using the saved default + naive header match.
        saved = (
            await db.users.find_one(
                {"id": current_user["id"]}, {"_id": 0, "file_import_mapping": 1},
            ) or {}
        ).get("file_import_mapping") or {}
        custom_fields = await _list_user_custom_fields(current_user["id"])
        suggested = suggest_mapping(columns, saved, custom_fields)
        return {
            "format":         fmt,
            "filename":       file.filename or "",
            "columns":        columns,
            "sample_rows":    _row_sample_for_preview(rows),
            "total_rows":     len(rows),
            "schema_fields":  SCHEMA_FIELDS,
            "custom_fields":  [
                # `name` is the canonical label key on user_custom_fields.
                {"id": cf.get("id"), "label": cf.get("name") or cf.get("label") or ""}
                for cf in custom_fields
            ],
            "suggested":      suggested,
        }

    # ── Commit (validate + insert) ────────────────────────────────

    @file_import_router.post("/orders/import/commit")
    async def import_commit(
        file: UploadFile = File(...),
        mapping: str = Form(..., description="JSON string {col_header: schema_field}"),
        save_default: bool = Form(False),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        try:
            mapping_obj: Dict[str, str] = json.loads(mapping)
            if not isinstance(mapping_obj, dict):
                raise ValueError("mapping must be a JSON object")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mapping JSON: {e}")

        blob = await file.read()
        if len(blob) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (limit { MAX_FILE_BYTES // (1024 * 1024) } MB)",
            )
        fmt, columns, rows = _parse_upload(file, blob)
        if len(rows) > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Too many rows ({len(rows)}). Max {MAX_IMPORT_ROWS} per "
                    "upload — please split the file."
                ),
            )

        # Fetch the user's custom fields once to validate "custom:<id>"
        # mapping values + accept them downstream.
        custom_fields = await _list_user_custom_fields(current_user["id"])
        cf_ids = {cf.get("id") for cf in custom_fields if cf.get("id")}

        # Reverse-lookup: schema_field → list[file_column].
        # Phase F1.1 — multiple columns can map to the same field
        # (specifically the virtual "address" field, which auto-merges
        # Address Line 1 + Address Line 2 from the source file). For
        # all other fields the LAST mapping wins.
        field_to_cols: Dict[str, List[str]] = {}
        custom_to_cols: Dict[str, List[str]] = {}
        for col, field in mapping_obj.items():
            if not field:
                continue
            if not validate_mapping_field(field, cf_ids):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown schema field in mapping: {field}",
                )
            if is_custom_field_mapping(field):
                custom_to_cols.setdefault(custom_field_id(field), []).append(col)
                continue
            field_to_cols.setdefault(field, []).append(col)

        if (not field_to_cols.get("customer_name")
                and not field_to_cols.get("customer_phone")):
            raise HTTPException(
                status_code=400,
                detail=(
                    "At minimum, map `customer_name` or `customer_phone` "
                    "before importing."
                ),
            )

        if save_default:
            await db.users.update_one(
                {"id": current_user["id"]},
                {"$set": {"file_import_mapping": mapping_obj}},
            )

        # Build pending_orders rows.
        now = datetime.now(timezone.utc).isoformat()
        imported = 0
        skipped = 0
        errors:  List[str] = []
        batch:   List[Dict[str, Any]] = []
        filename = file.filename or "import"
        # Pre-allocate master_order_ids in bulk to avoid per-row counter
        # round-trips.
        master_oids = [
            await generate_master_order_id() for _ in range(len(rows))
        ]
        for idx, row in enumerate(rows):
            try:
                doc: Dict[str, Any] = {
                    "id":                str(uuid.uuid4()),
                    "user_id":           current_user["id"],
                    "source":            "file",
                    "status":            "pending",   # pipeline: pending|shipped|skipped
                    "master_order_id":   master_oids[idx],
                    "order_id":          "",
                    "created_at":        now,
                    # Phase-F1 — extra metadata for the Orders UI badge.
                    "source_meta": {
                        "filename":     filename,
                        "format":       fmt,
                        "imported_at":  now,
                        "row_index":    idx + 2,   # +2 = 1 (header) + 1-based
                    },
                    "custom_values":     {},
                }
                # Initialise all schema fields to defaults.
                for field in SCHEMA_FIELDS:
                    if field == "address":
                        continue
                    if field in NUMERIC_FIELDS:
                        doc[field] = 0.0
                    elif field == "payment_mode":
                        doc[field] = "COD"
                    else:
                        doc[field] = ""
                doc["address_line1"] = ""
                doc["address_line2"] = ""

                # Fill from mapped columns.
                for field, cols in field_to_cols.items():
                    if field == "address":
                        parts = [
                            (str(row.get(c, "") or "")).strip()
                            for c in cols
                        ]
                        doc["address_line1"] = " ".join(p for p in parts if p)
                        continue
                    raw = row.get(cols[-1], "")  # last-mapped wins
                    doc[field] = normalise_value(field, raw)

                # Custom-field mappings → custom_values dict.
                for cf_id, cols in custom_to_cols.items():
                    raw = row.get(cols[-1], "")
                    doc["custom_values"][cf_id] = (
                        "" if raw is None else str(raw).strip()
                    )

                # Phase F2.1 — Status + Timestamp from import.
                #
                # The mapping loop above wrote any "status" /
                # "created_at_override" cells onto `doc` under those
                # exact key names, OVERWRITING the pipeline `status`
                # we set earlier. We need to relocate them:
                #   • doc["status"] (canonical "Shipped"/"Delivered"/…)
                #     → doc["imported_status"]; reset doc["status"] to
                #     the pipeline-pending sentinel so PendingOrder's
                #     own state-machine isn't broken.
                #   • doc["created_at_override"] (ISO timestamp)
                #     → doc["imported_at"].
                # Both downstream values are read by ship_pending_order
                # which copies them onto the resulting Shipment.
                doc["imported_status"] = (
                    doc.get("status") or "" if "status" in field_to_cols else ""
                )
                doc["status"] = "pending"   # always pipeline-pending on import
                doc["imported_at"] = (
                    doc.pop("created_at_override", "") or ""
                    if "created_at_override" in field_to_cols
                    else ""
                )
                doc.pop("created_at_override", None)   # schema-level key gone

                doc["order_id"] = doc.get("order_id") or doc["master_order_id"]
                # Skip blank rows (no name AND no phone)
                if not doc["customer_name"] and not doc["customer_phone"]:
                    skipped += 1
                    continue
                batch.append(doc)
                imported += 1
            except Exception as e:
                skipped += 1
                errors.append(f"row {idx + 2}: {e}")

        if batch:
            try:
                await db.pending_orders.insert_many(batch, ordered=False)
            except Exception as e:
                _logger.exception("pending_orders insert_many failed")
                raise HTTPException(status_code=500, detail=f"DB write failed: {e}")

        _logger.info(
            "file-import: user=%s file=%s fmt=%s imported=%d skipped=%d",
            current_user["id"], filename, fmt, imported, skipped,
        )
        return {
            "ok":         True,
            "imported":   imported,
            "skipped":    skipped,
            "total":      len(rows),
            "errors":     errors[:20],   # cap so the response stays small
            "filename":   filename,
            "format":     fmt,
        }

    _logger.info("file_import router mounted (4 endpoints)")
