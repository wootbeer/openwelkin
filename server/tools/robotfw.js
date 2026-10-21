// Print the flyweight id (robot type) of robots 5001/6001/7001 and their armor slot count.
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
var fwid = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetFlyweightID@WorldObject@GameClient@@QBEKXZ')), 'uint', ['pointer'], 'thiscall');
var m = inst();
[5001, 6001, 7001].forEach(function (id) {
    var r = get(m, id);
    send(id + ': ' + (r.isNull() ? 'null' : 'flyweight ' + fwid(r.add(0x18))));
});
