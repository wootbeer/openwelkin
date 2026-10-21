var g = Module.findBaseAddress('Game.dll');
var t = g.add(0x109c9894 - 0x10000000).readPointer();
var b = t.add(8).readPointer(), e = t.add(12).readPointer();
var n = e.sub(b).toInt32() / 4, out = [];
for (var i = 0; i < n; i++) out.push(i.toString(16) + ':' + b.add(i * 4).readS32());
send(out.join(' '));
