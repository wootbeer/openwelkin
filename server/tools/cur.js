function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var inst = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@RobotManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var get = new NativeFunction(rt(Module.findExportByName('Game.dll', '?GetRobot@RobotManager@GameClient@@QAEPAVRobot@2@J@Z')), 'pointer', ['pointer', 'int'], 'thiscall');
var r = get(inst(), 5001);
var cur = r.add(0x108).readPointer();
var ev = r.add(0x120).readPointer(), ee = r.add(0x124).readPointer(); send('equips=' + (ev.isNull() ? 0 : ee.sub(ev).toInt32() / 4) + (ev.isNull() || ee.equals(ev) ? '' : ' e0=' + ev.readPointer()) + ' robot=' + r + ' cur=' + cur + ' slot=' + r.add(0x10c).readS32() + ' 308=' + r.add(0x308).readU8() + ' 17c=' + r.add(0x17c).readU32() + ' 180=' + r.add(0x180).readU32() + ' 184=0x' + r.add(0x184).readU32().toString(16) + ' 194=' + r.add(0x194).readU32() + ' 19c=0x' + r.add(0x19c).readU32().toString(16) +
     (cur.isNull() ? '' : ' wstate=' + cur.add(0x374).readU32() + ' ammo=' + cur.add(0xe0).readFloat()));
