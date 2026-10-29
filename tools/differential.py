#!/usr/bin/env python3
"""Write tests/differential_tests.nv from independent oracles.

Three oracles stand behind the rows.

* Node 22's built-in WebSocket client (undici), a separate RFC 6455
  implementation.  A small server written here completes the opening
  handshake with it, sends it a scripted stream of frames and records
  every frame the client sends back.  The client checks the accept
  value, refuses a masked server frame, reassembles the fragments,
  answers the ping and validates the UTF-8 of text, so a stream it
  accepts is a stream the RFC allows.  The frames it sent are masked
  with keys of its own, and the test decodes them as they were
  captured.
* Python's `hashlib`, `base64` and strict UTF-8 codec, for the accept
  values, the client keys and the verdict and offset of every UTF-8
  string.
* A frame writer in this file written straight from RFC 6455 section
  5.2, whose output the Node client has just accepted.

Run from the package root:  python3 tools/differential.py
The output is passed through `novo fmt`.
"""
import base64
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading

SEED = 6455
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
rng = random.Random(SEED)


def accept(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def mask(data, key):
    return bytes(b ^ key[i % 4] for i, b in enumerate(data))


def frame(fin, op, payload, key=None):
    """One frame, written from RFC 6455 section 5.2."""
    n = len(payload)
    out = bytearray([(0x80 if fin else 0) | op])
    flag = 0x80 if key else 0
    if n < 126:
        out.append(flag | n)
    elif n < 65536:
        out += bytes([flag | 126]) + n.to_bytes(2, 'big')
    else:
        out += bytes([flag | 127]) + n.to_bytes(8, 'big')
    if key:
        out += bytes(key) + mask(payload, key)
    else:
        out += payload
    return bytes(out)


def rand_bytes(n):
    return bytes(rng.randrange(256) for _ in range(n))


def rand_key():
    return bytes(rng.randrange(256) for _ in range(4))


def hx(b):
    return b.hex()


def q(s):
    return '"' + s + '"'


# ── the Node client against a scripted server ──────────────────────────

NODE_CLIENT = r'''
const port = process.argv[2];
const ws = new WebSocket(`ws://127.0.0.1:${port}/chat`);
ws.binaryType = 'arraybuffer';
const seen = [];
let expect = Number(process.argv[3]);
ws.onmessage = (ev) => {
  if (typeof ev.data === 'string') seen.push(['text', Buffer.from(ev.data).toString('hex')]);
  else seen.push(['binary', Buffer.from(ev.data).toString('hex')]);
  if (seen.length === expect) {
    ws.send('héllo wörld ✓');
    ws.send('');
    ws.send(new Uint8Array(Array.from({length: 200}, (_, i) => (i * 7 + 3) & 255)));
    ws.send('x'.repeat(300));
    ws.close(1000, 'bye ✓');
  }
};
ws.onclose = (ev) => {
  console.log(JSON.stringify({seen, code: ev.code, reason: ev.reason, clean: ev.wasClean}));
};
ws.onerror = () => { console.log(JSON.stringify({error: true})); process.exit(1); };
'''


def read_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def read_frame(sock):
    """One masked client frame: (wire, fin, op, payload)."""
    head = read_exact(sock, 2)
    n = head[1] & 0x7F
    ext = b''
    if n == 126:
        ext = read_exact(sock, 2)
        n = int.from_bytes(ext, 'big')
    elif n == 127:
        ext = read_exact(sock, 8)
        n = int.from_bytes(ext, 'big')
    assert head[1] & 0x80, 'the client must mask'
    key = read_exact(sock, 4)
    body = read_exact(sock, n)
    return head + ext + key + body, bool(head[0] & 0x80), head[0] & 0xF, mask(body, key)


def run_node(script):
    """Run the scripted server against Node's client.  Returns what the
    server sent, the client frames it captured and what the client saw."""
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    data_messages = sum(1 for (fin, op, _) in script if op in (0, 1, 2) and fin)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
        f.write(NODE_CLIENT)
        path = f.name
    proc = subprocess.Popen(['node', path, str(port), str(data_messages)],
                            stdout=subprocess.PIPE, text=True)
    conn, _ = srv.accept()
    req = b''
    while b'\r\n\r\n' not in req:
        req += conn.recv(4096)
    key = [l.split(':', 1)[1].strip() for l in req.decode().split('\r\n')
           if l.lower().startswith('sec-websocket-key')][0]
    conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                  'Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n'
                  % accept(key)).encode())
    sent = b''
    for (fin, op, payload) in script:
        w = frame(fin, op, payload)
        conn.sendall(w)
        sent += w
    captured = []
    while True:
        wire, fin, op, payload = read_frame(conn)
        captured.append((wire, fin, op, payload))
        if op == 8:
            break
    conn.sendall(frame(True, 8, (1000).to_bytes(2, 'big') + 'done'.encode()))
    conn.close()
    out = proc.communicate(timeout=20)[0]
    os.unlink(path)
    result = json.loads(out.strip().splitlines()[-1])
    assert not result.get('error'), 'the client refused the scripted stream'
    return sent, captured, result


