"""
风险管理系统核心模块
实时风险监控、预警和限额管理
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
import json
import asyncio
from enum import Enum
import logging
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"        # 低风险
    MEDIUM = "medium"  # 中风险
    HIGH = "high"      # 高风险
    CRITICAL = "critical"  # 临界风险


class AlertType(Enum):
    """告警类型"""
    INFO = "info"          # 信息
    WARNING = "warning"    # 警告
    ERROR = "error"        # 错误
    CRITICAL = "critical"  # 严重


class RiskMetricType(Enum):
    """风险指标类型"""
    PORTFOLIO_VAR = "portfolio_var"          # 投资组合VaR
    PORTFOLIO_CVAR = "portfolio_cvar"        # 投资组合CVaR
    MAX_DRAWDOWN = "max_drawdown"            # 最大回撤
    VOLATILITY = "volatility"                # 波动率
    BETA = "beta"                            # Beta系数
    SHARPE_RATIO = "sharpe_ratio"            # 夏普比率
    SORTINO_RATIO = "sortino_ratio"          # 索提诺比率
    CONCENTRATION = "concentration"          # 集中度风险
    LIQUIDITY_RISK = "liquidity_risk"        # 流动性风险
    LEVERAGE_RATIO = "leverage_ratio"        # 杠杆率
    VALUE_AT_RISK = "value_at_risk"          # 在险价值
    EXPECTED_SHORTFALL = "expected_shortfall"  # 预期缺口


@dataclass
class RiskMetric:
    """风险指标"""
    metric_type: RiskMetricType
    value: float
    timestamp: datetime
    confidence_level: float = 0.95  # 置信水平
    time_horizon: int = 1  # 时间周期（天）
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskLimit:
    """风险限额"""
    metric_type: RiskMetricType
    limit_value: float
    alert_threshold: float = 0.8  # 告警阈值（限额的百分比）
    is_hard_limit: bool = False   # 是否硬性限制
    description: str = ""
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class RiskAlert:
    """风险告警"""
    alert_id: str
    alert_type: AlertType
    metric_type: RiskMetricType
    current_value: float
    limit_value: float
    breach_percent: float  # 突破百分比
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    actions_taken: List[str] = field(default_factory=list)


class PortfolioRiskAnalyzer:
    """投资组合风险分析器"""
    
    def __init__(self, confidence_level: float = 0.95, time_horizon: int = 1):
        self.confidence_level = confidence_level
        self.time_horizon = time_horizon
        
        # 风险指标缓存
        self.metrics_history: Dict[RiskMetricType, List[RiskMetric]] = {}
        
        # 协方差矩阵缓存
        self.covariance_matrix = None
        self.correlation_matrix = None
        
        logger.info(f"投资组合风险分析器初始化 (置信水平: {confidence_level}, 时间周期: {time_horizon}天)")
    
    def calculate_portfolio_risk(self, positions: Dict[str, Dict[str, float]],
                                historical_returns: pd.DataFrame,
                                risk_free_rate: float = 0.02) -> Dict[RiskMetricType, RiskMetric]:
        """计算投资组合风险指标"""
        metrics = {}
        timestamp = datetime.now()
        
        if not positions or historical_returns.empty:
            return metrics
        
        try:
            # 提取持仓信息
            symbols = list(positions.keys())
            weights = np.array([pos['weight'] for pos in positions.values()])
            market_values = np.array([pos['market_value'] for pos in positions.values()])
            
            # 提取相关股票的历史收益
            portfolio_returns = self._calculate_portfolio_returns(
                symbols, weights, historical_returns
            )
            
            if portfolio_returns is None or len(portfolio_returns) < 30:
                logger.warning("历史数据不足，无法计算风险指标")
                return metrics
            
            # 计算基础统计量
            portfolio_std = portfolio_returns.std() * np.sqrt(252)  # 年化波动率
            portfolio_mean = portfolio_returns.mean() * 252  # 年化收益率
            
            # 1. 计算VaR (历史模拟法)
            var_historical = self._calculate_var_historical(portfolio_returns)
            metrics[RiskMetricType.VALUE_AT_RISK] = RiskMetric(
                metric_type=RiskMetricType.VALUE_AT_RISK,
                value=var_historical,
                timestamp=timestamp,
                confidence_level=self.confidence_level,
                time_horizon=self.time_horizon
            )
            
            # 2. 计算CVaR (Expected Shortfall)
            cvar = self._calculate_cvar(portfolio_returns)
            metrics[RiskMetricType.EXPECTED_SHORTFALL] = RiskMetric(
                metric_type=RiskMetricType.EXPECTED_SHORTFALL,
                value=cvar,
                timestamp=timestamp,
                confidence_level=self.confidence_level,
                time_horizon=self.time_horizon
            )
            
            # 3. 计算最大回撤
            max_drawdown = self._calculate_max_drawdown(portfolio_returns)
            metrics[RiskMetricType.MAX_DRAWDOWN] = RiskMetric(
                metric_type=RiskMetricType.MAX_DRAWDOWN,
                value=max_drawdown,
                timestamp=timestamp
            )
            
            # 4. 计算波动率
            metrics[RiskMetricType.VOLATILITY] = RiskMetric(
                metric_type=RiskMetricType.VOLATILITY,
                value=portfolio_std,
                timestamp=timestamp
            )
            
            # 5. 计算夏普比率
            if portfolio_std > 0:
                sharpe_ratio = (portfolio_mean - risk_free_rate) / portfolio_std
                metrics[RiskMetricType.SHARPE_RATIO] = RiskMetric(
                    metric_type=RiskMetricType.SHARPE_RATIO,
                    value=sharpe_ratio,
                    timestamp=timestamp
                )
            
            # 6. 计算索提诺比率（仅考虑下行风险）
            downside_std = portfolio_returns[portfolio_returns < 0].std() * np.sqrt(252)
            if downside_std > 0:
                sortino_ratio = (portfolio_mean - risk_free_rate) / downside_std
                metrics[RiskMetricType.SORTINO_RATIO] = RiskMetric(
                    metric_type=RiskMetricType.SORTINO_RATIO,
                    value=sortino_ratio,
                    timestamp=timestamp
                )
            
            # 7. 计算集中度风险（赫芬达尔指数）
            concentration = self._calculate_concentration_risk(weights)
            metrics[RiskMetricType.CONCENTRATION] = RiskMetric(
                metric_type=RiskMetricType.CONCENTRATION,
                value=concentration,
                timestamp=timestamp
            )
            
            # 8. 计算流动性风险（基于持仓市值和平均成交量）
            liquidity_risk = self._calculate_liquidity_risk(positions, historical_returns)
            metrics[RiskMetricType.LIQUIDITY_RISK] = RiskMetric(
                metric_type=RiskMetricType.LIQUIDITY_RISK,
                value=liquidity_risk,
                timestamp=timestamp
            )
            
            # 保存到历史记录
            for metric_type, metric in metrics.items():
                if metric_type not in self.metrics_history:
                    self.metrics_history[metric_type] = []
                self.metrics_history[metric_type].append(metric)
            
            logger.info(f"投资组合风险计算完成: {len(metrics)}个指标")
            
        except Exception as e:
            logger.error(f"风险计算失败: {e}")
        
        return metrics
    
    def _calculate_portfolio_returns(self, symbols: List[str], weights: np.ndarray,
                                    historical_returns: pd.DataFrame) -> Optional[pd.Series]:
        """计算投资组合收益序列"""
        try:
            # 确保权重和为1
            weights = weights / weights.sum()
            
            # 提取相关股票的收益
            portfolio_returns = pd.Series(0, index=historical_returns.index)
            
            for i, symbol in enumerate(symbols):
                if symbol in historical_returns.columns:
                    portfolio_returns += weights[i] * historical_returns[symbol]
                else:
                    logger.warning(f"股票{symbol}的历史收益数据不存在")
            
            return portfolio_returns
        
        except Exception as e:
            logger.error(f"计算投资组合收益失败: {e}")
            return None
    
    def _calculate_var_historical(self, returns: pd.Series) -> float:
        """计算历史模拟法VaR"""
        if len(returns) < 100:
            # 数据不足时使用参数法估计
            return abs(returns.mean() - returns.std() * 2.33)  # 99%置信水平
        
        # 历史分位数法
        var = np.percentile(returns, (1 - self.confidence_level) * 100)
        return abs(var)
    
    def _calculate_cvar(self, returns: pd.Series) -> float:
        """计算CVaR (Expected Shortfall)"""
        if len(returns) < 100:
            # 数据不足时简单估计
            return abs(returns.mean() - returns.std() * 2.67)  # 近似99% CVaR
        
        var = np.percentile(returns, (1 - self.confidence_level) * 100)
        cvar = returns[returns <= var].mean()
        return abs(cvar)
    
    def _calculate_max_drawdown(self, returns: pd.Series) -> float:
        """计算最大回撤"""
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return abs(drawdown.min())
    
    def _calculate_concentration_risk(self, weights: np.ndarray) -> float:
        """计算集中度风险（赫芬达尔指数）"""
        # HHI = sum(weight_i^2)
        hhi = np.sum(weights ** 2)
        # 归一化到0-1范围
        n = len(weights)
        if n > 1:
            normalized_hhi = (hhi - 1/n) / (1 - 1/n)
        else:
            normalized_hhi = 1.0
        return normalized_hhi
    
    def _calculate_liquidity_risk(self, positions: Dict[str, Dict[str, float]],
                                 historical_returns: pd.DataFrame) -> float:
        """计算流动性风险"""
        if not positions or historical_returns.empty:
            return 0.0
        
        try:
            liquidity_scores = []
            
            for symbol, position in positions.items():
                if symbol in historical_returns.columns:
                    # 使用波动率作为流动性代理指标
                    volatility = historical_returns[symbol].std() * np.sqrt(252)
                    
                    # 考虑持仓市值
                    market_value = position.get('market_value', 0)
                    weight = position.get('weight', 0)
                    
                    # 流动性风险分数（越高风险越大）
                    # 波动率高 + 持仓市值大 = 流动性风险高
                    liquidity_score = volatility * weight
                    liquidity_scores.append(liquidity_score)
            
            if liquidity_scores:
                return np.mean(liquidity_scores)
            
        except Exception as e:
            logger.error(f"计算流动性风险失败: {e}")
        
        return 0.0
    
    def stress_test(self, positions: Dict[str, Dict[str, float]],
                   historical_returns: pd.DataFrame,
                   stress_scenarios: List[Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        """压力测试"""
        results = {}
        
        for scenario_name, scenario in enumerate(stress_scenarios):
            scenario_returns = historical_returns.copy()
            
            # 应用压力情景
            for symbol, shock in scenario.items():
                if symbol in scenario_returns.columns:
                    scenario_returns[symbol] = scenario_returns[symbol] * (1 + shock)
            
            # 计算压力情景下的风险指标
            stress_metrics = self.calculate_portfolio_risk(positions, scenario_returns)
            
            # 汇总结果
            results[f"scenario_{scenario_name}"] = {
                metric_type.value: metric.value
                for metric_type, metric in stress_metrics.items()
            }
        
        return results
    
    def get_risk_report(self, time_period: str = "1d") -> Dict[str, Any]:
        """生成风险报告"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'time_period': time_period,
            'metrics': {},
            'trends': {},
            'recommendations': []
        }
        
        # 汇总最新风险指标
        for metric_type, history in self.metrics_history.items():
            if history:
                latest = history[-1]
                report['metrics'][metric_type.value] = {
                    'value': latest.value,
                    'confidence_level': latest.confidence_level,
                    'time_horizon': latest.time_horizon,
                    'timestamp': latest.timestamp.isoformat()
                }
        
        # 分析趋势
        report['trends'] = self._analyze_risk_trends()
        
        # 生成建议
        report['recommendations'] = self._generate_recommendations()
        
        return report
    
    def _analyze_risk_trends(self) -> Dict[str, Any]:
        """分析风险趋势"""
        trends = {}
        
        for metric_type, history in self.metrics_history.items():
            if len(history) >= 5:
                recent_values = [m.value for m in history[-5:]]
                
                # 计算变化趋势
                if len(recent_values) >= 2:
                    change = recent_values[-1] - recent_values[-2]
                    percent_change = change / abs(recent_values[-2]) if recent_values[-2] != 0 else 0
                    
                    # 判断趋势
                    if abs(percent_change) < 0.01:
                        trend = "stable"
                    elif percent_change > 0:
                        trend = "increasing"
                    else:
                        trend = "decreasing"
                    
                    trends[metric_type.value] = {
                        'current_value': recent_values[-1],
                        'change': change,
                        'percent_change': percent_change,
                        'trend': trend
                    }
        
        return trends
    
    def _generate_recommendations(self) -> List[str]:
        """生成风险控制建议"""
        recommendations = []
        
        # 检查最新风险指标
        latest_metrics = {}
        for metric_type, history in self.metrics_history.items():
            if history:
                latest_metrics[metric_type] = history[-1].value
        
        # 基于VaR的建议
        if RiskMetricType.VALUE_AT_RISK in latest_metrics:
            var = latest_metrics[RiskMetricType.VALUE_AT_RISK]
            if var > 0.05:  # VaR超过5%
                recommendations.append("VaR过高，建议降低仓位或增加对冲")
        
        # 基于最大回撤的建议
        if RiskMetricType.MAX_DRAWDOWN in latest_metrics:
            max_dd = latest_metrics[RiskMetricType.MAX_DRAWDOWN]
            if max_dd > 0.2:  # 最大回撤超过20%
                recommendations.append("最大回撤过大，建议设置止损或调整策略")
        
        # 基于集中度的建议
        if RiskMetricType.CONCENTRATION in latest_metrics:
            concentration = latest_metrics[RiskMetricType.CONCENTRATION]
            if concentration > 0.5:  # 集中度超过0.5
                recommendations.append("投资组合过于集中，建议分散投资")
        
        # 基于流动性的建议
        if RiskMetricType.LIQUIDITY_RISK in latest_metrics:
            liquidity_risk = latest_metrics[RiskMetricType.LIQUIDITY_RISK]
            if liquidity_risk > 0.1:  # 流动性风险过高
                recommendations.append("流动性风险较高，建议减少低流动性资产持仓")
        
        return recommendations


