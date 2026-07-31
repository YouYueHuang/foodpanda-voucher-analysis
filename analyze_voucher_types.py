#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析 all_deals_flat.csv：優惠券的「歸屬」與「折扣結構」分類
（不是統計數量多寡，重點是類型）

歸屬分類（誰發的券）：
  - 判斷依據：同一個券碼（voucher_code）在同一國家內橫跨幾家不同的店
  - 只出現在 1 家店         → 店家專屬
  - 出現在 2~50 家店之間     → 小範圍共用（可能是連鎖品牌）
  - 出現在超過 50 家店       → 平台通用（foodpanda 總部發的）

折扣結構分類（怎麼打折）：
  - type=percentage 且有低消門檻 → 滿額打折
  - type=percentage 且無低消門檻 → 直接打折
  - type=amount      且有低消門檻 → 滿額折抵固定金額
  - type=amount      且無低消門檻 → 直接折抵固定金額
  - 其他 type（composite / text_freegift）→ 原樣列出（免運費、贈品等非數字型優惠）

前置處理：
  同一方案有時會被拆成很多組內容完全相同、只有券碼不同的「重複實例」
  （例如寮國同一優惠被拆成 new25~new43 共 18 組碼）。分析前先用
  (國家, vendor_code, description, type, value, min_order_value,
   max_discount, end_date) 當作「方案特徵」去重，避免重複實例灌水統計。

分析結果依「國家」分開呈現與輸出，而不是全部國家合併統計，
因為不同國家的優惠券生態差異很大（例如有些國家 foodpanda 平台親自
發券、有些國家完全是店家自己促銷），合併看會被大國的樣本數蓋掉小國
的特徵。

輸入：all_deals_flat.csv（export_deals_flat.py 產生）
輸出：
  voucher_analysis/
    voucher_classification_all.csv         全部國家明細（含 country 欄位可自行篩選）
    by_country/
      {國家}_classification.csv            該國每張券的完整分類明細
      {國家}_summary.csv                   該國「歸屬 x 結構」交叉表

執行方式：
  python analyze_voucher_types.py
  python analyze_voucher_types.py --input all_deals_flat.csv --output-dir voucher_analysis
