# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""MarsWar / Welkin4591 login server emulator.

Wire format (reverse-engineered from System/IONetwork.dll, Outpop::IONetwork):

Key exchange, client -> server (Connect_Bluider_Process::make_commit_data):
    "0001" | u32 len(rest) | u32 status=1 | u32 0 | u32 9 | u32 keylen | X.509 DER RSA-512 pubkey | first-message bytes
Key exchange, server -> client (Accept_Bluider_Process::handle_read_stream + make_respond_packet):
    "0001" | u32 len(rest) | u32 status=1 | RSAES-OAEP-SHA1( i32 rand(1..32) | u16 | 16-byte AES key )

After the handshake every packet is (Stream_Protocol_Head_Process::make_head):
    u8 flags | length (1/2/4 bytes, width in flags bits 6-7) | AES-128-ECB payload | u8 checksum
    flags: bit0 system packet, bit1 "key" flag (NOT encryption: packets without it are encrypted too), bits2-3 sequence,
           bits4-5 priority, bits6-7 length width (1=u8, 2=u16, 3=u32)
    length counts payload + checksum byte.
    plaintext is padded so the last byte holds the pad count (Crypto_Aes1::aes_decrypt_buff).
    checksum = sum(header bytes) + sum(payload[::key_index]) mod 256, key_index from the handshake.

Inside the payload is an Outpop::Message (MessageService.dll):
    u8 flags | u8 event catalog | [u16 event type] | [u32 token] | [u16 result] | [u32 multi id] | body
    flags: 0x80 has type, 0x04 request (token), 0x08 ack (token + result), 0x20/0x40 multicast type (multi id), 0x02 trans

Game server (TODO): after QS_ApplyEnter the client talks to GAME_IP:GAME_PORT over reliable UDP ("FDP",
IONetwork Dgram_* classes). 12-byte header (Outpop::IONetwork::Protocol accessors, HeadSize=12):
    u8 flags | u16 number | u16 session id | u16 mtu length | u8 split id | u16 ack id | u16 order id
    flags: 0x80 reliable, 0x60 order type, 0x18 split type, 0x04 system packet, 0x03 ack type
First packet seen (SYN, resent every 3s, released after 3 tries):
    07 1127 0900 0c00 02 2076 0000 | 0c000000 00000000 09000000 00000000
Server side builder to copy: Dgram_Accept_Bluider_Process::make_respond_packet (IONetwork.dll 0x100407c0).
SAP NAT detection (ADServer, UDP 5876) sends: fb 00 00 dc ff 12 00 00 00 00 00 00 83 04 "1000".
"""
import hashlib, hmac, json, math, os, random, re, secrets, socket, struct, sys, threading, time

import fdp
import snapshots

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = os.path.dirname(os.path.abspath(__file__))
MAGIC = b"0001"
# Address clients are told to connect to for the gate and game servers. Set MW_PUBLIC_IP to this machine's
# LAN or internet address when others play; 127.0.0.1 only works for a client on the same PC.
PUBLIC_IP = os.environ.get("MW_PUBLIC_IP", "127.0.0.1")
GATE_IP = int.from_bytes(socket.inet_aton(PUBLIC_IP), "big")  # host byte order
GATE_PORT = 6002
GAME_IP = GATE_IP
GAME_PORT = 6003
SPAWN_FACING = (0.953, 0.0, -0.304)  # facing used when (re)spawning the player (per-battlefield spawn points: BATTLEFIELDS)
WEAPON_AMMO = 120.0  # Viper (3101): flyweight magazine +0x160 (fire mode 0 costs 4, +0x9d8)
DUMMY_HP, WEAPON_DAMAGE, RESPAWN_DELAY = 1000, 40.0, 5.0  # target dummy armor, Viper damage per shot, seconds
PLAYER_HP = 1000  # player robot armor
KILL_HONOR, KILL_PAY = 10, 5  # Honor and Bonus awarded per destroyed dummy
# dummy AI: patrol back and forth, engage the player inside AI_RANGE with bursts of AI_BURST shots
SPAWN_PROTECTION = 4.0  # seconds after a respawn before the dummies open fire
AI_TICK, AI_SPEED, AI_PATROL = 0.2, 4.0, 12.0  # seconds per update, units per second, patrol half-length
AI_RANGE, AI_BURST, AI_PAUSE, AI_HIT_CHANCE, AI_DAMAGE = 70.0, 6, 1.5, 0.5, 15.0
HIT_RADIUS, HIT_HEIGHT = 6.0, 2.5  # generous hit sphere: the client raycast passes through remote robots
WEAPON_TIMING = (0.142, 0.015, 0.02, 0.0, 0.0, 0.0)  # Viper fire modes 0/1: flyweight +0x9d4, +0xa34, +0xa38
TRAINING_ROOMS = [(1, "Training Ground 1")]  # (battlefield id, name)
# Battlefield definitions served by MapInfo; "world" is the .reb under ..\Maps\<name>\ (field semantics mostly unknown).
BATTLEFIELDS = {
    # spawn: player spawn point; dummies: target robot positions (Y is up). The client ships no spawn data, so
    # these are found by hand (a robot dropped from above falls to the ground; GetLocation shows the height).
    1: {"name": "Training Ground 1", "world": "Atrain01.reb",
        "spawn": (10.0, -69.0, -50.0), "dummies": [(24.3, -69.0, -54.6), (10.0, -69.0, -65.0)]},
    2: {"name": "Mars Front", "world": "B01.reb",
        "spawn": (30.0, 83.0, -60.0), "dummies": [(45.0, 81.0, -54.6), (40.0, 81.0, -65.0)]},
}
# Grand Battle Field list: (campaign id, campaign name, battlefront id, battlefront name, battlefield id)
CAMPAIGNS = [(1, "Mars Campaign", 1, "Northern Front", 2)]

# Login hands the client a random session key; the gate handshake echoes it back and we look the account up here.
# Keys are single-use and expire, so nobody can reach the gate as another account by guessing or forging one.
SESSIONS = {}           # session key -> (account, expiry time)
SESSION_TTL = 120.0     # seconds between login and gate connect
AUTO_REGISTER = os.environ.get("MW_AUTO_REGISTER", "1") == "1"  # unknown account name + password = new account
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
LOGIN_FAILS_MAX, LOGIN_FAILS_WINDOW = 5, 300.0  # failed logins allowed per IP per window (seconds)
# Result codes: the client shows Language/ErrorInfoENG.xml text for them.
LOGIN_FAILED = 10001    # "Invalid account or password."
LOGIN_LOCKED = 10002    # "Excessive number of retries, the account is locked, ... waiting for 5 minutes"
ERR_UNKNOWN, ERR_PROTOCOL = 3, 6
AUTH_TIMEOUT = 30.0     # seconds a connection may stay open without logging in
MAX_CONNECTIONS = 1024  # open TCP connections (login + gate together)
PRE_AUTH = {(10, 10000)}  # the only request handled before the connection is authenticated
SESSION_LOCK = threading.Lock()
# Limits on untrusted input: checked before any buffer is allocated
MAX_FRAME = 4096        # key-exchange frame (an RSA-512 key plus a short first message)
MAX_PACKET = 64 * 1024  # one encrypted packet
MAX_STR = 1024          # one string field
# Character names: 2-16 letters (any script, so Chinese names work), digits or underscore
MAX_CHARACTERS = 4  # per account
NAME_RE = re.compile(r"^[^\W]{2,16}$")
# Hall content (later: from the database / web admin)
ANNOUNCEMENTS = [
    "Welcome to the MarsWar revival server!",
    "Training Ground is open: Quick Enter to fight the target robots.",
]
START_MONEY, START_POINTS = 100000, 1000
# Military ranks: the client names levels 0..70 itself (ENG.xml CHARACTER_EXPLOIT0..70, Recruit .. Marshal). The
# exploit needed per level came from the original server; this curve is ours. Exploit = total honor earned.
def load_products():
    """ProductID -> {name, type, subtype, intro} from the client's product flyweights (data/flyweights/Product)."""
    import xml.etree.ElementTree as ET
    out = {}
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flyweights", "Product")
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".xml"):
                continue
            try:
                data = ET.parse(os.path.join(dirpath, f)).getroot().find("FileData")
                vals, key = {}, None
                for el in data:
                    if el.tag == "DataInfo":
                        key = el.get("DataName")
                    elif key:
                        vals[key] = el.get("data", "")
                pid = int(vals.get("ProductID", "0"))
                out[pid] = {"name": vals.get("ProductName", ""), "type": int(vals.get("Type", "0") or 0),
                            "subtype": int(vals.get("SubType", "0") or 0), "intro": vals.get("ProductIntro", "")}
            except (ET.ParseError, ValueError, AttributeError):
                continue
    return out


PRODUCTS = load_products()


def load_weapons():
    """Weapon flyweight id (= product id) -> (magazine, fire interval) from data/flyweights/Weapon."""
    import xml.etree.ElementTree as ET
    out = {}
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flyweights", "Weapon")
    for f in os.listdir(root) if os.path.isdir(root) else []:
        try:
            data = ET.parse(os.path.join(root, f)).getroot().find("FileData")
            vals, key = {}, None
            for el in data:
                if el.tag == "DataInfo":
                    key = el.get("DataName")
                elif key and key not in vals:
                    vals[key] = el.get("data", "")
            out[int(vals["ProductID"])] = (float(vals.get("MaxCapacitance") or 120),
                                           float(vals.get("WP1DaltaShootTime") or 0.142) or 0.142)
        except (ET.ParseError, ValueError, KeyError, AttributeError):
            continue
    return out


WEAPONS = load_weapons()


def load_robots():
    """Robot flyweight id -> (body fw, armor fw, core fw, primary slots, secondary slots), from
    data/flyweights/Robot: C_Logic_MA_* name the parts (FIDRobotBody/Armor/Core), the armor sets the slot counts."""
    import xml.etree.ElementTree as ET
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flyweights", "Robot")
    vals = {}
    for f in os.listdir(root) if os.path.isdir(root) else []:
        try:
            d = ET.parse(os.path.join(root, f)).getroot()
            oid = int(d.find("FileInfo/Info").get("ObjectID"))
            v, key = {}, None
            for el in d.find("FileData"):
                if el.tag == "DataInfo":
                    key = el.get("DataName")
                elif key and key not in v:
                    v[key] = el.get("data") or ""
            vals[oid] = v
        except (ET.ParseError, ValueError, AttributeError, TypeError):
            continue
    out = {}
    for oid, v in vals.items():
        try:
            body, armor, core = int(v["FIDRobotBody"]), int(v["FIDRobotArmor"]), int(v["FIDRobotCore"])
            a = vals[armor]
            out[oid] = (body, armor, core, int(a.get("PrimarySlotCount") or 0), int(a.get("SecondarySlotCount") or 0))
        except (KeyError, ValueError):
            continue
    return out


ROBOTS = load_robots()
DEFAULT_ROBOT = 1311  # Walker_Basic (BattleRobot)


def player_robot(cid):
    """The robot type the character fights in: MW_ROBOT if set, else the last robot talent learned (Robot tab)."""
    forced = os.environ.get("MW_ROBOT")
    if forced and forced.isdigit() and usable_robot(int(forced)):
        return int(forced)
    rid = ((STORE.by_id(cid) or {}).get("robot")) or DEFAULT_ROBOT
    return rid if usable_robot(rid) else DEFAULT_ROBOT


def usable_robot(rid):
    """Heavy armors (no equipment slots, e.g. 1301 ArmorRobot) carry their weapons as armor parts we don't build
    yet; spawning one crashes the client, so they fall back to the default robot for now."""
    r = ROBOTS.get(rid)
    return bool(r and r[3] + r[4] > 0)
DEFAULT_WEAPON = 3101  # Viper


def loadout_weapon(cid):
    """The character's weapon for battle: the newest activated, unexpired weapon item, else the Viper."""
    now = time.time()
    for it in reversed(STORE.items(cid)):
        if (it.get("active") and it["item"] in WEAPONS and PRODUCTS.get(it["item"], {}).get("type") == 8
                and (it["expires"] == 0 or it["expires"] > now)):
            return it["item"]
    return DEFAULT_WEAPON


def weapon_stats(wid):
    """(ammo, timing) for the weapon snapshot."""
    ammo, interval = WEAPONS.get(wid, (WEAPON_AMMO, WEAPON_TIMING[0]))
    return ammo, (interval,) + WEAPON_TIMING[1:]
