# Changelog

Every published version, newest first. This file is on the publish
allow-list, so it travels with the package: it is the only thing a
consumer deciding whether to upgrade can read.

## 0.0.1 — 2026-09-09

The **interface**, before anyone implements it. Every signature, every
type and every effect row is published; every body is `todo()`, and the
release is stamped `NOT IMPLEMENTED — interface only`. Adding this
package works and calling it panics.

- `WsFrameHeader`, `WsFrame`, `WsOpcode` and `WsScan` — RFC 6455 § 5's
  header as values, with the length's minimal-form rule as
  `minimal_length_form` and a refusal rather than a tolerance for a
  length written longer than it needed.
- `WsRole` as an argument rather than a field, because the mask rule is
  directional and a proxy holds both halves of a connection.
- `WsCloseCode` with all sixteen assigned numbers and the two open
  ranges, and `is_sendable` — 1005, 1006 and 1015 may never go on a
  wire. `wswrite.close_frame` calls it, so the frame that obliges a
  peer to fail the connection cannot be built.
- `WsEvent` with both `DataFrame` and `DataMessage`, and
  `WsLimits.max_message = 0` to turn reassembly off: one caller wants
  the pieces as they land, the other wants the message, and both are
  real.
- The handshake as a pure computation. `accept_key` is the one line
  that is arithmetic; the nonce and every mask key arrive as arguments,
  which keeps `[rand]` off a `core` package and makes the handshake
  assertable against RFC 6455 § 1.3's own example. `frame_count` says
  how many mask keys a fragmentation will need before you have to make
  them.
- `WsDecodeError`, `WsEncodeError` and `WsHandshakeError` as three
  types, because they have three audiences and three answers: a close
  code, a bug report, and an HTTP status. `wsclose.close_code_for` is
  the first of those answers, so an endpoint does not send 1002 for a
  UTF-8 fault the RFC assigns 1007.
- `drain`, the one function with an effect row, which binds the
  standard library's `Read[e]`.

Two dependencies, both `core`. **http-codec-nv**, because the handshake
is an HTTP/1.1 exchange and spelling it in a second set of header types
would mean a server parsing its request twice. **crypto-nv**, for
SHA-1.

One toolchain defect travels with the release rather than being
designed around. There is **no `tests/embedded_probe.nv`**: `Result<T,
E>` cannot be spelled at `@tier(embedded)`, which is
`result-is-unusable-at-tier-embedded-no-error-trait`, open against the
toolchain. The signatures keep their `Result`.
