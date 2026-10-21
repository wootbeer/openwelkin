function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var done = false;
Interceptor.attach(p, { onEnter: function () {
    if (done) return; done = true;
    var r = this.context.ecx.add(0xb0).readPointer(), e = r.add(0xe4).readPointer();
    var s = [];
    for (var o = 0; o < 0xd0; o += 4) { var f = e.add(o).readFloat(), d = (o % 8 == 0) ? e.add(o).readDouble() : null;
        s.push(o.toString(16) + ':' + e.add(o).readU32().toString(16) + (d && Math.abs(d) > 1e-6 && Math.abs(d) < 1e7 ? '(d' + d + ')' : '')); }
    send(s.join(' '));
}});
