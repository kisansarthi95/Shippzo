"""
Phase-22 (2026-05-16) — Resend email notification service.

This module sends transactional ALERT emails (not actual replies)
when support-ticket events happen. Real conversations remain in-app —
emails simply nudge the recipient to open the app.

Design contract:
  * Fire-and-forget — caller never blocks waiting for delivery.
  * Best-effort — swallows all exceptions; missing credentials are
    silently treated as a no-op so dev installs don't crash.
  * Two flavours:
      `send_new_ticket_admin_alert()`  → admin@shippzo notify
      `send_reply_user_alert()`        → ticket creator notify
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# Lazy-imported so a missing `resend` package doesn't break the
# whole backend boot (defensive — the package is in requirements.txt
# now, but staying gentle keeps the rest of the system honest).
try:
    import resend  # type: ignore

    _RESEND_OK = True
except Exception as e:                                          # pragma: no cover
    log.warning("[email] resend package unavailable: %s", e)
    _RESEND_OK = False


def _api_key() -> Optional[str]:
    k = (os.getenv("RESEND_API_KEY") or "").strip()
    return k or None


def _sender() -> str:
    return (os.getenv("RESEND_FROM") or "Shippzo Support <onboarding@resend.dev>").strip()


def _admin_email() -> Optional[str]:
    a = (os.getenv("ADMIN_NOTIFY_EMAIL") or "").strip()
    return a or None


def _app_url() -> str:
    return (os.getenv("APP_PUBLIC_URL") or "https://app.shippzo.com").rstrip("/")


# ── Internal send helper ─────────────────────────────────────────
def _send(to: str, subject: str, html: str) -> bool:
    """
    Best-effort Resend send. Returns True if the API call succeeded,
    False otherwise. Never raises — caller can ignore the return.
    """
    if not _RESEND_OK:
        log.info("[email] skipping send to=%s — resend SDK missing", to)
        return False
    key = _api_key()
    if not key:
        log.info("[email] skipping send to=%s — RESEND_API_KEY not set", to)
        return False
    if not to or "@" not in to:
        log.info("[email] skipping send — invalid recipient %r", to)
        return False
    try:
        resend.api_key = key
        params = {
            "from":    _sender(),
            "to":      [to],
            "subject": subject,
            "html":    html,
        }
        resp = resend.Emails.send(params)
        log.info("[email] sent to=%s subject=%r resend_id=%s", to, subject, (resp or {}).get("id"))
        return True
    except Exception as e:                                      # noqa: BLE001
        log.warning("[email] send FAILED to=%s subject=%r err=%s", to, subject, e)
        return False


# ── HTML template helper ────────────────────────────────────────
def _wrap(body_html: str, *, title: str, cta_label: str, cta_url: str) -> str:
    """
    Minimal email template — branded header, body, CTA button, footer.
    Inline styles only (Gmail/Outlook strip <style>). Mobile-safe via
    max-width and 100% widths.
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F4F5F7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0F172A;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F4F5F7;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #E5E7EB;">
        <tr><td style="padding:18px 22px;background:#FB923C;color:#fff;">
          <div style="font-size:18px;font-weight:800;letter-spacing:0.2px;">📦 Shippzo Support</div>
        </td></tr>
        <tr><td style="padding:22px 22px 8px 22px;">
          <div style="font-size:18px;font-weight:800;color:#0F172A;margin-bottom:10px;">{title}</div>
          {body_html}
        </td></tr>
        <tr><td style="padding:18px 22px 22px 22px;">
          <a href="{cta_url}" target="_blank" rel="noopener"
             style="display:inline-block;background:#FB923C;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:800;font-size:14px;">
            {cta_label}
          </a>
        </td></tr>
        <tr><td style="padding:12px 22px 22px 22px;border-top:1px solid #F1F5F9;">
          <div style="font-size:11.5px;color:#94A3B8;line-height:18px;">
            This is an automated alert from the Shippzo Support Center.
            Replies to this email are not monitored — please respond inside the app.
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _esc(s: str) -> str:
    """Tiny HTML escape — enough for plain user input in templates."""
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


# ── Public API ──────────────────────────────────────────────────
def send_new_ticket_admin_alert(
    *,
    ticket_number: str,
    title: str,
    description: str,
    category: str,
    user_email: str,
) -> bool:
    """
    Notify the support owner that a new ticket was created.
    Recipient = ADMIN_NOTIFY_EMAIL.
    """
    admin = _admin_email()
    if not admin:
        log.info("[email] no ADMIN_NOTIFY_EMAIL configured — skipping new-ticket alert")
        return False
    cta = f"{_app_url()}/admin/support-inbox"
    preview = (description or "").strip()
    if len(preview) > 600:
        preview = preview[:597] + "…"
    body = f"""
      <div style="font-size:14px;color:#475569;line-height:21px;">
        A new support request has been submitted by a customer.
      </div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="margin-top:14px;background:#F8FAFC;border-radius:10px;border:1px solid #E5E7EB;">
        <tr><td style="padding:14px 16px;">
          <div style="font-size:11.5px;color:#94A3B8;font-weight:700;letter-spacing:0.4px;text-transform:uppercase;">
            {_esc(ticket_number)} · {_esc((category or '').replace('_', ' ').title())}
          </div>
          <div style="font-size:15px;font-weight:800;color:#0F172A;margin-top:4px;">
            {_esc(title)}
          </div>
          <div style="font-size:12.5px;color:#64748B;margin-top:6px;">
            from <b>{_esc(user_email or 'unknown user')}</b>
          </div>
          <div style="font-size:13.5px;color:#0F172A;line-height:20px;margin-top:12px;white-space:pre-wrap;">
            {_esc(preview)}
          </div>
        </td></tr>
      </table>
    """
    return _send(
        to=admin,
        subject=f"📩 New support request — {ticket_number}: {title}"[:140],
        html=_wrap(
            body,
            title="📩 New Support Request",
            cta_label="Open in Admin Inbox",
            cta_url=cta,
        ),
    )


def send_reply_user_alert(
    *,
    ticket_number: str,
    ticket_id: str,
    title: str,
    reply_preview: str,
    to_email: str,
) -> bool:
    """
    Notify the ticket creator that the admin has replied.
    Recipient = `to_email` (ticket's user_email).
    """
    if not to_email:
        return False
    cta = f"{_app_url()}/support-center/ticket/{ticket_id}"
    preview = (reply_preview or "").strip()
    if len(preview) > 600:
        preview = preview[:597] + "…"
    body = f"""
      <div style="font-size:14px;color:#475569;line-height:21px;">
        Our support team has replied to your request. Tap below to view the
        full message and continue the conversation.
      </div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="margin-top:14px;background:#F8FAFC;border-radius:10px;border:1px solid #E5E7EB;">
        <tr><td style="padding:14px 16px;">
          <div style="font-size:11.5px;color:#94A3B8;font-weight:700;letter-spacing:0.4px;text-transform:uppercase;">
            {_esc(ticket_number)}
          </div>
          <div style="font-size:15px;font-weight:800;color:#0F172A;margin-top:4px;">
            {_esc(title)}
          </div>
          <div style="font-size:13.5px;color:#0F172A;line-height:20px;margin-top:12px;white-space:pre-wrap;">
            {_esc(preview)}
          </div>
        </td></tr>
      </table>
    """
    return _send(
        to=to_email,
        subject=f"🛡️ Shippzo Support replied — {ticket_number}"[:140],
        html=_wrap(
            body,
            title="🛡️ Support replied to your request",
            cta_label="Open Conversation",
            cta_url=cta,
        ),
    )
