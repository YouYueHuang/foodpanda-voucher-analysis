#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foodpanda 多國 — 多執行緒版菜單爬蟲
在 foodpanda_menu_batch.py 的基礎上疊加：
  - 5 個 worker thread 同時處理各國任務（可用 --workers 調整）
  - 各國任務以「輪流交錯」方式排入佇列，讓多國同時有進度，
    而不是先把某一國全部抓完才輪到下一國
  - 除了台灣、孟加拉外的國家（可用 --exclude 調整）
  - 每個國家最多成功抓 400 家就停止（可用 --per-country-limit 調整），
    用 per-country 計數器 + stop flag 實作，一旦某國達標，
    佇列裡該國剩餘任務會直接跳過、不再發請求

沿用 foodpanda_menu_batch.py 既有邏輯（直接匯入，不重複維護）：
  - build_session()（含 warmup / Zyte 雙模式）
  - fetch_vendor_menu()（直接模式 / Zyte 模式統一入口）
  - parse_menu() / parse_deals()
  - discover_vendor_csvs() / load_vendor_codes_from_csv()

輸出：menu/{英文國家名}/{英文國家名}_{vendor code}.json（同原本規則）

執行方式：
  python foodpanda_menu_batch_threaded.py
  python foodpanda_menu_batch_threaded.py --workers 5 --per-country-limit 400
  python foodpanda_menu_batch_threaded.py --zyte              # 走 Zyte API
  python foodpanda_menu_batch_threaded.py --exclude Taiwan Bangladesh
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import foodpanda_menu_batch as fmb   # 沿用既有邏輯，不重複維護


# ─── 建立任務清單（依國家分組、去重、輪流交錯）───────────────────────────────

def build_country_vendor_lists(vendor_dir, exclude_english_names):
    """
    回傳 { (country_name, code, english_name): [vendor_info, ...] }
    exclude_english_names：要排除的英文國名清單（比對時忽略大小寫）
    """
    csv_files = fmb.discover_vendor_csvs(vendor_dir)
    exclude_set = {n.lower() for n in exclude_english_names}

    by_country = {}
    for path, country_name, code, english_name in csv_files:
        if english_name.lower() in exclude_set:
            continue
        by_country.setdefault((country_name, code, english_name), []).append(path)

    result = {}
    for key, paths in by_country.items():
        all_vendors = []
        for path in paths:
            all_vendors.extend(fmb.load_vendor_codes_from_csv(path))
        seen, unique = set(), []
        for v in all_vendors:
            if v["code"] not in seen:
                seen.add(v["code"])
                unique.append(v)
        result[key] = unique
    return result


def interleave_tasks(country_vendor_lists):
    """
    把各國的 vendor 清單「輪流交錯」成單一任務佇列：
      國A第1家、國B第1家、國C第1家、國A第2家、國B第2家 ...
    這樣多執行緒處理時，各國會同時有進度，而不是排隊等前一國抓完。
    """
    iterators = {key: iter(vendors) for key, vendors in country_vendor_lists.items()}
    tasks = []
    while iterators:
        exhausted = []
        for key, it in iterators.items():
            try:
                vinfo = next(it)
                tasks.append((key, vinfo))
            except StopIteration:
                exhausted.append(key)
        for key in exhausted:
            del iterators[key]
    return tasks


# ─── Worker ──────────────────────────────────────────────────────────────────

class CountryState:
    """每國一份：成功計數、停止旗標、輸出目錄、session（thread-safe：只用 lock 保護計數）"""
    def __init__(self, out_dir, session_ctx, limit):
        self.out_dir = out_dir
        self.session_ctx = session_ctx
        self.limit = limit
        self.success_count = 0
        self.lock = threading.Lock()
        self.stopped = False

    def try_reserve_slot(self):
        """
        檢查與佔位必須在同一個 lock 裡原子完成，否則多個 thread 會同時
        看到「還沒滿」而一起通過，导致 mark_success 各自 +1 造成超標。
        佔位後若請求失敗，呼叫 release_slot() 把名額還回去。
        """
        with self.lock:
            if self.success_count >= self.limit:
                self.stopped = True
                return False
            self.success_count += 1
            if self.success_count >= self.limit:
                self.stopped = True
            return True

    def release_slot(self):
        """請求失敗時把佔用的名額還回去，讓其他 vendor 有機會補上"""
        with self.lock:
            self.success_count = max(0, self.success_count - 1)
            self.stopped = self.success_count >= self.limit

    def current_count(self):
        with self.lock:
            return self.success_count


