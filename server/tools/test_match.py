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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the tools folder out
import tmpdir  # noqa: E402  (removes the temporary folders at exit)
os.environ.setdefault("MW_STATE_DIR", tmpdir.make("mw-test-"))  # never the real save files
import fdp  # noqa: E402
import loginserver as ls  # noqa: E402

ls.log = lambda *a, **k: None
ls.MOVE_CHECK = False  # on only for its own tests below: the others move robots around freely
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
            sent.append((addr, cat, typ, m.body[8:] if cat in (9, 19) else m.body))  # strip the event envelope


server = fdp.Server(Sock(), lambda *a: None, ls.hexdump)
server.on_message = ls.on_game_message
server.on_close = ls.on_game_close
server.allow = ls.admit_game_session
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
try:
    connect(X, a_cid, ticket=12345)
except KeyError:
    pass
ok &= check("no ticket, no session", X not in server.peers and not any(a == X for a, *_ in packets))
for port in range(ls.MAX_GAME_PER_IP + 2):
    try:
        connect(("9.9.9.9", 100 + port), a_cid)
    except KeyError:
        pass
ok &= check("sessions per address are capped", sum(a[0] == "9.9.9.9" for a in server.peers) == ls.MAX_GAME_PER_IP)
for a in [a for a in server.peers if a[0] == "9.9.9.9"]:
    server.close(server.peers[a], "test")
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
# aim bot check: far shots at a player are counted, and too many hits are flagged
step(ls.TRAIL_SECONDS + 0.2)  # B's old places near A are out of the trail
kept_hp = pb.player_hp
pa.aim_shots = pa.aim_hits = 0
pb.pos = (150.0, 0.0, 0.0)
for _ in range(5):
    shoot(aim=(150.0, 2.5, 60.0))  # 22 degrees off: not a shot at B
ok &= check("shots far from any player are not counted", pa.aim_shots == 0)
pb.pos = (50.0, 0.0, 0.0)
shoot(aim=(50.0, 2.5, 0.0))
ok &= check("shots at a near player are not counted", pa.aim_shots == 0)
pb.pos = (150.0, 0.0, 0.0)
for i in range(ls.AIM_SHOTS):
    pb.player_hp, pb.player_dead = ls.PLAYER_HP, False
    shoot(aim=(150.0, 2.5, 0.0) if i % 2 else (150.0, 2.5, 12.0))  # every other one misses by 12
ok &= check(f"far shots counted: {pa.aim_hits} of {pa.aim_shots} hit, not flagged",
            pa.aim_shots == ls.AIM_SHOTS and pa.aim_hits == ls.AIM_SHOTS // 2 and not pa.aim_warned)
pa.aim_shots = pa.aim_hits = 0
for _ in range(ls.AIM_SHOTS):
    pb.player_hp, pb.player_dead = ls.PLAYER_HP, False
    shoot(aim=(150.0, 2.5, 0.0))
rows = ls.admin_command("aim", "test")
ok &= check("every far shot hits: flagged, and the aim command shows it",
            pa.aim_warned and "Alpha: 30 of 30 far shots hit (100%)" in rows and "too good" in rows)
msg(A, 40125, b"\0" * 4)
pb.pos, pb.player_hp = (10.0, 0.0, 0.0), kept_hp
step(pa.fire_interval)
ls.STORE.by_id(a_cid)["model"] = ls.STORE.by_id(b_cid)["model"] = ls.ALLIANCE  # the nation chosen at creation
shoot()
ok &= check("no damage to a player on your own side", pb.player_hp == hp)
ls.STORE.by_id(b_cid)["model"] = ls.FEDERAL
shoot()
ok &= check("but the other side's players are hit", pb.player_hp < hp)

# terrain: a flat map at height 0 with a ridge 20 high across x = 20..30
hmd = os.path.join(tmpdir.make("mw-hmd-"), "ridge.hmd")
heights = [20.0 if c in (12, 13) else 0.0 for r in range(21) for c in range(21)]
with open(hmd, "wb") as f:
    f.write(struct.pack("<II", 21, 21) + struct.pack(f"<{len(heights)}f", *heights))
ridge = ls.mapdata.Terrain(hmd)
ok &= check("terrain: heights from the file, 10 apart, centered",
            ridge.height(0.0, 0.0) == 0.0 and ridge.height(20.0, 5.0) == 20.0 and ridge.height(500.0, 0.0) is None)
ok &= check("terrain: between two samples, the lower way of cutting the cell",
            ridge.height(15.0, 5.0) <= 10.0 and ridge.height(15.0, 0.0) == 10.0)
ok &= check("terrain: over the ridge is clear, through it is not",
            ridge.clear((0.0, 25.0, 0.0), (50.0, 25.0, 0.0)) and not ridge.clear((0.0, 2.5, 0.0), (50.0, 2.5, 0.0)))
ok &= check("terrain: a shot from under the ground (a tunnel) is not checked",
            ridge.clear((0.0, -10.0, 0.0), (50.0, 2.5, 0.0)))
saved_terrain, m.terrain = m.terrain, ridge
pb.pos = (60.0, 0.0, 0.0)
hp = pb.player_hp
shoot(aim=(60.0, 2.5, 0.0))
ok &= check("a shot through a hill does nothing", pb.player_hp == hp)
pa.pos = (0.0, 50.0, 0.0)
shoot(origin=(0.0, 52.5, 0.0), aim=(60.0, 2.5, 0.0))
ok &= check("the same robot shot from above the hill is hit", pb.player_hp < hp)
pa.pos = (0.0, 0.0, 0.0)
m.terrain = saved_terrain

# walls: one model, a 10 x 10 square in its own x-y plane, placed twice: at x = 20 turned to face along x, and
# at x = 500 as it is
md = ls.mapdata
quad = [(0.0, -5.0, -5.0), (0.0, 5.0, -5.0), (0.0, 5.0, 5.0), (0.0, -5.0, 5.0)]
flat = [(x, y, z) for z, y, x in quad]  # the same square lying in the x-y plane
turn = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0]  # swaps x and z
walls_file = os.path.join(tmpdir.make("mw-walls-"), "w.walls")
with open(walls_file, "wb") as f:
    f.write(md.pack_walls([md.build_tree(flat, [0, 1, 2, 0, 2, 3])],
                          [(0, turn + [20.0, 2.5, 0.0]), (0, [1, 0, 0, 0, 1, 0, 0, 0, 1, 500.0, 0.0, 0.0])]))
