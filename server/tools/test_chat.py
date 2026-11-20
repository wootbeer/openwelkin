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


async def player(name, nation=1):
    """(gate connection, character id) of a player in the hall. nation 1 is neither side."""
    code, key, _ = await ts.login(name)
    gate = await ts.gate_session(key)
    r = await gate.request(13, 13006, ls.wstr(name) + bytes([1]) + struct.pack("<I", nation) + bytes(8))
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


class Recorder(ts.lt.Game):
    """A fake battle client that keeps the commander (19,30020), order (19,30030) and detachment (19,30500 /
    30501) events it gets."""
    def __init__(self, *args):
        super().__init__(*args)
        self.events = []

    def datagram_received(self, data, addr):
        pkt = ts.fdp.Packet.parse(data)
        if not pkt.system:
            self.events += [(typ, raw[12:]) for raw, _, typ in ts.fdp.messages(pkt.payload) if typ in (30020, 30030, 30057, 30150, 30500, 30501, 30503, 30505, 50400, 50401, 50404)]
        super().datagram_received(data, addr)

    def last(self, typ):
        return next((body for t, body in reversed(self.events) if t == typ), None)

    def members(self, cid):
        """(own detachment id, the other characters the server put in it, in order of the events)."""
        own = next(struct.unpack_from("<I", b)[0] for t, b in self.events if t == 30500
                   and struct.unpack_from("<I", b, 4)[0] == cid)
        have = []
        for t, b in self.events:
            if t not in (30500, 30501):
                continue
            det, who = struct.unpack_from("<II", b)
            if det == own and who != cid:
                have = [c for c in have if c != who] + ([who] if t == 30500 else [])
        return own, have

    def squad(self, typ, number):
        self.send(typ, struct.pack("<fB", 0.0, number) if number is not None else bytes(4), reliable=True)


