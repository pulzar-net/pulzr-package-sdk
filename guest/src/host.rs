//! The host functions (docs/abi.md, module `pz`). Each is imported only when
//! called, and must be declared in the manifest's `imports` (`log`, `now`,
//! `state`, `http`), or the platform refuses the module at install. Off
//! wasm32 (in a crate's own tests) they do nothing useful: test translation
//! logic with plain functions, and the module with `pulzr-pkg test`.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[cfg(target_arch = "wasm32")]
#[link(wasm_import_module = "pz")]
extern "C" {
    #[link_name = "log"]
    fn pz_log(level: i32, ptr: i32, len: i32);
    #[link_name = "now"]
    fn pz_now() -> f64;
    #[link_name = "state_get"]
    fn pz_state_get(kp: i32, kl: i32, out: i32, cap: i32) -> i32;
    #[link_name = "state_set"]
    fn pz_state_set(kp: i32, kl: i32, vp: i32, vl: i32, ttl: i32) -> i32;
    #[link_name = "http"]
    fn pz_http(ptr: i32, len: i32) -> i32;
    #[link_name = "http_take"]
    fn pz_http_take(ptr: i32);
}

/// A log level, as `pz.log` numbers them.
#[derive(Debug, Clone, Copy)]
pub enum Level {
    Debug = 0,
    Info = 1,
    Warning = 2,
    Error = 3,
}

/// `pz.log`: a line in the platform's log now (an [`crate::Answer`]'s `log`
/// goes with the answer instead, and needs no import). Imports `log`.
pub fn log(level: Level, message: &str) {
    #[cfg(target_arch = "wasm32")]
    unsafe {
        pz_log(level as i32, message.as_ptr() as i32, message.len() as i32)
    }
    #[cfg(not(target_arch = "wasm32"))]
    let _ = (level, message);
}

/// `pz.now`: seconds since 1970 UTC. Imports `now`. Every event carries a
/// time too, which needs no import.
pub fn now() -> f64 {
    #[cfg(target_arch = "wasm32")]
    unsafe {
        pz_now()
    }
    #[cfg(not(target_arch = "wasm32"))]
    0.0
}

/// The state store refused a value: the install would hold more than the
/// manifest's `limits.stateKiB`, or the key is longer than 64 octets.
#[derive(Debug, Clone, PartialEq)]
pub struct StateFull;

/// `pz.state_get`: this install's value for `key`. Imports `state`.
pub fn state_get(key: &str) -> Option<Vec<u8>> {
    #[cfg(target_arch = "wasm32")]
    {
        let mut buf = vec![0u8; 256];
        loop {
            let n = unsafe {
                pz_state_get(
                    key.as_ptr() as i32,
                    key.len() as i32,
                    buf.as_mut_ptr() as i32,
                    buf.len() as i32,
                )
            };
            if n < 0 {
                return None;
            }
            if n as usize <= buf.len() {
                buf.truncate(n as usize);
                return Some(buf);
            }
            buf = vec![0u8; n as usize];
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = key;
        None
    }
}

/// `pz.state_set`: keep `value` for `key` (empty deletes), for `ttl_s`
/// seconds or, with 0, until replaced (an OAuth token: its lifetime).
/// Imports `state`.
pub fn state_set(key: &str, value: &[u8], ttl_s: u32) -> Result<(), StateFull> {
    #[cfg(target_arch = "wasm32")]
    {
        let r = unsafe {
            pz_state_set(
                key.as_ptr() as i32,
                key.len() as i32,
                value.as_ptr() as i32,
                value.len() as i32,
                ttl_s.min(i32::MAX as u32) as i32,
            )
        };
        if r == 0 {
            Ok(())
        } else {
            Err(StateFull)
        }
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = (key, value, ttl_s);
        Err(StateFull)
    }
}

/// An outbound HTTPS request (docs/abi.md, `http`). A credential the manifest
/// declares goes in a header or the body as `{{credential.<name>}}` (or
/// `{{basic:<user field>:<password field>}}` for HTTP Basic), and the host
/// fills it in: the module never holds the secret, and never sees it echoed.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Request {
    pub method: String,
    pub url: String,
    #[serde(skip_serializing_if = "Map::is_empty")]
    pub headers: Map<String, Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body: Option<String>,
    /// A GET's answer may be served from the host's cache for this many seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cache: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u32>,
}

impl Request {
    pub fn get(url: impl Into<String>) -> Request {
        Request {
            method: "GET".into(),
            url: url.into(),
            ..Default::default()
        }
    }

    pub fn post_json(url: impl Into<String>, body: &Value) -> Request {
        let mut r = Request {
            method: "POST".into(),
            url: url.into(),
            body: Some(body.to_string()),
            ..Default::default()
        };
        r.headers
            .insert("content-type".into(), "application/json".into());
        r
    }

    /// A header, which may hold a credential placeholder.
    pub fn header(mut self, name: &str, value: impl Into<String>) -> Request {
        self.headers
            .insert(name.into(), Value::String(value.into()));
        self
    }

    /// `Authorization: Bearer <the credential named>`.
    pub fn bearer(self, credential: &str) -> Request {
        self.header(
            "Authorization",
            format!("Bearer {{{{credential.{credential}}}}}"),
        )
    }

    pub fn cached(mut self, seconds: u32) -> Request {
        self.cache = Some(seconds);
        self
    }
}

/// What came back; `status` 0 with `error` when no request was made.
#[derive(Debug, Clone, Deserialize)]
pub struct Response {
    pub status: u16,
    #[serde(default)]
    pub headers: Map<String, Value>,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub cached: bool,
}

impl Response {
    pub fn json(&self) -> Option<Value> {
        serde_json::from_str(&self.body).ok()
    }
}

#[cfg(target_arch = "wasm32")]
fn exchange(request: &Value) -> Result<Value, String> {
    let req = serde_json::to_vec(request).map_err(|e| e.to_string())?;
    let n = unsafe { pz_http(req.as_ptr() as i32, req.len() as i32) };
    if n < 0 {
        return Err("too many requests in one call".into());
    }
    let mut buf = vec![0u8; n as usize];
    unsafe { pz_http_take(buf.as_mut_ptr() as i32) };
    serde_json::from_slice(&buf).map_err(|e| e.to_string())
}

/// `pz.http`: one request. `Err` only when the host could not answer at all;
/// a refused or failed request is a [`Response`] with `status` 0 and `error`.
/// Imports `http`.
pub fn http(request: &Request) -> Result<Response, String> {
    #[cfg(target_arch = "wasm32")]
    {
        let v = exchange(&serde_json::to_value(request).map_err(|e| e.to_string())?)?;
        serde_json::from_value(v).map_err(|e| e.to_string())
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = request;
        Err("http needs the platform's host".into())
    }
}

/// `pz.http` with an array: the requests made at once (a fleet of SIMs in
/// seconds), the responses in the same order. Imports `http`.
pub fn http_all(requests: &[Request]) -> Result<Vec<Response>, String> {
    #[cfg(target_arch = "wasm32")]
    {
        let v = exchange(&serde_json::to_value(requests).map_err(|e| e.to_string())?)?;
        serde_json::from_value(v).map_err(|e| e.to_string())
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = requests;
        Err("http needs the platform's host".into())
    }
}
