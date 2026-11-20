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
  "credentials": {"type": "object", "required": ["api_key"],
                  "properties": {"api_key": {"type": "string", "title": "API key", "writeOnly": true}}} }
```

| Key | |
|---|---|
| `module`, `sha256` | the release asset and its SHA-256; an asset that does not match is refused |
| `limits.fuel` | per call, about one unit per WebAssembly instruction; each service caps it (adapter-ingest: 100 million, about 65 ms of a core; the connectivity service: 2 billion) |
| `limits.memoryMiB` | linear memory; growing past it fails (`memory.grow` gives -1) |
| `limits.stateKiB` | the most `state` an install may hold, all keys together; default 64, at most 1024 |
| `imports` | the host functions the module may import. One it imports but did not declare is refused at install |
| `http.hosts` | the only hosts `http` reaches, each a name or `*.` and a name; HTTPS only |
| `credentials` | a JSON Schema object: the form an admin fills in when installing the module, validated against it. A property with `writeOnly` or `format: password` is secret, shown once and sealed. The module names a property in a request as a placeholder the host fills in; it never holds one |

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
| `state_set` (`"state"`) | `(key_ptr: i32, key_len: i32, val_ptr: i32, val_len: i32, ttl_s: i32) -> i32` | 0, or -1 past `stateKiB`; kept `ttl_s` seconds (0: until replaced); an empty value deletes the key |
| `http` (`"http"`) | `(req_ptr: i32, req_len: i32) -> i32` | makes a request, or an array of them at once, and returns the length of the response (or the array of responses), or -1 past the call's request limit |
| `http_take` (`"http"`) | `(out_ptr: i32)` | copies that response to `out_ptr`, which has room for the length `http` returned |

**State** is per install: the caller's key for everything kept apart (a
gateway package's organization and package; a carrier driver's provider, since
one organization may add the same carrier twice), kept across the module's
versions and the host's restarts. Keys are 1 to 64 octets; what a module
must keep between calls (an OAuth token, until its expiry) goes here, since
its memory may be lost at any time.

**http** requests and responses are JSON:

```json
{ "method": "POST", "url": "https://api.example.com/v1/sims/123/suspend",
  "headers": {"authorization": "Bearer {{credential.api_key}}", "content-type": "application/json"},
  "body": "{\"reason\": \"lost\"}", "cache": 0, "timeout_ms": 5000 }
```
```json
{ "status": 200, "headers": {"content-type": "application/json"}, "body": "...", "cached": false }
{ "status": 0, "error": "only api.example.com, over HTTPS" }
```

- HTTPS only, to a host `http.hosts` names, redirects not followed, `GET`,
  `POST`, `PUT`, `PATCH` and `DELETE`. Bodies are text.
- `{{credential.<name>}}` and `{{basic:<user name>:<password name>}}` (the HTTP
  Basic value of two fields) are filled in, in headers and the body only; a
  placeholder in a URL is refused, since URLs are logged. A response that
  echoes a credential has it replaced with `[credential]` before the module
  reads it.
- A `GET` with `"cache": <seconds>` (at most 3600) may be answered from the
  host's cache, per install; an answer member `"invalidate": [url, ...]`
  drops what a change made stale.
- An array is made concurrently: at most 128 requests in one, 512 per call,
  15 s each (less with `timeout_ms`), a response body of at most 4 MiB.
- A refused or failed request is answered (`status` 0 and `error`), never a
  trap.

Waiting on the network is not fuel, but it is the call's time: a kind whose
events must be answered quickly (a gateway's traffic) says in its own document
whether `http` is allowed for it.

## Metering

The host meters every call, per install, module version and UTC day, labelled
with the organization and package the caller names, so usage rolls up by
organization, package, version and day: calls, fuel used and the CPU time it took, peak linear
memory, traps and of them timeouts (fuel run out), `http` requests made,
answered from cache and timed out, octets sent and received, and the state an
install holds. The platform keeps these with its other usage records: they say
what a module costs, show a runaway one, and are what a price set for a
package is applied to.

## Failure

A call that traps, runs out of fuel or memory, or answers with something that
is not its kind's object is logged with the event's kind (never its payload)
and dropped; the instance is made again. A module that fails five calls in a
row (traps or malformed answers; an answer that reports an error is still an
answer) rests for a minute. What it holds in its memory may be lost at any time;
what must survive goes in `state`.

## Tenancy

The host keeps one instance per install and module version, so two
installs (two organizations, or one organization's two accounts with the same
carrier) never share a module's memory, state, cache or credentials. Which organization an
event is for, and which of its answers the host accepts, is each kind's to
say and the host's to enforce, never the module's.

## Cost

Fuel bounds a call's CPU. Measured on an Apple M1 core with wasmtime 50, for
a module that spends every unit (a loop), including making its instance again
after the trap:

| Fuel | One exhausted call |
|---|---|
| 1 million | 0.8 ms |
| 50 million (the default) | 33 ms |
| 100 million (adapter-ingest's cap) | about 65 ms |

A call that does its work costs about 0.04 ms of host time on top of the
module's own.
