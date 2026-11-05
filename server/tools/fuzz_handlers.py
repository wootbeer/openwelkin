"""fuzz_handlers.py -- call every gate/login handler with random and edge-case request bodies, offline.

Runs against a throwaway copy of the character store (nothing is saved, nothing is logged), and reports any
handler that raises. A raised exception drops the client's connection, so every handler should survive any body.
Usage: python -I tools/fuzz_handlers.py [rounds]"""
import copy
import re
import os
import random
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the tools folder out
import tmpdir  # noqa: E402  (removes the temporary folders at exit)
os.environ.setdefault("MW_STATE_DIR", tmpdir.make("mw-test-"))  # never the real save files
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.STORE.save = lambda: None
ls.STORE.data = copy.deepcopy(ls.STORE.data)
ls.Accounts.path = os.path.join(tmpdir.make(), "accounts.db")
ls.ACCOUNTS = ls.Accounts()
ls.login_throttled = lambda ip, failed=False: False

# a test account with one character, so "own character" paths run too
acct = "fuzzacct"
cid = ls.STORE.create(acct, "Fuzzer", gender=1, model=1, state=1, raw="")


class FakeSession(ls.Session):
    def __init__(self, account):
        super().__init__(None, ("fuzz", 0))
        self.account = account
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)


def bodies(rng):
    yield b""
    for n in (1, 2, 3, 4, 5, 8, 9, 10, 12, 13, 16, 64):
        yield bytes(n)
        yield b"\xff" * n
        yield rng.randbytes(n)
    for v in (cid, 0, 1, 0x7FFFFFFF, 0xFFFFFFFF):
        yield v.to_bytes(4, "little") + rng.randbytes(rng.randint(0, 12))
    yield cid.to_bytes(4, "little") + b"\x00\x01" + (1).to_bytes(4, "little")       # buy goods 1
    yield cid.to_bytes(4, "little") + (5).to_bytes(4, "little")                     # learn talent / activate
    yield b"\x80" * 40                                                              # endless varint
    yield rng.randbytes(5000)


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rng = random.Random(1)
    failures = {}
    calls = 0
    skip = set()
    for (cat, typ), handler in sorted(ls.HANDLERS.items()):
        if (cat, typ) in skip:
            continue
        for _ in range(rounds):
            for account in (acct, None, "nobody"):
                for body in bodies(rng):
                    s = FakeSession(account)
                    msg = ls.Message(cat, typ, token=1, flags=ls.MSG_REQUEST, body=body)
                    calls += 1
                    try:
                        handler(s, msg)
                    except Exception as e:  # noqa: BLE001 -- report everything
                        key = (cat, typ, type(e).__name__, re.sub(r"\d+", "N", str(e))[:60])
                        if key not in failures:
                            failures[key] = (body[:24].hex(), account, traceback.format_exc(limit=3))
    print(f"{calls} calls, {len(failures)} distinct failures")
    for (cat, typ, name, text), (body, account, tb) in sorted(failures.items()):
        print(f"\n({cat},{typ}) {handler_name(cat, typ)} {name}: {text}\n  body={body} account={account}")
        print("  " + tb.strip().splitlines()[-2].strip())
    return 1 if failures else 0


def handler_name(cat, typ):
    h = ls.HANDLERS.get((cat, typ))
    return h.__name__ if h else "?"


if __name__ == "__main__":
    sys.exit(main())
