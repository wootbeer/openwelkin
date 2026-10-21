function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var done = false;
function rtti(o) { try { var vt = o.readPointer(), col = vt.sub(4).readPointer(); return col.add(12).readPointer().add(8).readCString(); } catch (e) { return '?'; } }
Interceptor.attach(p, { onEnter: function () {
    if (done) return; done = true;
    var r = this.context.ecx.add(0xb0).readPointer(), e = r.add(0xe4).readPointer();
    send('robot=' + r + ' e4=' + e + ' ' + (e.isNull() ? '' : rtti(e)));
    if (!e.isNull()) { var s = []; for (var o = 0x10; o < 0x60; o += 8) s.push(o.toString(16) + '=' + e.add(o).readDouble().toFixed(2)); send(s.join(' ')); }
    var parts = []; for (var o = 0xd0; o < 0xf0; o += 4) { var q = r.add(o).readPointer(); parts.push(o.toString(16) + ':' + (q.isNull() ? 0 : rtti(q))); } send(parts.join(' '));
}});
