# OpenWelkin

MMOFPS server for Welkin4591 / Mars War: an open-source server emulator

A fan-made server for the MarsWar client. The stock client is used as-is; only its config file
points at this server. Work in progress: several players per battlefield (free for all), Training Ground and Northern Front, timed rounds, shop, warehouse,
talents (including Forget Talent), medals, badges, chat, friend and block lists, the Ranking window, server notices and the
Info window work.

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
| 6013 | TCP | admin web page (`tools/webadmin.py`, optional), 127.0.0.1 only |
| 6011, 6012 | TCP | private links between split servers (login, gate). Keep them off the internet |
| 6021 and up | UDP | more game servers from `start_split.py` (the first stays on 6003) |

`MW_PORT_OFFSET` adds to all of them, e.g. `100` moves login to 6101 (to run a second server on one machine; the
client then needs the moved login port).

## Settings (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MW_PUBLIC_IP` | `127.0.0.1` | Address clients are sent to for the gate and game servers. Set it to your LAN or internet address when others play. |
| `MW_AUTO_REGISTER` | `1` | A new account name with a password creates the account on first login. `0` = only existing accounts. |
| `MW_DEBUG` | `0` | `1` logs every packet with hex dumps (reverse-engineering only; large logs). |
| `MW_BADGES` | `1` | `0` turns badges off. |
| `MW_MATCH_PLAYERS` | `32` | Players per match. When a battlefield's match is full, the next player starts another one. |
| `MW_ROUND_SECONDS` | `900` | Length of a battlefield round. `0` means rounds never run out of time. Training grounds have no rounds. |
| `MW_ROUND_KILLS` | `25` | A round also ends when a player destroys this many players. `0` turns this off. |
| `MW_STATE_DIR` | the `server` folder | Where accounts, characters, bans and logs are kept. |
| `MW_PORT_OFFSET` | `0` | Added to every port (see Ports). |
| `MW_STATS_EVERY` | `60` | Seconds between `[stats]` lines in the log (players, tick time, CPU, traffic). `0` = off. |
| `MW_ROBOT` | (empty) | Dev only: force the player's robot type, e.g. `1313` (Walker Scout). |
| `MW_KILL_MODE` | `0` | `1` makes battlefields with rounds nation against nation: the client's own team arena, with each side's kills on screen. The side that reaches `MW_ROUND_KILLS` wins. Not tried in game yet. |
| `MW_SCORE_TABLE` | `0` | `1` ends a round with the score table: the Record window also lists the winner and the kills. Not tried in game yet. |
| `MW_MOVE_CHECK` | `1` | `0` turns off the speed check: a robot that moves faster than twice the walking speed is put back where it was. Not tried in game yet. |
| `MW_PASSIVE_DUMMIES` | `0` | Dev only: `1` stops the target dummies from shooting (PvP tests). |
| `MW_FORCE_BF` | `0` | Dev only: every Quick Enter goes to this battlefield id (multi-client tests). |
| `MW_SKIP` | (empty) | Dev only: `catalog:type,...` requests to leave unanswered. |

## Files

| File | What |
|------|------|
| `accounts.db` | SQLite: account names, salted scrypt password hashes (never passwords), and account and IP bans. |
| `characters.db` | SQLite: characters, stats, items, talents, event log. Battle stats are saved every few seconds. |
| `admin.key` | Secret for `tools/admin.py`, made on first start. Anyone who can read it can run admin commands. |
| `internal.key` | Split servers only: shared secret for the private links. Every server needs the same file. |
| `ticket.key` | Split servers only, on login: signs session keys. Never copy it. |
| `ticket.pub` | Split servers only: lets a gate check session keys. Safe to copy to each gate. |
| `backups/` | Copies made by the `backup` command. |
| `admin.log` | Every admin command, who ran it and the result. |
| `loginserver.log` | Server log. Contains no passwords or keys. With `MW_DEBUG=1` it grows fast. |
| `data/flyweights/` | Item, weapon and robot data unpacked from your client by `setup_data.py`. |