BIGN = 70000


def server_script():
    big = 'a' * BIGN
    return [
        (False, 1, 'héllo '.encode()[:3]),
        (False, 0, 'héllo '.encode()[3:]),
        (True, 9, b'hi'),
        (True, 0, 'wörld'.encode()),
        (True, 2, bytes((i * 11 + 5) & 255 for i in range(300))),
        (True, 1, b''),
        (True, 1, big.encode()),
        (True, 10, b'unsolicited'),
    ]


# ── rendering shared with the Novo side ────────────────────────────────

def render_events(script):
    """The events the reader gives for a stream of frames, in its order."""
    out = []
    open_kind, acc = 0, b''
    for (fin, op, payload) in script:
        if op >= 8:
            if op == 9:
                out.append('ping:' + hx(payload))
            elif op == 10:
                out.append('pong:' + hx(payload))
            else:
                code = int.from_bytes(payload[:2], 'big') if payload else 1005
                out.append('close:%d:%s' % (code, payload[2:].decode()))
            continue
        kind = op if op else open_kind
        acc += payload
        out.append('frame:%d:%d:%s' % (op, 1 if fin else 0, hx(payload) if len(payload) <= 64 else 'long%d' % len(payload)))
        if fin:
            out.append('%s:%s' % ('text' if kind == 1 else 'binary',
                                  hx(acc) if len(acc) <= 64 else 'long%d' % len(acc)))
            open_kind, acc = 0, b''
        else:
            open_kind = kind
    return out


# ── the rows ───────────────────────────────────────────────────────────

def frame_rows():
    rows = []
    lengths = [0, 1, 2, 5, 31, 100, 125, 126, 127, 300, 700]
    for op in (0, 1, 2, 8, 9, 10):
        for n in lengths:
            if op >= 8 and n > 125:
                continue
            for masked in (False, True):
                fin = True if op >= 8 else rng.choice([True, False])
                payload = rand_bytes(n)
                key = rand_key() if masked else None
                w = frame(fin, op, payload, key)
                rows.append((hx(w), op, fin, masked, hx(key or b''), hx(payload)))
    rng.shuffle(rows)
    return rows[:70]


