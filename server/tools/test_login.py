"""test_login.py -- offline checks for login: register, password check, throttle, pre-login guard.

Uses a temporary accounts file; nothing real is changed. Usage: python -I tools/test_login.py"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.STORE.save = lambda: None
tmp = tempfile.mkdtemp()
ls.Accounts.path = os.path.join(tmp, "accounts.json")
ls.ACCOUNTS = ls.Accounts()


class FakeSession(ls.Session):
    def __init__(self, ip="1.1.1.1"):
        super().__init__(None, (ip, 1))
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)


def login_body(account, password):
    a, p = account.encode("latin-1"), password.encode("latin-1")
    return (struct.pack("<I", 1) + bytes([len(a)]) + a + bytes([len(p)]) + p + struct.pack("<II", 10102, 1)
            + bytes([4]) + b"hash")


def login(account, password, ip="1.1.1.1"):
    s = FakeSession(ip)
    s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=login_body(account, password)))
    return s.sent[-1].result, s.account


def check(name, got, want):
    print(("ok  " if got == want else "FAIL") + f" {name}: {got!r}" + ("" if got == want else f" (want {want!r})"))
    return got == want


ok = True
ok &= check("new account registers", login("alice", "pw1"), (0, "alice"))
ok &= check("same password logs in", login("alice", "pw1"), (0, "alice"))
ok &= check("wrong password refused", login("alice", "nope", ip="2.2.2.2"), (ls.LOGIN_FAILED, None))
ok &= check("bad account name refused", login("a b'; --", "x", ip="3.3.3.3"), (ls.LOGIN_FAILED, None))
for _ in range(ls.LOGIN_FAILS_MAX):
    login("alice", "guess", ip="4.4.4.4")
ok &= check("throttled after failures (even right password)", login("alice", "pw1", ip="4.4.4.4"), (ls.LOGIN_LOCKED, None))
ok &= check("other IPs unaffected", login("alice", "pw1", ip="5.5.5.5"), (0, "alice"))
ok &= check("password not stored in clear", "pw1" in open(ls.Accounts.path).read(), False)
s = FakeSession()
s.handle(ls.Message(13, 13000, token=1, flags=ls.MSG_REQUEST, body=b""))
ok &= check("requests before login ignored", len(s.sent), 0)
s = FakeSession()
s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=b"\x01\x00"))
ok &= check("malformed login refused", s.sent[-1].result, ls.LOGIN_FAILED)
ls.AUTO_REGISTER = False
ok &= check("no auto-register when off", login("bob", "pw", ip="6.6.6.6"), (ls.LOGIN_FAILED, None))
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
