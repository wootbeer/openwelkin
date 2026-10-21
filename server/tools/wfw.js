function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var p = rt(Module.findExportByName('Game.dll', '?UpdateInput@Weapon@GameClient@@UAEXXZ'));
var done = false;
var h = Interceptor.attach(p, { onEnter: function () {
    if (done) return; done = true;
    var w = this.context.ecx, fw = w.add(8).readPointer();
    var mode = w.add(0x370).readU32();
    var m = fw.add(mode * 0xb0);
    send('weapon=' + w + ' fw=' + fw + ' mode=' + mode + ' state=' + w.add(0x374).readU32() +
         ' e0=' + w.add(0xe0).readFloat() + ' fw160=' + fw.add(0x160).readS32() + ' fw171=' + fw.add(0x171).readU8() +
         ' cost9d8=' + m.add(0x9d8).readS32() + ' a0c=' + m.add(0xa0c).readFloat() + ' a10=' + m.add(0xa10).readFloat() +
         ' a58=' + m.add(0xa58).readU8() + ' 430=' + w.add(0x430).readU8() + ' 384=' + w.add(0x384 + mode * 0x4c).readFloat() +
         ' 25c=' + w.add(0x25c).readPointer());
    var s = [];
    for (var o = 0x9a0; o < 0xa60; o += 4) s.push(o.toString(16) + '=' + m.add(o).readS32() + '/' + m.add(o).readFloat().toFixed(2));
    send(s.join(' '));
    var t = [];
    for (var o = 0x150; o < 0x180; o += 4) t.push(o.toString(16) + '=' + fw.add(o).readS32());
    send(t.join(' '));
}});
