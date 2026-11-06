# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""Line of sight on a map: the ground (Terrain) and the solid models placed on it (Walls).

Both come from the player's own client, prepared once by setup_data.py into data/maps/:
  <map>.hmd    copied from Maps/<map>/Terrain01.hmd
  <map>.walls  built from Maps/<map>/<map>.reb (where the models stand) and each model's collision mesh
               (Models/.../<model>.reb.240physx)
Read from the client files 2026-11-06, offline; see docs/index.html."""
import array
import math
import os
import struct
import sys

TERRAIN_STEP = 10.0    # map units between two height samples
TERRAIN_MARGIN = 2.0   # the ground must be this far above a shot's line to stop it
SIGHT_END = 4.0        # units at each end of a shot that are not checked (the muzzle, the robot's feet)
LEAF = 8               # triangles per leaf of a model's box tree
CELL = 64.0            # map units per cell of the grid that finds the models near a shot
SAMPLE = 16.0          # a shot looks up the grid every this many units (models are listed CELL/4 wider)


class Terrain:
    """A map's ground heights. Terrain01.hmd is u32 columns | u32 rows | rows x columns f32 heights in map units,
    one sample every TERRAIN_STEP along x (columns) and z (rows), centered on 0,0. The client's own terrain mesh
    tiles are these heights with no flip, the map's terrain nodes sit at those places, and the training ground
    floor reads -70 where robots stand at -69. Buildings, rocks and bridges are not in it (see Walls)."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.cols, self.rows = struct.unpack("<II", f.read(8))
            if not (2 <= self.cols <= 4097 and 2 <= self.rows <= 4097):
                raise ValueError("bad terrain size")
            self.h = array.array("f")
            self.h.fromfile(f, self.cols * self.rows)
        if sys.byteorder != "little":
            self.h.byteswap()
        self.x0 = -(self.cols - 1) * TERRAIN_STEP / 2
        self.z0 = -(self.rows - 1) * TERRAIN_STEP / 2

    def height(self, x, z):
        """Ground height at x,z, or None off the map. Of the two ways a grid cell can be cut into triangles, the
        lower one: the server never puts the ground higher than the client draws it."""
        fx, fz = (x - self.x0) / TERRAIN_STEP, (z - self.z0) / TERRAIN_STEP
        c, r = int(fx), int(fz)
        if not (0 <= fx and 0 <= fz and c < self.cols - 1 and r < self.rows - 1):
            return None
        tx, tz = fx - c, fz - r
        i, h = r * self.cols + c, self.h
        a, b, d, e = h[i], h[i + 1], h[i + self.cols], h[i + self.cols + 1]  # (c,r) (c+1,r) (c,r+1) (c+1,r+1)
        # cut along a-e: one triangle is a,b,e, the other a,d,e
        one = a + (b - a) * tx + (e - b) * tz if tx >= tz else a + (d - a) * tz + (e - d) * tx
        # cut along b-d: one triangle is a,b,d, the other b,d,e
        two = a + (b - a) * tx + (d - a) * tz if tx + tz <= 1 else e + (d - e) * (1 - tx) + (b - e) * (1 - tz)
        return min(one, two)

    def clear(self, a, b):
        """Is nothing of the ground in the way from a to b? Ends below the ground (in a tunnel or a building
        cut into a hill) are not checked, nor the first and last SIGHT_END units."""
        for x, y, z in (a, b):
            g = self.height(x, z)
            if g is None or y < g - TERRAIN_MARGIN:
                return True
        n = math.dist(a, b)
        steps = int(n / (TERRAIN_STEP / 2))
        for k in range(1, steps):
            s = k / steps
            if not SIGHT_END < s * n < n - SIGHT_END:
                continue
            x, y, z = (p + (q - p) * s for p, q in zip(a, b))
            g = self.height(x, z)
            if g is not None and g > y + TERRAIN_MARGIN:
                return False
        return True


# ---- reading the client's files (setup_data.py) ----

