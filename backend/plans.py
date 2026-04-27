"""
Phase-3a subscription plan limits + usage counters.

Plans (exact spec from product owner):
    • free_trial : 10 labels ONE-TIME, 7-day validity, no bulk print
    • silver     : 50 labels/month, single-label print only
    • gold       : 300 labels/month, bulk print up to 50 at a time  (Most Popular)
    • platinum   : 1500 labels/month, bulk print up to 100, 100/day

A "label" in this system == one successful shipment insert. The `/api/shipments`
create path and the `/api/orders/pending/{id}/ship` promotion path both call
`check_and_reserve(...)`; on success they insert as normal, on failure they
get a 402 Payment Required with the exact reason so the UI can show the
upgrade CTA.

Counters live in a single collection `usage_counters` keyed by
`{user_id, period}`.  For monthly counters period = "YYYY-MM"; for daily it's
"YYYY-MM-DD"; for the free trial it's the literal string "trial" so the
ONE-TIME lifetime cap never resets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException


# --- Plan table -----------------------------------------------------------

@dataclass(frozen=True)
class PlanSpec:
    key: str
    name: str
    feel: str          # 1-line positioning
    purpose: str       # who it is for
    price_inr: int     # 0 for free trial
    label_cap: int     # per period (or one-time for trial)
    period: str        # "trial" | "month"
    trial_days: Optional[int]  # only set for free_trial
    bulk_max: int      # 0 = no bulk print
    daily_cap: Optional[int]   # Platinum only
    badge: Optional[str]       # "Most Popular" | "🚀" | None
    cta: str           # upgrade line under the card


PLANS: Dict[str, PlanSpec] = {
    "free_trial": PlanSpec(
        key="free_trial",
        name="Free Trial",
        feel="Try Before You Start",
        purpose="Explore the system with limited access",
        price_inr=0,
        label_cap=10,
        period="trial",
        trial_days=7,
        bulk_max=0,
        daily_cap=None,
        badge=None,
        cta="Upgrade to unlock full features",
    ),
    "silver": PlanSpec(
        key="silver",
        name="Silver",
        feel="Start Small — Validate Your Orders",
        purpose="Best for beginners testing their sales",
        price_inr=199,
        label_cap=50,
        period="month",
        trial_days=None,
        bulk_max=0,
        daily_cap=None,
        badge=None,
        cta="Upgrade when your orders grow",
    ),
    "gold": PlanSpec(
        key="gold",
        name="Gold",
        feel="Grow Faster — Handle Regular Orders Smoothly",
        purpose="Perfect for consistent daily sellers",
        price_inr=499,
        label_cap=300,
        period="month",
        trial_days=None,
        bulk_max=50,
        daily_cap=None,
        badge="Most Popular",
        cta="Upgrade for high-volume efficiency",
    ),
    "platinum": PlanSpec(
        key="platinum",
        name="Platinum",
        feel="Scale Without Friction — Built for Serious Business",
        purpose="For high-volume sellers who need speed & power",
        price_inr=999,
        label_cap=1500,
        period="month",
        trial_days=None,
        bulk_max=100,
        daily_cap=100,
        badge="🚀",
        cta="No delays. No limits feeling. Full control.",
    ),
}


def plan_for(user: Dict[str, Any]) -> PlanSpec:
    return PLANS.get(user.get("plan") or "free_trial", PLANS["free_trial"])


# --- Period helpers -------------------------------------------------------

def _month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return d.strftime("%Y-%m")


def _day_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%d")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Usage counter access ------------------------------------------------

async def _get_count(db, user_id: str, period: str) -> int:
    doc = await db.usage_counters.find_one(
        {"user_id": user_id, "period": period}, {"_id": 0, "count": 1}
    )
    return int(doc.get("count", 0)) if doc else 0


async def _incr(db, user_id: str, period: str) -> int:
    r = await db.usage_counters.find_one_and_update(
        {"user_id": user_id, "period": period},
        {"$inc": {"count": 1}, "$set": {"last_at": _now().isoformat()}},
        upsert=True,
        return_document=True,
    )
    return int(r.get("count", 1))


# --- Core guard called from the shipment-create paths -------------------

async def plan_room_status(db, user: Dict[str, Any]) -> Dict[str, Any]:
    """Non-raising introspection: does the user have room for another label
    under their CURRENT PLAN only? (Ignores wallet.)

    Returns a small dict the caller can reason about — used by Phase-4a
    when the wallet engine may still allow overage on paid plans.
    """
    plan = plan_for(user)
    uid = user["id"]
    now = _now()
    out = {
        "plan": plan.key,
        "plan_name": plan.name,
        "trial_expired": False,
        "plan_expired": False,
        "plan_expires_at": user.get("plan_expires_at"),
        "plan_has_room": True,  # default optimistic
        "daily_blocked": False,
        "period": plan.period,
    }
    if plan.period == "trial":
        exp_iso = user.get("plan_expires_at")
        if exp_iso:
            try:
                exp = datetime.fromisoformat(exp_iso)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                out["trial_expired"] = now > exp
            except Exception:
                pass
        used = await _get_count(db, uid, "trial")
        out["plan_has_room"] = (not out["trial_expired"]) and (used < plan.label_cap)
        return out

    # Monthly — paid plans. Also surface paid-plan validity for the
    # caller (create_shipment) to enforce 402 on expired subscriptions.
    exp_iso = user.get("plan_expires_at")
    if exp_iso:
        try:
            exp = datetime.fromisoformat(exp_iso)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            out["plan_expired"] = now > exp
        except Exception:
            pass
    used_month = await _get_count(db, uid, _month_key(now))
    out["plan_has_room"] = (not out["plan_expired"]) and (used_month < plan.label_cap)
    if plan.daily_cap is not None:
        used_day = await _get_count(db, uid, _day_key(now))
        out["daily_blocked"] = used_day >= plan.daily_cap
    return out


async def ensure_can_create_label(db, user: Dict[str, Any]) -> PlanSpec:
    """Raise 402 Payment Required if the user's plan cannot fit one more
    label. Returns the PlanSpec on success.

    This does NOT increment the counter — call `bump_label_usage(...)` after
    the shipment row has actually been inserted so we never charge for a
    failed insert.
    """
    plan = plan_for(user)
    uid = user["id"]
    now = _now()

    if plan.period == "trial":
        # 1) trial expiry check
        exp_iso = user.get("plan_expires_at")
        if exp_iso:
            try:
                exp = datetime.fromisoformat(exp_iso)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now > exp:
                    raise HTTPException(
                        status_code=402,
                        detail="Your 7-day free trial has expired. Upgrade to continue.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass
        # 2) lifetime cap — one-time "trial" counter
        used = await _get_count(db, uid, "trial")
        if used >= plan.label_cap:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Free trial limit reached ({plan.label_cap} labels). "
                    "Upgrade to Silver or higher to keep shipping."
                ),
            )
        return plan

    # Monthly plans
    # 1) Subscription validity check (paid plans). Once expired, we
    #    block label creation with a 402 + suggest renewal — but DO
    #    NOT auto-downgrade because the user may have used way more
    #    than the free-trial cap. They simply renew to keep going.
    exp_iso = user.get("plan_expires_at")
    if exp_iso:
        try:
            exp = datetime.fromisoformat(exp_iso)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"Your {plan.name} subscription expired on "
                        f"{exp.strftime('%d %b %Y')}. Renew to keep "
                        "creating labels."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            # Malformed expiry stamp — fall through; better to allow
            # than to block. The Plans screen will surface the issue.
            pass

    mkey = _month_key(now)
    used_month = await _get_count(db, uid, mkey)
    if used_month >= plan.label_cap:
        raise HTTPException(
            status_code=402,
            detail=(
                f"{plan.name} plan monthly limit reached "
                f"({used_month}/{plan.label_cap}). Upgrade or wait for next month."
            ),
        )
    # Platinum also enforces a daily cap
    if plan.daily_cap is not None:
        dkey = _day_key(now)
        used_day = await _get_count(db, uid, dkey)
        if used_day >= plan.daily_cap:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Daily limit reached ({used_day}/{plan.daily_cap} for today). "
                    "Please try again tomorrow."
                ),
            )
    return plan


async def bump_label_usage(db, user: Dict[str, Any]) -> None:
    """Called AFTER a successful shipment insert. Increments the right
    counter(s) for the user's plan. Never raises."""
    try:
        plan = plan_for(user)
        uid = user["id"]
        if plan.period == "trial":
            await _incr(db, uid, "trial")
            return
        await _incr(db, uid, _month_key())
        if plan.daily_cap is not None:
            await _incr(db, uid, _day_key())
    except Exception:
        # Don't block a completed shipment because a counter hiccuped.
        pass


