"""xref.py <dll> <export-substring|addr> -- find direct calls/jmps to a function (and to its incremental-link thunk)."""
import sys, re, bisect, pefile, capstone

pe = pefile.PE(sys.argv[1])
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()


def thunk(a):
    if img[a - base] == 0xE9:
        return a + 5 + int.from_bytes(img[a - base + 1:a - base + 5], "little", signed=True)
    return a


funcs, targets = {}, set()
for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else []:
    if not e.name:
        continue
    a = base + e.address
    funcs.setdefault(thunk(a), e.name.decode())
    if sys.argv[2] in e.name.decode():
        targets |= {a, thunk(a)}
        print(f"# target {e.name.decode()[:100]} {a:#x} -> {thunk(a):#x}")
if re.fullmatch(r"0x[0-9a-fA-F]+", sys.argv[2]):
    a = int(sys.argv[2], 16)
    targets |= {a, thunk(a)}
# all incremental-link thunks that jump to a target are targets too
for off in range(0, 0x40000, 5):
    a = base + 0x1000 + off
    if img[a - base] == 0xE9 and thunk(a) in targets:
        targets.add(a)
starts = sorted(funcs)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
for s in pe.sections:
    if not s.Characteristics & 0x20000000:
        continue
    for ins in md.disasm(img[s.VirtualAddress:s.VirtualAddress + s.Misc_VirtualSize], base + s.VirtualAddress):
        if ins.mnemonic in ("call", "jmp") and ins.op_str.startswith("0x"):
            t = int(ins.op_str, 16)
            if t in targets and ins.address not in targets:
                i = bisect.bisect_right(starts, ins.address) - 1
                print(f"{ins.address:#x}  {ins.mnemonic} {t:#x}   near {funcs[starts[i]][:90] if i >= 0 else '?'}")
