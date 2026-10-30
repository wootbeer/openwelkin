# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""admin.py <command> -- send one admin command to the running server on this machine.

Examples:
    python tools/admin.py who
    python tools/admin.py ban somebody 7d cheating
    python tools/admin.py ipban 1.2.3.0/24
    python tools/admin.py help

Reads the server's admin.key (next to accounts.db; MW_STATE_DIR if set) and uses port 6010 + MW_PORT_OFFSET.
The same commands can be typed into the server window."""
import os
import socket
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("MW_STATE_DIR") or HERE
PORT = 6010 + int(os.environ.get("MW_PORT_OFFSET", "0") or 0)


def read_key():
    with open(os.path.join(STATE, "admin.key"), encoding="ascii") as f:
        return f.read().strip()


def send(command):
    """Send one command line and return the server's reply."""
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as s:
        s.sendall(f"{read_key()}\n{command}\n".encode("utf-8"))
        reply = b""
        while chunk := s.recv(65536):
            reply += chunk
    return reply.decode("utf-8", "replace").rstrip()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    try:
        print(send(" ".join(sys.argv[1:])))
    except OSError as e:
        print(f"cannot reach the server on 127.0.0.1:{PORT}, or no admin.key in {STATE}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
