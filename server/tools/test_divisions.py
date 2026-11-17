# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_divisions.py -- start a server in a temporary folder with fake players and walk through divisions (the
client's Team): make one, apply, agree, refuse, kick, leave, dismiss. Names a player types are checked like character
names, only the leader may manage, only the right side may join, and divisions are still there after a restart."""
import asyncio
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time

import test_split as ts
from test_chat import player

import loginserver as ls  # noqa: E402

check, wait_for = ts.check, ts.wait_for
U32 = struct.Struct("<I")


async def act(gate, type_, body=b"", catalog=15):
    """A request that changes something: players may make one every CHAT_EVERY seconds."""
    await asyncio.sleep(ls.CHAT_EVERY + 0.05)
    return await gate.request(catalog, type_, body)


async def create(gate, cid, name, short, extra=""):
    return await act(gate, 15003, U32.pack(cid) + ls.wstr(name) + ls.wstr(short) + ls.wstr(extra))


def info(r):
    """The fields of a (15,15023) reply that matter here."""
    name, off = ls.read_wstr(r.body, 0)
    did, level, _, _ = struct.unpack_from("<IBBB", r.body, off)
    _, off = ls.read_wstr(r.body, off + 7)
    created, off = ls.read_wstr(r.body, off)
    leader = U32.unpack_from(r.body, off)[0]
    leader_name, off = ls.read_wstr(r.body, off + 4)
    members, online = struct.unpack_from("<II", r.body, off)
    _, off = ls.read_wstr(r.body, off + 8)
    short, off = ls.read_wstr(r.body, off)
    points, limit, money = struct.unpack_from("<III", r.body, off)
    return dict(name=name, id=did, leader=leader, leader_name=leader_name, members=members, online=online,
                short=short, limit=limit, created=created, end=off + 24 == len(r.body))


def ids_of(r, first_field=True):
    """Character or division ids of a paged list reply (u32 total | u32 n | rows that start with a u32 id)."""
    total, n = struct.unpack_from("<II", r.body)
    out, off = [], 8
    for _ in range(n):
        out.append(U32.unpack_from(r.body, off)[0])
        off = row_end(r.body, off, r.type)
    return total, out


def row_end(b, off, type_):
    if type_ == 13031:  # u32 | str | u8 | str | u8
        _, off = ls.read_wstr(b, off + 4)
        _, off = ls.read_wstr(b, off + 1)
        return off + 1
    if type_ == 15021:  # u32 | str | str | u32 | str | u32 x3
        _, off = ls.read_wstr(b, off + 4)
        _, off = ls.read_wstr(b, off)
        _, off = ls.read_wstr(b, off + 4)
        return off + 12
    if type_ == 15013:  # u32 u32 | str x3 | u8
        off += 8
        for _ in range(3):
            _, off = ls.read_wstr(b, off)
        return off + 1
    raise ValueError(type_)


async def division_of(gate, cid):
    """The division id CharacterOnline gives (character +0x40)."""
    r = await gate.request(13, 13004, U32.pack(cid))
    return struct.unpack_from("<IBII", r.body)[3]


async def pushed(gate, seconds=1.5):
    """The division id of the next TeamChange (21,21074) push, or None."""
    try:
        while True:
            m = await asyncio.wait_for(gate.recv(), seconds)
            if (m.catalog, m.type) == (21, 21074):
                cid, _, did = struct.unpack("<III", m.body)
                return did
    except asyncio.TimeoutError:
        return None


async def application(gate, cid):
    """(division id, state) of my application, or None."""
    r = await gate.request(15, 15012, U32.pack(cid))
    if not U32.unpack_from(r.body)[0]:
        return None
    did = U32.unpack_from(r.body, 4)[0]
    off = 12
    for _ in range(6):
        _, off = ls.read_wstr(r.body, off)
    return did, r.body[off]


async def scenario():
    amy, amy_id = await player("amy", ls.FEDERAL)
    ben, ben_id = await player("ben", ls.FEDERAL)
    cara, cara_id = await player("cara", ls.FEDERAL)
    dan, dan_id = await player("dan", ls.ALLIANCE)

    # --- make one ---
    check("no division at first", await division_of(amy, amy_id) == 0)
    r = await create(amy, amy_id, "Red Wolves", "RW")
    check("a division is made", r.result == 0 and len(r.body) == 4, r.result)
    did = U32.unpack(r.body)[0]
    check("the maker is in it at the next login", await division_of(amy, amy_id) == did)
    r = await amy.request(15, 15023, U32.pack(did))
    i = info(r)
    check("its info reads to the end", r.result == 0 and i["end"], i)
    check("its info has name, short name, leader and one member",
          (i["name"], i["short"], i["id"], i["leader"], i["leader_name"], i["members"], i["online"])
          == ("Red Wolves", "RW", did, amy_id, "amy", 1, 1), i)
    check("a new division has the member limit", i["limit"] == ls.DIVISION_LIMIT)
    check("no info for a division that does not exist", (await amy.request(15, 15023, U32.pack(did + 99))).result
          == ls.ERR_NO_DIVISION)
    check("a member can not make a second division", (await create(amy, amy_id, "Other One", "OO")).result
          == ls.ERR_IN_DIVISION)

    # --- names: like character names, nothing a player types reaches SQL or looks like another name ---
    bad = {"Red Wolves": ls.ERR_DIVISION_TAKEN, "red wolves": ls.ERR_DIVISION_TAKEN, "Red Wo1ves": ls.ERR_DIVISION_TAKEN,
           "Rеd Wolves": ls.ERR_DIVISION_NAME,  # Cyrillic е among Latin letters
           "abc": ls.ERR_DIVISION_NAME_LEN, "x" * 17: ls.ERR_DIVISION_NAME_LEN,
           "x'); DROP TABLE divisions;--": ls.ERR_DIVISION_NAME_LEN, "Bad;Name": ls.ERR_DIVISION_NAME,
           "Two  Spaces": ls.ERR_DIVISION_NAME, " Lead": ls.ERR_DIVISION_NAME, "Tab\tName": ls.ERR_DIVISION_NAME,
           "Admin Corps": ls.ERR_DIVISION_NAME_WORD, "Server": ls.ERR_DIVISION_NAME_WORD,
           "Ｗｉｄｅ Name": ls.ERR_DIVISION_NAME, "ᅟᅟᅟᅟ": ls.ERR_DIVISION_NAME}
    for name, code in bad.items():
        r = await create(ben, ben_id, name, "BN")
        check(f"name {name!r} is refused with {code}", r.result == code, r.result)
    for short, code in {"RW": ls.ERR_DIVISION_TAKEN, "R": ls.ERR_DIVISION_SHORT_LEN, "TOOLONG": ls.ERR_DIVISION_SHORT_LEN,
                        "A'B": ls.ERR_DIVISION_NAME, "GM": ls.ERR_DIVISION_SHORT_WORD}.items():
        r = await create(ben, ben_id, "Blue Hawks", short)
        check(f"short name {short!r} is refused with {code}", r.result == code, r.result)
    check("a cut-off name is refused", (await act(ben, 15003, U32.pack(ben_id) + b"\x09ab")).result == ls.ERR_DIVISION_NAME)
    check("two changes at once: the second is refused",
          [r.result for r in await asyncio.gather(create(ben, ben_id, "Too Fast", "TF"),
                                                  ben.request(15, 15003, U32.pack(ben_id) + ls.wstr("Faster") + ls.wstr("FS")))]
          .count(ls.ERR_UNKNOWN) == 1)
    await asyncio.sleep(ls.CHAT_EVERY)
    with sqlite3.connect(os.path.join(ts.root, "login", "characters.db")) as con:
        names = {n for (n,) in con.execute("SELECT name FROM divisions")}
    check("only good names were saved", names <= {"Red Wolves", "Too Fast", "Faster"} and "Red Wolves" in names, names)
    await act(ben, 15005, U32.pack(ben_id) + U32.pack(await division_of(ben, ben_id)))  # undo the fast one

    # --- the list and applying ---
    r = await ben.request(15, 15021, struct.pack("<III", ben_id, 0, 50))
    check("the list shows the division to its side", ids_of(r) == (1, [did]), ids_of(r))
    r = await dan.request(15, 15021, struct.pack("<III", dan_id, 0, 50))
    check("the list does not show it to the other side", ids_of(r) == (0, []), ids_of(r))
    check("the other side can not apply", (await act(dan, 15011, struct.pack("<IIB", dan_id, did, 0))).result
          == ls.ERR_DIVISION_NATION)
    check("no application to a division that does not exist",
          (await act(ben, 15011, struct.pack("<IIB", ben_id, did + 99, 0))).result == ls.ERR_NO_DIVISION)
    r = await act(ben, 15011, struct.pack("<IIB", ben_id, did, 0))
    check("a player of the same side applies", r.result == 0 and U32.unpack_from(r.body)[0] == did
          and ls.read_wstr(r.body, 4)[0] == "Red Wolves", r.result)
    check("applying twice is refused", (await act(ben, 15011, struct.pack("<IIB", ben_id, did, 0))).result
          == ls.ERR_ALREADY_APPLIED)
    check("the applicant sees the application waiting", await application(ben, ben_id) == (did, 0))
    check("the leader sees one waiting", U32.unpack((await amy.request(15, 15066, U32.pack(did))).body)[0] == 1)
    check("others see none waiting", U32.unpack((await ben.request(15, 15066, U32.pack(did))).body)[0] == 0)
    r = await amy.request(15, 15013, struct.pack("<III", did, 0, 100000))
    check("the leader lists the applicant", ids_of(r) == (1, [ben_id]), ids_of(r))
    check("others can not list the applications", (await ben.request(15, 15013, struct.pack("<III", did, 0, 9))).result
          == ls.ERR_NOT_YOUR_DIVISION)
    check("an applicant can not take itself in",
          (await act(ben, 15015, struct.pack("<IIIB", ben_id, did, ben_id, 1))).result == ls.ERR_NOT_LEADER)

    # --- agree: the applicant joins at once and is told ---
    r = await act(amy, 15015, struct.pack("<IIIB", amy_id, did, ben_id, 1))
    check("the leader agrees", r.result == 0, r.result)
    check("the applicant hears it joined", await pushed(ben) == did)
    check("the new member is in it at the next login", await division_of(ben, ben_id) == did)
    check("its application is gone", await application(ben, ben_id) is None)
    r = await amy.request(13, 13031, struct.pack("<III", did, 0, 100000))
    check("the member list has both, the leader first", ids_of(r) == (2, [amy_id, ben_id]), ids_of(r))
    check("both are online", U32.unpack((await amy.request(15, 15062, U32.pack(did))).body)[0] == 2)
    check("a list page past the end is empty", ids_of(await amy.request(13, 13031, struct.pack("<III", did, 5, 9)))
          == (2, []))

    # --- refuse ---
    await act(cara, 15011, struct.pack("<IIB", cara_id, did, 0))
    check("the leader refuses", (await act(amy, 15015, struct.pack("<IIIB", amy_id, did, cara_id, 0))).result == 0)
    check("the refused applicant sees it", await application(cara, cara_id) == (did, 2))
    check("a refused applicant does not join", await division_of(cara, cara_id) == 0)
    check("a refused applicant can not confirm", (await act(cara, 15016, struct.pack("<II", cara_id, 0))).result
          == ls.ERR_NO_APPLICATION)
    check("the client clears a refused application", (await act(cara, 15014, struct.pack("<II", cara_id, 0))).result == 0
          and await application(cara, cara_id) is None)

    # --- other divisions stay private ---
    r = await create(cara, cara_id, "Blue Hawks", "BH")
    other = U32.unpack(r.body)[0]
    check("a second division is made", r.result == 0 and other != did, r.result)
    check("you can not read another division's members",
          (await amy.request(13, 13031, struct.pack("<III", other, 0, 9))).result == ls.ERR_NOT_YOUR_DIVISION)
    check("you can not answer for another division",
          (await act(cara, 15015, struct.pack("<IIIB", cara_id, did, ben_id, 0))).result == ls.ERR_NO_APPLICATION)

    # --- kick, leave ---
    check("a member can not kick", (await act(ben, 15076, struct.pack("<III", ben_id, did, amy_id))).result
          == ls.ERR_NOT_LEADER)
    check("the leader can not kick someone of another division",
          (await act(amy, 15076, struct.pack("<III", amy_id, did, cara_id))).result == ls.ERR_NOT_YOUR_DIVISION)
    check("the leader kicks", (await act(amy, 15076, struct.pack("<III", amy_id, did, ben_id))).result == 0)
    check("the kicked member hears it", await pushed(ben) == 0)
    check("the kicked member is out", await division_of(ben, ben_id) == 0)
    check("leaving with no division is refused", (await act(ben, 15018, U32.pack(ben_id))).result
          == ls.ERR_NOT_YOUR_DIVISION)
    await act(ben, 15011, struct.pack("<IIB", ben_id, did, 0))
    await act(amy, 15015, struct.pack("<IIIB", amy_id, did, ben_id, 1))
    await pushed(ben)
    check("a member leaves", (await act(ben, 15018, U32.pack(ben_id))).result == 0
          and await division_of(ben, ben_id) == 0)
    check("the leader can not leave", (await act(amy, 15018, U32.pack(amy_id))).result == ls.ERR_LEADER_STAYS)

    # --- bad requests: short bodies and unknown players get an error, never a crash ---
    stranger = await ts.gate_session((await ts.login("nochar"))[1])
    check("a player with no character can not make one",
          (await stranger.request(15, 15003, U32.pack(amy_id) + ls.wstr("Sneaky") + ls.wstr("SN"))).result == ls.ERR_UNKNOWN)
    for type_, catalog in ((15003, 15), (15011, 15), (15015, 15), (15076, 15), (15005, 15), (15023, 15), (13031, 13),
                           (15021, 15), (15013, 15), (15062, 15), (15066, 15), (15012, 15), (15016, 15), (15014, 15)):
        await asyncio.sleep(ls.CHAT_EVERY + 0.05)
        amy.send(ls.Message(catalog, type_, token=900, flags=ls.MSG_REQUEST, body=b"\x01"))
    await asyncio.sleep(0.3)
    check("the server still answers after short requests", (await amy.request(15, 15023, U32.pack(did))).result == 0)

    # --- dismiss ---
    check("a member of nothing can not dismiss", (await act(ben, 15005, struct.pack("<II", ben_id, did))).result
          == ls.ERR_NOT_LEADER)
    check("a leader can not dismiss another division",
          (await act(amy, 15005, struct.pack("<II", amy_id, other))).result == ls.ERR_NOT_LEADER
          and (await amy.request(15, 15023, U32.pack(other))).result == 0)
    check("the leader dismisses", (await act(amy, 15005, struct.pack("<II", amy_id, did))).result == 0)
    check("the leader hears it is gone", await pushed(amy) == 0)
    check("it is gone", (await amy.request(15, 15023, U32.pack(did))).result == ls.ERR_NO_DIVISION
          and await division_of(amy, amy_id) == 0)
    r = await create(amy, amy_id, "Red Wolves", "RW")
    check("its name is free again", r.result == 0, r.result)
    return U32.unpack(r.body)[0], other, amy_id, cara_id


def start(env):
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(ts.SERVER, "loginserver.py")], env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=ts.pump, args=(proc,), daemon=True).start()
    return proc


async def after_restart(did, other, amy_id, cara_id):
    amy = await ts.gate_session((await ts.login("amy"))[1])
    cara = await ts.gate_session((await ts.login("cara"))[1])
    check("after a restart the leader is still in it", await division_of(amy, amy_id) == did)
    check("and the other division's leader too", await division_of(cara, cara_id) == other)
    i = info(await amy.request(15, 15023, U32.pack(did)))
    check("after a restart the info is the same", (i["name"], i["short"], i["leader"], i["members"])
          == ("Red Wolves", "RW", amy_id, 1), i)
    check("after a restart names are still taken", (await create(amy, amy_id, "Blue Hawks", "BH")).result
          in (ls.ERR_DIVISION_TAKEN, ls.ERR_IN_DIVISION))


def main():
    env = dict(os.environ, MW_STATE_DIR=os.path.join(ts.root, "login"))
    os.makedirs(env["MW_STATE_DIR"], exist_ok=True)
    proc = start(env)
    try:
        check("the server starts", wait_for(lambda: ts.seen("admin commands on"), 15))
        kept = asyncio.run(scenario())
        proc.kill()
        proc.wait()
        ts.lines.clear()
        proc = start(env)
        check("the server starts again", wait_for(lambda: ts.seen("admin commands on"), 15))
        asyncio.run(after_restart(*kept))
    finally:
        proc.kill()
        time.sleep(0.5)
        shutil.rmtree(ts.root, ignore_errors=True)
    print(f"{len(ts.fails)} failed" if ts.fails else "all ok")
    return 1 if ts.fails else 0


if __name__ == "__main__":
    sys.exit(main())
