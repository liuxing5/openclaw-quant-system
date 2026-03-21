#!/usr/bin/env python3
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
from data.sources.data_pipeline import DataPipeline

# 初始化数据管道
pipeline = DataPipeline()

# 测试获取股票数据
print("测试数据管道...")
try:
    # 获取一只A股的基本数据
    data = pipeline.get_stock_data('000001', start_date='2025-03-01', end_date='2025-03-20')
    print(f"数据获取成功，数据类型: {type(data)}")
    if isinstance(data, dict):
        print(f"数据键: {list(data.keys())}")
        if 'ohlc' in data and data['ohlc'] is not None:
            print(f"OHLC数据形状: {data['ohlc'].shape}")
            print(f"OHLC数据前几行:\n{data['ohlc'].head()}")
        else:
            print("OHLC数据为空")
    else:
        print(f"数据: {data}")
except Exception as e:
    print(f"数据获取失败: {e}")
    import traceback
    traceback.print_exc()