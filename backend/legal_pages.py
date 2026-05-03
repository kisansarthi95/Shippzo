"""
Phase J — Legal pages (Privacy Policy + Terms) hosted directly by the
backend so we have a public, Play-Store-verifiable URL that works
immediately, without depending on the marketing domain (shippzo.com).

Routes (mounted on the FastAPI app at module import time):
    GET /api/legal/privacy    → HTML
    GET /api/legal/terms      → HTML
    GET /api/legal/refund     → HTML
"""
from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import HTMLResponse


legal_router = APIRouter(prefix="/api/legal", tags=["legal"])


_BASE_CSS = """
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px 16px 60px;
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #F7F7F9; color: #111827; line-height: 1.55;
  }
  .wrap { max-width: 760px; margin: 0 auto; background: #fff;
          border-radius: 12px; padding: 28px 22px;
          box-shadow: 0 2px 12px rgba(0,0,0,.04); }
  h1 { font-size: 24px; margin: 0 0 4px 0; color: #FF5A00; }
  .meta { color: #6B7280; font-size: 12px; margin-bottom: 22px; }
  h2 { font-size: 17px; margin: 22px 0 8px; color: #111827; }
  h3 { font-size: 14px; margin: 14px 0 4px; color: #374151; }
  p, li { font-size: 13.5px; color: #374151; }
  ul { padding-left: 22px; }
  a { color: #1F4FBF; }
  .pill { display: inline-block; background: #FFF1E6; color: #FF5A00;
          font-weight: 700; padding: 3px 10px; border-radius: 999px;
          font-size: 11px; letter-spacing: .4px; }
  hr { border: 0; border-top: 1px solid #E5E7EB; margin: 24px 0; }
  .footer { font-size: 11.5px; color: #9CA3AF; text-align: center;
            margin-top: 30px; }
</style>
"""

# Effective date is updated whenever this module changes.
EFFECTIVE = "May 3, 2026"
APP_NAME  = "Shippzo"
COMPANY   = "Shippzo"
SUPPORT   = "shippzo.support@gmail.com"


def _wrap(title: str, html_body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} · {APP_NAME}</title>
{_BASE_CSS}
</head><body>
<div class="wrap">{html_body}</div>
<div class="footer">© 2026 {COMPANY} · All rights reserved · Effective {EFFECTIVE}</div>
</body></html>
"""


# ──────────────────────────────────────────────────────────────────
# Privacy Policy
# ──────────────────────────────────────────────────────────────────
@legal_router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy() -> HTMLResponse:
    body = f"""
<span class="pill">Privacy Policy</span>
<h1>{APP_NAME} Privacy Policy</h1>
<div class="meta">Effective {EFFECTIVE} · Contact: <a href="mailto:{SUPPORT}">{SUPPORT}</a></div>

<p>This Privacy Policy explains how {COMPANY} (&ldquo;we&rdquo;, &ldquo;us&rdquo;) collects, uses, and protects your data when you use the {APP_NAME} mobile application and related services (the &ldquo;Service&rdquo;).</p>

<h2>1. Information We Collect</h2>
<h3>1.1 Account information</h3>
<ul>
  <li>Name, email, phone number provided at sign-up.</li>
  <li>Business name, sender address, GST number when added voluntarily for shipment labels.</li>
</ul>
<h3>1.2 Shipment data you create</h3>
<ul>
  <li>Customer names, addresses, phone numbers, order amounts, courier choice, status updates.</li>
  <li>Photos / barcodes you scan with your camera.</li>
</ul>
<h3>1.3 Device & usage data</h3>
<ul>
  <li>Device model, OS version, app version, crash reports.</li>
  <li>Push notification tokens issued by Apple / Google so we can send alerts you opt in to.</li>
  <li>Aggregate usage metrics (number of shipments created, WhatsApp messages sent) for billing & analytics.</li>
</ul>
<h3>1.4 How you provide shipment data</h3>
<p>You may input shipment data manually, by pasting text (such as from messaging apps), uploading files, or connecting your Google Sheet. We do not access your messaging apps directly.</p>

