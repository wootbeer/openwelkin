"""fuzz_game.py -- feed random datagrams to the FDP layer and random messages to the game dispatcher, offline.

A fake socket swallows everything sent; the AI thread is not started (threading.Thread is stubbed). Reports any
exception, since one escaping the UDP loop would stop the game server for every match.
Usage: python -I tools/fuzz_game.py [rounds]"""
import os
import random
import re
import struct
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the tools folder out
import tmpdir  # noqa: E402  (removes the temporary folders at exit)
os.environ.setdefault("MW_STATE_DIR", tmpdir.make("mw-test-"))  # never the real save files
import fdp  # noqa: E402
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.STORE.save = lambda: None


class FakeThread:
    def __init__(self, *a, **k): pass
    def start(self): pass


ls.threading.Thread = FakeThread
ls.threading.Timer = FakeThread


class FakeSock:
    def sendto(self, data, addr): return len(data)


def raw_message(rng, catalog, type_, body):
    # plaintext Outpop message as the client sends it: flags | catalog | u16 type | body (no token)
    return bytes([0x80, catalog]) + struct.pack("<H", type_) + body


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rng = random.Random(2)
    server = fdp.Server(FakeSock(), lambda *a: None, ls.hexdump)
    server.on_message = ls.on_game_message
    failures = {}
    types = [30050, 40130, 40105, 40122, 40125, 40301, 40302, 40310, 40312, 40314, 40331, 40332, 40333, 40335, 40110, 40102, 40851, 9001, 10006, rng.randint(0, 65535)]

    def run(what, fn, *args):
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            key = (what, type(e).__name__, re.sub(r"\d+", "N", str(e))[:60])
            failures.setdefault(key, traceback.format_exc(limit=4))

    # 1) raw datagrams at the FDP layer, from many addresses
    for i in range(rounds):
        addr = ("10.0.0.%d" % (i % 50), 4000 + i % 7)
        data = rng.randbytes(rng.choice([0, 1, 5, 12, 13, 20, 40, 200, 1500]))
        run("fdp.handle", server.handle, data, addr)
    # 2) game messages straight into the dispatcher with a connected-looking peer
    peer = fdp.Peer(("127.0.0.1", 9), 1)
    for i in range(rounds):
        t = rng.choice(types)
        body = rng.randbytes(rng.choice([0, 3, 4, 8, 15, 16, 35, 36, 44, 100]))
        if rng.random() < 0.2:  # non-finite floats in position / aim fields
            body = struct.pack("<I", 0) + struct.pack("<3f", float("nan"), float("inf"), -1e38) * 4
        run(f"game ({9},{t})", ls.on_game_message, server, peer, raw_message(rng, 9, t, body))
    run("game unparsable", ls.on_game_message, server, peer, b"\x01")
    print(f"{2 * rounds} inputs, {len(server.peers)} peers created, {len(failures)} distinct failures")
    for (what, name, text), tb in sorted(failures.items()):
        print(f"\n{what} {name}: {text}\n  " + "\n  ".join(tb.strip().splitlines()[-4:]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
