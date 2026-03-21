#!/usr/bin/env python3
"""
数据适配器 - 处理不同数据源返回格式的兼容性
"""

from typing import Dict, Any, Optional
import pandas as pd

class DataAdapter:
    """数据适配器"""
    
    @staticmethod
    def adapt_pipeline_result(result: Any) -> Dict[str, Any]:
        """
        适配DataPipeline返回结果
        
        Args:
            result: DataPipeline.get_stock_data()的返回值
        
        Returns:
            标准化格式的字典
        """
        if isinstance(result, dict):
            # 已经是字典格式
            if 'success' in result:
                # 标准格式
                adapted = {
                    'success': bool(result['success']),
                    'data': result.get('data'),
                    'source': result.get('source', 'unknown'),
                    'error': result.get('error'),
                    'metadata': result.get('metadata', {})
                }
            else:
                # 未知字典格式，尝试适配
                adapted = {
                    'success': False,
                    'data': None,
                    'source': 'unknown',
                    'error': f'未知返回格式: {type(result)}',
                    'metadata': {}
                }
        elif result is None:
            # 返回None
            adapted = {
                'success': False,
                'data': None,
                'source': 'none',
                'error': '返回None',
                'metadata': {}
            }
        elif isinstance(result, pd.DataFrame):
            # 直接返回DataFrame
            adapted = {
                'success': True,
                'data': result,
                'source': 'direct_dataframe',
                'error': None,
                'metadata': {'rows': len(result), 'columns': list(result.columns)}
            }
        else:
            # 其他类型
            adapted = {
                'success': False,
                'data': None,
                'source': 'unknown',
                'error': f'不支持的类型: {type(result)}',
                'metadata': {}
            }
        
        return adapted
    
    @staticmethod
    def safe_get_stock_data(pipeline, symbol: str, start_date: str, end_date: str, **kwargs) -> Dict[str, Any]:
        """
        安全获取股票数据
        
        Args:
            pipeline: DataPipeline实例
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
        
        Returns:
            标准化格式的数据
        """
        try:
            result = pipeline.get_stock_data(symbol, start_date, end_date, **kwargs)
            return DataAdapter.adapt_pipeline_result(result)
        except Exception as e:
            return {
                'success': False,
                'data': None,
                'source': 'error',
                'error': str(e),
                'metadata': {}
            }


# 测试函数
def test_data_adapter():
    """测试数据适配器"""
    print("测试数据适配器...")
    
    # 测试各种输入
    test_cases = [
        ({'success': True, 'data': 'test_data', 'source': 'test'}, True),
        ({'success': False, 'error': 'test error'}, False),
        (None, False),
        ('invalid', False),
    ]
    
    for input_data, expected_success in test_cases:
        result = DataAdapter.adapt_pipeline_result(input_data)
        success = result['success'] == expected_success
        print(f"  输入: {type(input_data)}, 预期成功: {expected_success}, 实际: {result['success']}, {'✅' if success else '❌'}")
    
    print("测试完成")

if __name__ == "__main__":
    test_data_adapter()