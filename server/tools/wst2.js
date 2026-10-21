function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var n = 0;
Interceptor.attach(p, { onEnter: function () {
    if (n++ % 60) return;
    var w = this.context.ecx, m = w.add(0x370).readU32();
    send('state=' + w.add(0x374).readU32() + ' ammo=' + w.add(0xe0).readFloat() + ' ec=' + w.add(0xec).readS32() +
         ' 37c=' + w.add(0x37c).readFloat() + ' 384=' + w.add(0x384 + m * 0x4c).readFloat() + ' 388=' + w.add(0x388 + m * 0x4c).readFloat() +
         ' 430=' + w.add(0x430).readU8() + ' 420=' + w.add(0x420).readFloat());
}});
