"""
Google Sheets writer — appends a new row to the Master Sheet
using a Service Account.

Env vars (read via dotenv in server.py):
  GOOGLE_SA_JSON_PATH  – absolute path to service-account JSON key file
  MASTER_SHEET_ID      – the spreadsheet ID (in the URL between /d/ and /edit)
  MASTER_SHEET_TAB     – worksheet/tab name (e.g. "All Master Data")
"""
from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column order MUST match the Master Sheet headers exactly (left → right).
# Phase-B (2026-04) — extended schema with user_name, master_order_id,
# alt_phone, token_amount, weight. Existing columns kept in original
# positions for backward compatibility; new columns appended at END so
# admins can safely add the new header cells without shifting old data.
COLUMNS = [
    "timestamp", "user_id", "order_id", "name", "phone", "address",
    "city", "state", "pincode", "item_type", "amount",
    "payment_mode", "status", "notice",
    # ── Phase-B extensions ──
    "user_name", "master_order_id", "alt_phone", "token_amount", "weight",
]


def _get_worksheet():
    """Authenticate and return the target worksheet. Raises on failure.

    Phase-B: First tries to read `master_sheet_id` / `master_sheet_tab`
    from MongoDB's `admin_config` collection (so the admin can change
    the sheet via the Admin Panel without touching env vars). Falls
    back to MASTER_SHEET_ID / MASTER_SHEET_TAB env vars if Mongo is
    unavailable or the values are blank.
    """
    key_path = os.getenv("GOOGLE_SA_JSON_PATH")

    # Best-effort: pull admin-managed master sheet config from Mongo.
    sheet_id: str = ""
    tab_name: str = ""
    try:
        from pymongo import MongoClient
        mongo_url = os.getenv("MONGO_URL")
        db_name = os.getenv("DB_NAME") or "test_database"
        if mongo_url:
            _mc = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
            _mdb = _mc[db_name]
            _doc = _mdb["admin_config"].find_one({"_id": "default"}) or {}
            sheet_id = str(_doc.get("master_sheet_id") or "").strip()
            tab_name = str(_doc.get("master_sheet_tab") or "").strip()
            _mc.close()
    except Exception as _e:
        log.debug(f"admin_config lookup skipped: {_e}")

    # Env-var fallback.
    if not sheet_id:
        sheet_id = os.getenv("MASTER_SHEET_ID", "") or ""
    if not tab_name:
        tab_name = os.getenv("MASTER_SHEET_TAB", "Sheet1") or "Sheet1"

    if not key_path or not os.path.isfile(key_path):
        raise RuntimeError(f"Service account JSON not found at {key_path!r}")
    if not sheet_id:
        raise RuntimeError(
            "Master Sheet not configured. Set it from Admin Panel → Master Sheet, "
            "or configure MASTER_SHEET_ID env var."
        )

    creds = Credentials.from_service_account_file(key_path, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        raise RuntimeError(
            f"Worksheet tab {tab_name!r} not found in sheet {sheet_id!r}. "
            f"Available tabs: {[w.title for w in spreadsheet.worksheets()]}"
        )


def get_service_account_email() -> str:
    """Return the client_email from the SA JSON. Used by the frontend to
    show the user *which* email they should share their Sheet with.

    Returns "" on any failure (e.g. JSON missing) so the UI can degrade
    gracefully rather than crashing.
    """
    try:
        import json as _json
        key_path = os.getenv("GOOGLE_SA_JSON_PATH")
        if not key_path or not os.path.isfile(key_path):
            return ""
        with open(key_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return str(data.get("client_email") or "")
    except Exception:
        log.exception("Failed to read service account email")
        return ""


def _open_user_sheet(sheet_id: str, tab_name_or_gid: str = ""):
    """Open ANY user-supplied sheet via the same Service Account.

    The user must have shared their sheet with the service-account email
    (Editor or Viewer). On 403 we raise a clear RuntimeError that the
    caller can convert into a friendly HTTP error.

    `tab_name_or_gid` may be:
      - empty / "0" / a numeric gid → first worksheet (or matching gid)
      - a tab title (e.g. "Orders") → that worksheet by name
    """
    key_path = os.getenv("GOOGLE_SA_JSON_PATH")
    if not key_path or not os.path.isfile(key_path):
        raise RuntimeError(f"Service account JSON not found at {key_path!r}")
    if not sheet_id:
        raise RuntimeError("Sheet ID is empty")

    creds = Credentials.from_service_account_file(key_path, scopes=_SCOPES)
    client = gspread.authorize(creds)
    try:
        ss = client.open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound:
        raise RuntimeError("SHEET_NOT_FOUND")
    except Exception as e:
        msg = str(e)
        if "403" in msg or "PERMISSION" in msg.upper() or "does not have permission" in msg.lower():
            raise RuntimeError("SHEET_NOT_SHARED")
        raise

    # Resolve which worksheet to open.
    if not tab_name_or_gid:
        return ss.sheet1  # default: first worksheet

    # Try numeric gid first (the URL fragment after gid=).
    try:
        gid_int = int(tab_name_or_gid)
        for ws in ss.worksheets():
            if int(getattr(ws, "id", -1)) == gid_int:
                return ws
        # gid not found → fall through to tab-name path
    except (ValueError, TypeError):
        pass

    # Treat as tab name.
    try:
        return ss.worksheet(tab_name_or_gid)
    except gspread.WorksheetNotFound:
        # Last resort: first sheet.
        return ss.sheet1


def read_user_sheet(
    sheet_id: str,
    tab_name_or_gid: str = "",
    *,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    """Read a user-linked Google Sheet via the Service Account.

    Returns: {
      "ok": True,
      "headers": [...],           # row 1 trimmed
      "rows": [{...}, {...}, ...], # header→value dicts (skips empty rows)
      "tab": "<tab name>",
      "total_rows": N,
    }
    Or {"ok": False, "error": "<short code>"} on failure where error is
    one of:
      "SHEET_NOT_SHARED"  – SA can't access this sheet (user must share)
      "SHEET_NOT_FOUND"   – ID doesn't exist
      "SA_MISSING"        – server-side SA JSON is missing
      "<other>"           – raw exception string for unknowns
    """
    try:
        ws = _open_user_sheet(sheet_id, tab_name_or_gid)
        all_values = ws.get_all_values()
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # pragma: no cover
        log.exception("read_user_sheet failed")
        return {"ok": False, "error": str(e)}

    if not all_values:
        return {"ok": True, "headers": [], "rows": [], "tab": ws.title, "total_rows": 0}

    headers = [(h or "").strip() for h in all_values[0]]
    out_rows: List[Dict[str, str]] = []
    for r in all_values[1:max_rows + 1]:
        if not any((c or "").strip() for c in r):
            continue
        rec: Dict[str, str] = {}
        for i, h in enumerate(headers):
            rec[h] = (r[i] if i < len(r) else "").strip()
        out_rows.append(rec)

    return {
        "ok": True,
        "headers": headers,
        "rows": out_rows,
        "tab": ws.title,
        "total_rows": len(out_rows),
    }


def _find_next_empty_row(ws) -> int:
    """Return the 1-based row number just after the last non-empty row.

    We use the entire sheet's values (including tombstones like "DELETED"
    in the Status column) so that append never lands on a previously used
    row. This makes our soft-delete markers permanent even across many
    subsequent appends.

    gspread's default `append_row` uses the Google Sheets values.append API
    which can occasionally "fall back" into the middle of the data block
    when it detects a perceived table boundary. Writing to an explicit row
    sidesteps that entirely.
    """
    # get_all_values returns a 2-D list; len() is the number of rows with data.
    # If the sheet has only a header, len == 1 → next row = 2.
    try:
        rows = ws.get_all_values()
    except Exception:
        # Fallback: ws.row_count is the allocated size, not the used count,
        # so we use it only if values fetch fails.
        return int(getattr(ws, "row_count", 1)) + 1
    # Strip trailing fully-empty rows; keep rows that have any non-blank cell.
    used = 0
    for i, row in enumerate(rows, start=1):
        if any((c or "").strip() for c in row):
            used = i
    return used + 1


def append_order_row(
    *,
    user_id: str = "",
    order_id: str = "",
    name: str = "",
    phone: str = "",
    address: str = "",
    city: str = "",
    state: str = "",
    pincode: str = "",
    item_type: str = "",
    amount: Any = "",
    payment_mode: str = "",
    status: str = "Pending",
    notice: str = "",
    # Phase-B extensions (default empty so existing callers keep working).
    user_name: str = "",
    master_order_id: str = "",
    alt_phone: str = "",
    token_amount: Any = "",
    weight: str = "",
) -> Dict[str, Any]:
    """
    Append one row to the Master Sheet at the first guaranteed-empty row.

    Unlike gspread's `append_row`, this writes to an explicit row index
    computed via `_find_next_empty_row`, so rows that were previously
    soft-deleted (Status="DELETED") are preserved forever — no accidental
    overwrite. Returns {"ok": True, "updated_range": "'Tab'!A<n>:S<n>",
    "tab": ..., "sheet_id": ...} on success. Raises on failure.

    Phase-B note: 19 columns total. New columns (user_name, master_order_id,
    alt_phone, token_amount, weight) are appended at the END so existing
    rows / headers don't shift positions.
    """
    ws = _get_worksheet()
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    row_values = [
        ts, user_id, order_id, name, phone, address,
        city, state, pincode, item_type,
        str(amount) if amount not in (None, "") else "",
        payment_mode, status, notice,
        # ── Phase-B extensions (positions 15–19) ──
        user_name,
        master_order_id,
        alt_phone,
        str(token_amount) if token_amount not in (None, "") else "",
        weight,
    ]
    next_row = _find_next_empty_row(ws)
    # Auto-grow the sheet if we're about to write past its allocated rows.
    if hasattr(ws, "row_count") and next_row > int(ws.row_count):
        try:
            ws.add_rows(max(100, next_row - int(ws.row_count)))
        except Exception:
            pass  # non-fatal; update() below will still work or raise cleanly.

    # Columns A..S (19 cols) — build A1 range for the exact row.
    last_col_letter = _col_letter(len(COLUMNS))  # "S" for 19 columns
    target_range = f"A{next_row}:{last_col_letter}{next_row}"

    ws.update(target_range, [row_values], value_input_option="USER_ENTERED")

    # Normalise the updated_range to the same shape gspread.append_row emits
    # so the caller (parse_row_from_updated_range) keeps working unchanged.
    updated_range = f"'{ws.title}'!{target_range}"
    return {
        "ok": True,
        "updated_range": updated_range,
        "tab": ws.title,
        "sheet_id": os.getenv("MASTER_SHEET_ID", ""),
    }


def append_order_row_to_user_sheet(
    sheet_id: str,
    tab_name: str = "",
    *,
    # Same argument shape as append_order_row.
    user_id: str = "",
    order_id: str = "",
    name: str = "",
    phone: str = "",
    address: str = "",
    city: str = "",
    state: str = "",
    pincode: str = "",
    item_type: str = "",
    amount: Any = "",
    payment_mode: str = "",
    status: str = "Pending",
    notice: str = "",
    user_name: str = "",
    master_order_id: str = "",
    alt_phone: str = "",
    token_amount: Any = "",
    weight: str = "",
) -> Dict[str, Any]:
    """Phase-B: Write the SAME row to the user's own (per-user) sheet so
    they have a personal copy without admin / cross-tenant leakage.

    Best-effort by design — caller MUST swallow exceptions (the master
    sheet is the source of truth). Auto-creates a header row on the
    very first write to a fresh user tab.

    Note: `tab_name` may be a worksheet title OR a numeric `gid` —
    `_open_user_sheet` handles both.
    """
    if not sheet_id:
        return {"ok": False, "skipped": True, "reason": "no sheet_id"}
    ws = _open_user_sheet(sheet_id, tab_name or "0")
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    row_values = [
        ts, user_id, order_id, name, phone, address,
        city, state, pincode, item_type,
        str(amount) if amount not in (None, "") else "",
        payment_mode, status, notice,
        user_name,
        master_order_id,
        alt_phone,
        str(token_amount) if token_amount not in (None, "") else "",
        weight,
    ]
    # Auto-create header row if the sheet appears empty (no header yet).
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []
    if not first_row:
        try:
            header = [c.replace("_", " ").title() for c in COLUMNS]
            ws.update("A1:S1", [header], value_input_option="USER_ENTERED")
        except Exception:
            log.warning("Could not write header row to user sheet — appending anyway")

    next_row = _find_next_empty_row(ws)
    if hasattr(ws, "row_count") and next_row > int(ws.row_count):
        try:
            ws.add_rows(max(100, next_row - int(ws.row_count)))
        except Exception:
            pass
    last_col = _col_letter(len(COLUMNS))
    target_range = f"A{next_row}:{last_col}{next_row}"
    ws.update(target_range, [row_values], value_input_option="USER_ENTERED")
    return {
        "ok": True,
        "updated_range": f"'{ws.title}'!{target_range}",
        "tab": ws.title,
        "sheet_id": sheet_id,
    }


def probe_connection() -> Dict[str, Any]:
    """Light-weight auth + access probe. Returns connection info or error."""
    try:
        ws = _get_worksheet()
        return {
            "ok": True,
            "tab": ws.title,
            "row_count": ws.row_count,
            "col_count": ws.col_count,
            "headers": ws.row_values(1)[:16],
        }
    except Exception as e:
        log.exception("Google Sheet probe failed")
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Soft-Delete / Tombstone support
# ----------------------------------------------------------------------

_RANGE_ROW_RE = re.compile(r"[A-Z]+(\d+)(?::[A-Z]+(\d+))?$")


def parse_row_from_updated_range(updated_range: Optional[str]) -> Optional[int]:
    """Extract the numeric row index from a Google Sheets update range.

    Examples:
      "All Master Data!A123:N123"           -> 123
      "'All Master Data'!A8:N8"             -> 8
      "Sheet1!B42"                          -> 42

    Returns None if the range is missing/malformed.
    """
    if not updated_range:
        return None
    try:
        # Strip the tab-name prefix before `!`
        tail = updated_range.split("!", 1)[-1]
        m = _RANGE_ROW_RE.search(tail.replace("$", ""))
        if not m:
            return None
        # Use the first group (start-row).
        return int(m.group(1))
    except Exception:
        return None


def _col_letter(col_index_1based: int) -> str:
    """Convert 1-based column index (1 -> A, 27 -> AA) to spreadsheet letter."""
    s = ""
    n = col_index_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def mark_row_deleted(
    row_num: int,
    reason: str = "deleted by user",
) -> Dict[str, Any]:
    """Mark a Master Sheet row as DELETED without removing it.

    Writes "DELETED" into the Status column and appends a timestamp + reason
    to the Notice column. The underlying row and data remain untouched —
    this is a tombstone, so re-imports can skip it and nothing is lost
    even if an app user hits Delete by accident.

    Returns {"ok": True, "row": <n>, "tab": <name>} on success.
    Raises gspread/RuntimeError on failure so the caller can decide whether
    to abort the local delete or log and proceed.
    """
    if not isinstance(row_num, int) or row_num < 2:
        raise ValueError(f"Invalid row_num for soft-delete: {row_num!r}")

    ws = _get_worksheet()
    # Column positions in COLUMNS list (0-based); Google Sheets columns are 1-based.
    try:
        status_col = COLUMNS.index("status") + 1   # e.g. 13 -> "M"
        notice_col = COLUMNS.index("notice") + 1   # e.g. 14 -> "N"
    except ValueError as e:
        raise RuntimeError(f"COLUMNS layout missing status/notice: {e}")

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    notice_text = f"DELETED {ts} — {reason}"

    status_a1 = f"{_col_letter(status_col)}{row_num}"
    notice_a1 = f"{_col_letter(notice_col)}{row_num}"

    # Batch update so Status + Notice land atomically.
    ws.batch_update([
        {"range": status_a1, "values": [["DELETED"]]},
        {"range": notice_a1, "values": [[notice_text]]},
    ], value_input_option="USER_ENTERED")

    return {
        "ok": True,
        "row": row_num,
        "tab": ws.title,
        "status_cell": status_a1,
        "notice_cell": notice_a1,
    }


def update_row_status(
    row_num: int,
    status: str,
    extra_notice: Optional[str] = None,
    append_to_notice: bool = True,
) -> Dict[str, Any]:
    """Write back a new Status value to a Master Sheet row.

    Used by the Two-Way Status Sync: when the app changes a shipment's
    status (Pending → Dispatched → Delivered → Returned), this keeps the
    Master Sheet's Status column in perfect sync. Existing Notice content
    is preserved; `extra_notice` is appended with a leading separator.

    Args:
      row_num:        1-based sheet row (>= 2, row 1 is the header).
      status:         New value for the Status column. Stored verbatim.
      extra_notice:   Optional short text (e.g. "Tracking: ND00042") to
                      append to the Notice column. Ignored if empty.
      append_to_notice:
                      If True (default), prepend the current Notice cell
                      value before writing (so history is kept). If False,
                      Notice is overwritten with just the new value.

    Returns {"ok": True, "row": n, "tab": ..., "status_cell": ...,
             "notice_cell": ..., "status_written": status} on success.
    Raises on sheet-level failure so the caller can decide whether to
    abort the local update or log and continue.
    """
    if not isinstance(row_num, int) or row_num < 2:
        raise ValueError(f"Invalid row_num for status update: {row_num!r}")
    if status is None:
        raise ValueError("status cannot be None")

    ws = _get_worksheet()
    try:
        status_col = COLUMNS.index("status") + 1
        notice_col = COLUMNS.index("notice") + 1
    except ValueError as e:
        raise RuntimeError(f"COLUMNS layout missing status/notice: {e}")

    status_a1 = f"{_col_letter(status_col)}{row_num}"
    notice_a1 = f"{_col_letter(notice_col)}{row_num}"

    writes: List[Dict[str, Any]] = [
        {"range": status_a1, "values": [[str(status)]]},
    ]
    if extra_notice:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        suffix = f"[{ts}] {status}: {extra_notice}"
        if append_to_notice:
            # Read current cell so we don't clobber previous audit entries.
            try:
                existing = ws.cell(row_num, notice_col).value or ""
            except Exception:
                existing = ""
            new_notice = f"{existing}\n{suffix}" if existing.strip() else suffix
        else:
            new_notice = suffix
        writes.append({"range": notice_a1, "values": [[new_notice]]})

    ws.batch_update(writes, value_input_option="USER_ENTERED")

    return {
        "ok": True,
        "row": row_num,
        "tab": ws.title,
        "status_cell": status_a1,
        "notice_cell": notice_a1 if extra_notice else None,
        "status_written": str(status),
    }
