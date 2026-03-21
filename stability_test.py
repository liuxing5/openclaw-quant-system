#!/usr/bin/env python3
"""
方案1稳定性测试脚本 - 1小时测试计划
测试组件：
1. Web服务稳定性 (Flask app)
2. 数据源连接性 (腾讯财经、tushare、akshare)
3. 缓存系统有效性
4. 监控系统运行状态
5. 量化信号功能
"""

import requests
import time
import json
import subprocess
import sys
from datetime import datetime, timedelta
import concurrent.futures

BASE_URL = "http://localhost"
TEST_STOCKS = ["600519", "300750", "002415"]  # 贵州茅台, 宁德时代, 海康威视
TEST_PERIODS = ["1d", "1w", "1m", "1y"]

def test_web_endpoint(url, name):
    """测试Web端点"""
    try:
        start_time = time.time()
        response = requests.get(url, timeout=10)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json() if 'api' in url else None
            return {
                "name": name,
                "status": "✅ PASS",
                "response_time": f"{elapsed:.2f}s",
                "status_code": response.status_code,
                "data_size": len(response.content) if response.content else 0
            }
        else:
            return {
                "name": name,
                "status": "❌ FAIL",
                "response_time": f"{elapsed:.2f}s",
                "status_code": response.status_code,
                "error": f"HTTP {response.status_code}"
            }
    except Exception as e:
        return {
            "name": name,
            "status": "❌ ERROR",
            "response_time": "N/A",
            "status_code": 0,
            "error": str(e)[:100]
        }

def test_data_source(stock_code, period):
    """测试数据源API"""
    url = f"{BASE_URL}/api/chart/{stock_code}?period={period}"
    try:
        start_time = time.time()
        response = requests.get(url, timeout=15)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            return {
                "stock": stock_code,
                "period": period,
                "status": "✅ PASS",
                "response_time": f"{elapsed:.2f}s",
                "data_source": data.get("data_source", "unknown"),
                "data_quality": data.get("data_quality", "unknown"),
                "data_points": len(data.get("prices", [])),
                "price": data.get("latest_data", {}).get("price", 0),
                "sources_tried": data.get("sources_tried", [])
            }
        else:
            return {
                "stock": stock_code,
                "period": period,
                "status": "❌ FAIL",
                "response_time": f"{elapsed:.2f}s",
                "status_code": response.status_code
            }
    except Exception as e:
        return {
            "stock": stock_code,
            "period": period,
            "status": "❌ ERROR",
            "response_time": "N/A",
            "error": str(e)[:100]
        }

def test_monitor_system():
    """测试监控系统"""
    try:
        # 检查进程
        result = subprocess.run(
            ["ps", "aux", "|", "grep", "auto_monitor_v2.py", "|", "grep", "-v", "grep"],
            capture_output=True, text=True, shell=True
        )
        
        if "auto_monitor_v2.py" in result.stdout:
            # 检查日志
            log_result = subprocess.run(
                ["tail", "-5", "/root/.openclaw/workspace/predict_engine/monitor.log"],
                capture_output=True, text=True
            )
            
            return {
                "name": "股票监控系统",
                "status": "✅ RUNNING",
                "process_found": True,
                "log_lines": len(log_result.stdout.strip().split('\n')),
                "last_log": log_result.stdout.strip().split('\n')[-1][:100] if log_result.stdout else "N/A"
            }
        else:
            return {
                "name": "股票监控系统",
                "status": "❌ STOPPED",
                "process_found": False,
                "error": "监控进程未找到"
            }
    except Exception as e:
        return {
            "name": "股票监控系统",
            "status": "❌ ERROR",
            "error": str(e)[:100]
        }

