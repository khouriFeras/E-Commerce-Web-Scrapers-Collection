import pandas as pd 
df = pd.read_excel("results - Copy.xlsx")
df2 = pd.read_excel("bashitihardware.xls")
df_concat = pd.merge(df2, df, on="SKU", how="right")
print(df_concat.head())
df_concat.to_excel("bashitihardware_concat.xlsx", index=False)
