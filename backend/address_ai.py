"""
Phase-4b Address complexity classifier (LLM-backed).

Exposes a single coroutine `classify_address(text) -> ("simple"|"medium"|"complex", reason)`.

Strategy:
  1. Check an in-memory LRU cache (500 entries) keyed by normalised text.
     We DO NOT hit the LLM for the same exact address twice — most apps
     submit the same line multiple times while iterating on the form.
  2. Call the Emergent LLM via emergentintegrations.
  3. Parse a strict JSON response. If the model misbehaves (off-schema,
     timeout, or error), we transparently fall back to the deterministic
     heuristic from `wallet.detect_complexity` so a shipment is NEVER
     blocked by an LLM outage.

Design constraints:
  • Response must be cheap + fast — we use a tiny fast model.
  • The public API returns the heuristic answer if the LLM isn't
    reachable, so the rest of the pipeline (wallet charge, plan gate)
    always gets a valid classification.
  • Blast radius of an LLM bug is limited to price bucketing — never to
    correctness of the shipment record.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

_LOG = logging.getLogger("address_ai")

# Tiny, cheap, deterministic model. User can swap by setting
# ADDRESS_AI_MODEL="openai:gpt-5-nano" (provider:model) in env.
_DEFAULT_PROVIDER = "openai"
_DEFAULT_MODEL = "gpt-4.1-nano"
_PROVIDER, _MODEL = (
    os.getenv("ADDRESS_AI_MODEL", f"{_DEFAULT_PROVIDER}:{_DEFAULT_MODEL}").split(":", 1)
    if ":" in os.getenv("ADDRESS_AI_MODEL", f"{_DEFAULT_PROVIDER}:{_DEFAULT_MODEL}")
    else (_DEFAULT_PROVIDER, _DEFAULT_MODEL)
)

_LLM_KEY = os.getenv("EMERGENT_LLM_KEY", "")
_TIMEOUT = float(os.getenv("ADDRESS_AI_TIMEOUT", "4.0"))  # seconds — keep the UX snappy

_SYSTEM_PROMPT = (
    "You are an Indian-postal-address triage assistant. "
    "Classify the clarity/completeness of each address into exactly one bucket. "
    "Respond with ONE JSON object and nothing else, matching this schema:\n"
    '{ "complexity": "simple"|"medium"|"complex", "reason": "<max 10 words>" }\n\n'
    "Rules of thumb:\n"
    "  • simple  → concise, clean, city+state+pincode clear, <80 chars\n"
    "  • medium  → mostly clear but extra landmarks or mixed order\n"
    "  • complex → very long, noisy, multi-line, unclear order, or appears "
    "to contain multiple addresses/personal notes.\n"
)


# ---- In-memory LRU cache ------------------------------------------------

_CACHE_MAX = 500
_cache: "OrderedDict[str, Tuple[str, str, float]]" = OrderedDict()  # key → (complexity, reason, epoch)
_CACHE_TTL = 60 * 30  # 30-minute TTL so a long-running server doesn't drift


def _cache_get(k: str) -> Optional[Tuple[str, str]]:
    v = _cache.get(k)
    if not v:
        return None
    complexity, reason, ts = v
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(k, None)
        return None
    _cache.move_to_end(k)  # mark as recently used
    return complexity, reason


def _cache_put(k: str, complexity: str, reason: str) -> None:
    _cache[k] = (complexity, reason, time.time())
    _cache.move_to_end(k)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ---- Heuristic fallback ------------------------------------------------

def _heuristic(text: str) -> Tuple[str, str]:
    """Mirror of wallet.detect_complexity — kept inline so we don't create
    an import cycle with wallet.py."""
    s = (text or "").strip()
    if not s:
        return ("simple", "empty address")
    n = len(s)
    digit_runs = len(re.findall(r"\d+", s))
    punct = len(re.findall(r"[,;:/\-\\|()#]", s))
    newlines = s.count("\n")
    if n >= 100 or digit_runs > 3 or punct > 5 or newlines > 2:
        return ("complex", "heuristic: long/noisy")
    if n >= 40 or digit_runs > 1 or punct > 2 or newlines > 0:
        return ("medium", "heuristic: moderate")
    return ("simple", "heuristic: short & clean")


# ---- Public API --------------------------------------------------------

def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:300]


async def classify_address(text: str) -> Tuple[str, str]:
    """Return (complexity, reason).

    • Falls back to the deterministic heuristic on ANY error so the
      shipment pipeline remains resilient to LLM outages.
    """
    norm = _normalise(text)
    if not norm:
        return ("simple", "empty address")

    cached = _cache_get(norm)
    if cached:
        return cached

    # No key configured → heuristic only.
    if not _LLM_KEY:
        result = _heuristic(text)
        _cache_put(norm, *result)
        return result

    try:
        # Import here so the module can still be imported without the
        # emergentintegrations package present (e.g. in some CI setups).
        from emergentintegrations.llm.chat import LlmChat, UserMessage

        chat = (
            LlmChat(
                api_key=_LLM_KEY,
                session_id=f"address-ai-{norm[:16]}",  # per-address session, short TTL
                system_message=_SYSTEM_PROMPT,
            )
            .with_model(_PROVIDER, _MODEL)
        )
        msg = UserMessage(text=f"Address:\n```\n{text}\n```")
        resp: str = await asyncio.wait_for(chat.send_message(msg), timeout=_TIMEOUT)
        complexity, reason = _parse_response(resp)
        _cache_put(norm, complexity, reason)
        return (complexity, reason)

    except Exception as e:
        _LOG.warning("LLM classify failed (%s) — falling back to heuristic", e)
        result = _heuristic(text)
        _cache_put(norm, *result)
        return result


def _parse_response(raw: str) -> Tuple[str, str]:
    """Extract the first valid JSON object. Tolerant of markdown fences,
    stray text, or 1-line prose responses."""
    if not raw:
        raise ValueError("empty LLM response")
    # Strip code fences or stray backticks.
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    # Locate the first '{'…'}' block greedily.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"no JSON object in: {text[:120]}")
    obj = json.loads(m.group(0))
    c = str(obj.get("complexity", "")).strip().lower()
    if c not in ("simple", "medium", "complex"):
        raise ValueError(f"unexpected complexity value: {c!r}")
    reason = str(obj.get("reason", "")).strip()[:160] or "llm classification"
    return c, reason
