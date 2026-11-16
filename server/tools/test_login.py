"""test_login.py -- offline checks for login: register, password check, throttle, pre-login guard, bans and the
admin commands.

Uses a temporary accounts file; nothing real is changed. Usage: python -I tools/test_login.py"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the tools folder out
import tmpdir  # noqa: E402  (removes the temporary folders at exit)
os.environ.setdefault("MW_STATE_DIR", tmpdir.make("mw-test-"))  # never the real save files
import loginserver as ls  # noqa: E402

real_log, ls.log = ls.log, lambda *a, **k: None
ls.STORE.save = lambda: None
tmp = tmpdir.make()
ls.Accounts.path = os.path.join(tmp, "accounts.db")
ls.ACCOUNTS = ls.Accounts()
ls.Bans.path = os.path.join(tmp, "accounts.db")
ls.BANS = ls.Bans()
ls.ADMIN_LOG = os.path.join(tmp, "admin.log")


class FakeSession(ls.Session):
    def __init__(self, ip="1.1.1.1"):
        super().__init__(None, (ip, 1))
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)


def login_body(account, password):
    a, p = account.encode("latin-1"), password.encode("latin-1")
    return (struct.pack("<I", 1) + bytes([len(a)]) + a + bytes([len(p)]) + p + struct.pack("<II", 10102, 1)
            + bytes([4]) + b"hash")


def login(account, password, ip="1.1.1.1"):
    s = FakeSession(ip)
    s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=login_body(account, password)))
    return s.sent[-1].result, s.account


def check(name, got, want):
    print(("ok  " if got == want else "FAIL") + f" {name}: {got!r}" + ("" if got == want else f" (want {want!r})"))
    return got == want


ok = True
ok &= check("new account registers", login("alice", "pw1"), (0, "alice"))
ok &= check("same password logs in", login("alice", "pw1"), (0, "alice"))
ok &= check("wrong password refused", login("alice", "nope", ip="2.2.2.2"), (ls.LOGIN_FAILED, None))
ok &= check("bad account name refused", login("a b'; --", "x", ip="3.3.3.3"), (ls.LOGIN_FAILED, None))
ok &= check("an account name ending in a newline refused", login("alice\n", "x", ip="3.3.3.4"), (ls.LOGIN_FAILED, None))

# character names: the create request with names the client's own check might let through
s = FakeSession(ip="3.3.3.5")
s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=login_body("namer", "pw")))


def create(name):
    s.handle(ls.Message(13, 13006, token=1, flags=ls.MSG_REQUEST,
                        body=ls.wstr(name) + bytes([1]) + struct.pack("<I", 1) + bytes(8)))
    return s.sent[-1].result


for bad, why in [("Alpha\n", "a newline at the end"), ("Server", "the server's own name"), ("gM", "staff, any case"),
                 ("\uff21lpha", "fullwidth letters"), ("\u0410dmin", "a Cyrillic letter among ASCII ones"),
                 ("ab\u00b2", "a superscript digit"), ("a", "too short"), ("a b", "a space"), ("x\u200d", "a joiner"),
                 ("\u115f\u115f", "Hangul fillers (draws blank)"), ("\u3164\uac00\u3164", "a filler among letters"),
                 ("__", "only underscores"), ("GM_Admin", "staff words joined"), ("Server1", "a staff word and digits")]:
    ok &= check(f"character name refused: {why}", create(bad) != 0, True)
ok &= check("a name with a staff word inside another word is fine", create("Modesty"), 0)
ok &= check("a plain name is fine", create("Alpha_2"), 0)
ok &= check("so is one in another script", create("\u5c0f\u660e"), 0)
ok &= check("a name taken in another case refused", create("ALPHA_2"), 13002)
ok &= check("a name drawn alike in Cyrillic refused (Моԁеѕту for Modesty)",
            create("Моԁеѕту"), 13002)
ok &= check("digits passing for letters refused (A1pha_2 for Alpha_2)", create("A1pha_2"), 13002)
ok &= check("Cyrillic СОВА and Latin COBA look the same", ls.name_skeleton("СОВА"),
            ls.name_skeleton("COBA"))
ok &= check("a staff word in look-alike letters is refused (Cyrillic system)",
            ls.valid_name("ѕуѕтем"), False)
for _ in range(ls.LOGIN_FAILS_MAX):
    login("alice", "guess", ip="4.4.4.4")
ok &= check("throttled after failures (even right password)", login("alice", "pw1", ip="4.4.4.4"), (ls.LOGIN_LOCKED, None))
ok &= check("other IPs unaffected", login("alice", "pw1", ip="5.5.5.5"), (0, "alice"))
ok &= check("password not stored in clear", "pw1" in open(ls.Accounts.path).read(), False)
s = FakeSession()
s.handle(ls.Message(13, 13000, token=1, flags=ls.MSG_REQUEST, body=b""))
ok &= check("requests before login ignored", len(s.sent), 0)

# new accounts: REGISTER_MAX per address per window (each costs a slow hash and a database row)
made = [login(f"new{i}", "pw", ip="7.7.7.7")[0] for i in range(ls.REGISTER_MAX)]
ok &= check(f"{ls.REGISTER_MAX} new accounts from one address", made, [0] * ls.REGISTER_MAX)
ok &= check("one more is refused", login("new_extra", "pw", ip="7.7.7.7"), (ls.LOGIN_LOCKED, None))
ok &= check("and was not made", ls.ACCOUNTS.exists("new_extra"), False)
ok &= check("an existing account still logs in from there", login("new0", "pw", ip="7.7.7.7"), (0, "new0"))
ok &= check("another address may still make one", login("new_extra", "pw", ip="7.7.7.8"), (0, "new_extra"))

# the login port only logs in: hall requests on it would get past kicks and bans (they close the gate connection)
s = FakeSession(ip="5.5.5.5")
s.port = ls.LOGIN_PORT
s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=login_body("alice", "pw1")))
n = len(s.sent)
s.handle(ls.Message(13, 13000, token=1, flags=ls.MSG_REQUEST, body=b""))
ok &= check("a hall request on the login port is ignored", (s.account, len(s.sent) - n), ("alice", 0))

# balances: only of your own characters
rich = ls.STORE.create("richacct", "Rich", gender=1, model=1, state=1, raw="")
s.port = None  # a gate connection, logged in as alice
for typ, what in ((24019, "money"), (24020, "credit")):
    s.handle(ls.Message(24, typ, token=1, flags=ls.MSG_REQUEST, body=struct.pack("<I", rich)))
    ok &= check(f"another account's {what} reads 0", s.sent[-1].body, struct.pack("<I", 0))
s.handle(ls.Message(14, 14057, token=1, flags=ls.MSG_REQUEST, body=struct.pack("<I", rich)))
ok &= check("another account's battle report reads 0", s.sent[-1].body, bytes(40))
own = FakeSession(ip="5.5.5.6")
own.account = "richacct"
own.handle(ls.Message(24, 24019, token=1, flags=ls.MSG_REQUEST, body=struct.pack("<I", rich)))
ok &= check("your own money reads right", own.sent[-1].body, struct.pack("<I", int(ls.STORE.stats(rich)["money"])))

# TCP connections per address are capped (loopback is this machine)
taken = [ls.take_ip("8.8.8.8") for _ in range(ls.MAX_CONNECTIONS_PER_IP + 1)]
ok &= check("connections from one address capped", taken.count(True), ls.MAX_CONNECTIONS_PER_IP)
ls.release_ip("8.8.8.8")
ok &= check("a closed one frees a slot", ls.take_ip("8.8.8.8"), True)
for _ in range(ls.MAX_CONNECTIONS_PER_IP):
    ls.release_ip("8.8.8.8")
ok &= check("all closed: the address is forgotten", "8.8.8.8" in ls.IP_CONNECTIONS, False)
ok &= check("loopback not capped", all(ls.take_ip("127.0.0.1") for _ in range(ls.MAX_CONNECTIONS_PER_IP + 5)), True)
s = FakeSession()
s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=b"\x01\x00"))
ok &= check("malformed login refused", s.sent[-1].result, ls.LOGIN_FAILED)

# a flood of requests is cut down, then closed
s = FakeSession(ip="6.6.6.6")
s.handle(ls.Message(10, 10000, token=1, flags=ls.MSG_REQUEST, body=login_body("flood", "pw")))
closed = []
s.close = lambda: closed.append(1)
for n in range(int(ls.REQ_BURST) + 50):
    s.handle(ls.Message(13, 13000, token=n, flags=ls.MSG_REQUEST, body=b""))
ok &= check("a burst is answered, past it requests are dropped", int(ls.REQ_BURST) - 5 <= len(s.sent) <= int(ls.REQ_BURST) + 5,
            True)
for n in range(ls.REQ_DROP_CLOSE):
    s.handle(ls.Message(13, 13000, token=n, flags=ls.MSG_REQUEST, body=b""))
ok &= check("a client that keeps flooding is closed", bool(closed), True)

# bans and admin commands
admin = lambda line: ls.admin_command(line, "test")  # noqa: E731
login("carol", "pw3", ip="7.7.7.7")
ok &= check("ban unknown account refused", admin("ban nobody"), "no account 'nobody'")
ok &= check("permanent ban", admin("ban alice cheating a lot"), "banned alice permanent: cheating a lot")
ok &= check("banned account refused (locked text)", login("alice", "pw1", ip="8.8.8.1"), (ls.ACCOUNT_BANNED, None))
ok &= check("banned account, wrong password: no hint", login("alice", "x", ip="8.8.8.2"), (ls.LOGIN_FAILED, None))
ok &= check("timed ban", admin("ban alice 2h spam").startswith("banned alice until "), True)
ok &= check("timed ban refused (punished text)", login("alice", "pw1", ip="8.8.8.3"), (ls.ACCOUNT_SUSPENDED, None))
ok &= check("bans lists it", "account alice" in admin("bans") and "spam" in admin("bans"), True)
ls.BANS.data["accounts"]["alice"]["until"] = 1  # expired long ago
ok &= check("expired ban ignored", login("alice", "pw1", ip="8.8.8.4"), (0, "alice"))
ok &= check("unban", admin("unban alice"), "unbanned alice")
ok &= check("unban again", admin("unban alice"), "alice is not banned")
reply = admin("ipban 9.9.0.0/16 1d bots")
ok &= check("ip ban of a network", reply.startswith("banned 9.9.0.0/16 until ") and reply.endswith(": bots; kicked 0"), True)
ok &= check("IP in the network refused", login("carol", "pw3", ip="9.9.4.5"), (ls.IP_BANNED, None))
ok &= check("IP outside it fine", login("carol", "pw3", ip="9.10.0.1"), (0, "carol"))
ok &= check("single address ban", admin("ipban 10.1.2.3"), "banned 10.1.2.3/32 permanent; kicked 0")
ok &= check("game sessions refuse banned IPs", ls.BANS.ip("10.1.2.3") is not None and ls.BANS.ip("10.1.2.4") is None, True)
ok &= check("bad address", admin("ipban 999.1.1.1"), "usage: ipban <address or network> [time] [reason]")
ok &= check("unipban", admin("unipban 9.9.0.0/16"), "unbanned 9.9.0.0/16")
ok &= check("role set", admin("role carol gm"), "carol is now gm")
ok &= check("role shown", admin("role carol"), "carol: gm")
ok &= check("bad role", admin("role carol king"), "usage: role <account> [player|gm|admin]")
ok &= check("unknown command", admin("explode").startswith("unknown command 'explode'"), True)


class Gate(FakeSession):  # an online hall connection
    closed = False

    def close(self):
        self.closed = True


g = Gate("7.7.7.7")
g.account = "carol"
ls.go_online(g)
ok &= check("who lists online accounts", admin("who"), "carol  7.7.7.7  in the hall")
g2 = Gate("7.7.7.8")
g2.account = "carol"
ls.go_online(g2)
ok &= check("second login closes the first", (g.closed, ls.ONLINE["carol"] is g2), (True, True))
ok &= check("kick", (admin("kick carol"), g2.closed), ("kicked carol", True))
ls.go_offline(g2)
ok &= check("kick offline account", admin("kick carol"), "carol is not online")
ok &= check("every command in the audit log", sum(1 for _ in open(ls.ADMIN_LOG)), 18)
ok &= check("durations", [ls.parse_duration(x) for x in ("30m", "12h", "7d", "2w", "perm", "soon")],
            [1800, 43200, 604800, 1209600, 0, None])
ls.AUTO_REGISTER = False
ok &= check("no auto-register when off", login("bob", "pw", ip="6.6.6.6"), (ls.LOGIN_FAILED, None))

# log lines: text from players can not start a line of its own, nor carry escape codes; any script can be logged
size = os.path.getsize(ls.LOG.name)
real_log("[chat] \u5c0f\u660e: hi\n12:00:00 [admin] forged\x1b[2J \u202eevil")
with open(ls.LOG.name, encoding="utf-8") as f:
    f.seek(size)
    written = f.read()
ok &= check("a newline in a log line starts an indented line", written.splitlines()[1], "  12:00:00 [admin] forged\\x1b[2J \\u202eevil")
ok &= check("and a Chinese name is logged as it is", "\u5c0f\u660e" in written.splitlines()[0], True)
print("all ok" if ok else "FAILURES")
sys.exit(0 if ok else 1)
