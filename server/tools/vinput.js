// Virtual keyboard and mouse for testing: hooks IDirectInputDevice8::GetDeviceState (the game polls the keyboard
// as a 256-byte state and the mouse as a 16-byte DIMOUSESTATE) and adds keys, mouse moves and buttons sent from
// tools/vinput.py. While anything is injected the call also reports success, so an unfocused client can be driven.
var create = new NativeFunction(Module.getExportByName('DINPUT8.dll', 'DirectInput8Create'), 'int',
    ['pointer', 'uint', 'pointer', 'pointer', 'pointer']);
function guid(s) {
    var h = s.replace(/[{}-]/g, ''), b = Memory.alloc(16);
    b.writeU32(parseInt(h.substr(0, 8), 16)); b.add(4).writeU16(parseInt(h.substr(8, 4), 16));
    b.add(6).writeU16(parseInt(h.substr(12, 4), 16));
    for (var i = 0; i < 8; i++) b.add(8 + i).writeU8(parseInt(h.substr(16 + 2 * i, 2), 16));
    return b;
}
var pp = Memory.alloc(4);
create(Module.findBaseAddress('MarsWar.exe'), 0x0800, guid('{BF798030-483A-4DA2-AA99-5D64ED369700}'), pp, ptr(0));
var di = pp.readPointer();
var createDevice = new NativeFunction(di.readPointer().add(12).readPointer(), 'int', ['pointer', 'pointer', 'pointer', 'pointer'], 'stdcall');
createDevice(di, guid('{6F1D2B61-D5A0-11CF-BFC7-444553540000}'), pp, ptr(0));
var getState = pp.readPointer().readPointer().add(36).readPointer();

var keys = {};          // scancode -> true while held
var dx = 0, dy = 0;     // mouse movement still to deliver
var buttons = [0, 0, 0, 0];
var STEP = 12;          // max mouse units per poll (smooth turning)
function active() { return Object.keys(keys).length || dx || dy || buttons.some(function (b) { return b; }); }

Interceptor.attach(getState, {
    onEnter: function (a) { this.size = a[1].toInt32(); this.buf = a[2]; },
    onLeave: function (ret) {
        if (!active()) return;
        if (ret.toInt32() < 0) {  // not acquired (window in the background): fake an empty, successful read
            Memory.writeByteArray(this.buf, new Array(this.size).fill(0));
            ret.replace(0);
        }
        if (this.size === 256) {
            for (var k in keys) this.buf.add(parseInt(k)).writeU8(0x80);
        } else if (this.size === 16 || this.size === 20) {
            var mx = Math.max(-STEP, Math.min(STEP, dx)), my = Math.max(-STEP, Math.min(STEP, dy));
            dx -= mx; dy -= my;
            this.buf.writeS32(this.buf.readS32() + mx);
            this.buf.add(4).writeS32(this.buf.add(4).readS32() + my);
            for (var i = 0; i < 4; i++) if (buttons[i]) this.buf.add(12 + i).writeU8(0x80);
        }
    }
});

function handle(m) {
    if (m.type === 'key') { if (m.down) keys[m.code] = true; else delete keys[m.code]; }
    else if (m.type === 'move') { dx += m.dx; dy += m.dy; }
    else if (m.type === 'button') { buttons[m.index] = m.down ? 1 : 0; }
    else if (m.type === 'clear') { keys = {}; dx = dy = 0; buttons = [0, 0, 0, 0]; }
    recv(handle);
}
recv(handle);
send('ready');
