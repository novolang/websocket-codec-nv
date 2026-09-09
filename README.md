# websocket-codec-nv

**Status: NOT IMPLEMENTED — interface only.**

Every public function below is published with its signature and its
effect row, and every body is `todo()`.  Installing this package works;
calling it panics with `not implemented`.

## What this is

The WebSocket arithmetic with no socket under it: RFC 6455's frame
header to and from bytes, the mask, fragmentation and reassembly, the
control-frame constraints, close codes as a typed value, and the § 4
handshake as a pure computation whose exchange the host performs.

It does not connect, does not answer a ping, does not send a close in
reply to a close, and does not wait.  Those are all things an endpoint
must do and all of them are sends; this package produces the events and
builds the frames, and websocket-nv above it puts them on a wire.

## Adding it, and checking it

```bash
novo pkg add websocket-codec-nv    # into your novo.toml
novo pkg build                     # type- and effect-check the package
novo test --isolate tests/wsframe_tests.nv
```

`novo test` is red today and that is the point of the release: every
assertion below the protocol constants fails with `not implemented:
websocket-codec-nv.<module>.<fn>`.  They turn green one at a time as
bodies land.

## The one example that will work

```novo
use wsread
use wswrite
use wsframe

// A socket handed us some bytes.  Feed them, take what came out, and
// hand back the frames the host has to send.
fn on_bytes(r: WsReader, chunk: [u8]) -> Result<(WsReader, [WsFrame]), WsDecodeError>
    var step = wsread.feed(r, chunk)!
    var out: [WsFrame] = []
    var go = true
    while go
        match step.event
            None     => go = false
            Some(ev) =>
                match ev
                    DataMessage(m) => deliver(m)
                    ControlPing(p) => out = list.push(out, wswrite.pong(p, None)!)
                    PeerClose(c)   => out = list.push(out, wswrite.close_frame(c, None)!)
                    ControlPong(_) => nothing()
                    DataFrame(_)   => nothing()
                step = wsread.take(step.reader)!
    Ok((step.reader, out))
```

## The layer, and why

`core`.  Everything here is arithmetic over bytes the caller already
holds, and the two things a WebSocket endpoint needs from the machine
arrive as **arguments** rather than as effects:

```novo
pub fn client_key(nonce: [u8]) -> Result<Str, WsHandshakeError>
pub fn message_frames(msg: WsMessage, max_payload: Int, mask_keys: [[u8]]) -> Result<[WsFrame], WsEncodeError>
```

A `Sec-WebSocket-Key` is sixteen random bytes and every client frame
carries four more.  Randomness is `[rand]`, which `core` does not have,
so the caller generates them and hands them over — and the side effect
of that is that the handshake and the fragmentation become
**assertable**: `tests/wshandshake_tests.nv` feeds RFC 6455 § 1.3's own
example nonce and asserts the RFC's own example key, which a package
that generated its own entropy could not have been tested for at all.
`frame_count` exists so a caller knows how many keys to make before it
has to make them.

**There is no `tests/embedded_probe.nv` in this release, and its
absence is a filed defect rather than a decision.**  `Result<T, E>`
cannot be spelled at `@tier(embedded)`: with the package's own error
type the compiler refuses `impl Error for WsDecodeError` because the
`Error` trait is not in the prelude at that tier [E2005], and without
the impl it refuses the `Result` itself [E2018].  That is
`result-is-unusable-at-tier-embedded-no-error-trait`, open against the
toolchain.  The signatures keep their `Result`.

## The load-bearing interface

Two decisions, and the fact that each is a refusal the encoder makes
rather than a rule the documentation states.

```novo
pub fn is_sendable(c: WsCloseCode) -> Bool
pub fn decode_header(buf: [u8], role: WsRole) -> Result<WsFrameHeader, WsDecodeError>
```

