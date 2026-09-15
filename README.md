# websocket-codec-nv

The WebSocket Protocol gives a client and a server two-way messaging over
one TCP connection, in both directions at once. It is specified in
[RFC 6455](https://www.rfc-editor.org/rfc/rfc6455). This package brings
the arithmetic of it to novo-lang, with no socket underneath: frame
headers to and from bytes, the mask, fragmentation and reassembly, the
close codes, and the opening handshake as a computation. The connection
itself belongs to [websocket-nv](https://novo-lang.org/packages/websocket-nv),
which is this package with a socket under it. The handshake is an
HTTP/1.1 exchange, so
[http-codec-nv](https://novo-lang.org/packages/http-codec-nv) supplies
the request and response types it is spelled in.

**Status: NOT IMPLEMENTED — interface only.** Every function is declared
with its full signature, but every body is a `todo()` that panics when
called. The package is published so its design can be reviewed and
depended on before it is implemented. Version 0.1.0 will be the first
working release.

## What a WebSocket frame is

A WebSocket connection begins as an ordinary HTTP/1.1 request that asks
to change protocol. The client sends a `GET` carrying
`Upgrade: websocket`, `Connection: Upgrade`, a `Sec-WebSocket-Key` and
`Sec-WebSocket-Version: 13`. The server answers `101 Switching
Protocols` with a `Sec-WebSocket-Accept`, which is
`base64(sha1(key + GUID))` for the one fixed GUID RFC 6455 section 4.2.2
names. After that exchange the same connection carries frames rather
than HTTP messages.

A **frame** is a two-byte header, then whichever conditional fields the
header called for, then a payload. The first byte holds the FIN bit, the
three reserved bits and a four-bit **opcode**, which says what the frame
is for. The second byte holds the mask bit and a seven-bit length.
Section 5.2 defines the layout.

| Opcode | Name | Meaning |
| --- | --- | --- |
| 0x0 | Continuation | More of the message the last data frame began |
| 0x1 | Text | A payload that must be valid UTF-8 |
| 0x2 | Binary | An opaque payload |
| 0x8 | Close | The close handshake |
| 0x9 | Ping | A liveness probe the peer must answer |
| 0xA | Pong | The answer to a ping |
| 0x3 to 0x7, 0xB to 0xF | Reserved | Unassigned, and refused by a conforming endpoint |

The payload length is not one field. It is seven bits, or seven bits and
then sixteen more, or seven bits and then sixty-four more, and which
form is legal depends on the value. Section 5.2 requires the shortest
form that holds the number.

| Payload bytes | Seven-bit field | Extra length bytes |
| --- | --- | --- |
| 0 to 125 | the length itself | none |
| 126 to 65535 | 126 | 2, big-endian |
| 65536 and up | 127 | 8, big-endian |

Every frame a **client** sends is masked and every frame a **server**
sends is not (section 5.1). Masking is exclusive-or with a four-byte key
that repeats, and the key travels in the frame, so it hides nothing from
anyone reading the connection. It exists to stop an intermediary that
does not understand WebSocket from being tricked into reading the
payload as a second HTTP request. The transformation is its own inverse,
so one function both applies and removes it.

A sender may split one message across any number of frames, which is
**fragmentation** (section 5.4). The first fragment carries the
message's opcode, the rest carry Continuation, and only the last carries
FIN. **Control frames**, which are close, ping and pong, may arrive
between the fragments of a data message. A control frame carries at most
125 bytes and is never fragmented.

A close frame's payload is a two-byte status code and an optional reason
in UTF-8. Section 7.4.1 assigns sixteen codes and reserves two ranges
for other people to assign.

| Code | Name | May be sent |
| --- | --- | --- |
| 1000 | Normal closure | yes |
| 1001 | Going away | yes |
| 1002 | Protocol error | yes |
| 1003 | Unsupported data | yes |
| 1005 | No status received | no, reported locally |
| 1006 | Abnormal closure | no, reported locally |
| 1007 | Invalid payload data | yes |
| 1008 | Policy violation | yes |
| 1009 | Message too big | yes |
| 1010 | Mandatory extension | yes, from a client |
| 1011 | Internal error | yes, from a server |
| 1012 | Service restart | yes |
| 1013 | Try again later | yes |
| 1014 | Bad gateway | yes |
| 1015 | TLS handshake failure | no, reported locally |
| 3000 to 3999 | Registered with IANA | yes |
| 4000 to 4999 | Private to an application | yes |

The fixed sizes of the protocol are small and worth having in one place.

| Quantity | Value | Where |
| --- | --- | --- |
| Mask key | 4 bytes | section 5.3 |
| Control frame payload | at most 125 bytes | section 5.5 |
| Close reason | at most 123 bytes | 125 minus the two-byte code |
| `Sec-WebSocket-Key` nonce | 16 bytes | section 4.1 |
| Protocol version | 13 | section 4.1 |

This package performs no input or output. It opens no connection, reads
no clock and draws no random bytes. The two values an endpoint needs
from the machine, the handshake nonce and one mask key per client frame,
arrive as arguments. Every function is arithmetic over bytes the caller
already holds, so the same code runs in a server, in a client, in a
proxy and in a capture tool reading a file.

## Install

```
novo pkg add websocket-codec-nv
```

## Example

```novo
use std.list
use wsframe
use wsclose
use wsread
use wswrite

fn main() [io]
    // One frame as a client sends it: a masked text frame carrying
    // "Hello", the example RFC 6455 section 5.7 prints.
    let buf: [u8] = [0x81 as u8, 0x85 as u8,
                     0x37 as u8, 0xFA as u8, 0x21 as u8, 0x3D as u8,
                     0x7F as u8, 0x9F as u8, 0x4D as u8, 0x51 as u8, 0x58 as u8]

    // Ask how many bytes at the front of the buffer are one frame.
    match wsframe.scan(buf)
        NeedMore    => println("the frame has not all arrived")
        Complete(n) => println("${n} bytes are one frame")

    // A server-side reader removes the mask and reassembles messages.
    let r = wsread.reader(ServerSide, wsread.default_limits())

    // Hand it the bytes. One call takes at most one event out.
    match wsread.feed(r, buf)
        Err(e)   => println("the peer broke the protocol: ${e.message()}")
        Ok(step) =>
            match step.event
                None     => println("nothing complete yet")
                Some(ev) => report(ev)

// One event, and what the endpoint owes the peer for it.
fn report(ev: WsEvent) [io]
    match ev
        DataFrame(f)   => println("a fragment of ${list.len(f.payload)} bytes")
        DataMessage(_) => println("a whole message")
        ControlPing(p) =>
            // A pong carries the ping's payload unchanged. A server
            // does not mask, so it passes no key.
            match wswrite.pong(p, None)
                Ok(_)  => println("a pong is ready to send")
                Err(_) => println("the ping was not a legal control frame")
        ControlPong(_) => println("the peer answered a ping")
        PeerClose(c)   => println("closing with ${wsclose.number_of(c.code)}")
```

Build and test with `novo pkg build` and `novo test`. Today `novo test`
fails on purpose: every test reaches a `not implemented:
websocket-codec-nv.<module>.<fn>` panic. The tests are the specification
the implementation will have to satisfy.

## What the package contains

| Module | Contents |
| --- | --- |
| `wsframe` | The frame as a type. The opcodes, the role, the header, encoding and decoding one frame, the masking function, and `scan`, which reads only the length field. |
| `wsclose` | The close handshake's status code as a typed value: the sixteen assigned numbers, the two open ranges, the rule about which may be sent, and a close payload to and from bytes. |
| `wshandshake` | The opening handshake as a computation. The accept key, the client key, and the functions that build and check the HTTP/1.1 request and response. |
| `wsread` | A reader that takes bytes as they arrive, removes the mask, reassembles fragmented messages and hands back one event at a time. |
| `wswrite` | Messages as frames. Fragmentation at a size the caller chooses, the six single-frame builders, and a whole message as bytes in one call. |
| `wserr` | The three refusal types, one per audience: a decode fault answered with a close code, an encode fault that is the calling program's mistake, and a handshake fault answered with an HTTP status. |

## How to choose an entry point

There are three ways to read what arrived, and they differ in how much
state you keep yourself.

**`wsframe.decode` reads one whole frame from one buffer.** Use it when
something upstream has already given you exactly one frame's bytes. It
ignores any tail beyond the frame.

**`wsframe.scan` reads only the length field.** It answers how many
bytes at the front of the buffer are one frame, and it copies nothing.
Use it when you hold a receive buffer and slice frames out of it
yourself.

**`wsread.reader` holds the state between "some bytes turned up" and
"here is an event".** It keeps a partial frame, removes the mask,
reassembles a fragmented message and reports pings and closes as their
own events. Use it in any endpoint that reads from a socket, because a
socket hands over whatever arrived rather than whole frames. Feed it
with `feed`, then call `take` until the event is `None`.

`wsread.drain` is that loop written once, over any source that
implements the standard library's `Read` trait. It is the only function
in the package that costs the caller anything, and what it costs is
whatever the source costs.

There are two ways to write, and they differ in what you hold.
**`wswrite.bytes` takes a whole message and hands back one buffer.**
**`wswrite.message_frames` hands back the frames**, for a caller
streaming something too large to hold, or one that writes each frame
separately.

## The rules a user needs

1. **A client masks and a server does not** (RFC 6455 section 5.1).
   `decode_header` and `decode` take the role of the endpoint doing the
   reading. A server that accepted an unmasked frame is the
   cache-poisoning hole the mask exists to close, so an unmasked client
   frame is `UnmaskedClientFrame` and a masked server frame is
   `MaskedServerFrame`.
2. **The mask key is yours to supply, four bytes per frame**
   (section 5.3). Randomness is a host effect and this package declares
   none, so `wswrite` takes the keys as an argument.
   `wswrite.frame_count` says how many a message will need before you
   have to make them. A server passes an empty list.
3. **The handshake nonce is yours to supply, sixteen bytes**
   (section 4.1). `wshandshake.client_key` takes the bytes and returns
   the base64. A nonce of any other length is `NonceWrongLength`.
4. **A length written in a longer form than it needed is a protocol
   error** (section 5.2). It is not a wasteful encoding to tolerate.
   Accepting both forms lets two peers disagree about where a frame
   ends. The refusal is `NonMinimalLength`, and
   `wsframe.minimal_length_form` is the rule on its own.
5. **A set reserved bit is refused** (section 5.2). RSV1 to RSV3 are
   zero unless an extension negotiated in the handshake says otherwise,
   and this package negotiates none, so a set bit is `ReservedBitSet`.
6. **A control frame carries at most 125 bytes and is never fragmented**
   (section 5.5). A longer one is `ControlFrameTooLong` and one with FIN
   clear is `FragmentedControlFrame`. A control frame between the
   fragments of a data message is legal. A second data frame there is
   not, and that is `InterleavedDataFrame`.
7. **A ping must be answered with a pong carrying the same payload**
   (section 5.5.2). This package reports the ping as `ControlPing` and
   builds the answer with `wswrite.pong`. Sending it is the host's.
8. **Three close codes may never go on a wire** (section 7.4.1). 1005,
   1006 and 1015 are values an application reports to its own code, and
   a peer that receives one must fail the connection.
   `wsclose.is_sendable` answers the question and `wswrite.close_frame`
   refuses the frame, so the illegal frame cannot be built.
9. **An empty close payload is legal and means no status was given**
   (section 5.5.1). It decodes as `NoStatusReceived`. A payload of
   exactly one byte is half a code and is `CloseFrameTooShort`.
10. **A text payload that is not valid UTF-8 must fail the connection
    with close code 1007** (section 8.1). Text is therefore carried as
    bytes, so the invalid case survives long enough to be reported.
    `wsread.is_valid_utf8` is the check, and it rejects surrogates and
    overlong forms.
11. **`wsclose.close_code_for` names the code a refusal is owed.** 1002
    for a framing violation, 1007 for bad UTF-8, 1009 for a message past
    the limit (section 7.4.1). An endpoint that chose the code itself
    sends 1002 for a UTF-8 fault sooner or later.
12. **An incomplete frame is not an error.** `wsframe.scan` answers
    `NeedMore`, and `wsread.feed` answers an event of `None`. Both mean
    keep the bytes and wait. An `Err` means something arrived complete
    and was wrong, and the connection has to be failed.
13. **The reader's limits are a denial-of-service bound, and the message
    limit doubles as a switch.** A frame header may declare 2^63 bytes,
    and a fragmented message may be extended forever with one-byte
    continuations, so `WsLimits` caps both. Setting `max_message` to
    zero turns reassembly off, and the reader then reports every
    fragment and never a whole message.
14. **The accept value in the server's 101 must be checked**
    (section 4.1). It is what stops a cache from replaying an unrelated
    101 at a client. `wshandshake.check_upgrade_response` checks it
    against the key that was sent and reports `AcceptMismatch`.

## Running on a microcontroller

novo-lang lets a package state which of its modules can run on a device
with no heap allocator, and the compiler checks that claim on every
build. Here the claim covers the whole package. Every function is
arithmetic over bytes the caller supplies, and the reader's state is a
value the caller owns rather than a buffer this package hides.

```bash
novo build --target=nrf52-qemu src/main.nv
```

`wsread.embedded_limits` is the limit set for that case: a frame of 1
KiB and a message of 4 KiB, against the 16 MiB and 64 MiB of
`wsread.default_limits`. The number that matters on a device is the one
that fits beside everything else in 64 KiB of RAM.

This release ships no device probe program. A probe would have to name
the package's own `Result` types, and a `Result` whose error type is
declared in the package cannot be spelled at the embedded tier with this
toolchain. The signatures keep their `Result`, and the probe arrives
with the toolchain fix.

## What is not included

- **A socket, a clock and a source of randomness.** Nothing here
  connects, waits, answers a ping or sends a close in reply to a close.
  Those are all sends, and this package performs nothing. It produces
  the events and builds the frames, and
  [websocket-nv](https://novo-lang.org/packages/websocket-nv) puts them
  on a wire.
- **Extension negotiation.** `permessage-deflate` (RFC 7692) is the one
  people want. It changes what RSV1 means, so a codec that
  half-supported it would accept frames it cannot decode. Until an
  extension is negotiated a set reserved bit is `ReservedBitSet`, which
  is what a conforming endpoint must answer.
  [websocket-nv](https://novo-lang.org/packages/websocket-nv) negotiates
  and performs `permessage-deflate` one layer up.
- **Choosing a subprotocol.** `check_upgrade_request` hands a server the
  list the client offered. Picking from it is policy, and a codec that
  picked would be picking for every application built on it.
- **Checking the `Origin` header.** Same reason. A client that is not a
  browser need not send one at all.
- **Decoding text to `Str`.** A text frame's payload is bytes, because
  invalid UTF-8 has to be reportable rather than unrepresentable.
- **WebSocket over HTTP/2 or HTTP/3** (RFC 8441). That is a different
  handshake over a different transport.

## Related packages

- [websocket-nv](https://novo-lang.org/packages/websocket-nv) is the
  host half of the same protocol. It dials or listens, performs the
  upgrade over a real connection, holds the connection value, answers
  pings, runs the close handshake with its deadlines and negotiates
  `permessage-deflate`. Every frame it sends was built here. Take that
  package if you want to talk to a peer. Take this one if you are
  writing an endpoint of your own, a proxy, a fuzzer or a capture tool,
  or if you have a transport this one has never heard of.
- [http-codec-nv](https://novo-lang.org/packages/http-codec-nv) is
  HTTP/1.1 without a socket. The opening handshake is an HTTP/1.1
  exchange, so this package depends on it and speaks its `H1Request` and
  `H1Response` rather than a second spelling of the same headers. A
  server therefore parses its request once.
- [crypto-nv](https://novo-lang.org/packages/crypto-nv) supplies the
  SHA-1 behind the accept key. That value is a handshake token neither
  party trusts, so SHA-1 being broken for collision resistance does not
  bear on it.
- [http2-nv](https://novo-lang.org/packages/http2-nv) is where to look
  for WebSocket over HTTP/2, which RFC 8441 defines and this package
  does not cover.
- `std.ws` in the standard library shares the name and almost nothing
  else. It is a `WebSocket` handle and six methods that hand a `Str` or
  a `Bytes` to the runtime, where the framing happens outside novo-lang.
  It has no frame type, no close code, no fragmentation and no
  handshake. It is the right answer for a program that talks to one
  server it trusts in three lines.

## Tests

```bash
novo test tests/wsframe_tests.nv       # the frame arithmetic
novo test tests/wsclose_tests.nv       # the close codes
novo test tests/wshandshake_tests.nv   # the upgrade exchange
novo test tests/wsread_tests.nv        # the reader and the writer
```

The byte strings the suite asserts against are RFC 6455's own. Section
5.7 prints a masked single-frame text message and its unmasked
counterpart, and both are here. Section 1.3 prints an example nonce, the
key it produces and the accept value that answers it, and that is the
vector `wshandshake` is checked against. The Autobahn Testsuite is the
oracle the finished implementation will be measured against.

The suite checks that an opcode and its four bits round trip, that a
frame survives encoding and decoding, that the mask is its own inverse,
that a server refuses an unmasked client frame and a client refuses a
masked server frame, that every close code round trips, that the three
local codes cannot be put in a frame, and that a whole frame arrives
from the reader as a fragment event and then as a message event.

`tests/wsread_tests.nv` also asserts something the compiler settles
before the run starts. `drain` over an in-memory buffer costs no
effects. Had `drain` spent an effect rather than binding the caller's,
that file would not compile.

The tests compile today and fail at run, each on the `not implemented:
websocket-codec-nv.<module>.<fn>` panic that is its body. That is the
expected state of an interface release. They turn green one at a time as
bodies land.

## Implementation status

The types, the enum variants and the effect rows are published in full.
This table is about the function bodies.

| Item | Implemented |
| --- | --- |
| `wsframe.CONTROL_PAYLOAD_MAX`, `.MASK_KEY_LEN` | yes (they are constants) |
| `wsclose.CLOSE_REASON_MAX` | yes (it is a constant) |
| `wshandshake.WS_GUID`, `.WS_VERSION`, `.NONCE_LEN` | yes (they are constants) |
| `wsframe.opcode_number`, `.opcode_of`, `.is_control`, `.header_len`, `.minimal_length_form` | no |
| `wsframe.scan`, `.encode_header`, `.decode_header`, `.mask`, `.encode`, `.decode` | no |
| `wsclose.number_of`, `.code_of`, `.is_sendable`, `.close_code_for`, `.encode_payload`, `.decode_payload` | no |
| `wshandshake.accept_key`, `.client_key`, `.is_valid_key` | no |
| `wshandshake.upgrade_request`, `.check_upgrade_request`, `.upgrade_response`, `.check_upgrade_response` | no |
| `wshandshake.version_refusal`, `.is_websocket_upgrade` | no |
| `wsread.default_limits`, `.embedded_limits`, `.reader`, `.feed`, `.take` | no |
| `wsread.pending_len`, `.assembling_len`, `.message_open`, `.closed`, `.is_valid_utf8` | no |
| `wsread.drain` | no |
| `wswrite.frame_count`, `.message_frames`, `.bytes` | no |
| `wswrite.text`, `.binary`, `.ping`, `.pong`, `.close_frame`, `.continuation`, `.fail_connection` | no |
| `wserr.http_status_for` and the three `message` implementations | no |

## Licence

Apache-2.0. See `LICENSE`.

<!-- docs/writing-a-readme.md is the style guide for this page. -->
