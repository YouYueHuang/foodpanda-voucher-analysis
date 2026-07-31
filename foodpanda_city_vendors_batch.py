#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foodpanda 多國 — 依 top_vendor_city_per_country.csv 的 city_id 抓每個城市的 vendor 清單
邏輯完全沿用 foodpanda_pk_vendors.py（vendors-gateway API + city_id 分頁）

輸入：CSV（國家, 城市名, city_id[, vendor_count]）
輸出：vendor/ 資料夾下，每城市各存一組
       {國家}_{城市名}_vendors.csv
       {國家}_{城市名}_vendors.json

API（來自使用者先前提供的 Copy as cURL，各國同一套 gateway，只換網域/國別代碼）：
  GET https://{code}.fd-api.com/vendors-gateway/api/v1/pandora/vendors
      ?configuration=&country={code}&city_id={id}&include=&language_id=1
      &sort=&offset={offset}&limit=48&vertical=restaurants

執行方式：
  python foodpanda_city_vendors_batch.py --input top_vendor_city_per_country.csv
  python foodpanda_city_vendors_batch.py --input xxx.csv --total 1000   # 每城市上限
  python foodpanda_city_vendors_batch.py --input xxx.csv --debug        # 存第一頁原始回應
"""

import argparse
import base64
import csv
import json
import os
import random
import re
import string
import sys
import time
import requests

# ─── 國家名稱 → 國別代碼／官網網域 ────────────────────────────────────────────
# 需與輸入 CSV 的「國家」欄位完全對應

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

# 官網網域尾碼：大部分國家是 <code>，少數是 com.<code>
SITE_DOMAIN_MAP = {
    "bd": "com.bd", "kh": "com.kh", "la": "la", "my": "my",
    "mm": "com.mm", "pk": "pk", "ph": "ph", "tw": "com.tw",
}

PAGE_SIZE = 48
REQUEST_DELAY = 0.6
SAFETY_CAP = 200_000    # 純粹防止異常無限迴圈，不是預期上限（實際以 API 回傳的
                        # total／items 為空自然停止，各城市會抓到真正的全部家數）

GATEWAY_HEADERS = {
    "x-disco-client-id": "pd-microfrontend/web-acquisition",
    "user-logged-in":    "false",
}


# ─── Perseus header ──────────────────────────────────────────────────────────

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
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Origin":           f"https://www.foodpanda.{domain}",
        "Referer":          f"https://www.foodpanda.{domain}/",
        "x-fp-api-key":     "volo",
    })
    s.headers.update(make_perseus_headers())
    return s


# ─── 抓取（分頁，邏輯照搬 foodpanda_pk_vendors.py）───────────────────────────

def fetch_page_gateway(session, country_code, city_id, offset, limit, debug=False, tag=""):
    gateway_api = f"https://{country_code}.fd-api.com/vendors-gateway/api/v1/pandora/vendors"
    params = {
        "configuration": "",
        "country":       country_code,
        "city_id":       city_id,
        "include":       "",
        "language_id":   1,
        "sort":          "",
        "offset":        offset,
        "limit":         limit,
        "vertical":      "restaurants",
    }
    resp = session.get(gateway_api, params=params, headers=GATEWAY_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if debug and offset == 0:
        fname = f"debug_page0_{tag}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"    [DEBUG] 原始回應已存 {fname}")

    d = data.get("data", {})
    if isinstance(d, list):
        return d, None
    items = d.get("items") or d.get("vendors") or []
    total = d.get("available_count") or d.get("returned_count") or d.get("total")
    return items, total


def crawl_vendors(session, country_code, city_id, total_wanted, debug=False, tag=""):
    vendors, seen = [], set()
    offset = 0
    while len(vendors) < total_wanted:
        try:
            items, total = fetch_page_gateway(session, country_code, city_id, offset,
                                              PAGE_SIZE, debug=debug, tag=tag)
        except Exception as e:
            print(f"    ❌ offset={offset} 失敗: {e}")
            break
        if not items:
            print(f"    offset={offset}: 沒有更多資料，停止")
            break
        new_count = 0
        for v in items:
            code = v.get("code")
            if code and code not in seen:
                seen.add(code)
                vendors.append(v)
                new_count += 1
        print(f"    offset={offset}: +{new_count} 家（累計 {len(vendors)}"
              + (f" / 全區共 {total}" if total else "") + "）")
        offset += PAGE_SIZE
        if total and offset >= int(total):
            break
        time.sleep(REQUEST_DELAY)
    return vendors[:total_wanted]


# ─── 欄位萃取（照搬 foodpanda_pk_vendors.py）─────────────────────────────────

def extract_fields(v):
    cuisines = [c.get("name") for c in (v.get("cuisines") or []) if c.get("name")]
    lat = v.get("latitude")
    lng = v.get("longitude")
    if lat is None and isinstance(v.get("location"), dict):
        lat = v["location"].get("latitude")
        lng = v["location"].get("longitude")

    address = v.get("address")
    if not address:
        parts = [v.get("address_line1"), v.get("address_line2")]
        city = v.get("city")
        if isinstance(city, dict):
            parts.append(city.get("name"))
        elif isinstance(city, str):
            parts.append(city)
        address = ", ".join(p for p in parts if p) or None

    rate_star = v.get("rating")
    rate_number = (
        v.get("review_number") or v.get("rating_count") or v.get("votes_number")
    )

    return {
        "name":        v.get("name"),
        "code":        v.get("code"),
        "latitude":    lat,
        "longitude":   lng,
        "category":    ", ".join(cuisines),
        "address":     address,
        "rate_star":   rate_star,
        "rate_number": rate_number,
    }


# ─── 讀取輸入 CSV（含錯位資料自動修復）───────────────────────────────────────

def load_cities(csv_path):
    """
    讀取「國家, 城市名, city_id[, vendor_count]」CSV。

    已知資料問題（來自 Bangladesh 的 "Cox's Bazar"）：
      來源資料中城市名含 HTML 實體 &#x27;（單引號），原本應為
      &#x27;s 卻在某個環節被截成 &#x27 + 一個逗號，導致該列欄位
      整體錯位一格：city_id 欄變成非數字文字、真正的 city_id 被推到
      vendor_count 欄。偵測方式：city_id 欄位無法轉成整數時，視為
      錯位列，自動修復（把被拆開的城市名接回、抓最後一欄當 city_id）。
    """
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for raw in reader:
            if not raw or not any(raw):
                continue
            row = dict(zip(header, raw))
            country = row.get("國家", "").strip()
            name    = row.get("城市名", "").strip()
            cid_raw = (row.get("city_id") or "").strip()

            if not cid_raw.isdigit():
                # 疑似錯位列：city_id 欄不是數字，嘗試修復
                extra_fields = raw[len(header):]           # 多出來的欄位（若有）
                tail = [row.get("vendor_count", "")] + extra_fields
                numeric_tail = [t for t in tail if t.strip().isdigit()]
                if numeric_tail:
                    fixed_id = numeric_tail[0]
                    # 若城市名結尾正好是被截斷的 &#x27（HTML 單引號實體缺分號），
                    # 直接接回不再額外補引號，交給 sanitize_filename 統一解碼實體
                    if name.endswith("&#x27"):
                        fixed_name = name + cid_raw
                    else:
                        fixed_name = f"{name} {cid_raw}".strip()
                    print(f"  ⚠️  修復錯位列：「{name},{cid_raw},{row.get('vendor_count','')}」"
                          f"→ 城市名={fixed_name!r}, city_id={fixed_id}")
                    name, cid_raw = fixed_name, fixed_id
                else:
                    print(f"  ⚠️  略過無法解析的列：{row}")
                    continue

            rows.append({"國家": country, "城市名": name, "city_id": int(cid_raw)})
    return rows


# ─── 檔名清理 ────────────────────────────────────────────────────────────────

def sanitize_filename(text: str) -> str:
    """去除／取代檔名不安全字元，並解碼常見 HTML 實體（如 &#x27; → '）"""
    text = re.sub(r"&#x27;?", "'", text)   # &#x27; / &#x27 → '
    text = re.sub(r"&amp;", "&", text)
    text = text.strip()
    text = re.sub(r'[\\/:*?"<>|]', "_", text)   # 檔名系統不允許的字元
    text = re.sub(r"\s+", "_", text)
    return text


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="依 CSV 的 city_id 批次抓各城市 vendor")
    p.add_argument("--input",   required=True,              help="輸入 CSV（國家, 城市名, city_id）")
    p.add_argument("--total",   type=int, default=SAFETY_CAP, help="每城市最多抓幾家（預設不設限，抓到完為止；此參數僅作異常保護）")
    p.add_argument("--outdir",  default="vendor",           help="輸出資料夾（預設 vendor）")
    p.add_argument("--debug",   action="store_true",        help="存第一頁原始回應")
    args = p.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    print("=" * 62)
    print("  Foodpanda 多國 — 批次抓取各城市 vendor")
    print("=" * 62)

    cities = load_cities(args.input)
    print(f"\n讀取到 {len(cities)} 個城市")

    summary = []
    for i, c in enumerate(cities, 1):
        country_name, city_name, city_id = c["國家"], c["城市名"], c["city_id"]
        code = COUNTRY_CODE_MAP.get(country_name)
        print(f"\n({i}/{len(cities)}) {country_name} — {city_name}（city_id={city_id}）")

        if not code:
            print(f"  ⚠️  未知國家「{country_name}」，跳過（請在 COUNTRY_CODE_MAP 補上對應代碼）")
            summary.append({**c, "vendor_fetched": None, "status": "unknown_country"})
            continue

        session = build_session(code)
        tag = f"{code}_{city_id}"
        try:
            raw = crawl_vendors(session, code, city_id, args.total, debug=args.debug, tag=tag)
        except Exception as e:
            print(f"  ❌ 失敗: {e}")
            summary.append({**c, "vendor_fetched": None, "status": f"error: {e}"})
            continue

        rows = [extract_fields(v) for v in raw]

        fname_base = f"{sanitize_filename(country_name)}_{sanitize_filename(city_name)}_vendors"
        json_path = os.path.join(args.outdir, f"{fname_base}.json")
        csv_path  = os.path.join(args.outdir, f"{fname_base}.csv")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "name", "code", "latitude", "longitude",
                "category", "address", "rate_star", "rate_number",
            ])
            w.writeheader()
            w.writerows(rows)

        print(f"  ✅ 共 {len(rows)} 家 → {csv_path}")
        summary.append({**c, "vendor_fetched": len(rows), "status": "ok"})
        time.sleep(REQUEST_DELAY)

    # ── 總結 ──
    print("\n" + "=" * 62)
    print("  執行總結")
    print("=" * 62)
    ok = [s for s in summary if s["status"] == "ok"]
    for s in summary:
        mark = "✅" if s["status"] == "ok" else "❌"
        print(f"  {mark} {s['國家']:<20s} {s['城市名']:<28s} "
              f"{s['vendor_fetched'] if s['vendor_fetched'] is not None else '-':>6}")
    print(f"\n  成功 {len(ok)} / {len(summary)} 個城市，"
          f"共 {sum(s['vendor_fetched'] for s in ok):,d} 家 vendor")
    print(f"  輸出資料夾: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()