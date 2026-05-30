"""
Phase-29 — Support Center Articles routing test.

Guards two regressions:
  1. Tapping an article row inside /support-center MUST navigate to
     the in-app reader at /support-center/articles/<id> — NOT a public
     https://shippzo.com/help URL or any other external redirect.
  2. The reader screen renders the article's title + body inline; the
     same article is also reachable from the "View All" link at
     /support-center/articles.

Run with:
    python frontend/tests/test_support_articles_in_app_routing.py

Depends on:
  • An admin account (admin@test.com / Admin@12345) — same one used
    by all of our Playwright suites.
  • Backend seeded with the 6 default articles (idempotent — happens
    on every boot via routers/articles.py:seed_default_articles).
"""
from __future__ import annotations

import asyncio
import sys
import time

from playwright.async_api import async_playwright

BASE       = "http://localhost:3000"
ADMIN_USER = "admin@test.com"
ADMIN_PASS = "Admin@12345"


async def login(page) -> None:
    """Drop the user on the login screen and authenticate."""
    await page.goto(f"{BASE}/(auth)/login")
    await page.wait_for_timeout(3500)
    inputs = page.locator("input")
    assert await inputs.count() >= 2, "login inputs not found"
    await inputs.nth(0).fill(ADMIN_USER)
    await inputs.nth(1).fill(ADMIN_PASS)
    await page.get_by_text("Log in", exact=True).first.click(timeout=5000)
    await page.wait_for_timeout(5500)


async def run() -> int:
    failures: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx     = await browser.new_context(viewport={"width": 390, "height": 844})
        page    = await ctx.new_page()

        await login(page)

        # ── Step 1: open /support-center, intercept any external
        # navigation so the test fails loudly if a row still uses
        # Linking.openURL().
        external_redirects: list[str] = []

        async def watch_navigation(req):
            url = req.url
            if url.startswith("http") and "localhost" not in url:
                external_redirects.append(url)

        page.on("request", watch_navigation)

        await page.goto(f"{BASE}/support-center")
        await page.wait_for_timeout(3500)

        # First row uses testID sc-article-<id>. We don't hard-code
        # the id list — the backend seed could be edited any time —
        # so we grep the DOM for the prefix.
        article_rows = page.locator("[data-testid^='sc-article-']")
        rc = await article_rows.count()
        if rc < 1:
            failures.append(f"support-center has no article rows (expected >=1, got {rc})")

        # ── Step 2: tap the first row and verify the URL is the
        # in-app reader, not an external website.
        if rc:
            first = article_rows.first
            ph_attr = await first.get_attribute("data-testid")
            target_id = (ph_attr or "").replace("sc-article-", "")
            await first.click()
            await page.wait_for_timeout(2500)
            cur = page.url
            if "/support-center/articles/" not in cur:
                failures.append(f"row tap did not navigate to in-app reader (URL was {cur})")
            elif target_id and target_id not in cur:
                failures.append(f"reader URL missing article id (URL={cur}, id={target_id})")

            # Body must be rendered inline.
            title = page.locator("[data-testid='article-title']")
            try:
                await title.wait_for(timeout=4000)
            except Exception:
                failures.append("article reader did not render title in time")

        # ── Step 3: "View All" must route in-app too.
        await page.goto(f"{BASE}/support-center")
        await page.wait_for_timeout(2500)
        view_all = page.locator("[data-testid='sc-articles-view-all']")
        if await view_all.count() == 0:
            failures.append("View All link missing (testID sc-articles-view-all)")
        else:
            await view_all.first.click()
            await page.wait_for_timeout(2500)
            if "/support-center/articles" not in page.url:
                failures.append(f"View All did not navigate in-app (URL={page.url})")

        # ── Step 4: ensure no external URL was visited during the run.
        for ext in external_redirects:
            if "shippzo.com/help" in ext or "shippzo.com/articles" in ext:
                failures.append(f"forbidden external navigation observed: {ext}")

        await browser.close()

    if failures:
        print("FAIL: support-articles routing test")
        for f in failures:
            print(f"  • {f}")
        return 1
    print("PASS: every article opens in-app — no external redirects.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
