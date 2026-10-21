"""Read terrain heights from a map's tile files (Maps/<map>/*.buf, 370520 bytes each).

Tile layout: 22-byte header, then vertices of 56 bytes (vec3 pos | vec3 normal | 8 floats). Y is up.
Usage: python -I terrain.py <map dir> [x z ...]   prints the map bounds and the ground height at each x,z."""
import os
import struct
import sys

TILE_SIZE = 370520
HEADER, STRIDE = 22, 56


def load(mapdir):
    pts = {}
    for name in os.listdir(mapdir):
        path = os.path.join(mapdir, name)
        if not name.endswith(".buf") or os.path.getsize(path) != TILE_SIZE:
            continue
        d = open(path, "rb").read()
        off = HEADER
        while off + STRIDE <= len(d):
            x, y, z, nx, ny, nz = struct.unpack_from("<6f", d, off)
            if not (abs(nx) <= 1.01 and abs(ny) <= 1.01 and abs(nz) <= 1.01) or abs(x) > 1e5 or abs(z) > 1e5:
                break
            pts[(round(x), round(z))] = y
            off += STRIDE
    return pts


def height(pts, x, z, step=20):
    # nearest grid vertex (good enough to pick a spawn height)
    gx, gz = round(x / step) * step, round(z / step) * step
    return pts.get((gx, gz))


if __name__ == "__main__":
    pts = load(sys.argv[1])
    xs = [k[0] for k in pts]
    zs = [k[1] for k in pts]
    ys = list(pts.values())
    print(f"{len(pts)} vertices  x {min(xs)}..{max(xs)}  z {min(zs)}..{max(zs)}  y {min(ys):.1f}..{max(ys):.1f}")
    a = sys.argv[2:]
    for i in range(0, len(a) - 1, 2):
        x, z = float(a[i]), float(a[i + 1])
        print(f"({x}, {z}) -> {height(pts, x, z)}")
