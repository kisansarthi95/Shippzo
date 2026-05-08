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


file_import_router = APIRouter(prefix="/api", tags=["file-import"])

# ────────────────────────────────────────────────────────────────────
# Schema fields the user can map columns to. Mirror PendingOrder so
# Settings UI can present a single dropdown per column.
# ────────────────────────────────────────────────────────────────────
SCHEMA_FIELDS: List[str] = [
    "order_id",
    "customer_name",
    "customer_phone",
    "customer_alt_phone",
    "customer_email",
    "customer_gstin",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pincode",
    "items",
    "amount",
    "token_amount",
    "payment_mode",
    "courier_hint",
    "weight",
    "notes",
]
NUMERIC_FIELDS = {"amount", "token_amount"}
PAYMENT_MODE_NORMALISE = {
    "cod": "COD", "c": "COD", "cash on delivery": "COD",
    "paid": "PAID", "p": "PAID", "prepaid": "PAID",
    "online": "PAID", "upi": "PAID",
}

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


def _parse_xlsx(blob: bytes) -> tuple[List[str], List[Dict[str, str]]]:
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
    rows: List[Dict[str, str]] = []
    for line in rows_iter:
        line = list(line) + [None] * (len(header) - len(line))
        line = line[: len(header)]
        if all(c is None or str(c).strip() == "" for c in line):
            continue   # skip blank rows
        rows.append({
            h: ("" if line[i] is None else str(line[i]).strip())
            for i, h in enumerate(header)
        })
    return header, rows


def _parse_upload(file: UploadFile, blob: bytes) -> tuple[str, List[str], List[Dict[str, str]]]:
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


def _normalise_value(field: str, raw: str) -> Any:
    raw = (raw or "").strip()
    if field in NUMERIC_FIELDS:
        if not raw:
            return 0.0
        try:
            return float(raw.replace(",", "").replace("₹", "").strip())
        except ValueError:
            return 0.0
    if field == "payment_mode":
        return PAYMENT_MODE_NORMALISE.get(raw.lower(), "COD" if not raw else raw.upper())
    if field == "pincode":
        # keep as string but strip non-digits
        return "".join(ch for ch in raw if ch.isdigit())
    return raw


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

    # ── Saved default mapping (per user) ──────────────────────────

    @file_import_router.get("/me/file-import-mapping")
    async def get_file_import_mapping(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0, "file_import_mapping": 1},
        ) or {}
        return {
            "mapping":     doc.get("file_import_mapping") or {},
            "schema_fields": SCHEMA_FIELDS,
        }

    @file_import_router.put("/me/file-import-mapping")
    async def put_file_import_mapping(
        payload: FileImportMappingPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Sanity-check the mapping values are known schema fields.
        bad = [v for v in payload.mapping.values() if v and v not in SCHEMA_FIELDS]
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
        suggested: Dict[str, str] = {}
        for c in columns:
            if c in saved:
                suggested[c] = saved[c]
                continue
            cl = c.lower().replace(" ", "_").replace("-", "_")
            if cl in SCHEMA_FIELDS:
                suggested[c] = cl
        return {
            "format":         fmt,
            "filename":       file.filename or "",
            "columns":        columns,
            "sample_rows":    rows[:MAX_SAMPLE_ROWS],
            "total_rows":     len(rows),
            "schema_fields":  SCHEMA_FIELDS,
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

        # Reverse-lookup: schema_field → file_column
        field_to_col: Dict[str, str] = {}
        for col, field in mapping_obj.items():
            if not field:
                continue
            if field not in SCHEMA_FIELDS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown schema field in mapping: {field}",
                )
            field_to_col[field] = col

        if not field_to_col.get("customer_name") and not field_to_col.get("customer_phone"):
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
                    "status":            "pending",
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
                }
                # Initialise all schema fields to defaults.
                for field in SCHEMA_FIELDS:
                    doc[field] = 0.0 if field in NUMERIC_FIELDS else (
                        "COD" if field == "payment_mode" else ""
                    )
                # Fill from mapped columns.
                for field, col in field_to_col.items():
                    raw = row.get(col, "")
                    doc[field] = _normalise_value(field, raw)
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
