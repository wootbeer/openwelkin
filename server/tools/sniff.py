"""Passive listener: accept connections on the given ports and hex-dump whatever arrives."""
import socket, threading, sys, time

LOG = open(sys.argv[1], "a", buffering=1)

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")

def hexdump(b):
    out = []
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        out.append(f"  {i:04x}  {chunk.hex(' '):<48}  {''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)}")
    return "\n".join(out)

def handle(conn, addr, port):
    log(f"[{port}] connect from {addr}")
    conn.settimeout(120)
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            log(f"[{port}] recv {len(data)} bytes\n{hexdump(data)}")
    except Exception as e:
        log(f"[{port}] error {e!r}")
    log(f"[{port}] closed {addr}")
    conn.close()

def serve(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(5)
    log(f"listening on {port}")
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a, port), daemon=True).start()

for p in map(int, sys.argv[2:]):
    threading.Thread(target=serve, args=(p,), daemon=True).start()
while True:
    time.sleep(1)
