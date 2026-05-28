

# ============================================================
# CLI入口
# ============================================================

def run_backtest():
    """运行回测"""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    cfg = MetaBacktestConfig()
    pm_cfg = PositionManagerConfig()

    backtester = BaostockBacktester(cfg, pm_cfg)
    result = backtester.run()

    if result:
        print(result['summary'])

        out_dir = Path(cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M')

        if result['trades']:
            trades_df = pd.DataFrame(result['trades'])
            trades_path = out_dir / f"meta_bt_v2_trades_{timestamp}.csv"
            trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
            print(f"\n  交易记录: {trades_path}")

        if not result['daily_equity'].empty:
            eq_path = out_dir / f"meta_bt_v2_equity_{timestamp}.csv"
            result['daily_equity'].to_csv(eq_path, index=False, encoding='utf-8-sig')
            print(f"  权益曲线: {eq_path}")

        report_path = out_dir / f"meta_bt_v2_report_{timestamp}.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(result['summary'])
        print(f"  回测报告: {report_path}")

        # 保存策略对比结果
        if result.get('compare_results'):
            compare_path = out_dir / f"meta_bt_v2_compare_{timestamp}.json"
            compare_data = {}
            for strategy_name, cmp in result['compare_results'].items():
                trades = cmp.get('trades', [])
                if trades:
                    pnls = [t['pnl_pct'] for t in trades]
                    compare_data[strategy_name] = {
                        'total_trades': len(trades),
                        'win_rate': round(len([p for p in pnls if p > 0]) / len(pnls), 4) if pnls else 0,
                        'avg_return': round(float(np.mean(pnls)), 4) if pnls else 0,
                        'total_return': round(float(sum(pnls)), 4) if pnls else 0,
                    }
                else:
                    compare_data[strategy_name] = {'total_trades': 0}
            with open(compare_path, 'w', encoding='utf-8') as f:
                json.dump(compare_data, f, ensure_ascii=False, indent=2)
            print(f"  策略对比: {compare_path}")

    return result


if __name__ == "__main__":
    run_backtest()
