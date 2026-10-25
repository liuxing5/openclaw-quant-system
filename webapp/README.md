# 量化投研一体化平台（webapp）

本地 PostgreSQL 数据底座驱动的股票投研看板：FastAPI 后端 + React/Vite/Tailwind 前端。

## 启动

```bash
# 1) 后端（默认 127.0.0.1:8000）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend

# 2) 前端（默认 127.0.0.1:5173，/api 已代理到 8000）
cd webapp/frontend && npm install && npm run dev
```

- 前端入口：<http://127.0.0.1:5173/dashboard>
- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

## 目录

```
webapp/
├── backend/app/
│   ├── main.py            # FastAPI 入口，挂载 /api/v1/*
│   ├── db.py              # webapp 专用连接池（真·复用，见文件头注释）
│   ├── cache.py           # TTL + stale-while-revalidate 缓存
│   ├── warmup.py          # 后台预热线程（交易时段 60s / 盘后 600s 刷新）
│   ├── routers/           # system / dashboard / market 路由
│   └── services/          # market（全市场聚合，SQL 下推）、calendar（交易日历）
├── scripts/               # 数据补齐与索引优化脚本
└── frontend/src/
    ├── App.jsx            # 50+ 路由
    ├── components/        # Layout / ui / Chart(ECharts 封装)
    ├── lib/               # api / format / nav / toast
    └── pages/             # 各业务页面
```

## 性能约定（重要）

实测本机到远端 PostgreSQL 的链路只有 **约 40~90 KB/s**，而同一条聚合 SQL 在数据库端只要 **2.3 毫秒**。
因此后端铁律：

1. **聚合、排序、分桶、窗口计算全部下推到 SQL**，接口只回传小结果集（总台 overview ≈ 38 KB）；
2. 热点结果走 `ttl_cache`，后台 `warmup` 线程持续重算，用户永远读到的是热缓存；
3. 连接必须复用 —— psycopg2 连接池 `minconn=0` 时每次查询都会关连接重建握手（0.5~3.7s），
   所以 `app/db.py` 自建了 LIFO 复用池。

## 数据口径

- 交易日以 `MAX(daily_quotes.trade_date)` 为准，交易日历落在 `trade_calendar` 小表；
- 涨跌停：优先交易所 `limit_up_price`，缺失时用 `前收 × (1±涨跌幅)` 兜底，ST 按 5%、
  创业板/科创板 20%、北交所 30%、其余 10%（`_FX_CTE` 统一三处口径）；
- 涨跌配色：**涨 = rose（红），跌 = emerald（绿）**。