To back up while the server runs, use the admin command `backup`. It saves pending stats and writes dated copies into
`backups/` next to each database, on each server's own disk. Copy those somewhere safe. Do not copy `accounts.db` or
`characters.db` by hand while the server runs.

Old `accounts.json`, `bans.json` and `characters.json` files are imported on the first start, then renamed to
`*.imported`. You can keep those as a backup or delete them.

## Split servers

By default one program does everything. For more players, run login, gate and game servers as separate programs:

```
python server/start_split.py --games 3
```

| Server | Keeps | Does |
|--------|-------|------|
| login | `accounts.db`, admin commands | Checks passwords, sends players to the gate |
| gate | `characters.db` | Hall, shop, characters. Sends players to the emptiest game server |
| game | nothing | Battles. Reports kills and stats to the gate |

Each game server is one process, so more of them use more CPU cores. Each server gets a folder under `split/`.
To move an existing server, put `accounts.db` in `split/login/` and `characters.db` in `split/gate/`. Each database
stays on its own server: a gate never sees password hashes, and a game server holds no database at all. Admin commands
go to the login server:

```
MW_STATE_DIR=server/split/login python server/tools/admin.py who
```

Use one gate per login server: each gate keeps its own `characters.db`, so login refuses a second one. Add game
servers for more players.

Any server can stop and start while the others run:

- Login down: nobody can log in. Players already on stay on.
- Gate down: players in the hall drop. Battles carry on, and their stats reach the gate when it is back. A game server
  keeps each report until the gate has saved it, so a gate crash loses nothing. It holds up to 50000 while it waits,
  and the log says if any were lost.
- Game server down: after about 6 seconds its players go back to the login screen. New battles use the other game servers.

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
| `kick <account> [reason]` | Disconnect an account (hall and battle). They see the reason in chat first |
| `goto <account> <x> <y> <z>` | Move a player's robot in battle, for someone stuck. Not tried in game yet |
| `tell <character> <text>` | A private line from Server to one player |
| `ban <account> [time] [reason]` | Ban and kick. Time like `30m`, `12h`, `7d`, `2w`; without it the ban is permanent |
| `unban <account>` | |
| `ipban <address or network> [time] [reason]` | e.g. `1.2.3.4` or `1.2.3.0/24`. Kicks everyone on it |
| `unipban <address or network>` | |
| `bans` | List bans |
| `role <account> [player\|gm\|admin]` | Show or set an account's role |
| `backup` | Copy the databases into `backups/` while the server runs |
| `notice <text>` | Tell everyone online: a Broadcast line from Server in the hall, big red text in battles |

The hall's scrolling announcements are in `announcements.txt` next to the databases, one per line (up to 20). It is
made on first start, and changes show the next time a player opens the hall.

A player with the role `gm` or `admin` can also type commands in game chat, starting with `!`, like `!kick bob`. A `gm` can
use `who`, `matches`, `kick`, `goto`, `tell`, `ban`, `unban` and `bans`. An `admin` can also use `ipban`, `unipban`, `role`, `backup` and `notice`.
The reply comes as a private message from Server, and the command goes in `admin.log`.

The client also has its own GM commands starting with `/`, and the server turns them on for `gm` and `admin` characters
at their next login: `/kick <character> <number>` works like `!kick`, and `/announce` and `/say` like `!notice`. Not tried in game yet.

Banned players see the client's own message: "Account has been locked" (permanent), "Account being punished"
(timed) or "IP is locked".

### Admin web page

A small page for the same commands. On the login server's machine:

```
python server/tools/webadmin.py
```

Open http://127.0.0.1:6013 and log in with the key from `admin.key`. In split mode, set `MW_STATE_DIR=server/split/login`
first. It has no HTTPS, so never open this port to the internet. From another machine, use an SSH tunnel or a VPN.

The page is one file with no extra packages. Edit `PAGE` to change how it looks. To add a button, add a form to `PAGE`
and a line to `ACTIONS`.

### Admin port

To build your own panel, use the admin port instead of the database. Connect to 127.0.0.1:6010 and send two lines, the
key from `admin.key` and one command. The server sends the reply as text and closes the connection. In Python,
`tools/admin.py` has `send(command)` for this.

