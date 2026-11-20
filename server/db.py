# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""db.py -- the two databases. accounts (accounts, bans) belongs to the login server, characters to the gate; a
server never opens the other's, and game servers open neither. Each is an SQLite file (accounts.db, characters.db)
or, for a server too big for one disk, a MariaDB database: MW_ACCOUNTS_DB / MW_CHARACTERS_DB =
mariadb://user@host:port/database, the password in MW_DB_PASSWORD (never in the address, so it is never logged),
and MW_DB_SSL_CA = a CA file to make the link to MariaDB use TLS. MariaDB needs PyMySQL (pip install PyMySQL).

Every value goes into a query as a bound parameter (?), never into the SQL text: nothing a player types can change a
query. Queries are written for SQLite; translate() turns the few SQLite-only words into MariaDB's. Old JSON save
files are imported once, into an empty database, and renamed to *.imported."""
import functools
import json
import os
import re
import sqlite3
import time
import urllib.parse

try:
    import pymysql
except ImportError:  # only needed for MariaDB
    pymysql = None

Error = (sqlite3.Error,) + ((pymysql.Error,) if pymysql else ())
MARIADB = "mariadb://"

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
CREATE TABLE IF NOT EXISTS texts (
    character_id INTEGER NOT NULL REFERENCES characters (id), key TEXT NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (character_id, key));
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters (id), at INTEGER NOT NULL,
    kind INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_log_character ON event_log (character_id, id);
CREATE TABLE IF NOT EXISTS reports (boot TEXT PRIMARY KEY, seq INTEGER NOT NULL, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS divisions (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, short TEXT NOT NULL,
    short_key TEXT NOT NULL UNIQUE, leader INTEGER NOT NULL REFERENCES characters (id), nation INTEGER NOT NULL,
    created INTEGER NOT NULL, money INTEGER NOT NULL, points INTEGER NOT NULL, member_limit INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS division_members (
    character_id INTEGER PRIMARY KEY REFERENCES characters (id),
    division_id INTEGER NOT NULL REFERENCES divisions (id) ON DELETE CASCADE, state INTEGER NOT NULL,
    since INTEGER NOT NULL, donated INTEGER NOT NULL DEFAULT 0, title INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS division_members_division ON division_members (division_id);
"""


# The same tables for MariaDB: text keys need a length, the event log an AUTO_INCREMENT id, and every text compares
# byte for byte (utf8mb4_bin), as in SQLite. Every number is a BIGINT and every free text a MEDIUMTEXT: SQLite takes
# any size, and a value too big for its column would make MariaDB (strict) refuse the whole save batch. Text keys are
# VARCHARs longer than anything the server lets in. `key` is a reserved word there.
MARIADB_TABLES = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin"  # nopad: 'bob ' is not 'bob'
MARIADB_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    name VARCHAR(64) PRIMARY KEY, salt VARCHAR(64) NOT NULL, hash VARCHAR(128) NOT NULL, created BIGINT NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'player')%(t)s;
CREATE TABLE IF NOT EXISTS bans (
    kind VARCHAR(16) NOT NULL, target VARCHAR(100) NOT NULL, until BIGINT NOT NULL, reason MEDIUMTEXT NOT NULL,
    banned_by VARCHAR(100) NOT NULL, banned_at BIGINT NOT NULL, PRIMARY KEY (kind, target))%(t)s;
""" % {"t": MARIADB_TABLES}

MARIADB_CHARACTERS = """
CREATE TABLE IF NOT EXISTS characters (
    id BIGINT PRIMARY KEY, account VARCHAR(64) NOT NULL, name VARCHAR(64) NOT NULL, name_key VARCHAR(64) NOT NULL UNIQUE,
    gender BIGINT NOT NULL, model BIGINT NOT NULL, state BIGINT NOT NULL, raw MEDIUMTEXT NOT NULL, robot BIGINT,
    INDEX characters_account (account))%(t)s;
