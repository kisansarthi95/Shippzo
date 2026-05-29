"""
FlowConnect.ai provider — default WhatsApp OTP delivery channel.

API shape (from FlowConnect docs):

    curl -X POST -H "accept:application/json" -G \
      https://login.flowconnect.ai/api/automations/<automation_id>/execute \
      -d api_token=<API_TOKEN> \
      -d contact_name=John \
      -d contact_phone=+919999999999

    # `{%contact.custom_field_key%}` placeholders mean we can pass
    # arbitrary custom fields (e.g. `otp`, `event_type`) which the
    # automation template can interpolate into the WhatsApp message.

Key design notes:
  • We send the request as ``GET`` with the credentials in the query
    string — matches the ``curl -G`` behaviour from FlowConnect's
    own docs and works regardless of how the automation is
    configured on their dashboard.
  • A short connect+read timeout (default 8 s) keeps the auth path
    snappy even if FlowConnect is having a bad day. If you need
    more breathing room set ``FLOWCONNECT_TIMEOUT`` env var.
  • Phone numbers are normalised to E.164 form (``+91XXXXXXXXXX``)
    on the way out — Indian users routinely type the number without
    the country code, and FlowConnect (like every BSP) requires it.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional

import httpx

from .base import ProviderResult, WhatsAppProvider

_LOG = logging.getLogger("whatsapp_providers.flowconnect")

_DEFAULT_TIMEOUT = float(os.getenv("FLOWCONNECT_TIMEOUT", "8.0"))
_DEFAULT_COUNTRY_CODE = os.getenv("FLOWCONNECT_DEFAULT_CC", "91")


def _normalise_phone(raw: str) -> str:
    """Best-effort E.164 normalisation for Indian phone numbers.

    Accepts ``9876543210``, ``+91 98765-43210``, ``919876543210`` and
    produces ``+919876543210``. Non-Indian inputs (already starting
    with ``+``) are passed through untouched.
    """
    s = (raw or "").strip()
    if not s:
        return s
    # Already E.164 -> normalise spaces/dashes only.
    if s.startswith("+"):
        return "+" + re.sub(r"\D", "", s)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    # 12-digit "919XXXXXXXXX" already has the country code embedded.
    if len(digits) >= 11 and digits.startswith(_DEFAULT_COUNTRY_CODE):
        return "+" + digits
    # Plain 10-digit Indian mobile -> prepend default cc.
    if len(digits) == 10:
        return f"+{_DEFAULT_COUNTRY_CODE}{digits}"
    # Fallback: just prepend a plus so BSP sees an E.164-shaped string.
    return "+" + digits


class FlowConnectProvider(WhatsAppProvider):
    """FlowConnect.ai concrete provider."""

    name = "flowconnect"

    def __init__(
        self,
        endpoint: str,
        api_token: str,
        message_template: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.endpoint   = (endpoint or "").strip()
        self.api_token  = (api_token or "").strip()
        # message_template is intentionally OPTIONAL — FlowConnect
        # automations usually carry their own message in the dashboard
        # template. When provided here, we ALSO pass it as a custom
        # field so the same automation can be re-templated without
        # changing the dashboard.
        self.message_template = message_template or ""
        self.timeout    = float(timeout)

    def _build_payload(
        self,
        phone: str,
        otp: str,
        event_type: str,
        user_name: str,
        extra: Optional[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Compose the query-string payload exactly the way FlowConnect
        expects (every value coerced to ``str``).
        """
        normalised = _normalise_phone(phone)
        payload: Dict[str, str] = {
            "api_token":      self.api_token,
            "contact_phone":  normalised,
            "contact_name":   (user_name or "Shippzo User")[:80],
            # Custom fields — accessible inside the FlowConnect template
            # as `{%contact.otp%}`, `{%contact.event_type%}`, etc.
            "otp":            str(otp),
            "event_type":     event_type,
            "app_name":       "Shippzo",
        }
        if self.message_template:
            # Pre-rendered fallback message so a single FlowConnect
            # automation can route different products through one
            # template field if desired.
            payload["message"] = self.message_template.format(
                otp=otp, app_name="Shippzo", event=event_type,
            )
        if extra:
            # Caller can append arbitrary string fields (e.g.
            # `language=gu`, `template_id=xyz`).
            for k, v in extra.items():
                if v is None:
                    continue
                payload[str(k)] = str(v)
        return payload

    async def send_otp(
        self,
        phone: str,
        otp: str,
        event_type: str,
        *,
        user_name: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        if not self.endpoint or not self.api_token:
            return ProviderResult(
                success=False,
                provider=self.name,
                error="FlowConnect endpoint / api_token not configured",
                request_payload=None,
            )

        payload = self._build_payload(phone, otp, event_type, user_name, extra)
        # Don't echo the OTP into the structured logs we'll attach to
        # the ProviderResult — the dispatcher has the value separately
        # and decides how / whether to log it.
        safe_payload = {**payload, "otp": "***", "api_token": "***"}

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as cli:
                # FlowConnect docs use `-X POST -G` which is curl's way
                # of saying "POST request but send the data as query
                # parameters rather than the body". We replicate that
                # exactly: POST with an empty body + params on the URL.
                resp = await cli.post(
                    self.endpoint,
                    params=payload,
                    headers={
                        "Accept":        "application/json",
                        "Content-Type":  "application/json",
                    },
                )
            duration_ms = (time.time() - t0) * 1000.0
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text

            ok = (resp.status_code < 400) and (
                # Accept both {"status":"success",...} and plain HTTP 2xx.
                (isinstance(body, dict) and body.get("status") in ("success", "ok", "queued"))
                or (isinstance(body, str) and resp.status_code in (200, 201, 202))
                or (isinstance(body, dict) and not body.get("error"))
            )
            return ProviderResult(
                success=ok,
                provider=self.name,
                status_code=resp.status_code,
                request_payload=safe_payload,
                response_body=body,
                duration_ms=duration_ms,
                error=None if ok else f"HTTP {resp.status_code}",
            )
        except httpx.TimeoutException as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                request_payload=safe_payload,
                duration_ms=(time.time() - t0) * 1000.0,
                error=f"timeout after {self.timeout}s",
                meta={"exception": e.__class__.__name__},
            )
        except httpx.HTTPError as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                request_payload=safe_payload,
                duration_ms=(time.time() - t0) * 1000.0,
                error=str(e),
                meta={"exception": e.__class__.__name__},
            )
        except Exception as e:
            # Belt-and-braces — anything else still returns a soft
            # failure rather than propagating into the auth path.
            _LOG.exception("FlowConnect send_otp unexpected failure: %s", e)
            return ProviderResult(
                success=False,
                provider=self.name,
                request_payload=safe_payload,
                duration_ms=(time.time() - t0) * 1000.0,
                error=f"unexpected: {e.__class__.__name__}: {e}",
            )
