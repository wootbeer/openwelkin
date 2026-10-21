"""handler.py <dll> <ClassNameSubstring> -- find vtables of matching RTTI classes and print their methods (thunks resolved)."""
import sys, re, pefile

pe = pefile.PE(sys.argv[1])
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
want = sys.argv[2]


def rd(a):
    return int.from_bytes(img[a - base:a - base + 4], "little")


def thunk(a):
    if base <= a < base + len(img) and img[a - base] == 0xE9:
        return a + 5 + int.from_bytes(img[a - base + 1:a - base + 5], "little", signed=True)
    return a


# type descriptors: ".?AV<name>@@" preceded by vtable ptr + spare (8 bytes)
tds = {}
for m in re.finditer(rb"\.\?AV([A-Za-z0-9_@]+)@@\0", img):
    name = m.group(1).decode()
    if want in name:
        tds[base + m.start() - 8] = name
# complete object locators: sig(0), offset, cdOffset, pTypeDescriptor, pClassDescriptor
cols = {}
for td, name in tds.items():
    pat = td.to_bytes(4, "little")
    for m in re.finditer(re.escape(pat), img):
        col = base + m.start() - 12
        if rd(col) == 0 and rd(col + 4) == 0:
            cols[col] = name
for col, name in cols.items():
    pat = col.to_bytes(4, "little")
    for m in re.finditer(re.escape(pat), img):
        vt = base + m.start() + 4
        methods = [thunk(rd(vt + 4 * i)) for i in range(3)]
        print(f"{name}  vtable={vt:#x}  methods={[hex(x) for x in methods]}")
