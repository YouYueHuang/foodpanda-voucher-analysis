import pandas as pd

# 1. 讀取 CSV 檔案
input_file = 'foodpanda_city_vendor_counts.csv'
df = pd.read_csv(input_file)

# 2. 確保 vendor_count 欄位轉為數值型態 (如有缺失值或非數字會自動轉為 NaN)
df['vendor_count'] = pd.to_numeric(df['vendor_count'], errors='coerce')

# 3. 依據「國家」分組，找出該國家中 vendor_count 最大的資料行索引 (index)
idx_max = df.groupby('國家')['vendor_count'].idxmax()

# 4. 篩選出最大值的行，並只保留要求的 4 個欄位
result_df = df.loc[idx_max, ['國家', '城市名', 'city_id', 'vendor_count']].reset_index(drop=True)

# 5. 輸出為新的 CSV 檔案 (使用 utf-8-sig 以避免中文亂碼)
output_file = 'top_vendor_city_per_country.csv'
result_df.to_csv(output_file, index=False, encoding='utf-8-sig')

print("處理完成，結果如下：")
print(result_df)