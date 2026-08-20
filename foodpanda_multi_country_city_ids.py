#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foodpanda 多國 — 抓取各國「城市列表頁」的所有 city_id
輸出格式：國家, 城市名, city_id 的 CSV

原理（沿用 foodpanda_tw_city_counts.py 已驗證的方法）：
  1. 開啟每個國家的 /city 入口頁，解析所有 /city/{slug} 連結
  2. 逐一開城市頁，從 HTML 中的 "city-description-{id}" /
     "city-internal-link-{id}" 取出 city_id
     （這是實測 20+ 個台灣城市頁 100% 驗證過的 pattern）

涵蓋國家：
  Bangladesh (bd) / Cambodia (kh) / Laos (la) / Malaysia (my)
  Myanmar (mm) / Pakistan (pk) / Philippines (ph)

執行方式：
  python foodpanda_multi_country_city_ids.py
  python foodpanda_multi_country_city_ids.py --country pk       # 只跑單一國家先測
  python foodpanda_multi_country_city_ids.py --debug            # 解析失敗時存 HTML
"""

import argparse
import base64
import csv
import json
import random
import re
import string
import sys
import time
import requests

# ─── 國家設定 ────────────────────────────────────────────────────────────────
# name        : 顯示用中文國名
# city_page   : 城市列表入口頁（可能帶語言前綴 /zh/city、/en/city）
# base        : 該國站台網域根，用來組出 /city/{slug} 的完整網址
#
# 特殊欄位（香港、新加坡）：
# mode        : "area" — 該國只有單一城市，直接從 /city/{city_slug}/area 頁抓 area 列表
# area_page   : area 列表頁完整 URL
# city_slug   : 城市 slug，用來組出各 area 的完整 URL

COUNTRIES = [
    {"code": "bd", "name": "Bangladesh 孟加拉", "city_page": "https://www.foodpanda.com.bd/city",
     "base": "https://www.foodpanda.com.bd"},
    {"code": "kh", "name": "Cambodia 柬埔寨",   "city_page": "https://www.foodpanda.com.kh/en/city",
     "base": "https://www.foodpanda.com.kh/en"},
    {"code": "la", "name": "Laos 寮國",         "city_page": "https://www.foodpanda.la/zh/city",
     "base": "https://www.foodpanda.la/zh"},
    {"code": "my", "name": "Malaysia 馬來西亞",  "city_page": "https://www.foodpanda.my/zh/city",
     "base": "https://www.foodpanda.my/zh"},
    {"code": "mm", "name": "Myanmar 緬甸",      "city_page": "https://www.foodpanda.com.mm/en/city",
     "base": "https://www.foodpanda.com.mm/en"},
    {"code": "pk", "name": "Pakistan 巴基斯坦",  "city_page": "https://www.foodpanda.pk/city",
     "base": "https://www.foodpanda.pk"},
    {"code": "ph", "name": "Philippines 菲律賓", "city_page": "https://www.foodpanda.ph/city",
     "base": "https://www.foodpanda.ph"},
    # ── Area 模式（單一城市，直接列 area）──────────────────────────────────
    {"code": "hk", "name": "Hong Kong 香港",    "mode": "area",
     "area_page": "https://www.foodpanda.hk/zh/city/hong-kong/area",
     "base": "https://www.foodpanda.hk", "city_slug": "hong-kong"},
    {"code": "sg", "name": "Singapore 新加坡",  "mode": "area",
     "area_page": "https://www.foodpanda.sg/city/singapore/area",
     "base": "https://www.foodpanda.sg", "city_slug": "singapore"},
]

REQUEST_DELAY = 0.6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


# ─── Perseus header（部分站台的頁面請求也會檢查）────────────────────────────

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


def build_session(country: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.headers.update(make_perseus_headers())
    s.headers["Origin"]  = country["base"].split("/")[0] + "//" + country["base"].split("/")[2]
    s.headers["Referer"] = country["base"] + "/"
    return s


# ─── Step 1：抓城市列表頁的連結 ───────────────────────────────────────────────

def fetch_city_links(session, country: dict):
    """回傳 [(slug, 顯示名稱), ...]，去重保序。相容 /city/xx 與 /en/city/xx 等前綴。"""
    resp = session.get(country["city_page"], headers=HTML_HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    html = resp.text

    # 相容各種語言前綴：href="/city/xxx"、href="/en/city/xxx"、含完整網域
    pattern = re.compile(
        r'href="(?:https?://[^"/]+)?(?:/[a-z]{2})?/city/([a-z0-9\-]+)/?"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    seen, cities = set(), []
    for slug, inner in pattern.findall(html):
        name = re.sub(r"<[^>]+>", "", inner).strip()
        if not name:
            continue
        if slug not in seen:
            seen.add(slug)
            cities.append((slug, name))
    return cities


# ─── Step 1b：Area 模式 — 直接抓 area 列表頁的連結 ─────────────────────────
# 適用於香港、新加坡這種只有單一城市的國家。
# 連結格式：/city/{city_slug}/area/{area_slug}（可能帶語言前綴 /zh/、/en/）

def fetch_area_links(session, country: dict):
    """回傳 [(area_slug, 顯示名稱), ...]，去重保序。"""
    resp = session.get(country["area_page"], headers=HTML_HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    html = resp.text

    city_slug = re.escape(country["city_slug"])
    pattern = re.compile(
        r'href="(?:https?://[^"/]+)?(?:/[a-z]{2})?/city/' + city_slug +
        r'/area/([a-z0-9\-]+)/?"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    seen, areas = set(), []
    for slug, inner in pattern.findall(html):
        name = re.sub(r"<[^>]+>", "", inner).strip()
        if not name:
            continue
        if slug not in seen:
            seen.add(slug)
            areas.append((slug, name))
    return areas


# ─── Step 2：從城市頁解析 city_id ─────────────────────────────────────────────
# 已用 25 個台灣城市頁 HTML 驗證：city_id 出現在
#   "city-description-{id}" 與 "city-internal-link-{id}" 這兩個 key 中
# 唯一命中才採用；並過濾掉時間戳等異常大數字（曾在 yilan 頁遇過 Unix ts 誤判）

CITY_ID_PATTERNS = [
    r'city-description-(\d+)',
    r'city-internal-link-(\d+)',
    r'"city_id"\s*:\s*(\d+)',
    r'"cityId"\s*:\s*(\d+)',
    r'city_id=(\d+)',
]

def fetch_city_id(session, country: dict, slug: str, debug=False):
    url = f"{country['base']}/city/{slug}"
    resp = session.get(url, headers=HTML_HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"           # requests 有時誤判編碼，強制指定避免中文/重音字元亂碼
    html = resp.text

    for pat in CITY_ID_PATTERNS:
        raw_matches = [int(x) for x in re.findall(pat, html)]
        raw_matches = [x for x in raw_matches if x > 0]
        if not raw_matches:
            continue

        distinct = set(raw_matches)
        if len(distinct) == 1:
            return distinct.pop()

        # 大城市頁常帶「附近熱門城市」推薦區塊，會混入其他城市的 id，
        # 造成同頁出現多個不同 id。自身 id 通常在 canonical/breadcrumb/
        # SEO meta/hero 等多處重複出現，而推薦區塊的其他城市 id 大多只
        # 出現一次 → 取出現次數最多者；次數相同則取文件中第一個出現的。
        from collections import Counter
        counts = Counter(raw_matches)
        best_count = max(counts.values())
        candidates = [v for v in raw_matches if counts[v] == best_count]
        chosen = candidates[0]   # raw_matches 保留原始出現順序，取第一個即最先出現者
        if debug:
            print(f"    [DEBUG] pattern {pat} 命中多個不同 id: {dict(counts)}，"
                  f"取出現次數最多者 = {chosen}")
        return chosen

    if debug:
        fname = f"debug_{country['code']}_{slug}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"    [DEBUG] 找不到 city_id，HTML 已存 {fname}")
    return None


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def crawl_country_by_city(country: dict, debug=False):
    """一般模式：先抓城市列表，再逐一進城市頁取得 city_id。"""
    session = build_session(country)
    rows = []

    try:
        cities = fetch_city_links(session, country)
    except Exception as e:
        print(f"  ❌ 無法抓取城市列表頁: {e}")
        return rows

    print(f"  找到 {len(cities)} 個城市連結")
    if not cities:
        print(f"  ⚠️  沒有解析到任何連結，該站台網頁結構可能不同，"
              f"建議用 --debug 或提供該頁 HTML")
        return rows

    for i, (slug, name) in enumerate(cities, 1):
        try:
            city_id = fetch_city_id(session, country, slug, debug=debug)
            if city_id is None:
                print(f"  ({i}/{len(cities)}) {name} ({slug}) → ⚠️  無餐廳，略過")
                continue
            print(f"  ({i}/{len(cities)}) {name} ({slug}) → {city_id}")
            rows.append({
                "國家":     country["name"],
                "城市名":   name,
                "slug":     slug,
                "city_id":  city_id,
            })
        except Exception as e:
            print(f"  ({i}/{len(cities)}) {name} ({slug}) → ❌ 失敗: {e}")
        time.sleep(REQUEST_DELAY)

    return rows


def crawl_country_by_area(country: dict, debug=False):
    """Area 模式（香港、新加坡）：單一城市，直接抓 area 列表頁的 area 名稱與 slug。
    不需要進每個 area 頁面，因為這類國家沒有城市層級的 city_id 概念，
    area 本身就是最細單位；「城市名」欄位直接填 area 名稱。
    """
    session = build_session(country)
    rows = []

    try:
        areas = fetch_area_links(session, country)
    except Exception as e:
        print(f"  ❌ 無法抓取 area 列表頁: {e}")
        return rows

    print(f"  找到 {len(areas)} 個 area 連結")
    if not areas:
        print(f"  ⚠️  沒有解析到任何連結，該站台網頁結構可能不同，"
              f"建議用 --debug 或提供該頁 HTML")
        return rows

    for i, (slug, name) in enumerate(areas, 1):
        print(f"  ({i}/{len(areas)}) {name} ({slug})")
        rows.append({
            "國家":     country["name"],
            "城市名":   name,   # area 名稱作為「城市名」欄位
            "slug":     slug,
            "city_id":  "",     # area 模式無 city_id 概念，留空
        })

    return rows


def crawl_country(country: dict, debug=False):
    print("\n" + "=" * 62)
    label = country.get("area_page", country.get("city_page"))
    print(f"  {country['name']}  —  {label}")
    print("=" * 62)

    if country.get("mode") == "area":
        return crawl_country_by_area(country, debug=debug)
    return crawl_country_by_city(country, debug=debug)


def main():
    p = argparse.ArgumentParser(description="Foodpanda 多國 city_id 爬蟲")
    p.add_argument("--country", default=None,
                   help="只跑單一國家代碼（如 pk、hk、sg），預設跑全部 9 國")
    p.add_argument("--debug",  action="store_true", help="解析失敗時存 HTML")
    p.add_argument("--output", default="foodpanda_multi_country_city_ids",
                   help="輸出檔名前綴")
    args = p.parse_args()

    targets = COUNTRIES
    if args.country:
        targets = [c for c in COUNTRIES if c["code"] == args.country]
        if not targets:
            print(f"❌ 找不到國家代碼 {args.country}")
            sys.exit(1)

    all_rows = []
    for country in targets:
        all_rows.extend(crawl_country(country, debug=args.debug))

    print("\n" + "=" * 62)
    print(f"  總結：共 {len(all_rows)} 個有效城市（無餐廳城市已略過）")
    print("=" * 62)
    for country in targets:
        cn = country["name"]
        sub = [r for r in all_rows if r["國家"] == cn]
        print(f"  {cn:<20s}: {len(sub)} 個城市")

    # 存 CSV（依需求格式：國家, 城市名, city id）
    csv_path = f"{args.output}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["國家", "城市名", "city id"])
        for r in all_rows:
            w.writerow([r["國家"], r["城市名"], r["city_id"]])
    print(f"\n💾 已儲存 {csv_path}（含 {len(all_rows)} 列）")

    # 額外存完整 JSON（含 slug，方便除錯）
    json_path = f"{args.output}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    print(f"💾 已儲存 {json_path}（含 slug 欄位，無餐廳城市已略過）")


if __name__ == "__main__":
    main()
