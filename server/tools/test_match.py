"""test_match.py -- offline match tests: tickets, join, movement relay, PvP kill, leaving, close packets,
rejoining, match size cap, batching, far culling and resends.

Fake clients talk to a real fdp.Server through a fake socket; the game loop is stepped by hand (game_tick), with
a fake clock where timers must fire. Nothing is saved.
Usage: python -I tools/test_match.py"""
import copy
import os
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("MW_STATE_DIR", tempfile.mkdtemp(prefix="mw-test-"))  # never the real save files
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
# robots farther away get fewer updates, and ones standing still almost none


def moves_of_a(n, x=None):
    """A reports n positions, one per tick (moving unless x is given); A's moves B got meanwhile."""
    before = len(got(B, 50100))
    for i in range(n):
        msg(A, 40130, struct.pack("<I3f", 0, 10.0 + i if x is None else x, 2.0, 3.0))
    return [x for x in got(B, 50100)[before:] if struct.unpack_from("<I", x)[0] == a_rid]


near = max(ls.NEAR_DISTANCE, pa.fire_range, pb.fire_range)
for away, every in ((near + ls.MID_BAND + 100, ls.FAR_EVERY), (near + ls.MID_BAND / 2, ls.MID_EVERY)):
    pb.pos = (10.0 + away, 2.0, 3.0)
    thinned = moves_of_a(every * 3)  # 3 at most; 2 when the window starts just after a send
    ok &= check(f"robot {away:.0f} away: {len(thinned)} of {every * 3} moves sent", len(thinned) in (2, 3))
pb.pos = (10.0 + near - 1, 2.0, 3.0)
ok &= check(f"a robot in weapon range ({near:.0f}): every move", len(moves_of_a(10)) == 10)
range_b, pb.fire_range = pb.fire_range, ls.fire_range(3105)
pb.pos = (1010.0, 2.0, 3.0)
ok &= check("a ThunderStrike 1000 away: every move", pb.fire_range >= 1000 and len(moves_of_a(10)) == 10)
pb.fire_range, pa.fire_range = range_b, ls.fire_range(3105)
seen = moves_of_a(10)
ok &= check(f"seen by someone 1000 away from a ThunderStrike: {len(seen)} of 10", len(seen) == 10)
pa.fire_range = range_b
pb.pos = (10.0, 0.0, 0.0)
moves_of_a(1, x=50.0)
still = moves_of_a(ls.STILL_EVERY * 2, x=50.0)
heights = [struct.unpack_from("<f", x, 12)[0] for x in still]
ok &= check(f"a robot standing still: sent again once a second ({heights})", len(still) == 4)
ok &= check("each time first raised, so the client puts it in place at once",
            heights == [2.0 + ls.SNAP_RISE, 2.0] * 2)
moving = [struct.unpack_from("<f", x, 12)[0] for x in moves_of_a(5)]
ok &= check("a walking robot is never raised", moving == [2.0] * 5)

# PvP: A shoots straight at B until B dies
pb.protected_until = pa.protected_until = 0
for target in m.targets.values():
    target["pos"] = target["home"] = (500.0, 0.0, 500.0)  # dummies out of the way
pa.pos = (0.0, 0.0, 0.0)
shots = 0
while not pb.player_dead and shots < 200:
    msg(A, 40122, struct.pack("<III3f3fII", 0, 0, 0, 0.0, 2.5, 0.0, 10.0, 2.5, 0.0, 0, 0))
    step(pa.fire_interval)
    shots += 1
