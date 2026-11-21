import pandas as pd
from strategy_lab import load

picks = pd.read_parquet("data/research/daily_picks.parquet")
panel = load().set_index(["ts_code", "trade_date"])
picks["next_date"] = [str(pd.Timestamp(panel.loc[(r.ts_code, r.trade_date)]["f_date1"]).date())
                      for r in picks.itertuples()]
picks.to_parquet("data/research/daily_picks.parquet", index=False)
print(picks.shape, picks[picks["rank"] == 1].shape)
