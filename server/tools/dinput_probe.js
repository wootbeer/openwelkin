// Which DirectInput device calls does the game make? Creates its own DirectInput8 keyboard device to find the
// shared IDirectInputDevice8 vtable, then logs GetDeviceState / GetDeviceData calls (size, count) for a moment.
var create = new NativeFunction(Module.getExportByName('DINPUT8.dll', 'DirectInput8Create'), 'int',
    ['pointer', 'uint', 'pointer', 'pointer', 'pointer']);
function guid(s) {  // "{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}" -> 16 bytes
    var h = s.replace(/[{}-]/g, ''), b = Memory.alloc(16);
    b.writeU32(parseInt(h.substr(0, 8), 16)); b.add(4).writeU16(parseInt(h.substr(8, 4), 16));
    b.add(6).writeU16(parseInt(h.substr(12, 4), 16));
    for (var i = 0; i < 8; i++) b.add(8 + i).writeU8(parseInt(h.substr(16 + 2 * i, 2), 16));
    return b;
}
var IID_IDirectInput8A = guid('{BF798030-483A-4DA2-AA99-5D64ED369700}');
var GUID_SysKeyboard = guid('{6F1D2B61-D5A0-11CF-BFC7-444553540000}');
var hinst = Module.findBaseAddress('MarsWar.exe');
var ppDI = Memory.alloc(4);
var hr = create(hinst, 0x0800, IID_IDirectInput8A, ppDI, ptr(0));
var di = ppDI.readPointer();
var diVt = di.readPointer();
var createDevice = new NativeFunction(diVt.add(3 * 4).readPointer(), 'int', ['pointer', 'pointer', 'pointer', 'pointer'], 'stdcall');
var ppDev = Memory.alloc(4);
var hr2 = createDevice(di, GUID_SysKeyboard, ppDev, ptr(0));
var vt = ppDev.readPointer().readPointer();
var getState = vt.add(9 * 4).readPointer(), getData = vt.add(10 * 4).readPointer();
send('create ' + hr + '/' + hr2 + ' GetDeviceState ' + getState + ' GetDeviceData ' + getData);
var seen = {};
Interceptor.attach(getState, { onEnter: function (a) { var k = 'state ' + a[1].toInt32(); seen[k] = (seen[k] || 0) + 1; } });
Interceptor.attach(getData, { onEnter: function (a) { var k = 'data cb=' + a[1].toInt32(); seen[k] = (seen[k] || 0) + 1; } });
setTimeout(function () { send(JSON.stringify(seen)); }, 2000);
