// Hook CMarketUI::SelectSort and the goods-list size; log the tab value and how many goods the client holds.
var g = Module.findBaseAddress('Game.dll');
function va(a) { return g.add(a - 0x10000000); }
var cnt = new NativeFunction(va(0x10026d4b), 'uint', ['pointer'], 'thiscall');
var lst = new NativeFunction(va(0x1001a424), 'pointer', ['pointer'], 'thiscall');
Interceptor.attach(va(0x10550460), {
    onEnter: function (args) {
        var mgr = va(0x109c95e4).readPointer();
        var v = lst(mgr);
        var n = cnt(v);
        var s = 'SelectSort tab=' + args[0].toInt32() + ' goods=' + n;
        var first = v.add(4).readPointer();
        for (var i = 0; i < n && i < 6; i++) {
            var r = first.add(i * 0x80);
            s += ' | id=' + r.readU32() + ' fw=' + r.add(0x68).readU32() + ' cat=' + r.add(0x7c).readU8();
        }
        send(s);
    }
});
send('hooked');
function rt(p) { return (p.readU8() === 0xe9) ? p.add(5).add(p.add(1).readS32()) : p; }
Interceptor.attach(rt(va(0x1001464b)), { onEnter: function (args) {
    send('AddItem id=' + args[0].toInt32() + ' a=' + args[1].add(0).readPointer() + ' flag=' + args[3].toInt32()); } });
var tabs = [];
Process.setExceptionHandler(function (d) {
    var g0 = Module.findBaseAddress('Game.dll');
    function rel(p) { var m = Process.findModuleByAddress(p); return m ? m.name + '+' + p.sub(m.base) + (m.name === 'Game.dll' ? ' (' + ptr(0x10000000).add(p.sub(m.base)) + ')' : '') : p; }
    var s = 'EXC ' + d.type + ' at ' + rel(d.address) + ' mem=' + (d.memory ? d.memory.operation + ' ' + d.memory.address : '');
    s += ' regs eax=' + d.context.eax + ' ecx=' + d.context.ecx + ' edx=' + d.context.edx + ' esi=' + d.context.esi + ' edi=' + d.context.edi;
    var sp = d.context.esp;
    for (var i = 0; i < 40; i++) { var v = sp.add(i * 4).readPointer(); var m = Process.findModuleByAddress(v); if (m && m.name === 'Game.dll') s += '\n  stack ' + rel(v); }
    send(s);
    return false;
});
