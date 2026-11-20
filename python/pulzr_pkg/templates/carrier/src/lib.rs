//! {{name}}: a carrier driver. It answers the platform's calls (what the
//! carrier-driver kind defines) with the carrier's own API, over `pz.http` to
//! the hosts driver.json declares, with the organization's credential injected
//! by the platform, never seen here.

use pulzr_package::host::{self, Request};
use pulzr_package::{export, json, Answer, Event, Package, Value};

#[derive(Default)]
pub struct Driver;

impl Package for Driver {
    fn handle(&mut self, event: Event) -> Answer {
        let mut answer = Answer::default();
        if let Event::Other(call) = event {
            if call.get("event").and_then(Value::as_str) == Some("ping") {
                let request = Request::get("https://api.example.com/v1/ping").with_credential("api");
                let result = match host::http(&request) {
                    Ok(r) => json!({"ok": r.status == 200}),
                    Err(e) => json!({"ok": false, "error": e}),
                };
                answer.extra.insert("result".into(), result);
            }
        }
        answer
    }
}

export!(Driver);
