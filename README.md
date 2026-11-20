# pulzr-package-sdk

Build extension modules for the Pulzr platform: code you add to it, run as
sandboxed WebAssembly, never as a service of your own.

- **Gateway support packages** bring another company's gateway onto the
  platform: adopted, its devices' readings arriving like any device's,
  managed through the capabilities you declare.
- **Carrier drivers** connect the platform to a cellular carrier's API.

| | |
|---|---|
| [The ABI](https://pulzar-net.github.io/pulzr-package-sdk/abi.html) | the ABI, for any language that compiles to WebAssembly |
| [Build a gateway package](https://pulzar-net.github.io/pulzr-package-sdk/gateway-package.html) | a guide, with a worked example |
| [Build a carrier driver](https://pulzar-net.github.io/pulzr-package-sdk/carrier-driver.html) | a guide |
| [`guest/`](guest) | `pulzr-package`, the Rust crate a module is written with |
| [`python/`](python) | `pulzr_host`, the host the platform runs modules in, and `pulzr-pkg`, the CLI |
| [`examples/beacon-gateway`](examples/beacon-gateway) | a worked gateway package |
| [`action.yml`](action.yml) | the GitHub Action that builds, checks, tests and releases a module |

## A module in Rust

<!-- ANCHOR: module -->
```rust
use pulzr_package::{export, Answer, Event, Package};

#[derive(Default)]
struct Adapter;

impl Package for Adapter {
    fn handle(&mut self, event: Event) -> Answer {
        let mut answer = Answer::default();
        if let Event::Message { topic, .. } = event {
            answer.log_info(format!("heard {topic}"));
        }
        answer
    }
}

export!(Adapter);
```
<!-- ANCHOR_END: module -->

## pulzr-pkg

<!-- ANCHOR: pkg -->
```bash
pip install "git+https://github.com/pulzar-net/pulzr-package-sdk@v0.1.0#subdirectory=python"
rustup target add wasm32-unknown-unknown

pulzr-pkg new gateway acme-gateway   # or: new carrier acme-carrier
cd acme-gateway
pulzr-pkg build      # cargo build for wasm32; the module into dist/, its sha256 into the manifest
pulzr-pkg validate   # the manifest, the module's digest, imports, exports and limits
pulzr-pkg test       # tests/*.jsonl through the platform's own host
```
<!-- ANCHOR_END: pkg -->

`pulzr-pkg test` runs your module in `pulzr_host`, the same host the
platform runs it in, and checks every answer as the platform would (the
protocol's schema, your manifest's topics), so what passes here passes there.

## Releasing

```yaml
- uses: pulzar-net/pulzr-package-sdk@v0.1.0
  with:
    release: ${{ startsWith(github.ref, 'refs/tags/v') }}
```

On a tag `v<the manifest's version>`, the release carries exactly what the
platform installs from: the manifest, its schemas and commands, and the
module.

## The host

`pulzr_host` is what the platform's services import to run modules: one
instance per install, the fuel and memory limits, the `pz` host functions
(`http` only to declared hosts, credentials filled in by the host and hidden
from the module, a GET cache, batches), and metering per install, module and
day. Each kind's routing (which install an event is for, which answers are
taken) is the calling service's.

```python
import pulzr_host

host = pulzr_host.Host(max_fuel=100_000_000)      # the service's own cap
module = host.load(wasm, manifest['adapter'], name='acme-gateway 1.0.0')
instance = module.instance('org-a/acme-gateway', state=store, cache=cache)
answer = instance.call({'event': 'tick', 'now': 1795000000.0}, credentials={'api_key': '...'})
for install, module_name, day, usage in host.usage.drain():
    ...                                            # into the service's metering
```

The schemas in `python/pulzr_pkg/schema/` are the Pulzr protocol's
(`PROTOCOL_COMMIT` names the version).

## License

MIT.
