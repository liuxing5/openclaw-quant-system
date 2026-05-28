import subprocess
result = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True, cwd='D:/pythonProject/openclaw-quant-system')
with open('D:/pythonProject/openclaw-quant-system/git_status.txt', 'w') as f:
    f.write(result.stdout)
    f.write(result.stderr)
