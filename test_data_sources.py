"""
数据源接口测试脚本
==================
逐个测试各个数据源接口，验证能拿到哪些数据。
运行: python test_data_sources.py
"""

import sys
import os
import time
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def print_result(name, success, data_info, error=None):
    status = "✅" if success else "❌"
    print(f"{status} {name}: {data_info}")
    if error:
        print(f"   错误: {error}")

# ============================================================
# 1. MootDX 实时行情 + 五档盘口
# ============================================================
def test_mootdx_realtime():
    print_section("1. MootDX 实时行情 + 五档盘口")
    
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=5)
        print("✅ MootDX 连接成功")
        
        # 测试实时行情
        symbols = [(1, '600519'), (0, '000001'), (1, '688981')]
        result = client.quotes(symbol=symbols)
        
        if result is not None and hasattr(result, 'empty') and not result.empty:
            print(f"✅ 实时行情: 获取到 {len(result)} 只股票")
            print(f"   字段: {list(result.columns)}")
            print(f"   示例数据:")
            for _, r in result.head(2).iterrows():
                print(f"   - {r.get('code')} {r.get('name')} 收盘:{r.get('close')} 涨跌幅:{r.get('percent')}%")
                
                # 检查五档盘口字段
                bid_fields = [c for c in result.columns if 'bid' in c.lower()]
                ask_fields = [c for c in result.columns if 'ask' in c.lower()]
                print(f"   盘口字段(bid): {bid_fields[:5]}")
                print(f"   盘口字段(ask): {ask_fields[:5]}")
        else:
            print("❌ 实时行情: 返回空数据")
            
        # 测试日K线
        df = client.bars(symbol='600519', market=1, frequency='d', count=5)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            print(f"\n✅ 日K线: 获取到 {len(df)} 条")
            print(f"   字段: {list(df.columns)}")
            print(f"   示例: {df.head(2).to_dict('records')}")
        else:
            print("\n❌ 日K线: 返回空数据")
            
        # 测试周K线
        df_w = client.bars(symbol='600519', market=1, frequency='w', count=5)
        if df_w is not None and hasattr(df_w, 'empty') and not df_w.empty:
            print(f"\n✅ 周K线: 获取到 {len(df_w)} 条")
        else:
            print("\n❌ 周K线: 返回空数据")
            
        # 测试月K线
        df_m = client.bars(symbol='600519', market=1, frequency='m', count=5)
        if df_m is not None and hasattr(df_m, 'empty') and not df_m.empty:
            print(f"\n✅ 月K线: 获取到 {len(df_m)} 条")
        else:
            print("\n❌ 月K线: 返回空数据")
            
        # 测试分钟K线 (5分钟)
        df_5m = client.bars(symbol='600519', market=1, frequency=5, count=5)
        if df_5m is not None and hasattr(df_5m, 'empty') and not df_5m.empty:
            print(f"\n✅ 5分钟K线: 获取到 {len(df_5m)} 条")
            print(f"   字段: {list(df_5m.columns)}")
        else:
            print("\n❌ 5分钟K线: 返回空数据")
            
        # 测试指数
        df_idx = client.index(symbol='000001', market=1, frequency='d', count=3)
        if df_idx is not None and hasattr(df_idx, 'empty') and not df_idx.empty:
            print(f"\n✅ 指数日线: 获取到 {len(df_idx)} 条")
        else:
            print("\n❌ 指数日线: 返回空数据")
        
        return True
        
    except ImportError:
        print("❌ mootdx 未安装")
        return False
    except Exception as e:
        print(f"❌ MootDX 测试失败: {e}")
        return False

# ============================================================
# 2. 腾讯财经 qt.gtimg.cn
# ============================================================
def test_tencent_quotes():
    print_section("2. 腾讯财经 qt.gtimg.cn")
    
    try:
        import requests
        
        # 测试单只股票
        url = "http://qt.gtimg.cn/q=sh600519,sz000001"
        resp = requests.get(url, timeout=10)
        resp.encoding = 'gbk'
        
        if resp.status_code == 200:
            print(f"✅ 腾讯财经: HTTP {resp.status_code}")
            
            lines = resp.text.strip().split('\n')
            for line in lines[:2]:
                if '~' not in line:
                    continue
                p = line.split('~')
                if len(p) < 50:
                    print(f"   字段数不足: {len(p)}")
                    continue
                    
                name = p[1]
                code = p[2]
                price = p[3]
                pre_close = p[4]
                volume = p[6]
                pct_chg = p[32]
                high = p[33]
                turnover = p[38]
                pe = p[39]
                mcap = p[44]
                circ_mcap = p[45]
                pb = p[46]
                limit_up = p[47] if len(p) > 47 else 'N/A'
                limit_down = p[48] if len(p) > 48 else 'N/A'
                
                print(f"\n   股票: {name} ({code})")
                print(f"   价格: {price}  昨收: {pre_close}  涨跌: {pct_chg}%")
                print(f"   最高: {high}  成交量: {volume}")
                print(f"   换手率: {turnover}%")
                print(f"   PE: {pe}  PB: {pb}")
                print(f"   总市值: {mcap}亿  流通市值: {circ_mcap}亿")
                print(f"   涨停价: {limit_up}  跌停价: {limit_down}")
                
                # 打印所有可用字段索引
                print(f"\n   全部字段示例(前60个):")
                for i in range(min(60, len(p))):
                    if p[i]:
                        print(f"   p[{i}]={p[i]}")
        else:
            print(f"❌ 腾讯财经: HTTP {resp.status_code}")
            
        return True
        
    except Exception as e:
        print(f"❌ 腾讯财经测试失败: {e}")
        return False

