// Log Robot::Move / Steer / Jump calls on the local robot for a few seconds.
var g = Module.findBaseAddress('Game.dll');
function va(a) { return g.add(a - 0x10000000); }
var t0 = Date.now();
function ts() { return ((Date.now() - t0) / 1000).toFixed(2); }
Interceptor.attach(va(0x10241d00), { onEnter: function (a) {
    var r = this.context.ecx;
    send(ts() + ' Move flags=0x' + a[0].toUInt32().toString(16) + ' (was 0x' + r.add(0x354).readU32().toString(16) + ')'); } });
Interceptor.attach(va(0x1023cf80), { onEnter: function (a) { send(ts() + ' Steer ' + a[0].toInt32()); } });
Interceptor.attach(va(0x10241e70), { onEnter: function (a) { send(ts() + ' Jump'); } });
send('hooked');