wall = md.Walls(walls_file)
ok &= check("walls: a shot through the wall is stopped, past its edge or over it is not",
            not wall.clear((0.0, 2.5, 0.0), (40.0, 2.5, 0.0)) and wall.clear((0.0, 2.5, 8.0), (40.0, 2.5, 8.0))
            and wall.clear((0.0, 9.0, 0.0), (40.0, 9.0, 0.0)))
ok &= check("walls: a robot right at the wall still hits (the ends are not checked)",
            wall.clear((0.0, 2.5, 0.0), (21.0, 2.5, 0.0)))
ok &= check("walls: the placement moves and turns the model",
            not wall.clear((500.0, 0.0, -10.0), (500.0, 0.0, 10.0)) and wall.clear((0.0, 2.5, -10.0), (0.0, 2.5, 10.0)))
saved_walls, m.walls = m.walls, wall
pb.pos = (40.0, 0.0, 0.0)
hp = pb.player_hp
shoot(aim=(40.0, 2.5, 0.0))
ok &= check("a shot through a wall does nothing", pb.player_hp == hp)
pb.pos = (40.0, 0.0, 24.0)
shoot(aim=(40.0, 2.5, 24.0))
ok &= check("the same robot next to the wall is hit", pb.player_hp < hp)
m.walls = saved_walls
pb.pos = (10.0, 0.0, 0.0)
real, solid = ls.terrain("Atrain01.reb"), ls.map_sight("Atrain01.reb", "walls")
if real and solid:  # only after setup_data.py
    bf = ls.BATTLEFIELDS[1]
    sp = bf["spawn"]
    ok &= check("Training Ground: spawn stands on the ground, the dummies are in sight",
                abs(real.height(sp[0], sp[2]) + 1 - sp[1]) < 0.5
                and all(real.clear((sp[0], sp[1] + 4, sp[2]), (x, y + 2.5, z))
                        and solid.clear((sp[0], sp[1] + 4, sp[2]), (x, y + 2.5, z)) for x, y, z in bf["dummies"]))

ls.STORE.by_id(a_cid)["model"] = ls.STORE.by_id(b_cid)["model"] = 1
ls.WEAPONS[1] = (100.0, 0.5, 2.0)
ok &= check("a melee weapon can reach a robot", ls.fire_range(1) > ls.HIT_RADIUS + ls.MUZZLE_REACH)
del ls.WEAPONS[1]
rh, o = ls.ray_hits_robot, (0.0, 2.5, 0.0)
ok &= check("hit shape: shoulder, head and feet hit", rh(o, (40.0, 2.5, 2.2), (40.0, 0.0, 0.0))
            and rh(o, (40.0, 7.0, 0.0), (40.0, 0.0, 0.0)) and rh(o, (40.0, -2.0, 0.0), (40.0, 0.0, 0.0)))
