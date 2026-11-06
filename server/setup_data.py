# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""setup_data.py [MarsWar install folder] -- unpack the game data the server needs from your own MarsWar install.

The server reads item, weapon and robot definitions (data/flyweights/) from the client's ENGFlyweights.opt,
each map's ground heights (data/maps/<map>.hmd, from Maps/<map>/Terrain01.hmd) to stop shots through hills, and
each map's solid models (data/maps/<map>.walls, from the map's .reb and the models' collision meshes) to stop shots
through buildings.
That data belongs to the game's publisher, so it is not shipped with the server: run this once instead.
Default folder: MW_CLIENT_DIR, else the folder above this one (server/ inside the MarsWar install)."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mapdata  # noqa: E402


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
    maps, out = os.path.join(client, "Maps"), os.path.join(HERE, "data", "maps")
    os.makedirs(out, exist_ok=True)
    copied = built = 0
    for name in sorted(os.listdir(maps)) if os.path.isdir(maps) else []:
        src = os.path.join(maps, name, "Terrain01.hmd")
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(out, name + ".hmd"))
            copied += 1
        walls = mapdata.build_walls(client, name)
        if walls:
            with open(os.path.join(out, name + ".walls"), "wb") as f:
                f.write(walls)
            built += 1
    print(f"copied {copied} map height files and built {built} wall files into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
