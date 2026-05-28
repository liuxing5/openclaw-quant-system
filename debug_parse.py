"""Debug: parse data.md"""
with open('strategies/data.md', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.strip().split('\n')
print(f"Total lines: {len(lines)}", flush=True)

# Check first few lines
for i, line in enumerate(lines[:3]):
    print(f"Line {i}: {line[:100]}...", flush=True)

# Parse first data line
if len(lines) > 2:
    line = lines[2].strip()
    cols = [c.strip() for c in line.split('|')]
    cols = [c for c in cols if c]
    print(f"\nFirst data line has {len(cols)} columns:", flush=True)
    for i, c in enumerate(cols):
        print(f"  [{i}] {c[:60]}", flush=True)
