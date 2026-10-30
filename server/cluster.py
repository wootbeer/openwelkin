# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""Private links between the split servers (login <- gate <- game).

One TCP connection per link, opened by the lower server: a gate connects to login, a game server to its gate.
Both ends prove they hold internal.key, without sending it, and every frame then carries an HMAC and a sequence
number, so a stranger on the network can neither talk on a link nor change or replay what is said. Frames are not
encrypted: keep these ports on 127.0.0.1 or a private network.

Frame: u32 length | 32 bytes HMAC-SHA256(session key, direction + u64 sequence + json) | json object
A message is {"t": kind, ...}; a request also has "id" and is answered with {"re": id, "r": result}."""
import hashlib
import hmac
import itertools
import json
import secrets
import socket
import struct
import threading
import time

MAX_FRAME = 1 << 20      # a character record is far smaller
HANDSHAKE_TIMEOUT = 5.0
MAX_LINKS = 64           # open links, and connections still proving themselves, per listening port
RETRY = 2.0              # seconds between attempts to reach the upper server
HEAD = struct.Struct("<I")
SEQ = struct.Struct("<Q")


def load_key(path):
    """The secret in this file, made on first use. Every server of one installation needs the same internal.key."""
    try:
        key = open(path, encoding="ascii").read().strip()
        if key:
            return key.encode()
    except OSError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w", encoding="ascii") as f:
        f.write(key)
    return key.encode()


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("link closed")
        data += chunk
    return data


class Link:
    """One end of an authenticated link. handler(link, msg) runs on the link's reader thread; for a request its
    return value is the answer. Anything may be kept on the object (a gate's load, a game server's address)."""

    def __init__(self, sock, secret, handler, on_close=None):
        self.sock, self.secret, self.handler, self.on_close = sock, secret, handler, on_close
        self.send_lock = threading.Lock()
        self.pending = {}   # request id -> [event, answer]
        self.ids = itertools.count(1)
        self.sent, self.received = 0, 0
        self.closed = False

    def proof(self, label, first, second):
        return hmac.new(self.secret, label + first + second, hashlib.sha256).digest()

    def handshake(self, connecting):
        """Both sides show they know the secret, on fresh random values, and derive the key for this link."""
        self.sock.settimeout(HANDSHAKE_TIMEOUT)
        mine = secrets.token_bytes(32)
        if connecting:
            theirs = recv_exact(self.sock, 32)
            self.sock.sendall(mine + self.proof(b"connect", theirs, mine))
            ok = hmac.compare_digest(recv_exact(self.sock, 32), self.proof(b"accept", mine, theirs))
            server_nonce, client_nonce = theirs, mine
        else:
            self.sock.sendall(mine)
            theirs = recv_exact(self.sock, 32)
            ok = hmac.compare_digest(recv_exact(self.sock, 32), self.proof(b"connect", mine, theirs))
            if ok:
                self.sock.sendall(self.proof(b"accept", theirs, mine))
            server_nonce, client_nonce = mine, theirs
        if not ok:
            raise ConnectionError("wrong internal key")
        self.session_key = self.proof(b"session", server_nonce, client_nonce)
        self.out_dir, self.in_dir = (b"c", b"s") if connecting else (b"s", b"c")
        self.sock.settimeout(None)

    def mac(self, direction, seq, data):
        return hmac.new(self.session_key, direction + SEQ.pack(seq) + data, hashlib.sha256).digest()

    def write(self, obj):
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        with self.send_lock:
            self.sent += 1
            self.sock.sendall(HEAD.pack(len(data)) + self.mac(self.out_dir, self.sent, data) + data)

    def read(self):
        (size,) = HEAD.unpack(recv_exact(self.sock, 4))
        if size > MAX_FRAME:
            raise ConnectionError("frame too large")
        tag = recv_exact(self.sock, 32)
        data = recv_exact(self.sock, size)
        self.received += 1
        if not hmac.compare_digest(tag, self.mac(self.in_dir, self.received, data)):
            raise ConnectionError("bad frame signature")
        return json.loads(data)

    def send(self, kind, **fields):
        """Fire and forget. False if the link is down."""
        try:
            self.write({"t": kind, **fields})
            return True
        except OSError:
            self.close()
            return False

    def request(self, kind, timeout=3.0, **fields):
        """Send and wait for the answer; raises ConnectionError if it does not come."""
        n = next(self.ids)
        slot = self.pending[n] = [threading.Event(), None]
        try:
            self.write({"t": kind, "id": n, **fields})
            if not slot[0].wait(timeout) or self.closed:
                raise ConnectionError(f"no answer to {kind}")
            return slot[1]
        except OSError as e:
            self.close()
            raise ConnectionError(str(e)) from e
        finally:
            self.pending.pop(n, None)

    def run(self):
        try:
            while True:
                msg = self.read()
                if "re" in msg:
                    slot = self.pending.get(msg["re"])
                    if slot:
                        slot[1] = msg.get("r")
                        slot[0].set()
                    continue
                result = self.handler(self, msg)
                if "id" in msg:
                    self.write({"re": msg["id"], "r": result})
        except (OSError, ValueError, TypeError, KeyError) as e:
            self.error = e
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        for slot in list(self.pending.values()):
            slot[0].set()
        if self.on_close:
            self.on_close(self)


def listen(bind, port, secret, handler, on_open, on_close, log):
    """Accept links on (bind, port) forever. on_open(link) runs after the handshake, before any message is read."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
    s.bind((bind, port))
    s.listen(16)
    log(f"internal link on {bind}:{port}")
    slots = threading.BoundedSemaphore(MAX_LINKS)

    def accept(c, addr):
        link = Link(c, secret, handler, on_close)
        try:
            link.handshake(connecting=False)
        except (OSError, ConnectionError) as e:
            log(f"[link] {addr} refused: {e}")
            c.close()
            return
        finally:
            slots.release()
        on_open(link)
        link.run()

    while True:
        c, addr = s.accept()
        if not slots.acquire(blocking=False):
            c.close()
            continue
        threading.Thread(target=accept, args=(c, addr), daemon=True).start()


def connect_forever(host, port, secret, handler, on_open, on_close, log):
    """Keep one link to (host, port) up: reconnects after every loss."""
    told = False
    while True:
        try:
            c = socket.create_connection((host, port), timeout=HANDSHAKE_TIMEOUT)
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            link = Link(c, secret, handler, on_close)
            link.handshake(connecting=True)
        except (OSError, ConnectionError) as e:
            if not told:
                log(f"[link] cannot reach {host}:{port} yet ({e}); retrying")
                told = True
            time.sleep(RETRY)
            continue
        told = False
        log(f"[link] connected to {host}:{port}")
        on_open(link)
        link.run()
        log(f"[link] lost {host}:{port}: {getattr(link, 'error', 'closed')}")
        time.sleep(RETRY)