# Shop catalog: (product id, crystal price or -1, credit price, days; 0 = forever)
SHOP = [
    (3101, 800, 80, 30), (3102, 1200, 120, 30), (3103, 1500, 150, 30), (3104, 1800, 180, 30),
    (3105, 2000, 200, 30), (3106, 2400, 240, 30), (3108, 2600, 260, 30), (3110, 3000, 300, 30),
    (3201, 300, 30, 7), (3202, 300, 30, 7), (3205, 200, 20, 7), (3206, 400, 40, 7), (3210, 500, 50, 7),
    (3213, 900, 90, 7), (3215, 600, 60, 7), (3217, 500, 50, 7),
    (3301, -1, 500, 0), (3302, -1, 500, 0),
]
RANK_THRESHOLDS = [int(50 * n ** 1.8) for n in range(71)]


# Robot tab talents: id -> (robot flyweight, column 0 Battle / 1 Engineer / 2 Scout, row, rank needed, max level)
TALENTS = {1: (1311, 0, 0, 0, 3), 2: (1314, 0, 1, 5, 3), 3: (1301, 0, 2, 10, 3), 4: (1312, 1, 0, 0, 3),
           5: (1313, 2, 0, 0, 3), 6: (1315, 2, 1, 5, 3)}
TALENT_COST = 1  # talent points per level
LOG_KEEP = 100   # event log lines kept per character

# Medals: the client ships icons icon_medal_<id>.dds for ids 101xx..105xx (5 kinds x 3 grades); names and rules are
# ours. (stat, label, [(grade threshold), ...]); battle_time is in seconds; "rank" is the rank level.
MEDAL_KINDS = [
    ("kills", "Combat Medal", "Destroy {} enemy robots.", [10, 100, 1000]),
    ("honor", "Honor Medal", "Earn {} honor.", [100, 1000, 10000]),
    ("battle_time", "Service Medal", "Fight for {} hours.", [3600, 36000, 360000]),
    ("crystal", "Bounty Medal", "Earn {} crystal in battle.", [50, 500, 5000]),
    ("rank", "Veteran Medal", "Reach rank level {}.", [5, 15, 30]),
]
GRADES = ["I", "II", "III"]


# Badges: icons icon_badge_<id>_<grade>.dds ship for ids 20101..21101, grades 1..3. Names and rules are ours.
# The client puts "Bronze"/"Silver"/"Gold" straight in front of the name, hence the leading space.
# MW_BADGES=0 turns them off (empty lists).
BADGES_ENABLED = os.environ.get("MW_BADGES", "1") == "1"
BADGE_KINDS = [
    (20101, "kills", " Shooter", "Destroy {} enemy robots.", [5, 50, 500]),
    (20201, "deaths", " Iron Will", "Get back up {} times.", [5, 50, 500]),
    (20301, "battle_time", " Veteran", "Fight for {} hours.", [3600, 36000, 360000]),
    (20401, "items", " Collector", "Own {} items.", [1, 5, 20]),
    (20501, "talents", " Expert", "Learn {} talent levels.", [1, 3, 6]),
]


def badge_counts(cid):
    st = dict(STORE.stats(cid))
    st["items"] = len(STORE.items(cid))
    st["talents"] = sum(STORE.talents(cid).values())
    return st


def badges_earned(cid):
    """[(badge id, best grade)]"""
    st = badge_counts(cid)
    out = []
    for bid, stat, _n, _t, steps in BADGE_KINDS:
        grade = sum(1 for need in steps if st.get(stat, 0) >= need)
        if grade:
            out.append((bid, grade))
    return out


def medal_list():
    """[(medal id, name, description)] for every medal."""
    out = []
    for k, (stat, label, text, steps) in enumerate(MEDAL_KINDS, 1):
        for g, need in enumerate(steps, 1):
            shown = need // 3600 if stat == "battle_time" else need
            out.append((10000 + k * 100 + g, f"{label} {GRADES[g - 1]}", text.format(shown)))
    return out


def medals_earned(stats):
    """Medal ids a character has earned from its saved stats."""
    have = dict(stats, rank=rank_for(stats.get("honor", 0)))
    return [10000 + k * 100 + g for k, (stat, _l, _t, steps) in enumerate(MEDAL_KINDS, 1)
            for g, need in enumerate(steps, 1) if have.get(stat, 0) >= need]


def rank_for(exploit):
    return max(n for n, t in enumerate(RANK_THRESHOLDS) if t <= exploit)  # crystals and points for every character until there is an economy
DEFAULT_REPLY = True  # answer unknown requests with a generic empty success
DEFAULT_REPLY_SIZE = 128  # long enough for every fixed-size reply seen (TodayMilitary reads 10 x u32 = 40 bytes)
# Requests whose zeroed default reply crashes the client ("DataError!!!" from Game::Tick):
#   (14,14057) CGS_GetCharacterTodayMilitary -> CNewHallUI::SetTodayMilitary
DEFAULT_REPLY_SKIP = set() | {tuple(map(int, x.split(":"))) for x in os.environ.get("MW_SKIP", "").split(",") if x}


def load_event_names():
    """(catalog, type) -> client observer names, from tools/eventmap.txt (generated by tools/eventmap.py)."""
    names = {}
    try:
        for line in open(os.path.join(HERE, "tools", "eventmap.txt"), encoding="utf-8"):
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit():
                names[(int(parts[0]), int(parts[1]))] = parts[2].strip()
    except OSError:
        pass
    return names


EVENT_NAMES = load_event_names()
LOG = open(os.path.join(HERE, "loginserver.log"), "a", buffering=1)


def wstr(s):
    """String as the client reads it: 7-bit varint length (.NET BinaryWriter style) + UTF-8 bytes."""
    b = s.encode("utf-8")
    n, out = len(b), b""
    while True:
        out += bytes([(n & 0x7F) | (0x80 if n > 0x7F else 0)])
        n >>= 7
        if not n:
            return out + b


def read_wstr(b, off):
    """Read a str field; raises ValueError on a malformed or oversized length (input is untrusted)."""
    n = shift = 0
    while True:
        if off >= len(b) or shift > 28:
            raise ValueError("bad string length")
        c = b[off]; off += 1
        n |= (c & 0x7F) << shift; shift += 7
        if not c & 0x80:
            break
    if n > MAX_STR or off + n > len(b):
        raise ValueError(f"string length {n} out of range")
    return b[off:off + n].decode("utf-8", "replace"), off + n


class Store:
    """Accounts and their characters, persisted to characters.json."""
    path = os.path.join(HERE, "characters.json")
    lock = threading.Lock()

    def __init__(self):
        try:
            self.data = json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {"next_id": 1000, "accounts": {}}

    dirty = False
    SAVE_INTERVAL = 5.0  # seconds; battle stats and log lines are written in batches

    def save(self):
        """Write now (call with the lock held). Atomic: a crash never leaves a half-written file."""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
        self.dirty = False

    def mark_dirty(self):
        """Save later (call with the lock held): for frequent, low-value changes like kills and log lines."""
        self.dirty = True

    def flush(self):
        with self.lock:
            if self.dirty:
                self.save()

    def flush_loop(self):
        while True:
            time.sleep(self.SAVE_INTERVAL)
            try:
                self.flush()
            except OSError as e:
                log(f"store: save failed: {e!r}")

    def characters(self, account):
        return self.data["accounts"].get(account, [])

    def create(self, account, name, **fields):
        with self.lock:
            cid = self.data["next_id"]; self.data["next_id"] += 1
            self.data["accounts"].setdefault(account, []).append(dict(id=cid, name=name, **fields))
            self.save()
            return cid

    def by_id(self, cid):
        return next((c for chars in self.data["accounts"].values() for c in chars if c["id"] == cid), None)

    def add_stats(self, cid, **deltas):
        """Add to a character's saved battle stats (kills, deaths, honor, crystal, battle_time, money)."""
        with self.lock:
            c = self.by_id(cid)
            if not c:
                return
            st = c.setdefault("stats", {})
            for k, v in deltas.items():
                st[k] = st.get(k, START_MONEY if k == "money" else START_POINTS if k == "points" else 0) + v
            self.mark_dirty()

    def buy(self, cid, item, field, price):
        """Charge price from stats[field] and add item to the character's items. False if not enough."""
        with self.lock:
            c = self.by_id(cid)
            if not c:
                return False
            st = c.setdefault("stats", {})
            have = st.get(field, START_MONEY if field == "money" else START_POINTS)
            if price < 0 or have < price:
                return False
            st[field] = have - price
            c.setdefault("items", []).append(item)
            self.save()
            return True

    def activate(self, cid, n):
        """Activate the character's n-th item (1-based): its rental time starts now."""
        with self.lock:
            items = (self.by_id(cid) or {}).get("items", [])
            if not 1 <= n <= len(items) or items[n - 1].get("active"):
                return False
            it = items[n - 1]
            it["active"] = True
            days = it.get("days", 0)
            it["expires"] = 0 if days == 0 else int(time.time()) + days * 86400
            self.save()
            return True

    def learn_talent(self, cid, tid, maxlvl):
        with self.lock:
            c = self.by_id(cid)
            if not c:
                return False
            t = c.setdefault("talents", {})
            if t.get(str(tid), 0) >= maxlvl:
                return False
            t[str(tid)] = t.get(str(tid), 0) + 1
            c["robot"] = TALENTS[tid][0]  # fight in the robot just learned
            self.save()
            return True

    def add_log(self, cid, text, kind=0):
        """Append a line to the character's event log (Info window, Event Log tab); keeps the newest LOG_KEEP."""
        with self.lock:
            c = self.by_id(cid)
            if not c:
                return
            log_ = c.setdefault("log", [])
            log_.append([int(time.time()), kind, text])
            del log_[:-LOG_KEEP]
            self.mark_dirty()

    def event_log(self, cid):
        return list((self.by_id(cid) or {}).get("log", []))

    def talents(self, cid):
        return {int(k): v for k, v in (self.by_id(cid) or {}).get("talents", {}).items()}

    def items(self, cid):
        return list((self.by_id(cid) or {}).get("items", []))

    def stats(self, cid):
        st = dict((self.by_id(cid) or {}).get("stats", {}))
        st.setdefault("money", START_MONEY)
        st.setdefault("points", START_POINTS)
        return st

    def name_taken(self, name):
        low = name.lower()
        return any(c["name"].lower() == low for chars in self.data["accounts"].values() for c in chars)

    def find(self, account, cid):
        return next((c for c in self.characters(account) if c["id"] == cid), None)


STORE = Store()
import atexit  # noqa: E402
atexit.register(STORE.flush)


class Accounts:
    """Account names and salted password hashes, kept apart from game data (accounts.json).
    The release design moves this to the login server's own database."""
    path = os.path.join(HERE, "accounts.json")
    lock = threading.Lock()

    def __init__(self):
        try:
            self.data = json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    @staticmethod
    def hash(password, salt):
        return hashlib.scrypt(password, salt=salt, n=2 ** 14, r=8, p=1, dklen=32)

    def check(self, account, password):
        """'ok', 'created' or 'bad'. password is the raw bytes the client sent."""
        with self.lock:
            rec = self.data.get(account)
            if rec is None:
                if not AUTO_REGISTER:
                    return "bad"
                salt = os.urandom(16)
                self.data[account] = {"salt": salt.hex(), "hash": self.hash(password, salt).hex(),
                                      "created": int(time.time())}
                tmp = self.path + ".tmp"
                json.dump(self.data, open(tmp, "w", encoding="utf-8"), indent=2)
                os.replace(tmp, self.path)
                return "created"
        good = hmac.compare_digest(self.hash(password, bytes.fromhex(rec["salt"])), bytes.fromhex(rec["hash"]))
        return "ok" if good else "bad"


ACCOUNTS = Accounts()
LOGIN_FAILS = {}  # ip -> [failure times]
LOGIN_FAILS_LOCK = threading.Lock()


def login_throttled(ip, failed=False):
    """True if this IP has used up its failed logins; failed=True records one more."""
    now = time.time()
    with LOGIN_FAILS_LOCK:
        fails = [t for t in LOGIN_FAILS.get(ip, []) if t > now - LOGIN_FAILS_WINDOW]
        if failed:
            fails.append(now)
        if fails:
            LOGIN_FAILS[ip] = fails
        else:
            LOGIN_FAILS.pop(ip, None)
        return len(fails) >= LOGIN_FAILS_MAX


def character_name(cid):
    """Name of a stored character by id (any account), as the client knows it from the hall."""
    for chars in STORE.data["accounts"].values():
        for c in chars:
            if c["id"] == cid:
                return c["name"]
    return ""


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")


DEBUG = os.environ.get("MW_DEBUG", "0") == "1"  # packet traces and hex dumps (reverse-engineering work)