def read_reb(path):
    """The objects of a .reb world: [(class, matrix, properties)]. Each object is str name | u32 | 16 f32 world
    matrix (rows; a point is the row vector times it, the position in the last row) | 16 bytes id | str class |
    u32 n | n x (str name | u32 type | value). Strings are u32 length | text, NUL, one more byte. Types: 1 u8,
    2 i32, 3 f32, 5 vec3, 0x0b str, 0x0c 16 bytes (a color). Every object's first property is " Name"."""
    with open(path, "rb") as f:
        d = f.read()
    sizes = {1: 1, 2: 4, 3: 4, 5: 12, 0x0C: 16}

    def text(o):
        n, = struct.unpack_from("<I", d, o)
        if n > 4096 or o + 4 + n > len(d):
            raise ValueError("bad string")
        return d[o + 4:o + 4 + n].split(b"\0")[0].decode("latin-1"), o + 4 + n

    out, i = [], 0
    while True:
        i = d.find(b"\x07\x00\x00\x00 Name\x00", i + 1)
        if i < 0:
            return out
        try:
            n, = struct.unpack_from("<I", d, i - 4)
            for size in range(2, 128):  # the class name before the property count
                if struct.unpack_from("<I", d, i - 8 - size)[0] == size:
                    break
            else:
                continue
            cls = d[i - 4 - size:i - 4].split(b"\0")[0].decode("latin-1")
            matrix = struct.unpack_from("<16f", d, i - 8 - size - 16 - 64)
            props, o = {}, i
            for _ in range(min(n, 256)):
                name, o = text(o)
                t, = struct.unpack_from("<I", d, o)
                o += 4
                if t == 0x0B:
                    props[name], o = text(o)
                elif t in sizes:
                    raw = d[o:o + sizes[t]]
                    o += sizes[t]
                    props[name] = (raw[0] if t == 1 else struct.unpack("<i", raw)[0] if t == 2 else
                                   struct.unpack("<f", raw)[0] if t == 3 else struct.unpack("<3f", raw) if t == 5
                                   else raw)
                else:
                    raise ValueError(f"property type {t}")
            out.append((cls, matrix, props))
        except (struct.error, ValueError):
            continue


