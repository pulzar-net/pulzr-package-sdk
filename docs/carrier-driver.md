# Build a carrier driver

A carrier driver connects the platform to a cellular carrier's API: the
platform calls it for what it needs from the carrier (the calls are the
carrier-driver kind's, below), and the driver answers with the carrier's own
API, over `pz.http`. It is an extension module like a gateway package's
adapter ([abi.md](abi.md)), and is built, tested and released the same way.

```bash
pulzr-pkg new carrier acme-carrier
cd acme-carrier
pulzr-pkg build && pulzr-pkg test
```

## What it may reach

`driver.json` holds the module object, and declares everything the driver
needs from the platform:

```json
"driver": {
  "runtime": "wasm", "module": "driver.wasm", "sha256": "...",
  "imports": ["http", "state"],
  "http": {"hosts": ["api.example.com"]},
  "credentials": [{"name": "api_key", "label": "API key", "secret": true}]
}
```

- `http.hosts`: the only hosts it reaches, HTTPS only.
- `credentials`: the form an organization's admin fills in when installing
  the driver. A request names a field as `{{credential.api_key}}` (in a header
  or the body, never the URL), or `{{basic:user:password}}` for HTTP Basic, and
  the platform fills it in: the driver never holds a secret, and one echoed in
  a response is replaced before the driver reads it. `Request::bearer("api_key")`
  writes the usual header.
- `state`, for what it must keep between calls: an OAuth token for its
  lifetime is `host::state_set("token", token.as_bytes(), expires_in)`.
- Fleets: `host::http_all` makes up to 128 requests at once; a `GET` can be
  `.cached(seconds)`, and an answer's `invalidate` drops what a change made
  stale.

## Calls

The carrier-driver kind's events (its calls) and its answers are defined by
the platform's connectivity service, which runs drivers; they arrive in your
module as [`Event::Other`](../guest/src/lib.rs) and go back in an
[`Answer`](../guest/src/lib.rs)'s `extra` members. Each call is one
organization's: a driver instance never sees another organization's calls,
state or credentials.

## Testing

`pulzr-pkg test` runs `tests/*.jsonl` through the platform's host. A test can
give its install a credential (`"credentials": {"api": "test-key"}`); requests
in tests reach the real network only for the declared hosts, so prefer
recorded answers in your own tests of the parsing logic (plain Rust tests of
your functions), and keep `tests/*.jsonl` for the calls' shapes.
