function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
var g = Module.findBaseAddress('Game.dll');
var cnt = {};
[['?UpdateInput@Weapon@GameClient@@UAEXXZ'], ['?UpdateInput@Equip@GameClient@@UAEXXZ'], ['?CanUseWeapon@Robot@GameClient@@QAE_NXZ'],
 ['?UpdateInput@Robot@GameClient@@UAEXXZ']].forEach(function (e) {
    var p = Module.findExportByName('Game.dll', e[0]);
    if (!p) { send('no ' + e[0]); return; }
    p = rt(p);
    try { Interceptor.attach(p, { onEnter: function () { this.t = this.context.ecx; }, onLeave: function (r) {
        var k = e[0].split('@')[1] + '::' + e[0].split('@')[0].slice(1);
        cnt[k] = (cnt[k] || 0) + 1;
        if (cnt[k] <= 2) send(k + ' this=' + this.t + ' ret=' + r);
    }}); } catch (x) { send('cannot ' + e[0] + ' ' + x); }
});
setTimeout(function () { send(JSON.stringify(cnt)); }, 2500);
