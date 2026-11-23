//! Write a native Pulzr codec in Rust: bytes in, readings out.
//!
//! A native codec is a core WASM module (wasm32-unknown-unknown) that imports
//! nothing but the Pulzr codec API, `pulzr:codec/v1`, and exports `memory`,
//! `pulzr_alloc` and `pulzr_decode`. [`codec!`] writes the exports:
//!
//! ```ignore
//! fn decode(bytes: &[u8], port: u8) -> Result<(), String> {
//!     pulzr_codec_sdk::emit_number("temperature", bytes[0] as f64 / 2.0);
//!     Ok(())
//! }
//! pulzr_codec_sdk::codec!(decode);
//! ```
//!
//! The runtime answers the call as a JS codec's `{data, warnings, errors}`:
//! `data` is what was emitted, and an `Err` is an error with no data.
//! Everything here is a thin wrapper over the host calls, except the
//! conveniences (number and string formatting), which the codec API leaves
//! to the codec's own code on purpose.

#[cfg(target_arch = "wasm32")]
#[allow(dead_code)]
mod sys {
    #[link(wasm_import_module = "pulzr:codec/v1")]
    unsafe extern "C" {
        pub fn hash(alg: i32, data: *const u8, len: usize) -> i32;
        pub fn hmac(alg: i32, key: *const u8, key_len: usize, data: *const u8, len: usize) -> i32;
        pub fn cipher(
            alg: i32,
            mode: i32,
            flags: i32,
            key: *const u8,
            key_len: usize,
            iv: *const u8,
            iv_len: usize,
            data: *const u8,
            len: usize,
        ) -> i32;
        pub fn base64(op: i32, data: *const u8, len: usize) -> i32;
        pub fn crc(name: *const u8, name_len: usize, data: *const u8, len: usize) -> i64;
        pub fn xml(data: *const u8, len: usize) -> i32;
        pub fn emit(key: *const u8, key_len: usize, json: *const u8, json_len: usize) -> i32;
        pub fn log(level: i32, msg: *const u8, len: usize);
        pub fn take(dst: *mut u8);
    }
}

/// Off WebAssembly there is no host: every call answers as a refusal, so a
/// codec's crate builds, tests and lints on the developer's own machine.
#[cfg(not(target_arch = "wasm32"))]
#[allow(dead_code, clippy::too_many_arguments)]
mod sys {
    pub unsafe fn hash(_: i32, _: *const u8, _: usize) -> i32 {
        -1
    }
    pub unsafe fn hmac(_: i32, _: *const u8, _: usize, _: *const u8, _: usize) -> i32 {
        -1
    }
    pub unsafe fn cipher(
        _: i32,
        _: i32,
        _: i32,
        _: *const u8,
        _: usize,
        _: *const u8,
        _: usize,
        _: *const u8,
        _: usize,
    ) -> i32 {
        -1
    }
    pub unsafe fn base64(_: i32, _: *const u8, _: usize) -> i32 {
        -1
    }
    pub unsafe fn crc(_: *const u8, _: usize, _: *const u8, _: usize) -> i64 {
        -1
    }
    pub unsafe fn xml(_: *const u8, _: usize) -> i32 {
        -1
    }
    pub unsafe fn emit(_: *const u8, _: usize, _: *const u8, _: usize) -> i32 {
        -1
    }
    pub unsafe fn log(_: i32, _: *const u8, _: usize) {}
    pub unsafe fn take(_: *mut u8) {}
}

fn taken(n: i32) -> Option<Vec<u8>> {
    if n < 0 {
        return None;
    }
    let mut v = vec![0u8; n as usize];
    unsafe { sys::take(v.as_mut_ptr()) };
    Some(v)
}

#[derive(Clone, Copy)]
pub enum Hash {
    Md5 = 1,
    Sha1 = 2,
}

pub fn md5(data: &[u8]) -> Vec<u8> {
    taken(unsafe { sys::hash(Hash::Md5 as i32, data.as_ptr(), data.len()) }).unwrap_or_default()
}

pub fn sha1(data: &[u8]) -> Vec<u8> {
    taken(unsafe { sys::hash(Hash::Sha1 as i32, data.as_ptr(), data.len()) }).unwrap_or_default()
}

pub fn hmac(alg: Hash, key: &[u8], data: &[u8]) -> Vec<u8> {
    taken(unsafe {
        sys::hmac(
            alg as i32,
            key.as_ptr(),
            key.len(),
            data.as_ptr(),
            data.len(),
        )
    })
    .unwrap_or_default()
}

#[derive(Clone, Copy)]
pub enum Cipher {
    Des = 1,
    TripleDes = 2,
    /// AES-128, -192 or -256, by the key's length.
    Aes = 3,
}

#[derive(Clone, Copy)]
pub enum Mode {
    Ecb = 1,
    Cbc = 2,
    /// AES only.
    Ctr = 3,
}