**Three close codes may never go on a wire.**  1005 (no status
received), 1006 (the connection dropped) and 1015 (the TLS handshake
failed) are values an application reports to its own code, and a peer
that receives one in a close frame is obliged to fail the connection.
A library that hands a caller a plain `Int` invites exactly that
mistake; `WsCloseCode` names all sixteen, `is_sendable` answers the
question, and `wswrite.close_frame` calls it — so the illegal frame
cannot be built, whatever the calling program believed.  The two open
ranges are variants rather than refusals: 3000–3999 is IANA's for
libraries and 4000–4999 is the application's, and a codec that refused
them would be refusing the half of the space designed for its callers.

**The mask is directional, and the direction is a parameter.**  Every
client frame is masked and every server frame is not (RFC 6455 § 5.1),
and a server that accepted an unmasked frame is the cache-poisoning
hole the mask exists to close.  So `WsRole` is an argument to
`decode_header` rather than a field on a long-lived object: a proxy
holds both halves of a connection and must not be able to mix them up
by holding one setting.

The third shape is the pair of events a message produces:

```novo
pub enum WsEvent
    DataFrame(frame: WsFrame)
    DataMessage(msg: WsMessage)
    ControlPing(payload: [u8])
    ControlPong(payload: [u8])
    PeerClose(close: WsClose)
```

RFC 6455 § 5.4 lets a sender split a message across any number of
frames and lets control frames arrive between the pieces.  A caller
handed only frames writes that reassembly itself, badly, once per
program; a caller streaming a 100 MB upload must not be handed a buffer
of the whole thing.  So the reader does both, and `WsLimits.max_message
= 0` turns reassembly off for the second kind of caller.  The three
control events are separate variants rather than frames a caller
inspects, because they are the three things an endpoint MUST act on and
burying them in a general frame event is how a program ends up never
answering a ping.

## Taking a stream from the host

`drain` is the one function in the package with an effect row, and the
row is a parameter rather than an effect:

```novo
pub fn drain<S: Read[e]>(r: WsReader, src: S) -> Result<WsDrained, WsDecodeError> [e]
```

It binds the standard library's `Read` effect parameter, so it costs
whatever the caller's source costs — `[io, net]` against a
`TcpStream`, nothing against an in-memory `Buffer` (SPEC § 5.6).
`tests/wsread_tests.nv` asserts that over a `Buffer`, and the
assertion is made by the compiler before the run starts.

## What it depends on, and why

- **http-codec-nv** — the handshake IS an HTTP/1.1 exchange (RFC 6455
  § 4), so `check_upgrade_request` takes an `H1Request` and
  `upgrade_response` returns an `H1Response`.  A second set of header
  types invented here would have meant a server parsing its handshake
  request twice, with two parsers that eventually disagree.  Same
  layer, so a consumer takes on no new effects.
- **crypto-nv** — SHA-1, for the one line of § 4.2.2 that is arithmetic
  rather than string handling.  SHA-1 is broken for every purpose that
  depends on collisions being hard and this is not one of them: the
  accept value proves the far end read the request and knows the RFC,
  which is enough to stop a cache or a plain HTTP server from being
  mistaken for an endpoint, and enough for nothing else.

## What this does not do, on purpose

- **It does not negotiate extensions.**  `permessage-deflate` is the
  one people want, and it belongs in a package of its own over this
  one: it changes what RSV1 means, and a codec that half-supported it
  would accept frames it cannot decode.  Until then a set reserved bit
  is `ReservedBitSet`, which is what a conforming endpoint that
  negotiated nothing must do.
- **It does not choose a subprotocol.**  `check_upgrade_request` hands
  the server the list the client offered; picking from it is policy,
  and a codec that picked would be picking for every application built
  on it.
- **It does not check the `Origin`.**  Same reason.
- **It does not decode text to `Str`.**  A text frame's payload is
  bytes, because RFC 6455 § 8.1 requires an endpoint to fail the
  connection with close code 1007 on invalid UTF-8 — so the invalid
  case has to be representable long enough to be reported.
  `wsread.is_valid_utf8` is the check, and it rejects surrogates and
  overlong forms, which a naive check accepts and the Autobahn suite
  tests for.

