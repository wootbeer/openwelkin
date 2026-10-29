# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""FDP: the reliable-UDP session layer the client uses for the game server (Outpop::IONetwork Session_Holder).

12-byte header (Protocol accessors, HeadSize = 12):
    u8 flags | u16 number | u16 session id | u16 mtu length | u8 split id | u16 ack id | u16 order id
    flags: 0x80 reliable, 0x60 order type, 0x18 split type, 0x04 system packet, 0x03 ack type

Handshake (client Session::make_syn_mtu 0x1005af20, server Session_Holder::process_mtu 0x1005be20):
    SYN      flags 0x07, number = client sequence, session = client session id, split id 2,
             payload = u32 len | connect stream (len bytes)
    SYN-ACK  flags 0x07, number = server sequence, session = client session id, mtu length 12, split id 3,
             ack id = server session id, order id = 1 (2 = no session, 3 = session id mismatch), no payload
After that the session field of every packet is the *receiver's* session id.

Acks (Session::make_ack_mtu 0x1005ac10): system packet, split id 1, number = sender's next reliable number,
    ack id = next reliable number expected (cumulative: everything below it is acknowledged; repeating the
    number of the oldest unacked packet counts as a duplicate ack and triggers fast retransmit after 3),
    order id = session state field (client sends 2). Reliable and unreliable packets have separate counters.
    flags 0x04 | ack bits: cumulative ack = 0x05; 0x06 variant appends u16 count + u16 list (selective acks).
Data: payload is a sequence of (u16 length | Outpop::Message), plaintext. Client data seen so far:
    reliable 0xe1 (reliable, order type 3, ack type 1) and unreliable 0x01 pings (catalog 9, type 9001).
    order id increments per ordered packet (1, 2, ...); a repeated order id is silently dropped.
Close: the client sends a system packet with split id 4 (flags 0x07, no payload) when it leaves the battle,
    repeated every ~2 s until it gives up.

