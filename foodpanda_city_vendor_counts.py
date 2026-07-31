#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foodpanda 多國 — 依 city_id 查詢每個城市的餐廳（vendor）數量
輸入：foodpanda_multi_country_city_ids.py 產出的 CSV（國家, 城市名, city id）
輸出：每個城市的 vendor 數量（CSV + JSON）

原理（沿用 foodpanda_tw_city_counts.py 的 Step 3 邏輯）：
  每個城市只發一次 limit=1 的 vendors-gateway 請求，直接讀
  available_count 欄位，不需要把店家全部翻完：
    GET https://{country}.fd-api.com/vendors-gateway/api/v1/pandora/vendors
        ?country={country}&city_id={id}&language_id=1&offset=0&limit=1&vertical=restaurants
  若該國回應沒有總數欄位，才退回分頁數到底的備援（同 tw 版）。

執行方式：
  python foodpanda_city_vendor_counts.py --input foodpanda_multi_country_city_ids.csv
  python foodpanda_city_vendor_counts.py --input xxx.csv --country pk   # 只跑單一國先測
"""

import argparse
import base64
import csv
import json
import random
import string
import sys
import time
import requests

# ─── 國家名稱 → fd-api 網域代碼 ──────────────────────────────────────────────
# 需與 CSV 的「國家」欄位完全對應（來自 foodpanda_multi_country_city_ids.py）

COUNTRY_CODE_MAP = {
    "Bangladesh 孟加拉":  "bd",
    "Cambodia 柬埔寨":    "kh",
    "Laos 寮國":          "la",
    "Malaysia 馬來西亞":   "my",
    "Myanmar 緬甸":       "mm",
    "Pakistan 巴基斯坦":   "pk",
    "Philippines 菲律賓":  "ph",
    "Taiwan 台灣":        "tw",
}

REQUEST_DELAY = 0.6

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-fp-api-key":    "volo",
}

GATEWAY_HEADERS = {
    "x-disco-client-id": "pd-microfrontend/web-acquisition",
    "user-logged-in":    "false",
}


# ─── Perseus header（gateway API 部分站台會檢查）────────────────────────────

def _perseus_id(ts_ms=None):
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    digits = "".join(random.choices(string.digits, k=18))
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{ts}.{digits}.{suffix}"


def make_perseus_headers():
    now_ms = int(time.time() * 1000)
    client_id = _perseus_id(now_ms - 86400_000)
    dps = base64.b64encode(json.dumps({
        "session_id": "".join(random.choices("0123456789abcdef", k=32)),
        "perseus_id": client_id,
        "timestamp":  now_ms,
    }, separators=(",", ":")).encode()).decode()
    return {
        "perseus-client-id":  client_id,
        "perseus-session-id": _perseus_id(now_ms),
        "dps-session-id":     dps,
    }


def build_session(country_code: str) -> requests.Session:
    domain = SITE_DOMAIN_MAP.get(country_code, country_code)
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.headers.update(make_perseus_headers())
    s.headers["Origin"]  = f"https://www.foodpanda.{domain}"
    s.headers["Referer"] = f"https://www.foodpanda.{domain}/"
    return s


# 官網網域尾碼：大部分國家是 <code>，少數是 com.<code>（明確列表，不用猜規則）
SITE_DOMAIN_MAP = {
    "bd": "com.bd",   # foodpanda.com.bd
    "kh": "com.kh",   # foodpanda.com.kh
    "la": "la",       # foodpanda.la
    "my": "my",       # foodpanda.my
    "mm": "com.mm",   # foodpanda.com.mm
    "pk": "pk",       # foodpanda.pk
    "ph": "ph",       # foodpanda.ph
    "tw": "com.tw",   # foodpanda.com.tw
}


# ─── 查詢 vendor 總數（同 tw 版邏輯）─────────────────────────────────────────

def fetch_vendor_count(session, country_code, city_id, language_id=1):
    gateway_api = f"https://{country_code}.fd-api.com/vendors-gateway/api/v1/pandora/vendors"
    params = {
        "configuration": "",
        "country":       country_code,
        "city_id":       city_id,
        "include":       "",
        "language_id":   language_id,
        "sort":          "",
        "offset":        0,
        "limit":         1,
        "vertical":      "restaurants",
    }
    resp = session.get(gateway_api, params=params, headers=GATEWAY_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    d = data.get("data", {})
    if isinstance(d, dict):
        for key in ("available_count", "total", "returned_count"):
            if d.get(key) is not None:
                return int(d[key]), key
    # 回應沒有總數欄位 → 分頁數到底（上限保護）
    return _count_by_pagination(session, gateway_api, country_code, city_id, language_id), "paginated"


def _count_by_pagination(session, gateway_api, country_code, city_id, language_id, cap=10000):
    seen = set()
    offset, page = 0, 48
    while offset < cap:
        params = {
            "configuration": "", "country": country_code, "city_id": city_id,
            "include": "", "language_id": language_id, "sort": "",
            "offset": offset, "limit": page, "vertical": "restaurants",
        }
        resp = session.get(gateway_api, params=params, headers=GATEWAY_HEADERS, timeout=30)
        resp.raise_for_status()
        d = resp.json().get("data", {})
        items = (d.get("items") or d.get("vendors") or []) if isinstance(d, dict) else d
        if not items:
            break
        for v in items:
            if v.get("code"):
                seen.add(v["code"])
        offset += page
        time.sleep(REQUEST_DELAY)
    return len(seen)


# ─── 讀取輸入 CSV ─────────────────────────────────────────────────────────────

def load_cities(csv_path):
    """讀取「國家, 城市名, city id」CSV，只保留 city_id 有值的列"""
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cid = (r.get("city id") or r.get("city_id") or "").strip()
            if not cid:
                continue
            rows.append({
                "國家":   r["國家"],
                "城市名": r["城市名"],
                "city_id": int(cid),
            })
    return rows


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="依 city_id 查詢各城市 vendor 數量")
    p.add_argument("--input",   required=True, help="輸入 CSV（國家, 城市名, city id）")
    p.add_argument("--country", default=None,  help="只跑單一國家（用 CSV 裡的國家欄位完整字串）")
    p.add_argument("--output",  default="foodpanda_city_vendor_counts", help="輸出檔名前綴")
    args = p.parse_args()

    print("=" * 62)
    print("  Foodpanda 多國 — 各城市 vendor 數量統計")
    print("=" * 62)

    cities = load_cities(args.input)
    if args.country:
        cities = [c for c in cities if c["國家"] == args.country]
    print(f"\n讀取到 {len(cities)} 個有效城市（含 city_id）")

    # 依國家分組，同一國共用一個 session
    by_country = {}
    for c in cities:
        by_country.setdefault(c["國家"], []).append(c)

    results = []
    for country_name, group in by_country.items():
        code = COUNTRY_CODE_MAP.get(country_name)
        if not code:
            print(f"\n⚠️  未知國家「{country_name}」，跳過（請在 COUNTRY_CODE_MAP 補上對應代碼）")
            for c in group:
                results.append({**c, "vendor_count": None})
            continue

        print(f"\n[{country_name}]  共 {len(group)} 個城市  (fd-api: {code})")
        session = build_session(code)

        for i, c in enumerate(group, 1):
            try:
                count, source = fetch_vendor_count(session, code, c["city_id"])
                print(f"  ({i}/{len(group)}) {c['城市名']}  city_id={c['city_id']}"
                      f"  →  {count} 家（{source}）")
                results.append({**c, "vendor_count": count})
            except Exception as e:
                print(f"  ({i}/{len(group)}) {c['城市名']}  city_id={c['city_id']}  ❌ 失敗: {e}")
                results.append({**c, "vendor_count": None})
            time.sleep(REQUEST_DELAY)

    # ── 統計輸出 ──
    ok = [r for r in results if r["vendor_count"] is not None]
    ok.sort(key=lambda r: r["vendor_count"], reverse=True)

    print("\n" + "=" * 62)
    print(f"  統計結果（成功 {len(ok)} / {len(results)} 個城市）")
    print("=" * 62)
    print(f"  {'國家':<20s}{'城市名':<24s}{'city_id':>10s}{'vendor 數':>10s}")
    print("  " + "-" * 66)
    for r in ok:
        print(f"  {r['國家']:<20s}{r['城市名']:<24s}{r['city_id']:>10d}{r['vendor_count']:>10,d}")

    print("\n  各國小計：")
    for country_name in by_country:
        sub = [r for r in ok if r["國家"] == country_name]
        subtotal = sum(r["vendor_count"] for r in sub)
        print(f"    {country_name:<20s}: {len(sub)} 城市，共 {subtotal:,d} 家")

    grand_total = sum(r["vendor_count"] for r in ok)
    print(f"\n  總計: {grand_total:,d} 家 vendor（{len(ok)} 個城市）")

    # ── 存檔 ──
    csv_path  = f"{args.output}.csv"
    json_path = f"{args.output}.json"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["國家", "城市名", "city_id", "vendor_count"])
        w.writeheader()
        w.writerows(results)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已儲存 {csv_path} 與 {json_path}")


if __name__ == "__main__":
    main()