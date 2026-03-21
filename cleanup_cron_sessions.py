#!/usr/bin/env python3
"""
清理会话脚本

支持两种清理模式：
1. 删除所有cron相关的会话（默认）
2. 删除指定的会话key
"""

import json
import os
import sys
import argparse
from datetime import datetime

def cleanup_sessions(sessions_file_path, delete_keys=None, delete_cron=True):
    """
    清理会话
    
    Args:
        sessions_file_path: 会话文件路径
        delete_keys: 要删除的特定会话key列表
        delete_cron: 是否删除所有cron会话
    
    Returns:
        清理结果统计
    """
    
    print(f"清理会话文件: {sessions_file_path}")
    
    # 读取原始文件
    with open(sessions_file_path, 'r', encoding='utf-8') as f:
        sessions_data = json.load(f)
    
    print(f"原始会话总数: {len(sessions_data)}")
    
    # 统计要删除的会话
    sessions_to_delete = []
    
    # 如果需要删除cron会话
    if delete_cron:
        cron_sessions = [key for key in sessions_data.keys() if 'cron' in key]
        sessions_to_delete.extend(cron_sessions)
        print(f"要删除的Cron会话数: {len(cron_sessions)}")
        if cron_sessions:
            print("Cron会话列表:")
            for i, key in enumerate(cron_sessions[:20]):  # 只显示前20个
                print(f"  {i+1}. {key}")
            if len(cron_sessions) > 20:
                print(f"  ... 和 {len(cron_sessions) - 20} 个更多会话")
    
    # 如果需要删除特定key
    if delete_keys:
        specific_keys = []
        for key_pattern in delete_keys:
            # 支持精确匹配和部分匹配
            matched_keys = [k for k in sessions_data.keys() if key_pattern in k]
            specific_keys.extend(matched_keys)
        
        # 去重
        specific_keys = list(set(specific_keys))
        
        print(f"要删除的特定会话数: {len(specific_keys)}")
        if specific_keys:
            print("特定会话列表:")
            for i, key in enumerate(specific_keys):
                print(f"  {i+1}. {key}")
            
            # 添加到删除列表
            sessions_to_delete.extend(specific_keys)
    
    # 去重
    sessions_to_delete = list(set(sessions_to_delete))
    
    # 列出要保留的会话
    sessions_to_keep = [key for key in sessions_data.keys() if key not in sessions_to_delete]
    print(f"\n要保留的会话数: {len(sessions_to_keep)}")
    if sessions_to_keep:
        print("保留的会话列表:")
        for key in sessions_to_keep:
            print(f"  - {key}")
    
    # 删除会话
    for key in sessions_to_delete:
        if key in sessions_data:
            del sessions_data[key]
            print(f"  ✅ 已删除: {key}")
    
    print(f"\n清理后会话总数: {len(sessions_data)}")
    
    # 写回文件
    with open(sessions_file_path, 'w', encoding='utf-8') as f:
        json.dump(sessions_data, f, indent=2, ensure_ascii=False)
    
    print("✅ 会话文件已更新")
    
    return {
        'total_before': len(sessions_to_delete) + len(sessions_to_keep),
        'deleted_count': len(sessions_to_delete),
        'kept_count': len(sessions_to_keep),
        'total_after': len(sessions_data),
        'deleted_keys': sessions_to_delete,
        'kept_keys': sessions_to_keep
    }

def main():
    parser = argparse.ArgumentParser(description='清理OpenClaw会话')
    parser.add_argument('--delete-key', action='append', 
                       help='要删除的会话key（支持部分匹配，可多次使用）')
    parser.add_argument('--no-cron', action='store_false', dest='delete_cron',
                       default=True, help='不删除cron会话')
    parser.add_argument('--dry-run', action='store_true',
                       help='只显示要删除的会话，不实际删除')
    parser.add_argument('--sessions-file', 
                       default="/root/.openclaw/agents/main/sessions/sessions.json",
                       help='会话文件路径（默认: %(default)s）')
    
    args = parser.parse_args()
    
    sessions_file = args.sessions_file
    
    if not os.path.exists(sessions_file):
        print(f"错误: 会话文件不存在: {sessions_file}")
        sys.exit(1)
    
    # 备份文件
    backup_file = f"{sessions_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    import shutil
    shutil.copy2(sessions_file, backup_file)
    print(f"已创建备份: {backup_file}")
    
    if args.dry_run:
        print("\n" + "="*80)
        print("DRY RUN模式 - 只显示要删除的会话，不实际删除")
        print("="*80)
        
        # 读取文件
        with open(sessions_file, 'r', encoding='utf-8') as f:
            sessions_data = json.load(f)
        
        print(f"当前会话总数: {len(sessions_data)}")
        
        # 计算要删除的会话
        sessions_to_delete = []
        
        if args.delete_cron:
            cron_sessions = [key for key in sessions_data.keys() if 'cron' in key]
            sessions_to_delete.extend(cron_sessions)
            print(f"\n要删除的Cron会话数: {len(cron_sessions)}")
        
        if args.delete_key:
            for key_pattern in args.delete_key:
                matched_keys = [k for k in sessions_data.keys() if key_pattern in k]
                sessions_to_delete.extend(matched_keys)
                print(f"\n匹配模式 '{key_pattern}' 的会话数: {len(matched_keys)}")
                for key in matched_keys:
                    print(f"  - {key}")
        
        sessions_to_delete = list(set(sessions_to_delete))
        
        print(f"\n总共要删除的会话数: {len(sessions_to_delete)}")
        print(f"删除后剩余会话数: {len(sessions_data) - len(sessions_to_delete)}")
        
        sys.exit(0)
    
    # 实际清理
    result = cleanup_sessions(
        sessions_file, 
        delete_keys=args.delete_key,
        delete_cron=args.delete_cron
    )
    
    print("\n" + "="*80)
    print("清理结果汇总:")
    print("="*80)
    print(f"清理前总会话数: {result['total_before']}")
    print(f"删除的会话数: {result['deleted_count']}")
    print(f"保留的会话数: {result['kept_count']}")
    print(f"清理后总会话数: {result['total_after']}")
    print("="*80)
    
    # 验证清理结果
    with open(sessions_file, 'r', encoding='utf-8') as f:
        final_data = json.load(f)
    
    # 检查是否还有cron会话（如果要求删除的话）
    if args.delete_cron:
        remaining_cron = sum(1 for key in final_data.keys() if 'cron' in key)
        if remaining_cron == 0:
            print("✅ 所有cron会话已成功删除")
        else:
            print(f"⚠️  仍有 {remaining_cron} 个cron会话未被删除")
            for key in final_data.keys():
                if 'cron' in key:
                    print(f"  - {key}")
    
    # 检查特定key是否已删除
    if args.delete_key:
        for key_pattern in args.delete_key:
            remaining_keys = [k for k in final_data.keys() if key_pattern in k]
            if not remaining_keys:
                print(f"✅ 所有匹配 '{key_pattern}' 的会话已删除")
            else:
                print(f"⚠️  仍有 {len(remaining_keys)} 个匹配 '{key_pattern}' 的会话未被删除")
                for key in remaining_keys:
                    print(f"  - {key}")

if __name__ == "__main__":
    main()