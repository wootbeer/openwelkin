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
"""
import struct
import time

HEAD = struct.Struct("<BHHHBHH")
SPLIT_SYN, SPLIT_SYN_ACK = 2, 3
MAX_PEERS = 512       # open sessions per game server; SYNs beyond this are ignored
PEER_TIMEOUT = 60.0   # seconds without any packet before a session can be dropped to make room


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


class Server:
    def __init__(self, sock, log, hexdump, debug=None):
        self.sock, self.log, self.hexdump = sock, log, hexdump
        self.debug = debug or log  # packet traces
        self.peers = {}
        self.on_message = None  # callback(server, peer, raw message) for game-server messages

    def handle(self, data, addr):
        try:
            pkt = Packet.parse(data)
        except struct.error:
            self.debug(f"[fdp] {addr} short packet\n{self.hexdump(data)}")
            return
        peer = self.peers.get(addr)
        if peer:
            peer.last_seen = time.time()
        quiet = peer and not pkt.system and messages(pkt.payload) and all(
            (m[1], m[2]) == (9, 9001) for m in messages(pkt.payload))
        bare_ack = pkt.system and pkt.split_id == 1 and not pkt.payload
        if bare_ack and peer:
            if pkt.ack_id != getattr(peer, "last_ack_seen", None):
                self.debug(f"[fdp] {addr} <- ack {pkt}")
                peer.last_ack_seen = pkt.ack_id
        elif not quiet:
            self.debug(f"[fdp] {addr} <- {pkt}\n{self.hexdump(pkt.payload)}")
        if pkt.system and pkt.split_id == SPLIT_SYN:
            self.on_syn(pkt, addr)
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
            if not peer and len(self.peers) >= MAX_PEERS and not self.expire_idle():
                self.log(f"[fdp] {addr} SYN ignored: {len(self.peers)} sessions open")
                return
            peer = self.peers[addr] = Peer(addr, pkt.session)
            peer.last_seen = time.time()
            (n,) = struct.unpack_from("<I", pkt.payload) if len(pkt.payload) >= 4 else (0,)
            peer.connect_stream = pkt.payload[4:4 + n]
            self.log(f"[fdp] {addr} new session client={pkt.session} server={peer.server_session} "
                     f"connect_stream={peer.connect_stream.hex()}")
        reply = Packet(0x07, peer.next_reliable, pkt.session, HEAD.size, SPLIT_SYN_ACK, peer.server_session, 1)
        self.send(reply, addr)

    def expire_idle(self):
        """Drop sessions that sent nothing for PEER_TIMEOUT seconds. True if any were dropped."""
        cutoff = time.time() - PEER_TIMEOUT
        stale = [a for a, p in self.peers.items() if getattr(p, "last_seen", 0) < cutoff]
        for a in stale:
            del self.peers[a]
        if stale:
            self.log(f"[fdp] dropped {len(stale)} idle sessions")
        return bool(stale)

    def send_messages(self, peer, raw_messages, reliable=True, quiet=False):
        """Send Outpop::Message blobs to a peer, framed as (u16 length | message)."""
        payload = b"".join(struct.pack("<H", len(m)) + m for m in raw_messages)
        order = 0
        if reliable:
            number, peer.next_reliable = peer.next_reliable, (peer.next_reliable + 1) & 0xFFFF
            order, peer.next_order = peer.next_order, (peer.next_order + 1) & 0xFFFF
        else:
            number, peer.next_unreliable = peer.next_unreliable, (peer.next_unreliable + 1) & 0xFFFF
        flags = 0xE1 if reliable else 0x01
        pkt = Packet(flags, number, peer.client_session, HEAD.size + len(payload), 1 if reliable else 0,
                     peer.expected, order, payload)
        self.send(pkt, peer.addr, quiet=quiet)

    def send(self, pkt, addr, quiet=False):
        if not quiet:
            self.debug(f"[fdp] {addr} -> {pkt}\n{self.hexdump(pkt.payload)}")
        self.sock.sendto(pkt.pack(), addr)


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
