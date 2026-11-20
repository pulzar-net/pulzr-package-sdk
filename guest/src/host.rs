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
    fn pz_state_set(kp: i32, kl: i32, vp: i32, vl: i32) -> i32;
    #[link_name = "http"]
    fn pz_http(ptr: i32, len: i32) -> i64;
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

/// `pz.state_set`: keep `value` for `key` (empty deletes). Imports `state`.
pub fn state_set(key: &str, value: &[u8]) -> Result<(), StateFull> {
    #[cfg(target_arch = "wasm32")]
    {
        let r = unsafe {
            pz_state_set(
                key.as_ptr() as i32,
                key.len() as i32,
                value.as_ptr() as i32,
                value.len() as i32,
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
        let _ = (key, value);
        Err(StateFull)
    }
}

/// An outbound HTTPS request (docs/abi.md, `http`).
#[derive(Debug, Clone, Default, Serialize)]
pub struct Request {
    pub method: String,
    pub url: String,
    #[serde(skip_serializing_if = "Map::is_empty")]
    pub headers: Map<String, Value>,
    /// Base64 of the body.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub body: String,
    /// A credential the manifest declares, injected by the host.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub credential: Option<String>,
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
        let mut headers = Map::new();
        headers.insert("content-type".into(), "application/json".into());
        Request {
            method: "POST".into(),
            url: url.into(),
            headers,
            body: crate::hexs::b64_encode(body.to_string().as_bytes()),
            ..Default::default()
        }
    }

    /// Has the host inject the organization's secret for `name`.
    pub fn with_credential(mut self, name: impl Into<String>) -> Request {
        self.credential = Some(name.into());
        self
    }
}

/// What came back.
#[derive(Debug, Clone, Deserialize)]
pub struct Response {
    pub status: u16,
    #[serde(default)]
    pub headers: Map<String, Value>,
    #[serde(default)]
    body: String,
}

impl Response {
    pub fn body(&self) -> Vec<u8> {
        crate::hexs::b64_decode(&self.body).unwrap_or_default()
    }

    pub fn json(&self) -> Option<Value> {
        serde_json::from_slice(&self.body()).ok()
    }
}

/// `pz.http`: the response, or why there is none (a host not declared, a
/// timeout, the network). Imports `http`.
pub fn http(request: &Request) -> Result<Response, String> {
    #[cfg(target_arch = "wasm32")]
    {
        let req = serde_json::to_vec(request).map_err(|e| e.to_string())?;
        let packed = unsafe { pz_http(req.as_ptr() as i32, req.len() as i32) } as u64;
        let (ptr, len) = ((packed >> 32) as i32, (packed & 0xffff_ffff) as i32);
        let answer: Value =
            serde_json::from_slice(&crate::abi::take(ptr, len)).map_err(|e| e.to_string())?;
        if let Some(e) = answer.get("error").and_then(Value::as_str) {
            return Err(e.to_owned());
        }
        serde_json::from_value(answer).map_err(|e| e.to_string())
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = request;
        Err("http needs the platform's host".into())
    }
}
