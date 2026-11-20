//! Protocol v1 messages a gateway package sends most, typed. Anything else
//! (and any member these leave out) goes as a [`serde_json::Value`] body
//! through [`crate::Answer::protocol`]; the platform checks every message
//! against the protocol's schema either way.

use serde::Serialize;
use serde_json::{Map, Value};

/// Where an advert was heard: `rx[]`.
#[derive(Debug, Clone, Serialize)]
pub struct Rx {
    /// `gw:<gateway EUI>`.
    pub via: String,
    pub ts: f64,
    /// `device` when the gateway's clock stamped it, `received` when the platform's did.
    pub time: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rssi: Option<i64>,
}

/// A device's reading or frame: `up`.
#[derive(Debug, Clone, Serialize)]
pub struct Up {
    pub v: u8,
    /// A stable hash of the frame, so a replay is harmless.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dedup: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw: Option<Value>,
    /// Readings the gateway decoded; an empty list says there is nothing to decode.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub r: Option<Vec<Value>>,
    pub rx: Vec<Rx>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub link: Option<Value>,
}

impl Up {
    /// An advert's bytes as heard by one gateway, its `dedup` made from the
    /// gateway, the device key, the time and the bytes, so the same advert
    /// delivered twice is one.
    pub fn raw(key: &str, bytes: &[u8], ts: f64, rx: Rx) -> Up {
        let raw = serde_json::json!({"ts": ts, "b64": crate::hexs::b64_encode(bytes)});
        let dedup = dedup(&[
            rx.via.as_bytes(),
            key.as_bytes(),
            &ts.to_bits().to_be_bytes(),
            bytes,
        ]);
        Up {
            v: 1,
            dedup: Some(dedup),
            raw: Some(raw),
            r: None,
            rx: vec![rx],
            link: None,
        }
    }
}

/// A stable 32-hex-digit key for a frame from its parts (FNV-1a, 128 bits):
/// the same parts give the same key in every build and on every host. Not a
/// cryptographic hash; it names a frame, it does not protect one.
pub fn dedup(parts: &[&[u8]]) -> String {
    const PRIME: u128 = 0x0000000001000000000000000000013B;
    let mut h: u128 = 0x6c62272e07bb014262b821756295c58d;
    for part in parts {
        for b in (part.len() as u64).to_be_bytes().iter().chain(part.iter()) {
            h ^= *b as u128;
            h = h.wrapping_mul(PRIME);
        }
    }
    format!("{h:032x}")
}

/// A gateway's own state: `state` on `gw:<EUI>`.
#[derive(Debug, Clone, Serialize)]
pub struct State {
    pub v: u8,
    pub online: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_heard: Option<f64>,
    #[serde(skip_serializing_if = "Map::is_empty")]
    pub diag: Map<String, Value>,
}

/// The answer to an intent: `cmd/res`.
#[derive(Debug, Clone, Serialize)]
pub struct CmdResult {
    pub v: u8,
    pub id: String,
    /// `sent`, `delivered`, `ok`, `refused` or `failed`.
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl CmdResult {
    fn new(id: &str, status: &'static str) -> CmdResult {
        CmdResult {
            v: 1,
            id: id.into(),
            status,
            reason: None,
            detail: None,
            data: None,
        }
    }

    pub fn sent(id: &str) -> CmdResult {
        CmdResult::new(id, "sent")
    }
    pub fn delivered(id: &str) -> CmdResult {
        CmdResult::new(id, "delivered")
    }
    pub fn ok(id: &str) -> CmdResult {
        CmdResult::new(id, "ok")
    }

    /// Not sent, and why (`invalid`, `unknown`, `expired`).
    pub fn refused(id: &str, reason: &str, detail: impl Into<String>) -> CmdResult {
        CmdResult {
            reason: Some(reason.into()),
            detail: Some(detail.into()),
            ..CmdResult::new(id, "refused")
        }
    }

    /// Sent and failed (`undeliverable`, `error`, `expired`).
    pub fn failed(id: &str, reason: &str, detail: impl Into<String>) -> CmdResult {
        CmdResult {
            reason: Some(reason.into()),
            detail: Some(detail.into()),
            ..CmdResult::new(id, "failed")
        }
    }

    pub fn with_data(mut self, data: Value) -> CmdResult {
        self.data = Some(data);
        self
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn dedup_is_stable_and_separates_parts() {
        let a = super::dedup(&[b"gw:1", b"ble:2", b"\x01"]);
        assert_eq!(a.len(), 32);
        assert_eq!(a, super::dedup(&[b"gw:1", b"ble:2", b"\x01"]));
        assert_ne!(a, super::dedup(&[b"gw:1ble:2", b"", b"\x01"]));
    }
}
