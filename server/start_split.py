# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""start_split.py -- run the login server, a gate and game servers on this machine, each its own program.

    python start_split.py --games 2

Each gets a folder under --state (default: split):
    login/   accounts.db, admin.key, admin.log, ticket.key
    gate/    characters.db
    game1/   only a log

To move an existing one-program server: put accounts.db in login/ and characters.db in gate/.
Ctrl+C stops all of them. A server that dies is not restarted; this prints a line when one does. For admin commands:
    MW_STATE_DIR=split/login python tools/admin.py who

To use several machines, skip this script: copy internal.key and ticket.pub by hand and set MW_ROLE, MW_LINK_BIND,
MW_LOGIN_LINK, MW_GATE_LINK and MW_PUBLIC_IP yourself (see README)."""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cluster  # noqa: E402


def game_port(n, offset):
    """6003 for the first game server, then 6021 up: 6004-6020 are taken or kept free."""
    return (6003 if n == 1 else 6019 + n) + offset


def pump(name, proc):
    for line in proc.stdout:
        print(f"{name:6} {line}", end="", flush=True)


def start(name, role, folder, extra, procs):
    os.makedirs(folder, exist_ok=True)
    env = dict(os.environ, MW_ROLE=role, MW_STATE_DIR=folder, **extra)
    proc = subprocess.Popen([sys.executable, "-u", "loginserver.py"], cwd=HERE, env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    threading.Thread(target=pump, args=(name, proc), daemon=True).start()
    procs[name] = proc
    return proc


def main():
    ap = argparse.ArgumentParser(description="Run login, gate and game servers on this machine.")
    ap.add_argument("--games", type=int, default=1, help="game servers (one process each)")
    ap.add_argument("--state", default=os.path.join(HERE, "split"), help="folder for the servers' data")
    args = ap.parse_args()
    offset = int(os.environ.get("MW_PORT_OFFSET", "0") or 0)
    root = os.path.abspath(args.state)
    login_dir, gate_dir = os.path.join(root, "login"), os.path.join(root, "gate")
    os.makedirs(login_dir, exist_ok=True)
    os.makedirs(gate_dir, exist_ok=True)
    cluster.load_key(os.path.join(login_dir, "internal.key"))
    shutil.copy(os.path.join(login_dir, "internal.key"), gate_dir)

    procs = {}
    start("login", "login", login_dir, {}, procs)
    pub = os.path.join(login_dir, "ticket.pub")
    for _ in range(100):  # login writes it on its first start
        if os.path.exists(pub):
            break
        time.sleep(0.1)
    shutil.copy(pub, gate_dir)
    start("gate", "gate", gate_dir, {}, procs)
    for n in range(1, args.games + 1):
        folder = os.path.join(root, f"game{n}")
        os.makedirs(folder, exist_ok=True)
        shutil.copy(os.path.join(login_dir, "internal.key"), folder)
        start(f"game{n}", "game", folder, {"MW_GAME_PORT": str(game_port(n, offset))}, procs)
    try:
        while True:
            time.sleep(1)
            for name, proc in list(procs.items()):
                if proc.poll() is not None:
                    print(f"{name:6} stopped with code {proc.returncode}", flush=True)
                    del procs[name]
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs.values():
            proc.terminate()


if __name__ == "__main__":
    main()
