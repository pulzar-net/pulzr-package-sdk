---
title: "Build a gateway package"
description: "A gateway support package brings another company's gateway onto the Pulzr platform: its owner adopts it like any gateway, its devices' readings arrive like any device's, and the console manages it thr"
---

A gateway support package brings another company's gateway onto the Pulzr
platform: its owner adopts it like any gateway, its devices' readings arrive
like any device's, and the console manages it through the capabilities the
package declares. The package is a repository you own; the platform installs
a release of it into an organization, and runs its adapter, a WebAssembly
module, in its sandbox. Adding a gateway type needs no platform release.

The worked example is [`examples/beacon-gateway`](https://github.com/pulzar-net/pulzr-package-sdk/tree/main/examples/beacon-gateway),
a made-up beacon gateway that speaks JSON over MQTT.

## Start

```bash
pipx install pulzr-pkg
rustup target add wasm32-unknown-unknown
pulzr-pkg new gateway acme-gateway
cd acme-gateway
pulzr-pkg build && pulzr-pkg test
```

| File | |
|---|---|
| `gateway.json` | the manifest: the gateway, how it is adopted, its broker topics, its management capabilities, and the adapter module |
| `commands.json` | the intents the adapter answers |
| `settings.schema.json`, `state.schema.json` | the gateway's settings and facts, as JSON Schema, when it declares those capabilities |
| `src/lib.rs` | the adapter |
| `tests/*.jsonl` | events and the answers they must get, run by `pulzr-pkg test` |
| `.github/workflows/release.yml` | builds, checks and tests every push; releases a tag |

## The manifest

What each key means is the platform's gateway package specification; the ones
you set first:

- `broker.gateway`: the topics the gateway's own login may publish and
  subscribe to, each naming the gateway (`{mac}`, `{eui}` or `{identity}`), so
  one gateway is never granted another's.
- `broker.adapter`: what the adapter hears (`subscribe`) and may send
  (`publish`). These are the vendor messages your module gets and gives.
- `adapter.prefix`: the name the adapter speaks as, `<prefix>-<gateway EUI>`,
  unique across packages.
- `adapter.limits`: fuel and memory per call ([abi.md](/pulzr-package-sdk/abi.html)).
- `adapter.imports`: `log`, `now`, `state`; never `http` (a gateway's traffic is
  answered in line with every other gateway's).

## Events and answers

Your module gets one event per call ([`Event`](https://github.com/pulzar-net/pulzr-package-sdk/blob/main/guest/src/lib.rs)):

- `message`: a vendor message on one of `broker.adapter`'s subscriptions, with
  its topic, payload and the time it was received.
- `intent`: a protocol v1 intent for one of your gateways or a device behind
  one, with its `id`, `name` and `params` (secret params opened for this
  call), its `expires` and the time now.
- `tick`: about every ten seconds.

and answers with ([`Answer`](https://github.com/pulzar-net/pulzr-package-sdk/blob/main/guest/src/lib.rs)):

- `protocol`: protocol v1 messages: `up` for a device's reading or frame,
  `state` and `settings` for the gateway itself (`gw:<EUI>`), `event`, and
  `cmd/res` for each intent: `sent` when it went to the gateway, then
  `delivered`, `ok`, `refused` or `failed` as the gateway answers.
- `publish`: vendor messages, on topics `broker.adapter` lets you publish on.
- `log`: lines for the platform's log.

**Matching without memory.** Carry the intent's `id` through the vendor's
correlation field (`correlation::seq_for_id` for an integer one), and read it
back from the vendor's answer (`correlation::id_for_seq`): your module then
keeps nothing about what is in flight, and losing its instance loses nothing.

**What the platform enforces**, whatever the module says: a vendor message
goes only to the instance of the organization whose gateway its topic names;
`protocol` messages are taken only for that organization's gateways, checked
against the protocol's schema; `publish` messages go only to those gateways'
own topics. `pulzr-pkg test` checks the same, so what passes there passes in
the platform.

## Test

Each line of a `tests/*.jsonl` file is one call:

```json
{"name": "a reboot is sent with the id as its seq",
 "event": {"event": "intent", "adapter": "exbg-aabbccfffeddeeff", "key": "gw:aabbccfffeddeeff",
           "id": "10000042", "name": "reboot", "params": {}, "expires": null, "now": 1795000000.0},
 "expect": {"publish": [{"topic": "example/down/AABBCCDDEEFF", "payload": "eyJjbWQiOiJyZWJvb3QiLCJzZXEiOjEwMDAwMDQyfQ=="}],
            "protocol": [{"adapter": "exbg-aabbccfffeddeeff", "key": "gw:aabbccfffeddeeff", "kind": "cmd/res",
                          "body": {"v": 1, "id": "10000042", "status": "sent"}}]}}
```

`expect` is the whole answer; `expect_contains` only what it must include.
`install` names the instance (a new name is a fresh one, as after a restart).
Every answer is also checked as the platform would check it.

## Release

Tag the manifest's version with the release notes as the tag's message
(`git tag -a v1.0.0 --cleanup=whitespace -F notes.md`) and push: the workflow builds the module,
writes its `sha256` into the manifest, checks and tests it, and releases the
manifest, `commands.json`, the schemas and the module. Pulzr staff add the
release to the platform's catalog by its tag, through the Pulzr GitHub App
installed on your repository (it reads releases, nothing else); an
organization's admin then installs it.
