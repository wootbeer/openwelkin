// Find who shows GAMEENGINE_DATAUNNORMAL ("Waiting to enter arena." in ENG.xml = bad reply data).
var gt = Module.getExportByName('Engine.dll', '?GetText@Precacher@@SA?AV?$basic_string@_WU?$char_traits@_W@std@@V?$allocator@_W@2@@std@@ABV23@@Z');
function rel(p) { var m = Process.findModuleByAddress(p); return m ? m.name + '+' + p.sub(m.base) + (m.name === 'Game.dll' ? ' (' + ptr(0x10000000).add(p.sub(m.base)) + ')' : '') : String(p); }
Interceptor.attach(gt, { onEnter: function (args) {
    // arg1 = const std::wstring& (MSVC 2005: 4-byte alloc ptr, then 16-byte buffer/pointer, size at +0x14, cap at +0x18)
    var s = args[1];
    var cap = s.add(0x18).readU32();
    var str = (cap < 8 ? s.add(4) : s.add(4).readPointer()).readUtf16String();
    if (str && str.indexOf('DATAUNNORMAL') >= 0) {
        send('GetText ' + str + '\n' + Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 14).map(rel).join('\n'));
    }
} });
send('hooked');