Sending: messages are queued per peer and sent once per game tick (flush), several to a packet. The client acks
lazily (its cumulative ack often lags 5+ seconds), so a lost reliable packet is resent when the client repeats the
same ack DUP_ACKS times, and the oldest unacked one again after RESEND_AFTER seconds (then 8, 16, 16, ...).
"""
import struct
import time

HEAD = struct.Struct("<BHHHBHH")
LENGTH = struct.Struct("<H")
SPLIT_ACK, SPLIT_SYN, SPLIT_SYN_ACK, SPLIT_FIN = 1, 2, 3, 4
MAX_PEERS = 512       # open sessions per game server; SYNs beyond this are ignored
PEER_TIMEOUT = 60.0   # seconds without any packet before a session is dropped
MAX_BATCH = 1024      # payload bytes per packet when queued messages are sent together (the client takes 953 B)
DUP_ACKS = 3          # the same ack this many times in a row resends the packet it asks for
RESEND_AFTER = 4.0    # seconds before the oldest unacked reliable packet is sent again
MAX_UNACKED = 4096    # reliable packets in flight before a session counts as dead


class Packet:
    def __init__(self, flags, number, session, mtu_len, split_id, ack_id, order_id, payload=b""):
        self.flags, self.number, self.session, self.mtu_len = flags, number, session, mtu_len
        self.split_id, self.ack_id, self.order_id, self.payload = split_id, ack_id, order_id, payload

    @classmethod
    def parse(cls, data):
        return cls(*HEAD.unpack_from(data), payload=data[HEAD.size:])

    def pack(self):
        return HEAD.pack(self.flags, self.number, self.session, self.mtu_len, self.split_id,
                         self.ack_id, self.order_id) + self.payload

    @property
    def system(self):
        return bool(self.flags & 0x04)

    def __repr__(self):
        return (f"FDP(flags={self.flags:#04x} num={self.number} sess={self.session} len={self.mtu_len} "
                f"split={self.split_id} ack={self.ack_id} order={self.order_id} payload={len(self.payload)}B)")


class Peer:
    """Server-side state for one client FDP session."""
    next_server_session = 1

    def __init__(self, addr, client_session):
        self.addr, self.client_session = addr, client_session
        self.server_session = Peer.next_server_session
        Peer.next_server_session += 1
        self.next_reliable = 1     # number of our next reliable packet (seeded by the SYN-ACK number)
        self.next_unreliable = 1   # unreliable packets have their own counter
        self.next_order = 1        # order id of our next ordered (reliable) packet; repeats are dropped as duplicates
        self.expected = 0          # next reliable number we expect from the client (cumulative ack value)
        self.connect_stream = b""
        self.out = []              # queued (raw message, reliable) waiting for the next flush
        self.unacked = {}          # our reliable packet number -> [packed packet, last send time, sends]
        self.last_ack, self.dup_acks = None, 0
        self.closed = False


class Server:
    def __init__(self, sock, log, hexdump, debug=None, trace=True):
        self.sock, self.log, self.hexdump = sock, log, hexdump
        self.debug = debug or log  # packet traces
        self.trace = trace         # False: skip building trace text at all (hex dumps of every packet are costly)
        self.peers = {}
        self.on_message = None  # callback(server, peer, raw message) for game-server messages
        self.on_close = None    # callback(server, peer, reason) when a session ends
        self.allow = None       # callback(addr) -> False to ignore a new session (banned address)
        self.stats = dict.fromkeys(("pkts_in", "bytes_in", "pkts_out", "bytes_out", "resent"), 0)
        self.next_expiry = 0.0

    def handle(self, data, addr):
        try:
            pkt = Packet.parse(data)
        except struct.error:
            self.debug(f"[fdp] {addr} short packet\n{self.hexdump(data)}")
            return
        self.stats["pkts_in"] += 1
        self.stats["bytes_in"] += len(data)
        peer = self.peers.get(addr)
        if peer:
            peer.last_seen = time.time()
        if self.trace:
            quiet = peer and not pkt.system and messages(pkt.payload) and all(
                (m[1], m[2]) == (9, 9001) for m in messages(pkt.payload))
            bare_ack = pkt.system and pkt.split_id == SPLIT_ACK and not pkt.payload
            if bare_ack and peer:
                if pkt.ack_id != getattr(peer, "last_ack_seen", None):
                    self.debug(f"[fdp] {addr} <- ack {pkt}")
                    peer.last_ack_seen = pkt.ack_id
            elif not quiet:
                self.debug(f"[fdp] {addr} <- {pkt}\n{self.hexdump(pkt.payload)}")
        if pkt.system and pkt.split_id == SPLIT_SYN:
            self.on_syn(pkt, addr)
            return
        if peer and pkt.system and pkt.split_id == SPLIT_ACK:
            self.on_ack(peer, pkt.ack_id)  # bare or with a selective-ack list (flags 0x06): ack id is cumulative
        if peer and pkt.system and pkt.split_id == SPLIT_FIN:
            self.close(peer, "left")
            return
        if not peer or pkt.system:
            return
        if pkt.flags & 0x80:  # reliable: cumulative ack = next number we expect
            if pkt.number == peer.expected or not peer.expected:
                peer.expected = (pkt.number + 1) & 0xFFFF
            self.send(Packet(0x05, peer.next_reliable, peer.client_session, HEAD.size, 1, peer.expected, 2),
                      addr, quiet=True)
        for raw, catalog, type_ in messages(pkt.payload):
            if (catalog, type_) == (9, 9001):  # ping: echo it back unreliably
                self.send_messages(peer, [raw], reliable=False, quiet=True)
            elif self.on_message:
                self.on_message(self, peer, raw)

    def on_syn(self, pkt, addr):
        peer = self.peers.get(addr)
        if not peer or peer.client_session != pkt.session:
            if self.allow and not self.allow(addr):
                self.debug(f"[fdp] {addr} SYN ignored: address not allowed")
                return
            if not peer and len(self.peers) >= MAX_PEERS and not self.expire_idle():
                self.log(f"[fdp] {addr} SYN ignored: {len(self.peers)} sessions open")
                return
            if peer:  # same address, new session: the client restarted its connection
                self.close(peer, "reconnected")
            peer = self.peers[addr] = Peer(addr, pkt.session)
            peer.last_seen = time.time()
            (n,) = struct.unpack_from("<I", pkt.payload) if len(pkt.payload) >= 4 else (0,)
            peer.connect_stream = pkt.payload[4:4 + n]
            self.log(f"[fdp] {addr} new session client={pkt.session} server={peer.server_session} "
                     f"connect_stream={peer.connect_stream.hex()}")
        reply = Packet(0x07, peer.next_reliable, pkt.session, HEAD.size, SPLIT_SYN_ACK, peer.server_session, 1)
        self.send(reply, addr)

    def close(self, peer, reason):
        """End a session (left, timed out, kicked, ...). Safe to call twice."""
        if peer.closed:
            return
        peer.closed = True
        if self.peers.get(peer.addr) is peer:
            del self.peers[peer.addr]
        if self.on_close:
            self.on_close(self, peer, reason)

    def expire_idle(self):
        """Drop sessions that sent nothing for PEER_TIMEOUT seconds. True if any were dropped."""
        cutoff = time.time() - PEER_TIMEOUT
        stale = [p for p in self.peers.values() if getattr(p, "last_seen", 0) < cutoff]
        for p in stale:
            self.close(p, "timed out")
        if stale:
            self.log(f"[fdp] dropped {len(stale)} idle sessions")
        return bool(stale)

    def on_ack(self, peer, ack):
        """Cumulative ack: every reliable packet numbered before ack has arrived."""
        for n in [n for n in peer.unacked if 0 < (ack - n) & 0xFFFF < 0x8000]:
            del peer.unacked[n]
        if ack == peer.last_ack and ack in peer.unacked:
            peer.dup_acks += 1
            if peer.dup_acks == DUP_ACKS:  # the client keeps asking for this one: it was lost
                self.resend(peer, ack)
        else:
            peer.last_ack, peer.dup_acks = ack, 0

    def resend(self, peer, number):
        entry = peer.unacked[number]
        entry[1], entry[2] = time.time(), entry[2] + 1
        self.stats["resent"] += 1
        self.sock.sendto(entry[0], peer.addr)

    def queue(self, peer, raw, reliable=True):
        """Send a message with the next flush (framed now: u16 length | message)."""
        peer.out.append((LENGTH.pack(len(raw)) + raw, reliable))

    def flush(self, now=None):
        """Send every peer's queued messages, packed into as few packets as fit, then resend what looks lost."""
        now = now or time.time()
        for peer in list(self.peers.values()):
            if peer.out:
                for reliable in (True, False):
                    batch, size = [], 0
                    for framed, rel in peer.out:
                        if rel != reliable:
                            continue
                        if batch and size + len(framed) > MAX_BATCH:
                            self.send_payload(peer, b"".join(batch), reliable)
                            batch, size = [], 0
                        batch.append(framed)
                        size += len(framed)
                    if batch:
                        self.send_payload(peer, b"".join(batch), reliable)
                peer.out.clear()
            if peer.unacked:
                number, (_, sent, sends) = next(iter(peer.unacked.items()))  # oldest first
                if now - sent > RESEND_AFTER * 2 ** min(sends - 1, 2):
                    self.resend(peer, number)
                if len(peer.unacked) > MAX_UNACKED:
                    self.close(peer, "not acking")
        if now >= self.next_expiry:
            self.next_expiry = now + 1.0
            self.expire_idle()

    def send_messages(self, peer, raw_messages, reliable=True, quiet=False):
        """Send Outpop::Message blobs to a peer now, framed as (u16 length | message)."""
        self.send_payload(peer, b"".join(LENGTH.pack(len(m)) + m for m in raw_messages), reliable, quiet)

    def send_payload(self, peer, payload, reliable=True, quiet=None):
        """One data packet; unreliable ones stay out of the trace unless quiet=False."""
        quiet = not reliable if quiet is None else quiet
        order = 0
        if reliable:
            number, peer.next_reliable = peer.next_reliable, (peer.next_reliable + 1) & 0xFFFF
            order, peer.next_order = peer.next_order, (peer.next_order + 1) & 0xFFFF
        else:
            number, peer.next_unreliable = peer.next_unreliable, (peer.next_unreliable + 1) & 0xFFFF
        flags = 0xE1 if reliable else 0x01
        pkt = Packet(flags, number, peer.client_session, HEAD.size + len(payload), 1 if reliable else 0,
                     peer.expected, order, payload)
        data = self.send(pkt, peer.addr, quiet=quiet)
        if reliable:
            peer.unacked[number] = [data, time.time(), 1]

    def send(self, pkt, addr, quiet=False):
        if self.trace and not quiet:
            self.debug(f"[fdp] {addr} -> {pkt}\n{self.hexdump(pkt.payload)}")
        data = pkt.pack()
        self.stats["pkts_out"] += 1
        self.stats["bytes_out"] += len(data)
        self.sock.sendto(data, addr)
        return data


def messages(payload):
    """Split an FDP data payload into (raw message, catalog, type) tuples."""
    out, off = [], 0
    while off + 2 <= len(payload):
        (n,) = struct.unpack_from("<H", payload, off)
        raw = payload[off + 2: off + 2 + n]
        off += 2 + n
        catalog = raw[1] if len(raw) > 1 else None
        type_ = struct.unpack_from("<H", raw, 2)[0] if len(raw) >= 4 and raw[0] & 0x80 else None
        out.append((raw, catalog, type_))
    return out
