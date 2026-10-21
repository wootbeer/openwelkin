"""unopt.py <file.opt> <outdir> -- extract a MarsWar .opt flyweight archive.

Layout: u32 data_start | u32 count | count x (u32 entry_len | u32 raw_size | u32 offset_from_data_start | u8 packed | name)
A packed blob is u32 compressed_size followed by a gzip stream.
"""
import os, struct, sys, zlib

src, out = sys.argv[1], sys.argv[2]
d = open(src, "rb").read()
data_start, count = struct.unpack_from("<II", d, 0)
off = 8
for _ in range(count):
    elen, raw_size, rel, packed = struct.unpack_from("<IIIB", d, off)
    name = d[off + 13: off + elen].decode("latin-1")
    off += elen
    chunk = d[data_start + rel:]
    if packed:
        (csize,) = struct.unpack_from("<I", chunk)
        body = zlib.decompress(chunk[4:4 + csize], 16 + zlib.MAX_WBITS)
    else:
        body = chunk[:raw_size]
    path = os.path.join(out, *name.strip("\\").split("\\"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(body[:raw_size])
print(f"extracted {count} files to {out}")
