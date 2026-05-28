#!/usr/bin/env python3
"""生成 baostock_backtester.py v2.0 的脚本"""
import os

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baostock_backtester.py')
PARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_bt_v2_parts')

os.makedirs(PARTS_DIR, exist_ok=True)

part_files = sorted([f for f in os.listdir(PARTS_DIR) if f.startswith('part') and f.endswith('.py')])
if not part_files:
    print("ERROR: No part files found in", PARTS_DIR)
    exit(1)

print(f"Found {len(part_files)} part files: {part_files}")

full_code = []
for pf in part_files:
    filepath = os.path.join(PARTS_DIR, pf)
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()
    full_code.append(code)
    print(f"  Loaded {pf}: {len(code)} chars")

combined = '\n'.join(full_code)

backup = TARGET + '.v1.bak'
if os.path.exists(TARGET) and not os.path.exists(backup):
    import shutil
    shutil.copy2(TARGET, backup)
    print(f"Backup saved: {backup}")

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(combined)

print(f"\nWritten: {TARGET}")
print(f"Total size: {len(combined)} chars, {len(combined.splitlines())} lines")

checks = {
    'Regime': 'regime',
    'DualPool': "pool = 'stable'",
    'IndustryRot': 'industry_rotation',
    'Seal': 'seal_quality',
    'Sentiment': 'sentiment_score',
    'Layer6': 'layer6_sustain',
    'Compare': 'strategy_compare',
    'DynamicWeights': 'weights_bull',
}
print("\nVerification:")
for name, keyword in checks.items():
    found = keyword in combined
    print(f"  {name}: {'OK' if found else 'MISSING!'}")
