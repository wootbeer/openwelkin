# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_split.py -- start login, gate and two game servers in a temporary folder and try them from outside.

Fake players use tools/loadtest.py's client. Checks: the servers link up, a player goes login -> gate -> game, stats
and event log lines reach the gate's characters.json, admin commands reach all servers, forged and reused session
keys and wrong link keys are refused, a gate takes no logins, IP bans reach the gate."""
import asyncio
import collections
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(TOOLS)
OFFSET = 200
os.environ["MW_PORT_OFFSET"] = str(OFFSET)
sys.path[:0] = [SERVER, TOOLS]
root = tempfile.mkdtemp(prefix="mw-split-")
os.environ["MW_STATE_DIR"] = os.path.join(root, "tool")
os.makedirs(os.environ["MW_STATE_DIR"])
import cluster  # noqa: E402
import fdp  # noqa: E402
import loadtest as lt  # noqa: E402
import loginserver as ls  # noqa: E402

fails = []
lines = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(name)


def pump(proc):
    for line in proc.stdout:
        lines.append(line.rstrip())


def wait_for(fn, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def seen(text):
    return any(text in l for l in lines)


def admin(command):
    with open(os.path.join(root, "login", "admin.key"), encoding="ascii") as f:
        key = f.read().strip()
    with socket.create_connection(("127.0.0.1", ls.ADMIN_PORT), timeout=10) as s:
        s.sendall(f"{key}\n{command}\n".encode())
        reply = b""
        while chunk := s.recv(65536):
            reply += chunk
    return reply.decode()


KEY = lt.rsa.generate_private_key(public_exponent=65537, key_size=1024)


async def login(name):
    """(result, session key, gate port)"""
    c = await lt.Tcp.open("127.0.0.1", ls.LOGIN_PORT, KEY)
    a = name.encode()
    r = await c.request(10, 10000, struct.pack("<I", 1) + bytes([len(a)]) + a + bytes([8]) + b"loadtest"
                        + struct.pack("<II", 10102, 1) + bytes([4]) + b"hash")
    c.close()
    if r.result:
        return r.result, None, 0
    key, off = ls.read_wstr(r.body, 10)
    return 0, key, struct.unpack_from("<H", r.body, 8)[0]


async def gate_session(key):
    """A gate connection opened with this session key."""
    return await lt.Tcp.open("127.0.0.1", ls.GATE_PORT, KEY, ls.wstr(key + " hash"))


async def answers(gate):
    """Does the gate answer a character list request? It stays silent on connections that are not logged in."""
    try:
        await asyncio.wait_for(gate.request(13, 13000), 2)
        return True
    except (asyncio.TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError):
        return False


async def enter(gate, name):
    """(character id, game port, ticket)"""
    r = await gate.request(13, 13000)
    if struct.unpack_from("<I", r.body)[0]:
        cid = struct.unpack_from("<I", r.body, 4)[0]
    else:
        r = await gate.request(13, 13006, ls.wstr(name) + bytes([1]) + struct.pack("<I", 1) + bytes(8))
        cid = struct.unpack_from("<I", r.body)[0]
    r = await gate.request(18, 18000, struct.pack("<IIIH", cid, 1, 0, 0))
    if r.result:
        return cid, 0, 0
    _, _, port, ticket = struct.unpack_from("<IIHI", r.body)
    return cid, port, ticket


class Player:
    """A connected client in battle."""

    async def join(self, name, res):
        self.res = res
        code, key, _ = await login(name)
        self.gate = await gate_session(key)
        self.cid, self.port, ticket = await enter(self.gate, name)
        loop = asyncio.get_running_loop()
        self.t, self.g = await loop.create_datagram_endpoint(lambda: lt.Game(False, res),
                                                             remote_addr=("127.0.0.1", self.port))
        stream = struct.pack("<III", ticket, 9, ticket)
        self.t.sendto(fdp.Packet(0x07, 1, 9, 12, fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack())
        await asyncio.wait_for(asyncio.shield(self.g.syn_ack), 3)
        self.g.send(40851, bytes(4), reliable=True)
        await asyncio.sleep(0.2)
        self.g.send(30050, bytes(4), reliable=True)

    def leave(self):
        self.t.sendto(fdp.Packet(0x07, self.g.rel, self.g.server_session, 12, fdp.SPLIT_FIN, 0, 0).pack())
        self.gate.close()


def stored():
    try:
        with open(os.path.join(root, "gate", "characters.json"), encoding="utf-8") as f:
            return json.load(f)["accounts"]
    except (OSError, ValueError, KeyError):
        return {}


async def scenario():
    res = collections.defaultdict(int)  # the fake client's counters

    # --- login -> gate -> game ---
    code, key, gate_port = await login("alice")
    check("login hands out a gate", code == 0 and gate_port == ls.GATE_PORT, f"{code} {gate_port}")
    check("session key is signed", len(key) > 150, key)
    gate = await gate_session(key)
    check("gate takes the signed key", await answers(gate))
    gate.close()

    # --- what must fail ---
    g2 = await gate_session(key)
    check("a session key works once", not await answers(g2))
    g2.close()
    raw = bytearray(bytes.fromhex((await login("alice"))[1]))
    raw[13] ^= 1  # changes the account name
    g3 = await gate_session(raw.hex())
    check("a changed session key is refused", not await answers(g3))
    g3.close()
    g4 = await gate_session("00" * 90)
    check("a made-up session key is refused", not await answers(g4))
    g4.close()
    g5 = await lt.Tcp.open("127.0.0.1", ls.GATE_PORT, KEY)
    a = b"mallory"
    try:
        await asyncio.wait_for(g5.request(10, 10000, struct.pack("<I", 1) + bytes([len(a)]) + a + bytes([8]) + b"loadtest"
                                          + struct.pack("<II", 10102, 1) + bytes([4]) + b"hash"), 2)
        took = True
    except (asyncio.TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError):
        took = False
    g5.close()
    check("the gate takes no logins", not took)
    check("the gate made no account", not seen("mallory"))

    # --- two players are spread over the two game servers ---
    p1, p2 = Player(), Player()
    await p1.join("bob", res)
    await asyncio.sleep(2.5)  # game servers report their load every 2 s
    await p2.join("carol", res)
    await asyncio.sleep(0.5)
    check("players go to different game servers", p1.port != p2.port, f"{p1.port} {p2.port}")
    who = await asyncio.to_thread(admin, "who")
    check("who sees both, on their game servers", "bob" in who and "carol" in who and "game port" in who, who)
    matches = await asyncio.to_thread(admin, "matches")
    check("matches lists both servers", matches.count("game port") == 2, matches)

    # --- a stat reaches the gate ---
    p1.leave()
    p1left = await asyncio.to_thread(wait_for, lambda: seen("left") or seen("closed"), 3)
    ok = await asyncio.to_thread(wait_for, lambda: any(
        c.get("stats", {}).get("battle_time", 0) > 0 for c in stored().get("bob", [])), 8)
    check("time played reaches characters.json on the gate", p1left and ok, str(stored().get("bob")))

    # --- kick across servers ---
    reply = await asyncio.to_thread(admin, "kick carol")
    check("kick reaches the gate", reply.strip() == "kicked carol", reply)
    ok = await asyncio.to_thread(wait_for, lambda: "carol" not in admin("matches"), 4)
    check("kick reaches the game server", ok)

    # --- bans ---
    await asyncio.to_thread(admin, "ban bob 1h test")
    check("banned account is refused at login", (await login("bob"))[0] == ls.ACCOUNT_SUSPENDED)
    await asyncio.to_thread(admin, "unban bob")
    code, key, _ = await login("bob")
    gate = await gate_session(key)
    _, port, ticket = await enter(gate, "bob")
    check("login works again after unban", code == 0 and port != 0)
    await asyncio.to_thread(admin, "ipban 127.0.0.1 1h test")
    await asyncio.sleep(0.5)
    try:
        g6 = await asyncio.wait_for(gate_session("00"), 2)
        refused = not await answers(g6)
    except (OSError, asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        refused = True
    logged = await asyncio.to_thread(wait_for, lambda: seen("refused: IP banned"), 3)
    check("IP ban reaches the gate", refused and logged)
    await asyncio.to_thread(admin, "unipban 127.0.0.1")
    await asyncio.sleep(0.5)
    code, key, _ = await login("dave")
    gate = await gate_session(key)
    check("unipban reaches the gate", code == 0 and await answers(gate))
    gate.close()


def reports():
    """What a game server tells the gate about a kill, and the gate storing it (no server needed)."""
    class Uplink:
        sent = []

        def send(self, kind, **fields):
            self.sent.append((kind, fields))
            return True

    game, gate = ls.RemoteStore(), ls.Store()
    gate.data = {"next_id": 1000, "accounts": {"erin": [{"id": 1000, "name": "Erin"}]}}
    gate.indexed = None
    gate.save = lambda: None
    ls.STORE, ls.UPLINK = game, Uplink()
    game.announce("erin", {"id": 1000, "name": "Erin"})
    game.add_log(1000, "Destroyed by Target 1")
    game.add_stats(1000, deaths=1)
    ls.STORE = gate
    for kind, fields in Uplink.sent:
        ls.on_game_server_message(None, {"t": kind, **fields})
    ls.UPLINK = None
    c = gate.by_id(1000)
    check("a kill's log line and stats reach the gate's store",
          "Destroyed" in str(c.get("log")) and c.get("stats", {}).get("deaths") == 1, str(c))


def link_attack():
    """A stranger without internal.key on the private port."""
    s = socket.create_connection(("127.0.0.1", ls.GATE_LINK_PORT), timeout=5)
    link = cluster.Link(s, b"wrong key", lambda *a: None)
    try:
        link.handshake(connecting=True)
        check("a wrong link key is refused", False)
    except (OSError, ConnectionError):
        check("a wrong link key is refused", True)
    s.close()


def main():
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(SERVER, "start_split.py"), "--games", "2", "--state", root],
                            text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    threading.Thread(target=pump, args=(proc,), daemon=True).start()
    try:
        linked = wait_for(lambda: sum("connected" in l and "[link] server on port" in l for l in lines) >= 3, 15)
        check("gate and two game servers link up", linked, "\n".join(lines[-20:]))
        check("signing key stays on login", not os.path.exists(os.path.join(root, "gate", "ticket.key")))
        check("accounts stay on login", not os.path.exists(os.path.join(root, "gate", "accounts.json")))
        link_attack()
        reports()
        asyncio.run(scenario())
        check("no errors in the logs", not any("Traceback" in l or "tick error" in l for l in lines),
              "\n".join(l for l in lines if "Traceback" in l or "error" in l)[:800])
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True) if os.name == "nt" \
            else proc.terminate()
        proc.wait(10)
        time.sleep(0.5)
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(fails)} failed" if fails else "all ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
