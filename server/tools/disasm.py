"""dis.py <dll> <addr|export-substring> [maxbytes]  -- disassemble with symbol annotation."""
import sys, re, os, pefile, capstone, pickle

dll = sys.argv[1]
cache = os.path.join(os.path.dirname(__file__), os.path.basename(dll) + ".symcache")
pe = pefile.PE(dll)
base = pe.OPTIONAL_HEADER.ImageBase
if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(dll):
    syms = pickle.load(open(cache, "rb"))
else:
    syms = {}
    for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else []:
        if e.name:
            syms.setdefault(base + e.address, e.name.decode())
    for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        for i in d.imports:
            syms[i.address] = d.dll.decode() + "!" + (i.name.decode() if i.name else f"#{i.ordinal}")
    pickle.dump(syms, open(cache, "wb"))

target = sys.argv[2]
if re.fullmatch(r"(0x)?[0-9a-fA-F]+", target):
    addr = int(target, 16)
else:
    m = [(a, n) for a, n in syms.items() if target in n and "!" not in n]
    for a, n in m:
        print(f"# {a:#x} {n}")
    addr = m[0][0]
size = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x400
NOSTOP = bool(os.environ.get("NOSTOP"))
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
img = pe.get_memory_mapped_image()

def thunk_target(a):
    """Resolve an incremental-link 'jmp rel32' thunk."""
    b = img[a - base: a - base + 5]
    if b[:1] == b"\xe9":
        return a + 5 + int.from_bytes(b[1:5], "little", signed=True)
    return None

# name thunk targets after the export they belong to
for a, n in list(syms.items()):
    if "!" not in n:
        t = thunk_target(a)
        if t and t not in syms:
            syms[t] = n
while (t := thunk_target(addr)):
    print(f"# thunk {addr:#x} -> {t:#x}")
    addr = t
data = img[addr - base: addr - base + size]

def strat(a):
    try:
        b = pe.get_memory_mapped_image()[a - base: a - base + 80]
    except Exception:
        return None
    m = re.match(rb"[\x20-\x7e]{4,}", b)
    return m.group().decode() if m else None

for ins in md.disasm(data, addr):
    note = ""
    for v in re.findall(r"0x[0-9a-f]+", ins.op_str):
        v = int(v, 16)
        if v in syms:
            note += " ; " + syms[v]
        elif base <= v < base + pe.OPTIONAL_HEADER.SizeOfImage:
            s = strat(v)
            if s:
                note += f' ; "{s[:60]}"'
    print(f"{ins.address:08x}  {ins.mnemonic:6} {ins.op_str}{note}")
    if ins.mnemonic == "int3" and not NOSTOP:
        break
