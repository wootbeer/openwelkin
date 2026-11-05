# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_db.py -- the SQLite storage: everything survives a restart, old JSON files are imported once, a character name
is taken only once, and hostile text stays plain text. Runs in a temporary folder."""
import json
import os
import re
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TOOLS))
root = tempfile.mkdtemp(prefix="mw-db-")
os.environ["MW_STATE_DIR"] = root
import db  # noqa: E402
import loginserver as ls  # noqa: E402

fails = []
EVIL = "x'); DROP TABLE characters;--"


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(name)


def reopen():
    """What a restart sees."""
    ls.STORE.flush()
    ls.STORE.con.close()
    ls.STORE.reader.close()
    ls.STORE = ls.Store()


def counts(table):
    return ls.STORE.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def characters():
    cid = ls.STORE.create("alice", "Alpha", gender=1, model=32, state=1, raw="00ff")
    check("second use of a name is refused", ls.STORE.create("bob", "ALPHA", gender=0, model=0, state=1, raw="") is None)
    ls.STORE.add_stats(cid, kills=2, money=-50, battle_time=1.5)
    ls.STORE.add_log(cid, "first")
    ls.STORE.add_log(cid, EVIL)
    ls.STORE.buy(cid, {"goods": 3, "item": 3103, "days": 30, "expires": 0, "active": False}, "money", 100)
    ls.STORE.activate(cid, 1)
    talent = next(iter(ls.TALENTS))
    ls.STORE.learn_talent(cid, talent, 5)
    before = json.dumps(ls.STORE.by_id(cid), sort_keys=True)
    reopen()
    check("a character survives a restart unchanged", json.dumps(ls.STORE.by_id(cid), sort_keys=True) == before,
          f"{before}\n{ls.STORE.by_id(cid)}")
    check("money stays a whole number", isinstance(ls.STORE.stats(cid)["money"], int))
    check("ids carry on after a restart", ls.STORE.create("bob", "Bravo", gender=0, model=0, state=1, raw="") == cid + 1)
    check("names are remembered after a restart", ls.STORE.name_taken("alpha"))
    ls.STORE.add_stats(cid, talent_reset=0)
    money = ls.STORE.stats(cid)["money"]
    check("forgetting talents works", ls.STORE.reset_talents(cid) == 0 and not ls.STORE.talents(cid))
    check("and costs crystal", ls.STORE.stats(cid)["money"] == money - ls.TALENT_RESET_CRYSTAL)
    check("and goes back to the basic robot", "robot" not in ls.STORE.by_id(cid))
    reopen()
    check("forgotten talents stay forgotten", not ls.STORE.talents(cid))
    check("no talents, nothing to forget", ls.STORE.reset_talents(cid) == 14005)
    ls.STORE.learn_talent(cid, talent, 5)
    check("forgetting again has to wait", ls.STORE.reset_talents(cid) == 14003 and ls.STORE.talents(cid))
    ls.STORE.add_stats(cid, talent_reset=-ls.TALENT_RESET_WAIT * 2 - ls.STORE.stats(cid)["talent_reset"])
    ls.STORE.add_stats(cid, money=-ls.STORE.stats(cid)["money"])
    check("forgetting needs the crystal", ls.STORE.reset_talents(cid) == 24010 and ls.STORE.talents(cid))
    check("a name finds its character after a restart", ls.STORE.id_of_name("ALPHA") == cid)
    ls.TOP_EVERY = 0
    ls.STORE.add_stats(cid + 1, kills=5)
    top = ls.STORE.top("kills")
    check("rankings sort best first", [r[0] for r in top[:2]] == [cid + 1, cid] and top[0][3] == 5, str(top))
    ls.TOP_EVERY = 60
    ls.STORE.add_stats(cid, kills=50)
    ls.STORE.top("kills")
    check("and are kept for a while", ls.STORE.top("kills")[0][0] == cid + 1)
    ls.TOP_EVERY = 0
    check("today counts too", ls.STORE.stats(cid)["kills_day"] == 52 and ls.STORE.stats(cid)["kills_month"] == 52)
    day = ls.STORE.top("kills", "day")
    check("a period ranks what was earned in it", [r[:4:3] for r in day[:2]] == [(cid, 52), (cid + 1, 5)], str(day))
    ls.STORE.add_stats(cid + 1, kills=-ls.STORE.stats(cid + 1)["kills"], talent_reset=0)
    ls.STORE.add_stats(cid, money=3)
    check("other stats leave the periods alone", ls.STORE.stats(cid)["kills_day"] == 52)
    with ls.STORE.lock:  # as if the kills were yesterday
        ls.STORE.by_id(cid)["stats"]["at_day"] -= 1
        ls.STORE.mark_dirty(cid, stats={"at_day"})
    check("yesterday is not today, and nothing earned is not listed", ls.STORE.top("kills", "day") == [],
          str(ls.STORE.top("kills", "day")))
    ls.STORE.add_stats(cid, kills=1)
    reopen()
    st = ls.STORE.stats(cid)
    check("a new day starts again", st["kills_day"] == 1 and st["kills_week"] == 53 and st["at_day"] == ls.period_now("day"),
          str(st))
    check("and a new period is saved as such", ls.STORE.top("kills", "day")[0][:4:3] == (cid, 1))
    ls.STORE.add_stats(cid, weapon3101=2)
    check("kills with a weapon list only who made them", [r[:4:3] for r in ls.STORE.top("weapon3101")] == [(cid, 2)]
          and [r[:4:3] for r in ls.STORE.top("weapon3101", "week")] == [(cid, 2)])
    ls.STORE.by_id(cid)["stats"]["at_week"] -= 1
    ls.STORE.add_stats(cid, honor=1)
    check("a new week starts weapon kills again too", ls.STORE.stats(cid)["weapon3101_week"] == 0
          and ls.STORE.top("weapon3101", "week") == [] and ls.STORE.stats(cid)["weapon3101"] == 2)
    ls.TOP_EVERY = 60
    with ls.STORE.lock:  # a whole-character change waiting for the batch, then a stats-only one on top of it
        ls.STORE.by_id(cid)["state"] = 7
        ls.STORE.mark_dirty(cid)
    ls.STORE.add_stats(cid, honor=2)
    honor = ls.STORE.stats(cid)["honor"]
    reopen()
    check("a stats change does not hide another waiting change", ls.STORE.by_id(cid)["state"] == 7
          and ls.STORE.stats(cid)["honor"] == honor)
    ls.STORE.add_stats(cid, kills=1)
    check("a stats-only batch writes only those stats", ls.STORE.changed == {cid: {"kills", "kills_day", "kills_week",
                                                                                  "kills_month"}}, str(ls.STORE.changed))
    reopen()
    check("and they are saved", ls.STORE.stats(cid)["kills_day"] == 2)
    ls.STORE.set_contact(cid, "friends", cid + 1, True)
    ls.STORE.set_contact(cid, "blocked", cid + 1, True)
    ls.STORE.set_contact(cid, "friends", cid + 1, True)
    reopen()
    check("friend and block lists survive a restart", ls.STORE.contacts(cid, "friends") == [cid + 1]
          and ls.STORE.contacts(cid, "blocked") == [cid + 1])
    ls.STORE.set_contact(cid, "friends", cid + 1, False)
    reopen()
    check("a removed friend stays removed", not ls.STORE.contacts(cid, "friends"))
    for n in range(ls.MAX_CONTACTS):
        ls.STORE.set_contact(cid, "blocked", 100000 + n, True)
    check("the lists have a limit", len(ls.STORE.contacts(cid, "blocked")) == ls.MAX_CONTACTS)
    with ls.STORE.lock:
        ls.STORE.by_id(cid).setdefault("items", []).append({"item": 3101, "days": 0, "expires": 0, "active": True})
    check("an item goes in use", ls.STORE.set_in_use(cid, 3101, 7))
    reopen()
    check("its in-use slot survives a restart", [it.get("equipped") for it in ls.STORE.items(cid)
                                                 if it["item"] == 3101 and it.get("active")][-1:] == [7])
    ls.STORE.save_suite(cid, 7, "Fast 1311 0 0 0 2 3101 3102 0")
    ls.STORE.save_suite(cid, 8, EVIL)
    ls.STORE.save_suite(cid, 7, "Faster 1311 0 0 0 0 0")
    reopen()
    check("loadout presets survive a restart", ls.STORE.suites(cid) == {7: "Faster 1311 0 0 0 0 0", 8: EVIL})
    for n in range(ls.MAX_SUITES):
        ls.STORE.save_suite(cid, 100 + n, "x")
    check("presets have a limit", len(ls.STORE.suites(cid)) == ls.MAX_SUITES
          and ls.STORE.save_suite(cid, 7, "changed"), str(len(ls.STORE.suites(cid))))
    for n in range(ls.LOG_KEEP + 20):
        ls.STORE.add_log(cid, f"line {n}")
    reopen()
    log_ = ls.STORE.event_log(cid)
    check("the event log keeps the newest lines", len(log_) == ls.LOG_KEEP and log_[-1][2] == f"line {ls.LOG_KEEP + 19}")
    check("the database trims it too", counts("event_log") == ls.LOG_KEEP)


def hostile():
    """Text a player controls, sent straight to the storage calls (the handlers refuse most of it earlier)."""
    cid = ls.STORE.create(EVIL, EVIL, gender=0, model=0, state=1, raw="")
    ls.STORE.add_log(cid, EVIL)
    reopen()
    check("hostile character text is stored as text", ls.STORE.by_id(cid)["name"] == EVIL and ls.STORE.account_of(cid) == EVIL)
    check("the characters table is still there", counts("characters") >= 3)
    check("hostile login makes one plain account", ls.ACCOUNTS.check(EVIL, b"pw") == "created" and ls.ACCOUNTS.exists(EVIL))
    check("' OR '1'='1 finds nothing", not ls.ACCOUNTS.exists("' OR '1'='1"))
    ls.BANS.add("accounts", EVIL, 0, EVIL, EVIL)
    ls.BANS = ls.Bans()
    check("a ban survives a restart", ls.BANS.account(EVIL)["reason"] == EVIL)
    check("unban", ls.BANS.remove("accounts", EVIL) and not ls.Bans().account(EVIL))
    source = open(db.__file__, encoding="utf-8").read() + open(ls.__file__, encoding="utf-8").read()
    built = re.findall(r'(?:execute|executemany)\(f["\']|(?:execute|executemany)\([^)\n]*["\'] *[%+]', source)
    check("no query is built from strings", not built, str(built))


def accounts():
    check("new account", ls.ACCOUNTS.check("erin", b"pw") == "created")
    check("right password", ls.ACCOUNTS.check("erin", b"pw") == "ok")
    check("wrong password", ls.ACCOUNTS.check("erin", b"no") == "bad")
    check("role", ls.ACCOUNTS.set_role("erin", "gm") and ls.ACCOUNTS.role("erin") == "gm")
    check("role of nobody", not ls.ACCOUNTS.set_role("nobody", "gm") and ls.ACCOUNTS.role("nobody") == "player")


def rollback():
    """A failed save loses nothing: the change is kept and written by the next one."""
    cid = ls.STORE.create("carl", "Charlie", gender=0, model=0, state=1, raw="")
    ls.STORE.add_stats(cid, kills=7)
    good, ls.STORE.lines = ls.STORE.lines, [(999999, 0, 0, "no such character")]
    try:
        ls.STORE.flush()
        check("a bad save fails", False)
    except db.Error:
        pass
    ls.STORE.lines = good
    reopen()
    check("a failed save loses nothing", ls.STORE.stats(cid).get("kills") == 7)


def loading():
    """Only accounts in use are in memory; the rest is read from characters.db when needed."""
    ids = [ls.STORE.create(f"many{n}", f"Many{n}", gender=0, model=0, state=1, raw="") for n in range(5)]
    ls.STORE.add_stats(ids[3], kills=1000)
    reopen()
    check("nothing is loaded at start", not ls.STORE.data["accounts"] and ls.STORE.name_taken("many2"))
    check("a name is found without loading", ls.STORE.id_of_name("Many2") == ids[2] and not ls.STORE.data["accounts"])
    check("ids carry on without loading", ls.STORE.create("late", "Late", gender=0, model=0, state=1, raw="") > ids[4])
    top = ls.STORE.top("kills")
    check("rankings include characters not loaded", top[0][:2] == (ids[3], "Many3") and "many3" not in ls.STORE.used,
          str(top[:2]))
    check("a character loads when used", ls.STORE.by_id(ids[1])["name"] == "Many1" and "many1" in ls.STORE.used)
    check("an account loads when used", [c["id"] for c in ls.STORE.characters("many4")] == [ids[4]])
    keep, ls.CACHE_ACCOUNTS = ls.CACHE_ACCOUNTS, 2
    ls.STORE.add_stats(ids[1], kills=1)  # not saved yet: must stay
    ls.STORE.by_id(ids[0])
    ls.STORE.by_id(ids[2])
    check("old accounts are dropped, unsaved ones kept", sorted(ls.STORE.used) == ["many1", "many2"],
          str(list(ls.STORE.used)))
    check("a dropped character loads again", ls.STORE.stats(ids[4]).get("kills", 0) == 0
          and ls.STORE.stats(ids[1])["kills"] == 1)
    ls.CACHE_ACCOUNTS = keep
    reopen()
    check("changes to a kept account are saved", ls.STORE.stats(ids[1])["kills"] == 1)
    check("an unknown id loads nothing", ls.STORE.by_id(99999999) is None and ls.STORE.characters("nobody") == [])


def importing():
    folder = os.path.join(root, "old")
    os.makedirs(folder)
    salt = os.urandom(16)
    with open(os.path.join(folder, "accounts.json"), "w", encoding="utf-8") as f:
        json.dump({"old": {"salt": salt.hex(), "hash": ls.Accounts.hash(b"pw", salt).hex(), "created": 5, "role": "gm"}}, f)
    with open(os.path.join(folder, "bans.json"), "w", encoding="utf-8") as f:
        json.dump({"accounts": {"old": {"until": 0, "reason": "r", "by": "me", "at": 1}}, "ips": {}}, f)
    old = {"id": 1000, "name": "tester", "gender": 0, "model": 32, "state": 1, "raw": "00", "robot": 1311,
           "stats": {"kills": 11, "money": 97755, "battle_time": 2258.9},
           "items": [{"item": 3101, "expires": 1795053302, "goods": 1, "active": True, "days": 30}],
           "talents": {"5": 1}, "log": [[1792513811, 0, "Bought Viper"]]}
    with open(os.path.join(folder, "characters.json"), "w", encoding="utf-8") as f:
        json.dump({"next_id": 1001, "accounts": {"old": [old]}}, f)
    con = db.open_characters(os.path.join(folder, "characters.db"))
    data = db.load_characters(con)
    con.close()
    check("old characters are imported", data["accounts"]["old"][0] == old and data["next_id"] == 1001,
          str(data["accounts"]["old"][0]))
    con = db.open_accounts(os.path.join(folder, "accounts.db"))
    check("old accounts are imported", con.execute("SELECT role FROM accounts WHERE name = 'old'").fetchone() == ("gm",))
    check("old bans are imported", db.load_bans(con)["accounts"]["old"]["reason"] == "r")
    con.close()
    check("old files are kept, renamed", sorted(f for f in os.listdir(folder) if f.endswith(".imported")) == [
        "accounts.json.imported", "bans.json.imported", "characters.json.imported"])
    bad = os.path.join(root, "bad")
    os.makedirs(bad)
    with open(os.path.join(bad, "characters.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    try:
        db.open_characters(os.path.join(bad, "characters.db"))
        check("a broken old file stops the server", False)
    except ValueError:
        check("a broken old file stops the server and stays", os.path.exists(os.path.join(bad, "characters.json")))


def main():
    try:
        characters()
        hostile()
        accounts()
        rollback()
        loading()
        importing()
    finally:
        ls.STORE.con.close()
        ls.STORE.reader.close()
        ls.ACCOUNTS.con.close()
        ls.BANS.con.close()
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(fails)} failed" if fails else "all ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
