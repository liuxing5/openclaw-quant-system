"""Test if Python execution works"""
import os
print(f"Current dir: {os.getcwd()}")
with open('test_output.txt', 'w') as f:
    f.write('Python is working!\n')
    f.write(f"Current dir: {os.getcwd()}\n")
print('File written')
