"""tmpdir.py -- temporary folders for the test tools, removed when the tool exits.

make(prefix) returns a new folder under the system temp folder. At exit it closes the store's database files if
loginserver was imported, and its log (Windows will not delete open files) and removes the folder."""
import atexit
import shutil
import sys
import tempfile

_dirs = []


def make(prefix="mw-test-"):
    path = tempfile.mkdtemp(prefix=prefix)
    _dirs.append(path)
    return path


@atexit.register
def _clean():
    ls = sys.modules.get("loginserver")
    store = getattr(ls, "STORE", None) if ls else None
    for name in ("con", "reader"):
        try:
            getattr(store, name).close()
        except Exception:  # noqa: BLE001 -- not opened, or already closed
            pass
    try:
        ls.LOG.close()
    except Exception:  # noqa: BLE001
        pass
    for name in ("ACCOUNTS", "BANS"):
        try:
            getattr(ls, name).con.close()
        except Exception:  # noqa: BLE001
            pass
    for path in _dirs:
        shutil.rmtree(path, ignore_errors=True)
