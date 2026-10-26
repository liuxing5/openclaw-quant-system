# -*- coding: utf-8 -*-
"""D1 资讯 / 内容 / 外围行情 服务层。

为什么单独抽一层（而不是把 SQL 写在路由里）
-------------------------------------------
1. 本模块的端点横跨三类数据源：
   * **本地 PostgreSQL**（raw_signals / extracted_recommendations / *_board_quotes /
     lhb_detail / stock_announcements）——聚合必须下推 SQL，回传行数要小；
   * **外部免费 HTTP 源**（东财 push2、新浪 hq.sinajs.cn）——慢、会挂、会被封，
     必须带超时 + TTL 缓存 + 结构完整的降级兜底，**任何情况下不得把异常抛给前端**；
   * **纯静态内容**（学院文章）——正文就放在本文件的模块级常量里，零 IO。
   三者的降级策略完全不同，混在路由里会让「谁可能失败」这件事不可读。

2. 缓存口径（对应任务硬性规则）：
   * 资讯类本地查询 → 300s TTL；
   * 外部行情源 → **成功 600s**，失败 60s（失败也缓存，否则源挂掉时每个请求都要
     干等一次网络超时；但只缓存 60s，便于网络恢复后尽快自愈）。

3. 时间口径：本机数据最新交易日为 `MAX(daily_quotes.trade_date)`，快讯 / 推荐类
   表本身带 pub_time，因此**一切「近 N 小时 / 近 N 天」都以表内 MAX(pub_time) 为锚点**
   而不是 now()——否则周末（今天 2026-10-25 周日）去查「近 24 小时」永远是空的。

参数占位符约定（psycopg2）：SQL 字面量里的 `%` 写 `%%`；**作为参数值传入的 LIKE
模式写单个 `%`**（实测：参数值不会被二次模板化）。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta

from ..cache import cache_get, cache_set, ttl_cache
from ..db import query, scalar

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _f(v, default=None):
    """Decimal / str -> float，失败给默认值（JSON 可序列化铁律）。"""
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _iso(v):
    """date / datetime -> ISO 字符串。"""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _rows(sql: str, params=()) -> list[dict]:
    """query() 的便捷包装：RealDictRow -> 纯 dict（确保 json 可序列化）。"""
    return [dict(r) for r in query(sql, params)]


def _like(value: str) -> str:
    """把用户输入转成安全的 LIKE 模式（配合 SQL 的 ESCAPE '\\' 使用）。"""
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").strip()
    )


def _meta(**kw) -> dict:
    return kw


# ---------------------------------------------------------------------------
# 一、投资学院文章（**原创正文**，纯静态，零外部依赖）
#    结构照 spec/pages/academy-basics.md、academy-books.md 的栏目与标题风格：
#    每篇 = 若干小节，每小节 = 若干段落；正文 400~900 字，必须有真实信息量。
# ---------------------------------------------------------------------------

ACADEMY_ARTICLES: list[dict] = [
    {
        "id": "call-auction",
        "category": "盘面基础",
        "category_key": "basics",
        "level": "入门",
        "reading_minutes": 6,
        "title": "集合竞价：开盘价是怎样撮合出来的",
        "summary": "看懂 9:15—9:25 的申报与撤单窗口、虚拟成交价与匹配量，判断高开低开背后真实的委托结构。",
        "tags": ["开盘", "撮合", "盘口"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "三个时间窗，信息价值完全不同",
                "paras": [
                    "A股连续竞价开始前，交易所先在 9:15—9:25 集中收集买卖申报，按“价格优先、时间优先”并以能实现最大成交量的原则，撮合出一个统一的开盘价。9:15—9:20 既可以申报也可以撤单，用一笔大单试盘几乎没有成本，所以这段时间的买一卖一变化参考价值有限；9:20—9:25 通常只能申报不能撤单，委托一旦挂出就是真实意图，这段的委托结构最值得看；9:25 集中撮合产生开盘价，9:25—9:30 只接受申报、不撮合，是盘前的空档期。",
                    "看竞价不要只看“高开几个点”。更有效的三个比较是：匹配价相对昨收的位置、匹配量相对近 5 日同时段均量的倍数、以及未匹配量的方向。匹配量明显放大而匹配价稳步上移，说明有一致性买盘在抢筹；匹配量很小却高开很多，往往是少量单子打出来的虚假强度。",
                ],
            },
            {
                "heading": "开盘后 5 分钟才是验证期",
                "paras": [
                    "竞价强不等于全天强。开盘后价格若跌回匹配价下方且成交量放大，通常意味着竞价阶段的买盘缺乏后续承接；低开后迅速收复竞价区间并站稳均价线，则可能是承接盘在吸纳。把竞价当“预告”，把开盘后 5—15 分钟当“确认”，才不会被一笔随时可撤的单子骗进场。",
                    "常见误区：把 9:15 的大买单当成主力表态；只看高开幅度不看承接；用单一竞价量下结论（必须结合流通盘与近期均量）；开盘直接追价。竞价之前先写好支撑位、压力位与失效条件，盘中只做验证，不要临时改计划。",
                ],
            },
        ],
    },
    {
        "id": "intraday-chart",
        "category": "盘面基础",
        "category_key": "basics",
        "level": "入门",
        "reading_minutes": 7,
        "title": "如何看分时图：价格线、均价线与承接",
        "summary": "从价格线、均价线、成交量柱与指数叠加四个维度，判断日内强弱和资金承接，而不是猜下一分钟的涨跌。",
        "tags": ["分时", "均价线", "承接"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "三条线各自在说什么",
                "paras": [
                    "白色价格线是逐笔成交价；黄色均价线是当日成交额除以成交量得到的加权均价，代表到目前为止进场者的平均成本；下方量柱是每个时间单位的成交活跃度。价格在均价线上方回踩不破，说明当天买入者平均仍盈利、抛压较轻；价格反复在均价线下方反抽受阻，说明均价线已经从支撑变成压力。",
                    "分时强弱必须和指数对比：个股横盘而指数下挫，属于相对强势；个股与指数同步跳水，多半只是 β 波动。把个股分时与所属板块指数叠在一起看，能分辨这是“板块行情”还是“个股独立行情”，二者的持续性差别很大。",
                ],
            },
            {
                "heading": "量价配合的三个观察点",
                "paras": [
                    "第一看拉升时量柱是否同步放大，第二看回落时量柱是否明显收缩，第三看均价线斜率是否稳定。缩量回调、均价线走平通常是良性结构；放量下穿均价线而反抽无量，说明承接资金在撤退。",
                    "均价线也常被用来做日内回转：价格偏离均价线过多时回归概率上升，但只有在趋势明确、板块联动清晰的前提下才值得做；震荡市里机械做 T，很容易把底仓在半山腰做丢。",
                    "分时的局限也要记住：它不能预测每一分钟；单根脉冲可能是消息刺激或资金对倒，必须看是否有持续成交；尾盘急拉未必是好事，它可能透支次日的上行空间。",
                ],
            },
        ],
    },
    {
        "id": "volume-amount-turnover",
        "category": "盘面基础",
        "category_key": "basics",
        "level": "入门",
        "reading_minutes": 6,
        "title": "成交量、成交额与换手率：三个最容易混的口径",
        "summary": "量是股数、额是金额、换手率是相对流通盘的比例，三者不可混用；先把口径分清，横向比较才有意义。",
        "tags": ["成交量", "换手率", "口径"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "定义先分清",
                "paras": [
                    "成交量是成交的股数（A股一手为 100 股，行情软件常以“手”显示，1 手 = 100 股）；成交额是成交的金额，单位是元；换手率 = 成交量 ÷ 流通股本 × 100%。三者混用会直接得出错误结论：同样“放量一倍”，流通盘 10 亿股和 1 亿股的股票，代表的资金强度完全不同。",
                    "直接比成交量之所以不可靠，是因为股本差异太大：大盘股一天成交几万手就已是天量，小盘股十几万手可能只是常态。换手率把规模归一化之后，个股之间、板块之间才可以横向比较。",
                ],
            },
            {
                "heading": "实战中的经验区间",
                "paras": [
                    "换手率的经验分档：低于 1% 基本无人问津，属于冷清；3%—7% 为活跃区间，资金参与度尚可；非新股、非解禁日出现 10% 以上的换手，意味着筹码在剧烈换手——出现在高位通常是分歧加剧，出现在低位突破位更可能是新资金进场。",
                    "成交额常用来判断市场整体温度：两市成交额连续放大而指数滞涨，说明抛压同步增加；缩量阴跌到地量、随后放量转阳，往往比单纯的价格止跌更可信。",
                    "常见失真场景要剔除：长期停牌复牌日、除权除息日、限售解禁日的换手率会异常放大；科创板与北交所流通盘偏小，换手率天然更高；ST 股的换手也要单独看，不能与正常标的并列排序。",
                ],
            },
        ],
    },
    {
        "id": "volume-price",
        "category": "技术与量价",
        "category_key": "volume-price",
        "level": "进阶",
        "reading_minutes": 7,
        "title": "量价关系：放量与缩量的四种基本组合",
        "summary": "量是资金留下的脚印，价是结果；先记住价涨量增/价涨量缩/价跌量增/价跌量缩的含义，再叠加所处位置。",
        "tags": ["量价", "背离", "突破"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "四象限与位置",
                "paras": [
                    "价涨量增是最健康的组合，说明上涨有增量资金持续介入；价涨量缩是背离，多出现在存量博弈的轻装过顶阶段，持续性存疑；价跌量增意味着有人在兑现筹码，趋势转弱信号；价跌量缩属于阴跌，卖压在衰竭但买盘同样缺席，往往要等到地量之后才可能止跌。",
                    "同样的组合放在不同位置含义不同：低位首次放量是启动信号，高位放量滞涨是分歧信号，上涨末端的加速放量常常伴随阶段性见顶。脱离位置谈量价，等于只看一半的证据。",
                ],
            },
            {
                "heading": "两个高价值细节",
                "paras": [
                    "突破要看量能门槛：有效突破通常需要较前 5 日均量明显放大（例如 1.5 倍以上），缩量突破容易被反手打回；反过来，回踩缩量到地量后再启动，往往是风险收益比更好的二次买点。",
                    "“天量天价”需要警惕：单日成交创阶段新高、但价格冲高回落留下长上影，说明大资金在兑现；即便之后价格再创新高，也应主动降低仓位预期。",
                ],
            },
            {
                "heading": "什么时候会失效",
                "paras": [
                    "消息驱动的一字板行情、流动性极差的品种，量价结构都没有参考价值；还要剔除对倒放量——分时上量柱很大而价格几乎不动，通常是资金对敲，不代表方向。量价分析给的是概率，不是承诺，必须配仓位与止损使用。",
                ],
            },
        ],
    },
    {
        "id": "kline-shapes",
        "category": "技术与量价",
        "category_key": "volume-price",
        "level": "入门",
        "reading_minutes": 6,
        "title": "K线形态：先看实体与影线，再记名字",
        "summary": "实体表示多空结果，影线表示盘中被拒绝的幅度；吞没、孕线、启明星都要等收盘确认并结合位置与量能。",
        "tags": ["K线", "反转", "确认"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "形态的内核是“被拒绝”",
                "paras": [
                    "一根 K 线由开盘、最高、最低、收盘四个价格构成。实体长度代表这一段时间多空的最终结果，影线长度代表盘中某一方被拒绝的幅度。长下影线且收在高位，说明下方有资金承接；长上影线且伴随放量，说明上方抛压沉重。很多形态名字不同，内核其实一样：某一方在某个位置被明显拒绝。",
                ],
            },
            {
                "heading": "三种最常见的组合",
                "paras": [
                    "吞没形态指后一根 K 线的实体完全包住前一根的实体，意味着主导权易手，出现在趋势末端比出现在中继更值得重视；孕线（后一根被前一根包住）代表动能衰减，需要下一根 K 线来确认方向；启明星与黄昏星是三根 K 线构成的反转确认，要求第三根实体收进第一根实体内部才算成立。",
                ],
            },
            {
                "heading": "别单独使用",
                "paras": [
                    "形态必须结合位置与量能：低位长下影的价值远高于高位长下影；同样一根大阴线，在缩量回踩中可能是洗盘，在放量破位中就是出货。盘中出现的“假形态”非常多，绝大多数形态都要等收盘确认，盘中价格不作数。",
                ],
            },
        ],
    },
    {
        "id": "moving-average",
        "category": "技术与量价",
        "category_key": "volume-price",
        "level": "进阶",
        "reading_minutes": 7,
        "title": "均线系统：金叉死叉之外，它其实是持仓成本",
        "summary": "N 日均线就是过去 N 天买入者的平均成本；多头排列适合顺势，但震荡市里机械交叉会连续挨打。",
        "tags": ["均线", "趋势", "离场规则"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "均线的本质",
                "paras": [
                    "N 日均线就是过去 N 天买入者的平均成本：5 日线是一周成本，20 日线是一个月成本，60 日线是一个季度成本。价格站上 20 日线并促使 20 日线拐头向上，说明近一个月进场的人普遍盈利、抛压自然减轻——这才是“金叉”背后真正的因果关系，而不是两条线交叉这个动作本身。",
                ],
            },
            {
                "heading": "用法与陷阱",
                "paras": [
                    "多头排列（5 > 20 > 60 且三者都向上）适合顺势持有；跌破 20 日线且反抽不回，通常作为减仓信号。陷阱发生在震荡市：均线在区间内反复交叉，金叉买、死叉卖会连续被打脸。可行的过滤条件是要求价格与 60 日线斜率同向，或只在突破箱体时才启用交叉信号。",
                ],
            },
            {
                "heading": "参数不必迷信",
                "paras": [
                    "20 日对应月线、55/60 日对应季线，是 A 股常见的节奏；把参数调到刚好吻合历史走势，是过拟合的开始。均线更适合作为“持仓与离场的纪律规则”，而不是精确的买卖点——它天生滞后，但滞后换来的是规则的稳定性。",
                ],
            },
        ],
    },
    {
        "id": "intelligent-investor",
        "category": "阅读经典",
        "category_key": "classics",
        "level": "进阶",
        "reading_minutes": 8,
        "title": "《聪明的投资者》：安全边际到底怎么用",
        "summary": "格雷厄姆关心的不是选股，而是“估错也不至于亏钱”的缓冲垫，以及防御型投资者可执行的四条防线。",
        "tags": ["价值投资", "安全边际", "仓位"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "核心不是选股，是安全边际",
                "paras": [
                    "格雷厄姆把投资者与投机者的区别落在三条标准上：是否基于深入分析、能否保证本金安全、收益是否充分。安全边际就是内在价值与市场价格之间的差，用他的话说，即使估值高估或低估了两三成，也不至于让本金受损。它不是一个精确数字，而是承认“我可能会错”的冗余设计。",
                ],
            },
            {
                "heading": "防御型投资者的四条防线",
                "paras": [
                    "第一是适度分散（他建议至少 10 只、单只不超过资金的 5%）；第二是只买财务稳健的公司（流动比率、盈利稳定性这类硬指标）；第三是以合理而非“便宜的想象”买入；第四是给自己的股票仓位设定上下限（经典做法是 50/50 股债再平衡）。这四条的共同点是：可执行、可纪律化，不依赖对未来的预判。",
                ],
            },
            {
                "heading": "怎么读这本书",
                "paras": [
                    "最该反复读的是“市场先生”那一章：市场报价是给你用的，不是给你信的。关于分级债券与早年套利案例的部分已经过时，可以略读；第 8 章对成长股估值的讨论反而常被跳过，其实很有价值。读完的标准不是记住几个公式，而是能写出自己的仓位上下限与买入条件。",
                ],
            },
        ],
    },
    {
        "id": "remiscences",
        "category": "阅读经典",
        "category_key": "classics",
        "level": "进阶",
        "reading_minutes": 7,
        "title": "《股票大作手回忆录》：关键点与认赔的纪律",
        "summary": "利弗莫尔的价值在时机框架与心理纪律：等价格突破关键点再进场，判断错了立刻认赔，判断对了坐着不动。",
        "tags": ["交易心理", "关键点", "止损"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "关键点：让市场先表态",
                "paras": [
                    "书中反复出现的“关键点”，是指价格突破某个明确位置时才进场，而不是在模糊的中间价位猜方向。突破关键点意味着市场已经用真金白银表态，此时顺势的胜率与赔率都更好；在没有关键点的区间里频繁进出，只会不断消耗手续费与耐心。",
                ],
            },
            {
                "heading": "坐着不动与立刻认赔",
                "paras": [
                    "利弗莫尔强调“坐着不动”的能力：判断正确时让利润奔跑，判断错误时立即认赔出局。他把亏损当作经营成本，而不是对自尊的打击。多数人恰好相反——盈利一点就落袋，亏损却死扛到不可收拾，这正是书中“赚小钱亏大钱”的根源。",
                ],
            },
            {
                "heading": "这本书的历史局限",
                "paras": [
                    "这是上世纪早期的投机回忆录，其中许多操纵手法在今天的监管环境下已不可复制，作者本人的交易纪律与生活方式也并不一致。它的价值在心理与时机框架，不在具体手法。建议配合《交易心理分析》一起读，把“等待”和“认赔”变成可写进交易计划的条款。",
                ],
            },
        ],
    },
    {
        "id": "random-walk",
        "category": "阅读经典",
        "category_key": "classics",
        "level": "入门",
        "reading_minutes": 6,
        "title": "《漫步华尔街》：费用、配置与指数化",
        "summary": "马尔基尔的核心结论是：扣掉费用后持续战胜指数极难，普通人更该用资产配置与再平衡代替择时。",
        "tags": ["指数投资", "费用", "资产配置"],
        "updated_at": "2026-10-25",
        "sections": [
            {
                "heading": "有效市场到底在说什么",
                "paras": [
                    "马尔基尔的核心命题是：价格已经反映了可得信息，因此随机选股的组合长期很难跑赢指数基金。他并不是断言市场永远有效，而是提醒“持续战胜市场”远比看起来困难——尤其是把交易成本、管理费与税费都算进去之后。",
                ],
            },
            {
                "heading": "三条可以直接执行的结论",
                "paras": [
                    "一是费用的复利侵蚀被严重低估：每年 1% 的费率差异，在三十年周期里会吃掉约四分之一的最终收益；二是资产配置决定绝大部分回报，用股债比例加定期再平衡代替择时，是普通投资者最省力的方案；三是“核心—卫星”结构：核心仓位用宽基指数，卫星仓位才留给主动研究与个股观点。",
                ],
            },
            {
                "heading": "什么时候这本书不够用",
                "paras": [
                    "书中对行为金融只点到为止，而 A 股的投资者结构、涨跌停限制与 T+1 制度，会带来与美股不同的波动节奏。读它的目的，是先放弃“明天就能跑赢市场”的幻觉，把省下来的精力投入到可重复、可检验的规则上。",
                ],
            },
        ],
    },
]

_ACADEMY_BY_ID = {a["id"]: a for a in ACADEMY_ARTICLES}
_ACADEMY_CATEGORIES = ["盘面基础", "技术与量价", "阅读经典"]


def academy_articles(category: str | None = None) -> dict:
    """学院文章列表（正文全量返回：9 篇合计约 8KB，远低于 100KB 上限）。"""
    items = ACADEMY_ARTICLES
    if category:
        key = category.strip()
        items = [
            a
            for a in items
            if key in (a["category"], a["category_key"], a["id"])
        ]
    return {
        "items": list(items),
        "total": len(items),
        "categories": [
            {
                "key": c,
                "count": sum(1 for a in ACADEMY_ARTICLES if a["category"] == c),
            }
            for c in _ACADEMY_CATEGORIES
        ],
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def academy_article(article_id: str) -> dict | None:
    """单篇文章；不存在返回 None（路由转 404）。"""
    return _ACADEMY_BY_ID.get(article_id)


# ---------------------------------------------------------------------------
# 二、快讯流（raw_signals）
# ---------------------------------------------------------------------------

# 快捷来源别名 -> 实际 source_name 前缀（`cls` 是原站财联社栏目的简称）
_SOURCE_ALIASES = {
    "cls": ["财联社"],
    "财联社": ["财联社"],
    "jin10": ["金十"],
    "金十": ["金十"],
    "eastmoney": ["东财", "EM-", "巨潮"],
    "ths": ["THS-"],
    "akshare": ["AKShare-"],
}


@ttl_cache(300)
def news_flash(source: str = "", page: int = 1, size: int = 20) -> dict:
    """快讯分页：按 pub_time DESC 下推分页，摘要截断到 160 字控制 payload。

    `source` 是原站栏目别名（cls/金十/...）：能识别就过滤，识别不了就不过滤，
    并在 meta 里注明——避免用户因为一个拼不出来的别名看到空列表。
    """
    size = max(1, min(int(size), 100))
    page = max(1, int(page))
    raw_source = (source or "").strip()
    prefixes = _SOURCE_ALIASES.get(raw_source.lower()) or _SOURCE_ALIASES.get(raw_source)
    note = ""

    where = ""
    params: list = []
    if prefixes:
        # OR 前缀匹配，命中财联社-电报这类带后缀的 source_name
        cond = " OR ".join(["source_name LIKE %s ESCAPE '\\'"] * len(prefixes))
        where = f" WHERE ({cond})"
        params.extend(f"{p}%" for p in prefixes)
    elif raw_source:
        note = f"未识别的来源别名 {raw_source}，已返回全部来源"

    total = scalar(f"SELECT COUNT(*) FROM raw_signals{where}", tuple(params)) or 0
    rows = _rows(
        f"""
        SELECT id, title, source_name, source_tier, pub_time, url,
               LEFT(COALESCE(NULLIF(content, ''), title), 160) AS summary
        FROM raw_signals{where}
        ORDER BY pub_time DESC NULLS LAST, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params) + (size, (page - 1) * size),
    )

    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "source": r["source_name"],
            "tier": r["source_tier"],
            "pub_time": _iso(r["pub_time"]),
            "url": r["url"] or "",
            "summary": r["summary"] or "",
        }
        for r in rows
    ]

    meta = {
        "source": raw_source,
        "source_filter": bool(prefixes),
        "aliases": sorted(_SOURCE_ALIASES.keys()),
        "note": note,
    }
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "as_of": _iso(scalar("SELECT MAX(pub_time) FROM raw_signals")),
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# 三、新闻雷达：近 N 小时信号量按小时聚合（曲线数据）
# ---------------------------------------------------------------------------


