"""test_match.py -- offline two-player match: tickets, join, movement relay, PvP kill, leaving.

Fake clients talk to a real fdp.Server through a fake socket; timers run inline. Nothing is saved.
Usage: python -I tools/test_match.py"""
import copy
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fdp  # noqa: E402
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.STORE.save = lambda: None
ls.STORE.data = copy.deepcopy(ls.STORE.data)


class NoThread:
    def __init__(self, *a, **k): pass
    def start(self): pass


class InlineTimer:  # run a respawn right away; fire ticks only once (no repeat)
    def __init__(self, delay, fn, args=()):
        self.fn, self.args = fn, args
    def start(self):
        if self.fn is not ls.fire_tick:
            self.fn(*self.args)


ls.threading.Thread = NoThread
ls.threading.Timer = InlineTimer
sent = []  # (addr, catalog, type, body)


class Sock:
    def sendto(self, data, addr):
        pkt = fdp.Packet.parse(data)
        for raw, cat, typ in fdp.messages(pkt.payload):
            m = ls.Message.parse(raw)
            sent.append((addr, cat, typ, m.body[8:]))  # strip the event envelope


server = fdp.Server(Sock(), lambda *a: None, ls.hexdump)
server.on_message = ls.on_game_message
a_cid = ls.STORE.create("acctA", "Alpha", gender=1, model=1, state=1, raw="")
b_cid = ls.STORE.create("acctB", "Bravo", gender=1, model=1, state=1, raw="")


def connect(addr, cid, ticket=None):
    t = ticket if ticket is not None else ls.issue_ticket(cid, 1)
    stream = struct.pack("<III", t, 9, t)
    server.handle(fdp.Packet(0x07, 1, 9, 12, fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack(), addr)
    return server.peers[addr]


def msg(addr, typ, body):
    raw = bytes([0x80, 9]) + struct.pack("<H", typ) + body
    payload = struct.pack("<H", len(raw)) + raw
    peer = server.peers[addr]
    server.handle(fdp.Packet(0x01, 1, peer.server_session, 12 + len(payload), 0, 0, 0, payload).pack(), addr)


def got(addr, typ):
    return [b for a, c, t, b in sent if a == addr and t == typ]


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    return ok


A, B, X = ("1.1.1.1", 1), ("2.2.2.2", 2), ("3.3.3.3", 3)
ok = True
pa = connect(A, a_cid)
msg(A, 40851, b"\0" * 4)
ok &= check("A gets battle init", len(got(A, 40050)) == 1)
px = connect(X, a_cid, ticket=12345)
msg(X, 40851, b"\0" * 4)
ok &= check("no ticket, no battle", len(got(X, 40050)) == 0)
msg(A, 30050, b"\0" * 4)
ok &= check("A spawned as Alpha", pa.name == "Alpha" and pa.player_rid == ls.PLAYER_RID_STEP + 1)
pb = connect(B, b_cid)
msg(B, 40851, b"\0" * 4)
msg(B, 30050, b"\0" * 4)
m = pa.match
ok &= check("same match, 2 players", m is pb.match and len(m.peers) == 2)
a_rid, b_rid = pa.player_rid, pb.player_rid
ok &= check("A was told about B's robot", any(struct.unpack_from("<III", x)[2] == b_rid for x in got(A, 50001)))
ok &= check("B was told about A's robot", any(struct.unpack_from("<III", x)[2] == a_rid for x in got(B, 50001)))
ok &= check("character ids distinct from dummies", all(t["cid"] >= ls.DUMMY_CID_BASE for t in m.targets.values()))
before = len(got(B, 50100))
msg(A, 40130, struct.pack("<I3f", 0, 1.0, 2.0, 3.0))
moves = got(B, 50100)[before:]
ok &= check("A's move relayed to B", any(struct.unpack_from("<I", x)[0] == a_rid and struct.unpack_from("<3f", x, 8) == (1.0, 2.0, 3.0) for x in moves))
ok &= check("A's move not echoed to A", not any(struct.unpack_from("<I", x)[0] == a_rid for x in got(A, 50100)))
# PvP: A shoots straight at B until B dies
pb.protected_until = pa.protected_until = 0
pb.pos = (10.0, 0.0, 0.0)
for target in m.targets.values():
    target["pos"] = target["home"] = (500.0, 0.0, 500.0)  # dummies out of the way
shots = 0
while not pb.player_dead and shots < 200:
    msg(A, 40122, struct.pack("<III3f3fII", 0, 0, 0, 0.0, 2.5, 0.0, 10.0, 2.5, 0.0, 0, 0))
    shots += 1
ok &= check(f"B destroyed by A ({shots} bursts)", pb.player_dead is False and pb.deaths == 1)  # respawned inline
ok &= check("A credited a kill", pa.kills == 1)
feed = [x for x in got(B, 50151)]
ok &= check("kill feed reached B", any(b"Alpha" in x and b"Bravo" in x for x in feed))
ok &= check("shots shown to B, not A", len(got(B, 50122)) > 0 and not any(struct.unpack_from("<I", x)[0] == a_rid for x in got(A, 50122)))
ok &= check("stats saved per character", ls.STORE.stats(a_cid).get("kills") == 1 and ls.STORE.stats(b_cid).get("deaths") == 1)
# B goes quiet -> leaves
pb.last_seen = 0
now_peers = list(m.peers)
for p in [p for p in m.peers if p.last_seen < 1]:
    ls.leave_match(p)
ok &= check("B left, A remains", m.peers == [pa])
ls.leave_match(pa)
ok &= check("empty match closed", 1 not in ls.MATCHES)
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
