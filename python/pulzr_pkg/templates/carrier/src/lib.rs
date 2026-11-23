//! {{name}}: a carrier driver. Each event is one operation the platform asks
//! of the carrier, `{"op", "args", "config", "now"}`, answered `{"ok": ...}`
//! or `{"error": {"message", "status"}}`. The carrier's API is reached over
//! `pz.http`, only to the hosts driver.json declares, with the organization's
//! credential filled in by the platform and never seen here.

use pulzr_package::host::{self, Request};
use pulzr_package::{export, json, Answer, Event, Package, Value};

#[derive(Default)]
pub struct Driver;

fn op(call: &Value) -> std::result::Result<Value, (String, u16)> {
    match call.get("op").and_then(Value::as_str).unwrap_or("") {
        // Proves the credentials and says what they give.
        "check" => {
            let request = Request::get("https://api.example.com/v1/sims").bearer("api_key");
            match host::http(&request) {
                Ok(r) if r.status == 200 => Ok(json!(["sim.inventory", "sim.status"])),
                Ok(r) => Err((format!("the carrier answered {}", r.status), 403)),
                Err(e) => Err((e, 502)),
            }
        }
        other => Err((format!("{other} is not implemented"), 501)),
    }
}

impl Package for Driver {
    fn handle(&mut self, event: Event) -> Answer {
        let mut answer = Answer::default();
        if let Event::Other(call) = event {
            let (key, value) = match op(&call) {
                Ok(result) => ("ok", result),
                Err((message, status)) => ("error", json!({"message": message, "status": status})),
            };
            answer.extra.insert(key.into(), value);
        }
        answer
    }
}

export!(Driver);
