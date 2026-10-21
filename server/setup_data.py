# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""setup_data.py [MarsWar install folder] -- unpack the game data the server needs from your own MarsWar install.

The server reads item, weapon and robot definitions (data/flyweights/) from the client's ENGFlyweights.opt.
That data belongs to the game's publisher, so it is not shipped with the server: run this once instead.
Default folder: MW_CLIENT_DIR, else the folder above this one (server/ inside the MarsWar install)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    client = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MW_CLIENT_DIR", os.path.dirname(HERE))
    opt = os.path.join(client, "ENGFlyweights.opt")
    if not os.path.isfile(opt):
        print(f"ENGFlyweights.opt not found in {client!r}. Pass your MarsWar install folder:\n"
              f"  python setup_data.py \"C:\\Games\\MarsWar\"")
        return 1
    out = os.path.join(HERE, "data", "flyweights")
    os.makedirs(out, exist_ok=True)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "tools", "unopt.py"), opt, out], check=True)
    n = sum(len(f) for _, _, f in os.walk(out))
    print(f"unpacked {n} files into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
