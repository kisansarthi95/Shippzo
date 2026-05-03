"""
Phase-13 Smart Paste Gujarati/Hindi digit-accuracy validation.

Generates PNG images at runtime with Pillow using Noto Sans Gujarati /
Devanagari fonts, base64-encodes them, and POSTs to
/api/smart-paste/photo to validate RULE X0 (digit accuracy).

Run: python /app/backend_test.py
"""

import base64
import io
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

BACKEND = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

GU_FONT = "/usr/share/fonts/truetype/noto/NotoSansGujarati-Regular.ttf"
HI_FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"
LATIN_FONT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FALLBACK = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"


def login() -> str:
    r = requests.post(
        f"{BACKEND}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=20,
    )
    r.raise_for_status()
    tok = r.json().get("token")
    if not tok:
        raise RuntimeError(f"No token in login response: {r.text}")
    return tok


def pick_font(text: str, size: int) -> ImageFont.FreeTypeFont:
    has_gu = any(0x0A80 <= ord(c) <= 0x0AFF for c in text)
    has_hi = any(0x0900 <= ord(c) <= 0x097F for c in text)
    if has_gu and os.path.exists(GU_FONT):
        return ImageFont.truetype(GU_FONT, size)
    if has_hi and os.path.exists(HI_FONT):
        return ImageFont.truetype(HI_FONT, size)
    if os.path.exists(LATIN_FONT):
        return ImageFont.truetype(LATIN_FONT, size)
    return ImageFont.truetype(FALLBACK, size)


def render_image(lines: List[str], size=(900, 260), font_size: int = 44) -> bytes:
    W, H = size
    # auto-grow height for many lines
    needed_h = 30 + len(lines) * (font_size + 24) + 30
    if needed_h > H:
        H = needed_h
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for ln in lines:
        f = pick_font(ln, font_size)
        draw.text((30, y), ln, fill="black", font=f)
        try:
            bbox = draw.textbbox((30, y), ln, font=f)
            h = bbox[3] - bbox[1]
        except Exception:
            h = font_size + 6
        y += h + 18
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def call_smart_paste_photo(token: str, png_bytes: bytes) -> Dict[str, Any]:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    r = requests.post(
        f"{BACKEND}/smart-paste/photo",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"image_base64": b64, "mime": "image/png"},
        timeout=180,
    )
    if r.status_code != 200:
        return {"_error": True, "_status": r.status_code, "_body": r.text}
    return r.json()


def get_phones(resp: Dict[str, Any]) -> Tuple[str, str]:
    f = resp.get("fields") or {}
    phone = (f.get("customer_phone") or f.get("PHONE") or "").strip()
    alt = (f.get("customer_alt_phone") or f.get("ALT_PHONE") or "").strip()
    return phone, alt


def equal_alt_blank(actual: str) -> bool:
    return actual in ("", "-", None)


def run_test(test_id, lines, expected_phone, expected_alt, token,
             save_dir="/tmp"):
    print(f"\n========== {test_id} ==========")
    print("Lines rendered:")
    for ln in lines:
        print(f"  {ln}")
    png = render_image(lines)
    img_path = os.path.join(save_dir, f"phase13_{test_id}.png")
    with open(img_path, "wb") as fh:
        fh.write(png)
    print(f"Image written → {img_path} ({len(png)} bytes)")

    resp = call_smart_paste_photo(token, png)
    if resp.get("_error"):
        print(f"  HTTP {resp['_status']}: {resp['_body'][:600]}")
        return False, resp

    phone, alt = get_phones(resp)
    print(f"  expected PHONE = {expected_phone!r}")
    print(f"  returned PHONE = {phone!r}")
    print(f"  expected ALT   = {expected_alt!r}")
    print(f"  returned ALT   = {alt!r}")

    phone_ok = (phone == expected_phone)
    if expected_alt in (None, "", "-"):
        alt_ok = equal_alt_blank(alt)
    else:
        alt_ok = (alt == expected_alt)

    ok = phone_ok and alt_ok
    if not ok:
        print("  MISMATCH — dumping diagnostics:")
        print("     fields:", json.dumps(resp.get("fields"), ensure_ascii=False))
        print("     reason:", resp.get("reason"))
        print("     warnings:", resp.get("warnings"))
        msg = resp.get("ai_message", "")
        if msg:
            print("     ai_message (first 400):", msg[:400])
    else:
        print("  ✓ MATCH")
    return ok, resp


def main() -> int:
    if not os.path.exists(GU_FONT):
        print(f"WARN: Gujarati font missing at {GU_FONT}")
    if not os.path.exists(HI_FONT):
        print(f"WARN: Devanagari font missing at {HI_FONT}")

    print("Logging in as admin…")
    token = login()
    print(f"  token len={len(token)}")

    results = []

    t1_ok, t1_resp = run_test(
        "T1",
        ["ભરતભાઈ ૯૪૨૮૪૪૬૧૮૪", "મયુરભાઈ ૯૩૭૨૫૨૮૮૭૮"],
        expected_phone="9428446184",
        expected_alt="9372528878",
        token=token,
    )
    results.append(("T1", t1_ok, t1_resp))

    t2_ok, t2_resp = run_test(
        "T2",
        ["Ramesh  ९८२४४४६१८४"],
        expected_phone="9824446184",
        expected_alt="-",
        token=token,
    )
    results.append(("T2", t2_ok, t2_resp))

    t3_ok, t3_resp = run_test(
        "T3",
        ["Call ૯૩૭૨૫૨૮૮૭૮ OR 9824446184"],
        expected_phone="9372528878",
        expected_alt="9824446184",
        token=token,
    )
    results.append(("T3", t3_ok, t3_resp))

    t4_ok, t4_resp = run_test(
        "T4",
        ["Call 9876543210"],
        expected_phone="9876543210",
        expected_alt="-",
        token=token,
    )
    results.append(("T4", t4_ok, t4_resp))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for tid, ok, _ in results:
        print(f"  {tid}: {'PASS' if ok else 'FAIL'}")
    all_ok = all(ok for _, ok, _ in results)
    print(f"\nOVERALL: {'ALL PASS' if all_ok else 'SOME FAILED'}")

    dump_path = "/tmp/phase13_responses.json"
    with open(dump_path, "w", encoding="utf-8") as fh:
        json.dump(
            [{"id": tid, "ok": ok, "response": resp}
             for tid, ok, resp in results],
            fh, ensure_ascii=False, indent=2,
        )
    print(f"Full responses dumped → {dump_path}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