@ttl_cache(300)
def news_radar(hours: int = 48) -> dict:
    """按 `date_trunc('hour', pub_time)` 分组，SQL 内直接切片取每小时 TOP3 标题。

    锚点用表内 MAX(pub_time) 而不是 now()：今天是周日，采集停在 10-23，
    用 now() 查「近 48 小时」会得到一条空曲线，前端图直接塌掉。
    """
    hours = max(1, min(int(hours), 168))
    rows = _rows(
        """
        WITH a AS (SELECT MAX(pub_time) AS t FROM raw_signals)
        SELECT date_trunc('hour', r.pub_time) AS hour,
               COUNT(*) AS count,
               (array_agg(LEFT(COALESCE(r.title, ''), 60)
                          ORDER BY r.pub_time DESC))[1:3] AS top_titles
        FROM raw_signals r, a
        WHERE r.pub_time > a.t - (%s * INTERVAL '1 hour')
          AND r.pub_time <= a.t
        GROUP BY 1
        ORDER BY 1 ASC
        """,
        (hours,),
    )

    items = [
        {
            "hour": _iso(r["hour"]),
            "count": int(r["count"] or 0),
            "top_titles": [t for t in (r["top_titles"] or []) if t],
        }
        for r in rows
    ]
    anchor = scalar("SELECT MAX(pub_time) FROM raw_signals")
    return {
        "items": items,
        "total": sum(i["count"] for i in items),
        "hours": hours,
        "as_of": _iso(anchor),
        "meta": {
            "anchor": _iso(anchor),
            "note": "时间窗以最新一条信号为锚点（周末/休市期间 now() 窗口会为空）",
            "degraded": not items,
            "reason": "" if items else "raw_signals 无数据",
        },
    }


