var inst = new NativeFunction(Module.getExportByName('Engine.dll', '?Instance@CDlgMgr@@SAPAV1@XZ'), 'pointer', []);
var show = new NativeFunction(Module.getExportByName('Engine.dll', '?ShowDialog@CDlgMgr@@QAE_NH_N0M@Z'), 'uint8', ['pointer', 'int', 'uint8', 'uint8', 'float'], 'thiscall');
send('show 112 -> ' + show(inst(), 112, 1, 0, 0));
