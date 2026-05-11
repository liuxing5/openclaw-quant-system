"""测试数据库连接是否正常工作"""
import os
import sys

# 设置环境变量用于测试（模拟GitHub Actions环境）
# 注意：实际运行时应该从GitHub Secrets中获取这些值
os.environ.setdefault('POSTGRES_HOST', 'aws-1-ap-northeast-1.pooler.supabase.com')
os.environ.setdefault('POSTGRES_PORT', '6543')
os.environ.setdefault('POSTGRES_USER', 'postgres.qoakbxswwjqfsgbcgepr')
os.environ.setdefault('POSTGRES_PASSWORD', 'wYFBB91zViSrk2vl')
os.environ.setdefault('POSTGRES_DB', 'postgres')
os.environ.setdefault('POSTGRES_SSLMODE', 'require')

try:
    from core.db.connection import get_db, db_configured
    
    print("测试1: 检查数据库配置...")
    if db_configured():
        print("✓ 数据库已配置")
    else:
        print("✗ 数据库未配置")
        sys.exit(1)
    
    print("\n测试2: 尝试连接数据库...")
    conn = get_db()
    print("✓ 数据库连接成功")
    
    print("\n测试3: 查询数据库版本...")
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()
    print(f"✓ 数据库版本: {version[0][:50]}...")
    
    print("\n测试4: 查询现有表...")
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = cur.fetchall()
    print(f"✓ 找到 {len(tables)} 个表:")
    for t in tables[:5]:
        print(f"  - {t[0]}")
    if len(tables) > 5:
        print(f"  ... 还有 {len(tables) - 5} 个表")
    
    conn.close()
    print("\n✅ 所有测试通过！数据库连接正常工作。")
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
