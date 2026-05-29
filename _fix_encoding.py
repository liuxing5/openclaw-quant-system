"""Fix remaining syntax errors in layer0_market_guard.py"""

filepath = r'strategies\funnel_strategy\layer0_market_guard.py'

with open(filepath, 'rb') as f:
    data = f.read()

replacements = [
    # Line 257: 满仓操作\r\n -> 满仓操作'\r\n
    (b'\xe6\xbb\xa1\xe4\xbb\x93\xe6\x93\x8d\xe4\xbd\x9c\r\n',
     b'\xe6\xbb\xa1\xe4\xbb\x93\xe6\x93\x8d\xe4\xbd\x9c\'\r\n'),

    # Line 262: EMA)，\r\n -> EMA)，'\r\n
    (b'}{ema_period}EMA)\xef\xbc\x8c\r\n',
     b'}{ema_period}EMA)\xef\xbc\x8c\'\r\n'),

    # Line 271: ❌休或?) -> ❌休市')
    (b'\xe2\x9d\x8c\xe4\xbc\x91\xe6\x88?)',
     b'\xe2\x9d\x8c\xe4\xbc\x91\xe5\xb8\x82\')'),
]

for old, new in replacements:
    count = data.count(old)
    if count > 0:
        data = data.replace(old, new)
        print(f'Replaced {count}x: {old!r} -> {new!r}')
    else:
        print(f'NOT FOUND: {old!r}')

with open(filepath, 'wb') as f:
    f.write(data)

print('\nDone. Verifying...')

import py_compile
try:
    py_compile.compile(filepath, doraise=True)
    print('Syntax OK')
except py_compile.PyCompileError as e:
    print(f'Syntax error: {e}')
