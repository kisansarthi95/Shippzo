"""
End-to-end Playwright test — Support Center FAQ card routing.

What this confirms:
  1. The FAQ card on /support-center is reachable and tappable.
  2. Tapping it lands on /support-center/faq (NOT /refund-policy).
  3. The FAQ screen renders its hero + accordion list + search box.
  4. Expanding a row reveals the answer text inline.
  5. The search box filters down the list and shows the live count.

The screen is checked via Playwright against the local Expo web build
(http://localhost:3000) — no Expo Go required.

Run:  python3 -m frontend.tests.test_support_center_faq_routing
Exit code 0 = all assertions passed.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Add frontend/ to sys.path so this script is importable as a module
# regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Missing dep — install with: pip install playwright && playwright install chromium")
    sys.exit(2)


BASE_URL  = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")
LOGIN_EMAIL = os.environ.get("TEST_EMAIL", "admin@test.com")
LOGIN_PWD   = os.environ.get("TEST_PASSWORD", "Admin@12345")


async def login(page: Any) -> None:
    await page.goto(f"{BASE_URL}/(auth)/login", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(6000)
    await page.locator('[data-testid="login-email"]').fill(LOGIN_EMAIL)
    await page.locator('[data-testid="login-password"]').fill(LOGIN_PWD)
    await page.locator('[data-testid="login-submit"]').click()
    # Login redirect → dashboard. Generous wait so the JWT lands.
    await page.wait_for_timeout(8000)


async def run() -> int:
    failures = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx     = await browser.new_context(viewport={"width": 390, "height": 844})
        page    = await ctx.new_page()
        try:
            await login(page)

            # 1. Navigate to Support Center.
            await page.goto(
                f"{BASE_URL}/support-center",
                wait_until="domcontentloaded", timeout=60000,
            )
            await page.wait_for_timeout(6000)

            faq_count = await page.locator('[data-testid="sc-faqs"]').count()
            if faq_count == 0:
                print("  ✗ FAQ card missing from /support-center")
                return 1
            print("  ✓ FAQ card visible on /support-center")

            # 2. Tap the FAQ card.
            await page.locator('[data-testid="sc-faqs"]').click()
            await page.wait_for_timeout(5000)

            # 3. Confirm the URL is the FAQ screen, NOT the legal page.
            current = page.url
            if "/support-center/faq" not in current:
                print(f"  ✗ FAQ card opened the wrong URL: {current}")
                failures += 1
            elif "refund-policy" in current or "privacy" in current:
                print(f"  ✗ FAQ card still routes to legal page: {current}")
                failures += 1
            else:
                print(f"  ✓ Landed on FAQ screen: {current}")

            # 4. Hero + search box render.
            body = await page.locator("body").inner_text()
            if "Frequently Asked Questions" not in body:
                print("  ✗ FAQ hero title missing from rendered output")
                failures += 1
            else:
                print("  ✓ FAQ hero title rendered")

            search_count = await page.locator('[data-testid="faq-search"]').count()
            if search_count == 0:
                print("  ✗ FAQ search box missing")
                failures += 1
            else:
                print("  ✓ FAQ search box rendered")

            # 5. Click the very first FAQ row to confirm the accordion
            #    actually expands an answer. The first row id from the
            #    FAQ array is `gs-signup`.
            row_count = await page.locator('[data-testid="faq-row-gs-signup"]').count()
            if row_count == 0:
                print("  ✗ First FAQ row (gs-signup) missing")
                failures += 1
            else:
                await page.locator('[data-testid="faq-row-gs-signup"]').click()
                await page.wait_for_timeout(800)
                expanded_body = await page.locator("body").inner_text()
                # Answer contains a phrase that's NOT in the question.
                if "6-digit code" in expanded_body or "WhatsApp OTP" in expanded_body:
                    print("  ✓ Accordion expanded — answer text visible")
                else:
                    print("  ✗ Accordion did not reveal the answer body")
                    failures += 1

            # 6. Search filters the list.
            await page.locator('[data-testid="faq-search"]').fill("Razorpay")
            await page.wait_for_timeout(700)
            after_body = await page.locator("body").inner_text()
            # The Razorpay answer mentions UPI; non-matching rows should
            # be hidden, so the page count tag should drop well below the
            # total (25).
            if "25 questions" in after_body and " of " in after_body:
                print("  ✗ Search did NOT filter (count still shows all 25)")
                failures += 1
            else:
                print("  ✓ Search filtered the FAQ list")

        finally:
            await ctx.close()
            await browser.close()

    return failures


def main() -> int:
    print("Running test_support_center_faq_routing …")
    failures = asyncio.run(run())
    if failures == 0:
        print("\nALL FAQ ROUTING CHECKS PASSED ✅")
        return 0
    print(f"\n  ✗ {failures} check(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
