"""
Backend tests — Smart Paste Duplicate Detection
Target: POST /api/smart-paste/check-duplicate
Plus regression checks for /api/smart-paste/parse and /api/smart-paste.
"""
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
HDRS = {"Content-Type": "application/json"}

PASSED: List[str] = []
FAILED: List[str] = []


def _assert(cond: bool, label: str, detail: str = "") -> bool:
    if cond:
        PASSED.append(label)
        print(f"  PASS: {label}")
        return True
    FAILED.append(f"{label} :: {detail}")
    print(f"  FAIL: {label} :: {detail}")
    return False


def _get(path: str, **kw) -> requests.Response:
    return requests.get(BASE + path, timeout=30, **kw)


def _post(path: str, body: Dict[str, Any]) -> requests.Response:
    return requests.post(BASE + path, headers=HDRS, data=json.dumps(body), timeout=60)


def _delete(path: str) -> requests.Response:
    return requests.delete(BASE + path, timeout=30)


def _clean10(p: str) -> str:
    d = "".join(c for c in (p or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def main() -> int:
    print("=" * 72)
    print(f"Smart Paste Duplicate Detection — {BASE}")
    print("=" * 72)

    # ---- Case 1: brand new phone, fresh order_id → no duplicates ----
    print("\n[CASE 1] No duplicates — brand new phone + fresh order_id")
    body1 = {
        "text": (
            "Name: Fresh Customer\n"
            "Phone: 9000000001\n"
            "Address: 1 New St\n"
            "City: Surat\n"
            "State: Gujarat\n"
            "Pincode: 395001\n"
            "Item: X\n"
            "Amount: 100\n"
            "Payment: COD\n"
            "Order ID: NEW-UNIQUE-001"
        )
    }
    r = _post("/smart-paste/check-duplicate", body1)
    _assert(r.status_code == 200, "CASE1 HTTP 200", f"status={r.status_code} body={r.text[:200]}")
    data1 = r.json() if r.status_code == 200 else {}
    print(f"  response summary: fields={list((data1.get('fields') or {}).keys())} "
          f"duplicates_count={len(data1.get('duplicates') or [])}")
    print(f"  fields.customer_phone={data1.get('fields', {}).get('customer_phone')!r}")
    print(f"  fields.order_id={data1.get('fields', {}).get('order_id')!r}")
    _assert(isinstance(data1.get("duplicates"), list), "CASE1 duplicates is a list")
    _assert(data1.get("duplicates") == [], "CASE1 duplicates is empty []",
            f"got={data1.get('duplicates')}")
    _assert(
        data1.get("fields", {}).get("customer_phone") == "9000000001",
        "CASE1 fields.customer_phone parsed correctly",
        f"got={data1.get('fields', {}).get('customer_phone')!r}",
    )
    _assert(
        "fields" in data1 and "confidence" in data1 and "warnings" in data1,
        "CASE1 response has fields/confidence/warnings keys",
    )

    # ---- Pick an existing shipment to use for CASE 2/3/4 ----
    print("\n[fetch] GET /shipments to pick an existing record for subsequent cases")
    r = _get("/shipments")
    _assert(r.status_code == 200, "GET /shipments HTTP 200")
    ships: List[Dict[str, Any]] = r.json() if r.status_code == 200 else []
    print(f"  got {len(ships)} shipments")
    existing_phone: Optional[str] = None
    existing_name: Optional[str] = None
    existing_order_id: Optional[str] = None
    existing_id: Optional[str] = None
    for s in ships:
        p = s.get("customer_phone") or ""
        if _clean10(p):
            existing_phone = p
            existing_name = s.get("customer_name") or ""
            existing_order_id = s.get("order_id") or ""
            existing_id = s.get("id")
            break
    print(f"  picked: id={existing_id} name={existing_name!r} "
          f"phone={existing_phone!r} order_id={existing_order_id!r}")

    # ---- Case 2: phone match ----
    print("\n[CASE 2] Phone match — same phone, different name/order_id")
    data2: Dict[str, Any] = {}
    if existing_phone:
        body2 = {
            "text": (
                "Name: Totally Different Person\n"
                f"Phone: {_clean10(existing_phone)}\n"
                "Address: 99 Other Rd\n"
                "City: Surat\n"
                "State: Gujarat\n"
                "Pincode: 395001\n"
                "Item: Y\n"
                "Amount: 250\n"
                "Payment: PAID\n"
                "Order ID: DIFFERENT-ORDER-XYZ-9999"
            )
        }
        r = _post("/smart-paste/check-duplicate", body2)
        _assert(r.status_code == 200, "CASE2 HTTP 200",
                f"status={r.status_code} body={r.text[:200]}")
        data2 = r.json() if r.status_code == 200 else {}
        dups2: List[Dict[str, Any]] = data2.get("duplicates") or []
        print(f"  duplicates_count={len(dups2)}")
        for d in dups2[:5]:
            print(f"    - kind={d.get('kind')} phone={d.get('customer_phone')!r} "
                  f"match_on={d.get('match_on')} created_at={d.get('created_at')}")
        _assert(len(dups2) >= 1, "CASE2 duplicates.length >= 1", f"got={len(dups2)}")
        # match_on includes "phone"
        ok_match = all("phone" in (d.get("match_on") or []) for d in dups2)
        _assert(ok_match, "CASE2 every duplicate has 'phone' in match_on",
                f"match_ons={[d.get('match_on') for d in dups2]}")
        # kind is pending or shipment
        ok_kind = all((d.get("kind") in ("pending", "shipment")) for d in dups2)
        _assert(ok_kind, "CASE2 kind is 'pending' or 'shipment'",
                f"kinds={[d.get('kind') for d in dups2]}")
        # last 10 digits match
        want = _clean10(existing_phone)
        ok_phone = all(_clean10(d.get("customer_phone") or "") == want for d in dups2)
        _assert(ok_phone, "CASE2 last-10-digits of duplicate.customer_phone match input",
                f"want={want} got={[_clean10(d.get('customer_phone') or '') for d in dups2]}")
        # sorted newest first
        cas = [d.get("created_at") or "" for d in dups2]
        _assert(
            cas == sorted(cas, reverse=True),
            "CASE2 results sorted newest first (created_at desc)",
            f"created_ats={cas}",
        )
        # cap at 5
        _assert(len(dups2) <= 5, "CASE2 cap at 5", f"got={len(dups2)}")
    else:
        _assert(False, "CASE2 pre-req: could not find an existing shipment with a phone")

    # ---- Case 3: phone with +91 prefix ----
    print("\n[CASE 3] Phone with +91 prefix — same matches expected")
    if existing_phone:
        body3 = {
            "text": (
                "Name: Another Name\n"
                f"Phone: +91 {_clean10(existing_phone)}\n"
                "Address: 77 Demo Ln\n"
                "City: Surat\n"
                "State: Gujarat\n"
                "Pincode: 395001\n"
                "Item: Z\n"
                "Amount: 150\n"
                "Payment: COD\n"
                "Order ID: TEST-PLUS91-ABC"
            )
        }
        r = _post("/smart-paste/check-duplicate", body3)
        _assert(r.status_code == 200, "CASE3 HTTP 200",
                f"status={r.status_code} body={r.text[:200]}")
        data3 = r.json() if r.status_code == 200 else {}
        dups3 = data3.get("duplicates") or []
        dups2 = data2.get("duplicates") or []
        ids3 = sorted([d.get("id") for d in dups3])
        ids2 = sorted([d.get("id") for d in dups2])
        print(f"  case2 ids={ids2}\n  case3 ids={ids3}")
        _assert(len(dups3) >= 1, "CASE3 duplicates.length >= 1", f"got={len(dups3)}")
        _assert(ids3 == ids2, "CASE3 same duplicates as CASE2", f"ids3={ids3} ids2={ids2}")
        # Extra — parsed phone should include +91 text OR stripped digits
        parsed_phone = (data3.get("fields") or {}).get("customer_phone") or ""
        _assert(
            _clean10(parsed_phone) == _clean10(existing_phone),
            "CASE3 parsed customer_phone cleans to same 10 digits",
            f"parsed={parsed_phone!r}",
        )
    else:
        _assert(False, "CASE3 pre-req missing (no existing phone)")

    # ---- Case 4: order_id match with new phone ----
    print("\n[CASE 4] Order ID match — fresh phone, existing order_id")
    data4: Dict[str, Any] = {}
    used_order_id: Optional[str] = None
    if existing_order_id and existing_order_id.strip():
        used_order_id = existing_order_id.strip()
    else:
        # Scan shipments list for one with a non-empty order_id
        for s in ships:
            oid = (s.get("order_id") or "").strip()
            if oid:
                used_order_id = oid
                break
    print(f"  chosen order_id: {used_order_id!r}")
    if used_order_id:
        body4 = {
            "text": (
                "Name: Yet Another Buyer\n"
                "Phone: 9999999999\n"
                "Address: 1 Random St\n"
                "City: Surat\n"
                "State: Gujarat\n"
                "Pincode: 395001\n"
                "Item: Q\n"
                "Amount: 500\n"
                "Payment: COD\n"
                f"Order ID: {used_order_id}"
            )
        }
        r = _post("/smart-paste/check-duplicate", body4)
        _assert(r.status_code == 200, "CASE4 HTTP 200",
                f"status={r.status_code} body={r.text[:200]}")
        data4 = r.json() if r.status_code == 200 else {}
        dups4: List[Dict[str, Any]] = data4.get("duplicates") or []
        print(f"  duplicates_count={len(dups4)}")
        for d in dups4[:5]:
            print(f"    - kind={d.get('kind')} order_id={d.get('order_id')!r} "
                  f"match_on={d.get('match_on')}")
        _assert(len(dups4) >= 1, "CASE4 at least one duplicate found",
                f"got={len(dups4)}")
        any_order_match = any("order_id" in (d.get("match_on") or []) for d in dups4)
        _assert(any_order_match, "CASE4 some duplicate has 'order_id' in match_on",
                f"match_ons={[d.get('match_on') for d in dups4]}")
        # fields.order_id should be parsed correctly
        _assert(
            (data4.get("fields") or {}).get("order_id") == used_order_id,
            "CASE4 parsed fields.order_id matches input",
            f"got={(data4.get('fields') or {}).get('order_id')!r}",
        )
    else:
        print("  WARN: no existing shipment has an order_id — skipping CASE4 assertions")

    # ---- Case 5: no phone + no order_id ----
    print("\n[CASE 5] No phone + no order_id — duplicates == []")
    body5 = {"text": "Name: Foo\nCity: Bar"}
    r = _post("/smart-paste/check-duplicate", body5)
    _assert(r.status_code == 200, "CASE5 HTTP 200",
            f"status={r.status_code} body={r.text[:200]}")
    data5 = r.json() if r.status_code == 200 else {}
    _assert(data5.get("duplicates") == [],
            "CASE5 duplicates is empty []",
            f"got={data5.get('duplicates')}")

    # ---- Case 6: Cap at 5 (soft check if we already have >5) ----
    print("\n[CASE 6] Cap at 5 (soft check)")
    if existing_phone:
        count_same_phone = sum(
            1 for s in ships
            if _clean10(s.get("customer_phone") or "") == _clean10(existing_phone)
        )
        dups2_len = len(data2.get("duplicates") or [])
        print(f"  shipments_with_same_phone={count_same_phone} "
              f"case2_duplicates={dups2_len}")
        _assert(dups2_len <= 5, "CASE6 cap at 5 holds in CASE2",
                f"got={dups2_len}")

    # ---- Case 7: Regression — /smart-paste/parse has no 'duplicates' key ----
    print("\n[CASE 7] Regression — /smart-paste/parse contract unchanged")
    r = _post("/smart-paste/parse", {
        "text": (
            "Name: Regression Test\n"
            "Phone: 9000000001\n"
            "Address: 1 Test St\n"
            "City: Surat\n"
            "State: Gujarat\n"
            "Pincode: 395001\n"
            "Item: X\n"
            "Amount: 100\n"
            "Payment: COD"
        )
    })
    _assert(r.status_code == 200, "CASE7 HTTP 200",
            f"status={r.status_code} body={r.text[:200]}")
    data7 = r.json() if r.status_code == 200 else {}
    print(f"  keys={list(data7.keys())}")
    _assert("fields" in data7, "CASE7 fields key present")
    _assert("confidence" in data7, "CASE7 confidence key present")
    _assert("warnings" in data7, "CASE7 warnings key present")
    _assert("duplicates" not in data7, "CASE7 'duplicates' key is ABSENT",
            f"got_keys={list(data7.keys())}")

    # ---- Case 8: Regression — /smart-paste still creates + soft-deletes ----
    print("\n[CASE 8] Regression — POST /smart-paste create + DELETE pending")
    created_pending_id: Optional[str] = None
    try:
        body8 = {
            "text": (
                "Name: Regression Paste Meera\n"
                "Phone: 9012345670\n"
                "Address: 7 Regression Rd\n"
                "City: Surat\n"
                "State: Gujarat\n"
                "Pincode: 395004\n"
                "Item: Cotton Suit\n"
                "Amount: 899\n"
                "Payment: COD\n"
                "Order ID: REGRESSION-TEST-ABC123"
            )
        }
        r = _post("/smart-paste", body8)
        _assert(r.status_code == 200, "CASE8 POST /smart-paste HTTP 200",
                f"status={r.status_code} body={r.text[:300]}")
        po = r.json() if r.status_code == 200 else {}
        created_pending_id = po.get("id")
        sheet_row_num = po.get("sheet_row_num")
        print(f"  created pending id={created_pending_id} "
              f"sheet_row_num={sheet_row_num} "
              f"customer_name={po.get('customer_name')!r}")
        _assert(bool(created_pending_id), "CASE8 PendingOrder returned with id")
        _assert(
            isinstance(sheet_row_num, int) and sheet_row_num > 1,
            "CASE8 sheet_row_num is int > 1 (Master Sheet appended)",
            f"got={sheet_row_num!r}",
        )
        _assert(po.get("customer_phone") == "9012345670",
                "CASE8 customer_phone persisted correctly",
                f"got={po.get('customer_phone')!r}")
    finally:
        # Cleanup + DELETE pending assertion
        if created_pending_id:
            print(f"  cleanup: DELETE /orders/pending/{created_pending_id}")
            r = _delete(f"/orders/pending/{created_pending_id}")
            _assert(r.status_code == 200, "CASE8 DELETE /orders/pending/{id} HTTP 200",
                    f"status={r.status_code} body={r.text[:200]}")
            del_body = r.json() if r.status_code == 200 else {}
            print(f"  delete response: {json.dumps(del_body)[:300]}")
            _assert(del_body.get("ok") is True,
                    "CASE8 DELETE response ok=true",
                    f"got={del_body}")
            sheet_info = del_body.get("sheet") or {}
            _assert(sheet_info.get("attempted") is True,
                    "CASE8 DELETE sheet.attempted=true",
                    f"got={sheet_info}")
            _assert(sheet_info.get("ok") is True,
                    "CASE8 DELETE sheet.ok=true (tombstone written)",
                    f"got={sheet_info}")

    # ---- Print summarized JSONs for key cases ----
    print("\n" + "=" * 72)
    print("SUMMARISED RESPONSE JSON FOR CASES 1, 2, 4, 7")
    print("=" * 72)

    def _summ(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "fields": d.get("fields"),
            "warnings": d.get("warnings"),
            "duplicates_count": len(d.get("duplicates") or []),
            "duplicates_sample": [
                {k: v for k, v in dd.items()
                 if k in ("kind", "id", "customer_name", "customer_phone",
                          "order_id", "status", "match_on", "created_at")}
                for dd in (d.get("duplicates") or [])[:3]
            ],
        }

    print("\nCASE1 =>")
    print(json.dumps(_summ(data1), indent=2, default=str))
    print("\nCASE2 =>")
    print(json.dumps(_summ(data2), indent=2, default=str))
    print("\nCASE4 =>")
    print(json.dumps(_summ(data4), indent=2, default=str))
    print("\nCASE7 =>")
    print(json.dumps({"keys": list(data7.keys()),
                      "fields": data7.get("fields"),
                      "warnings": data7.get("warnings")},
                     indent=2, default=str))

    # ---- Final tally ----
    print("\n" + "=" * 72)
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}")
    if FAILED:
        print("\nFailed assertions:")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 72)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