<h2>2. How We Use Your Data</h2>
<ul>
  <li>To create courier labels, manage shipments, and sync with your own Google Sheet.</li>
  <li>To send transactional and operational notifications you have explicitly enabled (SLA breaches, daily digest, payment receipts).</li>
  <li>To support features that automatically extract or format address and shipment details from text or images you provide.</li>
  <li>To maintain billing, enforce subscription tier limits, prevent abuse.</li>
  <li>To improve product reliability via aggregated, non-identifying analytics.</li>
</ul>

<h2>3. Sharing &amp; Third Parties</h2>
<p>We do not sell your personal data.</p>
<p>We share data only with essential service providers required to operate the app:</p>
<ul>
  <li><strong>Google services</strong> &mdash; when you choose to connect your Google Sheet for data sync.</li>
  <li><strong>Push notification services (Apple / Google)</strong> &mdash; to deliver notifications you opt in to.</li>
  <li><strong>Payment providers</strong> &mdash; only when you make payments within the app (if applicable).</li>
</ul>
<p>We share only the minimum data necessary to provide these features.</p>

<h2>4. Automated Processing</h2>
<p>Some features use automated processing to extract and format address or shipment details from text or images you provide.</p>
<p>This processing may be performed on-device or through secure services. We do not use your data to train any models.</p>

<h2>5. Data Retention &amp; Deletion</h2>
<ul>
  <li>Shipment records are retained for the lifetime of your account so you can audit history.</li>
  <li>You may delete individual shipments at any time from inside the app.</li>
  <li>You may request full account deletion by emailing <a href="mailto:{SUPPORT}">{SUPPORT}</a>; we will permanently erase your data within 30 days, retaining only aggregate billing records as required by law.</li>
</ul>

<h2>6. Permissions</h2>
<ul>
  <li><strong>Camera</strong> &mdash; to scan addresses and barcodes; photos are used only as you direct inside the app.</li>
  <li><strong>Photos</strong> &mdash; to attach images for label printing; shown as a system picker.</li>
  <li><strong>Notifications</strong> &mdash; only after you tap &ldquo;Allow&rdquo;; you can revoke in OS settings or in-app Notifications screen.</li>
</ul>

<h2>7. Children</h2>
<p>{APP_NAME} is intended for businesses and adults age 18+. We do not knowingly collect data from children.</p>

<h2>8. Security</h2>
<p>All connections are encrypted in transit. Passwords are securely hashed. Access to systems handling your data is restricted to authorised personnel.</p>

<h2>9. Your Rights</h2>
<p>You can access, correct, export, or delete your data anytime by emailing <a href="mailto:{SUPPORT}">{SUPPORT}</a>. India PDP, EU GDPR, and California CCPA rights are honoured.</p>

<h2>10. Changes to This Policy</h2>
<p>We will publish an updated effective date at the top of this page when we make material changes. Continued use after publication constitutes acceptance.</p>

<hr/>
<p>Questions? Contact <a href="mailto:{SUPPORT}">{SUPPORT}</a>.</p>
"""
    return HTMLResponse(_wrap("Privacy Policy", body))


# ──────────────────────────────────────────────────────────────────
# Terms of Service
# ──────────────────────────────────────────────────────────────────
@legal_router.get("/terms", response_class=HTMLResponse)
async def terms_of_service() -> HTMLResponse:
    body = f"""
<span class="pill">Terms of Service</span>
<h1>{APP_NAME} Terms of Service</h1>
<div class="meta">Effective {EFFECTIVE} · Contact: <a href="mailto:{SUPPORT}">{SUPPORT}</a></div>

<p>By creating an account or using {APP_NAME}, you agree to these Terms.</p>

<h2>1. Service</h2>
<p>{APP_NAME} is a courier-label management tool that lets businesses create shipping labels, send WhatsApp updates to customers, sync with Google Sheets, and receive operational analytics.</p>

<h2>2. Accounts</h2>
<ul>
  <li>You must be at least 18 years old or a registered business.</li>
  <li>You are responsible for safeguarding your login credentials.</li>
  <li>One paid subscription covers a single business; sharing across organisations is prohibited.</li>
</ul>

