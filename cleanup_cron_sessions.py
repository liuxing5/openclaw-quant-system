#!/usr/bin/env python3
"""
清理cron会话脚本

删除所有cron相关的会话，只保留主会话和其他非cron会话
"""

import json
import os
from datetime import datetime

def cleanup_cron_sessions(sessions_file_path):
    """清理cron会话"""
    
    print(f"清理会话文件: {sessions_file_path}")
    
    # 读取原始文件
    with open(sessions_file_path, 'r', encoding='utf-8') as f:
        sessions_data = json.load(f)
    
    print(f"原始会话总数: {len(sessions_data)}")
    
    # 统计不同类型的会话
    cron_sessions = []
    non_cron_sessions = []
    
    for session_key in list(sessions_data.keys()):
        if 'cron' in session_key:
            cron_sessions.append(session_key)
        else:
            non_cron_sessions.append(session_key)
    
    print(f"Cron会话数: {len(cron_sessions)}")
    print(f"非Cron会话数: {len(non_cron_sessions)}")
    
    # 列出要删除的cron会话
    print("\n要删除的Cron会话:")
    for i, key in enumerate(cron_sessions[:20]):  # 只显示前20个
        print(f"  {i+1}. {key}")
    if len(cron_sessions) > 20:
        print(f"  ... 和 {len(cron_sessions) - 20} 个更多会话")
    
    # 列出要保留的非cron会话
    print("\n要保留的非Cron会话:")
    for key in non_cron_sessions:
        print(f"  - {key}")
    
    # 删除cron会话
    for cron_key in cron_sessions:
        del sessions_data[cron_key]
    
    print(f"\n清理后会话总数: {len(sessions_data)}")
    
    # 写回文件
    with open(sessions_file_path, 'w', encoding='utf-8') as f:
        json.dump(sessions_data, f, indent=2, ensure_ascii=False)
    
    print("✅ 会话文件已更新")
    
    return {
        'total_before': len(cron_sessions) + len(non_cron_sessions),
        'cron_deleted': len(cron_sessions),
        'non_cron_kept': len(non_cron_sessions),
        'total_after': len(sessions_data)
    }

def main():
    sessions_file = "/root/.openclaw/agents/main/sessions/sessions.json"
    
    if not os.path.exists(sessions_file):
        print(f"错误: 会话文件不存在: {sessions_file}")
        return
    
    # 备份文件
    backup_file = f"{sessions_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    import shutil
    shutil.copy2(sessions_file, backup_file)
    print(f"已创建备份: {backup_file}")
    
    # 清理会话
    result = cleanup_cron_sessions(sessions_file)
    
    print("\n" + "="*80)
    print("清理结果汇总:")
    print("="*80)
    print(f"清理前总会话数: {result['total_before']}")
    print(f"删除的Cron会话数: {result['cron_deleted']}")
    print(f"保留的非Cron会话数: {result['non_cron_kept']}")
    print(f"清理后总会话数: {result['total_after']}")
    print("="*80)
    
    # 验证清理结果
    with open(sessions_file, 'r', encoding='utf-8') as f:
        final_data = json.load(f)
    
    remaining_cron = sum(1 for key in final_data.keys() if 'cron' in key)
    if remaining_cron == 0:
        print("✅ 所有cron会话已成功删除")
    else:
        print(f"⚠️  仍有 {remaining_cron} 个cron会话未被删除")
        for key in final_data.keys():
            if 'cron' in key:
                print(f"  - {key}")

if __name__ == "__main__":
    main()