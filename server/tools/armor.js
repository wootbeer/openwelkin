function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
var cnt = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetArmorCount@DamageUnit@GameClient@@QBEJXZ')), 'int', ['pointer'], 'thiscall');
var arm = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetArmor@DamageUnit@GameClient@@QAEABUArmorInfo@12@I@Z')), 'pointer', ['pointer', 'uint'], 'thiscall');
var m = inst();
[5001, 6001, 7001].forEach(function (id) {
    var r = get(m, id); if (r.isNull()) return;
    var n = cnt(r), s = [];
    for (var i = 0; i < n; i++) { var a = arm(r, i); s.push('[' + a.readS32() + ',' + a.add(4).readS32() + ',' + a.add(8).readFloat() + ',' + a.add(12).readFloat() + ']'); }
    send(id + ' armors=' + n + ' ' + s.join(' ') + ' type(+0x88)=' + r.add(0x88).readS32());
});
