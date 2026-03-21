#!/usr/bin/env python3
import os
import json

skills_dir = "/root/.openclaw/workspace/skills"
missing_skills = []

for skill_name in os.listdir(skills_dir):
    skill_path = os.path.join(skills_dir, skill_name)
    if os.path.isdir(skill_path):
        skill_md = os.path.join(skill_path, "SKILL.md")
        meta_json = os.path.join(skill_path, "_meta.json")
        
        missing_files = []
        if not os.path.exists(skill_md):
            missing_files.append("SKILL.md")
        if not os.path.exists(meta_json):
            missing_files.append("_meta.json")
        
        if missing_files:
            missing_skills.append({
                'skill': skill_name,
                'missing': missing_files
            })

print(f"检查 {len(os.listdir(skills_dir))} 个技能目录")
print(f"发现 {len(missing_skills)} 个技能缺少标准文件:")
for skill in missing_skills:
    print(f"  - {skill['skill']}: 缺少 {', '.join(skill['missing'])}")