"""test_match.py -- offline match tests: tickets, join, movement relay, PvP kill, leaving, close packets,
rejoining, match size cap, batching, far culling and resends.

Fake clients talk to a real fdp.Server through a fake socket; the game loop is stepped by hand (game_tick), with
a fake clock where timers must fire. Nothing is saved.
Usage: python -I tools/test_match.py"""
import copy
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fdp  # noqa: E402
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.STORE.save = lambda: None
ls.STORE.data = copy.deepcopy(ls.STORE.data)
sent = []     # (addr, catalog, type, body)
packets = []  # (addr, number of messages)


class Sock:
    def sendto(self, data, addr):
        pkt = fdp.Packet.parse(data)
        msgs = fdp.messages(pkt.payload)
        packets.append((addr, len(msgs), pkt.flags))
        for raw, cat, typ in msgs:
            m = ls.Message.parse(raw)
            sent.append((addr, cat, typ, m.body[8:]))  # strip the event envelope


server = fdp.Server(Sock(), lambda *a: None, ls.hexdump)
server.on_message = ls.on_game_message
server.on_close = ls.on_game_close
a_cid = ls.STORE.create("acctA", "Alpha", gender=1, model=1, state=1, raw="")
b_cid = ls.STORE.create("acctB", "Bravo", gender=1, model=1, state=1, raw="")
c_cid = ls.STORE.create("acctC", "Charlie", gender=1, model=1, state=1, raw="")
clock = [time.time()]


def step(dt=0.0):
    """Advance the fake clock and run one game loop step."""
    clock[0] += dt
    for p in server.peers.values():
        p.last_seen = clock[0]  # everyone keeps talking
    ls.game_tick(server, clock[0])


