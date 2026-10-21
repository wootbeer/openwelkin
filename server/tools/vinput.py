"""vinput.py <pid|MarsWar.exe> <step> [step ...] -- drive the client's DirectInput keyboard/mouse (tools/vinput.js).

Steps run in order:
  w:3          hold a key for 3 s (keys: w a s d q e r f v space shift ctrl tab 1-9 0)
  w+d:2        hold several keys together
  turn:200     move the mouse 200 units right (negative = left); turn:0,-50 also moves up
  fire:1.5     hold the left mouse button for 1.5 s
  wait:2       pause
Works on an unfocused (even minimized) client. Example: python -I vinput.py 1234 w:2 turn:300 fire:1"""
import os
import sys
import time

import frida

SCANCODES = {"esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09,
             "9": 0x0A, "0": 0x0B, "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "a": 0x1E, "s": 0x1F,
             "d": 0x20, "f": 0x21, "ctrl": 0x1D, "shift": 0x2A, "v": 0x2F, "space": 0x39, "m": 0x32}


def main():
    target = int(sys.argv[1]) if sys.argv[1].isdigit() else sys.argv[1]
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vinput.js")).read()
    session = frida.attach(target)
    script = session.create_script(js)
    ready = []
    script.on("message", lambda m, d: ready.append(m))
    script.load()
    time.sleep(0.3)
    try:
        for step in sys.argv[2:]:
            name, _, arg = step.partition(":")
            if name == "wait":
                time.sleep(float(arg))
            elif name == "turn":
                parts = [int(x) for x in arg.split(",")]
                script.post({"type": "move", "dx": parts[0], "dy": parts[1] if len(parts) > 1 else 0})
                time.sleep(abs(parts[0]) / 12 / 50 + 0.2)
            elif name == "fire":
                script.post({"type": "button", "index": 0, "down": True})
                time.sleep(float(arg))
                script.post({"type": "button", "index": 0, "down": False})
            else:
                codes = [SCANCODES[k] for k in name.split("+")]
                for c in codes:
                    script.post({"type": "key", "code": c, "down": True})
                time.sleep(float(arg or 0.2))
                for c in codes:
                    script.post({"type": "key", "code": c, "down": False})
            time.sleep(0.05)
    finally:
        script.post({"type": "clear"})
        time.sleep(0.2)
        session.detach()
    print("done")


if __name__ == "__main__":
    main()