<h2>3. Acceptable Use</h2>
<ul>
  <li>Do not send spam, fraudulent, or harassing WhatsApp messages.</li>
  <li>Do not use the platform to ship illegal, hazardous, or restricted goods.</li>
  <li>Do not reverse-engineer the API or attempt unauthorised access to other users' data.</li>
  <li>Comply with WhatsApp&rsquo;s Business Policy when using the click-to-chat helpers.</li>
</ul>

<h2>4. Subscriptions &amp; Wallet</h2>
<ul>
  <li>Plans are billed in advance via Razorpay; auto-renewal can be cancelled anytime from the Subscription screen.</li>
  <li>Wallet credits used for AI generation are non-refundable once consumed.</li>
  <li>Refunds for unused subscription days follow the <a href="/api/legal/refund">Refund Policy</a>.</li>
</ul>

<h2>5. Third-Party Integrations</h2>
<p>{APP_NAME} integrates Google Sheets, Razorpay, Gemini AI, and Expo Push. Outages or policy changes by these providers may affect functionality. We are not liable for issues originating outside our platform.</p>

<h2>6. Intellectual Property</h2>
<p>All app content, branding, code, and AI-generated templates remain the property of {COMPANY}. Shipment data you create is yours; you grant us a limited license to process it solely to deliver the Service.</p>

<h2>7. Limitation of Liability</h2>
<p>To the fullest extent permitted by law, {COMPANY} is not liable for indirect, incidental, or consequential damages arising from your use of the Service. Our total liability for any claim is limited to the fees you have paid in the past 3 months.</p>

<h2>8. Termination</h2>
<p>You may delete your account anytime by contacting <a href="mailto:{SUPPORT}">{SUPPORT}</a>. We may suspend accounts that violate these Terms after a reasonable warning, except in cases of fraud or abuse.</p>

<h2>9. Governing Law</h2>
<p>These Terms are governed by the laws of India, with exclusive jurisdiction in Gujarat courts.</p>

<h2>10. Updates</h2>
<p>We may update these Terms; material changes will be announced inside the app. Continued use means acceptance.</p>

<hr/>
<p>Contact <a href="mailto:{SUPPORT}">{SUPPORT}</a> for questions.</p>
"""
    return HTMLResponse(_wrap("Terms of Service", body))


# ──────────────────────────────────────────────────────────────────
# Refund / Cancellation Policy
# ──────────────────────────────────────────────────────────────────
@legal_router.get("/refund", response_class=HTMLResponse)
async def refund_policy() -> HTMLResponse:
    body = f"""
<span class="pill">Refund &amp; Cancellation Policy</span>
<h1>{APP_NAME} Refund &amp; Cancellation Policy</h1>
<div class="meta">Effective {EFFECTIVE} · Contact: <a href="mailto:{SUPPORT}">{SUPPORT}</a></div>

<h2>1. Subscription Refunds</h2>
<ul>
  <li>You may cancel an active subscription at any time from the Subscription screen.</li>
  <li>If you cancel within <strong>7 days</strong> of starting a new paid plan, email <a href="mailto:{SUPPORT}">{SUPPORT}</a> for a full refund.</li>
  <li>After 7 days, the remaining unused days of the current billing period are non-refundable; the plan stays active until the period ends and does not auto-renew.</li>
</ul>

<h2>2. Wallet / AI Credits</h2>
<ul>
  <li>Wallet credits and AI generation rates already consumed are non-refundable.</li>
  <li>Unused wallet balance from a recent top-up can be refunded within <strong>14 days</strong> of the top-up date if you have not yet used credits from that top-up.</li>
</ul>

<h2>3. Failed Charges</h2>
<p>If Razorpay double-charges or the app fails to credit your wallet, contact us within 7 days with the Razorpay payment ID. We will reconcile and refund within 7 working days.</p>

<h2>4. How to Request a Refund</h2>
<ol>
  <li>Email <a href="mailto:{SUPPORT}">{SUPPORT}</a> from the address linked to your account.</li>
  <li>Include order/payment ID and a brief reason.</li>
  <li>Refund credits return to the original payment method within 5-10 business days after approval.</li>
</ol>

<hr/>
<p>Thank you for using {APP_NAME}.</p>
"""
    return HTMLResponse(_wrap("Refund Policy", body))
