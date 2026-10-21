// Log every C++ throw with a Game.dll backtrace (finds which reply parser raised a DataError).
var thr = Module.getExportByName('MSVCR80.dll', '_CxxThrowException');
function rel(p) {
    var m = Process.findModuleByAddress(p);
    if (!m) return String(p);
    var s = m.name + '+' + p.sub(m.base);
    if (m.name.toLowerCase() === 'game.dll') s += ' (' + ptr(0x10000000).add(p.sub(m.base)) + ')';
    return s;
}
Interceptor.attach(thr, { onEnter: function (args) {
    send('THROW\n' + Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 16).map(rel).join('\n'));
} });
send('hooked');
