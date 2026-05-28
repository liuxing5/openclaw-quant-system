import sys, os, traceback
try:
    sys.path.insert(0, '.')
    import psycopg2
    result = "psycopg2 OK\n"
except Exception as e:
    result = f"psycopg2 ERROR: {e}\n{traceback.format_exc()}\n"

try:
    import pandas
    result += "pandas OK\n"
except Exception as e:
    result += f"pandas ERROR: {e}\n"

try:
    import numpy
    result += "numpy OK\n"
except Exception as e:
    result += f"numpy ERROR: {e}\n"

result += f"Python: {sys.executable}\n"
result += f"CWD: {os.getcwd()}\n"

# 写到多个位置确保至少一个成功
for path in ['py_check.txt', r'D:\pythonProject\openclaw-quant-system\py_check2.txt']:
    try:
        with open(path, 'w') as f:
            f.write(result)
    except Exception as e:
        result += f"Write to {path} failed: {e}\n"
