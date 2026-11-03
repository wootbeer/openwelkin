# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_chat.py -- start a server in a temporary folder with fake players: chat reaches the others, nobody can write as
someone else or send what the client should not get, and only the right roles can use ! commands."""
import asyncio
import collections
import os
import shutil
import struct
import subprocess
import sys
import threading
import time

import test_split as ts

import loginserver as ls  # noqa: E402

check, wait_for = ts.check, ts.wait_for


async def player(name):
    """(gate connection, character id) of a player in the hall."""
    code, key, _ = await ts.login(name)
    gate = await ts.gate_session(key)
    r = await gate.request(13, 13006, ls.wstr(name) + bytes([1]) + struct.pack("<I", 1) + bytes(8))
    cid = struct.unpack_from("<I", r.body)[0]
    await gate.request(13, 13004, struct.pack("<I", cid))
    return gate, cid


def say(gate, text, type_=21513, before=b""):
    gate.send(ls.Message(21, type_, body=before + ls.wstr(text)))


async def heard(gate, seconds=1.5):
    """(type, id, name, text) of the next chat line, or None."""
    try:
        while True:
            m = await asyncio.wait_for(gate.recv(), seconds)
            if m.catalog == 21:
                name, off = ls.read_wstr(m.body, 4)
                return m.type, struct.unpack_from("<I", m.body)[0], name, ls.read_wstr(m.body, off)[0]
    except asyncio.TimeoutError:
        return None


async def battle(gate, cid):
    """Enter a battle as a fake client (it only needs to stay open while the test runs)."""
    r = await gate.request(18, 18000, struct.pack("<IIIH", cid, 1, 0, 0))
    _, _, port, ticket = struct.unpack_from("<IIHI", r.body)
    transport, game = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: ts.lt.Game(False, collections.defaultdict(int)), remote_addr=("127.0.0.1", port))
    stream = struct.pack("<III", ticket, 9, ticket)
    transport.sendto(ts.fdp.Packet(0x07, 1, 9, 12, ts.fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack())
    await asyncio.wait_for(asyncio.shield(game.syn_ack), 3)
    game.send(40851, bytes(4), reliable=True)
    await asyncio.sleep(0.2)
    game.send(30050, bytes(4), reliable=True)


async def scenario():
    amy, amy_id = await player("amy")
    ben, ben_id = await player("ben")
    gate = (await ts.login("nochar"))[1]
    stranger = await ts.gate_session(gate)

    # --- ordinary chat ---
    say(amy, "hello")
    check("broadcast reaches the others", await heard(ben) == (21514, amy_id, "amy", "hello"))
    check("broadcast comes back to the sender", await heard(amy) == (21514, amy_id, "amy", "hello"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "not in a battle", 21052)
    check("no battle, no battle chat", await heard(ben, 0.4) is None and await heard(amy, 0.2) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    cara, _ = await player("cara")
    dan, dan_id = await player("dan")
    await battle(amy, amy_id)
    await battle(ben, ben_id)
    await battle(dan, dan_id)  # MW_MATCH_PLAYERS is 2: a new match
    await asyncio.sleep(0.5)
    say(amy, "in battle", 21052)
    check("battle chat reaches the same match", await heard(ben) == (21211, amy_id, "amy", "in battle"))
    check("the client shows its own battle chat", await heard(amy, 0.3) is None)
    check("players in the hall do not get it", await heard(cara, 0.3) is None)
    check("players in another match do not get it", await heard(dan, 0.3) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "country", 21004, struct.pack("<H", 5))
    check("the battle chat box reaches the match", await heard(ben) == (21204, amy_id, "amy", "country"))
    check("and comes back to the sender", await heard(amy) == (21204, amy_id, "amy", "country"))
    check("but not another match", await heard(dan, 0.3) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "no such tab", 21004, struct.pack("<H", 9))
    check("an unknown channel is dropped", await heard(ben, 0.3) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "to all")
    check("broadcast still reaches a battle", (await heard(dan))[3] == "to all")
    await heard(ben)
    await heard(amy)
    await heard(cara)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "psst", 21032, ls.wstr("BEN"))
    check("a private line reaches its target", await heard(ben) == (21200, amy_id, "amy", "psst"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "real client", 21051, ls.wstr("ben"))
    check("a whisper the way the client sends it", await heard(ben) == (21200, amy_id, "amy", "real client"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "psst", 21032, ls.wstr("nobody"))
    check("a private line to nobody says so", await heard(amy) == (21200, 0, "Server", "nobody is not online"))

    # --- bad input ---
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "one\r\n[chat] ben: two\0\x1b")
    check("line breaks and control characters go", (await heard(ben))[3] == "one[chat] ben: two")
    await heard(amy)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "x" * 900)
    check("a long line is cut", len((await heard(ben))[3]) == ls.CHAT_MAX)
    await heard(amy)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, " \n ")
    check("an empty line is dropped", await heard(ben, 0.4) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    for n in range(5):
        say(amy, f"spam {n}")
    check("spam is limited", await heard(ben) is not None and await heard(ben, 0.3) is None)
    await heard(amy)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    amy.send(ls.Message(21, 21513, body=b"\xff\xff\xff\xff\xff"))
    amy.send(ls.Message(21, 21032, body=b"\x7f"))
    check("broken strings are ignored", await ts.answers(amy))
    say(stranger, "no character yet")
    check("no character, no chat", await heard(ben, 0.4) is None)
    amy.send(ls.Message(21, 21513, token=7, flags=ls.MSG_REQUEST, body=ls.wstr("request")))
    check("a request gets its answer", await ts.answers(amy))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)

    # --- friend and block lists ---
    async def ask(type_, body=b""):
        return await asyncio.wait_for(amy.request(21, type_, struct.pack("<I", amy_id) + body), 5)

    def names(m):
        n, off, rows = struct.unpack_from("<I", m.body)[0], 4, []
        for _ in range(n):
            name, off = ls.read_wstr(m.body, off + 4)
            rows.append(name)
            off += 4
        return rows

    r = await ask(21524, ls.wstr("amy") + ls.wstr("BEN"))
    check("a friend is added by name", r.result == 0 and ls.read_wstr(r.body, 0)[0] == "ben")
    check("the list shows them", names(await ask(21522)) == ["ben"])
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    check("nobody by that name", (await ask(21524, ls.wstr("nobody") + ls.wstr("amy"))).result != 0)
    check("not your own name", (await ask(21524, ls.wstr("amy") + ls.wstr("amy"))).result != 0)
    check("too many changes wait", (await ask(21526, ls.wstr("ben") + ls.wstr("amy"))).result == 0
          and (await ask(21527, struct.pack("<I", ben_id))).result != 0)
    r = await asyncio.wait_for(ben.request(21, 21522, struct.pack("<I", amy_id)), 5)
    check("nobody reads another character's list", r.result != 0)
    check("the block list shows them", names(await ask(21523)) == ["ben"])
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(ben, "hello amy")
    check("a blocked player is not heard", await heard(amy, 0.4) is None)
    await heard(ben)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(ben, "psst", 21032, ls.wstr("amy"))
    check("a block looks like being offline", await heard(ben) == (21200, 0, "Server", "amy is not online"))
    check("no private line either", await heard(amy, 0.3) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    check("a block can be removed", (await ask(21527, struct.pack("<I", ben_id))).result == 0)
    check("the block list is empty", names(await ask(21523)) == [])
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(ben, "hello again")
    check("heard again", (await heard(amy))[3] == "hello again")
    await heard(ben)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)

    # --- battlefront contribution ---
    r = await amy.request(14, 14051, struct.pack("<II", amy_id, 1))
    check("contribution on a front", r.result == 0 and len(r.body) >= 4)
    r = await amy.request(14, 14050, struct.pack("<I", amy_id))
    n = struct.unpack_from("<I", r.body)[0]
    check("contribution on every front", n == 1 and struct.unpack_from("<I", r.body, 4)[0] == 1)

    # --- ranking ---
    r = await amy.request(15, 15106, struct.pack("<BII", 1, 1, 20))  # Total, from the second row
    check("a list can start further down", struct.unpack_from("<II", r.body)[1] == struct.unpack_from("<I", r.body)[0] - 1)
    r = await amy.request(15, 15106, struct.pack("<BII", 1, 0, 1000))
    total, count = struct.unpack_from("<II", r.body)
    off, rows = 8, []
    for _ in range(count):
        cid = struct.unpack_from("<I", r.body, off)[0]
        name, off = ls.read_wstr(r.body, off + 4)
        check(f"no team for {name}", struct.unpack_from("<I", r.body, off + 1)[0] == 0)
        off = ls.read_wstr(r.body, off + 5)[1] + 1
        score = struct.unpack_from("<I", r.body, off)[0]
        rows.append((cid, name, score))
        off += 4
    check("the Crystal tab lists every character", total == count >= 4 and rows[0][:2] == (amy_id, "amy")
          and off == len(r.body), str(rows))
    r = await amy.request(15, 15107, struct.pack("<IB", amy_id, 1))
    check("my place, counted from 0", struct.unpack("<II", r.body) == (0, ls.START_MONEY))
    r = await ben.request(15, 15107, struct.pack("<IB", amy_id, 1))
    check("not someone else's", struct.unpack("<II", r.body)[1] == 0)

    # --- talents ---
    r = await amy.request(14, 14043, struct.pack("<I", amy_id))
    last, off = ls.read_wstr(r.body, 8)
    check("the client gets a date, a price and a wait", len(last) == 19
          and struct.unpack_from("<II", r.body, off) == (ls.TALENT_RESET_WAIT // 60, ls.TALENT_RESET_CRYSTAL))
    check("nothing to forget", (await amy.request(14, 14045, struct.pack("<I", amy_id))).result == 14005)
    check("not another character's", (await ben.request(14, 14045, struct.pack("<I", amy_id))).result != 0)

    # --- commands ---
    say(amy, "!role amy admin")
    check("a player can not use commands", await heard(amy) == (21200, 0, "Server", "not allowed"))
    check("the role did not change", "player" in ts.admin("role amy"))
    check("commands are not shown to others", await heard(ben, 0.4) is None)
    ts.admin("role amy gm")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!who")
    rows = [await heard(amy) for _ in range(4)] + [await heard(amy, 0.3)]
    check("a GM can use who", any(r and "ben" in r[3] for r in rows), str(rows))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!role amy admin")
    check("a GM can not change roles", await heard(amy) == (21200, 0, "Server", "not allowed"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!ban ben 1h spam")
    await heard(amy)
    check("a GM can ban", wait_for(lambda: "account ben" in ts.admin("bans"), 3))
    ts.admin("unban ben")
    ts.admin("role amy admin")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!backup")
    check("an admin can back up", "accounts.db ->" in (await heard(amy, 6))[3])
    check("commands are in the admin log", wait_for(lambda: ts.seen("gm amy: backup"), 3))

    # --- notices and announcements ---
    reply = ts.admin("notice Restart in 5 minutes\r\nkick amy")
    line = await heard(cara)
    while line and line[2] != "Server":
        line = await heard(cara)
    check("a notice reaches the hall", line == (21514, 0, "Server", "Restart in 5 minutes"), str(line))
    check("and does not run as a command", "notice sent to" in reply and "amy" in ts.admin("who"))
    check("an empty notice is refused", "usage" in ts.admin("notice "))
    path = os.path.join(ts.root, "login", "announcements.txt")
    await amy.request(21, 21047)
    check("announcements.txt is made", wait_for(lambda: os.path.exists(path), 3))
    with open(path, "w", encoding="utf8") as f:
        f.write("first line\n\n" + "x" * 900 + "\n")
    r = await amy.request(21, 21047)
    n = struct.unpack_from("<I", r.body)[0]
    first, off = ls.read_wstr(r.body, 8)
    second = ls.read_wstr(r.body, off + 8)[0]
    check("announcements come from the file", n == 2 and first == "first line" and len(second) == ls.CHAT_MAX)

    # --- kicked players are told why ---
    async def from_server(gate):
        while True:
            line = await heard(gate)
            if line is None or line[0] == 21514 and line[2] == "Server" and line[3].startswith("You"):
                return line

    ts.admin("kick cara rude\x1b talk")
    check("a kicked player sees why", await from_server(cara) == (21514, 0, "Server", "You were kicked: rude talk"))
    await asyncio.sleep(ls.KICK_NOTICE + 0.5)
    check("then the connection closes", not await ts.answers(cara))
    ts.admin("ban dan 1h spam")
    line = await from_server(dan)
    check("a banned player sees why", line and line[3].startswith("You were banned until ") and line[3].endswith(": spam"), str(line))
    await asyncio.sleep(ls.KICK_NOTICE + 0.5)
    check("and leaves too", not await ts.answers(dan))
    await from_server(amy)  # the rest of the backup reply
    ts.admin("ipban 127.0.0.1 1h bots")
    line = await from_server(amy)
    check("an IP ban says why", line and line[3].startswith("Your address was banned until ") and line[3].endswith(": bots"),
          str(line))


def main():
    env = dict(os.environ, MW_STATE_DIR=os.path.join(ts.root, "login"), MW_MATCH_PLAYERS="2")
    os.makedirs(env["MW_STATE_DIR"], exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(ts.SERVER, "loginserver.py")], env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=ts.pump, args=(proc,), daemon=True).start()
    try:
        check("the server starts", wait_for(lambda: ts.seen("admin commands on"), 15))
        asyncio.run(scenario())
    finally:
        proc.kill()
        time.sleep(0.5)
        shutil.rmtree(ts.root, ignore_errors=True)
    print(f"{len(ts.fails)} failed" if ts.fails else "all ok")
    return 1 if ts.fails else 0


if __name__ == "__main__":
    sys.exit(main())
