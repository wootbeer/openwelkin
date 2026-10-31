# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""webadmin.py -- a small admin page. Run it on the login server's machine, open http://127.0.0.1:6013 and log in with
the key from admin.key:

    python tools/webadmin.py

It only sends admin commands to the server, like tools/admin.py, and never touches a database. To change the page,
edit PAGE. To add a button, add a form to PAGE and a line to ACTIONS. Or write your own: see "Admin port" in the README.

It listens on 127.0.0.1 only and has no HTTPS. Never open this port to the internet: from another machine, use an SSH
tunnel or a VPN."""
import hmac
import html
import http.server
import ipaddress
import os
import re
import secrets
import string
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import admin  # noqa: E402

PORT = 6013 + int(os.environ.get("MW_PORT_OFFSET", "0") or 0)
SESSION_TIME = 3600      # seconds a login lasts
MAX_FAILS, LOCK_TIME = 5, 300  # wrong keys before the login form locks, and for how long
MAX_BODY = 4096
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}  # any other name in a request is a web site attacking this page
HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                      "frame-ancestors 'none'; base-uri 'none'",
           "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store"}

STYLE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1"><style>
body { font: 15px sans-serif; max-width: 800px; margin: 20px auto; padding: 0 16px; }
pre { background: #eee; padding: 8px; overflow-x: auto; }
input { margin: 2px; } form { margin: 6px 0; } .note { font-weight: bold; }
</style>"""

LOGIN = string.Template(STYLE + """<title>MarsWar admin</title>
<h1>MarsWar admin</h1>
<p class=note>$note</p>
<form method=post action=/login><input type=password name=key placeholder="admin.key" autofocus>
<input type=submit value="Log in"></form>""")

PAGE = string.Template(STYLE + """<title>MarsWar admin</title>
<h1>MarsWar admin</h1>
<p class=note>$note</p>
<h2>Online</h2><pre>$who</pre>
<h2>Matches</h2><pre>$matches</pre>
<h2>Bans</h2><pre>$bans</pre>
<h2>Actions</h2>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=kick>
<input name=account placeholder=account required><input type=submit value=Kick></form>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=ban>
<input name=account placeholder=account required><input name=time placeholder="time: 7d (empty = permanent)">
<input name=reason placeholder=reason><input type=submit value=Ban></form>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=unban>
<input name=account placeholder=account required><input type=submit value=Unban></form>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=ipban>
<input name=net placeholder="1.2.3.4 or 1.2.3.0/24" required><input name=time placeholder="time: 7d (empty = permanent)">
<input name=reason placeholder=reason><input type=submit value="IP ban"></form>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=unipban>
<input name=net placeholder="1.2.3.4 or 1.2.3.0/24" required><input type=submit value="Remove IP ban"></form>
<form method=post action=/do><input type=hidden name=csrf value="$csrf"><input type=hidden name=action value=backup>
<input type=submit value="Back up databases"></form>
<form method=post action=/logout><input type=hidden name=csrf value="$csrf"><input type=submit value="Log out"></form>""")


# Everything from a form is checked here before it becomes part of a command: one line, words the command expects.
def account(form):
    value = form.get("account", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", value):
        raise ValueError("bad account name")
    return value


def network(form):
    return str(ipaddress.ip_network(form.get("net", "").strip(), strict=False))


def duration(form):
    value = form.get("time", "").strip().lower() or "perm"
    if not re.fullmatch(r"perm|\d{1,6}[mhdw]", value):
        raise ValueError("bad time: use 30m, 12h, 7d or 2w")
    return value


def reason(form):
    return " ".join("".join(c for c in form.get("reason", "") if c.isprintable()).split())[:200]


ACTIONS = {  # action name in the form -> the admin command it sends
    "kick": lambda f: f"kick {account(f)}",
    "ban": lambda f: f"ban {account(f)} {duration(f)} {reason(f)}",
    "unban": lambda f: f"unban {account(f)}",
    "ipban": lambda f: f"ipban {network(f)} {duration(f)} {reason(f)}",
    "unipban": lambda f: f"unipban {network(f)}",
    "backup": lambda f: "backup",
}

SESSIONS = {}  # cookie -> {"until": time, "csrf": token, "note": text to show once}
FAILS = []     # times of recent wrong keys
LOCK = threading.Lock()


def key_accepted(given):
    with LOCK:
        now = time.time()
        FAILS[:] = [t for t in FAILS if t > now - LOCK_TIME]
        if len(FAILS) >= MAX_FAILS:
            return False
        if hmac.compare_digest(given.encode(), admin.read_key().encode()):
            return True
        FAILS.append(now)
        return False


def ask(command):
    try:
        return admin.send(command)
    except OSError as e:
        return f"cannot reach the server: {e}"


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10
    server_version, sys_version = "webadmin", ""

    def log_message(self, *args):
        pass

    def respond(self, code, body="", **headers):
        data = body.encode("utf-8")
        self.send_response(code)
        for name, value in {**HEADERS, **headers}.items():
            self.send_header(name.replace("_", "-"), value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def trusted(self):
        """Is this request for this page, and not sent by another web site from the admin's browser?"""
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in HOSTS and (origin is None or origin.split("//")[-1] in HOSTS)

    def session(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            s = SESSIONS.get(value) if name == "session" else None
            if s and s["until"] > time.time():
                return value, s
        return None, None

    def do_GET(self):
        if not self.trusted():
            return self.respond(403, "refused")
        if self.path != "/":
            return self.respond(404, "not found")
        _, s = self.session()
        if not s:
            return self.respond(200, LOGIN.substitute(note=""))
        note, s["note"] = s["note"], ""
        self.respond(200, PAGE.substitute(note=html.escape(note), csrf=s["csrf"], who=html.escape(ask("who")),
                                      matches=html.escape(ask("matches")), bans=html.escape(ask("bans"))))

    def do_POST(self):
        if not self.trusted():
            return self.respond(403, "refused")
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = -1
        if not 0 <= size <= MAX_BODY:
            return self.respond(413, "too large")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(size).decode("utf-8", "replace"),
                                                          keep_blank_values=True).items()}
        if self.path == "/login":
            return self.login(form)
        cookie, s = self.session()
        if not s or not hmac.compare_digest(form.get("csrf", ""), s["csrf"]):
            return self.respond(403, "refused")
        if self.path == "/logout":
            del SESSIONS[cookie]
        elif self.path == "/do" and form.get("action") in ACTIONS:
            try:
                s["note"] = ask(ACTIONS[form["action"]](form))
            except ValueError as e:
                s["note"] = str(e)
        else:
            return self.respond(404, "not found")
        self.respond(303, Location="/")

    def login(self, form):
        if not key_accepted(form.get("key", "")):
            return self.respond(403, LOGIN.substitute(note="Wrong key, or locked for a few minutes."))
        cookie = secrets.token_hex(32)
        SESSIONS[cookie] = {"until": time.time() + SESSION_TIME, "csrf": secrets.token_hex(32), "note": ""}
        self.respond(303, Location="/", Set_Cookie=f"session={cookie}; HttpOnly; SameSite=Strict; Path=/")


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False  # no other program may share this port
    daemon_threads = True


def main():
    server = Server(("127.0.0.1", PORT), Handler)
    print(f"admin page on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