/// Encrypts `data`; `pkcs7` pads it (ECB and CBC). None for a key, IV or
/// length the cipher does not take.
pub fn encrypt(
    c: Cipher,
    m: Mode,
    pkcs7: bool,
    key: &[u8],
    iv: &[u8],
    data: &[u8],
) -> Option<Vec<u8>> {
    let flags = if pkcs7 { 2 } else { 0 };
    taken(unsafe {
        sys::cipher(
            c as i32,
            m as i32,
            flags,
            key.as_ptr(),
            key.len(),
            iv.as_ptr(),
            iv.len(),
            data.as_ptr(),
            data.len(),
        )
    })
}

pub fn decrypt(
    c: Cipher,
    m: Mode,
    pkcs7: bool,
    key: &[u8],
    iv: &[u8],
    data: &[u8],
) -> Option<Vec<u8>> {
    let flags = 1 | if pkcs7 { 2 } else { 0 };
    taken(unsafe {
        sys::cipher(
            c as i32,
            m as i32,
            flags,
            key.as_ptr(),
            key.len(),
            iv.as_ptr(),
            iv.len(),
            data.as_ptr(),
            data.len(),
        )
    })
}

pub fn base64_encode(data: &[u8]) -> String {
    String::from_utf8(
        taken(unsafe { sys::base64(0, data.as_ptr(), data.len()) }).unwrap_or_default(),
    )
    .unwrap_or_default()
}

pub fn base64_decode(text: &str) -> Option<Vec<u8>> {
    taken(unsafe { sys::base64(1, text.as_ptr(), text.len()) })
}

/// A CRC by its reveng name ("CRC-16/MODBUS"); None for a name the host lacks.
pub fn crc(name: &str, data: &[u8]) -> Option<u32> {
    let v = unsafe { sys::crc(name.as_ptr(), name.len(), data.as_ptr(), data.len()) };
    (v >= 0).then_some(v as u32)
}

/// The document as the host's JSON tree (`{name, local, attrs, text, children}`),
/// or the parser's error.
pub fn xml(text: &str) -> Result<String, String> {
    let out = String::from_utf8(
        taken(unsafe { sys::xml(text.as_ptr(), text.len()) }).unwrap_or_default(),
    )
    .unwrap_or_default();
    match out.strip_prefix('!') {
        Some(e) => Err(e.to_string()),
        None => Ok(out),
    }
}

/// A reading whose value is JSON text.
pub fn emit_json(key: &str, json: &str) {
    unsafe { sys::emit(key.as_ptr(), key.len(), json.as_ptr(), json.len()) };
}

/// A numeric reading. NaN and Infinity are dropped, as a JS codec's are.
pub fn emit_number(key: &str, v: f64) {
    if v.is_finite() {
        emit_json(key, &format!("{v}"));
    }
}

pub fn emit_bool(key: &str, v: bool) {
    emit_json(key, if v { "true" } else { "false" });
}

pub fn emit_str(key: &str, v: &str) {
    let mut s = String::with_capacity(v.len() + 2);
    s.push('"');
    for c in v.chars() {
        match c {
            '"' => s.push_str("\\\""),
            '\\' => s.push_str("\\\\"),
            '\n' => s.push_str("\\n"),
            '\r' => s.push_str("\\r"),
            '\t' => s.push_str("\\t"),
            c if (c as u32) < 0x20 => s.push_str(&format!("\\u{:04x}", c as u32)),
            c => s.push(c),
        }
    }
    s.push('"');
    emit_json(key, &s);
}

pub fn warn(msg: &str) {
    unsafe { sys::log(1, msg.as_ptr(), msg.len()) };
}

pub fn error(msg: &str) {
    unsafe { sys::log(2, msg.as_ptr(), msg.len()) };
}

/// The host writes a frame here before calling decode. Leaked on purpose:
/// an instance lives for one codec and its memory goes with it.
#[doc(hidden)]
pub fn alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len.max(1));
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

/// # Safety
/// `ptr` and `len` are a buffer [`alloc`] returned, which the host filled.
#[doc(hidden)]
pub unsafe fn run(
    ptr: *mut u8,
    len: usize,
    port: i32,
    f: fn(&[u8], u8) -> Result<(), String>,
) -> i32 {
    let bytes = unsafe { Vec::from_raw_parts(ptr, len, len.max(1)) };
    match f(&bytes, port as u8) {
        Ok(()) => 0,
        Err(e) => {
            error(&e);
            1
        }
    }
}

/// Exports `pulzr_alloc` and `pulzr_decode` for `fn(&[u8], u8) -> Result<(), String>`.
#[macro_export]
macro_rules! codec {
    ($decode:path) => {
        #[unsafe(no_mangle)]
        pub extern "C" fn pulzr_alloc(len: usize) -> *mut u8 {
            $crate::alloc(len)
        }
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn pulzr_decode(ptr: *mut u8, len: usize, port: i32) -> i32 {
            unsafe { $crate::run(ptr, len, port, $decode) }
        }
    };
}
