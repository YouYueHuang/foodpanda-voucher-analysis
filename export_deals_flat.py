#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 menu/ 資料夾裡所有店家的優惠券「攤平」成單一 CSV，方便一次性分析／上傳。

輸入：menu/{英文國家名}/*.json
輸出：
  - all_deals_flat.csv     每一列 = 一張優惠券（沒優惠的店不會出現在這裡）
  - all_vendors_flat.csv   每一列 = 一家店（含 deal_count，沒優惠的店 deal_count=0）

執行方式：
  python export_deals_flat.py
  python export_deals_flat.py --menu-dir menu --output-dir .
"""

import argparse
import csv
import glob
import json
import os
import sys


def main():
    p = argparse.ArgumentParser(description="攤平 menu/ 資料夾的優惠券資料成單一 CSV")
    p.add_argument("--menu-dir",   default="menu", help="菜單資料來源資料夾（預設 menu）")
    p.add_argument("--output-dir", default=".",    help="輸出資料夾（預設當前目錄）")
    args = p.parse_args()

    country_dirs = sorted(
        d for d in glob.glob(os.path.join(args.menu_dir, "*"))
        if os.path.isdir(d) and not os.path.basename(d).startswith("_")
    )
    if not country_dirs:
        print(f"❌ 在 {args.menu_dir}/ 找不到任何國家資料夾")
        sys.exit(1)

    deal_rows = []
    vendor_rows = []

    for country_dir in country_dirs:
        country = os.path.basename(country_dir)
        json_files = glob.glob(os.path.join(country_dir, "*.json"))
        print(f"讀取 {country}: {len(json_files)} 個檔案 ...")

        for path in json_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"  ⚠️  無法讀取 {path}: {e}")
                continue

            vendor = data.get("vendor", {})
            vcode = vendor.get("code") or os.path.splitext(os.path.basename(path))[0]
            deals = data.get("deals", []) or []

            vendor_rows.append({
                "country":       country,
                "vendor_code":   vcode,
                "vendor_name":   vendor.get("name"),
                "rating":        vendor.get("rating"),
                "deal_count":    len(deals),
            })

            for d in deals:
                deal_rows.append({
                    "country":         country,
                    "vendor_code":     vcode,
                    "vendor_name":     vendor.get("name"),
                    "description":     d.get("description"),
                    "type":            d.get("type"),
                    "value":           d.get("value"),
                    "voucher_code":    d.get("voucher_code"),
                    "min_order_value": d.get("min_order_value"),
                    "max_discount":    d.get("max_discount"),
                    "end_date":        d.get("end_date"),
                    "is_new_customer": d.get("is_new_customer"),
                    "terms":           " | ".join(d.get("terms") or []),
                })

    # 存 CSV
    os.makedirs(args.output_dir, exist_ok=True)

    deals_path = os.path.join(args.output_dir, "all_deals_flat.csv")
    with open(deals_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "country", "vendor_code", "vendor_name", "description", "type", "value",
            "voucher_code", "min_order_value", "max_discount", "end_date",
            "is_new_customer", "terms",
        ])
        w.writeheader()
        w.writerows(deal_rows)

    vendors_path = os.path.join(args.output_dir, "all_vendors_flat.csv")
    with open(vendors_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "country", "vendor_code", "vendor_name", "rating", "deal_count",
        ])
        w.writeheader()
        w.writerows(vendor_rows)

    print(f"\n✅ 共 {len(vendor_rows)} 家店、{len(deal_rows)} 張優惠券")
    print(f"💾 已儲存 {deals_path}")
    print(f"💾 已儲存 {vendors_path}")
    print(f"\n把這兩個檔案上傳給 Claude 即可進行大量分析。")


if __name__ == "__main__":
    main()