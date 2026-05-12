"""
漏斗选股引擎 — 主编排器
=========================
七层漏斗串联执行：
  Layer 0 → 1 → 2 → 3 → 4 → 5 → 6 → 输出

核心纪律：每晚复盘，任一步未严格满足即推倒重来，
连续3次止损失败则暂停交易一天。

吸收策略：④严格执行纪律/复盘强化规则
"""
from __future__ import annotations

import csv
import sys
import os
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from .funnel_config import FunnelConfig, DEFAULT_FUNNEL_CONFIG
from .layer0_market_guard import check_market_environment
from .layer1_fundamental_filter import run_layer1_fundamental_filter
from .layer2_liquidity_filter import run_layer2_liquidity_filter
from .layer3_trend_filter import run_layer3_trend_filter
from .layer4_momentum_filter import run_layer4_momentum_filter
from .layer5_popularity_filter import run_layer5_popularity_filter
from .layer6_risk_control import run_layer6_risk_control

from core.db.connection import get_db
from psycopg2.extras import RealDictCursor

BEIJING_TZ = timezone(timedelta(hours=8))


def load_universe(trade_date: date, min_amount: float = 1e8) -> List[str]:
    """从 daily_quotes 加载全市场初筛股票池（日成交额>1亿）"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ts_code
            FROM daily_quotes
            WHERE trade_date = %s AND amount > %s
            ORDER BY ts_code;
        """, (trade_date, min_amount))
        codes = [row['ts_code'] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return codes
    except Exception as e:
        print(f"❌ 全市场加载失败: {e}")
        return []


class FunnelEngine:
    """七步漏斗选股引擎"""

    def __init__(self, cfg: FunnelConfig = None):
        self.cfg = cfg or DEFAULT_FUNNEL_CONFIG
        self.errors = self.cfg.validate()
        if self.errors:
            print(f"⚠️ 配置验证有 {len(self.errors)} 个问题:")
            for e in self.errors:
                print(f"  - {e}")

        self._stats = {
            'layer0_pass': False,
            'layer0_max_position': 1.0,
            'input_count': 0,
            'layer1_pass': 0,
            'layer2_pass': 0,
            'layer3_pass': 0,
            'layer4_pass': 0,
            'layer5_pass': 0,
            'layer6_pass': 0,
        }

    def run(
        self,
        trade_date: date = None,
        custom_universe: List[str] = None,
    ) -> Dict:
        """
        执行完整七步漏斗。
        
        返回:
          {
            'timestamp': str,
            'trade_date': str,
            'market_env': dict,       # Layer 0 结果
            'stats': dict,            # 各层统计
            'candidates': List[dict], # 最终推荐
            'layers_detail': dict,    # 各层详情（用于复盘）
          }
        """
        cfg = self.cfg

        if trade_date is None:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else date.today()
            cur.close()
            conn.close()

        timestamp = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

        print("\n" + "=" * 70)
        print("  🎯 七步漏斗选股引擎 v1.0")
        print("=" * 70)
        print(f"  运行时间: {timestamp}")
        print(f"  交易日期: {trade_date}")
        print(f"  活跃层级: {', '.join(cfg.enabled_layers)}")
        print("=" * 70)

        # ════════════════════════════════════════════════════════
        # Layer 0: 大盘风控
        # ════════════════════════════════════════════════════════
        if cfg.layer0_enabled:
            market_env = check_market_environment(
                trade_date=trade_date,
                min_advancers=cfg.layer0_min_advancers,
                index_code=cfg.layer0_index_code,
                ema_period=cfg.layer0_index_ema_period,
                partial_cap=cfg.layer0_partial_cap,
                verbose=cfg.verbose,
            )
            self._stats['layer0_pass'] = market_env['passed']
            self._stats['layer0_max_position'] = market_env['max_position_pct']

            if not market_env['can_trade']:
                print(f"\n{'='*70}")
                print(f"  ❌ Layer 0 大盘风控未通过: {market_env['reason']}")
                print(f"  → 当日不荐股，流程终止")
                print(f"{'='*70}\n")
                return {
                    'timestamp': timestamp,
                    'trade_date': str(trade_date),
                    'market_env': market_env,
                    'stats': self._stats,
                    'candidates': [],
                    'layers_detail': {'L0': market_env},
                }
        else:
            market_env = {'passed': True, 'can_trade': True, 'max_position_pct': 1.0}

        # ════════════════════════════════════════════════════════
        # 加载全市场股票池
        # ════════════════════════════════════════════════════════
        if custom_universe:
            stock_list = custom_universe
        else:
            stock_list = load_universe(trade_date, min_amount=1e8)

        self._stats['input_count'] = len(stock_list)

        if not stock_list:
            print("❌ 股票池为空，无法继续")
            return {
                'timestamp': timestamp,
                'trade_date': str(trade_date),
                'market_env': market_env,
                'stats': self._stats,
                'candidates': [],
                'layers_detail': {'L0': market_env},
            }

        print(f"\n  全市场初筛: {len(stock_list)} 只 (日成交额>1亿)")

        # ════════════════════════════════════════════════════════
        # Layer 1: 硬性防雷
        # ════════════════════════════════════════════════════════
        l1_result = run_layer1_fundamental_filter(
            stock_list, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer1_pass'] = len(l1_result)

        if not l1_result:
            print("\n❌ Layer 1 无标的通过防雷筛选")
            return {
                'timestamp': timestamp, 'trade_date': str(trade_date),
                'market_env': market_env, 'stats': self._stats,
                'candidates': [], 'layers_detail': {'L0': market_env, 'L1': l1_result},
            }

        # ════════════════════════════════════════════════════════
        # Layer 2: 流动性筛选
        # ════════════════════════════════════════════════════════
        l2_result = run_layer2_liquidity_filter(
            l1_result, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer2_pass'] = len(l2_result)

        if not l2_result:
            print("\n❌ Layer 2 无标的通过流动性筛选")
            return {
                'timestamp': timestamp, 'trade_date': str(trade_date),
                'market_env': market_env, 'stats': self._stats,
                'candidates': [],
                'layers_detail': {'L0': market_env, 'L1': l1_result, 'L2': l2_result},
            }

        # ════════════════════════════════════════════════════════
        # Layer 3: 趋势结构过滤
        # ════════════════════════════════════════════════════════
        l3_result = run_layer3_trend_filter(
            l2_result, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer3_pass'] = len(l3_result)

        if not l3_result:
            print("\n❌ Layer 3 无标的通过趋势过滤")
            return {
                'timestamp': timestamp, 'trade_date': str(trade_date),
                'market_env': market_env, 'stats': self._stats,
                'candidates': [],
                'layers_detail': {'L0': market_env, 'L1': l1_result, 'L2': l2_result, 'L3': l3_result},
            }

        # ════════════════════════════════════════════════════════
        # Layer 4: 动能与买入信号
        # ════════════════════════════════════════════════════════
        l4_stock_list = [item['ts_code'] for item in l3_result]
        l4_result = run_layer4_momentum_filter(
            l4_stock_list, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer4_pass'] = len(l4_result)

        if not l4_result:
            print("\n❌ Layer 4 无标的出现买入信号")
            return {
                'timestamp': timestamp, 'trade_date': str(trade_date),
                'market_env': market_env, 'stats': self._stats,
                'candidates': [],
                'layers_detail': {'L0': market_env, 'L1': l1_result, 'L2': l2_result,
                                   'L3': l3_result, 'L4': l4_result},
            }

        # 合并 L3 trend_bonus 到 L4 结果
        l3_bonus_map = {item['ts_code']: item.get('score_bonus', 0) for item in l3_result}
        for item in l4_result:
            item['trend_bonus'] = l3_bonus_map.get(item['ts_code'], 0)

        # ════════════════════════════════════════════════════════
        # Layer 5: 人气精选
        # ════════════════════════════════════════════════════════
        l5_result = run_layer5_popularity_filter(
            l4_result, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer5_pass'] = len(l5_result)

        if not l5_result:
            print("\n❌ Layer 5 无标的通过人气评分")
            return {
                'timestamp': timestamp, 'trade_date': str(trade_date),
                'market_env': market_env, 'stats': self._stats,
                'candidates': [],
                'layers_detail': {'L0': market_env, 'L1': l1_result, 'L2': l2_result,
                                   'L3': l3_result, 'L4': l4_result, 'L5': l5_result},
            }

        # ════════════════════════════════════════════════════════
        # Layer 6: 刚性风控
        # ════════════════════════════════════════════════════════
        l6_result = run_layer6_risk_control(
            l5_result, trade_date, cfg, verbose=cfg.verbose
        )
        self._stats['layer6_pass'] = len(l6_result)

        # ════════════════════════════════════════════════════════
        # 输出最终结果
        # ════════════════════════════════════════════════════════
        output_dir = Path(cfg.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        out_path = output_dir / f"funnel_{today_str}.csv"

        if l6_result:
            with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'code', 'score', 'pct', 'entry_price', 'atr', 'stop_loss',
                    'target_price', 'profit_loss_ratio', 'signal_type', 'tags',
                    'time_window_ok', 'max_position_pct',
                ])
                for item in l6_result:
                    writer.writerow([
                        item.get('ts_code', ''),
                        item.get('score', 0),
                        item.get('pct', 0),
                        item.get('entry_price', 0),
                        item.get('atr', 0),
                        item.get('stop_loss', 0),
                        item.get('target_price', 0),
                        item.get('profit_loss_ratio', 0),
                        item.get('signal_type', ''),
                        '|'.join(item.get('tags', [])),
                        item.get('time_window_ok', False),
                        self._stats['layer0_max_position'],
                    ])

        # 打印漏斗摘要
        self._print_funnel_summary()

        # 打印最终推荐
        if l6_result:
            print(f"\n{'='*70}")
            print(f"  🎯 最终推荐 ({len(l6_result)} 只)")
            print(f"{'='*70}")
            print(f"  {'代码':<14} {'入场价':>8} {'评分':>5} {'ATR':>7} "
                  f"{'止损':>8} {'目标':>8} {'盈亏比':>6}  信号")
            print(f"  {'─'*80}")
            for item in l6_result:
                tags_str = ', '.join(item.get('tags', [])[:3])
                print(f"  {item['ts_code']:<14} {item['entry_price']:>8.2f} "
                      f"{item['score']:>5.0f} {item['atr']:>7.3f} "
                      f"{item['stop_loss']:>8.2f} {item['target_price']:>8.2f} "
                      f"{item['profit_loss_ratio']:>6.1f}  {item.get('signal_type', '')} "
                      f"[{tags_str}]")

            print(f"\n  💡 操作指引")
            print(f"  入场时段: ≥{cfg.layer6_entry_after_time}")
            print(f"  仓位上限: {int(self._stats['layer0_max_position']*100)}%")
            print(f"  初始止损: 入场价 - {cfg.layer6_initial_stop_atr}ATR")
            print(f"  移动止盈: 参考{cfg.layer6_trailing_ref}")
            print(f"  最低盈亏比: {cfg.layer6_min_profit_loss_ratio}:1")
            print(f"  纪律: 每晚复盘，连续{cfg.discipline_max_consecutive_fails}次止损失败暂停一天")
            print(f"  结果文件: {out_path}")
            print(f"{'='*70}\n")
        else:
            print(f"\n{'='*70}")
            print(f"  ❌ 无标的通过全部七层漏斗")
            print(f"  → 当日不荐股，等待更好机会")
            print(f"{'='*70}\n")

        return {
            'timestamp': timestamp,
            'trade_date': str(trade_date),
            'market_env': market_env,
            'stats': self._stats,
            'candidates': l6_result,
            'layers_detail': {
                'L0': market_env,
                'L1_pass_count': len(l1_result),
                'L2_pass_count': len(l2_result),
                'L3_pass_count': len(l3_result),
                'L4_pass_count': len(l4_result),
                'L5_pass_count': len(l5_result),
                'L6_pass_count': len(l6_result),
            },
        }

    def _print_funnel_summary(self):
        """打印漏斗统计"""
        s = self._stats
        print(f"\n  📊 漏斗统计")
        print(f"  {'─'*50}")
        print(f"  全市场初筛: {s['input_count']:>6} 只")
        print(f"  Layer 0 大盘: {'✅通过' if s['layer0_pass'] else '⚠️限仓'}  "
              f"(仓位≤{int(s['layer0_max_position']*100)}%)")
        print(f"  Layer 1 防雷: {s['layer1_pass']:>6} 只 (淘汰{s['input_count']-s['layer1_pass']})")
        prev = s['input_count']
        for i, key in enumerate([('layer1_pass', 'L1防雷'), ('layer2_pass', 'L2流动'),
                                  ('layer3_pass', 'L3趋势'), ('layer4_pass', 'L4动能'),
                                  ('layer5_pass', 'L5人气'), ('layer6_pass', 'L6风控')], 2):
            curr = s[key[0]]
            if curr == 0 and prev == 0:
                continue
            print(f"  Layer {i} {key[1]}: {curr:>6} 只 (淘汰{prev-curr})" if i > 1 else "")
            prev = curr


def run_funnel_strategy(
    trade_date: date = None,
    cfg: FunnelConfig = None,
    custom_universe: List[str] = None,
) -> Dict:
    """便捷函数：一键运行七步漏斗"""
    engine = FunnelEngine(cfg)
    return engine.run(trade_date=trade_date, custom_universe=custom_universe)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="七步漏斗选股引擎")
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期 (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", type=str, default="./results",
                        help="输出目录")
    parser.add_argument("--disable", nargs="*", type=int, default=[],
                        help="禁用的层级 (0-6)")
    args = parser.parse_args()

    cfg = DEFAULT_FUNNEL_CONFIG
    cfg.output_dir = args.output
    for layer_num in (args.disable or []):
        if 0 <= layer_num <= 6:
            setattr(cfg, f'layer{layer_num}_enabled', False)

    trade_date = None
    if args.date:
        trade_date = date.fromisoformat(args.date)

    result = run_funnel_strategy(trade_date=trade_date, cfg=cfg)
    sys.exit(0 if result['candidates'] else 1)