async def ensure_can_bulk(db, user: Dict[str, Any], batch_size: int) -> PlanSpec:
    """Guard for a future bulk-print endpoint.

    Returns the active plan on success; 402 otherwise. Used by the /bulk
    endpoint we'll wire in Phase 3c once the UI lands.
    """
    plan = plan_for(user)
    if plan.bulk_max <= 0:
        raise HTTPException(
            status_code=402,
            detail=(
                f"{plan.name} plan does not include bulk printing. "
                "Upgrade to Gold for up to 50 or Platinum for up to 100 labels at once."
            ),
        )
    if batch_size > plan.bulk_max:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Bulk batch size {batch_size} exceeds your {plan.name} limit "
                f"of {plan.bulk_max}. Split the batch or upgrade."
            ),
        )
    return plan


# --- Usage summary for /api/me/usage ------------------------------------

async def usage_summary(db, user: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_for(user)
    uid = user["id"]
    now = _now()

    out: Dict[str, Any] = {
        "plan": plan.key,
        "plan_name": plan.name,
        "price_inr": plan.price_inr,
        "bulk_max": plan.bulk_max,
        "can_bulk": plan.bulk_max > 0,
        "daily_cap": plan.daily_cap,
        "period": plan.period,
    }

    if plan.period == "trial":
        used = await _get_count(db, uid, "trial")
        exp_iso = user.get("plan_expires_at")
        expired = False
        days_left: Optional[int] = None
        if exp_iso:
            try:
                exp = datetime.fromisoformat(exp_iso)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                expired = now > exp
                days_left = max(0, (exp - now).days)
            except Exception:
                pass
        out.update({
            "label_cap": plan.label_cap,
            "labels_used": used,
            "labels_remaining": max(0, plan.label_cap - used),
            "trial_expires_at": exp_iso,
            "trial_days_left": days_left,
            "trial_expired": expired,
            # Mirror these on the trial branch too so the frontend
            # can use the same null-checks regardless of period.
            "plan_expires_at": None,
            "plan_days_left": None,
            "plan_expired": False,
            "plan_billing_cycle": None,
            "can_create_label": (not expired) and used < plan.label_cap,
        })
        return out

    mkey = _month_key(now)
    used_m = await _get_count(db, uid, mkey)
    out.update({
        "label_cap": plan.label_cap,
        "labels_used": used_m,
        "labels_remaining": max(0, plan.label_cap - used_m),
        "period_key": mkey,
        "can_create_label": used_m < plan.label_cap,
    })
    # Surface paid-plan validity so the Plans / Home screens can
    # show "Renews on …", "Expires in N days", and badge expired plans.
    exp_iso = user.get("plan_expires_at")
    expired = False
    days_left: Optional[int] = None
    if exp_iso:
        try:
            exp = datetime.fromisoformat(exp_iso)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            expired = now > exp
            days_left = max(0, (exp - now).days)
        except Exception:
            pass
    out.update({
        "plan_expires_at": exp_iso,
        "plan_days_left": days_left,
        "plan_expired": expired,
        "plan_billing_cycle": user.get("plan_billing_cycle"),
        "can_create_label": out["can_create_label"] and not expired,
    })
    if plan.daily_cap is not None:
        dkey = _day_key(now)
        used_d = await _get_count(db, uid, dkey)
        out.update({
            "today_used": used_d,
            "today_remaining": max(0, plan.daily_cap - used_d),
            "daily_key": dkey,
            "can_create_label": out["can_create_label"] and used_d < plan.daily_cap,
        })
    return out


# --- Mutations used by /api/plans/upgrade -------------------------------

def plan_start_payload(plan_key: str) -> Dict[str, Any]:
    """Build the `$set` dict for switching a user to another plan."""
    plan = PLANS.get(plan_key)
    if plan is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{plan_key}'")
    now = _now()
    payload: Dict[str, Any] = {
        "plan": plan.key,
        "plan_started_at": now.isoformat(),
    }
    if plan.trial_days:
        payload["plan_expires_at"] = (now + timedelta(days=plan.trial_days)).isoformat()
    else:
        # Paid plans run open-ended; Razorpay renewal will re-stamp later.
        payload["plan_expires_at"] = None
    return payload


def public_plan_list() -> list:
    """Serializable list of plans for the /plans screen."""
    out = []
    for p in PLANS.values():
        out.append({
            "key": p.key,
            "name": p.name,
            "feel": p.feel,
            "purpose": p.purpose,
            "price_inr": p.price_inr,
            "label_cap": p.label_cap,
            "period": p.period,
            "trial_days": p.trial_days,
            "bulk_max": p.bulk_max,
            "daily_cap": p.daily_cap,
            "badge": p.badge,
            "cta": p.cta,
        })
    return out
