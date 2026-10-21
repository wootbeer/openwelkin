// Basic speed / velocity of the local player's robot (slot 1) and the dummies.
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
function f(n) { return rt(Module.findExportByName('Game.dll', n)); }
var inst = new NativeFunction(f('?Instance@RobotManager@GameClient@@SAPAV12@XZ'), 'pointer', []);
var get = new NativeFunction(f('?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z'), 'pointer', ['pointer', 'int'], 'thiscall');
var speed = new NativeFunction(f('?GetBasicSpeed@Robot@GameClient@@QBEMXZ'), 'float', ['pointer'], 'thiscall');
var up = new NativeFunction(f('?GetUpSpeed@Robot@GameClient@@QBEMXZ'), 'float', ['pointer'], 'thiscall');
var m = inst(), s = [];
[1000001, 100001].forEach(function (id) {
    var r = get(m, id);
    if (r.isNull()) { s.push(id + ' -'); return; }
    s.push(id + ' basic=' + speed(r).toFixed(3) + ' up=' + up(r).toFixed(3) + ' steer=' + r.add(0x354).readU32() + ' 19c=0x' + r.add(0x19c).readU32().toString(16));
});
send(s.join(' | '));
