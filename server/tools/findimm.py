"""findimm.py <dll> <imm> [<imm>...] -- find instructions using an immediate; report enclosing export (nearest preceding)."""
import sys, bisect, pefile, capstone, pickle, os

dll = sys.argv[1]
imms = [int(x, 0) for x in sys.argv[2:]]
pe = pefile.PE(dll)
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
cache = os.path.join(os.path.dirname(__file__), os.path.basename(dll) + ".funcs")
if os.path.exists(cache):
    funcs = pickle.load(open(cache, "rb"))
else:
    funcs = {}
    for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else []:
        if not e.name:
            continue
        a = base + e.address
        b = img[a - base: a - base + 5]
        if b[:1] == b"\xe9":
            a = a + 5 + int.from_bytes(b[1:5], "little", signed=True)
        funcs.setdefault(a, e.name.decode())
    pickle.dump(funcs, open(cache, "wb"))
starts = sorted(funcs)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
for s in pe.sections:
    if not s.Characteristics & 0x20000000:
        continue
    va = base + s.VirtualAddress
    code = img[s.VirtualAddress: s.VirtualAddress + s.Misc_VirtualSize]
    for ins in md.disasm(code, va):
        for imm in imms:
            if f"{imm:#x}" in ins.op_str.split(", ")[-1:] or f"{imm:#x}" == ins.op_str or ins.op_str.endswith(f", {imm:#x}"):
                i = bisect.bisect_right(starts, ins.address) - 1
                fn = f"{funcs[starts[i]]} +{ins.address - starts[i]:#x}" if i >= 0 else "?"
                print(f"{ins.address:08x} {ins.mnemonic} {ins.op_str}   in {fn}")
