"""
MootDX 接口测试 (v0.11.7 API)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("  MootDX 接口测试 (v0.11.7)")
print("=" * 60)

try:
    from mootdx.quotes import Quotes
    
    # 创建客户端
    client = Quotes.factory(market='std', timeout=10)
    print("✅ MootDX 连接成功")
    
    # 1. 测试实时行情 (新API: symbol 传字符串列表)
    print("\n--- 1. 实时行情 ---")
    symbols = ['sh600519', 'sz000001', 'sh688981', 'sz300750']
    result = client.quotes(symbol=symbols)
    
    if result is not None and hasattr(result, 'empty') and not result.empty:
        print(f"✅ 实时行情: 获取到 {len(result)} 只股票")
        print(f"   字段: {list(result.columns)}")
        print(f"\n   示例数据:")
        for _, r in result.iterrows():
            code = r.get('code', '')
            mkt = r.get('market', 0)
            name = r.get('name', '')
            close = r.get('close', 0)
            pct = r.get('percent', 0)
            volume = r.get('volume', 0)
            amount = r.get('amount', 0)
            print(f"   {code} {name} 收盘:{close} 涨跌幅:{pct}% 成交量:{volume} 成交额:{amount}")
            
            # 打印第一只股票的完整字段
            if r.name == 0:
                print(f"\n   完整字段示例:")
                for col in result.columns:
                    val = r.get(col, 'N/A')
                    print(f"   {col}: {val}")
    else:
        print("❌ 实时行情: 返回空数据")
    
    # 2. 测试五档盘口 (quotes 返回中可能包含)
    print("\n--- 2. 五档盘口 ---")
    if result is not None and hasattr(result, 'empty') and not result.empty:
        # 查找所有可能的盘口字段
        bid_cols = [c for c in result.columns if any(x in c.lower() for x in ['bid', 'b1', 'b2', 'b3', 'b4', 'b5', 'bp'])]
        ask_cols = [c for c in result.columns if any(x in c.lower() for x in ['ask', 'a1', 'a2', 'a3', 'a4', 'a5', 'ap', 'bv', 'av'])]
        vol_cols = [c for c in result.columns if any(x in c.lower() for x in ['vol', 'bv', 'av'])]
        print(f"   盘口相关字段: {bid_cols + ask_cols + vol_cols}")
        
        # 打印具体盘口数据
        for _, r in result.head(1).iterrows():
            for col in bid_cols[:5] + ask_cols[:5] + vol_cols[:5]:
                print(f"   {col}: {r.get(col, 'N/A')}")
    else:
        print("❌ 无行情数据，无法测试盘口")
    
    # 3. 测试日K线
    print("\n--- 3. 日K线 ---")
    df = client.bars(symbol='sh600519', frequency='d', count=5)
    if df is not None and hasattr(df, 'empty') and not df.empty:
        print(f"✅ 日K线: 获取到 {len(df)} 条")
        print(f"   字段: {list(df.columns)}")
        print(f"   示例:")
        print(df.head(3).to_string())
    else:
        print("❌ 日K线: 返回空数据")
    
    # 4. 测试周K线
    print("\n--- 4. 周K线 ---")
    df_w = client.bars(symbol='sh600519', frequency='w', count=5)
    if df_w is not None and hasattr(df_w, 'empty') and not df_w.empty:
        print(f"✅ 周K线: 获取到 {len(df_w)} 条")
        print(f"   字段: {list(df_w.columns)}")
    else:
        print("❌ 周K线: 返回空数据")
    
    # 5. 测试月K线
    print("\n--- 5. 月K线 ---")
    df_m = client.bars(symbol='sh600519', frequency='m', count=5)
    if df_m is not None and hasattr(df_m, 'empty') and not df_m.empty:
        print(f"✅ 月K线: 获取到 {len(df_m)} 条")
        print(f"   字段: {list(df_m.columns)}")
    else:
        print("❌ 月K线: 返回空数据")
    
    # 6. 测试分钟K线 (5分钟)
    print("\n--- 6. 5分钟K线 ---")
    df_5m = client.bars(symbol='sh600519', frequency=5, count=10)
    if df_5m is not None and hasattr(df_5m, 'empty') and not df_5m.empty:
        print(f"✅ 5分钟K线: 获取到 {len(df_5m)} 条")
        print(f"   字段: {list(df_5m.columns)}")
        print(f"   示例:")
        print(df_5m.head(3).to_string())
    else:
        print("❌ 5分钟K线: 返回空数据")
    
    # 7. 测试指数
    print("\n--- 7. 指数日线 ---")
    df_idx = client.index(symbol='sh000001', frequency='d', count=3)
    if df_idx is not None and hasattr(df_idx, 'empty') and not df_idx.empty:
        print(f"✅ 指数日线: 获取到 {len(df_idx)} 条")
        print(f"   字段: {list(df_idx.columns)}")
    else:
        print("❌ 指数日线: 返回空数据")
    
    # 8. 测试财务数据
    print("\n--- 8. 财务数据 ---")
    try:
        df_fin = client.finance(symbol='sh600519')
        if df_fin is not None and hasattr(df_fin, 'empty') and not df_fin.empty:
            print(f"✅ 财务数据: {len(df_fin)} 条")
            print(f"   字段: {list(df_fin.columns)}")
            print(f"   示例:")
            print(df_fin.head(2).to_string())
        else:
            print("❌ 财务数据: 返回空数据")
    except Exception as e:
        print(f"❌ 财务数据失败: {e}")
    
    # 9. 测试公司信息
    print("\n--- 9. 公司信息 ---")
    try:
        info = client.company(symbol='sh600519')
        if info:
            print(f"✅ 公司信息: {type(info)}")
            if hasattr(info, 'to_dict'):
                print(f"   内容: {info.to_dict()}")
            elif hasattr(info, '__dict__'):
                print(f"   内容: {info.__dict__}")
            else:
                print(f"   内容: {info}")
        else:
            print("❌ 公司信息: 空数据")
    except Exception as e:
        print(f"❌ 公司信息失败: {e}")
    
    # 10. 测试股票列表
    print("\n--- 10. 股票列表 ---")
    try:
        df_stocks = client.stocks()
        if df_stocks is not None and hasattr(df_stocks, 'empty') and not df_stocks.empty:
            print(f"✅ 股票列表: {len(df_stocks)} 只")
            print(f"   字段: {list(df_stocks.columns)}")
            print(f"   示例:")
            print(df_stocks.head(5).to_string())
        else:
            print("❌ 股票列表: 返回空数据")
    except Exception as e:
        print(f"❌ 股票列表失败: {e}")
    
    print("\n" + "=" * 60)
    print("  MootDX 测试完成")
    print("=" * 60)

except ImportError as e:
    print(f"❌ mootdx 未安装: {e}")
except Exception as e:
    print(f"❌ MootDX 测试失败: {e}")
    import traceback
    traceback.print_exc()