def test_quant_signals():
    """测试量化交易信号系统"""
    try:
        script_path = "/root/.openclaw/workspace/skills/quant-trading-signals/scripts/signals.py"
        
        # 测试AAPL美股
        result = subprocess.run(
            ["python3", script_path, "AAPL"],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            return {
                "name": "量化交易信号",
                "status": "✅ PASS",
                "symbol": "AAPL",
                "output_summary": output[:200] + "..." if len(output) > 200 else output
            }
        else:
            return {
                "name": "量化交易信号",
                "status": "❌ FAIL",
                "symbol": "AAPL",
                "error": result.stderr[:100]
            }
    except Exception as e:
        return {
            "name": "量化交易信号",
            "status": "❌ ERROR",
            "error": str(e)[:100]
        }

def run_stability_test(duration_minutes=60):
    """运行稳定性测试"""
    print("=" * 80)
    print(f"方案1稳定性测试开始 - 持续时间: {duration_minutes}分钟")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    test_results = {
        "web_services": [],
        "data_sources": [],
        "monitor_system": None,
        "quant_signals": None,
        "timestamps": []
    }
    
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)
    
    # 初始状态检查
    print("\n📊 初始状态检查:")
    print("-" * 40)
    
    # 测试Web服务
    web_endpoints = [
        (f"{BASE_URL}/", "首页"),
        (f"{BASE_URL}/chart/600519", "贵州茅台走势图"),
        (f"{BASE_URL}/api/chart/600519?period=1d", "API日线数据")
    ]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        web_futures = [executor.submit(test_web_endpoint, url, name) for url, name in web_endpoints]
        for future in concurrent.futures.as_completed(web_futures):
            result = future.result()
            test_results["web_services"].append(result)
            print(f"{result['name']}: {result['status']} ({result['response_time']})")
    
    # 测试数据源
    print("\n📈 数据源测试:")
    print("-" * 40)
    
    data_tests = []
    for stock in TEST_STOCKS[:1]:  # 先测试一个股票
        for period in TEST_PERIODS:
            data_tests.append((stock, period))
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        data_futures = [executor.submit(test_data_source, stock, period) for stock, period in data_tests]
        for future in concurrent.futures.as_completed(data_futures):
            result = future.result()
            test_results["data_sources"].append(result)
            print(f"{result['stock']} {result['period']}: {result['status']} "
                  f"(来源:{result.get('data_source', 'N/A')}, "
                  f"质量:{result.get('data_quality', 'N/A')}, "
                  f"价格:{result.get('price', 0):.2f})")
    
    # 测试监控系统
    print("\n🔍 监控系统测试:")
    print("-" * 40)
    monitor_result = test_monitor_system()
    test_results["monitor_system"] = monitor_result
    print(f"{monitor_result['name']}: {monitor_result['status']}")
    if monitor_result.get('last_log'):
        print(f"最新日志: {monitor_result['last_log']}")
    
    # 测试量化信号
    print("\n📊 量化信号测试:")
    print("-" * 40)
    quant_result = test_quant_signals()
    test_results["quant_signals"] = quant_result
    print(f"{quant_result['name']}: {quant_result['status']}")
    if quant_result.get('output_summary'):
        print(f"输出摘要: {quant_result['output_summary']}")
    
    # 持续监控
    print(f"\n🔄 开始持续监控 ({duration_minutes}分钟)...")
    iteration = 1
    
    while time.time() < end_time:
        current_time = datetime.now().strftime('%H:%M:%S')
        remaining = int((end_time - time.time()) / 60)
        
        print(f"\n⏱️ 迭代 {iteration} - {current_time} (剩余: {remaining}分钟)")
        
        # 定期测试关键API
        critical_tests = [
            (f"{BASE_URL}/api/chart/600519?period=1d", "API日线"),
            (f"{BASE_URL}/", "首页")
        ]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(test_web_endpoint, url, name) for url, name in critical_tests]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                print(f"  {result['name']}: {result['status']} ({result['response_time']})")
        
        # 记录时间戳
        test_results["timestamps"].append({
            "time": current_time,
            "iteration": iteration,
            "remaining_minutes": remaining
        })
        
        iteration += 1
        time.sleep(300)  # 每5分钟检查一次
    
    # 生成最终报告
    print("\n" + "=" * 80)
    print("方案1稳定性测试完成")
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总测试时长: {duration_minutes}分钟")
    print(f"总迭代次数: {iteration - 1}")
    print("=" * 80)
    
    # 汇总统计
    print("\n📋 测试结果汇总:")
    print("-" * 40)
    
    total_tests = len(test_results["web_services"]) + len(test_results["data_sources"]) + 2
    passed_tests = 0
    
    for test in test_results["web_services"]:
        if "PASS" in test["status"]:
            passed_tests += 1
    
    for test in test_results["data_sources"]:
        if "PASS" in test["status"]:
            passed_tests += 1
    
    if test_results["monitor_system"] and "RUNNING" in test_results["monitor_system"]["status"]:
        passed_tests += 1
    
    if test_results["quant_signals"] and "PASS" in test_results["quant_signals"]["status"]:
        passed_tests += 1
    
    success_rate = (passed_tests / total_tests) * 100
    
    print(f"总测试项: {total_tests}")
    print(f"通过项: {passed_tests}")
    print(f"成功率: {success_rate:.1f}%")
    
    # 保存测试结果
    result_file = f"/root/.openclaw/workspace/stability_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(test_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n📄 详细结果已保存至: {result_file}")
    
    # 结论
    print("\n🎯 稳定性评估:")
    print("-" * 40)
    if success_rate >= 90:
        print("✅ 优秀 - 方案1稳定性非常好，可以投入生产使用")
    elif success_rate >= 80:
        print("🟡 良好 - 方案1稳定性良好，建议持续观察")
    elif success_rate >= 70:
        print("🟠 一般 - 方案1稳定性一般，需要优化")
    else:
        print("🔴 较差 - 方案1稳定性较差，需要修复")
    
    return test_results, success_rate

if __name__ == "__main__":
    # 默认运行60分钟测试，但可以传参数
    duration = 60  # 分钟
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except:
            pass
    
    try:
        results, success_rate = run_stability_test(duration)
        sys.exit(0 if success_rate >= 80 else 1)
    except KeyboardInterrupt:
        print("\n测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n测试发生错误: {e}")
        sys.exit(1)