ok &= check("hit shape: 4 to the side or over the head misses", not rh(o, (40.0, 2.5, 4.0), (40.0, 0.0, 0.0))
            and not rh(o, (40.0, 8.5, 0.0), (40.0, 0.0, 0.0)))
ok &= check("hit shape: a shot down into the ground beside it misses", not rh(o, (40.0, 0.0, 5.0), (40.0, 0.0, 0.0)))
ok &= check("hit shape: straight down from above hits, behind the muzzle misses",
            rh((40.0, 30.0, 0.0), (40.0, 0.0, 1.0), (40.0, 0.0, 0.0)) and not rh(o, (40.0, 2.5, 0.0), (-10.0, 0.0, 0.0)))
ok &= check("hit shape: past the reach misses", not rh(o, (40.0, 2.5, 0.0), (40.0, 0.0, 0.0), reach=30.0))
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

# speed check: the clock is the real one, so waiting is done by moving the check's own times back
def wait(peer, dt):
    peer.move_at -= dt
    peer.placed_at -= dt


def a_moves_to(pos, typ=40130):
    msg(A, typ, struct.pack("<I3f", 0, *pos) if typ == 40130 else struct.pack("<fI3f3f", 0.0, 1, *pos, 1.0, 0.0, 0.0))
    return pa.pos == pos


ls.MOVE_CHECK = True
pa.pos = (0.0, 0.0, 0.0)
ls.placed(pa)
wait(pa, 5)
sent.clear()
ok &= check("speed check: walking passes", a_moves_to((10.0, 0.0, 0.0)) and not got(A, 33000))
wait(pa, 1)
ok &= check("speed check: a second later another 16 passes", a_moves_to((10.0, 0.0, 16.0)))
before = len(got(B, 50100))
ok &= check("speed check: a jump of 200 is refused", not a_moves_to((210.0, 0.0, 16.0)) and pa.pos == (10.0, 0.0, 16.0))
ok &= check("speed check: the player is put back", got(A, 33000) == [struct.pack("<3f", 10.0, 0.0, 16.0)])
ok &= check("speed check: the others never see the jump",
            not any(struct.unpack_from("<f", x, 8)[0] == 210.0 for x in got(B, 50100)[before:]))
ok &= check("speed check: GC_Robot_Action is checked too", not a_moves_to((10.0, 0.0, 300.0), typ=40100))
ok &= check("speed check: put back at most every half second", len(got(A, 33000)) == 1)
ok &= check("speed check: falling is free", a_moves_to((10.0, -100.0, 16.0)))
wait(pa, 3)
ok &= check("speed check: reports bunched up after lag pass",
            all(a_moves_to((10.0, -100.0, 16.0 + 12 * i)) for i in (1, 2, 3)))
sent.clear()
ls.placed(pa)
ok &= check("speed check: right after a respawn or goto, an old report is dropped quietly",
            not a_moves_to((500.0, 0.0, 500.0)) and not got(A, 33000))
ls.MOVE_CHECK = False

# account logout (gate closed) takes its characters out of the game
ls.GAME_SERVER = server
sent.clear()
moved = ls.teleport("acctA", (5.0, 6.0, 7.0))
step(0.1)
ok &= check("goto moves a player in battle", moved and pa.pos == (5.0, 6.0, 7.0)
            and got(A, 33000) == [struct.pack("<3f", 5.0, 6.0, 7.0)])
ok &= check("the others see it move", any(struct.unpack_from("<I3f", b, 0)[0] == pa.player_rid
                                          and struct.unpack_from("<3f", b, 8) == (5.0, 6.0, 7.0) for b in got(B, 50100)))
ok &= check("goto someone not in battle", not ls.teleport("nobody", (0.0, 0.0, 0.0)))

# the client's own GM /moveto <character>: (9,11000) u32 11 | str name
sent.clear()
moveto = struct.pack("<I", 11) + ls.wstr("Bravo")
msg(A, 11000, moveto)
ok &= check("/moveto: refused without a GM level", pa.gm_level == 0 and not got(A, 33000))
pa.gm_level = ls.GM_LEVEL["gm"]  # what a GM's battle ticket gives
msg(A, 11000, moveto)
there = (pb.pos[0] + ls.SLOT_OFFSET, pb.pos[1], pb.pos[2])
ok &= check("/moveto: a GM goes next to the player", got(A, 33000) == [struct.pack("<3f", *there)] and pa.pos == there)
sent.clear()
msg(A, 11000, struct.pack("<I", 11) + ls.wstr("Nobody"))
msg(A, 11000, struct.pack("<II", 7, 1))
msg(A, 11000, struct.pack("<I", 11) + b"\xff\xff\xff\xff\xff")
ok &= check("/moveto: nobody by that name, other commands and bad names move nothing", not got(A, 33000))
t = ls.issue_ticket(a_cid, 1, 101)
ok &= check("a battle ticket carries the GM level", ls.TICKETS[t][3] == 101)
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
step()

