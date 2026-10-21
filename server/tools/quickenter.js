// Press the hall's Quick Enter button from code (for a second client that ignores synthetic mouse clicks).
// BFID: battlefield to enter (1 Training Ground 1, 2 Mars Front); 0 keeps the hall's selection.
var BFID = 0;
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
function rtti(o) { try { var col = o.readPointer().sub(4).readPointer(); return col.add(12).readPointer().add(8).readCString(); } catch (e) { return '?'; } }
var inst = new NativeFunction(Module.getExportByName('Engine.dll', '?Instance@CDlgMgr@@SAPAV1@XZ'), 'pointer', []);
var get = new NativeFunction(Module.getExportByName('Engine.dll', '?GetDialog@CDlgMgr@@QAEPAVCTYDialog@@H@Z'), 'pointer', ['pointer', 'int'], 'thiscall');
var click = new NativeFunction(rt(Module.findExportByName('Game.dll', '?ClickBtnQuickEnter@CNewHallUI@@AAEXXZ')), 'void', ['pointer'], 'thiscall');
var m = inst(), hall = null;
for (var id = 0; id < 400 && !hall; id++) {
    var d = get(m, id);
    if (!d.isNull() && rtti(d).indexOf('CNewHallUI') >= 0) { hall = d; send('hall dialog id ' + id); }
}
var bfm = new NativeFunction(rt(Module.findExportByName('Game.dll', '?Instance@BattleFieldManager@GameClient@@SAPAV12@XZ')), 'pointer', []);
var setBf = new NativeFunction(rt(Module.findExportByName('Game.dll', '?SetQuickEnterBFID@BattleFieldManager@GameClient@@QAEXJ@Z')), 'void', ['pointer', 'int'], 'thiscall');
if (BFID) { setBf(bfm(), BFID); send('quick enter battlefield ' + BFID); }
if (hall) { click(hall); send('clicked Quick Enter'); } else send('hall not found');
