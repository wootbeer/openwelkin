var g = Module.findBaseAddress('Game.dll');
function at(a) { return g.add(a - 0x10000000); }
var getc = new NativeFunction(at(0x10001adc), 'pointer', ['pointer', 'uint'], 'thiscall');
var c = getc(at(0x109c95e4).readPointer(), 0);
function wstr(p) { var size = p.add(0x14).readU32(), cap = p.add(0x18).readU32();
    var buf = cap >= 8 ? p.add(4).readPointer() : p.add(4); return buf.readUtf16String(size) + ' (len ' + size + ')'; }
send('char=' + c + ' +4=' + c.add(4).readU32() + ' name=' + wstr(c.add(8)) + ' deaths(+0xfc)=' + c.add(0xfc).readU32());
