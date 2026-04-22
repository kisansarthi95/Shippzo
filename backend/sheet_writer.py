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
