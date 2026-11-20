//! Write a Pulzr extension module in Rust.
//!
//! An extension module is code a third party adds to the Pulzr platform, run
//! in the platform's WebAssembly sandbox: a gateway support package's adapter
//! (another vendor's gateway, translated into protocol v1) or a carrier driver.
//! This crate hides the ABI ([`abi`], `docs/src/content/docs/abi.md`): implement [`Package`],
//! name your type in [`export!`], and build for `wasm32-unknown-unknown`.
//!
//! ```no_run
//! use pulzr_package::{export, Answer, Event, Package};
//!
//! #[derive(Default)]
//! struct Hello;
//!
//! impl Package for Hello {
//!     fn handle(&mut self, event: Event) -> Answer {
//!         let mut answer = Answer::default();
//!         if let Event::Tick { .. } = event {
//!             answer.log_info("still here");
//!         }
//!         answer
//!     }
//! }
//!
//! export!(Hello);
//! ```
//!
//! The host functions ([`host`]) are imported only when called, and each must
//! be declared in the manifest's `imports`; [`ids`], [`hexs`], [`correlation`]
//! and [`settings`] are the helpers gateway translators keep needing.

pub mod abi;
pub mod correlation;
pub mod host;
pub mod ids;
pub mod settings;
pub mod v1;

/// The JSON library events and answers are made with, for a module's own parsing.
pub use serde_json;
pub use serde_json::{json, Map, Value};

use base64::Engine as _;
use serde::Serialize;

/// The ABI version this crate's exports implement.
pub const ABI_VERSION: i32 = 2;

/// One event from the host. Gateway packages receive the first three; a kind
/// with other events (a carrier driver's) reads them from [`Event::Other`].
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// A vendor message on one of the package's broker subscriptions.
    Message {
        topic: String,
        payload: Vec<u8>,
        received: f64,
    },
    /// A protocol v1 intent for one of the package's gateways, its secret
    /// params opened for this call.
    Intent {
        adapter: String,
        key: String,
        id: String,
        name: String,
        params: Value,
        expires: Option<f64>,
        now: f64,
    },
    /// About every ten seconds.
    Tick { now: f64 },
    /// An event of another kind, whole.
    Other(Value),
}

impl Event {
    /// Reads an event object; anything not one of the gateway events above is
    /// [`Event::Other`].
    pub fn from_value(v: Value) -> Event {
        let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_owned);
        let f = |k: &str| v.get(k).and_then(Value::as_f64);
        match v.get("event").and_then(Value::as_str) {
            Some("message") => match (s("topic"), s("payload"), f("received")) {
                (Some(topic), Some(p), Some(received)) => match hexs::b64_decode(&p) {
                    Some(payload) => Event::Message {
                        topic,
                        payload,
                        received,
                    },
                    None => Event::Other(v),
                },
                _ => Event::Other(v),
            },
            Some("intent") => match (s("adapter"), s("key"), s("id"), s("name"), f("now")) {
                (Some(adapter), Some(key), Some(id), Some(name), Some(now)) => Event::Intent {
                    adapter,
                    key,
                    id,
                    name,
                    now,
                    params: v
                        .get("params")
                        .cloned()
                        .unwrap_or_else(|| Value::Object(Map::new())),
                    expires: f("expires"),
                },
                _ => Event::Other(v),
            },
            Some("tick") => match f("now") {
                Some(now) => Event::Tick { now },
                None => Event::Other(v),
            },
            _ => Event::Other(v),
        }
    }
}

/// A vendor message to publish.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Publish {
    pub topic: String,
    /// Base64 of the payload.
    pub payload: String,
}

/// A protocol v1 message, as an adapter would publish it on
/// `pulzr/a/<adapter>/d/<key>/<kind>`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Protocol {
    pub adapter: String,
    pub key: String,
    /// `up`, `state`, `settings`, `event` or `cmd/res`.
    pub kind: String,
    pub body: Value,
}

/// A line for the platform's log.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LogLine {
    pub level: &'static str,
    pub message: String,
}

