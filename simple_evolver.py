#!/usr/bin/env python3
"""
简单自我进化系统
分析工作空间历史，提出改进建议
"""

import os
import sys
import json
import datetime
import re
from pathlib import Path
import hashlib
import subprocess

class SimpleEvolver:
    def __init__(self, workspace_dir="/root/.openclaw/workspace"):
        self.workspace_dir = workspace_dir
        self.memory_file = os.path.join(workspace_dir, "MEMORY.md")
        self.evolutions_dir = os.path.join(workspace_dir, "memory", "evolutions")
        
        # 确保目录存在
        os.makedirs(self.evolutions_dir, exist_ok=True)
    
    def analyze_memory(self):
        """分析长期记忆"""
        print("📊 分析长期记忆...")
        
        if not os.path.exists(self.memory_file):
            print("  ❌ MEMORY.md 不存在")
            return []
        
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 提取关键部分
            issues = []
            
            # 检查未完成的任务
            todo_pattern = r"\[ \]\s*(.*?)(?:\n|$)"
            todos = re.findall(todo_pattern, content)
            if todos:
                issues.append({
                    'type': 'todo',
                    'description': f"有 {len(todos)} 个未完成的任务",
                    'items': todos[:5]
                })
            
            # 检查警告标记
            warning_pattern = r"⚠️\s*(.*?)(?:\n|$)"
            warnings = re.findall(warning_pattern, content)
            if warnings:
                issues.append({
                    'type': 'warning',
                    'description': f"有 {len(warnings)} 个警告需要处理",
                    'items': warnings[:5]
                })
            
            # 检查改进标记
            improvement_pattern = r"待改进[:：]\s*(.*?)(?:\n|$)"
            improvements = re.findall(improvement_pattern, content, re.IGNORECASE)
            if improvements:
                issues.append({
                    'type': 'improvement',
                    'description': f"有 {len(improvements)} 个待改进项",
                    'items': improvements[:5]
                })
            
            # 统计记忆结构
            sections = re.findall(r"^##\s+(.*?)$", content, re.MULTILINE)
            
            print(f"  ✅ 分析完成: {len(sections)} 个章节, {len(todos)} 个待办, {len(warnings)} 个警告")
            return issues
            
        except Exception as e:
            print(f"  ❌ 分析失败: {e}")
            return []
    
    def analyze_skills(self):
        """分析技能目录"""
        print("📦 分析技能...")
        
        skills_dir = os.path.join(self.workspace_dir, "skills")
        if not os.path.exists(skills_dir):
            print("  ⚠️ skills 目录不存在")
            return []
        
        issues = []
        skill_count = 0
        outdated_skills = []
        
        try:
            for skill_name in os.listdir(skills_dir):
                skill_path = os.path.join(skills_dir, skill_name)
                if os.path.isdir(skill_path):
                    skill_count += 1
                    
                    # 检查SKILL.md文件
                    skill_md = os.path.join(skill_path, "SKILL.md")
                    if not os.path.exists(skill_md):
                        outdated_skills.append(f"{skill_name}: 缺少SKILL.md")
                    
                    # 检查_meta.json
                    meta_json = os.path.join(skill_path, "_meta.json")
                    if not os.path.exists(meta_json):
                        outdated_skills.append(f"{skill_name}: 缺少_meta.json")
            
            if outdated_skills:
                issues.append({
                    'type': 'skill_outdated',
                    'description': f"有 {len(outdated_skills)} 个技能需要更新",
                    'items': outdated_skills[:5]
                })
            
            print(f"  ✅ 发现 {skill_count} 个技能")
            return issues
            
        except Exception as e:
            print(f"  ❌ 技能分析失败: {e}")
            return []
    
    def analyze_quant_system(self):
        """分析量化系统"""
        print("📈 分析量化系统...")
        
        quant_dir = os.path.join(self.workspace_dir, "skills", "quant")
        if not os.path.exists(quant_dir):
            print("  ⚠️ quant 技能不存在")
            return []
        
        issues = []
        
        try:
            # 检查data.py是否存在
            data_py = os.path.join(quant_dir, "lib", "data.py")
            if os.path.exists(data_py):
                with open(data_py, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 检查双数据源实现
                if "Baostock" in content and "AKShare" in content:
                    print("  ✅ 双数据源已实现")
                else:
                    issues.append({
                        'type': 'quant_data_source',
                        'description': "量化数据源需要升级",
                        'suggestion': "实现Baostock+AKShare双数据源"
                    })
            
            # 检查配置文件
            config_yaml = os.path.join(quant_dir, "config.yaml")
            if os.path.exists(config_yaml):
                with open(config_yaml, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if "dual" in content.lower():
                    print("  ✅ 双数据源配置已设置")
                else:
                    issues.append({
                        'type': 'quant_config',
                        'description': "量化配置需要更新",
                        'suggestion': "更新config.yaml支持双数据源"
                    })
            
            return issues
            
        except Exception as e:
            print(f"  ❌ 量化系统分析失败: {e}")
            return []
    
    def generate_evolution_plan(self, issues):
        """生成进化计划"""
        print("\n🧬 生成进化计划...")
        
        if not issues:
            print("  ✅ 未发现需要改进的问题")
            return None
        
        plan = {
            'timestamp': datetime.datetime.now().isoformat(),
            'total_issues': sum(len(issue.get('items', [])) if 'items' in issue else 1 for issue in issues),
            'issue_categories': len(issues),
            'priority_issues': [],
            'recommendations': []
        }
        
        # 分类处理问题
        for issue in issues:
            issue_type = issue.get('type', 'unknown')
            
            if issue_type == 'todo':
                plan['priority_issues'].append({
                    'title': '处理未完成任务',
                    'description': issue['description'],
                    'action': 'complete_todos',
                    'priority': 'high'
                })
                plan['recommendations'].append("更新MEMORY.md，完成标记为[ ]的任务")
                
            elif issue_type == 'warning':
                plan['priority_issues'].append({
                    'title': '处理警告',
                    'description': issue['description'],
                    'action': 'resolve_warnings',
                    'priority': 'medium'
                })
                plan['recommendations'].append("检查并解决MEMORY.md中的⚠️警告")
                
            elif issue_type == 'skill_outdated':
                plan['priority_issues'].append({
                    'title': '更新技能格式',
                    'description': issue['description'],
                    'action': 'update_skills',
                    'priority': 'low'
                })
                plan['recommendations'].append("为缺少SKILL.md或_meta.json的技能添加标准文件")
                
            elif issue_type in ['quant_data_source', 'quant_config']:
                plan['priority_issues'].append({
                    'title': '优化量化系统',
                    'description': issue['description'],
                    'action': 'enhance_quant',
                    'priority': 'medium'
                })
                plan['recommendations'].append(issue.get('suggestion', '优化量化系统配置'))
        
        # 保存进化计划
        plan_file = os.path.join(self.evolutions_dir, 
                                f"evolution_plan_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        
        print(f"  ✅ 进化计划已保存: {plan_file}")
        return plan
    
    def apply_evolution(self, plan):
        """应用进化（演示模式）"""
        print("\n⚡ 应用进化（演示模式）...")
        
        if not plan:
            print("  ⚠️ 没有进化计划可应用")
            return False
        
        print("=" * 60)
        print("进化计划摘要:")
        print("=" * 60)
        
        for i, priority_issue in enumerate(plan.get('priority_issues', []), 1):
            print(f"{i}. [{priority_issue['priority'].upper()}] {priority_issue['title']}")
            print(f"   描述: {priority_issue['description']}")
            print(f"   操作: {priority_issue['action']}")
            print()
        
        print("=" * 60)
        print(f"总计: {plan['total_issues']} 个问题, {plan['issue_categories']} 个类别")
        print("\n📋 建议操作:")
        for rec in plan.get('recommendations', []):
            print(f"  • {rec}")
        
        # 在MEMORY.md中添加进化记录
        self._record_evolution(plan)
        
        return True
    
    def _record_evolution(self, plan):
        """记录进化到MEMORY.md"""
        try:
            with open(self.memory_file, 'a', encoding='utf-8') as f:
                f.write(f"\n### {datetime.datetime.now().strftime('%Y-%m-%d')} ⭐ (自我进化分析)\n")
                f.write(f"- **进化分析时间**: {plan['timestamp']}\n")
                f.write(f"- **发现问题**: {plan['total_issues']} 个问题, {plan['issue_categories']} 个类别\n")
                f.write("- **优先级问题**:\n")
                
                for issue in plan.get('priority_issues', []):
                    f.write(f"  - [{issue['priority']}] {issue['title']}: {issue['description']}\n")
                
                f.write("- **建议操作**:\n")
                for rec in plan.get('recommendations', []):
                    f.write(f"  - {rec}\n")
                
                f.write(f"- **进化状态**: 分析完成，等待执行\n")
            
            print("  ✅ 进化记录已添加到MEMORY.md")
            
        except Exception as e:
            print(f"  ⚠️ 记录进化失败: {e}")
    
    def run(self):
        """运行进化分析"""
        print("🧬 启动简单自我进化系统")
        print("=" * 60)
        
        # 1. 分析记忆
        memory_issues = self.analyze_memory()
        
        # 2. 分析技能
        skill_issues = self.analyze_skills()
        
        # 3. 分析量化系统
        quant_issues = self.analyze_quant_system()
        
        # 合并所有问题
        all_issues = memory_issues + skill_issues + quant_issues
        
        # 4. 生成进化计划
        plan = self.generate_evolution_plan(all_issues)
        
        # 5. 应用进化（演示）
        if plan:
            self.apply_evolution(plan)
        
        print("\n" + "=" * 60)
        print("✅ 自我进化分析完成")
        print("=" * 60)
        
        return plan

def main():
    """主函数"""
    evolver = SimpleEvolver()
    plan = evolver.run()
    
    # 提供后续操作建议
    print("\n🎯 后续操作:")
    print("1. 查看详细进化计划: /root/.openclaw/workspace/memory/evolutions/")
    print("2. 手动执行建议操作")
    print("3. 运行 'python simple_evolver.py' 定期检查")
    print("\n💡 提示: 这是简化版进化系统，专注于分析而非自动执行。")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())