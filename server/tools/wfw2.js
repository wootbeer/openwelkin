function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var done = false;
Interceptor.attach(p, { onEnter: function () {
    if (done) return; done = true;
    var w = this.context.ecx, fw = w.add(8).readPointer();
    for (var m = 0; m < 2; m++) { var b = fw.add(m * 0xb0);
        send('mode' + m + ': 9d4=' + b.add(0x9d4).readFloat() + ' a34=' + b.add(0xa34).readFloat() + ' a38=' + b.add(0xa38).readFloat() + ' cost=' + b.add(0x9d8).readS32()); }
    send('fw b14=' + fw.add(0xb14).readU8() + ' w364=' + w.add(0x364).readFloat() + ' w368=' + w.add(0x368).readFloat() + ' wb4=' + w.add(0xb4).readFloat() + ' wb8=' + w.add(0xb8).readFloat());
}});