# ---------------------------------------------------------------------------
# 四、热搜榜
# ---------------------------------------------------------------------------


@ttl_cache(300)
def hot_search(platform: str = "eastmoney", limit: int = 50) -> dict:
    """热搜主榜：extracted_recommendations 按 ts_code 聚合出现次数与强度。

    trend 口径：以表内最新 pub_time 为锚，比较「最近 7 天」与「再往前 7 天」
    的被提及次数；为空时降级到 raw_signals 标题抽股票名。
    """
    limit = max(1, min(int(limit), 100))
    anchor = scalar("SELECT MAX(pub_time) FROM extracted_recommendations")

    rows = _rows(
        """
        WITH a AS (SELECT MAX(pub_time) AS t FROM extracted_recommendations)
        SELECT r.ts_code, r.stock_name,
               COUNT(*) AS mentions,
               MAX(r.strength) AS strength,
               MAX(r.pub_time) AS last_pub,
               COUNT(*) FILTER (WHERE r.pub_time > a.t - INTERVAL '7 days') AS recent7,
               COUNT(*) FILTER (WHERE r.pub_time <= a.t - INTERVAL '7 days'
                                 AND r.pub_time > a.t - INTERVAL '14 days') AS prev7
        FROM extracted_recommendations r, a
        WHERE UPPER(r.ts_code) <> 'SKIP'
          AND COALESCE(UPPER(r.stock_name), '') <> 'SKIP'
          AND COALESCE(r.stock_name, '') <> ''
        GROUP BY r.ts_code, r.stock_name
        ORDER BY COUNT(*) DESC, MAX(r.pub_time) DESC
        LIMIT %s
        """,
        (limit,),
    )

    total = 0
    if rows:
        total = (
            scalar(
                """
                SELECT COUNT(*) FROM (
                    SELECT ts_code FROM extracted_recommendations
                    WHERE UPPER(ts_code) <> 'SKIP'
                      AND COALESCE(UPPER(stock_name), '') <> 'SKIP'
                      AND COALESCE(stock_name, '') <> ''
                    GROUP BY ts_code, stock_name
                ) t
                """
            )
            or 0
        )
        source_note = "extracted_recommendations 推荐池聚合"
    else:
        rows = _hot_search_from_titles(limit)
        total = len(rows)
        source_note = "推荐池为空，已降级为 raw_signals 标题抽取股票名"

    max_mentions = max([int(r.get("mentions") or 0) for r in rows] or [0])
    items = []
    for i, r in enumerate(rows):
        recent7 = int(r.get("recent7") or 0)
        prev7 = int(r.get("prev7") or 0)
        if r.get("recent7") is None:
            trend = "flat"
        elif recent7 > prev7:
            trend = "up"
        elif recent7 < prev7:
            trend = "down"
        else:
            trend = "flat"
        mentions = int(r.get("mentions") or 0)
        items.append(
            {
                "rank": i + 1,
                "ts_code": r.get("ts_code"),
                "stock_name": r.get("stock_name"),
                "mentions": mentions,
                "strength": r.get("strength"),
                "trend": trend,
                # 相对热度：仅用于榜单展示（榜首=100万），由 mentions 换算，非外部数据
                "heat": int(round(mentions / max_mentions * 1000000)) if max_mentions else 0,
                "last_pub": _iso(r.get("last_pub")),
            }
        )

    return {
        "items": items,
        "total": total,
        "platform": platform or "eastmoney",
        "as_of": _iso(anchor),
        "meta": {
            "source": source_note,
            "window": "7d vs 7d（锚点=最新一条推荐）",
            "degraded": not rows,
            "reason": "" if rows else "无可用数据源",
        },
    }


