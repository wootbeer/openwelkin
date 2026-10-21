// Local robot (slot 1) movement state: pose (+0x18c), move flags (+0x354), steer (+0x358), jump speed (+0x360),
// +0x168 (jump source), velocity, basic/up speed.
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
function f(n) { return rt(Module.findExportByName('Game.dll', n)); }
var inst = new NativeFunction(f('?Instance@RobotManager@GameClient@@SAPAV12@XZ'), 'pointer', []);
var get = new NativeFunction(f('?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z'), 'pointer', ['pointer', 'int'], 'thiscall');
var vel = new NativeFunction(f('?GetVelocity@Robot@GameClient@@QBEABVVector@@XZ'), 'pointer', ['pointer'], 'thiscall');
var r = get(inst(), 1000001);
function v3(p) { return '(' + [0, 4, 8].map(function (o) { return p.add(o).readFloat().toFixed(2); }).join(',') + ')'; }
send('pose18c=' + r.add(0x18c).readU32() + ' flags=' + r.add(0x354).readU32() + ' steer=' + r.add(0x358).readU32() +
     ' 360=' + r.add(0x360).readFloat().toFixed(2) + ' 168=' + r.add(0x168).readFloat().toFixed(2) +
     ' 164=' + r.add(0x164).readFloat().toFixed(2) + ' 16c=' + r.add(0x16c).readFloat().toFixed(2) + ' vel=' + v3(vel(r)) +
     ' 19c=0x' + r.add(0x19c).readU32().toString(16) + ' actor(d4)=' + r.add(0xd4).readPointer() + ' 194=' + r.add(0x194).readU32() + ' y=' + r.add(0x18 + 0x1c).readFloat().toFixed(2));
