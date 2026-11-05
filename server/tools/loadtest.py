# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""loadtest.py -- many fake players against a real server, using the real protocol: login, hall, character,
Quick Enter, then in battle they move (10 updates/s), turn, fire and ping like a client.

Prints the server's own load lines ([stats]: tick time, CPU, packets) and how late other players' moves arrive
(each bot writes its send time into its position, so any bot that receives the move can measure the delay).

Usage: python -I tools/loadtest.py [--bots 64] [--per-match 32] [--seconds 60] [--procs 2]
By default it starts its own server (temporary data folder, ports 6101-6110) so nothing real is touched.
--target HOST runs against a server that is already running there instead (with --port-offset)."""
import argparse
import asyncio
import json
import math
import os
import random
import struct
import shutil
import subprocess
import sys
import tempfile
import threading
import time

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the tools folder out
import tmpdir  # noqa: E402  (removes the temporary folders at exit)
os.environ.setdefault("MW_STATE_DIR", tmpdir.make("mw-loadtest-"))  # importing loginserver opens files
import fdp  # noqa: E402
import loginserver as ls  # noqa: E402

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


class Tcp:
    """Client side of the login/gate transport (see the loginserver docstring)."""

    @classmethod
    async def open(cls, host, port, key, first=b""):
        self = cls()
        self.r, self.w = await asyncio.open_connection(host, port)
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        body = struct.pack("<IIII", 1, 0, 9, len(der)) + der + first
        self.w.write(ls.MAGIC + struct.pack("<I", len(body)) + body)
        head = await self.r.readexactly(8)
        resp = await self.r.readexactly(struct.unpack("<I", head[4:])[0])
        plain = key.decrypt(resp[4:], OAEP)
        self.key_index, self.aes_key = struct.unpack_from("<i", plain)[0], plain[6:22]
        self.token = 0
        return self

    def aes(self, decrypt, data):
        c = Cipher(algorithms.AES(self.aes_key), modes.ECB())
        x = c.decryptor() if decrypt else c.encryptor()
        return x.update(data) + x.finalize()

    def send(self, msg):
        payload = msg.pack()
        pad = 16 - len(payload) % 16
        body = self.aes(False, payload + bytes(pad - 1) + bytes([pad]))
        n = len(body) + 1
        head = bytes([0x40 | 0x1E, n]) if n < 0x100 else bytes([0x80 | 0x1E]) + struct.pack("<H", n)
        csum = (sum(head) + sum(body[::self.key_index])) & 0xFF
        self.w.write(head + body + bytes([csum]))

    async def recv(self):
        flags = (await self.r.readexactly(1))[0]
        width = {0x40: 1, 0x80: 2, 0xC0: 4}[flags & 0xC0]
        n = int.from_bytes(await self.r.readexactly(width), "little")
        plain = self.aes(True, (await self.r.readexactly(n))[:-1])
        return ls.Message.parse(plain[:len(plain) - plain[-1]])

    async def request(self, catalog, type_, body=b""):
        self.token += 1
        self.send(ls.Message(catalog, type_, token=self.token, flags=ls.MSG_REQUEST, body=body))
        while True:
            m = await asyncio.wait_for(self.recv(), 15)
            if m.token == self.token:
                return m

    def close(self):
        self.w.close()


class Game(asyncio.DatagramProtocol):
    """Client side of an FDP game session."""

    def __init__(self, observe, results):
        self.observe, self.res = observe, results
        self.syn_ack = asyncio.get_running_loop().create_future()
        self.server_session, self.rel, self.unrel, self.order = 0, 1, 1, 1
        self.expected, self.need_ack = None, False
        self.rid = None

    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        self.res["rx_bytes"] += len(data)
        self.res["rx_pkts"] += 1
        pkt = fdp.Packet.parse(data)
        if pkt.system:
            if pkt.split_id == fdp.SPLIT_SYN_ACK and not self.syn_ack.done():
                self.server_session = pkt.ack_id
                self.syn_ack.set_result(True)
            return
        if pkt.flags & 0x80:
            if self.expected is None or pkt.number == self.expected:
                self.expected = (pkt.number + 1) & 0xFFFF
            self.need_ack = True
        if not self.observe and self.rid:
            return
        now_ms = time.time() * 1000
        for raw, cat, typ in fdp.messages(pkt.payload):
            if typ == 30100 and self.rid is None:
                self.rid = struct.unpack_from("<II", raw, 12)[1]  # header 4 + event envelope 8 | cid | robot
            elif typ == 50100 and self.observe:
                rid, _, x, y, z = struct.unpack_from("<II3f", raw, 12)
                if rid % ls.PLAYER_RID_STEP == 1 and 0 <= y < 10000:  # a player (dummies stay on the ground)
                    lat = (now_ms - y * 100) % 1_000_000
                    if lat < 60_000:
                        self.res["latency"].append(lat)

    def error_received(self, exc):
        pass

    def send(self, typ, body, reliable=False):
        raw = bytes([0x80, 9]) + struct.pack("<H", typ) + body
        payload = struct.pack("<H", len(raw)) + raw
        if reliable:
            pkt = fdp.Packet(0xE1, self.rel, self.server_session, 12 + len(payload), 1, 0, self.order, payload)
            self.rel, self.order = self.rel + 1, self.order + 1
        else:
            pkt = fdp.Packet(0x01, self.unrel, self.server_session, 12 + len(payload), 0, 0, 0, payload)
            self.unrel += 1
        self.t.sendto(pkt.pack())

    def ack(self):
        if self.need_ack:
            self.need_ack = False
            self.t.sendto(fdp.Packet(0x05, self.rel, self.server_session, 12, fdp.SPLIT_ACK, self.expected, 2).pack())


async def run_bot(i, args, key, res, stop_at):
    t0 = time.time()
    try:
        login = await Tcp.open(args.target, ls.LOGIN_PORT, key)
        a = f"loadbot{i}".encode()
        r = await login.request(10, 10000, struct.pack("<I", 1) + bytes([len(a)]) + a + bytes([8]) + b"loadtest"
                                + struct.pack("<II", 10102, 1) + bytes([4]) + b"hash")
        login.close()
        if r.result:
            raise RuntimeError(f"login result {r.result}")
        session_key, _ = ls.read_wstr(r.body, 10)
        gate = await Tcp.open(args.target, ls.GATE_PORT, key, ls.wstr(session_key + " hash"))
        r = await gate.request(13, 13000)
        if struct.unpack_from("<I", r.body)[0]:
            cid = struct.unpack_from("<I", r.body, 4)[0]
        else:
            # the create request as the client sends it; bots alternate between the two nations
            nation = (ls.FEDERAL, ls.ALLIANCE)[i % 2]
            r = await gate.request(13, 13006, ls.wstr(f"Bot{i}") + ls.wstr("") + struct.pack("<IIBI", nation, 0, 0, 1))
            if r.result:
                raise RuntimeError(f"create character result {r.result}")
            cid = struct.unpack_from("<I", r.body)[0]
        r = await gate.request(18, 18000, struct.pack("<IIIH", cid, args.battlefield, 0, 0))
        if r.result:
            raise RuntimeError(f"apply enter result {r.result}")
        _, _, port, ticket = struct.unpack_from("<IIHI", r.body)
        loop = asyncio.get_running_loop()
        t, g = await loop.create_datagram_endpoint(lambda: Game(i % args.observe_every == 0, res),
                                                   remote_addr=(args.target, port))
        stream = struct.pack("<III", ticket, 9, ticket)
        for _ in range(3):  # SYN, resent like the client does
            t.sendto(fdp.Packet(0x07, 1, 9, 12, fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack())
            try:
                await asyncio.wait_for(asyncio.shield(g.syn_ack), 2)
                break
            except asyncio.TimeoutError:
                pass
        if not g.syn_ack.done():
            raise RuntimeError("no SYN-ACK")
        g.send(40851, bytes(4), reliable=True)
        await asyncio.sleep(0.3)
        g.send(30050, bytes(4), reliable=True)
        res["joined"] += 1
        res["join_s"].append(time.time() - t0)
        # walk a circle around a point near the others, turn, fire now and then, ping every second
        ang = random.uniform(0, 6.283)
        cx, cz = random.uniform(-args.spread, args.spread), random.uniform(-args.spread, args.spread)
        n = 0
        while time.time() < stop_at:
            n += 1
            ang += 0.05
            x, z = cx + 10 * math.cos(ang), cz + 10 * math.sin(ang)
            y = (time.time() * 1000 % 1_000_000) / 100  # send time in ms, read back by whoever receives the move
            g.send(40130, struct.pack("<I3f", 0, x, y, z))
            if n % 20 == 0:
                g.send(40100, struct.pack("<fI3f3f", 0.0, (n // 20) % 2, x, y, z, 1.0, 0.0, 0.0), reliable=True)
            if n % 10 == 0:
                g.send(9001, struct.pack("<I", n))
            if n % 40 == 0:
                g.send(40122, struct.pack("<III3f3fII", 0, 0, 0, x, y + 3, z, x + 50, y, z, 0, 0), reliable=True)
            if n % 40 == 10:
                g.send(40125, bytes(8), reliable=True)
            if n % 2 == 0:
                g.ack()
            await asyncio.sleep(0.1)
        t.sendto(fdp.Packet(0x07, g.rel, g.server_session, 12, fdp.SPLIT_FIN, 0, 0).pack())  # leave the battle
        gate.close()
    except Exception as e:  # noqa: BLE001
        res["failed"].append(f"bot {i}: {e!r}"[:200])


async def worker_main(args):
    key = rsa.generate_private_key(65537, 1024)  # one key for all bots of this process (the server accepts any)
    res = {"joined": 0, "failed": [], "latency": [], "join_s": [], "rx_bytes": 0, "rx_pkts": 0}
    ids = list(range(args.first, args.first + args.count))
    stop_at = args.stop_at
    tasks = []
    for k, i in enumerate(ids):
        tasks.append(asyncio.create_task(run_bot(i, args, key, res, stop_at)))
        await asyncio.sleep(args.ramp / max(1, len(ids)))
    await asyncio.gather(*tasks)
    lat = res["latency"]
    if len(lat) > 20000:
        lat = random.sample(lat, 20000)
    res["latency"] = lat
    res["cpu"] = time.process_time()
    print("RESULT " + json.dumps(res), flush=True)


def pct(v, p):
    return sorted(v)[min(len(v) - 1, int(len(v) * p))] if v else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bots", type=int, default=64)
    ap.add_argument("--per-match", type=int, default=32, help="players per match (server MW_MATCH_PLAYERS)")
    ap.add_argument("--seconds", type=float, default=60, help="time in battle after the last bot joined")
    ap.add_argument("--procs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)), help="bot processes")
    ap.add_argument("--battlefield", type=int, default=1)
    ap.add_argument("--spread", type=float, default=60.0, help="bots walk within this distance of the spawn area")
    ap.add_argument("--observe-every", type=int, default=8, help="every n-th bot measures delays")
    ap.add_argument("--target", default="127.0.0.1", help="server address (default: start a private server)")
    ap.add_argument("--port-offset", type=int, default=100)
    ap.add_argument("--keep", action="store_true", help="with --target: don't start a server")
    # worker mode (started by main)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--first", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--count", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--ramp", type=float, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--stop-at", type=float, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        asyncio.run(worker_main(args))
        return 0

    env = dict(os.environ, MW_PORT_OFFSET=str(args.port_offset))
    server = None
    stats = []
    if not args.keep:
        state = tempfile.mkdtemp(prefix="mw-loadtest-server-")
        senv = dict(env, MW_STATE_DIR=state, MW_STATS_EVERY="5", MW_PUBLIC_IP="127.0.0.1", MW_AUTO_REGISTER="1",
                    MW_MATCH_PLAYERS=str(args.per_match), MW_DEBUG="0",
                    MW_MOVE_CHECK="0")  # bots send the time as their height
        server = subprocess.Popen([sys.executable, "loginserver.py"], cwd=SERVER_DIR, env=senv, text=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        ready = threading.Event()

        def read_server():
            for line in server.stdout:
                if "listening on udp" in line and str(6003 + args.port_offset) in line:
                    ready.set()
                if "[stats]" in line or "tick error" in line or "Traceback" in line or "handling packet" in line:
                    stats.append(line.rstrip())
        threading.Thread(target=read_server, daemon=True).start()
        if not ready.wait(20):
            print("server did not start")
            server.kill()
            return 1
        print(f"server started (data in {state}, removed after the run; ports {6001 + args.port_offset}-{6010 + args.port_offset})")
    ramp = max(1.0, args.bots / 8)  # account creation (scrypt) is deliberately slow: ~8 new logins/s
    stop_at = time.time() + ramp + 2 + args.seconds
    per = -(-args.bots // args.procs)
    workers = []
    for k in range(args.procs):
        first, count = k * per, min(per, args.bots - k * per)
        if count <= 0:
            break
        workers.append(subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), "--worker", "--first", str(first), "--count", str(count),
             "--ramp", str(ramp), "--stop-at", str(stop_at), "--target", args.target, "--battlefield",
             str(args.battlefield), "--spread", str(args.spread), "--observe-every", str(args.observe_every)],
            env=env, text=True, stdout=subprocess.PIPE))
    print(f"{args.bots} bots in {len(workers)} processes, {args.per_match} per match; joining over {ramp:.0f} s, "
          f"then {args.seconds:.0f} s in battle")
    results = []
    for w in workers:
        out, _ = w.communicate()
        for line in out.splitlines():
            if line.startswith("RESULT "):
                results.append(json.loads(line[7:]))
    time.sleep(1)
    if server:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(state, ignore_errors=True)
    lat = [x for r in results for x in r["latency"]]
    joins = [x for r in results for x in r["join_s"]]
    failed = [x for r in results for x in r["failed"]]
    rx = sum(r["rx_bytes"] for r in results)
    print(f"\njoined {sum(r['joined'] for r in results)}/{args.bots}; join took median {pct(joins, 0.5):.1f} s, "
          f"max {max(joins or [0]):.1f} s")
    for f in failed[:10]:
        print("  " + f)
    print(f"move delay seen by other players (ms): median {pct(lat, 0.5):.0f}, 95% {pct(lat, 0.95):.0f}, "
          f"99% {pct(lat, 0.99):.0f}, max {max(lat or [0]):.0f}  ({len(lat)} samples)")
    print(f"each bot received {rx / max(1, args.bots) / (args.seconds + ramp) / 1024:.1f} KB/s on average; "
          f"bot processes used {sum(r['cpu'] for r in results):.0f} CPU seconds")
    if stats:
        print("\nserver load (every 5 s):")
        for line in stats[-12:]:
            print("  " + line.split("] ", 1)[-1] if "[stats]" in line else "  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
