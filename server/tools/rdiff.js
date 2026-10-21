function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
var m = inst(), a = get(m, 5001), b = get(m, 7001), out = [];
for (var o = 0; o < 0x800; o += 4) {
    var x = a.add(o).readU32(), y = b.add(o).readU32();
    if (x !== y && !(x > 0x1000000 && y > 0x1000000 && Math.abs(x - y) > 0x10000)) out.push(o.toString(16) + ':' + x.toString(16) + '/' + y.toString(16));
}
send(out.join(' '));
var ca = a.add(0xd0).readPointer(), cb = b.add(0xd0).readPointer(), o2 = [];
for (var o = 0; o < 0x400; o += 4) { var x = ca.add(o).readU32(), y = cb.add(o).readU32();
    if (x !== y && !(x > 0x1000000 && y > 0x1000000)) o2.push(o.toString(16) + ':' + x.toString(16) + '/' + y.toString(16)); }
send('CombinRobot: ' + o2.join(' '));
