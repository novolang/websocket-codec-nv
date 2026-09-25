# Changelog

Every published version, newest first. This file is on the publish
allow-list, so it travels with the package: it is the only thing a
consumer deciding whether to upgrade can read.

## 0.0.5 — 2026-09-25

The package builds with novo 0.11.  Every body is still `todo()`.

- The lock file moves crypto-nv 0.1.1 to 0.1.6.  crypto-nv 0.1.1 writes
  into lists through names that are not declared `var`, which novo 0.11
  refuses (E2038), so this package did not build with novo 0.11 against
  it.  No requirement in the manifest changed.
- Seven test assertions put `as Int` in parentheses, the form `novo fmt`
  writes.  They assert the same things.

## 0.0.4 — 2026-09-15

README rewritten to the package README style guide (docs/writing-a-readme.md); no change to the interface.

## 0.0.3 — 2026-09-10

- **Toolchain floor is 0.8.9**: the bodies and signatures use what 0.8.9 added (`todo()`, a bound effect parameter, the four layers), and the manifest says so instead of letting an older toolchain fail on an undefined function.  No signature changed.

## 0.0.2 — 2026-09-09

- **Dependencies are registry ranges**, not paths: the interface release 0.0.1 shipped a manifest whose dependencies pointed at sibling directories that exist only in the monorepo, so a consumer resolved the closure and then could not load the dependency.  No signature changed.

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

### Design notes

The reference implementation for the port is `tungstenite`'s framing
half: its `Frame` and `FrameHeader` split, its insistence on the
minimal length form, and its `WebSocketContext` state machine. Three
things change in the crossing. Its one `Error` enum becomes three types,
because a decode fault is answered with a close code, an encode fault is
a bug report, and a handshake fault is an HTTP status. Its
`Message::Text(String)` becomes `TextMessage([u8])`, so invalid UTF-8
can be reported rather than being unrepresentable. Its internally
generated mask keys become an argument, which is what a package with no
`[rand]` requires and what makes fragmentation assertable.
