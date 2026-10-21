function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
['?Use@Robot@GameClient@@QAEXXZ', '?Use@Weapon@GameClient@@UAE_NXZ', '?CanBeUsed@Weapon@GameClient@@UAE_NXZ',
 '?CanUseWeapon@Robot@GameClient@@QAE_NXZ', '?StopUse@Weapon@GameClient@@UAE_NXZ', '?Stop@Weapon@GameClient@@UAEXXZ'].forEach(function (n) {
    var p = rt(Module.findExportByName('Game.dll', n)), s = n.split('@')[1] + '::' + n.split('@')[0].slice(1);
    Interceptor.attach(p, { onEnter: function () { this.ra = this.returnAddress; },
        onLeave: function (r) { send(s + ' -> ' + (r.toInt32() & 0xff) + ' from 0x' + this.ra.sub(Module.findBaseAddress('Game.dll')).add(0x10000000).toString(16)); } });
});