def _hot_search_from_titles(limit: int) -> list[dict]:
    """降级：用 raw_signals 标题匹配股票名统计热度。

    只取 30 天窗口 + 名称长度 ≥3，既控制笛卡尔量级，也避免“中/东”这类
    短名把所有标题都命中。
    """
    try:
        return _rows(
            """
            WITH a AS (SELECT MAX(pub_time) AS t FROM raw_signals)
            SELECT b.ts_code, b.stock_name,
                   COUNT(*) AS mentions,
                   NULL::int AS strength,
                   MAX(r.pub_time) AS last_pub,
                   NULL::int AS recent7,
                   NULL::int AS prev7
            FROM raw_signals r, a, stock_basic_info b
            WHERE r.pub_time > a.t - INTERVAL '30 days'
              AND COALESCE(r.title, '') <> ''
              AND char_length(b.stock_name) >= 3
              AND COALESCE(b.is_active, TRUE)
              AND strpos(r.title, b.stock_name) > 0
            GROUP BY b.ts_code, b.stock_name
            ORDER BY COUNT(*) DESC, MAX(r.pub_time) DESC
            LIMIT %s
            """,
            (limit,),
        )
    except Exception:  # noqa: BLE001 降级路径绝不能抛错
        return []


# ---------------------------------------------------------------------------
# 五、板块资金流（industry / concept 两张快照表）
# ---------------------------------------------------------------------------


