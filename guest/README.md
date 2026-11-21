# pulzr-package

[![crates.io](https://img.shields.io/crates/v/pulzr-package.svg)](https://crates.io/crates/pulzr-package)
[![docs.rs](https://img.shields.io/docsrs/pulzr-package)](https://docs.rs/pulzr-package)

Write Pulzr extension modules in Rust: gateway support packages and carrier
drivers, run in the platform's WebAssembly sandbox. The crate hides the ABI:
implement `Package`, name your type in `export!`, and build for
`wasm32-unknown-unknown`.

```sh
cargo add pulzr-package
```

`pulzr-pkg new gateway my-gateway` starts a package with it, and
`pulzr-pkg build && pulzr-pkg test` builds it and runs its tests through the
platform's own host. See the [guides](https://oss.pulzr.net/pulzr-package-sdk/)
and the [ABI](https://oss.pulzr.net/pulzr-package-sdk/abi.html).

Part of [pulzr-package-sdk](https://github.com/pulzar-net/pulzr-package-sdk),
maintained by [Pulzar](https://pulzr.net). License: MIT.
