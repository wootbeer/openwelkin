function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
function rtti(o) { try { var vt = o.readPointer(), col = vt.sub(4).readPointer(); return col.add(12).readPointer().add(8).readCString(); } catch (e) { return '?'; } }
var m = inst();
[5001, 6001, 7001].forEach(function (id) {
    var r = get(m, id);
    if (r.isNull()) { send(id + ': null'); return; }
    var wo = r.add(0x18);
    send(id + ': ' + r + ' ' + rtti(r) + ' pos=(' + [0x18, 0x1c, 0x20].map(function (o) { return wo.add(o).readFloat().toFixed(1); }).join(',') +
         ') owner=' + wo.add(0x10).readU32() + ' 17c=' + r.add(0x17c).readU32() + ' 184=0x' + r.add(0x184).readU32().toString(16) +
         ' 19c=0x' + r.add(0x19c).readU32().toString(16) + ' 308=' + r.add(0x308).readU8());
});
