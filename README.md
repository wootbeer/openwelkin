# OpenWelkin

MMOFPS server for Welkin4591 / Mars War: an open-source server emulator

A fan-made server for the MarsWar client. The stock client is used as-is; only its config file
points at this server. Work in progress: several players per battlefield (free for all), Training Ground and Northern Front, shop, warehouse,
talents, medals, badges and the Info window work.

## Run it

Needs Python 3.10+, the `cryptography` package (`pip install cryptography`) and a MarsWar client install.

From the `server` folder, once, unpack the game data the server needs from your own client (it is not shipped here, see License):

```
cd server
python setup_data.py "C:\Games\MarsWar"
```

Then start the server:

```
python loginserver.py
```

Then point the client at it. In `game_client_config.xml` (next to the client's `System` folder):

```
<LoginServer ID ="1" Address="127.0.0.1" Port="6001"/>
<ADServer Address="127.0.0.1" Port="5876"/>
```

Use the server's address instead of 127.0.0.1 when it runs on another machine. Start the client with
`System\MarsWar.exe` (the old `Loader.exe` updater looks for servers that no longer exist).

## Ports

| Port | Proto | What |
|------|-------|------|
| 6001 | TCP | login |
| 6002 | TCP | gate (hall, shop, characters) |
| 6003 | UDP | game server (battles) |
| 5876 | UDP | client NAT probe (only logged) |
| 6010 | TCP | admin commands (`tools/admin.py`), 127.0.0.1 only |

`MW_PORT_OFFSET` adds to all of them, e.g. `100` gives 6101-6110 (to run a second server on one machine; the
client then needs the moved login port).

## Settings (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MW_PUBLIC_IP` | `127.0.0.1` | Address clients are sent to for the gate and game servers. Set it to your LAN or internet address when others play. |
| `MW_AUTO_REGISTER` | `1` | A new account name with a password creates the account on first login. `0` = only existing accounts. |
| `MW_DEBUG` | `0` | `1` logs every packet with hex dumps (reverse-engineering only; large logs). |
| `MW_BADGES` | `1` | `0` turns badges off. |
| `MW_MATCH_PLAYERS` | `32` | Players per match. When a battlefield's match is full, the next player starts another one. |
| `MW_STATE_DIR` | the `server` folder | Where accounts, characters, bans and logs are kept. |
| `MW_PORT_OFFSET` | `0` | Added to every port (see Ports). |
| `MW_STATS_EVERY` | `60` | Seconds between `[stats]` lines in the log (players, tick time, CPU, traffic). `0` = off. |
| `MW_ROBOT` | (empty) | Dev only: force the player's robot type, e.g. `1313` (Walker Scout). |
| `MW_PASSIVE_DUMMIES` | `0` | Dev only: `1` stops the target dummies from shooting (PvP tests). |
| `MW_FORCE_BF` | `0` | Dev only: every Quick Enter goes to this battlefield id (multi-client tests). |
| `MW_SKIP` | (empty) | Dev only: `catalog:type,...` requests to leave unanswered. |

## Files

| File | What |
|------|------|
| `accounts.json` | Account names and salted scrypt password hashes. Never contains passwords. |
| `characters.json` | Characters, stats, items, talents, event log. Battle stats are saved every few seconds. |
| `bans.json` | Account and IP bans. |
| `admin.key` | Secret for `tools/admin.py`, made on first start. Anyone who can read it can run admin commands. |
| `admin.log` | Every admin command, who ran it and the result. |
| `loginserver.log` | Server log. Contains no passwords or keys. With `MW_DEBUG=1` it grows fast. |
| `data/flyweights/` | Item, weapon and robot data unpacked from your client by `setup_data.py`. |

Back up `accounts.json` and `characters.json` together. Stop the server with Ctrl+C so pending stats are saved.

## Split servers

By default one program does everything. For more players, run login, gate and game servers as separate programs:

```
python server/start_split.py --games 3
```

| Server | Keeps | Does |
|--------|-------|------|
| login | accounts, bans, admin commands | Checks passwords, sends players to the emptiest gate |
| gate | characters | Hall, shop, characters. Sends players to the emptiest game server |
| game | nothing | Battles. Reports kills and stats to the gate |

Each game server is one process, so more of them use more CPU cores. Each server gets a folder under `split/`.
To move an existing server, put `accounts.json` and `bans.json` in `split/login/`, and `characters.json` in
`split/gate/`. Admin commands go to the login server:

```
MW_STATE_DIR=server/split/login python server/tools/admin.py who
```

`start_split.py` does not restart a server that dies.

### On several machines

Set these yourself on each machine and run `python loginserver.py`:

| Setting | On | What |
|---------|----|------|
| `MW_ROLE` | all | `login`, `gate` or `game` |
| `MW_PUBLIC_IP` | gate, game | The address players connect to |
| `MW_LINK_BIND` | login, gate | Address of the private ports (6011, 6012). Use a private network address |
| `MW_LOGIN_LINK` | gate | Address of the login server |
| `MW_GATE_LINK` | game | Address of the gate |
| `MW_GAME_PORT` | game | UDP port, if a machine runs more than one |

Copy `internal.key` to every server and `ticket.pub` from login to each gate. Never copy `ticket.key`: it makes logins.

The private ports check `internal.key` and sign every message, but they are not encrypted. Keep them off the
internet: use a private network or a VPN between machines.

## Admin commands

Type them into the server window, or from another window on the same machine:

```
python server/tools/admin.py who
```

| Command | What |
|---------|------|
| `who` | Who is online, and in which battle |
| `matches` | Running matches and their players |
| `kick <account>` | Disconnect an account (hall and battle) |
| `ban <account> [time] [reason]` | Ban and kick. Time like `30m`, `12h`, `7d`, `2w`; without it the ban is permanent |
| `unban <account>` | |
| `ipban <address or network> [time] [reason]` | e.g. `1.2.3.4` or `1.2.3.0/24`. Kicks everyone on it |
| `unipban <address or network>` | |
| `bans` | List bans |
| `role <account> [player\|gm\|admin]` | Show or set an account's role (for GM commands later) |

Banned players see the client's own message: "Account has been locked" (permanent), "Account being punished"
(timed) or "IP is locked".

## Security notes

- Passwords are stored as salted scrypt hashes. On the wire they are protected only by the client's own
  encryption (RSA-512 + AES), which is weak by today's standards: tell players not to reuse passwords.
- 5 failed logins from one IP lock that IP out for 5 minutes.
- Nothing but login is accepted before a connection logs in; idle unauthenticated connections close after 30 s.
- Connection limits: 1024 TCP connections, 512 game sessions (idle sessions expire after 60 s).
- The admin port only listens on 127.0.0.1 and wants the key from `admin.key` first.
- Every request is length-checked; `tools/fuzz_handlers.py` and `tools/fuzz_game.py` throw random data at all
  handlers, and `tools/test_login.py` checks the login rules. Run them after changes:

```
python -I server/tools/fuzz_handlers.py 2
python -I server/tools/fuzz_game.py
python -I server/tools/test_login.py
python -I server/tools/test_match.py
```

## Load test

`server/tools/loadtest.py` starts a private server (temporary folder, ports 6101-6110) and connects fake players
to it over the real protocol. They log in, make a character, Quick Enter, then walk, fire and ping. It prints
the server's tick time, CPU and traffic, and how late other players' moves arrive:

```
python -I server/tools/loadtest.py --bots 128 --seconds 60
```

Results on a 16-thread PC are in `docs/TODO.md` (Performance).

## More

- Protocol notes: `docs/index.html`.
- What still needs work: `docs/TODO.md`.
- Dev scripts (Windows): `scripts/cycle.ps1` restarts the server and client, `scripts/launch.ps1` starts the
  client, `scripts/snap.ps1` screenshots it. They look for the client in `MW_CLIENT_DIR` (default `C:\Games\MarsWar`).
- Reverse-engineering and test tools: `server/tools/` (Frida probes need `frida==16.5.9`).

## License

The emulator (server code, tools and protocol docs) is free software under the GNU Affero General Public License
v3 or later: see `LICENSE`. You can use, change and host it; if you run a modified version as a public server,
you must offer your changes to its players under the same license.

MarsWar itself, its client, data files and texts belong to their publisher and are not part of this project.
The server only reads them from your own install (`setup_data.py`); do not commit `data/` to the repository.
