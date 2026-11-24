---
title: "Write a codec"
description: "A device type's codec turns a device's bytes into readings: in JavaScript with the pulzr codec API, or natively in Rust with pulzr-codec-sdk."
---

A device type's codec turns the bytes a device sends into readings. Write it
in JavaScript, in the console's codec editor, or in Rust as a native
WebAssembly module with [`pulzr-codec-sdk`](https://docs.rs/pulzr-codec-sdk).
Both run sandboxed, with no network, clock or storage, and are held to the
same limits.

| | JavaScript | Rust (native) |
|---|---|---|
| Entry point | `decodeUplink(input)`, `encodeDownlink(input)` | `pulzr_codec_sdk::codec!(decode)` |
| Readings | the `data` it returns, plus `pulzr.emit` | `emit_number`, `emit_bool`, `emit_str`, `emit_json` |
| Errors | `errors` it returns, or a throw | `Err(..)` from `decode` |
| Per call | 50 ms, 16 MB | 50 ms, 16 MB |
| Existing codecs | TTN and ChirpStack codecs run unchanged | — |

## JavaScript

The TTN codec shape: `input` is `{bytes, fPort, recvTime, variables}` and the
answer `{data, warnings, errors}`.

```js
function decodeUplink(input) {
  const b = input.bytes;
  if (b.length < 3) return { errors: ['too short'] };
  return { data: { temperature: ((b[0] << 8) | b[1]) / 10, humidity: b[2] / 2 } };
}
```

### The `pulzr` codec API

Version 1, in scope as `pulzr` in every codec. Every call is pure. Bytes are a
`Uint8Array`, an array of numbers (TTN's `bytes`), an `ArrayBuffer` or a
string (its UTF-8).

| Call | Returns |
|---|---|
| `pulzr.md5(data)`, `pulzr.sha1(data)` | the digest, 16 or 20 bytes |
| `pulzr.hmac('md5' \| 'sha1', key, data)` | the MAC |
| `pulzr.encrypt(cipher, data)`, `pulzr.decrypt(cipher, data)` | bytes; throws on a key, IV or length the cipher does not take |
| `pulzr.base64.encode(data, url?)`, `pulzr.base64.decode(text, url?)` | text, bytes; whitespace and missing padding are forgiven |
| `pulzr.crc(name, data)` | a number, for `CRC-8/SMBUS`, `CRC-16/MODBUS`, `CRC-16/XMODEM`, `CRC-16/KERMIT`, `CRC-16/IBM-3740`, `CRC-16/ARC`, `CRC-16/IBM-SDLC`, `CRC-32`, `CRC-32/ISCSI`, `CRC-32/MPEG-2` |
| `pulzr.xml(text)` | `{name, local, attrs, text, children}`; `local` is the name without its prefix |
| `pulzr.emit(key, value)` | a reading beside `data`; a key `data` has itself wins |

A cipher is `{alg, mode, key, iv, padding}`:

| `alg` | Key | `mode` |
|---|---|---|
| `'des'` | 8 bytes | `'ecb'`, `'cbc'` (default) |
| `'3des'` | 24 bytes | `'ecb'`, `'cbc'` |
| `'aes'` | 16, 24 or 32 bytes | `'ecb'`, `'cbc'`, `'ctr'` |

`padding` is PKCS#7 for ECB and CBC, on by default; `false` needs whole
blocks.

## Rust

A native codec is a core WebAssembly module (`wasm32-unknown-unknown`) that
imports nothing but `pulzr:codec/v1` and exports `memory`, `pulzr_alloc` and
`pulzr_decode`. The crate writes the exports and wraps the same calls as the
JavaScript API.

```rust
use pulzr_codec_sdk::{crc, emit_number};

fn decode(b: &[u8], port: u8) -> Result<(), String> {
    if port != 1 {
        return Err(format!("no frame on port {port}"));
    }
    let [t0, t1, rh, mv, check] = b.try_into().map_err(|_| format!("5 bytes, not {}", b.len()))?;
    if crc("CRC-8/SMBUS", &b[..4]) != Some(check as u32) {
        return Err("CRC mismatch".into());
    }
    emit_number("temperature", i16::from_be_bytes([t0, t1]) as f64 / 10.0);
    emit_number("humidity", rh as f64 / 2.0);
    emit_number("battery_mv", mv as f64 * 20.0);
    Ok(())
}

pulzr_codec_sdk::codec!(decode);
```

```toml
[lib]
crate-type = ["cdylib"]

[dependencies]
pulzr-codec-sdk = "0.1"

[profile.release]
opt-level = "s"
lto = true
panic = "abort"
strip = true
```

```bash
rustup target add wasm32-unknown-unknown
cargo build --release --target wasm32-unknown-unknown
```

The answer is a JavaScript codec's: `data` is what was emitted, and an `Err`
is an error with no data. The whole example is
[`examples/sensor-codec`](https://github.com/pulzar-net/pulzr-package-sdk/tree/main/examples/sensor-codec)
(45 KB built).

## In the console

The codec editor takes all three: choose **Rust** under **Language** to write
`decoder.rs` in the browser, compiled by Pulzr with this crate, with no
toolchain of your own; or **WASM Upload** for a module you built. **Build &
Test** runs the test vectors through the runtime that decodes your uplinks.