@ttl_cache(600)
def _columns(table: str) -> set[str]:
    """列名集合（概念表没有 amount/up_count/down_count，用它决定 SELECT NULL）。"""
    return {
        r["column_name"]
        for r in query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
    }


def sector_fund_flow(sector_type: str = "industry", limit: int = 50) -> dict:
    """最新交易日板块净流入排行；缺失列一律返回 None，保持结构完整。"""
    kind = "concept" if str(sector_type).lower().startswith("conc") else "industry"
    table = "concept_board_quotes" if kind == "concept" else "industry_board_quotes"
    code_col = "concept_code" if kind == "concept" else "industry_code"
    name_col = "concept_name" if kind == "concept" else "industry_name"
    limit = max(1, min(int(limit), 200))

    latest = scalar(f"SELECT MAX(trade_date) FROM {table}")
    if latest is None:
        return {
            "items": [],
            "total": 0,
            "trade_date": None,
            "sector_type": kind,
            "meta": {"degraded": True, "reason": f"{table} 暂无数据", "unit": "亿元"},
        }

    present = _columns(table)
    extras = [c for c in ("amount", "up_count", "down_count") if c in present]
    select_nulls = "".join(
        f", NULL::{ 'numeric' if c == 'amount' else 'int' } AS {c}"
        for c in ("amount", "up_count", "down_count")
        if c not in present
    )
    select_extra = "".join(f", {c}" for c in extras)

    total = scalar(f"SELECT COUNT(*) FROM {table} WHERE trade_date = %s", (latest,)) or 0
    rows = _rows(
        f"""
        SELECT {code_col} AS code, {name_col} AS name, pct_chg, net_inflow{select_extra}{select_nulls},
               lead_stock_name
        FROM {table}
        WHERE trade_date = %s
        ORDER BY net_inflow DESC NULLS LAST, pct_chg DESC NULLS LAST
        LIMIT %s
        """,
        (latest, limit),
    )
    items = [
        {
            "code": r["code"],
            "name": r["name"],
            "pct_chg": _f(r.get("pct_chg")),
            "net_inflow": _f(r.get("net_inflow")),
            "amount": _f(r.get("amount")),
            "up_count": r.get("up_count"),
            "down_count": r.get("down_count"),
            "lead_stock_name": r.get("lead_stock_name"),
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "trade_date": str(latest),
        "sector_type": kind,
        "meta": {
            "unit": "亿元",
            "degraded": False,
            "note": "净流入/成交额口径与 /boards/* 一致（akshare 板块资金流，单位亿元）",
        },
    }


# ---------------------------------------------------------------------------
# 六、外部行情（东财 push2 → 新浪 → 静态兜底），全部 600s TTL
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: float = 6.0, referer: str | None = None,
              encoding: str = "utf-8") -> str:
    """只用标准库 urllib 的最小 GET；固定白名单域名、必带超时。"""
    headers = {"User-Agent": _FETCH_UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 域名写死在调用方
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="replace")


def _net_cache(key: str, ttl_ok: float, ttl_fail: float, fetcher):
    """外部源专用缓存：成功缓存 600s，失败缓存 60s。

    为什么失败也要缓存：源挂掉时若不缓存，每个请求都要干等一次网络超时；
    只缓存 60s 是为了让网络恢复后能尽快自愈，而不是把失败锁死 10 分钟。
    """
    hit = cache_get(key)
    if hit is not None:
        return hit
    try:
        ok, value = fetcher()
        if not ok and not isinstance(value, dict):
            ok, value = False, {"error": "empty payload"}
    except Exception as exc:  # noqa: BLE001 外部异常一律转降级，不外抛
        ok, value = False, {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    payload = {"ok": bool(ok), "value": value, "fetched_at": _now()}
    cache_set(key, payload, ttl_ok if ok else ttl_fail)
    return payload


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _em_push(secids: str, fields: str = "f2,f3,f12,f14") -> list[dict]:
    """东财 push2 全球行情（任务指定主源）。返回 diff 列表，异常直接抛给上层。"""
    url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get?"
        f"secids={secids}&fields={fields}&fltt=2"
    )
    body = _http_get(url, timeout=5.0, referer="https://quote.eastmoney.com/")
    data = json.loads(body)
    diff = (data.get("data") or {}).get("diff") or []
    if isinstance(diff, dict):  # 部分版本返回 {序号:{f12:..}} 结构
        diff = list(diff.values())
    return diff


# ---- 外围指数 ----

_GLOBAL_SECIDS = "100.DJIA,100.NDX,100.SPX,100.HSI,100.N225,100.FTSE,100.GDAXI,100.FCHI"

# 东财 secid -> 统一代码 / 展示名
_GLOBAL_NAMES = {
    "DJIA": ("DJI", "道琼斯工业指数", "美国", "USD"),
    "DJI": ("DJI", "道琼斯工业指数", "美国", "USD"),
    "NDX": ("NDX", "纳斯达克100指数", "美国", "USD"),
    "SPX": ("SPX", "标普500指数", "美国", "USD"),
    "HSI": ("HSI", "恒生指数", "中国香港", "HKD"),
    "N225": ("N225", "日经225指数", "日本", "JPY"),
    "FTSE": ("FTSE", "富时100指数", "英国", "GBP"),
    "GDAXI": ("GDAXI", "德国DAX指数", "德国", "EUR"),
    "FCHI": ("FCHI", "法国CAC40指数", "法国", "EUR"),
    "KOSPI": ("KOSPI", "韩国综合指数", "韩国", "KRW"),
}

# 新浪 hq.sinajs.cn 的 int_* 代码 -> 统一代码
_SINA_INDEX_MAP = {
    "int_dji": "DJI",
    "int_nasdaq": "NDX",
    "int_sp500": "SPX",
    "int_hangseng": "HSI",
    "int_nikkei": "N225",
    "int_ftse": "FTSE",
}

_STATIC_INDEX = ["DJI", "NDX", "SPX", "HSI", "N225", "FTSE", "GDAXI", "FCHI"]