def debug(msg):
    if DEBUG:
        log(msg)


def hexdump(b):
    return "\n".join(
        f"  {i:04x}  {b[i:i + 16].hex(' '):<48}  {''.join(chr(c) if 32 <= c < 127 else '.' for c in b[i:i + 16])}"
        for i in range(0, len(b), 16))


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_frame(conn):
    head = recv_exact(conn, 8)
    if head[:4] != MAGIC:
        raise ValueError(f"bad magic {head!r}")
    (n,) = struct.unpack("<I", head[4:])
    if n > MAX_FRAME:
        raise ValueError(f"frame too large ({n} bytes)")
    return recv_exact(conn, n)


def send_frame(conn, body):
    conn.sendall(MAGIC + struct.pack("<I", len(body)) + body)


MSG_HAS_TYPE, MSG_TRANS, MSG_REQUEST, MSG_ACK, MSG_MULTI1, MSG_MULTI2 = 0x80, 0x02, 0x04, 0x08, 0x20, 0x40


class Message:
    def __init__(self, catalog, type_=None, token=None, result=None, multi=None, flags=0, body=b""):
        self.catalog, self.type, self.token, self.result, self.multi = catalog, type_, token, result, multi
        self.flags, self.body = flags, body

    @classmethod
    def parse(cls, data):
        flags, catalog = data[0], data[1]
        off = 2
        m = cls(catalog, flags=flags)
        if flags & MSG_HAS_TYPE:
            (m.type,) = struct.unpack_from("<H", data, off); off += 2
        if flags & MSG_REQUEST:
            (m.token,) = struct.unpack_from("<I", data, off); off += 4
        elif flags & MSG_ACK:
            m.token, m.result = struct.unpack_from("<IH", data, off); off += 6
        if flags & (MSG_MULTI1 | MSG_MULTI2):
            (m.multi,) = struct.unpack_from("<I", data, off); off += 4
        m.body = data[off:]
        return m

    def pack(self):
        flags = self.flags & ~(MSG_HAS_TYPE | MSG_REQUEST | MSG_ACK)
        out = b""
        if self.type is not None:
            flags |= MSG_HAS_TYPE; out += struct.pack("<H", self.type)
        if self.result is not None:
            flags |= MSG_ACK; out += struct.pack("<IH", self.token, self.result)
        elif self.token is not None:
            flags |= MSG_REQUEST; out += struct.pack("<I", self.token)
        if flags & (MSG_MULTI1 | MSG_MULTI2):
            out += struct.pack("<I", self.multi or 0)
        return bytes([flags, self.catalog]) + out + self.body

    def __repr__(self):
        return (f"Message(flags={self.flags:#04x} catalog={self.catalog} type={self.type} token={self.token} "
                f"result={self.result} multi={self.multi} body={len(self.body)}B)")


