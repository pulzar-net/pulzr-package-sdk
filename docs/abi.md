# The extension ABI, version 2

The contract between the Pulzr platform and an extension module, for any
language that compiles to WebAssembly. `pulzr-package` (Rust, `guest/`)
implements the module's side and `pulzr_host` (Python, `python/`) the
platform's; this document is what both are written to. It is published from
the platform's protocol repository, where its source of truth lives.

Code a third party adds to the platform runs as a WebAssembly module in the
platform's sandbox, never as a service of its own: a gateway support
package's adapter ([gateway-package.md](gateway-package.md)), a carrier driver, and whatever kind comes
next. This is the contract every kind shares: how a module is described in
its manifest, what it exports, what the host lends it, and what happens when
it fails. Each kind adds only its events and its answer ([gateway-package.md](gateway-package.md) for gateway packages).

A module is a WebAssembly core module (`wasm32`), written in any language
that compiles to one; `pulzr-package` (Rust) hides the plumbing below, and
nothing here depends on it.

Each kind's events and answers: [gateway packages](gateway-package.md#events-and-answers), [carrier drivers](carrier-driver.md).

## In the manifest

Each kind's manifest holds one module object (`adapter` for a gateway
package), validated by `schema/extension.schema.json#/$defs/module`:

```json
{ "runtime": "wasm", "module": "adapter.wasm",
  "sha256": "9f2c1a7e5b3d4c6f8a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f",
  "limits": {"fuel": 50000000, "memoryMiB": 32, "stateKiB": 64},
  "imports": ["log", "now", "state", "http"],
  "http": {"hosts": ["api.example.com"]},
  "credentials": [{"name": "api", "label": "API key",
                   "inject": {"header": "Authorization", "format": "Bearer {secret}"}}] }
```

| Key | |
|---|---|
| `module`, `sha256` | the release asset and its SHA-256; an asset that does not match is refused |
| `limits.fuel` | per call, about one unit per WebAssembly instruction; the host caps it (adapter-ingest: 100 million, about 65 ms of a core) |
| `limits.memoryMiB` | linear memory; growing past it fails (`memory.grow` gives -1) |
| `limits.stateKiB` | the most `state` an install may hold, all keys together; default 64, at most 1024 |
| `imports` | the host functions the module may import. One it imports but did not declare is refused at install |
| `http.hosts` | the only hosts `http` reaches, each a name or `*.` and a name; HTTPS only |
| `credentials` | secrets an organization enters when it installs the module, sealed by the platform. `http` injects one by name into a request (`inject.header`, its value `inject.format` with `{secret}` or `{secret_b64}` filled in); the module never sees a secret |

## Exports

| Export | Signature | |
|---|---|---|
| `memory` | | its linear memory |
| `pz_abi` | `() -> i32` | `2` (or `1`: a module of ABI 1 imports nothing and is run the same way) |
| `pz_alloc` | `(len: i32) -> i32` | room for `len` octets the host writes into: an event, or a host call's answer |
| `pz_handle` | `(ptr: i32, len: i32) -> i64` | takes the event at `ptr` (and frees it) and returns its answer as `(answer_ptr << 32) \| answer_len`; the answer stays readable until the next call |

An event and an answer are each one JSON object, UTF-8. Every member of an
answer is optional, and every kind's answer may carry `log`:
`[{"level": "debug|info|warning|error", "message": "..."}]`.

## Host functions (module `pz`)

Each is linked only when the manifest's `imports` names it; there is no other
import. Pointers and lengths are into the module's memory; text is UTF-8.

| Import | Signature | |
|---|---|---|
| `log` (`"log"`) | `(level: i32, ptr: i32, len: i32)` | a line for the platform's log under the module's name, now rather than with the answer; level 0 debug, 1 info, 2 warning, 3 error |
| `now` (`"now"`) | `() -> f64` | seconds since 1970 UTC |
| `state_get` (`"state"`) | `(key_ptr: i32, key_len: i32, out_ptr: i32, out_cap: i32) -> i32` | the value's length, written to `out_ptr` when it fits in `out_cap` (call again with room otherwise); -1 for no value |
| `state_set` (`"state"`) | `(key_ptr: i32, key_len: i32, val_ptr: i32, val_len: i32) -> i32` | 0, or -1 past `stateKiB`; an empty value deletes the key |
| `http` (`"http"`) | `(req_ptr: i32, req_len: i32) -> i64` | the response, written into room the host takes with `pz_alloc`, as `(ptr << 32) \| len` |

**State** is per install: one organization's, for one package, kept across
its versions and the host's restarts. Keys are at most 64 octets.

**http** request and response:

```json
{ "method": "POST", "url": "https://api.example.com/v1/sims/123/suspend",
  "headers": {"content-type": "application/json"}, "body": "<base64>",
  "credential": "api", "timeout_ms": 5000 }
```
```json
{ "status": 200, "headers": {"content-type": "application/json"}, "body": "<base64>" }
{ "error": "host not declared" }
```

HTTPS only, to a host `http.hosts` names, redirects not followed. At most 8
requests per call, 10 s each (less if `timeout_ms` says so), a response body
of at most 1 MiB. Time spent waiting on the network is not fuel, but it is
the call's: a kind whose events must be answered quickly (a gateway's
traffic) says in its own document whether `http` is allowed for it.

## Failure

A call that traps, runs out of fuel or memory, or answers with something that
is not its kind's object is logged with the event's kind (never its payload)
and dropped; the instance is made again. A module that fails five calls in a
row rests for a minute. What it holds in its memory may be lost at any time;
what must survive goes in `state`.

## Tenancy

The host keeps one instance per organization and module version, so two
organizations never share a module's memory or state. Which organization an
event is for, and which of its answers the host accepts, is each kind's to
say and the host's to enforce, never the module's.

## Cost

A call runs on the host's worker, so a module's CPU is bounded per call by
its fuel, capped by the platform at 100 million units. Measured on an Apple M1
core with wasmtime 50, for a module that spends every unit (a loop), including
making its instance again after the trap:

| Fuel | One exhausted call |
|---|---|
| 1 million | 0.8 ms |
| 50 million (the default) | 33 ms |
| 100 million (the cap) | about 65 ms |

A call that does its work costs about 0.04 ms of host time on top of the
module's own.
