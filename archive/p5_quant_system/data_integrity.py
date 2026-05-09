#!/usr/bin/env python3
"""
数据完整性验证模块 - 严格PIT（Point-In-Time）合规性检查

用户指出问题：pit_data_pipeline.py可能未做到真正的point-in-time严格性
典型问题：
1. 财务因子（ROE/利润率/资产周转等）在t日用了t+1、t+2甚至t+3季报
2. rolling window标准化用了全局mean/std
3. neutralization用了全市场未来数据

解决方案：强制每个历史截面独立重算，实现真正PIT
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set, Union, Callable
import warnings
import logging
from dataclasses import dataclass
from enum import Enum
import traceback

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PITViolationType(Enum):
    """PIT违规类型"""
    FINANCIAL_FUTURE_REPORT = "financial_future_report"  # 使用未来财报
    GLOBAL_STATISTICS = "global_statistics"  # 使用全局统计量
    MARKET_NEUTRAL_FUTURE = "market_neutral_future"  # 中性化用未来数据
    FEATURE_DATE_LEAKAGE = "feature_date_leakage"  # 特征日期泄露
    ROLLING_WINDOW_CONTAMINATION = "rolling_window_contamination"  # 滚动窗口污染


@dataclass
class PITValidationResult:
    """PIT验证结果"""
    is_valid: bool
    violation_type: Optional[PITViolationType]
    message: str
    severity: str  # 'critical', 'high', 'medium', 'low'
    details: Dict[str, Any]
    recommended_fix: str


class DataIntegrityValidator:
    """
    数据完整性验证器 - 确保严格PIT合规性
    
    核心要求：
    1. 强制所有因子计算函数接受 as_of_date 参数
    2. 财务因子内部只加载 report_date ≤ as_of_date 的最新财报
    3. 每个walk-forward fold独立重算所有统计量
    4. 实施严格的日期断言检查
    """
    
    def __init__(self, strict_mode: bool = True):
        """
        初始化验证器
        
        Args:
            strict_mode: 严格模式，发现PIT违规时抛出异常
        """
        self.strict_mode = strict_mode
        self.validation_results = []
        logger.info(f"初始化DataIntegrityValidator（严格模式: {strict_mode}）")
    
    def reset(self):
        """重置验证结果"""
        self.validation_results = []
    
    def validate_no_lookahead(self, 
                            df_factor: pd.DataFrame, 
                            as_of_date: pd.Timestamp,
                            context: str = "未知") -> PITValidationResult:
        """
        严格检查数据中是否包含未来信息
        
        关键检查点：
        1. 财务因子日期检查：assert (df_factor['report_date'] <= as_of_date).all()
        2. 特征日期检查：assert df_factor.index.get_level_values('date').max() <= as_of_date
        
        Args:
            df_factor: 特征数据DataFrame
            as_of_date: 截止日期（模拟时间点）
            context: 检查上下文（用于日志）
            
        Returns:
            验证结果
        """
        logger.info(f"验证PIT合规性 [上下文: {context}]，截止日期: {as_of_date.date()}")
        
        if df_factor is None or df_factor.empty:
            return PITValidationResult(
                is_valid=True,
                violation_type=None,
                message="数据为空，跳过检查",
                severity='low',
                details={'reason': 'empty_data'},
                recommended_fix="无"
            )
        
        violations = []
        details = {
            'as_of_date': as_of_date,
            'context': context,
            'df_shape': df_factor.shape,
            'df_columns': list(df_factor.columns)
        }
        
        # 1. 检查财务报告日期（如果有）
        if 'report_date' in df_factor.columns:
            try:
                report_dates = pd.to_datetime(df_factor['report_date'])
                future_reports = report_dates[report_dates > as_of_date]
                
                if len(future_reports) > 0:
                    violation_msg = (
                        f"发现{len(future_reports)}个未来财务报告: "
                        f"最早未来日期 {future_reports.min().date()} > 截止日期 {as_of_date.date()}"
                    )
                    violations.append({
                        'type': PITViolationType.FINANCIAL_FUTURE_REPORT,
                        'message': violation_msg,
                        'severity': 'critical',
                        'future_dates': future_reports.tolist(),
                        'future_count': len(future_reports)
                    })
                    details['financial_check'] = 'FAILED'
                else:
                    latest_report = report_dates.max()
                    details['financial_check'] = 'PASSED'
                    details['latest_report_date'] = latest_report
                    details['report_date_range'] = f"{report_dates.min().date()} - {latest_report.date()}"
            except Exception as e:
                logger.warning(f"财务报告日期检查失败: {e}")
                violations.append({
                    'type': PITViolationType.FINANCIAL_FUTURE_REPORT,
                    'message': f"财务报告日期检查失败: {e}",
                    'severity': 'medium',
                    'error': str(e)
                })
        
        # 2. 检查特征日期（如果有多级索引包含日期）
        try:
            # 尝试从索引中提取日期
            if hasattr(df_factor.index, 'names') and 'date' in df_factor.index.names:
                date_index = df_factor.index.get_level_values('date')
                max_date = pd.to_datetime(date_index).max()
                
                if max_date > as_of_date:
                    violation_msg = (
                        f"特征包含未来日期: 最大特征日期 {max_date.date()} > 截止日期 {as_of_date.date()}"
                    )
                    violations.append({
                        'type': PITViolationType.FEATURE_DATE_LEAKAGE,
                        'message': violation_msg,
                        'severity': 'critical',
                        'max_feature_date': max_date,
                        'leakage_days': (max_date - as_of_date).days
                    })
                    details['feature_date_check'] = 'FAILED'
                else:
                    details['feature_date_check'] = 'PASSED'
                    details['feature_date_range'] = f"{pd.to_datetime(date_index).min().date()} - {max_date.date()}"
            # 如果索引本身就是日期
            elif isinstance(df_factor.index, pd.DatetimeIndex):
                max_date = df_factor.index.max()
                if max_date > as_of_date:
                    violation_msg = (
                        f"特征包含未来日期: 最大特征日期 {max_date.date()} > 截止日期 {as_of_date.date()}"
                    )
                    violations.append({
                        'type': PITViolationType.FEATURE_DATE_LEAKAGE,
                        'message': violation_msg,
                        'severity': 'critical',
                        'max_feature_date': max_date,
                        'leakage_days': (max_date - as_of_date).days
                    })
                    details['feature_date_check'] = 'FAILED'
                else:
                    details['feature_date_check'] = 'PASSED'
                    details['feature_date_range'] = f"{df_factor.index.min().date()} - {max_date.date()}"
        except Exception as e:
            logger.warning(f"特征日期检查失败: {e}")
            details['feature_date_check'] = 'ERROR'
            details['feature_date_error'] = str(e)
        
        # 3. 检查是否使用全局统计量（启发式检查）
        if len(df_factor) > 100:  # 只有数据量足够大时才检查
            numeric_cols = df_factor.select_dtypes(include=[np.number]).columns
            
            for col in numeric_cols[:5]:  # 检查前5个数值列
                try:
                    series = df_factor[col].dropna()
                    if len(series) > 20:
                        # 计算整体和前半部分的统计量
                        global_mean = series.mean()
                        half_mean = series.iloc[:len(series)//2].mean()
                        
                        mean_diff = abs(global_mean - half_mean) / (abs(global_mean) + 1e-10)
                        
                        if mean_diff < 0.1:  # 差异小于10%，可能使用了全局标准化
                            violation_msg = (
                                f"列'{col}'可能使用了全局标准化: "
                                f"整体均值({global_mean:.4f}) ≈ 前半均值({half_mean:.4f})"
                            )
                            violations.append({
                                'type': PITViolationType.GLOBAL_STATISTICS,
                                'message': violation_msg,
                                'severity': 'medium',
                                'column': col,
                                'global_mean': global_mean,
                                'half_mean': half_mean,
                                'mean_diff_ratio': mean_diff
                            })
                            details[f'global_check_{col}'] = 'SUSPICIOUS'
                except Exception as e:
                    continue
        
        # 汇总结果
        if len(violations) == 0:
            result = PITValidationResult(
                is_valid=True,
                violation_type=None,
                message=f"PIT合规性检查通过（截止日期: {as_of_date.date()}）",
                severity='low',
                details=details,
                recommended_fix="无"
            )
        else:
            # 按严重程度排序
            severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
            violations.sort(key=lambda x: severity_order.get(x['severity'], 4))
            
            primary_violation = violations[0]
            result = PITValidationResult(
                is_valid=False,
                violation_type=primary_violation['type'],
                message=primary_violation['message'],
                severity=primary_violation['severity'],
                details={**details, 'all_violations': violations},
                recommended_fix=self._generate_recommended_fix(primary_violation['type'])
            )
            
            # 如果开启严格模式且有关键违规，抛出异常
            if self.strict_mode and primary_violation['severity'] in ['critical', 'high']:
                error_msg = f"PIT合规性检查失败: {primary_violation['message']}"
                if len(violations) > 1:
                    error_msg += f" (共发现{len(violations)}个违规)"
                raise ValueError(error_msg)
        
        self.validation_results.append(result)
        return result
    
    def validate_rolling_calculation(self,
                                   feature_series: pd.Series,
                                   window_size: int,
                                   as_of_date: pd.Timestamp,
                                   calculation_func: Callable) -> PITValidationResult:
        """
        验证滚动计算是否污染了未来信息
        
        Args:
            feature_series: 特征序列
            window_size: 滚动窗口大小
            as_of_date: 截止日期
            calculation_func: 计算函数（如mean, std等）
            
        Returns:
            验证结果
        """
        if len(feature_series) < window_size:
            return PITValidationResult(
                is_valid=True,
                violation_type=None,
                message=f"数据长度({len(feature_series)})小于窗口大小({window_size})，跳过检查",
                severity='low',
                details={
                    'data_length': len(feature_series),
                    'window_size': window_size,
                    'as_of_date': as_of_date
                },
                recommended_fix="无"
            )
        
        # 在截止日期之前的数据
        mask = feature_series.index <= as_of_date
        available_data = feature_series[mask]
        
        if len(available_data) < window_size:
            return PITValidationResult(
                is_valid=True,
                violation_type=None,
                message=f"可用数据长度({len(available_data)})小于窗口大小({window_size})",
                severity='low',
                details={
                    'available_length': len(available_data),
                    'window_size': window_size,
                    'as_of_date': as_of_date
                },
                recommended_fix="无"
            )
        
        # 执行滚动计算
        rolling_result = available_data.rolling(window=window_size).apply(calculation_func)
        
        # 检查是否有NaN值（表示窗口不足）
        nan_count = rolling_result.isna().sum()
        
        details = {
            'window_size': window_size,
            'as_of_date': as_of_date,
            'available_data_points': len(available_data),
            'rolling_result_points': len(rolling_result.dropna()),
            'nan_count': nan_count,
            'calculation_func': calculation_func.__name__ if hasattr(calculation_func, '__name__') else str(calculation_func)
        }
        
        if nan_count > len(available_data) * 0.5:  # 超过50%为NaN
            return PITValidationResult(
                is_valid=False,
                violation_type=PITViolationType.ROLLING_WINDOW_CONTAMINATION,
                message=f"滚动计算产生过多NaN值({nan_count}/{len(available_data)})，可能使用了未来数据填充",
                severity='medium',
                details=details,
                recommended_fix="确保滚动计算只使用截止日期之前的数据，不要用未来数据回填"
            )
        
        return PITValidationResult(
            is_valid=True,
            violation_type=None,
            message=f"滚动计算验证通过（窗口大小: {window_size}）",
            severity='low',
            details=details,
            recommended_fix="无"
        )
    
    def validate_financial_factor(self,
                                factor_data: pd.DataFrame,
                                financial_data: pd.DataFrame,
                                as_of_date: pd.Timestamp,
                                factor_name: str) -> PITValidationResult:
        """
        验证财务因子是否严格使用截至日期的财务数据
        
        Args:
            factor_data: 因子数据
            financial_data: 财务数据（必须包含report_date列）
            as_of_date: 截止日期
            factor_name: 因子名称
            
        Returns:
            验证结果
        """
        if 'report_date' not in financial_data.columns:
            return PITValidationResult(
                is_valid=False,
                violation_type=PITViolationType.FINANCIAL_FUTURE_REPORT,
                message=f"财务数据缺少report_date列，无法验证PIT合规性",
                severity='high',
                details={
                    'factor_name': factor_name,
                    'as_of_date': as_of_date,
                    'missing_column': 'report_date'
                },
                recommended_fix="财务数据必须包含report_date列，标识报告发布日期"
            )
        
        # 转换日期
        try:
            financial_data['report_date'] = pd.to_datetime(financial_data['report_date'])
            latest_report_date = financial_data['report_date'].max()
            
            if latest_report_date > as_of_date:
                return PITValidationResult(
                    is_valid=False,
                    violation_type=PITViolationType.FINANCIAL_FUTURE_REPORT,
                    message=(
                        f"财务因子'{factor_name}'使用了未来财报: "
                        f"最新报告日期{latest_report_date.date()} > 截止日期{as_of_date.date()}"
                    ),
                    severity='critical',
                    details={
                        'factor_name': factor_name,
                        'as_of_date': as_of_date,
                        'latest_report_date': latest_report_date,
                        'report_count': len(financial_data),
                        'report_date_range': f"{financial_data['report_date'].min().date()} - {latest_report_date.date()}"
                    },
                    recommended_fix=f"财务因子计算时使用: financial_data[financial_data['report_date'] <= '{as_of_date.date()}']"
                )
            
            return PITValidationResult(
                is_valid=True,
                violation_type=None,
                message=f"财务因子'{factor_name}'PIT验证通过，使用截至{as_of_date.date()}的财务数据",
                severity='low',
                details={
                    'factor_name': factor_name,
                    'as_of_date': as_of_date,
                    'latest_report_date': latest_report_date,
                    'report_count': len(financial_data),
                    'report_date_range': f"{financial_data['report_date'].min().date()} - {latest_report_date.date()}"
                },
                recommended_fix="无"
            )
            
        except Exception as e:
            return PITValidationResult(
                is_valid=False,
                violation_type=PITViolationType.FINANCIAL_FUTURE_REPORT,
                message=f"财务因子验证失败: {e}",
                severity='medium',
                details={
                    'factor_name': factor_name,
                    'as_of_date': as_of_date,
                    'error': str(e)
                },
                recommended_fix="确保财务数据的report_date列格式正确，可转换为datetime"
            )
    
    def _generate_recommended_fix(self, violation_type: PITViolationType) -> str:
        """根据违规类型生成修复建议"""
        fixes = {
            PITViolationType.FINANCIAL_FUTURE_REPORT: (
                "财务因子计算时严格筛选：financial_data[financial_data['report_date'] <= as_of_date]"
            ),
            PITViolationType.GLOBAL_STATISTICS: (
                "使用滚动窗口标准化代替全局标准化：rolling_mean = data.rolling(window).mean()"
            ),
            PITViolationType.MARKET_NEUTRAL_FUTURE: (
                "中性化时只使用截至as_of_date的市场数据，不要用全样本"
            ),
            PITViolationType.FEATURE_DATE_LEAKAGE: (
                "特征计算时检查日期：assert feature_data.index.max() <= as_of_date"
            ),
            PITViolationType.ROLLING_WINDOW_CONTAMINATION: (
                "滚动计算时不要用未来数据回填，接受窗口期的NaN值"
            )
        }
        
        return fixes.get(violation_type, "检查数据预处理流程，确保符合PIT原则")
    
    def generate_validation_report(self) -> str:
        """生成验证报告"""
        if not self.validation_results:
            return "未执行验证检查"
        
        report = []
        report.append("=" * 80)
        report.append("数据完整性验证报告 - PIT合规性检查")
        report.append("=" * 80)
        
        total_checks = len(self.validation_results)
        passed_checks = sum(1 for r in self.validation_results if r.is_valid)
        failed_checks = total_checks - passed_checks
        
        critical_failures = sum(1 for r in self.validation_results 
                               if not r.is_valid and r.severity in ['critical', 'high'])
        
        report.append(f"\n📊 检查汇总")
        report.append(f"   总检查项: {total_checks}")
        report.append(f"   通过项: {passed_checks} ({passed_checks/total_checks*100:.1f}%)")
        report.append(f"   失败项: {failed_checks} ({failed_checks/total_checks*100:.1f}%)")
        report.append(f"   关键失败: {critical_failures}")
        
        if critical_failures > 0:
            report.append(f"   ⚠️  存在关键PIT违规，回测结果不可信！")
        
        report.append(f"\n🔍 详细检查结果")
        for i, result in enumerate(self.validation_results, 1):
            status = "✅" if result.is_valid else "❌"
            report.append(f"\n  {i}. {status} {result.message}")
            if not result.is_valid:
                report.append(f"     违规类型: {result.violation_type.value}")
                report.append(f"     严重程度: {result.severity}")
                report.append(f"     修复建议: {result.recommended_fix}")
        
        # 关键建议
        report.append(f"\n💡 关键建议")
        
        if critical_failures > 0:
            report.append("   1. 立即修复所有关键PIT违规")
            report.append("   2. 重新实现财务因子，严格筛选report_date")
            report.append("   3. 确保每个walk-forward fold独立重算所有统计量")
            report.append("   4. 实施自动化PIT测试，防止未来函数引入")
        elif failed_checks > 0:
            report.append("   1. 审查中等风险问题，评估影响")
            report.append("   2. 优化特征工程流程，避免全局标准化")
            report.append("   3. 加强数据预处理检查")
        else:
            report.append("   1. 当前PIT实现良好，保持现有流程")
            report.append("   2. 定期运行完整性验证")
            report.append("   3. 考虑实施更严格的实时PIT检查")
        
        report.append(f"\n" + "=" * 80)
        
        return "\n".join(report)


# 装饰器：强制函数接收as_of_date参数并进行PIT检查
def pit_strict(func):
    """
    PIT严格性装饰器
    
    强制被装饰函数：
    1. 必须接受as_of_date参数
    2. 返回前进行PIT合规性检查
    3. 记录PIT验证结果
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 检查是否有as_of_date参数
        as_of_date = None
        
        # 从kwargs中查找
        if 'as_of_date' in kwargs:
            as_of_date = kwargs['as_of_date']
        else:
            # 从args中查找（根据函数签名）
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            
            if 'as_of_date' in params:
                idx = params.index('as_of_date')
                if idx < len(args):
                    as_of_date = args[idx]
        
        if as_of_date is None:
            raise ValueError(f"函数{func.__name__}必须接收as_of_date参数以进行PIT检查")
        
        # 执行原函数
        result = func(*args, **kwargs)
        
        # 如果返回的是DataFrame，进行PIT检查
        if isinstance(result, pd.DataFrame):
            validator = DataIntegrityValidator(strict_mode=False)
            validation = validator.validate_no_lookahead(result, as_of_date, f"函数{func.__name__}")
            
            if not validation.is_valid:
                logger.warning(f"函数{func.__name__}可能违反PIT原则: {validation.message}")
        
        return result
    
    return wrapper