# ============================================================
# 3. 同花顺 THS 强势股 + 概念
# ============================================================
def test_ths_strong_stocks():
    print_section("3. 同花顺 THS 强势股 + 概念")
    
    try:
        import akshare as ak
        
        # 测试连续上涨
        try:
            df = ak.stock_rank_lxsz_ths()
            if df is not None and not df.empty:
                print(f"✅ 连续上涨: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(2).to_dict('records')}")
            else:
                print("❌ 连续上涨: 空数据")
        except Exception as e:
            print(f"❌ 连续上涨失败: {e}")
            
        time.sleep(1)
        
        # 测试创新高
        try:
            df = ak.stock_rank_cxg_ths(symbol='创月新高')
            if df is not None and not df.empty:
                print(f"\n✅ 创月新高: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 创月新高: 空数据")
        except Exception as e:
            print(f"\n❌ 创月新高失败: {e}")
            
        time.sleep(1)
        
        # 测试量价齐升
        try:
            df = ak.stock_rank_ljqd_ths()
            if df is not None and not df.empty:
                print(f"\n✅ 量价齐升: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 量价齐升: 空数据")
        except Exception as e:
            print(f"\n❌ 量价齐升失败: {e}")
            
        time.sleep(1)
        
        # 测试概念板块
        try:
            df = ak.stock_board_concept_name_ths()
            if df is not None and not df.empty:
                print(f"\n✅ 概念板块: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(2).to_dict('records')}")
            else:
                print("\n❌ 概念板块: 空数据")
        except Exception as e:
            print(f"\n❌ 概念板块失败: {e}")
        
        return True
        
    except ImportError:
        print("❌ akshare 未安装")
        return False
    except Exception as e:
        print(f"❌ THS测试失败: {e}")
        return False

# ============================================================
# 4. 东财 reportapi + akshare 机构一致预期
# ============================================================
def test_earnings_forecast():
    print_section("4. 机构一致预期 EPS")
    
    try:
        import akshare as ak
        
        # 测试机构一致预期
        try:
            df = ak.stock_profit_forecast_ths(symbol='600519')
            if df is not None and not df.empty:
                print(f"✅ 机构一致预期(THS): {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(3).to_dict('records')}")
            else:
                print("❌ 机构一致预期(THS): 空数据")
        except Exception as e:
            print(f"❌ 机构一致预期(THS)失败: {e}")
            
        time.sleep(1)
        
        # 测试东财盈利预测
        try:
            df = ak.stock_profit_forecast_em(symbol='600519')
            if df is not None and not df.empty:
                print(f"\n✅ 盈利预测(东财): {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(3).to_dict('records')}")
            else:
                print("\n❌ 盈利预测(东财): 空数据")
        except Exception as e:
            print(f"\n❌ 盈利预测(东财)失败: {e}")
        
        return True
        
    except ImportError:
        print("❌ akshare 未安装")
        return False
    except Exception as e:
        print(f"❌ 盈利预测测试失败: {e}")
        return False

# ============================================================
# 5. 新闻层: akshare + 财联社
# ============================================================
def test_news_sources():
    print_section("5. 新闻层: akshare + 财联社")
    
    try:
        import akshare as ak
        
        # 测试财联社电报
        try:
            df = ak.stock_info_global_cls()
            if df is not None and not df.empty:
                print(f"✅ 财联社电报: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   最新3条:")
                for _, r in df.head(3).iterrows():
                    print(f"   - {r.to_dict()}")
            else:
                print("❌ 财联社电报: 空数据")
        except Exception as e:
            print(f"❌ 财联社电报失败: {e}")
            
        time.sleep(1)
        
        # 测试新浪财经
        try:
            df = ak.stock_news_em(symbol='600519')
            if df is not None and not df.empty:
                print(f"\n✅ 个股新闻(东财): {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 个股新闻(东财): 空数据")
        except Exception as e:
            print(f"\n❌ 个股新闻(东财)失败: {e}")
        
        return True
        
    except ImportError:
        print("❌ akshare 未安装")
        return False
    except Exception as e:
        print(f"❌ 新闻测试失败: {e}")
        return False

