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
    Append one row to the Master Sheet.
    Returns {"row": <row_number>, "sheet_id": ..., "tab": ...} on success.
    Raises on failure (caller must wrap in try/except).
    """
    ws = _get_worksheet()
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    row = [
        ts, user_id, order_id, name, phone, address,
        city, state, pincode, item_type,
        str(amount) if amount not in (None, "") else "",
        payment_mode, status, notice,
    ]
    # value_input_option="USER_ENTERED" — respects Sheet formatting (numbers, dates)
    result = ws.append_row(row, value_input_option="USER_ENTERED")
    # gspread returns the update range, e.g. 'All Master Data!A123:N123'
    updated = (result or {}).get("updates", {})
    return {
        "ok": True,
        "updated_range": updated.get("updatedRange"),
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
