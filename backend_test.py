"""
Phase-5c Anchor Pricing & Countdown Timer — backend test suite.

Run:
    cd /app && python backend_test.py

Targets the live preview backend:
    https://logistics-hub-740.preview.emergentagent.com/api
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Tuple

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"


# --------------------------------------------------------------------- helpers

class Tally:
    def __init__(self) -> None:
        self.passed: List[str] = []
        self.failed: List[Tuple[str, str]] = []

    def ok(self, label: str) -> None:
        self.passed.append(label)
        print(f"  PASS  {label}")

    def bad(self, label: str, why: str) -> None:
        self.failed.append((label, why))
        print(f"  FAIL  {label}\n        -> {why}")

    def assert_true(self, cond: bool, label: str, why: str = "") -> None:
        if cond:
            self.ok(label)
        else:
            self.bad(label, why or "condition false")

    def assert_eq(self, got: Any, want: Any, label: str) -> None:
        if got == want:
            self.ok(label)
        else:
            self.bad(label, f"got={got!r} want={want!r}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get(path: str, token: str | None = None) -> requests.Response:
    h = headers(token) if token else {}
    return requests.get(f"{BASE}{path}", headers=h, timeout=30)


def put(path: str, token: str, body: Dict[str, Any]) -> requests.Response:
    return requests.put(f"{BASE}{path}", headers=headers(token), json=body, timeout=30)


# --------------------------------------------------------------------- main

def main() -> int:
    t = Tally()

    # ------------------------------------------------ Section A — Schema baseline
    print("\n=== A) Schema baseline (admin) ===")
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    t.assert_true(bool(admin_token), "A1 admin login returns token")

    r = get("/admin/global-config", admin_token)
    t.assert_eq(r.status_code, 200, "A2 GET /admin/global-config = 200")
    cfg = r.json() if r.status_code == 200 else {}
    expected_keys = {"global_ai_rates", "credit_packages", "plan_pricing", "countdown"}
    t.assert_true(
        expected_keys.issubset(set(cfg.keys())),
        f"A2 body has 4 top-level keys ({sorted(expected_keys)})",
        f"missing={expected_keys - set(cfg.keys())}",
    )

    pp = cfg.get("plan_pricing", {})
    t.assert_true(
        all(k in pp for k in ("free_trial", "silver", "gold", "platinum")),
        "A3 plan_pricing has free_trial+silver+gold+platinum",
        f"keys={sorted(pp.keys())}",
    )
    silver = pp.get("silver", {})
    silver_req_fields = {
        "monthly_price", "monthly_anchor", "yearly_price", "yearly_anchor",
        "yearly_base_months", "yearly_bonus_months", "show_strikethrough",
    }
    t.assert_true(
        silver_req_fields.issubset(silver.keys()),
        "A3 silver has required schema fields",
        f"missing={silver_req_fields - set(silver.keys())}",
    )
    free = pp.get("free_trial", {})
    t.assert_true(
        free.get("monthly_price") == 0
        and free.get("monthly_anchor") == 0
        and free.get("yearly_price") == 0
        and free.get("yearly_anchor") == 0,
        "A3 free_trial all zeros",
        f"free={free}",
    )

    cd = cfg.get("countdown", {})
    cd_req = {"enabled", "mode", "countdown_minutes", "global_expires_at", "headline"}
    t.assert_true(
        cd_req.issubset(cd.keys()),
        "A4 countdown has required keys",
        f"missing={cd_req - set(cd.keys())}",
    )
    t.assert_true(
        cd.get("mode") in ("off", "per_device", "global"),
        "A4 countdown.mode is a valid enum",
        f"mode={cd.get('mode')!r}",
    )
    t.assert_true(
        isinstance(cd.get("countdown_minutes"), int) and cd.get("countdown_minutes") >= 1,
        "A4 countdown.countdown_minutes >= 1",
        f"got={cd.get('countdown_minutes')!r}",
    )

    # ------------------------------------------------ Section B — Public readability
    print("\n=== B) Public readability (user2) ===")
    user_token = login(USER_EMAIL, USER_PASS)
    t.assert_true(bool(user_token), "B user2 login returns token")

    r = get("/plans-pricing", user_token)
    t.assert_eq(r.status_code, 200, "B5 user2 GET /plans-pricing = 200")
    body = r.json() if r.status_code == 200 else {}
    t.assert_true(
        "plan_pricing" in body and "countdown" in body,
        "B5 /plans-pricing returns plan_pricing+countdown",
        f"keys={list(body.keys())}",
    )

    r = get("/admin/global-config", user_token)
    t.assert_eq(r.status_code, 403, "B6 user2 GET /admin/global-config = 403")

    r = put("/admin/global-config", user_token, {"plan_pricing": {}})
    t.assert_eq(r.status_code, 403, "B7 user2 PUT /admin/global-config = 403")

    # ------------------------------------------------ Section C — Validation/persistence
    print("\n=== C) Validation & persistence (admin) ===")

    cfg_pre = (get("/admin/global-config", admin_token).json())
    pkg_pre = cfg_pre.get("credit_packages")
    rates_pre = cfg_pre.get("global_ai_rates")

    body8 = {
        "plan_pricing": {
            "free_trial": {
                "monthly_price": 0, "monthly_anchor": 0,
                "yearly_price": 0,  "yearly_anchor": 0,
                "yearly_base_months": 12, "yearly_bonus_months": 0,
                "show_strikethrough": False,
            },
            "silver": {
                "monthly_price": 249, "monthly_anchor": 599,
                "yearly_price": 2241, "yearly_anchor": 5999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
            "gold": {
                "monthly_price": 499, "monthly_anchor": 999,
                "yearly_price": 4491, "yearly_anchor": 9999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
            "platinum": {
                "monthly_price": 999, "monthly_anchor": 1999,
                "yearly_price": 8991, "yearly_anchor": 19999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
        },
    }
    r = put("/admin/global-config", admin_token, body8)
    t.assert_eq(r.status_code, 200, "C8 PUT plan_pricing only = 200")
    body = r.json() if r.status_code == 200 else {}
    t.assert_eq(
        body.get("plan_pricing", {}).get("silver", {}).get("monthly_price"),
        249,
        "C8 silver.monthly_price=249 in PUT response",
    )
    t.assert_eq(
        body.get("credit_packages"),
        pkg_pre,
        "C8 credit_packages UNCHANGED",
    )
    t.assert_eq(
        body.get("global_ai_rates"),
        rates_pre,
        "C8 global_ai_rates UNCHANGED",
    )

    body9 = {
        "countdown": {
            "enabled": True,
            "mode": "global",
            "countdown_minutes": 60,
            "global_expires_at": "2027-01-01T00:00:00+05:30",
            "headline": "Mega sale ends Jan 1st",
        },
    }
    r = put("/admin/global-config", admin_token, body9)
    t.assert_eq(r.status_code, 200, "C9 PUT countdown only = 200")
    body = r.json() if r.status_code == 200 else {}
    t.assert_eq(
        body.get("countdown", {}).get("mode"), "global",
        "C9 countdown.mode = global",
    )
    t.assert_eq(
        body.get("countdown", {}).get("global_expires_at"),
        "2027-01-01T00:00:00+05:30",
        "C9 countdown.global_expires_at persisted verbatim",
    )
    t.assert_eq(
        body.get("countdown", {}).get("headline"),
        "Mega sale ends Jan 1st",
        "C9 countdown.headline persisted",
    )
    t.assert_eq(
        body.get("plan_pricing", {}).get("silver", {}).get("monthly_price"),
        249,
        "C9 plan_pricing from step 8 preserved",
    )

    body10 = {"countdown": {"mode": "INVALID_MODE", "countdown_minutes": -100}}
    r = put("/admin/global-config", admin_token, body10)
    t.assert_eq(r.status_code, 200, "C10 PUT countdown invalid sanitised = 200")
    body = r.json() if r.status_code == 200 else {}
    t.assert_eq(
        body.get("countdown", {}).get("mode"),
        "per_device",
        "C10 invalid mode -> sanitised to per_device",
    )
    t.assert_true(
        isinstance(body.get("countdown", {}).get("countdown_minutes"), int)
        and body["countdown"]["countdown_minutes"] >= 1,
        "C10 negative countdown_minutes -> sanitised to >= 1",
        f"got={body.get('countdown', {}).get('countdown_minutes')!r}",
    )

    body11 = {
        "plan_pricing": {
            "free_trial": {
                "monthly_price": 0, "monthly_anchor": 0,
                "yearly_price": 0,  "yearly_anchor": 0,
                "yearly_base_months": 12, "yearly_bonus_months": 0,
                "show_strikethrough": False,
            },
            "silver": {"monthly_price": -50},
            "gold": {
                "monthly_price": 499, "monthly_anchor": 999,
                "yearly_price": 4491, "yearly_anchor": 9999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
            "platinum": {
                "monthly_price": 999, "monthly_anchor": 1999,
                "yearly_price": 8991, "yearly_anchor": 19999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
        }
    }
    r = put("/admin/global-config", admin_token, body11)
    t.assert_eq(r.status_code, 200, "C11 PUT negative silver.monthly_price = 200 (no crash)")
    body = r.json() if r.status_code == 200 else {}
    silver_after = body.get("plan_pricing", {}).get("silver", {})
    t.assert_eq(silver_after.get("monthly_price"), 0, "C11 silver.monthly_price clamped to 0")

    body12 = {
        "plan_pricing": {
            "free_trial": {
                "monthly_price": 0, "monthly_anchor": 0,
                "yearly_price": 0,  "yearly_anchor": 0,
                "yearly_base_months": 12, "yearly_bonus_months": 0,
                "show_strikethrough": False,
            },
            "silver": {
                "monthly_price": 199, "monthly_anchor": 499,
                "yearly_price": 1791, "yearly_anchor": 4999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
            "gold": {
                "monthly_price": 499, "monthly_anchor": 999,
                "yearly_price": 4491, "yearly_anchor": 9999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
            "platinum": {
                "monthly_price": 999, "monthly_anchor": 1999,
                "yearly_price": 8991, "yearly_anchor": 19999,
                "yearly_base_months": 12, "yearly_bonus_months": 1,
                "show_strikethrough": True,
            },
        },
        "countdown": {
            "enabled": True,
            "mode": "per_device",
            "countdown_minutes": 60,
            "global_expires_at": None,
            "headline": "Limited time offer — save up to 60%",
        },
    }
    r = put("/admin/global-config", admin_token, body12)
    t.assert_eq(r.status_code, 200, "C12 final reset PUT = 200")

    r = get("/admin/global-config", admin_token)
    t.assert_eq(r.status_code, 200, "C12 verify GET = 200")
    body = r.json() if r.status_code == 200 else {}
    pp = body.get("plan_pricing", {})

    expected_pp = body12["plan_pricing"]
    for tier in ("free_trial", "silver", "gold", "platinum"):
        for key, want in expected_pp[tier].items():
            t.assert_eq(
                pp.get(tier, {}).get(key),
                want,
                f"C12 plan_pricing.{tier}.{key} == {want!r}",
            )

    cd = body.get("countdown", {})
    t.assert_eq(cd.get("enabled"), True, "C12 countdown.enabled=true")
    t.assert_eq(cd.get("mode"), "per_device", "C12 countdown.mode=per_device")
    t.assert_eq(cd.get("countdown_minutes"), 60, "C12 countdown.countdown_minutes=60")
    t.assert_eq(cd.get("global_expires_at"), None, "C12 countdown.global_expires_at=null")
    t.assert_eq(
        cd.get("headline"),
        "Limited time offer — save up to 60%",
        "C12 countdown.headline restored",
    )
    t.assert_true(
        str(cd.get("headline", "")).startswith("Limited time offer"),
        "A4/C12 countdown.headline starts with 'Limited time offer'",
        f"headline={cd.get('headline')!r}",
    )

    # ------------------------------------------------ Section D — Regression
    print("\n=== D) Regression ===")

    r = get("/shipments/stats", admin_token)
    t.assert_eq(r.status_code, 200, "D13 admin GET /shipments/stats = 200")

    r = get("/couriers", admin_token)
    t.assert_eq(r.status_code, 200, "D13 admin GET /couriers = 200")

    r = get("/wallet", admin_token)
    t.assert_eq(r.status_code, 200, "D13 admin GET /wallet = 200")

    r = get("/credit-packages", user_token)
    t.assert_eq(r.status_code, 200, "D14 user2 GET /credit-packages = 200")
    body = r.json() if r.status_code == 200 else {}
    pkgs = body.get("packages", [])
    t.assert_eq(len(pkgs), 4, f"D14 returns 4 packages (got {len(pkgs)})")

    r = get("/me/ai-rates", user_token)
    t.assert_eq(r.status_code, 200, "D15 user2 GET /me/ai-rates = 200")
    body = r.json() if r.status_code == 200 else {}
    t.assert_true(
        isinstance(body, dict)
        and all(k in body for k in ("simple", "medium", "complex")),
        "D15 /me/ai-rates returns rate dict with simple/medium/complex",
        f"got={body!r}",
    )

    # ------------------------------------------------ Summary
    print("\n" + "=" * 70)
    print(f"PASSED: {len(t.passed)}")
    print(f"FAILED: {len(t.failed)}")
    if t.failed:
        print("\nFailures:")
        for label, why in t.failed:
            print(f"  - {label}\n      {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
