"""
策略整合器 — 八步法 + LLM多源策略
================================================
将主升前夜策略的候选池输入八步法进行二次筛选
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "overnight_8step"))
sys.path.append(str(Path(__file__).parent / "llm_multisource/pre_surge"))

from overnight_8step.zuiyou1 import scan_pool, fetch_market_sentiment, CONFIG
from llm_multisource.pre_surge.main import cmd_scan as llm_scan
from llm_multisource.pre_surge.config import ScreenerConfig
from llm_multisource.pre_surge.data_loader import DataLoader
from llm_multisource.pre_surge.screener import scan_universe


def load_llm_candidates(csv_path: str, min_score: int = 5) -> list[tuple[str, str]]:
    """从LLM策略扫描结果加载候选标的"""
    candidates = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                score = int(row.get('score', 0))
                if score >= min_score:
                    code = row['symbol']
                    name = row['name']
                    candidates.append((code, name))
        print(f"✓ 从 {csv_path} 加载 {len(candidates)} 只候选标的")
    except Exception as e:
        print(f"✗ 加载候选文件失败: {e}")
    return candidates


def run_combined_strategy(llm_input: str = None, output_dir: str = "./results"):
    """运行整合策略"""
    print("=" * 70)
    print("  策略整合器 — 八步法 + LLM多源策略")
    print("=" * 70)
    
    # 步骤1: 获取候选池
    candidates = []
    if llm_input and Path(llm_input).exists():
        # 使用已有LLM扫描结果
        candidates = load_llm_candidates(llm_input)
    else:
        # 实时运行LLM策略
        print("\n[Step 1/3] 运行LLM多源策略筛选候选池...")
        loader = DataLoader()
        cfg = ScreenerConfig()
        
        # 获取全市场股票列表
        all_stocks = loader.get_all_stocks()
        if all_stocks is None or all_stocks.empty:
            print("✗ 无法获取股票列表")
            return
        
        # 过滤A股普通股
        valid_prefixes = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "688")
        df = all_stocks.copy()
        df = df[df["symbol"].astype(str).str.startswith(valid_prefixes)]
        df = df[~df["name"].str.contains("ST|退", regex=True, na=False)]
        
        symbols = list(zip(df["symbol"].astype(str), df["name"]))
        print(f"  待扫描: {len(symbols)} 只")
        
        # 运行LLM筛选
        result = scan_universe(symbols[:200], cfg=cfg, loader=loader)  # 取前200只加速
        
        # 提取候选(得分>=5的)
        candidates = []
        for _, row in result.iterrows():
            if row.get('score', 0) >= 5:
                candidates.append((row['symbol'], row['name']))
        print(f"  LLM策略筛选结果: {len(candidates)} 只候选")
    
    if not candidates:
        print("✗ 没有候选标的")
        return
    
    # 步骤2: 获取市场情绪
    print("\n[Step 2/3] 获取市场情绪数据...")
    sentiment = fetch_market_sentiment()
    sentiment_score = sentiment.get('score', 50)
    print(f"  市场情绪评分: {sentiment_score:.1f}")
    print(f"  涨停家数: {sentiment.get('zt_count', 0)}")
    print(f"  涨跌比: {sentiment.get('up_down_ratio', 0):.2f}")
    
    # 步骤3: 八步法二次筛选
    print("\n[Step 3/3] 八步法二次筛选...")
    
    # 将候选转换为八步法格式
    stock_list = [f"{code}.SH" if code.startswith('6') else f"{code}.SZ" for code, _ in candidates]
    
    # 运行八步法扫描
    result = scan_pool(stock_list, verbose=True)
    
    # 输出结果
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_path = out_dir / f"combined_{today}.csv"
    
    if result:
        # 写入CSV
        with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['code', 'name', 'pct', 'vol_ratio', 'turn', 'score', 'tags', 'industry'])
            for item in result:
                writer.writerow([
                    item.get('code', ''),
                    item.get('name', ''),
                    item.get('pct', 0),
                    item.get('vol_ratio', 0),
                    item.get('turn', 0),
                    item.get('score', 0),
                    '|'.join(item.get('tags', [])),
                    item.get('industry', '')
                ])
        
        print(f"\n✅ 整合筛选完成!")
        print(f"   输出文件: {out_path}")
        print(f"   入选标的: {len(result)} 只")
        
        # 打印详细结果
        print("\n--- 入选标的详情 ---")
        for item in result:
            print(f"  {item['code']} {item['name']}")
            print(f"    涨幅: {item['pct']:.2f}%  量比: {item['vol_ratio']:.2f}  换手: {item['turn']:.2f}%")
            print(f"    评分: {item['score']}  标签: {' | '.join(item['tags'])}")
            print()
    else:
        print("✗ 没有通过双重筛选的标的")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="策略整合器")
    parser.add_argument("--input", "-i", help="LLM策略扫描结果CSV路径")
    parser.add_argument("--output", "-o", default="./results", help="输出目录")
    args = parser.parse_args()
    
    run_combined_strategy(args.input, args.output)
