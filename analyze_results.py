import pandas as pd

df = pd.read_csv('recommend/recommend2/results/scan_20260424.csv')

print(f'总扫描数量：{len(df)}')
print(f'最高得分：{df["score"].max()}')
print(f'得分≥8 的数量：{(df["score"]>=8).sum()}')
print(f'得分≥6 的数量：{(df["score"]>=6).sum()}')
print(f'得分≥4 的数量：{(df["score"]>=4).sum()}')
print('\n得分分布:')
print(df['score'].value_counts().sort_index())

print('\n得分最高的前 10 只股票:')
top10 = df.nlargest(10, 'score')[['symbol', 'name', 'score', 'last_close']]
print(top10.to_string(index=False))