class RiskMonitor:
    """风险监控器"""
    
    def __init__(self):
        self.risk_limits: Dict[RiskMetricType, RiskLimit] = {}
        self.active_alerts: Dict[str, RiskAlert] = {}
        self.alert_history: List[RiskAlert] = []
        
        # 预警规则
        self.alert_rules = {}
        
        # 初始化默认风险限额
        self._initialize_default_limits()
        
        logger.info("风险监控器初始化完成")
    
    def _initialize_default_limits(self):
        """初始化默认风险限额"""
        default_limits = {
            RiskMetricType.VALUE_AT_RISK: RiskLimit(
                metric_type=RiskMetricType.VALUE_AT_RISK,
                limit_value=0.05,  # VaR不超过5%
                alert_threshold=0.8,
                description="单日VaR限额"
            ),
            RiskMetricType.MAX_DRAWDOWN: RiskLimit(
                metric_type=RiskMetricType.MAX_DRAWDOWN,
                limit_value=0.2,  # 最大回撤不超过20%
                alert_threshold=0.8,
                description="最大回撤限额"
            ),
            RiskMetricType.CONCENTRATION: RiskLimit(
                metric_type=RiskMetricType.CONCENTRATION,
                limit_value=0.5,  # 集中度不超过0.5
                alert_threshold=0.8,
                description="集中度风险限额"
            ),
            RiskMetricType.VOLATILITY: RiskLimit(
                metric_type=RiskMetricType.VOLATILITY,
                limit_value=0.3,  # 年化波动率不超过30%
                alert_threshold=0.8,
                description="波动率限额"
            ),
            RiskMetricType.LEVERAGE_RATIO: RiskLimit(
                metric_type=RiskMetricType.LEVERAGE_RATIO,
                limit_value=2.0,  # 杠杆率不超过2倍
                alert_threshold=0.8,
                description="杠杆率限额",
                is_hard_limit=True
            )
        }
        
        self.risk_limits.update(default_limits)
    
    def set_risk_limit(self, metric_type: RiskMetricType, limit_value: float,
                      alert_threshold: float = 0.8, is_hard_limit: bool = False,
                      description: str = ""):
        """设置风险限额"""
        limit = RiskLimit(
            metric_type=metric_type,
            limit_value=limit_value,
            alert_threshold=alert_threshold,
            is_hard_limit=is_hard_limit,
            description=description
        )
        
        self.risk_limits[metric_type] = limit
        logger.info(f"设置风险限额: {metric_type.value} = {limit_value}")
    
    def check_risk_metrics(self, risk_metrics: Dict[RiskMetricType, RiskMetric]) -> List[RiskAlert]:
        """检查风险指标是否超出限额"""
        new_alerts = []
        
        for metric_type, metric in risk_metrics.items():
            if metric_type not in self.risk_limits:
                continue
            
            limit = self.risk_limits[metric_type]
            current_value = metric.value
            
            # 计算突破百分比（对于风险指标，值越大风险越高）
            if limit.limit_value > 0:
                breach_percent = current_value / limit.limit_value
            else:
                breach_percent = 1.0
            
            # 检查是否触发告警
            if breach_percent > limit.alert_threshold:
                # 确定告警级别
                if breach_percent >= 1.0:
                    alert_type = AlertType.CRITICAL if limit.is_hard_limit else AlertType.ERROR
                elif breach_percent >= 0.9:
                    alert_type = AlertType.ERROR
                elif breach_percent >= 0.8:
                    alert_type = AlertType.WARNING
                else:
                    alert_type = AlertType.INFO
                
                # 生成告警消息
                message = self._generate_alert_message(metric_type, current_value, 
                                                      limit.limit_value, breach_percent)
                
                # 创建告警
                alert_id = f"alert_{metric_type.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                alert = RiskAlert(
                    alert_id=alert_id,
                    alert_type=alert_type,
                    metric_type=metric_type,
                    current_value=current_value,
                    limit_value=limit.limit_value,
                    breach_percent=breach_percent,
                    message=message
                )
                
                new_alerts.append(alert)
                
                # 保存告警
                self.active_alerts[alert_id] = alert
                self.alert_history.append(alert)
                
                logger.warning(f"风险告警: {message}")
        
        return new_alerts
    
    def _generate_alert_message(self, metric_type: RiskMetricType, current_value: float,
                               limit_value: float, breach_percent: float) -> str:
        """生成告警消息"""
        metric_name = metric_type.value.replace('_', ' ').title()
        
        if breach_percent >= 1.0:
            severity = "严重超出"
        elif breach_percent >= 0.9:
            severity = "接近超出"
        else:
            severity = "达到预警线"
        
        return f"{metric_name} {severity}: 当前值={current_value:.4f}, 限额={limit_value:.4f}, 超出={breach_percent:.1%}"
    
    def acknowledge_alert(self, alert_id: str, user: str, action_taken: str = ""):
        """确认告警"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.is_acknowledged = True
            alert.acknowledged_by = user
            alert.acknowledged_at = datetime.now()
            
            if action_taken:
                alert.actions_taken.append(action_taken)
            
            logger.info(f"告警已确认: {alert_id} by {user}")
    
    def get_active_alerts(self, alert_type: Optional[AlertType] = None) -> List[RiskAlert]:
        """获取活动告警"""
        alerts = list(self.active_alerts.values())
        if alert_type:
            alerts = [a for a in alerts if a.alert_type == alert_type]
        return alerts
    
    def get_alert_summary(self) -> Dict[str, Any]:
        """获取告警摘要"""
        total_alerts = len(self.alert_history)
        active_alerts = len(self.active_alerts)
        acknowledged_alerts = sum(1 for a in self.active_alerts.values() if a.is_acknowledged)
        
        # 按类型统计
        by_type = {}
        for alert_type in AlertType:
            count = sum(1 for a in self.active_alerts.values() if a.alert_type == alert_type)
            by_type[alert_type.value] = count
        
        return {
            'total_alerts': total_alerts,
            'active_alerts': active_alerts,
            'acknowledged_alerts': acknowledged_alerts,
            'alerts_by_type': by_type,
            'timestamp': datetime.now().isoformat()
        }


class RiskManager:
    """风险管理主控制器"""
    
    def __init__(self, trading_engine=None):
        self.trading_engine = trading_engine
        self.risk_analyzer = PortfolioRiskAnalyzer()
        self.risk_monitor = RiskMonitor()
        
        # 监控状态
        self.is_monitoring = False
        self.monitoring_interval = 60  # 监控间隔（秒）
        
        # 数据存储
        self.risk_history = []
        
        logger.info("风险管理系统初始化完成")
    
    def start_monitoring(self, interval: int = 60):
        """启动风险监控"""
        self.is_monitoring = True
        self.monitoring_interval = interval
        
        # 这里应该启动定时监控任务
        # 简化实现：记录启动状态
        logger.info(f"风险监控已启动，间隔: {interval}秒")
    
    def stop_monitoring(self):
        """停止风险监控"""
        self.is_monitoring = False
        logger.info("风险监控已停止")
    
    def update_portfolio_risk(self, positions: Dict[str, Dict[str, float]],
                             historical_returns: pd.DataFrame):
        """更新投资组合风险"""
        try:
            # 计算风险指标
            risk_metrics = self.risk_analyzer.calculate_portfolio_risk(
                positions, historical_returns
            )
            
            # 检查风险限额
            alerts = self.risk_monitor.check_risk_metrics(risk_metrics)
            
            # 保存风险数据
            risk_snapshot = {
                'timestamp': datetime.now(),
                'positions': positions,
                'risk_metrics': {
                    mt.value: {
                        'value': m.value,
                        'confidence_level': m.confidence_level
                    }
                    for mt, m in risk_metrics.items()
                },
                'alerts': [a.to_dict() for a in alerts] if alerts and hasattr(alerts[0], 'to_dict') else alerts
            }
            
            self.risk_history.append(risk_snapshot)
            
            # 如果有关键告警，触发风险控制动作
            self._handle_critical_alerts(alerts)
            
            return risk_metrics, alerts
            
        except Exception as e:
            logger.error(f"更新投资组合风险失败: {e}")
            return {}, []
    
    def _handle_critical_alerts(self, alerts: List[RiskAlert]):
        """处理关键告警"""
        critical_alerts = [a for a in alerts if a.alert_type == AlertType.CRITICAL]
        
        if not critical_alerts or not self.trading_engine:
            return
        
        for alert in critical_alerts:
            logger.critical(f"处理关键风险告警: {alert.message}")
            
            # 根据风险类型采取不同措施
            if alert.metric_type == RiskMetricType.LEVERAGE_RATIO:
                # 杠杆率过高，强制平仓
                self._force_reduce_leverage()
            elif alert.metric_type == RiskMetricType.VALUE_AT_RISK:
                # VaR过高，降低仓位
                self._reduce_position_size()
            elif alert.metric_type == RiskMetricType.MAX_DRAWDOWN:
                # 回撤过大，设置止损
                self._set_stop_loss()
    
    def _force_reduce_leverage(self):
        """强制降低杠杆"""
        logger.warning("执行强制降杠杆操作")
        # 这里应该调用交易引擎执行减仓操作
        # 简化实现：记录操作
        
    def _reduce_position_size(self):
        """降低仓位规模"""
        logger.warning("执行降低仓位操作")
        
    def _set_stop_loss(self):
        """设置止损"""
        logger.warning("执行设置止损操作")
    
    def stress_test_portfolio(self, positions: Dict[str, Dict[str, float]],
                             historical_returns: pd.DataFrame,
                             scenarios: List[Dict[str, float]] = None) -> Dict[str, Any]:
        """投资组合压力测试"""
        if scenarios is None:
            # 默认压力情景
            scenarios = [
                {"market_crash": -0.2},  # 市场暴跌20%
                {"interest_rate_shock": 0.02},  # 利率上升2%
                {"liquidity_crisis": -0.3},  # 流动性危机30%
                {"sector_crash": {"600519": -0.15, "000858": -0.2}}  # 行业暴跌
            ]
        
        stress_results = self.risk_analyzer.stress_test(
            positions, historical_returns, scenarios
        )
        
        return {
            'timestamp': datetime.now().isoformat(),
            'scenarios': scenarios,
            'results': stress_results,
            'worst_case': self._identify_worst_case(stress_results)
        }
    
    def _identify_worst_case(self, stress_results: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        """识别最坏情况"""
        if not stress_results:
            return {}
        
        # 找出VaR最大的情景
        worst_scenario = None
        worst_var = -float('inf')
        
        for scenario_name, results in stress_results.items():
            if 'value_at_risk' in results and results['value_at_risk'] > worst_var:
                worst_var = results['value_at_risk']
                worst_scenario = scenario_name
        
        if worst_scenario:
            return {
                'scenario': worst_scenario,
                'value_at_risk': worst_var,
                'metrics': stress_results[worst_scenario]
            }
        
        return {}
    
    def generate_risk_report(self, period: str = "1d") -> Dict[str, Any]:
        """生成风险报告"""
        report = {
            'summary': self.risk_monitor.get_alert_summary(),
            'risk_analysis': self.risk_analyzer.get_risk_report(period),
            'risk_limits': {
                mt.value: {
                    'limit_value': limit.limit_value,
                    'alert_threshold': limit.alert_threshold,
                    'is_hard_limit': limit.is_hard_limit,
                    'description': limit.description
                }
                for mt, limit in self.risk_monitor.risk_limits.items()
            },
            'active_alerts': [
                {
                    'alert_id': alert.alert_id,
                    'alert_type': alert.alert_type.value,
                    'metric_type': alert.metric_type.value,
                    'current_value': alert.current_value,
                    'limit_value': alert.limit_value,
                    'breach_percent': alert.breach_percent,
                    'message': alert.message,
                    'timestamp': alert.timestamp.isoformat()
                }
                for alert in self.risk_monitor.get_active_alerts()
            ],
            'recommendations': self._generate_risk_recommendations()
        }
        
        return report
    
    def _generate_risk_recommendations(self) -> List[str]:
        """生成风险管理建议"""
        recommendations = []
        
        # 获取风险分析器的建议
        risk_recs = self.risk_analyzer._generate_recommendations()
        recommendations.extend(risk_recs)
        
        # 基于告警的建议
        active_alerts = self.risk_monitor.get_active_alerts()
        if active_alerts:
            critical_count = sum(1 for a in active_alerts if a.alert_type == AlertType.CRITICAL)
            if critical_count > 0:
                recommendations.append(f"有{critical_count}个严重风险告警，建议立即处理")
        
        # 通用建议
        if len(recommendations) < 3:
            recommendations.extend([
                "定期进行压力测试，评估极端市场情况下的风险敞口",
                "建立动态风险限额，根据市场波动调整风险容忍度",
                "考虑使用衍生品工具进行风险对冲"
            ])
        
        return recommendations[:5]  # 返回前5条建议