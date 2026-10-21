// Sample the local robot's pose / grounded bit / height every 0.25 s for 6 s.
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
function f(n) { return rt(Module.findExportByName('Game.dll', n)); }
var inst = new NativeFunction(f('?Instance@RobotManager@GameClient@@SAPAV12@XZ'), 'pointer', []);
var get = new NativeFunction(f('?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z'), 'pointer', ['pointer', 'int'], 'thiscall');
var r = get(inst(), 1000001), n = 0, out = [];
var t = setInterval(function () {
    out.push((n * 0.25).toFixed(2) + ' pose=' + r.add(0x18c).readU32() + ' g=' + ((r.add(0x19c).readU32() >> 7) & 1) +
             ' flags=' + r.add(0x354).readU32() + ' vy=' + r.add(0x360).readFloat().toFixed(1) + ' y=' + r.add(0x34).readFloat().toFixed(2));
    if (++n >= 24) { clearInterval(t); send(out.join('\n')); }
}, 250);