"""

import argparse
import os
import sys
import pandas as pd

PLATFORM_THRESHOLD = 50   # 券碼橫跨超過幾家店就視為「平台通用」


def load_and_dedupe(input_path: str) -> pd.DataFrame:
    """讀取 flat CSV，並依「方案特徵」去重，避免同一方案被拆成多組券碼而灌水"""
    deals = pd.read_csv(input_path)
    deals["terms"] = deals["terms"].fillna("")
    deals["description"] = deals["description"].fillna("")
    deals["voucher_code"] = deals["voucher_code"].astype(str)

    sig_cols = ["country", "vendor_code", "description", "type",
               "value", "min_order_value", "max_discount", "end_date"]
    deals["offer_signature"] = deals[sig_cols].astype(str).agg("|".join, axis=1)

    before = len(deals)
    distinct = deals.drop_duplicates(subset=["country", "vendor_code", "offer_signature"]).copy()
    after = len(distinct)
    if before != after:
        print(f"  去重：{before} 筆 → {after} 筆（移除 {before - after} 筆重複實例）")

    return distinct


def classify_scope(distinct: pd.DataFrame) -> pd.DataFrame:
    """依「同一券碼橫跨幾家不同店」判斷歸屬：店家專屬 / 小範圍共用 / 平台通用"""
    code_scope = (distinct.groupby(["country", "voucher_code"])["vendor_code"]
                 .nunique().reset_index(name="n_vendors"))
    distinct = distinct.merge(code_scope, on=["country", "voucher_code"], how="left")

    def _scope(n):
        if n == 1:
            return "店家專屬"
        elif n <= PLATFORM_THRESHOLD:
            return "小範圍共用(可能連鎖店)"
        else:
            return "平台通用"

    distinct["scope"] = distinct["n_vendors"].apply(_scope)
    return distinct


def classify_structure(distinct: pd.DataFrame) -> pd.DataFrame:
    """依「折扣機制 type」x「有無低消門檻」分類折扣結構"""
    distinct["has_min_order"] = distinct["min_order_value"].fillna(0) > 0

    def _structure(row):
        if row["type"] == "percentage":
            return "滿額打折" if row["has_min_order"] else "直接打折"
        elif row["type"] == "amount":
            return "滿額折抵固定金額" if row["has_min_order"] else "直接折抵固定金額"
        else:
            return f"其他({row['type']})"

    distinct["structure"] = distinct.apply(_structure, axis=1)
    return distinct


def print_report(distinct: pd.DataFrame):
    """依國家分開印報告，而不是全部國家合併統計"""
    for country in sorted(distinct["country"].unique()):
        sub = distinct[distinct["country"] == country]
        print("\n" + "=" * 62)
        print(f"  {country}（共 {len(sub)} 張去重後優惠）")
        print("=" * 62)

        print("\n  歸屬分布：")
        scope_pct = (sub["scope"].value_counts(normalize=True) * 100).round(1)
        for k, v in scope_pct.items():
            print(f"    {k:<20s}{v:>5.1f}%")

        print("\n  折扣結構分布：")
        structure_pct = (sub["structure"].value_counts(normalize=True) * 100).round(1)
        for k, v in structure_pct.items():
            print(f"    {k:<20s}{v:>5.1f}%")

        print("\n  歸屬 x 結構：")
        cross = pd.crosstab(sub["scope"], sub["structure"])
        print(cross.to_string().replace("\n", "\n    "))


def main():
    global PLATFORM_THRESHOLD

    p = argparse.ArgumentParser(description="分析優惠券的歸屬（店家/平台）與折扣結構")
    p.add_argument("--input", default="all_deals_flat.csv", help="輸入 CSV（export_deals_flat.py 產生）")
    p.add_argument("--output-dir", default="voucher_analysis", help="輸出資料夾")
    p.add_argument("--platform-threshold", type=int, default=PLATFORM_THRESHOLD,
                   help=f"券碼橫跨超過幾家店視為「平台通用」（預設 {PLATFORM_THRESHOLD}）")
    args = p.parse_args()

    PLATFORM_THRESHOLD = args.platform_threshold

    if not os.path.exists(args.input):
        print(f"❌ 找不到 {args.input}")
        sys.exit(1)

    print("讀取並去重...")
    distinct = load_and_dedupe(args.input)
    distinct = classify_scope(distinct)
    distinct = classify_structure(distinct)

    print_report(distinct)

    os.makedirs(args.output_dir, exist_ok=True)

    # 全部國家的完整明細（方便自己在 Excel 用篩選器切換國家看）
    detail_path = os.path.join(args.output_dir, "voucher_classification_all.csv")
    distinct.to_csv(detail_path, index=False, encoding="utf-8-sig")
    print(f"\n💾 已儲存 {detail_path}（全部國家明細，含 country 欄位可自行篩選）")

    # 每個國家各自獨立輸出：分類明細 + 歸屬/結構摘要
    country_dir = os.path.join(args.output_dir, "by_country")
    os.makedirs(country_dir, exist_ok=True)

    for country in sorted(distinct["country"].unique()):
        sub = distinct[distinct["country"] == country]
        safe_name = country.replace(" ", "_").replace("/", "_")

        detail_c = os.path.join(country_dir, f"{safe_name}_classification.csv")
        sub.to_csv(detail_c, index=False, encoding="utf-8-sig")

        summary_c = os.path.join(country_dir, f"{safe_name}_summary.csv")
        cross = pd.crosstab(sub["scope"], sub["structure"])
        cross.to_csv(summary_c, encoding="utf-8-sig")

        print(f"💾 已儲存 {detail_c}")
        print(f"💾 已儲存 {summary_c}")


if __name__ == "__main__":
    main()