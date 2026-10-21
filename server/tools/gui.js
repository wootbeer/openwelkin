var inst = new NativeFunction(Module.getExportByName('Engine.dll', '?Instance@CDlgMgr@@SAPAV1@XZ'), 'pointer', []);
var dm = inst();
send('CDlgMgr=' + dm + ' +0x80=' + dm.add(0x80).readU8());
var p = Module.findBaseAddress('Game.dll').add(0x106e32b0 - 0x10000000);
var n = 0;
Interceptor.attach(p, { onLeave: function (r) { if (n++ < 3) send('HasAnyGUIopen -> ' + r); } });
