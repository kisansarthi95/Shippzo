"""
Abstract base class + result dataclass for every WhatsApp provider.

Keeping this tiny on purpose: every concrete provider must implement
ONE coroutine — ``send_otp()`` — and return a ``ProviderResult`` so
the dispatcher (services/otp_whatsapp.py) can log it identically
irrespective of which vendor was used.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProviderResult:
    """Uniform shape returned by every provider's ``send_otp()``.

    Callers should treat ``success=False`` as a soft failure — the
    OTP delivery couldn't be confirmed, but the authentication flow
    keeps moving (user can still validate via the existing OTP code
    they already received via the legacy channel, or get a retry).
    """
    success:       bool
    provider:      str
    status_code:   Optional[int] = None
    request_payload:  Optional[Dict[str, Any]] = None
    response_body: Any           = None
    error:         Optional[str] = None
    duration_ms:   float         = 0.0
    # Free-form metadata the dispatcher logs alongside the result.
    meta:          Dict[str, Any] = field(default_factory=dict)

    def to_log_dict(self) -> Dict[str, Any]:
        """Compact representation suitable for structured logging.
        Phone & OTP are intentionally NOT included here — they're
        attached by the dispatcher (which already has those values
        in scope) so the provider implementation can't accidentally
        leak them."""
        return {
            "provider":     self.provider,
            "success":      self.success,
            "status_code":  self.status_code,
            "duration_ms":  round(self.duration_ms, 1),
            "error":        self.error,
            # Truncate response body when very large so the audit
            # collection doesn't blow up — full body still passes
            # through structured logs.
            "response":     _truncate(self.response_body, 2000),
            "meta":         self.meta,
        }


def _truncate(val: Any, limit: int) -> Any:
    if val is None:
        return None
    if isinstance(val, str):
        return val if len(val) <= limit else val[:limit] + "…"
    if isinstance(val, (dict, list)):
        try:
            import json
            s = json.dumps(val, ensure_ascii=False)
            return val if len(s) <= limit else json.loads(s[:limit])  # best-effort
        except Exception:
            pass
    return val


class WhatsAppProvider(ABC):
    """Every concrete provider subclasses this and supplies a name."""

    #: Stable slug used in registry + logs. Override per subclass.
    name: str = "abstract"

    @abstractmethod
    async def send_otp(
        self,
        phone: str,
        otp: str,
        event_type: str,
        *,
        user_name: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        """Push the OTP to the user's WhatsApp via this provider.

        Implementations MUST NOT raise — every error path returns a
        ``ProviderResult(success=False, error=...)`` instead, so the
        dispatcher's contract ("never block auth") is preserved.

        ``event_type`` is a short stable string ("login", "signup",
        "phone_verification", "auth") used purely for logging and
        per-event-type templating where the provider supports it.

        ``extra`` lets callers pass provider-specific overrides
        without bloating the public signature — most providers
        will ignore unknown keys.
        """