def _index_item(code: str, price, pct_chg, change=None, source=None, open_=None, high=None, low=None) -> dict:
    meta = _GLOBAL_NAMES.get(code)
    if meta and len(meta) >= 4:
        _, name, market, currency = meta[:4]
    else:
        name, market, currency = code, "海外", "USD"
    p = _f(price)
    pct = _f(pct_chg)
    chg = _f(change)
    if p is not None and pct is not None and (1 + pct / 100.0) != 0:
        calc = round(p - p / (1 + pct / 100.0), 4)
        # 东财 f4 偶尔与 f3 方向不一致（跨时区更新），以涨跌幅反推的涨跌额为准
        if chg is None or (chg > 0) != (calc > 0):
            chg = calc
    return {
        "code": code,
        "name": name,
        "market": market,
        "currency": currency,
        "price": p,
        "pct_chg": pct,
        "change": chg,
        "open": _f(open_),
        "high": _f(high),
        "low": _f(low),
        "source": source,
    }


def _fetch_global_indexes():
    """主源东财 push2 → 备源新浪；两个都拿不到才返回 (False, ...) 走静态兜底。"""
    push2_err = sina_err = ""
    try:
        diff = _em_push(_GLOBAL_SECIDS, "f2,f3,f4,f12,f14,f15,f16,f17,f18")
        items = []
        for it in diff:
            raw_code = str(it.get("f12") or "").upper()
            meta = _GLOBAL_NAMES.get(raw_code)
            if meta and it.get("f2") not in (None, "", "-"):
                items.append(
                    _index_item(
                        meta[0],
                        it.get("f2"),
                        it.get("f3"),
                        it.get("f4"),
                        source="eastmoney-push2",
                        open_=it.get("f17"),
                        high=it.get("f15"),
                        low=it.get("f16"),
                    )
                )
        if items:
            got = {i["code"] for i in items}
            return True, {
                "source": "eastmoney-push2",
                "items": items,
                "push2_ok": True,
                "push2_error": "",
                "sina_error": "",
                "missing": [c for c in _STATIC_INDEX if c not in got],
            }
        push2_err = "push2 返回空数据"
    except Exception as exc:  # noqa: BLE001
        push2_err = f"{type(exc).__name__}: {str(exc)[:120]}"

    try:
        # 新浪全球指数：本机实测可用（push2 在本机被拒连时的备源）
        text = _http_get(
            "https://hq.sinajs.cn/list=" + ",".join(_SINA_INDEX_MAP),
            referer="https://finance.sina.com.cn",
            encoding="gbk",
        )
        items = []
        for line in text.splitlines():
            if "hq_str_" not in line or '"' not in line:
                continue
            key = line.split("hq_str_")[1].split("=")[0].strip()
            payload = line.split('"')[1] if '"' in line else ""
            parts = payload.split(",")
            code = _SINA_INDEX_MAP.get(key)
            if not code or len(parts) < 4 or not parts[1]:
                continue
            items.append(
                _index_item(code, parts[1], parts[3], parts[2], source="sina-hq")
            )
        if items:
            got = {i["code"] for i in items}
            return True, {
                "source": "sina-hq",
                "items": items,
                "push2_ok": False,
                "push2_error": push2_err,
                "sina_error": "",
                "missing": [c for c in _STATIC_INDEX if c not in got],
            }
        sina_err = "sina 返回空数据"
    except Exception as exc:  # noqa: BLE001
        sina_err = f"{type(exc).__name__}: {str(exc)[:120]}"

    return False, {"push2_error": push2_err, "sina_error": sina_err}


def global_indexes() -> dict:
    """外围指数：成功 600s 缓存；两个外部源都失败 → 静态名单 + meta.degraded。"""
    cached = _net_cache("content:global-indexes", 600, 60, _fetch_global_indexes)
    if cached["ok"]:
        value = cached["value"]
        items = sorted(value["items"], key=lambda x: _STATIC_INDEX.index(x["code"])
                       if x["code"] in _STATIC_INDEX else 99)
        return {
            "items": items,
            "as_of": cached["fetched_at"],
            "meta": {
                "source": value["source"],
                "degraded": False,
                "push2_ok": value["push2_ok"],
                "push2_error": value["push2_error"],
                "sina_error": value["sina_error"],
                "missing": value["missing"],
                "cached_until": cached["fetched_at"],
                "note": "东财 push2 为任务指定主源；被拒连时自动切换新浪免费源",
            },
        }
    return {
        "items": [_index_item(c, None, None, None, source="static") for c in _STATIC_INDEX],
        "as_of": None,
        "meta": {
            "source": "static",
            "degraded": True,
            "push2_ok": False,
            "push2_error": cached["value"].get("push2_error", ""),
            "sina_error": cached["value"].get("sina_error", ""),
            "reason": "外部行情源均不可用，已返回结构完整的静态兜底名单",
        },
    }


# ---- 大宗商品 ----

_COMMODITY_META = [
    # (symbol, 展示名, 货币, 计价单位)
    ("BRENT", "布伦特原油", "USD", "美元/桶"),
    ("CL", "WTI原油", "USD", "美元/桶"),
    ("SC", "INE原油", "CNY", "元/桶"),
    ("GC", "COMEX黄金", "USD", "美元/盎司"),
    ("XAU", "伦敦金", "USD", "美元/盎司"),
    ("SI", "COMEX白银", "USD", "美元/盎司"),
    ("HG", "COMEX铜", "USD", "美分/磅"),
    ("NG", "NYMEX天然气", "USD", "美元/百万英热"),
    ("C", "CBOT玉米", "USD", "美分/蒲式耳"),
]
_COMM_ORDER = [m[0] for m in _COMMODITY_META]
_COMM_DICT = {m[0]: m for m in _COMMODITY_META}

# 新浪 hf_* 代码 -> symbol
_SINA_COMMODITY = {
    "hf_OIL": "BRENT",
    "hf_CL": "CL",
    "hf_GC": "GC",
    "hf_XAU": "XAU",
    "hf_SI": "SI",
    "hf_HG": "HG",
    "hf_NG": "NG",
    "hf_C": "C",
}

# 东财 secid 代码段 -> symbol（push2 若恢复即可直接用）
_EM_COMMODITY_MAP = {
    "COMEX_GC": "GC",
    "GC00Y": "GC",
    "COMEX_SILVER": "SI",
    "SI00Y": "SI",
    "CL00Y": "CL",
    "OIL00Y": "BRENT",
    "HG00Y": "HG",
    "NG00Y": "NG",
    "C00Y": "C",
    "SC00Y": "SC",
}
_COMMODITY_SECIDS = "100.COMEX_GC,100.COMEX_SILVER,101.GC00Y,101.SI00Y,102.CL00Y,102.HG00Y"


def _comm_item(symbol: str, price=None, pct_chg=None, as_of=None, source=None) -> dict:
    _, name, currency, unit = _COMM_DICT[symbol]
    return {
        "symbol": symbol,
        "name": name,
        "price": _f(price),
        "pct_chg": _f(pct_chg),
        "currency": currency,
        "unit": unit,
        "as_of": as_of,
        "source": source,
    }