## What `std.ws` keeps

`std.ws` is forty lines: a `WebSocket` struct holding a session id, and
six methods that hand a `Str` or a `Bytes` to the runtime — `connect`,
`send`, `recv`, `send_bytes`, `recv_bytes`, `close`, every one of them
`[io, net]`.  There is no frame type in it, no close code, no
fragmentation and no handshake, because the framing happens outside
novo-lang entirely.

That surface stays exactly as it is.  It is the two-line client a
program wants when it is talking to one server it trusts, and nothing
in this package makes it worse.  What this package is for is everything
`std.ws` cannot express: a server, a proxy, a fuzzer, a device, an
endpoint that has to answer a ping within a deadline, and any program
that needs to know why a connection was closed rather than only that it
was.

## The reference implementation

`tungstenite`'s framing half.  Its `Frame` / `FrameHeader` split, its
insistence that a length be written in the minimal form, and its
`WebSocketContext` state machine are the three shapes this package
takes.  RFC 6455 is the specification, its § 1.3 and § 5.7 are the
source of the test vectors, and the Autobahn Testsuite is the oracle
the implementation will be measured against.

Three things change in the port.  `tungstenite`'s one `Error` enum
becomes three types — `WsDecodeError`, `WsEncodeError`,
`WsHandshakeError` — because they have three different audiences and
three different answers: a close code, a bug report, and an HTTP
status.  Its `Message::Text(String)` becomes `TextMessage([u8])`, so
that invalid UTF-8 can be reported rather than being unrepresentable.
And the random mask keys it generates internally become an argument,
which is what a `core` package with no `[rand]` requires and what makes
fragmentation testable.

## Status

| item | implemented |
| --- | --- |
| `wsframe` — `WsOpcode`, `WsRole`, `WsFrameHeader`, `WsFrame`, `WsScan` | types only |
| `wsframe.CONTROL_PAYLOAD_MAX`, `.MASK_KEY_LEN` | yes — they are constants |
| `wsframe.opcode_number`, `.opcode_of`, `.is_control`, `.header_len`, `.minimal_length_form` | no |
| `wsframe.scan`, `.encode_header`, `.decode_header`, `.mask`, `.encode`, `.decode` | no |
| `wsclose` — `WsCloseCode`, `WsClose` | types only |
| `wsclose.CLOSE_REASON_MAX` | yes — it is a constant |
| `wsclose.number_of`, `.code_of`, `.is_sendable`, `.close_code_for`, `.encode_payload`, `.decode_payload` | no |
| `wshandshake.WS_GUID`, `.WS_VERSION`, `.NONCE_LEN` | yes — they are constants |
| `wshandshake` — `WsUpgrade` | type only |
| `wshandshake.accept_key`, `.client_key`, `.is_valid_key` | no |
| `wshandshake.upgrade_request`, `.check_upgrade_request`, `.upgrade_response`, `.check_upgrade_response` | no |
| `wshandshake.version_refusal`, `.is_websocket_upgrade` | no |
| `wsread` — `WsLimits`, `WsEvent`, `WsMessage`, `WsStep`, `WsReader`, `WsDrained` | types only |
| `wsread.default_limits`, `.embedded_limits`, `.reader`, `.feed`, `.take` | no |
| `wsread.pending_len`, `.assembling_len`, `.message_open`, `.closed`, `.is_valid_utf8` | no |
| `wsread.drain` | no |
| `wswrite.frame_count`, `.message_frames`, `.bytes` | no |
| `wswrite.text`, `.binary`, `.ping`, `.pong`, `.close_frame`, `.continuation`, `.fail_connection` | no |
| `wserr` — `WsDecodeError`, `WsEncodeError`, `WsHandshakeError`, `.http_status_for`, the three `message` impls | types only |
