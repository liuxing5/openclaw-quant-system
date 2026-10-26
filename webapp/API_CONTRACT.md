# webapp API 契约（`/api/v1/*`）

> 本文件是**前后端 + 多个子任务并行开发的唯一契约**。所有接口由本项目自行定义，
> 原站 `vip.biaogecaijing.com` 的接口只作字段语义参考（见 `spec/pages/*.md`）。

## 通用约定

- 所有路径前缀 `/api/v1`（前端 `src/lib/api.js` 的 `request()` 会自动加）。
- **响应包封**：业务接口一律返回业务体本身（不做 `{code,data}` 外壳）；分页统一为
  `{ "items": [...], "total": 0, "page": 1, "size": 20 }`。
- 时间字段 ISO 字符串；日期字段 `YYYY-MM-DD`（psycopg2 date 会序列化成 `date`，**必须 `str()`**）。
- 金额单位：元；涨跌幅为**百分数数值**（如 `2.35` 表示 +2.35%）。
- 涨=红（rose）、跌=绿（emerald）。涨跌停口径必须走 `services/market.py` 的 `_FX_CTE`。
- **性能铁律**：聚合/排序/分桶/窗口全部下推 SQL；单接口 payload 目标 < 100KB。
- psycopg2 参数用 `%s`；SQL 字符串里字面量 `%` 必须写 `%%`。
- `db.query()` 返回 `list[dict]`，取一行用 `rows[0] if rows else {}`。
- 可写表见 `app/db_schema.py`（启动自动建表）。

## 已有接口（勿改语义）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/dashboard/overview` | 总台七模块聚合 |
| GET | `/dashboard/summary` | 顶栏状态条轮询 |
| GET/PUT | `/dashboard/global-schedule` | 全局交易时段设置 |
| GET | `/market-data/summary` | 指数 + 快照 |
| GET | `/market-data/breadth` | 涨跌分布直方图 |
| GET | `/market-data/rankings` | 榜单（`sort`/`limit`/`market`/`security_type`） |
| GET | `/market-data/quotes/daily` | 日线（`symbol`/`limit`） |
| GET | `/market-data/securities/search` | 代码/名称搜索 |
| GET | `/limit-ladder` | 连板梯队（`min_level`/`detail`） |
| GET | `/boards/concepts` `/boards/industries` | 板块列表（`page`/`size`/`sort`） |
| GET | `/boards/{kind}/{code}` | 板块详情+历史（`days`） |
| GET | `/health` `/settings/public` `/auth/me` | 系统 |
| GET | `/system/data-sources` | 数据源状态 |

---

## A. 实时层（BE-realtime）

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/market-data/realtime` | `codes=600519.SH,000001.SZ`（可空=自选+持仓） | `{"as_of": "...", "trading": true, "items":[{"ts_code","name","price","pct_chg","open","high","low","pre_close","volume","amount","turnover_rate","market_cap","status"}]}` |
| GET | `/market-data/realtime/batch` | `codes=...` 同上，专供批量行情轮询 | 同上 |
| GET | `/market-data/realtime/status` | - | `{"source":"sina|tencent|akshare","trading":bool,"next_refresh_sec":n,"cached":n,"as_of":...}` |

- 交易时段 10s 缓存，非交易时段 600s；失败降级到 `daily_quotes` 当日值。
- 数据源优先级：新浪 `hq.sinajs.cn` → 腾讯 `qt.gtimg.cn` → akshare `stock_zh_a_spot_em`。
- 必须在后台线程刷新，**请求路径上不得阻塞超过 300ms**（拿不到就返回缓存/降级）。

## B. 可操作闭环

### B1 自选 / 持仓 / 提醒（BE-portfolio）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/watchlists` | 分组列表 `{"items":[{id,name,sort,item_count,is_shared}]}` |
| POST | `/watchlists` | `{name}` 建分组 |
| GET | `/watchlists/default` | 默认分组（无则自动建「默认分组」） |
| PATCH/DELETE | `/watchlists/{id}` | 改名 / 删除 |
| GET | `/watchlists/{id}/items` | 分组内标的 |
| POST | `/watchlists/{id}/items` | `{ts_code, stock_name?}`（重复则 200 幂等） |
| DELETE | `/watchlists/{id}/items/{item_id}` | 移除 |
| GET | `/watchlists/{id}/quotes?page&size&refresh` | 行情列（实时优先，降级日线），含 `as_of`、`meta` |
| GET/PUT | `/watchlists/holding-alert-settings` | `{"enabled": bool}` |
| GET | `/holdings?page&size` | 持仓列表，含 `price/pct_chg/market_value/pnl/pnl_pct` |
| POST | `/holdings` | `{ts_code, qty, cost, path?, entry_date?, notes?}` |
| PATCH/DELETE | `/holdings/{id}` | 改 / 删 |
| GET | `/holdings/summary` | `{total_value,total_cost,total_pnl,pnl_pct,today_pnl,items:n,win_count,lose_count}` |
| GET | `/alerts?page&size&type` | 提醒列表 |
| POST | `/alerts` | `{ts_code, alert_type, threshold?, direction?}` |
| PATCH/DELETE | `/alerts/{id}` | 启停 / 删除 |
| GET | `/alerts/triggered?limit` | 最近触发记录 |

