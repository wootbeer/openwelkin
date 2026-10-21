var g = Module.findBaseAddress('Game.dll');
function at(a) { return g.add(a - 0x10000000); }
Interceptor.attach(at(0x10613c00), { onEnter: function () { send('SR4_SystemPartChange OnBegin called'); } });
var getUser = new NativeFunction(at(0x1000d2a1), 'pointer', ['pointer', 'uint'], 'thiscall');
var lum = at(0x109c9898).readPointer();
var u = getUser(lum, 0);
send('user0=' + u + (u.isNull() ? '' : ' +0x4c=' + u.add(0x4c).readU32()));
var lu2 = at(0x109c95e4).readPointer();
send('0x109c95e4=' + lu2);
var inst = new NativeFunction(Module.getExportByName('Engine.dll', '?Instance@EventAttemper@@SAPAV1@XZ'), 'pointer', []);
var add = new NativeFunction(Module.getExportByName('Engine.dll', '?AddEvent@EventAttemper@@QAEPAVEvent@@EGMW4EVENT_ACTION_TYPE@@@Z'), 'pointer', ['pointer', 'uint8', 'uint16', 'float', 'int'], 'thiscall');
send('AddEvent -> ' + add(inst(), 9, 40150, 0.0, 0));