def _fetch_commodity():
    """新浪 hf_*（品种最全）为主，东财 push2 补齐缺口；两边都失败才算失败。

    为什么新浪优先：实测新浪一次返回布伦特/WTI/黄金/伦敦金/白银/铜/天然气/玉米，
    而东财 push2 只稳定返回 GC/SI/HG；且两家「铜」的量纲不同（美元/磅 vs 美分/磅），
    混用会把 6.48 和 647 画成两个数量级。
    """
    push2_err = sina_err = ""
    found: dict[str, dict] = {}

    try:
        text = _http_get(
            "https://hq.sinajs.cn/list=" + ",".join(_SINA_COMMODITY),
            referer="https://finance.sina.com.cn",
            encoding="gbk",
        )
        for line in text.splitlines():
            if "hq_str_" not in line or '"' not in line:
                continue
            key = line.split("hq_str_")[1].split("=")[0].strip()
            payload = line.split('"')[1] if '"' in line else ""
            symbol = _SINA_COMMODITY.get(key)
            parts = payload.split(",")
            if not symbol or len(parts) < 8 or not parts[0]:
                continue
            price, prev = _f(parts[0]), _f(parts[7])
            pct = round((price - prev) / prev * 100, 4) if price and prev else None
            as_of = None
            if len(parts) > 12 and parts[12] and parts[6]:
                as_of = f"{parts[12]}T{parts[6]}"
            found[symbol] = _comm_item(symbol, price, pct, as_of, source="sina-hq")
    except Exception as exc:  # noqa: BLE001
        sina_err = f"{type(exc).__name__}: {str(exc)[:120]}"

    try:
        diff = _em_push(_COMMODITY_SECIDS, "f2,f3,f4,f12,f14")
        for it in diff:
            sym = _EM_COMMODITY_MAP.get(str(it.get("f12") or "").upper())
            if not sym or sym in found or it.get("f2") in (None, "", "-"):
                continue
            price = _f(it.get("f2"))
            # push2 的铜是美元/磅，统一折算成美分/磅（与新浪口径一致）
            if sym == "HG" and price is not None and price < 100:
                price = round(price * 100, 4)
            found[sym] = _comm_item(sym, price, it.get("f3"), source="eastmoney-push2")
    except Exception as exc:  # noqa: BLE001
        push2_err = f"{type(exc).__name__}: {str(exc)[:120]}"

    if found:
        sources = sorted({v["source"] for v in found.values() if v.get("source")})
        return True, {
            "source": "+".join(sources) or "mixed",
            "items": list(found.values()),
            "push2_ok": bool(push2_err == ""),
            "push2_error": push2_err,
            "sina_error": sina_err,
        }
    return False, {"push2_error": push2_err, "sina_error": sina_err}


def commodity_quotes() -> dict:
    """油 / 金 / 铜 / 白银 / 玉米等：东财 push2 → 新浪 hf_* → 静态兜底（价格 null）。"""
    cached = _net_cache("content:commodity-quotes", 600, 60, _fetch_commodity)
    if cached["ok"]:
        value = cached["value"]
        by_sym = {i["symbol"]: i for i in value["items"]}
        # INE 原油等新浪不提供的品种保留条目（price=null），结构不塌
        items = [by_sym.get(s) or _comm_item(s, None, None, source="") for s in _COMM_ORDER]
        as_of = max([i["as_of"] for i in items if i["as_of"]] or [None]) or cached["fetched_at"]
        return {
            "items": items,
            "as_of": as_of,
            "meta": {
                "source": value["source"],
                "degraded": False,
                "push2_ok": value["push2_ok"],
                "push2_error": value["push2_error"],
                "sina_error": value["sina_error"],
                "note": "新浪 hf_* 为实测可用源；INE 原油无免费源，price 为 null",
            },
        }
    return {
        "items": [_comm_item(s, None, None, source="static") for s in _COMM_ORDER],
        "as_of": None,
        "meta": {
            "source": "static",
            "degraded": True,
            "push2_ok": False,
            "push2_error": cached["value"].get("push2_error", ""),
            "sina_error": cached["value"].get("sina_error", ""),
            "reason": "外部商品行情源均不可用，已返回结构完整的静态兜底（价格为 null）",
        },
    }


# ---------------------------------------------------------------------------
# 七、并购重组
# ---------------------------------------------------------------------------

# 标题/分类关键词（参数值里的 % 不需要转义，psycopg2 不做二次模板化）
_MERGER_KEYWORDS = [
    "%并购%", "%重组%", "%借壳%", "%要约%",
    "%收购%", "%股权转让%", "%资产注入%", "%吸收合并%",
]
_MERGER_NOISE = "%重组蛋白%"  # 「重组蛋白」是生物医药概念，不是并购重组


def merger_restructuring(page: int = 1, size: int = 20) -> dict:
    """主源 stock_announcements（当前为空表），空则降级 raw_signals 标题关键词。"""
    page = max(1, int(page))
    size = max(1, min(int(size), 100))
    offset = (page - 1) * size

    cond = " OR ".join(
        [f"category ILIKE %s"] * len(_MERGER_KEYWORDS)
        + [f"title ILIKE %s"] * len(_MERGER_KEYWORDS)
    )
    params = _MERGER_KEYWORDS + _MERGER_KEYWORDS
    try:
        total = scalar(
            f"SELECT COUNT(*) FROM stock_announcements WHERE ({cond})", tuple(params)
        ) or 0
    except Exception:  # noqa: BLE001 表不存在也要能降级
        total = 0

    if total:
        rows = _rows(
            f"""
            SELECT id, ts_code, stock_name, title, category, publish_date, url
            FROM stock_announcements
            WHERE ({cond})
            ORDER BY publish_date DESC NULLS LAST, id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params) + (size, offset),
        )
        items = [
            {
                "id": r["id"],
                "ts_code": r["ts_code"],
                "stock_name": r["stock_name"],
                "title": r["title"],
                "category": r["category"],
                "publish_date": str(r["publish_date"]) if r["publish_date"] else None,
                "url": r["url"],
            }
            for r in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "as_of": str(scalar("SELECT MAX(publish_date) FROM stock_announcements") or ""),
            "meta": {"source": "stock_announcements", "degraded": False, "note": ""},
        }

    # ---- 降级：公告表为空，改读快讯标题（去重 + 关键词 + 噪声过滤） ----
    kw_cond = " OR ".join(["r.title ILIKE %s"] * len(_MERGER_KEYWORDS))
    where = (
        f"({kw_cond}) AND r.title NOT ILIKE %s "
        "AND r.pub_time >= (SELECT MAX(pub_time) - INTERVAL '180 days' FROM raw_signals)"
    )
    kw_params = tuple(_MERGER_KEYWORDS) + (_MERGER_NOISE,)
    total = (
        scalar(f"SELECT COUNT(DISTINCT title) FROM raw_signals r WHERE {where}", kw_params)
        or 0
    )
    rows = _rows(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (r.title)
                   r.id, r.title, r.source_name, r.pub_time, r.url,
                   (SELECT s.stock_name FROM stock_basic_info s
                     WHERE char_length(s.stock_name) >= 3
                       AND strpos(r.title, s.stock_name) > 0
                     ORDER BY char_length(s.stock_name) DESC LIMIT 1) AS stock_name,
                   (SELECT s.ts_code FROM stock_basic_info s
                     WHERE char_length(s.stock_name) >= 3
                       AND strpos(r.title, s.stock_name) > 0
                     ORDER BY char_length(s.stock_name) DESC LIMIT 1) AS ts_code
            FROM raw_signals r
            WHERE {where}
            ORDER BY r.title, r.pub_time DESC
        ) t
        ORDER BY t.pub_time DESC
        LIMIT %s OFFSET %s
        """,
        kw_params + (size, offset),
    )
    items = [
        {
            "id": r["id"],
            "ts_code": r["ts_code"],
            "stock_name": r["stock_name"],
            "title": r["title"],
            "category": "并购重组",
            "publish_date": str(r["pub_time"].date()) if r.get("pub_time") else None,
            "url": r["url"] or "",
            "source": r["source_name"],
        }
        for r in rows
    ]
    as_of = scalar(
        "SELECT MAX(pub_time) FROM raw_signals WHERE "
        + kw_cond.replace("r.title", "title")
        + " AND title NOT ILIKE %s",
        tuple(_MERGER_KEYWORDS) + (_MERGER_NOISE,),
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "as_of": _iso(as_of),
        "meta": {
            "source": "raw_signals",
            "degraded": True,
            "note": "stock_announcements 为空表，已降级为快讯标题关键词（近 180 天、按标题去重）",
        },
    }


