#!/usr/bin/env python3
import sys
import pandas as pd
sys.path.append('/root/.openclaw/workspace/quant_system')
from data.sources.data_pipeline import DataPipeline
import json

# 初始化数据管道
pipeline = DataPipeline()

# 测试获取股票数据
print("测试数据管道...")
try:
    data = pipeline.get_stock_data('000001', start_date='2025-03-01', end_date='2025-03-20')
    print(f"数据获取成功")
    print(f"数据类型: {type(data)}")
    print(f"数据键: {list(data.keys())}")
    
    if 'data' in data:
        print(f"'data' 键类型: {type(data['data'])}")
        if isinstance(data['data'], dict):
            print(f"'data' 键: {list(data['data'].keys())}")
        elif isinstance(data['data'], pd.DataFrame):
            print(f"'data' DataFrame 形状: {data['data'].shape}")
            print(data['data'].head())
    
    if 'metadata' in data:
        print(f"metadata: {json.dumps(data['metadata'], indent=2, ensure_ascii=False)}")
        
except Exception as e:
    print(f"数据获取失败: {e}")
    import traceback
    traceback.print_exc()