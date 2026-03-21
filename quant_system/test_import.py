#!/usr/bin/env python3
"""
测试数据库导入
"""

import sys
import os

# 添加路径
sys.path.append('/root/.openclaw/workspace/quant_system')

try:
    # 测试从data.sources导入
    from data.sources.data_pipeline import DataPipeline
    print("✅ DataPipeline导入成功")
    
    # 测试数据库管理器
    from data.database.database_manager import DatabaseManager
    print("✅ DatabaseManager导入成功")
    
    # 测试实例化
    print("测试DataPipeline实例化...")
    pipeline = DataPipeline()
    print("✅ DataPipeline实例化成功")
    
    # 测试数据库管理器实例化
    print("测试DatabaseManager实例化...")
    db = DatabaseManager()
    print("✅ DatabaseManager实例化成功")
    
    # 测试数据库连接
    print("测试数据库连接...")
    stats = db.get_update_stats()
    print(f"✅ 数据库连接成功，统计: {stats}")
    
except Exception as e:
    print(f"❌ 导入失败: {e}")
    import traceback
    traceback.print_exc()