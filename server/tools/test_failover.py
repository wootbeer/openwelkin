# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_failover.py -- split servers killed and restarted while the others run. Checks: stats made while the gate is
away arrive after it restarts, logins work again after a gate or login restart, bans survive and reach a restarted
gate, players on a dead game server are sent out, a restarted game server rejoins, a second gate is refused, and a
hung server can block neither a link nor the game loop."""
import asyncio
import collections
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import test_split as ts

import cluster  # noqa: E402
import loginserver as ls  # noqa: E402

check, lines, wait_for, seen, root = ts.check, ts.lines, ts.wait_for, ts.seen, ts.root
procs = {}


def game_port(n):
    return (6003 if n == 1 else 6019 + n) + ts.OFFSET


def start(name):
    role = name.rstrip("12")
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    env = dict(os.environ, MW_ROLE=role, MW_STATE_DIR=folder)
    if role == "game":
        env["MW_GAME_PORT"] = str(game_port(int(name[-1])))
    if role != "login":
        shutil.copy(os.path.join(root, "login", "internal.key"), folder)
    if role == "gate":
        shutil.copy(os.path.join(root, "login", "ticket.pub"), folder)
    proc = subprocess.Popen([sys.executable, "-u", "loginserver.py"], cwd=ts.SERVER, env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=ts.pump, args=(proc,), daemon=True).start()
    procs[name] = proc


def stop(name):
    procs[name].kill()
    procs.pop(name).wait(10)


def links():
    return sum(len(re.findall(r"\[link\] server on port \d+ connected", l)) for l in lines)


def linked(n, seconds=15):
    return wait_for(lambda: links() >= n, seconds)


async def login_works(name, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        code, key, _ = await ts.login(name)
        if code == 0:
            gate = await ts.gate_session(key)
            ok = await ts.answers(gate)
            gate.close()
            if ok:
                return True
        await asyncio.sleep(0.3)
    return False


async def scenario():
    res = collections.defaultdict(int)

    # --- the gate dies while a player fights, and comes back ---
    bob = ts.Player()
    await bob.join("bob", res)
    n = links()
    stop("gate")
    await asyncio.sleep(1)
    bob.leave()  # battle time is reported to a gate that is not there
    await asyncio.to_thread(wait_for, lambda: seen("left") or seen("closed"), 3)
    start("gate")
    check("game servers rejoin a restarted gate", await asyncio.to_thread(linked, n + 3))
    check("login works after a gate restart", await login_works("erin"))
    ok = await asyncio.to_thread(wait_for, lambda: any(
        c.get("stats", {}).get("battle_time", 0) > 0 for c in ts.stored().get("bob", [])), 12)
    check("stats made while the gate was away arrive", ok, str(ts.stored().get("bob")))
    check("the character survived the crash", len(ts.stored().get("bob", [])) == 1)

    # --- login dies: bans are kept, and the gate gets them again ---
    await ts.login("frank")
    await asyncio.to_thread(ts.admin, "ban frank 1h test")
    await asyncio.to_thread(ts.admin, "ipban 203.0.113.0/24 1h test")
    n = links()
    gate = await ts.gate_session((await ts.login("bob"))[1])
    stop("login")
    check("a player in the hall stays while login is down", await ts.answers(gate))
    gate.close()
    start("login")
    check("the gate rejoins a restarted login", await asyncio.to_thread(linked, n + 1))
    check("login works after a login restart", await login_works("erin"))
    check("a ban survives a login restart", (await ts.login("frank"))[0] == ls.ACCOUNT_SUSPENDED)
    await asyncio.to_thread(ts.admin, "unban frank")
    bans = await asyncio.to_thread(ts.admin, "bans")
    check("an IP ban survives a login restart", "203.0.113.0/24" in bans, bans)

    # --- a game server dies with a player on it ---
    carol = ts.Player()
    await carol.join("carol", res)
    for _ in range(2):  # dave goes to the other game server, or to the same one if its load report is late
        await asyncio.sleep(2.5)
    name = "game1" if carol.port == game_port(1) else "game2"
    other = "game2" if name == "game1" else "game1"
    await asyncio.sleep(0.5)
    stop(name)
    closed = await asyncio.to_thread(wait_for, lambda: seen("was on a game server that is gone"), ls.LOST_GRACE + 4)
    check("the gate notices the players of a dead game server", closed)
    check("their gate session is closed", not await ts.answers(carol.gate))
    code, key, _ = await ts.login("carol")
    gate = await ts.gate_session(key)
    _, port, _ = await ts.enter(gate, "carol")
    check("they can enter a battle on the other game server", port == game_port(int(other[-1])), str(port))
    gate.close()

    # --- it comes back ---
    n = links()
    start(name)
    check("a restarted game server rejoins", await asyncio.to_thread(linked, n + 1))
    await asyncio.sleep(0.5)
    matches = await asyncio.to_thread(ts.admin, "who")
    check("admin commands still work", "Traceback" not in matches, matches)


class FakeLink:
    def __init__(self, port, accounts=(), boot=None):
        self.ip, self.port, self.accounts, self.boot, self.load, self.closed = 1, port, list(accounts), boot, 0, False

    def request(self, kind, **fields):
        return {"ip": self.ip, "port": self.port, "boot": self.boot}

    def send(self, *a, **k):
        return True

    def close(self):
        self.closed = True


def units():
    # a second gate
    ls.log, role, ls.ROLE = lambda *a: None, ls.ROLE, "login"
    first, second, again = FakeLink(6002), FakeLink(6102), FakeLink(6002)
    for link in (first, second, again):
        ls.register_lower_server(link)
    check("a second gate is refused", second.closed and second not in ls.LINKS)
    check("the same gate may reconnect", first in ls.LINKS and again in ls.LINKS)
    ls.LINKS.clear()
    ls.ROLE = role

    # a gate restart forgets which session keys were used
    secret = ls.ed25519.Ed25519PrivateKey.generate()
    ls.TICKET_SIGNER, ls.TICKET_VERIFIER = secret, secret.public_key()
    key = ls.signed_session("amy")
    check("a session key works", ls.redeem_signed_session(key)[0] == "amy")
    check("a session key works once", ls.redeem_signed_session(key)[0] is None)
    key = ls.signed_session("amy")
    started, ls.STARTED = ls.STARTED, int(ls.time.time()) + 1
    check("a key from before a gate restart is refused", ls.redeem_signed_session(key)[0] is None)
    ls.STARTED = started

    # a dropped link to a live game server is not a dead server
    grace, kicked, ls.kick, ls.LOST_GRACE = ls.LOST_GRACE, [], lambda account, reason: kicked.append(account), 0
    ls.LINKS.append(FakeLink(1, boot="same"))
    ls.game_server_lost(FakeLink(1, ["amy"], boot="same"))
    check("a game server that reconnects keeps its players", not kicked)
    ls.LINKS.clear()
    ls.LINKS.append(FakeLink(2, ["ben"], boot="other"))
    ls.game_server_lost(FakeLink(1, ["amy", "ben"], boot="dead"))
    check("only players left on the dead server are sent out", kicked == ["amy"], str(kicked))
    ls.LOST_GRACE = grace
    ls.LINKS.clear()

    # the game loop never waits for the gate
    class Stuck:
        def send(self, *a, **k):
            time.sleep(30)

    game = ls.RemoteStore()
    game.announce("amy", {"id": 1, "name": "Amy"})
    ls.STORE, ls.UPLINK = game, Stuck()
    t = time.time()
    for _ in range(100):
        game.add_stats(1, kills=1)
    check("the game loop does not wait for a stuck gate", time.time() - t < 1)
    ls.UPLINK = None

    # a full report queue is counted
    game.pending.clear()
    game.pending.extend([("log", {})] * game.pending.maxlen)
    game.report("log")
    check("reports lost to a full queue are counted", game.lost == 1)
    game.pending.clear()

    # a server that stops reading loses its link
    cluster.SEND_TIMEOUT = 1.0
    server = socket.create_server(("127.0.0.1", 0))
    c = socket.create_connection(server.getsockname())
    s, _ = server.accept()
    near, far = cluster.Link(c, b"k", None), cluster.Link(s, b"k", None)
    thread = threading.Thread(target=far.handshake, args=(False,))
    thread.start()
    near.handshake(connecting=True)
    thread.join()
    t = time.time()
    blob = "x" * 500000
    gone = next((n for n in range(500) if not near.send("big", blob=blob)), None)
    check("a hung server loses its link instead of blocking", gone is not None and time.time() - t < 15, str(gone))
    far.close()
    server.close()


def main():
    os.makedirs(os.path.join(root, "login"), exist_ok=True)
    cluster.load_key(os.path.join(root, "login", "internal.key"))
    try:
        units()
        start("login")
        wait_for(lambda: os.path.exists(os.path.join(root, "login", "ticket.pub")), 10)
        start("gate")
        start("game1")
        start("game2")
        check("all servers link up", linked(3))
        asyncio.run(scenario())
        check("no errors in the logs", not any("Traceback" in l or "tick error" in l for l in lines),
              "\n".join(l for l in lines if "Traceback" in l or "rror" in l)[:1500])
    finally:
        for proc in procs.values():
            proc.kill()
        time.sleep(0.5)
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(ts.fails)} failed" if ts.fails else "all ok")
    return 1 if ts.fails else 0


if __name__ == "__main__":
    sys.exit(main())
