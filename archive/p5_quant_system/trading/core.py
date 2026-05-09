"""
实时交易接口核心模块
支持模拟交易和券商API对接
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import json
import hashlib
import asyncio
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"    # 限价单
    STOP = "stop"      # 止损单
    STOP_LIMIT = "stop_limit"  # 止损限价单


class OrderSide(Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"      # 待处理
    SUBMITTED = "submitted"  # 已提交
    PARTIAL_FILLED = "partial_filled"  # 部分成交
    FILLED = "filled"        # 全部成交
    CANCELLED = "cancelled"  # 已取消
    REJECTED = "rejected"    # 已拒绝
    EXPIRED = "expired"      # 已过期


class AccountType(Enum):
    """账户类型"""
    SIMULATION = "simulation"  # 模拟账户
    PAPER_TRADING = "paper_trading"  # 纸交易
    REAL_TRADING = "real_trading"    # 实盘交易


class Order:
    """订单类"""
    
    def __init__(self, order_id: str, symbol: str, side: OrderSide, 
                 order_type: OrderType, quantity: float, price: float = None,
                 account_id: str = None, strategy_id: str = None):
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.price = price
        self.account_id = account_id
        self.strategy_id = strategy_id
        
        self.status = OrderStatus.PENDING
        self.filled_quantity = 0
        self.filled_price = 0
        self.filled_amount = 0
        self.commission = 0
        self.slippage = 0
        
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.submitted_at = None
        self.filled_at = None
        self.cancelled_at = None
        
        # 止损止盈参数
        self.stop_price = None
        self.take_profit_price = None
        
        # 元数据
        self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side.value,
            'order_type': self.order_type.value,
            'quantity': self.quantity,
            'price': self.price,
            'status': self.status.value,
            'filled_quantity': self.filled_quantity,
            'filled_price': self.filled_price,
            'filled_amount': self.filled_amount,
            'commission': self.commission,
            'slippage': self.slippage,
            'account_id': self.account_id,
            'strategy_id': self.strategy_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'filled_at': self.filled_at.isoformat() if self.filled_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'metadata': self.metadata
        }
    
    def update_status(self, status: OrderStatus, **kwargs):
        """更新订单状态"""
        self.status = status
        self.updated_at = datetime.now()
        
        if status == OrderStatus.SUBMITTED:
            self.submitted_at = datetime.now()
        elif status == OrderStatus.FILLED:
            self.filled_at = datetime.now()
            if 'filled_quantity' in kwargs:
                self.filled_quantity = kwargs['filled_quantity']
            if 'filled_price' in kwargs:
                self.filled_price = kwargs['filled_price']
            if 'commission' in kwargs:
                self.commission = kwargs['commission']
            if 'slippage' in kwargs:
                self.slippage = kwargs['slippage']
        elif status == OrderStatus.CANCELLED:
            self.cancelled_at = datetime.now()


class Position:
    """持仓类"""
    
    def __init__(self, symbol: str, quantity: float, avg_cost: float,
                 account_id: str = None):
        self.symbol = symbol
        self.quantity = quantity
        self.avg_cost = avg_cost
        self.account_id = account_id
        
        self.current_price = 0
        self.market_value = 0
        self.unrealized_pnl = 0
        self.unrealized_pnl_percent = 0
        
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        
        # 交易记录
        self.trades = []
    
    def update_price(self, current_price: float):
        """更新当前价格"""
        self.current_price = current_price
        self.market_value = self.quantity * current_price
        self.unrealized_pnl = (current_price - self.avg_cost) * self.quantity
        if self.avg_cost > 0:
            self.unrealized_pnl_percent = (current_price - self.avg_cost) / self.avg_cost
        self.updated_at = datetime.now()
    
    def add_trade(self, side: OrderSide, quantity: float, price: float, 
                  commission: float = 0):
        """添加交易记录"""
        if side == OrderSide.BUY:
            # 更新平均成本
            total_cost = self.avg_cost * self.quantity + price * quantity + commission
            self.quantity += quantity
            if self.quantity > 0:
                self.avg_cost = total_cost / self.quantity
        elif side == OrderSide.SELL:
            self.quantity -= quantity
            # 如果全部卖出，重置平均成本
            if self.quantity <= 0:
                self.avg_cost = 0
                self.quantity = 0
        
        self.trades.append({
            'timestamp': datetime.now(),
            'side': side.value,
            'quantity': quantity,
            'price': price,
            'commission': commission
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'quantity': self.quantity,
            'avg_cost': self.avg_cost,
            'current_price': self.current_price,
            'market_value': self.market_value,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_pnl_percent': self.unrealized_pnl_percent,
            'account_id': self.account_id,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class TradingAccount:
    """交易账户"""
    
    def __init__(self, account_id: str, account_type: AccountType,
                 initial_capital: float = 1000000):
        self.account_id = account_id
        self.account_type = account_type
        self.initial_capital = initial_capital
        
        # 资金信息
        self.cash = initial_capital
        self.total_assets = initial_capital
        self.realized_pnl = 0
        self.total_commission = 0
        
        # 持仓管理
        self.positions: Dict[str, Position] = {}
        
        # 订单管理
        self.orders: Dict[str, Order] = {}
        
        # 交易历史
        self.trade_history = []
        
        # 账户统计
        self.trades_count = 0
        self.win_trades = 0
        self.loss_trades = 0
        
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def update_portfolio_value(self, market_prices: Dict[str, float]):
        """更新投资组合价值"""
        total_market_value = 0
        
        for symbol, position in self.positions.items():
            if symbol in market_prices:
                position.update_price(market_prices[symbol])
                total_market_value += position.market_value
        
        self.total_assets = self.cash + total_market_value
        self.updated_at = datetime.now()
    
    def place_order(self, order: Order) -> bool:
        """下达订单"""
        if order.order_id in self.orders:
            logger.warning(f"订单已存在: {order.order_id}")
            return False
        
        # 检查资金是否足够（对于买单）
        if order.side == OrderSide.BUY:
            estimated_cost = order.quantity * (order.price or 0)
            if estimated_cost > self.cash:
                logger.warning(f"资金不足: 需要{estimated_cost}, 可用{self.cash}")
                return False
        
        self.orders[order.order_id] = order
        order.account_id = self.account_id
        order.update_status(OrderStatus.PENDING)
        
        logger.info(f"订单下达: {order.order_id} {order.side.value} {order.symbol} {order.quantity}")
        return True
    
    def execute_order(self, order_id: str, fill_price: float = None, 
                     fill_quantity: float = None, commission: float = 0,
                     slippage: float = 0) -> bool:
        """执行订单"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False
        
        order = self.orders[order_id]
        
        if order.status not in [OrderStatus.PENDING, OrderStatus.SUBMITTED]:
            logger.warning(f"订单状态不可执行: {order.status.value}")
            return False
        
        # 确定成交价格和数量
        if fill_price is None:
            fill_price = order.price or 0
        if fill_quantity is None:
            fill_quantity = order.quantity
        
        # 计算成交金额
        fill_amount = fill_price * fill_quantity
        total_cost = fill_amount + commission + slippage
        
        # 更新账户
        if order.side == OrderSide.BUY:
            # 检查资金是否足够
            if total_cost > self.cash:
                logger.warning(f"执行失败: 资金不足 {total_cost} > {self.cash}")
                return False
            
            self.cash -= total_cost
            
            # 更新持仓
            if order.symbol not in self.positions:
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    quantity=0,
                    avg_cost=0,
                    account_id=self.account_id
                )
            
            position = self.positions[order.symbol]
            position.add_trade(order.side, fill_quantity, fill_price, commission)
            
        elif order.side == OrderSide.SELL:
            # 检查持仓是否足够
            if order.symbol not in self.positions:
                logger.warning(f"执行失败: 无持仓 {order.symbol}")
                return False
            
            position = self.positions[order.symbol]
            if position.quantity < fill_quantity:
                logger.warning(f"执行失败: 持仓不足 {fill_quantity} > {position.quantity}")
                return False
            
            position.add_trade(order.side, fill_quantity, fill_price, commission)
            self.cash += (fill_amount - commission - slippage)
            
            # 如果持仓为0，移除持仓记录
            if position.quantity <= 0:
                del self.positions[order.symbol]
        
        # 更新订单状态
        order.update_status(
            OrderStatus.FILLED,
            filled_quantity=fill_quantity,
            filled_price=fill_price,
            commission=commission,
            slippage=slippage
        )
        
        # 更新统计
        self.total_commission += commission
        self.trades_count += 1
        
        # 记录交易历史
        trade_record = {
            'order_id': order_id,
            'timestamp': datetime.now(),
            'symbol': order.symbol,
            'side': order.side.value,
            'quantity': fill_quantity,
            'price': fill_price,
            'amount': fill_amount,
            'commission': commission,
            'slippage': slippage,
            'strategy_id': order.strategy_id
        }
        self.trade_history.append(trade_record)
        
        # 更新胜率统计
        if order.side == OrderSide.SELL:
            # 对于卖出交易，检查是否为盈利交易
            position_before = order.metadata.get('position_before', {})
            if 'avg_cost' in position_before:
                profit = (fill_price - position_before['avg_cost']) * fill_quantity
                if profit > 0:
                    self.win_trades += 1
                else:
                    self.loss_trades += 1
        
        logger.info(f"订单执行: {order_id} {fill_quantity}@{fill_price}")
        return True
    
    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        if order_id not in self.orders:
            return False
        
        order = self.orders[order_id]
        if order.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED]:
            order.update_status(OrderStatus.CANCELLED)
            logger.info(f"订单取消: {order_id}")
            return True
        
        return False
    
    def get_account_summary(self) -> Dict[str, Any]:
        """获取账户摘要"""
        return {
            'account_id': self.account_id,
            'account_type': self.account_type.value,
            'cash': self.cash,
            'total_assets': self.total_assets,
            'initial_capital': self.initial_capital,
            'realized_pnl': self.realized_pnl,
            'unrealized_pnl': sum(p.unrealized_pnl for p in self.positions.values()),
            'total_commission': self.total_commission,
            'positions_count': len(self.positions),
            'trades_count': self.trades_count,
            'win_rate': self.win_trades / self.trades_count if self.trades_count > 0 else 0,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """获取所有持仓"""
        return [position.to_dict() for position in self.positions.values()]
    
    def get_orders(self, status: Optional[OrderStatus] = None) -> List[Dict[str, Any]]:
        """获取订单列表"""
        orders = self.orders.values()
        if status:
            orders = [o for o in orders if o.status == status]
        return [order.to_dict() for order in orders]


class TradingEngine:
    """交易引擎"""
    
    def __init__(self, data_feed=None):
        self.data_feed = data_feed
        self.accounts: Dict[str, TradingAccount] = {}
        self.strategies: Dict[str, Any] = {}
        
        # 订单路由
        self.order_routes = {}
        
        # 市场数据缓存
        self.market_data_cache = {}
        
        # 交易时间控制
        self.trading_hours = {
            'A股': ('09:30', '11:30', '13:00', '15:00'),
            '美股': ('21:30', '04:00'),
        }
        
        logger.info("交易引擎初始化完成")
    
    def create_account(self, account_id: str, account_type: AccountType,
                      initial_capital: float = 1000000) -> TradingAccount:
        """创建交易账户"""
        if account_id in self.accounts:
            logger.warning(f"账户已存在: {account_id}")
            return self.accounts[account_id]
        
        account = TradingAccount(account_id, account_type, initial_capital)
        self.accounts[account_id] = account
        
        logger.info(f"创建账户: {account_id} 类型:{account_type.value} 资金:{initial_capital}")
        return account
    
    def register_strategy(self, strategy_id: str, strategy_instance):
        """注册交易策略"""
        self.strategies[strategy_id] = strategy_instance
        logger.info(f"注册策略: {strategy_id}")
    
    def place_order(self, strategy_id: str, symbol: str, side: OrderSide,
                   order_type: OrderType, quantity: float, price: float = None,
                   account_id: str = None) -> Optional[str]:
        """下达订单（策略调用）"""
        if strategy_id not in self.strategies:
            logger.warning(f"策略未注册: {strategy_id}")
            return None
        
        # 如果没有指定账户，使用策略默认账户
        if account_id is None:
            account_id = f"strategy_{strategy_id}"
            if account_id not in self.accounts:
                self.create_account(account_id, AccountType.SIMULATION, 1000000)
        
        if account_id not in self.accounts:
            logger.warning(f"账户不存在: {account_id}")
            return None
        
        # 生成订单ID
        order_id = hashlib.md5(
            f"{strategy_id}_{symbol}_{side.value}_{datetime.now().timestamp()}".encode()
        ).hexdigest()[:12]
        
        # 创建订单
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            account_id=account_id,
            strategy_id=strategy_id
        )
        
        # 保存持仓快照（用于后续盈亏计算）
        account = self.accounts[account_id]
        if symbol in account.positions:
            position = account.positions[symbol]
            order.metadata['position_before'] = {
                'quantity': position.quantity,
                'avg_cost': position.avg_cost
            }
        
        # 下达订单
        if account.place_order(order):
            # 根据账户类型执行订单
            if account.account_type in [AccountType.SIMULATION, AccountType.PAPER_TRADING]:
                # 模拟交易立即执行
                self._execute_simulation_order(order_id)
            elif account.account_type == AccountType.REAL_TRADING:
                # 实盘交易提交到券商
                self._submit_real_order(order_id)
            
            return order_id
        
        return None
    
    def _execute_simulation_order(self, order_id: str):
        """执行模拟交易订单"""
        # 查找订单和账户
        order = None
        account = None
        
        for acc in self.accounts.values():
            if order_id in acc.orders:
                order = acc.orders[order_id]
                account = acc
                break
        
        if not order or not account:
            logger.warning(f"订单未找到: {order_id}")
            return
        
        # 获取市场数据
        current_price = self._get_market_price(order.symbol)
        if current_price is None:
            logger.warning(f"无法获取价格: {order.symbol}")
            return
        
        # 计算成交价格（考虑滑点）
        if order.order_type == OrderType.MARKET:
            fill_price = current_price
        elif order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.BUY and order.price >= current_price:
                fill_price = current_price
            elif order.side == OrderSide.SELL and order.price <= current_price:
                fill_price = current_price
            else:
                # 限价单未触发
                order.update_status(OrderStatus.PENDING)
                return
        else:
            # 其他订单类型暂不支持
            order.update_status(OrderStatus.REJECTED)
            return
        
        # 计算手续费和滑点
        commission = self._calculate_commission(order, fill_price)
        slippage = self._calculate_slippage(order, fill_price)
        
        # 执行订单
        account.execute_order(
            order_id=order_id,
            fill_price=fill_price,
            fill_quantity=order.quantity,
            commission=commission,
            slippage=slippage
        )
    
    def _submit_real_order(self, order_id: str):
        """提交实盘订单到券商"""
        # 这里应该集成券商API
        # 简化实现：标记为已提交，实际执行需要券商接口
        for account in self.accounts.values():
            if order_id in account.orders:
                order = account.orders[order_id]
                order.update_status(OrderStatus.SUBMITTED)
                logger.info(f"实盘订单提交: {order_id}")
                break
    
    def _get_market_price(self, symbol: str) -> Optional[float]:
        """获取市场当前价格"""
        # 优先使用数据源
        if self.data_feed:
            try:
                return self.data_feed.get_current_price(symbol)
            except:
                pass
        
        # 使用缓存
        if symbol in self.market_data_cache:
            return self.market_data_cache[symbol].get('price')
        
        # 返回模拟价格
        return np.random.uniform(10, 200)
    
    def _calculate_commission(self, order: Order, price: float) -> float:
        """计算手续费"""
        # 模拟手续费：万分之三，最低5元
        amount = order.quantity * price
        commission = amount * 0.0003
        return max(commission, 5)
    
    def _calculate_slippage(self, order: Order, price: float) -> float:
        """计算滑点成本"""
        # 模拟滑点：市价单0.1%，限价单0.05%
        if order.order_type == OrderType.MARKET:
            slippage_rate = 0.001
        else:
            slippage_rate = 0.0005
        
        return order.quantity * price * slippage_rate
    
    def update_market_data(self, market_data: Dict[str, Dict[str, Any]]):
        """更新市场数据"""
        self.market_data_cache.update(market_data)
        
        # 更新所有账户的持仓市值
        market_prices = {symbol: data.get('price', 0) 
                        for symbol, data in market_data.items()}
        
        for account in self.accounts.values():
            account.update_portfolio_value(market_prices)
    
    def get_account(self, account_id: str) -> Optional[TradingAccount]:
        """获取账户"""
        return self.accounts.get(account_id)
    
    def get_all_accounts_summary(self) -> Dict[str, Dict[str, Any]]:
        """获取所有账户摘要"""
        return {
            account_id: account.get_account_summary()
            for account_id, account in self.accounts.items()
        }