ok &= check(f"B destroyed by A ({shots} bursts)", pb.player_dead and pb.deaths == 1)
step(ls.RESPAWN_DELAY + 0.1)
ok &= check("B respawned after the delay", not pb.player_dead)
ok &= check("A credited a kill", pa.kills == 1)
feed = [x for x in got(B, 50151)]
ok &= check("kill feed reached B", any(b"Alpha" in x and b"Bravo" in x for x in feed))
ok &= check("shots shown to B, not A", len(got(B, 50122)) > 0 and not any(struct.unpack_from("<I", x)[0] == a_rid for x in got(A, 50122)))
ok &= check("stats saved per character", ls.STORE.stats(a_cid).get("kills") == 1 and ls.STORE.stats(b_cid).get("deaths") == 1)
ok &= check("and kills per weapon, today too", ls.STORE.stats(a_cid).get(f"weapon{ls.DEFAULT_WEAPON}") == 1
            and ls.STORE.stats(a_cid).get(f"weapon{ls.DEFAULT_WEAPON}_day") == 1)

# cheating shots
def pull(origin=(0.0, 2.5, 0.0), aim=(10.0, 2.5, 0.0)):
    msg(A, 40122, struct.pack("<III3f3fII", 0, 0, 0, *origin, *aim, 0, 0))


def shoot(**kw):
    msg(A, 40125, b"\0" * 4)  # let go of the trigger and wait: nothing left over from the last shot
    step(pa.fire_interval)
    pull(**kw)


pa.pos, pb.pos = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)  # the respawn moved B
pb.protected_until = 0
step(pa.fire_interval)
hp = pb.player_hp
shoot()
ok &= check("a fair shot hits", pb.player_hp < hp)
hp = pb.player_hp
for _ in range(50):
    pull()
ok &= check("spamming the trigger does not beat the fire rate", pb.player_hp == hp)
msg(A, 40125, b"\0" * 4)
step(pa.fire_interval)
ok &= check("the spammed shots land at most once", pb.player_hp >= hp - ls.WEAPON_DAMAGE)
pa.pos = (500.0, 0.0, 500.0)
hp = pb.player_hp
shoot(origin=(9.0, 2.5, 0.0))
ok &= check("a shot not fired from the robot does nothing", pb.player_hp == hp)
far = pa.fire_range + 50
pa.pos, pb.pos = (0.0, 0.0, 0.0), (far, 0.0, 0.0)
shoot(aim=(far, 2.5, 0.0))
ok &= check("a shot past the weapon's range does nothing", pb.player_hp == hp)
pb.pos = (pa.fire_range - 20, 0.0, 0.0)
shoot(aim=(pb.pos[0], 2.5, 0.0))
ok &= check("a shot inside the range hits", pb.player_hp < hp)
hp = pb.player_hp
pb.pos = (far, 0.0, 0.0)
step(pa.fire_interval)
step(pa.fire_interval)
ok &= check("a held trigger stops at the range limit", pb.player_hp == hp)
msg(A, 40125, b"\0" * 4)
pb.pos = (10.0, 0.0, 0.0)
step(pa.fire_interval)
hp = pb.player_hp
shoot(aim=(3.0, 2.5, 0.0))
ok &= check("a shot that landed on a wall in front of the robot does nothing", pb.player_hp == hp)
shoot(aim=(14.0, 2.5, 0.0))
ok &= check("a shot that landed on the ground behind it hits", pb.player_hp < hp)
sky = pa.fire_range / ls.RANGE_MARGIN
pb.pos = (sky + 20, 0.0, 0.0)
hp = pb.player_hp
shoot(aim=(sky, 2.5, 0.0))
ok &= check("a shot at open sky reaches the whole range", sky + 20 < pa.fire_range and pb.player_hp < hp)
msg(B, 40130, struct.pack("<I3f", 0, 10.0, 0.0, 0.0))
step(0.1)
msg(B, 40130, struct.pack("<I3f", 0, 10.0, 0.0, 30.0))  # B walked away; A's client still draws it where it was
step(0.5)
hp = pb.player_hp
shoot(aim=(14.0, 2.5, 0.0))
ok &= check("a shot at where a robot was half a second ago hits", pb.player_hp < hp)
step(ls.TRAIL_SECONDS + 0.2)
hp = pb.player_hp
shoot(aim=(14.0, 2.5, 0.0))
ok &= check("but not after the trail runs out", pb.player_hp == hp)
msg(B, 40130, struct.pack("<I3f", 0, 10.0, 0.0, 0.0))
step(0.1)
ls.STORE.by_id(a_cid)["model"] = ls.STORE.by_id(b_cid)["model"] = ls.ALLIANCE  # the nation chosen at creation
shoot()
ok &= check("no damage to a player on your own side", pb.player_hp == hp)
ls.STORE.by_id(b_cid)["model"] = ls.FEDERAL
shoot()
ok &= check("but the other side's players are hit", pb.player_hp < hp)
ls.STORE.by_id(a_cid)["model"] = ls.STORE.by_id(b_cid)["model"] = 1
ls.WEAPONS[1] = (100.0, 0.5, 2.0)
ok &= check("a melee weapon can reach a robot", ls.fire_range(1) > ls.HIT_RADIUS + ls.MUZZLE_REACH)
del ls.WEAPONS[1]
msg(A, 40125, b"\0" * 4)
pb.pos = (10.0, 0.0, 0.0)

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


