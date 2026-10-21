"""ackreads.py <dll> <handler addr> [maxbytes] -- list the stream reads a request/reply observer makes, in call order.

Hall replies are read with small Outpop Stream ">>" helpers (thunks) that start with get_readable() and
`cmp eax, N` (N = bytes needed). This walks the handler, follows every direct call once, and prints each helper's
size, plus the Message getters (GetAckMessage / GetRequestMessage) so you can tell which stream is being read.
Strings show up as calls into the varint string reader (size "str").
"""
import sys, pickle, os
import pefile, capstone

dll, start = sys.argv[1], int(sys.argv[2], 0)
maxbytes = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x600
pe = pefile.PE(dll)
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()
here = os.path.dirname(os.path.abspath(__file__))
syms = pickle.load(open(os.path.join(here, os.path.basename(dll) + ".symcache"), "rb"))
funcs = pickle.load(open(os.path.join(here, os.path.basename(dll) + ".funcs"), "rb"))
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True


def resolve(a):
    b = img[a - base:a - base + 5]
    return a + 5 + int.from_bytes(b[1:5], "little", signed=True) if b[:1] == b"\xe9" else a


def helper_size(a):
    """Return the byte count a stream helper checks for, 'str' for the string reader, or None."""
    t = resolve(a)
    ins = list(md.disasm(img[t - base:t - base + 0x60], t))[:30]
    text = " ".join(f"{i.mnemonic} {i.op_str}" for i in ins)
    if "get_readable" in text or any("0x109d2c7c" in i.op_str for i in ins):
        for i in ins:
            if i.mnemonic == "cmp" and i.op_str.startswith("eax, "):
                return int(i.op_str.split(", ")[1], 0)
    if "MultiByteToWideChar" in text or any("0x109d2258" in i.op_str for i in ins):
        return "str"
    for i in ins:  # inline BinStream reader: lea edx, [ecx + N]; cmp edx, [eax + 8]
        if i.mnemonic == "lea" and "+ " in i.op_str and i.op_str.startswith(("edx", "esi", "edi", "ecx")):
            try:
                return "bin" + str(int(i.op_str.split("+ ")[1].rstrip("]"), 0))
            except ValueError:
                pass
    return None


for ins in md.disasm(img[start - base:start - base + maxbytes], start):
    if ins.mnemonic == "call":
        op = ins.op_str
        name = None
        if op.startswith("dword ptr [0x"):
            slot = int(op[op.index("0x"):op.index("]")], 16)
            name = syms.get(slot)
        elif op.startswith("0x"):
            tgt = int(op, 16)
            name = syms.get(tgt) or funcs.get(resolve(tgt))
            size = helper_size(tgt)
            if size is not None:
                print(f"{ins.address:08x}  read {size}")
                continue
        if name and any(k in name for k in ("GetAckMessage", "GetRequestMessage", "GetStream", "get_result")):
            print(f"{ins.address:08x}  {name.split('@')[0].lstrip('?')}")
    if ins.mnemonic == "int3":
        int3s = globals().get("int3s", 0) + 1
        globals()["int3s"] = int3s
        if int3s >= 3:  # padding after the function: done
            break
    else:
        globals()["int3s"] = 0
