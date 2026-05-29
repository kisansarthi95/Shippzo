"""
WhatsApp Provider package — pluggable delivery back-ends for OTPs.

Why provider-based?
-------------------
Different merchants prefer different WhatsApp BSP / aggregator stacks
(FlowConnect, WATI, Interakt, Meta Cloud API, Gupshup, AiSensy …).
Each has its own auth scheme, payload shape, rate-limit profile, and
error semantics. We isolate every quirk behind one abstract class
(see ``base.WhatsAppProvider``) so the rest of Shippzo only ever
depends on the *shape* of a provider — never on a specific vendor.

Switching providers later is a 3-line operation:

    1. Drop a new module in ``whatsapp_providers/<name>.py`` that
       subclasses ``WhatsAppProvider``.
    2. Register it in ``_PROVIDER_REGISTRY`` below (one line).
    3. Flip the ``WHATSAPP_OTP_PROVIDER`` env var (or the per-tenant
       DB override in ``settings.whatsapp_otp.provider``) to the new
       provider's slug.

No authentication / signup / OTP-validation code needs to be touched.

Resolution order (highest priority first) for the active provider:

    1. Per-tenant override in MongoDB:
         settings.whatsapp_otp = { provider, endpoint, api_token, active }
       — useful when a specific shop wants to use its own BSP.
    2. App-wide env var ``WHATSAPP_OTP_PROVIDER`` (default "flowconnect").

Returning ``None`` from ``get_active_provider()`` is a valid state and
must be handled gracefully by callers — the dispatcher service skips
delivery and logs a warning instead of raising.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

from .base import WhatsAppProvider, ProviderResult
from .flowconnect import FlowConnectProvider

_LOG = logging.getLogger("whatsapp_providers")


# ---------------------------------------------------------------------------
# Provider registry — map a stable slug to a *factory* callable.
#
# The factory takes a ``config`` dict (merged from DB-override + env
# fallback) and returns a ready-to-use provider instance. Keeping it
# lazy-instantiated lets us recompute the active provider on every
# call without paying a network/auth cost up front.
# ---------------------------------------------------------------------------
ProviderFactory = Callable[[Dict[str, Any]], WhatsAppProvider]

_PROVIDER_REGISTRY: Dict[str, ProviderFactory] = {
    "flowconnect": lambda cfg: FlowConnectProvider(
        endpoint  = cfg.get("endpoint")  or os.getenv("FLOWCONNECT_ENDPOINT", ""),
        api_token = cfg.get("api_token") or os.getenv("FLOWCONNECT_API_TOKEN", ""),
        message_template = cfg.get("message_template")
            or os.getenv("FLOWCONNECT_OTP_TEMPLATE", ""),
    ),
    # ── Add more providers here, e.g.:
    # "wati":      lambda cfg: WatiProvider(...),
    # "interakt":  lambda cfg: InteraktProvider(...),
    # "meta_cloud": lambda cfg: MetaCloudApiProvider(...),
}


async def _resolve_config(db: Any | None = None) -> Dict[str, Any]:
    """Combine the DB-side override (if any) with env defaults.

    The DB record lives under settings._id == "whatsapp_otp" and is
    intentionally optional — the integration starts working from a
    fresh deployment with ONLY env vars set. Super-admins can later
    write a settings row to point all OTPs at a different BSP
    without touching env files.
    """
    cfg: Dict[str, Any] = {
        "provider":  os.getenv("WHATSAPP_OTP_PROVIDER", "flowconnect"),
        "endpoint":  "",
        "api_token": "",
        "active":    os.getenv("WHATSAPP_OTP_ENABLED", "true").lower()
                      in {"1", "true", "yes", "on"},
        "message_template": "",
    }
    if db is not None:
        try:
            row = await db.settings.find_one({"_id": "whatsapp_otp"})
            if row and isinstance(row, dict):
                for k in ("provider", "endpoint", "api_token",
                          "message_template"):
                    if row.get(k):
                        cfg[k] = row[k]
                if "active" in row:
                    cfg["active"] = bool(row["active"])
        except Exception as e:
            _LOG.warning(
                "WhatsApp provider config: DB override read failed; "
                "falling back to env (%s)", e,
            )
    return cfg


async def get_active_provider(db: Any | None = None) -> Optional[WhatsAppProvider]:
    """Resolve the active provider instance — or ``None`` if disabled
    or no factory matches the configured slug.

    Pure async + side-effect-free; safe to call from a hot path.
    """
    cfg = await _resolve_config(db)
    if not cfg["active"]:
        _LOG.info("WhatsApp OTP delivery is globally DISABLED via config.")
        return None
    slug = (cfg.get("provider") or "").strip().lower()
    factory = _PROVIDER_REGISTRY.get(slug)
    if factory is None:
        _LOG.error(
            "WhatsApp provider slug %r is not registered. "
            "Known slugs: %s", slug, list(_PROVIDER_REGISTRY.keys()),
        )
        return None
    try:
        return factory(cfg)
    except Exception as e:
        _LOG.exception("Failed to instantiate provider %r: %s", slug, e)
        return None


__all__ = [
    "WhatsAppProvider",
    "ProviderResult",
    "FlowConnectProvider",
    "get_active_provider",
]