# ---------------------------------------------------------------------------
# 八、龙虎榜
# ---------------------------------------------------------------------------


def dragon_tiger(
    page: int = 1,
    size: int = 20,
    trade_date: str | None = None,
    keyword: str = "",
) -> dict:
    """龙虎榜明细：按 trade_date DESC + |net_amt| DESC 下推分页。"""
    page = max(1, int(page))
    size = max(1, min(int(size), 100))
    offset = (page - 1) * size

    where: list[str] = []
    params: list = []
    if trade_date:
        try:
            datetime.strptime(str(trade_date)[:10], "%Y-%m-%d")
            where.append("trade_date = %s::date")
            params.append(str(trade_date)[:10])
        except ValueError:
            pass  # 日期格式不合法时忽略该条件，并在 meta 标注
    kw = (keyword or "").strip()
    if kw:
        where.append("(stock_name ILIKE %s ESCAPE '\\' OR reason ILIKE %s ESCAPE '\\')")
        escaped = _like(kw)
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    cond = (" WHERE " + " AND ".join(where)) if where else ""

    total = scalar(f"SELECT COUNT(*) FROM lhb_detail{cond}", tuple(params)) or 0
    rows = _rows(
        f"""
        SELECT trade_date, ts_code, stock_name, reason, buy_amt, sell_amt, net_amt, is_inst
        FROM lhb_detail{cond}
        ORDER BY trade_date DESC NULLS LAST, net_amt DESC NULLS LAST
        LIMIT %s OFFSET %s
        """,
        tuple(params) + (size, offset),
    )
    items = [
        {
            "trade_date": str(r["trade_date"]) if r["trade_date"] else None,
            "ts_code": r["ts_code"],
            "stock_name": r["stock_name"],
            "reason": r["reason"],
            "buy_amt": _f(r["buy_amt"]),
            "sell_amt": _f(r["sell_amt"]),
            "net_amt": _f(r["net_amt"]),
            "is_inst": bool(r["is_inst"]),
        }
        for r in rows
    ]
    as_of = scalar("SELECT MAX(trade_date) FROM lhb_detail")
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "as_of": str(as_of) if as_of else None,
        "meta": {
            "date_filter": bool(where and trade_date),
            "keyword": kw,
            "note": "本地 lhb_detail 未采集营业部名称，席位维度见 /dragon-tiger/seat-rank",
            "degraded": total == 0,
        },
    }


def dragon_tiger_seat_rank(days: int = 20, limit: int = 50) -> dict:
    """按股票 + 上榜原因聚合的净买入榜单（本地表无营业部字段，故按 is_inst 分席位型）。"""
    days = max(1, min(int(days), 120))
    limit = max(1, min(int(limit), 200))
    anchor = scalar("SELECT MAX(trade_date) FROM lhb_detail")
    if anchor is None:
        return {
            "items": [],
            "by_seat_type": [],
            "total": 0,
            "days": days,
            "as_of": None,
            "meta": {"degraded": True, "reason": "lhb_detail 无数据"},
        }
    from datetime import timedelta  # 局部导入保留（模块级已引入，仅为可读性就近说明）

    cutoff = anchor - timedelta(days=days)

    total = (
        scalar("SELECT COUNT(DISTINCT ts_code) FROM lhb_detail WHERE trade_date >= %s",
               (cutoff,)) or 0
    )
    rows = _rows(
        """
        SELECT ts_code, stock_name,
               COUNT(*) AS records,
               COUNT(DISTINCT trade_date) AS day_count,
               SUM(buy_amt) AS buy_amt,
               SUM(sell_amt) AS sell_amt,
               SUM(net_amt) AS net_amt,
               BOOL_OR(is_inst) AS is_inst,
               array_remove(array_agg(DISTINCT reason), NULL) AS reasons,
               MAX(trade_date) AS last_date
        FROM lhb_detail
        WHERE trade_date >= %s
        GROUP BY ts_code, stock_name
        ORDER BY SUM(net_amt) DESC NULLS LAST
        LIMIT %s
        """,
        (cutoff, limit),
    )
    items = [
        {
            "rank": i + 1,
            "ts_code": r["ts_code"],
            "stock_name": r["stock_name"],
            "days": int(r["day_count"] or 0),
            "records": int(r["records"] or 0),
            "buy_amt": _f(r["buy_amt"]),
            "sell_amt": _f(r["sell_amt"]),
            "net_amt": _f(r["net_amt"]),
            "is_inst": bool(r["is_inst"]),
            "reasons": r["reasons"] or [],
            "last_date": str(r["last_date"]) if r["last_date"] else None,
        }
        for i, r in enumerate(rows)
    ]

    seats = _rows(
        """
        SELECT (CASE WHEN is_inst THEN '机构' ELSE '普通/游资席位' END) AS seat_type,
               COUNT(*) AS records,
               COUNT(DISTINCT ts_code) AS stock_count,
               SUM(net_amt) AS net_amt
        FROM lhb_detail
        WHERE trade_date >= %s
        GROUP BY 1
        ORDER BY SUM(net_amt) DESC NULLS LAST
        """,
        (cutoff,),
    )
    by_seat = [
        {
            "seat_type": s["seat_type"],
            "stock_count": int(s["stock_count"] or 0),
            "records": int(s["records"] or 0),
            "net_amt": _f(s["net_amt"]),
        }
        for s in seats
    ]

    return {
        "items": items,
        "by_seat_type": by_seat,
        "total": total,
        "days": days,
        "as_of": str(anchor),
        "meta": {
            "cutoff": str(cutoff),
            "sort": "SUM(net_amt) DESC",
            "note": "本地表无营业部/席位名称字段，故按 stock_name 聚合上榜原因，并以 is_inst 区分机构与普通席位；"
                    "部分日期 buy_amt/sell_amt 采集为 0，有效口径是净额 net_amt",
            "degraded": not items,
        },
    }