def process_one(task, states, args, summary_lock, summary):
    (country_name, code, english_name), vinfo = task
    state = states[(country_name, code, english_name)]

    if not state.try_reserve_slot():
        with summary_lock:
            summary.append({"country": english_name, "code": vinfo["code"], "status": "skipped_cap"})
        return

    vcode = vinfo["code"]
    out_path = os.path.join(state.out_dir, f"{english_name}_{vcode}.json")
    debug_dir = "debug_menu" if args.debug else None

    try:
        data = fmb.fetch_vendor_menu(state.session_ctx, code, vcode,
                                     latitude=vinfo["latitude"], longitude=vinfo["longitude"],
                                     debug_dir=debug_dir)
        menu  = fmb.parse_menu(data)
        deals = fmb.parse_deals(data)
        result = {
            "vendor": {
                "code":    data.get("code"),
                "name":    data.get("name"),
                "rating":  data.get("rating"),
                "address": data.get("address"),
            },
            "deals": deals,
            "menu":  menu,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        item_total = sum(c["item_count"] for c in menu)
        print(f"  [{english_name}] ({state.current_count()}/{state.limit}) {vcode}  ✅  "
              f"{len(menu)} 分類 / {item_total} 品項 / {len(deals)} 優惠")
        with summary_lock:
            summary.append({"country": english_name, "code": vcode, "status": "ok"})

    except requests.exceptions.HTTPError as e:
        state.release_slot()   # 佔位失敗要還回去，讓其他 vendor 能補上這個名額
        status = e.response.status_code if e.response is not None else "?"
        print(f"  [{english_name}] {vcode}  ❌  HTTP {status}")
        with summary_lock:
            summary.append({"country": english_name, "code": vcode, "status": f"http_{status}"})
    except Exception as e:
        state.release_slot()
        print(f"  [{english_name}] {vcode}  ❌  {e}")
        with summary_lock:
            summary.append({"country": english_name, "code": vcode, "status": f"error: {e}"})

    time.sleep(0.3)   # 輕度禮貌限速；因為是多執行緒併發，總吞吐量已由 workers 數決定


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="多執行緒批次抓取 vendor/ 資料夾內各 vendor 的菜單")
    p.add_argument("--vendor-dir", default="vendor", help="vendor CSV 所在資料夾（預設 vendor）")
    p.add_argument("--menu-dir",   default="menu",   help="輸出根資料夾（預設 menu）")
    p.add_argument("--workers",    type=int, default=5, help="同時執行的 thread 數（預設 5）")
    p.add_argument("--per-country-limit", type=int, default=400,
                   help="每個國家最多成功抓幾家就停止（預設 400）")
    p.add_argument("--exclude",    nargs="*", default=["Taiwan", "Bangladesh"],
                   help="要排除的國家（用英文國名，預設排除 Taiwan Bangladesh）")
    p.add_argument("--zyte",       action="store_true",
                   help="改用 Zyte API 抓取（需先設定環境變數 ZYTE_API_KEY）")
    p.add_argument("--debug",      action="store_true", help="失敗時把錯誤回應存到 debug_menu/")
    args = p.parse_args()

    if args.zyte and not fmb.ZYTE_API_KEY:
        print("❌ 加了 --zyte 但環境變數 ZYTE_API_KEY（或 CRAWLERA_API_KEY）未設定。")
        sys.exit(1)

    print("=" * 62)
    print(f"  Foodpanda 多國多執行緒 — {args.workers} workers，"
          f"每國上限 {args.per_country_limit} 家")
    print(f"  排除國家: {args.exclude}")
    print(f"  模式: {'Zyte API' if args.zyte else '直接連線'}")
    print("=" * 62)

    country_vendor_lists = build_country_vendor_lists(args.vendor_dir, args.exclude)
    if not country_vendor_lists:
        print(f"❌ 在 {args.vendor_dir}/ 找不到任何符合條件的 vendor CSV")
        sys.exit(1)

    print("\n各國 vendor 數（去重後）：")
    for (country_name, code, english_name), vendors in country_vendor_lists.items():
        capped = min(len(vendors), args.per_country_limit)
        print(f"  {country_name:<20s}: {len(vendors)} 家（本次最多處理 {capped} 家）")

    # 每國建立 session + 輸出資料夾 + 狀態物件
    states = {}
    for key in country_vendor_lists:
        country_name, code, english_name = key
        out_dir = os.path.join(args.menu_dir, english_name)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n建立 session：{country_name} ...")
        session_ctx = fmb.build_session(code, use_zyte=args.zyte)
        states[key] = CountryState(out_dir, session_ctx, args.per_country_limit)

    tasks = interleave_tasks(country_vendor_lists)
    print(f"\n共 {len(tasks)} 個任務（交錯排入佇列），開始以 {args.workers} 個 thread 處理 ...\n")

    summary = []
    summary_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_one, task, states, args, summary_lock, summary)
                  for task in tasks]
        for _ in as_completed(futures):
            pass   # 進度已在 process_one 內即時印出

    # ── 總結 ──
    print("\n" + "=" * 62)
    print("  執行總結")
    print("=" * 62)
    by_status = {}
    for s in summary:
        by_status.setdefault(s["country"], {}).setdefault(s["status"], 0)
        by_status[s["country"]][s["status"]] += 1
    for country, statuses in by_status.items():
        total = sum(statuses.values())
        ok = statuses.get("ok", 0)
        print(f"  {country:<15s}: {ok}/{total} 成功  {statuses}")

    os.makedirs(args.menu_dir, exist_ok=True)
    summary_path = os.path.join(args.menu_dir, "_fetch_summary_threaded.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已儲存總結 {summary_path}")


if __name__ == "__main__":
    main()