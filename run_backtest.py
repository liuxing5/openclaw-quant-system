import subprocess
import sys

result = subprocess.run(
    [sys.executable, "backtest_t_plus1.py"],
    capture_output=True,
    text=True,
    timeout=600
)

with open("backtest_run_output.txt", "w", encoding="utf-8") as f:
    f.write("STDOUT:\n")
    f.write(result.stdout)
    f.write("\n\nSTDERR:\n")
    f.write(result.stderr)
    f.write(f"\n\nReturn code: {result.returncode}")

print(f"Done. Return code: {result.returncode}")
print(f"Output written to backtest_run_output.txt")