`alert_type` 枚举：`price_above` `price_below` `pct_chg_above` `pct_chg_below` `limit_up` `limit_down` `break_cost` `amount_above`。

### B2 模拟盘（BE-simulation）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/simulation/accounts` | 账户列表 |
| POST | `/simulation/accounts` | `{name?, initial_cash?}` |
| GET | `/simulation/summary?account_id` | `{cash,equity,total_value,total_pnl,pnl_pct,today_pnl,positions,win_rate,roi}` |
| GET | `/simulation/positions?account_id` | 持仓（含实时价、浮盈） |
| GET | `/simulation/orders?account_id&page&size` | 委托历史 |
| POST | `/simulation/orders` | 下单 `{account_id?, ts_code, side:"buy"\|"sell", qty, order_type:"market"\|"limit", price?}` → 撮合成交，返回 `{order, position, account}` |
| DELETE | `/simulation/orders/{id}` | 撤单（仅 pending） |
| POST | `/simulation/reset` | `{account_id}` 清仓重置 |

撮合规则：市价单按最新价（实时→日线 close）成交；佣金 `max(5, amount*0.0003)`；印花税卖出 `amount*0.0005`；过户费 `amount*0.00002`；T+1 不限制但卖出不得超持仓；买入最小 100 股（整手）。

### B3 反馈 / 系统设置写（BE-review）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/feedbacks/my?page&size` | 我的反馈 |
| POST | `/feedbacks` | `{category, content, contact?}` |
| PATCH | `/feedbacks/{id}` | 撤回/删除（本人） |
| GET | `/settings/all` | 管理端全量设置 |
| PUT | `/settings/all` | 批量写 `{key: value}` |

---

## C. 策略与算法

### C1 策略（BE-strategy）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/strategies` | 策略定义 + 最近一次运行摘要 `{"items":[{code,name,family,description,params,enabled,last_run:{...},best_hit_rate}]}` |
| POST | `/strategies` | 新建 `{code,name,family,description,params}` |
| PATCH/DELETE | `/strategies/{code}` | 改 / 删 |
| GET | `/strategies/run-stats` | `{total_strategies, runs_30d, total_hits_30d, avg_elapsed_ms, last_run_at, by_family:[...]}` |
| POST | `/strategies/{code}/run` | 立即跑批 → `{run_id, elapsed_ms, total_scanned, total_passed, hits:[...]}` |
| GET | `/strategies/{code}/runs?limit` | 历史运行 |
| GET | `/strategies/{code}/hits?run_id&size` | 该次命中明细（含 `ts_code,name,price,pct_chg,reasons[]`） |
| GET | `/strategies/scans?strategy&days` | 读 `strategy_scans` 历史（filter_stats/sentiment/mood） |

策略实现放 `services/strategy.py`，**纯 SQL 扫描**，内置至少 6 个可跑策略：
`limit_up_breakout`（涨停突破）、`trend_pullback`（趋势回踩）、`volume_surge`（放量异动）、
`high_turnover`（高换手活跃）、`board_leader`（板块龙头联动）、`northbound_inflow`（北向增持）。

### C2 复盘 / 情绪历史 / 异动（BE-review）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/reviews?page&size` | 复盘列表 `{"items":[{trade_date,title,score,mood,summary}]}` |
| GET | `/reviews/{date}?region=cn` | 单日详情，无则 404（前端自动触发生成） |
| POST | `/reviews/{date}/generate` | 由当日 `daily_quotes`/`lhb_detail`/板块数据**自动撰写**复盘 JSON |
| GET | `/sentiment/history?days=180` | 情绪历史序列 `{"items":[{trade_date,score,mood}]}` |
| GET | `/anomaly/events?page&size&scope&all_market` | 实时异动事件（基于当日行情+快照计算） |
| POST | `/anomaly/events/scan` | 立即扫描，返回新增事件数 |
| GET | `/anomaly/rules?page&size` | 异动规则（含内置种子规则） |
| POST/PATCH/DELETE | `/anomaly/rules[/{id}]` | 增改删 |

