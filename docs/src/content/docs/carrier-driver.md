---
title: "Build a carrier driver"
description: "A carrier driver connects the platform to a cellular carrier's API: its manifest, the operations the platform asks of it, and its answers."
---

A carrier driver connects the platform to a cellular carrier's API. It is an
extension module like a gateway package's adapter ([abi.md](/pulzr-package-sdk/abi.html)),
built, tested and released the same way. The first is
[connectivity-1nce](https://github.com/pulzar-net/connectivity-1nce).

```bash
pulzr-pkg new carrier acme-carrier
cd acme-carrier
pulzr-pkg build && pulzr-pkg validate && pulzr-pkg test
```

An organization adds a carrier from the platform's catalog and fills in the
driver's `credentials` form. The platform proves them with the driver's own
`check`, seals them, and runs the module with one install per provider: an
organization may add the same carrier twice (two accounts), and the two never
share state, cached responses or an instance.

## The manifest, `driver.json`

Checked against [`schema/carrier.schema.json`](https://github.com/pulzar-net/pulzr-package-sdk/blob/main/python/pulzr_pkg/schema/carrier.schema.json).

| Key | |
|---|---|
| `id`, `name`, `version` | the catalog's id, the name shown, the release (its tag is `v<version>`) |
| `kind` | `cellular` (SIMs) or `network` |
| `description`, `homepage` | shown in the catalog |
| `logo`, `logoDark` | files in the repository, SVG or PNG, released with the module; an SVG may hold shapes only, nothing that runs or reaches out |
| `capabilities` | what the driver does ([`schema/connectivity.schema.json`](https://github.com/pulzar-net/pulzr-package-sdk/blob/main/python/pulzr_pkg/schema/connectivity.schema.json) `Capabilities`); the platform asks only for what is declared |
| `config` | a JSON Schema object: the provider's settings, not secret, handed to every event |
| `driver` | the module object, with `http.hosts` and `credentials` required |

```json
"driver": {
  "runtime": "wasm", "module": "driver.wasm", "sha256": "...",
  "imports": ["http", "state"],
  "http": {"hosts": ["api.example.com"]},
  "credentials": {"type": "object", "required": ["api_key"],
                  "properties": {"api_key": {"type": "string", "title": "API key", "writeOnly": true}}}
}
```

- `http.hosts`: the only hosts it reaches, HTTPS only.
- `credentials`: a JSON Schema of the form an organization's admin fills in
  (`writeOnly` or `format: password` marks a secret). A request names a field
  as `{{credential.api_key}}` (in a header or the body, never the URL), or
  `{{basic:user:password}}` for HTTP Basic, and the platform fills it in: the
  driver never holds a secret, and one echoed in a response is replaced before
  the driver reads it. `Request::bearer("api_key")` writes the usual header.
- `state`, for what it must keep between calls: an OAuth token for its
  lifetime is `host::state_set("token", token.as_bytes(), expires_in)`.
- Fleets: `host::http_all` makes up to 128 requests at once; a `GET` can be
  `.cached(seconds)`, and an answer's `invalidate` drops what a change made
  stale.

## Operations

Each event is one operation, arriving as
[`Event::Other`](https://github.com/pulzar-net/pulzr-package-sdk/blob/main/guest/src/lib.rs):

```json
{"op": "sim", "args": {"iccid": "8988..."}, "config": {}, "now": 1795619000}
```

| `op` | `args` | Capability | Result |
|---|---|---|---|
| `check` | | | the capability names the credentials actually give |
| `list_sims` | `cursor`, `pageSize` | `sim.inventory` | `Page` |
| `sim` | `iccid` | `sim.status` | `Sim` |
| `usage` | `iccid`, `start`, `end` (dates) | `sim.usage` | `[Day]` |
| `set_state` | `iccid`, `active` | `sim.suspend` / `sim.activate` | `Sim` |
| `topup` | `iccid`, `mb` | `sim.topup` | `{}` |
| `reset` | `iccid` | `sim.reset` | `{}` |
| `apn_profile` | | `apn.profile` | `ApnProfile` |
| `update` | `iccid`, `changes` | | `Sim` |
| `quota` | `iccid`, `kind` | | the carrier's allowance |
| `statuses` | `iccids` | | `{iccid: state or null}` |
| `connection`, `cell`, `events` | `iccid` | | live detail |
| `prices` | | | what SIMs and top-ups cost |

The shapes are `connectivity.schema.json`'s. The platform refuses an operation
whose capability the manifest does not declare before asking the module; one a
driver does not implement answers status 501. `apn_profile` is asked without
credentials.

## Answers

In the `Answer`'s `extra` members:

```json
{"ok": <result>, "invalidate": ["https://..."]}
{"error": {"message": "...", "status": 404}}
```

An `error` is the carrier's or the driver's refusal and reaches the caller
with its status (400 to 599); it is not a failure of the module and never
rests it. `pulzr-pkg test` checks every answer this way.

## Testing

`pulzr-pkg test` runs `tests/*.jsonl` through the platform's host. A test can
give its install a credential (`"credentials": {"api_key": "test-key"}`);
requests in tests reach the real network only for the declared hosts, so test
the parsing with recorded answers in plain Rust tests, and keep
`tests/*.jsonl` for the operations' shapes.

## Releases

A `v<version>` tag, annotated with its release notes, releases `driver.json`
(with `driver.sha256` filled in), the module and the logos. The platform's
catalog adds a release by its tag and checks it whole: the manifest against
the schema, the module against its sha256 and the ABI, the logos.
