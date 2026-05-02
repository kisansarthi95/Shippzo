"""
Pincode → State/District/Taluka lookup with MongoDB caching.

Uses the free public api.postalpincode.in service. Each fresh lookup is
cached in MongoDB so subsequent requests are instant and we minimise
external calls. The cache never expires (Indian pincode-to-locality
mapping is essentially static).

Usage:
    from pincode_lookup import resolve_pincode
    info = await resolve_pincode(db, "390019")
    # → {"state": "Gujarat", "district": "Vadodara",
    #    "taluka": "Vadodara", "office": "Manjalpur"}
    # or None if invalid/unknown.
"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import Any, Dict, Optional

import httpx

_LOG = logging.getLogger("pincode_lookup")
_PINCODE_RE = re.compile(r"^\d{6}$")
_API_URL = "https://api.postalpincode.in/pincode/{}"
_TIMEOUT = httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=3.0)


async def resolve_pincode(db, pincode: str) -> Optional[Dict[str, str]]:
    """Resolve a 6-digit Indian pincode to {state, district, taluka, office}.

    Behaviour:
      * Returns None for malformed pincodes.
      * Hits MongoDB `pincode_cache` first (collection auto-created).
      * Falls back to api.postalpincode.in. Stores the result on success.
      * On API failure (timeout / non-200) returns None — caller should
        treat the pincode as un-resolvable rather than retry-loop.

    The picked record is the FIRST PostOffice in the response. India Post
    sometimes returns multiple post offices for a pincode (different
    sub-localities); they all share state + district, so the first one
    is fine for our needs. We keep `office` purely for transparency.
    """
    pin = (pincode or "").strip()
    if not _PINCODE_RE.match(pin):
        return None

    # 1. Cache hit?
    try:
        cached = await db.pincode_cache.find_one({"_id": pin})
        if cached:
            return {
                "state":    cached.get("state", "") or "",
                "district": cached.get("district", "") or "",
                "taluka":   cached.get("taluka", "") or "",
                "office":   cached.get("office", "") or "",
            }
    except Exception as e:
        _LOG.warning("pincode cache read failed: %s", e)

    # 2. External API.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_API_URL.format(pin))
        if resp.status_code != 200:
            return None
        body = resp.json()
        if not isinstance(body, list) or not body:
            return None
        first = body[0]
        if first.get("Status") != "Success":
            return None
        offices = first.get("PostOffice") or []
        if not offices:
            return None
        po = offices[0]
        info = {
            "state":    (po.get("State") or "").strip(),
            "district": (po.get("District") or "").strip(),
            # India Post calls Taluka "Block" or "Division" depending
            # on the state — try both.
            "taluka":   (po.get("Block") or po.get("Division") or "").strip(),
            "office":   (po.get("Name") or "").strip(),
        }
    except (httpx.HTTPError, ValueError, KeyError) as e:
        _LOG.warning("pincode API failed for %s: %s", pin, e)
        return None
    except Exception:
        _LOG.exception("pincode API unexpected error for %s", pin)
        return None

    # 3. Cache for next time.
    try:
        await db.pincode_cache.update_one(
            {"_id": pin},
            {"$set": {**info, "_id": pin}},
            upsert=True,
        )
    except Exception as e:
        _LOG.warning("pincode cache write failed: %s", e)

    return info


async def enrich_with_pincode(db, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate-in-place: when PINCODE is known but STATE / CITY are
    missing, fetch them via resolve_pincode and fill the gaps.
    Never overwrites a value the model already produced.

    Works on the SCHEMA-key dict that _parse_schema_block returns
    (PINCODE, STATE, CITY, etc.).
    """
    pin = (fields.get("PINCODE") or "").strip()
    if not _PINCODE_RE.match(pin):
        return fields
    info = await resolve_pincode(db, pin)
    if not info:
        return fields
    state = info.get("state") or ""
    district = info.get("district") or ""
    if state and not (fields.get("STATE") or "").strip():
        fields["STATE"] = state
    if district and not (fields.get("CITY") or "").strip():
        fields["CITY"] = district
    return fields


async def validate_pincode_consistency(
    db,
    fields: Dict[str, Any],
) -> list:
    """Compare AI-extracted CITY/STATE against the canonical
    state/district returned by India Post for the given PINCODE.

    Returns a list of human-readable warning strings the frontend
    can surface as a banner / alert. Empty list = all consistent
    (or pincode unresolvable — silent).

    Does NOT mutate fields. Run AFTER enrich_with_pincode so we
    don't false-flag the gap-fill case.
    """
    warnings: list = []
    pin = (fields.get("PINCODE") or "").strip()
    if not _PINCODE_RE.match(pin):
        return warnings
    info = await resolve_pincode(db, pin)
    if not info:
        return warnings

    canonical_state    = (info.get("state") or "").strip().lower()
    canonical_district = (info.get("district") or "").strip().lower()
    user_state = (fields.get("STATE") or "").strip().lower()
    user_city  = (fields.get("CITY") or "").strip().lower()

    # State mismatch is a strong signal — different state means a
    # completely different region; almost certainly the pincode is
    # wrong.
    if canonical_state and user_state and canonical_state != user_state:
        warnings.append(
            f"⚠️ Pincode {pin} belongs to {info.get('state')} — but "
            f"address says {fields.get('STATE','').strip() or '—'}. "
            f"Please verify the pincode."
        )
        return warnings  # don't double-warn on city if state is wrong

    # City vs district: India Post returns the district name; the
    # AI may have extracted a sub-locality / town within that
    # district instead. A mismatch here is suggestive but not
    # certain — surface as a soft note.
    if (
        canonical_district
        and user_city
        and canonical_district != user_city
        and not _is_locality_within(canonical_district, user_city)
    ):
        warnings.append(
            f"ℹ️ Pincode {pin} is registered under "
            f"{info.get('district')} district. You entered city "
            f"\"{fields.get('CITY','').strip()}\" — please double-"
            f"check (it may be a locality within the district)."
        )
    return warnings


