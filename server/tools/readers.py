"""readers.py <dll> <addr|export> [maxbytes] -- summarize a BinStream reader function.

Recognizes the bounded-read idiom used by Game.dll's (Write)Snapshot readers:
    lea R, [cur + N] ; cmp R, [stream + 8] ; ... ; add [stream + 0xc], N   -> read N bytes
and prints reads in order together with calls (named via exports) and pushed string constants.
"""
import sys, re, pefile, capstone

pe = pefile.PE(sys.argv[1])
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()


def thunk(a):
    if base <= a < base + len(img) and img[a - base] == 0xE9:
        return a + 5 + int.from_bytes(img[a - base + 1:a - base + 5], "little", signed=True)
    return a


names = {}
for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
    if e.name:
        names.setdefault(thunk(base + e.address), e.name.decode())
pe.parse_data_directories(directories=[1])
for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
    for i in d.imports:
        names[i.address] = (i.name or b"?").decode()

arg = sys.argv[2]
addr = int(arg, 16) if re.fullmatch(r"0x[0-9a-fA-F]+", arg) else next(a for a, n in names.items() if arg in n)
addr = thunk(addr)
size = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x1000
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
print(f"# {addr:#x} {names.get(addr, '')}")
pending = {}
for ins in md.disasm(img[addr - base: addr - base + size], addr):
    op = ins.op_str
    m = re.match(r"(\w+), \[(\w+) \+ (0x[0-9a-f]+|\d+)\]$", op)
    if ins.mnemonic == "lea" and m and int(m.group(3), 0) in (1, 2, 4, 8, 0xc, 0x10, 0x40):
        pending[m.group(1)] = int(m.group(3), 0)
    elif ins.mnemonic == "cmp" and re.match(r"(\w+), dword ptr \[\w+ \+ 8\]$", op) and op.split(",")[0] in pending:
        n = pending.pop(op.split(",")[0])
        print(f"{ins.address:08x}  read {n}")
    elif ins.mnemonic == "call":
        t = None
        if op.startswith("0x"):
            t = thunk(int(op, 16))
        elif op.startswith("dword ptr [0x"):
            t = int(op[11:-1], 16)
        label = names.get(t, hex(t) if t else op)
        if not re.search(r"Logger|release@Ref|addref@Ref|basic_string|_invalid_parameter|lock@Lock", label):
            print(f"{ins.address:08x}  call {label[:110]}")
    elif ins.mnemonic == "push" and op.startswith("0x"):
        v = int(op, 16)
        if base <= v < base + len(img):
            s = img[v - base: v - base + 60].split(b"\0")[0]
            if len(s) > 3 and all(32 <= c < 127 for c in s):
                print(f"{ins.address:08x}  str {s.decode()!r}")
    if ins.mnemonic == "ret":
        print(f"{ins.address:08x}  ret")
        if "NOSTOP" not in sys.argv:
            break
