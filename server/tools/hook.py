"""hook.py [seconds] -- attach Frida to the running MarsWar client and trace message/event dispatch.

Addresses are static (preferred-base) addresses from the DLLs; they are rebased to where each module loaded.
Output goes to stdout and tools/hook.log.
"""
import os, sys, time
import frida

JS = r"""
function resolveThunk(p) {
    // incremental-link thunk: E9 rel32 -> follow it (Frida cannot hook 5-byte thunks packed back to back)
    if (p && p.readU8() === 0xe9) return p.add(5).add(p.add(1).readS32());
    return p;
}
function exp(mod, name) { return resolveThunk(Module.findExportByName(mod, name)); }
function addr(mod, a) {
    var m = Process.getModuleByName(mod);
    var pref = (mod === 'Game.dll' || mod === 'Engine.dll' || mod === 'MessageService.dll') ? 0x10000000 : 0x10000000;
    return m.base.add(a - pref);
}
function msgInfo(msgObj) {
    var hdr = msgObj.add(0x64).readPointer();
    var layout = msgObj.add(0x60).readU16();
    var flags = hdr.readU8(), catalog = hdr.add(1).readU8();
    var typeOff = layout >> 12;
    var type = typeOff ? hdr.add(typeOff).readU16() : -1;
    return 'flags=0x' + flags.toString(16) + ' catalog=' + catalog + ' type=' + type;
}
var hooks = {
    'Game.dll': {
        0x106c43d0: ['GameDefaultMessageHandler::handle_message', function (args) {
            var msg = args[0].readPointer();   // Smart_Ptr<Message>& -> Message*
            send('default handler: ' + msgInfo(msg));
        }],
        0x106c23f0: ['SynchLayer ready check', null, function (ret) { send('  ready check -> ' + ret); }],
        0x103faf30: ['GS_Battle_Initialize_Observer::OnBegin', function (args) { send('BATTLE INIT observer called'); }],
        0x10418b70: ['GS_InitBattle_MU_Observer::OnBegin', function (args) { send('MU BATTLE INIT observer called'); }],
        0x10418c63: ['MU battle init: LogicWorld init returned', function (args) {
            send('  LogicWorld init returned al=' + (this.context.eax.toInt32() & 0xff)); }],
        0x10418bc2: ['MU battle init: user count check', function (args) {
            send('  user counter=' + this.context.eax); }],
    },
};
for (var mod in hooks) {
    for (var a in hooks[mod]) {
        (function (name, onEnter, onLeave) {
            var p = addr(mod, parseInt(a));
            Interceptor.attach(p, {
                onEnter: function (args) { if (onEnter) onEnter.call(this, args); },
                onLeave: function (ret) { if (onLeave) onLeave.call(this, ret); }
            });
            send('hooked ' + name + ' @ ' + p);
        })(hooks[mod][a][0], hooks[mod][a][1], hooks[mod][a][2]);
    }
}
var addEvent = Module.getExportByName('Engine.dll', '?AddEvent@EventAttemper@@QAEPAVEvent@@EGMW4EVENT_ACTION_TYPE@@@Z');
Interceptor.attach(addEvent, { onEnter: function (args) {
    send('AddEvent catalog=' + (args[0].toInt32() & 0xff) + ' type=' + (args[1].toInt32() & 0xffff) + ' action=' + args[3].toInt32());
}});
send('hooked AddEvent @ ' + addEvent);
function rttiName(obj) {
    try {
        var vt = obj.readPointer(), col = vt.sub(4).readPointer(), td = col.add(12).readPointer();
        return td.add(8).readCString();
    } catch (e) { return '?'; }
}
var addObs = Module.getExportByName('Engine.dll', '?AddObserver@EventAttemper@@QAEHEGPAVEventObserver@@@Z');
Interceptor.attach(addObs, {
    onEnter: function (args) {
        this.desc = (args[0].toInt32() & 0xff) + ',' + (args[1].toInt32() & 0xffff) + ' ' + rttiName(args[2]);
    },
    onLeave: function (ret) {
        if (this.desc.indexOf('19,') === 0 || this.desc.indexOf('9,4') === 0)
            send('AddObserver ' + this.desc + ' -> id ' + ret.toInt32());
    }
});
var spawnHooks = {
    0x1035f9e0: 'GS_Character_New_Character', 0x1022cc10: 'GS_Object_Attach_Object',
    0x10353650: 'GS_Character_SetControl', 0x1024dc90: 'Robot::WriteSystemPartSnapshot',
    0x10457800: 'FlyweightManager::NewWorldObject',
};
for (var a in spawnHooks) {
    (function (name, p) {
        Interceptor.attach(p, { onEnter: function (args) { send('ENTER ' + name); },
                                onLeave: function (ret) { send('LEAVE ' + name + ' -> ' + ret); } });
    })(spawnHooks[a], addr('Game.dll', parseInt(a)));
}
var btOnce = {};
function bt(ctx) {
    return Thread.backtrace(ctx, Backtracer.ACCURATE).slice(0, 8).map(function (a) {
        var m = Process.findModuleByAddress(a); return '  ' + a + ' ' + (m ? m.name + '+' + a.sub(m.base) : ''); }).join('\n');
}
[['?LoadHUD@Weapon@GameClient@@UAEXXZ', 'Weapon::LoadHUD'], ['?UpdateHUD@Weapon@GameClient@@UAEXXZ', 'Weapon::UpdateHUD'],
 ['?SetCurEquip@Robot@GameClient@@QAEXHH@Z', 'Robot::SetCurEquip'], ['?LoadHUD@Equip@GameClient@@UAEXXZ', 'Equip::LoadHUD'], ['?LoadResourceFPV@Equip@GameClient@@UAE_NXZ', 'Equip::LoadResourceFPV'], ['?SetLocal@WorldObject@GameClient@@QAEX_N@Z', 'WorldObject::SetLocal']]
.forEach(function (e) {
    var p = exp('Game.dll', e[0]);
    if (!p) { send('no export ' + e[0]); return; }
    try { Interceptor.attach(p, { onEnter: function (args) {
        if (!btOnce[e[1]]) { btOnce[e[1]] = 1; send('FIRST ' + e[1] + ' this=' + this.context.ecx + ' args=' + args[0] + ',' + args[1] + '\n' + bt(this.context)); }
    }}); } catch (err) { send('cannot hook ' + e[1] + ': ' + err); }
});
var useCount = {};
['?Use@Robot@GameClient@@QAEXXZ', '?Use@Weapon@GameClient@@UAE_NXZ', '?Use@Equip@GameClient@@UAE_NXZ',
 '?OnUse@Weapon@GameClient@@UAE_NABVVector@@0@Z', '?Shoot@Weapon@GameClient@@QAEXHABVVector@@0H@Z',
 '?OnUse@Equip@GameClient@@UAE_NABVVector@@0@Z', '?UpdateInput@Weapon@GameClient@@UAEXXZ',
 '?CanUseWeapon@Robot@GameClient@@QAE_NXZ', '?UpdateInput@Robot@GameClient@@UAEXXZ'].forEach(function (n) {
    var p = exp('Game.dll', n);
    if (!p) { send('no export ' + n); return; }
    var short = n.split('@')[0].slice(1) + '@' + n.split('@')[1];
    try {
        Interceptor.attach(p, {
            onEnter: function (a) { this.go = (useCount[short] = (useCount[short] || 0) + 1) <= 5; },
            onLeave: function (r) { if (this.go) send('USE ' + short + ' -> ' + r); }
        });
    } catch (e) { send('cannot hook ' + short + ': ' + e); }
});
['?SetMask@Input@@QAEXHW4InputMaskState@@@Z', '?SetMaskMouse@Input@@QAEXW4InputMaskState@@@Z',
 '?SetMaskAll@Input@@QAEXW4InputMaskState@@@Z'].forEach(function (n) {
    var p = exp('Engine.dll', n);
    try { Interceptor.attach(p, { onEnter: function (a) {
        send('MASK ' + n.split('@')[0].slice(1) + ' ' + a[0].toInt32() + ',' + a[1].toInt32() + '\n' + bt(this.context).split('\n').slice(0, 3).join('\n'));
    }}); } catch (e) { send('cannot hook ' + n + ': ' + e); }
});
var ctlSeen = {};
['?ControlDown@Input@@QAE_NHH@Z', '?ControlJustPressed@Input@@QAE_NHH@Z'].forEach(function (n) {
    var p = exp('Engine.dll', n);
    try { Interceptor.attach(p, {
        onEnter: function (a) { this.h = a[0].toInt32(); this.ra = this.returnAddress; },
        onLeave: function (r) {
            var k = n.slice(1, 12) + ' h=' + this.h + ' r=' + (r.toInt32() & 0xff);
            if (!ctlSeen[k]) { ctlSeen[k] = 1; var m = Process.findModuleByAddress(this.ra);
                send('CTL ' + k + ' from ' + (m ? m.name + '+' + this.ra.sub(m.base) : this.ra)); }
        }}); } catch (e) { send('cannot hook ' + n + ': ' + e); }
});
var steerSeen = {};
var steer = exp('Game.dll', '?Steer@Robot@GameClient@@UAEXW4ROBOT_STEER_FLAG@2@@Z');
Interceptor.attach(steer, { onEnter: function (a) {
    var f = a[0].toInt32();
    if (!steerSeen[f]) { steerSeen[f] = 1; send('STEER flag 0x' + (f >>> 0).toString(16)); }
}});
var fpw = exp('Game.dll', '?WriteSnapshot@Func_Power@GameClient@@UAE_NAAVBinStream@@@Z');
Interceptor.attach(fpw, { onEnter: function () { this.t = this.context.ecx; },
    onLeave: function () { send('Func_Power snapshot read: energy=' + this.t.add(0x20).readDouble() + ' hyper=' +
        this.t.add(0x28).readDouble() + ' max=' + this.t.add(0x30).readDouble()); } });
var bfp = exp('Game.dll', '?BindFuncPower@Robot@GameClient@@QAEXKK@Z');
Interceptor.attach(bfp, { onEnter: function (a) { send('BindFuncPower fw=' + a[0].toInt32() + ' id=' + a[1].toInt32()); } });
['?ShowDialog@CDlgMgr@@QAE_NH_N0M@Z', '?HideDialog@CDlgMgr@@QAE_NH@Z', '?HideAllUI@CDlgMgr@@QAEXXZ'].forEach(function (n) {
    var p = Module.findExportByName('Engine.dll', n);
    if (!p) { send('no export ' + n); return; }
    Interceptor.attach(p, { onEnter: function (a) {
        var m = Process.findModuleByAddress(this.returnAddress);
        send('DLG ' + n.split('@')[0].slice(1) + ' id=' + a[0].toInt32() + ' show=' + (a[1].toInt32() & 0xff) + ' from ' +
             (m ? m.name + '+' + this.returnAddress.sub(m.base) : this.returnAddress));
    }});
});
Interceptor.attach(addr('Game.dll', 0x10613c00), { onEnter: function () { send('SR4_SystemPartChangeObserver::OnBegin'); } });
var badParam = Module.getExportByName('MSVCR80.dll', '_invalid_parameter_noinfo');
Interceptor.attach(badParam, { onEnter: function (args) {
    send('INVALID PARAMETER, backtrace:\n' + Thread.backtrace(this.context, Backtracer.ACCURATE)
        .map(function (a) { var m = Process.findModuleByAddress(a);
                            return '  ' + a + ' ' + (m ? m.name + '+' + a.sub(m.base) : ''); }).join('\n'));
}});
Process.setExceptionHandler(function (d) {
    send('EXCEPTION ' + d.type + ' at ' + d.address + ' ' + JSON.stringify(d.memory || {}));
    return false;
});
var remObs = Module.getExportByName('Engine.dll', '?RemoveObserver@EventAttemper@@QAE_NH@Z');
Interceptor.attach(remObs, { onEnter: function (args) { this.id = args[0].toInt32(); } });
"""

log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.log"), "a", buffering=1)


def on_message(message, data):
    line = f"{time.strftime('%H:%M:%S')} {message.get('payload', message)}"
    print(line, flush=True)
    log.write(line + "\n")


session = frida.attach("MarsWar.exe")
script = session.create_script(JS)
script.on("message", on_message)
script.load()
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 3600)