# ============================================================
# 6. mootdx 财务数据 + 行业 + 公司概况
# ============================================================
def test_mootdx_fundamentals():
    print_section("6. MootDX 财务数据 + 行业 + 公司概况")
    
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=5)
        
        # 测试财务数据
        try:
            df = client.finance(symbol='600519', market=1)
            if df is not None and hasattr(df, 'empty') and not df.empty:
                print(f"✅ 财务数据: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(2).to_dict('records')}")
            else:
                print("❌ 财务数据: 空数据")
        except Exception as e:
            print(f"❌ 财务数据失败: {e}")
            
        # 测试公司信息
        try:
            info = client.company(symbol='600519', market=1)
            if info:
                print(f"\n✅ 公司信息: {type(info)}")
                if hasattr(info, 'to_dict'):
                    print(f"   内容: {info.to_dict()}")
                else:
                    print(f"   内容: {info}")
            else:
                print("\n❌ 公司信息: 空数据")
        except Exception as e:
            print(f"\n❌ 公司信息失败: {e}")
        
        return True
        
    except ImportError:
        print("❌ mootdx 未安装")
        return False
    except Exception as e:
        print(f"❌ MootDX基础数据测试失败: {e}")
        return False

# ============================================================
# 7. 巨潮 cninfo 公告
# ============================================================
def test_cninfo_announcements():
    print_section("7. 巨潮 cninfo 公告")
    
    try:
        import akshare as ak
        
        # 测试巨潮公告
        try:
            df = ak.stock_notice_report(symbol='600519')
            if df is not None and not df.empty:
                print(f"✅ 巨潮公告: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(2).to_dict('records')}")
            else:
                print("❌ 巨潮公告: 空数据")
        except Exception as e:
            print(f"❌ 巨潮公告失败: {e}")
        
        return True
        
    except ImportError:
        print("❌ akshare 未安装")
        return False
    except Exception as e:
        print(f"❌ 公告测试失败: {e}")
        return False

# ============================================================
# 8. Baostock 行业 + 股东数据
# ============================================================
def test_baostock_industry_shareholder():
    print_section("8. Baostock 行业 + 股东数据")
    
    try:
        import baostock as bs
        
        lg = bs.login()
        print(f"✅ Baostock 登录: error_code={lg.error_code}")
        
        try:
            rs = bs.query_stock_industry(code='sh.600519')
            if rs.error_code == '0':
                row = rs.get_row_data()
                print(f"✅ 行业分类: {row}")
                print(f"   字段: {rs.fields}")
            else:
                print(f"❌ 行业分类失败: {rs.error_msg}")
        except Exception as e:
            print(f"❌ 行业分类异常: {e}")
            
        # 测试季频盈利能力
        try:
            rs = bs.query_profit_data(code='sh.600519', year=2025, quarter=1)
            df = rs.get_data()
            if not df.empty:
                print(f"\n✅ 盈利能力: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
                print(f"   示例: {df.head(2).to_dict('records')}")
            else:
                print("\n❌ 盈利能力: 空数据")
        except Exception as e:
            print(f"\n❌ 盈利能力失败: {e}")
            
        # 测试季频偿债能力
        try:
            rs = bs.query_balance_data(code='sh.600519', year=2025, quarter=1)
            df = rs.get_data()
            if not df.empty:
                print(f"\n✅ 偿债能力: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 偿债能力: 空数据")
        except Exception as e:
            print(f"\n❌ 偿债能力失败: {e}")
            
        # 测试季频营运能力
        try:
            rs = bs.query_cash_flow_data(code='sh.600519', year=2025, quarter=1)
            df = rs.get_data()
            if not df.empty:
                print(f"\n✅ 营运能力: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 营运能力: 空数据")
        except Exception as e:
            print(f"\n❌ 营运能力失败: {e}")
            
        # 测试季频成长能力
        try:
            rs = bs.query_growth_data(code='sh.600519', year=2025, quarter=1)
            df = rs.get_data()
            if not df.empty:
                print(f"\n✅ 成长能力: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 成长能力: 空数据")
        except Exception as e:
            print(f"\n❌ 成长能力失败: {e}")
            
        # 测试股东数据
        try:
            rs = bs.query_stock_industry(code='sh.600519')
            df = rs.get_data()
            if not df.empty:
                print(f"\n✅ 股东数据: {len(df)} 条")
                print(f"   字段: {list(df.columns)}")
            else:
                print("\n❌ 股东数据: 空数据")
        except Exception as e:
            print(f"\n❌ 股东数据失败: {e}")
        
        bs.logout()
        return True
        
    except ImportError:
        print("❌ baostock 未安装")
        return False
    except Exception as e:
        print(f"❌ Baostock测试失败: {e}")
        try:
            bs.logout()
        except Exception:
            pass
        return False

# ============================================================
# 主函数
# ============================================================
if __name__ == '__main__':
    print(f"数据源接口测试 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version}")
    
    results = {}
    
    results['MootDX实时行情'] = test_mootdx_realtime()
    results['腾讯财经'] = test_tencent_quotes()
    results['同花顺THS'] = test_ths_strong_stocks()
    results['机构一致预期'] = test_earnings_forecast()
    results['新闻层'] = test_news_sources()
    results['MootDX财务'] = test_mootdx_fundamentals()
    results['巨潮公告'] = test_cninfo_announcements()
    results['Baostock行业股东'] = test_baostock_industry_shareholder()
    
    print_section("测试总结")
    for name, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {name}")
    
    success_count = sum(1 for v in results.values() if v)
    print(f"\n总计: {success_count}/{len(results)} 个接口测试成功")