# --- rounds, on a battlefield that has them ---
class FakeTime:
    """Timers use time.time(): here they follow the fake clock, so a round can be timed exactly."""
    def __getattr__(self, name):
        return (lambda: clock[0]) if name == "time" else getattr(time, name)


ls.time = FakeTime()

def kill(by, victim):
    victim.protected_until = 0.0
    ls.hit_player(server, by.match, victim, ls.PLAYER_HP, by.name, by)


def join_front(addr, cid):
    return join(addr, cid, ticket=ls.issue_ticket(cid, 2))


wins = ls.STORE.stats(a_cid).get("wins", 0)
front1 = ls.STORE.stats(a_cid).get("front1", 0)
ls.ROUND_KILLS = 2
pa, pb = join_front(A, a_cid), join_front(B, b_cid)
m = pa.match
kill(pa, pb)
step(ls.RESPAWN_DELAY + 1)
ok &= check("one kill does not end the round", not m.over and not got(A, 30150))
pa.next_shot = 0.0
for rid in list(m.targets):
    ls.hit_dummy(server, m, pa, rid)
ok &= check("target robots do not count", pa.player_kills == 1 and not m.over)
kill(pa, pb)
step()
ok &= check("the kill limit ends the round", m.over and len(got(A, 30150)) == 1 and len(got(B, 30150)) == 1)
ok &= check("kills on a battlefront count as contribution",
            ls.STORE.stats(a_cid).get("front1", 0) == 2 * ls.KILL_HONOR + front1)
ok &= check("the winner gets a win", ls.STORE.stats(a_cid).get("wins") == wins + 1
            and not ls.STORE.stats(b_cid).get("wins"))
ls.notice_game("maintenance")
step()
ok &= check("a notice shows in battle", any(b"maintenance" in x for x in got(A, 30250)))
ok &= check("everyone is told who won", any(b"Alpha" in x for x in got(B, 30250)))
step(ls.RESPAWN_DELAY + 1)
ok &= check("nobody respawns after the end", pb.player_dead)
pc = join_front(C, c_cid)
ok &= check("a new player gets a new round", pc.match is not m)
step(ls.RECORD_SECONDS + ls.RECORD_GRACE + 1)
ok &= check("players who stay are closed after the Record window", A not in server.peers and B not in server.peers)
ok &= check("the ended match is gone", m.id not in ls.MATCHES)

pa = join_front(A, a_cid)
m2 = pa.match
ok &= check("a player alone keeps playing", m2 is pc.match)
server.close(server.peers[C], "test over")
step(ls.ROUND_SECONDS + 1)
ok &= check("the timer does not end a round for one player", not m2.over and A in server.peers)
pb = join_front(B, b_cid)
step(ls.ROUND_SECONDS - ls.ROUND_WARNING + 1)
ok &= check("players are warned", any(b"one minute" in x for x in got(B, 30250)))
step(ls.ROUND_WARNING)
ok &= check("the timer ends the round", m2.over and len(got(B, 30150)) == 2)
for addr in list(server.peers):
    server.close(server.peers[addr], "test over")
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