def _is_locality_within(district: str, city: str) -> bool:
    """Heuristic: is `city` plausibly a locality of `district`?
    Returns True when one contains the other (token-wise) so we
    don't false-flag legitimate sub-locality names."""
    d = (district or "").strip().lower()
    c = (city or "").strip().lower()
    if not d or not c:
        return False
    # Exact / containment match (e.g. district "Ahmedabad" vs
    # city "Ahmedabad city" or vice versa).
    return d in c or c in d


# ──────────── Phase-15: City → State + Pincode reverse lookup ─────────
# Used by the Smart Paste Summary Card to AUTO-FILL state and SUGGEST
# pincode candidates when the user has a city/locality but no pincode.
# Backed by the same free India Post API + a Mongo cache so a city is
# resolved exactly once.

_CITY_API_URL = "https://api.postalpincode.in/postoffice/{}"


async def resolve_city(db, city: str) -> Optional[Dict[str, Any]]:
    """Resolve a city / locality name to a list of post offices.

    Returns:
        {
          "state":      "Gujarat",   # only when ALL matches share one
                                     # state — empty string otherwise
          "state_confidence": "high" | "medium" | "low",
          "suggestions": [
            {"pincode": "395003", "office": "Adajan", "district": "Surat"},
            ...
          ],
          "count": int,
        }
        or None when the API gives nothing usable.

    A "high" confidence on `state` means the caller can silently
    auto-fill it; "medium" means show the value but flag it visually;
    "low" means the caller should NOT auto-apply.

    Caching:
      We key by lower-cased trimmed city name. Because India Post
      mappings are essentially static, we cache forever in the
      `city_cache` collection (separate from pincode_cache).
    """
    name = (city or "").strip()
    if len(name) < 3:
        return None
    key = name.lower()

    # 1. Cache hit.
    try:
        cached = await db.city_cache.find_one({"_id": key})
        if cached:
            cached.pop("_id", None)
            return cached
    except Exception as e:
        _LOG.warning("city cache read failed: %s", e)

    # 2. External API call.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_CITY_API_URL.format(name))
        if resp.status_code != 200:
            return None
        body = resp.json()
        if not isinstance(body, list) or not body:
            return None
        first = body[0]
        if first.get("Status") != "Success":
            return None
        offices = first.get("PostOffice") or []
        if not offices:
            return None
    except (httpx.HTTPError, ValueError, KeyError) as e:
        _LOG.warning("city API failed for %s: %s", name, e)
        return None
    except Exception:
        _LOG.exception("city API unexpected error for %s", name)
        return None

    # 3. Reduce + dedupe.
    seen = set()
    suggestions: list[Dict[str, str]] = []
    states: set[str] = set()
    for po in offices:
        pin = (po.get("Pincode") or "").strip()
        if not pin or pin in seen:
            continue
        seen.add(pin)
        st  = (po.get("State") or "").strip()
        dist = (po.get("District") or "").strip()
        off  = (po.get("Name") or "").strip()
        if st:
            states.add(st)
        suggestions.append({
            "pincode":  pin,
            "office":   off,
            "district": dist,
            "state":    st,
        })

    # Cap suggestions so the UI doesn't drown the user.
    suggestions = suggestions[:8]

    # State confidence rules:
    #   1 distinct state across all matches  → high
    #   2 distinct states but >70% of matches share one → medium
    #   3+ states → low
    state = ""
    confidence = "low"
    if len(states) == 1:
        state = next(iter(states))
        confidence = "high"
    elif len(states) >= 2:
        # Count occurrences of each state in the suggestions list
        # (offices we kept) and pick the dominant one if >70%.
        counts: Dict[str, int] = {}
        for s in suggestions:
            ss = s["state"]
            counts[ss] = counts.get(ss, 0) + 1
        if counts:
            top_state, top_count = max(counts.items(), key=lambda kv: kv[1])
            if top_count / max(len(suggestions), 1) >= 0.7:
                state = top_state
                confidence = "medium"

    out = {
        "state":             state,
        "state_confidence":  confidence,
        "suggestions":       suggestions,
        "count":             len(suggestions),
    }

    # 4. Cache forever.
    try:
        await db.city_cache.update_one(
            {"_id": key},
            {"$set": {**out, "_id": key}},
            upsert=True,
        )
    except Exception as e:
        _LOG.warning("city cache write failed: %s", e)

    return out
