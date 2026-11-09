"""msgids.py <dll> [low] [high] -- list the catalog 9 messages the client builds: every `push <id>; push 9` with an
id in [low, high] (default 40000..40999), with the function it is in. Needs <dll>.funcs next to this script
(written by findimm.py on its first run). Example: python -I msgids.py "...\\System\\Game.dll" 40300 40399"""
import sys, bisect, pickle, os
import pefile, capstone

dll = sys.argv[1]
lo = int(sys.argv[2], 0) if len(sys.argv) > 2 else 40000
hi = int(sys.argv[3], 0) if len(sys.argv) > 3 else 40999
pe = pefile.PE(dll)
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
funcs = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(dll) + ".funcs"), "rb"))
starts = sorted(funcs)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
hits = []
for s in pe.sections:
    if not s.Characteristics & 0x20000000:
        continue
    prev = None
    for ins in md.disasm(img[s.VirtualAddress: s.VirtualAddress + s.Misc_VirtualSize], base + s.VirtualAddress):
        if ins.mnemonic == "push" and ins.op_str == "9" and prev is not None and prev.mnemonic == "push":
            try:
                v = int(prev.op_str, 0)
            except ValueError:
                v = None
            if v is not None and lo <= v <= hi:
                i = bisect.bisect_right(starts, ins.address) - 1
                hits.append((v, ins.address, funcs[starts[i]][:110] if i >= 0 else "?"))
        prev = ins
for v, addr, name in sorted(hits):
    print(f"{v} {addr:08x} {name}")