async def battle(gate, cid):
    """Enter a battle as a fake client; returns it (it only needs to stay open while the test runs)."""
    r = await gate.request(18, 18000, struct.pack("<IIIH", cid, 1, 0, 0))
    _, _, port, ticket = struct.unpack_from("<IIHI", r.body)
    transport, game = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: Recorder(False, collections.defaultdict(int)), remote_addr=("127.0.0.1", port))
    stream = struct.pack("<III", ticket, 9, ticket)
    transport.sendto(ts.fdp.Packet(0x07, 1, 9, 12, ts.fdp.SPLIT_SYN, 0, 0, struct.pack("<I", len(stream)) + stream).pack())
    await asyncio.wait_for(asyncio.shield(game.syn_ack), 3)
    game.send(40851, bytes(4), reliable=True)
    await asyncio.sleep(0.2)
    game.send(30050, bytes(4), reliable=True)
    return game


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
    dan, dan_id = await player("dan", ls.ALLIANCE)
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
    eve, eve_id = await player("eve", ls.FEDERAL)
    await battle(eve, eve_id)  # joins dan's match: the other side
    await asyncio.sleep(0.5)
    say(eve, "federal only", 21004, struct.pack("<H", 5))
    check("Country does not reach the other side", await heard(dan, 0.4) is None)
    check("but comes back to the sender", await heard(eve) == (21204, eve_id, "eve", "federal only"))
    await commanders(amy, cara)
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

    # --- loadout presets (Robot Roof) ---
    r = await amy.request(14, 14005, struct.pack("<I", 3) + ls.wstr("Fast 1311 0 0 0 1 3101 0"))
    check("a preset is saved", r.result == 0)
    r = await amy.request(14, 14005, struct.pack("<I", 4) + ls.wstr("x" * (ls.MAX_SUITE_TEXT + 1)))
    check("a long preset is refused", r.result != 0)
    r = await amy.request(14, 14005, struct.pack("<I", 4) + ls.wstr("bad\nline"))
    check("a preset with control characters is refused", r.result != 0)
    r = await amy.request(14, 14004, struct.pack("<IB", amy_id, 0))
    check("presets come back", r.body == struct.pack("<II", 1, 3) + ls.wstr("Fast 1311 0 0 0 1 3101 0"), r.body.hex())
    r = await ben.request(14, 14004, struct.pack("<IB", amy_id, 0))
    check("no one else reads them", r.body == struct.pack("<I", 0))

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
    r = await amy.request(15, 15098, struct.pack("<BII", 4, 0, 20))  # Daily (what goes in it: tools/test_db.py)
    check("Daily lists only who earned honor today", struct.unpack("<II", r.body) == (0, 0))
    r = await amy.request(15, 15099, struct.pack("<IB", amy_id, 4))
    check("and my place today has no score", struct.unpack("<II", r.body) == (0, 0))
    r = await amy.request(15, 15100, struct.pack("<BBII", 3, 1, 0, 20))  # Shooting, Weekly
    check("nobody has kills this week", struct.unpack("<II", r.body) == (0, 0))
    r = await amy.request(15, 15108, bytes(4))
    n, = struct.unpack_from("<I", r.body)
    kind, wid = struct.unpack_from("<II", r.body, 4)
    check("the Weapon tab gets the shop's weapons", n == len(ls.RANKED_WEAPONS) >= 1 and (kind, wid) == (1, 3101)
          and ls.read_wstr(r.body, 12)[0] == "Viper")
    r = await amy.request(15, 15102, struct.pack("<BIIII", 1, 1, 3101, 0, 20))
    check("nobody has Viper kills yet", struct.unpack("<II", r.body) == (0, 0))
    r = await amy.request(15, 15102, struct.pack("<BIIII", 1, 1, 7, 0, 20))
    check("an unknown weapon has no list", struct.unpack("<II", r.body) == (0, 0))
    r = await amy.request(15, 15103, struct.pack("<IBII", amy_id, 4, 1, 3101))
    check("nor a place", struct.unpack("<II", r.body) == (0, 0))

    # --- talents ---
    r = await amy.request(14, 14043, struct.pack("<I", amy_id))
    last, off = ls.read_wstr(r.body, 8)
    check("the client gets a date, a price and a wait", len(last) == 19
          and struct.unpack_from("<II", r.body, off) == (ls.TALENT_RESET_WAIT // 60, ls.TALENT_RESET_CRYSTAL))
    check("nothing to forget", (await amy.request(14, 14045, struct.pack("<I", amy_id))).result == 14005)
    check("not another character's", (await ben.request(14, 14045, struct.pack("<I", amy_id))).result != 0)

    # --- equipping (Warehouse drag and drop) ---
    gid = min(range(1, len(ls.SHOP) + 1), key=lambda g: max(ls.SHOP[g - 1][1], ls.SHOP[g - 1][2]))
    item = ls.SHOP[gid - 1][0]
    check("bought", (await amy.request(24, 24021, struct.pack("<IBBI", amy_id, 0, 0, gid))).result == 0)
    r = await amy.request(24, 24011, struct.pack("<IBII", amy_id, 2, 1, 10000))
    n = struct.unpack_from("<I", r.body, len(r.body) - 8)[0]  # the instance id of the last bought, not activated

    def in_use(r):
        slots, count = struct.unpack_from("<II", r.body)
        out, off = [], 8
        for _ in range(count):
            slot, it = struct.unpack_from("<II", r.body, off)
            _, off = ls.read_wstr(r.body, off + 13)
            out.append((slot, it))
        return out
    r = await amy.request(24, 24007, struct.pack("<III", amy_id, item, 2))
    check("a not activated item cannot be put in use", r.result != 0)
    check("activated", (await amy.request(24, 24015, struct.pack("<II", amy_id, n))).result == 0)
    r = await amy.request(24, 24007, struct.pack("<III", amy_id, item, 2))
    check("an item goes into a slot", r.result == 0)
    check("the in-use list shows it", in_use(await amy.request(24, 24006, struct.pack("<I", amy_id))) == [(2, item)])
    r = await amy.request(24, 24007, struct.pack("<III", amy_id, item, 5))
    check("it can move to another slot", r.result == 0 and
          in_use(await amy.request(24, 24006, struct.pack("<I", amy_id))) == [(5, item)])
    check("the Warehouse has 10 in-use slots", struct.unpack_from("<I", (await amy.request(
        24, 24006, struct.pack("<I", amy_id))).body)[0] == 10)
    check("an item saved in a slot past 10 (when 32 were sent) counts as not in use",
          ls.in_use_slot({"equipped": 20}) == 0 and ls.in_use_slot({"equipped": 10}) == 10)
    for bad, why in ((struct.pack("<III", amy_id, item, ls.IN_USE_SLOTS + 1), "a slot past the last"),
                     (struct.pack("<III", amy_id, 999999, 1), "an item not owned"),
                     (struct.pack("<II", amy_id, item), "a short request")):
        check(f"refused: {why}", (await amy.request(24, 24007, bad)).result != 0)
    r = await ben.request(24, 24007, struct.pack("<III", amy_id, item, 1))
    check("no one else equips your items", r.result != 0)
    check("no one else sees them", in_use(await ben.request(24, 24006, struct.pack("<I", amy_id))) == [])
    r = await amy.request(24, 24007, struct.pack("<III", amy_id, item, 0))
    check("slot 0 takes it out", r.result == 0 and
          in_use(await amy.request(24, 24006, struct.pack("<I", amy_id))) == [])
    check("taking out twice is refused", (await amy.request(24, 24007, struct.pack("<III", amy_id, item, 0))).result != 0)
    await amy.request(24, 24007, struct.pack("<III", amy_id, item, 3))
    r = await amy.request(24, 24025, struct.pack("<IB", amy_id, 0))
    check("nothing expired yet", r.result == 0 and r.body == struct.pack("<I", 0))

    # --- shop Package tab ---
    r = await amy.request(24, 24028, struct.pack("<II", 0xFFFF, 1))
    count = struct.unpack_from("<I", r.body, 4)[0]
    pid, = struct.unpack_from("<I", r.body, 8)
    name, off = ls.read_wstr(r.body, 12)
    _, off = ls.read_wstr(r.body, off)
    pic, name_item, credit, _, crystal, _ = struct.unpack_from("<6I", r.body, off)
    first = ls.PACKAGES[0]
    check("the Package tab lists the packages", count == len(ls.PACKAGES) and (pid, name) == first[:2]
          and (pic, name_item, credit, crystal) == (pid, pid, first[4], first[3]))
    r = await amy.request(24, 24002, struct.pack("<I", name_item))
    check("a package's name comes through its item id", ls.read_wstr(r.body, 0)[0] == first[1])
    points = struct.unpack("<I", (await amy.request(24, 24020, struct.pack("<I", amy_id))).body)[0]
    goods = struct.unpack_from("<I", (await amy.request(24, 24011, struct.pack("<IBII", amy_id, 2, 1, 10000))).body, 4)[0]
    check("a package is bought with Credit", (await amy.request(24, 24029, struct.pack("<IBI", amy_id, 0, pid))).result == 0)
    after = struct.unpack("<I", (await amy.request(24, 24020, struct.pack("<I", amy_id))).body)[0]
    r = await amy.request(24, 24011, struct.pack("<IBII", amy_id, 2, 1, 10000))
    got = struct.unpack_from("<I", r.body, 4)[0]
    last = [struct.unpack_from("<I", r.body, 8 + 8 * i + 4)[0] for i in range(goods, got)]
    check("its price is taken once", after == points - first[4], f"{points} -> {after}")
    check("all its items are in the warehouse", last == [ls.GOODS_OF[i] for i in first[2]], str(last))
    for bad, why in ((struct.pack("<IBI", amy_id, 0, 99), "an unknown package"),
                     (struct.pack("<IB", amy_id, 0), "a short request")):
        check(f"package refused: {why}", (await amy.request(24, 24029, bad)).result != 0)
    check("no one buys with your Credit", (await ben.request(24, 24029, struct.pack("<IBI", amy_id, 0, pid))).result != 0)

    # --- commands ---
    say(amy, "!role amy admin")
    check("a player can not use commands", await heard(amy) == (21200, 0, "Server", "not allowed"))
    check("the role did not change", "player" in ts.admin("role amy"))
    check("commands are not shown to others", await heard(ben, 0.4) is None)
    ts.admin("role amy gm")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!who")
    rows = [await heard(amy)]
    while rows[-1]:
        rows.append(await heard(amy, 0.3))
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
    r = await amy.request(20, 20116, struct.pack("<I", 0))
    check("held tasks: an explicit empty list", r.result == 0 and r.body == bytes(4), repr(r.body[:8]))

    # --- the client's own GM / commands ---
    async def online(name, cid):
        """A new gate session for the account and its CharacterOnline reply."""
        _, key, _ = await ts.login(name)
        gate = await ts.gate_session(key)
        return gate, (await gate.request(13, 13004, struct.pack("<I", cid))).body

    hal, hal_id = await player("hal")
    gail, gail_id = await player("gail")
    gail, body = await online("gail", gail_id)
    check("a player is not a GM to the client", body[13] == 0)
    ts.admin("role gail gm")
    gail, body = await online("gail", gail_id)
    check("a GM's character comes with GM level 101", body[13] == ls.GM_LEVEL["gm"])
    kick_cara = struct.pack("<I", hal_id) + ls.wstr("cara") + struct.pack("<I", 5)
    check("a player's /kick is refused", (await hal.request(13, 13041, kick_cara)).result != 0
          and "cara" in ts.admin("who"))
    gail.send(ls.Message(21, 21032, body=ls.wstr("hal") + ls.wstr("hello\x07 there")))
    told = await heard(hal)
    check("/tell is a whisper from the GM", told[2:] == ("gail", "hello there"), str(told))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    gail.send(ls.Message(21, 21030, body=struct.pack("<HI", 0, 0) + ls.wstr("Hall event")))
    check("a GM's /announce needs the notice command", await heard(gail) == (21200, 0, "Server", "not allowed"))
    ts.admin("role gail admin")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    gail.send(ls.Message(21, 21031, body=struct.pack("<HII", 2, 0, 0) + ls.wstr("Hall event")))
    line = await heard(hal)
    while line and line[0] != 21514:
        line = await heard(hal)
    check("an admin's /say is a notice", line == (21514, 0, "Server", "Hall event"), str(line))
    no_one = struct.pack("<I", gail_id) + ls.wstr("nobody") + struct.pack("<I", 5)
    check("/kick of someone not online fails", (await gail.request(13, 13041, no_one)).result != 0)
    check("/kick with someone else's character id fails",
          (await gail.request(13, 13041, struct.pack("<I", hal_id) + kick_cara[4:])).result != 0)
    check("a short /kick is refused", (await gail.request(13, 13041, struct.pack("<I", gail_id))).result != 0)
    kick_hal = struct.pack("<I", gail_id) + ls.wstr("hal") + struct.pack("<I", 5)
    check("an admin's /kick works", (await gail.request(13, 13041, kick_hal)).result == 0
          and wait_for(lambda: "hal" not in [r.split()[0] for r in ts.admin("who").splitlines() if r.strip()], 4))

    # --- chat mute (!mute, and the client's /banchat) ---
    ivy, ivy_id = await player("ivy")
    check("mute needs a time", "usage" in ts.admin("mute ivy") and "usage" in ts.admin("mute ivy perm"))
    check("mute of nobody", "no character" in ts.admin("mute nobody 10m"))
    check("mute", "muted ivy until" in ts.admin("mute ivy 10m"))
    told = await heard(ivy)
    check("the muted player is told", told and told[3].startswith("You can not chat until"), str(told))
    say(ivy, "can you hear me")
    told = await heard(ivy)
    check("a muted line is not sent", told and told[3].startswith("You can not chat") and "can you hear me" not in str([await heard(gail, 0.3) for _ in range(3)]),
          str(told))
    check("unmute", "unmuted ivy" in ts.admin("unmute ivy") and (await heard(ivy))[3] == "You can chat again")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    gail.send(ls.Message(21, 21008, body=struct.pack("<I", ivy_id) + struct.pack("<I", 5 * 60)))
    told = await heard(ivy)
    check("/banchat by character id mutes", told and told[3].startswith("You can not chat until"), str(told))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    gail.send(ls.Message(21, 21008, body=struct.pack("<I", 0) + ls.wstr("ivy") + struct.pack("<I", 0)))
    told = await heard(ivy)
    check("/banchat by name with 0 minutes unmutes", told and told[3] == "You can chat again", str(told))
    hal_gone = struct.pack("<I", ivy_id) + struct.pack("<I", 60)
    ivy.send(ls.Message(21, 21008, body=hal_gone))
    check("a player's /banchat does nothing", await heard(ivy, 0.6) is None)

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


async def squads(kim, kim_id, kim_game, lou, lou_id, lou_game):
    """/a N makes squad N of the player's side, /e N joins it, /L leaves; members show in each other's detachment."""
    async def told(gate):
        line = await heard(gate)
        return line[3] if line and line[2] == "Server" else line

    gap = ls.SQUAD_EVERY + 0.1
    lou_game.squad(10402, 3)
    check("/e of a squad nobody made", await told(lou) == "There is no squad 3: /a 3 makes it")
    kim_game.squad(10401, 3)
    check("/a makes a squad", await told(kim) == "You made squad 3 and lead it. /L leaves it")
    await asyncio.sleep(gap)
    lou_game.squad(10402, 3)
    check("/e joins it", (await told(lou) or "").startswith("You joined squad 3, led by kim (2 players)"))
    check("the squad hears who joined", await told(kim) == "lou joined squad 3")
    await asyncio.sleep(0.3)

    def leader(game, cid):
        body = game.last(30503)
        return body and (struct.unpack_from("<I", body)[0] == game.members(cid)[0],
                         struct.unpack_from("<I", body, 4)[0], ls.read_wstr(body, 8)[0])

    check("the client is told who leads its squad",
          leader(kim_game, kim_id) == (True, kim_id, "kim") and leader(lou_game, lou_id) == (True, kim_id, "kim"))
    await asyncio.sleep(gap)
    lou_game.squad(10404, None)
    check("/l says who leads", await told(lou) == "kim leads squad 3")
    check("each sees the other in their own detachment",
          kim_game.members(kim_id)[1] == [lou_id] and lou_game.members(lou_id)[1] == [kim_id],
          f"{kim_game.members(kim_id)} {lou_game.members(lou_id)}")
    check("and keeps its own detachment id", kim_game.members(kim_id)[0] != lou_game.members(lou_id)[0])

    def target(game, cid):
        body = game.last(30505)
        return body and struct.unpack("<Ii", body)[0] == game.members(cid)[0] and struct.unpack("<Ii", body)[1]

    # zones are the map's Respawn Platforms (data/maps, made by setup_data.py): battlefield 1 has one, if set up
    zones = ls.map_zones(ls.BATTLEFIELDS[1]["world"])
    await asyncio.sleep(gap)
    lou_game.squad(10407, 1)
    check("/t: only the leader sets the target", await told(lou) == "Only the squad leader (kim) sets the target")
    await asyncio.sleep(gap)
    kim_game.squad(10407, len(zones) + 1)
    check("/t of a zone the map does not have", await told(kim) == (f"Zones here are 1 to {len(zones)}" if zones
                                                                    else "This map has no zones"))
    if not zones:
        print("note: no data/maps (run setup_data.py); the target checks are skipped")
        zone1 = 1
        await asyncio.sleep(gap)
    else:
        zone1 = zones[0][0]
        await asyncio.sleep(gap)
        kim_game.squad(10407, zone1)
        await asyncio.sleep(0.3)
    check("/t 1 shows the target zone to the squad", not zones or (target(kim_game, kim_id) == zone1
          and target(lou_game, lou_id) == zone1), f"{kim_game.last(30505)} {lou_game.last(30505)}")
    say(kim, "squad hi", 21004, struct.pack("<H", 3))
    check("squad chat reaches the squad", await heard(lou) == (ls.SQUAD_CHAT, kim_id, "kim", "squad hi"))
    check("and comes back to the sender", await heard(kim) == (ls.SQUAD_CHAT, kim_id, "kim", "squad hi"))
    group = ls.group_id((ls.ALLIANCE, 3))

    def grouped(game):
        return sorted(struct.unpack_from("<I", b, 4)[0] for t, b in game.events
                      if t == 50400 and struct.unpack_from("<I", b)[0] == group)

    check("the squad is a client group on both", grouped(kim_game) == sorted([kim_id, lou_id])
          and grouped(lou_game) == sorted([kim_id, lou_id]), f"{grouped(kim_game)} {grouped(lou_game)}")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    # group chat as the client sends it: its own member list (ignored: the server uses the squad), then the text
    say(lou, "group hi", 21063, struct.pack("<III", 2, kim_id, 999))
    check("group chat reaches the squad", await heard(kim) == (ls.GROUP_CHAT, lou_id, "lou", "group hi"))
    check("and the sender", await heard(lou) == (ls.GROUP_CHAT, lou_id, "lou", "group hi"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(lou, "bad list", 21063, struct.pack("<I", 1000))
    check("a group line with a bad member list is dropped", await heard(kim, 0.4) is None)
    await asyncio.sleep(gap)
    lou_game.squad(10402, 3)
    check("joining your own squad again", await told(lou) == "You are in squad 3")
    lou_game.squad(10402, 3)
    check("changes are rate-limited", await heard(lou, 0.4) is None)
    await asyncio.sleep(gap)
    lou_game.squad(10401, 0)
    check("squad numbers start at 1", await told(lou) == f"Squads are numbered 1 to {ls.SQUAD_NUMBERS}")
    await asyncio.sleep(gap)
    lou_game.squad(10403, None)
    check("/L leaves", await told(lou) == "You left squad 3")
    check("the squad hears it", await told(kim) == "lou left squad 3")
    await asyncio.sleep(0.3)
    check("the leaver's target is cancelled", not zones or target(lou_game, lou_id) == -1)
    check("the leaver's client group goes", lou_game.last(50404) == struct.pack("<I", group)
          and kim_game.last(50401) == struct.pack("<II", group, lou_id)
          and kim_game.last(30057) == struct.pack("<II", lou_id, 0))
    check("both drop the other from their detachment",
          kim_game.members(kim_id)[1] == [] and lou_game.members(lou_id)[1] == [])
    say(lou, "anyone?", 21004, struct.pack("<H", 3))
    check("out of a squad, squad chat goes nowhere", (await told(lou) or "").startswith("You are not in a squad")
          and await heard(kim, 0.4) is None)
    def quits_self(game, cid):
        return [b for t, b in game.events if t == 30501 and struct.unpack_from("<I", b, 4)[0] == cid]
    check("nobody's own detachment is taken away", not quits_self(kim_game, kim_id) and not quits_self(lou_game, lou_id))
    await asyncio.sleep(gap)
    lou_game.squad(10402, 3)
    await told(lou)
    await told(kim)
    await asyncio.sleep(0.3)
    check("a new member is shown the target", not zones or target(lou_game, lou_id) == zone1)
    await asyncio.sleep(gap)
    lou_game.squad(10409, None)
    check("only the leader can resign", await told(lou) == "Only the squad leader (kim) can resign")
    kim_game.squad(10409, None)
    check("resign hands the squad on", await told(kim) == "kim resigned; lou leads squad 3"
          and await told(lou) == "kim resigned; you lead squad 3")
    await asyncio.sleep(0.3)
    check("both clients are told the new leader",
          leader(kim_game, kim_id) == (True, lou_id, "lou") and leader(lou_game, lou_id) == (True, lou_id, "lou"))
    await asyncio.sleep(gap)
    lou_game.squad(10409, None)
    await told(lou)
    await told(kim)
    await asyncio.sleep(gap)
    kim_game.squad(10407, ls.SQUAD_NO_TARGET)
    await asyncio.sleep(0.3)
    check("/t -1 cancels it", (target(kim_game, kim_id) == -1 and target(lou_game, lou_id) == -1) if zones
          else await told(kim) == "Your squad has no target")
    await asyncio.sleep(gap)
    kim_game.squad(10403, None)
    await told(kim)
    check("when the leader leaves, the next one leads", await told(lou) == "kim left squad 3; lou leads it now")
    await asyncio.sleep(0.3)
    check("and both clients are told", leader(lou_game, lou_id) == (True, lou_id, "lou")
          and leader(kim_game, kim_id) == (True, 0, "kim"))
    await asyncio.sleep(gap)
    lou_game.squad(10403, None)
    await told(lou)
    neo, neo_id = await player("neo")  # a fresh one: the fake clients send nothing, and go stale after STALE_PEER
    neo_game = await battle(neo, neo_id)
    await asyncio.sleep(0.5)
    neo_game.squad(10401, 1)
    line = await told(neo)
    check("players of neither side have no squads", line == "Squads are for players of a side", str(line))

    # --- an admin takes a player out of their battle; they stay online ---
    reply = await asyncio.to_thread(ts.admin, "unbattle neo afk\x07 too long")
    check("unbattle", reply.strip() == "took neo out of the battle", reply)
    check("the player is told why", await told(neo) == "You were taken out of the battle: afk too long")
    await asyncio.sleep(0.3)
    check("and gets the Record window", neo_game.last(30150) == struct.pack("<f", ls.RECORD_SECONDS))
    reply = await asyncio.to_thread(ts.admin, "unbattle neo")
    check("only once", reply.strip() == "neo is not in a battle", reply)
    check("and stays in the hall", await ts.answers(neo))


async def commanders(amy, cara):
    """The first player of a side in a match commands it; !order in battle chat reaches only that side."""
    def leader(body):
        return body and (struct.unpack_from("<i", body)[0], ls.read_wstr(body, 4)[0] if len(body) > 4 else None)

    kim, kim_id = await player("kim", ls.ALLIANCE)
    lou, lou_id = await player("lou", ls.ALLIANCE)
    kim_game = await battle(kim, kim_id)
    await asyncio.sleep(0.3)
    lou_game = await battle(lou, lou_id)  # MW_MATCH_PLAYERS is 2: a match of their own, one side
    await asyncio.sleep(0.5)
    check("the first player of a side commands it", leader(kim_game.last(30020)) == (kim_id, "kim"))
    check("the next one is told who commands", leader(lou_game.last(30020)) == (kim_id, "kim"))
    say(lou, "!order charge", 21052)
    check("only the commander gives orders", (await heard(lou))[3] == "Only your side's commander can give orders")
    say(kim, "!order hold the  bridge", 21004, struct.pack("<H", 5))
    await asyncio.sleep(0.5)
    want = ls.order_body("hold the bridge")
    check("an order reaches the commander's side, cleaned", kim_game.last(30030) == want and lou_game.last(30030) == want)
    check("and is not also a chat line", await heard(lou, 0.3) is None)
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(kim, "!order " + "x" * 300, 21052)
    check("orders are rate-limited", (await heard(kim))[3].startswith("One order every"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(amy, "!ORDER go", 21052)
    check("a player of neither side commands nobody", (await heard(amy))[3] == "Only your side's commander can give orders")
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(cara, "!order go")
    check("no orders from the hall", (await heard(cara))[3] == "You are not in a battle")
    await squads(kim, kim_id, kim_game, lou, lou_id, lou_game)
    kim.close()  # logs out: out of the battle too
    await asyncio.sleep(1.0)
    check("when the commander leaves, the next player commands", leader(lou_game.last(30020)) == (lou_id, "lou"))
    await asyncio.sleep(ls.CHAT_EVERY + 0.1)
    say(lou, "!order " + "y" * 300, 21052)
    await asyncio.sleep(0.5)
    check("an order is cut to ORDER_MAX", lou_game.last(30030) == ls.order_body("y" * ls.ORDER_MAX))


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