# 券商API接口抽象类
class BrokerAPI:
    """券商API抽象接口"""
    
    def __init__(self, broker_name: str, api_key: str = None, api_secret: str = None):
        self.broker_name = broker_name
        self.api_key = api_key
        self.api_secret = api_secret
        self.is_connected = False
        
    def connect(self) -> bool:
        """连接到券商API"""
        raise NotImplementedError
    
    def disconnect(self):
        """断开连接"""
        self.is_connected = False
    
    def get_account_info(self) -> Dict[str, Any]:
        """获取账户信息"""
        raise NotImplementedError
    
    def place_order(self, symbol: str, side: str, order_type: str,
                   quantity: float, price: float = None) -> str:
        """下单"""
        raise NotImplementedError
    
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        raise NotImplementedError
    
    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """查询订单状态"""
        raise NotImplementedError
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """获取持仓"""
        raise NotImplementedError
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场数据"""
        raise NotImplementedError


# 模拟数据源
class SimulationDataFeed:
    """模拟数据源"""
    
    def __init__(self):
        self.price_data = {}
        self.historical_data = {}
    
    def update_price(self, symbol: str, price: float, timestamp=None):
        """更新价格"""
        if timestamp is None:
            timestamp = datetime.now()
        
        self.price_data[symbol] = {
            'price': price,
            'timestamp': timestamp,
            'volume': np.random.randint(1000, 1000000),
            'bid': price * 0.999,
            'ask': price * 1.001
        }
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        if symbol in self.price_data:
            return self.price_data[symbol]['price']
        return None
    
    def get_market_snapshot(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场快照"""
        snapshot = {}
        for symbol in symbols:
            if symbol in self.price_data:
                snapshot[symbol] = self.price_data[symbol].copy()
            else:
                # 生成模拟数据
                snapshot[symbol] = {
                    'price': np.random.uniform(10, 200),
                    'timestamp': datetime.now(),
                    'volume': np.random.randint(1000, 1000000),
                    'bid': np.random.uniform(9.5, 195),
                    'ask': np.random.uniform(10.5, 205)
                }
        return snapshot


