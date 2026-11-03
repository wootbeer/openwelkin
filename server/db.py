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
CREATE INDEX IF NOT EXISTS stats_by_stat ON stats (stat, value);
CREATE TABLE IF NOT EXISTS items (
    character_id INTEGER NOT NULL REFERENCES characters (id), slot INTEGER NOT NULL, goods INTEGER, item INTEGER NOT NULL,
    days INTEGER NOT NULL, expires INTEGER NOT NULL, active INTEGER NOT NULL, equipped INTEGER NOT NULL,
    PRIMARY KEY (character_id, slot));
CREATE TABLE IF NOT EXISTS talents (
    character_id INTEGER NOT NULL REFERENCES characters (id), talent TEXT NOT NULL, level INTEGER NOT NULL,
    PRIMARY KEY (character_id, talent));
CREATE TABLE IF NOT EXISTS contacts (
    character_id INTEGER NOT NULL REFERENCES characters (id), kind TEXT NOT NULL, other_id INTEGER NOT NULL,
    PRIMARY KEY (character_id, kind, other_id));
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters (id), at INTEGER NOT NULL,
    kind INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_log_character ON event_log (character_id, id);
CREATE TABLE IF NOT EXISTS reports (boot TEXT PRIMARY KEY, seq INTEGER NOT NULL, at INTEGER NOT NULL);
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


def reader(path):
    """A second, read-only connection for slow reads (rankings), so writes on the main one need not wait."""
    con = sqlite3.connect(path, timeout=5, check_same_thread=False)
    con.execute("PRAGMA query_only=ON")
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
    con.execute("DELETE FROM contacts WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO contacts VALUES (?, ?, ?)",
                    [(cid, kind, other) for kind in ("friends", "blocked") for other in c.get(kind, [])])


def add_log_lines(con, lines, keep):
    """lines = (character id, time, kind, text). Keeps the newest `keep` of each (call inside a transaction)."""
    con.executemany("INSERT INTO event_log (character_id, at, kind, text) VALUES (?, ?, ?, ?)", lines)
    con.executemany("DELETE FROM event_log WHERE character_id = ? AND id NOT IN "
                    "(SELECT id FROM event_log WHERE character_id = ? ORDER BY id DESC LIMIT ?)",
                    [(cid, cid, keep) for cid in {line[0] for line in lines}])


def load_reports(con):
    """{game server run: the last of its reports in this database}"""
    return dict(con.execute("SELECT boot, seq FROM reports"))


def write_reports(con, done, now, keep):
    """Remember which game server reports are saved, so a resent one is not counted twice (call inside a
    transaction). Runs older than `keep` seconds are forgotten."""
    con.executemany("INSERT INTO reports VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET "
                    "seq = excluded.seq, at = excluded.at",
                    [(boot, seq, now) for boot, seq in done.items()])
    con.execute("DELETE FROM reports WHERE at < ?", (now - keep,))


def load_account(con, account):
    """One account's characters, in the shape the server keeps them in memory."""
    ids = {}
    for cid, _, name, _, gender, model, state, raw, robot in con.execute(
            "SELECT * FROM characters WHERE account = ? ORDER BY id", (account,)):
        ids[cid] = c = dict(id=cid, name=name, gender=gender, model=model, state=state, raw=raw, stats={}, items=[],
                            talents={}, log=[])
        if robot is not None:
            c["robot"] = robot
    if not ids:
        return []
    for cid, stat, value in con.execute(
            "SELECT * FROM stats WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid]["stats"][stat] = value
    for cid, _, goods, item, days, expires, active, equipped in con.execute(
            "SELECT * FROM items WHERE character_id IN (SELECT id FROM characters WHERE account = ?) "
            "ORDER BY character_id, slot", (account,)):
        it = dict(item=item, days=days, expires=expires, active=bool(active))
        if goods is not None:
            it["goods"] = goods
        if equipped:
            it["equipped"] = True
        ids[cid]["items"].append(it)
    for cid, talent, level in con.execute(
            "SELECT * FROM talents WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid]["talents"][talent] = level
    for cid, kind, other in con.execute(
            "SELECT * FROM contacts WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid].setdefault(kind, []).append(other)
    for cid, at, kind, text in con.execute(
            "SELECT character_id, at, kind, text FROM event_log "
            "WHERE character_id IN (SELECT id FROM characters WHERE account = ?) ORDER BY id", (account,)):
        ids[cid]["log"].append([at, kind, text])
    return list(ids.values())


def load_characters(con):
    """All characters: {next_id, accounts: {account: [character]}}. The server loads accounts as they are used;
    this is for tools and tests."""
    data = {"next_id": 1000, "accounts": {}}
    for (account,) in con.execute("SELECT DISTINCT account FROM characters").fetchall():
        data["accounts"][account] = load_account(con, account)
    data["next_id"] = next_id(con)
    return data


def next_id(con):
    return max(1000, con.execute("SELECT coalesce(max(id), 0) + 1 FROM characters").fetchone()[0])


def load_index(con):
    """{id: (account, name)} of every character: all the server keeps of characters nobody is using."""
    return {cid: (account, name) for cid, account, name in con.execute("SELECT id, account, name FROM characters")}


def ranking(con, stat, default=0, counter=None, current=None, everyone=False):
    """[(id, name, honor, value)] in no order. With `counter` (a period's counter, like kills_day) its value counts
    while at_<period> is `current`. `everyone`: characters without the stat too, at `default`."""
    if counter:
        rows = con.execute(
            "SELECT c.id, c.name, h.value, s.value FROM stats s JOIN characters c ON c.id = s.character_id "
            "JOIN stats a ON a.character_id = c.id AND a.stat = ? AND a.value = ? "
            "LEFT JOIN stats h ON h.character_id = c.id AND h.stat = 'honor' "
            "WHERE s.stat = ? AND s.value > 0", ("at_" + counter.rpartition("_")[2], current, counter))
    elif everyone:
        rows = con.execute(
            "SELECT c.id, c.name, h.value, coalesce(s.value, ?) FROM characters c "
            "LEFT JOIN stats s ON s.character_id = c.id AND s.stat = ? "
            "LEFT JOIN stats h ON h.character_id = c.id AND h.stat = 'honor'", (default, stat))
    else:
        rows = con.execute(
            "SELECT c.id, c.name, h.value, s.value FROM stats s JOIN characters c ON c.id = s.character_id "
            "LEFT JOIN stats h ON h.character_id = c.id AND h.stat = 'honor' "
            "WHERE s.stat = ? AND s.value > 0", (stat,))
    return [(cid, name, honor or 0, value) for cid, name, honor, value in rows]


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
