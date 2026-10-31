# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_webadmin.py -- start a server and the admin page in a temporary folder and attack the page: no key, wrong keys,
requests from other web sites, forged forms, script in a ban reason, extra commands hidden in a field."""
import asyncio
import http.client
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

import test_split as ts

import webadmin as web  # noqa: E402

check, wait_for = ts.check, ts.wait_for
cookie, csrf = "", ""


def request(method, path, form=None, host=None, origin=None, cookies=True, body=None):
    c = http.client.HTTPConnection("127.0.0.1", web.PORT, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{web.PORT}"}
    data = body if body is not None else urllib.parse.urlencode(form).encode() if form is not None else None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if origin:
        headers["Origin"] = origin
    if cookies and cookie:
        headers["Cookie"] = f"session={cookie}"
    c.request(method, path, data, headers)
    r = c.getresponse()
    text = r.read().decode("utf-8", "replace")
    c.close()
    return r, text


def act(action, **fields):
    return request("POST", "/do", {"csrf": csrf, "action": action, **fields})[0].status


def page():
    return request("GET", "/")[1]


def scenario():
    global cookie, csrf
    key = web.admin.read_key()

    # --- no way in without the key ---
    r, text = request("GET", "/")
    check("no login: only the login form", "password" in text and "Online" not in text)
    check("no login: no actions", request("POST", "/do", {"action": "ban", "account": "alice"})[0].status == 403)
    r, _ = request("POST", "/login", {"key": "wrong"})
    check("a wrong key is refused", r.status == 403 and not r.getheader("Set-Cookie"))

    # --- a good login ---
    r, _ = request("POST", "/login", {"key": key})
    set_cookie = r.getheader("Set-Cookie") or ""
    check("the key logs in", r.status == 303 and "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie, set_cookie)
    cookie = set_cookie.split("=")[1].split(";")[0]
    r, text = request("GET", "/")
    csrf = re.search(r'name=csrf value="(\w+)"', text)[1]
    check("the page shows the server's data", "Online" in text and "cannot reach" not in text, text)
    check("the page is locked down", "default-src 'none'" in r.getheader("Content-Security-Policy", ""))
    check("browsers send this page its own Origin", r.getheader("Referrer-Policy") == "same-origin")
    check("the key is not on the page", key not in text)

    # --- other web sites ---
    check("another site's name is refused (DNS rebinding)", request("GET", "/", host="evil.example")[0].status == 403)
    check("a post from another site is refused", request(
        "POST", "/do", {"csrf": csrf, "action": "kick", "account": "x"}, origin="http://evil.example")[0].status == 403)
    check("a post from a sandboxed page is refused", request(
        "POST", "/do", {"csrf": csrf, "action": "kick", "account": "x"}, origin="null")[0].status == 403)
    check("a post without the form token is refused", request(
        "POST", "/do", {"action": "ban", "account": "alice"})[0].status == 403)
    check("a post with a wrong token is refused", request(
        "POST", "/do", {"csrf": "0" * 64, "action": "ban", "account": "alice"})[0].status == 403)
    check("nobody was banned by any of that", "alice" not in web.admin.send("bans"))

    # --- the actions work ---
    act("ban", account="alice", time="1h", reason="<script>alert(1)</script>")
    text = page()
    check("ban works", "account alice" in web.admin.send("bans"))
    check("a reason cannot run in the page", "<script>" not in text and "&lt;script&gt;" in text, text)
    act("unban", account="alice")
    check("unban works", "alice" not in web.admin.send("bans"))
    act("ipban", net="203.0.113.0/24")
    check("IP ban works", "203.0.113.0/24" in web.admin.send("bans"))
    act("unipban", net="203.0.113.0/24")
    check("removing an IP ban works", "203.0.113.0" not in web.admin.send("bans"))

    act("backup")
    check("backup works", "characters.db -&gt;" in page())

    # --- nothing extra can reach the admin port ---
    act("ban", account="alice\nrole alice admin")
    check("a line break in a name is refused", "bad account" in page() and "alice" not in web.admin.send("bans"))
    act("ban", account="alice", reason="x\nrole alice admin")
    check("a line break in a reason stays in the reason", "player" in web.admin.send("role alice"))
    act("unban", account="alice")
    act("ban", account="alice", time="1h\nrole alice admin")
    check("a bad time is refused", "bad time" in page() and "alice" not in web.admin.send("bans"))
    act("ipban", net="1.2.3.4 1h\nrole alice admin")
    check("a bad network is refused", "player" in web.admin.send("role alice") and "1.2.3.4" not in web.admin.send("bans"))
    check("an unknown action is refused", act("role", account="alice") == 404)
    check("a huge form is refused", request("POST", "/do", body=b"x" * 5000)[0].status == 413)

    # --- sessions end ---
    good = web.SESSIONS[cookie]["until"]
    web.SESSIONS[cookie]["until"] = 1
    check("an old login stops working", "Online" not in page())
    web.SESSIONS[cookie]["until"] = good
    request("POST", "/logout", {"csrf": csrf})
    check("log out works", "Online" not in page())

    # --- guessing the key ---
    cookie = ""
    for _ in range(web.MAX_FAILS):
        request("POST", "/login", {"key": "guess"})
    check("after 5 wrong keys even the right one is refused", request("POST", "/login", {"key": key})[0].status == 403)


def main():
    env = dict(os.environ, MW_STATE_DIR=os.path.join(ts.root, "tool"))
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(ts.SERVER, "loginserver.py")], env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=ts.pump, args=(proc,), daemon=True).start()
    server = web.Server(("127.0.0.1", web.PORT), web.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        check("the server starts", wait_for(lambda: ts.seen("admin commands on"), 15))
        asyncio.run(ts.login("alice"))
        scenario()
    finally:
        proc.kill()
        server.shutdown()
        time.sleep(0.5)
        shutil.rmtree(ts.root, ignore_errors=True)
    print(f"{len(ts.fails)} failed" if ts.fails else "all ok")
    return 1 if ts.fails else 0


if __name__ == "__main__":
    sys.exit(main())
