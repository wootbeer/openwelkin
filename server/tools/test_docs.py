# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""test_docs.py -- docs/PROTOCOL.md is up to date with docs/index.html, every message made it across, and every
link inside it goes somewhere."""
import re
import sys

import docs_md

fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(name)


source = open(docs_md.SOURCE, encoding="utf-8").read()
built = docs_md.build(source)
current = open(docs_md.TARGET, encoding="utf-8").read() if docs_md.os.path.exists(docs_md.TARGET) else ""
check("PROTOCOL.md is up to date (run tools/docs_md.py)", built == current)
cards = re.findall(r'<div class="msg" id="([^"]+)"', source)
anchors = set(re.findall(r'<a name="([^"]+)"></a>', built))
missing = [c for c in cards if c not in anchors]
check(f"all {len(cards)} messages are in it", not missing, str(missing))
broken = sorted({t for t in re.findall(r"\]\(#([^)]+)\)", built) if t not in anchors})
check("every link inside it has a target", not broken, str(broken))
prose = re.sub(r"```.*?```|`[^`\n]*`", "", built, flags=re.S)  # code shows < as it is
left = re.findall(r"<(?!a name=|/a>)[a-z/][^>]{0,30}", prose)
check("no HTML tags left over but anchors", not left, str(left[:5]))
print(f"{len(fails)} failed" if fails else "all ok")
sys.exit(1 if fails else 0)
