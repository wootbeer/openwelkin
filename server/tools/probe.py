"""probe.py <js-file> [seconds] [pid] -- run a one-off Frida snippet in the running client and print its messages."""
import sys, time, frida
js = open(sys.argv[1]).read()
target = int(sys.argv[3]) if len(sys.argv) > 3 else "MarsWar.exe"
s = frida.attach(target).create_script(js)
s.on("message", lambda m, d: print(m.get("payload", m), flush=True))
s.load()
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 2)
