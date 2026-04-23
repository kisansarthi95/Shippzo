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

# Column order MUST match the Master Sheet headers exactly (left → right):
#   Timestamp | User ID | Order ID | Name | Phone | Address | City |
#   State | Pincode | Item Type (Product Name) | Amount (Total Value) |
#   Payment Mode | Status | Notice
COLUMNS = [
    "timestamp", "user_id", "order_id", "name", "phone", "address",
    "city", "state", "pincode", "item_type", "amount",
    "payment_mode", "status", "notice",
]


def _get_worksheet():
    """Authenticate and return the target worksheet. Raises on failure."""
    key_path = os.getenv("GOOGLE_SA_JSON_PATH")
    sheet_id = os.getenv("MASTER_SHEET_ID")
    tab_name = os.getenv("MASTER_SHEET_TAB", "Sheet1")

    if not key_path or not os.path.isfile(key_path):
        raise RuntimeError(f"Service account JSON not found at {key_path!r}")
    if not sheet_id:
        raise RuntimeError("MASTER_SHEET_ID env var is not set")

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
) -> Dict[str, Any]:
    """
    Append one row to the Master Sheet at the first guaranteed-empty row.

    Unlike gspread's `append_row`, this writes to an explicit row index
    computed via `_find_next_empty_row`, so rows that were previously
    soft-deleted (Status="DELETED") are preserved forever — no accidental
    overwrite. Returns {"ok": True, "updated_range": "'Tab'!A<n>:N<n>",
    "tab": ..., "sheet_id": ...} on success. Raises on failure.
    """
    ws = _get_worksheet()
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    row_values = [
        ts, user_id, order_id, name, phone, address,
        city, state, pincode, item_type,
        str(amount) if amount not in (None, "") else "",
        payment_mode, status, notice,
    ]
    next_row = _find_next_empty_row(ws)
    # Auto-grow the sheet if we're about to write past its allocated rows.
    if hasattr(ws, "row_count") and next_row > int(ws.row_count):
        try:
            ws.add_rows(max(100, next_row - int(ws.row_count)))
        except Exception:
            pass  # non-fatal; update() below will still work or raise cleanly.

    # Columns A..N (14 cols) — build A1 range for the exact row.
    last_col_letter = _col_letter(len(COLUMNS))  # "N" for 14 columns
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
