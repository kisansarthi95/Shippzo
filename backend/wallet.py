"""
Phase-4a Credit Wallet engine.

Spec (from product owner, verbatim):
    • ₹100 = 100 Credits
    • After plan limit exhausted → pay per shipment:
        - Silver   → 4 credits / shipment
        - Gold     → 2 credits / shipment
        - Platinum → 1 credit  / shipment
    • AI Address Processing (always applied, EXCEPT on free trial):
        - Simple  → 0.5 credits
        - Medium  → 1   credit
        - Complex → 2   credits
      Max cap = 2 credits per order.
    • Order flow:
        1. Deduct AI processing credits (0.5 / 1 / 2)
        2. Check plan limit
            - available → use plan slot (no shipment credit)
            - exhausted → deduct shipment credit
        3. Update wallet
    • Block rule:
        - wallet == 0  AND  plan exhausted  →  refuse label generation.

Free-trial users are EXEMPT from AI credits (a conscious product decision
so the funnel stays clean — spec doesn't mandate paying-on-trial). The
moment a user upgrades to Silver+, AI credits start applying.

Collections:
    • wallets:
        { user_id, total_credits, used_credits, remaining_credits,
          created_at, updated_at }
    • credit_history:
        { id, user_id, created_at, order_id, credits, type, address_type,
          description, balance_after }

AI complexity detection in Phase-4a uses a deterministic heuristic
(address length + "noisy char" ratio). Phase-4b will replace this with a
real LLM call via Emergent integrations — but the public API in this
module stays the same, so the rest of the app never changes.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException

# -- Pricing ---------------------------------------------------------------

ComplexityLiteral = Literal["simple", "medium", "complex"]

AI_COST: Dict[ComplexityLiteral, float] = {
    "simple": 0.5,
    "medium": 1.0,
    "complex": 2.0,
}

SHIPMENT_OVERAGE: Dict[str, float] = {
    "silver": 4.0,
    "gold": 2.0,
    "platinum": 1.0,
}

CreditType = Literal["ai_processing", "shipment_charge", "purchase", "bonus", "refund"]


@dataclass(frozen=True)
class LabelCostBreakdown:
    """How much a single label is going to cost the user."""
    ai_credits: float
    ai_complexity: ComplexityLiteral
    ai_applies: bool              # False for free-trial users
    plan_has_room: bool           # True → only AI will be charged
    shipment_credits: float       # overage per spec, 0 if plan has room
    total: float


# -- Helpers ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_complexity(address_text: str) -> ComplexityLiteral:
    """Phase-4a heuristic. Replaced by real LLM in Phase-4b — public API
    preserved so this module is the ONLY place the downstream changes.

    Rules (deterministic & unit-testable):
      • length < 40 chars AND ≤ 2 digit-runs       → simple
      • length < 100 chars OR 2-3 digit-runs       → medium
      • length ≥ 100 OR  >3 digit-runs OR
        >5 punctuation chars OR newline-heavy      → complex
    """
    if not address_text:
        return "simple"
    s = address_text.strip()
    n = len(s)
    digit_runs = len(re.findall(r"\d+", s))
    punct = len(re.findall(r"[,;:/\-\\|()#]", s))
    newlines = s.count("\n")

    if n >= 100 or digit_runs > 3 or punct > 5 or newlines > 2:
        return "complex"
    if n >= 40 or digit_runs > 1 or punct > 2 or newlines > 0:
        return "medium"
    return "simple"


def ai_cost_for(complexity: ComplexityLiteral, overrides: Optional[Dict[str, float]] = None) -> float:
    """Per-spec AI charge with an optional per-user override.

    overrides dict keys: "simple" | "medium" | "complex".
    All values are clamped into [0, 2] (spec cap "max 2 credits per order").
    """
    if overrides and complexity in overrides:
        try:
            return min(max(float(overrides[complexity]), 0.0), 2.0)
        except (TypeError, ValueError):
            pass
    return min(AI_COST[complexity], 2.0)


def overage_cost_for(plan_key: str) -> float:
    return SHIPMENT_OVERAGE.get(plan_key, 0.0)


# -- Wallet doc CRUD -------------------------------------------------------

async def ensure_wallet(db, user_id: str) -> Dict[str, Any]:
    """Create a zero-balance wallet on first access."""
    doc = await db.wallets.find_one({"user_id": user_id}, {"_id": 0})
    if doc:
        return doc
    now = _now_iso()
    doc = {
        "user_id": user_id,
        "total_credits": 0.0,
        "used_credits": 0.0,
        "remaining_credits": 0.0,
        "created_at": now,
        "updated_at": now,
    }
    await db.wallets.insert_one(doc)
    return doc


async def get_balance(db, user_id: str) -> float:
    w = await ensure_wallet(db, user_id)
    return float(w.get("remaining_credits", 0.0))


async def _apply_delta(
    db,
    user_id: str,
    *,
    used_delta: float = 0.0,
    total_delta: float = 0.0,
) -> Dict[str, Any]:
    """Mutate the wallet atomically. Remaining is always derived from
    (total - used) so drift is impossible."""
    await ensure_wallet(db, user_id)
    updated = await db.wallets.find_one_and_update(
        {"user_id": user_id},
        {
            "$inc": {
                "used_credits": used_delta,
                "total_credits": total_delta,
            },
            "$set": {"updated_at": _now_iso()},
        },
        return_document=True,
    )
    # Recompute remaining (cheap).
    total = float(updated.get("total_credits", 0.0))
    used = float(updated.get("used_credits", 0.0))
    updated["remaining_credits"] = round(total - used, 4)
    await db.wallets.update_one(
        {"user_id": user_id}, {"$set": {"remaining_credits": updated["remaining_credits"]}}
    )
    return updated


# -- Ledger entries --------------------------------------------------------

async def record_history(
    db,
    user_id: str,
    *,
    credits: float,
    ctype: CreditType,
    order_id: Optional[str] = None,
    address_type: Optional[ComplexityLiteral] = None,
    description: str = "",
    balance_after: Optional[float] = None,
) -> Dict[str, Any]:
    entry = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "created_at": _now_iso(),
        "order_id": order_id or "",
        "credits": round(float(credits), 4),   # can be negative (refund/purchase positive)
        "type": ctype,
        "address_type": address_type or "",
        "description": description,
        "balance_after": round(float(balance_after or 0.0), 4),
    }
    await db.credit_history.insert_one(entry)
    return entry


async def list_history(
    db, user_id: str, limit: int = 100
) -> List[Dict[str, Any]]:
    docs = await db.credit_history.find(
        {"user_id": user_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(limit)
    return docs


# -- High-level ops used by the shipment path ------------------------------

def compute_label_cost(
    user: Dict[str, Any],
    address_text: str,
    plan_has_room: bool,
    complexity_override: Optional[ComplexityLiteral] = None,
    ai_costs: Optional[Dict[str, float]] = None,
) -> LabelCostBreakdown:
    plan = (user.get("plan") or "free_trial").lower()
    # Free trial is exempt from AI (see module docstring — product decision).
    ai_applies = plan != "free_trial"
    complexity: ComplexityLiteral = (
        complexity_override if complexity_override in AI_COST else detect_complexity(address_text)
    )
    ai_c = ai_cost_for(complexity, ai_costs) if ai_applies else 0.0
    ship_c = 0.0 if plan_has_room else overage_cost_for(plan)
    return LabelCostBreakdown(
        ai_credits=ai_c,
        ai_complexity=complexity,
        ai_applies=ai_applies,
        plan_has_room=plan_has_room,
        shipment_credits=ship_c,
        total=round(ai_c + ship_c, 4),
    )


async def classify_and_cost(
    user: Dict[str, Any],
    address_text: str,
    plan_has_room: bool,
    ai_costs: Optional[Dict[str, float]] = None,
) -> Tuple[LabelCostBreakdown, str]:
    """Phase-4b entry point — awaits the LLM classifier and returns the
    priced breakdown together with the LLM's reason string. Free-trial
    users skip the LLM entirely (AI charges are waived for them anyway).
    """
    plan = (user.get("plan") or "free_trial").lower()
    if plan == "free_trial":
        return (compute_label_cost(user, address_text, plan_has_room, ai_costs=ai_costs), "free-trial (AI waived)")
    # Deferred import to keep the wallet module a leaf dependency.
    from address_ai import classify_address
    complexity, reason = await classify_address(address_text)
    bd = compute_label_cost(user, address_text, plan_has_room, complexity_override=complexity, ai_costs=ai_costs)
    return (bd, reason)


async def require_balance(
    db,
    user: Dict[str, Any],
    address_text: str,
    plan_has_room: bool,
    complexity_override: Optional[ComplexityLiteral] = None,
    ai_costs: Optional[Dict[str, float]] = None,
) -> LabelCostBreakdown:
    """Preflight: raise 402 if wallet cannot cover this label.

    ALSO enforces the hard block rule:
        wallet == 0 AND plan exhausted → refuse outright (even if
        plan-has-room logic somehow matched, we double-check).
    """
    breakdown = compute_label_cost(user, address_text, plan_has_room, complexity_override, ai_costs)
    if breakdown.total <= 0:
        return breakdown  # free trial, plan has room → nothing to charge
    bal = await get_balance(db, user["id"])
    if bal < breakdown.total - 1e-6:
        # Explain exactly what's missing.
        need = round(breakdown.total, 2)
        have = round(bal, 2)
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient credits. This label needs {need} credits "
                f"(AI {breakdown.ai_credits} + shipment {breakdown.shipment_credits}); "
                f"you have {have}. Top up from Wallet to continue."
            ),
        )
    return breakdown


async def charge_for_label(
    db,
    user: Dict[str, Any],
    order_id: str,
    breakdown: LabelCostBreakdown,
) -> Tuple[float, List[Dict[str, Any]]]:
    """Debit the wallet and write up to 2 history entries.

    Returns (new_balance, [entries_written]).
    """
    if breakdown.total <= 0:
        return (await get_balance(db, user["id"]), [])

    uid = user["id"]
    entries: List[Dict[str, Any]] = []
    plan = (user.get("plan") or "free_trial").title()

    if breakdown.ai_credits > 0:
        w = await _apply_delta(db, uid, used_delta=breakdown.ai_credits)
        bal = float(w.get("remaining_credits", 0.0))
        entries.append(await record_history(
            db, uid,
            credits=-breakdown.ai_credits,
            ctype="ai_processing",
            order_id=order_id,
            address_type=breakdown.ai_complexity,
            description=f"AI address formatting · {breakdown.ai_complexity}",
            balance_after=bal,
        ))

    if breakdown.shipment_credits > 0:
        w = await _apply_delta(db, uid, used_delta=breakdown.shipment_credits)
        bal = float(w.get("remaining_credits", 0.0))
        entries.append(await record_history(
            db, uid,
            credits=-breakdown.shipment_credits,
            ctype="shipment_charge",
            order_id=order_id,
            description=f"{plan} overage shipment charge",
            balance_after=bal,
        ))

    return (await get_balance(db, uid), entries)


# -- Purchases / bonuses / refunds ----------------------------------------

async def add_credits(
    db,
    user_id: str,
    credits: float,
    *,
    ctype: CreditType = "purchase",
    description: str = "",
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    if credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive")
    w = await _apply_delta(db, user_id, total_delta=credits)
    bal = float(w.get("remaining_credits", 0.0))
    entry = await record_history(
        db, user_id,
        credits=credits,
        ctype=ctype,
        order_id=order_id,
        description=description or ("Credit top-up" if ctype == "purchase" else ctype.replace("_", " ").title()),
        balance_after=bal,
    )
    return {"wallet": w, "history": entry}
