import subprocess, sys, os, time

# Run the actual query script as a detached subprocess
script = os.path.join(os.path.dirname(__file__), 'check_rec_v6.py')
out_file = os.path.join(os.path.dirname(__file__), 'rec_v6_output.txt')

# Use pythonw to avoid console interference
proc = subprocess.Popen(
    [sys.executable, script],
    stdout=open(out_file, 'w', encoding='utf-8'),
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    cwd=os.path.dirname(__file__),
)

print(f"Started process {proc.pid}, waiting...")

# Wait up to 5 minutes
for i in range(60):
    time.sleep(5)
    ret = proc.poll()
    if ret is not None:
        print(f"Process exited with code {ret}")
        break
    if i % 6 == 0:
        print(f"Still running... ({i*5}s)")

if proc.poll() is None:
    print("Process still running after 5 min, terminating")
    proc.terminate()

# Read output
with open(out_file, 'r', encoding='utf-8') as f:
    content = f.read()

print("\n=== OUTPUT ===")
print(content)
