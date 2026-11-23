# pulzr-pkg

The CLI that starts, builds, validates and tests Pulzr extension modules
(gateway support packages and carrier drivers), and `pulzr_host`, the
WebAssembly host the platform runs them in, so a module's tests here run it
exactly as the platform will.

```bash
pipx install pulzr-pkg
pulzr-pkg new gateway acme-gateway
cd acme-gateway && pulzr-pkg build && pulzr-pkg test
```

Modules are written in Rust with the [`pulzr-package`](https://crates.io/crates/pulzr-package)
crate, or in any language that compiles to WebAssembly against the
[ABI](https://oss.pulzr.net/pulzr-package-sdk/abi.html). Guides:
https://oss.pulzr.net/pulzr-package-sdk/. Part of
[pulzr-package-sdk](https://github.com/pulzar-net/pulzr-package-sdk), maintained by
[Pulzr](https://pulzr.net). License: MIT.