Keep your panel on the same machine and check what users type: one command per line, and a line break in a field would
start a new line. Show replies as plain text, because they contain names and reasons that players or admins typed.

## Security notes

- Passwords are stored as salted scrypt hashes. On the wire they are protected only by the client's own
  encryption (RSA-512 + AES), which is weak by today's standards: tell players not to reuse passwords.
- 5 failed logins from one IP lock that IP out for 5 minutes.
- Nothing but login is accepted before a connection logs in; idle unauthenticated connections close after 30 s.
- Connection limits: 1024 TCP connections, 512 game sessions (idle sessions expire after 60 s).
- Speed check: position reports may cover 16 units a second (twice the walking speed), with up to 40 units saved up
  for reports that arrive bunched after lag. Falling does not count. A faster move is not passed on, and the robot is
  put back where it was. Shots must come from the robot and stay within the weapon's range.
- The admin port only listens on 127.0.0.1 and wants the key from `admin.key` first.
- The admin web page only listens on 127.0.0.1, wants the same key, locks after 5 wrong keys, and checks every field
  before it becomes a command. `tools/test_webadmin.py` attacks it.
- Every request is length-checked; `tools/fuzz_handlers.py` and `tools/fuzz_game.py` throw random data at all
  handlers, and `tools/test_login.py` checks the login rules. Run them after changes:

```
python -I server/tools/fuzz_handlers.py 2
python -I server/tools/fuzz_game.py
python server/tools/test_login.py
python server/tools/test_match.py
python server/tools/test_chat.py
python server/tools/test_db.py
python server/tools/test_split.py
python server/tools/test_failover.py
python server/tools/test_webadmin.py
```

## Rounds and chat

A battlefield round ends after `MW_ROUND_SECONDS`, or when a player reaches `MW_ROUND_KILLS` kills on other players.
Target robots do not count. The player with the most kills gets a win, shown on the Battle tab, and a tie gives a win to
everyone tied. Everyone then sees the Record window for 20 seconds and goes back to the lobby. A player alone keeps
playing, and Training Grounds have no rounds.

Players can chat in the hall's Lobby Channel, which reaches everyone online, in whispers, and in the battle chat box's
Country tab, which reaches only their own match. The client allows about one Lobby line or whisper a minute. Team and
division chat are not done yet. Lines are cut at 200 characters, one player can send
a line every half second, and control characters are removed. The sender's name always comes from the server.

Each character has a friend list and a block list of up to 100 names, kept in `characters.db`. Lines from a blocked player
do not reach you, and to them you look offline in private messages.

## Load test

`server/tools/loadtest.py` starts a private server (temporary folder, ports 6101-6110) and connects fake players
to it over the real protocol. They log in, make a character, Quick Enter, then walk, fire and ping. It prints
the server's tick time, CPU and traffic, and how late other players' moves arrive:

```
python -I server/tools/loadtest.py --bots 128 --seconds 60
```

Results on a 16-thread PC are in `docs/TODO.md` (Performance).

## More

- Protocol notes: [docs/PROTOCOL.md](docs/PROTOCOL.md) (built from `docs/index.html`, the page to edit, by
  `server/tools/docs_md.py`).
- What still needs work: `docs/TODO.md`.
- Dev scripts (Windows): `scripts/cycle.ps1` restarts the server and client, `scripts/launch.ps1` starts the
  client, `scripts/snap.ps1` screenshots it. They look for the client in `MW_CLIENT_DIR` (default `C:\Games\MarsWar`).
- Reverse-engineering and test tools: `server/tools/` (Frida probes need `frida==16.5.9`).

## License

The emulator (server code, tools and protocol docs) is free software under the GNU Affero General Public License
v3 or later: see `LICENSE`. You can use, change and host it; if you run a modified version as a public server,
you must offer your changes to its players under the same license.

MarsWar itself, its client, data files and texts belong to their publisher and are not part of this project.
This is an unofficial fan project, not affiliated with or endorsed by the game's publisher or developer. MarsWar,
Welkin4591 and the game's other names are the trademarks of their owners and are used here only to say which
client this server works with.
The server only reads them from your own install (`setup_data.py`); do not commit `data/` to the repository.