# 交易策略基类
class TradingStrategy:
    """交易策略基类"""
    
    def __init__(self, strategy_id: str, trading_engine: TradingEngine):
        self.strategy_id = strategy_id
        self.trading_engine = trading_engine
        self.account_id = f"strategy_{strategy_id}"
        
        # 注册策略
        self.trading_engine.register_strategy(strategy_id, self)
        
        # 策略状态
        self.is_running = False
        self.last_signal = None
        
        # 策略参数
        self.parameters = {}
        
        logger.info(f"策略初始化: {strategy_id}")
    
    def initialize(self, **kwargs):
        """初始化策略"""
        self.parameters.update(kwargs)
        
        # 创建交易账户
        self.trading_engine.create_account(
            account_id=self.account_id,
            account_type=AccountType.SIMULATION,
            initial_capital=self.parameters.get('initial_capital', 1000000)
        )
    
    def start(self):
        """启动策略"""
        self.is_running = True
        logger.info(f"策略启动: {self.strategy_id}")
    
    def stop(self):
        """停止策略"""
        self.is_running = False
        logger.info(f"策略停止: {self.strategy_id}")
    
    def on_market_data(self, market_data: Dict[str, Dict[str, Any]]):
        """市场数据回调"""
        if not self.is_running:
            return
        
        # 策略逻辑在这里实现
        self._generate_signals(market_data)
    
    def _generate_signals(self, market_data: Dict[str, Dict[str, Any]]):
        """生成交易信号（子类实现）"""
        raise NotImplementedError
    
    def place_order(self, symbol: str, side: OrderSide, quantity: float,
                   order_type: OrderType = OrderType.MARKET, price: float = None) -> Optional[str]:
        """下达订单"""
        return self.trading_engine.place_order(
            strategy_id=self.strategy_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            account_id=self.account_id
        )
    
    def get_account_summary(self) -> Dict[str, Any]:
        """获取账户摘要"""
        account = self.trading_engine.get_account(self.account_id)
        if account:
            return account.get_account_summary()
        return {}