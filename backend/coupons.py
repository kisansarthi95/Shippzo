"""
Coupon system (2026-04-30) — admin-managed discount codes.

Data model (stored in `db.coupons`):
    id                uuid4 hex string (primary key)
    code              UPPERCASE unique short code (e.g. "DIWALI25")
    discount_type     "flat" | "percent"
    discount_value    number — rupees for flat, 0-100 for percent
    valid_from        ISO datetime (inclusive)
    valid_to          ISO datetime (exclusive)
    max_uses          int | None (None = unlimited)
    used_count        int (default 0)
    applies_to_plans  list[str]  — subset of ["silver","gold","platinum"]
                                   (empty list = all plans)
    billing_cycles    list[str]  — subset of ["monthly","yearly"]
                                   (empty list = all cycles)
    active            bool (default True) — admin can pause without delete
    created_at        ISO datetime
    updated_at        ISO datetime

Business rules:
  • Code is stored UPPERCASE and compared case-insensitively on validate.
  • A coupon is VALID iff: active + within date window + usage < max_uses +
    plan matches applies_to_plans (or list empty) + cycle matches
    billing_cycles (or list empty).
  • flat discount is capped at the base price (can't go below 0).
  • percent discount is rounded DOWN (int floor) to match the "no decimals"
    rule from the Plans UI.
  • validate_coupon(...) is a pure function + DB read — no writes happen
    until the coupon is actually consumed at payment-verify time.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import re
import uuid

from pydantic import BaseModel, Field, field_validator
from fastapi import HTTPException


_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,20}$")


class CouponCreate(BaseModel):
    code: str
    discount_type: str              # "flat" | "percent"
    discount_value: float
    valid_from: str                 # ISO date / datetime
    valid_to: str
    max_uses: Optional[int] = None
    applies_to_plans: List[str] = Field(default_factory=list)
    billing_cycles: List[str] = Field(default_factory=list)
    active: bool = True

    @field_validator("code")
    @classmethod
    def _norm_code(cls, v: str) -> str:
        if v is None:
            raise ValueError("code is required")
        v = v.strip().upper()
        if not _CODE_RE.match(v):
            raise ValueError("code must be 3-20 chars, A-Z / 0-9 / _ / -")
        return v

    @field_validator("discount_type")
    @classmethod
    def _dt(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("flat", "percent"):
            raise ValueError("discount_type must be 'flat' or 'percent'")
        return v

    @field_validator("discount_value")
    @classmethod
    def _dv(cls, v: float) -> float:
        if v is None or v <= 0:
            raise ValueError("discount_value must be > 0")
        return float(v)

    @field_validator("applies_to_plans")
    @classmethod
    def _plans(cls, v: List[str]) -> List[str]:
        allowed = {"silver", "gold", "platinum"}
        if not v:
            return []
        cleaned = [p.strip().lower() for p in v if p]
        for p in cleaned:
            if p not in allowed:
                raise ValueError(f"applies_to_plans: '{p}' is not a paid plan")
        return cleaned

    @field_validator("billing_cycles")
    @classmethod
    def _cycles(cls, v: List[str]) -> List[str]:
        allowed = {"monthly", "yearly"}
        if not v:
            return []
        cleaned = [c.strip().lower() for c in v if c]
        for c in cleaned:
            if c not in allowed:
                raise ValueError(f"billing_cycles: '{c}' invalid")
        return cleaned


class CouponUpdate(BaseModel):
    """All fields optional — partial update."""
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    max_uses: Optional[int] = None
    applies_to_plans: Optional[List[str]] = None
    billing_cycles: Optional[List[str]] = None
    active: Optional[bool] = None


def _now_iso() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def coupon_to_api(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Strip Mongo-internal fields and compute a live `status` hint."""
    d = dict(doc or {})
    d.pop("_id", None)
    now = datetime.now(tz=timezone.utc)
    vf = _parse_iso(d.get("valid_from"))
    vt = _parse_iso(d.get("valid_to"))
    used = int(d.get("used_count") or 0)
    maxu = d.get("max_uses")
    status = "active"
    if not d.get("active"):
        status = "paused"
    elif vf and now < vf:
        status = "scheduled"
    elif vt and now >= vt:
        status = "expired"
    elif maxu is not None and used >= int(maxu):
        status = "exhausted"
    d["status"] = status
    return d


def validate_coupon(
    doc: Optional[Dict[str, Any]],
    plan_key: str,
    billing_cycle: str,
    base_inr: int,
) -> Tuple[bool, str, int, int]:
    """Check whether a coupon can be applied.

    Returns (ok, reason, discount_inr, final_inr). On failure, discount is
    0 and final == base.
    """
    base_inr = int(base_inr or 0)
    if not doc:
        return False, "No such coupon", 0, base_inr
    if not doc.get("active"):
        return False, "Coupon is paused", 0, base_inr
    now = datetime.now(tz=timezone.utc)
    vf = _parse_iso(doc.get("valid_from"))
    vt = _parse_iso(doc.get("valid_to"))
    if vf and now < vf:
        return False, "Coupon not yet active", 0, base_inr
    if vt and now >= vt:
        return False, "Coupon has expired", 0, base_inr
    maxu = doc.get("max_uses")
    used = int(doc.get("used_count") or 0)
    if maxu is not None and used >= int(maxu):
        return False, "Coupon usage limit reached", 0, base_inr
    atp = doc.get("applies_to_plans") or []
    if atp and plan_key not in atp:
        return False, f"Not valid for {plan_key.title()} plan", 0, base_inr
    bcs = doc.get("billing_cycles") or []
    if bcs and billing_cycle not in bcs:
        label = "yearly billing" if billing_cycle == "yearly" else "monthly billing"
        return False, f"Not valid on {label}", 0, base_inr
    # ---- Compute discount ----
    dtype = doc.get("discount_type")
    dval = float(doc.get("discount_value") or 0)
    if dtype == "percent":
        # floor to int (no decimals in UI)
        discount = int(base_inr * dval / 100.0)
    elif dtype == "flat":
        discount = int(dval)
    else:
        return False, "Unknown discount type", 0, base_inr
    if discount <= 0:
        return False, "No discount to apply", 0, base_inr
    if discount > base_inr:
        discount = base_inr
    final_inr = base_inr - discount
    return True, "OK", discount, final_inr


def new_coupon_doc(payload: CouponCreate) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "id": uuid.uuid4().hex,
        "code": payload.code,
        "discount_type": payload.discount_type,
        "discount_value": float(payload.discount_value),
        "valid_from": payload.valid_from,
        "valid_to": payload.valid_to,
        "max_uses": payload.max_uses,
        "used_count": 0,
        "applies_to_plans": payload.applies_to_plans,
        "billing_cycles": payload.billing_cycles,
        "active": bool(payload.active),
        "created_at": now,
        "updated_at": now,
    }


def ensure_code_valid(code: str) -> str:
    """Helper the server endpoint uses to normalise user-supplied codes
    at validate time. Raises 400 if malformed."""
    c = (code or "").strip().upper()
    if not _CODE_RE.match(c):
        raise HTTPException(
            status_code=400,
            detail="Coupon code must be 3-20 chars, letters / digits / _ / -",
        )
    return c