class Session:
    def __init__(self, conn, addr):
        self.conn, self.addr = conn, addr
        self.aes_key = os.urandom(16)
        self.key_index = 7  # server picks rand()%32+1; client only checks the range
        self.rbuf = b""
        self.account = None

    # --- transport ---------------------------------------------------------------------------
    def aes(self, decrypt, data):
        c = Cipher(algorithms.AES(self.aes_key), modes.ECB())
        x = c.decryptor() if decrypt else c.encryptor()
        return x.update(data) + x.finalize()

    def checksum(self, head, payload):
        return (sum(head) + sum(payload[::self.key_index])) & 0xFF

    def recv_packet(self):
        while True:
            if len(self.rbuf) >= 2:
                flags = self.rbuf[0]
                width = {0x40: 1, 0x80: 2, 0xC0: 4}[flags & 0xC0]
                if len(self.rbuf) >= 1 + width:
                    n = int.from_bytes(self.rbuf[1:1 + width], "little")
                    if n > MAX_PACKET:
                        raise ValueError(f"packet too large ({n} bytes)")
                    total = 1 + width + n
                    if len(self.rbuf) >= total:
                        pkt, self.rbuf = self.rbuf[:total], self.rbuf[total:]
                        return self.decode_packet(pkt, width)
            chunk = self.conn.recv(65536)
            if not chunk:
                raise ConnectionError("peer closed")
            self.rbuf += chunk

    def decode_packet(self, pkt, width):
        flags, head, payload, csum = pkt[0], pkt[:1 + width], pkt[1 + width:-1], pkt[-1]
        if self.checksum(head, payload) != csum:
            log(f"{self.addr} checksum mismatch (got {csum:#x}, want {self.checksum(head, payload):#x})")
        # Every client packet seen so far is AES-encrypted regardless of flags bit1 (its meaning is still unknown).
        if payload and len(payload) % 16 == 0:
            plain = self.aes(True, payload)
            if 1 <= plain[-1] <= 16:
                return flags, plain[:len(plain) - plain[-1]]
            log(f"{self.addr} bad padding (flags={flags:#04x}), passing payload through raw")
        return flags, payload

    def send_packet(self, payload, flags=0x1E):
        pad = 16 - len(payload) % 16
        body = self.aes(False, payload + os.urandom(pad - 1) + bytes([pad]))
        n = len(body) + 1
        if n < 0x100:
            head = bytes([0x40 | flags]) + struct.pack("<B", n)
        elif n < 0x10000:
            head = bytes([0x80 | flags]) + struct.pack("<H", n)
        else:
            head = bytes([0xC0 | flags]) + struct.pack("<I", n)
        self.conn.sendall(head + body + bytes([self.checksum(head, body)]))

    def send_message(self, msg):
        debug(f"{self.addr} -> {msg}")
        self.send_packet(msg.pack())

    # --- handlers ----------------------------------------------------------------------------
    def handle(self, msg):
        if self.account is None and (msg.catalog, msg.type) not in PRE_AUTH:
            log(f"{self.addr} ignored {msg} before login")
            return
        handler = HANDLERS.get((msg.catalog, msg.type))
        if handler:
            handler(self, msg)
        else:
            debug(f"{self.addr} unhandled {EVENT_NAMES.get((msg.catalog, msg.type), '?')} {msg}\n{hexdump(msg.body)}")
            if msg.flags & MSG_REQUEST and DEFAULT_REPLY and (msg.catalog, msg.type) not in DEFAULT_REPLY_SKIP:
                # Generic success with a zeroed body: list replies read "count 0", scalars read 0, strings read "".
                # Too short a reply makes the client's stream reader throw (DataError), so pad generously.
                self.reply(msg, bytes(DEFAULT_REPLY_SIZE))

    def reply(self, req, body=b"", result=0):
        self.send_message(Message(req.catalog, req.type, token=req.token, result=result, body=body,
                                  flags=req.flags & (MSG_MULTI1 | MSG_MULTI2), multi=req.multi))

    def on_get_character_list(self, msg):
        # CS_GetCharacterList (13,13000); reply read by CS_GetCharacterList_LoginWorld_Observer (Game.dll 0x10401930):
        #   u32 count | count x (u32 id | str name | u8 gender? | u32 | u8 | u8 state)
        # count 0 opens Choose Nation / Create Character. With one character, state 2/4/8 reopens Choose Nation,
        # anything else makes the client send CS_CharacterOnline.
        chars = STORE.characters(self.account)
        body = struct.pack("<I", len(chars))
        for c in chars:
            body += struct.pack("<I", c["id"]) + wstr(c["name"]) + struct.pack("<BIBB", c["gender"], c["model"], 0, c["state"])
        log(f"{self.addr} character list for {self.account!r}: {[c['name'] for c in chars]}")
        self.reply(msg, body)

    def on_active_character(self, msg):
        # CS_Active_Character (13,13006) = create character. Request: str name | u8 gender | u32 model? | u32 | u32 | u8?..
        # Reply read by CS_Active_Character_Observer (0x10401000): u32 new character id; client then re-requests the list.
        if self.account is None or len(STORE.characters(self.account)) >= MAX_CHARACTERS:
            log(f"{self.addr} character create refused (not logged in or character limit)")
            self.reply(msg, result=ERR_UNKNOWN)
            return
        try:
            name, off = read_wstr(msg.body, 0)
        except ValueError:
            name, off = "", len(msg.body)
        rest = msg.body[off:]
        # never trust the client's own checks: validate the name here before storing or showing it to anyone
        if not NAME_RE.match(name) or STORE.name_taken(name):
            log(f"{self.addr} rejected character name {name!r}")
            self.reply(msg, result=13002 if NAME_RE.match(name) else ERR_PROTOCOL)  # 13002 "Character already exist."
            return
        gender = rest[0] if rest else 0
        model = struct.unpack_from("<I", rest, 1)[0] if len(rest) >= 5 else 0
        cid = STORE.create(self.account, name, gender=gender, model=model, state=1, raw=rest.hex())
        log(f"{self.addr} created character {name!r} id={cid} for {self.account!r} extra={rest.hex()}")
        self.reply(msg, struct.pack("<I", cid))

    def on_character_online(self, msg):
        # CS_CharacterOnline (13,13004). Reply read by CS_CharacterOnline_LoginWorld_Observer (0x10402510), which builds
        # CNewHallUI. Body = character descriptor (deserializer Game.dll 0x1031a010):
        #   u32 id | u8 state | u32 | u32 | u8 (101..110 sets a flag) | str name | str | u8 rank level (character
        #   +0x44, "Lv." in the hall) | u8 gender | u32 model
        #   | str | str | str | u32 n | n x (u32 | u16 | u8 | u8)
        debug(f"{self.addr} character online request body={msg.body.hex()}")
        chars = STORE.characters(self.account)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else None
        c = STORE.find(self.account, cid) or (chars[0] if chars else None)
        if not c:
            self.reply(msg, result=13000)  # "This character does not exist"
            return
        body = (struct.pack("<IBIIB", c["id"], c["state"], 0, 0, 0) + wstr(c["name"]) + wstr("")
                + struct.pack("<BBI", rank_for(STORE.stats(c["id"]).get("honor", 0)), c["gender"], c["model"])
                + wstr("") + wstr("") + wstr("")
                + struct.pack("<I", 0))
        self.reply(msg, body)

    def on_get_shoot_rooms(self, msg):
        # GetShootRoomIDByCountryID (18,18029), request: u8 country. Polled every 10s by the hall's Training Ground tab.
        # Reply read by GetShootRoomIDByCountryIDObserver (Game.dll 0x1041b850):
        #   u32 count | count x (u32 battlefield id | u32 | str name)
        # For each id the client calls GetBattleFieldIntro, RequestMapStatus (18021) and RequestMapInfo (17008).
        body = struct.pack("<I", len(TRAINING_ROOMS))
        for rid, name in TRAINING_ROOMS:
            body += struct.pack("<II", rid, 0) + wstr(name)
        self.reply(msg, body)

    def on_map_info(self, msg):
        # MapInfo (17,17008), request: u32 battlefield id. Reply read by MapInfoObserver (Game.dll 0x1041c910) into a
        # BATTLE_FIELD_INFO passed to BattleFieldManager::AddBattleFieldIntro:
        #   u32 id | str name | str desc | u8 x4 | u8 type | u32 x4 | astr world file | f32 x3 | astr
        bfid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        bf = BATTLEFIELDS.get(bfid)
        if not bf:
            self.reply(msg, result=17000)  # "This battlefield does not exist."
            return
        body = (struct.pack("<I", bfid) + wstr(bf["name"]) + wstr(bf.get("desc", ""))
                + bytes(bf.get("b4", (0, 0, 0, 0))) + struct.pack("<B", bf.get("type", 0))
                + struct.pack("<IIII", *bf.get("u4", (0, 0, 0, 0))) + wstr(bf["world"])
                + struct.pack("<fff", *bf.get("pos", (0.0, 0.0, 0.0))) + wstr(bf.get("extra", "")))
        self.reply(msg, body)

    def on_map_status(self, msg):
        # MapStatus (18,18021), request: u32 battlefield id. Reply read by MapStatusObserver (Game.dll 0x1041d410):
        #   u32 status | u32 count | count x (u8 nation | u32 value)  -> BattleFieldManager::SetMapStatus
        self.reply(msg, struct.pack("<II", 1, 0))

    def on_campaigns(self, msg):
        # GetOpenedCampaignInfo (17,17110), polled by the Grand Battle Field tab. Reply read by
        # GetOpenedCampaignInfoObserver (Game.dll 0x1041bee0):
        #   u32 count | count x (u32 campaign id | str name | u32 battlefront id | str name | u32 battlefield id)
        # For each battlefield the client then requests MapStatus (18,18021) and MapInfo (17,17008).
        body = struct.pack("<I", len(CAMPAIGNS))
        for cid, cname, fid, fname, bfid in CAMPAIGNS:
            body += struct.pack("<I", cid) + wstr(cname) + struct.pack("<I", fid) + wstr(fname) + struct.pack("<I", bfid)
        self.reply(msg, body)

    def on_apply_enter(self, msg):
        # QS_ApplyEnter (18,18000), request: u32 character id | u32 battlefield id | u32 | u16 (seen: e8030000 01000000 07000000 0000)
        # Reply read by QS_ApplyEnter_UserEntry_Observer (Game.dll 0x1040bf50); success body:
        #   u32 ticket | u32 game server ip | u16 game server port | u32
        # The client then connects to that address as remote type 19 (game server).
        # The last u32 is echoed by the client as the first u32 of its game-server connect stream: a battle ticket
        # telling the game server which character and battlefield the connection is for.
        debug(f"{self.addr} apply enter body={msg.body.hex()}")
        if len(msg.body) < 8:
            return self.reply(msg, result=ERR_PROTOCOL)
        cid, bfid = struct.unpack_from("<II", msg.body)
        if FORCE_BF in BATTLEFIELDS:
            bfid = FORCE_BF  # dev: everyone into one battlefield
        if not self.own_character(cid):
            return self.reply(msg, result=13000)
        if bfid not in BATTLEFIELDS:
            return self.reply(msg, result=17000)
        self.reply(msg, struct.pack("<IIHI", 1, GAME_IP, GAME_PORT, issue_ticket(cid, bfid)))

    def on_announcements(self, msg):
        # UpdateAllAnnouncement (21,21047). Reply read by UpdateAllAnnouncementObserver (Game.dll 0x1051e040):
        #   u32 count | count x (u32 id | str text | u32) -> CHallMapUI::AddAnnouncement (the hall's scrolling bar)
        body = struct.pack("<I", len(ANNOUNCEMENTS))
        for i, text in enumerate(ANNOUNCEMENTS, 1):
            body += struct.pack("<I", i) + wstr(text) + struct.pack("<I", 0)
        self.reply(msg, body)

    def on_today_military(self, msg):
        # CGS_GetCharacterTodayMilitary (14,14057): reply = 10 x u32 (stTodayMilitary) -> CNewHallUI::SetTodayMilitary,
        # the hall's Battle Report panel: battlefield time (s) | kills | deaths | honor | crystal, then the same five
        # for arena (the panel adds the two honor and the two crystal values). We report lifetime totals as "today".
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        st = STORE.stats(cid)
        self.reply(msg, struct.pack("<10I", int(st.get("battle_time", 0)), st.get("kills", 0), st.get("deaths", 0),
                                    st.get("honor", 0), st.get("crystal", 0), 0, 0, 0, 0, 0))

    def on_exploit(self, msg):
        # CG_Character_Get_Exploit (14,14010), request: u32 character id. Reply: u32 exploit (shown as "exploit /
        # next rank" in the hall) | 4 x u32 (summed by the client) | u8 (+0xbc) | u32 (+0xc0)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        exploit = STORE.stats(cid).get("honor", 0)
        self.reply(msg, struct.pack("<IIIIIBI", exploit, exploit, 0, 0, 0, 0, 0))

    def on_rank_table(self, msg):
        # CGS_GetAllMilitaryRank (14,14040). Reply read by CGS_GetAllMilitaryRankObserver (Game.dll 0x10357ad0):
        #   u32 count | count x (u8 level | str name | u32 exploit needed)
        # The client finds its own rank (character +0x44) and the next one: their thresholds go to +0x3bc / +0x3c0
        # and drive the rank progress bar.
        body = struct.pack("<I", len(RANK_THRESHOLDS))
        for n, t in enumerate(RANK_THRESHOLDS):
            body += struct.pack("<B", n) + wstr(f"CHARACTER_EXPLOIT{n}") + struct.pack("<I", t)
        self.reply(msg, body)

    def on_money(self, msg):
        # GS_UpdateCharacterMoney (24,24019), request: u32 character id. Reply: u32 money (character +0x1ac,
        # raises the client's money-changed event 12160).
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        self.reply(msg, struct.pack("<I", int(STORE.stats(cid)["money"])))

    def on_item_flyweight(self, msg):
        # GS_UpdateItemFlyweight (24,24002), request: u32 item id. The shop asks for each item it shows. Reply read by
        # GS_UpdateItemFlyweightObserver (0x10359240), stored at manager +0x1cc as:
        #   str name (+0x24) | u8 | u8 (+0x10) | u8 price kind (+0x14) | str (+0x40) | u32 (+0x5c) | u32 (+0x8)
        # Price kind 2 = rented by days. Any other kind is "per use", and the English template for that
        # (GUITEXT_MARKET_*_TIMES_TEMPLATE "%dCrystal(%s)") has %s where the client passes a number: it crashes.
        iid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        name = PRODUCTS.get(iid, {}).get("name", f"Item {iid}")
        debug(f"{self.addr} item flyweight {iid} -> {name}")
        self.reply(msg, wstr(name) + struct.pack("<BBB", 1, 1, 2) + wstr("") + struct.pack("<II", iid, 0))

    @staticmethod
    def goods_record(gid):
        """Goods flyweight record (manager +0x200, built by 0x1032e620):
        str name | u32 item id (+0x20) | u8 | str | str | u8 | u32 | u32 | u32 picture id (+0x6c) | u32"""
        pid = SHOP[gid - 1][0] if 1 <= gid <= len(SHOP) else 0
        name = PRODUCTS.get(pid, {}).get("name", "")
        return wstr(name) + struct.pack("<IB", pid, 0) + wstr("") + wstr("") + struct.pack("<BIIII", 0, 0, 0, pid, 0)

    def on_goods_flyweights(self, msg):
        # GS_RequestAllGoodsFlyweight (24,24008). Reply read by GS_RequestAllGoodsFlyweightObserver (0x1035a380):
        #   u32 count | count x (u32 goods id | goods record)
        body = struct.pack("<I", len(SHOP))
        for gid in range(1, len(SHOP) + 1):
            body += struct.pack("<I", gid) + self.goods_record(gid)
        self.reply(msg, body)

    def on_goods_flyweight(self, msg):
        # GS_RequestGoodsFlyweight (24,24009), request: u32 goods id. Reply read by
        # GS_RequestGoodsFlyweightObserver (0x1035a030): one goods record (the id comes from the request).
        gid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        self.reply(msg, self.goods_record(gid))

    def on_talents(self, msg):
        # CGS_UpdateTalentInfo (14,14042), request: u8 nation (seen 1). Reply read by CGS_UpdateTalentInfoObserver
        # (0x1035fe20), 0x40-byte records for CTalentConfigUI::InitTalent:
        #   u32 count | count x (u32 talent id | str name | u8 (+0x24) | u8 (+0x25) | u8 order (+0x26)
        #   | u8 rank needed (+0x27) | u8 (+0x28) | u8 (+0x29, 1 = true) | u32 robot flyweight (+0x2c, FRobot))
        # +0x26 is the column (0 Battle, 1 Engineer, 2 Scout), +0x28 the row; locked talents show "need <rank>".
        body = struct.pack("<I", len(TALENTS))
        for tid, (fw, col, row, rank, maxlvl) in TALENTS.items():
            name = PRODUCTS.get(fw, {}).get("name", f"Robot {fw}")
            body += struct.pack("<I", tid) + wstr(name) + struct.pack("<BBBBBBI", maxlvl, 0, col, rank, row, 1, fw)
        self.reply(msg, body)

    def talent_points(self, cid):
        """(total, remaining): one point per rank level."""
        total = rank_for(STORE.stats(cid).get("honor", 0))
        spent = sum(STORE.talents(cid).values())
        return total, max(0, total - spent)

    def on_talent_remain(self, msg):
        # CGS_UpdateCharacterRemainTalentPoint (14,14043), request: u32 character id. Reply read by
        # CGS_UpdateCharacterRemainTalentPointObserver (0x10358fe0):
        #   u32 total (char +0x180) | u32 remaining (+0x17c; 0 disables upgrades) | str last reset time | u32 | u32
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        total, remaining = self.talent_points(cid) if self.own_character(cid) else (0, 0)
        self.reply(msg, struct.pack("<II", total, remaining) + wstr("") + struct.pack("<II", 0, 0))

    def on_all_medals(self, msg):
        # CGS_UpdateAllMedalInfo (14,14019). Reply read by CGS_UpdateAllMedalInfoObserver (0x1035e740):
        #   u32 count | count x (u16 medal id | str name | str description); icon = icon_medal_<id>.dds
        medals = medal_list()
        body = struct.pack("<I", len(medals))
        for mid, name, text in medals:
            body += struct.pack("<H", mid) + wstr(name) + wstr(text)
        self.reply(msg, body)

    def on_my_medals(self, msg):
        # CGS_UpdateMyMedal (14,14007), request: u32 character id. Reply read by CGS_UpdateMyMedalObserver
        # (0x1035b570): u32 count | count x u16 medal id
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        mine = medals_earned(STORE.stats(cid)) if STORE.by_id(cid) else []
        self.reply(msg, struct.pack("<I", len(mine)) + b"".join(struct.pack("<H", m) for m in mine))

    def on_all_badges(self, msg):
        # CGS_UpdateAllBadgeInfo (14,14027). Reply read by CGS_UpdateAllBadgeInfoObserver (0x1035b050), one entry
        # per badge grade: u32 count | count x (u16 badge id | str name | str grade text | u8 grade)
        rows = []
        for bid, stat, name, text, steps in BADGE_KINDS if BADGES_ENABLED else []:
            for g, need in enumerate(steps, 1):
                shown = need // 3600 if stat == "battle_time" else need
                rows.append(struct.pack("<H", bid) + wstr(name) + wstr(text.format(shown)) + struct.pack("<B", g))
        self.reply(msg, struct.pack("<I", len(rows)) + b"".join(rows))

    def on_my_badges(self, msg):
        # CGS_UpdateMyBadge (14,14028), request: u32 character id. Reply read by CGS_UpdateMyBadgeObserver
        # (0x1035b350): u32 count | count x (u16 badge id | u8 grade)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        mine = badges_earned(cid) if STORE.by_id(cid) and BADGES_ENABLED else []
        self.reply(msg, struct.pack("<I", len(mine)) + b"".join(struct.pack("<HB", b, g) for b, g in mine))

    def on_badge_progress(self, msg):
        # CGS_UpdateMyBadge (14,14028) as sent by the Info window, whose reply observer is CGS_GetCharacterAllBadge
        # (0x1035c780) -- not CGS_UpdateMyBadgeObserver's (u16 | u8) layout:
        #   u32 count | count x (u16 badge id | u8 grade | u32 progress (MyBadgeInfo +0x40))
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        mine = badges_earned(cid) if STORE.by_id(cid) and BADGES_ENABLED else []
        st = badge_counts(cid) if mine else {}
        stat_of = {bid: stat for bid, stat, _n, _t, _s in BADGE_KINDS}
        body = struct.pack("<I", len(mine))
        for bid, grade in mine:
            body += struct.pack("<HBI", bid, grade, int(st.get(stat_of[bid], 0)))
        self.reply(msg, body)

    def on_military(self, msg):
        # CGS_GetCharacterMilitarySuccesses (14,14056), request: u32 character id | u8 kind (1 battlefield, 2 arena).
        # Reply read by CGS_GetCharacterMilitarySuccesses (0x10356a50), Info window Battle tab:
        #   u32 battle time (s) | u32 victories | u32 kills | u32 deaths | u32 vehicles destroyed
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        kind = msg.body[4] if len(msg.body) >= 5 else 1
        st = STORE.stats(cid) if STORE.by_id(cid) else {}
        if kind == 1:
            vals = (int(st.get("battle_time", 0)), st.get("wins", 0), st.get("kills", 0), st.get("deaths", 0),
                    st.get("vehicles", 0))
        else:
            vals = (0, 0, 0, 0, 0)  # no arena yet
        self.reply(msg, struct.pack("<5I", *(int(v) for v in vals)))

    def on_event_log(self, msg):
        # CGS_GetCharacterLog (14,14003), request: u32 character id | u8 | u32 page (1-based) | u32 per page (50).
        # Reply read by CGS_GetCharacterLog (Game.dll 0x1035cb40) into CInfomationUI::SetLogInfo, newest first:
        #   u32 total | u32 count | count x (u32 id | u8 kind | str text (shown as " - text") | str time | 6 x u32)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        page, per = struct.unpack_from("<II", msg.body, 5) if len(msg.body) >= 13 else (1, 50)
        per = min(max(per, 1), 100)
        allines = STORE.event_log(cid)[::-1] if self.own_character(cid) else []
        lines = allines[(max(page, 1) - 1) * per:][:per]
        body = struct.pack("<II", len(allines), len(lines))
        for n, (when, kind, text) in enumerate(lines, 1):
            stamp = time.strftime("%m-%d %H:%M", time.localtime(when))
            body += struct.pack("<IB", n, kind) + wstr(text) + wstr(stamp) + bytes(24)
        self.reply(msg, body)

    def on_talent_costs(self, msg):
        # CGS_CharacterLearnTalentCost (14,14058). Reply read by CGS_CharacterLearnTalentCostObserver (0x1035b750):
        #   u32 count | count x (u32 talent id | u32 levels | levels x u32 points for that level)
        body = struct.pack("<I", len(TALENTS))
        for tid, (_fw, _col, _row, _rank, maxlvl) in TALENTS.items():
            body += struct.pack("<II", tid, maxlvl) + struct.pack(f"<{maxlvl}I", *([TALENT_COST] * maxlvl))
        self.reply(msg, body)

    def on_learn_talent(self, msg):
        # CGS_CharacterLearnTalent (14,14047), request: u32 character id | u32 talent id. The upgrade button.
        # Reply read by CGS_CharacterLearnTalentObserver (0x10355790).
        if len(msg.body) < 8:
            return self.reply(msg, b"", result=ERR_PROTOCOL)
        cid, tid = struct.unpack_from("<II", msg.body)
        ok, err = False, 14004  # "This type does not exist."
        if self.own_character(cid) and tid in TALENTS:
            _fw, _col, _row, rank, maxlvl = TALENTS[tid]
            _total, remaining = self.talent_points(cid)
            honor = STORE.stats(cid).get("honor", 0)
            if rank_for(honor) < rank:
                err = 14007  # "Military rank is not enough to learn the robot."
            elif remaining < TALENT_COST:
                err = 14005  # "Do not have enough talent points."
            elif STORE.talents(cid).get(tid, 0) >= maxlvl:
                err = 14008  # "The robot has reached the highest level."
            else:
                ok = STORE.learn_talent(cid, tid, maxlvl)
        log(f"{self.addr} learn talent cid={cid} talent={tid} -> {'ok' if ok else 'refused'}")
        if ok:
            fw = TALENTS[tid][0]
            STORE.add_log(cid, f"Learned {PRODUCTS.get(fw, {}).get('name', fw)} level {STORE.talents(cid).get(tid, 0)}")
        # Reply: u32 remaining points (the client adds the level itself, then sets remaining)
        if not ok:
            return self.reply(msg, b"", result=err)
        self.reply(msg, struct.pack("<I", self.talent_points(cid)[1]))

    def on_talent_levels(self, msg):
        # CGS_UpdateCharacterTalentPoint (14,14044), request: u32 character id. Reply read by
        # CGS_UpdateCharacterTalentPointObserver (0x1035bfc0): u32 count | count x (u32 talent id | u8 level)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        levels = STORE.talents(cid) if self.own_character(cid) else {}
        body = struct.pack("<I", len(levels))
        for tid, lvl in levels.items():
            body += struct.pack("<IB", int(tid), lvl)
        self.reply(msg, body)

    def on_goods_in_sell(self, msg):
        # GS_UpdateGoodsInSell (24,24010), request: u32 | u32 | u32 (seen 2, 1, 0x7fffffff). Reply read by
        # GS_UpdateGoodsInSellObserver (0x103599d0), one 0x80-byte record per goods at manager +0x234:
        #   u32 | u32 count | count x (u32 goods id | str | str | str | u8 | u32 picture id (GoodsPic_<n>.dds)
        #   | u32 item id (looked up with 24002) | u32 credit price | u32 | u32 crystal price (-1 = not for
        #   crystal) | u32 | u32 uses | u32 minutes (> 1000000 = forever) | u8 filter (1 Crystal, 2 Credit))
        # CMarketUI::SelectSort (0x10550460) lists the goods whose filter byte matches the clicked filter.
        body = struct.pack("<II", 0, len(SHOP))
        for gid, (pid, crystal, credit, days) in enumerate(SHOP, 1):
            minutes = 2000000 if days == 0 else days * 1440
            body += struct.pack("<I", gid) + wstr("") + wstr("") + wstr("")
            body += struct.pack("<B8IB", 0, pid, pid, credit, 0, crystal & 0xFFFFFFFF, 0, 0, minutes, 1 if crystal >= 0 else 2)
        self.reply(msg, body)

    def on_points(self, msg):
        # GS_UpdateCharacterPoint (24,24020), request: u32 character id. Reply: u32 points (Credit).
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        self.reply(msg, struct.pack("<I", int(STORE.stats(cid)["points"])))

    def own_character(self, cid):
        """True when cid is one of this session's account characters (never trust ids from the client)."""
        return self.account is not None and STORE.find(self.account, cid) is not None

    def on_buy(self, msg):
        # GS_RequestBuyGoods (24,24021), request: u32 character id | u8 | u8 | u32 goods id. The reply has no body:
        # result 0 = bought (the client then re-reads money 24019 and items 24006), otherwise the client shows
        # the error text for that result code.
        if len(msg.body) < 10:
            return self.reply(msg, b"", result=ERR_PROTOCOL)
        cid, a, b, gid = struct.unpack_from("<IBBI", msg.body)
        if not self.own_character(cid) or not 1 <= gid <= len(SHOP):
            log(f"{self.addr} buy refused: cid={cid} goods={gid}")
            return self.reply(msg, b"", result=24004)  # "Such items do not exist."
        pid, crystal, credit, days = SHOP[gid - 1]
        field, price = ("money", crystal) if crystal >= 0 else ("points", credit)
        ok = STORE.buy(cid, {"goods": gid, "item": pid, "days": days, "expires": 0, "active": False}, field, price)
        log(f"{self.addr} buy cid={cid} goods={gid} item={pid} {field}-{price} flags={a},{b} -> {'ok' if ok else 'refused'}")
        if ok:
            unit = "Crystal" if field == "money" else "Credit"
            STORE.add_log(cid, f"Bought {PRODUCTS.get(pid, {}).get('name', pid)} for {price} {unit}")
        # 24010 "Crystal of your balance is insufficient", 24011 "Your credit is not enough"
        self.reply(msg, b"", result=0 if ok else (24010 if field == "money" else 24011))

    def on_my_goods(self, msg):
        # GS_RequestMyGoods (24,24011), request: u32 character id | u8 | u32 | u32 (seen 2, 1, 10000).
        # Reply read by GS_RequestMyGoodsObserver (0x10355f90): u32 | u32 count | count x (u32 instance id
        # | u32 goods id). These are bought, not yet activated goods (Warehouse "Not Activated").
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        items = STORE.items(cid) if self.own_character(cid) else []
        mine = [(n, it) for n, it in enumerate(items, 1) if not it.get("active") and "goods" in it]
        body = struct.pack("<II", len(mine), len(mine))
        for n, it in mine:
            body += struct.pack("<II", n, it["goods"])
        self.reply(msg, body)

    def on_my_active_goods(self, msg):
        # GS_RequestMyActivedGoods (24,24003), same request as 24011. Reply read by
        # GS_RequestMyActivedGoodsObserver (0x10359dd0): u32 | u32 count | count x (u32 | u32 | str | u8)
        #   (u32 item id (+0xc, picture) | u32 instance id (+0x10) | str expiry date (the client cuts it at the
        #   first ':') | u8 1; 0 = expired)
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        items = STORE.items(cid) if self.own_character(cid) else []
        now = int(time.time())
        act = [(n, it) for n, it in enumerate(items, 1)
               if it.get("active") and "goods" in it and (it["expires"] == 0 or it["expires"] > now)]
        body = struct.pack("<II", len(act), len(act))
        for n, it in act:
            when = "" if it["expires"] == 0 else time.strftime("%Y-%m-%d ", time.localtime(it["expires"]))
            body += struct.pack("<II", it["item"], n) + wstr(when) + b"\1"
        self.reply(msg, body)

    def on_activate_goods(self, msg):
        # GS_RequestActiveGoods (24,24015), request: u32 character id | u32 instance id. No reply body; on result 0
        # the client calls CWareHouseUI::OnActiveGoods and re-reads the warehouse lists.
        if len(msg.body) < 8:
            return self.reply(msg, b"", result=ERR_PROTOCOL)
        cid, n = struct.unpack_from("<II", msg.body)
        ok = self.own_character(cid) and STORE.activate(cid, n)
        log(f"{self.addr} activate cid={cid} instance={n} -> {'ok' if ok else 'refused'}")
        if ok:
            it = STORE.items(cid)[n - 1]
            STORE.add_log(cid, f"Activated {PRODUCTS.get(it['item'], {}).get('name', it['item'])}")
        self.reply(msg, b"", result=0 if ok else 24005)  # "This activated item does not exist."

    def on_items_in_use(self, msg):
        # GS_UpdateItemInUse (24,24006), request: u32 character id. Reply read by GS_UpdateItemInUseObserver
        # (0x10359550): u32 slots | u32 count | count x (u32 slot (1-based) | u32 item id | u8 | u32 minutes left
        # | str)
        # Items listed here count as equipped: the warehouse hides them. Nothing equips items yet, so the list
        # stays empty until the loadout is worked out.
        cid = struct.unpack_from("<I", msg.body)[0] if len(msg.body) >= 4 else 0
        items = [it for it in STORE.items(cid) if it.get("equipped")] if self.own_character(cid) else []
        now = int(time.time())
        items = [it for it in items if it.get("active") and (it["expires"] == 0 or it["expires"] > now)]
        body = struct.pack("<II", max(32, len(items)), len(items))
        for slot, it in enumerate(items, 1):
            left = 2000000 if it["expires"] == 0 else (it["expires"] - now) // 60
            body += struct.pack("<IIBI", slot, it["item"], 0, left) + wstr("")
        self.reply(msg, body)

    def on_login(self, msg):
        # body: u32 version | str8 account | str8 password | u32 region(10102) | u32 1 | str8 machine hash
        b = msg.body
        try:
            off = 0
            (version,) = struct.unpack_from("<I", b, off); off += 4
            strs = []
            for _ in range(2):
                n = b[off]
                if off + 1 + n > len(b):
                    raise IndexError("string past end")
                strs.append(b[off + 1:off + 1 + n]); off += 1 + n
            account, password = strs[0].decode("latin-1"), strs[1]
            region, unk = struct.unpack_from("<II", b, off); off += 8
            n = b[off]; machine = b[off + 1:off + 1 + n].decode("latin-1")
        except (IndexError, struct.error):
            log(f"{self.addr} malformed login")
            return self.reply(msg, result=LOGIN_FAILED)
        ip = self.addr[0]
        if login_throttled(ip):
            log(f"{self.addr} login refused: too many failures")
            return self.reply(msg, result=LOGIN_LOCKED)
        if not ACCOUNT_RE.match(account) or not password:
            login_throttled(ip, failed=True)
            log(f"{self.addr} login refused: bad account name {account[:40]!r}")
            return self.reply(msg, result=LOGIN_FAILED)
        status = ACCOUNTS.check(account, password)
        if status == "bad":
            login_throttled(ip, failed=True)
            log(f"{self.addr} login refused: wrong password for {account!r}")
            return self.reply(msg, result=LOGIN_FAILED)
        log(f"{self.addr} LOGIN account={account!r} ({status}) version={version} region={region} unk={unk} "
            f"machine={machine}")
        # success body as read by the (10,10000) observer in Game.dll (0x103fe961):
        #   u32 user id | u32 next-server ip | u16 next-server port | str session key | 8 bytes | 4 bytes | u32 ip2 | u8 flag
        self.account = account
        key = secrets.token_hex(16)
        with SESSION_LOCK:
            now = time.time()
            for k in [k for k, (_, exp) in SESSIONS.items() if exp < now]:
                del SESSIONS[k]
            SESSIONS[key] = (account, now + SESSION_TTL)
        body = (struct.pack("<IIH", 1, GATE_IP, GATE_PORT) + wstr(key)
                + b"\0" * 8 + b"\0" * 4 + struct.pack("<IB", GATE_IP, 1))  # last u8: ID verified (LocalUser +0x7d); 0 shows the anti-addiction banner
        self.reply(msg, body)

    def key_exchange(self):
        body = recv_frame(self.conn)
        status, a, b, keylen = struct.unpack_from("<IIII", body)
        der = body[16:16 + keylen]
        first_msg = body[16 + keylen:]
        debug(f"{self.addr} kx: status={status} a={a} b={b} keylen={keylen} first_msg={len(first_msg)}B")
        # gate connections send "<session key> <hash>" as the first message; recover the account from it
        if len(first_msg) > 4:
            key, _ = read_wstr(first_msg, 0)
            with SESSION_LOCK:
                account, expiry = SESSIONS.pop(key.split(" ")[0], (None, 0))
            if account and expiry >= time.time():
                self.account = account
                log(f"{self.addr} gate session for account {self.account!r}")
        pub = serialization.load_der_public_key(der)
        plain = struct.pack("<iH", self.key_index, 0) + self.aes_key
        ct = pub.encrypt(plain, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
        send_frame(self.conn, struct.pack("<I", 1) + ct)
        debug(f"{self.addr} kx reply sent")

    def run(self):
        try:
            self.conn.settimeout(AUTH_TIMEOUT)
            self.key_exchange()
            while True:
                if self.account is not None:
                    # the gate connection idles for the whole battle; a timeout makes the client quit
                    self.conn.settimeout(None)
                flags, payload = self.recv_packet()
                msg = Message.parse(payload)
                debug(f"{self.addr} <- pkt flags={flags:#04x} {msg}")
                self.handle(msg)
        except Exception as e:
            log(f"{self.addr} error {e!r}")
        finally:
            log(f"{self.addr} closed")
            self.conn.close()
            CONNECTIONS.release()


# (event catalog, event type) -> handler. Names come from the client's observer class names (RTTI).
HANDLERS = {
    (10, 10000): Session.on_login,               # LS_ValidatePassword
    (13, 13000): Session.on_get_character_list,  # CS_GetCharacterList
    (13, 13004): Session.on_character_online,    # CS_CharacterOnline
    (13, 13006): Session.on_active_character,    # CS_Active_Character (create)
    (18, 18029): Session.on_get_shoot_rooms,     # GetShootRoomIDByCountryID (Training Ground list)
    (17, 17008): Session.on_map_info,            # MapInfo
    (18, 18021): Session.on_map_status,          # MapStatus
    (18, 18000): Session.on_apply_enter,         # QS_ApplyEnter (enter a battlefield / training ground)
    (21, 21047): Session.on_announcements,       # UpdateAllAnnouncement (hall scrolling bar)
    (14, 14057): Session.on_today_military,      # CGS_GetCharacterTodayMilitary (Battle Report panel)
    (14, 14010): Session.on_exploit,             # CG_Character_Get_Exploit (rank / experience)
    (14, 14040): Session.on_rank_table,          # CGS_GetAllMilitaryRank
    (24, 24019): Session.on_money,               # GS_UpdateCharacterMoney
    (24, 24008): Session.on_goods_flyweights,    # GS_RequestAllGoodsFlyweight
    (14, 14042): Session.on_talents,             # CGS_UpdateTalentInfo (Robot tab)
    (14, 14043): Session.on_talent_remain,       # CGS_UpdateCharacterRemainTalentPoint
    (14, 14044): Session.on_talent_levels,       # CGS_UpdateCharacterTalentPoint
    (14, 14058): Session.on_talent_costs,        # CGS_CharacterLearnTalentCost
    (14, 14019): Session.on_all_medals,          # CGS_UpdateAllMedalInfo
    (14, 14007): Session.on_my_medals,           # CGS_UpdateMyMedal
    (14, 14027): Session.on_all_badges,          # CGS_UpdateAllBadgeInfo
    (14, 14028): Session.on_badge_progress,      # CGS_UpdateMyBadge (Info window reads it with CGS_GetCharacterAllBadge)
    (14, 14003): Session.on_event_log,           # CGS_GetCharacterLog (Info window, Event Log tab)
    (14, 14056): Session.on_military,            # CGS_GetCharacterMilitarySuccesses (Info window, Battle tab)
    (14, 14047): Session.on_learn_talent,        # CGS_CharacterLearnTalent
    (24, 24009): Session.on_goods_flyweight,     # GS_RequestGoodsFlyweight
    (24, 24002): Session.on_item_flyweight,      # GS_UpdateItemFlyweight
    (24, 24021): Session.on_buy,                 # GS_RequestBuyGoods
    (24, 24006): Session.on_items_in_use,        # GS_UpdateItemInUse
    (24, 24011): Session.on_my_goods,            # GS_RequestMyGoods (warehouse)
    (24, 24003): Session.on_my_active_goods,     # GS_RequestMyActivedGoods (warehouse)
    (24, 24015): Session.on_activate_goods,      # GS_RequestActiveGoods (warehouse Apply)
    (24, 24010): Session.on_goods_in_sell,       # GS_UpdateGoodsInSell
    (17, 17110): Session.on_campaigns,           # GetOpenedCampaignInfo (Grand Battle Field list)
    (24, 24020): Session.on_points,              # GS_UpdateCharacterPoint
}


CONNECTIONS = threading.BoundedSemaphore(MAX_CONNECTIONS)


def serve(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(16)
    log(f"listening on {port}")
    while True:
        c, a = s.accept()
        if not CONNECTIONS.acquire(blocking=False):
            log(f"[{port}] connect from {a} refused: {MAX_CONNECTIONS} connections open")
            c.close()
            continue
        log(f"[{port}] connect from {a}")
        threading.Thread(target=Session(c, a).run, daemon=True).start()


GAME_LOCK = threading.RLock()  # FDP peer state is shared by the UDP loop and the damage timers


def send_game_event(server, peer, catalog, type_, body, reliable=True):
    """Push a game-server event. The client's GameDefaultMessageHandler (Game.dll 0x106c43d0) only turns catalog 9
    and 19 messages without multicast flags into Events, and expects the body to start with an envelope:
        f32 event time (passed to EventAttemper::AddEvent; 0 = now) | u32 unique key (duplicates dropped) | payload
    """
    with GAME_LOCK:
        peer.event_key = getattr(peer, "event_key", 0) + 1
        # the float is handed to EventAttemper::AddEvent as the event time; 0 = dispatch immediately
        envelope = struct.pack("<fI", 0.0, peer.event_key)
        # unreliable events are the high-rate AI updates: keep them out of the log
        server.send_messages(peer, [Message(catalog, type_, body=envelope + body).pack()], reliable=reliable,
                             quiet=not reliable)


def ray_hits(origin, target, center, radius):
    """Does the ray from origin through target pass within radius of center (in front of the origin)?"""
    d = [t - o for t, o in zip(target, origin)]
    n = sum(x * x for x in d) ** 0.5 or 1.0
    d = [x / n for x in d]
    oc = [c - o for c, o in zip(center, origin)]
    along = sum(a * b for a, b in zip(oc, d))
    if along < 0:
        return False
    closest = [o + along * x for o, x in zip(origin, d)]
    return sum((c - p) ** 2 for c, p in zip(center, closest)) <= radius * radius


TICKETS = {}          # battle ticket -> (character id, battlefield id, expiry); issued by QS_ApplyEnter
TICKET_TTL = 120.0    # seconds between Quick Enter and the game server connection
MATCHES = {}          # battlefield id -> Match
PLAYER_RID_STEP = 1_000_000  # player robot ids: slot * step + 1; part ids follow the robot id (+20 per rebuild)
DUMMY_CID_BASE = 900_000     # character ids for the target dummies (real characters start at 1000)
STALE_PEER = 15.0     # seconds without any packet before a player is taken out of the match
PASSIVE_DUMMIES = os.environ.get("MW_PASSIVE_DUMMIES") == "1"  # dev: dummies don't shoot (PvP tests)
FORCE_BF = int(os.environ.get("MW_FORCE_BF", "0") or 0)  # dev: every Quick Enter goes to this battlefield
MOVE_SPEED, JUMP_SPEED = 8.0, 5.0  # Walker body flyweight NormalSpeed / JumpSpeed
SPAWN_DROP = 3.0      # spawn this far above the battlefield's spawn height
SLOT_OFFSET = 5.0     # players spawn this far apart along x (slot 1 on the battlefield's spawn point)


def issue_ticket(cid, bfid):
    """A random single-use battle ticket. The client sends it back as the first u32 of its FDP connect stream."""
    with GAME_LOCK:
        now = time.time()
        for t in [t for t, (_, _, exp) in TICKETS.items() if exp < now]:
            del TICKETS[t]
        ticket = secrets.randbits(32) or 1
        TICKETS[ticket] = (cid, bfid, now + TICKET_TTL)
        return ticket


def redeem_ticket(peer):
    """Match the game connection to the character that applied: (cid, bfid) or None."""
    if len(peer.connect_stream) < 4:
        return None
    (ticket,) = struct.unpack_from("<I", peer.connect_stream)
    with GAME_LOCK:
        cid, bfid, exp = TICKETS.pop(ticket, (None, None, 0))
    return (cid, bfid) if cid is not None and exp >= time.time() else None


class Match:
    """Everyone on one battlefield shares one world: the players' robots and the target dummies."""

    def __init__(self, server, bfid):
        self.server, self.bfid, self.bf = server, bfid, BATTLEFIELDS[bfid]
        self.peers = []
        self.next_slot = 1
        self.targets = {}
        for i, dpos in enumerate(self.bf["dummies"]):
            self.targets[100_000 * (i + 1) + 1] = {
                "home": dpos, "pos": dpos, "name": f"Target {i + 1}", "cid": DUMMY_CID_BASE + i + 1,
                "detachment": 2 + i, "hp": float(DUMMY_HP), "dead": False, "phase": 0.0, "cooldown": AI_PAUSE,
                "burst": 0}
        threading.Thread(target=ai_loop, args=(self,), daemon=True).start()

    def broadcast(self, catalog, type_, body_for, reliable=True, skip=None):
        """Send an event to every player; body_for(viewer) builds the body (object ids can differ per viewer)."""
        for p in list(self.peers):
            if p is not skip:
                send_game_event(self.server, p, catalog, type_, body_for(p) if callable(body_for) else body_for,
                                reliable=reliable)


def is_player(cid):
    return STORE.by_id(cid) is not None


def send_speed(server, viewer, rid):
    """GS_Robot_Skill (19,50132): u32 robot | f32 basic speed | f32 up speed | vec3 velocity -> Robot::SetBasicSpeed,
    SetUpSpeed, SetVelocity (observer Game.dll 0x10266b20). Without it a robot's speed stays 0 and it can't walk
    (Walker body flyweight: NormalSpeed 8, JumpSpeed 5)."""
    send_game_event(server, viewer, 19, 50132, struct.pack("<Iff3f", rid, MOVE_SPEED, JUMP_SPEED, 0.0, 0.0, 0.0))


def relive_on(server, viewer, rid, cid, pos, facing, hp, max_hp):
    """Make robot rid alive at pos on one client: GS_Robot_Survival 50108 u32 robot | vec3 pos | vec3 facing
    (normalized; 0 = NaN matrix, invisible robot) -> Relive, 50109 -> Revived (first-person view for the local
    robot), then rebuild its parts (Relive and Dead clear the equipment), armor (50005) and current weapon (50105)."""
    send_game_event(server, viewer, 19, 50108, struct.pack("<I3f3f", rid, *pos, *facing))
    send_game_event(server, viewer, 19, 50109, struct.pack("<I", rid))
    rebuild_robot(server, viewer, cid, rid, pos, max_hp)
    send_game_event(server, viewer, 19, 50005, struct.pack("<I", rid) + snapshots.armor_info(max_hp, hp)
                    + struct.pack("<I", 0))
    send_game_event(server, viewer, 19, 50105, struct.pack("<Iii", rid, 1, -1))
    send_speed(server, viewer, rid)


def show_dummy(server, viewer, rid, t):
    spawn_robot(server, viewer, t["cid"], rid, t["pos"], t["detachment"], t["name"], local=False)
    send_base_info(server, viewer, t["cid"], t["name"], rid)
    if t["dead"]:
        send_game_event(server, viewer, 19, 50107, struct.pack("<Iff", rid, 0.0, 0.0))
    else:
        relive_on(server, viewer, rid, t["cid"], t["pos"], (0.0, 0.0, 1.0), t["hp"], DUMMY_HP)


def show_player(server, viewer, other):
    """Create another player's robot on viewer's client (as a remote robot)."""
    spawn_robot(server, viewer, other.cid, other.player_rid, other.pos, other.detachment, other.name, local=False)
    send_base_info(server, viewer, other.cid, other.name, other.player_rid, other.kills, other.deaths,
                   rank_for(STORE.stats(other.cid).get("honor", 0)))
    if other.player_dead:
        send_game_event(server, viewer, 19, 50107, struct.pack("<Iff", other.player_rid, 0.0, 0.0))
    else:
        relive_on(server, viewer, other.player_rid, other.cid, other.pos, other.facing, other.player_hp, PLAYER_HP)


def spawn_point(m, slot):
    x, y, z = m.bf["spawn"]
    # drop in a little above the ground: a robot placed exactly at ground height starts inside the terrain, never
    # touches ground (pose stays "falling") and can't jump or stop cleanly
    return (x + SLOT_OFFSET * ((slot - 1) % 8), y + SPAWN_DROP, z)


def join_match(server, peer):
    """Client finished loading the world (9,30050): create its robot, show it the dummies and the other players,
    and show it to them."""
    m = MATCHES.get(peer.bfid)
    if m is None or m.peers is None:
        m = MATCHES[peer.bfid] = Match(server, peer.bfid)
    slot, m.next_slot = m.next_slot, m.next_slot + 1
    peer.match, peer.slot = m, slot
    peer.player_rid = PLAYER_RID_STEP * slot + 1
    peer.name = character_name(peer.cid)
    peer.detachment = 10 + slot  # every player on their own side: free for all
    peer.pos, peer.facing = spawn_point(m, slot), SPAWN_FACING
    peer.player_hp, peer.player_dead = float(PLAYER_HP), False
    peer.trigger, peer.kills, peer.deaths, peer.fire, peer.steer = 0, 0, 0, None, 0
    peer.stats_clock = time.time()
    peer.protected_until = time.time() + SPAWN_PROTECTION
    peer.fire_interval = weapon_stats(loadout_weapon(peer.cid))[1][0]
    rid = peer.player_rid
    spawn_robot(server, peer, peer.cid, rid, peer.pos, peer.detachment, peer.name, local=True)
    send_base_info(server, peer, peer.cid, peer.name, rid, rank=rank_for(STORE.stats(peer.cid).get("honor", 0)))
    # take up the weapon (the client ignores the hotkeys for the first seconds after spawning), then Revived:
    # first-person view (robot +0x308), the only view the weapons fire in
    send_game_event(server, peer, 19, 50105, struct.pack("<Iii", rid, 1, -1))
    send_game_event(server, peer, 19, 50109, struct.pack("<I", rid))
    send_speed(server, peer, rid)
    for drid, t in m.targets.items():
        show_dummy(server, peer, drid, t)
    for other in m.peers:
        show_player(server, peer, other)
        show_player(server, other, peer)
    m.peers.append(peer)
    log(f"[game] {peer.name} (cid {peer.cid}) joined {m.bf['name']} as slot {slot}; {len(m.peers)} player(s)")


def leave_match(peer):
    m = getattr(peer, "match", None)
    if not m or peer not in m.peers:
        return
    m.peers.remove(peer)
    peer.match = None
    log(f"[game] {peer.name} left {m.bf['name']}; {len(m.peers)} player(s)")
    # no detach event known yet: show the robot as destroyed
    m.broadcast(19, 50107, struct.pack("<Iff", peer.player_rid, 0.0, 0.0))
    if not m.peers:
        m.peers = None  # stops the AI loop
        MATCHES.pop(m.bfid, None)


def kill_report(m, victim_name, killer_name, weapon):
    """GS_Robot_Malice (19,50151): wstr victim | u32 nation | wstr killer | u32 nation | u32 weapon flyweight
    (0 = no feed line). Each client compares the names with its own character: victim = me counts a death,
    killer = me a kill streak."""
    m.broadcast(19, 50151, wstr(victim_name) + struct.pack("<I", 0) + wstr(killer_name) + struct.pack("<II", 0, weapon))


def credit_kill(server, killer, victim_text):
    """K/D count (GS_UpdateKillInfo 19,30204: u32 kind | i32 | u32 total kills -> character +0xf8) and score."""
    killer.kills += 1
    send_game_event(server, killer, 19, 30204, struct.pack("<IiI", 0, 0, killer.kills))
    award_kill(server, killer, victim_text)


def hit_dummy(server, m, shooter, rid):
    t = m.targets[rid]
    t["hp"] = max(0.0, t["hp"] - WEAPON_DAMAGE)
    m.broadcast(19, 50005, struct.pack("<I", rid) + snapshots.armor_info(DUMMY_HP, t["hp"]) + struct.pack("<I", 1))
    if t["hp"] > 0:
        return False
    t["dead"] = True
    log(f"[game] {t['name']} destroyed by {shooter.name}")
    m.broadcast(19, 50107, struct.pack("<Iff", rid, 0.0, 0.0))
    kill_report(m, t["name"], shooter.name, loadout_weapon(shooter.cid))
    credit_kill(server, shooter, "a target robot")
    threading.Timer(RESPAWN_DELAY, relive_target, (m, rid)).start()
    return True


def hit_player(server, m, victim, damage, killer_name, killer=None):
    """Damage a player's robot (armor update to everyone); at zero it dies, the kill is reported and it respawns."""
    if victim.player_dead or time.time() < victim.protected_until:
        return False
    victim.player_hp = max(0.0, victim.player_hp - damage)
    m.broadcast(19, 50005, struct.pack("<I", victim.player_rid) + snapshots.armor_info(PLAYER_HP, victim.player_hp)
                + struct.pack("<I", 1))
    if victim.player_hp > 0:
        return False
    victim.player_dead = True
    victim.deaths += 1
    record_stats(victim, deaths=1)
    STORE.add_log(victim.cid, f"Destroyed by {killer_name}")
    log(f"[game] {victim.name} destroyed by {killer_name}")
    m.broadcast(19, 50107, struct.pack("<Iff", victim.player_rid, 0.0, 0.0))
    kill_report(m, victim.name, killer_name, loadout_weapon(killer.cid) if killer else DEFAULT_WEAPON)
    if killer:
        credit_kill(server, killer, victim.name)
    threading.Timer(RESPAWN_DELAY, relive_player, (server, victim)).start()
    return True


def fire_tick(server, peer, trig, n):
    """One shot of a burst: show it to the other players (GS_Robot_SystemPart 19,50122 remote branch: u32 robot
    | u32 weapon object | u32 mode | vec3 muzzle | vec3 target | u32 | f32 ammo | u32 -> Weapon::Shoot; the shooter
    itself gets nothing, its local branch would overwrite its ammo) and damage what the trigger-pull ray hit.
    Repeats every fire interval until the trigger is released (9,40125)."""
    with GAME_LOCK:
        m = getattr(peer, "match", None)
        if not m or m.peers is None or peer not in m.peers or peer.trigger != trig or peer.player_dead or n > 600:
            return
        origin, aim, victim = peer.fire
        if isinstance(victim, int):
            t = m.targets.get(victim)
            if t and not t["dead"]:
                aim = (t["pos"][0], t["pos"][1] + HIT_HEIGHT, t["pos"][2])
                if hit_dummy(server, m, peer, victim):
                    peer.fire = (origin, aim, None)
        elif victim is not None:
            if victim in m.peers and not victim.player_dead:
                aim = (victim.pos[0], victim.pos[1] + HIT_HEIGHT, victim.pos[2])
                if hit_player(server, m, victim, WEAPON_DAMAGE, peer.name, peer):
                    peer.fire = (origin, aim, None)
        rid = peer.player_rid
        m.broadcast(19, 50122, lambda v: struct.pack("<III3f3fIfI", rid, part_base(v, rid) + 4, 0, *origin, *aim,
                                                      0, WEAPON_AMMO, 1), reliable=False, skip=peer)
    threading.Timer(peer.fire_interval, fire_tick, (server, peer, trig, n + 1)).start()


def pick_victim(m, shooter, origin, aim):
    """What the trigger-pull ray hits: a dummy rid, another player, or None."""
    def hit(pos):
        center = (pos[0], pos[1] + HIT_HEIGHT, pos[2])
        # aim is where the client's own raycast ended, so a hit on a robot ends at the robot
        near = sum((a - c) ** 2 for a, c in zip(aim, center)) <= (HIT_RADIUS + 1) ** 2
        return near or ray_hits(origin, aim, center, HIT_RADIUS)
    for rid, t in m.targets.items():
        if not t["dead"] and hit(t["pos"]):
            return rid
    for p in m.peers:
        if p is not shooter and not p.player_dead and hit(p.pos):
            return p
    return None


def rebuild_robot(server, peer, cid, rid, pos, hp):
    """Robot::Dead clears the robot's equipment, so a respawn re-sends its parts: GS_Robot_SystemPart (19,50104)
    u32 robot | u8 full | u8 avatar | system part block (snapshots.system_parts) -> Robot::ClearRobot +
    WriteSystemPartSnapshot, then takes up the first weapon (19,50105)."""
    peer.part_gen = getattr(peer, "part_gen", {})
    peer.part_gen[rid] = peer.part_gen.get(rid, 0) + 1
    base = part_base(peer, rid)
    power = (512001, base + 10, snapshots.func_power())
    send_game_event(server, peer, 19, 50104, struct.pack("<IBB", rid, 1, 0)
                    + snapshots.system_parts(robot_parts(cid, rid, pos, base), pos=pos, power=power, hp=hp))
    send_game_event(server, peer, 19, 50105, struct.pack("<Iii", rid, 1, -1))


def relive_target(m, rid):
    with GAME_LOCK:
        if m.peers is None:
            return
        t = m.targets[rid]
        t.update(hp=float(DUMMY_HP), dead=False, pos=t["home"], phase=0.0, cooldown=AI_PAUSE, burst=0)
        for p in list(m.peers):
            relive_on(m.server, p, rid, t["cid"], t["pos"], (0.0, 0.0, 1.0), DUMMY_HP, DUMMY_HP)


def send_base_info(server, peer, cid, name, rid, kills=0, deaths=0, rank=0):
    """GS_Character_Set_BaseInfo (19,30040) creates or updates a character (handler Game.dll 0x1035fab0):
    u32 char id | wstr name (character +8, shown on nameplates and in the kill feed) | u32 | u32 | u32
    | u32 rank (+0x44, 0 = Recruit) | u8 | u32 (+0xe8) | u32 (+0x108) | u8 (+0x168) | u8 (+0x169) | u8 (+0x220)
    | u32 kills (+0xf8) | u32 deaths (+0xfc) | u32 n x (u16 | u8) | u32 controlled robot id (+0xec)
    Without it a remote character has no name and its nameplate shows only "[GM]". A character with no group
    (+0xe8 = 0) gets a "[Need Team]" tag over its robot unless the +0x220 byte is set (Robot::updateHeading)."""
    send_game_event(server, peer, 19, 30040, struct.pack("<I", cid) + wstr(name) + struct.pack("<IIIIB", 0, 0, 0, rank, 0)
                    + struct.pack("<IIBBB", 0, 0, 0, 0, 1) + struct.pack("<III", kills, deaths, 0)
                    + struct.pack("<I", rid))


def record_stats(peer, **deltas):
    """Save the player's battle stats, adding the time played since the last save."""
    now = time.time()
    played = now - getattr(peer, "stats_clock", now)
    peer.stats_clock = now
    STORE.add_stats(peer.cid, battle_time=played, **deltas)


def award_kill(server, peer, victim_text="a target robot"):
    """Score for a kill. Honor (CPlayerInfoUI shows robot +0x40c) comes from GS_Robot_Malice (19,50130):
    u32 honor | u32 reason (0..6, message text) | u32 | u32, applied to the event owner's robot. Bonus
    (CPlayerInfoUI::AddPay) comes from GS_Character_Wage (19,30201): u32 char id | u32 pay (character +0x3f4)
    | u32 reason (message text, 0 = none)."""
    send_game_event(server, peer, 19, 50130, struct.pack("<IIII", KILL_HONOR, 0, 0, 0))
    send_game_event(server, peer, 19, 30201, struct.pack("<III", peer.cid, KILL_PAY, 0))
    record_stats(peer, kills=1, honor=KILL_HONOR, crystal=KILL_PAY, money=KILL_PAY)
    STORE.add_log(peer.cid, f"Destroyed {victim_text} (+{KILL_HONOR} honor, +{KILL_PAY} crystal)")


def relive_player(server, peer):
    with GAME_LOCK:
        m = getattr(peer, "match", None)
        if not m or peer not in m.peers:
            return
        peer.player_hp, peer.player_dead = float(PLAYER_HP), False
        peer.protected_until = time.time() + SPAWN_PROTECTION
        peer.pos, peer.facing, peer.steer = spawn_point(m, peer.slot), SPAWN_FACING, 0
        for p in list(m.peers):
            relive_on(server, p, peer.player_rid, peer.cid, peer.pos, peer.facing, PLAYER_HP, PLAYER_HP)


def ai_loop(m):
    """Drive one match's target dummies: GS_Robot_Action (19,50100) u32 robot | u32 steer flags (1 = walking)
    | vec3 pos | vec3 facing moves a remote robot (Robot::Move + MoveTo); 50122 plays their shots. Each dummy
    fights the nearest player in range. Also takes players who stopped sending packets out of the match."""
    while m.peers is not None:
        time.sleep(AI_TICK)
        with GAME_LOCK:
            if m.peers is None:
                return
            now = time.time()
            for p in [p for p in m.peers if now - getattr(p, "last_seen", now) > STALE_PEER]:
                leave_match(p)
            if m.peers is None:
                return
            for rid, t in m.targets.items():
                if t["dead"]:
                    continue
                hx, hy, hz = t["home"]
                best, dist, dx, dz = None, AI_RANGE, 0.0, 0.0
                for p in m.peers:
                    if p.player_dead or now < p.protected_until:
                        continue
                    ddx, ddz = p.pos[0] - t["pos"][0], p.pos[2] - t["pos"][2]
                    d = math.hypot(ddx, ddz)
                    if d < dist:
                        best, dist, dx, dz = p, d, ddx, ddz
                if best:  # stand and face the player
                    face, flags = ((dx / dist, 0.0, dz / dist) if dist else (0.0, 0.0, 1.0)), 0
                else:     # patrol along x around home
                    t["phase"] += AI_TICK * AI_SPEED / AI_PATROL
                    t["pos"] = (hx + AI_PATROL * math.sin(t["phase"]), hy, hz)
                    face, flags = (1.0 if math.cos(t["phase"]) >= 0 else -1.0, 0.0, 0.0), 1
                m.broadcast(19, 50100, struct.pack("<II3f3f", rid, flags, *t["pos"], *face), reliable=False)
                if not best or PASSIVE_DUMMIES:
                    continue
                t["cooldown"] -= AI_TICK
                if t["cooldown"] > 0:
                    continue
                t["burst"] += 1
                t["cooldown"] = WEAPON_TIMING[0] * 2 if t["burst"] < AI_BURST else AI_PAUSE
                if t["burst"] >= AI_BURST:
                    t["burst"] = 0
                hit = random.random() < AI_HIT_CHANCE
                spread = 0.5 if hit else 4.0
                muzzle = (t["pos"][0] + face[0] * 2, t["pos"][1] + 4.0, t["pos"][2] + face[2] * 2)
                # hits aim at the robot's body; misses at the ground around it, so their tracers end in visible
                # impacts instead of passing next to the first-person camera (which looks like a shot into the sky)
                aim = (best.pos[0] + random.uniform(-spread, spread), best.pos[1] + (2.0 if hit else 0.3),
                       best.pos[2] + random.uniform(-spread, spread))
                m.broadcast(19, 50122, lambda v: struct.pack("<III3f3fIfI", rid, part_base(v, rid) + 4, 0, *muzzle,
                                                              *aim, 0, WEAPON_AMMO, 1), reliable=False)
                if hit:
                    hit_player(m.server, m, best, AI_DAMAGE, t["name"])


def part_base(peer, rid):
    """Object id base for a robot's parts on one client. Each rebuild needs fresh ids: the client keeps the old
    parts registered, and an existing id makes the new weapon fail to attach (the equip slot stays empty)."""
    return rid + 20 * getattr(peer, "part_gen", {}).get(rid, 0)


def robot_parts(cid, rid, pos, base=None):
    """Body, armor, core, then one entry per armor equipment slot with the weapon in the first.
    Part object ids are base+1..base+4 (weapon base+4), energy function base+10."""
    base = rid if base is None else base
    player = is_player(cid)
    wid = loadout_weapon(cid) if player else DEFAULT_WEAPON
    ammo, timing = weapon_stats(wid)
    robot = player_robot(cid) if player else DEFAULT_ROBOT
    body, armor, core, primary, secondary = ROBOTS.get(robot, (221010, 221020, 2101, 2, 2))
    # one entry per armor slot, primary slots first; the weapon takes the first slot (a scout armor has no
    # primary slots, so it lands in the first secondary slot)
    slots = [(wid, base + 4, snapshots.weapon(robot_id=rid, pos=pos, char=cid, ammo=ammo, timing=timing))]
    slots += [(-1, 0, b"")] * (primary + secondary - 1)
    slots = slots[:primary + secondary]  # heavy armors have no slots (their weapons are part of the armor)
    return [(body, base + 1, snapshots.robot_body(pos=pos, char=cid)),
            (armor, base + 2, snapshots.robot_armor(pos=pos, char=cid)),
            (core, base + 3, snapshots.robot_core(pos=pos, char=cid))] + slots


def spawn_robot(server, peer, cid, rid, pos, detachment, name, local):
    """Create a character and its robot on the client:
      GS_Character_New_Character (19,30001): u32 char id
      AddToDetachment (19,30500): u32 detachment id | u32 char id | u32 | str name | vec3 | i32 | i32 | i32
        (GroupManager::AddToDetachment needs the character to exist; robot setup crashes without a detachment)
      GS_Object_Attach_Object (19,50001): u32 owner char id | u32 flyweight | u32 object id | snapshot | u8 avatar
      GS_Character_SetControl_Character (19,30100): u32 char id | u32 object id | u32  (local player only)
    """
    send_game_event(server, peer, 19, 30001, struct.pack("<I", cid))
    send_game_event(server, peer, 19, 30500, struct.pack("<III", detachment, cid, cid) + wstr(name)
                    + struct.pack("<3f", *pos) + struct.pack("<iii", -1, -1, -1))
    if local:
        # SetControl before creating the robot: the character remembers its object id, so the robot is already
        # local when it is created and its weapons load their HUD (otherwise Weapon::UpdateHUD hits a null HUD).
        send_game_event(server, peer, 19, 30100, struct.pack("<III", cid, rid, 0))
    power = (512001, rid + 10, snapshots.func_power())  # Func_BasicCore_Energy: energy for reloads
    robot = player_robot(cid) if is_player(cid) else DEFAULT_ROBOT
    send_game_event(server, peer, 19, 50001, struct.pack("<III", cid, robot, rid)
                    + snapshots.robot(robot_parts(cid, rid, pos), pos=pos, char=cid, power=power, hp=DUMMY_HP)
                    + b"\x00")
    if local:
        # ...and again afterwards so the character actually attaches to (and the camera follows) the robot
        send_game_event(server, peer, 19, 30100, struct.pack("<III", cid, rid, 0))


def on_game_message(server, peer, raw):
    """Game-server message received over FDP (plaintext Outpop::Message)."""
    try:
        msg = Message.parse(raw)
    except (IndexError, struct.error):
        debug(f"[game] {peer.addr} unparsable message\n{hexdump(raw)}")
        return
    debug(f"[game] {peer.addr} <- {EVENT_NAMES.get((msg.catalog, msg.type), '?')} {msg}\n{hexdump(msg.body)}")
    key, body = (msg.catalog, msg.type), msg.body
    if key == (9, 40851) and not getattr(peer, "battle_sent", False):
        # First message after the FDP session opens. The connect stream carries the battle ticket from
        # QS_ApplyEnter; without a valid one the connection gets nothing. Then GS_Battle_Initialize (19,40050),
        # handled by GS_Battle_Initialize_Observer (Game.dll 0x103faf30):
        #   u32 map id | astr world file | u8 | u32 n x preload item (3 strings) | u32 m x ProductServerInfo (3 x u32)
        #   | u32 arena logic (0..5, extra logic data may follow) -> LogicWorld::Initialize
        who = redeem_ticket(peer)
        if not who:
            log(f"[game] {peer.addr} has no valid battle ticket")
            return
        peer.cid, peer.bfid = who
        peer.battle_sent = True
        bf = BATTLEFIELDS[peer.bfid]
        send_game_event(server, peer, 19, 40050, struct.pack("<I", 1) + wstr(bf["world"]) + struct.pack("<BIII", 0, 0, 0, 0))
        return
    if not getattr(peer, "battle_sent", False):
        return  # nothing before the ticket
    if key == (9, 30050) and not getattr(peer, "spawned", False):
        # Client finished loading the battle world (sent from GS_InitBattle_MU_Observer).
        peer.spawned = True
        with GAME_LOCK:
            join_match(server, peer)
        return
    m = getattr(peer, "match", None)
    if not m:
        return
    if key == (9, 40130) and len(body) >= 16:
        # the player's robot position report: u32 | vec3. Moves its robot on the other clients.
        pos = struct.unpack_from("<3f", body, 4)
        if all(math.isfinite(v) for v in pos):
            peer.pos = pos
            m.broadcast(19, 50100, struct.pack("<II3f3f", peer.player_rid, peer.steer, *pos, *peer.facing),
                        reliable=False, skip=peer)
    elif key == (9, 40100) and len(body) >= 32:
        # GC_Robot_Action, sent by Robot::Move when the steer flags change: f32 | u32 steer flags (ROBOT_STEER_FLAG,
        # 0 = standing) | vec3 pos | vec3 facing -- the same layout as GS_Robot_Action (19,50100), relayed as is.
        steer = struct.unpack_from("<I", body, 4)[0]
        pos, facing = struct.unpack_from("<3f", body, 8), struct.unpack_from("<3f", body, 20)
        if all(math.isfinite(v) for v in pos + facing):
            peer.steer, peer.pos = steer, pos
            if any(abs(v) > 1e-3 for v in facing):
                peer.facing = facing
            m.broadcast(19, 50100, struct.pack("<II3f3f", peer.player_rid, steer, *pos, *peer.facing),
                        reliable=False, skip=peer)
    elif key == (9, 40105) and len(body) >= 8:
        # Weapon switch request (hotkey 1-9 / 0 / Q in Robot::UpdateInput): i32 | i32 equipment slot (1-based,
        # 0 = take off). The robot only gets a current equip (and Weapon::UpdateInput, i.e. firing) once the server
        # confirms with GS_Robot_SystemPart (19,50105): u32 robot id | i32 slot | i32 (-1) -> Robot::SetCurEquip.
        _, slot = struct.unpack_from("<ii", body)
        send_game_event(server, peer, 19, 50105, struct.pack("<Iii", peer.player_rid, slot, -1))
    elif key in ((9, 40122), (9, 40125)):
        # Trigger pulled (9,40122, Weapon::Use): u32 | u32 weapon id | u32 | vec3 muzzle | vec3 target | u32 | u32;
        # trigger released (9,40125). Automatic weapons keep firing client-side between the two (one shot per
        # fire-mode interval); the client tracks its own ammo and reloads. Hits are decided here from the
        # trigger-pull ray.
        with GAME_LOCK:
            peer.trigger += 1
            if msg.type == 40122 and len(body) >= 36 and not peer.player_dead:
                origin, aim = struct.unpack_from("<3f", body, 12), struct.unpack_from("<3f", body, 24)
                if all(math.isfinite(v) for v in origin + aim):
                    victim = pick_victim(m, peer, origin, aim)
                    peer.fire = (origin, aim, victim)
                    if victim is not None:
                        what = m.targets[victim]["name"] if isinstance(victim, int) else victim.name
                        log(f"[game] {peer.name} fires at {what}")
                    fire_tick(server, peer, peer.trigger, 0)


def serve_udp(port):
    """UDP listener: FDP game-server sessions on GAME_PORT, capture-only for anything else (SAP NAT detection)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    log(f"listening on udp {port}")
    server = fdp.Server(s, log, hexdump, debug=debug) if port == GAME_PORT else None
    if server:
        server.on_message = on_game_message
    while True:
        try:
            data, addr = s.recvfrom(65536)
        except ConnectionResetError:  # Windows reports ICMP port-unreachable from an earlier sendto here
            continue
        if server:
            # one bad packet must never stop the game server for every match
            try:
                with GAME_LOCK:
                    server.handle(data, addr)
            except Exception as e:  # noqa: BLE001
                log(f"[game] {addr} error handling packet: {e!r}\n{hexdump(data[:64])}")
        else:
            debug(f"[udp {port}] {addr} {len(data)} bytes\n{hexdump(data)}")


if __name__ == "__main__":
    if not PRODUCTS or not ROBOTS:
        log("WARNING: game data missing (data/flyweights). Run: python setup_data.py <your MarsWar install folder>")
    threading.Thread(target=STORE.flush_loop, daemon=True).start()
    for p in (GAME_PORT, 5876):
        threading.Thread(target=serve_udp, args=(p,), daemon=True).start()
    for p in (6001, GATE_PORT, GAME_PORT):
        threading.Thread(target=serve, args=(p,), daemon=True).start()
    while True:
        time.sleep(1)