def connect(addr, cid, ticket=None, session=9):
    t = ticket if ticket is not None else ls.issue_ticket(cid, 1)
    stream = struct.pack("<III", t, 9, t)
    server.handle(fdp.Packet(0x07, 1, session, 12, fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack(),
                  addr)
    return server.peers[addr]


def msg(addr, typ, body, flush=True):
    raw = bytes([0x80, 9]) + struct.pack("<H", typ) + body
    payload = struct.pack("<H", len(raw)) + raw
    peer = server.peers[addr]
    server.handle(fdp.Packet(0x01, 1, peer.server_session, 12 + len(payload), 0, 0, 0, payload).pack(), addr)
    if flush:
        step()


def join(addr, cid, **kw):
    p = connect(addr, cid, **kw)
    msg(addr, 40851, b"\0" * 4)
    msg(addr, 30050, b"\0" * 4)
    return p


def system(addr, split, ack=0):
    peer = server.peers[addr]
    server.handle(fdp.Packet(0x05 if split == 1 else 0x07, 1, peer.server_session, 12, split, ack, 2).pack(), addr)


def got(addr, typ):
    return [b for a, c, t, b in sent if a == addr and t == typ]


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    return ok


A, B, C, X = ("1.1.1.1", 1), ("2.2.2.2", 2), ("3.3.3.3", 3), ("4.4.4.4", 4)
ok = True
pa = connect(A, a_cid)
msg(A, 40851, b"\0" * 4)
ok &= check("A gets battle init", len(got(A, 40050)) == 1)
px = connect(X, a_cid, ticket=12345)
msg(X, 40851, b"\0" * 4)
ok &= check("no ticket, no battle", len(got(X, 40050)) == 0)
msg(A, 30050, b"\0" * 4)
ok &= check("A spawned as Alpha", pa.name == "Alpha" and pa.player_rid == ls.PLAYER_RID_STEP + 1)
before = len(packets)
pb = join(B, b_cid)
m = pa.match
ok &= check("same match, 2 players", m is pb.match and len(m.peers) == 2)
b_pkts = [n for a, n, f in packets[before:] if a == B and f & 0x80]
ok &= check(f"join events batched ({sum(b_pkts)} messages in {len(b_pkts)} packets)",
            len(b_pkts) < sum(b_pkts) / 2)
a_rid, b_rid = pa.player_rid, pb.player_rid
ok &= check("A was told about B's robot", any(struct.unpack_from("<III", x)[2] == b_rid for x in got(A, 50001)))
ok &= check("B was told about A's robot", any(struct.unpack_from("<III", x)[2] == a_rid for x in got(B, 50001)))
ok &= check("character ids distinct from dummies", all(t["cid"] >= ls.DUMMY_CID_BASE for t in m.targets.values()))

# movement: relayed, not echoed, only the newest per tick
before = len(got(B, 50100))
msg(A, 40130, struct.pack("<I3f", 0, 1.0, 2.0, 3.0))
moves = got(B, 50100)[before:]
ok &= check("A's move relayed to B", any(struct.unpack_from("<I", x)[0] == a_rid and struct.unpack_from("<3f", x, 8) == (1.0, 2.0, 3.0) for x in moves))
ok &= check("A's move not echoed to A", not any(struct.unpack_from("<I", x)[0] == a_rid for x in got(A, 50100)))
before = len(got(B, 50100))
for x in (4.0, 5.0, 6.0):
    msg(A, 40130, struct.pack("<I3f", 0, x, 2.0, 3.0), flush=False)
step()
moves = [x for x in got(B, 50100)[before:] if struct.unpack_from("<I", x)[0] == a_rid]
ok &= check("three moves in one tick: B gets only the newest", [struct.unpack_from("<f", x, 8)[0] for x in moves] == [6.0])
# far robots get fewer updates
pb.pos = (1000.0, 0.0, 1000.0)
before = len(got(B, 50100))
for i in range(ls.FAR_EVERY):
    msg(A, 40130, struct.pack("<I3f", 0, 10.0 + i, 2.0, 3.0))
far = [x for x in got(B, 50100)[before:] if struct.unpack_from("<I", x)[0] == a_rid]
ok &= check(f"far robot: {len(far)} of {ls.FAR_EVERY} moves sent", len(far) == 1)
pb.pos = (10.0, 0.0, 0.0)

# PvP: A shoots straight at B until B dies
pb.protected_until = pa.protected_until = 0
for target in m.targets.values():
    target["pos"] = target["home"] = (500.0, 0.0, 500.0)  # dummies out of the way
shots = 0
while not pb.player_dead and shots < 200:
    msg(A, 40122, struct.pack("<III3f3fII", 0, 0, 0, 0.0, 2.5, 0.0, 10.0, 2.5, 0.0, 0, 0))
    shots += 1
ok &= check(f"B destroyed by A ({shots} bursts)", pb.player_dead and pb.deaths == 1)
step(ls.RESPAWN_DELAY + 0.1)
ok &= check("B respawned after the delay", not pb.player_dead)
ok &= check("A credited a kill", pa.kills == 1)
feed = [x for x in got(B, 50151)]
ok &= check("kill feed reached B", any(b"Alpha" in x and b"Bravo" in x for x in feed))
ok &= check("shots shown to B, not A", len(got(B, 50122)) > 0 and not any(struct.unpack_from("<I", x)[0] == a_rid for x in got(A, 50122)))
ok &= check("stats saved per character", ls.STORE.stats(a_cid).get("kills") == 1 and ls.STORE.stats(b_cid).get("deaths") == 1)

# resend: the client repeats the ack of a packet it never got
server.peers[A].unacked.clear()
ls.send_game_event(server, pa, 19, 30204, struct.pack("<IiI", 0, 0, 0))
step()
lost = next(iter(server.peers[A].unacked))
resent = server.stats["resent"]
for _ in range(fdp.DUP_ACKS + 1):
    system(A, fdp.SPLIT_ACK, ack=lost)
ok &= check("lost packet resent after repeated acks", server.stats["resent"] == resent + 1)
system(A, fdp.SPLIT_ACK, ack=(lost + 1) & 0xFFFF)
ok &= check("ack clears it", lost not in server.peers[A].unacked)

# leaving: the close packet takes B out, and the others are told
system(B, fdp.SPLIT_FIN)
step()
ok &= check("B's close packet: B left, A remains", m.peers == [pa] and B not in server.peers)
ok &= check("A told to remove B's robot and character",
            struct.pack("<I", b_rid) in got(A, 50002) and struct.pack("<I", b_cid) in got(A, 30002))
# the same character joining again (client restarted) replaces its old connection
pb = join(B, b_cid)
pb2 = join(("2.2.2.2", 22), b_cid)
ok &= check("rejoin replaces the old connection", pb not in m.peers and pb2 in m.peers and B not in server.peers)
# quiet players time out
pb2.last_seen = 0
ls.game_tick(server, clock[0])
ok &= check("quiet player taken out", pb2 not in m.peers and m.peers == [pa])

# a full match opens a second match of the same battlefield
ls.MAX_MATCH_PLAYERS = 2
pb = join(B, b_cid)
pc = join(C, c_cid)
ok &= check("full match: third player gets a new match", pc.match is not m and pc.match.bfid == m.bfid
            and len(ls.MATCHES) == 2)
ls.MAX_MATCH_PLAYERS = 32

# account logout (gate closed) takes its characters out of the game
ls.GAME_SERVER = server
ls.leave_game("acctC", "logged out")
ok &= check("logout closes the game session", C not in server.peers and pc.match is None)
for addr in list(server.peers):
    server.close(server.peers[addr], "test over")
ok &= check("empty matches closed", not ls.MATCHES)
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
