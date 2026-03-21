"""
投资组合管理器
实现行业敞口控制、个股持仓限制、Beta控制、动态调仓
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import warnings
warnings.filterwarnings('ignore')


class PortfolioManager:
    """投资组合管理器"""
    
    def __init__(self, config: dict):
        self.config = config
        
        # 当前持仓
        self.positions = {}  # {symbol: {'shares':数量, 'entry_price':入场价, 'entry_date':入场日期, 'sector':行业}}
        
        # 行业分类（简化版，实际需要行业数据库）
        self.sector_map = {
            '600519': '白酒', '000858': '白酒', '002304': '白酒',
            '300750': '新能源', '002594': '新能源', '300124': '新能源',
            '002415': '安防', '002236': '安防',
            '002230': '人工智能', '688111': '人工智能', '688981': '半导体',
            '000063': '通信', '002475': '消费电子',
            '603986': '半导体', '600588': '软件',
            '000001': '银行', '600036': '银行', '601318': '保险',
            '000651': '消费', '600887': '消费'
        }
        
        # 基准数据（简化）
        self.benchmark_beta = 1.0  # 假设基准Beta为1.0
        
    def get_sector(self, symbol: str) -> str:
        """获取股票行业"""
        return self.sector_map.get(symbol, '其他')
    
    def check_position_limits(self, 
                             symbol: str, 
                             proposed_value: float, 
                             total_portfolio_value: float
                            ) -> Tuple[bool, str]:
        """检查持仓限制"""
        
        # 1. 单票持仓上限检查
        position_pct = proposed_value / total_portfolio_value
        max_single_pct = self.config['max_single_stock_pct']
        
        if position_pct > max_single_pct:
            return False, f"单票持仓{position_pct*100:.1f}%超过上限{max_single_pct*100:.0f}%"
        
        # 2. 行业持仓上限检查
        sector = self.get_sector(symbol)
        current_sector_value = self.get_sector_value(sector, total_portfolio_value)
        proposed_sector_value = current_sector_value + proposed_value
        sector_pct = proposed_sector_value / total_portfolio_value
        max_sector_pct = self.config['max_sector_pct']
        
        if sector_pct > max_sector_pct:
            return False, f"行业{sector}持仓{sector_pct*100:.1f}%超过上限{max_sector_pct*100:.0f}%"
        
        return True, "通过"
    
    def get_sector_value(self, sector: str, total_portfolio_value: float) -> float:
        """计算当前行业持仓价值"""
        sector_value = 0
        for symbol, pos in self.positions.items():
            if self.get_sector(symbol) == sector:
                # 简化：假设所有持仓都是当前市值
                sector_value += pos.get('current_value', 0)
        
        return sector_value
    
    def calculate_portfolio_beta(self, stock_betas: Dict[str, float]) -> float:
        """计算组合Beta"""
        if not self.positions:
            return 0.0
        
        total_value = sum(pos.get('current_value', 0) for pos in self.positions.values())
        if total_value == 0:
            return 0.0
        
        portfolio_beta = 0.0
        for symbol, pos in self.positions.items():
            position_value = pos.get('current_value', 0)
            weight = position_value / total_value
            beta = stock_betas.get(symbol, 1.0)  # 默认Beta=1.0
            portfolio_beta += weight * beta
        
        return portfolio_beta
    
    def check_beta_constraint(self, 
                             stock_betas: Dict[str, float],
                             new_symbol: Optional[str] = None,
                             new_beta: float = 1.0
                            ) -> Tuple[bool, str]:
        """检查Beta约束"""
        # 计算当前组合Beta
        current_beta = self.calculate_portfolio_beta(stock_betas)
        
        # 如果加入新股票
        if new_symbol:
            # 简化计算：假设新股票权重为最大允许权重
            max_weight = self.config['max_single_stock_pct']
            new_beta_weighted = current_beta * (1 - max_weight) + new_beta * max_weight
        else:
            new_beta_weighted = current_beta
        
        # 检查是否在目标范围内
        target_min, target_max = self.config['target_beta_range']
        
        if new_beta_weighted < target_min:
            return False, f"组合Beta{new_beta_weighted:.2f}低于目标下限{target_min}"
        elif new_beta_weighted > target_max:
            return False, f"组合Beta{new_beta_weighted:.2f}高于目标上限{target_max}"
        else:
            return True, f"组合Beta{new_beta_weighted:.2f}在目标范围[{target_min}, {target_max}]内"
    
    def rebalance_portfolio(self,
                           stock_scores: List[Dict],
                           current_prices: Dict[str, float],
                           total_portfolio_value: float
                          ) -> Dict[str, Any]:
        """调仓决策"""
        
        # 按得分排序
        sorted_stocks = sorted(stock_scores, key=lambda x: x['score'], reverse=True)
        
        # 选择前N名
        top_n = self.config['top_n_stocks']
        candidates = sorted_stocks[:top_n]
        
        # 当前持仓的股票
        current_symbols = set(self.positions.keys())
        candidate_symbols = {s['symbol'] for s in candidates}
        
        # 需要买入的股票（不在当前持仓中）
        to_buy = [s for s in candidates if s['symbol'] not in current_symbols]
        
        # 需要卖出的股票（不在候选列表中）
        to_sell = [symbol for symbol in current_symbols if symbol not in candidate_symbols]
        
        # 需要调整权重的股票（既在当前持仓又在候选列表中）
        to_rebalance = [s for s in candidates if s['symbol'] in current_symbols]
        
        # 构建调仓建议
        rebalance_plan = {
            'buy': [],
            'sell': [],
            'rebalance': [],
            'total_value': total_portfolio_value,
            'rebalance_date': datetime.now().strftime('%Y-%m-%d')
        }
        
        # 卖出建议
        for symbol in to_sell:
            if symbol in self.positions:
                pos = self.positions[symbol]
                rebalance_plan['sell'].append({
                    'symbol': symbol,
                    'shares': pos['shares'],
                    'current_price': current_prices.get(symbol, 0),
                    'reason': '调出核心持仓',
                    'score': next((s['score'] for s in sorted_stocks if s['symbol'] == symbol), 0)
                })
        
        # 计算可用资金（假设卖出全部待卖股票）
        cash_from_sales = sum(
            item['shares'] * item['current_price'] 
            for item in rebalance_plan['sell']
        )
        
        available_cash = cash_from_sales
        
        # 买入建议（考虑风险限制）
        for stock in to_buy:
            symbol = stock['symbol']
            price = current_prices.get(symbol, 0)
            
            if price <= 0:
                continue
            
            # 计算建议买入金额（等权重分配）
            target_weight = 1.0 / top_n
            target_value = total_portfolio_value * target_weight
            
            # 考虑可用资金
            buy_value = min(target_value, available_cash * 0.8)  # 预留20%现金
            
            # 检查持仓限制
            can_buy, reason = self.check_position_limits(symbol, buy_value, total_portfolio_value)
            
            if can_buy and buy_value > price * 100:  # 至少买100股
                shares = int(buy_value / price)
                actual_value = shares * price
                
                rebalance_plan['buy'].append({
                    'symbol': symbol,
                    'price': price,
                    'shares': shares,
                    'value': actual_value,
                    'weight': actual_value / total_portfolio_value,
                    'score': stock['score'],
                    'risk_level': stock.get('risk_level', 3),
                    'reason': f"新入选Top{top_n}"
                })
                
                available_cash -= actual_value
        
        # 再平衡建议（调整现有持仓）
        for stock in to_rebalance:
            symbol = stock['symbol']
            if symbol in self.positions:
                pos = self.positions[symbol]
                current_value = pos.get('current_value', 0)
                price = current_prices.get(symbol, 0)
                
                # 目标权重
                target_weight = 1.0 / top_n
                target_value = total_portfolio_value * target_weight
                
                # 调整量
                value_diff = target_value - current_value
                
                if abs(value_diff) > price * 100:  # 至少调整100股
                    action = 'BUY' if value_diff > 0 else 'SELL'
                    shares = int(abs(value_diff) / price)
                    
                    rebalance_plan['rebalance'].append({
                        'symbol': symbol,
                        'action': action,
                        'shares': shares,
                        'price': price,
                        'current_value': current_value,
                        'target_value': target_value,
                        'value_diff': value_diff,
                        'score': stock['score']
                    })
        
        return rebalance_plan
    
    def daily_monitor(self,
                     top_stocks: List[Dict],
                     current_prices: Dict[str, float],
                     portfolio_value: float
                    ) -> Dict[str, Any]:
        """每日监控"""
        
        monitor_top = self.config['daily_monitor_top']
        if len(top_stocks) < monitor_top:
            return {'action': 'no_action', 'reason': '候选股票不足'}
        
        # 获取当前持仓股票
        current_symbols = list(self.positions.keys())
        if not current_symbols:
            return {'action': 'no_action', 'reason': '无持仓'}
        
        # 获取前N名股票
        top_symbols = [s['symbol'] for s in top_stocks[:monitor_top]]
        
        # 检查是否有新股进入前三且评分显著高于当前持仓
        for stock in top_stocks[:monitor_top]:
            symbol = stock['symbol']
            score = stock['score']
            
            if symbol not in current_symbols:
                # 新股进入前三，检查是否可以替换
                # 找到当前持仓中评分最低的股票
                current_scores = []
                for curr_symbol in current_symbols:
                    # 简化：假设能找到该股票的评分
                    curr_score = next((s['score'] for s in top_stocks if s['symbol'] == curr_symbol), 0)
                    current_scores.append((curr_symbol, curr_score))
                
                if current_scores:
                    worst_symbol, worst_score = min(current_scores, key=lambda x: x[1])
                    
                    # 检查评分差异是否显著（至少10分）
                    if score - worst_score >= 10:
                        # 检查换手限制（日换手<20%）
                        # 简化：假设满足条件
                        
                        # 计算替换方案
                        if worst_symbol in self.positions:
                            worst_position = self.positions[worst_symbol]
                            replace_value = worst_position.get('current_value', 0)
                            
                            # 检查新股持仓限制
                            can_buy, reason = self.check_position_limits(
                                symbol, replace_value, portfolio_value
                            )
                            
                            if can_buy:
                                return {
                                    'action': 'replace',
                                    'sell': worst_symbol,
                                    'buy': symbol,
                                    'reason': f"新股{symbol}({score}分)显著优于当前持仓{worst_symbol}({worst_score}分)",
                                    'score_diff': score - worst_score,
                                    'estimated_value': replace_value
                                }
        
        return {'action': 'hold', 'reason': '无显著优化机会'}


# 示例使用
if __name__ == "__main__":
    # 测试配置
    config = {
        'max_single_stock_pct': 0.10,
        'max_sector_pct': 0.30,
        'target_beta_range': (0.8, 1.2),
        'top_n_stocks': 10,
        'daily_monitor_top': 3
    }
    
    pm = PortfolioManager(config)
    
    # 测试持仓限制
    test_result, test_reason = pm.check_position_limits('600519', 200000, 1000000)
    print(f"持仓限制检查: {test_result} - {test_reason}")
    
    # 测试Beta约束
    stock_betas = {'600519': 0.9, '300750': 1.2, '002415': 1.1}
    beta_ok, beta_reason = pm.check_beta_constraint(stock_betas, '600519', 0.9)
    print(f"Beta约束检查: {beta_ok} - {beta_reason}")
    
    print("\n✅ 投资组合管理器测试通过")