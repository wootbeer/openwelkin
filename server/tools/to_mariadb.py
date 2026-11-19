# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""to_mariadb.py <accounts.db|characters.db> <mariadb://user@host:port/database> -- copy an SQLite database into an
empty MariaDB database, to move a server to MariaDB. The password comes from MW_DB_PASSWORD. Stop the server that
uses the file first. The MariaDB database must have no rows yet; everything is copied in one transaction, so a
failed copy leaves it empty. The SQLite file is opened read-only."""
import os
import re
import sqlite3
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402

# parents before the tables that point at them
TABLES = {"accounts": ("accounts", "bans"),
          "characters": ("characters", "stats", "items", "talents", "contacts", "texts", "event_log", "reports",
                         "divisions", "division_members")}


def copy(path, url, out=print):
    """Copy one database; returns {table: rows copied}."""
    kind = "accounts" if os.path.basename(path).startswith("accounts") else "characters"
    if not os.path.exists(path):
        raise SystemExit(f"{path}: no such file")
    source = sqlite3.connect("file:" + urllib.parse.quote(os.path.abspath(path).replace("\\", "/")) + "?mode=ro",
                             uri=True)  # quoted, so no part of the path is read as an option
    target = (db.open_accounts if kind == "accounts" else db.open_characters)(url)
    try:
        for table in TABLES[kind]:
            if target.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone():
                raise SystemExit(f"{url}: table {table} already has rows; this copies into an empty database only")
        counts = {}
        with target:
            for table in TABLES[kind]:
                cur = source.execute("SELECT * FROM " + table)
                names = [d[0] for d in cur.description]  # an older file may lack newer columns: they get defaults
                if not all(re.fullmatch(r"[a-z_]+", n) for n in names):
                    raise SystemExit(f"{path}: unexpected column in {table}")
                rows = cur.fetchall()
                if rows:
                    target.executemany("INSERT INTO " + table + " (" + ", ".join(f"`{n}`" for n in names)
                                       + ") VALUES (" + ", ".join("?" * len(names)) + ")", rows)
                counts[table] = len(rows)
    finally:
        source.close()
        target.close()
    for table, n in counts.items():
        out(f"{table}: {n} rows")
    out(f"done. Start the server with MW_{kind.upper()}_DB={url}")
    return counts


def main():
    if len(sys.argv) != 3 or not db.is_mariadb(sys.argv[2]):
        sys.exit(__doc__)
    copy(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
