function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var last = null, t0 = Date.now();
Interceptor.attach(p, { onEnter: function () {
    var w = this.context.ecx, a = w.add(0xe0).readFloat(), s = w.add(0x374).readU32();
    var k = a.toFixed(2) + '/' + s;
    if (k !== last) { last = k; send((Date.now() - t0) + 'ms ammo=' + a.toFixed(2) + ' state=' + s + ' max=' + w.add(0x364).readFloat()); }
}});
var d = rt(Module.findExportByName('Game.dll', '?GetEnergy@Robot@GameClient@@QBENXZ'));
