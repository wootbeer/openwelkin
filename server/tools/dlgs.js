var inst = new NativeFunction(Module.getExportByName('Engine.dll', '?Instance@CDlgMgr@@SAPAV1@XZ'), 'pointer', []);
var getd = new NativeFunction(Module.getExportByName('Engine.dll', '?GetDialog@CDlgMgr@@QAEPAVCTYDialog@@H@Z'), 'pointer', ['pointer', 'int'], 'thiscall');
var isvis = Module.findExportByName('Engine.dll', '?IsVisible@CREControl@@QAE_NXZ') || Module.findExportByName('Engine.dll', '?IsVisible@CTYDialog@@QAE_NXZ');
var vis = isvis ? new NativeFunction(isvis, 'uint8', ['pointer'], 'thiscall') : null;
function rtti(o) { try { var vt = o.readPointer(), col = vt.sub(4).readPointer(); return col.add(12).readPointer().add(8).readCString(); } catch (e) { return '?'; } }
var m = inst(), out = [];
for (var i = 0; i < 400; i++) { var d = getd(m, i); if (!d.isNull()) out.push(i + ':' + rtti(d).replace('.?AV', '').replace('@@', '') + (vis ? '/' + vis(d) : '')); }
send('isvis=' + isvis + ' ' + out.join(' '));
