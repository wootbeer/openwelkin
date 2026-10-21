var g = Module.findBaseAddress('Game.dll');
Interceptor.attach(g.add(0x6c46dd), { onEnter: function () { send('cat19 local_id ' + this.context.eax.toInt32()); } });
Interceptor.attach(g.add(0x6c46f0), { onEnter: function () { send('cat19 owner ' + this.context.ebp.toInt32()); } });
Interceptor.attach(g.add(0x6c45a4), { onEnter: function () { send('owner(fallthrough) ' + this.context.ebp.toInt32()); } });
