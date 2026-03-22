#!/usr/bin/env python3
"""
Alpha预测模型 - 替代打分选股
使用机器学习预测未来收益，而非简单打分
支持LightGBM、XGBoost、梯度提升等
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any, Union
import warnings
warnings.filterwarnings('ignore')

# 尝试导入各种ML库
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False
    print("警告: LightGBM不可用")

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    print("警告: XGBoost不可用")

try:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Lasso, Ridge, ElasticNet
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import mean_squared_error, r2_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("警告: scikit-learn不可用")

try:
    from real_factors.real_factor_manager import RealFactorManager
    FACTOR_MANAGER_AVAILABLE = True
except ImportError:
    FACTOR_MANAGER_AVAILABLE = False


class AlphaPredictor:
    """Alpha预测模型（预测未来收益）"""
    
    def __init__(self, 
                 model_type: str = 'lightgbm',
                 prediction_horizon: int = 5,  # 预测未来5日收益
                 feature_lookback: int = 20,   # 使用过去20日特征
                 target_lookforward: int = 5,  # 预测未来5日
                 validation_split: float = 0.2,
                 random_seed: int = 42):
        """
        初始化Alpha预测器
        
        Args:
            model_type: 模型类型 ('lightgbm', 'xgboost', 'gbr', 'rf', 'lasso', 'ridge')
            prediction_horizon: 预测未来多少日的收益
            feature_lookback: 特征回溯窗口
            target_lookforward: 目标前瞻窗口
            validation_split: 验证集比例
            random_seed: 随机种子
        """
        self.model_type = model_type.lower()
        self.prediction_horizon = prediction_horizon
        self.feature_lookback = feature_lookback
        self.target_lookforward = target_lookforward
        self.validation_split = validation_split
        self.random_seed = random_seed
        
        # 检查模型可用性
        self.model = None
        self._init_model()
        
        # 特征工程器
        self.scaler = StandardScaler() if SKLEARN_AVAILABLE else None
        
        # 因子管理器
        if FACTOR_MANAGER_AVAILABLE:
            self.factor_manager = RealFactorManager()
        else:
            self.factor_manager = None
        
        # 训练结果
        self.training_history = {}
        self.feature_importance = None
        self.validation_score = None
        
        print(f"Alpha预测器初始化: {model_type}, 预测未来{target_lookforward}日收益")
    
    def _init_model(self):
        """初始化预测模型"""
        if self.model_type == 'lightgbm' and LIGHTGBM_AVAILABLE:
            self.model = lgb.LGBMRegressor(
                n_estimators=100,
                learning_rate=0.05,
                max_depth=6,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=self.random_seed,
                n_jobs=-1
            )
            print("✓ 使用LightGBM模型")
            
        elif self.model_type == 'xgboost' and XGBOOST_AVAILABLE:
            self.model = xgb.XGBRegressor(
                n_estimators=100,
                learning_rate=0.05,
                max_depth=6,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=self.random_seed,
                n_jobs=-1
            )
            print("✓ 使用XGBoost模型")
            
        elif self.model_type == 'gbr' and SKLEARN_AVAILABLE:
            self.model = GradientBoostingRegressor(
                n_estimators=100,
                learning_rate=0.05,
                max_depth=6,
                subsample=0.8,
                random_state=self.random_seed
            )
            print("✓ 使用梯度提升回归模型")
            
        elif self.model_type == 'rf' and SKLEARN_AVAILABLE:
            self.model = RandomForestRegressor(
                n_estimators=100,
                max_depth=6,
                random_state=self.random_seed,
                n_jobs=-1
            )
            print("✓ 使用随机森林模型")
            
        elif self.model_type in ['lasso', 'ridge', 'elasticnet'] and SKLEARN_AVAILABLE:
            if self.model_type == 'lasso':
                self.model = Lasso(alpha=0.01, random_state=self.random_seed)
            elif self.model_type == 'ridge':
                self.model = Ridge(alpha=1.0, random_state=self.random_seed)
            else:
                self.model = ElasticNet(alpha=0.01, l1_ratio=0.5, random_state=self.random_seed)
            print(f"✓ 使用{self.model_type}线性模型")
            
        else:
            # 回退到简单模型
            if SKLEARN_AVAILABLE:
                self.model = GradientBoostingRegressor(
                    n_estimators=50,
                    learning_rate=0.1,
                    max_depth=4,
                    random_state=self.random_seed
                )
                print("⚠ 使用回退模型（梯度提升）")
            else:
                raise ValueError("无可用机器学习库")
    
    def create_features(self, 
                       price_data: pd.DataFrame,
                       volume_data: pd.DataFrame,
                       fundamental_data: pd.DataFrame = None,
                       market_data: pd.DataFrame = None) -> pd.DataFrame:
        """
        创建特征矩阵（100+个特征）
        
        Returns:
            特征DataFrame
        """
        features = pd.DataFrame(index=price_data.index)
        
        # ========== 价格技术特征 ==========
        if price_data is not None and len(price_data) > self.feature_lookback:
            # 收益率特征
            for window in [1, 3, 5, 10, 20]:
                if len(price_data) >= window:
                    returns = price_data.pct_change(window)
                    features[f'return_{window}d'] = returns
            
            # 波动率特征
            returns_daily = price_data.pct_change()
            for window in [5, 10, 20]:
                if len(returns_daily) >= window:
                    vol = returns_daily.rolling(window).std()
                    features[f'volatility_{window}d'] = vol
            
            # 动量特征
            for short, long in [(5, 20), (10, 30), (20, 60)]:
                if len(price_data) >= long:
                    ma_short = price_data.rolling(short).mean()
                    ma_long = price_data.rolling(long).mean()
                    features[f'ma_cross_{short}_{long}'] = (ma_short / ma_long - 1)
            
            # 相对强弱
            if len(price_data) >= 14:
                delta = price_data.diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = 100 - (100 / (1 + gain / loss))
                features['rsi_14'] = rsi / 100
        
        # ========== 成交量特征 ==========
        if volume_data is not None and len(volume_data) > 0:
            # 成交量比率
            for window in [5, 10, 20]:
                if len(volume_data) >= window:
                    vol_avg = volume_data.rolling(window).mean()
                    features[f'volume_ratio_{window}d'] = volume_data / vol_avg
            
            # 价量相关性
            if price_data is not None and len(price_data) == len(volume_data):
                price_change = price_data.pct_change()
                volume_change = volume_data.pct_change()
                features['price_volume_corr'] = price_change.rolling(10).corr(volume_change)
        
        # ========== 基本面特征 ==========
        if fundamental_data is not None:
            # 这里应该从real_factor_manager获取真实基本面数据
            # 简化实现：使用传入的基本面数据
            for col in fundamental_data.columns:
                if col not in features.columns:
                    features[col] = fundamental_data[col]
        
        # ========== 市场特征 ==========
        if market_data is not None:
            # 市场相对表现
            if price_data is not None and 'close' in market_data.columns:
                market_returns = market_data['close'].pct_change()
                stock_returns = price_data.pct_change()
                features['market_beta'] = stock_returns.rolling(20).cov(market_returns) / market_returns.rolling(20).var()
                features['relative_strength'] = stock_returns.rolling(10).mean() - market_returns.rolling(10).mean()
        
        # ========== 时间特征 ==========
        if not features.empty:
            # 星期几（周一=0，周日=6）
            features['day_of_week'] = features.index.dayofweek / 6
            # 月份
            features['month'] = features.index.month / 12
            # 是否月末
            features['is_month_end'] = features.index.is_month_end.astype(int)
            # 是否季末
            features['is_quarter_end'] = features.index.is_quarter_end.astype(int)
        
        # 清理NaN值
        features = features.fillna(method='ffill').fillna(method='bfill').fillna(0)
        
        print(f"特征工程完成: {len(features.columns)}个特征")
        return features
    
    def create_target(self, 
                     price_data: pd.DataFrame,
                     horizon: int = None) -> pd.Series:
        """
        创建预测目标（未来收益）
        
        Args:
            price_data: 价格数据
            horizon: 预测未来多少日的收益
            
        Returns:
            目标收益率序列
        """
        if horizon is None:
            horizon = self.target_lookforward
        
        if len(price_data) < horizon + 1:
            raise ValueError(f"数据长度{len(price_data)}不足，需要至少{horizon+1}个数据点")
        
        # 计算未来horizon日的累计收益率
        # 🚨 关键修复：解决off-by-one错误
        # 原错误代码：future_prices = price_data.shift(-horizon); target = (future_prices / price_data - 1).shift(horizon)
        # 两次shift相互抵消，得到的是原始收益，不是未来收益
        # 正确方法：计算price_data.shift(-horizon) / price_data - 1，然后去掉末尾horizon行的NaN
        target = price_data.shift(-horizon) / price_data - 1
        
        # 移除末尾无法计算的数据（未来horizon日的数据为NaN）
        if horizon > 0:
            # 去掉最后horizon行（这些行没有未来数据）
            target = target.iloc[:-horizon]
        # 注：如果horizon=0，则target就是price_data / price_data - 1 = 0，无意义
        
        # 移除NaN值
        target = target.dropna()
        
        print(f"目标创建完成: 预测未来{horizon}日收益，形状={target.shape}")
        return target
    
    def prepare_training_data(self,
                             features: pd.DataFrame,
                             target: pd.Series,
                             test_size: float = None) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
        """
        准备训练和测试数据（时间序列分割）
        
        Returns:
            X_train, y_train, X_test, y_test
        """
        if test_size is None:
            test_size = self.validation_split
        
        # 对齐索引
        common_idx = features.index.intersection(target.index)
        if len(common_idx) < 100:
            raise ValueError(f"数据点不足: {len(common_idx)}个，需要至少100个")
        
        features_aligned = features.loc[common_idx]
        target_aligned = target.loc[common_idx]
        
        # 确保按时间排序（防止随机切分引入未来函数）
        features_aligned = features_aligned.sort_index()
        target_aligned = target_aligned.sort_index()
        
        # 时间序列分割（不能随机打乱）
        split_idx = int(len(common_idx) * (1 - test_size))
        
        X_train = features_aligned.iloc[:split_idx]
        y_train = target_aligned.iloc[:split_idx]
        X_test = features_aligned.iloc[split_idx:]
        y_test = target_aligned.iloc[split_idx:]
        
        # 标准化特征
        if self.scaler is not None:
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)
            X_train = pd.DataFrame(X_train_scaled, index=X_train.index, columns=X_train.columns)
            X_test = pd.DataFrame(X_test_scaled, index=X_test.index, columns=X_test.columns)
        
        print(f"数据准备完成: 训练集={len(X_train)}个样本, 测试集={len(X_test)}个样本")
        
        return X_train, y_train, X_test, y_test
    
    def train(self,
             features: pd.DataFrame,
             target: pd.Series,
             early_stopping: bool = True,
             eval_metric: str = 'mse') -> Dict[str, Any]:
        """
        训练预测模型
        
        Returns:
            训练结果字典
        """
        print(f"\n开始训练{self.model_type}模型...")
        
        # 准备数据
        X_train, y_train, X_test, y_test = self.prepare_training_data(features, target)
        
        if len(X_train) < 50 or len(X_test) < 20:
            raise ValueError(f"训练数据不足: 训练集{len(X_train)}个, 测试集{len(X_test)}个")
        
        # ✅ 新增：从训练集末尾切出 early stopping 验证集（不碰 X_test）
        # 三段切分：训练集（60%）、early stopping 验证集（20%）、IC 评估集（20%）
        val_split = int(len(X_train) * 0.8)
        X_tr = X_train.iloc[:val_split]
        y_tr = y_train.iloc[:val_split]
        X_val = X_train.iloc[val_split:]
        y_val = y_train.iloc[val_split:]
        
        print(f"  三段切分完成: 训练集={len(X_tr)}个样本, 早停验证集={len(X_val)}个样本, IC评估集={len(X_test)}个样本")
        
        # 训练模型
        if self.model_type == 'lightgbm' and LIGHTGBM_AVAILABLE and early_stopping:
            # LightGBM早停 - 使用独立的早停验证集
            train_data = lgb.Dataset(X_tr, label=y_tr)
            valid_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
            
            params = {
                'objective': 'regression',
                'metric': eval_metric,
                'boosting_type': 'gbdt',
                'num_leaves': 31,
                'learning_rate': 0.05,
                'feature_fraction': 0.9,
                'bagging_fraction': 0.8,
                'bagging_freq': 5,
                'verbose': -1,
                'random_state': self.random_seed
            }
            
            self.model = lgb.train(
                params,
                train_data,
                valid_sets=[valid_data],
                num_boost_round=100,
                callbacks=[lgb.early_stopping(stopping_rounds=10)]
            )
            
            # 提取特征重要性
            self.feature_importance = pd.DataFrame({
                'feature': X_tr.columns,
                'importance': self.model.feature_importance()
            }).sort_values('importance', ascending=False)
            
        elif self.model_type == 'xgboost' and XGBOOST_AVAILABLE and early_stopping:
            # XGBoost早停 - 使用独立的早停验证集
            self.model.fit(
                X_tr, y_tr,
                eval_set=[(X_val, y_val)],
                eval_metric=eval_metric,
                early_stopping_rounds=10,
                verbose=False
            )
            
            # 特征重要性
            self.feature_importance = pd.DataFrame({
                'feature': X_tr.columns,
                'importance': self.model.feature_importances_
            }).sort_values('importance', ascending=False)
            
        else:
            # 标准scikit-learn训练
            self.model.fit(X_tr, y_tr)
            
            # 特征重要性（如果可用）
            if hasattr(self.model, 'feature_importances_'):
                self.feature_importance = pd.DataFrame({
                    'feature': X_tr.columns,
                    'importance': self.model.feature_importances_
                }).sort_values('importance', ascending=False)
            elif hasattr(self.model, 'coef_'):
                self.feature_importance = pd.DataFrame({
                    'feature': X_tr.columns,
                    'importance': np.abs(self.model.coef_)
                }).sort_values('importance', ascending=False)
        
        # 评估模型
        train_pred = self.predict(X_tr)
        val_pred = self.predict(X_val) if len(X_val) > 0 else None
        test_pred = self.predict(X_test)
        
        train_mse = mean_squared_error(y_tr, train_pred) if SKLEARN_AVAILABLE else None
        val_mse = mean_squared_error(y_val, val_pred) if SKLEARN_AVAILABLE and val_pred is not None else None
        test_mse = mean_squared_error(y_test, test_pred) if SKLEARN_AVAILABLE else None
        train_r2 = r2_score(y_tr, train_pred) if SKLEARN_AVAILABLE else None
        val_r2 = r2_score(y_val, val_pred) if SKLEARN_AVAILABLE and val_pred is not None else None
        test_r2 = r2_score(y_test, test_pred) if SKLEARN_AVAILABLE else None
        
        # 计算IC（信息系数） - 排除末尾泄露行（确保没有NaN）
        if len(y_test) > 10:
            # 移除任何NaN或无穷值
            valid_mask = ~(np.isnan(y_test) | np.isnan(test_pred) | 
                          np.isinf(y_test) | np.isinf(test_pred))
            
            if valid_mask.sum() > 10:
                y_test_clean = y_test[valid_mask]
                test_pred_clean = test_pred[valid_mask]
                
                test_ic = np.corrcoef(y_test_clean, test_pred_clean)[0, 1]
                test_rank_ic = pd.Series(y_test_clean).rank().corr(pd.Series(test_pred_clean).rank())
            else:
                test_ic = test_rank_ic = 0
        else:
            test_ic = test_rank_ic = 0
        
        # 存储训练结果
        self.training_history = {
            'model_type': self.model_type,
            'train_samples': len(X_tr),
            'val_samples': len(X_val),
            'test_samples': len(X_test),
            'train_mse': train_mse,
            'val_mse': val_mse,
            'test_mse': test_mse,
            'train_r2': train_r2,
            'val_r2': val_r2,
            'test_r2': test_r2,
            'test_ic': test_ic,
            'test_rank_ic': test_rank_ic,
            'feature_count': len(X_tr.columns),
            'top_features': self.feature_importance.head(10).to_dict('records') if self.feature_importance is not None else []
        }
        
        self.validation_score = test_r2
        
        print(f"训练完成:")
        print(f"  训练集R²: {train_r2:.4f}" if train_r2 is not None else "  训练集R²: N/A")
        print(f"  验证集R²: {val_r2:.4f}" if val_r2 is not None else "  验证集R²: N/A")
        print(f"  测试集R²: {test_r2:.4f}" if test_r2 is not None else "  测试集R²: N/A")
        print(f"  测试集IC: {test_ic:.4f}")
        print(f"  测试集Rank IC: {test_rank_ic:.4f}")
        
        if self.feature_importance is not None and len(self.feature_importance) > 0:
            print(f"  最重要特征: {self.feature_importance.iloc[0]['feature']} "
                  f"(重要性: {self.feature_importance.iloc[0]['importance']:.4f})")
        
        return self.training_history
    
    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """预测未来收益"""
        if self.model is None:
            raise ValueError("模型未训练")
        
        # 标准化特征
        if self.scaler is not None and hasattr(self.scaler, 'transform'):
            features_scaled = self.scaler.transform(features)
        else:
            features_scaled = features.values
        
        # 预测
        if self.model_type == 'lightgbm' and LIGHTGBM_AVAILABLE:
            predictions = self.model.predict(features_scaled)
        elif self.model_type == 'xgboost' and XGBOOST_AVAILABLE:
            predictions = self.model.predict(features_scaled)
        else:
            predictions = self.model.predict(features_scaled)
        
        return predictions
    
    def predict_returns(self, 
                       current_features: pd.DataFrame,
                       current_prices: pd.Series) -> pd.Series:
        """
        预测未来收益并返回带股票代码的Series
        
        Returns:
            预测收益Series，索引为股票代码
        """
        predictions = self.predict(current_features)
        
        # 创建结果Series
        if current_features.index.nlevels == 1:
            # 单级索引
            result = pd.Series(predictions, index=current_features.index)
        else:
            # 多级索引（日期×股票）
            result = pd.Series(predictions, index=current_features.index)
        
        # 按预测收益排序
        result = result.sort_values(ascending=False)
        
        return result
    
    def generate_trading_signals(self,
                                predicted_returns: pd.Series,
                                threshold_long: float = 0.01,   # 买入阈值
                                threshold_short: float = -0.01, # 卖出阈值
                                top_n: int = 10) -> Dict[str, Any]:
        """
        生成交易信号
        
        Returns:
            交易信号字典
        """
        # 多头信号（预测收益高）
        long_candidates = predicted_returns[predicted_returns > threshold_long]
        if len(long_candidates) > top_n:
            long_signals = long_candidates.nlargest(top_n)
        else:
            long_signals = long_candidates
        
        # 空头信号（预测收益低）
        short_candidates = predicted_returns[predicted_returns < threshold_short]
        if len(short_candidates) > top_n:
            short_signals = short_candidates.nsmallest(top_n)
        else:
            short_signals = short_candidates
        
        # 中性信号
        neutral_mask = (predicted_returns <= threshold_long) & (predicted_returns >= threshold_short)
        neutral_signals = predicted_returns[neutral_mask]
        
        return {
            'long': {
                'count': len(long_signals),
                'signals': long_signals.to_dict(),
                'mean_return': long_signals.mean() if len(long_signals) > 0 else 0
            },
            'short': {
                'count': len(short_signals),
                'signals': short_signals.to_dict(),
                'mean_return': short_signals.mean() if len(short_signals) > 0 else 0
            },
            'neutral': {
                'count': len(neutral_signals),
                'mean_return': neutral_signals.mean() if len(neutral_signals) > 0 else 0
            },
            'prediction_summary': {
                'mean': predicted_returns.mean(),
                'std': predicted_returns.std(),
                'min': predicted_returns.min(),
                'max': predicted_returns.max()
            }
        }
    
    def save_model(self, filepath: str):
        """保存模型到文件"""
        import pickle
        
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'model_type': self.model_type,
            'training_history': self.training_history,
            'feature_importance': self.feature_importance
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(model_data, f)
        
        print(f"模型已保存到: {filepath}")
    
    def load_model(self, filepath: str):
        """从文件加载模型"""
        import pickle
        
        with open(filepath, 'rb') as f:
            model_data = pickle.load(f)
        
        self.model = model_data['model']
        self.scaler = model_data['scaler']
        self.model_type = model_data['model_type']
        self.training_history = model_data['training_history']
        self.feature_importance = model_data['feature_importance']
        
        print(f"模型已从 {filepath} 加载")


# 测试函数
def test_alpha_predictor():
    """测试Alpha预测器"""
    print("=== 测试Alpha预测器 ===")
    
    # 创建模拟数据
    np.random.seed(42)
    n_dates = 200
    n_stocks = 3
    
    dates = pd.date_range('2023-01-01', periods=n_dates, freq='D')
    
    # 模拟价格数据
    price_data = pd.DataFrame(
        np.random.randn(n_dates, n_stocks).cumsum(axis=0) * 0.01 + 100,
        index=dates,
        columns=[f'Stock{i}' for i in range(n_stocks)]
    )
    
    # 模拟成交量数据
    volume_data = pd.DataFrame(
        np.random.randn(n_dates, n_stocks).cumsum(axis=0) * 1000 + 1000000,
        index=dates,
        columns=[f'Stock{i}' for i in range(n_stocks)]
    )
    
    # 创建预测器
    try:
        predictor = AlphaPredictor(model_type='gbr', prediction_horizon=5)
        
        # 创建特征
        print("\n1. 创建特征...")
        features = predictor.create_features(
            price_data=price_data['Stock0'],  # 单只股票
            volume_data=volume_data['Stock0'],
            fundamental_data=None,
            market_data=None
        )
        
        # 创建目标
        print("\n2. 创建目标...")
        target = predictor.create_target(price_data['Stock0'], horizon=5)
        
        # 训练模型
        print("\n3. 训练模型...")
        training_result = predictor.train(features, target, early_stopping=False)
        
        print(f"\n训练结果:")
        print(f"  测试集R²: {training_result.get('test_r2', 'N/A')}")
        print(f"  测试集IC: {training_result.get('test_ic', 0):.4f}")
        print(f"  特征数量: {training_result.get('feature_count', 0)}")
        
        # 测试预测
        print("\n4. 测试预测...")
        if len(features) > 10:
            test_features = features.iloc[-10:]
            predictions = predictor.predict(test_features)
            print(f"  预测形状: {predictions.shape}")
            print(f"  预测范围: [{predictions.min():.4f}, {predictions.max():.4f}]")
        
        # 测试交易信号生成
        print("\n5. 测试交易信号生成...")
        if predictor.feature_importance is not None:
            print(f"  最重要特征: {predictor.feature_importance.iloc[0]['feature']}")
        
        # 多股票预测示例
        print("\n6. 多股票预测示例...")
        multi_predictions = {}
        for stock in ['Stock0', 'Stock1', 'Stock2'][:2]:
            stock_features = predictor.create_features(
                price_data=price_data[stock],
                volume_data=volume_data[stock],
                fundamental_data=None,
                market_data=None
            )
            
            if len(stock_features) > 0:
                pred = predictor.predict(stock_features.iloc[-1:])
                multi_predictions[stock] = pred[0] if len(pred) > 0 else 0
        
        if multi_predictions:
            print(f"  股票预测结果:")
            for stock, pred in multi_predictions.items():
                print(f"    {stock}: {pred:.4f}")
            
            # 生成交易信号
            pred_series = pd.Series(multi_predictions)
            signals = predictor.generate_trading_signals(pred_series, top_n=2)
            print(f"  交易信号: {signals['long']['count']}个买入, {signals['short']['count']}个卖出")
        
        print("\n✅ Alpha预测器测试完成")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_alpha_predictor()