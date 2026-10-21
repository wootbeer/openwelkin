function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var done = false;
Interceptor.attach(p, { onEnter: function () {
    if (done) return; done = true;
    var w = this.context.ecx, r = w.add(0xb0).readPointer();
    send('weapon=' + w + ' state=' + w.add(0x374).readU32() + ' e0=' + w.add(0xe0).readFloat() + ' robot=' + r +
         (r.isNull() ? '' : ' r308=' + r.add(0x308).readU8() + ' r17c=' + r.add(0x17c).readU32() + ' r184=0x' + r.add(0x184).readU32().toString(16) +
          ' r19c=0x' + r.add(0x19c).readU32().toString(16) + ' r10c=' + r.add(0x10c).readS32()));
}});
