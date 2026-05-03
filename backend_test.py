"""
Phase-14 Smart Paste REAL-WORLD Indic-digit phone accuracy validation.

- T1: real Gujarati visiting card (https URL)
- T2: synthetic PNG, two Gujarati numbers
- T3: synthetic PNG, one Hindi number
- T4: synthetic PNG, mixed Gujarati + Arabic
- T5: synthetic PNG, plain English
"""
import base64
import io
import json
import sys

import requests
from PIL import Image, ImageDraw, ImageFont

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"
TIMEOUT = 120  # server side can take up to ~45s

IMG_URL = "https://customer-assets.emergentagent.com/job_logistics-hub-740/artifacts/5shlr2fd_1000113281.jpg"

GUJ_FONT = "/usr/share/fonts/truetype/noto/NotoSansGujarati-Regular.ttf"
DEV_FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"
LATIN_FONT = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"


def login() -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=60,
    )
    r.raise_for_status()
    token = r.json()["token"]
    print(f"[auth] logged in as {EMAIL}")
    return token


def b64_from_url(url: str) -> str:
    print(f"[fetch] {url}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    print(f"[fetch] {len(r.content)} bytes")
    return base64.b64encode(r.content).decode("ascii")


def make_text_png(lines, *, size: int = 56) -> str:
    W, H = 1400, 150 + (size + 40) * len(lines)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    y = 60
    for ln in lines:
        has_guj = any("\u0A80" <= ch <= "\u0AFF" for ch in ln)
        has_dev = any("\u0900" <= ch <= "\u097F" for ch in ln)
        font_path = GUJ_FONT if has_guj else (DEV_FONT if has_dev else LATIN_FONT)
        fnt = ImageFont.truetype(font_path, size)
        d.text((80, y), ln, fill="black", font=fnt)
        y += size + 40
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def call_photo(token: str, b64: str, mime: str = "image/png") -> dict:
    import time
    last_err = None
    for attempt in range(4):
        try:
            r = requests.post(
                f"{BASE}/smart-paste/photo",
                headers={"Authorization": f"Bearer {token}"},
                json={"image_base64": b64, "mime": mime},
                timeout=TIMEOUT,
            )
            if r.status_code == 502:
                last_err = f"HTTP 502 (attempt {attempt+1}): {r.text[:400]}"
                print(f"[retry] {last_err}")
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code != 200:
                print(f"[HTTP {r.status_code}] {r.text[:800]}")
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            last_err = str(e)
            print(f"[retry] attempt {attempt+1}: {last_err}")
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"call_photo failed after retries: {last_err}")


def get_phones(resp: dict):
    f = resp.get("fields", {}) or {}
    phone = f.get("customer_phone") or f.get("PHONE") or ""
    alt = f.get("customer_alt_phone") or f.get("ALT_PHONE") or ""
    return (str(phone or "").strip(), str(alt or "").strip())


def dump_resp(label: str, resp: dict) -> None:
    safe = dict(resp)
    if "raw" in safe and isinstance(safe["raw"], str) and len(safe["raw"]) > 900:
        safe["raw"] = safe["raw"][:900] + "...(truncated)"
    print(f"[{label}] resp:")
    print(json.dumps(safe, indent=2, ensure_ascii=False)[:4000])


def run() -> int:
    token = login()
    fails = []

    # T1 — real visiting card
    try:
        b64 = b64_from_url(IMG_URL)
        resp = call_photo(token, b64, mime="image/jpeg")
        p, ap = get_phones(resp)
        print(f"[T1] PHONE={p!r}  ALT_PHONE={ap!r}")
        expected = {"9824475100", "9712544747"}
        got = {p, ap}
        if got != expected:
            dump_resp("T1", resp)
            fails.append(f"T1 REAL CARD: expected {expected} got {got}")
        else:
            if p == "9824475100" and ap == "9712544747":
                print("[T1] PASS (exact slots)")
            else:
                print(f"[T1] PASS (slots swapped) PHONE={p} ALT={ap}")
    except Exception as e:
        fails.append(f"T1 EXC: {e}")
        print(f"[T1] EXC: {e}")

    # T2 — two Gujarati numbers
    try:
        b64 = make_text_png([
            "ભરતભાઈ ૯૪૨૮૪૪૬૧૮૪",
            "મયુરભાઈ ૯૩૭૨૫૨૮૮૭૮",
        ])
        resp = call_photo(token, b64)
        p, ap = get_phones(resp)
        print(f"[T2] PHONE={p!r}  ALT_PHONE={ap!r}")
        if p != "9428446184" or ap != "9372528878":
            dump_resp("T2", resp)
            fails.append(f"T2: expected 9428446184/9372528878 got {p}/{ap}")
        else:
            print("[T2] PASS")
    except Exception as e:
        fails.append(f"T2 EXC: {e}")
        print(f"[T2] EXC: {e}")

    # T3 — Hindi single number
    try:
        b64 = make_text_png([
            "Ramesh ९८२४४४६१८४",
        ])
        resp = call_photo(token, b64)
        p, ap = get_phones(resp)
        print(f"[T3] PHONE={p!r}  ALT_PHONE={ap!r}")
        if p != "9824446184":
            dump_resp("T3", resp)
            fails.append(f"T3: expected PHONE=9824446184 got {p}")
        elif ap not in ("", "-", None):
            dump_resp("T3", resp)
            fails.append(f"T3: expected empty ALT_PHONE got {ap!r}")
        else:
            print("[T3] PASS")
    except Exception as e:
        fails.append(f"T3 EXC: {e}")
        print(f"[T3] EXC: {e}")

    # T4 — mixed Gujarati + Arabic
    try:
        b64 = make_text_png([
            "Call ૯૩૭૨૫૨૮૮૭૮ OR 9824446184",
        ])
        resp = call_photo(token, b64)
        p, ap = get_phones(resp)
        print(f"[T4] PHONE={p!r}  ALT_PHONE={ap!r}")
        if p != "9372528878" or ap != "9824446184":
            dump_resp("T4", resp)
            fails.append(f"T4: expected 9372528878/9824446184 got {p}/{ap}")
        else:
            print("[T4] PASS")
    except Exception as e:
        fails.append(f"T4 EXC: {e}")
        print(f"[T4] EXC: {e}")

    # T5 — plain English
    try:
        b64 = make_text_png([
            "Call 9876543210",
        ])
        resp = call_photo(token, b64)
        p, ap = get_phones(resp)
        print(f"[T5] PHONE={p!r}  ALT_PHONE={ap!r}")
        if p != "9876543210":
            dump_resp("T5", resp)
            fails.append(f"T5: expected PHONE=9876543210 got {p}")
        else:
            print("[T5] PASS")
    except Exception as e:
        fails.append(f"T5 EXC: {e}")
        print(f"[T5] EXC: {e}")

    print("\n============ SUMMARY ============")
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