def read_nxs(path):
    """A PhysX cooked triangle mesh (.240physx): "NXS" 1 "MESH" | u32 version | u32 flags | f32 | u32 | f32 |
    u32 vertices | u32 triangles | vertices x vec3 | triangles x 3 indices (u8 with flag 8, u16 with flag 0x10,
    else u32). The rest (collision tree, materials) is not needed. Returns (vertices, flat index list) or None."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:8] != b"NXS\x01MESH" or len(d) < 0x24:
        return None
    flags, = struct.unpack_from("<I", d, 12)
    nv, nt = struct.unpack_from("<II", d, 0x1C)
    o = 0x24 + nv * 12
    size = 1 if flags & 8 else 2 if flags & 0x10 else 4
    if nv > 1 << 20 or nt > 1 << 20 or o + nt * 3 * size > len(d):
        return None
    v = struct.unpack_from(f"<{nv * 3}f", d, 0x24)
    idx = list(struct.unpack_from(f"<{nt * 3}{'BHI'[size.bit_length() - 1]}", d, o))
    if idx and max(idx) >= nv:
        return None
    return [v[i:i + 3] for i in range(0, len(v), 3)], idx


def build_tree(verts, idx):
    """Triangles (corner lists) and a box tree over them: nodes of 6 f32 (box) and 2 u32. A leaf has
    (first triangle, count); an inner node (first child, 0), its children are next to each other."""
    tris = [tuple(verts[idx[i + k]] for k in range(3)) for i in range(0, len(idx), 3)]
    boxes, links, order = [], [], []

    def box(ts):
        return [min(p[a] for t in ts for p in t) for a in range(3)] + [max(p[a] for t in ts for p in t) for a in range(3)]

    def make(ts, node):
        b = box(ts)
        boxes[node] = b
        if len(ts) <= LEAF:
            links[node] = (len(order), len(ts))
            order.extend(ts)
            return
        axis = max(range(3), key=lambda a: b[3 + a] - b[a])
        ts.sort(key=lambda t: t[0][axis] + t[1][axis] + t[2][axis])
        half = len(ts) // 2
        first = len(boxes)
        boxes.extend((None, None))
        links.extend((None, None))
        links[node] = (first, 0)
        make(ts[:half], first)
        make(ts[half:], first + 1)

    if tris:
        boxes.append(None)
        links.append(None)
        make(tris, 0)
    return order, boxes, links


def build_stations(client, name):
    """The map's Respawn Platforms (Esb_Base_RobotPlatform with its Func_RobotPlatform_Receive, joined by the
    function's EsbParent = the establishment's Name), as a list of dicts: id (EsbID), nation (EsbCurNation: 1 none,
    0x10 / 0x20 a side), pos (the establishment), use (the function, where its use area is), respawn (the
    function's EsbProducePos turned into a world point), radius and height of the use area. The client has these
    objects only as editor placeholders (Hook*): the server has to create them."""
    path = os.path.join(client, "Maps", name, name + ".reb")
    if not os.path.isfile(path):
        return []
    objs = read_reb(path)
    esbs = {p.get(" Name"): (m, p) for _, m, p in objs if p.get("FlyweightName") == "Esb_Base_RobotPlatform"}
    out = []
    for _, m, p in objs:
        if p.get("FlyweightName") != "Func_RobotPlatform_Receive" or p.get("EsbParent") not in esbs:
            continue
        em, ep = esbs[p["EsbParent"]]
        local = tuple(float(p.get("EsbProducePos" + a, 0.0)) for a in "XYZ")
        respawn = tuple(local[0] * m[k] + local[1] * m[4 + k] + local[2] * m[8 + k] + m[12 + k] for k in range(3))
        out.append({"id": int(ep.get("EsbID", 0)), "nation": int(ep.get("EsbCurNation", 0)),
                    "pos": [round(v, 2) for v in em[12:15]], "use": [round(v, 2) for v in m[12:15]],
                    "respawn": [round(v, 2) for v in respawn],
                    "radius": float(p.get("EsbEffectRadius", 6.0)), "height": float(p.get("EsbEffectHeight", 4.0))})
    return sorted(out, key=lambda st: st["id"])


def build_walls(client, name):
    """The .walls file for map <name> from the client folder, or None. Solid models are the map's PrefabInstance
    objects with CollidesRays on and GhostObject off; the mesh is found by file name, because some maps still
    name an older folder. Capture zones and other trigger objects are left out.
    File: "OWWALLS1" | u32 models | models x (u32 triangles | triangles x 9 f32 | u32 nodes | nodes x (6 f32 |
    2 u32)) | u32 placements | placements x (u32 model | 12 f32: the matrix's first three columns, by row)."""
    reb = os.path.join(client, "Maps", name, name + ".reb")
    if not os.path.isfile(reb):
        return None
    meshes = {}
    for folder, _, files in os.walk(os.path.join(client, "Models")):
        for f in files:
            if f.lower().endswith(".reb.240physx"):
                meshes.setdefault(f.lower()[:-len(".240physx")], os.path.join(folder, f))
    models, placed = {}, []
    for cls, m, p in read_reb(reb):
        model = p.get("ModelFilename")
        if cls != "PrefabInstance" or not model or p.get("GhostObject") or not p.get("CollidesRays"):
            continue
        path = meshes.get(model.replace("\\", "/").rsplit("/", 1)[-1].lower())
        if not path:
            continue  # bushes and the like have no collision mesh
        if path not in models:
            mesh = read_nxs(path)
            good = sum(1 for v in models.values() if v)  # model numbers count only the meshes that were read
            models[path] = (good, build_tree(*mesh)) if mesh and mesh[1] else None
        if models[path] and all(map(math.isfinite, m)):
            placed.append((models[path][0], [m[r * 4 + c] for r in range(4) for c in range(3)]))
    return pack_walls([v[1] for v in sorted((v for v in models.values() if v), key=lambda v: v[0])], placed)


def pack_walls(trees, placed):
    """The .walls file from build_tree results and (model index, 12 floats) placements."""
    parts = [b"OWWALLS1", struct.pack("<I", len(trees))]
    for order, boxes, links in trees:
        parts.append(struct.pack("<I", len(order)))
        parts.append(struct.pack(f"<{len(order) * 9}f", *(c for t in order for p in t for c in p)))
        parts.append(struct.pack("<I", len(boxes)))
        parts.extend(struct.pack("<6f2I", *b, *ln) for b, ln in zip(boxes, links))
    parts.append(struct.pack("<I", len(placed)))
    parts.extend(struct.pack("<I12f", i, *m) for i, m in placed)
    return b"".join(parts)


# ---- the server side ----

def segment_box(a, d, lo, hi, t0, t1):
    """Does a + t*d for t in [t0, t1] pass through the box lo..hi?"""
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if a[k] < lo[k] or a[k] > hi[k]:
                return False
            continue
        u, v = (lo[k] - a[k]) / d[k], (hi[k] - a[k]) / d[k]
        if u > v:
            u, v = v, u
        t0, t1 = max(t0, u), min(t1, v)
        if t0 > t1:
            return False
    return True


def segment_triangle(a, d, t, t0, t1):
    """Does a + s*d for s in [t0, t1] cross the triangle t (9 floats, either side)? Moller-Trumbore."""
    px, py, pz, qx, qy, qz, rx, ry, rz = t
    e1x, e1y, e1z, e2x, e2y, e2z = qx - px, qy - py, qz - pz, rx - px, ry - py, rz - pz
    hx, hy, hz = d[1] * e2z - d[2] * e2y, d[2] * e2x - d[0] * e2z, d[0] * e2y - d[1] * e2x
    det = e1x * hx + e1y * hy + e1z * hz
    if abs(det) < 1e-12:
        return False
    f = 1.0 / det
    sx, sy, sz = a[0] - px, a[1] - py, a[2] - pz
    u = f * (sx * hx + sy * hy + sz * hz)
    if u < 0.0 or u > 1.0:
        return False
    qx, qy, qz = sy * e1z - sz * e1y, sz * e1x - sx * e1z, sx * e1y - sy * e1x
    v = f * (d[0] * qx + d[1] * qy + d[2] * qz)
    if v < 0.0 or u + v > 1.0:
        return False
    s = f * (e2x * qx + e2y * qy + e2z * qz)
    return t0 <= s <= t1


class Walls:
    """The solid models on a map, from a .walls file. Each model's triangles are kept once, in its own space; a
    shot is moved into each nearby placement's space and tested against that model's box tree."""

    def __init__(self, path):
        with open(path, "rb") as f:
            d = f.read()
        if d[:8] != b"OWWALLS1":
            raise ValueError("not a walls file")
        o = 8
        n, = struct.unpack_from("<I", d, o)
        o += 4
        self.models = []
        for _ in range(n):
            nt, = struct.unpack_from("<I", d, o)
            o += 4
            tris = floats(d[o:o + nt * 36])  # 9 per triangle; flat arrays keep a big map near 5 MB
            o += nt * 36
            nn, = struct.unpack_from("<I", d, o)
            o += 4
            boxes, links = array.array("f"), array.array("I")
            for k, (*box, first, count) in enumerate(struct.iter_unpack("<6f2I", d[o:o + nn * 32])):
                if (first + count > nt) if count else (first <= k or first + 1 >= nn):
                    raise ValueError("bad walls tree")
                boxes.extend(box)
                links.extend((first, count))
            o += nn * 32
            if len(tris) != nt * 9 or len(links) != nn * 2:
                raise ValueError("short walls file")
            self.models.append((tris, boxes, links))
        n, = struct.unpack_from("<I", d, o)
        o += 4
        self.placed, self.grid = [], {}
        for _ in range(n):
            i, *m = struct.unpack_from("<I12f", d, o)
            o += 52
            if i >= len(self.models) or not self.models[i][2]:
                continue
            inverse = invert(m)
            if inverse is None:
                continue
            lo, hi = world_box(self.models[i][1][0:6], m)
            k = len(self.placed)
            self.placed.append((i, inverse, lo, hi))
            pad = CELL / 4
            for gx in range(int(math.floor((lo[0] - pad) / CELL)), int(math.floor((hi[0] + pad) / CELL)) + 1):
                for gz in range(int(math.floor((lo[2] - pad) / CELL)), int(math.floor((hi[2] + pad) / CELL)) + 1):
                    self.grid.setdefault((gx, gz), []).append(k)

    def near(self, a, b):
        """Placements whose box might touch the segment a-b."""
        n = math.dist(a, b)
        steps = max(1, int(n / SAMPLE) + 1)
        seen = set()
        for k in range(steps + 1):
            s = k / steps
            cell = (int(math.floor((a[0] + (b[0] - a[0]) * s) / CELL)),
                    int(math.floor((a[2] + (b[2] - a[2]) * s) / CELL)))
            seen.update(self.grid.get(cell, ()))
        return seen

    def clear(self, a, b):
        """Is no solid model in the way from a to b (the first and last SIGHT_END units not checked)?"""
        n = math.dist(a, b)
        if n <= 2 * SIGHT_END:
            return True
        t0, t1 = SIGHT_END / n, 1 - SIGHT_END / n
        d = [q - p for p, q in zip(a, b)]
        for k in self.near(a, b):
            i, inv, lo, hi = self.placed[k]
            if not segment_box(a, d, lo, hi, t0, t1):
                continue
            la, lb = apply(a, inv), apply(b, inv)
            if self.hits(self.models[i], la, [q - p for p, q in zip(la, lb)], t0, t1):
                return False
        return True

    @staticmethod
    def hits(model, a, d, t0, t1):
        tris, boxes, links = model
        stack = [0]
        while stack:
            k = stack.pop()
            if not segment_box(a, d, boxes[k * 6:k * 6 + 3], boxes[k * 6 + 3:k * 6 + 6], t0, t1):
                continue
            first, count = links[k * 2], links[k * 2 + 1]
            if count:
                for j in range(first, first + count):
                    if segment_triangle(a, d, tris[j * 9:j * 9 + 9], t0, t1):
                        return True
            else:
                stack.extend((first, first + 1))
        return False


def floats(raw):
    out = array.array("f")
    out.frombytes(raw[:len(raw) // 4 * 4])
    if sys.byteorder != "little":
        out.byteswap()
    return out


def invert(m):
    """Inverse of the affine transform m (12 floats: 3x3 rows, then the position), or None."""
    a, b, c, d, e, f, g, h, i, x, y, z = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-9:
        return None
    r = 1.0 / det
    inv = [(e * i - f * h) * r, (c * h - b * i) * r, (b * f - c * e) * r,
           (f * g - d * i) * r, (a * i - c * g) * r, (c * d - a * f) * r,
           (d * h - e * g) * r, (b * g - a * h) * r, (a * e - b * d) * r]
    # a point p maps to p*M + t, so back is (p - t)*M^-1
    tx = -(x * inv[0] + y * inv[3] + z * inv[6])
    ty = -(x * inv[1] + y * inv[4] + z * inv[7])
    tz = -(x * inv[2] + y * inv[5] + z * inv[8])
    return inv + [tx, ty, tz]


def apply(p, m):
    x, y, z = p
    return (x * m[0] + y * m[3] + z * m[6] + m[9], x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def world_box(root, m):
    """The world box around a model's root box placed with m."""
    corners = [apply((root[0 if i & 1 else 3], root[1 if i & 2 else 4], root[2 if i & 4 else 5]), m)
               for i in range(8)]
    return ([min(c[k] for c in corners) for k in range(3)], [max(c[k] for c in corners) for k in range(3)])