CREATE TABLE IF NOT EXISTS stats (
    character_id BIGINT NOT NULL, stat VARCHAR(64) NOT NULL, value DOUBLE NOT NULL,
    PRIMARY KEY (character_id, stat), INDEX stats_by_stat (stat, value),
    FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS items (
    character_id BIGINT NOT NULL, slot BIGINT NOT NULL, goods BIGINT, item BIGINT NOT NULL, days BIGINT NOT NULL,
    expires BIGINT NOT NULL, active BIGINT NOT NULL, equipped BIGINT NOT NULL, PRIMARY KEY (character_id, slot),
    FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS talents (
    character_id BIGINT NOT NULL, talent VARCHAR(64) NOT NULL, level BIGINT NOT NULL, PRIMARY KEY (character_id, talent),
    FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS contacts (
    character_id BIGINT NOT NULL, kind VARCHAR(16) NOT NULL, other_id BIGINT NOT NULL,
    PRIMARY KEY (character_id, kind, other_id), FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS texts (
    character_id BIGINT NOT NULL, `key` VARCHAR(64) NOT NULL, text MEDIUMTEXT NOT NULL, PRIMARY KEY (character_id, `key`),
    FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS event_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT, character_id BIGINT NOT NULL, at BIGINT NOT NULL, kind BIGINT NOT NULL,
    text MEDIUMTEXT NOT NULL, INDEX event_log_character (character_id, id),
    FOREIGN KEY (character_id) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS reports (boot VARCHAR(100) PRIMARY KEY, seq BIGINT NOT NULL, at BIGINT NOT NULL)%(t)s;
CREATE TABLE IF NOT EXISTS divisions (
    id BIGINT PRIMARY KEY, name VARCHAR(64) NOT NULL, name_key VARCHAR(64) NOT NULL UNIQUE, short VARCHAR(16) NOT NULL,
    short_key VARCHAR(16) NOT NULL UNIQUE, leader BIGINT NOT NULL, nation BIGINT NOT NULL, created BIGINT NOT NULL,
    money BIGINT NOT NULL, points BIGINT NOT NULL, member_limit BIGINT NOT NULL,
    FOREIGN KEY (leader) REFERENCES characters (id))%(t)s;
CREATE TABLE IF NOT EXISTS division_members (
    character_id BIGINT PRIMARY KEY, division_id BIGINT NOT NULL, state BIGINT NOT NULL, since BIGINT NOT NULL,
    donated BIGINT NOT NULL DEFAULT 0, title BIGINT NOT NULL DEFAULT 0, INDEX division_members_division (division_id),
    FOREIGN KEY (character_id) REFERENCES characters (id),
    FOREIGN KEY (division_id) REFERENCES divisions (id) ON DELETE CASCADE)%(t)s;
""" % {"t": MARIADB_TABLES}


@functools.lru_cache(maxsize=512)
def translate(sql):
    """An SQLite query in MariaDB's words: ? -> %s, INSERT OR REPLACE -> REPLACE, ON CONFLICT ... DO UPDATE SET
    x = excluded.x -> ON DUPLICATE KEY UPDATE x = VALUES(x). Only the query text changes; values stay bound."""
    sql = sql.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    m = re.search(r" ON CONFLICT(?: \([^)]*\))? DO UPDATE SET ", sql)
    if m:
        sql = sql[:m.start()] + " ON DUPLICATE KEY UPDATE " + re.sub(r"excluded\.(\w+)", r"VALUES(\1)", sql[m.end():])
    return sql.replace("%", "%%").replace("?", "%s")


class MariaDB:
    """A MariaDB connection that works like the sqlite3 one the server uses: execute / executemany / executescript
    return a cursor, and `with con:` is one transaction. Outside a transaction every statement commits at once, so a
    long-lived connection never reads old data. A connection idle for a while is checked first and made again if
    MariaDB dropped it."""
    IDLE = 60.0  # seconds
    LOST = {0, 2006, 2013, 2055, 4031}  # MariaDB error numbers for a dropped or timed-out connection

    def __init__(self, url, read_only=False):
        if pymysql is None:
            raise RuntimeError("MariaDB needs PyMySQL: pip install PyMySQL")
        u = urllib.parse.urlsplit(url)
        if u.password is not None:
            raise ValueError("put the MariaDB password in MW_DB_PASSWORD, not in the address")
        if not u.path.strip("/"):
            raise ValueError(f"no database name in {url}")
        self.args = dict(host=u.hostname or "127.0.0.1", port=u.port or 3306,
                         user=urllib.parse.unquote(u.username or ""), password=os.environ.get("MW_DB_PASSWORD", ""),
                         database=urllib.parse.unquote(u.path.strip("/")), charset="utf8mb4", collation="utf8mb4_bin",
                         autocommit=True, connect_timeout=5, read_timeout=60, write_timeout=60,
                         local_infile=False,  # MariaDB may never ask for a file from this machine
                         client_flag=pymysql.constants.CLIENT.FOUND_ROWS)  # rowcount = rows matched, as in SQLite
        if os.environ.get("MW_DB_SSL_CA"):
            self.args["ssl"] = {"ca": os.environ["MW_DB_SSL_CA"]}
        self.read_only, self.depth, self.con = read_only, 0, None
        self.open()

    def open(self):
        if self.con is not None:
            try:
                self.con.close()
            except Error:
                pass
        self.con = pymysql.connect(**self.args)
        with self.con.cursor() as cur:
            cur.execute("SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION,NO_ZERO_DATE'")
            cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            if self.read_only:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        self.used = time.monotonic()

    def ready(self):
        """Outside a transaction, check a connection that was idle (and make it again if it was dropped)."""
        if self.depth == 0 and time.monotonic() - self.used > self.IDLE:
            try:
                self.con.ping(reconnect=False)
            except Error:
                self.open()

    def run(self, call, sql, params):
        self.ready()
        cur = self.con.cursor()
        try:
            call(cur, translate(sql), params)
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            # only a lost link is tried again, and only outside a transaction (inside, the caller's rollback handles
            # it): writes always run in a transaction, so nothing is ever written twice
            if self.depth or not e.args or e.args[0] not in self.LOST:
                raise
            self.open()  # the link dropped between two statements: once more on a new connection
            cur = self.con.cursor()
            call(cur, translate(sql), params)
        self.used = time.monotonic()
        return cur

    def execute(self, sql, params=()):
        return self.run(lambda cur, q, p: cur.execute(q, p), sql, tuple(params))

    def executemany(self, sql, rows):
        return self.run(lambda cur, q, p: cur.executemany(q, p) if p else None, sql, [tuple(r) for r in rows])

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def __enter__(self):
        if self.depth == 0:
            self.ready()
            try:
                self.con.begin()
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
                self.open()  # dropped since the last use: nothing is written yet, so start on a new connection
                self.con.begin()
        self.depth += 1
        return self

    def __exit__(self, kind, value, tb):
        self.depth -= 1
        if self.depth == 0:
            if kind is None:
                self.con.commit()
            else:
                try:
                    self.con.rollback()
                except Error:
                    self.open()
        return False

    def close(self):
        self.con.close()


def is_mariadb(target):
    return isinstance(target, str) and target.startswith(MARIADB)


def num(value):
    """A stat as the server keeps it: MariaDB hands DOUBLE values back as floats, 3.0 means 3."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def backup(path, copy):
    """Copy a database while the server is using it."""
    if is_mariadb(path):
        raise OSError("it is in MariaDB: back it up there (mariadb-dump --single-transaction)")
    source, target = sqlite3.connect(path), sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def connect(path, schema, mariadb_schema=None):
    """One connection per database, used from many threads: the caller holds its lock around every use. path is a
    file, or a mariadb:// address."""
    if is_mariadb(path):
        con = MariaDB(path)
        con.executescript(mariadb_schema)
        return con
    con = sqlite3.connect(path, timeout=5, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")  # a crash never leaves a half-written database
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(schema)
    con.execute("PRAGMA user_version=1")
    return con


def reader(path):
    """A second, read-only connection for slow reads (rankings), so writes on the main one need not wait."""
    if is_mariadb(path):
        return MariaDB(path, read_only=True)
    con = sqlite3.connect(path, timeout=5, check_same_thread=False)
    con.execute("PRAGMA query_only=ON")
    return con


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def retire(path):
    os.replace(path, path + ".imported")


def open_accounts(path):
    con = connect(path, ACCOUNTS, MARIADB_ACCOUNTS)
    if is_mariadb(path):  # old JSON files are only imported next to an SQLite file
        return con
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
                      int(bool(it.get("active"))), int(it.get("equipped") or 0))
                     for n, it in enumerate(c.get("items", []))])
    con.execute("DELETE FROM talents WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO talents VALUES (?, ?, ?)", [(cid, k, v) for k, v in c.get("talents", {}).items()])
    con.execute("DELETE FROM contacts WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO contacts VALUES (?, ?, ?)",
                    [(cid, kind, other) for kind in ("friends", "blocked") for other in c.get(kind, [])])
    con.execute("DELETE FROM texts WHERE character_id = ?", (cid,))
    con.executemany("INSERT INTO texts VALUES (?, ?, ?)", [(cid, k, v) for k, v in c.get("texts", {}).items()])


def write_stats(con, cid, stats):
    """Save only these stats of a character already in the database (call inside a transaction)."""
    con.executemany("INSERT INTO stats VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET value = excluded.value",
                    [(cid, k, v) for k, v in stats.items()])


def add_log_lines(con, lines, keep):
    """lines = (character id, time, kind, text, line id). Keeps the newest `keep` of each (call inside a transaction).
    The caller numbers the lines (next_log_id), so writing a batch again adds nothing: when the link drops during a
    COMMIT that the database applied anyway, the retry does not write the lines twice."""
    con.executemany("INSERT INTO event_log (character_id, at, kind, text, id) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET id = excluded.id", lines)
    for cid in {line[0] for line in lines}:  # the oldest line kept, then everything before it goes
        row = con.execute("SELECT id FROM event_log WHERE character_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
                          (cid, max(keep - 1, 0))).fetchone()
        if row:
            con.execute("DELETE FROM event_log WHERE character_id = ? AND id < ?", (cid, row[0]))


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
        ids[cid]["stats"][stat] = num(value)
    for cid, _, goods, item, days, expires, active, equipped in con.execute(
            "SELECT * FROM items WHERE character_id IN (SELECT id FROM characters WHERE account = ?) "
            "ORDER BY character_id, slot", (account,)):
        it = dict(item=item, days=days, expires=expires, active=bool(active))
        if goods is not None:
            it["goods"] = goods
        if equipped:
            it["equipped"] = equipped  # the in-use slot, 1-based
        ids[cid]["items"].append(it)
    for cid, talent, level in con.execute(
            "SELECT * FROM talents WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid]["talents"][talent] = level
    for cid, kind, other in con.execute(
            "SELECT * FROM contacts WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid].setdefault(kind, []).append(other)
    for cid, key, text in con.execute(
            "SELECT * FROM texts WHERE character_id IN (SELECT id FROM characters WHERE account = ?)", (account,)):
        ids[cid].setdefault("texts", {})[key] = text
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


def next_log_id(con):
    return con.execute("SELECT coalesce(max(id), 0) + 1 FROM event_log").fetchone()[0]


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
    return [(cid, name, num(honor or 0), num(value)) for cid, name, honor, value in rows]


DIVISION_FIELDS = ("id", "name", "short", "leader", "nation", "created", "money", "points", "limit")


def load_divisions(con):
    """({division id: division}, {character id: [division id, state, since, crystal donated, title]}): all of them,
    they are few."""
    if isinstance(con, MariaDB):  # its table was made with every column
        have = {r[0] for r in con.execute("SHOW COLUMNS FROM division_members")}
    else:
        have = {r[1] for r in con.execute("PRAGMA table_info(division_members)")}
    for column, add in (("donated", "ALTER TABLE division_members ADD COLUMN donated INTEGER NOT NULL DEFAULT 0"),
                        ("title", "ALTER TABLE division_members ADD COLUMN title INTEGER NOT NULL DEFAULT 0")):
        if column not in have:  # added 2026-11-17 and 2026-11-18; older databases get them here
            with con:
                con.execute(add)
    divisions = {row[0]: dict(zip(DIVISION_FIELDS, row)) for row in con.execute(
        "SELECT id, name, short, leader, nation, created, money, points, member_limit FROM divisions")}
    members = {cid: [did, state, since, donated, title] for cid, did, state, since, donated, title in con.execute(
        "SELECT character_id, division_id, state, since, donated, title FROM division_members")}
    return divisions, members


def write_division(con, d):
    """Add or update a division (call inside a transaction)."""
    con.execute("INSERT INTO divisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "leader = excluded.leader, money = excluded.money, points = excluded.points, "
                "member_limit = excluded.member_limit",
                (d["id"], d["name"], d["name"].casefold(), d["short"], d["short"].casefold(), d["leader"], d["nation"],
                 d["created"], d["money"], d["points"], d["limit"]))


def delete_division(con, did):
    """Remove a division and every member and application row (call inside a transaction)."""
    con.execute("DELETE FROM division_members WHERE division_id = ?", (did,))
    con.execute("DELETE FROM divisions WHERE id = ?", (did,))


def write_division_member(con, cid, row):
    """row = [division id, state, since(, crystal donated(, title))], or None to remove the character's row (call
    inside a transaction). Parts left out start again at 0."""
    if row is None:
        con.execute("DELETE FROM division_members WHERE character_id = ?", (cid,))
    else:
        con.execute("INSERT INTO division_members VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (character_id) DO UPDATE SET "
                    "division_id = excluded.division_id, state = excluded.state, since = excluded.since, "
                    "donated = excluded.donated, title = excluded.title", (cid, *(list(row[:5]) + [0, 0])[:5]))


def open_characters(path):
    con = connect(path, CHARACTERS, MARIADB_CHARACTERS)
    if is_mariadb(path):
        return con
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
