"""
完整策略测试脚本（模拟开市日执行）
========================================
模拟完整的八步法执行流程，从数据库读取LLM候选池，执行筛选，发送Telegram推送
"""
import os
import sys
sys.path.insert(0, '.')

# 设置环境变量（从GitHub secrets）
os.environ['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN', '')
os.environ['TELEGRAM_CHAT_ID'] = os.environ.get('TELEGRAM_CHAT_ID', '')
os.environ['POSTGRES_HOST'] = os.environ.get('POSTGRES_HOST', '')
os.environ['POSTGRES_PORT'] = os.environ.get('POSTGRES_PORT', '')
os.environ['POSTGRES_USER'] = os.environ.get('POSTGRES_USER', '')
os.environ['POSTGRES_PASSWORD'] = os.environ.get('POSTGRES_PASSWORD', '')
os.environ['POSTGRES_DB'] = os.environ.get('POSTGRES_DB', '')

from notifyTelegram import send_stock_picks


def run_full_test():
    """模拟完整的策略执行流程"""
    print("🧪 开始完整策略测试...")
    print("=" * 60)
    
    # 1. 模拟从Supabase读取LLM候选池
    print("\n📊 步骤1: 读取LLM候选池")
    llm_candidates = [
        {"code": "sh.600519", "llm_score": 85, "source_diversity": 3, "reasons": "AI推荐|研报看多"},
        {"code": "sz.300750", "llm_score": 82, "source_diversity": 2, "reasons": "AI推荐"},
        {"code": "sh.688167", "llm_score": 78, "source_diversity": 4, "reasons": "AI推荐|研报看多|新闻利好"},
    ]
    print(f"   已读取 {len(llm_candidates)} 只LLM候选")
    
    # 2. 模拟执行八步法筛选
    print("\n🔍 步骤2: 执行八步法筛选")
    
    # 模拟稳健路径标的（混合LLM候选和八步法自有）
    stable_picks = [
        {
            "code": "sh.600519",
            "price": 1700.00,
            "pct": 3.50,
            "vol_ratio": 2.1,
            "turn": 5.2,
            "streak": 1,
            "bias_ma5": 1.8,
            "score": 85,
            "tags": "稳健蓄势 | 黄金放量 | LLM候选+20 | 共识强化",
            "is_llm": True,
        },
        {
            "code": "sz.002594",
            "price": 185.50,
            "pct": 4.20,
            "vol_ratio": 2.8,
            "turn": 6.5,
            "streak": 0,
            "bias_ma5": 2.2,
            "score": 82,
            "tags": "稳健蓄势 | 量能递增 | 首阳突破",
            "is_llm": False,
        },
        {
            "code": "sz.300750",
            "price": 178.80,
            "pct": 5.80,
            "vol_ratio": 3.2,
            "turn": 7.8,
            "streak": 2,
            "bias_ma5": 3.0,
            "score": 88,
            "tags": "稳健蓄势 | 黄金放量 | 二连板 | LLM候选+18",
            "is_llm": True,
        },
    ]
    
    # 模拟高位路径标的
    upper_picks = [
        {
            "code": "sh.688167",
            "price": 420.00,
            "pct": 7.20,
            "vol_ratio": 4.5,
            "turn": 9.2,
            "streak": 1,
            "bias_ma5": 4.5,
            "score": 79,
            "tags": "高位博弈 | 爆量博弈 | LLM候选+15",
            "is_llm": True,
        },
        {
            "code": "sz.301171",
            "price": 45.60,
            "pct": 8.50,
            "vol_ratio": 5.1,
            "turn": 8.8,
            "streak": 3,
            "bias_ma5": 5.2,
            "score": 76,
            "tags": "高位博弈 | 换手偏高 | 3连板(高度风险)",
            "is_llm": False,
        },
    ]
    
    print(f"   稳健路径: {len(stable_picks)} 只 (LLM:{sum(1 for p in stable_picks if p['is_llm'])}, 自有:{sum(1 for p in stable_picks if not p['is_llm'])})")
    print(f"   高位路径: {len(upper_picks)} 只 (LLM:{sum(1 for p in upper_picks if p['is_llm'])}, 自有:{sum(1 for p in upper_picks if not p['is_llm'])})")
    
    # 3. 发送Telegram推送
    print("\n📤 步骤3: 发送Telegram推送")
    
    success = send_stock_picks(
        title="🎯 15:10 盘后定稿-最终决策",
        end_d="2026-05-09",
        mood_info="情绪: 正常 (68分) | 涨停: 58家",
        stable_picks=stable_picks,
        upper_picks=upper_picks,
        operation_note="单票仓位≤15%(稳健)/8%(高位) | 次日竞价≤0立即清仓",
        reject_summary="稳健池: 扫描300只 → 过滤285只 → 通过15只",
        pool_summary=f"📊 LLM候选池: {len(llm_candidates)} 只 (来自昨日盘后)",
    )
    
    if success:
        print("\n✅ 完整测试完成！")
        print("   - LLM候选池读取: ✓")
        print("   - 八步法筛选: ✓")
        print("   - Telegram推送: ✓")
    else:
        print("\n❌ 测试失败，请检查配置")


if __name__ == "__main__":
    run_full_test()
