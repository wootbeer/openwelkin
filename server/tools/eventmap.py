"""eventmap.py <Game.dll> -- list EventAttemper::AddObserver(catalog, type, observer) registrations with RTTI class names.

Heuristic: for each 'push <type>; push <catalog>; call ...' the observer's vtable is the most recent
'mov dword ptr [reg], <imm>' whose imm has an RTTI complete-object locator.
"""
import sys, pefile, capstone

pe = pefile.PE(sys.argv[1])
base = pe.OPTIONAL_HEADER.ImageBase
img = pe.get_memory_mapped_image()


def rd(a):
    return int.from_bytes(img[a - base:a - base + 4], "little")


def rtti_name(vt):
    try:
        col = rd(vt - 4)
        td = rd(col + 12)
        raw = img[td + 8 - base: td + 8 - base + 160].split(b"\0")[0]
        if raw.startswith(b".?AV"):
            return raw[4:].decode().split("@@")[0].replace("@", "::")
    except Exception:
        pass
    return None


md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.skipdata = True
found = {}
for s in pe.sections:
    if not s.Characteristics & 0x20000000:
        continue
    window = []
    for ins in md.disasm(img[s.VirtualAddress: s.VirtualAddress + s.Misc_VirtualSize], base + s.VirtualAddress):
        window.append(ins)
        if len(window) > 14:
            window.pop(0)
        if ins.mnemonic != "call" or len(window) < 3:
            continue
        p_cat, p_type = window[-2], window[-3]
        if p_cat.mnemonic != "push" or p_type.mnemonic != "push":
            continue
        try:
            cat, typ = int(p_cat.op_str, 0), int(p_type.op_str, 0)
        except ValueError:
            continue
        if not (0 < cat < 64 and 1000 <= typ < 0x10000):
            continue
        name = None
        for w in reversed(window[:-3]):
            if w.mnemonic == "mov" and w.op_str.startswith("dword ptr [") and "], 0x" in w.op_str:
                name = rtti_name(int(w.op_str.split(", ")[-1], 16))
                if name:
                    break
        if name and "Observer" in name:
            found.setdefault((cat, typ), set()).add(name)

for (cat, typ), names in sorted(found.items()):
    print(f"{cat:3d} {typ:6d}  {', '.join(sorted(names))}")