/// The answer to one event. Every part is optional; `extra` carries a kind's
/// own members (a carrier driver's).
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Answer {
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub publish: Vec<Publish>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub protocol: Vec<Protocol>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub log: Vec<LogLine>,
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

impl Answer {
    /// Publish `payload` on a vendor topic.
    pub fn publish(&mut self, topic: impl Into<String>, payload: impl AsRef<[u8]>) -> &mut Self {
        self.publish.push(Publish {
            topic: topic.into(),
            payload: hexs::b64_encode(payload.as_ref()),
        });
        self
    }

    /// Publish a JSON value on a vendor topic.
    pub fn publish_json(&mut self, topic: impl Into<String>, value: &Value) -> &mut Self {
        self.publish(topic, value.to_string())
    }

    /// A protocol v1 message for `key` behind `adapter`.
    pub fn protocol(
        &mut self,
        adapter: impl Into<String>,
        key: impl Into<String>,
        kind: &str,
        body: Value,
    ) -> &mut Self {
        self.protocol.push(Protocol {
            adapter: adapter.into(),
            key: key.into(),
            kind: kind.into(),
            body,
        });
        self
    }

    /// The answer to an intent: `cmd/res` with its `id` and `status`.
    pub fn result(&mut self, adapter: &str, key: &str, result: v1::CmdResult) -> &mut Self {
        self.protocol(
            adapter,
            key,
            "cmd/res",
            serde_json::to_value(result).unwrap_or(Value::Null),
        )
    }

    fn line(&mut self, level: &'static str, message: impl Into<String>) -> &mut Self {
        self.log.push(LogLine {
            level,
            message: message.into(),
        });
        self
    }

    pub fn log_debug(&mut self, m: impl Into<String>) -> &mut Self {
        self.line("debug", m)
    }
    pub fn log_info(&mut self, m: impl Into<String>) -> &mut Self {
        self.line("info", m)
    }
    pub fn log_warning(&mut self, m: impl Into<String>) -> &mut Self {
        self.line("warning", m)
    }
    pub fn log_error(&mut self, m: impl Into<String>) -> &mut Self {
        self.line("error", m)
    }
}

/// An extension module. One value lives for as long as its instance does:
/// what it keeps may be lost at any time (a trap, an upgrade, a restart), so
/// keep in it only what costs nothing to lose, and the rest in
/// [`host::state_set`].
pub trait Package: Default {
    fn handle(&mut self, event: Event) -> Answer;
}

/// Hex and base64.
pub mod hexs {
    use super::*;

    pub fn b64_encode(b: &[u8]) -> String {
        base64::engine::general_purpose::STANDARD.encode(b)
    }

    pub fn b64_decode(s: &str) -> Option<Vec<u8>> {
        base64::engine::general_purpose::STANDARD.decode(s).ok()
    }

    /// Lower-case hex.
    pub fn encode(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }

    /// Hex of either case, or None.
    pub fn decode(s: &str) -> Option<Vec<u8>> {
        if s.len() % 2 != 0 || !s.is_ascii() {
            return None;
        }
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
            .collect()
    }
}

/// Exports the ABI for a [`Package`] type: `pz_abi`, `pz_alloc`, `pz_handle`.
#[macro_export]
macro_rules! export {
    ($t:ty) => {
        ::std::thread_local! {
            static PZ_PACKAGE: ::std::cell::RefCell<$t> = ::std::cell::RefCell::new(<$t>::default());
        }

        #[no_mangle]
        pub extern "C" fn pz_abi() -> i32 {
            $crate::ABI_VERSION
        }

        #[no_mangle]
        pub extern "C" fn pz_alloc(len: i32) -> i32 {
            $crate::abi::alloc(len)
        }

        #[no_mangle]
        pub extern "C" fn pz_handle(ptr: i32, len: i32) -> i64 {
            $crate::abi::handle(ptr, len, |event| PZ_PACKAGE.with(|p| p.borrow_mut().handle(event)))
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_are_read_and_answers_written() {
        let e = Event::from_value(
            json!({"event": "message", "topic": "t/1", "payload": "aGk=", "received": 5.0}),
        );
        assert_eq!(
            e,
            Event::Message {
                topic: "t/1".into(),
                payload: b"hi".to_vec(),
                received: 5.0
            }
        );
        let i = Event::from_value(
            json!({"event": "intent", "adapter": "x-1", "key": "gw:1", "id": "7",
                                         "name": "reboot", "now": 1.0, "expires": null}),
        );
        assert!(matches!(i, Event::Intent { ref name, expires: None, .. } if name == "reboot"));
        assert!(matches!(
            Event::from_value(json!({"event": "call", "x": 1})),
            Event::Other(_)
        ));
        assert!(matches!(
            Event::from_value(json!({"event": "message", "topic": "t"})),
            Event::Other(_)
        ));

        let mut a = Answer::default();
        a.publish("t/down", b"hi")
            .protocol("x-1", "gw:1", "state", json!({"v": 1}))
            .log_info("ok");
        a.result("x-1", "gw:1", v1::CmdResult::ok("7"));
        assert_eq!(
            serde_json::to_value(&a).unwrap(),
            json!({
            "publish": [{"topic": "t/down", "payload": "aGk="}],
            "protocol": [{"adapter": "x-1", "key": "gw:1", "kind": "state", "body": {"v": 1}},
                         {"adapter": "x-1", "key": "gw:1", "kind": "cmd/res", "body": {"v": 1, "id": "7", "status": "ok"}}],
            "log": [{"level": "info", "message": "ok"}]})
        );
        assert_eq!(serde_json::to_value(Answer::default()).unwrap(), json!({}));
        assert_eq!(hexs::decode("0aFF"), Some(vec![10, 255]));
        assert_eq!(hexs::encode(&[10, 255]), "0aff");
        assert_eq!(hexs::decode("abc"), None);
    }
}
