# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""db.py -- the two SQLite databases. accounts.db (accounts, bans) belongs to the login server, characters.db to the
gate; a server never opens the other's file, and game servers open neither.

Every value goes into a query as a bound parameter (?), never into the SQL text: nothing a player types can change a
query. Old JSON save files are imported once, into an empty database, and renamed to *.imported."""
import json
import os
import sqlite3

Error = sqlite3.Error

ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL, created INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'player');
CREATE TABLE IF NOT EXISTS bans (
    kind TEXT NOT NULL, target TEXT NOT NULL, until INTEGER NOT NULL, reason TEXT NOT NULL, banned_by TEXT NOT NULL,
    banned_at INTEGER NOT NULL, PRIMARY KEY (kind, target));
"""

CHARACTERS = """
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
    gender INTEGER NOT NULL, model INTEGER NOT NULL, state INTEGER NOT NULL, raw TEXT NOT NULL, robot INTEGER);
CREATE INDEX IF NOT EXISTS characters_account ON characters (account);
CREATE TABLE IF NOT EXISTS stats (
    character_id INTEGER NOT NULL REFERENCES characters (id), stat TEXT NOT NULL, value NUMERIC NOT NULL,
    PRIMARY KEY (character_id, stat));
CREATE TABLE IF NOT EXISTS items (
    character_id INTEGER NOT NULL REFERENCES characters (id), slot INTEGER NOT NULL, goods INTEGER, item INTEGER NOT NULL,
    days INTEGER NOT NULL, expires INTEGER NOT NULL, active INTEGER NOT NULL, equipped INTEGER NOT NULL,
    PRIMARY KEY (character_id, slot));
CREATE TABLE IF NOT EXISTS talents (
    character_id INTEGER NOT NULL REFERENCES characters (id), talent TEXT NOT NULL, level INTEGER NOT NULL,
    PRIMARY KEY (character_id, talent));
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters (id), at INTEGER NOT NULL,
    kind INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_log_character ON event_log (character_id, id);
"""


def backup(path, copy):
    """Copy a database while the server is using it."""
    source, target = sqlite3.connect(path), sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def connect(path, schema):
    """One connection per database, used from many threads: the caller holds its lock around every use."""
    con = sqlite3.connect(path, timeout=5, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")  # a crash never leaves a half-written database
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(schema)
    con.execute("PRAGMA user_version=1")
    return con


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def retire(path):
    os.replace(path, path + ".imported")


def open_accounts(path):
    con = connect(path, ACCOUNTS)
    folder = os.path.dirname(path)
    old = os.path.join(folder, "accounts.json")
    if os.path.exists(old) and not con.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        with con:
            con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                            [(name, r["salt"], r["hash"], r.get("created", 0), r.get("role", "player"))
                             for name, r in read_json(old).items()])
        retire(old)
    old = os.path.join(folder, "bans.json")
    if os.path.exists(old) and not con.execute("SELECT 1 FROM bans LIMIT 1").fetchone():
        data = read_json(old)
        with con:
            con.executemany("INSERT INTO bans VALUES (?, ?, ?, ?, ?, ?)",
                            [(kind, key, r["until"], r["reason"], r["by"], r["at"])
                             for kind in ("accounts", "ips") for key, r in data.get(kind, {}).items()])
        retire(old)
    return con


def load_bans(con):
    data = {"accounts": {}, "ips": {}}
    for kind, target, until, reason, by, at in con.execute("SELECT * FROM bans"):
        data[kind][target] = {"until": until, "reason": reason, "by": by, "at": at}
    return data


def write_character(con, account, c):
    """Save everything but the event log (call inside a transaction)."""
    con.execute("INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "state = excluded.state, robot = excluded.robot",
                (c["id"], account, c["name"], c["name"].lower(), c["gender"], c["model"], c["state"], c["raw"],
                 c.get("robot")))
    cid = c["id"]
    con.executemany("INSERT INTO stats VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET value = excluded.value",
                    [(cid, k, v) for k, v in c.get("stats", {}).items()])
    con.execute("DELETE FROM items WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(cid, n, it.get("goods"), it["item"], it.get("days", 0), it.get("expires", 0),
                      int(bool(it.get("active"))), int(bool(it.get("equipped"))))
                     for n, it in enumerate(c.get("items", []))])
    con.execute("DELETE FROM talents WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO talents VALUES (?, ?, ?)", [(cid, k, v) for k, v in c.get("talents", {}).items()])


def add_log_lines(con, lines, keep):
    """lines = (character id, time, kind, text). Keeps the newest `keep` of each (call inside a transaction)."""
    con.executemany("INSERT INTO event_log (character_id, at, kind, text) VALUES (?, ?, ?, ?)", lines)
    con.executemany("DELETE FROM event_log WHERE character_id = ? AND id NOT IN "
                    "(SELECT id FROM event_log WHERE character_id = ? ORDER BY id DESC LIMIT ?)",
                    [(cid, cid, keep) for cid in {line[0] for line in lines}])


def load_characters(con):
    """All characters, in the shape the server keeps them in memory: {next_id, accounts: {account: [character]}}."""
    data = {"next_id": 1000, "accounts": {}}
    ids = {}
    for cid, account, name, _, gender, model, state, raw, robot in con.execute("SELECT * FROM characters ORDER BY id"):
        c = dict(id=cid, name=name, gender=gender, model=model, state=state, raw=raw, stats={}, items=[], talents={},
                 log=[])
        if robot is not None:
            c["robot"] = robot
        data["accounts"].setdefault(account, []).append(c)
        ids[cid] = c
        data["next_id"] = max(data["next_id"], cid + 1)
    for cid, stat, value in con.execute("SELECT * FROM stats"):
        ids[cid]["stats"][stat] = value
    for cid, _, goods, item, days, expires, active, equipped in con.execute("SELECT * FROM items ORDER BY character_id, slot"):
        it = dict(item=item, days=days, expires=expires, active=bool(active))
        if goods is not None:
            it["goods"] = goods
        if equipped:
            it["equipped"] = True
        ids[cid]["items"].append(it)
    for cid, talent, level in con.execute("SELECT * FROM talents"):
        ids[cid]["talents"][talent] = level
    for cid, at, kind, text in con.execute("SELECT character_id, at, kind, text FROM event_log ORDER BY id"):
        ids[cid]["log"].append([at, kind, text])
    return data


def open_characters(path):
    con = connect(path, CHARACTERS)
    old = os.path.join(os.path.dirname(path), "characters.json")
    if os.path.exists(old) and not con.execute("SELECT 1 FROM characters LIMIT 1").fetchone():
        data = read_json(old)
        with con:
            for account, chars in data["accounts"].items():
                for c in chars:
                    write_character(con, account, c)
                    con.executemany("INSERT INTO event_log (character_id, at, kind, text) VALUES (?, ?, ?, ?)",
                                    [(c["id"], *line) for line in c.get("log", [])])
        retire(old)
    return con
