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

None of these clash with a WoW / mangos server (3724, 8085, 3306).

## Settings (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MW_PUBLIC_IP` | `127.0.0.1` | Address clients are sent to for the gate and game servers. Set it to your LAN or internet address when others play. |
| `MW_AUTO_REGISTER` | `1` | A new account name with a password creates the account on first login. `0` = only existing accounts. |
| `MW_DEBUG` | `0` | `1` logs every packet with hex dumps (reverse-engineering only; large logs). |
| `MW_BADGES` | `1` | `0` turns badges off. |
| `MW_ROBOT` | (empty) | Dev only: force the player's robot type, e.g. `1313` (Walker Scout). |
| `MW_PASSIVE_DUMMIES` | `0` | Dev only: `1` stops the target dummies from shooting (PvP tests). |
| `MW_FORCE_BF` | `0` | Dev only: every Quick Enter goes to this battlefield id (multi-client tests). |
| `MW_SKIP` | (empty) | Dev only: `catalog:type,...` requests to leave unanswered. |

## Files

| File | What |
|------|------|
| `accounts.json` | Account names and salted scrypt password hashes. Never contains passwords. |
| `characters.json` | Characters, stats, items, talents, event log. Battle stats are saved every few seconds. |
| `loginserver.log` | Server log. Contains no passwords or keys. With `MW_DEBUG=1` it grows fast. |
| `data/flyweights/` | Item, weapon and robot data unpacked from your client by `setup_data.py`. |

Back up `accounts.json` and `characters.json` together. Stop the server with Ctrl+C so pending stats are saved.

## Security notes

- Passwords are stored as salted scrypt hashes. On the wire they are protected only by the client's own
  encryption (RSA-512 + AES), which is weak by today's standards: tell players not to reuse passwords.
- 5 failed logins from one IP lock that IP out for 5 minutes.
- Nothing but login is accepted before a connection logs in; idle unauthenticated connections close after 30 s.
- Connection limits: 1024 TCP connections, 512 game sessions (idle sessions expire after 60 s).
- Every request is length-checked; `tools/fuzz_handlers.py` and `tools/fuzz_game.py` throw random data at all
  handlers, and `tools/test_login.py` checks the login rules. Run them after changes:

```
python -I server/tools/fuzz_handlers.py 2
python -I server/tools/fuzz_game.py
python -I server/tools/test_login.py
python -I server/tools/test_match.py
```

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
