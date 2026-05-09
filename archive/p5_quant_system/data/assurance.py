#!/usr/bin/env python3
"""
DataAssurance - 数据质量保证与未来函数检查
专门检测和防止Walk-forward回测中的look-ahead bias（未来函数）

常见未来函数错误：
1. 因子标准化使用全局统计量（global z-score）而非滚动窗口统计量
2. 因子IC/IR计算时使用了未来信息
3. 财务因子未严格使用t-1期报告期数据
4. LightGBM训练时特征未严格滞后（label_date.min() <= train_end）
5. 特征数据包含未来日期的信息（feature_date.max() > train_end）

解决方法：
1. 强制所有因子在每个滚动窗口内只使用截至训练期最后一天的信息重新计算
2. 财务因子必须严格使用 report_date ≤ train_end 的最新一期数据
3. 实现严格的静态检查：assert feature_date.max() <= train_end, assert label_date.min() > train_end
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any, Set, Union
from datetime import datetime, timedelta
import warnings
import logging
from dataclasses import dataclass
from enum import Enum

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FutureFunctionType(Enum):
    """未来函数类型"""
    GLOBAL_ZSCORE = "global_zscore"  # 因子标准化使用全局统计量
    FEATURE_LEAKAGE = "feature_leakage"  # 特征数据泄露未来信息
    FINANCIAL_DATE_MISMATCH = "financial_date_mismatch"  # 财务因子日期不匹配
    LABEL_LEAKAGE = "label_leakage"  # 标签数据泄露未来信息
    IC_CALC_LEAKAGE = "ic_calc_leakage"  # IC计算泄露未来信息
    ROLLING_WINDOW_ERROR = "rolling_window_error"  # 滚动窗口错误


@dataclass
class FutureFunctionCheck:
    """未来函数检查结果"""
    check_type: FutureFunctionType
    is_passed: bool
    message: str
    severity: str  # 'high', 'medium', 'low'
    details: Dict[str, Any]


class DataAssurance:
    """数据质量保证与未来函数检查器"""
    
    def __init__(self, strict_mode: bool = True):
        """
        初始化DataAssurance
        
        Args:
            strict_mode: 严格模式，发现未来函数时抛出异常
        """
        self.strict_mode = strict_mode
        self.checks = []
        self.violations = []
        
        logger.info("初始化DataAssurance检查器（严格模式: %s）", strict_mode)
    
    def reset(self):
        """重置检查结果"""
        self.checks = []
        self.violations = []
    
    def check_walkforward_period(self,
                               train_start: pd.Timestamp,
                               train_end: pd.Timestamp,
                               test_start: pd.Timestamp,
                               test_end: pd.Timestamp,
                               features_df: pd.DataFrame = None,
                               labels_df: pd.DataFrame = None,
                               financial_data: pd.DataFrame = None) -> List[FutureFunctionCheck]:
        """
        检查Walk-forward期间的数据质量
        
        Args:
            train_start: 训练集开始日期
            train_end: 训练集结束日期
            test_start: 测试集开始日期
            test_end: 测试集结束日期
            features_df: 特征数据DataFrame（索引为日期，列为特征）
            labels_df: 标签数据DataFrame（索引为日期，列为标签）
            financial_data: 财务数据DataFrame（包含report_date列）
            
        Returns:
            检查结果列表
        """
        logger.info(f"检查Walk-forward期间: Train[{train_start.date()} - {train_end.date()}]")
        
        self.reset()
        
        # 1. 检查时间顺序
        self._check_temporal_order(train_start, train_end, test_start, test_end)
        
        # 2. 检查特征数据（如果有）
        if features_df is not None:
            self._check_feature_leakage(features_df, train_end)
            self._check_feature_normalization(features_df, train_start, train_end)
        
        # 3. 检查标签数据（如果有）
        if labels_df is not None:
            self._check_label_leakage(labels_df, train_end, test_start)
        
        # 4. 检查财务数据（如果有）
        if financial_data is not None:
            self._check_financial_data(financial_data, train_end)
        
        # 5. 汇总检查结果
        summary_check = self._create_summary_check()
        self.checks.append(summary_check)
        
        # 6. 如果有严重违规且开启严格模式，抛出异常
        if self.strict_mode and self._has_critical_violations():
            critical_violations = [v for v in self.violations if v.severity == 'high']
            error_msg = f"发现{len(critical_violations)}个严重未来函数错误:\n"
            for violation in critical_violations[:3]:  # 显示前3个
                error_msg += f"  - {violation.message}\n"
            raise ValueError(error_msg)
        
        return self.checks
    
    def _check_temporal_order(self,
                            train_start: pd.Timestamp,
                            train_end: pd.Timestamp,
                            test_start: pd.Timestamp,
                            test_end: pd.Timestamp) -> FutureFunctionCheck:
        """检查时间顺序：确保训练集在测试集之前"""
        
        is_passed = True
        messages = []
        
        # 检查训练集在测试集之前
        if train_end >= test_start:
            is_passed = False
            messages.append(f"训练集结束日期({train_end.date()})不应晚于或等于测试集开始日期({test_start.date()})")
        
        # 检查测试集在训练集之后
        if test_start <= train_end:
            is_passed = False
            messages.append(f"测试集开始日期({test_start.date()})应晚于训练集结束日期({train_end.date()})")
        
        # 检查日期范围有效性
        if train_start >= train_end:
            is_passed = False
            messages.append(f"训练集开始日期({train_start.date()})不应晚于或等于结束日期({train_end.date()})")
        
        if test_start >= test_end:
            is_passed = False
            messages.append(f"测试集开始日期({test_start.date()})不应晚于或等于结束日期({test_end.date()})")
        
        message = "时间顺序检查通过" if is_passed else "; ".join(messages)
        severity = 'high' if not is_passed else 'low'
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.ROLLING_WINDOW_ERROR,
            is_passed=is_passed,
            message=message,
            severity=severity,
            details={
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end,
                'issues': messages if not is_passed else []
            }
        )
        
        if not is_passed:
            self.violations.append(check)
        
        self.checks.append(check)
        return check
    
    def _check_feature_leakage(self, features_df: pd.DataFrame, train_end: pd.Timestamp) -> FutureFunctionCheck:
        """
        检查特征数据泄露：确保特征日期不超过训练集结束日期
        
        关键检查：assert feature_date.max() <= train_end
        """
        
        if features_df.empty:
            return FutureFunctionCheck(
                check_type=FutureFunctionType.FEATURE_LEAKAGE,
                is_passed=True,
                message="特征数据为空，跳过泄露检查",
                severity='low',
                details={'reason': 'empty_data'}
            )
        
        # 获取特征数据的最大日期
        feature_dates = features_df.index
        if isinstance(feature_dates, pd.DatetimeIndex):
            max_feature_date = feature_dates.max()
        else:
            # 尝试转换为日期
            try:
                max_feature_date = pd.to_datetime(feature_dates).max()
            except:
                logger.warning("无法解析特征数据日期，跳过泄露检查")
                return FutureFunctionCheck(
                    check_type=FutureFunctionType.FEATURE_LEAKAGE,
                    is_passed=False,
                    message="无法解析特征数据日期",
                    severity='medium',
                    details={'error': 'date_parsing_failed'}
                )
        
        # 检查是否存在未来信息泄露
        is_passed = max_feature_date <= train_end
        
        if not is_passed:
            # 计算泄露的天数
            leakage_days = (max_feature_date - train_end).days
            message = (f"特征数据泄露未来信息: 最大特征日期({max_feature_date.date()}) "
                      f"> 训练集结束日期({train_end.date()}), 泄露{leakage_days}天")
        else:
            message = f"特征数据无泄露: 最大特征日期({max_feature_date.date()}) ≤ 训练集结束日期({train_end.date()})"
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.FEATURE_LEAKAGE,
            is_passed=is_passed,
            message=message,
            severity='high' if not is_passed else 'low',
            details={
                'max_feature_date': max_feature_date,
                'train_end': train_end,
                'leakage_days': (max_feature_date - train_end).days if not is_passed else 0,
                'feature_date_range': f"{feature_dates.min().date()} - {feature_dates.max().date()}"
            }
        )
        
        if not is_passed:
            self.violations.append(check)
        
        self.checks.append(check)
        return check
    
    def _check_feature_normalization(self, 
                                   features_df: pd.DataFrame,
                                   train_start: pd.Timestamp,
                                   train_end: pd.Timestamp) -> FutureFunctionCheck:
        """
        检查因子标准化方法：确保使用滚动窗口标准化而非全局标准化
        
        常见错误：在整个样本上计算z-score（使用全局均值和标准差）
        正确做法：在训练窗口内计算滚动统计量
        """
        
        # 这里无法直接检查计算过程，但可以检查是否存在明显的全局标准化模式
        # 通过检查特征在整个时间序列上的统计量是否异常稳定来判断
        
        is_passed = True
        messages = []
        suspicious_features = []
        
        # 对每个特征进行简单检查
        for col in features_df.columns:
            # 计算整个序列的均值和标准差
            global_mean = features_df[col].mean()
            global_std = features_df[col].std()
            
            # 计算训练窗口内的均值和标准差
            train_mask = (features_df.index >= train_start) & (features_df.index <= train_end)
            if train_mask.sum() > 0:
                train_mean = features_df.loc[train_mask, col].mean()
                train_std = features_df.loc[train_mask, col].std()
                
                # 如果全局和训练窗口的统计量差异很大，可能使用了全局标准化
                mean_ratio = abs(global_mean - train_mean) / (abs(train_mean) + 1e-10)
                std_ratio = abs(global_std - train_std) / (train_std + 1e-10)
                
                if mean_ratio > 0.5 or std_ratio > 0.5:
                    suspicious_features.append({
                        'feature': col,
                        'global_mean': global_mean,
                        'train_mean': train_mean,
                        'global_std': global_std,
                        'train_std': train_std,
                        'mean_ratio': mean_ratio,
                        'std_ratio': std_ratio
                    })
                    is_passed = False
        
        if suspicious_features:
            message = (f"发现{len(suspicious_features)}个特征可能使用了全局标准化: "
                      f"{[f['feature'] for f in suspicious_features[:3]]}")
            if len(suspicious_features) > 3:
                message += f" 等{len(suspicious_features)}个特征"
            severity = 'medium'
        else:
            message = "特征标准化检查通过（未发现明显全局标准化模式）"
            severity = 'low'
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.GLOBAL_ZSCORE,
            is_passed=is_passed,
            message=message,
            severity=severity,
            details={
                'suspicious_features': suspicious_features,
                'train_window': f"{train_start.date()} - {train_end.date()}",
                'total_features': len(features_df.columns)
            }
        )
        
        if not is_passed:
            self.violations.append(check)
        
        self.checks.append(check)
        return check
    
    def _check_label_leakage(self, 
                           labels_df: pd.DataFrame, 
                           train_end: pd.Timestamp,
                           test_start: pd.Timestamp) -> FutureFunctionCheck:
        """
        检查标签数据泄露：确保标签日期严格在训练集之后
        
        关键检查：assert label_date.min() > train_end
        LightGBM训练时label是未来N日收益，但特征必须严格滞后
        """
        
        if labels_df.empty:
            return FutureFunctionCheck(
                check_type=FutureFunctionType.LABEL_LEAKAGE,
                is_passed=True,
                message="标签数据为空，跳过泄露检查",
                severity='low',
                details={'reason': 'empty_data'}
            )
        
        # 获取标签数据的最小日期
        label_dates = labels_df.index
        if isinstance(label_dates, pd.DatetimeIndex):
            min_label_date = label_dates.min()
        else:
            try:
                min_label_date = pd.to_datetime(label_dates).min()
            except:
                logger.warning("无法解析标签数据日期，跳过泄露检查")
                return FutureFunctionCheck(
                    check_type=FutureFunctionType.LABEL_LEAKAGE,
                    is_passed=False,
                    message="无法解析标签数据日期",
                    severity='medium',
                    details={'error': 'date_parsing_failed'}
                )
        
        # 检查标签是否在训练集之后
        is_passed = min_label_date > train_end
        
        if not is_passed:
            # 计算泄露的天数
            if min_label_date <= train_end:
                leakage_days = (train_end - min_label_date).days
                message = (f"标签数据泄露: 最小标签日期({min_label_date.date()}) "
                          f"≤ 训练集结束日期({train_end.date()}), 泄露{leakage_days}天")
            else:
                message = f"标签数据检查通过"
        else:
            message = (f"标签数据无泄露: 最小标签日期({min_label_date.date()}) "
                      f"> 训练集结束日期({train_end.date()})")
        
        # 额外检查：标签是否在测试集开始之前（对于多步预测）
        if min_label_date < test_start:
            message += f"，标签开始于测试集开始之前，符合多步预测要求"
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.LABEL_LEAKAGE,
            is_passed=is_passed,
            message=message,
            severity='high' if not is_passed else 'low',
            details={
                'min_label_date': min_label_date,
                'train_end': train_end,
                'test_start': test_start,
                'leakage_days': (train_end - min_label_date).days if min_label_date <= train_end else 0,
                'label_date_range': f"{label_dates.min().date()} - {label_dates.max().date()}"
            }
        )
        
        if not is_passed:
            self.violations.append(check)
        
        self.checks.append(check)
        return check
    
    def _check_financial_data(self, financial_data: pd.DataFrame, train_end: pd.Timestamp) -> FutureFunctionCheck:
        """
        检查财务数据：确保使用截至训练集结束日期的财务报告
        
        财务因子（如ROE、利润增长）必须严格使用 report_date ≤ train_end 的最新一期数据
        """
        
        if financial_data.empty:
            return FutureFunctionCheck(
                check_type=FutureFunctionType.FINANCIAL_DATE_MISMATCH,
                is_passed=True,
                message="财务数据为空，跳过检查",
                severity='low',
                details={'reason': 'empty_data'}
            )
        
        # 检查是否有report_date列
        if 'report_date' not in financial_data.columns:
            logger.warning("财务数据缺少report_date列，无法进行日期检查")
            return FutureFunctionCheck(
                check_type=FutureFunctionType.FINANCIAL_DATE_MISMATCH,
                is_passed=False,
                message="财务数据缺少report_date列",
                severity='medium',
                details={'missing_column': 'report_date'}
            )
        
        # 转换report_date为日期类型
        try:
            financial_data['report_date'] = pd.to_datetime(financial_data['report_date'])
        except Exception as e:
            logger.warning(f"无法转换report_date: {e}")
            return FutureFunctionCheck(
                check_type=FutureFunctionType.FINANCIAL_DATE_MISMATCH,
                is_passed=False,
                message=f"无法转换report_date: {e}",
                severity='medium',
                details={'error': str(e)}
            )
        
        # 检查是否存在未来财务报告
        future_reports = financial_data[financial_data['report_date'] > train_end]
        is_passed = future_reports.empty
        
        if not is_passed:
            # 找出未来报告的数量和最早未来日期
            num_future_reports = len(future_reports)
            earliest_future_date = future_reports['report_date'].min()
            message = (f"财务数据包含未来信息: 发现{num_future_reports}个报告日期 > 训练集结束日期({train_end.date()}), "
                      f"最早未来日期: {earliest_future_date.date()}")
        else:
            # 检查最新的报告日期
            latest_report_date = financial_data['report_date'].max()
            message = (f"财务数据检查通过: 最新报告日期({latest_report_date.date()}) "
                      f"≤ 训练集结束日期({train_end.date()})")
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.FINANCIAL_DATE_MISMATCH,
            is_passed=is_passed,
            message=message,
            severity='high' if not is_passed else 'low',
            details={
                'latest_report_date': financial_data['report_date'].max() if not financial_data.empty else None,
                'train_end': train_end,
                'future_reports_count': len(future_reports) if not is_passed else 0,
                'earliest_future_date': future_reports['report_date'].min() if not is_passed else None,
                'report_date_range': f"{financial_data['report_date'].min().date()} - {financial_data['report_date'].max().date()}"
            }
        )
        
        if not is_passed:
            self.violations.append(check)
        
        self.checks.append(check)
        return check
    
    def _create_summary_check(self) -> FutureFunctionCheck:
        """创建汇总检查结果"""
        
        total_checks = len(self.checks)
        passed_checks = sum(1 for check in self.checks if check.is_passed)
        failed_checks = total_checks - passed_checks
        
        critical_violations = [v for v in self.violations if v.severity == 'high']
        medium_violations = [v for v in self.violations if v.severity == 'medium']
        
        if failed_checks == 0:
            message = f"所有{total_checks}项检查通过，无未来函数错误"
            is_passed = True
            severity = 'low'
        else:
            message = (f"检查结果: {passed_checks}/{total_checks}项通过, "
                      f"发现{len(critical_violations)}个严重错误, "
                      f"{len(medium_violations)}个中等错误")
            is_passed = len(critical_violations) == 0
            severity = 'high' if len(critical_violations) > 0 else 'medium'
        
        check = FutureFunctionCheck(
            check_type=FutureFunctionType.ROLLING_WINDOW_ERROR,
            is_passed=is_passed,
            message=message,
            severity=severity,
            details={
                'total_checks': total_checks,
                'passed_checks': passed_checks,
                'failed_checks': failed_checks,
                'critical_violations': len(critical_violations),
                'medium_violations': len(medium_violations),
                'all_violations': [
                    {
                        'type': v.check_type.value,
                        'message': v.message,
                        'severity': v.severity
                    }
                    for v in self.violations
                ]
            }
        )
        
        return check
    
    def _has_critical_violations(self) -> bool:
        """检查是否有严重违规"""
        return any(v.severity == 'high' for v in self.violations)
    
    def generate_report(self) -> str:
        """生成详细检查报告"""
        
        report = []
        report.append("=" * 80)
        report.append("DATA ASSURANCE 未来函数检查报告")
        report.append("=" * 80)
        
        # 汇总信息
        total_checks = len(self.checks)
        passed_checks = sum(1 for check in self.checks if check.is_passed)
        failed_checks = total_checks - passed_checks
        
        report.append(f"\n📊 检查汇总")
        report.append(f"   总检查项: {total_checks}")
        report.append(f"   通过项: {passed_checks}")
        report.append(f"   失败项: {failed_checks}")
        
        critical_count = sum(1 for v in self.violations if v.severity == 'high')
        medium_count = sum(1 for v in self.violations if v.severity == 'medium')
        
        report.append(f"\n⚠️  违规统计")
        report.append(f"   严重违规: {critical_count}")
        report.append(f"   中等违规: {medium_count}")
        
        if critical_count > 0:
            report.append(f"   ❌ 存在严重未来函数错误，回测结果不可信")
        elif medium_count > 0:
            report.append(f"   ⚠️  存在中等风险问题，建议修复")
        else:
            report.append(f"   ✅ 无未来函数错误，回测结果可信")
        
        # 详细检查结果
        report.append(f"\n🔍 详细检查结果")
        for i, check in enumerate(self.checks, 1):
            status = "✅" if check.is_passed else "❌"
            report.append(f"\n  {i}. {check.check_type.value} [{status}]")
            report.append(f"     信息: {check.message}")
            report.append(f"     风险级别: {check.severity}")
        
        # 建议措施
        report.append(f"\n💡 建议措施")
        
        if critical_count > 0:
            report.append("   1. 立即修复所有严重违规")
            report.append("   2. 重新进行因子标准化，使用滚动窗口统计量")
            report.append("   3. 确保财务因子使用 report_date ≤ train_end 的数据")
            report.append("   4. 重新训练模型前，重新运行DataAssurance检查")
        elif medium_count > 0:
            report.append("   1. 审查中等风险问题，评估影响")
            report.append("   2. 优化特征工程流程，避免全局标准化")
            report.append("   3. 实施更严格的数据预处理检查")
        else:
            report.append("   1. 保持当前数据预处理流程")
            report.append("   2. 定期运行DataAssurance检查")
            report.append("   3. 实施自动化测试，防止未来函数引入")
        
        report.append(f"\n" + "=" * 80)
        
        return "\n".join(report)


# 辅助函数：创建安全的滚动窗口特征处理器
class RollingFeatureProcessor:
    """安全的滚动窗口特征处理器，防止未来函数"""
    
    def __init__(self, train_end: pd.Timestamp):
        self.train_end = train_end
        self.window_stats = {}  # 存储每个特征的窗口统计量
    
    def fit_transform(self, features_df: pd.DataFrame, feature_col: str) -> pd.Series:
        """
        在训练窗口内拟合并转换特征
        
        使用滚动窗口标准化，避免全局统计量
        """
        if features_df.empty:
            return pd.Series(dtype=float)
        
        # 确定训练窗口
        train_mask = features_df.index <= self.train_end
        train_data = features_df.loc[train_mask, feature_col]
        
        if train_data.empty:
            return pd.Series(dtype=float)
        
        # 计算训练窗口内的统计量
        train_mean = train_data.mean()
        train_std = train_data.std()
        
        # 存储统计量
        self.window_stats[feature_col] = {
            'mean': train_mean,
            'std': train_std if train_std > 0 else 1.0
        }
        
        # 应用标准化
        standardized = (features_df[feature_col] - train_mean) / (train_std if train_std > 0 else 1.0)
        
        return standardized
    
    def transform(self, features_df: pd.DataFrame, feature_col: str) -> pd.Series:
        """使用存储的统计量转换特征"""
        if feature_col not in self.window_stats:
            raise ValueError(f"特征{feature_col}未拟合，请先调用fit_transform")
        
        stats = self.window_stats[feature_col]
        standardized = (features_df[feature_col] - stats['mean']) / stats['std']
        
        return standardized


# 测试函数
def test_data_assurance():
    """测试DataAssurance类"""
    print("=== 测试DataAssurance未来函数检查 ===")
    
    # 创建测试数据
    dates = pd.date_range('2020-01-01', '2023-12-31', freq='D')
    
    # 1. 创建特征数据（模拟泄露未来信息）
    np.random.seed(42)
    features = pd.DataFrame({
        'feature1': np.random.randn(len(dates)),
        'feature2': np.random.randn(len(dates)) * 2
    }, index=dates)
    
    # 2. 创建标签数据（正确：标签在训练集之后）
    label_dates = pd.date_range('2022-07-01', '2023-12-31', freq='D')
    labels = pd.DataFrame({
        'return_5d': np.random.randn(len(label_dates)) * 0.01
    }, index=label_dates)
    
    # 3. 创建财务数据（模拟包含未来报告）
    financial_dates = pd.date_range('2020-03-31', '2023-12-31', freq='Q')
    financial_data = pd.DataFrame({
        'report_date': financial_dates,
        'roe': np.random.randn(len(financial_dates)) * 0.1 + 0.15,
        'profit_growth': np.random.randn(len(financial_dates)) * 0.2 + 0.1
    })
    
    # 创建DataAssurance实例
    assurance = DataAssurance(strict_mode=False)  # 测试时不抛出异常
    
    # 定义Walk-forward期间
    train_start = pd.Timestamp('2020-01-01')
    train_end = pd.Timestamp('2022-06-30')
    test_start = pd.Timestamp('2022-07-01')
    test_end = pd.Timestamp('2022-12-31')
    
    # 运行检查
    print(f"\n检查期间: Train[{train_start.date()} - {train_end.date()}], Test[{test_start.date()} - {test_end.date()}]")
    
    try:
        checks = assurance.check_walkforward_period(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            features_df=features,
            labels_df=labels,
            financial_data=financial_data
        )
        
        # 生成报告
        report = assurance.generate_report()
        print(report)
        
        # 测试严格模式
        print("\n=== 测试严格模式 ===")
        assurance_strict = DataAssurance(strict_mode=True)
        
        # 故意创建有问题的数据
        problematic_features = features.copy()
        # 添加一个明显泄露的特征（日期超过训练集结束）
        problematic_dates = pd.date_range('2020-01-01', '2023-12-31', freq='D')
        problematic_features = pd.DataFrame({
            'leaky_feature': np.random.randn(len(problematic_dates))
        }, index=problematic_dates)
        
        try:
            checks = assurance_strict.check_walkforward_period(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                features_df=problematic_features
            )
            print("严格模式测试通过（应抛出异常）")
        except ValueError as e:
            print(f"严格模式正确捕获错误: {str(e)[:100]}...")
        
    except Exception as e:
        print(f"测试失败: {e}")
    
    # 测试RollingFeatureProcessor
    print("\n=== 测试RollingFeatureProcessor ===")
    processor = RollingFeatureProcessor(train_end)
    
    # 处理特征
    feature_series = features['feature1']
    standardized = processor.fit_transform(features, 'feature1')
    
    print(f"原始特征均值: {feature_series.mean():.4f}, 标准差: {feature_series.std():.4f}")
    print(f"训练窗口({train_start.date()} - {train_end.date()})内均值: {feature_series[feature_series.index <= train_end].mean():.4f}")
    print(f"标准化后均值: {standardized.mean():.4f}, 标准差: {standardized.std():.4f}")
    print("✓ 滚动窗口特征处理器测试完成")


if __name__ == "__main__":
    test_data_assurance()