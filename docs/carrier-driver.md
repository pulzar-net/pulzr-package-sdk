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
  "imports": ["http"],
  "http": {"hosts": ["api.example.com"]},
  "credentials": [{"name": "api", "label": "API key",
                   "inject": {"header": "Authorization", "format": "Bearer {secret}"}}]
}
```

- `http.hosts`: the only hosts it reaches, HTTPS only.
- `credentials`: what an organization's admin enters when installing the
  driver. The platform keeps it sealed and adds it to a request that names it
  (`Request::with_credential("api")`); the driver never sees it, and cannot
  send it anywhere but a declared host.
- `state` (with `limits.stateKiB`), if it must remember something between
  calls, such as a token's expiry.

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
