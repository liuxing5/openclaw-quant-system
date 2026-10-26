# 量化投研一体化平台（webapp）

本地 PostgreSQL 数据底座驱动的股票投研看板：FastAPI 后端 + React/Vite/Tailwind 前端。

## 启动

```bash
# 1) 后端（默认 127.0.0.1:8000）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend

# 2) 前端开发服务器（默认 127.0.0.1:5173，/api 已代理到 8000）
cd webapp/frontend && npm install && npm run dev

# 3) 生产构建（构建后后端会直接托管 dist，只需 8000 一个端口）
cd webapp/frontend && npm run build
```

- **生产访问**：<http://127.0.0.1:8000/dashboard>（后端 `main.py` 已托管 `frontend/dist`，SPA 路由全部回退到 `index.html`）
- 前端开发访问：<http://127.0.0.1:5173/dashboard>
- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

> 数据库连接池默认 `WEBAPP_DB_POOL=8`；若 Supabase 同时跑其他任务，建议
> `WEBAPP_DB_POOL=4`。webapp 会自动把 Supabase 的 `:5432`（session 模式，全项目 15 个客户端）
> 切到 `:6543`（transaction 模式），避免 `EMAXCONNSESSION`。

## 目录

```
webapp/
├── API_CONTRACT.md        # ★ 唯一接口契约（前后端 + 并行开发的唯一真相源）
├── backend/app/
│   ├── main.py            # FastAPI 入口，挂载全部 /api/v1/* 路由 + 启动建表/预热
│   ├── db.py              # webapp 专用连接池（真·复用，见文件头注释）
│   ├── db_schema.py       # 17 张可写业务表（自选/持仓/提醒/模拟盘/策略/复盘…）
│   ├── cache.py           # TTL + stale-while-revalidate 缓存
│   ├── warmup.py          # 后台预热线程（交易时段 60s / 盘后 600s 刷新）
│   ├── routers/           # 13 个领域路由（见下表）
│   └── services/          # market / realtime / … 领域服务
├── scripts/
│   └── smoke.py           # ★ 全站接口冒烟验收
└── frontend/src/
    ├── App.jsx            # 53 条路由（已全量注册）
    ├── components/        # Layout / ui / Chart(ECharts 封装)
    ├── lib/               # api / format / nav / toast
    └── pages/             # 50+ 业务页面
```

## 路由分工

| 路由文件 | 领域 | 典型端点 |
| --- | --- | --- |
| `system.py` | 系统 | `/health` `/settings/public` `/auth/me` `/system/data-sources` |
| `dashboard.py` | 总台 | `/dashboard/overview` `/dashboard/summary` |
| `market.py` | 行情 | `/market-data/*` `/limit-ladder` `/boards/*` |
| `realtime.py` | 实时层 | `/market-data/realtime[+/batch+/status]` |
| `portfolio.py` | 自选/持仓/提醒 | `/watchlists/*` `/holdings/*` `/alerts/*` |
| `simulation.py` | 模拟盘 | `/simulation/{accounts,summary,positions,orders,reset}` |
| `strategy.py` | 选股策略 | `/strategies/*`（6 个可跑策略 + 跑批统计） |
| `review.py` | 复盘/情绪 | `/reviews/*` `/sentiment/history` |
| `anomaly.py` | 实时异动 | `/anomaly/events` `/anomaly/rules` |
| `feedback.py` | 反馈 | `/feedbacks/*` |
| `content.py` | 资讯/内容 | `/academy/*` `/info/*` `/global-indexes` `/commodity/*` `/dragon-tiger` |
| `finance.py` | 指数/财务/环境/数据 | `/index/*` `/finance/*` `/market-env*` `/data/*` |
| `admin.py` | 管理/工作台 | `/system/users` `/workbench/*` `/profile` `/settings/all` |

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

## 实时行情取数

`app/services/realtime.py` 分四级降级，任何一级失败都往下走，**永不抛异常**：

```
新浪 hq.sinajs.cn  →  腾讯 qt.gtimg.cn  →  东财 push2  →  本地 daily_quotes 收盘价
      10s 缓存（交易时段）/ 600s（非交易时段），请求路径只读缓存不做网络 I-O
```

接口只回传小结果集，实时值覆盖在日线底表之上，因此换手率、市值、涨跌停价等字段
即使在网络源不可用时也完整。

前端刷新节奏：

| 页面 | 周期 | 说明 |
| --- | --- | --- |
| 总台实时行情条 | **10s** | 六大指数实时价，直接展示「现在」而非上一交易日收盘 |
| 自选行情 / 模拟盘持仓 | 30s | `/market-data/realtime`，失败降级日线 |
| 实时异动 / 价格提醒 | 30~60s | 事件流与触发记录 |
| 总台 / 板块 / 环境 | 60~120s | 后端 warmup 同时刷新缓存 |

日线底表是 T-1 收盘；盘中要看实时变化，以实时行情条与自选行情为准。

## 盘中实时总台（T+0）

日线表 `daily_quotes` 最新只到上一交易日；为了让「每 60 秒刷新」真的刷新出当日数据，
`services/live_market.py` 用腾讯全市场快照（5224 只、20 秒刷新）重算：