def message_rows():
    rows = []
    for _ in range(30):
        kind = rng.choice([1, 2])
        n = rng.choice([0, 1, 3, 10, 50, 129, 300])
        payload = rand_bytes(n)
        if kind == 1:
            payload = ''.join(chr(rng.choice([0x41, 0xE9, 0x20AC, 0x1F600]))
                              for _ in range(n // 3)).encode()
        size = rng.choice([1, 2, 7, 64, 1000])
        masked = rng.choice([False, True])
        count = max(1, -(-len(payload) // size))
        keys = [rand_key() for _ in range(count)] if masked else []
        wire = b''
        for i in range(count):
            part = payload[i * size:(i + 1) * size]
            wire += frame(i == count - 1, kind if i == 0 else 0, part,
                          keys[i] if masked else None)
        rows.append((kind, hx(payload), size, [hx(k) for k in keys], hx(wire),
                     rng.choice([1, 3, 11, 4096])))
    return rows


def utf8_rows():
    rows = []
    pieces = [b'a', 'é'.encode(), '€'.encode(), '😀'.encode(), '\U0010ffff'.encode(),
              b'\xed\x9f\xbf', b'\xee\x80\x80']
    for _ in range(220):
        mode = rng.randrange(3)
        if mode == 0:
            s = b''.join(rng.choice(pieces) for _ in range(rng.randrange(1, 6)))
        elif mode == 1:
            s = b''.join(rng.choice(pieces) for _ in range(rng.randrange(1, 6)))
            s = bytearray(s)
            s[rng.randrange(len(s))] = rng.randrange(256)
            s = bytes(s)
        else:
            s = rand_bytes(rng.randrange(1, 6))
        try:
            s.decode('utf-8')
            fault = -1
        except UnicodeDecodeError as e:
            fault = e.start
        rows.append((hx(s), fault))
    # the boundary sequences RFC 3629 narrows
    for s in [b'\xe0\x9f\x80', b'\xe0\xa0\x80', b'\xed\xa0\x80', b'\xed\x9f\xbf',
              b'\xf0\x8f\x80\x80', b'\xf0\x90\x80\x80', b'\xf4\x8f\xbf\xbf',
              b'\xf4\x90\x80\x80', b'\xc0\x80', b'\xc1\xbf', b'\xc2\x80',
              b'\xf5\x80\x80\x80', b'\xe1\x80', b'\xf1\x80\x80']:
        try:
            s.decode('utf-8')
            fault = -1
        except UnicodeDecodeError as e:
            fault = e.start
        rows.append((hx(s), fault))
    return rows


def accept_rows():
    rows = []
    for _ in range(40):
        nonce = rand_bytes(16)
        key = base64.b64encode(nonce).decode()
        rows.append((hx(nonce), key, accept(key)))
    return rows


def mask_rows():
    rows = []
    for _ in range(30):
        n = rng.choice([0, 1, 3, 4, 5, 17, 100])
        p, k = rand_bytes(n), rand_key()
        rows.append((hx(p), hx(k), hx(mask(p, k))))
    return rows


BIG = [125, 126, 127, 65535, 65536, 70000]


def pattern(n):
    return bytes((i * 7 + 3) & 255 for i in range(n))


def big_rows():
    rows = []
    for n in BIG:
        key = rand_key()
        w = frame(True, 2, pattern(n), key)
        rows.append((n, hx(key), hx(w[:14]), len(w)))
    return rows


def main():
    sent, captured, seen = run_node(server_script())
    server_events = render_events(server_script())
    client_script = [(f, o, p) for (_, f, o, p) in captured]
    client_events = render_events(client_script)
    # Node answers the ping with a pong, so the client frames are the four
    # messages, the pong and the close; the pong comes between them.
    assert any(o == 10 for (_, _, o, _) in captured), 'the client must pong'
    assert seen['code'] == 1000 and seen['reason'] == 'done'
    # The frame of BIGN bytes is left out of the file; the test writes its
    # payload, which is the letter a repeated.
    big_header = frame(True, 1, b'a' * BIGN)[:10]
    cut = sent.index(big_header)
    assert sent[cut + 10:cut + 10 + BIGN] == b'a' * BIGN
    ctx = {
        'frames': frame_rows(),
        'messages': message_rows(),
        'utf8': utf8_rows(),
        'accepts': accept_rows(),
        'masks': mask_rows(),
        'big': big_rows(),
        'server_stream': (hx(sent[:cut]), hx(sent[cut:cut + 10]), hx(sent[cut + 10 + BIGN:]), server_events),
        'client_stream': (hx(b''.join(w for (w, _, _, _) in captured)), client_events),
        'node_saw': [s[0] + ':' + (s[1] if len(s[1]) <= 128 else 'long%d' % (len(s[1]) // 2)) for s in seen['seen']],
    }
    emit(ctx)


def nv_list(rows, fmt):
    return '[\n' + ',\n'.join('        ' + fmt(r) for r in rows) + ']'


def emit(c):
    def strs(xs):
        return '[' + ', '.join(q(x) for x in xs) + ']'

    out = []
    out.append(HEADER)
    out.append('fn frame_rows() -> [(Str, Int, Bool, Bool, Str, Str)]\n    ' +
               nv_list(c['frames'], lambda r: '(%s, %d, %s, %s, %s, %s)' % (
                   q(r[0]), r[1], str(r[2]).lower(), str(r[3]).lower(), q(r[4]), q(r[5]))) + '\n')
    out.append('fn message_rows() -> [(Int, Str, Int, [Str], Str, Int)]\n    ' +
               nv_list(c['messages'], lambda r: '(%d, %s, %d, %s, %s, %d)' % (
                   r[0], q(r[1]), r[2], strs(r[3]), q(r[4]), r[5])) + '\n')
    out.append('fn utf8_rows() -> [(Str, Int)]\n    ' +
               nv_list(c['utf8'], lambda r: '(%s, %d)' % (q(r[0]), r[1])) + '\n')
    out.append('fn accept_rows() -> [(Str, Str, Str)]\n    ' +
               nv_list(c['accepts'], lambda r: '(%s, %s, %s)' % (q(r[0]), q(r[1]), q(r[2]))) + '\n')
    out.append('fn mask_rows() -> [(Str, Str, Str)]\n    ' +
               nv_list(c['masks'], lambda r: '(%s, %s, %s)' % (q(r[0]), q(r[1]), q(r[2]))) + '\n')
    out.append('fn big_rows() -> [(Int, Str, Str, Int)]\n    ' +
               nv_list(c['big'], lambda r: '(%d, %s, %s, %d)' % (r[0], q(r[1]), q(r[2]), r[3])) + '\n')
    out.append('fn server_before() -> Str\n    %s\n' % q(c['server_stream'][0]))
    out.append('fn server_big_header() -> Str\n    %s\n' % q(c['server_stream'][1]))
    out.append('fn server_after() -> Str\n    %s\n' % q(c['server_stream'][2]))
    out.append('fn server_events() -> [Str]\n    %s\n' % strs(c['server_stream'][3]))
    out.append('fn client_stream() -> Str\n    %s\n' % q(c['client_stream'][0]))
    out.append('fn client_events() -> [Str]\n    %s\n' % strs(c['client_stream'][1]))
    out.append('fn node_saw() -> [Str]\n    %s\n' % strs(c['node_saw']))
    out.append(BODY)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tests', 'differential_tests.nv')
    with open(path, 'w') as f:
        f.write('\n'.join(out))
    for _ in range(3):
        subprocess.run(['novo', 'fmt', path], check=True)


HEADER = """// tests/differential_tests.nv — this package against independent oracles.
//
// Written by tools/differential.py; do not edit by hand.  The rows come
// from three oracles.  Node's built-in WebSocket client accepted the
// server stream and sent the client stream, whose masks are its own.
// Python's `hashlib`, `base64` and strict UTF-8 codec give the accept
// values, the client keys and the verdict and offset of every UTF-8
// string.  The frame rows are written from RFC 6455 section 5.2 by the
// script, and the Node client accepted the same writer's output.

use std.test
use std.bytes
use wsframe
use wsclose
use wshandshake
use wsread
use wswrite
"""

BODY = """
// The bytes a hex string names.
fn raw(h: Str) -> [u8]
    match bytes.from_hex(h)
        Some(b) => bytes.to_byte_list(b)
        None    => []

fn hex_of(b: [u8]) -> Str
    bytes.to_hex(bytes.from_byte_list(b))

fn opcode(n: Int) -> WsOpcode
    wsframe.opcode_of(n) ?? Continuation

// A payload longer than 64 bytes is rendered by its length alone.
fn shown(b: [u8]) -> Str
    if list.len(b) <= 64 then hex_of(b) else "long${list.len(b)}"

// The server stream: the frame of 70000 letters is written here.
fn server_stream() -> [u8]
    var big: [u8] = []
    for i in 0..70000
        list.push(big, 0x61 as u8)
    list.concat(list.concat(raw(server_before()), raw(server_big_header())), list.concat(big, raw(server_after())))

// An event as the script renders it.
fn render(ev: WsEvent) -> Str
    match ev
        DataFrame(f) =>
            // workaround(fmt-rewrites-an-if-expression-inside-a-string-interpolation-and-is-not-stable): the bit is bound first.
            let bit = if f.header.fin then 1 else 0
            "frame:${wsframe.opcode_number(f.header.opcode)}:${bit}:${shown(f.payload)}"
        DataMessage(m) =>
            match m
                TextMessage(u)   => "text:${shown(u)}"
                BinaryMessage(d) => "binary:${shown(d)}"
        ControlPing(p) => "ping:${hex_of(p)}"
        ControlPong(p) => "pong:${hex_of(p)}"
        PeerClose(c)   => "close:${wsclose.number_of(c.code)}:${c.reason}"

// Every event a stream gives when it is fed in pieces of `piece` bytes.
fn events_of(role: WsRole, stream: [u8], piece: Int) -> Result<[Str], WsDecodeError>
    var r = wsread.reader(role, wsread.default_limits())
    var out: [Str] = []
    var at = 0
    while at < list.len(stream)
        let hi = if at + piece < list.len(stream) then at + piece else list.len(stream)
        var step = wsread.feed(r, stream[at:hi])!
        while step.event != None
            match step.event
                Some(ev) => list.push(out, render(ev))
                None     => ()
            step = wsread.take(step.reader)!
        r = step.reader
        at = hi
    Ok(out)

fn same_events(got: Result<[Str], WsDecodeError>, want: [Str])
    match got
        Err(e)  => test.fail(e.message())
        Ok(evs) =>
            test.assert_eq(list.len(evs), list.len(want))
            for i in 0..list.len(want)
                if i < list.len(evs)
                    test.assert_eq_str(evs[i], want[i])

@test
fn test_frames_are_written_and_read_as_the_reference_writes_them() [io]
    var n = 0
    for row in frame_rows()
        let (wire_hex, op, fin, masked, key_hex, payload_hex) = row
        test.case("frame ${n}")
        let wire = raw(wire_hex)
        let payload = raw(payload_hex)
        let header = WsFrameHeader {
            fin: fin, rsv1: false, rsv2: false, rsv3: false,
            opcode: opcode(op), masked: masked, payload_len: list.len(payload),
            mask_key: if masked then Some(raw(key_hex)) else None }
        match wsframe.encode(WsFrame { header: header, payload: payload })
            Ok(got) => test.assert_eq_str(hex_of(got), wire_hex)
            Err(e)  => test.fail(e.message())
        let role = if masked then ServerSide else ClientSide
        match wsframe.decode(wire, role)
            Ok(f) =>
                test.assert_eq_str(hex_of(f.payload), payload_hex)
                test.assert_eq(wsframe.opcode_number(f.header.opcode), op)
                test.assert(f.header.fin == fin)
            Err(e) => test.fail(e.message())
        match wsframe.scan(wire)
            Complete(len) => test.assert_eq(len, list.len(wire))
            NeedMore      => test.fail("the whole frame is here")
        if list.len(wire) <= 40
            for k in 0..list.len(wire)
                match wsframe.scan(wire[0:k])
                    NeedMore    => test.assert(true)
                    Complete(_) => test.fail("a prefix is not a frame")
        n = n + 1

@test
fn test_messages_fragment_and_reassemble_as_the_reference_does() [io]
    var n = 0
    for row in message_rows()
        let (kind, payload_hex, size, key_hexes, wire_hex, piece) = row
        test.case("message ${n}")
        let payload = raw(payload_hex)
        let msg = if kind == 1 then TextMessage(payload) else BinaryMessage(payload)
        let keys = list.map(key_hexes, k => raw(k))
        match wswrite.bytes(msg, size, keys)
            Ok(got) => test.assert_eq_str(hex_of(got), wire_hex)
            Err(e)  => test.fail(e.message())
        let role = if list.len(keys) > 0 then ServerSide else ClientSide
        match events_of(role, raw(wire_hex), piece)
            Err(e)  => test.fail(e.message())
            Ok(evs) =>
                let last = if list.len(evs) > 0 then evs[list.len(evs) - 1] else ""
                let want = (if kind == 1 then "text:" else "binary:") + shown(payload)
                test.assert_eq_str(last, want)
        n = n + 1

@test
fn test_long_frames_use_the_length_form_the_reference_uses() [io]
    var n = 0
    for row in big_rows()
        let (len, key_hex, head_hex, total) = row
        test.case("length ${len}")
        var payload: [u8] = []
        for i in 0..len
            list.push(payload, ((i * 7 + 3) & 255) as u8)
        match wswrite.binary(payload, Some(raw(key_hex)))
            Err(e) => test.fail(e.message())
            Ok(f)  =>
                match wsframe.encode(f)
                    Err(e)   => test.fail(e.message())
                    Ok(wire) =>
                        test.assert_eq(list.len(wire), total)
                        test.assert_eq_str(hex_of(wire[0:14]), head_hex)
                        match wsframe.decode(wire, ServerSide)
                            Err(e)  => test.fail(e.message())
                            Ok(back) =>
                                test.assert_eq(list.len(back.payload), len)
                                test.assert_eq_str(hex_of(back.payload[0:8]), hex_of(payload[0:8]))
        n = n + 1

@test
fn test_utf8_is_judged_as_python_judges_it() [io]
    var n = 0
    for row in utf8_rows()
        let (hex, fault) = row
        test.case("string ${n}")
        let b = raw(hex)
        test.assert(wsread.is_valid_utf8(b) == (fault < 0))
        match wsframe.utf8_fault(b)
            Some(at) => test.assert_eq(at, fault)
            None     => test.assert_eq(-1, fault)
        n = n + 1

@test
fn test_keys_and_accept_values_are_the_ones_hashlib_computes() [io]
    var n = 0
    for row in accept_rows()
        let (nonce_hex, key, accept) = row
        test.case("nonce ${n}")
        match wshandshake.client_key(raw(nonce_hex))
            Ok(got) => test.assert_eq_str(got, key)
            Err(e)  => test.fail(e.message())
        test.assert_eq_str(wshandshake.accept_key(key), accept)
        test.assert(wshandshake.is_valid_key(key))
        n = n + 1

@test
fn test_the_mask_is_xor_with_the_repeating_key() [io]
    var n = 0
    for row in mask_rows()
        let (payload_hex, key_hex, want) = row
        test.case("mask ${n}")
        test.assert_eq_str(hex_of(wsframe.mask(raw(payload_hex), raw(key_hex))), want)
        n = n + 1

@test
fn test_the_stream_node_accepted_reads_as_the_script_says() [io]
    test.case("whole")
    same_events(events_of(ClientSide, server_stream(), 100000), server_events())
    test.case("one byte at a time")
    same_events(events_of(ClientSide, server_stream(), 1), server_events())
    test.case("in pieces of 7")
    same_events(events_of(ClientSide, server_stream(), 7), server_events())

@test
fn test_the_frames_node_sent_decode_as_captured() [io]
    test.case("whole")
    same_events(events_of(ServerSide, raw(client_stream()), 100000), client_events())
    test.case("in pieces of 5")
    same_events(events_of(ServerSide, raw(client_stream()), 5), client_events())

@test
fn test_drain_gives_the_same_events_as_feeding() [io]
    test.case("the client stream over a buffer")
    let src = Buffer.from_bytes(raw(client_stream()))
    match wsread.drain(wsread.reader(ServerSide, wsread.default_limits()), src)
        Err(e) => test.fail(e.message())
        Ok(d)  =>
            test.assert_eq(list.len(d.events), list.len(client_events()))
            test.assert(wsread.closed(d.reader))
    src.drop()
"""

if __name__ == '__main__':
    main()
