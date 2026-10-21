var g = Module.findBaseAddress('Game.dll');
function at(a) { return g.add(a - 0x10000000); }
var cnt = {};
[[0x106099a0, 'Tick'], [0x10604720, 'OnRender'], [0x10613cd0, 'R4Tick'], [0x1060ddb0, 'R4Render'], [0x1060f750, 'R4Init'], [0x10609600, 'ctor'], [0x1060fa90, 'R4ctor'], [0x10604960, 'RefreshData'], [0x10604ae0, 'RefreshArmor'], [0x10604e80, 'RefreshEnergy']].forEach(function (e) {
    Interceptor.attach(at(e[0]), { onEnter: function () { cnt[e[1]] = (cnt[e[1]] || 0) + 1; if (cnt[e[1]] == 1) this.first = 1; } });
});
setTimeout(function () { send(JSON.stringify(cnt)); }, 1500);
