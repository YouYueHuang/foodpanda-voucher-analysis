#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析 menu/ 資料夾裡爬到的菜單資料：
  1. 各國「有優惠 / 沒優惠」的店家數量與比例
  2. 各國「優惠數量」的 histogram（一家店有幾張優惠券的分布）

輸入：menu/{英文國家名}/*.json（foodpanda_menu_batch.py / 
      foodpanda_menu_batch_threaded.py 產生的檔案，每個檔案是一家店，
      結構為 {"vendor": {...}, "deals": [...], "menu": [...]}）

輸出：
  - 終端機印出統計表格
  - deal_summary.csv          各國有無優惠的數量與比例
  - deal_count_histogram.csv  各國優惠數量分布的原始數字（方便自己另外畫圖）
  - deal_histogram.png        視覺化圖表（長條圖：有無優惠比例 + 優惠數量 histogram）

執行方式：
  python analyze_deal_coverage.py
  python analyze_deal_coverage.py --menu-dir menu --output-dir analysis
"""

import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter


def load_vendor_deal_counts(menu_dir):
    """
    掃描 menu/{country}/*.json，回傳 { country: [deal_count, deal_count, ...] }
    每個元素代表一家店的優惠券數量（0 代表沒有優惠）
    """
    result = {}
    country_dirs = sorted(
        d for d in glob.glob(os.path.join(menu_dir, "*"))
        if os.path.isdir(d) and not os.path.basename(d).startswith("_")
    )
    if not country_dirs:
        return result

    for country_dir in country_dirs:
        country = os.path.basename(country_dir)
        json_files = glob.glob(os.path.join(country_dir, "*.json"))
        counts = []
        for path in json_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                deals = data.get("deals", [])
                counts.append(len(deals) if isinstance(deals, list) else 0)
            except Exception as e:
                print(f"  ⚠️  無法讀取 {path}: {e}")
        if counts:
            result[country] = counts
    return result


def compute_summary(country_counts):
    """回傳每國的統計：總數、有優惠數、無優惠數、比例"""
    summary = []
    for country, counts in country_counts.items():
        total = len(counts)
        with_deal = sum(1 for c in counts if c > 0)
        without_deal = total - with_deal
        summary.append({
            "country": country,
            "total_vendors": total,
            "with_deal": with_deal,
            "without_deal": without_deal,
            "with_deal_pct": round(with_deal / total * 100, 1) if total else 0,
            "without_deal_pct": round(without_deal / total * 100, 1) if total else 0,
        })
    summary.sort(key=lambda r: r["total_vendors"], reverse=True)
    return summary


def bucket_deal_counts(counts, max_bucket=5):
    """
    把優惠數量分桶：0, 1, 2, 3, 4, 5+
    回傳 dict，key 為桶名稱（字串），value 為家數
    """
    buckets = Counter()
    for c in counts:
        key = str(c) if c < max_bucket else f"{max_bucket}+"
        buckets[key] += 1
    # 固定順序：0,1,2,3,4,5+
    ordered = {}
    for i in range(max_bucket):
        ordered[str(i)] = buckets.get(str(i), 0)
    ordered[f"{max_bucket}+"] = buckets.get(f"{max_bucket}+", 0)
    return ordered


# ─── 輸出 ────────────────────────────────────────────────────────────────────

def print_summary_table(summary):
    print("\n" + "=" * 70)
    print("  各國「有優惠 / 沒優惠」店家數量與比例")
    print("=" * 70)
    print(f"  {'國家':<15s}{'總店數':>8s}{'有優惠':>10s}{'比例':>8s}"
          f"{'沒優惠':>10s}{'比例':>8s}")
    print("  " + "-" * 66)
    grand_total = grand_with = 0
    for r in summary:
        print(f"  {r['country']:<15s}{r['total_vendors']:>8d}"
              f"{r['with_deal']:>10d}{r['with_deal_pct']:>7.1f}%"
              f"{r['without_deal']:>10d}{r['without_deal_pct']:>7.1f}%")
        grand_total += r["total_vendors"]
        grand_with += r["with_deal"]
    print("  " + "-" * 66)
    if grand_total:
        pct = grand_with / grand_total * 100
        print(f"  {'總計':<15s}{grand_total:>8d}{grand_with:>10d}{pct:>7.1f}%"
              f"{grand_total - grand_with:>10d}{100 - pct:>7.1f}%")


def print_histogram_table(country_counts, max_bucket=5):
    print("\n" + "=" * 70)
    print("  各國「優惠數量」分布（一家店有幾張優惠券）")
    print("=" * 70)
    bucket_labels = [str(i) for i in range(max_bucket)] + [f"{max_bucket}+"]
    header = f"  {'國家':<15s}" + "".join(f"{b:>8s}" for b in bucket_labels)
    print(header)
    print("  " + "-" * (15 + 8 * len(bucket_labels)))
    for country, counts in country_counts.items():
        buckets = bucket_deal_counts(counts, max_bucket)
        row = f"  {country:<15s}" + "".join(f"{buckets[b]:>8d}" for b in bucket_labels)
        print(row)


def save_csv_outputs(summary, country_counts, output_dir, max_bucket=5):
    os.makedirs(output_dir, exist_ok=True)

    # 1) 有無優惠統計
    summary_path = os.path.join(output_dir, "deal_summary.csv")
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "country", "total_vendors", "with_deal", "without_deal",
            "with_deal_pct", "without_deal_pct",
        ])
        w.writeheader()
        w.writerows(summary)
    print(f"\n💾 已儲存 {summary_path}")

    # 2) 優惠數量分布（histogram 原始數字，含每國每桶的家數）
    bucket_labels = [str(i) for i in range(max_bucket)] + [f"{max_bucket}+"]
    hist_path = os.path.join(output_dir, "deal_count_histogram.csv")
    with open(hist_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["country"] + [f"deals={b}" for b in bucket_labels])
        for country, counts in country_counts.items():
            buckets = bucket_deal_counts(counts, max_bucket)
            w.writerow([country] + [buckets[b] for b in bucket_labels])
    print(f"💾 已儲存 {hist_path}")


def save_chart(summary, country_counts, output_dir, max_bucket=5):
    """畫兩張圖：① 各國有無優惠比例（堆疊長條圖） ② 各國優惠數量 histogram"""
    try:
        import matplotlib
        matplotlib.use("Agg")   # 不需要顯示視窗，直接存檔
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n⚠️  未安裝 matplotlib，略過畫圖（統計數字仍已存進 CSV）。")
        print("   安裝方式: pip install matplotlib")
        return

    countries = [r["country"] for r in summary]
    with_pct = [r["with_deal_pct"] for r in summary]
    without_pct = [r["without_deal_pct"] for r in summary]
    bucket_labels = [str(i) for i in range(max_bucket)] + [f"{max_bucket}+"]

    fig, axes = plt.subplots(2, 1, figsize=(max(8, len(countries) * 1.2), 10))

    # ① 有無優惠比例（堆疊長條圖）
    ax1 = axes[0]
    ax1.bar(countries, with_pct, label="Has Deal", color="#4CAF50")
    ax1.bar(countries, without_pct, bottom=with_pct, label="No Deal", color="#E0E0E0")
    ax1.set_ylabel("Percentage (%)")
    ax1.set_title("Vendors With vs Without Deals by Country")
    ax1.legend()
    ax1.set_ylim(0, 100)
    for i, r in enumerate(summary):
        ax1.text(i, r["with_deal_pct"] / 2, f"{r['with_deal_pct']:.0f}%",
                 ha="center", va="center", fontsize=9)

    # ② 優惠數量 histogram（分組長條圖，每國一組）
    ax2 = axes[1]
    x = range(len(bucket_labels))
    width = 0.8 / max(len(countries), 1)
    for i, country in enumerate(countries):
        buckets = bucket_deal_counts(country_counts[country], max_bucket)
        values = [buckets[b] for b in bucket_labels]
        offset = (i - len(countries) / 2) * width + width / 2
        ax2.bar([xi + offset for xi in x], values, width=width, label=country)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([f"{b} deals" for b in bucket_labels])
    ax2.set_ylabel("Number of Vendors")
    ax2.set_title("Deal Count Distribution by Country")
    ax2.legend(fontsize=8)

    fig.tight_layout()
    chart_path = os.path.join(output_dir, "deal_histogram.png")
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)
    print(f"💾 已儲存 {chart_path}")


# ─── 主程式 ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="分析已爬取的菜單資料：優惠覆蓋率與數量分布")
    p.add_argument("--menu-dir",   default="menu",     help="菜單資料來源資料夾（預設 menu）")
    p.add_argument("--output-dir", default="analysis", help="分析結果輸出資料夾（預設 analysis）")
    p.add_argument("--max-bucket", type=int, default=5, help="histogram 最後一桶「N+」的門檻（預設 5）")
    args = p.parse_args()

    print("=" * 70)
    print("  Foodpanda 菜單資料分析 — 優惠覆蓋率 / 優惠數量分布")
    print("=" * 70)

    country_counts = load_vendor_deal_counts(args.menu_dir)
    if not country_counts:
        print(f"❌ 在 {args.menu_dir}/ 找不到任何國家資料夾或 JSON 檔")
        sys.exit(1)

    total_files = sum(len(v) for v in country_counts.values())
    print(f"\n讀取到 {len(country_counts)} 個國家，共 {total_files} 家店的菜單資料")

    summary = compute_summary(country_counts)
    print_summary_table(summary)
    print_histogram_table(country_counts, args.max_bucket)

    save_csv_outputs(summary, country_counts, args.output_dir, args.max_bucket)
    save_chart(summary, country_counts, args.output_dir, args.max_bucket)


if __name__ == "__main__":
    main()