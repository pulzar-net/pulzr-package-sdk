//! {{name}}: a gateway support package. Translate the vendor's messages into
//! protocol v1, and protocol v1 intents into the vendor's messages.
//! The worked example is pulzr-package-sdk's examples/beacon-gateway.

use pulzr_package::{correlation, export, ids, json, serde_json, v1, Answer, Event, Package, Value};

pub const PREFIX: &str = "chgme";

#[derive(Default)]
pub struct Adapter;

impl Package for Adapter {
    fn handle(&mut self, event: Event) -> Answer {
        let mut answer = Answer::default();
        match event {
            Event::Message { topic, payload, received } => {
                let Ok(message) = serde_json::from_slice::<Value>(&payload) else {
                    answer.log_warning(format!("unreadable message on {topic}"));
                    return answer;
                };
                // The gateway's MAC, from the message: the vendor's own field.
                let Some(gw) = message.get("gw").and_then(Value::as_str).and_then(ids::eui_for_mac) else {
                    return answer;
                };
                let state = json!({"v": 1, "online": true, "last_heard": received});
                answer.protocol(ids::adapter(PREFIX, &gw), ids::key("gw", &gw), "state", state);
            }
            Event::Intent { adapter, key, id, name, .. } => {
                let Some(_seq) = correlation::seq_for_id(&id) else {
                    answer.result(&adapter, &key, v1::CmdResult::refused(&id, "invalid", "The id must be decimal"));
                    return answer;
                };
                answer.result(&adapter, &key, v1::CmdResult::refused(&id, "unknown", format!("{name} is not done yet")));
            }
            _ => {}
        }
        answer
    }
}

export!(Adapter);