- `/dashboard/overview` `/dashboard/summary`：指数 / 广度 / 涨跌停 / 情绪六维 / 连板梯队 / 四榜单 / 板块热度
- `/market-data/summary|breadth|rankings`：同一口径
- `/limit-ladder`：今日涨停 + 截至上一交易日的连板高度
- `/market-env`、`/index/summary`：指数与广度全部实时覆盖
- `/boards/concepts|industries`：东财实时板块榜；源断连时自动回退本地板块表

页面每 60 秒轮询一次；`/dashboard/overview?refresh=1` 会强制拉一张新快照（约 3~7 秒）。
快照不可用时 `meta.live=false`，自动回退到日线口径，接口形状不变。

## 个股分析增强

`/stock-analysis/:symbol` 已对齐原站细节：

- 实时价 10 秒刷新，今日实时价追加为 K 线最后一根；
- MA5/10/20/60、压力支撑 / 枢轴点 / 前高前低 / 布林带 四个叠加 Tab；
- R1/P/S1、20 日区间卡片，涨跌幅相对现价计算；
- 「点位提醒」直接创建 `/alerts` 规则；
- **AI 量价推演**：`POST /api/v1/stock-analysis/{code}/projection`，由本地量化引擎
  按均线/量价/枢轴推演目标价、概率、路径、触发条件与止损（不依赖外部 LLM）。

## 实时异动表

`/anomaly` 改为原站风格的全市场信号表：

- 板块（主板/创业板/科创板/北交所/ST/板块）、信号（涨停/炸板/跌停/急拉/急跌/量能/换手/新高/板块大涨）、
  方向、时段（竞价/盘中/偏移）、只看持仓 五组筛选；
- 事件由腾讯快照推导，含量比、振幅、换手、成交时间；
- 30 秒轮询 + 立即扫描（强制刷新快照）。

## 连板 / 策略

- `/limit-ladder` 支持 **涨停 / 跌停 / 炸板 / 断板** 四种模式 + 板块筛选；
- `/strategy` 支持创建自定义策略：选择内置扫描器作模板（AI 生成只做关键字到模板的规则映射），
  创建后可直接运行（`params.base_code` 绑定扫描器）。

## 情绪历史回填

`app_sentiment_history` 需要的历史序列由离线脚本生成（单日约 3~6 秒，禁止放请求路径）：

```bash
python webapp/scripts/backfill_sentiment.py --days 180   # 幂等，缺哪天补哪天
```

## 财务数据回填

`stock_fundamentals` 由东财「按报告期全市场」接口批量回填：一期报告 4 张表 × 约 12 页，
比逐只股票请求快两个数量级。当前已回填 **8 个季度、5240 只、37,656 行**（600519 有 7 期）。

```bash
python webapp/scripts/backfill_fundamentals.py --quarters 8 --prune-stale --prune-unknown
```

- 数据：利润表（营收/归母净利/扣非/营业成本）、资产负债表（总资产/负债/净资产/负债率）、
  现金流量表（经营现金流）、业绩报表（EPS/BPS/ROE/同比/行业）；
- 派生：毛利率、净利率、ROE（净利润 / 净资产）在服务层计算；
- 幂等：主键 `(ts_code, report_date)`，重复执行只覆盖不重复插入；
- `--prune-stale` 清掉被新季度覆盖的远古行，`--prune-unknown` 清掉非 A 股代码。

## 实时异动

`/anomaly/events` 默认使用**腾讯全市场实时快照**（`services/live_snapshot.py`）：

```
stock_basic_info 5240 只  →  腾讯 qt.gtimg.cn 400 只/批 × 4 线程  →  约 3 秒拿全市场
后台线程每 20 秒刷新一次；请求路径只读内存快照，绝不等待外部行情源
```

- 7 类事件：涨停 / 跌停 / 急拉 / 放量 / 高换手 / 60 日新高 / 板块大涨；
- 事件时间取该标的**最近一笔成交时间**（`tick_snapshot`），不是合成回放；
- 行情源失败或快照不足 1000 只时自动退回日线快照（`meta.live=false` 标注）。

## 验收

```bash
# 后端起来后
python webapp/scripts/smoke.py                 # 只读端点全量冒烟
python webapp/scripts/smoke.py --with-writes   # 附带写接口（自带清理，不留脏数据）
python webapp/scripts/smoke.py --json out.json # 结果落盘
```

退出码 0 表示全通过；>3s 标 SLOW、>200KB 标 BIG、失败清单会逐条打印。
前端构建验证用独立 outDir，避免并行开发时互相覆盖：`vite build --outDir dist-check`。

## 并行开发约定（重要）

- **共享文件只由主控修改**：`main.py` / `db.py` / `db_schema.py` / `cache.py` / `warmup.py` /
  `frontend/src/App.jsx` / `nav.js` / `components/*` / `lib/*`；
- 每个开发单元只碰自己的 `routers/<领域>.py`、`services/<领域>.py`、`pages/<页面>.jsx`；
- 路由已在 `main.py` 全量挂载，新增端点只需往对应 router 里加 `@router.get(...)`；
- 页面规格以 `spec/pages/*.md`（原站渲染正文 + 真实接口响应）为准，**只还原信息结构与文案，不复制原站代码**。