复盘 `content` 结构：`{title, mood, score, summary, sections:[{heading, bullets:[...]}], metrics:{...}, leaders:{...}}`。

### C3 实时异动事件（BE-review 实现）

`kind` 枚举：`rapid_rise`（急拉）、`volume_spike`（放量）、`limit_up`（涨停）、
`limit_down`（跌停）、`turnover_high`（高换手）、`new_high`（创阶段新高）、`board_surge`（板块大涨）。
响应项：`{id, ts_code, name, kind, price, pct_chg, detail, occurred_at, scope}`。

---

## D. 页面配套读接口

### D1 资讯/内容（BE-content）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/academy/articles` | 学院文章 `{"items":[{id,title,category,reading_minutes,summary,sections:[{heading,paras[]}]}]}` |
| GET | `/info/news-flash?source=cls&page&size` | 快讯（读 `raw_signals`） |
| GET | `/info/news-radar?limit` | 雷达：近 N 小时信号按小时聚合 |
| GET | `/info/hot-search?platform&limit` | 热搜（读 `extracted_recommendations`/`raw_signals` 派生，缺源时返回内置榜单） |
| GET | `/info/fund-flow/sectors?sector_type` | 行业/概念资金流（读 `*_board_quotes.net_inflow`） |
| GET | `/global-indexes` | 外围指数（akshare `index_global` 缓存 + DB 降级） |
| GET | `/commodity/quotes` | 油/金/铜等商品（akshare 缓存 + 内置降级） |
| GET | `/merger-restructuring` | 并购重组（读 `stock_announcements` category=并购重组 + `raw_signals`） |
| GET | `/dragon-tiger?page&size&date` | 龙虎榜（读 `lhb_detail`，按席位/营业部分组） |

### D2 指数 / 财务 / 环境 / 数据（BE-finance）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/index/summary` | 指数总览（`index_basic` + `index_quotes`，6 大指数） |
| GET | `/index/quotes/{ts_code}?days` | 指数日线 |
| GET | `/index/components/{ts_code}?page&size` | 指数成分（用 `total_market_cap` 前 N 近似 + 说明） |
| GET | `/finance/statements?ts_code&limit` | 财报（`stock_fundamentals` 利润/资产负债/现金流） |
| GET | `/finance/indicators?ts_code` | 指标（毛利率、ROE、EPS、负债率、成长性等） |
| GET | `/market-env` | 市场环境（指数趋势 + 广度 + 情绪 + 资金，一屏） |
| GET | `/market-env/history?days=250` | 环境历史（广度/情绪/成交额序列） |
| GET | `/data/quotes?page&size&market&sort&keyword` | 全市场行情表（服务端分页排序） |
| GET | `/data/fundamentals?page&size&sort&keyword` | 全市场基本面表 |
| GET | `/market-data/securities/{code}` | 个股档案 `{ts_code,name,market,list_date,is_st,industry,latest:{...}}` |

### D3 系统/工作台/权限（BE-admin）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/permissions/menus` | 当前用户可见菜单（本系统全量放行） |
| GET | `/system/users?page&size&keyword` | 用户列表（读 `auth.users` 脱敏） |
| GET | `/system/permissions` | 角色-权限矩阵 |
| GET | `/system/audit-logs?page&size` | 审计日志（读 `auth.audit_log_entries` + 本系统操作日志） |
| GET | `/workbench/monitor` | 运行监控（DB 池、缓存、数据源、定时任务、接口耗时） |
| GET | `/workbench/students?page&size` | 学员/成员列表 |
| GET | `/workbench/payments?page&size` | 订单列表 |
| GET | `/workbench/feedbacks?page&size` | 全部反馈 + `PATCH` 回复 |
| GET | `/profile` | 个人资料（`/auth/me` 扩展） |

---

## 前端约定

- 页面文件：`webapp/frontend/src/pages/XxxPage.jsx`；路由注册 `src/App.jsx`；菜单 `src/lib/nav.js`。
- 数据：`useApi(path, {poll})`（`src/lib/api.js`）。写操作用 `api.post/patch/put/del`。
- 样式：Tailwind + 深色玻璃拟态（`bg-white/5 backdrop-blur border border-white/10 rounded-xl`）。
- 涨跌色：`text-rose-400` 涨 / `text-emerald-400` 跌（`src/lib/format.js` 有 `pctClass`）。
- 图表：`src/components/Chart.jsx` 的 `<Chart option={...}/>` 与 `<Sparkline data={...}/>`。
- 页面规格：`spec/pages/<路由>.md`（原站渲染正文 + 接口响应），照其**文案与信息结构**复刻。