# 示例：符合PIT原则的财务因子计算函数
@pit_strict
def calculate_roe_factor_pit(financial_data: pd.DataFrame, 
                           as_of_date: pd.Timestamp,
                           report_date_col: str = 'report_date',
                           net_income_col: str = 'net_income',
                           equity_col: str = 'shareholders_equity') -> pd.DataFrame:
    """
    PIT安全的ROE因子计算
    
    严格使用截至as_of_date的最新财务报告
    """
    # 筛选截至日期的财务数据
    valid_data = financial_data[financial_data[report_date_col] <= as_of_date]
    
    if valid_data.empty:
        raise ValueError(f"在{as_of_date.date()}之前没有可用的财务数据")
    
    # 获取每个公司的最新报告
    latest_reports = valid_data.sort_values(report_date_col).groupby('symbol').last()
    
    # 计算ROE
    roe_series = latest_reports[net_income_col] / latest_reports[equity_col]
    
    # 创建结果DataFrame
    result = pd.DataFrame({
        'roe': roe_series,
        'report_date': latest_reports[report_date_col],
        'calculation_date': as_of_date
    })
    
    return result


# 测试函数
def test_data_integrity():
    """测试DataIntegrityValidator"""
    print("=== 测试数据完整性验证器 ===")
    
    validator = DataIntegrityValidator(strict_mode=False)
    
    # 创建测试数据
    np.random.seed(42)
    
    # 1. 模拟有未来财报的数据
    dates = pd.date_range('2020-01-01', '2023-12-31', freq='Q')
    financial_data = pd.DataFrame({
        'symbol': ['600519'] * len(dates),
        'report_date': dates,
        'roe': np.random.randn(len(dates)) * 0.1 + 0.15,
        'profit_margin': np.random.randn(len(dates)) * 0.05 + 0.2
    })
    
    as_of_date = pd.Timestamp('2022-06-30')
    
    print(f"\n测试1: 验证财务数据（截止日期: {as_of_date.date()}）")
    result1 = validator.validate_financial_factor(
        factor_data=financial_data,
        financial_data=financial_data,
        as_of_date=as_of_date,
        factor_name='ROE因子'
    )
    print(f"结果: {'通过' if result1.is_valid else '失败'} - {result1.message}")
    
    # 2. 测试PIT合规性检查
    print(f"\n测试2: 验证无未来信息泄露")
    
    # 创建包含未来日期的数据
    future_dates = pd.date_range('2022-01-01', '2023-12-31', freq='D')
    future_data = pd.DataFrame({
        'value': np.random.randn(len(future_dates)),
        'report_date': future_dates
    }, index=future_dates)
    
    result2 = validator.validate_no_lookahead(
        df_factor=future_data,
        as_of_date=as_of_date,
        context="测试未来数据泄露"
    )
    print(f"结果: {'通过' if result2.is_valid else '失败'} - {result2.message}")
    
    # 3. 测试符合PIT的数据
    print(f"\n测试3: 验证符合PIT的数据")
    
    past_dates = pd.date_range('2020-01-01', '2022-06-30', freq='D')
    past_data = pd.DataFrame({
        'value': np.random.randn(len(past_dates)),
        'report_date': past_dates
    }, index=past_dates)
    
    result3 = validator.validate_no_lookahead(
        df_factor=past_data,
        as_of_date=as_of_date,
        context="测试过去数据"
    )
    print(f"结果: {'通过' if result3.is_valid else '失败'} - {result3.message}")
    
    # 4. 测试装饰器
    print(f"\n测试4: 测试PIT装饰器")
    
    try:
        # 使用装饰器函数
        factor_result = calculate_roe_factor_pit(
            financial_data=financial_data,
            as_of_date=as_of_date
        )
        print(f"装饰器测试通过，返回形状: {factor_result.shape}")
    except Exception as e:
        print(f"装饰器测试失败: {e}")
    
    # 生成报告
    print(f"\n=== 验证报告 ===")
    report = validator.generate_validation_report()
    print(report)
    
    # 测试严格模式
    print(f"\n=== 测试严格模式 ===")
    strict_validator = DataIntegrityValidator(strict_mode=True)
    
    try:
        strict_result = strict_validator.validate_no_lookahead(
            df_factor=future_data,
            as_of_date=as_of_date,
            context="严格模式测试"
        )
        print("严格模式测试通过（应抛出异常）")
    except ValueError as e:
        print(f"严格模式正确捕获错误: {str(e)[:100]}...")


if __name__ == "__main__":
    test_data_integrity()