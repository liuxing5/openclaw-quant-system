import sys, os
f = open('py_hello.txt', 'w')
f.write('hello from python\n')
f.write(sys.executable + '\n')
f.write(os.getcwd() + '\n')
f.close()
