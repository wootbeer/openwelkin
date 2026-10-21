// List the match robots this client knows: players (slot*1000000+1) and dummies (100001, 200001).
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
var gl = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetLocation@Robot@GameClient@@UBE?AVVector@@XZ')), 'pointer', ['pointer', 'pointer'], 'thiscall');
var fwid = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetFlyweightID@WorldObject@GameClient@@QBEKXZ')), 'uint', ['pointer'], 'thiscall');
var m = inst(), out = Memory.alloc(16), s = [];
[1000001, 2000001, 100001, 200001].forEach(function (id) {
    var r = get(m, id);
    if (r.isNull()) { s.push(id + ': -'); return; }
    gl(r, out);
    s.push(id + ': fw ' + fwid(r.add(0x18)) + ' at (' + [0, 4, 8].map(function (o) { return out.add(o).readFloat().toFixed(1); }).join(',') + ')');
});
send(s.join(' | '));
