"""
Phase-21 Video Tutorials backend test.

Tests endpoints:
- GET /api/video-tutorials, /api/video-tutorials/{id}
- GET /api/video-tutorial-categories
- POST/PATCH/DELETE /api/admin/video-tutorials
- POST/PATCH/DELETE /api/admin/video-tutorial-categories
"""

import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER  = {"email": "user2@test.com", "password": "User@12345"}

passed = 0
failed = 0
failures = []


def chk(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {label}")
    else:
        failed += 1
        failures.append(f"{label} — {extra}")
        print(f"  ❌ {label}  {extra}")


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def main():
    print("=" * 70)
    print("Phase-21 Video Tutorials Backend Test")
    print("=" * 70)

    admin_tok = login(ADMIN)
    user_tok  = login(USER)
    print(f"[+] Logged in as admin & regular user\n")

    created_tutorial_ids = []
    created_category_ids = []

    # ── 2) Categories seed + list ──────────────────────────────────
    print("Test 2: Categories seed + list (as regular user)")
    r = requests.get(f"{BASE}/video-tutorial-categories", headers=H(user_tok), timeout=30)
    chk(r.status_code == 200, "GET categories returns 200 for regular user", f"got {r.status_code}: {r.text[:200]}")
    data = r.json()
    cats = data.get("items", [])
    names = {c["name"] for c in cats}
    expected = {"Labels", "Wallet", "Excel", "WhatsApp", "Smart Fill"}
    chk(expected.issubset(names), "All 5 default categories seeded", f"got {names}")
    chk(all(c.get("is_active") is True for c in cats if c["name"] in expected), "Default cats are active")

    # ── 1) Auto YouTube ID extraction & thumbnail ──────────────────
    print("\nTest 1: Auto YouTube ID extraction & thumbnail")
    body = {
        "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Test Tutorial",
        "category": "Labels",
        "duration": "03:14",
    }
    r = requests.post(f"{BASE}/admin/video-tutorials", json=body, headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "Admin creates tutorial w/ watch?v= URL", f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        t = r.json()
        created_tutorial_ids.append(t["id"])
        chk(t.get("youtube_video_id") == "dQw4w9WgXcQ", "video_id extracted = dQw4w9WgXcQ", f"got {t.get('youtube_video_id')}")
        chk(t.get("thumbnail_url") == "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
            "thumbnail_url matches expected", f"got {t.get('thumbnail_url')}")

    # short-form youtu.be
    body2 = {
        "youtube_url": "https://youtu.be/ABC123def",
        "title": "Short-form URL Tutorial",
        "category": "Wallet",
    }
    r = requests.post(f"{BASE}/admin/video-tutorials", json=body2, headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "POST with youtu.be/<id> URL", f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        t = r.json()
        created_tutorial_ids.append(t["id"])
        chk(t.get("youtube_video_id") == "ABC123def", "video_id from youtu.be extracted", f"got {t.get('youtube_video_id')}")
        chk(t.get("thumbnail_url") == "https://img.youtube.com/vi/ABC123def/hqdefault.jpg", "thumbnail correct for short URL")

    # embed
    body3 = {
        "youtube_url": "https://www.youtube.com/embed/XYZ789abc",
        "title": "Embed URL Tutorial",
        "category": "Excel",
    }
    r = requests.post(f"{BASE}/admin/video-tutorials", json=body3, headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "POST with embed/<id> URL", f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        t = r.json()
        created_tutorial_ids.append(t["id"])
        chk(t.get("youtube_video_id") == "XYZ789abc", "video_id from embed URL extracted", f"got {t.get('youtube_video_id')}")

    # Invalid URL
    bad = {"youtube_url": "not-a-youtube-link", "title": "Bad Tutorial", "category": "Labels"}
    r = requests.post(f"{BASE}/admin/video-tutorials", json=bad, headers=H(admin_tok), timeout=30)
    # Note: "not-a-youtube-link" has 18 chars with hyphens; regex r"[A-Za-z0-9_-]{6,15}" wouldn't match (18>15); also _YT_REGEXES wouldn't match -> returns "" -> 400.
    chk(r.status_code == 400, "Invalid URL returns 400", f"got {r.status_code}: {r.text[:200]}")

    # ── 3) Admin access control ────────────────────────────────────
    print("\nTest 3: Admin access control (regular user blocked)")
    r = requests.post(f"{BASE}/admin/video-tutorials", json=body, headers=H(user_tok), timeout=30)
    chk(r.status_code == 403, "Regular user POST /admin/video-tutorials → 403", f"got {r.status_code}: {r.text[:200]}")

    if created_tutorial_ids:
        tid = created_tutorial_ids[0]
        r = requests.delete(f"{BASE}/admin/video-tutorials/{tid}", headers=H(user_tok), timeout=30)
        chk(r.status_code == 403, "Regular user DELETE /admin/video-tutorials/{id} → 403", f"got {r.status_code}: {r.text[:200]}")

    r = requests.post(f"{BASE}/admin/video-tutorial-categories",
                      json={"name": "RegUserShouldFail"}, headers=H(user_tok), timeout=30)
    chk(r.status_code == 403, "Regular user POST /admin/video-tutorial-categories → 403", f"got {r.status_code}: {r.text[:200]}")

    r = requests.delete(f"{BASE}/admin/video-tutorial-categories/fake-id", headers=H(user_tok), timeout=30)
    chk(r.status_code == 403, "Regular user DELETE /admin/video-tutorial-categories/{id} → 403", f"got {r.status_code}: {r.text[:200]}")

    r = requests.get(f"{BASE}/video-tutorials", headers=H(user_tok), timeout=30)
    chk(r.status_code == 200, "Regular user GET /video-tutorials → 200")
    r = requests.get(f"{BASE}/video-tutorial-categories", headers=H(user_tok), timeout=30)
    chk(r.status_code == 200, "Regular user GET /video-tutorial-categories → 200")

    # ── 4) Tutorial CRUD ───────────────────────────────────────────
    print("\nTest 4: Tutorial CRUD")
    r = requests.get(f"{BASE}/video-tutorials", headers=H(user_tok), timeout=30)
    items = r.json().get("items", [])
    ids_in_list = {t["id"] for t in items}
    chk(any(tid in ids_in_list for tid in created_tutorial_ids), "Created tutorials appear in list", f"ids:{ids_in_list}")

    # Filter by category=Labels
    r = requests.get(f"{BASE}/video-tutorials?category=Labels", headers=H(user_tok), timeout=30)
    chk(r.status_code == 200, "GET with category=Labels → 200")
    labels_items = r.json().get("items", [])
    chk(all(t.get("category") == "Labels" for t in labels_items), "All filtered items are Labels category", f"got cats: {[t.get('category') for t in labels_items]}")
    chk(any(t["id"] == created_tutorial_ids[0] for t in labels_items), "Our Labels-category tutorial appears in filter")

    # GET single
    r = requests.get(f"{BASE}/video-tutorials/{created_tutorial_ids[0]}", headers=H(user_tok), timeout=30)
    chk(r.status_code == 200, "GET single tutorial → 200")
    chk(r.json().get("id") == created_tutorial_ids[0], "Single tutorial id matches")

    # PATCH title
    tid = created_tutorial_ids[0]
    r = requests.patch(f"{BASE}/admin/video-tutorials/{tid}",
                       json={"title": "Updated Tutorial Title"},
                       headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "PATCH title → 200", f"got {r.status_code}: {r.text[:200]}")
    chk(r.json().get("title") == "Updated Tutorial Title", "Title updated", f"got {r.json().get('title')}")

    # PATCH youtube_url → thumbnail auto-update
    new_url = "https://www.youtube.com/watch?v=NEWVID123XY"
    r = requests.patch(f"{BASE}/admin/video-tutorials/{tid}",
                       json={"youtube_url": new_url},
                       headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "PATCH youtube_url → 200", f"got {r.status_code}: {r.text[:200]}")
    j = r.json()
    chk(j.get("youtube_video_id") == "NEWVID123XY", "video_id updated", f"got {j.get('youtube_video_id')}")
    chk(j.get("thumbnail_url") == "https://img.youtube.com/vi/NEWVID123XY/hqdefault.jpg", "thumbnail_url auto-updated", f"got {j.get('thumbnail_url')}")

    # DELETE
    r = requests.delete(f"{BASE}/admin/video-tutorials/{tid}", headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "DELETE tutorial → 200", f"got {r.status_code}: {r.text[:200]}")
    # confirm gone
    r = requests.get(f"{BASE}/video-tutorials/{tid}", headers=H(user_tok), timeout=30)
    chk(r.status_code == 404, "Deleted tutorial returns 404", f"got {r.status_code}")
    created_tutorial_ids.remove(tid)

    # ── 5) Category CRUD ───────────────────────────────────────────
    print("\nTest 5: Category CRUD")
    r = requests.post(f"{BASE}/admin/video-tutorial-categories",
                      json={"name": "Tracking", "icon": "location-outline", "display_order": 6},
                      headers=H(admin_tok), timeout=30)
    chk(r.status_code == 200, "Admin creates category Tracking → 200", f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        c = r.json()
        cid = c["id"]
        created_category_ids.append(cid)
        chk(c.get("name") == "Tracking" and c.get("icon") == "location-outline" and c.get("display_order") == 6,
            "Created category fields correct", f"got {c}")

        # List includes it
        r = requests.get(f"{BASE}/video-tutorial-categories", headers=H(user_tok), timeout=30)
        names = [c["name"] for c in r.json().get("items", [])]
        chk("Tracking" in names, "List includes new Tracking category", f"got {names}")

        # PATCH name
        r = requests.patch(f"{BASE}/admin/video-tutorial-categories/{cid}",
                           json={"name": "TrackingUpdated"}, headers=H(admin_tok), timeout=30)
        chk(r.status_code == 200, "PATCH category name → 200", f"got {r.status_code}: {r.text[:200]}")
        chk(r.json().get("name") == "TrackingUpdated", "Category name updated")

        # DELETE (soft)
        r = requests.delete(f"{BASE}/admin/video-tutorial-categories/{cid}", headers=H(admin_tok), timeout=30)
        chk(r.status_code == 200, "DELETE category → 200", f"got {r.status_code}: {r.text[:200]}")
        # list should not include it (is_active filter)
        r = requests.get(f"{BASE}/video-tutorial-categories", headers=H(user_tok), timeout=30)
        names = [c["name"] for c in r.json().get("items", [])]
        chk("TrackingUpdated" not in names, "Soft-deleted category not in list", f"got {names}")

    # ── 6) Filter All / no param ──────────────────────────────────
    print("\nTest 6: Filter 'All' or no param returns all tutorials")
    r1 = requests.get(f"{BASE}/video-tutorials", headers=H(user_tok), timeout=30)
    r2 = requests.get(f"{BASE}/video-tutorials?category=All", headers=H(user_tok), timeout=30)
    chk(r1.status_code == 200 and r2.status_code == 200, "Both endpoints 200")
    items1 = {t["id"] for t in r1.json().get("items", [])}
    items2 = {t["id"] for t in r2.json().get("items", [])}
    chk(items1 == items2, "No-param == 'All' filter (same results)", f"diff: {items1 ^ items2}")

    # ── Cleanup ────────────────────────────────────────────────────
    print("\nCleanup: delete remaining test tutorials")
    for tid in list(created_tutorial_ids):
        r = requests.delete(f"{BASE}/admin/video-tutorials/{tid}", headers=H(admin_tok), timeout=30)
        print(f"  cleanup delete tutorial {tid}: {r.status_code}")

    print("\n" + "=" * 70)
    print(f"RESULTS: passed={passed}  failed={failed}")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