# Kill mode: the two nations against each other
ls.KILL_MODE, ls.ROUND_KILLS = True, 2
ls.STORE.by_id(a_cid)["model"], ls.STORE.by_id(b_cid)["model"], ls.STORE.by_id(c_cid)["model"] = (
    ls.FEDERAL, ls.ALLIANCE, ls.ALLIANCE)
wins_a, wins_c = ls.STORE.stats(a_cid).get("wins", 0), ls.STORE.stats(c_cid).get("wins", 0)
sent.clear()
pa, pb = join_front(A, a_cid), join_front(B, b_cid)
pc = join_front(C, c_cid)
m3 = pa.match
step()
ok &= check("kill mode: arena logic 5 with both nations", m3.kill_mode and got(A, 40050)[-1].endswith(
    struct.pack("<IBBf", ls.ARENA_KILL, ls.FEDERAL, ls.ALLIANCE, float(ls.ROUND_SECONDS))))
ok &= check("kill mode: a joining player gets both scores and the time left",
            sorted(got(C, 52001)) == sorted([struct.pack("<Bi", ls.FEDERAL, 0), struct.pack("<Bi", ls.ALLIANCE, 0)])
            and len(got(C, 49000)) == 1 and 0 < struct.unpack_from("<f", got(C, 49000)[0])[0] <= ls.ROUND_SECONDS)
kill(pa, pb)
step()
ok &= check("kill mode: a kill scores for the killer's side", struct.pack("<Bi", ls.FEDERAL, 1) in got(B, 52001))
kill(pc, pb)
step()
ok &= check("kill mode: a kill on your own side scores nothing", m3.side_kills == {ls.FEDERAL: 1, ls.ALLIANCE: 0})
kill(pa, pc)
step()
ok &= check("kill mode: the side reaching the limit wins", m3.over and any(b"Federal" in x for x in got(B, 30250)))


def score_rows(body):
    n, at = struct.unpack_from("<I", body)[0], 4
    rows = []
    for _ in range(n):
        row = []
        for _ in range(6):
            size = body[at]  # short strings: a one-byte length
            row.append(body[at + 1:at + 1 + size].decode())
            at += 1 + size
        rows.append(row)
    return rows, struct.unpack_from("<I", body, at)[0], at + 4 == len(body)


tables = got(B, 20130)
rows, seconds, whole = score_rows(tables[0]) if tables else ([], 0, False)
ok &= check("kill mode: the score table is sent in place of DisconnectByServer", len(tables) == 1 and not got(B, 30150) and whole)
ok &= check("score table: winner and both sides' kills, with the countdown",
            [r[1:4] for r in rows] == [["Winner", "1", "Federal"], ["Federal kills", "1", "2"],
                                       ["Alliance kills", "1", "0"]]
            and all(r[0].endswith(".dds") and r[4] == "14" and r[5] == "ff22cf00" for r in rows)
            and seconds == int(ls.RECORD_SECONDS))
ok &= check("kill mode: its players get a win", ls.STORE.stats(a_cid).get("wins") == wins_a + 1
            and ls.STORE.stats(c_cid).get("wins", 0) == wins_c)
for addr in list(server.peers):
    server.close(server.peers[addr], "test over")
ls.KILL_MODE = False
step()

# a session flooding messages is cut down, then closed
pa = join_front(A, a_cid)
flood = b"".join(struct.pack("<H", 4) + bytes([0x80, 9]) + struct.pack("<H", 1) for _ in range(250))
for _ in range(2):
    server.handle(fdp.Packet(0x01, 1, pa.server_session, 12 + len(flood), 0, 0, 0, flood).pack(), A)
ok &= check("a message flood is dropped past the burst", pa.dropped > 0 and A in server.peers)
for _ in range(fdp.MSG_DROP_CLOSE // 250 + 1):
    server.handle(fdp.Packet(0x01, 1, pa.server_session, 12 + len(flood), 0, 0, 0, flood).pack(), A)
ok &= check("a session that keeps flooding is closed", A not in server.peers)
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
