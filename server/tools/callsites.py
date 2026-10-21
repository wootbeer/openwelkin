"""callsites.py <dll> <import-or-export-substring> <imm> [window] -- find calls to a function (direct or through an
import pointer) that have `push <imm>` within the preceding <window> instructions (default 12); prints the enclosing
export so you can find e.g. every ShowDialog(id) for one dialog id."""
import sys, bisect, pickle, os
import pefile, capstone

dll, needle, imm = sys.argv[1], sys.argv[2], int(sys.argv[3], 0)
window = int(sys.argv[4]) if len(sys.argv) > 4 else 12
pe = pefile.PE(dll)
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
funcs = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(dll) + ".funcs"), "rb"))
starts = sorted(funcs)
# import address table slots whose name matches
slots = {imp.address for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) for imp in d.imports
         if imp.name and needle in imp.name.decode()}
targets = {a for a, n in funcs.items() if needle in n}
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
for s in pe.sections:
    if not s.Characteristics & 0x20000000:
        continue
    recent = []
    for ins in md.disasm(img[s.VirtualAddress: s.VirtualAddress + s.Misc_VirtualSize], base + s.VirtualAddress):
        if ins.mnemonic == "call":
            op = ins.op_str
            hit = any(f"[{a:#x}]" in op for a in slots) or any(op == f"{a:#x}" for a in targets)
            if hit and any(r.mnemonic == "push" and r.op_str == f"{imm:#x}" for r in recent):
                i = bisect.bisect_right(starts, ins.address) - 1
                print(f"{ins.address:08x}  in {funcs[starts[i]][:100]} +{ins.address - starts[i]:#x}")
        recent = (recent + [ins])[-window:]
